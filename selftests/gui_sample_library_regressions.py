"""样例库 GUI 回归自检。

覆盖 ``docs/sample-library.md`` 中适合自动化的手测项：
- 样例库基线项目/字段
- 搜索历史、字段/文件关键词搜索、全库搜索
- 左侧未分类、待审阅、未读 MCP、标签筛选
- 文件树、扁平视图、来源过滤、视图模式持久化
- 项目编辑对话框中的 LLM 待审阅建议
- MCP audit 对话框筛选
- 样例项目导出时同名外链文件不互相覆盖

不覆盖真实系统交互：文件选择框、真实拖放、资源管理器定位、人工视觉检查。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T, closing_repos

from app.cabinet import resolve_library_paths
from app.db import connect
from app.exporter import ExportOptions, export_project
from app.library import Library
from app.repository import Repository
from app.search_history import HISTORY_SETTING_KEY, SAVED_SEARCHES_SETTING_KEY
from app.ui.main_window import MainWindow
from app.ui.mcp_audit_dialog import MCPAuditDialog
from app.ui.project_dialog import ProjectDialog
from tools.create_sample_library import build_sample_library


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["llm-cabinet-sample-gui-selftest"])
    app.setQuitOnLastWindowClosed(False)
    return app


def _sample_window(root: Path) -> tuple[MainWindow, Repository]:
    build_sample_library(root)
    db_path, library_root = resolve_library_paths(root)
    repo = Repository(connect(db_path))
    win = MainWindow(repo, Library(library_root), db_path=db_path, library_root=root)
    win.show()
    _app().processEvents()
    return win, repo


def _close_window(win: MainWindow) -> None:
    win._mcp_timer.stop()
    win.close()
    win.deleteLater()
    _app().processEvents()


def _titles(win: MainWindow) -> set[str]:
    return {
        p.title
        for row in range(win.proj_model.rowCount())
        if (p := win.proj_model.project_at(row)) is not None
    }


def _project_id(repo: Repository, title: str) -> int:
    for p in repo.list_projects():
        if p.title == title and p.id is not None:
            return int(p.id)
    raise AssertionError(f"project not found: {title}")


def _field_id(repo: Repository, name: str) -> int:
    for f in repo.list_fields():
        if f.name == name and f.id is not None:
            return int(f.id)
    raise AssertionError(f"field not found: {name}")


def _search(win: MainWindow, text: str, *, all_library: bool = True) -> set[str]:
    win.search_box.setText(text)
    win._search_timer.stop()
    win.btn_search_all.setChecked(all_library)
    win.refresh_projects()
    _app().processEvents()
    return _titles(win)


def _tree_items(win: MainWindow) -> list[QTreeWidgetItem]:
    out: list[QTreeWidgetItem] = []

    def walk(item: QTreeWidgetItem) -> None:
        out.append(item)
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(win.tbl_files.topLevelItemCount()):
        walk(win.tbl_files.topLevelItem(i))
    return out


def _file_labels(win: MainWindow) -> list[str]:
    return [
        item.text(1)
        for item in _tree_items(win)
        if (item.data(0, Qt.UserRole) or 0) > 0
    ]


def _dir_paths(win: MainWindow) -> set[str]:
    return {
        item.data(0, Qt.UserRole + 1) or ""
        for item in _tree_items(win)
        if item.data(0, Qt.UserRole) == -1
    }


def test_sample_baseline_and_search(tmp: Path, t: T) -> None:
    win, repo = _sample_window(tmp)
    with closing_repos(repo):
        try:
            expected = {
                "三体研究资料",
                "银河帝国整理",
                "AI Team Workspace 方案",
                "未分类草稿",
                "缺失链接修复样例",
                "导出导入闭环样例",
                "空项目边界样例",
            }
            t.assert_eq("sample library project titles", _titles(win), expected)

            field_names = {f.name for f in repo.list_fields()}
            for name in ("作者", "日期", "评分", "来源", "状态", "优先级", "负责人", "备注"):
                t.assert_in(f"sample field exists: {name}", name, field_names)

            history = json.loads(repo.get_setting(HISTORY_SETTING_KEY, "[]"))
            saved = json.loads(repo.get_setting(SAVED_SEARCHES_SETTING_KEY, "[]"))
            t.assert_in("sample search history has author query", "author:刘慈欣", history)
            t.assert_true("sample saved searches exist", len(saved) >= 3)

            t.assert_eq("sample search author", _search(win, "author:刘慈欣"), {"三体研究资料"})
            t.assert_eq(
                "sample search high rating sci-fi",
                _search(win, "tag:科幻 AND rating:>=4"),
                {"三体研究资料", "银河帝国整理"},
            )
            t.assert_eq(
                "sample search status field",
                _search(win, "状态:待整理"),
                {"三体研究资料", "未分类草稿", "空项目边界样例"},
            )
            t.assert_eq("sample broad file keyword search", _search(win, "深层目录笔记"), {"三体研究资料"})

            win._current_filter_kind = "tag"
            win._current_filter_value = "领域/科幻"
            t.assert_eq(
                "sample sidebar tag combines with keyword",
                _search(win, "视频说明", all_library=False),
                set(),
            )
            t.assert_eq(
                "sample full-library search ignores sidebar",
                _search(win, "视频说明", all_library=True),
                {"未分类草稿"},
            )
        finally:
            _close_window(win)


def test_sample_sidebar_filters(tmp: Path, t: T) -> None:
    win, repo = _sample_window(tmp)
    with closing_repos(repo):
        try:
            cases = [
                ("tag", "领域/科幻", {"三体研究资料", "银河帝国整理"}),
                ("untagged", "", {"未分类草稿"}),
                ("review", "", {"AI Team Workspace 方案"}),
                ("mcp", "", {"AI Team Workspace 方案"}),
            ]
            for kind, value, expected in cases:
                win._current_filter_kind = kind
                win._current_filter_value = value
                win.search_box.setText("")
                win.refresh_projects()
                t.assert_eq(f"sample sidebar filter {kind}", _titles(win), expected)

            win._on_mark_mcp_seen([_project_id(repo, "AI Team Workspace 方案")])
            win._current_filter_kind = "mcp"
            win._current_filter_value = ""
            win.refresh_projects()
            t.assert_eq("sample mcp filter refreshes after mark seen", _titles(win), set())
        finally:
            _close_window(win)


def test_sample_file_view_modes_and_origin_filter(tmp: Path, t: T) -> None:
    win, repo = _sample_window(tmp)
    with closing_repos(repo):
        try:
            three_body_id = _project_id(repo, "三体研究资料")
            win._current_project_id = three_body_id
            win._show_project(repo.get_project(three_body_id))
            _app().processEvents()

            dirs = _dir_paths(win)
            for expected_dir in (
                "source/pdf",
                "notes",
                "notes/drafts",
                "figures",
                "research/2024/phase-a/notes/deep",
                "generated",
            ):
                t.assert_in(f"sample file tree dir: {expected_dir}", expected_dir, dirs)

            labels = _file_labels(win)
            t.assert_in("sample generated file visible by default", "生成封面", labels)
            t.assert_true("sample origin filter button visible", win._btn_origin_filter.isVisible())
            win._btn_origin_filter.setChecked(True)
            win._on_origin_filter_toggled()
            _app().processEvents()
            t.assert_true("sample generated hidden in user-only mode", "生成封面" not in _file_labels(win))
            t.assert_eq(
                "sample origin filter persisted",
                repo.get_project_setting(three_body_id, "files_view_origin_filter", ""),
                "user",
            )

            win._btn_origin_filter.setChecked(False)
            win._on_origin_filter_toggled()
            win._set_files_view_mode("flat", three_body_id)
            _app().processEvents()
            t.assert_eq("sample file view mode is flat", win._files_view_mode, "flat")
            t.assert_eq(
                "sample file view mode persisted",
                repo.get_project_setting(three_body_id, "files_view_mode", ""),
                "flat",
            )
            t.assert_eq(
                "sample flat view has one row per file",
                win.tbl_files.topLevelItemCount(),
                len(repo.list_files(three_body_id)),
            )

            galaxy_id = _project_id(repo, "银河帝国整理")
            win._current_project_id = galaxy_id
            win._show_project(repo.get_project(galaxy_id))
            win._current_project_id = three_body_id
            win._show_project(repo.get_project(three_body_id))
            _app().processEvents()
            t.assert_eq("sample file view mode restores after selection", win._files_view_mode, "flat")

            empty_id = _project_id(repo, "空项目边界样例")
            win._current_project_id = empty_id
            win._show_project(repo.get_project(empty_id))
            _app().processEvents()
            t.assert_eq("sample empty project has no file rows", win.tbl_files.topLevelItemCount(), 0)
            t.assert_true("sample empty project hides origin filter", not win._btn_origin_filter.isVisible())
        finally:
            _close_window(win)


def test_sample_llm_suggestions_in_project_dialog(tmp: Path, t: T) -> None:
    win, repo = _sample_window(tmp)
    with closing_repos(repo):
        try:
            pid = _project_id(repo, "AI Team Workspace 方案")
            status_id = _field_id(repo, "状态")
            p = repo.get_project(pid)
            dlg = ProjectDialog(p, repo=repo, parent=win)
            dlg.show()
            _app().processEvents()
            t.assert_eq("sample pending suggestions shown in project dialog", len(dlg._suggestions), 2)
            t.assert_true("sample bulk suggestion buttons visible", dlg.btn_accept_all.isVisible())

            expected_status = dlg._suggestions[status_id].suggested_value
            dlg._apply_one(status_id)
            dlg._accept()
            repo.save_project(dlg.project())
            _app().processEvents()
            updated = repo.get_project(pid)
            t.assert_eq(
                "sample applied status suggestion writes field",
                updated.field_values.get(status_id),
                expected_status,
            )
            t.assert_eq("sample one pending suggestion remains", len(repo.list_pending_suggestions(pid)), 1)
            dlg.deleteLater()

            dlg2 = ProjectDialog(repo.get_project(pid), repo=repo, parent=win)
            dlg2.show()
            _app().processEvents()
            dlg2._reject_all()
            dlg2._accept()
            repo.save_project(dlg2.project())
            win._current_filter_kind = "review"
            win._current_filter_value = ""
            win.refresh_projects()
            t.assert_true("sample review filter clears after resolving suggestions", "AI Team Workspace 方案" not in _titles(win))
            dlg2.deleteLater()
        finally:
            _close_window(win)


def test_sample_mcp_audit_dialog_filters(tmp: Path, t: T) -> None:
    win, repo = _sample_window(tmp)
    with closing_repos(repo):
        try:
            dlg = MCPAuditDialog(repo, parent=win)
            dlg.show()
            _app().processEvents()
            t.assert_eq("sample mcp audit has seeded rows", dlg.tbl.rowCount(), 3)
            idx = dlg.cmb_status.findText("失败")
            dlg.cmb_status.setCurrentIndex(idx)
            _app().processEvents()
            t.assert_eq("sample mcp audit error filter row count", dlg.tbl.rowCount(), 1)
            t.assert_eq("sample mcp audit error message", dlg.tbl.item(0, 5).toolTip(), "示例错误：路径不存在")
        finally:
            dlg.close()
            dlg.deleteLater()
            _close_window(win)


def test_sample_export_duplicate_links_are_unique(tmp: Path, t: T) -> None:
    win, repo = _sample_window(tmp)
    with closing_repos(repo):
        try:
            pid = _project_id(repo, "导出导入闭环样例")
            export_root = tmp / "exports"
            export_root.mkdir()
            result = export_project(
                repo,
                win.library,
                repo.get_project(pid),
                ExportOptions(target_root=export_root, copy_link_files=True),
            )
            files_json = json.loads((result.project_dir / "files.json").read_text(encoding="utf-8"))
            duplicate_entries = [
                f for f in files_json["files"]
                if f.get("subfolder") == "duplicates"
            ]
            exported_to = [f.get("exported_to") for f in duplicate_entries]
            t.assert_eq("sample duplicate link entries exported", len(exported_to), 2)
            t.assert_eq("sample duplicate link exports are unique", len(set(exported_to)), 2)
            for rel in exported_to:
                t.assert_true(
                    f"sample duplicate exported file exists: {rel}",
                    (result.project_dir / rel).is_file(),
                )
        finally:
            _close_window(win)


def main() -> bool:
    _app()
    t = T()
    print("=" * 60)
    print("sample library GUI regression selftest")
    print("=" * 60)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        tests = [
            ("Baseline and search", test_sample_baseline_and_search),
            ("Sidebar filters", test_sample_sidebar_filters),
            ("File view modes and origin filter", test_sample_file_view_modes_and_origin_filter),
            ("LLM suggestions in project dialog", test_sample_llm_suggestions_in_project_dialog),
            ("MCP audit dialog filters", test_sample_mcp_audit_dialog_filters),
            ("Export duplicate links", test_sample_export_duplicate_links_are_unique),
        ]
        for name, fn in tests:
            print(f"\n-> {name}")
            fn(root / name.replace(" ", "_").lower(), t)
            print("  OK")
    print("\n" + "=" * 60)
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
