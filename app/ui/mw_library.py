"""库菜单（切换/新建/删除/备份恢复/工具菜单）（task #35：从 main_window.py 拆分，方法体未改动）。

Mixin：库菜单（切换/新建/删除/备份恢复/工具菜单）
"""
from __future__ import annotations

import json
import logging
import shutil
import warnings
from pathlib import Path

from PySide6.QtCore import (
    QEvent, QItemSelectionModel, QSize, Qt, QTimer,
)
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
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyledItemDelegate,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

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
from ..utils import (
    OperationCancelled,
    detect_kind,
    human_size as _human_size,
    move_to_trash,
    open_with_default_app,
    reveal_in_explorer,
)
from .cover_cache import get_cover
from .dialogs import ask_yes_no_cancel, confirm, error, info, warn
from .dnd import FilesTableDnD, ProjectViewDnD
from .export_dialog import ExportDialog
from .files_table_columns import (
    COLUMNS as FILES_COLUMNS,
    SETTING_KEY as FILES_COLUMNS_SETTING_KEY,
    column_by_key as files_column_by_key,
    dump_prefs as files_dump_prefs,
    load_prefs as files_load_prefs,
    resolve_pref as files_resolve_pref,
)
from .palette import current as _current_palette
from .preview import PreviewPanel
from .project_card import (
    CARD_W as _CARD_W,
    COVER_H as _COVER_H,
    PAD as _CARD_PAD,
    ProjectCardDelegate,
    ProjectModel,
)
from .project_dialog import ProjectDialog
from .search_completion import SearchBoxKeyFilter, SearchCompletionPopup, current_token
from .settings import SettingsDialog
from .workers import ExportSnapshotRepo, run_with_progress

logger = logging.getLogger("llm_cabinet.ui")


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



class LibraryMenuMixin:
    """库菜单（切换/新建/删除/备份恢复/工具菜单）"""

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
        if not confirm(
            self, "回到欢迎页",
            "关闭当前库并回到欢迎页？\n\n"
            "应用会重启，当前库不会被删除（仍在最近列表里）。\n"
            "未保存的修改会丢失。",
            yes="回到欢迎页",
        ):
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
            info(self, "提示", "已是当前库。")
            return

        if not is_library_dir(path):
            warn(
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
        if not confirm(
            self, "切换库",
            f"切换到库：\n{label or path.name}\n{path}\n\n"
            "应用将重启以加载新库，是否继续？",
            yes="切换",
        ):
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
        n_files_total = self.repo.count_files_total()
        try:
            db_size = _Path(self.db_path).stat().st_size if self.db_path else 0
        except OSError:
            db_size = 0

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
        # library/ 大小：后台线程统计回填（task #33，避免大库卡 UI）
        lbl_lib_size = QLabel("统计中…")
        form.addRow("library/ 大小：", lbl_lib_size)
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

        # 后台统计 library/ 大小（递归求和可能耗时，task #33）
        from PySide6.QtCore import QObject as _QObj, QThread as _QThread, Signal as _Sig

        class _LibSizeWorker(_QObj):
            done = _Sig(int)

            def __init__(self, root):
                super().__init__()
                self._root = root

            def run(self):
                total = 0
                try:
                    for p in self._root.rglob("*"):
                        if p.is_file():
                            try:
                                total += p.stat().st_size
                            except OSError:
                                pass
                except OSError:
                    pass
                self.done.emit(total)

        size_thread = _QThread(dlg)  # 随对话框销毁
        size_worker = _LibSizeWorker(_Path(self.library.root))
        size_worker.moveToThread(size_thread)
        size_thread.started.connect(size_worker.run)

        def _on_lib_size(total: int) -> None:
            try:
                lbl_lib_size.setText(_human_size(total))
            except RuntimeError:
                pass  # 对话框已关闭
            size_thread.quit()

        size_worker.done.connect(_on_lib_size)
        size_thread.finished.connect(size_worker.deleteLater)
        size_thread.finished.connect(size_thread.deleteLater)
        # 防止 worker 被 GC（thread 有 parent，worker 挂到线程引用上）
        dlg._lib_size_refs = (size_thread, size_worker)
        size_thread.start()

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
                info(
                    dlg, "提示", "请选择**其它**库的目录（不能是当前库）。",
                )
                cmb.setCurrentIndex(prev_idx)
                return
            if not is_library_dir(path):
                warn(
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
            warn(self, "无法解析库", f"无法从 {src_root} 解析出 db 路径。")
            return
        if src_db.resolve() == _Path(self.db_path).resolve():
            info(self, "提示", "请选择**其它**库（不能是当前库）。")
            return

        # 读出 llm_config + 默认 provider / 默认语言
        keys = ["llm_config", "llm_default_provider", "llm_default_language"]
        imported = import_settings_from_other_db(src_db, keys)
        if not imported:
            warn(
                self, "未读到配置",
                "未能从该库读取到 llm_config 等设置，文件可能不可读或格式不符。",
            )
            return

        # 二次确认
        keys_str = "\n".join(f"  • {k}" for k in imported.keys())
        if not confirm(
            self, "确认导入",
            f"将把以下 {len(imported)} 项设置写入当前库（覆盖同名项）：\n{keys_str}\n\n确认？",
            yes="导入",
        ):
            return
        for k, val in imported.items():
            self.repo.set_setting(k, val)
        # task #42：源库 key 存在 keyring（哨兵形式）时，按源作用域解出并写入当前作用域
        note = ""
        if "llm_config" in imported:
            from ..llm.config import rekey_imported_llm_config
            ok, fail = rekey_imported_llm_config(self.repo, src_root)
            if ok:
                note += f"\n{ok} 个平台的 API Key 已随配置迁移到本机凭据管理器。"
            if fail:
                note += f"\n{fail} 个平台的 API Key 未能迁移，请在「设置 → API」重新填写。"
        info(self, "完成", f"已导入 {len(imported)} 项。{note}")


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
                if not confirm(
                    dlg, "从列表移除当前库",
                    "当前正在使用这个库。从列表移除后，应用会**重启并回到欢迎页**\n"
                    "让你重新选择库（库目录与文件都不会被删除，只是从最近列表移除）。\n\n"
                    "继续？",
                    yes="移除",
                ):
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
            if not confirm(
                dlg, "确认删除（1/2）",
                f"将删除库『{display}』：\n\n{p}\n\n"
                f"库数据占用：{_human_size(scan.owned_size)}\n"
                + "\n".join(extras)
                + "\n\n此操作**不可恢复**。继续？",
                yes="继续", danger=True,
            ):
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
                info(dlg, "已取消", "名称不匹配，取消删除。")
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
                    warn(
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
                info(
                    dlg, "完成",
                    f"已删除库数据，外来文件保留在：\n{p}",
                )
                return

            # mode == "all"：删 owned + foreign，保留 app_global（如 cabinet.json）
            failures = delete_library_all(p)
            if failures:
                msg = "\n".join(f"• {fp.name}：{err}" for fp, err in failures[:10])
                error(
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
        """库一致性检查（task #14 T1；task #36 线程化：扫描放后台线程）。"""
        from ..library_check import run_consistency_check, snapshot_file_rows

        # 文件清单在主线程快照（sqlite 连接不能跨线程），worker 只做 stat
        rows = snapshot_file_rows(self.repo)

        def _do(progress_cb, is_cancelled):
            def _cb(done, total, name):
                if is_cancelled():
                    raise OperationCancelled()
                progress_cb(done, total, name)
            return run_consistency_check(
                self.repo, self.library, progress=_cb, rows=rows,
            )

        run_with_progress(
            self, "检查库一致性", "正在扫描库...", _do,
            on_done=self._show_consistency_report,
            on_error=lambda msg: error(self, "检查失败", msg),
        )


    def _show_consistency_report(self, rep) -> None:
        """一致性检查报告对话框（扫描完成后在主线程弹出）。"""
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import (
            QButtonGroup, QDialog, QDialogButtonBox, QHeaderView, QLabel,
            QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
        )
        from ..library_check import apply_consistency_action

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
        info(
            self, "完成",
            f"已处理：标记 {n_marked} 个 / 移除 {n_deleted} 个。",
        )
        self.refresh_projects()


    def _tools_backup_library(self) -> None:
        """备份当前库（task #14 T2；task #36 线程化）。"""
        from PySide6.QtWidgets import QFileDialog
        from ..cabinet import scan_library_for_deletion
        from ..library_check import backup_library
        from datetime import datetime as _dt
        from pathlib import Path as _Path

        if self.library_root is None:
            warn(self, "不可用", "当前库目录未知，无法备份。")
            return

        # 扫一下目录里的"外来内容"（用户自己放进去的非库文件），
        # 让用户决定要不要一并打包；默认包含
        scan = scan_library_for_deletion(_Path(self.library_root))
        include_foreign = True
        if scan.foreign:
            ans = ask_yes_no_cancel(
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
                "「一并打包」：得到完整目录快照（推荐——恢复后能拿回这些文件）\n"
                "「只打包库自身」：仅 cabinet.db / library/ / 标记文件等，"
                "备份体积更小",
                yes="一并打包", no="只打包库自身", default="yes",
            )
            if ans == "cancel":
                return
            include_foreign = (ans == "yes")

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
        self.repo.wal_checkpoint()

        # task #36：打包放后台线程（中途取消会留残包，故不提供取消按钮）
        def _do(progress_cb, _is_cancelled):
            return backup_library(
                _Path(self.library_root), _Path(target),
                include_foreign=include_foreign,
                progress=progress_cb,
            )

        def _on_done(out):
            try:
                self.repo.set_setting("last_backup_dir", str(_Path(target).parent))
            except Exception:
                logger.warning("记录备份目录失败", exc_info=True)
            info(
                self, "完成",
                f"备份完成：\n{out}\n大小：{_human_size(out.stat().st_size)}",
            )

        run_with_progress(
            self, "备份库", "正在打包库目录...", _do,
            on_done=_on_done,
            on_error=lambda msg: error(self, "备份失败", msg),
            cancellable=False,
        )


    def _tools_restore_library(self) -> None:
        """从备份恢复一个库（解到新目录，然后用『切换库』流程打开）。"""
        from PySide6.QtWidgets import QFileDialog
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
            warn(
                self, "目录不为空",
                f"目标目录非空：{target_dir}\n请选一个空目录或新建目录。",
            )
            return

        # task #36：解压放后台线程（中途取消会留残目录，故不提供取消按钮）
        def _do(progress_cb, _is_cancelled):
            return restore_library(_Path(zip_path), tp, progress=progress_cb)

        def _on_done(root):
            ans = confirm(
                self, "恢复完成",
                f"已恢复到：\n{root}\n\n"
                "是否立即切换到这个库？应用将重启以加载新库。",
                yes="切换",
            )
            if ans and self.cabinet_config is not None:
                self.cabinet_config.touch(root)
                self.cabinet_config.save()
                # 直接走 _confirm_and_restart_to 的"重启"分支，不再二次弹"切换库"确认框
                # （恢复完成的对话框文案已经把"应用将重启"说明了，再多弹一次冗余）
                self._pending_switch_to = root
                self.close()

        run_with_progress(
            self, "恢复库", "正在解压...", _do,
            on_done=_on_done,
            on_error=lambda msg: error(self, "恢复失败", msg),
            cancellable=False,
        )


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
