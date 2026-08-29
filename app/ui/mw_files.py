"""右栏文件面板：文件表、子文件夹、文件操作、封面（task #35：从 main_window.py 拆分，方法体未改动）。

Mixin：右栏文件面板：文件表、子文件夹、文件操作、封面
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
from .widgets import NoElideDelegate
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


class FilesPanelMixin:
    """右栏文件面板：文件表、子文件夹、文件操作、封面"""

    EXPLICIT_SUBFOLDERS_SETTING_KEY = "explicit_subfolders"

    FILES_TREE_SORT_SETTING_KEY = "files_table_sort_tree"

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

        # task #39 T1：多选批量操作面板（默认隐藏，多选项目时显示）
        self._batch_panel = QWidget()
        bp = QVBoxLayout(self._batch_panel)
        bp.setContentsMargins(0, 4, 0, 4)
        bp.setSpacing(6)
        for text, slot in [
            ("🏷  批量加标签…", self._batch_add_tag_dialog),
            ("✨  LLM 元数据建议", self._batch_llm_suggest),
            ("📤  导出选中项目…", self.action_export_project),
            ("👁  标记 MCP 修改已读", self._batch_mark_mcp_seen),
            ("🗑  删除选中项目…", self.action_delete_project),
        ]:
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            bp.addWidget(b)
        self._batch_panel.setVisible(False)

        top = QWidget()
        top.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        top_l = QVBoxLayout(top)
        top_l.setContentsMargins(12, 10, 12, 6)
        top_l.setSpacing(4)
        top_l.addWidget(self.lbl_meta_title)
        top_l.addWidget(self.lbl_meta_desc)
        top_l.addWidget(self._batch_panel)
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


    def _file_size_str(self, f: FileItem) -> str:
        """文件大小显示串（task #33：会话级缓存，按 (mtime, size) 自动失效）。"""
        try:
            abs_path = self.library.resolve(f.path, f.is_relative)
            st = abs_path.stat()
        except Exception:
            return "—"
        key = str(abs_path)
        ent = self._file_size_cache.get(key)
        if ent is not None and ent[0] == st.st_mtime_ns and ent[1] == st.st_size:
            return ent[2]
        s = _human_size(st.st_size)
        self._file_size_cache[key] = (st.st_mtime_ns, st.st_size, s)
        return s


    @staticmethod
    def _fmt_added_at(added_at: str | None) -> str:
        """添加时间显示串：'2026-06-10 18:34:08' → '2026-06-10 18:34'。"""
        if not added_at:
            return "—"
        try:
            return added_at[:16] if len(added_at) >= 16 else added_at
        except Exception:
            return "—"


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
        self._disconnect_files_header_clicked()
        self.tbl_files.header().sectionClicked.connect(self._on_files_tree_header_clicked)


    def _disconnect_files_header_clicked(self) -> None:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r".*Failed to disconnect.*sectionClicked.*",
                category=RuntimeWarning,
            )
            try:
                self.tbl_files.header().sectionClicked.disconnect()
            except RuntimeError:
                pass


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
                node.setToolTip(0, partial)  # task #41 T5：目录完整路径
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
            item.setText(3, self._file_size_str(f))
            item.setText(4, self._fmt_added_at(f.added_at))
            item.setText(5, "📦 仓储" if f.is_relative else "🔗 链接")
            item.setData(0, Qt.UserRole, f.id)
            item.setData(0, Qt.UserRole + 2, f.subfolder or "")
            # 文件节点默认不可编辑（label 编辑通过 _on_file_item_changed 控制）
            item.setFlags(
                (item.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsEditable)
                & ~Qt.ItemIsDropEnabled
            )

            # task #41 T5：文件名列 hover 显示完整文件名（缺失时追加说明）
            tip0 = name
            if f.missing:
                tip0 += (
                    "\n此文件被标记为缺失（库一致性检查发现物理路径不存在）。\n"
                    "再次跑「工具 → 检查库一致性」可重新评估。"
                )
            item.setToolTip(0, tip0)
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


    def _populate_files_flat(self, files: list[FileItem]) -> None:
        """扁平视图：所有文件平铺（忽略 subfolder 分组）。

        task #31b: 支持按列排序
        """
        kind_icons = {"image": "🖼", "video": "🎬", "pdf": "📄", "doc": "📝",
                      "code": "💻", "other": "📦"}

        for f in files:
            name = Path(f.path).name
            kind_icon = kind_icons.get(f.kind, "📦")
            warn_prefix = "⚠ " if f.missing else ""

            item = QTreeWidgetItem()
            item.setText(0, f"{warn_prefix}{kind_icon}  {name}")
            item.setText(1, f.label)
            item.setText(2, f.kind)
            item.setText(3, self._file_size_str(f))
            item.setText(4, self._fmt_added_at(f.added_at))
            item.setText(5, "📦 仓储" if f.is_relative else "🔗 链接")
            item.setData(0, Qt.UserRole, f.id)
            item.setFlags(item.flags() & ~Qt.ItemIsDropEnabled)

            # task #41 T5：文件名列 hover 显示完整文件名（缺失时追加说明）
            tip0 = name
            if f.missing:
                tip0 += (
                    "\n此文件被标记为缺失（库一致性检查发现物理路径不存在）。\n"
                    "再次跑「工具 → 检查库一致性」可重新评估。"
                )
            item.setToolTip(0, tip0)
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
        self._disconnect_files_header_clicked()
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


    def action_add_files(self) -> None:
        if self._current_project_id is None:
            info(self, "提示", "请先选择或新建一个项目")
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
        # task #37：批量导入不在循环里逐文件弹窗，失败收集后一次汇总
        errors: list[str] = []
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
                errors.append(f"{src}：{e}")
        if added and project.id is not None:
            self.repo.touch_project(project.id)
        if errors:
            warn(
                self, "部分文件导入失败",
                f"成功 {added} 个，失败 {len(errors)} 个。",
                detailed="\n".join(errors),
            )
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
            warn(self, "提示", f"文件不存在：{path}")
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
                f"📦 仓储（物理文件将移入系统回收站） · {len(copy_files)} 个：\n"
                + _fmt_list(copy_files)
            )

        if not confirm(
            self, "确认移除文件",
            f"即将从项目中移除 {len(files_to_delete)} 个文件：",
            informative="\n\n".join(parts),
            yes="移除", danger=True,
        ):
            return

        # task #37：仓储文件进回收站；失败收集后统一汇报
        delete_failures: list[str] = []
        for f in files_to_delete:
            if f.is_relative:
                try:
                    move_to_trash(self.library.resolve(f.path, True))
                except OSError as e:
                    delete_failures.append(f"{Path(f.path).name}：{e}")
            if f.id is not None:
                self.repo.delete_file(f.id)
        if self._current_project_id is not None:
            self.repo.touch_project(self._current_project_id)
            self._show_project(self.repo.get_project(self._current_project_id))
        if delete_failures:
            warn(
                self, "部分文件删除失败",
                f"{len(delete_failures)} 个仓储文件的磁盘副本未能删除"
                "（项目中的记录已移除），可到仓库目录手动清理。",
                detailed="\n".join(delete_failures),
            )


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
            info(self, "提示", f"{scope_label}中没有可处理的文件。")
            return

        # 分类：🔗 链接文件才需要转换，📦 仓储已在内
        link_files = [f for f in files if not f.is_relative]
        storage_files = [f for f in files if f.is_relative]

        if not link_files:
            info(
                self, "提示",
                f"{scope_label}已都是仓储模式，无需转换。"
            )
            return

        # 确认对话框
        if not confirm(
            self, "确认转换",
            f"{scope_label}：将把 {len(link_files)} 个🔗链接文件复制进库：\n\n"
            + "\n".join(f"  • {f.label or f.path}" for f in link_files[:5])
            + (f"\n  … 等共 {len(link_files)} 个" if len(link_files) > 5 else "")
            + f"\n\n已仓储文件 {len(storage_files)} 个将跳过。\n\n"
            "⚠️ 原外部文件不会被删除（复制语义）。",
            yes="转换",
        ):
            return

        # task #36：复制放后台线程；DB 更新回主线程按结果清单应用
        def _do(progress_cb, is_cancelled):
            results: list = []  # (file_id, rel_path)
            errors: list[str] = []
            total = len(link_files)
            for i, f in enumerate(link_files):
                if is_cancelled():
                    break
                progress_cb(i, total, f"正在处理：{f.label or f.path}")
                try:
                    abs_src = self.library.resolve(f.path, is_relative=False)
                    if not abs_src.exists():
                        errors.append(f"原文件不存在：{abs_src}")
                        continue
                    rel_path = self.library.import_copy(f.project_id, abs_src)
                    results.append((f.id, rel_path))
                except Exception as e:
                    errors.append(f"{f.path}：{e}")
            progress_cb(total, total, "")
            return {"results": results, "errors": errors,
                    "cancelled": is_cancelled()}

        def _on_done(payload):
            copied = 0
            for fid, rel_path in payload["results"]:
                f = self.repo.get_file(fid)
                if f is None:
                    continue
                f.path = rel_path
                f.is_relative = True
                self.repo.update_file(f)
                copied += 1

            errors = payload["errors"]
            unhandled = len(link_files) - copied - len(errors)
            lines = [f"✅ 已复制到库内：{copied} 个"]
            if payload["cancelled"] and unhandled > 0:
                lines.append(f"⏸ 已取消：{unhandled} 个未处理")
            if errors:
                lines.append(f"⏭ 失败/跳过：{len(errors)} 个")
                lines.append("\n错误详情：")
                lines.extend(f"  • {e}" for e in errors[:10])

            info(self, "转换完成", "\n".join(lines))
            # 刷新文件表
            self._refresh_files_table()

        run_with_progress(
            self, "链接转仓储", f"{scope_label}：正在转换...", _do,
            on_done=_on_done,
            on_error=lambda msg: warn(self, "转换失败", msg),
        )


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
            info(self, "提示", f"{scope_label}中没有可移动的文件。")
            return

        # 选择目标目录
        target_dir = QFileDialog.getExistingDirectory(
            self, "选择目标目录",
        )
        if not target_dir:
            return
        target_dir = Path(target_dir)

        # task #36：物理移动放后台线程；DB 更新回主线程按结果清单应用
        def _do(progress_cb, is_cancelled):
            results: list = []  # (file_id, new_abs_path)
            errors: list[str] = []
            total = len(files)
            for i, f in enumerate(files):
                if is_cancelled():
                    break
                progress_cb(i, total, f"正在处理：{f.label or f.path}")
                try:
                    abs_src = self.library.resolve(f.path, f.is_relative)
                    if not abs_src.exists():
                        errors.append(f"{f.path}：文件不存在")
                        continue

                    # 目标路径（同名冲突自动加序号）
                    dst = target_dir / abs_src.name
                    j = 1
                    while dst.exists():
                        dst = target_dir / f"{abs_src.stem}_{j}{abs_src.suffix}"
                        j += 1

                    shutil.move(str(abs_src), str(dst))
                    results.append((f.id, str(dst.resolve())))
                except Exception as e:
                    errors.append(f"{f.path}：{e}")
            progress_cb(total, total, "")
            return {"results": results, "errors": errors,
                    "cancelled": is_cancelled()}

        def _on_done(payload):
            moved = 0
            for fid, new_abs in payload["results"]:
                f = self.repo.get_file(fid)
                if f is None:
                    continue
                f.path = new_abs
                f.is_relative = False  # 移动后都变成外部链接
                self.repo.update_file(f)
                moved += 1

            errors = payload["errors"]
            unhandled = len(files) - moved - len(errors)
            lines = [f"✅ 已移动：{moved} 个"]
            if payload["cancelled"] and unhandled > 0:
                lines.append(f"⏸ 已取消：{unhandled} 个未处理")
            if errors:
                lines.append(f"⏭ 跳过：{len(errors)} 个")
                lines.append("\n错误详情：")
                lines.extend(f"  • {e}" for e in errors[:10])

            info(self, "移动完成", "\n".join(lines))
            # 刷新文件表
            self._refresh_files_table()

        run_with_progress(
            self, "移动文件", f"{scope_label}：正在移动...", _do,
            on_done=_on_done,
            on_error=lambda msg: warn(self, "移动失败", msg),
        )


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
            info(self, "提示", f"{scope_label}中没有可重关联的文件。")
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
            warn(self, "无法读取目录", str(e))
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
            warn(
                self, "未找到匹配文件",
                f"{scope_label}中没有一个文件能在指定目录下找到同名文件。\n\n"
                "请确认文件是否在选择的目录中。"
            )
            return

        # 确认对话框
        if not confirm(
            self, "确认重关联",
            f"{scope_label}：将为 {len(matched)} 个文件重新关联到外部文件：\n\n"
            + "\n".join(f"  • {old.path} → {new.name}" for old, new in matched[:5])
            + (f"\n  … 等共 {len(matched)} 个" if len(matched) > 5 else "")
            + (f"\n\n未找到：{len(unmatched)} 个" if unmatched else ""),
            yes="重关联",
        ):
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

        info(
            self, "重关联完成",
            "\n".join(lines)
        )

        # 刷新
        self._refresh_files_table()


    def action_replace_link_target(self) -> None:
        """task #29 T3b：替换链接目标（仅单选）。"""
        file_ids = self._selected_file_ids()
        if len(file_ids) != 1:
            info(
                self, "提示",
                "请先选择一个🔗链接文件来替换目标。"
            )
            return

        f = self.repo.get_file(file_ids[0])
        if not f:
            return

        if f.is_relative:
            info(
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
        if not confirm(
            self, "确认替换",
            f"将把链接目标从：\n  {f.path}\n\n替换为：\n  {new_file}\n\n是否继续？",
            yes="替换",
        ):
            return

        # 执行替换
        try:
            f.path = str(new_file.resolve())
            self.repo.update_file(f)

            info(
                self, "完成",
                "链接目标已替换。"
            )
            self._refresh_files_table()
        except Exception as e:
            error(
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
            info(
                self, "提示",
                "当前文件无可截取的预览画面。\n（视频请先开始播放，PDF 请先加载完成）",
            )
            return
        try:
            cover_fid = self._save_cover_snapshot(p, pix, source_kind=f.kind)
        except Exception as e:
            warn(self, "失败", f"保存封面失败：{e}")
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
            info(self, "提示", "剪切板里没有可用的图片。")
            return

        try:
            cover_fid = self._save_cover_snapshot(p, pix, source_kind="clipboard")
        except Exception as e:
            warn(self, "失败", f"保存封面失败：{e}")
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
            warn(self, "无效名称", "文件夹名称不能包含路径分隔符。")
            return

        new_subfolder = f"{parent_subfolder}/{name}" if parent_subfolder else name

        existing_files = self.repo.list_files(pid)
        existing_sf = {f.subfolder for f in existing_files}
        explicit = self._load_explicit_subfolders(pid)
        if new_subfolder in existing_sf or new_subfolder in explicit:
            warn(self, "已存在", f"文件夹「{new_subfolder}」已存在。")
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
            warn(self, "无效名称", "文件名不能为空。")
            return
        if any(ch in new_name for ch in '<>:"/\\|?*'):
            warn(self, "无效名称", "文件名包含 Windows 不允许的字符。")
            return
        if new_name == old_name:
            return

        new_abs = old_abs.with_name(new_name)
        if new_abs.exists():
            warn(self, "已存在", f"目标文件已存在：\n{new_abs}")
            return
        if not old_abs.exists():
            warn(self, "文件不存在", f"原文件不存在：\n{old_abs}")
            return

        try:
            old_abs.rename(new_abs)
            if f.is_relative:
                f.path = (Path(f.path).parent / new_name).as_posix()
            else:
                f.path = str(new_abs.resolve())
            self.repo.update_file(f)
        except OSError as e:
            warn(self, "重命名失败", str(e))
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
            warn(self, "无效名称", "文件夹名不能为空。")
            return
        if "/" in new_name or "\\" in new_name:
            warn(self, "无效名称", "文件夹名称不能包含路径分隔符。")
            return
        new = f"{parent}/{new_name}" if parent else new_name
        if new == old:
            return

        files = self.repo.list_files(pid)
        existing = {f.subfolder for f in files if f.subfolder and f.subfolder != old}
        explicit = self._load_explicit_subfolders(pid)
        if new in existing or (new in explicit and new != old):
            warn(self, "已存在", f"文件夹「{new}」已存在。")
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
            info(self, "无法删除", "只能删除空文件夹。")
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


    def _files_f2_rename(self) -> None:
        """F2：目录节点 → 重命名文件夹；文件节点 → 编辑说明列（task #41 T4）。"""
        item = self.tbl_files.currentItem()
        if item is None:
            return
        fid = item.data(0, Qt.UserRole)
        if fid is not None and fid < 0:
            self.action_rename_subfolder()
        else:
            self.tbl_files.editItem(item, 1)
