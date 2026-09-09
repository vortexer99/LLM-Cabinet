"""主窗口：左侧项目卡片墙 / 中间文件列表 / 右侧详情+预览。"""
from __future__ import annotations

import json
import logging
import shutil
import warnings
from pathlib import Path

logger = logging.getLogger("llm_cabinet.ui")

from PySide6.QtCore import QEvent, QItemSelectionModel, QSize, Qt, QTimer
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
from ..utils import (
    OperationCancelled,
    detect_kind,
    human_size as _human_size,
    move_to_trash,
    open_with_default_app,
    reveal_in_explorer,
)
from .dnd import FilesTableDnD, ProjectViewDnD
from .files_table_columns import (
    COLUMNS as FILES_COLUMNS,
    SETTING_KEY as FILES_COLUMNS_SETTING_KEY,
    column_by_key as files_column_by_key,
    dump_prefs as files_dump_prefs,
    load_prefs as files_load_prefs,
    resolve_pref as files_resolve_pref,
)
from .cover_cache import get_cover
from .dialogs import ask_yes_no_cancel, confirm, error, info, warn
from .preview import PreviewPanel
from .search_completion import SearchBoxKeyFilter, SearchCompletionPopup, current_token
from .workers import ExportSnapshotRepo, run_with_progress
from .project_card import (
    CARD_W as _CARD_W,
    COVER_H as _COVER_H,
    PAD as _CARD_PAD,
    ProjectCardDelegate,
    ProjectModel,
)
from .project_dialog import ProjectDialog
from .export_dialog import ExportDialog
from .settings import SettingsDialog
from .palette import current as _current_palette
from .tag_tree import TagTree
from .widgets import ClickableLabel, DropZone  # noqa: F401  # DropZone 在 _build_ui 使用



from .mw_dnd import DnDMixin
from .mw_files import FilesPanelMixin
from .mw_library import LibraryMenuMixin
from .mw_projects import ProjectsMixin
from .mw_search import SearchMixin


class MainWindow(
    LibraryMenuMixin,
    ProjectsMixin,
    FilesPanelMixin,
    DnDMixin,
    SearchMixin,
    QMainWindow,
):
    MAIN_SPLITTER_SETTING_KEY = "main_splitter_sizes"

    MAIN_SPLITTER_DEFAULT_SIZES = [200, 800, 400]

    MAIN_SPLITTER_MIN_SIZE = 80

    WINDOW_GEOMETRY_SETTING_KEY = "main_window_geometry"

    WINDOW_MAXIMIZED_SETTING_KEY = "main_window_maximized"

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
        # task #33：文件大小缓存 {abs_path: (单调时钟, size, 显示串)}，1 秒过期
        self._file_size_cache: dict[str, tuple[float, int, str]] = {}
        # task #33：字段定义指纹，没变就跳过列重建
        self._fields_fingerprint: tuple | None = None

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

        # task #41 T1：恢复上次的窗口位置/尺寸/最大化状态
        self._restore_window_geometry()


    def _restore_window_geometry(self) -> None:
        try:
            raw = self.repo.get_setting(self.WINDOW_GEOMETRY_SETTING_KEY, "")
            if raw:
                from PySide6.QtCore import QByteArray
                if not self.restoreGeometry(QByteArray.fromHex(raw.encode("ascii"))):
                    logger.warning("窗口几何恢复失败（已忽略）：数据可能损坏")
            if self.repo.get_setting(self.WINDOW_MAXIMIZED_SETTING_KEY, "0") == "1":
                self.showMaximized()
        except Exception:
            logger.warning("恢复窗口布局失败", exc_info=True)


    def closeEvent(self, ev) -> None:  # noqa: N802
        try:
            self.repo.set_setting(
                self.WINDOW_GEOMETRY_SETTING_KEY,
                bytes(self.saveGeometry().toHex()).decode("ascii"),
            )
            self.repo.set_setting(
                self.WINDOW_MAXIMIZED_SETTING_KEY,
                "1" if self.isMaximized() else "0",
            )
        except Exception:
            logger.warning("保存窗口布局失败", exc_info=True)
        super().closeEvent(ev)


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
        self.tag_tree.tag_action_requested.connect(self._on_tag_action)
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
        # task #38 T2：即时补全下拉（字段语法 / 标签值 / 历史收藏）
        self._completion = SearchCompletionPopup(self)
        self._completion.item_chosen.connect(self._apply_completion)
        self.search_box.textChanged.connect(self._update_search_completion)
        # task #41 T4：搜索框按键走控件级过滤器（补全优先，Esc 清空）
        self._search_key_filter = SearchBoxKeyFilter(self._completion, self.search_box, self)
        self.search_box.installEventFilter(self._search_key_filter)

        self.btn_search_menu = QToolButton()
        self.btn_search_menu.setText("⌄")
        self.btn_search_menu.setToolTip("搜索历史与收藏")
        self.btn_search_menu.setFixedSize(28, 28)
        self.btn_search_menu.clicked.connect(self._show_search_menu)

        self.btn_save_search = QToolButton()
        self.btn_save_search.setText("☆")
        self.btn_save_search.setToolTip("收藏当前搜索表达式")
        self.btn_save_search.setFixedSize(28, 28)
        self.btn_save_search.clicked.connect(self._save_current_search)

        self.btn_search_all = QToolButton()
        self.btn_search_all.setText("全库")
        self.btn_search_all.setToolTip("搜索全部项目（忽略左侧筛选）")
        self.btn_search_all.setCheckable(True)
        self.btn_search_all.setFixedSize(46, 28)
        self.btn_search_all.clicked.connect(lambda _checked=False: self.refresh_projects())

        self.btn_view_grid = QToolButton()
        self.btn_view_grid.setText("▦")
        self.btn_view_grid.setToolTip("网格视图")
        self.btn_view_grid.setCheckable(True)
        self.btn_view_grid.setChecked(True)
        self.btn_view_grid.setFixedSize(28, 28)
        self.btn_view_list = QToolButton()
        self.btn_view_list.setText("≡")
        self.btn_view_list.setToolTip("列表视图")
        self.btn_view_list.setCheckable(True)
        self.btn_view_list.setFixedSize(28, 28)
        self.btn_view_grid.clicked.connect(lambda: self._set_view_mode("grid"))
        self.btn_view_list.clicked.connect(lambda: self._set_view_mode("list"))

        search_tools = QWidget()
        search_tools.setObjectName("SearchTools")
        search_tools_l = QHBoxLayout(search_tools)
        search_tools_l.setContentsMargins(0, 0, 0, 0)
        search_tools_l.setSpacing(4)
        search_tools_l.addWidget(self.btn_search_menu)
        search_tools_l.addWidget(self.btn_save_search)
        search_tools_l.addWidget(self.btn_search_all)
        search_tools.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        view_tools = QWidget()
        view_tools.setObjectName("ProjectViewTools")
        view_tools_l = QHBoxLayout(view_tools)
        view_tools_l.setContentsMargins(0, 0, 0, 0)
        view_tools_l.setSpacing(4)
        view_tools_l.addWidget(self.btn_view_grid)
        view_tools_l.addWidget(self.btn_view_list)
        view_tools.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)
        top_row.addWidget(self.search_box, 1)
        top_row.addWidget(search_tools)
        top_row.addSpacing(12)
        top_row.addWidget(view_tools)

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
        # 快捷键（task #38 T3 Ctrl+F；task #41 T3 Delete / F2 系列）
        from PySide6.QtGui import QShortcut
        self._sc_focus_search = QShortcut(QKeySequence("Ctrl+F"), self)
        self._sc_focus_search.setContext(Qt.WidgetWithChildrenShortcut)
        self._sc_focus_search.activated.connect(self._focus_search)
        # Delete：WidgetShortcut 语境——内联编辑器获得焦点时不触发
        for w, slot in (
            (self.proj_view, self.action_delete_project),
            (self.proj_table, self.action_delete_project),
            (self.tbl_files, self.action_delete_files),
        ):
            sc = QShortcut(QKeySequence(Qt.Key_Delete), w)
            sc.setContext(Qt.WidgetShortcut)
            sc.activated.connect(slot)
        # F2 重命名（task #41 T4：从全局 eventFilter 改为控件级快捷键）
        sc_f2 = QShortcut(QKeySequence(Qt.Key_F2), self.tbl_files)
        sc_f2.setContext(Qt.WidgetShortcut)
        sc_f2.activated.connect(self._files_f2_rename)
        sc_sf2 = QShortcut(QKeySequence("Shift+F2"), self.tbl_files)
        sc_sf2.setContext(Qt.WidgetShortcut)
        sc_sf2.activated.connect(self.action_rename_physical_file)
        # 状态栏右侧：MCP 操作计数（点击打开记录面板）
        _pal = _current_palette()
        _status_style = (
            "QLabel{padding:2px 10px;border-radius:4px;}"
            f"QLabel:hover{{background:rgba(34,139,230,0.12);color:{_pal.accent_hover};}}"
        )
        self.lbl_mcp_count = ClickableLabel("📋 MCP 操作: 0")
        self.lbl_mcp_count.setStyleSheet(_status_style)
        self.lbl_mcp_count.setToolTip("点击查看 MCP 操作记录")
        self.lbl_mcp_count.clicked.connect(self.action_open_mcp_audit)
        sb.addPermanentWidget(self.lbl_mcp_count)

        # 状态栏右侧：LLM 任务计数（点击打开任务面板）
        self.lbl_llm_count = ClickableLabel("⚡ LLM 任务: 0")
        self.lbl_llm_count.setStyleSheet(_status_style)
        self.lbl_llm_count.setToolTip("点击打开 LLM 任务面板")
        self.lbl_llm_count.clicked.connect(self.action_open_llm_tasks)
        sb.addPermanentWidget(self.lbl_llm_count)


    def _resolve(self, f: FileItem) -> Path:
        return self.library.resolve(f.path, f.is_relative)


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
