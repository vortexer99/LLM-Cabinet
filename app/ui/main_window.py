"""主窗口：左侧项目卡片墙 / 中间文件列表 / 右侧详情+预览。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt
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
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..library import Library
from ..models import FileItem, Project
from ..repository import Repository
from ..utils import detect_kind, open_with_default_app, reveal_in_explorer
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


class MainWindow(QMainWindow):
    def __init__(self, repo: Repository, library: Library, db_path=None, llm_queue=None):
        super().__init__()
        self.repo = repo
        self.library = library
        self.db_path = db_path
        self.llm_queue = llm_queue

        self.setWindowTitle("LLM Cabinet  ·  AI 项目化文件管理器")
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
        tb.addSeparator()
        tb.addAction(make_action("▶", "打开", self.action_open_current_file, "Ctrl+Return"))
        tb.addAction(make_action("📂", "在资源管理器中显示", self.action_reveal_current_file))

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
        self.tag_tree.filter_changed.connect(self._on_tag_filter_changed)
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
        self.search_box.setPlaceholderText("🔍  搜索（暂未实现）")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setEnabled(False)  # 搜索功能后续再实现

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
        self.proj_view.setSelectionMode(QAbstractItemView.SingleSelection)
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
        self.proj_table.setSelectionMode(QAbstractItemView.SingleSelection)
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
        cl.addWidget(self.view_stack, 1)
        cl.addWidget(self.drop_zone)

        # ============================================================
        # 右：上=预览 / 下=详情卡片 + 文件表
        # ============================================================
        right = self._build_right_panel()

        # ============================================================ 拼装
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([220, 520, 600])
        splitter.setHandleWidth(1)

        root = QWidget()
        root.setObjectName("CentralRoot")
        rl = QHBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(splitter)
        self.setCentralWidget(root)

        sb = QStatusBar()
        self.setStatusBar(sb)
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

        self.lbl_meta_desc = QLabel("")
        self.lbl_meta_desc.setProperty("muted", True)
        self.lbl_meta_desc.setWordWrap(True)
        # 限制为 2~3 行：用最大高度限制 + 文本省略
        fm = self.lbl_meta_desc.fontMetrics()
        self.lbl_meta_desc.setMaximumHeight(fm.lineSpacing() * 3 + 4)
        self.lbl_meta_desc.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.preview = PreviewPanel()

        top = QWidget()
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
        btn_add_file = QPushButton("＋  添加文件")
        btn_add_file.setProperty("primary", True)
        btn_add_file.clicked.connect(self.action_add_files)
        files_header.addWidget(btn_add_file)

        self.tbl_files = QTableWidget(0, len(FILES_COLUMNS))
        self.tbl_files.setHorizontalHeaderLabels([c.label for c in FILES_COLUMNS])
        self._files_dnd = FilesTableDnD(self.tbl_files)
        self._files_dnd.files_dropped.connect(self._on_dropped_on_files_table)
        h = self.tbl_files.horizontalHeader()
        # 所有列都 Interactive：允许用户自由拖宽（包括文件名列右侧）。
        # 不用 Stretch，避免 stretch 列右边缘无法拖动，以及其它列变窄时
        # 反向被吃掉空间的怪异交互。列总宽超出视口时显示水平滚动条。
        for i, _col in enumerate(FILES_COLUMNS):
            h.setSectionResizeMode(i, QHeaderView.Interactive)
        h.setStretchLastSection(False)
        self.tbl_files.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.tbl_files.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # 列宽偏好在 _show_project 中按项目加载
        h.setSectionsMovable(False)   # 暂不开放调换列顺序
        # 表头右键菜单：切换列可见性
        h.setContextMenuPolicy(Qt.CustomContextMenu)
        h.customContextMenuRequested.connect(self._files_header_context_menu)
        # 列宽变化时保存到 project_settings
        h.sectionResized.connect(self._on_files_section_resized)
        self.tbl_files.verticalHeader().setVisible(False)
        self.tbl_files.setAlternatingRowColors(True)
        self.tbl_files.setShowGrid(False)
        # 文件名过长时直接截断显示，不显示省略号
        self.tbl_files.setTextElideMode(Qt.ElideNone)
        self._no_elide_delegate = NoElideDelegate(self.tbl_files)
        self.tbl_files.setItemDelegate(self._no_elide_delegate)
        self.tbl_files.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_files.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self.tbl_files.itemChanged.connect(self._on_file_item_changed)
        self.tbl_files.itemSelectionChanged.connect(self._on_file_selected)
        self.tbl_files.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tbl_files.customContextMenuRequested.connect(self._file_context_menu)
        self.tbl_files.cellDoubleClicked.connect(self._on_file_double_clicked)
        self.tbl_files.verticalHeader().setDefaultSectionSize(30)
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

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(12, 6, 12, 10)
        bl.setSpacing(8)
        bl.addLayout(files_header)
        bl.addWidget(self.tbl_files, 1)
        bl.addLayout(ops)

        # 上下垂直 splitter
        v_split = QSplitter(Qt.Vertical)
        v_split.addWidget(top)
        v_split.addWidget(bottom)
        v_split.setStretchFactor(0, 1)
        v_split.setStretchFactor(1, 1)
        v_split.setSizes([320, 480])
        v_split.setHandleWidth(1)
        return v_split

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
        仅显示 visible=True 的字段；标题永远第一列。
        """
        all_fields = self.repo.list_fields()
        # 仅取可见的；title 即使不可见也强制保留
        visible_fields = [f for f in all_fields if f.visible or f.is_title]
        # 把 title 排到最前
        title_first = sorted(
            visible_fields,
            key=lambda f: (0 if f.is_title else 1, f.ord, f.id or 0),
        )
        self.proj_model.set_columns(title_first, include_extras=True)
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
        menu.exec(self.tbl_files.horizontalHeader().mapToGlobal(pos))

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

    # ============================================================ projects
    def refresh_projects(self) -> None:
        # 0) 同步列定义（字段可能改了）
        self._rebuild_columns()
        # 1) 重建标签树（计数会变）
        self._refresh_tag_tree()

        # 2) 根据当前过滤条件取项目
        kind = self._current_filter_kind
        value = self._current_filter_value
        if kind == "untagged":
            projects = self.repo.list_projects_untagged()
            title_text = "未分类"
        elif kind == "review":
            projects = self.repo.list_projects_pending_review()
            title_text = "⚡ 待审阅 LLM 建议"
        elif kind == "tag" and value:
            projects = self.repo.list_projects(tag=value)
            title_text = f"#{value}"
        else:
            projects = self.repo.list_projects()
            title_text = "全部项目"

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
            self.proj_view.setCurrentIndex(self.proj_model.index(0, 0))
        else:
            self._current_project_id = None
            self._show_project(None)

    def _refresh_tag_tree(self) -> None:
        self.tag_tree.populate(
            tag_counts=self.repo.tag_counts(),
            total=self.repo.count_projects_total(),
            untagged=self.repo.count_projects_untagged(),
            pending_review=self.repo.count_projects_with_pending_suggestions(),
        )

    def _on_tag_filter_changed(self, kind: str, value: str) -> None:
        self._current_filter_kind = kind
        self._current_filter_value = value
        # 不重建标签树（避免选中跳掉），只刷新项目区
        # 但因为 refresh_projects 会调 _refresh_tag_tree，会重建——
        # populate 内部会保留当前选中，所以没问题
        self.refresh_projects()

    def _on_project_selected(self, cur, _prev) -> None:
        if not cur.isValid():
            self._current_project_id = None
            self._show_project(None)
            return
        pid = cur.data(ProjectModel.RoleId)
        self._current_project_id = pid
        self._show_project(self.repo.get_project(pid))

    def _show_project(self, p: Project | None) -> None:
        # 清空文件表 & 预览
        self.tbl_files.blockSignals(True)
        self.tbl_files.setRowCount(0)
        self.tbl_files.blockSignals(False)
        self.preview.show_file(None)

        if p is None:
            self.lbl_meta_title.setText("（未选择项目）")
            self.lbl_meta_desc.setText("")
            self.lbl_files_hint.setText("")
            self.statusBar().showMessage("")
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

        # 文件表
        files = self.repo.list_files(p.id)  # type: ignore[arg-type]
        self.tbl_files.blockSignals(True)
        self.tbl_files.setRowCount(len(files))
        kind_icons = {"image": "🖼", "video": "🎬", "pdf": "📄", "doc": "📝",
                      "code": "💻", "other": "📦"}
        from .files_table_columns import INDEX_BY_KEY as _COL_IDX
        for r, f in enumerate(files):
            name = Path(f.path).name
            it_name = QTableWidgetItem(f"{kind_icons.get(f.kind, '📦')}  {name}")
            it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)
            it_name.setData(Qt.UserRole, f.id)
            it_label = QTableWidgetItem(f.label)
            it_kind = QTableWidgetItem(f.kind)
            it_kind.setFlags(it_kind.flags() & ~Qt.ItemIsEditable)
            it_kind.setTextAlignment(Qt.AlignCenter)
            # 存储方式：is_relative=True → 仓储（统一仓库目录），False → 链接（原始路径）
            storage_text = "📦 仓储" if f.is_relative else "🔗 链接"
            it_storage = QTableWidgetItem(storage_text)
            it_storage.setFlags(it_storage.flags() & ~Qt.ItemIsEditable)
            it_storage.setTextAlignment(Qt.AlignCenter)
            it_storage.setToolTip(
                "文件已复制到统一仓库目录" if f.is_relative
                else "仅记录原始路径，文件留在原位"
            )
            self.tbl_files.setItem(r, _COL_IDX["name"], it_name)
            self.tbl_files.setItem(r, _COL_IDX["label"], it_label)
            self.tbl_files.setItem(r, _COL_IDX["kind"], it_kind)
            self.tbl_files.setItem(r, _COL_IDX["storage"], it_storage)
        self.tbl_files.blockSignals(False)

        # 应用项目级列偏好（可见性 + 列宽）
        self._apply_files_columns_prefs(p.id)

        self.lbl_files_hint.setText(f"共 {len(files)} 个文件 · 双击说明列可编辑")
        self.statusBar().showMessage(
            f"项目 #{p.id}  ·  {p.title}  ·  {len(files)} 文件"
        )

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
        dlg.exec()

    def _apply_theme_now(self, name: str) -> None:
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, name)

    # ============================================================ LLM
    def action_open_llm_tasks(self) -> None:
        from .llm_tasks_panel import LLMTasksDialog
        dlg = LLMTasksDialog(self.repo, parent=self)
        dlg.suggestions_reapplied.connect(self._on_llm_suggestions_added)
        dlg.exec()

    def action_llm_suggest_for_project(self, pid: int | None = None) -> None:
        """从右键菜单触发：跳过项目编辑对话框，直接弹 LLMSuggestDialog。"""
        if pid is None:
            pid = self._current_project_id
        if pid is None or self.llm_queue is None:
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
        # 新建项目尚无 id，无法发起 LLM；按下按钮提示
        dlg.request_llm_suggest.connect(
            lambda: QMessageBox.information(
                self, "提示", "请先保存项目再发起 LLM 建议。"
            )
        )
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
        if self._current_project_id is None:
            return
        p = self.repo.get_project(self._current_project_id)
        if not p:
            return

        files = self.repo.list_files(p.id)  # type: ignore[arg-type]
        copy_files = [f for f in files if f.is_relative]
        link_files = [f for f in files if not f.is_relative]

        lines = [f"确定删除项目「{p.title}」？"]
        if not files:
            lines.append("（该项目目前没有文件）")
        else:
            if link_files:
                lines.append(f"🔗 链接 · {len(link_files)} 个：原文件不受影响。")
            if copy_files:
                lines.append(f"📦 仓储 · {len(copy_files)} 个：将从仓库目录删除。")

        ans = QMessageBox.question(self, "确认删除", "\n".join(lines))
        if ans != QMessageBox.Yes:
            return

        # 删除仓储文件（仅 is_relative=True 的）
        if copy_files:
            for f in copy_files:
                self.library.remove_relative(f.path)
            pdir = self.library.project_dir(p.id)  # type: ignore[arg-type]
            try:
                for child in pdir.iterdir():
                    try:
                        child.unlink()
                    except OSError:
                        pass
                pdir.rmdir()
            except OSError:
                pass
        self.repo.delete_project(p.id)  # type: ignore[arg-type]
        self.refresh_projects()

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
        menu = QMenu(self)
        menu.addAction("✎  编辑…", self.action_edit_project)
        menu.addSeparator()
        menu.addAction("✨  LLM 元数据建议…", self.action_llm_suggest_for_project)
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

        # 询问本次导入要用哪种存储方式（默认 = 全局设置的「默认存储方式」）
        default_mode = self.repo.get_setting("default_storage_mode", "link") or "link"
        storage, ask_label_text = self._ask_storage_for_import(
            paths, default_mode,
        )
        if storage is None:
            return  # 用户取消

        # 说明已经在第一个对话框里填了（单文件场景），不再弹第二次
        added = self._import_files(
            p, paths, ask_label=False, storage=storage,
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
        self, paths: list[str], default_mode: str,
    ) -> tuple[str | None, str | None]:
        """弹出存储方式选择对话框。

        返回 (storage, label)：
        - storage = "link" | "copy" 或 None（取消）
        - label：仅单文件时可能携带的说明文本；None 表示对话框没收集说明
        """
        from PySide6.QtWidgets import (
            QDialog, QDialogButtonBox, QLabel, QLineEdit,
            QRadioButton, QVBoxLayout,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("添加文件")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 16, 20, 12)
        lay.setSpacing(10)

        head = QLabel(
            f"将导入 {len(paths)} 个文件，请选择存储方式："
            if len(paths) > 1
            else f"将导入「{Path(paths[0]).name}」，请选择存储方式："
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
        paths: list[str],
        ask_label: bool = False,
        storage: str | None = None,
    ) -> int:
        """批量导入文件，返回成功数量。

        storage: "link" | "copy" | None。None 表示按全局「默认存储方式」设置。
        """
        added = 0
        for src in paths:
            try:
                self._import_one(project, src, ask_label=ask_label, storage=storage)
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
            )
        else:
            f = FileItem(
                project_id=p.id,  # type: ignore[arg-type]
                path=str(src_path.resolve()), is_relative=False, label="",
                kind=detect_kind(src_path), ord=10_000,
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
        r = self.tbl_files.currentRow()
        if r < 0:
            return None
        from .files_table_columns import INDEX_BY_KEY as _COL_IDX
        it = self.tbl_files.item(r, _COL_IDX["name"])
        return it.data(Qt.UserRole) if it else None

    def _selected_file_ids(self) -> list[int]:
        from .files_table_columns import INDEX_BY_KEY as _COL_IDX
        rows = sorted({i.row() for i in self.tbl_files.selectedIndexes()})
        ids: list[int] = []
        for r in rows:
            it = self.tbl_files.item(r, _COL_IDX["name"])
            if it:
                ids.append(it.data(Qt.UserRole))
        return ids

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

    def _on_file_item_changed(self, item: QTableWidgetItem) -> None:
        from .files_table_columns import INDEX_BY_KEY as _COL_IDX
        if item.column() != _COL_IDX["label"]:
            return
        name_item = self.tbl_files.item(item.row(), _COL_IDX["name"])
        if not name_item:
            return
        fid = name_item.data(Qt.UserRole)
        f = self.repo.get_file(fid)
        if not f:
            return
        f.label = item.text()
        self.repo.update_file(f)

    def _on_file_double_clicked(self, _row: int, col: int) -> None:
        if col == 0:
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
        ids = self._selected_file_ids()
        if not ids:
            return

        # 按存储方式分类：链接（不动原文件） vs 仓储（会删除仓库内物理文件）
        link_files: list[FileItem] = []
        copy_files: list[FileItem] = []
        for fid in ids:
            f = self.repo.get_file(fid)
            if not f:
                continue
            if f.is_relative:
                copy_files.append(f)
            else:
                link_files.append(f)

        if not link_files and not copy_files:
            return

        def _fmt_list(files: list[FileItem], max_show: int = 8) -> str:
            from pathlib import Path
            names = [Path(f.path).name for f in files]
            if len(names) <= max_show:
                shown = names
            else:
                shown = names[:max_show] + [f"…（其余 {len(names) - max_show} 个）"]
            return "\n".join(f"  • {n}" for n in shown)

        parts: list[str] = []
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
            f"即将从项目中移除 {len(link_files) + len(copy_files)} 个文件："
        )
        msg.setInformativeText("\n\n".join(parts))
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        if msg.exec() != QMessageBox.Yes:
            return

        for f in link_files + copy_files:
            if f.is_relative:
                self.library.remove_relative(f.path)
            if f.id is not None:
                self.repo.delete_file(f.id)
        if self._current_project_id is not None:
            self.repo.touch_project(self._current_project_id)
            self._show_project(self.repo.get_project(self._current_project_id))

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
        )
        new_fid = self.repo.add_file(fi)
        return new_fid

    def _file_context_menu(self, pos) -> None:
        if self._current_file_row_id() is None:
            return
        menu = QMenu(self)
        menu.addAction("▶  打开", self.action_open_current_file)
        menu.addAction("📂  在资源管理器中显示", self.action_reveal_current_file)
        menu.addSeparator()
        menu.addAction("🖼  设为封面", self.action_set_cover)
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

    # ---- 信号槽：来自子组件的 drop ----
    def _on_dropped_on_project(self, pid: int, paths: list) -> None:
        if self._drop_busy:
            return
        self._drop_busy = True
        try:
            files = self._expand_paths(paths)
            if not files:
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
            files = self._expand_paths(paths)
            if not files:
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
            files = self._expand_paths(paths)
            self._hide_drop_zone()
            if files:
                self._drop_create_project(files, source_paths=paths)
        finally:
            self._drop_busy = False

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

    @staticmethod
    def _expand_paths(paths: list) -> list[str]:
        out: list[str] = []
        for raw in paths:
            p = Path(raw)
            if p.is_file():
                out.append(str(p))
            elif p.is_dir():
                for sub in p.iterdir():
                    if sub.is_file():
                        out.append(str(sub))
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
            default_title = Path(files[0]).stem
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
