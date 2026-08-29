"""启动期 Welcome 对话框（task #15 T3 + 失效兜底）。

两种触发场景：

1. **首次安装**：``cabinet.json`` 不存在 → 用户从未用过本应用 → 引导建库 / 打开
2. **启动期兜底**：``cabinet.json`` 存在但 ``active_library`` 已不可用，或 recent
   全失效 / 全空（用户在主界面里把所有库都"删除整个库"了）→ 弹本对话框让用户
   重新选

选项（不再有"使用默认位置" — appdata 不再充当默认库，仅存软件全局配置）：
- **新建库**（→ 走 task #15 T1 多页向导）
- **打开最近使用的库**（仅当 recent 非空时显示；列出 cabinet.json 里登记过的库）
- **打开其它已有库目录**（系统目录选择器，目录必须含 ``.llm-cabinet`` 标记）

「退出」 → 直接退出应用，不写任何 cabinet.json。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..cabinet import CabinetConfig, is_library_dir
from .dialogs import error, info, warn


# 对话框返回值（QDialog.DialogCode 自定义复用）
RESULT_NEW_CUSTOM = 100        # 新建（自定义位置） → 走 T1 向导
RESULT_OPEN_EXISTING = 102     # 打开已有目录（已知 recent / 系统目录选择器）


class WelcomeDialog(QDialog):
    """欢迎 / 启动兜底对话框。

    用法：
        dlg = WelcomeDialog(cabinet_config, parent=..., stale_active=Path|None)
        rc = dlg.exec()
        if rc == RESULT_OPEN_EXISTING:
            path = dlg.opened_path  # 用户选的现有库目录
        elif rc == RESULT_NEW_CUSTOM:
            ...  # 主程序后续走 NewLibraryWizard
        else:
            sys.exit(0)
    """

    def __init__(
        self,
        cabinet_config: CabinetConfig,
        *,
        parent=None,
        stale_active: Optional[Path] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("欢迎使用 LLM Cabinet")
        # 高度加大以容纳放大后的品牌区（图标 160 + 大字标题 + 副标题）+ 4 个选项区块
        self.setMinimumSize(700, 720)
        self.cabinet_config = cabinet_config
        self.opened_path: Optional[Path] = None
        # 上次活动库失效时，UI 角落显示提示
        self._stale_active: Optional[Path] = stale_active

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 36, 40, 18)
        outer.setSpacing(12)

        # ====== 顶部品牌区（图标 + 标题 + 副标题），约占 40% 高度 ======
        brand_box = QWidget()
        brand_lay = QVBoxLayout(brand_box)
        brand_lay.setContentsMargins(0, 0, 0, 0)
        brand_lay.setSpacing(14)
        brand_lay.addStretch(1)

        # 应用图标（居中，160×160，高 DPI 友好）
        from ..utils import app_icon_path
        icon_path = app_icon_path()
        if icon_path is not None:
            target_logical = 160
            dpr = self.devicePixelRatioF() or 1.0
            target_phys = int(round(target_logical * dpr))
            ico_lbl = QLabel()
            ico_lbl.setAlignment(Qt.AlignCenter)
            pix = QIcon(str(icon_path)).pixmap(target_phys, target_phys)
            if not pix.isNull():
                pix.setDevicePixelRatio(dpr)
                ico_lbl.setPixmap(pix)
                # setFixedSize 替代 setFixedHeight：保证 label 不被横向压缩、
                # pixmap 能足额显示
                ico_lbl.setFixedSize(target_logical, target_logical)
                brand_lay.addWidget(ico_lbl, 0, Qt.AlignCenter)

        # 标题
        # 用 stylesheet 写字号，绕开 theme.py 全局 `* { font-size: 13px }`
        # 通配符规则——QFont.setPointSize 在 QSS 之下不生效。
        ttl = QLabel("LLM Cabinet")
        ttl.setStyleSheet("font-size: 32pt; font-weight: 700;")
        ttl.setAlignment(Qt.AlignCenter)
        brand_lay.addWidget(ttl)

        # 副标题
        sub = QLabel("你的本地资料库 / AI 元数据助理")
        sub.setStyleSheet("font-size: 13pt;")
        sub.setAlignment(Qt.AlignCenter)
        sub.setProperty("muted", True)
        brand_lay.addWidget(sub)
        brand_lay.addStretch(1)
        # 提高 brand_box 最小高度匹配新尺寸（图标 160 + 标题 ~60 + 副标题 ~28
        # + 间距留白，整体接近 320）
        brand_box.setMinimumHeight(320)
        outer.addWidget(brand_box, 2)  # stretch=2

        # 启动兜底场景：失效的 active 用红字提示
        if stale_active is not None:
            warn = QLabel(
                f"<span style='color:#c14545'>⚠ 上次打开的库已不可用："
                f"<code>{stale_active}</code><br/>"
                "可能被移动 / 删除 / 损坏。请在下方重新选择。</span>"
            )
            warn.setTextFormat(Qt.RichText)
            warn.setWordWrap(True)
            outer.addWidget(warn)

        # ====== 选项区（占剩下 60%） ======
        # 单行按钮统一样式：左对齐 + 较大内边距（让文字离左右边界都有呼吸感）
        BTN_QSS = (
            "QPushButton { text-align: left; padding: 12px 20px; "
            "  font-size: 11pt; }"
        )

        # 选项 1：新建库（单行）
        b_new = QPushButton("📁    选择空目录新建库")
        b_new.setMinimumHeight(48)
        b_new.setCursor(Qt.PointingHandCursor)
        b_new.setFocusPolicy(Qt.NoFocus)
        b_new.setAutoDefault(False)
        b_new.setDefault(False)
        b_new.setStyleSheet(BTN_QSS)
        b_new.clicked.connect(lambda: self.done(RESULT_NEW_CUSTOM))
        outer.addWidget(b_new)

        # 选项 2：打开最近使用的库（仅当 recent 非空时显示）
        if self.cabinet_config.recent_libraries:
            recent_box = QFrame()
            recent_box.setFrameShape(QFrame.StyledPanel)
            rb_lay = QVBoxLayout(recent_box)
            rb_lay.setContentsMargins(14, 10, 14, 12)
            rb_lay.setSpacing(6)
            rb_title = QLabel("<b>📂   打开最近使用的库</b>")
            rb_title.setTextFormat(Qt.RichText)
            rb_lay.addWidget(rb_title)
            self.lst_recent = QListWidget()
            # 每项单行 → 整体高度按条数自适应，但锁个上限避免太长
            self.lst_recent.setUniformItemSizes(True)
            # 大约一行 22~26 px；最多展示 5 条；高度可再加一点 padding
            visible_rows = min(len(self.cabinet_config.recent_libraries), 5)
            self.lst_recent.setFixedHeight(max(1, visible_rows) * 26 + 12)
            for h in self.cabinet_config.recent_libraries:
                stale = (
                    self._stale_active is not None
                    and h.path.resolve() == self._stale_active.resolve()
                )
                tag = "  ⚠ 不可用" if stale else ""
                # 一行：名字 — 路径
                it = QListWidgetItem(f"{h.display_name}  —  {h.path}{tag}")
                it.setData(Qt.UserRole, str(h.path))
                it.setToolTip(str(h.path))
                if stale:
                    it.setFlags(it.flags() & ~Qt.ItemIsEnabled)
                self.lst_recent.addItem(it)
            self.lst_recent.itemDoubleClicked.connect(
                lambda _it: self._on_open_recent_clicked()
            )
            rb_lay.addWidget(self.lst_recent)
            row = QHBoxLayout()
            row.addStretch(1)
            # 「管理列表...」语义与主菜单 → 库 → 最近打开 → 管理列表... 一致；
            # welcome 这个入口只暴露「移除 / 改名」两条破坏性较低的操作，避免误
            # 触"删除整个库"这种重操作（那个仍然只在主界面的同名对话框里给）。
            b_manage_recent = QPushButton("管理列表...")
            b_manage_recent.setAutoDefault(False)
            b_manage_recent.setDefault(False)
            b_manage_recent.setProperty("flat", True)
            b_manage_recent.clicked.connect(self._on_manage_recent_clicked)
            row.addWidget(b_manage_recent)
            b_open_recent = QPushButton("打开选中的库")
            b_open_recent.setAutoDefault(False)
            b_open_recent.setDefault(False)
            b_open_recent.clicked.connect(self._on_open_recent_clicked)
            row.addWidget(b_open_recent)
            rb_lay.addLayout(row)
            outer.addWidget(recent_box)
        else:
            self.lst_recent = None  # type: ignore[assignment]

        # 选项 3：打开其它已有库目录（单行）
        b_open = QPushButton("🔍    打开其它已有库目录...")
        b_open.setMinimumHeight(48)
        b_open.setCursor(Qt.PointingHandCursor)
        b_open.setFocusPolicy(Qt.NoFocus)
        b_open.setAutoDefault(False)
        b_open.setDefault(False)
        b_open.setStyleSheet(BTN_QSS)
        b_open.clicked.connect(self._on_open_existing_browse)
        outer.addWidget(b_open)

        # 选项 4：从备份恢复库（单行）
        b_restore = QPushButton("📥    从备份 zip 恢复库...")
        b_restore.setMinimumHeight(48)
        b_restore.setCursor(Qt.PointingHandCursor)
        b_restore.setFocusPolicy(Qt.NoFocus)
        b_restore.setAutoDefault(False)
        b_restore.setDefault(False)
        b_restore.setStyleSheet(BTN_QSS)
        b_restore.clicked.connect(self._on_restore_from_backup)
        outer.addWidget(b_restore)

        outer.addStretch(1)

        # 底部：左下角「关于」/ 右下角「退出」
        bottom = QHBoxLayout()
        b_about = QPushButton("ℹ 关于")
        b_about.setAutoDefault(False)
        b_about.setDefault(False)
        b_about.clicked.connect(self._on_about)
        bottom.addWidget(b_about)
        bottom.addStretch(1)
        b_quit = QPushButton("退出")
        b_quit.setAutoDefault(False)
        b_quit.setDefault(False)
        b_quit.clicked.connect(self.reject)
        bottom.addWidget(b_quit)
        outer.addLayout(bottom)

    # ---- 关于 -------------------------------------------------------------
    def _on_about(self) -> None:
        from .about_dialog import AboutDialog
        AboutDialog(self).exec()

    # ---- 选项 2：打开最近使用的库 -----------------------------------------
    def _on_open_recent_clicked(self) -> None:
        if self.lst_recent is None:
            return
        it = self.lst_recent.currentItem()
        if it is None:
            info(self, "提示", "请先在列表里选一个库。")
            return
        path = Path(str(it.data(Qt.UserRole)))
        if not is_library_dir(path):
            warn(
                self, "目录无效",
                f"目录\n  {path}\n不再是有效的库（标记 / cabinet.db 缺失）。\n\n"
                "可能已被移动 / 删除 / 损坏；请改用「打开其它已有库目录」"
                "或「新建库」。",
            )
            return
        self.opened_path = path
        self.done(RESULT_OPEN_EXISTING)

    # ---- 选项 2 副 ：管理最近列表（移除 / 改名） ---------------------------
    # 语义对齐主菜单 → 库 → 最近打开 → 管理列表...：
    # - 「从列表移除」：从 cabinet.json 的 recent 中删除该条（**不动磁盘**）
    # - 「改名」：修改显示名（仅本地展示，不动目录名）
    # 不在 welcome 里给「切换到此库」（用户用列表 + 「打开选中的库」就能切），
    # 也不给「删除整个库」（破坏性操作仍然只在主界面同名对话框里给）。
    def _on_manage_recent_clicked(self) -> None:
        if self.lst_recent is None:
            return
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtGui import QAction as _QA
        from PySide6.QtWidgets import (
            QDialogButtonBox, QInputDialog, QListWidget, QListWidgetItem,
            QMenu,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("管理最近打开的库")
        dlg.resize(560, 360)
        v = QVBoxLayout(dlg)
        tip = QLabel(
            "右键单条目可「从列表移除」/「改名」。这两项都不会改动磁盘上的库。"
        )
        tip.setProperty("hint", True)
        tip.setWordWrap(True)
        v.addWidget(tip)
        lst = QListWidget()
        lst.setContextMenuPolicy(_Qt.CustomContextMenu)
        v.addWidget(lst)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        v.addWidget(bb)

        def _refresh():
            lst.clear()
            for h in self.cabinet_config.recent_libraries:
                it = QListWidgetItem(f"{h.display_name}\n  {h.path}")
                it.setData(_Qt.UserRole, str(h.path))
                lst.addItem(it)

        def _remove_from_list(p: Path):
            self.cabinet_config.remove(p)
            self.cabinet_config.save()
            _refresh()

        def _rename_lib(p: Path):
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

        def _on_menu(pos):
            it = lst.itemAt(pos)
            if it is None:
                return
            path = Path(str(it.data(_Qt.UserRole)))
            menu = QMenu(dlg)
            a_rm = _QA("从列表移除", dlg)
            a_rm.triggered.connect(lambda _c=False: _remove_from_list(path))
            menu.addAction(a_rm)
            a_ren = _QA("改名...", dlg)
            a_ren.triggered.connect(lambda _c=False: _rename_lib(path))
            menu.addAction(a_ren)
            menu.exec(lst.viewport().mapToGlobal(pos))

        lst.customContextMenuRequested.connect(_on_menu)
        _refresh()
        dlg.exec()

        # 关闭管理对话框后，回灌外层 welcome 列表 —— 保持显示与 cabinet.json 同步
        self._reload_recent_list()

    def _reload_recent_list(self) -> None:
        """重新填充 ``self.lst_recent``，反映 cabinet.json 当前内容。
        渲染规则与 ``__init__`` 里的初次填充完全一致（避免管理对话框关闭后
        视觉与初次进入不一致）。"""
        if self.lst_recent is None:
            return
        from PySide6.QtWidgets import QListWidgetItem
        self.lst_recent.clear()
        for h in self.cabinet_config.recent_libraries:
            stale = (
                self._stale_active is not None
                and h.path.resolve() == self._stale_active.resolve()
            )
            tag = "  ⚠ 不可用" if stale else ""
            it = QListWidgetItem(f"{h.display_name}  —  {h.path}{tag}")
            it.setData(Qt.UserRole, str(h.path))
            it.setToolTip(str(h.path))
            if stale:
                it.setFlags(it.flags() & ~Qt.ItemIsEnabled)
            self.lst_recent.addItem(it)

    # ---- 选项 3：打开其它已有库目录（浏览） --------------------------------
    def _on_open_existing_browse(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "选择已有的库目录（必须含 .llm-cabinet 标记）",
        )
        if not d:
            return  # 用户取消选择，不关闭 Welcome
        path = Path(d)
        if not is_library_dir(path):
            warn(
                self, "目录无效",
                f"目录 {path} 不是一个有效的 LLM Cabinet 库（缺少 .llm-cabinet 标记）。\n"
                "请选择由本应用之前创建过的目录，或选「新建库」走新建流程。",
            )
            return
        self.opened_path = path
        self.done(RESULT_OPEN_EXISTING)

    # ---- 选项 4：从备份 zip 恢复库 ----------------------------------------
    def _on_restore_from_backup(self) -> None:
        """从 ``backup_library`` 产生的 zip 解出一个库目录，然后当作"已有库"打开。

        与「工具 → 📥 从备份恢复库...」（``MainWindow._tools_restore_library``）
        共享底层 ``app.library_check.restore_library``；区别仅在于本入口完成后
        不走"切换库重启"，而是直接走 ``RESULT_OPEN_EXISTING`` 让 main 把它当作
        本次启动要打开的库。
        """
        from PySide6.QtWidgets import QProgressDialog
        from ..library_check import restore_library

        # Step 1：选 zip
        zip_path, _ = QFileDialog.getOpenFileName(
            self, "选择备份 zip 文件", "",
            "ZIP 备份 (*.zip);;所有文件 (*)",
        )
        if not zip_path:
            return
        # Step 2：选解压目标目录（须空 / 不存在）
        target_dir = QFileDialog.getExistingDirectory(
            self, "选择解压目标目录（须为空目录）",
        )
        if not target_dir:
            return
        tp = Path(target_dir)
        try:
            if tp.exists() and tp.is_dir() and any(tp.iterdir()):
                warn(
                    self, "目录不为空",
                    f"目标目录非空：{tp}\n请选一个空目录或新建目录。",
                )
                return
        except OSError as e:
            warn(self, "目录不可访问", str(e))
            return

        # Step 3：解压 + 进度对话框（zipfile 没有细粒度进度，做一个忙状态指示）
        prog = QProgressDialog("正在解压备份...", None, 0, 0, self)
        prog.setWindowTitle("从备份恢复库")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.show()
        try:
            root = restore_library(Path(zip_path), tp)
        except Exception as e:  # noqa: BLE001
            prog.close()
            error(self, "恢复失败", str(e))
            return
        prog.close()

        # 解出来若不是有效库（zip 损坏 / 不是本应用产物）→ 拦下来不让进主界面
        if not is_library_dir(root):
            warn(
                self, "恢复结果无效",
                f"解压完成的目录\n  {root}\n不是有效的 LLM Cabinet 库"
                "（缺少 .llm-cabinet 标记 / cabinet.db）。\n请检查备份文件来源。",
            )
            return

        info(
            self, "恢复完成",
            f"已从备份恢复到：\n{root}\n\n点击 OK 立即打开该库。",
        )
        self.opened_path = root
        self.done(RESULT_OPEN_EXISTING)
