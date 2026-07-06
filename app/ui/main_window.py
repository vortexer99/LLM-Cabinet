"""主窗口：左侧项目卡片墙 / 中间文件列表 / 右侧详情+预览。"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QStyledItemDelegate,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..library import Library
from ..models import FileItem, Project
from ..repository import Repository
from ..search import combine_and, field_term, parse_search
from ..search_history import (
    HISTORY_SETTING_KEY,
    SAVED_SEARCHES_SETTING_KEY,
    add_history,
    load_history,
    load_saved_searches,
    remove_history,
    remove_saved_search,
    upsert_saved_search,
)
from ..utils import detect_kind, human_size as _human_size, open_with_default_app, reveal_in_explorer
from .dnd import FilesTableDnD, ProjectViewDnD
from .files_table_columns import (
    COLUMNS as FILES_COLUMNS,
    SETTING_KEY as FILES_COLUMNS_SETTING_KEY,
    column_by_key as files_column_by_key,
    dump_prefs as files_dump_prefs,
    load_prefs as files_load_prefs,
    resolve_pref as files_resolve_pref,
)
from .preview import PreviewPanel
from .project_card import ProjectCardDelegate, ProjectModel
from .project_dialog import ProjectDialog
from .export_dialog import ExportDialog
from .settings_dialog import SettingsDialog
from .tag_tree import TagTree
from .theme import apply_theme
from .widgets import DropZone


class NoElideDelegate(QStyledItemDelegate):
    """强制单元格文本不省略：列宽不够时直接截断显示，不渲染 …。

    某些 Qt style（Windows / Fusion 都见过）即便设置 ``option.textElideMode =
    Qt.ElideNone``，仍会在 ``style->drawControl(CE_ItemViewItem, ...)`` 内部按
    ``Qt.ElideRight`` 再 elide 一次。所以这里让 style 只画背景/选中态/图标，
    文本自己用 painter 画并 clip 到 cell rect，超出部分被硬裁掉。
    """

    def initStyleOption(self, option, index):  # noqa: N802 (Qt 命名)
        super().initStyleOption(option, index)
        option.textElideMode = Qt.ElideNone

    def paint(self, painter, option, index):  # noqa: N802
        from PySide6.QtWidgets import (
            QApplication,
            QStyle,
            QStyleOptionViewItem,
        )

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        text = opt.text
        # 让 style 正常画背景/选中态/焦点/图标，但不要让它画文字
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        if not text:
            return

        # 计算文本应当占据的矩形（已扣除图标和内边距）
        text_rect = style.subElementRect(
            QStyle.SE_ItemViewItemText, opt, opt.widget
        )

        painter.save()
        painter.setClipRect(text_rect)
        painter.setFont(opt.font)
        # 选中态用高亮色，否则用普通前景色
        if opt.state & QStyle.State_Selected:
            painter.setPen(opt.palette.highlightedText().color())
        else:
            painter.setPen(opt.palette.text().color())
        painter.drawText(text_rect, int(opt.displayAlignment), text)
        painter.restore()


def _ask_delete_mode(parent, root_path, scan) -> str | None:
    """删除整个库时，库目录下还存在外来内容时的"删除范围"选择对话框。

    返回：``"owned"`` = 仅删库数据保留外来文件 / ``"all"`` = 一并删除（含目录）/
    ``None`` = 用户取消。

    UI：上方一段说明 + 一份"外来内容"清单（最多展示 N 行 + "更多 K 项"省略）+
    两个 RadioButton + Cancel/OK。默认选 owned（更安全）。
    """
    from PySide6.QtWidgets import (
        QButtonGroup, QDialog, QDialogButtonBox, QLabel, QPlainTextEdit,
        QRadioButton, QVBoxLayout,
    )
    dlg = QDialog(parent)
    dlg.setWindowTitle("检测到非库内容")
    dlg.setMinimumWidth(560)
    v = QVBoxLayout(dlg)

    intro = QLabel(
        f"<b>库目录下检测到 {len(scan.foreign)} 项不属于库自身的内容</b>"
        f"（共 {_human_size(scan.foreign_size)}）。<br/>"
        "这些内容**不是** LLM Cabinet 创建的（可能是你自己放进库目录的笔记、"
        "备份、临时文件等）。请选择如何处理："
    )
    intro.setTextFormat(Qt.RichText)
    intro.setWordWrap(True)
    v.addWidget(intro)

    # 外来清单（只读文本框，避免对话框被巨长列表撑爆）
    MAX_LINES = 30
    lines: list[str] = []
    for entry in scan.foreign[:MAX_LINES]:
        suffix = "/" if entry.is_dir() else ""
        lines.append(f"  • {entry.name}{suffix}")
    if len(scan.foreign) > MAX_LINES:
        lines.append(f"  ... 还有 {len(scan.foreign) - MAX_LINES} 项")
    lst = QPlainTextEdit()
    lst.setReadOnly(True)
    lst.setPlainText("\n".join(lines))
    lst.setMaximumHeight(180)
    v.addWidget(lst)

    rb_owned = QRadioButton(
        "🟢 保留这些文件，只删除库数据（推荐）"
    )
    rb_owned.setToolTip(
        "删除 cabinet.db / library/ / .llm-cabinet 等库自身条目，保留目录本身"
        "与上面列出的外来文件。删完后该目录将不再是 LLM Cabinet 的库。"
    )
    rb_owned.setChecked(True)
    rb_all = QRadioButton(
        "🔴 一并删除（包括上面列出的外来文件，以及目录本身）"
    )
    rb_all.setToolTip(
        "等同于 rmtree(库目录)；连同你自己放进来的内容也一起删。"
    )
    grp = QButtonGroup(dlg)
    grp.addButton(rb_owned)
    grp.addButton(rb_all)
    v.addWidget(rb_owned)
    v.addWidget(rb_all)

    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    v.addWidget(bb)

    if dlg.exec() != QDialog.Accepted:
        return None
    return "all" if rb_all.isChecked() else "owned"


class MainWindow(QMainWindow):
    MAIN_SPLITTER_SETTING_KEY = "main_splitter_sizes"
    MAIN_SPLITTER_DEFAULT_SIZES = [200, 800, 400]
    MAIN_SPLITTER_MIN_SIZE = 80

    def __init__(
        self,
        repo: Repository,
        library: Library,
        db_path=None,
        llm_queue=None,
        cabinet_config=None,
        library_root=None,
    ):
        super().__init__()
        self.repo = repo
        self.library = library
        self.db_path = db_path
        self.llm_queue = llm_queue
        # task #08 多库切换
        self.cabinet_config = cabinet_config       # CabinetConfig | None
        self.library_root = library_root           # Path | None；当前活动库根目录
        self._pending_switch_to = None             # 关闭后由 main() 检测的"重启切换"目标：
                                                   # Path = 切到该路径；"__welcome__" = 重启进 Welcome；None = 不重启
        self._search_menu_open = False
        self._search_menu_last_closed_at = 0.0

        # 标题栏带上当前库 label，方便用户知道在哪个库
        title = "LLM Cabinet  ·  AI 项目化文件管理器"
        if cabinet_config is not None and library_root is not None:
            handle = cabinet_config.find(library_root)
            if handle is not None and handle.display_name:
                title = f"LLM Cabinet — {handle.display_name}"
        self.setWindowTitle(title)
        self.resize(1400, 880)
        # 显式给主窗口设图标（QApplication.setWindowIcon 通常会被继承，
        # 这里冗余设置以兼容某些 Qt 版本）
        from PySide6.QtGui import QIcon
        from ..utils import app_icon_path as _app_icon
        _ip = _app_icon()
        if _ip is not None:
            self.setWindowIcon(QIcon(str(_ip)))

        self._current_project_id: int | None = None
        self._current_file_id: int | None = None

        self._build_menubar()
        self._build_toolbar()
        self._build_ui()
        self._install_dnd()
        # 应用默认视图模式
        default_view = self.repo.get_setting("default_view_mode", "grid") or "grid"
        self._set_view_mode(default_view)
        self.refresh_projects()

        # 接入 LLM queue 信号
        if self.llm_queue is not None:
            self.llm_queue.counts_changed.connect(self._on_llm_counts)
            self.llm_queue.suggestions_added.connect(self._on_llm_suggestions_added)
            self.llm_queue.task_failed.connect(self._on_llm_task_failed)
            self._on_llm_counts(self.llm_queue.active_count())

        # 轻量轮询：检测 MCP 是否有新操作（只查 MAX(id) 开销极小）
        self._mcp_last_audit_id: int = 0
        self._mcp_timer = QTimer(self)
        self._mcp_timer.timeout.connect(self._check_mcp_activity)
        self._mcp_timer.start(10_000)  # 每 10 秒
        self._check_mcp_activity()

    # ============================================================ menubar (task #08)
    def _build_menubar(self) -> None:
        """主菜单栏。当前仅含「库」菜单（多库切换）。"""
        if self.cabinet_config is None:
            # 单库模式（理论上不应出现，但保留兜底，避免无菜单/崩溃）
            return

        bar = self.menuBar()
        m_lib = bar.addMenu("库(&L)")

        from PySide6.QtGui import QAction, QKeySequence
        act_switch = QAction("切换库...", self)
        act_switch.setShortcut(QKeySequence("Ctrl+Shift+O"))
        act_switch.triggered.connect(lambda _checked=False: self._lib_switch())
        m_lib.addAction(act_switch)

        act_new = QAction("新建库...", self)
        act_new.setShortcut(QKeySequence("Ctrl+Shift+N"))
        act_new.triggered.connect(lambda _checked=False: self._lib_new())
        m_lib.addAction(act_new)

        act_welcome = QAction("🏠 回到欢迎页...", self)
        act_welcome.setToolTip(
            "关闭当前库并回到欢迎页（应用会重启；当前库不会被删除，只是不再打开）"
        )
        act_welcome.triggered.connect(lambda _checked=False: self._lib_back_to_welcome())
        m_lib.addAction(act_welcome)

        m_lib.addSeparator()

        # 最近打开 — 子菜单（动态构建，捕获快照避免 lambda 闭包问题）
        self._m_recent = m_lib.addMenu("最近打开")
        self._m_recent.aboutToShow.connect(self._lib_rebuild_recent_menu)
        # 菜单不被打开时，仍要在静态构建一次确保占位
        self._lib_rebuild_recent_menu()

        m_lib.addSeparator()

        act_info = QAction("当前库信息...", self)
        act_info.triggered.connect(lambda _checked=False: self._lib_info())
        m_lib.addAction(act_info)

        act_imp = QAction("从其它库导入 API 配置...", self)
        act_imp.triggered.connect(lambda _checked=False: self._lib_import_api())
        m_lib.addAction(act_imp)

        # 「工具」菜单（task #14 + task #11 T3）：库一致性检查 / 备份 / 恢复 / LLM 助手
        m_tools = bar.addMenu("工具(&T)")
        act_wiz = QAction("🪄 LLM 助手...", self)
        act_wiz.triggered.connect(lambda _c=False: self._tools_open_wizards())
        m_tools.addAction(act_wiz)
        m_tools.addSeparator()
        act_check = QAction("🔍 检查库一致性...", self)
        act_check.triggered.connect(lambda _c=False: self._tools_check_consistency())
        m_tools.addAction(act_check)
        m_tools.addSeparator()
        act_backup = QAction("📦 备份此库...", self)
        act_backup.triggered.connect(lambda _c=False: self._tools_backup_library())
        m_tools.addAction(act_backup)
        act_restore = QAction("📥 从备份恢复库...", self)
        act_restore.triggered.connect(lambda _c=False: self._tools_restore_library())
        m_tools.addAction(act_restore)

        # task #28 T3：导入项目包
        m_tools.addSeparator()
        act_import_pkg = QAction("📥 导入项目包...", self)
        act_import_pkg.setShortcut(QKeySequence("Ctrl+I"))
        act_import_pkg.triggered.connect(lambda _c=False: self._tools_import_package())
        m_tools.addAction(act_import_pkg)

    def _lib_rebuild_recent_menu(self) -> None:
        """重建「最近打开」子菜单。每个条目支持右键菜单（移除/删除/改名）。"""
        if self.cabinet_config is None:
            return
        m = self._m_recent
        m.clear()
        from pathlib import Path as _Path
        cur = _Path(self.library_root).resolve() if self.library_root else None
        from PySide6.QtGui import QAction
        for h in self.cabinet_config.recent_libraries:
            display = h.display_name
            is_current = (h.path.resolve() == cur) if cur is not None else False
            text = f"{'● ' if is_current else '   '}{display}    ({h.path})"
            act = QAction(text, self)
            act.setToolTip(str(h.path))
            # 关键：path 通过默认参数捕获，不会被循环变量改写
            act.triggered.connect(
                lambda _checked=False, p=h.path: self._lib_open_recent(p)
            )
            m.addAction(act)

        if self.cabinet_config.recent_libraries:
            m.addSeparator()

        from PySide6.QtGui import QAction as _QA
        act_manage = _QA("管理列表...", self)
        act_manage.triggered.connect(lambda _c=False: self._lib_manage_recent())
        m.addAction(act_manage)

    # ---- 库菜单各操作 ----
    def _lib_switch(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from pathlib import Path as _Path
        d = QFileDialog.getExistingDirectory(self, "选择库目录")
        if not d:
            return
        # 严格边界：「切换库」只打开已存在的库目录（含 .llm-cabinet 标记），
        # 选到空目录 / 普通目录 → 直接报错让用户改走「新建库」，不在此路径里
        # 走"问是否新建"——新建必须经过 task #15 多页向导（要采集描述/字段/视图等）。
        self._lib_open_recent(_Path(d))

    def _lib_back_to_welcome(self) -> None:
        """关闭当前库并回到欢迎页（重启）。

        与"删除当前库 → Welcome 兜底"复用同一个机制：写哨兵
        ``_pending_switch_to = "__welcome__"`` 关主窗口，``main`` 检测到后
        ``cabinet.active_library = None`` + restart，启动期 Welcome 弹出让
        用户重新选库。**不**删除任何磁盘数据；当前库仍在最近列表里、可重新打开。
        """
        from PySide6.QtWidgets import QMessageBox
        ans = QMessageBox.question(
            self, "回到欢迎页",
            "关闭当前库并回到欢迎页？\n\n"
            "应用会重启，当前库不会被删除（仍在最近列表里）。\n"
            "未保存的修改会丢失。",
        )
        if ans != QMessageBox.Yes:
            return
        self._pending_switch_to = "__welcome__"
        self.close()

    def _lib_new(self) -> None:
        """新建一个空库目录（task #15 T1：多页向导，晚建 + 失败 rmtree 回滚）。"""
        from .wizards.new_library_wizard import NewLibraryWizard

        wiz = NewLibraryWizard(self.cabinet_config, parent=self)
        if wiz.exec() != QDialog.Accepted:
            return  # 用户取消（D1 零副作用）
        if wiz.created_root is None:
            return  # 防御
        # 向导内部已经 touch + save 过 cabinet_config；这里只需走重启确认
        label = self.cabinet_config.find(wiz.created_root)
        self._confirm_and_restart_to(
            wiz.created_root,
            label=label.label if label else None,
        )

    def _lib_open_recent(self, path) -> None:
        """打开指定路径的库（来自最近列表 / 切换对话框）。

        严格边界：path 必须已经是有效的 LLM Cabinet 库（含 ``.llm-cabinet``
        标记或 ``cabinet.db``）；不是 → 直接报错引导用户走「新建库」。本方法
        **不会**在任何情况下顺手创建新库 —— 创建走 task #15 的多页向导。
        """
        from PySide6.QtWidgets import QMessageBox
        from ..cabinet import is_library_dir
        from pathlib import Path as _Path
        path = _Path(path)

        if path == _Path(self.library_root):
            QMessageBox.information(self, "提示", "已是当前库。")
            return

        if not is_library_dir(path):
            QMessageBox.warning(
                self, "无法打开",
                f"目录\n  {path}\n不是有效的 LLM Cabinet 库"
                "（缺少 .llm-cabinet 标记 / cabinet.db）。\n\n"
                "如果想在此创建新库，请改用「库 → 新建库...」走完整向导。",
            )
            return

        self.cabinet_config.touch(path)
        self.cabinet_config.save()
        self._confirm_and_restart_to(path)

    def _confirm_and_restart_to(self, path, label: str | None = None) -> None:
        """切换/新建后弹确认，确认后重启。"""
        from PySide6.QtWidgets import QMessageBox
        ans = QMessageBox.question(
            self, "切换库",
            f"切换到库：\n{label or path.name}\n{path}\n\n"
            "应用将重启以加载新库，是否继续？",
        )
        if ans != QMessageBox.Yes:
            return
        # 写入"待切换"标记，main() 在 app.exec() 返回后会 execv 重启
        self._pending_switch_to = path
        self.close()

    def _lib_info(self) -> None:
        """显示当前库信息 + label / 描述 可改。"""
        from PySide6.QtWidgets import (
            QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
            QPlainTextEdit, QPushButton, QVBoxLayout,
        )
        from pathlib import Path as _Path

        n_projects = len(self.repo.list_projects())
        n_files_total = self.repo.conn.execute(
            "SELECT COUNT(*) AS c FROM files"
        ).fetchone()["c"]
        try:
            db_size = _Path(self.db_path).stat().st_size if self.db_path else 0
        except OSError:
            db_size = 0
        # library/ 大小：递归求和（保守）
        lib_size = 0
        try:
            for p in _Path(self.library.root).rglob("*"):
                if p.is_file():
                    lib_size += p.stat().st_size
        except OSError:
            pass

        handle = self.cabinet_config.find(self.library_root) if self.cabinet_config else None
        label = handle.display_name if handle else _Path(self.library_root).name

        # 库级描述（settings.library_description；首次为空，库字段设计助手会写入）
        cur_desc = self.repo.get_setting("library_description", "") or ""

        dlg = QDialog(self)
        dlg.setWindowTitle("当前库信息")
        dlg.setMinimumWidth(520)
        dlg.resize(560, 460)
        v = QVBoxLayout(dlg)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        ed_label = QLineEdit(label)
        form.addRow("名称：", ed_label)
        form.addRow("路径：", QLabel(str(self.library_root)))
        form.addRow("项目数：", QLabel(str(n_projects)))
        form.addRow("文件数：", QLabel(str(n_files_total)))
        form.addRow("数据库大小：", QLabel(_human_size(db_size)))
        form.addRow("library/ 大小：", QLabel(_human_size(lib_size)))
        v.addLayout(form)

        # 库级描述（可编辑多行；提供给 LLM 助手作为额外上下文）
        v.addWidget(QLabel("库描述（可选）："))
        ed_desc = QPlainTextEdit(cur_desc)
        ed_desc.setPlaceholderText(
            "用一段话说明本库管理什么内容、有什么特别约定等。\n"
            "「工具 → 🪄 LLM 助手 → 库字段设计助手」会读取并完善这段描述。"
        )
        ed_desc.setMinimumHeight(120)
        v.addWidget(ed_desc)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("保存")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        if dlg.exec() != QDialog.Accepted:
            return
        new_label = ed_label.text().strip()
        if new_label and new_label != label:
            self.cabinet_config.rename(self.library_root, new_label)
            self.cabinet_config.save()
            # 标题栏与最近菜单刷新
            self.setWindowTitle(f"LLM Cabinet — {new_label}")
        new_desc = ed_desc.toPlainText().strip()
        if new_desc != cur_desc:
            self.repo.set_setting("library_description", new_desc)
            # Sync to cabinet.json so MCP can see it via list_libraries()
            self.cabinet_config.touch(self.library_root, description=new_desc)
            self.cabinet_config.save()

    def _lib_import_api(self) -> None:
        """从其它库读 llm_config 等设置写入当前库。

        提供两种入口（与新建库向导第 4 页保持一致）：
        - 「最近的库」下拉，按 last_opened 倒序，排除当前活动库自身
        - 「浏览其它库目录...」末项 → 弹 ``QFileDialog.getExistingDirectory``，
          要求目录含 ``.llm-cabinet`` 标记
        """
        from PySide6.QtWidgets import (
            QComboBox, QDialog, QDialogButtonBox, QFileDialog, QLabel,
            QMessageBox, QVBoxLayout,
        )
        from ..cabinet import (
            import_settings_from_other_db, is_library_dir, resolve_library_paths,
        )
        from pathlib import Path as _Path

        cur_root = _Path(self.library_root).resolve()

        dlg = QDialog(self)
        dlg.setWindowTitle("从其它库导入 API 配置")
        dlg.setMinimumWidth(520)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(
            "选择要从中导入 LLM 配置 / API Key / 默认 provider / 默认语言的源库："
        ))
        cmb = QComboBox()
        # 近期库（排除当前活动库）
        for h in self.cabinet_config.recent_libraries:
            if h.path.resolve() == cur_root:
                continue
            cmb.addItem(f"{h.display_name}  —  {h.path}", userData=str(h.path))
        cmb.addItem("📂 浏览其它库目录...", userData="__browse__")
        v.addWidget(cmb)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        def _on_activated(index: int) -> None:
            if cmb.itemData(index) != "__browse__":
                return
            d = QFileDialog.getExistingDirectory(
                dlg, "选择要导入配置的库目录（必须含 .llm-cabinet 标记）",
            )
            prev_idx = max(0, index - 1) if cmb.count() > 1 else 0
            if not d:
                cmb.setCurrentIndex(prev_idx)
                return
            path = _Path(d)
            if path.resolve() == cur_root:
                QMessageBox.information(
                    dlg, "提示", "请选择**其它**库的目录（不能是当前库）。",
                )
                cmb.setCurrentIndex(prev_idx)
                return
            if not is_library_dir(path):
                QMessageBox.warning(
                    dlg, "不是有效的库目录",
                    f"目录 {path} 缺少 .llm-cabinet 标记，无法识别为 LLM Cabinet 库。",
                )
                cmb.setCurrentIndex(prev_idx)
                return
            target_str = str(path)
            for i in range(cmb.count()):
                if cmb.itemData(i) == target_str:
                    cmb.setCurrentIndex(i)
                    return
            browse_idx = cmb.count() - 1  # "浏览..." 是末项
            cmb.insertItem(
                browse_idx, f"{path.name}  —  {path}", userData=target_str,
            )
            cmb.setCurrentIndex(browse_idx)

        cmb.activated.connect(_on_activated)

        if dlg.exec() != QDialog.Accepted:
            return
        data = cmb.currentData()
        if not isinstance(data, str) or not data or data == "__browse__":
            return  # 没选有效目标，静默退出
        src_root = _Path(data)
        try:
            src_db, _ = resolve_library_paths(src_root)
        except Exception:  # noqa: BLE001
            QMessageBox.warning(self, "无法解析库", f"无法从 {src_root} 解析出 db 路径。")
            return
        if src_db.resolve() == _Path(self.db_path).resolve():
            QMessageBox.information(self, "提示", "请选择**其它**库（不能是当前库）。")
            return

        # 读出 llm_config + 默认 provider / 默认语言
        keys = ["llm_config", "llm_default_provider", "llm_default_language"]
        imported = import_settings_from_other_db(src_db, keys)
        if not imported:
            QMessageBox.warning(
                self, "未读到配置",
                "未能从该库读取到 llm_config 等设置，文件可能不可读或格式不符。",
            )
            return

        # 二次确认
        keys_str = "\n".join(f"  • {k}" for k in imported.keys())
        ans = QMessageBox.question(
            self, "确认导入",
            f"将把以下 {len(imported)} 项设置写入当前库（覆盖同名项）：\n{keys_str}\n\n确认？",
        )
        if ans != QMessageBox.Yes:
            return
        for k, val in imported.items():
            self.repo.set_setting(k, val)
        QMessageBox.information(self, "完成", f"已导入 {len(imported)} 项。")

    def _release_active_db_resources(self) -> None:
        """关闭当前库的所有文件句柄，让 sqlite db / WAL 边车 文件可被删除。

        Windows 不允许删占用中的文件；当用户在主界面里"删除整个库"操作的目标恰好
        是**当前库**时，必须先调用本方法关掉 ``self.repo.conn`` 才能让 rmtree
        / unlink 成功。

        操作内容：
        1. 通知 LLM worker 线程退出（带 2 秒 join 等它真正回收）
        2. 关闭 ``self.repo.conn``；后续任何对 ``self.repo`` 的访问都会抛错
        3. （可选）把对应字段置 ``None`` 让悬垂访问尽早失败

        本方法**只应在即将关主窗口的删除流程里调用**。一般操作不要碰它。
        """
        try:
            if self.llm_queue is not None:
                self.llm_queue.stop(join_timeout=2.0)
        except Exception:
            pass
        try:
            if self.repo is not None and self.repo.conn is not None:
                self.repo.conn.close()
        except Exception:
            pass

    def _lib_manage_recent(self) -> None:
        """最近列表管理对话框（切换 / 移除 / 删除 / 改名）。"""
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtGui import QAction as _QA
        from PySide6.QtWidgets import (
            QDialog, QDialogButtonBox, QInputDialog, QListWidget, QListWidgetItem,
            QMenu, QMessageBox, QPushButton, QVBoxLayout,
        )
        from pathlib import Path as _Path

        dlg = QDialog(self)
        dlg.setWindowTitle("管理最近打开的库")
        dlg.resize(600, 380)
        v = QVBoxLayout(dlg)
        lst = QListWidget()
        lst.setContextMenuPolicy(_Qt.CustomContextMenu)
        v.addWidget(lst)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        # 左侧附加"切换到选中库"按钮（仅对非当前库可用）
        btn_switch = QPushButton("🔀 切换到选中库")
        btn_switch.setEnabled(False)
        bb.addButton(btn_switch, QDialogButtonBox.ActionRole)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        v.addWidget(bb)

        cur = _Path(self.library_root).resolve()

        def _refresh():
            lst.clear()
            for h in self.cabinet_config.recent_libraries:
                tag = " ●(当前)" if h.path.resolve() == cur else ""
                it = QListWidgetItem(f"{h.display_name}{tag}\n  {h.path}")
                it.setData(_Qt.UserRole, str(h.path))
                lst.addItem(it)
            _refresh_switch_btn()

        def _refresh_switch_btn():
            it = lst.currentItem()
            if it is None:
                btn_switch.setEnabled(False)
                return
            path = _Path(str(it.data(_Qt.UserRole)))
            btn_switch.setEnabled(path.resolve() != cur)

        def _do_switch(p):
            """关闭本对话框，再走标准的"校验 + 确认 + 重启"流程切到目标库。

            统一入口走 ``_lib_open_recent`` 而不是直奔 ``_confirm_and_restart_to``，
            目的是让"管理列表里的切换"享受与"最近打开二级菜单 / 切换库..."完全
            一致的严格边界（如果选中项的目录已被删除 / 缺少 .llm-cabinet 标记 /
            无 cabinet.db，也会被拒绝并明确报错而不是无声地切回原库）。
            """
            dlg.accept()
            self._lib_open_recent(p)

        def _on_switch_clicked():
            it = lst.currentItem()
            if it is None:
                return
            path = _Path(str(it.data(_Qt.UserRole)))
            if path.resolve() == cur:
                return
            _do_switch(path)

        btn_switch.clicked.connect(_on_switch_clicked)
        lst.currentItemChanged.connect(lambda *_a: _refresh_switch_btn())
        # 双击列表项 = 切换（与按钮等效）
        lst.itemDoubleClicked.connect(lambda it: (
            _do_switch(_Path(str(it.data(_Qt.UserRole))))
            if _Path(str(it.data(_Qt.UserRole))).resolve() != cur else None
        ))
        _refresh()

        def _on_menu(pos):
            it = lst.itemAt(pos)
            if it is None:
                return
            path = _Path(str(it.data(_Qt.UserRole)))
            is_current = (path.resolve() == cur)
            menu = QMenu(dlg)
            a_sw = _QA("🔀 切换到此库", dlg)
            a_sw.setEnabled(not is_current)
            a_sw.triggered.connect(lambda _c=False: _do_switch(path))
            menu.addAction(a_sw)
            menu.addSeparator()

            # 「从列表移除」对当前库也可用 —— 移除 = 关闭后走 Welcome 重选
            a_rm = _QA("从列表移除", dlg)
            a_rm.triggered.connect(lambda _c=False: _remove_from_list(path, is_current))
            menu.addAction(a_rm)

            # 「删除整个库」对当前库也可用 —— 删完后关闭主窗口走 Welcome 兜底
            a_del = _QA("删除整个库...", dlg)
            a_del.triggered.connect(lambda _c=False: _delete_lib(path, is_current))
            menu.addAction(a_del)

            menu.addSeparator()

            a_ren = _QA("改名...", dlg)
            a_ren.triggered.connect(lambda _c=False: _rename_lib(path))
            menu.addAction(a_ren)
            menu.exec(lst.viewport().mapToGlobal(pos))

        def _remove_from_list(p, is_current: bool):
            """从最近列表移除（不动磁盘）。当前库被移除 → 关主窗口走 Welcome。"""
            if is_current:
                ans = QMessageBox.question(
                    dlg, "从列表移除当前库",
                    "当前正在使用这个库。从列表移除后，应用会**重启并回到欢迎页**\n"
                    "让你重新选择库（库目录与文件都不会被删除，只是从最近列表移除）。\n\n"
                    "继续？",
                )
                if ans != QMessageBox.Yes:
                    return
            self.cabinet_config.remove(p)
            self.cabinet_config.save()
            if is_current:
                # 关闭管理对话框 → 设置兜底标志 → 关主窗口 → main 检测后 restart 走 Welcome
                dlg.accept()
                self._pending_switch_to = "__welcome__"
                self.close()
                return
            _refresh()

        def _rename_lib(p):
            handle = self.cabinet_config.find(p)
            cur_label = handle.display_name if handle else p.name
            new_label, ok = QInputDialog.getText(
                dlg, "改名", "新名称：", text=cur_label,
            )
            if not ok or not new_label.strip():
                return
            self.cabinet_config.rename(p, new_label.strip())
            self.cabinet_config.save()
            _refresh()
            if p.resolve() == cur:
                self.setWindowTitle(f"LLM Cabinet — {new_label.strip()}")

        def _delete_lib(p, is_current: bool):
            """删除整个库的二次/三次确认流程。

            额外保护：
            - 库自身条目（cabinet.db / library/ / .llm-cabinet / cabinet.v*.bak / db-wal/-shm）
              在"一并删除"模式下被 rmtree
            - **软件全局文件**（cabinet.json）任何模式下都保留（``delete_library_all``）
            - 用户外来内容（笔记 / 备份等）发现存在时强制让用户选「保留」/「一并删」
            - **当前库**也允许删；删完后 ``_pending_switch_to = "__welcome__"`` 关
              主窗口，由 main 重启进入 Welcome 兜底
            """
            from ..cabinet import (
                delete_library_all, delete_library_owned_only,
                scan_library_for_deletion,
            )
            handle = self.cabinet_config.find(p)
            display = handle.display_name if handle else p.name

            # 第 1 步：列出代价 + 概览
            scan = scan_library_for_deletion(p)
            extras = []
            if scan.foreign:
                extras.append(
                    f"⚠ 目录下还有 {len(scan.foreign)} 项非库内容"
                    f"（{_human_size(scan.foreign_size)}），下一步需要你选择如何处理。"
                )
            else:
                extras.append("（目录下没有非库内容）")
            if is_current:
                extras.append(
                    "⚠ 这是**当前库**——删除完成后应用会重启并回到欢迎页。"
                )
            ans1 = QMessageBox.warning(
                dlg, "确认删除（1/2）",
                f"将删除库『{display}』：\n\n{p}\n\n"
                f"库数据占用：{_human_size(scan.owned_size)}\n"
                + "\n".join(extras)
                + "\n\n此操作**不可恢复**。继续？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ans1 != QMessageBox.Yes:
                return

            # 第 2 步（仅当存在外来内容）：选删除模式
            mode = "all"  # "owned" = 仅删库数据保留外来；"all" = 一并删除（保留 cabinet.json 等软件全局）
            if scan.foreign:
                mode = _ask_delete_mode(dlg, p, scan)
                if mode is None:
                    return  # 用户取消

            # 第 3 步：要求输入 label 作为最终确认
            typed, ok = QInputDialog.getText(
                dlg, "确认删除（2/2）",
                f"请输入库的名称『{display}』以确认删除：",
            )
            if not ok or typed.strip() != display:
                QMessageBox.information(dlg, "已取消", "名称不匹配，取消删除。")
                return

            # 第 4 步（仅当前库）：在动手删之前释放当前库的所有文件句柄。
            # Windows 下 sqlite db / WAL 边车被占用时无法删除；必须先 stop
            # LLM worker + close repo.conn，否则 rmtree / unlink 会在
            # cabinet.db / cabinet.db-wal / cabinet.db-shm 上失败并报
            # "另一个进程正在使用此文件"。
            if is_current:
                self._release_active_db_resources()

            # 执行
            if mode == "owned":
                failures = delete_library_owned_only(p)
                if failures:
                    msg = "\n".join(f"• {fp.name}：{err}" for fp, err in failures[:10])
                    QMessageBox.warning(
                        dlg, "部分删除失败",
                        f"以下库内条目未能删除（其余已成功）：\n{msg}",
                    )
                self.cabinet_config.remove(p)
                self.cabinet_config.save()
                if is_current:
                    dlg.accept()
                    self._pending_switch_to = "__welcome__"
                    self.close()
                    return
                _refresh()
                QMessageBox.information(
                    dlg, "完成",
                    f"已删除库数据，外来文件保留在：\n{p}",
                )
                return

            # mode == "all"：删 owned + foreign，保留 app_global（如 cabinet.json）
            failures = delete_library_all(p)
            if failures:
                msg = "\n".join(f"• {fp.name}：{err}" for fp, err in failures[:10])
                QMessageBox.critical(
                    dlg, "删除失败",
                    f"以下条目未能删除：\n{msg}",
                )
                return
            self.cabinet_config.remove(p)
            self.cabinet_config.save()
            if is_current:
                dlg.accept()
                self._pending_switch_to = "__welcome__"
                self.close()
                return
            _refresh()

        lst.customContextMenuRequested.connect(_on_menu)
        dlg.exec()

    # ============================================================ 工具菜单（task #14）
    def _tools_open_wizards(self) -> None:
        """工具 → 🪄 LLM 助手...（task #11 T3）。"""
        from .wizard_list_dialog import WizardListDialog
        dlg = WizardListDialog(self.repo, self.library, parent=self)
        dlg.exec()
        # 任一助手实际写过库 → 刷新主界面（字段列变化会影响列表）
        if dlg.any_applied():
            self.refresh_projects()
            # task #15 T2 D4：跑过助手 → 永久隐藏首次引导横幅
            self._on_user_action_dismiss_banner()

    def _tools_check_consistency(self) -> None:
        """库一致性检查（task #14 T1）。"""
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import (
            QButtonGroup, QDialog, QDialogButtonBox, QHeaderView, QLabel,
            QMessageBox, QProgressDialog, QRadioButton, QTableWidget,
            QTableWidgetItem, QVBoxLayout,
        )
        from ..library_check import (
            apply_consistency_action, run_consistency_check,
        )

        prog = QProgressDialog("正在扫描库...", "取消", 0, 100, self)
        prog.setWindowTitle("检查库一致性")
        prog.setWindowModality(_Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.show()

        def _on_prog(done, total, _name):
            if total > 0:
                prog.setMaximum(total)
                prog.setValue(done)

        try:
            rep = run_consistency_check(self.repo, self.library, progress=_on_prog)
        except Exception as e:
            prog.close()
            QMessageBox.critical(self, "检查失败", str(e))
            return
        prog.close()

        # 报告对话框
        dlg = QDialog(self)
        dlg.setWindowTitle("库一致性检查报告")
        dlg.resize(720, 480)
        v = QVBoxLayout(dlg)

        summary = QLabel(
            f"扫描总文件数：{rep.total_files}<br>"
            f"📦 仓储文件：完整 {rep.n_storage - len(rep.storage_missing)} / {rep.n_storage}"
            + (f"，<b>失效 {len(rep.storage_missing)}</b>" if rep.storage_missing else "")
            + f"<br>🔗 链接文件：完整 {rep.n_link - len(rep.link_missing)} / {rep.n_link}"
            + (f"，<b>失效 {len(rep.link_missing)}</b>" if rep.link_missing else "")
        )
        summary.setTextFormat(_Qt.RichText)
        v.addWidget(summary)

        if rep.total_missing == 0:
            v.addWidget(QLabel("🎉 全部文件物理存在，未发现失效项。"))
            bb = QDialogButtonBox(QDialogButtonBox.Close)
            bb.rejected.connect(dlg.reject)
            bb.accepted.connect(dlg.accept)
            v.addWidget(bb)
            dlg.exec()
            return

        # 失效清单表
        tbl = QTableWidget(rep.total_missing, 4)
        tbl.setHorizontalHeaderLabels(["项目", "文件", "存储模式", "原始路径"])
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setAlternatingRowColors(True)
        h = tbl.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Interactive)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.Fixed)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        tbl.setColumnWidth(0, 140)
        tbl.setColumnWidth(1, 180)
        tbl.setColumnWidth(2, 80)
        for r, entry in enumerate(rep.storage_missing + rep.link_missing):
            tbl.setItem(r, 0, QTableWidgetItem(entry.project_title))
            tbl.setItem(r, 1, QTableWidgetItem(entry.file_name))
            tbl.setItem(r, 2, QTableWidgetItem(entry.storage_label))
            tbl.setItem(r, 3, QTableWidgetItem(entry.resolved))
        v.addWidget(tbl, 1)

        v.addWidget(QLabel("处理：选择如何对这些失效文件做后续处理"))
        rb_noop = QRadioButton("仅查看，不动数据")
        rb_mark = QRadioButton("标记为缺失（文件表显示 ⚠ 图标）")
        rb_del = QRadioButton("从项目中移除（保留磁盘上别处的文件）")
        rb_noop.setChecked(True)
        bg = QButtonGroup(dlg)
        bg.addButton(rb_noop); bg.addButton(rb_mark); bg.addButton(rb_del)
        v.addWidget(rb_noop); v.addWidget(rb_mark); v.addWidget(rb_del)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("应用")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        if dlg.exec() != QDialog.Accepted:
            return
        action = "noop"
        if rb_mark.isChecked():
            action = "mark"
        elif rb_del.isChecked():
            action = "delete"
        if action == "noop":
            return
        n_marked, n_deleted = apply_consistency_action(self.repo, rep, action)
        QMessageBox.information(
            self, "完成",
            f"已处理：标记 {n_marked} 个 / 移除 {n_deleted} 个。",
        )
        self.refresh_projects()

    def _tools_backup_library(self) -> None:
        """备份当前库（task #14 T2）。"""
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import (
            QFileDialog, QMessageBox, QProgressDialog,
        )
        from ..cabinet import scan_library_for_deletion
        from ..library_check import backup_library
        from datetime import datetime as _dt
        from pathlib import Path as _Path

        if self.library_root is None:
            QMessageBox.warning(self, "不可用", "当前库目录未知，无法备份。")
            return

        # 扫一下目录里的"外来内容"（用户自己放进去的非库文件），
        # 让用户决定要不要一并打包；默认包含
        scan = scan_library_for_deletion(_Path(self.library_root))
        include_foreign = True
        if scan.foreign:
            ans = QMessageBox.question(
                self, "目录里有外来文件",
                "当前库目录下还有 "
                f"<b>{len(scan.foreign)}</b> 项不是库自身的内容"
                f"（约 {_human_size(scan.foreign_size)}）：\n\n"
                + "\n".join(
                    f"  • {p.name}" for p in scan.foreign[:8]
                )
                + (f"\n  ……还有 {len(scan.foreign) - 8} 项"
                   if len(scan.foreign) > 8 else "")
                + "\n\n是否一并打包到备份 zip？\n\n"
                "<b>是</b>：得到完整目录快照（推荐——恢复后能拿回这些文件）\n"
                "<b>否</b>：只打包库自身（cabinet.db / library/ / 标记文件等），"
                "备份体积更小",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if ans == QMessageBox.Cancel:
                return
            include_foreign = (ans == QMessageBox.Yes)

        # 默认文件名：<libname>-YYYYMMDD-HHMMSS[-slim].zip
        handle = (
            self.cabinet_config.find(self.library_root)
            if self.cabinet_config else None
        )
        label = handle.display_name if handle else _Path(self.library_root).name
        ts = _dt.now().strftime("%Y%m%d-%H%M%S")
        slim_tag = "" if include_foreign else "-slim"
        default_name = f"{label}-{ts}{slim_tag}.zip"
        last_dir = self.repo.get_setting("last_backup_dir", "") or str(_Path.home())
        target, _ = QFileDialog.getSaveFileName(
            self, "选择备份保存位置",
            str(_Path(last_dir) / default_name),
            "ZIP 备份 (*.zip)",
        )
        if not target:
            return

        # 闪存 WAL 到主 db（如果用了 WAL 模式）
        try:
            self.repo.conn.execute("PRAGMA wal_checkpoint(FULL)")
        except Exception:
            pass

        prog = QProgressDialog("正在打包库目录...", None, 0, 0, self)
        prog.setWindowTitle("备份库")
        prog.setWindowModality(_Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.show()
        try:
            out = backup_library(
                _Path(self.library_root), _Path(target),
                include_foreign=include_foreign,
            )
        except Exception as e:
            prog.close()
            QMessageBox.critical(self, "备份失败", str(e))
            return
        prog.close()
        try:
            self.repo.set_setting("last_backup_dir", str(_Path(target).parent))
        except Exception:
            pass
        QMessageBox.information(
            self, "完成", f"备份完成：\n{out}\n大小：{_human_size(out.stat().st_size)}",
        )

    def _tools_restore_library(self) -> None:
        """从备份恢复一个库（解到新目录，然后用『切换库』流程打开）。"""
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import (
            QFileDialog, QMessageBox, QProgressDialog,
        )
        from ..library_check import restore_library
        from pathlib import Path as _Path

        zip_path, _ = QFileDialog.getOpenFileName(
            self, "选择备份 zip", "", "ZIP 备份 (*.zip);;所有文件 (*)",
        )
        if not zip_path:
            return
        target_dir = QFileDialog.getExistingDirectory(
            self, "选择解压目标目录（须为空目录）",
        )
        if not target_dir:
            return

        # 校验目标空
        tp = _Path(target_dir)
        if any(tp.iterdir()):
            QMessageBox.warning(
                self, "目录不为空",
                f"目标目录非空：{target_dir}\n请选一个空目录或新建目录。",
            )
            return

        prog = QProgressDialog("正在解压...", None, 0, 0, self)
        prog.setWindowTitle("恢复库")
        prog.setWindowModality(_Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.show()
        try:
            root = restore_library(_Path(zip_path), tp)
        except Exception as e:
            prog.close()
            QMessageBox.critical(self, "恢复失败", str(e))
            return
        prog.close()

        ans = QMessageBox.question(
            self, "恢复完成",
            f"已恢复到：\n{root}\n\n"
            "是否立即切换到这个库？应用将重启以加载新库。",
        )
        if ans == QMessageBox.Yes and self.cabinet_config is not None:
            self.cabinet_config.touch(root)
            self.cabinet_config.save()
            # 直接走 _confirm_and_restart_to 的"重启"分支，不再二次弹"切换库"确认框
            # （恢复完成的对话框文案已经把"应用将重启"说明了，再多弹一次冗余）
            self._pending_switch_to = root
            self.close()

    def _tools_import_package(self) -> None:
        """导入项目包（ZIP 或目录）（task #28 T3）。"""
        from PySide6.QtWidgets import QFileDialog, QDialog
        from pathlib import Path as _Path

        path, _ = QFileDialog.getOpenFileName(
            self, "选择项目包 ZIP", "",
            "项目包 (*.zip);;所有文件 (*)",
        )
        if not path:
            path = QFileDialog.getExistingDirectory(
                self, "选择项目包目录",
            )
        if not path:
            return

        # 复用批量导入流程
        self._run_batch_folder_import([path])

    # ============================================================ toolbar
    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)

        def make_action(icon: str, text: str, slot, shortcut: str | None = None) -> QAction:
            a = QAction(f"{icon}  {text}", self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(shortcut)
            return a

        tb.addAction(make_action("＋", "新建项目", self.action_new_project, "Ctrl+N"))
        tb.addAction(make_action("📥", "添加文件", self.action_add_files, "Ctrl+I"))
        tb.addAction(make_action("✎", "编辑项目", self.action_edit_project, "F2"))
        tb.addAction(make_action("🗑", "删除项目", self.action_delete_project))
        tb.addAction(make_action("📤", "导出项目", self.action_export_project))

        # 右侧推到末端的设置按钮
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        tb.addAction(make_action("⚙", "设置", self.action_open_settings, "Ctrl+,"))

    # ============================================================ UI
    def _build_ui(self) -> None:
        # ============================================================
        # 左：标签筛选树
        # ============================================================
        self.tag_tree = TagTree()
        self.tag_tree.attach_setting_io(
            setter=self.repo.set_setting,
            getter=self.repo.get_setting,
        )
        self.tag_tree.filter_changed.connect(self._on_tag_filter_changed)
        self.tag_tree.projects_dropped_on_tag.connect(self._on_projects_dropped_on_tag)
        self._current_filter_kind: str = "all"
        self._current_filter_value: str = ""

        left = QWidget()
        left.setObjectName("SidePanel")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(8, 10, 4, 10)
        ll.setSpacing(8)
        lbl_lib = QLabel("标签筛选")
        lbl_lib.setProperty("h2", True)
        ll.addWidget(lbl_lib)
        ll.addWidget(self.tag_tree, 1)

        # ============================================================
        # 中：搜索 + 视图切换 + 项目区 + DropZone
        # ============================================================
        # —— 顶部搜索条 + 视图切换
        self.search_box = QLineEdit()
        self.search_box.setObjectName("SearchBox")
        self.search_box.setPlaceholderText("🔍  搜索：三体 / tag:科幻 AND rating:>=4")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self.refresh_projects)
        self.search_box.textChanged.connect(lambda _text: self._search_timer.start())

        self.btn_search_menu = QToolButton()
        self.btn_search_menu.setText("⌄")
        self.btn_search_menu.setToolTip("搜索历史与收藏")
        self.btn_search_menu.clicked.connect(self._show_search_menu)

        self.btn_save_search = QToolButton()
        self.btn_save_search.setText("☆")
        self.btn_save_search.setToolTip("收藏当前搜索表达式")
        self.btn_save_search.clicked.connect(self._save_current_search)

        self.btn_search_all = QToolButton()
        self.btn_search_all.setText("全库")
        self.btn_search_all.setToolTip("搜索全部项目（忽略左侧筛选）")
        self.btn_search_all.setCheckable(True)
        self.btn_search_all.clicked.connect(lambda _checked=False: self.refresh_projects())

        self.btn_view_grid = QToolButton()
        self.btn_view_grid.setText("▦")
        self.btn_view_grid.setToolTip("网格视图")
        self.btn_view_grid.setCheckable(True)
        self.btn_view_grid.setChecked(True)
        self.btn_view_list = QToolButton()
        self.btn_view_list.setText("≡")
        self.btn_view_list.setToolTip("列表视图")
        self.btn_view_list.setCheckable(True)
        self.btn_view_grid.clicked.connect(lambda: self._set_view_mode("grid"))
        self.btn_view_list.clicked.connect(lambda: self._set_view_mode("list"))

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.addWidget(self.search_box, 1)
        top_row.addWidget(self.btn_search_menu)
        top_row.addWidget(self.btn_save_search)
        top_row.addWidget(self.btn_search_all)
        top_row.addSpacing(6)
        top_row.addWidget(self.btn_view_grid)
        top_row.addWidget(self.btn_view_list)

        # —— 项目计数
        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 0)
        self.lbl_filter_title = QLabel("全部项目")
        self.lbl_filter_title.setProperty("h2", True)
        info_row.addWidget(self.lbl_filter_title)
        info_row.addStretch(1)
        self.lbl_count = QLabel("")
        self.lbl_count.setProperty("muted", True)
        info_row.addWidget(self.lbl_count)

        # —— 项目视图（网格 + 列表，共享同一 model 与 selection model）
        self.proj_model = ProjectModel()

        # 网格 (QListView)
        self.proj_view = QListView()
        self.proj_view.setObjectName("ProjectGrid")
        self.proj_view.setModel(self.proj_model)
        self.proj_view.setModelColumn(0)
        self._proj_delegate = ProjectCardDelegate(self.proj_view)
        self.proj_view.setItemDelegate(self._proj_delegate)
        self.proj_view.setViewMode(QListView.IconMode)
        self.proj_view.setResizeMode(QListView.Adjust)
        self.proj_view.setMovement(QListView.Static)
        self.proj_view.setSelectionMode(QAbstractItemView.ExtendedSelection)  # task #25: 多选
        self.proj_view.setUniformItemSizes(True)
        self.proj_view.setSpacing(4)
        self.proj_view.setMouseTracking(True)
        self.proj_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.proj_view.customContextMenuRequested.connect(self._project_context_menu)
        self.proj_view.doubleClicked.connect(lambda _i: self.action_edit_project())

        # 列表 (QTableView)
        self.proj_table = QTableView()
        self.proj_table.setObjectName("ProjectTable")
        self.proj_table.setModel(self.proj_model)
        self.proj_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.proj_table.setSelectionMode(QAbstractItemView.ExtendedSelection)  # task #25: 多选
        self.proj_table.setShowGrid(False)
        self.proj_table.setAlternatingRowColors(True)
        self.proj_table.setSortingEnabled(False)
        # 列总宽超出视口时显示水平滚动条
        self.proj_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.proj_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.proj_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.proj_table.verticalHeader().setVisible(False)
        self.proj_table.verticalHeader().setDefaultSectionSize(28)
        self.proj_table.horizontalHeader().setStretchLastSection(False)
        self.proj_table.horizontalHeader().setHighlightSections(False)
        self.proj_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.proj_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.proj_table.customContextMenuRequested.connect(self._project_context_menu)
        self.proj_table.doubleClicked.connect(lambda _i: self.action_edit_project())

        # 让两个视图共享 selection model（同一行选中状态同步）
        self.proj_table.setSelectionModel(self.proj_view.selectionModel())
        self.proj_view.selectionModel().currentChanged.connect(self._on_project_selected)

        # DnD helper（网格视图）
        self._proj_dnd = ProjectViewDnD(self.proj_view, ProjectModel.RoleId)
        self._proj_dnd.files_dropped_on_item.connect(self._on_dropped_on_project)
        self._proj_dnd.drag_hover_changed.connect(self._on_drag_hover_changed)
        # DnD helper（列表视图）
        self._proj_table_dnd = ProjectViewDnD(self.proj_table, ProjectModel.RoleId)
        self._proj_table_dnd.files_dropped_on_item.connect(self._on_dropped_on_project)

        # 切换器
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.proj_view)    # index 0 = grid
        self.view_stack.addWidget(self.proj_table)   # index 1 = list

        # 列显示状态：默认显示集
        # 列由 refresh_projects 中根据 fields schema 自动构建

        # —— DropZone
        self.drop_zone = DropZone()

        center = QWidget()
        center.setObjectName("CenterPanel")
        cl = QVBoxLayout(center)
        cl.setContentsMargins(10, 10, 10, 10)
        cl.setSpacing(8)
        cl.addLayout(top_row)
        cl.addLayout(info_row)

        # 首次进入引导横幅（task #15 T2）
        from .first_run_banner import FirstRunBanner
        self.first_run_banner = FirstRunBanner.install(
            cl, self.repo,
            on_run_wizard=self._tools_open_wizards,
            on_open_settings_fields=self.action_open_settings_fields,
        )

        cl.addWidget(self.view_stack, 1)
        cl.addWidget(self.drop_zone)

        # ============================================================
        # 右：上=预览 / 下=详情卡片 + 文件表
        # ============================================================
        right = self._build_right_panel()
        center.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)

        # ============================================================ 拼装
        splitter = QSplitter(Qt.Horizontal)
        self._main_splitter = splitter
        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right)
        # 默认宽度比 = 1 : 4 : 2。窗口大小变化时只让中间项目区伸缩；
        # 右侧详情/预览栏保持用户当前拖拽出的宽度，不随选中项目内容变化。
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes(self._load_main_splitter_sizes())
        splitter.splitterMoved.connect(self._on_main_splitter_moved)
        splitter.setHandleWidth(1)

        root = QWidget()
        root.setObjectName("CentralRoot")
        rl = QHBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(splitter)
        self.setCentralWidget(root)

        sb = QStatusBar()
        self.setStatusBar(sb)
        # 状态栏右侧：MCP 操作计数（点击打开记录面板）
        self.lbl_mcp_count = QLabel("📋 MCP 操作: 0")
        self.lbl_mcp_count.setStyleSheet(
            "QLabel{padding:2px 10px;border-radius:4px;}"
            "QLabel:hover{background:rgba(77,171,247,0.15);color:#74c0fc;}"
        )
        self.lbl_mcp_count.setCursor(Qt.PointingHandCursor)
        self.lbl_mcp_count.setToolTip("点击查看 MCP 操作记录")
        self.lbl_mcp_count.mousePressEvent = lambda _ev: self.action_open_mcp_audit()
        sb.addPermanentWidget(self.lbl_mcp_count)

        # 状态栏右侧：LLM 任务计数（点击打开任务面板）
        self.lbl_llm_count = QLabel("⚡ LLM 任务: 0")
        self.lbl_llm_count.setStyleSheet(
            "QLabel{padding:2px 10px;border-radius:4px;}"
            "QLabel:hover{background:rgba(77,171,247,0.15);color:#74c0fc;}"
        )
        self.lbl_llm_count.setCursor(Qt.PointingHandCursor)
        self.lbl_llm_count.setToolTip("点击打开 LLM 任务面板")
        self.lbl_llm_count.mousePressEvent = lambda _ev: self.action_open_llm_tasks()
        sb.addPermanentWidget(self.lbl_llm_count)

    def _build_right_panel(self) -> QWidget:
        # ============================================================
        # 右上：标题 + 描述 + 预览
        # ============================================================
        self.lbl_meta_title = QLabel("（未选择项目）")
        self.lbl_meta_title.setProperty("h1", True)
        self.lbl_meta_title.setWordWrap(True)
        self.lbl_meta_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        self.lbl_meta_desc = QLabel("")
        self.lbl_meta_desc.setProperty("muted", True)
        self.lbl_meta_desc.setWordWrap(True)
        self.lbl_meta_desc.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        # 限制为 2~3 行：用最大高度限制 + 文本省略
        fm = self.lbl_meta_desc.fontMetrics()
        self.lbl_meta_desc.setMaximumHeight(fm.lineSpacing() * 3 + 4)
        self.lbl_meta_desc.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.preview = PreviewPanel()

        top = QWidget()
        top.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        top_l = QVBoxLayout(top)
        top_l.setContentsMargins(12, 10, 12, 6)
        top_l.setSpacing(4)
        top_l.addWidget(self.lbl_meta_title)
        top_l.addWidget(self.lbl_meta_desc)
        top_l.addSpacing(6)
        top_l.addWidget(self.preview, 1)

        # ============================================================
        # 右下：文件列表
        # ============================================================
        files_header = QHBoxLayout()
        lbl_files = QLabel("文件")
        lbl_files.setProperty("h2", True)
        files_header.addWidget(lbl_files)
        self.lbl_files_hint = QLabel("")
        self.lbl_files_hint.setProperty("hint", True)
        files_header.addWidget(self.lbl_files_hint)
        files_header.addStretch(1)

        # task #04：文件来源过滤 toggle（仅用户文件 / 显示所有）
        self._btn_origin_filter = QToolButton()
        self._btn_origin_filter.setText("👤")
        self._btn_origin_filter.setToolTip("仅显示用户文件")
        self._btn_origin_filter.setCheckable(True)
        self._btn_origin_filter.setChecked(False)
        self._btn_origin_filter.setVisible(False)  # 项目无 generated 文件时隐藏
        self._btn_origin_filter.clicked.connect(self._on_origin_filter_toggled)
        files_header.addWidget(self._btn_origin_filter)

        self._btn_detach_files = QPushButton("⇱")
        self._btn_detach_files.setToolTip("弹出为独立窗口")
        self._btn_detach_files.setFixedSize(28, 28)
        self._btn_detach_files.setProperty("flat", True)
        self._btn_detach_files.clicked.connect(self._toggle_files_detach)
        files_header.addWidget(self._btn_detach_files)

        # task #31b: 视图模式切换（树形/扁平）
        self._btn_view_mode = QPushButton("🌲")
        self._btn_view_mode.setToolTip("切换视图：树形 / 扁平")
        self._btn_view_mode.setFixedSize(28, 28)
        self._btn_view_mode.setProperty("flat", True)
        self._btn_view_mode.clicked.connect(self._toggle_files_view_mode)
        files_header.addWidget(self._btn_view_mode)

        btn_add_file = QPushButton("＋  添加文件")
        btn_add_file.setProperty("primary", True)
        btn_add_file.clicked.connect(self.action_add_files)
        files_header.addWidget(btn_add_file)

        self.tbl_files = QTreeWidget()
        self.tbl_files.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.tbl_files.setHeaderLabels([c.label for c in FILES_COLUMNS])
        self._files_dnd = FilesTableDnD(self.tbl_files)
        self._files_dnd.files_dropped.connect(self._on_dropped_on_files_table)
        self._files_dnd.files_moved.connect(self._on_files_moved)
        h = self.tbl_files.header()
        # 所有列都 Interactive：允许用户自由拖宽
        for i, _col in enumerate(FILES_COLUMNS):
            h.setSectionResizeMode(i, QHeaderView.Interactive)
        h.setStretchLastSection(False)
        self.tbl_files.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.tbl_files.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # 列宽偏好在 _show_project 中按项目加载
        h.setSectionsMovable(False)
        # 表头右键菜单：切换列可见性
        h.setContextMenuPolicy(Qt.CustomContextMenu)
        h.customContextMenuRequested.connect(self._files_header_context_menu)
        # 列宽变化时保存到 project_settings
        h.sectionResized.connect(self._on_files_section_resized)
        self.tbl_files.setAlternatingRowColors(True)
        # 文件名过长时直接截断显示，不显示省略号
        self.tbl_files.setTextElideMode(Qt.ElideNone)
        self._no_elide_delegate = NoElideDelegate(self.tbl_files)
        self.tbl_files.setItemDelegate(self._no_elide_delegate)
        self.tbl_files.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tbl_files.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self.tbl_files.itemChanged.connect(self._on_file_item_changed)
        self.tbl_files.itemSelectionChanged.connect(self._on_file_selected)
        self.tbl_files.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tbl_files.customContextMenuRequested.connect(self._file_context_menu)
        self.tbl_files.itemDoubleClicked.connect(self._on_file_double_clicked)
        self._files_context_subfolder = ""
        # 防止列宽信号触发时还没绑定项目导致空操作
        self._files_columns_loading = False

        # 文件操作行
        ops = QHBoxLayout()
        for icon, txt, slot in [
            ("▶", "打开", self.action_open_current_file),
            ("📂", "定位", self.action_reveal_current_file),
            ("🖼", "设为封面", self.action_set_cover),
        ]:
            b = QPushButton(f"{icon}  {txt}")
            b.setProperty("flat", True)
            b.clicked.connect(slot)
            ops.addWidget(b)
        ops.addStretch(1)
        b_del2 = QPushButton("🗑  移除")
        b_del2.setProperty("flat", True)
        b_del2.setProperty("danger", True)
        b_del2.clicked.connect(self.action_delete_files)
        ops.addWidget(b_del2)

        self._files_panel = QWidget()
        self._files_panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        bl = QVBoxLayout(self._files_panel)
        bl.setContentsMargins(12, 6, 12, 10)
        bl.setSpacing(8)
        bl.addLayout(files_header)
        bl.addWidget(self.tbl_files, 1)
        bl.addLayout(ops)

        # 上下垂直 splitter
        self._right_v_split = QSplitter(Qt.Vertical)
        self._right_v_split.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._right_v_split.addWidget(top)
        self._right_v_split.addWidget(self._files_panel)
        self._right_v_split.setStretchFactor(0, 1)
        self._right_v_split.setStretchFactor(1, 1)
        self._right_v_split.setSizes([320, 480])
        self._right_v_split.setHandleWidth(1)

        self._files_detach_window: QDialog | None = None
        self._files_detach_placeholder: QLabel | None = None

        return self._right_v_split

    # ============================================================ files detach
    def _toggle_files_detach(self) -> None:
        if self._files_detach_window is not None:
            self._attach_files_panel()
        else:
            self._detach_files_panel()

    def _detach_files_panel(self) -> None:
        if self._files_detach_window is not None:
            return
        placeholder = QLabel("文件列表已弹出为独立窗口\n关闭该窗口即可恢复")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setProperty("muted", True)
        self._right_v_split.addWidget(placeholder)
        self._files_detach_placeholder = placeholder

        dlg = QDialog(self, Qt.Window)
        dlg.setWindowTitle("文件列表")
        dlg.resize(700, 500)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._files_panel)
        dlg.finished.connect(self._attach_files_panel)
        dlg.show()
        self._files_detach_window = dlg

        self._btn_detach_files.setText("⇲")
        self._btn_detach_files.setToolTip("收回到主窗口")

    def _attach_files_panel(self) -> None:
        if self._files_detach_window is None:
            return
        dlg = self._files_detach_window
        self._files_detach_window = None

        try:
            dlg.finished.disconnect(self._attach_files_panel)
        except RuntimeError:
            pass

        if self._files_detach_placeholder is not None:
            self._files_detach_placeholder.setParent(None)
            self._files_detach_placeholder.deleteLater()
            self._files_detach_placeholder = None

        self._right_v_split.addWidget(self._files_panel)
        self._right_v_split.setSizes([320, 480])

        if dlg.isVisible():
            dlg.close()
        dlg.deleteLater()

        self._btn_detach_files.setText("⇱")
        self._btn_detach_files.setToolTip("弹出为独立窗口")

    # ============================================================ origin filter (task #04)
    def _on_origin_filter_toggled(self) -> None:
        """切换「仅用户文件」/「显示所有」过滤。"""
        is_user_only = self._btn_origin_filter.isChecked()
        self._btn_origin_filter.setText("👤" if is_user_only else "🌐")

        # 持久化
        pid = self._current_project_id
        if pid is not None:
            self.repo.set_project_setting(pid, "files_view_origin_filter", "user" if is_user_only else "all")

        # 刷新文件列表
        if pid is not None:
            self._show_project(self.repo.get_project(pid))

    # ============================================================ task #31b 视图模式切换
    def _toggle_files_view_mode(self) -> None:
        """切换文件表视图模式：树形 ↔ 扁平。"""
        pid = self._current_project_id
        current_mode = getattr(self, '_files_view_mode', 'tree')

        if current_mode == 'tree':
            self._set_files_view_mode('flat', pid)
        else:
            self._set_files_view_mode('tree', pid)

    def _set_files_view_mode(self, mode: str, pid: int | None) -> None:
        """设置文件表视图模式并刷新。"""
        self._files_view_mode = mode

        # 更新按钮图标
        self._btn_view_mode.setText("📋" if mode == "flat" else "🌲")

        # 持久化
        if pid is not None:
            self.repo.set_project_setting(pid, "files_view_mode", mode)

        # 刷新
        if pid is not None:
            self._show_project(self.repo.get_project(pid))

    # ============================================================ view mode
    def _set_view_mode(self, mode: str) -> None:
        self.btn_view_grid.setChecked(mode == "grid")
        self.btn_view_list.setChecked(mode == "list")
        self.view_stack.setCurrentIndex(1 if mode == "list" else 0)

    # ---- 列显示 ----
    DEFAULT_COL_WIDTHS = {
        "title": 240, "author": 140, "date": 110,
        "rating": 90, "source_url": 200, "description": 260,
        "tags": 200, "__files__": 70, "__updated__": 150,
    }

    def _rebuild_columns(self) -> None:
        """根据当前 fields schema + 附加列 重建 QTableView 的列。
        仅显示 visible=True 的字段；列顺序完全跟随 fields.ord（task #22 round 5：
        取消"标题强制第一列"的硬约束，与 Step 2 / 设置 → 字段 / 现有字段编辑面板
        对齐——那几个面板都允许标题字段任意排序）。
        """
        all_fields = self.repo.list_fields()
        # 仅取可见的；title 即使不可见也强制保留
        visible_fields = [f for f in all_fields if f.visible or f.is_title]
        # 按 ord / id 排序，标题字段不再特判
        cols = sorted(
            visible_fields,
            key=lambda f: (f.ord, f.id or 0),
        )
        self.proj_model.set_columns(cols, include_extras=True)
        self._apply_col_widths()

    def _apply_col_widths(self) -> None:
        h = self.proj_table.horizontalHeader()
        h.setStretchLastSection(False)
        n = self.proj_model.columnCount()
        for c in range(n):
            h.setSectionResizeMode(c, QHeaderView.Interactive)
            key = self.proj_model.column_key(c)
            # 仅在用户尚未拖宽时设默认值
            w = self.proj_table.columnWidth(c)
            if w <= 0 or w == 100:
                self.proj_table.setColumnWidth(c, self.DEFAULT_COL_WIDTHS.get(key, 140))

    # ============================================================ helpers
    # ============================================================ files table columns
    def _apply_files_columns_prefs(self, project_id: int | None) -> None:
        """根据项目级偏好应用文件表的列可见性 + 列宽。
        文件名列强制可见；所有列的宽度都由偏好/默认值决定。"""
        if project_id is None:
            prefs: dict = {}
        else:
            raw = self.repo.get_project_setting(
                int(project_id), FILES_COLUMNS_SETTING_KEY, "",
            )
            prefs = files_load_prefs(raw)

        self._files_columns_loading = True
        try:
            for i, col in enumerate(FILES_COLUMNS):
                visible, width = files_resolve_pref(prefs, col)
                self.tbl_files.setColumnHidden(i, not visible)
                self.tbl_files.setColumnWidth(i, width)
        finally:
            self._files_columns_loading = False

    def _save_files_columns_prefs(self) -> None:
        if self._current_project_id is None:
            return
        prefs: dict[str, dict] = {}
        for i, col in enumerate(FILES_COLUMNS):
            prefs[col.key] = {
                "visible": not self.tbl_files.isColumnHidden(i),
                "width": int(self.tbl_files.columnWidth(i)),
            }
        self.repo.set_project_setting(
            int(self._current_project_id),
            FILES_COLUMNS_SETTING_KEY,
            files_dump_prefs(prefs),
        )

    def _on_files_section_resized(self, logical_index: int, _old: int, _new: int) -> None:
        # 初始加载时不保存；用户拖动时才保存
        if getattr(self, "_files_columns_loading", False):
            return
        if 0 <= logical_index < len(FILES_COLUMNS):
            self._save_files_columns_prefs()

    def _files_header_context_menu(self, pos) -> None:
        """表头右键：勾选切换列可见性；文件名禁用。"""
        from PySide6.QtWidgets import QMenu, QWidgetAction, QCheckBox
        menu = QMenu(self)
        # 不用 QAction 的 checkable，而是嵌一个 QCheckBox：
        # 因为 QAction 勾选会立即关菜单，不方便快速多选
        for i, col in enumerate(FILES_COLUMNS):
            cb = QCheckBox(col.label, menu)
            cb.setChecked(not self.tbl_files.isColumnHidden(i))
            if col.mandatory:
                cb.setEnabled(False)
                cb.setToolTip(f"『{col.label}』列必显，不可隐藏")
            cb.stateChanged.connect(
                lambda _state, idx=i, box=cb:
                    self._toggle_file_column(idx, box.isChecked())
            )
            wa = QWidgetAction(menu)
            # 加点 padding 让看着舒服
            cb.setStyleSheet("QCheckBox { padding: 4px 16px 4px 16px; }")
            wa.setDefaultWidget(cb)
            menu.addAction(wa)
        menu.addSeparator()
        act_reset = menu.addAction("↺  恢复默认列宽")
        act_reset.triggered.connect(self._reset_files_columns_to_default)
        menu.exec(self.tbl_files.header().mapToGlobal(pos))

    def _toggle_file_column(self, col_index: int, visible: bool) -> None:
        col = FILES_COLUMNS[col_index]
        if col.mandatory and not visible:
            return   # 安全网
        self.tbl_files.setColumnHidden(col_index, not visible)
        self._save_files_columns_prefs()

    def _reset_files_columns_to_default(self) -> None:
        self._files_columns_loading = True
        try:
            for i, col in enumerate(FILES_COLUMNS):
                self.tbl_files.setColumnHidden(i, False)
                self.tbl_files.setColumnWidth(i, col.default_width)
        finally:
            self._files_columns_loading = False
        self._save_files_columns_prefs()

    def _resolve(self, f: FileItem) -> Path:
        return self.library.resolve(f.path, f.is_relative)

    def _cover_pix(self, p: Project) -> QPixmap | None:
        if not p.cover_file_id:
            return None
        f = self.repo.get_file(p.cover_file_id)
        if not f:
            return None
        path = self._resolve(f)
        if not path.exists() or detect_kind(path) != "image":
            return None
        pix = QPixmap(str(path))
        return pix if not pix.isNull() else None

    # ============================================================ search helpers
    def _apply_search_query(self, query: str) -> None:
        """从历史/收藏菜单选择表达式后填入并触发搜索。"""
        query = (query or "").strip()
        self.search_box.setText(query)
        self._search_timer.start()

    def _record_search_history(self, query: str) -> None:
        """保存成功执行的非空查询。语法错误路径不会调用到这里。"""
        query = (query or "").strip()
        if not query:
            return
        raw = self.repo.get_setting(HISTORY_SETTING_KEY, "[]")
        items = load_history(raw)
        if items and items[0] == query:
            return
        self.repo.set_setting(HISTORY_SETTING_KEY, add_history(raw, query))

    def _show_search_menu(self) -> None:
        """显示收藏表达式与最近搜索。"""
        if self._search_menu_open:
            return
        self._search_menu_open = True
        menu = QMenu(self)
        try:
            saved = load_saved_searches(
                self.repo.get_setting(SAVED_SEARCHES_SETTING_KEY, "[]")
            )
            history = load_history(self.repo.get_setting(HISTORY_SETTING_KEY, "[]"))

            if saved:
                m_saved = menu.addMenu("收藏")
                for item in saved:
                    name = item["name"]
                    query = item["query"]
                    act = m_saved.addAction(f"☆ {name}：{query}")
                    act.triggered.connect(
                        lambda _checked=False, q=query: self._apply_search_query(q)
                    )
                m_del_saved = menu.addMenu("删除收藏")
                for item in saved:
                    name = item["name"]
                    act = m_del_saved.addAction(f"✕ {name}")
                    act.triggered.connect(
                        lambda _checked=False, n=name: self._delete_saved_search(n)
                    )

            if history:
                if saved:
                    menu.addSeparator()
                m_hist = menu.addMenu("最近搜索")
                for query in history:
                    act = m_hist.addAction(query)
                    act.triggered.connect(
                        lambda _checked=False, q=query: self._apply_search_query(q)
                    )
                m_del_hist = menu.addMenu("删除历史")
                for query in history:
                    label = query if len(query) <= 48 else query[:45] + "..."
                    act = m_del_hist.addAction(f"✕ {label}")
                    act.triggered.connect(
                        lambda _checked=False, q=query: self._delete_search_history(q)
                    )

            if not saved and not history:
                act = menu.addAction("暂无搜索历史")
                act.setEnabled(False)

            anchor = self.btn_search_menu if hasattr(self, "btn_search_menu") else self.search_box
            menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        finally:
            self._search_menu_open = False
            self._search_menu_last_closed_at = time.monotonic()

    def _delete_search_history(self, query: str) -> None:
        raw = self.repo.get_setting(HISTORY_SETTING_KEY, "[]")
        self.repo.set_setting(HISTORY_SETTING_KEY, remove_history(raw, query))
        self.statusBar().showMessage("已删除一条搜索历史", 3000)

    def _delete_saved_search(self, name: str) -> None:
        raw = self.repo.get_setting(SAVED_SEARCHES_SETTING_KEY, "[]")
        self.repo.set_setting(SAVED_SEARCHES_SETTING_KEY, remove_saved_search(raw, name))
        self.statusBar().showMessage(f"已删除收藏「{name}」", 3000)

    def _save_current_search(self) -> None:
        query = self.search_box.text().strip()
        if not query:
            QMessageBox.information(self, "收藏搜索", "请先输入搜索表达式。")
            return
        parsed = parse_search(query)
        if not parsed.ok:
            err = parsed.error
            msg = f"{err.message}（位置 {err.position + 1}）" if err else "语法错误"
            QMessageBox.warning(self, "收藏搜索", f"当前表达式有语法错误：{msg}")
            return
        name, ok = QInputDialog.getText(
            self,
            "收藏搜索",
            "给这条搜索起个名字：",
            text=query[:24],
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, "收藏搜索", "收藏名称不能为空。")
            return

        raw = self.repo.get_setting(SAVED_SEARCHES_SETTING_KEY, "[]")
        exists = any(item["name"] == name for item in load_saved_searches(raw))
        if exists:
            ans = QMessageBox.question(
                self,
                "覆盖收藏",
                f"已存在名为「{name}」的收藏，是否覆盖？",
            )
            if ans != QMessageBox.Yes:
                return
        new_raw, overwritten = upsert_saved_search(raw, name, query)
        self.repo.set_setting(SAVED_SEARCHES_SETTING_KEY, new_raw)
        action = "已覆盖收藏" if overwritten else "已收藏搜索"
        self.statusBar().showMessage(f"{action}「{name}」", 3000)

    # ============================================================ projects
    def refresh_projects(self) -> None:
        prev_project_id = self._current_project_id
        # 0) 同步列定义（字段可能改了）
        self._rebuild_columns()
        # 1) 重建标签树（计数会变）
        self._refresh_tag_tree()

        # 2) 根据当前过滤条件取项目
        kind = self._current_filter_kind
        value = self._current_filter_value
        keyword = self.search_box.text().strip() if hasattr(self, "search_box") else ""
        search_all = (
            bool(keyword)
            and hasattr(self, "btn_search_all")
            and self.btn_search_all.isChecked()
        )
        if search_all:
            kind = "all"
            value = ""
        query_ast = None
        if hasattr(self, "search_box"):
            self.search_box.setStyleSheet("")
            self.search_box.setToolTip("")
        if keyword:
            parsed = parse_search(keyword)
            if not parsed.ok:
                err = parsed.error
                msg = f"搜索语法错误：{err.message}（位置 {err.position + 1}）" if err else "搜索语法错误"
                self.search_box.setStyleSheet("QLineEdit#SearchBox { color: #b00020; }")
                self.search_box.setToolTip(msg)
                self.statusBar().showMessage(msg, 5000)
                return
            query_ast = parsed.ast
            self.search_box.setStyleSheet("")
            self.search_box.setToolTip("")

        filter_ast = None
        if kind == "untagged":
            filter_ast = field_term("__untagged", "1", "=")
            projects = self.repo.list_projects_query(combine_and(query_ast, filter_ast))
            title_text = "未分类"
        elif kind == "review":
            review_pids = {
                p.id for p in self.repo.list_projects_pending_review()
                if p.id is not None
            }
            projects = [
                p for p in self.repo.list_projects_query(query_ast)
                if p.id in review_pids
            ]
            title_text = "⚡ 待审阅 LLM 建议"
        elif kind == "tag" and value:
            filter_ast = field_term("tag", value, "=")
            projects = self.repo.list_projects_query(combine_and(query_ast, filter_ast))
            title_text = f"#{value}"
        elif kind == "tag_prefix" and value:
            filter_ast = field_term("__tag_prefix", value)
            projects = self.repo.list_projects_query(combine_and(query_ast, filter_ast))
            title_text = f"📁 {value} / *"
        elif kind == "mcp":
            mcp_pids = {p["id"] for p in self.repo.list_mcp_modified_projects()}
            all_projects = self.repo.list_projects_query(query_ast)
            projects = [p for p in all_projects if p.id in mcp_pids]
            title_text = "🤖 未读MCP修改"
        else:
            projects = self.repo.list_projects_query(query_ast)
            title_text = "全部项目"

        if keyword:
            title_text = f"{title_text} · 搜索「{keyword}」"
            self._record_search_history(keyword)
        self.lbl_filter_title.setText(title_text)

        covers: dict[int, QPixmap] = {}
        file_counts: dict[int, int] = {}
        for p in projects:
            pix = self._cover_pix(p)
            if pix is not None and p.id is not None:
                covers[p.id] = pix
            if p.id is not None:
                file_counts[p.id] = len(self.repo.list_files(p.id))
        self.proj_model.set_data(projects, covers, file_counts)
        self.lbl_count.setText(f"{len(projects)} 个项目")

        if projects:
            target_idx = self.proj_model.index(0, 0)
            if prev_project_id is not None:
                idx = self.proj_model.index_of_id(prev_project_id)
                if idx.isValid():
                    target_idx = idx
            self.proj_view.setCurrentIndex(target_idx)
            # task #15 T2 D4：库里有项目 → 永久隐藏首次引导横幅
            self._on_user_action_dismiss_banner()
            if keyword:
                self.statusBar().showMessage(f"搜索命中 {len(projects)} 个项目", 3000)
        else:
            self._current_project_id = None
            self._show_project(None)
            if keyword:
                self.statusBar().showMessage("搜索命中 0 个项目", 3000)

    def _refresh_tag_tree(self) -> None:
        self.tag_tree.populate(
            tag_counts=self.repo.tag_counts(),
            total=self.repo.count_projects_total(),
            untagged=self.repo.count_projects_untagged(),
            pending_review=self.repo.count_projects_with_pending_suggestions(),
            mcp_modified=len(self.repo.list_mcp_modified_projects()),
        )

    def _on_tag_filter_changed(self, kind: str, value: str) -> None:
        self._current_filter_kind = kind
        self._current_filter_value = value
        self.refresh_projects()

    def _on_projects_dropped_on_tag(self, project_ids: list[int], tag: str) -> None:
        """把拖到标签树上的项目批量追加标签。"""
        n_changed = self.repo.batch_add_tag(project_ids, tag)
        self.refresh_projects()
        valid_ids: list[int] = []
        for raw in project_ids:
            try:
                pid = int(raw)
            except (TypeError, ValueError):
                continue
            if pid > 0 and pid not in valid_ids:
                valid_ids.append(pid)
        total = len(valid_ids)
        if n_changed:
            self.statusBar().showMessage(
                f"已为 {n_changed} / {total} 个项目添加标签「{tag}」", 4000,
            )
        else:
            self.statusBar().showMessage(
                f"选中项目已拥有标签「{tag}」", 3000,
            )

    # ============================================================ project selection (task #25)
    def _selected_project_ids(self) -> list[int]:
        """返回当前选中的项目 id 列表（支持多选）。"""
        rows = self.proj_view.selectionModel().selectedRows()
        return [idx.data(ProjectModel.RoleId) for idx in rows if idx.isValid()]

    def _on_project_selected(self, cur, _prev) -> None:
        # task #25: 多选模式下，若选中多个项目，显示选中数量而非单个项目
        selected = self._selected_project_ids()
        if len(selected) > 1:
            self._current_project_id = None
            self._show_multi_selection(len(selected))
            return

        if not cur.isValid():
            self._current_project_id = None
            self._show_project(None)
            return
        pid = cur.data(ProjectModel.RoleId)
        self._current_project_id = pid
        self._show_project(self.repo.get_project(pid))

    def _show_multi_selection(self, count: int) -> None:
        """多选时显示选中数量，清空文件表和预览。"""
        splitter_sizes = self._main_splitter_sizes()
        # 清空文件表 & 预览
        self.tbl_files.blockSignals(True)
        self.tbl_files.clear()
        self.tbl_files.blockSignals(False)
        self.preview.show_file(None)

        # 更新预览区显示选中数量
        self.lbl_meta_title.setText(f"已选 {count} 个项目")
        self.lbl_meta_desc.setText("")
        self.lbl_files_hint.setText("")
        self.statusBar().showMessage(f"已选中 {count} 个项目")
        self._restore_main_splitter_sizes(splitter_sizes)

    def _show_project(self, p: Project | None) -> None:
        splitter_sizes = self._main_splitter_sizes()
        # 清空文件表 & 预览
        self.tbl_files.blockSignals(True)
        self.tbl_files.clear()
        self.tbl_files.blockSignals(False)
        self.preview.show_file(None)

        if p is None:
            self.lbl_meta_title.setText("（未选择项目）")
            self.lbl_meta_desc.setText("")
            self.lbl_files_hint.setText("")
            self.statusBar().showMessage("")
            self._restore_main_splitter_sizes(splitter_sizes)
            return

        # 标题
        self.lbl_meta_title.setText(p.title or "(未命名)")

        # 描述（取 description_md 的前几行 / 前 ~160 字符；空则提示）
        desc = (p.description_md or "").strip()
        if not desc:
            desc = "（暂无描述）"
        # 把 Markdown 简化成纯文本（去掉常见标记），单段显示
        desc = self._desc_plain(desc)
        self.lbl_meta_desc.setText(desc)

        # 文件表（task #17：按 subfolder 建树；task #04：按 origin 过滤）
        files = self.repo.list_files(p.id)  # type: ignore[arg-type]

        # task #04：按 origin 过滤
        has_generated = any((f.origin or "user") == "generated" for f in files)
        self._btn_origin_filter.setVisible(has_generated)
        origin_filter = self.repo.get_project_setting(p.id, "files_view_origin_filter", "all")
        is_user_only = origin_filter == "user"
        self._btn_origin_filter.setChecked(is_user_only)
        self._btn_origin_filter.setText("👤" if is_user_only else "🌐")
        if is_user_only:
            files = [f for f in files if (f.origin or "user") == "user"]

        # task #31b：加载视图模式（tree/flat）
        view_mode = self.repo.get_project_setting(p.id, "files_view_mode", "tree")
        self._files_view_mode = view_mode
        self._btn_view_mode.setText("📋" if view_mode == "flat" else "🌲")

        self.tbl_files.blockSignals(True)
        if view_mode == "flat":
            self._populate_files_flat(files)
        else:
            self.tbl_files.setSortingEnabled(False)
            self._populate_files_tree(files)
            self.tbl_files.expandAll()
        self.tbl_files.blockSignals(False)

        # 应用项目级列偏好（可见性 + 列宽）
        self._apply_files_columns_prefs(p.id)

        # 显示所有文件的数量（不过滤原始数量）
        all_files_count = len(self.repo.list_files(p.id))  # type: ignore[arg-type]
        self.lbl_files_hint.setText(f"共 {all_files_count} 个文件 · 双击说明列可编辑")
        self.statusBar().showMessage(
            f"项目 #{p.id}  ·  {p.title}  ·  {len(files)} 文件"
        )
        self._restore_main_splitter_sizes(splitter_sizes)

    def _main_splitter_sizes(self) -> list[int]:
        splitter = getattr(self, "_main_splitter", None)
        return splitter.sizes() if splitter is not None else []

    def _valid_main_splitter_sizes(self, sizes: object) -> list[int] | None:
        if not isinstance(sizes, list) or len(sizes) != 3:
            return None
        valid_sizes: list[int] = []
        for size in sizes:
            if not isinstance(size, int):
                return None
            if size < self.MAIN_SPLITTER_MIN_SIZE:
                return None
            valid_sizes.append(size)
        return valid_sizes if sum(valid_sizes) > 0 else None

    def _load_main_splitter_sizes(self) -> list[int]:
        raw = self.repo.get_setting(self.MAIN_SPLITTER_SETTING_KEY, "")
        if raw:
            try:
                saved_sizes = json.loads(raw)
            except Exception:
                saved_sizes = None
            valid_sizes = self._valid_main_splitter_sizes(saved_sizes)
            if valid_sizes is not None:
                return valid_sizes
        return list(self.MAIN_SPLITTER_DEFAULT_SIZES)

    def _on_main_splitter_moved(self, _pos: int, _index: int) -> None:
        sizes = self._valid_main_splitter_sizes(self._main_splitter_sizes())
        if sizes is None:
            return
        self.repo.set_setting(self.MAIN_SPLITTER_SETTING_KEY, json.dumps(sizes))

    def _restore_main_splitter_sizes(self, sizes: list[int]) -> None:
        splitter = getattr(self, "_main_splitter", None)
        if splitter is None or not sizes or sum(sizes) <= 0:
            return
        splitter.setSizes(sizes)
        QTimer.singleShot(0, lambda s=list(sizes): splitter.setSizes(s))

    FILES_TREE_SORT_SETTING_KEY = "files_table_sort_tree"
    EXPLICIT_SUBFOLDERS_SETTING_KEY = "explicit_subfolders"

    def _load_explicit_subfolders(self, project_id: int | None) -> set[str]:
        if project_id is None:
            return set()
        raw = self.repo.get_project_setting(
            int(project_id), self.EXPLICIT_SUBFOLDERS_SETTING_KEY, "[]",
        )
        try:
            data = json.loads(raw)
        except Exception:
            return set()
        if not isinstance(data, list):
            return set()
        out: set[str] = set()
        for item in data:
            if isinstance(item, str):
                sf = item.strip().strip("/")
                if sf:
                    out.add(sf)
        return out

    def _save_explicit_subfolders(self, project_id: int, subfolders: set[str]) -> None:
        cleaned = sorted(sf.strip().strip("/") for sf in subfolders if sf.strip().strip("/"))
        self.repo.set_project_setting(
            int(project_id),
            self.EXPLICIT_SUBFOLDERS_SETTING_KEY,
            json.dumps(cleaned, ensure_ascii=False),
        )

    def _tree_sort_state(self) -> tuple[str, bool]:
        pid = self._current_project_id
        if pid is None:
            return "custom", False
        raw = self.repo.get_project_setting(
            int(pid), self.FILES_TREE_SORT_SETTING_KEY, "",
        )
        if raw:
            try:
                data = json.loads(raw)
                key = str(data.get("key") or "custom")
                descending = bool(data.get("descending", False))
                if key == "custom" or files_column_by_key(key) is not None:
                    return key, descending
            except Exception:
                pass
        return "custom", False

    def _set_tree_sort_state(self, key: str, descending: bool = False) -> None:
        pid = self._current_project_id
        if pid is None:
            return
        if key != "custom" and files_column_by_key(key) is None:
            key = "custom"
            descending = False
        self.repo.set_project_setting(
            int(pid),
            self.FILES_TREE_SORT_SETTING_KEY,
            json.dumps({"key": key, "descending": bool(descending)}, ensure_ascii=False),
        )

    def _connect_files_tree_header(self) -> None:
        header = self.tbl_files.header()
        try:
            header.sectionClicked.disconnect(self._on_files_flat_header_clicked)
        except (RuntimeError, TypeError):
            pass
        try:
            header.sectionClicked.disconnect(self._on_files_tree_header_clicked)
        except (RuntimeError, TypeError):
            pass
        header.sectionClicked.connect(self._on_files_tree_header_clicked)

    def _on_files_tree_header_clicked(self, col: int) -> None:
        if not (0 <= col < len(FILES_COLUMNS)):
            return
        key = FILES_COLUMNS[col].key
        cur_key, cur_desc = self._tree_sort_state()
        descending = (not cur_desc) if cur_key == key else False
        self._set_tree_sort_state(key, descending)
        self._sort_files_tree()
        order = Qt.DescendingOrder if descending else Qt.AscendingOrder
        self.tbl_files.header().setSortIndicator(col, order)

    def _populate_files_tree(self, files: list[FileItem]) -> None:
        """按 files.subfolder 分组建树（task #17）。

        - subfolder="" → 顶层文件节点
        - subfolder="ML" → 📁 ML/ 下挂文件
        - subfolder="ML/NLP" → 📁 ML/ → 📁 NLP/ 下挂文件
        - 两种模式（仓储/链接）展示方式完全一致

        task #31b: 新增 size / added_at 列显示
        """
        kind_icons = {"image": "🖼", "video": "🎬", "pdf": "📄", "doc": "📝",
                      "code": "💻", "other": "📦"}

        # 缓存文件大小（避免重复 stat）
        size_cache: dict[int, str] = {}

        def _get_size(fid: int, fpath: str, is_rel: bool) -> str:
            if fid in size_cache:
                return size_cache[fid]
            try:
                abs_path = self.library.resolve(fpath, is_rel)
                if abs_path.exists():
                    size = abs_path.stat().st_size
                    # 格式化大小
                    if size < 1024:
                        s = f"{size} B"
                    elif size < 1024 * 1024:
                        s = f"{size / 1024:.1f} KB"
                    elif size < 1024 * 1024 * 1024:
                        s = f"{size / (1024 * 1024):.1f} MB"
                    else:
                        s = f"{size / (1024 * 1024 * 1024):.2f} GB"
                    size_cache[fid] = s
                    return s
            except Exception:
                pass
            size_cache[fid] = "—"
            return "—"

        def _format_added_at(added_at: str | None) -> str:
            if not added_at:
                return "—"
            # 格式化为 YYYY-MM-DD HH:MM
            try:
                # 原始格式可能是 "2026-06-10 18:34:08"
                dt = added_at[:16] if len(added_at) >= 16 else added_at
                return dt
            except Exception:
                return "—"

        # dir_nodes: "a/b" → QTreeWidgetItem，逐级缓存避免重复创建
        dir_nodes: dict[str, QTreeWidgetItem] = {}

        def _get_dir_node(subfolder: str) -> QTreeWidgetItem | None:
            """为 subfolder 路径逐级创建/复用目录节点，返回最深一级。"""
            if not subfolder:
                return None  # 顶层
            if subfolder in dir_nodes:
                return dir_nodes[subfolder]
            parts = subfolder.split("/")
            parent_node: QTreeWidgetItem | None = None
            for depth in range(1, len(parts) + 1):
                partial = "/".join(parts[:depth])
                if partial in dir_nodes:
                    parent_node = dir_nodes[partial]
                    continue
                node = QTreeWidgetItem()
                node.setText(0, f"📁 {parts[depth - 1]}/")
                node.setFlags(node.flags() & ~Qt.ItemIsEditable)
                # 目录节点不可选中执行（存一个特殊标记）
                node.setData(0, Qt.UserRole, -1)
                node.setData(0, Qt.UserRole + 1, partial)
                if parent_node is None:
                    self.tbl_files.addTopLevelItem(node)
                else:
                    parent_node.addChild(node)
                dir_nodes[partial] = node
                parent_node = node
            return parent_node

        explicit_subfolders = self._load_explicit_subfolders(self._current_project_id)
        file_subfolders = {f.subfolder for f in files if f.subfolder}
        for subfolder in sorted(explicit_subfolders | file_subfolders):
            _get_dir_node(subfolder)

        for f in files:
            name = Path(f.path).name
            kind_icon = kind_icons.get(f.kind, "📦")
            warn_prefix = "⚠ " if f.missing else ""

            item = QTreeWidgetItem()
            item.setText(0, f"{warn_prefix}{kind_icon}  {name}")
            item.setText(1, f.label)
            item.setText(2, f.kind)
            item.setText(3, _get_size(f.id, f.path, f.is_relative))
            item.setText(4, _format_added_at(f.added_at))
            item.setText(5, "📦 仓储" if f.is_relative else "🔗 链接")
            item.setData(0, Qt.UserRole, f.id)
            item.setData(0, Qt.UserRole + 2, f.subfolder or "")
            # 文件节点默认不可编辑（label 编辑通过 _on_file_item_changed 控制）
            item.setFlags(
                (item.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsEditable)
                & ~Qt.ItemIsDropEnabled
            )

            if f.missing:
                item.setToolTip(0,
                    "此文件被标记为缺失（库一致性检查发现物理路径不存在）。\n"
                    "再次跑「工具 → 检查库一致性」可重新评估。"
                )
            item.setToolTip(5,
                "文件已复制到统一仓库目录" if f.is_relative
                else "仅记录原始路径，文件留在原位"
            )

            parent_node = _get_dir_node(f.subfolder)
            if parent_node is None:
                self.tbl_files.addTopLevelItem(item)
            else:
                parent_node.addChild(item)

        # 排序：同级内目录先（按名字），文件后（按当前树形排序设置）
        self._sort_files_tree()
        self._connect_files_tree_header()

    # task #31b: 扁平视图
    def _populate_files_flat(self, files: list[FileItem]) -> None:
        """扁平视图：所有文件平铺（忽略 subfolder 分组）。

        task #31b: 支持按列排序
        """
        kind_icons = {"image": "🖼", "video": "🎬", "pdf": "📄", "doc": "📝",
                      "code": "💻", "other": "📦"}

        def _get_size(fid: int, fpath: str, is_rel: bool) -> str:
            try:
                abs_path = self.library.resolve(fpath, is_rel)
                if abs_path.exists():
                    size = abs_path.stat().st_size
                    if size < 1024:
                        return f"{size} B"
                    elif size < 1024 * 1024:
                        return f"{size / 1024:.1f} KB"
                    elif size < 1024 * 1024 * 1024:
                        return f"{size / (1024 * 1024):.1f} MB"
                    else:
                        return f"{size / (1024 * 1024 * 1024):.2f} GB"
            except Exception:
                pass
            return "—"

        def _format_added_at(added_at: str | None) -> str:
            if not added_at:
                return "—"
            try:
                return added_at[:16] if len(added_at) >= 16 else added_at
            except Exception:
                return "—"

        for f in files:
            name = Path(f.path).name
            kind_icon = kind_icons.get(f.kind, "📦")
            warn_prefix = "⚠ " if f.missing else ""

            item = QTreeWidgetItem()
            item.setText(0, f"{warn_prefix}{kind_icon}  {name}")
            item.setText(1, f.label)
            item.setText(2, f.kind)
            item.setText(3, _get_size(f.id, f.path, f.is_relative))
            item.setText(4, _format_added_at(f.added_at))
            item.setText(5, "📦 仓储" if f.is_relative else "🔗 链接")
            item.setData(0, Qt.UserRole, f.id)
            item.setFlags(item.flags() & ~Qt.ItemIsDropEnabled)

            if f.missing:
                item.setToolTip(0,
                    "此文件被标记为缺失（库一致性检查发现物理路径不存在）。\n"
                    "再次跑「工具 → 检查库一致性」可重新评估。"
                )
            item.setToolTip(5,
                "文件已复制到统一仓库目录" if f.is_relative
                else "仅记录原始路径，文件留在原位"
            )

            self.tbl_files.addTopLevelItem(item)

        # task #31b: 启用列排序
        self.tbl_files.setSortingEnabled(True)
        # 加载排序偏好
        pid = self._current_project_id
        if pid:
            sort_col = self.repo.get_project_setting(pid, "files_flat_sort_col", "0")
            sort_order = self.repo.get_project_setting(pid, "files_flat_sort_order", "0")
            self.tbl_files.sortByColumn(int(sort_col), Qt.SortOrder(int(sort_order)))

        # 连接排序信号
        try:
            self.tbl_files.header().sectionClicked.disconnect()
        except RuntimeError:
            pass
        self.tbl_files.header().sectionClicked.connect(self._on_files_flat_header_clicked)

    def _on_files_flat_header_clicked(self, col: int) -> None:
        """扁平视图：列点击排序（task #31b）。"""
        pid = self._current_project_id
        if not pid:
            return

        # 获取当前排序状态
        order = self.tbl_files.header().sortIndicatorOrder()
        self.repo.set_project_setting(pid, "files_flat_sort_col", str(col))
        self.repo.set_project_setting(pid, "files_flat_sort_order", str(order))

    def _sort_files_tree(self) -> None:
        """对树的每一级排序：目录在前，文件按树形排序偏好排列。"""
        sort_key, descending = self._tree_sort_state()
        key_to_col = {col.key: i for i, col in enumerate(FILES_COLUMNS)}

        def _file_sort_value(item: QTreeWidgetItem):
            if sort_key == "custom":
                fid = item.data(0, Qt.UserRole)
                f = self.repo.get_file(int(fid)) if fid is not None and fid > 0 else None
                return (f.ord if f else 0, f.id if f and f.id is not None else 0)
            col = key_to_col.get(sort_key, 0)
            text = item.text(col) or ""
            return text.casefold()

        def _sort_items(parent: QTreeWidget | QTreeWidgetItem) -> None:
            # 用 take* 取出所有子节点（不删除）
            children: list[QTreeWidgetItem] = []
            if isinstance(parent, QTreeWidget):
                while parent.topLevelItemCount():
                    children.append(parent.takeTopLevelItem(0))
            else:
                while parent.childCount():
                    children.append(parent.takeChild(0))

            # 分离目录节点和文件节点
            dirs = [c for c in children if c.data(0, Qt.UserRole) == -1]
            files = [c for c in children if c.data(0, Qt.UserRole) != -1]

            # 目录按文本排序，文件保持原序
            dirs.sort(key=lambda n: n.text(0))
            files.sort(key=_file_sort_value, reverse=descending)

            # 重新放回
            for n in dirs:
                if isinstance(parent, QTreeWidget):
                    parent.addTopLevelItem(n)
                else:
                    parent.addChild(n)
            for n in files:
                if isinstance(parent, QTreeWidget):
                    parent.addTopLevelItem(n)
                else:
                    parent.addChild(n)

            # 递归子目录
            for n in dirs:
                _sort_items(n)

        _sort_items(self.tbl_files)
        if sort_key != "custom":
            col = key_to_col.get(sort_key, 0)
            order = Qt.DescendingOrder if descending else Qt.AscendingOrder
            self.tbl_files.header().setSortIndicatorShown(True)
            self.tbl_files.header().setSortIndicator(col, order)

    @staticmethod
    def _desc_plain(md: str) -> str:
        """把 Markdown 简化为单段纯文本（用于 2~3 行省略显示）。"""
        import re
        text = md
        # 去掉代码块
        text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
        # 去掉行内代码 / 加粗 / 斜体标记符
        text = re.sub(r"[`*_>#~]+", "", text)
        # 链接 [text](url) → text
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        # 多空白合并
        text = re.sub(r"\s+", " ", text).strip()
        return text

    # ============================================================ project actions
    def action_open_settings(self) -> None:
        dlg = SettingsDialog(
            self.repo,
            library_root=self.library.root,
            db_path=self.db_path,
            parent=self,
        )
        dlg.theme_changed.connect(self._apply_theme_now)
        dlg.default_view_changed.connect(self._set_view_mode)
        dlg.default_storage_changed.connect(lambda _v: None)  # 仅持久化
        # 字段定义变化后刷新项目列表（描述可能被追加；字段值/列会变）
        dlg.fields_changed.connect(self.refresh_projects)
        # task #15 T2 D4 一次性标志：在设置页加过非系统字段 → 触发；
        # 同步把横幅隐藏掉（避免再次出现）
        dlg.fields_changed.connect(self._on_user_action_dismiss_banner)
        dlg.exec()

    def action_open_settings_fields(self) -> None:
        """直接跳到设置 → 字段（task #15 T2 横幅按钮入口）。"""
        dlg = SettingsDialog(
            self.repo,
            library_root=self.library.root,
            db_path=self.db_path,
            parent=self,
        )
        dlg.theme_changed.connect(self._apply_theme_now)
        dlg.default_view_changed.connect(self._set_view_mode)
        dlg.default_storage_changed.connect(lambda _v: None)
        dlg.fields_changed.connect(self.refresh_projects)
        dlg.fields_changed.connect(self._on_user_action_dismiss_banner)
        dlg.set_active_category("字段")
        dlg.exec()

    def _on_user_action_dismiss_banner(self) -> None:
        """task #15 T2 D4：用户做过任何"成长性动作"后永久隐藏首次引导横幅。

        触发点：跑过库字段设计助手 / 在设置加过非系统字段 / 创建过第一个项目 /
        主动点了横幅的"不再显示"。本方法幂等。
        """
        from .first_run_banner import dismiss_banner
        dismiss_banner(self.repo)
        if hasattr(self, "first_run_banner") and self.first_run_banner is not None:
            self.first_run_banner.hide()

    def _apply_theme_now(self, name: str) -> None:
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, name)

    # ============================================================ MCP audit

    def action_open_mcp_audit(self) -> None:
        from .mcp_audit_dialog import MCPAuditDialog
        dlg = MCPAuditDialog(self.repo, parent=self)
        dlg.exec()
        self._check_mcp_activity()

    def _check_mcp_activity(self) -> None:
        """轻量轮询：只查 audit 最新 id，有变化才完整刷新。"""
        row = self.repo.conn.execute("SELECT MAX(id) FROM mcp_audit").fetchone()
        latest = row[0] or 0
        if latest > self._mcp_last_audit_id:
            self._mcp_last_audit_id = latest
            total = self.repo.count_mcp_audit()
            self.lbl_mcp_count.setText(f"📋 MCP 操作: {total}")
            self.refresh_projects()  # 刷新项目列表 + 标签树（更新 MCP 计数）

    def _on_mark_mcp_seen(self) -> None:
        """右键菜单：标记已了解该项目的 MCP 修改（支持多选）。"""
        selected_ids = self._selected_project_ids()
        if not selected_ids:
            return
        for pid in selected_ids:
            self.repo.clear_project_mcp_modified(pid)
        self.refresh_projects()
        self.statusBar().showMessage(f"已标记 {len(selected_ids)} 个项目的 MCP 修改为已读", 3000)

    # ============================================================ LLM
    def action_open_llm_tasks(self) -> None:
        from .llm_tasks_panel import LLMTasksDialog
        dlg = LLMTasksDialog(self.repo, parent=self)
        dlg.suggestions_reapplied.connect(self._on_llm_suggestions_added)
        dlg.exec()

    def action_llm_suggest_for_project(self, pid: int | None = None) -> None:
        """从右键菜单触发：跳过项目编辑对话框，直接弹 LLMSuggestDialog。"""
        # QAction.triggered 会传 bool；Python 里 bool 是 int 的子类，
        # 必须先把 bool 显式排除再判 int。
        if isinstance(pid, bool) or not isinstance(pid, int):
            pid = self._current_project_id
        if pid is None or self.llm_queue is None:
            return
        # 未配置 API → 提示用户去设置但不主动跳转（按用户偏好：文字引导即可）
        if not self._llm_check_configured_or_prompt():
            return
        p = self.repo.get_project(pid)
        if not p:
            return
        files = self.repo.list_files(pid)
        from ..llm import load_config
        from .llm_suggest_dialog import LLMSuggestDialog
        cfg = load_config(self.repo)
        fields = self.repo.list_fields()
        dlg = LLMSuggestDialog(p.title, files, cfg, fields, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        self._enqueue_meta_suggest(
            p, dlg.ref_file_ids, dlg.user_note, dlg.target_field_ids,
        )

    def _launch_llm_from_dialog(self, edit_dlg, project) -> None:
        """从项目编辑对话框中点 ✨ 触发：弹 LLMSuggestDialog；执行后关闭编辑对话框。"""
        if self.llm_queue is None or project.id is None:
            return
        # 与右键入口一致：未配置 API 时给文字提示，不强行跳转
        if not self._llm_check_configured_or_prompt(parent=edit_dlg):
            return
        files = self.repo.list_files(project.id)
        from ..llm import load_config
        from .llm_suggest_dialog import LLMSuggestDialog
        cfg = load_config(self.repo)
        fields = self.repo.list_fields()
        dlg = LLMSuggestDialog(project.title, files, cfg, fields, parent=edit_dlg)
        if dlg.exec() != QDialog.Accepted:
            return
        # 执行 → 入队 + 关闭项目编辑对话框（不保存当前修改）
        self._enqueue_meta_suggest(
            project, dlg.ref_file_ids, dlg.user_note, dlg.target_field_ids,
        )
        edit_dlg.reject()

    def _enqueue_meta_suggest(
        self, project, ref_file_ids, user_note, target_field_ids=None,
    ) -> None:
        try:
            self.llm_queue.enqueue_meta_suggest(
                project, ref_file_ids, user_note, target_field_ids,
            )
            self.statusBar().showMessage(
                f"已提交 LLM 任务：{project.title}", 4000,
            )
        except Exception as e:
            QMessageBox.warning(self, "失败", str(e))

    def _llm_check_configured_or_prompt(self, *, parent=None) -> bool:
        """检查默认 provider 是否配好 API Key；未配则弹提示并返回 False。

        提示走「文字引导、不主动跳转」路线（用户偏好：避免帮用户决定下一步）：
        - 默认 provider 不存在 / api_key 为空 → 弹 information 框，文案明确指向
          「设置 → API」，但不自动打开该页。
        """
        from ..llm import load_config
        cfg = load_config(self.repo)
        active = cfg.active()
        if active is not None and (active.api_key or "").strip():
            return True
        QMessageBox.information(
            parent or self,
            "未配置 API",
            "尚未配置 LLM API Key，无法生成元数据建议。\n\n"
            "请打开「设置 → API」页填入默认 provider 的 API Key 后再试。",
        )
        return False

    # 信号槽
    def _on_llm_counts(self, n: int) -> None:
        self.lbl_llm_count.setText(f"⚡ LLM 任务: {n}")

    def _on_llm_suggestions_added(self, project_id: int, count: int) -> None:
        # 刷新 TagTree 和列表（待审阅计数变化、可能影响列表）
        self.refresh_projects()
        if count > 0:
            p = self.repo.get_project(project_id)
            title = p.title if p else f"#{project_id}"
            self.statusBar().showMessage(
                f"✨ 项目「{title}」获得 {count} 条 LLM 建议", 6000,
            )

    def _on_llm_task_failed(self, _tid: int, err: str) -> None:
        self.statusBar().showMessage(f"❌ LLM 任务失败: {err[:80]}", 6000)

    def action_new_project(self) -> None:
        initial = Project()
        dlg = ProjectDialog(initial, repo=self.repo, parent=self)
        # 新建对话框不再展示 ✨ LLM 建议按钮（M1 决策，2026-06-02）：
        # 此时项目还没文件、没历史，建议无意义；统一改为引导用户先保存再请 LLM。
        # request_llm_suggest 信号在新建模式下永远不会触发，所以这里也不再连接。
        if dlg.exec() == ProjectDialog.Accepted:
            p = dlg.project()
            pid = self.repo.save_project(p)
            self.refresh_projects()
            self._select_project_by_id(pid)

    def action_edit_project(self) -> None:
        if self._current_project_id is None:
            return
        p = self.repo.get_project(self._current_project_id)
        if not p:
            return
        dlg = ProjectDialog(p, repo=self.repo, parent=self)
        # 用户在编辑对话框中点 ✨ → 弹 LLMSuggestDialog → 入队 → 关闭编辑对话框
        dlg.request_llm_suggest.connect(
            lambda d=dlg, pp=p: self._launch_llm_from_dialog(d, pp)
        )
        if dlg.exec() == ProjectDialog.Accepted:
            self.repo.save_project(dlg.project())
            self.refresh_projects()
            self._select_project_by_id(p.id)  # type: ignore[arg-type]
        else:
            # 即便用户 Cancel，建议状态可能已被独立修改（applied/rejected）→ 也刷新
            self.refresh_projects()

    def action_delete_project(self) -> None:
        """删除项目（支持多选）。"""
        selected_ids = self._selected_project_ids()
        if not selected_ids:
            return

        # 收集所有选中项目的信息
        total_copy = 0
        total_link = 0
        titles = []
        for pid in selected_ids:
            p = self.repo.get_project(pid)
            if p:
                titles.append(p.title)
                files = self.repo.list_files(pid)  # type: ignore[arg-type]
                total_copy += sum(1 for f in files if f.is_relative)
                total_link += sum(1 for f in files if not f.is_relative)

        # 构建确认消息
        if len(titles) == 1:
            lines = [f"确定删除项目「{titles[0]}」？"]
        else:
            lines = [f"确定删除这 {len(selected_ids)} 个项目？"]
            lines.append(f"项目：{', '.join(titles[:3])}" + ("..." if len(titles) > 3 else ""))

        if total_copy == 0 and total_link == 0:
            lines.append("（这些项目目前没有文件）")
        else:
            if total_link:
                lines.append(f"🔗 链接 · {total_link} 个：原文件不受影响。")
            if total_copy:
                lines.append(f"📦 仓储 · {total_copy} 个：将从仓库目录删除。")

        ans = QMessageBox.question(self, "确认删除", "\n".join(lines))
        if ans != QMessageBox.Yes:
            return

        # 逐个删除
        for pid in selected_ids:
            p = self.repo.get_project(pid)
            if not p:
                continue

            files = self.repo.list_files(pid)  # type: ignore[arg-type]
            copy_files = [f for f in files if f.is_relative]

            # 删除仓储文件
            if copy_files:
                for f in copy_files:
                    self.library.remove_relative(f.path)
                pdir = self.library.project_dir(pid)  # type: ignore[arg-type]
                try:
                    for child in pdir.iterdir():
                        try:
                            child.unlink()
                        except OSError:
                            pass
                    pdir.rmdir()
                except OSError:
                    pass
            self.repo.delete_project(pid)

        self.refresh_projects()
        self.statusBar().showMessage(f"已删除 {len(selected_ids)} 个项目", 3000)

    def action_export_project(self, pid: int | None = None) -> None:
        """导出当前/指定项目到本地目录。支持单项目和批量导出。"""
        from PySide6.QtWidgets import QProgressDialog

        from ..exporter import ExportOptions, export_project
        from ..utils import open_with_default_app

        # task #28 T2: 检查是否是多选导出
        selected_ids = self._selected_project_ids()

        # QAction.triggered 会传 bool(checked) 进来
        is_multi = len(selected_ids) > 1
        is_explicit_single = isinstance(pid, int) and pid > 0

        if is_multi:
            # 批量导出模式
            self._export_batch(selected_ids)
            return

        # 单项目导出模式
        if isinstance(pid, bool) or not isinstance(pid, int):
            pid = self._current_project_id
        if pid is None:
            QMessageBox.information(self, "提示", "请先选择一个项目")
            return
        project = self.repo.get_project(pid)
        if project is None:
            QMessageBox.warning(self, "提示", f"项目 id={pid} 不存在")
            return
        n_files = len(self.repo.list_files(pid))

        last_dir = self.repo.get_setting("last_export_dir", "") or str(Path.home())
        dlg = ExportDialog(project, n_files, last_dir, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        opts = ExportOptions(
            target_root=dlg.target_root(),
            copy_link_files=dlg.copy_link_files(),
        )

        # 进度对话框：用户能看到当前文件名 + 取消
        prog = QProgressDialog(
            "正在导出…", "取消", 0, max(n_files, 1), self,
        )
        prog.setWindowTitle("导出项目")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setValue(0)

        cancelled = False

        def _on_progress(done: int, total: int, name: str) -> None:
            nonlocal cancelled
            if prog.wasCanceled():
                cancelled = True
                raise InterruptedError("用户取消导出")
            prog.setMaximum(max(total, 1))
            prog.setValue(done)
            prog.setLabelText(f"正在复制（{done}/{total}）：{name}")
            QApplication.processEvents()

        try:
            result = export_project(
                self.repo, self.library, project, opts,
                progress=_on_progress,
            )
        except InterruptedError:
            prog.close()
            QMessageBox.information(self, "已取消", "导出已被取消。")
            return
        except (OSError, NotADirectoryError) as e:
            prog.close()
            QMessageBox.warning(self, "导出失败", f"{type(e).__name__}: {e}")
            return
        finally:
            prog.close()

        # 记忆下次默认目录
        self.repo.set_setting("last_export_dir", str(opts.target_root))

        # 结果摘要
        self._show_export_result(result)

    def _export_batch(self, selected_ids: list[int]) -> None:
        """批量导出多个项目（task #28 T2）。"""
        from PySide6.QtWidgets import QProgressDialog

        from ..exporter import ExportOptions, export_project
        from ..utils import open_with_default_app, human_size

        # 收集项目信息
        projects_info: list[tuple[int, str, int]] = []
        for pid in selected_ids:
            p = self.repo.get_project(pid)
            if p:
                n_files = len(self.repo.list_files(pid))
                projects_info.append((pid, p.title, n_files))

        if not projects_info:
            QMessageBox.information(self, "提示", "没有可导出的项目")
            return

        last_dir = self.repo.get_setting("last_export_dir", "") or str(Path.home())
        dlg = ExportDialog(projects=projects_info, last_export_dir=last_dir, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        target_root = dlg.target_root()
        selected_indices = dlg.selected_projects()

        # 进度对话框
        total_projects = len(selected_indices)
        prog = QProgressDialog(
            f"正在批量导出 {total_projects} 个项目…", "取消", 0, total_projects, self,
        )
        prog.setWindowTitle("批量导出")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setValue(0)

        results: list = []
        try:
            for i, idx in enumerate(selected_indices):
                if prog.wasCanceled():
                    break
                pid, title, n_files = projects_info[idx]
                project = self.repo.get_project(pid)
                if not project:
                    continue

                prog.setValue(i)
                prog.setLabelText(f"正在导出：{title}")
                QApplication.processEvents()

                opts = ExportOptions(
                    target_root=target_root,
                    copy_link_files=dlg.copy_link_files(),
                    mode=dlg.mode(),
                    export_format=dlg.export_format(),
                    preserve_structure=dlg.preserve_structure(),
                    include_readme=dlg.include_readme(),
                    include_llm_history=dlg.include_llm_history(),
                )

                try:
                    result = export_project(
                        self.repo, self.library, project, opts,
                        progress=None,  # 批量导出不显示单文件进度
                    )
                    results.append((title, result))
                except Exception as e:
                    results.append((title, f"导出失败: {e}"))

            prog.setValue(total_projects)
        finally:
            prog.close()

        # 记忆下次默认目录
        self.repo.set_setting("last_export_dir", str(target_root))

        # 结果摘要
        success_count = sum(1 for _, r in results if hasattr(r, 'n_files_copied'))
        msg = f"批量导出完成：\n\n成功 {success_count}/{total_projects} 个项目\n\n"
        for title, r in results:
            if hasattr(r, 'n_files_copied'):
                msg += f"✓ {title}: {r.n_files_copied} 文件 → {r.project_dir.name}\n"
            else:
                msg += f"✗ {title}: {r}\n"

        box = QMessageBox(QMessageBox.Information, "批量导出完成", msg, parent=self)
        btn_open = box.addButton("📂 打开导出目录", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is btn_open:
            try:
                open_with_default_app(target_root)
            except Exception:
                pass

    def _show_export_result(self, result) -> None:
        """显示导出结果。"""
        from ..utils import open_with_default_app

        msg = (
            f"导出成功：\n\n"
            f"目录：{result.project_dir}\n"
            f"复制文件：{result.n_files_copied} 个\n"
            f"仅记录（未复制）：{result.n_files_referenced} 个\n"
        )
        if result.warnings:
            msg += f"\n⚠️ 警告 {len(result.warnings)} 条：\n"
            msg += "\n".join(f"- {w}" for w in result.warnings[:5])
            if len(result.warnings) > 5:
                msg += f"\n…（共 {len(result.warnings)} 条，详情见导出包内 README.md）"

        box = QMessageBox(QMessageBox.Information, "导出完成", msg, parent=self)
        btn_open = box.addButton("📂 打开导出目录", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is btn_open:
            try:
                open_with_default_app(result.project_dir)
            except Exception:
                pass

    def _select_project_by_id(self, pid: int) -> None:
        idx = self.proj_model.index_of_id(pid)
        if idx.isValid():
            self.proj_view.setCurrentIndex(idx)

    def _project_context_menu(self, pos) -> None:
        sender = self.sender()
        view = sender if sender in (self.proj_view, self.proj_table) else self.proj_view
        idx = view.indexAt(pos)
        if not idx.isValid():
            return
        view.setCurrentIndex(idx)

        # task #25: 检查选中数量
        selected_ids = self._selected_project_ids()
        is_multi = len(selected_ids) > 1

        menu = QMenu(self)

        if is_multi:
            # 多选菜单
            menu.addAction(f"已选 {len(selected_ids)} 个项目")
            menu.addSeparator()
            menu.addAction("✨  LLM 元数据建议…", self.action_llm_suggest_for_project)
            menu.addSeparator()
            menu.addAction("📤  导出选中项目…", self.action_export_project)
            menu.addSeparator()
            menu.addAction("👁  已读MCP修改", self._on_mark_mcp_seen)
            menu.addSeparator()
            menu.addAction("🗑  删除", self.action_delete_project)
        else:
            # 单选菜单
            menu.addAction("✎  编辑…", self.action_edit_project)
            menu.addSeparator()
            menu.addAction("✨  LLM 元数据建议…", self.action_llm_suggest_for_project)
            menu.addSeparator()
            menu.addAction("📤  导出项目…", self.action_export_project)
            menu.addSeparator()
            act_paste_cover = menu.addAction(
                "📋  从剪切板设为封面", self.action_set_cover_from_clipboard,
            )
            # 没有图片就禁用
            from PySide6.QtWidgets import QApplication
            cb = QApplication.clipboard()
            has_img = False
            try:
                md = cb.mimeData()
                has_img = md is not None and (md.hasImage() or md.hasUrls())
            except Exception:
                pass
            act_paste_cover.setEnabled(has_img)
            if not has_img:
                act_paste_cover.setToolTip("剪切板中没有图片")
            menu.addSeparator()
            menu.addAction("👁  已读MCP修改", self._on_mark_mcp_seen)
            menu.addSeparator()
            menu.addAction("🗑  删除", self.action_delete_project)

        global_pos = view.viewport().mapToGlobal(pos) if hasattr(view, "viewport") else view.mapToGlobal(pos)
        menu.exec(global_pos)

    # ============================================================ file actions
    def action_add_files(self) -> None:
        if self._current_project_id is None:
            QMessageBox.information(self, "提示", "请先选择或新建一个项目")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "添加文件")
        if not paths:
            return
        p = self.repo.get_project(self._current_project_id)
        if not p:
            return

        # task #17：若当前选中了目录节点，新文件归到该目录的 subfolder
        target_subfolder = self._selected_tree_subfolder()

        # 询问本次导入要用哪种存储方式（默认 = 全局设置的「默认存储方式」）
        default_mode = self.repo.get_setting("default_storage_mode", "link") or "link"
        storage, ask_label_text = self._ask_storage_for_import(
            paths, default_mode,
        )
        if storage is None:
            return  # 用户取消

        # 说明已经在第一个对话框里填了（单文件场景），不再弹第二次
        from ..models import PendingFile
        pending = [PendingFile(src=Path(p).resolve(), subfolder=target_subfolder)
                   for p in paths]
        added = self._import_files(
            p, pending, ask_label=False, storage=storage,
        )
        # 若对话框里填了说明（仅单文件路径），写到新增的最后一个文件
        if added and ask_label_text:
            files = self.repo.list_files(p.id)  # type: ignore[arg-type]
            if files:
                last = files[-1]
                last.label = ask_label_text
                self.repo.update_file(last)
        if added:
            self._show_project(self.repo.get_project(p.id))  # type: ignore[arg-type]

    def _ask_storage_for_import(
        self, paths: list, default_mode: str,
    ) -> tuple[str | None, str | None]:
        """弹出存储方式选择对话框。

        paths: list[PendingFile] 或 list[str]。
        返回 (storage, label)：
        - storage = "link" | "copy" 或 None（取消）
        - label：仅单文件时可能携带的说明文本；None 表示对话框没收集说明
        """
        from ..models import PendingFile
        from PySide6.QtWidgets import (
            QDialog, QDialogButtonBox, QLabel, QLineEdit,
            QRadioButton, QVBoxLayout,
        )

        # 兼容 PendingFile 和 str
        first = paths[0]
        first_name = first.src.name if isinstance(first, PendingFile) else Path(first).name

        dlg = QDialog(self)
        dlg.setWindowTitle("添加文件")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 16, 20, 12)
        lay.setSpacing(10)

        head = QLabel(
            f"将导入 {len(paths)} 个文件，请选择存储方式："
            if len(paths) > 1
            else f"将导入「{first_name}」，请选择存储方式："
        )
        head.setWordWrap(True)
        lay.addWidget(head)

        rb_link = QRadioButton("🔗 链接 — 仅记录原始路径，不动用户文件")
        rb_copy = QRadioButton("📦 仓储 — 复制到统一仓库目录")
        if default_mode == "copy":
            rb_copy.setChecked(True)
        else:
            rb_link.setChecked(True)
        lay.addWidget(rb_link)
        lay.addWidget(rb_copy)

        hint = QLabel("默认值来自「设置 → 默认存储方式」，可逐次更改。")
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # 单文件时顺手收集说明，避免再弹第二个对话框
        edit_label: QLineEdit | None = None
        if len(paths) == 1:
            lay.addSpacing(4)
            lbl_cap = QLabel("说明（可留空）：")
            lay.addWidget(lbl_cap)
            edit_label = QLineEdit()
            edit_label.setPlaceholderText("例如：中文版 / 第 1 章 / 草稿")
            lay.addWidget(edit_label)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)

        if dlg.exec() != QDialog.Accepted:
            return None, None
        storage = "copy" if rb_copy.isChecked() else "link"
        label = edit_label.text().strip() if edit_label else None
        return storage, (label or None)

    def _import_files(
        self,
        project: Project,
        paths: list,
        ask_label: bool = False,
        storage: str | None = None,
    ) -> int:
        """批量导入文件，返回成功数量。

        paths: list[PendingFile] 或 list[str]（兼容旧调用）。
        storage: "link" | "copy" | None。None 表示按全局「默认存储方式」设置。
        """
        from ..models import PendingFile
        added = 0
        for item in paths:
            if isinstance(item, PendingFile):
                src = str(item.src)
                subfolder = item.subfolder
            else:
                src = str(item)
                subfolder = ""
            try:
                self._import_one(project, src, ask_label=ask_label, storage=storage,
                                 subfolder=subfolder)
                added += 1
            except Exception as e:
                QMessageBox.warning(self, "导入失败", f"{src}\n{e}")
        if added and project.id is not None:
            self.repo.touch_project(project.id)
        return added

    def _import_one(
        self,
        p: Project,
        src: str,
        ask_label: bool = False,
        storage: str | None = None,
        subfolder: str = "",
    ) -> None:
        src_path = Path(src)
        if not src_path.exists():
            raise FileNotFoundError(src)
        mode = storage or (
            self.repo.get_setting("default_storage_mode", "link") or "link"
        )
        if mode == "copy":
            rel = self.library.import_copy(p.id, src_path)  # type: ignore[arg-type]
            f = FileItem(
                project_id=p.id,  # type: ignore[arg-type]
                path=rel, is_relative=True, label="",
                kind=detect_kind(src_path), ord=10_000,
                subfolder=subfolder,
            )
        else:
            f = FileItem(
                project_id=p.id,  # type: ignore[arg-type]
                path=str(src_path.resolve()), is_relative=False, label="",
                kind=detect_kind(src_path), ord=10_000,
                subfolder=subfolder,
            )
        if ask_label:
            label, ok = QInputDialog.getText(
                self, "文件说明",
                f"为「{src_path.name}」添加说明（可留空）", text="",
            )
            if ok:
                f.label = label.strip()
        self.repo.add_file(f)

    def _current_file_row_id(self) -> int | None:
        it = self.tbl_files.currentItem()
        if it is None:
            return None
        fid = it.data(0, Qt.UserRole)
        # 目录节点存 -1，文件节点存 file_id
        return fid if fid is not None and fid > 0 else None

    def _selected_tree_subfolder(self) -> str:
        """返回当前选中的目录节点对应的 subfolder 路径。

        - 选中目录节点 → 从节点文本反推 subfolder（去掉 📁 前缀和 / 后缀，逐级拼接）
        - 选中文件节点或无选中 → ""（顶层）
        """
        it = self.tbl_files.currentItem()
        if it is None:
            return ""
        fid = it.data(0, Qt.UserRole)
        if fid is None or fid > 0:
            return ""  # 文件节点或无效
        stored = it.data(0, Qt.UserRole + 1)
        if stored is not None:
            return stored or ""
        # 目录节点：向上遍历拼路径
        parts: list[str] = []
        node = it
        while node is not None:
            text = node.text(0)
            # 去掉 "📁 " 前缀和 "/" 后缀
            name = text.replace("📁 ", "").rstrip("/")
            parts.append(name)
            node = node.parent()
        parts.reverse()
        return "/".join(parts)

    def _selected_file_ids(self) -> list[int]:
        ids: list[int] = []
        for it in self.tbl_files.selectedItems():
            fid = it.data(0, Qt.UserRole)
            if fid is not None and fid > 0 and fid not in ids:
                ids.append(fid)
        return ids

    def _selected_files(self) -> list[FileItem]:
        """返回当前选中的文件对象，过滤目录节点和失效 id。"""
        files: list[FileItem] = []
        for fid in self._selected_file_ids():
            f = self.repo.get_file(fid)
            if f is not None:
                files.append(f)
        return files

    def _files_under_subfolder(self, subfolder: str, *, missing_only: bool = False) -> list[FileItem]:
        """收集某个逻辑文件夹及其子层级下的文件。"""
        if self._current_project_id is None:
            return []
        return self.repo.list_files_under_subfolder(
            self._current_project_id, subfolder, missing_only=missing_only,
        )

    def _on_file_selected(self) -> None:
        fid = self._current_file_row_id()
        self._current_file_id = fid
        if fid is None:
            self.preview.show_file(None)
            return
        f = self.repo.get_file(fid)
        if not f:
            self.preview.show_file(None)
            return
        path = self._resolve(f)
        self.preview.show_file(str(path) if path.exists() else None)

    def _on_file_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        fid = item.data(0, Qt.UserRole)
        if fid is None or fid <= 0:
            return  # 目录节点或无效
        if column != 1:  # 仅 label 列可编辑；其它列还原
            self.tbl_files.blockSignals(True)
            f = self.repo.get_file(fid)
            if f and column == 0:
                name = Path(f.path).name
                kind_icons = {"image": "🖼", "video": "🎬", "pdf": "📄", "doc": "📝",
                              "code": "💻", "other": "📦"}
                item.setText(0, f"{kind_icons.get(f.kind, '📦')}  {name}")
            elif f and column == 2:
                item.setText(2, f.kind)
            elif f and column == 3:
                item.setText(3, "📦 仓储" if f.is_relative else "🔗 链接")
            self.tbl_files.blockSignals(False)
            return
        f = self.repo.get_file(fid)
        if not f:
            return
        f.label = item.text(1)
        self.repo.update_file(f)

    def _on_file_double_clicked(self, item: QTreeWidgetItem, col: int) -> None:
        fid = item.data(0, Qt.UserRole)
        if fid is not None and fid > 0 and col == 0:
            self.action_open_current_file()

    def action_open_current_file(self) -> None:
        fid = self._current_file_row_id()
        if fid is None:
            return
        f = self.repo.get_file(fid)
        if not f:
            return
        path = self._resolve(f)
        if not path.exists():
            QMessageBox.warning(self, "提示", f"文件不存在：{path}")
            return
        open_with_default_app(path)

    def action_reveal_current_file(self) -> None:
        fid = self._current_file_row_id()
        if fid is None:
            return
        f = self.repo.get_file(fid)
        if not f:
            return
        path = self._resolve(f)
        if path.exists():
            reveal_in_explorer(path)

    def action_delete_files(self) -> None:
        # task #17 T3：支持删除目录节点（整棵子树）和文件节点
        file_ids = self._selected_file_ids()
        dir_subfolders = self._selected_dir_subfolders()

        if not file_ids and not dir_subfolders:
            return

        # 收集要删除的文件
        files_to_delete: list[FileItem] = []

        # 目录节点 → 递归收集该 subfolder 下所有文件
        if dir_subfolders and self._current_project_id is not None:
            all_files = self.repo.list_files(self._current_project_id)
            for sf in dir_subfolders:
                for f in all_files:
                    if f.subfolder == sf or f.subfolder.startswith(sf + "/"):
                        if f not in files_to_delete:
                            files_to_delete.append(f)

        # 文件节点
        for fid in file_ids:
            f = self.repo.get_file(fid)
            if f and f not in files_to_delete:
                files_to_delete.append(f)

        if not files_to_delete:
            return

        # 按存储方式分类
        link_files = [f for f in files_to_delete if not f.is_relative]
        copy_files = [f for f in files_to_delete if f.is_relative]

        def _fmt_list(files: list[FileItem], max_show: int = 8) -> str:
            names = [Path(f.path).name for f in files]
            if len(names) <= max_show:
                shown = names
            else:
                shown = names[:max_show] + [f"…（其余 {len(names) - max_show} 个）"]
            return "\n".join(f"  • {n}" for n in shown)

        parts: list[str] = []
        if dir_subfolders:
            parts.append(f"📁 目录（连带所有子文件）· {len(dir_subfolders)} 个：\n"
                         + "\n".join(f"  • 📁 {sf}/" for sf in dir_subfolders))
        if link_files:
            parts.append(
                f"🔗 链接（不影响原文件） · {len(link_files)} 个：\n"
                + _fmt_list(link_files)
            )
        if copy_files:
            parts.append(
                f"📦 仓储（会从仓库删除物理文件） · {len(copy_files)} 个：\n"
                + _fmt_list(copy_files)
            )

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("确认移除文件")
        msg.setText(
            f"即将从项目中移除 {len(files_to_delete)} 个文件："
        )
        msg.setInformativeText("\n\n".join(parts))
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        if msg.exec() != QMessageBox.Yes:
            return

        for f in files_to_delete:
            if f.is_relative:
                self.library.remove_relative(f.path)
            if f.id is not None:
                self.repo.delete_file(f.id)
        if self._current_project_id is not None:
            self.repo.touch_project(self._current_project_id)
            self._show_project(self.repo.get_project(self._current_project_id))

    def _selected_dir_subfolders(self) -> list[str]:
        """返回当前选中的目录节点对应的 subfolder 路径列表。"""
        subfolders: list[str] = []
        for it in self.tbl_files.selectedItems():
            fid = it.data(0, Qt.UserRole)
            if fid is not None and fid < 0:  # 目录节点
                sf = self._subfolder_from_tree_node(it)
                if sf:
                    subfolders.append(sf)
        return subfolders

    @staticmethod
    def _subfolder_from_tree_node(node: QTreeWidgetItem) -> str:
        """从目录树节点向上遍历，拼出 subfolder 路径。"""
        stored = node.data(0, Qt.UserRole + 1)
        if stored is not None:
            return stored or ""
        parts: list[str] = []
        n: QTreeWidgetItem | None = node
        while n is not None:
            text = n.text(0)
            name = text.replace("📁 ", "").rstrip("/")
            parts.append(name)
            n = n.parent()
        parts.reverse()
        return "/".join(parts)

    # ============================================================ task #29 文件存储位置管理
    def action_convert_to_storage(self) -> None:
        """task #29 T1：链接文件转为仓储文件（复制进库）。"""
        files = self._selected_files()
        self._convert_files_to_storage(files)

    def action_convert_folder_to_storage(self) -> None:
        """task #29 T3c：整个逻辑文件夹转仓储。"""
        sf = getattr(self, "_files_context_subfolder", "") or ""
        files = self._files_under_subfolder(sf)
        self._convert_files_to_storage(files, scope_label=f"文件夹「{sf}」")

    def _convert_files_to_storage(
        self, files: list[FileItem], *, scope_label: str = "所选文件",
    ) -> None:
        if not files:
            QMessageBox.information(self, "提示", f"{scope_label}中没有可处理的文件。")
            return

        # 分类：🔗 链接文件才需要转换，📦 仓储已在内
        link_files = [f for f in files if not f.is_relative]
        storage_files = [f for f in files if f.is_relative]

        if not link_files:
            QMessageBox.information(
                self, "提示",
                f"{scope_label}已都是仓储模式，无需转换。"
            )
            return

        # 确认对话框
        ans = QMessageBox.question(
            self, "确认转换",
            f"{scope_label}：将把 {len(link_files)} 个🔗链接文件复制进库：\n\n"
            + "\n".join(f"  • {f.label or f.path}" for f in link_files[:5])
            + (f"\n  … 等共 {len(link_files)} 个" if len(link_files) > 5 else "")
            + f"\n\n已仓储文件 {len(storage_files)} 个将跳过。\n\n"
            "⚠️ 原外部文件不会被删除（复制语义）。",
        )
        if ans != QMessageBox.Yes:
            return

        # 执行转换
        from PySide6.QtWidgets import QProgressDialog
        from PySide6.QtCore import Qt
        prog = QProgressDialog(
            f"{scope_label}：正在转换...", "取消", 0, len(link_files), self,
        )
        prog.setWindowTitle("链接转仓储")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.show()

        copied = 0
        skipped = 0
        errors: list[str] = []

        for i, f in enumerate(link_files):
            if prog.wasCanceled():
                break
            prog.setValue(i)
            prog.setLabelText(f"正在处理：{f.label or f.path}")

            try:
                # 解析外部文件的绝对路径
                abs_src = self.library.resolve(f.path, is_relative=False)
                if not abs_src.exists():
                    skipped += 1
                    errors.append(f"原文件不存在：{abs_src}")
                    continue

                # 复制进库
                rel_path = self.library.import_copy(f.project_id, abs_src)
                f.path = rel_path
                f.is_relative = True
                self.repo.update_file(f)
                copied += 1
            except Exception as e:
                errors.append(f"{f.path}：{e}")
                skipped += 1

        prog.setValue(len(link_files))
        prog.close()

        # 结果反馈
        lines = [f"✅ 已复制到库内：{copied} 个"]
        if skipped:
            lines.append(f"⏭ 跳过：{skipped} 个")
        if errors:
            lines.append("\n错误详情：")
            lines.extend(f"  • {e}" for e in errors[:10])

        QMessageBox.information(
            self, "转换完成",
            "\n".join(lines)
        )

        # 刷新文件表
        self._refresh_files_table()

    def action_move_file(self) -> None:
        """task #29 T2：移动文件到新位置。"""
        files = self._selected_files()
        self._move_files_to_location(files)

    def action_move_folder(self) -> None:
        """task #29 T3c：移动整个逻辑文件夹下的文件。"""
        sf = getattr(self, "_files_context_subfolder", "") or ""
        files = self._files_under_subfolder(sf)
        self._move_files_to_location(files, scope_label=f"文件夹「{sf}」")

    def _move_files_to_location(
        self, files: list[FileItem], *, scope_label: str = "所选文件",
    ) -> None:
        if not files:
            QMessageBox.information(self, "提示", f"{scope_label}中没有可移动的文件。")
            return

        # 选择目标目录
        target_dir = QFileDialog.getExistingDirectory(
            self, "选择目标目录",
        )
        if not target_dir:
            return
        target_dir = Path(target_dir)

        # 执行移动
        from PySide6.QtWidgets import QProgressDialog
        from PySide6.QtCore import Qt
        prog = QProgressDialog(
            f"{scope_label}：正在移动...", "取消", 0, len(files), self,
        )
        prog.setWindowTitle("移动文件")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.show()

        moved = 0
        errors: list[str] = []

        for i, f in enumerate(files):
            if prog.wasCanceled():
                break
            prog.setValue(i)
            prog.setLabelText(f"正在处理：{f.label or f.path}")

            try:
                # 解析当前物理路径
                abs_src = self.library.resolve(f.path, f.is_relative)
                if not abs_src.exists():
                    errors.append(f"{f.path}：文件不存在")
                    continue

                # 目标路径
                dst = target_dir / abs_src.name
                # 同名冲突处理
                j = 1
                while dst.exists():
                    dst = target_dir / f"{abs_src.stem}_{j}{abs_src.suffix}"
                    j += 1

                # 物理移动
                shutil.move(str(abs_src), str(dst))

                # 更新数据库
                f.path = str(dst.resolve())
                f.is_relative = False  # 移动后都变成外部链接
                self.repo.update_file(f)
                moved += 1
            except Exception as e:
                errors.append(f"{f.path}：{e}")

        prog.setValue(len(files))
        prog.close()

        # 结果反馈
        lines = [f"✅ 已移动：{moved} 个"]
        if errors:
            lines.append(f"⏭ 跳过：{len(errors)} 个")
            lines.append("\n错误详情：")
            lines.extend(f"  • {e}" for e in errors[:10])

        QMessageBox.information(
            self, "移动完成",
            "\n".join(lines)
        )

        # 刷新文件表
        self._refresh_files_table()

    def action_relink_file(self) -> None:
        """task #29 T3a：重关联到外部文件（修复 missing 文件）。"""
        files = self._selected_files()
        self._relink_files_to_directory(files)

    def action_relink_folder(self) -> None:
        """task #29 T3c：按文件夹批量重关联 missing 文件。"""
        sf = getattr(self, "_files_context_subfolder", "") or ""
        files = self._files_under_subfolder(sf, missing_only=True)
        self._relink_files_to_directory(files, scope_label=f"文件夹「{sf}」")

    def _relink_files_to_directory(
        self, files: list[FileItem], *, scope_label: str = "所选文件",
    ) -> None:
        if not files:
            QMessageBox.information(self, "提示", f"{scope_label}中没有可重关联的文件。")
            return

        # 选择外部目录
        source_dir = QFileDialog.getExistingDirectory(
            self, "选择文件所在目录",
            "选择包含要关联的外部文件的目录",
        )
        if not source_dir:
            return
        source_dir = Path(source_dir)

        # 收集目录中的文件（按文件名匹配）
        try:
            available_files = {p.name: p for p in source_dir.iterdir() if p.is_file()}
        except OSError as e:
            QMessageBox.warning(self, "无法读取目录", str(e))
            return

        # 匹配文件
        matched = []
        unmatched = []
        for f in files:
            name = Path(f.path).name
            if name in available_files:
                matched.append((f, available_files[name]))
            else:
                unmatched.append(name)

        if not matched:
            QMessageBox.warning(
                self, "未找到匹配文件",
                f"{scope_label}中没有一个文件能在指定目录下找到同名文件。\n\n"
                "请确认文件是否在选择的目录中。"
            )
            return

        # 确认对话框
        ans = QMessageBox.question(
            self, "确认重关联",
            f"{scope_label}：将为 {len(matched)} 个文件重新关联到外部文件：\n\n"
            + "\n".join(f"  • {old.path} → {new.name}" for old, new in matched[:5])
            + (f"\n  … 等共 {len(matched)} 个" if len(matched) > 5 else "")
            + (f"\n\n未找到：{len(unmatched)} 个" if unmatched else "")
        )
        if ans != QMessageBox.Yes:
            return

        # 执行重关联
        relinked = 0
        errors: list[str] = []
        for f, new_path in matched:
            try:
                f.path = str(new_path.resolve())
                f.is_relative = False
                # 清除 missing 标记
                self.repo.set_file_missing(f.id, False)
                self.repo.update_file(f)
                relinked += 1
            except Exception as e:
                errors.append(f"{f.path}：{e}")

        # 结果反馈
        lines = [f"✅ 已重关联：{relinked} 个"]
        if errors:
            lines.append(f"⏭ 错误：{len(errors)} 个")
            lines.extend(f"  • {e}" for e in errors[:10])

        QMessageBox.information(
            self, "重关联完成",
            "\n".join(lines)
        )

        # 刷新
        self._refresh_files_table()

    def action_replace_link_target(self) -> None:
        """task #29 T3b：替换链接目标（仅单选）。"""
        file_ids = self._selected_file_ids()
        if len(file_ids) != 1:
            QMessageBox.information(
                self, "提示",
                "请先选择一个🔗链接文件来替换目标。"
            )
            return

        f = self.repo.get_file(file_ids[0])
        if not f:
            return

        if f.is_relative:
            QMessageBox.information(
                self, "提示",
                "该文件是仓储模式，无法替换链接目标。\n\n"
                "如需替换，请先「转为仓储文件」后再操作。"
            )
            return

        # 选择新文件
        new_file, _ = QFileDialog.getOpenFileName(
            self, "选择新文件", "",
            "所有文件 (*)",
        )
        if not new_file:
            return
        new_file = Path(new_file)

        # 确认
        ans = QMessageBox.question(
            self, "确认替换",
            f"将把链接目标从：\n  {f.path}\n\n替换为：\n  {new_file}\n\n是否继续？",
        )
        if ans != QMessageBox.Yes:
            return

        # 执行替换
        try:
            f.path = str(new_file.resolve())
            self.repo.update_file(f)

            QMessageBox.information(
                self, "完成",
                "链接目标已替换。"
            )
            self._refresh_files_table()
        except Exception as e:
            QMessageBox.critical(
                self, "错误",
                f"替换失败：{e}"
            )

    def action_set_cover(self) -> None:
        fid = self._current_file_row_id()
        if fid is None or self._current_project_id is None:
            return
        f = self.repo.get_file(fid)
        if not f:
            return
        p = self.repo.get_project(self._current_project_id)
        if not p:
            return
        if f.kind == "image":
            # 直接把图片本身设为封面
            p.cover_file_id = fid
            self.repo.save_project(p)
            self.refresh_projects()
            self._select_project_by_id(p.id)  # type: ignore[arg-type]
            return
        # 非图片：尝试截取当前预览画面
        pix = self.preview.capture_pixmap() if hasattr(self, "preview") else None
        if pix is None or pix.isNull():
            QMessageBox.information(
                self, "提示",
                "当前文件无可截取的预览画面。\n（视频请先开始播放，PDF 请先加载完成）",
            )
            return
        try:
            cover_fid = self._save_cover_snapshot(p, pix, source_kind=f.kind)
        except Exception as e:
            QMessageBox.warning(self, "失败", f"保存封面失败：{e}")
            return
        p.cover_file_id = cover_fid
        self.repo.save_project(p)
        self.refresh_projects()
        self._select_project_by_id(p.id)  # type: ignore[arg-type]
        self.statusBar().showMessage("已截取当前画面并设为封面", 4000)

    def action_set_cover_from_clipboard(self) -> None:
        """从系统剪切板抓图片，落地到项目仓库目录，设为该项目封面。"""
        pid = self._current_project_id
        if pid is None:
            return
        p = self.repo.get_project(pid)
        if not p:
            return
        from PySide6.QtGui import QImage, QPixmap
        from PySide6.QtWidgets import QApplication
        cb = QApplication.clipboard()
        md = cb.mimeData() if cb is not None else None
        pix: QPixmap | None = None

        # 1) 直接含图片
        if md is not None and md.hasImage():
            img = cb.image()
            if not img.isNull():
                pix = QPixmap.fromImage(img)

        # 2) 剪切板里是文件 URL（资源管理器复制了一张图片）
        if (pix is None or pix.isNull()) and md is not None and md.hasUrls():
            for url in md.urls():
                if not url.isLocalFile():
                    continue
                lp = url.toLocalFile()
                if detect_kind(lp) != "image":
                    continue
                cand = QPixmap(lp)
                if not cand.isNull():
                    pix = cand
                    break

        if pix is None or pix.isNull():
            QMessageBox.information(self, "提示", "剪切板里没有可用的图片。")
            return

        try:
            cover_fid = self._save_cover_snapshot(p, pix, source_kind="clipboard")
        except Exception as e:
            QMessageBox.warning(self, "失败", f"保存封面失败：{e}")
            return
        p.cover_file_id = cover_fid
        self.repo.save_project(p)
        self.refresh_projects()
        self._select_project_by_id(p.id)  # type: ignore[arg-type]
        self.statusBar().showMessage("已从剪切板设置封面", 4000)

    def _save_cover_snapshot(self, p, pix, source_kind: str) -> int:
        """把 QPixmap 落地到项目仓库目录作为 png，并写入 files 表。
        返回新建 file 的 id。
        source_kind: 'pdf' / 'video' / 'image' / 'clipboard' …
        """
        from datetime import datetime as _dt
        if p.id is None:
            raise RuntimeError("项目尚未保存")
        # 落到 project_dir 下；不论项目原本是 link 还是 copy 模式，
        # 截图都是衍生物，统一放仓库
        target_dir = self.library.project_dir(int(p.id))
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        fname = f"__cover_{ts}.png"
        full = target_dir / fname
        if not pix.save(str(full), "PNG"):
            raise RuntimeError("QPixmap.save 失败")
        rel = f"project_{int(p.id)}/{fname}"
        # 标签按来源区分
        label_map = {
            "clipboard": "封面（来自剪切板）",
            "pdf": "封面（截取自 PDF）",
            "video": "封面（截取自视频）",
        }
        label = label_map.get(source_kind, f"封面（{source_kind}）")
        from ..models import FileItem
        fi = FileItem(
            project_id=int(p.id),
            path=rel,
            is_relative=True,
            label=label,
            kind="image",
            origin="generated",  # task #30：封面快照是软件衍生物
        )
        new_fid = self.repo.add_file(fi)
        return new_fid

    # ============================================================ task #31a 新建文件夹 + 重命名
    def action_new_subfolder(self) -> None:
        """task #31a T3：新建空文件夹（subfolder）。"""
        pid = self._current_project_id
        if pid is None:
            return

        parent_subfolder = getattr(self, "_files_context_subfolder", "") or ""
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "新建文件夹",
            "请输入文件夹名称：",
            text="新建文件夹",
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        if "/" in name or "\\" in name:
            QMessageBox.warning(self, "无效名称", "文件夹名称不能包含路径分隔符。")
            return

        new_subfolder = f"{parent_subfolder}/{name}" if parent_subfolder else name

        existing_files = self.repo.list_files(pid)
        existing_sf = {f.subfolder for f in existing_files}
        explicit = self._load_explicit_subfolders(pid)
        if new_subfolder in existing_sf or new_subfolder in explicit:
            QMessageBox.warning(self, "已存在", f"文件夹「{new_subfolder}」已存在。")
            return

        explicit.add(new_subfolder)
        self._save_explicit_subfolders(pid, explicit)
        self._show_project(self.repo.get_project(pid))
        self.statusBar().showMessage(f"已创建文件夹「{new_subfolder}」", 4000)

    def action_rename_file(self) -> None:
        """task #31a T4：F2 重命名文件（label）。"""
        item = self.tbl_files.currentItem()
        if item is not None and item.data(0, Qt.UserRole) is not None and item.data(0, Qt.UserRole) < 0:
            self.action_rename_subfolder()
            return
        fid = self._current_file_row_id()
        if fid is None:
            return

        f = self.repo.get_file(fid)
        if not f:
            return

        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "重命名文件",
            "请输入新的说明（label）：",
            text=f.label or "",
        )
        if not ok:
            return

        f.label = name.strip()
        self.repo.update_file(f)
        self._refresh_files_table()
        self.statusBar().showMessage(f"已重命名为「{f.label}」", 4000)

    def action_rename_physical_file(self) -> None:
        """task #31a T4：Shift+F2 重命名物理文件名。"""
        fid = self._current_file_row_id()
        if fid is None:
            return
        f = self.repo.get_file(fid)
        if not f:
            return

        old_abs = self.library.resolve(f.path, f.is_relative)
        old_name = old_abs.name
        from PySide6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(
            self, "重命名物理文件",
            "请输入新的文件名：",
            text=old_name,
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name in (".", ".."):
            QMessageBox.warning(self, "无效名称", "文件名不能为空。")
            return
        if any(ch in new_name for ch in '<>:"/\\|?*'):
            QMessageBox.warning(self, "无效名称", "文件名包含 Windows 不允许的字符。")
            return
        if new_name == old_name:
            return

        new_abs = old_abs.with_name(new_name)
        if new_abs.exists():
            QMessageBox.warning(self, "已存在", f"目标文件已存在：\n{new_abs}")
            return
        if not old_abs.exists():
            QMessageBox.warning(self, "文件不存在", f"原文件不存在：\n{old_abs}")
            return

        try:
            old_abs.rename(new_abs)
            if f.is_relative:
                f.path = (Path(f.path).parent / new_name).as_posix()
            else:
                f.path = str(new_abs.resolve())
            self.repo.update_file(f)
        except OSError as e:
            QMessageBox.warning(self, "重命名失败", str(e))
            return

        self._refresh_files_table()
        self.statusBar().showMessage(f"已重命名物理文件为「{new_name}」", 4000)

    def action_rename_subfolder(self) -> None:
        """task #31a T4：重命名逻辑文件夹。"""
        pid = self._current_project_id
        item = self.tbl_files.currentItem()
        if pid is None or item is None:
            return
        old = self._subfolder_from_tree_node(item)
        if not old:
            return
        parent = old.rsplit("/", 1)[0] if "/" in old else ""
        old_name = old.rsplit("/", 1)[-1]

        from PySide6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(
            self, "重命名文件夹",
            "请输入新的文件夹名：",
            text=old_name,
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "无效名称", "文件夹名不能为空。")
            return
        if "/" in new_name or "\\" in new_name:
            QMessageBox.warning(self, "无效名称", "文件夹名称不能包含路径分隔符。")
            return
        new = f"{parent}/{new_name}" if parent else new_name
        if new == old:
            return

        files = self.repo.list_files(pid)
        existing = {f.subfolder for f in files if f.subfolder and f.subfolder != old}
        explicit = self._load_explicit_subfolders(pid)
        if new in existing or (new in explicit and new != old):
            QMessageBox.warning(self, "已存在", f"文件夹「{new}」已存在。")
            return

        self.repo.rename_subfolder(pid, old, new)
        updated_explicit: set[str] = set()
        prefix = old + "/"
        for sf in explicit:
            if sf == old:
                updated_explicit.add(new)
            elif sf.startswith(prefix):
                updated_explicit.add(new + sf[len(old):])
            else:
                updated_explicit.add(sf)
        self._save_explicit_subfolders(pid, updated_explicit)
        self._refresh_files_table()
        self.statusBar().showMessage(f"已重命名文件夹为「{new}」", 4000)

    def action_delete_empty_subfolder(self) -> None:
        """删除显式创建且不含文件的空文件夹。"""
        pid = self._current_project_id
        item = self.tbl_files.currentItem()
        if pid is None or item is None:
            return
        sf = self._subfolder_from_tree_node(item)
        if not sf:
            return
        files = self.repo.list_files(pid)
        if any(f.subfolder == sf or f.subfolder.startswith(sf + "/") for f in files):
            QMessageBox.information(self, "无法删除", "只能删除空文件夹。")
            return
        explicit = self._load_explicit_subfolders(pid)
        explicit.discard(sf)
        self._save_explicit_subfolders(pid, explicit)
        self._refresh_files_table()
        self.statusBar().showMessage(f"已删除空文件夹「{sf}」", 4000)

    def _on_files_moved(
        self,
        file_ids: list[int],
        target_subfolder: str,
        target_file_id,
        before: bool,
    ) -> None:
        """task #31a T2：内部拖动文件，更新 subfolder 与同级顺序。"""
        pid = self._current_project_id
        if pid is None or getattr(self, "_files_view_mode", "tree") != "tree":
            return
        selected_ids: list[int] = []
        for fid in file_ids:
            if fid not in selected_ids:
                selected_ids.append(int(fid))
        if not selected_ids:
            return
        if target_file_id in selected_ids:
            target_file_id = None

        target_subfolder = (target_subfolder or "").strip("/")
        selected_files = [self.repo.get_file(fid) for fid in selected_ids]
        selected_files = [
            f for f in selected_files
            if f is not None and f.project_id == pid and f.id is not None
        ]
        if not selected_files:
            return

        source_subfolders = {f.subfolder or "" for f in selected_files}
        for f in selected_files:
            if f.subfolder != target_subfolder and f.id is not None:
                self.repo.set_file_subfolder(f.id, target_subfolder)
                f.subfolder = target_subfolder

        all_files = self.repo.list_files(pid)
        selected_set = {int(f.id) for f in selected_files if f.id is not None}
        siblings = [
            f for f in all_files
            if (f.subfolder or "") == target_subfolder and f.id not in selected_set
        ]
        siblings.sort(key=lambda f: (f.ord, f.id or 0))

        insert_at = len(siblings)
        if target_file_id is not None:
            for i, f in enumerate(siblings):
                if f.id == target_file_id:
                    insert_at = i if before else i + 1
                    break

        selected_order = [int(f.id) for f in selected_files if f.id is not None]
        new_order = [int(f.id) for f in siblings[:insert_at] if f.id is not None]
        new_order.extend(selected_order)
        new_order.extend(int(f.id) for f in siblings[insert_at:] if f.id is not None)
        self.repo.reorder_files(new_order)

        for sf in source_subfolders - {target_subfolder}:
            remaining = [
                f for f in self.repo.list_files(pid)
                if (f.subfolder or "") == sf and f.id is not None
            ]
            remaining.sort(key=lambda f: (f.ord, f.id or 0))
            self.repo.reorder_files([int(f.id) for f in remaining])

        self._set_tree_sort_state("custom", False)
        self._refresh_files_table()
        self.statusBar().showMessage(
            f"已移动 {len(selected_files)} 个文件到「{target_subfolder or '顶层'}」",
            4000,
        )

    def _refresh_files_table(self) -> None:
        """刷新文件表（重载当前项目）。"""
        pid = self._current_project_id
        if pid is not None:
            self._show_project(self.repo.get_project(pid))

    def _file_context_menu(self, pos) -> None:
        # 获取右键点击位置的项目
        item = self.tbl_files.itemAt(pos)
        if item is None:
            # 在空白处右键 → 新建文件夹
            self._files_context_subfolder = ""
            menu = QMenu(self)
            menu.addAction("📁  新建文件夹", self.action_new_subfolder)
            menu.exec(self.tbl_files.viewport().mapToGlobal(pos))
            return

        self.tbl_files.setCurrentItem(item)
        fid = item.data(0, Qt.UserRole)
        if fid is None or fid <= 0:
            # 右键在目录节点上 → 可新建子文件夹
            sf = self._subfolder_from_tree_node(item)
            self._files_context_subfolder = sf
            files = self.repo.list_files(self._current_project_id) if self._current_project_id else []
            is_empty = bool(sf) and not any(
                f.subfolder == sf or f.subfolder.startswith(sf + "/") for f in files
            )
            has_missing = bool(sf) and any(
                (f.subfolder == sf or f.subfolder.startswith(sf + "/")) and f.missing
                for f in files
            )
            menu = QMenu(self)
            menu.addAction("📁  在此文件夹下新建子文件夹", self.action_new_subfolder)
            menu.addAction("✏️  重命名文件夹", self.action_rename_subfolder)
            act_del_empty = menu.addAction("🗑  删除空文件夹", self.action_delete_empty_subfolder)
            act_del_empty.setEnabled(is_empty)
            menu.addSeparator()
            act_folder_storage = menu.addAction(
                "📦  整个文件夹转为仓储", self.action_convert_folder_to_storage,
            )
            act_folder_move = menu.addAction(
                "📂  整个文件夹移到...", self.action_move_folder,
            )
            act_folder_relink = menu.addAction(
                "🔧  整个文件夹重关联到...", self.action_relink_folder,
            )
            act_folder_storage.setEnabled(not is_empty)
            act_folder_move.setEnabled(not is_empty)
            act_folder_relink.setEnabled(has_missing)
            menu.exec(self.tbl_files.viewport().mapToGlobal(pos))
            return

        # 右键在文件节点上 → 显示文件操作菜单
        self._files_context_subfolder = item.data(0, Qt.UserRole + 2) or ""
        menu = QMenu(self)
        menu.addAction("▶  打开", self.action_open_current_file)
        menu.addAction("📂  在资源管理器中显示", self.action_reveal_current_file)
        menu.addSeparator()
        menu.addAction("🖼  设为封面", self.action_set_cover)
        menu.addSeparator()
        # task #29 文件存储位置管理
        menu.addAction("📦  转为仓储文件", self.action_convert_to_storage)
        menu.addAction("📂  移动文件到...", self.action_move_file)
        # task #29 T3a/T3b
        menu.addAction("🔧  重关联到外部文件...", self.action_relink_file)
        menu.addAction("🔗  替换链接目标...", self.action_replace_link_target)
        menu.addSeparator()
        # task #31a T4: F2 重命名
        menu.addAction("✏️  重命名", self.action_rename_file)
        menu.addAction("✏️  重命名物理文件", self.action_rename_physical_file)
        menu.addSeparator()
        menu.addAction("🗑  移除", self.action_delete_files)
        menu.exec(self.tbl_files.viewport().mapToGlobal(pos))

    # ============================================================ Drag & Drop
    def _install_dnd(self) -> None:
        """启用拖放：

        - 卡片视图、文件表通过 ProjectViewDnD/FilesTableDnD helper 处理。
        - DropZone 显示/隐藏由 QApplication 全局事件过滤器控制。
        """
        from PySide6.QtWidgets import QApplication

        # 主窗口本身也接受 drop（兜底）
        self.setAcceptDrops(True)

        QApplication.instance().installEventFilter(self)

        # DropZone 信号
        self.drop_zone.dropped.connect(self._on_dropzone_dropped)

        # 拖动时高亮项目卡片
        self._drag_hover_pid: int | None = None
        # 防抖：防止单次 drop 触发多次新建对话框
        self._drop_busy: bool = False

    def eventFilter(self, obj, ev):  # noqa: D401
        """全局监听 drag 进出，控制 DropZone 显隐。"""
        et = ev.type()
        if et == QEvent.FocusIn and obj is self.search_box:
            if self._search_menu_open:
                return super().eventFilter(obj, ev)
            if time.monotonic() - self._search_menu_last_closed_at < 0.4:
                return super().eventFilter(obj, ev)
            has_saved = bool(load_saved_searches(
                self.repo.get_setting(SAVED_SEARCHES_SETTING_KEY, "[]")
            ))
            has_history = bool(load_history(
                self.repo.get_setting(HISTORY_SETTING_KEY, "[]")
            ))
            if has_saved or has_history:
                QTimer.singleShot(0, self._show_search_menu)
        if et == QEvent.KeyPress and obj is self.search_box:
            if ev.key() == Qt.Key_Escape and self.search_box.text():
                self.search_box.clear()
                return True
        if et == QEvent.KeyPress and obj in (self.tbl_files, self.tbl_files.viewport()):
            if ev.key() == Qt.Key_F2:
                if ev.modifiers() & Qt.ShiftModifier:
                    self.action_rename_physical_file()
                else:
                    item = self.tbl_files.currentItem()
                    if item is not None and item.data(0, Qt.UserRole) is not None and item.data(0, Qt.UserRole) < 0:
                        self.action_rename_subfolder()
                    elif item is not None:
                        self.tbl_files.editItem(item, 1)
                return True
        if et == QEvent.DragEnter:
            md = ev.mimeData() if hasattr(ev, "mimeData") else None
            if md and md.hasUrls():
                self._show_drop_zone()
        elif et == QEvent.Drop:
            # 任何位置完成 drop 都隐藏
            self._hide_drop_zone()
        elif et == QEvent.DragLeave:
            # 离开顶层窗口时隐藏（子控件之间切换不会触发顶层 leave）
            if obj is self:
                self._hide_drop_zone()
        return super().eventFilter(obj, ev)

    # 主窗口级兜底
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            self._show_drop_zone()

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dragLeaveEvent(self, _ev):
        self._hide_drop_zone()

    def dropEvent(self, ev):
        # 落到主窗口空白区（详情面板/工具栏等）：不做处理，仅隐藏
        self._hide_drop_zone()
        ev.ignore()

    # ---- DropZone 显隐 ----
    def _show_drop_zone(self) -> None:
        if not self.drop_zone.isVisible():
            self.drop_zone.show()

    def _hide_drop_zone(self) -> None:
        self.drop_zone.set_active(False)
        self.drop_zone.hide()
        self._set_drag_hover(None)

    # ---- 导入前检查 ----
    def _filter_library_paths(self, paths: list) -> list:
        """过滤掉库目录自身的路径，防止递归导入。返回过滤后的路径列表。"""
        lib_root = self.library.root.resolve()
        filtered: list[str] = []
        skipped = 0
        for raw in paths:
            p = Path(raw).resolve()
            try:
                p.relative_to(lib_root)
                skipped += 1  # 在库目录内，跳过
            except ValueError:
                filtered.append(str(p))
        if skipped:
            QMessageBox.warning(
                self, "提示",
                f"已跳过 {skipped} 个位于库目录内的路径（不能导入库自身）。"
            )
        return filtered

    def _warn_if_deep_or_large(self, files: list) -> bool:
        """检查待导入文件是否过深或过多，弹确认对话框。返回 True 表示继续。"""
        from ..models import PendingFile
        max_depth = 0
        for f in files:
            sf = f.subfolder if isinstance(f, PendingFile) else ""
            if sf:
                depth = sf.count("/") + 1
                max_depth = max(max_depth, depth)

        count = len(files)
        warnings: list[str] = []
        if max_depth >= 5:
            warnings.append(f"目录层级较深（最深 {max_depth} 层）")
        if count >= 500:
            warnings.append(f"文件数量较多（共 {count} 个）")

        if not warnings:
            return True

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Question)
        msg.setWindowTitle("导入确认")
        msg.setText("检测到以下情况，是否继续导入？")
        msg.setInformativeText("\n".join(f"• {w}" for w in warnings))
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.Yes)
        return msg.exec() == QMessageBox.Yes

    # ---- 信号槽：来自子组件的 drop ----
    def _on_dropped_on_project(self, pid: int, paths: list) -> None:
        if self._drop_busy:
            return
        self._drop_busy = True
        try:
            paths = self._filter_library_paths(paths)
            if not paths:
                return
            files = self._expand_paths(paths)
            if not files:
                self._warn_empty_import(paths)
                return
            self._drop_into_project(pid, files)
            self._hide_drop_zone()
        finally:
            self._drop_busy = False

    def _on_dropped_on_files_table(self, paths: list) -> None:
        if self._drop_busy:
            return
        self._drop_busy = True
        try:
            paths = self._filter_library_paths(paths)
            if not paths:
                return
            files = self._expand_paths(paths)
            if not files:
                if len(paths) == 1 and Path(paths[0]).is_dir():
                    self._create_empty_project(Path(paths[0]).name)
                else:
                    self._warn_empty_import(paths)
                return
            if not self._warn_if_deep_or_large(files):
                return
            if self._current_project_id is not None:
                self._drop_into_project(self._current_project_id, files)
            else:
                self._drop_create_project(files, source_paths=paths)
            self._hide_drop_zone()
        finally:
            self._drop_busy = False

    def _on_dropzone_dropped(self, paths: list) -> None:
        if self._drop_busy:
            return
        self._drop_busy = True
        try:
            self._hide_drop_zone()
            paths = self._filter_library_paths(paths)
            if not paths:
                return

            # task #28 T3：检测 ZIP 文件 → 走项目包导入
            zip_paths = [p for p in paths if Path(p).suffix.lower() == ".zip"]
            if zip_paths:
                # 有 ZIP 文件，弹确认让用户选择处理方式
                from PySide6.QtWidgets import QMessageBox
                ans = QMessageBox.question(
                    self, "检测到 ZIP 文件",
                    f"检测到 {len(zip_paths)} 个 ZIP 文件。\n\n"
                    "是导入为项目包，还是解压后作为普通文件夹处理？",
                    "导入项目包", "解压为文件夹", "取消",
                )
                if ans == 0:  # 导入项目包
                    self._run_batch_folder_import(zip_paths)
                    return
                elif ans == 2:  # 取消
                    return
                # 否则继续作为文件夹处理（解压后）

            # 分支：全是目录且 ≥ 2 个 → 走批量文件夹导入流程（task #10）
            from ..importer import split_paths_by_kind
            dirs, plain_files = split_paths_by_kind(paths)
            if dirs and not plain_files and len(dirs) >= 2:
                self._handle_multi_folder_drop(dirs)
                return
            # 否则沿用旧路径
            files = self._expand_paths(paths)
            if not files:
                # 空文件夹处理：单个空目录 → 创建 0 文件项目；否则提示
                if len(paths) == 1 and Path(paths[0]).is_dir():
                    self._create_empty_project(Path(paths[0]).name)
                else:
                    self._warn_empty_import(paths)
                return
            if not self._warn_if_deep_or_large(files):
                return
            self._drop_create_project(files, source_paths=paths)
        finally:
            self._drop_busy = False

    def _create_empty_project(self, title: str) -> None:
        """创建一个 0 文件的项目（用户拖入空文件夹时）。"""
        p = Project(title=title)
        self.repo.save_project(p)
        self.refresh_projects()
        self.statusBar().showMessage(f"已创建空项目「{title}」", 4000)

    def _warn_empty_import(self, paths: list) -> None:
        """所有路径展开后为空时提示用户。"""
        from ..importer import split_paths_by_kind
        dirs, _ = split_paths_by_kind(paths)
        if dirs:
            QMessageBox.information(
                self, "提示",
                f"拖入的 {len(dirs)} 个文件夹都是空的，没有文件可导入。"
            )
        else:
            QMessageBox.information(self, "提示", "没有文件可导入。")

    # ------------------------------------------------------------------
    # 批量文件夹导入（task #10）
    # ------------------------------------------------------------------
    def _handle_multi_folder_drop(self, dirs: list) -> None:
        """≥ 2 个目录拖到 DropZone：先问"单/多项目"，再走批量导入。"""
        from .folder_drop_mode_dialog import FolderDropModeDialog
        mode_dlg = FolderDropModeDialog(len(dirs), parent=self)
        if mode_dlg.exec() != QDialog.Accepted:
            return  # 用户取消

        if mode_dlg.mode() == "merge":
            # 合并为一个新项目：沿用旧路径（_drop_create_project）
            files = self._expand_paths([str(d) for d in dirs])
            if not files:
                self._warn_empty_import([str(d) for d in dirs])
                return
            if not self._warn_if_deep_or_large(files):
                return
            self._drop_create_project(files, source_paths=[str(d) for d in dirs])
            return

        # separate 模式：批量导入
        self._run_batch_folder_import(dirs)

    def _run_batch_folder_import(self, dirs: list) -> None:
        """对每个文件夹独立建项目（task #10 主流程）。"""
        from pathlib import Path as _Path
        from ..importer import (
            cleanup_extracted_zips, import_folder_as_project, scan_folders,
        )
        from .import_dialog import FieldPolicyAskDialog, ImportDialog

        plans = scan_folders([_Path(d) for d in dirs], self.repo)
        try:
            dlg = ImportDialog(plans, parent=self)
            if dlg.exec() != QDialog.Accepted:
                return
            options = dlg.options()

            # 进度对话框：以"文件夹个数"为粗粒度
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import QProgressDialog
            prog = QProgressDialog(
                "正在导入项目…", "取消", 0, len(plans), self,
            )
            prog.setWindowTitle("批量导入文件夹")
            prog.setWindowModality(Qt.WindowModal)
            prog.setMinimumDuration(0)
            prog.setAutoClose(False)
            prog.setAutoReset(False)
            prog.show()

            # 单项目级未匹配字段询问回调
            def _ask_field_policy(folder, fields):
                ask = FieldPolicyAskDialog(folder, fields, parent=self)
                if ask.exec() != QDialog.Accepted:
                    return options.field_policy  # 用户取消 → 回退默认
                return ask.policy()

            results: list = []
            all_warnings: list[str] = []
            last_pid: int | None = None
            cancelled = False
            for i, plan in enumerate(plans):
                if prog.wasCanceled():
                    cancelled = True
                    break
                prog.setValue(i)
                prog.setLabelText(f"导入「{plan.folder.name}」…")
                try:
                    res = import_folder_as_project(
                        self.repo, self.library, plan, options,
                        progress=None,
                        ask_field_policy=_ask_field_policy,
                    )
                    results.append(res)
                    if res.warnings:
                        all_warnings.extend(
                            f"[{plan.folder.name}] {w}" for w in res.warnings
                        )
                    last_pid = res.project_id
                except Exception as e:
                    all_warnings.append(f"[{plan.folder.name}] 导入失败：{e}")
            prog.setValue(len(plans))
            prog.close()

            # 刷新 UI
            self.refresh_projects()
            if last_pid is not None:
                self._select_project_by_id(last_pid)

            # 统计反馈
            n_ok = len(results)
            n_files_total = sum(r.n_files for r in results)
            msg = (
                f"批量导入完成：{n_ok} / {len(plans)} 个项目，共 {n_files_total} 个文件"
            )
            if cancelled:
                msg = "批量导入已取消（" + msg + "）"
            self.statusBar().showMessage(msg, 6000)

            # 如果有 warning，弹一次汇总
            if all_warnings:
                from PySide6.QtWidgets import QMessageBox
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Information)
                box.setWindowTitle("批量导入：警告与提示")
                head = msg + f"\n\n{len(all_warnings)} 条提示信息："
                box.setText(head)
                box.setDetailedText("\n".join(all_warnings))
                box.exec()
        finally:
            cleanup_extracted_zips(plans)



    def _on_drag_hover_changed(self, pid) -> None:
        self._set_drag_hover(pid)

    def _set_drag_hover(self, pid) -> None:
        if pid == self._drag_hover_pid:
            return
        self._drag_hover_pid = pid
        if pid is not None:
            idx = self.proj_model.index_of_id(int(pid))
            if idx.isValid():
                self.proj_view.setCurrentIndex(idx)
        self.proj_view.viewport().update()

    def _expand_paths(self, paths: list) -> list:
        """把混合路径展开为 [PendingFile, ...]。

        - 文件 → PendingFile(src=绝对路径, subfolder="")
        - 目录 → 递归收集所有文件，目录名作为 subfolder 前缀
          例：拖入 myfolder/sub/a.txt → subfolder="myfolder/sub"
        - import_ignore_dotfiles 设置为 "1" 时，跳过 . 开头的文件和目录
        """
        from ..models import PendingFile
        ignore_dot = self.repo.get_setting("import_ignore_dotfiles", "1") == "1"
        out: list[PendingFile] = []
        for raw in paths:
            p = Path(raw)
            if p.is_file():
                if ignore_dot and p.name.startswith("."):
                    continue
                out.append(PendingFile(src=p.resolve(), subfolder=""))
            elif p.is_dir():
                root = p.resolve()
                dir_name = root.name
                for sub in sorted(root.rglob("*")):
                    if ignore_dot and any(
                        part.startswith(".") for part in sub.relative_to(root).parts
                    ):
                        continue
                    if sub.is_file():
                        rel = sub.parent.relative_to(root)
                        if str(rel) == ".":
                            subfolder = dir_name
                        else:
                            subfolder = f"{dir_name}/{rel.as_posix()}"
                        out.append(PendingFile(src=sub.resolve(), subfolder=subfolder))
        return out

    # ---- 业务实现 ----
    def _drop_into_project(self, pid: int, files: list) -> None:
        if not files:
            return
        p = self.repo.get_project(pid)
        if not p:
            return
        # 拖放也走"添加文件"对话框，统一存储方式询问语义
        default_mode = self.repo.get_setting("default_storage_mode", "link") or "link"
        storage, _label = self._ask_storage_for_import(files, default_mode)
        if storage is None:
            return  # 用户取消
        added = self._import_files(p, files, ask_label=False, storage=storage)
        self._select_project_by_id(pid)
        self._show_project(self.repo.get_project(pid))
        self.statusBar().showMessage(
            f"已加入「{p.title}」：{added} / {len(files)} 个文件", 4000
        )

    def _drop_create_project(
        self, files: list, source_paths: list | None = None,
    ) -> None:
        from ..models import PendingFile
        if not files:
            return
        # 默认标题：
        # 1) 拖入的原始路径里只要有文件夹，就用第一个文件夹的名字（按目录组织时更直观）
        # 2) 否则用第一个文件的 stem
        default_title = ""
        if source_paths:
            for raw in source_paths:
                p = Path(raw)
                if p.is_dir():
                    default_title = p.name
                    break
        if not default_title:
            first = files[0]
            default_title = first.src.stem if isinstance(first, PendingFile) else Path(first).stem
        title, ok = QInputDialog.getText(
            self, "新建项目",
            f"将 {len(files)} 个文件加入新项目。请输入项目标题：",
            text=default_title,
        )
        if not ok:
            return
        title = title.strip() or default_title
        default_mode = self.repo.get_setting("default_storage_mode", "link") or "link"
        storage, _label = self._ask_storage_for_import(files, default_mode)
        if storage is None:
            return  # 用户取消（不创建项目）
        p = Project(title=title)
        pid = self.repo.save_project(p)
        p.id = pid
        added = self._import_files(p, files, ask_label=False, storage=storage)
        self.refresh_projects()
        self._select_project_by_id(pid)
        self.statusBar().showMessage(
            f"新建项目「{title}」并加入 {added} / {len(files)} 个文件", 4000
        )
