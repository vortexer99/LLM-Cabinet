"""右栏文件面板：文件表、子文件夹、文件操作、封面（task #35：从 main_window.py 拆分，方法体未改动）。

Mixin：右栏文件面板：文件表、子文件夹、文件操作、封面
"""
from __future__ import annotations

import json
import logging
import time
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
from ..storage_cleanup import (
    STORAGE_RECYCLE_SOURCE_KEY, SourceSnapshot,
    capture_storage_source,
)
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
from .dnd import FILE_PATH_ROLE, FilesTableDnD, ProjectFileTree
from .export_dialog import ExportDialog
from .file_sorting import FileTreeItem, RAW_NAME_ROLE, natural_name_key
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
from .project_tags import ProjectTagsWidget
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
from .storage_cleanup import recycle_sources_with_progress
from .workers import ExportSnapshotRepo, run_with_progress

logger = logging.getLogger("llm_cabinet.ui")


def _file_operation_error(
    file: FileItem, source: str | Path, target: str | Path,
    stage: str, exc: Exception, *, outcome: str = "",
) -> str:
    """统一保留失败文件、完整路径、阶段和原始异常，供结果详情与日志使用。"""
    detail = (
        f"文件 ID：{file.id}；名称：{file.label or Path(file.path).name}\n"
        f"源路径：{source}\n目标路径：{target}\n"
        f"失败阶段：{stage}\n异常：{type(exc).__name__}: {exc}"
    )
    if outcome:
        detail += f"\n处理结果：{outcome}"
    logger.error("文件操作失败：%s", detail, exc_info=(type(exc), exc, exc.__traceback__))
    return detail


def _rollback_file_update(repo: Repository) -> str:
    """写回失败后结束未提交事务，避免后续成功项意外提交失败记录。"""
    try:
        repo.conn.rollback()
        return ""
    except Exception as exc:
        logger.exception("文件记录写回失败后的数据库回滚失败")
        return f"\n数据库回滚失败：{type(exc).__name__}: {exc}；记录状态需检查。"


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

        self.project_tags = ProjectTagsWidget()
        self.project_tags.tag_clicked.connect(lambda tag: self._on_tag_filter_changed("tag", tag))

        self.preview = PreviewPanel(
            wheel_navigation_enabled=lambda: self.repo.get_setting("preview_wheel_navigation", "0") == "1",
        )
        self.preview.previous_requested.connect(lambda: self._navigate_preview_file(-1))
        self.preview.next_requested.connect(lambda: self._navigate_preview_file(1))
        self.preview.video_finished.connect(self._on_preview_video_finished)
        self.preview.status_changed.connect(lambda text: self.statusBar().showMessage(text, 5000))
        self.preview.relink_requested.connect(self._relink_preview_file)

        # task #39 T1：多选批量操作面板（默认隐藏，多选项目时显示）
        self._batch_panel = QWidget()
        bp = QVBoxLayout(self._batch_panel)
        bp.setContentsMargins(0, 4, 0, 4)
        bp.setSpacing(6)
        for text, slot in [
            ("🏷  批量加标签…", self._batch_add_tag_dialog),
            ("批量设置封面…", lambda: self.action_batch_auto_cover()),
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
        top_l.addWidget(self.project_tags)
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
        self._btn_origin_filter.setToolTip("仅显示用户文件（默认隐藏封面快照等生成文件）；点击显示全部文件")
        self._btn_origin_filter.setCheckable(True)
        self._btn_origin_filter.setChecked(True)
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

        self.tbl_files = ProjectFileTree()
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
        self.tbl_files.currentItemChanged.connect(lambda *_: self._update_preview_file_navigation())
        self.tbl_files.model().layoutChanged.connect(self._update_preview_file_navigation)
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
        self._right_v_split.setSizes(self.RIGHT_SPLITTER_DEFAULT_SIZES)
        self._right_v_split.setHandleWidth(1)

        self._files_detach_window: QDialog | None = None
        self._files_detach_placeholder: QLabel | None = None
        self._right_v_split.splitterMoved.connect(self._on_right_splitter_moved)

        return self._right_v_split


    def _toggle_files_detach(self) -> None:
        if self._files_detach_window is not None:
            self._attach_files_panel()
        else:
            self._detach_files_panel()


    def _detach_files_panel(self) -> None:
        if self._files_detach_window is not None:
            return
        self._on_right_splitter_moved(0, 0)
        self._files_panel_splitter_sizes = self._right_v_split.sizes()
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
        self._right_v_split.setSizes(self._files_panel_splitter_sizes)

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
            self._show_project(self.repo.get_project(pid), preserve_preview=True)


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
        """大小缓存有效期 1 秒；连续读取不 stat，外部变动最多延迟 1 秒反映。"""
        try:
            abs_path = self.library.resolve(f.path, f.is_relative)
            key = str(abs_path)
            now = time.monotonic()
            ent = self._file_size_cache.get(key)
            if ent is not None and now - ent[0] < 1.0:
                return ent[2]
            st = abs_path.stat()
        except Exception:
            if "key" in locals():
                self._file_size_cache.pop(key, None)
            return "—"
        s = _human_size(st.st_size)
        self._file_size_cache[key] = (now, st.st_size, s)
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
            return "name", False
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
        # 无排序偏好时直接按自然名称排列，避免导入顺序中的 (10) 排到 (2) 前。
        # 用户明确保存的 custom 仍在上方返回，保留拖拽顺序。
        return "name", False


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
        # setSortingEnabled(False) 也会禁用表头点击；树形排序由自己的槽处理。
        self.tbl_files.header().setSectionsClickable(True)
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
        self._update_preview_file_navigation()


    def _populate_files_tree(self, files: list[FileItem]) -> None:
        """按 files.subfolder 分组建树（task #17）。

        - subfolder="" → 顶层文件节点
        - subfolder="ML" → 📁 ML/ 下挂文件
        - subfolder="ML/NLP" → 📁 ML/ → 📁 NLP/ 下挂文件
        - 两种模式（仓储/链接）展示方式完全一致

        task #31b: 新增 size / added_at 列显示
        """
        kind_icons = {"image": "🖼", "video": "🎬", "pdf": "📄", "doc": "📝",
                      "code": "💻", "archive": "🗜", "other": "📦"}

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
                node.setData(0, RAW_NAME_ROLE, parts[depth - 1])
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
            display_kind = "archive" if detect_kind(f.path) == "archive" else f.kind
            kind_icon = kind_icons.get(display_kind, "📦")
            warn_prefix = "⚠ " if f.missing else ""

            item = FileTreeItem()
            item.setData(0, RAW_NAME_ROLE, name)
            item.setText(0, f"{warn_prefix}{kind_icon}  {name}")
            item.setText(1, f.label)
            item.setText(2, "archive" if detect_kind(f.path) == "archive" else f.kind)
            item.setText(3, self._file_size_str(f))
            item.setText(4, self._fmt_added_at(f.added_at))
            item.setText(5, "📦 仓储" if f.is_relative else "🔗 链接")
            item.setData(0, Qt.UserRole, f.id)
            item.setData(0, FILE_PATH_ROLE, str(self._resolve(f).absolute()))
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
                      "code": "💻", "archive": "🗜", "other": "📦"}

        for f in files:
            name = Path(f.path).name
            display_kind = "archive" if detect_kind(f.path) == "archive" else f.kind
            kind_icon = kind_icons.get(display_kind, "📦")
            warn_prefix = "⚠ " if f.missing else ""

            item = FileTreeItem()
            item.setData(0, RAW_NAME_ROLE, name)
            item.setText(0, f"{warn_prefix}{kind_icon}  {name}")
            item.setText(1, f.label)
            item.setText(2, "archive" if detect_kind(f.path) == "archive" else f.kind)
            item.setText(3, self._file_size_str(f))
            item.setText(4, self._fmt_added_at(f.added_at))
            item.setText(5, "📦 仓储" if f.is_relative else "🔗 链接")
            item.setData(0, Qt.UserRole, f.id)
            item.setData(0, FILE_PATH_ROLE, str(self._resolve(f).absolute()))
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
            # 兼容早期 PySide 将枚举写成 SortOrder.DescendingOrder 的偏好值。
            order = (
                Qt.DescendingOrder if sort_order in ("1", "SortOrder.DescendingOrder")
                else Qt.AscendingOrder
            )
            self.tbl_files.sortByColumn(int(sort_col), order)

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
        self.repo.set_project_setting(pid, "files_flat_sort_order", str(order.value))


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
            if col == 0:
                return natural_name_key(item.data(0, RAW_NAME_ROLE) or item.text(0))
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

            # 目录始终在前并按自然名称升序，文件遵循所选列及方向。
            dirs.sort(key=lambda n: natural_name_key(n.data(0, RAW_NAME_ROLE) or n.text(0)))
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
        if self.repo.get_setting(STORAGE_RECYCLE_SOURCE_KEY, "0") == "1":
            hint.setText(
                "本库已开启原文件回收：仓储复制及入库成功后，原文件将移入回收站。\n"
                "原目录及未导入文件保留；回收站不可用时保留原文件并提示。"
            )
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
        cleanup_candidates: list[tuple[SourceSnapshot, str, int]] = []
        for item in paths:
            if isinstance(item, PendingFile):
                src = str(item.src)
                subfolder = item.subfolder
            else:
                src = str(item)
                subfolder = ""
            try:
                candidate = self._import_one(
                    project, src, ask_label=ask_label, storage=storage, subfolder=subfolder,
                )
                if candidate is not None:
                    cleanup_candidates.append(candidate)
                added += 1
            except Exception as e:
                errors.append(f"{src}：{e}")
        if added and project.id is not None:
            self.repo.touch_project(project.id)
        cleanup_warnings = recycle_sources_with_progress(self, self.repo, self.library, cleanup_candidates)
        if errors or cleanup_warnings:
            warn(
                self, "文件导入结果",
                f"成功 {added} 个，失败 {len(errors)} 个。"
                + (f"\n原文件处理有 {len(cleanup_warnings)} 条提示，入库结果已保留。" if cleanup_warnings else ""),
                detailed="\n".join(errors + cleanup_warnings),
            )
        return added


    def _import_one(
        self,
        p: Project,
        src: str,
        ask_label: bool = False,
        storage: str | None = None,
        subfolder: str = "",
    ) -> tuple[SourceSnapshot, str, int] | None:
        src_path = Path(src)
        if not src_path.exists():
            raise FileNotFoundError(src)
        mode = storage or (
            self.repo.get_setting("default_storage_mode", "link") or "link"
        )
        snapshot = None
        if mode == "copy":
            if self.repo.get_setting(STORAGE_RECYCLE_SOURCE_KEY, "0") == "1":
                snapshot = capture_storage_source(src_path)
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
        fid = self.repo.add_file(f)
        if snapshot is not None:
            return snapshot, f.path, fid
        return None


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


    def _preview_file_items(self) -> list[QTreeWidgetItem]:
        """按当前显示排序遍历文件，含折叠目录内容，排除隐藏节点和目录。"""
        files: list[QTreeWidgetItem] = []

        def collect(parent: QTreeWidgetItem) -> None:
            for index in range(parent.childCount()):
                item = parent.child(index)
                if item.isHidden():
                    continue
                fid = item.data(0, Qt.UserRole)
                if fid is not None and fid > 0:
                    files.append(item)
                collect(item)

        collect(self.tbl_files.invisibleRootItem())
        return files

    def _preview_file_index(self, items: list[QTreeWidgetItem]) -> int:
        """没有实际选中文件时视为未开始浏览，避免沿用清空选择后的 current。"""
        current = self.tbl_files.currentItem()
        if current is None or not current.isSelected():
            return -1
        return next((index for index, item in enumerate(items) if item is current), -1)

    def _update_preview_file_navigation(self, *_args) -> None:
        items = self._preview_file_items()
        index = self._preview_file_index(items)
        self.preview.set_file_navigation(index > 0, index + 1 < len(items), index + 1, len(items))

    def _navigate_preview_file(self, step: int, *, autoplay: bool | None = None) -> None:
        """翻页等同于在文件列表单选相邻文件，沿用原预览和自动播放逻辑。"""
        if step not in (-1, 1):
            return
        items = self._preview_file_items()
        index = self._preview_file_index(items)
        target_index = index + step
        if not 0 <= target_index < len(items):
            self._update_preview_file_navigation()
            return
        target = items[target_index]
        parent = target.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        # Qt 的选择/current 信号先后顺序不同，合并成一次完整选择后再更新预览。
        blocked = self.tbl_files.blockSignals(True)
        try:
            self.tbl_files.setCurrentItem(target, 0, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        finally:
            self.tbl_files.blockSignals(blocked)
        self.tbl_files.scrollToItem(target)
        self._on_file_selected(autoplay=autoplay)

    def _on_preview_video_finished(self, path: str) -> None:
        """播完动作按当前库即时读取；仅处理当前选中文件的自然播放结束。"""
        from PySide6.QtMultimedia import QMediaPlayer
        video = self.preview._video
        player = video._player
        if (self.preview._stack.currentWidget() is not video
                or player.mediaStatus() != QMediaPlayer.EndOfMedia
                or player.source().toLocalFile() != path):
            return
        fid = self._current_file_row_id()
        file = self.repo.get_file(fid) if fid is not None else None
        if file is None or self._resolve(file) != Path(path):
            return
        action = self.repo.get_setting("video_end_action", "stop")
        if action == "loop":
            player.setPosition(0)
            player.play()
        elif action == "next":
            # 连播是独立的显式偏好，不受手动选中视频的自动播放开关影响。
            self._navigate_preview_file(1, autoplay=True)
            if (self._current_file_row_id() != fid
                    and self.preview._stack.currentWidget() is video
                    and player.source().toLocalFile() == path
                    and player.mediaStatus() == QMediaPlayer.EndOfMedia):
                # 两条登记记录可能引用同一个路径；重复源保护不会重新载入它。
                player.setPosition(0)
                player.play()

    def _on_file_selected(self, *, autoplay: bool | None = None) -> None:
        self._update_preview_file_navigation()
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
        self.preview.set_file_context(f.id, f.label or Path(f.path).name)
        self.preview.show_file(
            str(path),
            autoplay=(self.repo.get_setting("video_autoplay", "0") == "1"
                      if autoplay is None else autoplay),
        )

    def _relink_preview_file(self) -> None:
        """离线预览只重关联当前文件，多选时不扩大操作范围。"""
        fid = self._current_file_id
        file = self.repo.get_file(fid) if fid is not None else None
        if file is not None:
            self._relink_files_to_directory([file])


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
                              "code": "💻", "archive": "🗜", "other": "📦"}
                display_kind = "archive" if detect_kind(f.path) == "archive" else f.kind
                item.setText(0, f"{kind_icons.get(display_kind, '📦')}  {name}")
            elif f and column == 2:
                item.setText(2, "archive" if detect_kind(f.path) == "archive" else f.kind)
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

    def action_extract_archive(self) -> None:
        """将当前压缩包解压到本项目的同名子目录。"""
        from .archive_actions import extract_current_archive
        extract_current_archive(self)


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
                + "\n⚠ 回收站不可用时将直接永久删除，无法从回收站恢复。"
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
        permanently_deleted: list[str] = []
        for f in files_to_delete:
            if f.is_relative:
                try:
                    if move_to_trash(self.library.resolve(f.path, True)) == "deleted":
                        permanently_deleted.append(Path(f.path).name)
                except OSError as e:
                    delete_failures.append(f"{Path(f.path).name}：{e}")
            if f.id is not None:
                self.repo.delete_file(f.id)
        if self._current_project_id is not None:
            self.repo.touch_project(self._current_project_id)
            self._show_project(self.repo.get_project(self._current_project_id))
        if permanently_deleted:
            delete_failures.append("回收站不可用，以下文件已永久删除：\n" + "\n".join(permanently_deleted))
        if delete_failures:
            warn(
                self, "文件移除结果",
                "文件记录已移除。以下磁盘操作失败或回退为永久删除，请查看明细。",
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


    def action_convert_project_to_storage(self, project_id: int) -> None:
        """转换整个项目的链接文件，不受视图过滤、折叠和文件选择影响。"""
        self.action_convert_projects_to_storage([project_id])


    def action_convert_projects_to_storage(self, project_ids: list[int]) -> None:
        """合并所选项目为一个转换批次，整批写回后再回收共享源文件。"""
        projects = [project for pid in dict.fromkeys(project_ids)
                    if (project := self.repo.get_project(pid)) is not None]
        if not projects:
            return
        files = [file for project in projects for file in self.repo.list_files(project.id)]
        scope = (f"项目「{projects[0].title}」" if len(projects) == 1
                 else f"所选 {len(projects)} 个项目")
        self._convert_files_to_storage(
            files, scope_label=scope,
        )


    def _convert_files_to_storage(
        self, files: list[FileItem], *, scope_label: str = "所选文件",
    ) -> None:
        from ..storage_move import STORAGE_CONVERT_MOVE_KEY
        if self.repo.get_setting(STORAGE_CONVERT_MOVE_KEY, "0") == "1":
            from .storage_move import convert_files_by_move
            convert_files_by_move(self, files, scope_label=scope_label)
            return
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

        recycle_source = self.repo.get_setting(STORAGE_RECYCLE_SOURCE_KEY, "0") == "1"
        source_notice = (
            "本库已开启原文件回收：复制及入库成功后，将原文件移入回收站。\n"
            "原目录保留；仍被本库其他链接引用或无法回收的原文件会保留并提示。"
            if recycle_source else "原外部文件不会被删除（复制语义）。"
        )
        # 确认对话框
        if not confirm(
            self, "确认转换",
            f"{scope_label}：将把 {len(link_files)} 个🔗链接文件复制进库：\n\n"
            + "\n".join(f"  • {f.label or f.path}" for f in link_files[:5])
            + (f"\n  … 等共 {len(link_files)} 个" if len(link_files) > 5 else "")
            + f"\n\n已仓储文件 {len(storage_files)} 个将跳过。\n\n"
            + source_notice,
            yes="转换",
        ):
            return

        # task #36：复制放后台线程；DB 更新回主线程按结果清单应用
        def _do(progress_cb, is_cancelled):
            results: list = []  # (file_id, rel_path, source_snapshot)
            errors: list[str] = []
            total = len(link_files)
            for i, f in enumerate(link_files):
                if is_cancelled():
                    break
                progress_cb(i, total, f"正在处理：{f.label or f.path}")
                abs_src = f.path
                target = self.library.root / f"project_{f.project_id}" / Path(f.path).name
                stage = "解析源文件"
                try:
                    abs_src = self.library.resolve(f.path, is_relative=False)
                    stage = "检查源文件"
                    if not abs_src.exists():
                        raise FileNotFoundError(f"原文件不存在：{abs_src}")
                    stage = "读取源文件状态"
                    snapshot = capture_storage_source(abs_src) if recycle_source else None
                    # 与 import_copy 的冲突命名一致，以便复制失败时仍能显示尝试的目标。
                    j = 1
                    while target.exists():
                        target = target.with_name(f"{abs_src.stem}_{j}{abs_src.suffix}")
                        j += 1
                    stage = "复制文件到库内"
                    rel_path = self.library.import_copy(f.project_id, abs_src)
                    results.append((f.id, rel_path, snapshot))
                except Exception as e:
                    errors.append(_file_operation_error(f, abs_src, target, stage, e))
            progress_cb(total, total, "")
            return {"results": results, "errors": errors,
                    "cancelled": is_cancelled()}

        def _on_done(payload):
            copied = 0
            errors = list(payload["errors"])
            originals = {f.id: f for f in link_files}
            cleanup_candidates: list[tuple[SourceSnapshot, str, int]] = []
            for fid, rel_path, snapshot in payload["results"]:
                original = originals[fid]
                target = self.library.root / rel_path
                try:
                    f = self.repo.get_file(fid)
                    if f is None:
                        raise LookupError(f"文件记录 {fid} 已不存在")
                    f.path = rel_path
                    f.is_relative = True
                    self.repo.update_file(f)
                    copied += 1
                    if snapshot is not None:
                        cleanup_candidates.append((snapshot, rel_path, fid))
                except Exception as exc:
                    rollback_note = _rollback_file_update(self.repo)
                    errors.append(_file_operation_error(
                        original, original.path, target, "写入仓储文件记录", exc,
                        outcome="保留原文件；库内副本已复制，但未完成记录更新，副本保留于目标路径。" + rollback_note,
                    ))

            # 等整批记录更新后再判断剩余引用，避免同源多条记录提前回收。
            cleanup_warnings = recycle_sources_with_progress(self, self.repo, self.library, cleanup_candidates)
            unhandled = len(link_files) - copied - len(errors)
            lines = [f"✅ 已复制到库内：{copied} 个"]
            if payload["cancelled"] and unhandled > 0:
                lines.append(f"⏸ 已取消：{unhandled} 个未处理")
            if errors:
                lines.append(f"⏭ 失败/跳过：{len(errors)} 个")
                lines.extend(f"  • {detail.splitlines()[0]}" for detail in errors[:3])
                lines.append("完整失败文件列表和原因请展开「详细信息」。")
            if cleanup_warnings:
                lines.append(f"\n原文件处理有 {len(cleanup_warnings)} 条提示（入库结果已保留）：")
                lines.extend(f"  • {message}" for message in cleanup_warnings[:10])
            elif recycle_source and copied:
                lines.append("成功转换的原文件已移入回收站，原目录保留。")

            info(self, "转换完成", "\n".join(lines),
                 detailed="\n".join(errors + cleanup_warnings) or None)
            # 刷新文件表
            self._refresh_files_table()

        def _on_error(msg):
            details = [_file_operation_error(
                f, f.path, self.library.root / f"project_{f.project_id}",
                "复制转仓储后台任务", RuntimeError(msg),
                outcome="批次异常中断，文件处理状态未确认，请检查源路径和库内目标目录。",
            ) for f in link_files]
            warn(self, "转换失败", "后台任务异常中断，请展开详细信息查看本批文件清单及错误。",
                 detailed="\n\n".join(details))

        run_with_progress(
            self, "链接转仓储", f"{scope_label}：正在转换...", _do,
            on_done=_on_done,
            on_error=_on_error,
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
            results: list = []  # (file_id, source_path, new_abs_path)
            errors: list[str] = []
            total = len(files)
            for i, f in enumerate(files):
                if is_cancelled():
                    break
                progress_cb(i, total, f"正在处理：{f.label or f.path}")
                abs_src = f.path
                dst = target_dir / Path(f.path).name
                stage = "解析源文件"
                try:
                    abs_src = self.library.resolve(f.path, f.is_relative)
                    stage = "检查源文件"
                    if not abs_src.exists():
                        raise FileNotFoundError(f"文件不存在：{abs_src}")

                    # 目标路径（同名冲突自动加序号）
                    dst = target_dir / abs_src.name
                    j = 1
                    while dst.exists():
                        dst = target_dir / f"{abs_src.stem}_{j}{abs_src.suffix}"
                        j += 1

                    stage = "移动物理文件"
                    shutil.move(str(abs_src), str(dst))
                    results.append((f.id, str(abs_src), str(dst.resolve())))
                except Exception as e:
                    errors.append(_file_operation_error(f, abs_src, dst, stage, e))
            progress_cb(total, total, "")
            return {"results": results, "errors": errors,
                    "cancelled": is_cancelled()}

        def _on_done(payload):
            moved = 0
            errors = list(payload["errors"])
            originals = {f.id: f for f in files}
            for fid, source, new_abs in payload["results"]:
                try:
                    f = self.repo.get_file(fid)
                    if f is None:
                        raise LookupError(f"文件记录 {fid} 已不存在")
                    f.path = new_abs
                    f.is_relative = False  # 移动后都变成外部链接
                    self.repo.update_file(f)
                    moved += 1
                except Exception as exc:
                    rollback_note = _rollback_file_update(self.repo)
                    errors.append(_file_operation_error(
                        originals[fid], source, new_abs, "写入移动后的文件记录", exc,
                        outcome=f"物理文件已移动，实际位置：{new_abs}；记录未完成更新，请重关联到此位置。" + rollback_note,
                    ))

            unhandled = len(files) - moved - len(errors)
            lines = [f"✅ 已移动：{moved} 个"]
            if payload["cancelled"] and unhandled > 0:
                lines.append(f"⏸ 已取消：{unhandled} 个未处理")
            if errors:
                lines.append(f"⏭ 失败：{len(errors)} 个")
                lines.extend(f"  • {detail.splitlines()[0]}" for detail in errors[:3])
                lines.append("完整失败文件列表、实际位置和原因请展开「详细信息」。")

            info(self, "移动完成", "\n".join(lines), detailed="\n\n".join(errors) or None)
            # 刷新文件表
            self._refresh_files_table()

        def _on_error(msg):
            details = [_file_operation_error(
                f, self.library.root / f.path if f.is_relative else f.path,
                target_dir / Path(f.path).name, "移动文件后台任务", RuntimeError(msg),
                outcome="批次异常中断，实际位置未确认；已完成的物理移动不会自动回滚，请检查源路径和目标目录。",
            ) for f in files]
            warn(self, "移动失败", "后台任务异常中断，请展开详细信息查看本批文件清单及错误。",
                 detailed="\n\n".join(details))

        run_with_progress(
            self, "移动文件", f"{scope_label}：正在移动...", _do,
            on_done=_on_done,
            on_error=_on_error,
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
            details = [_file_operation_error(
                f, self.library.resolve(f.path, f.is_relative), source_dir / Path(f.path).name,
                "读取重关联目录", e,
            ) for f in files]
            warn(self, "无法读取目录", f"读取目录失败，{len(files)} 个文件未处理。",
                 detailed="\n\n".join(details))
            return

        # 匹配文件
        matched = []
        unmatched = []
        for f in files:
            name = Path(f.path).name
            if name in available_files:
                matched.append((f, available_files[name]))
            else:
                unmatched.append(_file_operation_error(
                    f, self.library.resolve(f.path, f.is_relative), source_dir / name,
                    "匹配重关联文件", FileNotFoundError("指定目录下未找到同名文件"),
                ))

        if not matched:
            warn(
                self, "未找到匹配文件",
                f"{scope_label}中没有一个文件能在指定目录下找到同名文件。\n\n"
                "请确认文件是否在选择的目录中。",
                detailed="\n\n".join(unmatched),
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
            detailed="\n\n".join(unmatched) or None,
        ):
            return

        # 执行重关联
        relinked = 0
        errors: list[str] = []
        for f, new_path in matched:
            old_path, old_relative = f.path, f.is_relative
            source = self.library.resolve(old_path, old_relative)
            stage = "解析重关联目标"
            try:
                f.path = str(new_path.resolve())
                f.is_relative = False
                stage = "写入重关联文件记录"
                self.repo.update_file(f)
                # 更新成功后才清除 missing，避免失败记录提前变成正常。
                stage = "清除文件缺失标记"
                self.repo.set_file_missing(f.id, False)
                relinked += 1
            except Exception as e:
                rollback_note = _rollback_file_update(self.repo)
                f.path, f.is_relative = old_path, old_relative
                outcome = "物理文件未改动。"
                if stage == "清除文件缺失标记":
                    outcome += "目标路径已更新，但缺失标记未完成更新。"
                errors.append(_file_operation_error(
                    f, source, new_path, stage, e, outcome=outcome + rollback_note,
                ))

        # 结果反馈
        lines = [f"✅ 已重关联：{relinked} 个"]
        if errors:
            lines.append(f"⏭ 错误：{len(errors)} 个")
        if unmatched:
            lines.append(f"⏭ 未找到同名文件：{len(unmatched)} 个")
        if errors or unmatched:
            lines.append("完整失败及未匹配文件列表请展开「详细信息」。")

        info(
            self, "重关联完成",
            "\n".join(lines), detailed="\n\n".join(errors + unmatched) or None,
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
            if self._apply_project_cover(p, fid):
                self.statusBar().showMessage("已设为封面", 4000)
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
            logging.getLogger(__name__).exception("保存封面快照失败")
            warn(self, "保存封面失败", "未能保存新封面，详细信息中包含失败阶段和回滚结果。", detailed=str(e))
            return
        if self._apply_project_cover(p, cover_fid, new_snapshot=True):
            self.statusBar().showMessage("已截取当前画面并设为封面", 4000)


    def _apply_project_cover(self, project: Project, cover_fid: int,
                             *, new_snapshot: bool = False) -> bool:
        """统一图片、截图和剪切板入口，并将清理问题集中报告一次。"""
        from ..cover_files import CoverFileError, discard_cover_snapshot, replace_project_cover
        try:
            cleanup_warnings = replace_project_cover(self.repo, self.library, project, cover_fid)
        except Exception as exc:
            logging.getLogger(__name__).exception("保存项目封面失败")
            snapshot = None
            details = [str(exc)] if isinstance(exc, CoverFileError) else [
                f"文件/记录 ID：{cover_fid}\n源路径/目标路径：（新封面路径未能读取）\n"
                f"失败阶段：设置项目 {project.id} 的封面\n原因：{type(exc).__name__}: {exc}\n"
                "处理结果：设置未完成，旧封面清理状态需查看后续信息。"
            ]
            if new_snapshot and not (isinstance(exc, CoverFileError) and exc.rollback_failed):
                try:
                    snapshot = self.repo.get_file(cover_fid)
                    if snapshot is not None:
                        failures = discard_cover_snapshot(self.repo, self.library, snapshot)
                        details.extend(failures or ["回滚结果：本次未使用的新封面已回收，文件记录已移除。"])
                except Exception as cleanup_exc:
                    logging.getLogger(__name__).exception("清理未使用的新封面失败")
                    actual_path = str(self.library.resolve(snapshot.path, snapshot.is_relative)) if snapshot else "（新封面路径未能读取）"
                    details.append(
                        f"文件/记录 ID：{cover_fid}\n源路径/目标路径：{actual_path}\n"
                        f"失败阶段：清理未使用的新封面\n原因：{type(cleanup_exc).__name__}: {cleanup_exc}\n"
                        f"处理结果：无法确认清理状态；实际保留位置需核实：{actual_path}"
                    )
            warn(self, "保存封面失败", "未能完成新封面设置，详细信息中包含失败阶段和回滚结果。",
                 detailed="\n\n".join(details))
            return False
        self._refresh_cover_without_preview_reset(project)
        if cleanup_warnings:
            warn(self, "封面已设置，旧封面清理未完成",
                 "新封面已成功设置；旧封面未能全部清理，请查看详细信息。",
                 detailed="\n\n".join(cleanup_warnings))
        return True


    def _refresh_cover_without_preview_reset(self, project: Project) -> None:
        """只刷新封面和文件清单，保留当前选择、视频/PDF 的预览状态。"""
        self.proj_model.update_project(
            project, self._cover_pix(project), len(self.repo.list_files(project.id)),
        )
        self._show_project(project, preserve_preview=True)


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
            logging.getLogger(__name__).exception("保存剪切板封面快照失败")
            warn(self, "保存封面失败", "未能保存新封面，详细信息中包含失败阶段和回滚结果。", detailed=str(e))
            return
        if self._apply_project_cover(p, cover_fid, new_snapshot=True):
            self.statusBar().showMessage("已从剪切板设置封面", 4000)


    def _save_cover_snapshot(self, p: Project, pix: QPixmap, source_kind: str) -> int:
        """落盘并登记新快照，失败时回滚本次生成物。"""
        from ..cover_files import save_cover_snapshot
        return save_cover_snapshot(self.repo, self.library, p, pix, source_kind)


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
            detail = _file_operation_error(f, old_abs, new_abs, "检查重命名目标", FileExistsError("目标文件已存在"))
            warn(self, "已存在", f"目标文件已存在：\n{new_abs}", detailed=detail)
            return
        if not old_abs.exists():
            detail = _file_operation_error(f, old_abs, new_abs, "检查重命名源文件", FileNotFoundError("原文件不存在"))
            warn(self, "文件不存在", f"原文件不存在：\n{old_abs}", detailed=detail)
            return

        old_path, old_relative = f.path, f.is_relative
        renamed = False
        stage = "重命名物理文件"
        try:
            old_abs.rename(new_abs)
            renamed = True
            stage = "写入重命名后的文件记录"
            if f.is_relative:
                f.path = (Path(f.path).parent / new_name).as_posix()
            else:
                f.path = str(new_abs.resolve())
            self.repo.update_file(f)
        except Exception as e:
            rollback_note = _rollback_file_update(self.repo) if renamed else ""
            f.path, f.is_relative = old_path, old_relative
            outcome = (
                f"物理文件已重命名，实际位置：{new_abs}；记录未完成更新，请重关联到此位置。"
                if renamed else "物理重命名未完成，请检查源路径和目标路径。"
            )
            detail = _file_operation_error(f, old_abs, new_abs, stage, e, outcome=outcome + rollback_note)
            warn(self, "重命名失败", outcome, detailed=detail)
            if renamed:
                self._refresh_files_table()
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
            # 落回所选文件自身不是移动，不能把它解释为追加到目录末尾。
            return

        target_subfolder = (target_subfolder or "").strip("/")
        selected_files = [self.repo.get_file(fid) for fid in selected_ids]
        selected_files = [
            f for f in selected_files
            if f is not None and f.project_id == pid and f.id is not None
        ]
        if not selected_files:
            return

        source_subfolders = {f.subfolder or "" for f in selected_files}
        cross_folder = source_subfolders != {target_subfolder}
        if not cross_folder and target_file_id is None:
            # 已在该目录，落到目录/空白处不改变当前排序。
            return

        all_files = self.repo.list_files(pid)
        by_id = {f.id: f for f in all_files}
        target_file = by_id.get(target_file_id)
        if target_file_id is not None and (target_file is None or target_file.subfolder != target_subfolder):
            return

        if not cross_folder:
            # 手工插入时从屏幕上的顺序开始；不能退回旧导入 ord，连带打乱其他目录。
            displayed_ids = [int(item.data(0, Qt.UserRole)) for item in self._preview_file_items()]
            selected_set = {f.id for f in selected_files}
            sibling_ids = [fid for fid in displayed_ids if by_id[fid].subfolder == target_subfolder]
            selected_order = [fid for fid in sibling_ids if fid in selected_set]
            remaining = [fid for fid in sibling_ids if fid not in selected_set]
            if target_file_id not in remaining or len(selected_order) != len(selected_set):
                return
            insert_at = remaining.index(target_file_id) + (0 if before else 1)
            reordered = remaining[:insert_at] + selected_order + remaining[insert_at:]
            if reordered == sibling_ids:
                return
            # 切换项目级自定义顺序前，将所有可见目录的当前顺序作为基线保存。
            # 隐藏项保留原有相对次序，不在拖动时丢失记录。
            visible = set(displayed_ids)
            baseline = displayed_ids + [f.id for f in all_files if f.id not in visible]
            self.repo.reorder_files(baseline)
            self.repo.reorder_files(reordered)
            self._set_tree_sort_state("custom", False)
            self._refresh_files_table()
            self.statusBar().showMessage("已调整文件顺序；点击“文件名”表头可恢复自然排序", 4000)
            return

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

        # 跨目录只修改归属；保留名称/类型等列排序，不将全项目切回导入顺序。
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

        # 对已选中文件打开菜单时保留多选，批量转换等操作才能覆盖完整选择。
        selection_command = (
            QItemSelectionModel.NoUpdate if item.isSelected()
            else QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
        )
        self.tbl_files.setCurrentItem(item, 0, selection_command)
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
        current_file = self.repo.get_file(fid)
        if current_file is not None and detect_kind(current_file.path) == "archive":
            menu.addAction("🗜  解压到当前项目…", self.action_extract_archive)
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
