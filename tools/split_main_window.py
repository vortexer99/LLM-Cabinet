"""一次性重构脚本（task #35）：把 app/ui/main_window.py 按职责拆成 mixin 文件。

原理：用 AST 定位 MainWindow 类里每个方法的源码区间，按"方法名 → mixin"映射
搬运到 mw_library.py / mw_search.py / mw_dnd.py / mw_files.py / mw_projects.py；
main_window.py 保留类骨架（__init__ / _build_ui / splitter / 窗口状态）。
搬运不改任何方法体文本，保证零手误。

用法：python tools/split_main_window.py（幂等性不保证，跑一次后请删除或保留归档）
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path("app/ui/main_window.py")

# ---------------------------------------------------------------- 方法分配
LIBRARY = {
    "_build_menubar", "_lib_rebuild_recent_menu", "_lib_switch",
    "_lib_back_to_welcome", "_lib_new", "_lib_open_recent",
    "_confirm_and_restart_to", "_lib_info", "_lib_import_api",
    "_release_active_db_resources", "_lib_manage_recent",
    "_tools_open_wizards", "_tools_check_consistency",
    "_show_consistency_report", "_tools_backup_library",
    "_tools_restore_library", "_tools_import_package",
}
SEARCH = {
    "_update_search_completion", "_apply_completion", "_focus_search",
    "_apply_search_query", "_record_search_history", "_show_search_menu",
    "_delete_search_history", "_delete_saved_search", "_save_current_search",
}
DND = {
    "_install_dnd", "eventFilter", "dragEnterEvent", "dragMoveEvent",
    "dragLeaveEvent", "dropEvent", "_show_drop_zone", "_hide_drop_zone",
    "_filter_library_paths", "_warn_if_deep_or_large",
    "_on_dropped_on_project", "_on_dropped_on_files_table",
    "_on_dropzone_dropped", "_create_empty_project", "_warn_empty_import",
    "_handle_multi_folder_drop", "_run_batch_folder_import",
    "_on_drag_hover_changed", "_set_drag_hover", "_expand_paths",
    "_drop_into_project", "_drop_create_project",
}
FILES = {
    "_build_right_panel", "_toggle_files_detach", "_detach_files_panel",
    "_attach_files_panel", "_on_origin_filter_toggled",
    "_toggle_files_view_mode", "_set_files_view_mode",
    "_apply_files_columns_prefs", "_save_files_columns_prefs",
    "_on_files_section_resized", "_files_header_context_menu",
    "_toggle_file_column", "_reset_files_columns_to_default",
    "_file_size_str", "_fmt_added_at",
    "_load_explicit_subfolders", "_save_explicit_subfolders",
    "_tree_sort_state", "_set_tree_sort_state",
    "_connect_files_tree_header", "_disconnect_files_header_clicked",
    "_on_files_tree_header_clicked", "_populate_files_tree",
    "_populate_files_flat", "_on_files_flat_header_clicked", "_sort_files_tree",
    "_selected_tree_subfolder", "_selected_file_ids", "_selected_files",
    "_files_under_subfolder", "_on_file_selected", "_on_file_item_changed",
    "_on_file_double_clicked", "_current_file_row_id",
    "action_open_current_file", "action_reveal_current_file",
    "action_delete_files", "_selected_dir_subfolders",
    "_subfolder_from_tree_node",
    "action_convert_to_storage", "action_convert_folder_to_storage",
    "_convert_files_to_storage", "action_move_file", "action_move_folder",
    "_move_files_to_location", "action_relink_file", "action_relink_folder",
    "_relink_files_to_directory", "action_replace_link_target",
    "action_set_cover", "action_set_cover_from_clipboard",
    "_save_cover_snapshot", "action_new_subfolder", "action_rename_file",
    "action_rename_physical_file", "action_rename_subfolder",
    "action_delete_empty_subfolder", "_on_files_moved",
    "_refresh_files_table", "_file_context_menu", "_files_f2_rename",
    "action_add_files", "_ask_storage_for_import", "_import_files",
    "_import_one",
}
PROJECTS = {
    "refresh_projects", "_refresh_tag_tree", "_on_tag_filter_changed",
    "_on_tag_action", "_on_projects_dropped_on_tag",
    "_selected_project_ids", "_on_project_selected", "_show_multi_selection",
    "_batch_add_tag_dialog", "_batch_llm_suggest", "_batch_mark_mcp_seen",
    "_show_project", "_desc_plain", "_rebuild_columns", "_apply_col_widths",
    "_cover_pix", "action_new_project", "action_edit_project",
    "action_delete_project", "action_export_project", "_export_batch",
    "_show_export_result", "_select_project_by_id", "_project_context_menu",
    "_project_context_target_ids", "_on_mark_mcp_seen",
    "action_open_settings", "action_open_settings_fields",
    "_on_user_action_dismiss_banner", "action_open_mcp_audit",
    "_check_mcp_activity", "action_open_llm_tasks",
    "action_llm_suggest_for_project", "_launch_llm_from_dialog",
    "_enqueue_meta_suggest", "_llm_check_configured_or_prompt",
    "_on_llm_counts", "_on_llm_suggestions_added", "_on_llm_task_failed",
    "_set_view_mode",
}

# ---------------------------------------------------------------- 各文件头部
HEADER_COMMON = '''"""{title}（task #35：从 main_window.py 拆分，方法体未改动）。

{mixin_doc}
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models import FileItem, Project
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
from .dialogs import ask_yes_no_cancel, confirm, error, info, warn
from .files_table_columns import (
    COLUMNS as FILES_COLUMNS,
    SETTING_KEY as FILES_COLUMNS_SETTING_KEY,
    column_by_key as files_column_by_key,
    dump_prefs as files_dump_prefs,
    load_prefs as files_load_prefs,
    resolve_pref as files_resolve_pref,
)
from .palette import current as _current_palette
from .workers import ExportSnapshotRepo, run_with_progress

logger = logging.getLogger("llm_cabinet.ui")


'''

CLASS_TMPL = '''class {cls}:
    """{doc}"""

'''


def main() -> None:
    src = SRC.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)

    # 找 MainWindow 类与模块级元素
    mw = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    no_elide = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NoElideDelegate")
    ask_delete = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_ask_delete_mode")

    def seg(node) -> str:
        # ast 行号是 1-based 且含装饰器起点
        start = node.lineno - 1
        if getattr(node, "decorator_list", None) and node.decorator_list:
            start = node.decorator_list[0].lineno - 1
        return "".join(lines[start:node.end_lineno])

    methods: dict[str, str] = {}
    class_attrs: dict[str, str] = {}  # 类级赋值（常量），name -> 源码
    for n in mw.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods[n.name] = seg(n)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    class_attrs[t.id] = seg(n)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            class_attrs[n.target.id] = seg(n)

    # 类属性归属（未列出的默认留在 main_window）
    ATTRS_FILES = {"FILES_TREE_SORT_SETTING_KEY", "EXPLICIT_SUBFOLDERS_SETTING_KEY"}
    ATTRS_PROJECTS = {"DEFAULT_COL_WIDTHS"}

    assigned = LIBRARY | SEARCH | DND | FILES | PROJECTS
    all_names = set(methods)
    keep = all_names - assigned
    dup = assigned & keep
    assert not dup
    # 检查：映射表里没有漏掉 / 不存在的方法
    unknown = assigned - all_names
    assert not unknown, f"映射了不存在的方法: {unknown}"
    print(f"保留在 main_window.py: {sorted(keep)}")
    print(f"类属性: {sorted(class_attrs)}")

    groups = [
        ("mw_library.py", "LibraryMenuMixin", "库菜单（切换/新建/删除/备份恢复/工具菜单）", LIBRARY),
        ("mw_search.py", "SearchMixin", "搜索框行为：补全、历史/收藏菜单、保存搜索", SEARCH),
        ("mw_dnd.py", "DnDMixin", "拖放导入：DropZone 显隐、拖放编排、批量文件夹导入", DND),
        ("mw_files.py", "FilesPanelMixin", "右栏文件面板：文件表、子文件夹、文件操作、封面", FILES),
        ("mw_projects.py", "ProjectsMixin", "项目列表：刷新/筛选/选择、项目操作、导出、LLM 入口", PROJECTS),
    ]

    out_dir = SRC.parent
    attr_owner = {
        "mw_files.py": ATTRS_FILES,
        "mw_projects.py": ATTRS_PROJECTS,
    }
    for fname, cls, doc, names in groups:
        body = [HEADER_COMMON.format(title=doc, mixin_doc=f"Mixin：{doc}")]
        if fname == "mw_library.py":
            # 模块级函数 _ask_delete_mode 放在类之前
            body.append(seg(ask_delete) + "\n\n\n")
        body.append(CLASS_TMPL.format(cls=cls, doc=doc))
        # 类属性（常量）先行
        for attr in attr_owner.get(fname, set()):
            if attr in class_attrs:
                body.append("    " + class_attrs[attr].strip() + "\n\n")
        ordered = [n for n in methods if n in names]
        for name in ordered:
            body.append(methods[name].rstrip() + "\n\n\n")
        text = "".join(body).rstrip() + "\n"
        (out_dir / fname).write_text(text, encoding="utf-8", newline="\n")
        print(f"写出 {fname}: {len(ordered)} 个方法")

    # ---- 新 main_window.py ----
    # 头部：取原文件开头到 NoElideDelegate 之前（docstring + imports + logger）
    head_start = 0
    head_end = no_elide.lineno - 1
    header = "".join(lines[head_start:head_end])

    mixin_imports = (
        "\nfrom .mw_dnd import DnDMixin\n"
        "from .mw_files import FilesPanelMixin\n"
        "from .mw_library import LibraryMenuMixin\n"
        "from .mw_projects import ProjectsMixin\n"
        "from .mw_search import SearchMixin\n"
    )

    new_class_def = (
        "class MainWindow(\n"
        "    LibraryMenuMixin,\n"
        "    ProjectsMixin,\n"
        "    FilesPanelMixin,\n"
        "    DnDMixin,\n"
        "    SearchMixin,\n"
        "    QMainWindow,\n"
        "):"
    )

    parts = [header, mixin_imports, "\n\n", seg(no_elide), "\n\n\n"]
    # MainWindow 类：新 class 行 + 留在主文件的类属性 + 保留的方法
    parts.append(new_class_def + "\n")
    for attr, text in class_attrs.items():
        if attr not in ATTRS_FILES and attr not in ATTRS_PROJECTS:
            parts.append("    " + text.strip() + "\n\n")
    for name, text in methods.items():
        if name in keep:
            parts.append(text.rstrip() + "\n\n\n")
    new_src = "".join(parts).rstrip() + "\n"
    SRC.write_text(new_src, encoding="utf-8", newline="\n")
    print(f"main_window.py 重写完成，保留 {len(keep)} 个方法")


if __name__ == "__main__":
    main()
