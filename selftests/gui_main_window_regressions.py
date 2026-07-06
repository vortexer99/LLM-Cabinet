"""主窗口 GUI 回归自检。

这组测试故意实例化 PySide6 控件，覆盖普通数据层 selftest 测不到的交互状态：
- 搜索框 FocusIn 不应在菜单已打开或刚关闭时反复触发菜单
- “全库”搜索应忽略左侧筛选
- GUI 搜索路径应命中自定义字段值和文件说明
- 项目右键菜单应固定本次右键目标，MCP 已读不能清错项目
- MCP 未读筛选清除后应刷新列表
- 目录项目包和 ZIP 项目包导入/导出 round-trip 应刷新 GUI 列表
- 主 splitter 宽度应写入设置并可被新窗口读取
- 主 splitter 右栏宽度不应随项目选择漂移

运行要求：使用安装了 PySide6 的 Python；Windows 本机通常为
``C:\\Users\\hfdxm\\AppData\\Local\\Python\\bin\\python.exe -B``。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QItemSelectionModel
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T, closing_repos

from app.db import connect
from app.exporter import ExportOptions, export_project
from app.importer import ImportOptions, cleanup_extracted_zips, import_folder_as_project, scan_folders
from app.library import Library
from app.models import FileItem, Project
from app.repository import Repository
from app.search_history import HISTORY_SETTING_KEY
from app.ui.main_window import MainWindow


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["llm-cabinet-gui-selftest"])
    app.setQuitOnLastWindowClosed(False)
    return app


def _window(tmp: Path) -> tuple[MainWindow, Repository]:
    repo = Repository(connect(tmp / "cabinet.db"))
    library = Library(tmp / "library")
    win = MainWindow(repo, library, db_path=tmp / "cabinet.db", library_root=tmp)
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


def _save_project(
    repo: Repository, title: str, tags: list[str] | None = None,
) -> int:
    return repo.save_project(Project(title=title, tags=tags or []))


def _field(repo: Repository, name: str, ftype: str, key: str = "") -> int:
    row = repo.conn.execute("SELECT COALESCE(MAX(ord), -1) AS m FROM fields").fetchone()
    cur = repo.conn.execute(
        "INSERT INTO fields(name, type, ord, visible, key) VALUES(?,?,?,?,?)",
        (name, ftype, int(row["m"]) + 1, 1, key or None),
    )
    repo.conn.commit()
    return int(cur.lastrowid)


def test_search_menu_focus_guard(tmp: Path, t: T) -> None:
    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            repo.set_setting(HISTORY_SETTING_KEY, json.dumps(["三体"]))
            calls = {"n": 0}

            def fake_show_search_menu() -> None:
                calls["n"] += 1

            win._show_search_menu = fake_show_search_menu  # type: ignore[method-assign]
            win._search_menu_open = False
            win._search_menu_last_closed_at = 0.0
            win.eventFilter(win.search_box, QEvent(QEvent.FocusIn))
            _app().processEvents()
            t.assert_eq("focus opens search menu once", calls["n"], 1)

            win._search_menu_open = True
            win.eventFilter(win.search_box, QEvent(QEvent.FocusIn))
            _app().processEvents()
            t.assert_eq("open menu suppresses repeated focus open", calls["n"], 1)

            win._search_menu_open = False
            win._search_menu_last_closed_at = time.monotonic()
            win.eventFilter(win.search_box, QEvent(QEvent.FocusIn))
            _app().processEvents()
            t.assert_eq("recently closed menu suppresses focus reopen", calls["n"], 1)
        finally:
            _close_window(win)


def test_search_all_toggle_ignores_sidebar_filter(tmp: Path, t: T) -> None:
    repo = Repository(connect(tmp / "cabinet.db"))
    with closing_repos(repo):
        _save_project(repo, "三体 设定集", ["领域/科幻"])
        _save_project(repo, "三体 厨房笔记", ["生活/烹饪"])
    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            win.search_box.setText("三体")
            win._search_timer.stop()
            win._current_filter_kind = "tag"
            win._current_filter_value = "领域/科幻"
            win.btn_search_all.setChecked(False)
            win.refresh_projects()
            t.assert_eq(
                "keyword combines with sidebar filter",
                _titles(win),
                {"三体 设定集"},
            )

            win.btn_search_all.setChecked(True)
            win.refresh_projects()
            t.assert_eq(
                "search all ignores sidebar filter",
                _titles(win),
                {"三体 设定集", "三体 厨房笔记"},
            )
        finally:
            _close_window(win)


def test_gui_keyword_searches_fields_and_files(tmp: Path, t: T) -> None:
    repo = Repository(connect(tmp / "cabinet.db"))
    with closing_repos(repo):
        fid_author = _field(repo, "作者", "text", "author")
        pid_author = repo.save_project(Project(
            title="作者字段命中",
            description_md="标题和描述没有目标词",
            field_values={fid_author: "刘慈欣"},
        ))
        pid_file = _save_project(repo, "文件说明命中")
        repo.add_file(FileItem(
            project_id=pid_file,
            path="foundation/timeline.pdf",
            label="基地年表",
            kind="pdf",
            subfolder="资料/年表",
        ))
        _save_project(repo, "普通项目")

    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            win.search_box.setText("刘慈欣")
            win._search_timer.stop()
            win.refresh_projects()
            t.assert_eq(
                "GUI keyword searches custom field values",
                _titles(win),
                {"作者字段命中"},
            )

            win.search_box.setText("年表")
            win._search_timer.stop()
            win.refresh_projects()
            t.assert_eq(
                "GUI keyword searches file label and subfolder",
                _titles(win),
                {"文件说明命中"},
            )
            t.assert_true("author project exists", repo.get_project(pid_author) is not None)
        finally:
            _close_window(win)


def test_mcp_seen_context_targets(tmp: Path, t: T) -> None:
    repo = Repository(connect(tmp / "cabinet.db"))
    with closing_repos(repo):
        pid1 = _save_project(repo, "P1")
        pid2 = _save_project(repo, "P2")
        pid3 = _save_project(repo, "P3")
        for pid in (pid1, pid2, pid3):
            repo.mark_project_mcp_modified(pid)
    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            idx1 = win.proj_model.index_of_id(pid1)
            idx2 = win.proj_model.index_of_id(pid2)
            idx3 = win.proj_model.index_of_id(pid3)
            sel = win.proj_view.selectionModel()
            sel.select(idx1, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            sel.select(idx2, QItemSelectionModel.Select | QItemSelectionModel.Rows)
            sel.setCurrentIndex(idx2, QItemSelectionModel.NoUpdate)

            target = win._project_context_target_ids(win.proj_view, idx2)
            t.assert_eq("right click selected row keeps multi selection", set(target), {pid1, pid2})
            win._on_mark_mcp_seen(target)
            t.assert_true("p1 mcp cleared by multi target", repo.get_project(pid1).mcp_modified_at is None)
            t.assert_true("p2 mcp cleared by multi target", repo.get_project(pid2).mcp_modified_at is None)
            t.assert_true("p3 mcp still unread", repo.get_project(pid3).mcp_modified_at is not None)

            idx3 = win.proj_model.index_of_id(pid3)
            target = win._project_context_target_ids(win.proj_view, idx3)
            t.assert_eq("right click unselected row targets clicked project only", target, [pid3])
            win._on_mark_mcp_seen(target)
            t.assert_true("p3 mcp cleared by clicked target", repo.get_project(pid3).mcp_modified_at is None)
        finally:
            _close_window(win)


def test_table_context_targets_and_mcp_filter_refresh(tmp: Path, t: T) -> None:
    repo = Repository(connect(tmp / "cabinet.db"))
    with closing_repos(repo):
        pid1 = _save_project(repo, "列表 P1")
        pid2 = _save_project(repo, "列表 P2")
        pid3 = _save_project(repo, "列表 P3")
        for pid in (pid1, pid2, pid3):
            repo.mark_project_mcp_modified(pid)

    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            win._set_view_mode("list")
            idx1 = win.proj_model.index_of_id(pid1)
            idx2 = win.proj_model.index_of_id(pid2)
            idx3 = win.proj_model.index_of_id(pid3)
            sel = win.proj_table.selectionModel()
            sel.select(idx1, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            sel.select(idx2, QItemSelectionModel.Select | QItemSelectionModel.Rows)
            sel.setCurrentIndex(idx2, QItemSelectionModel.NoUpdate)

            target = win._project_context_target_ids(win.proj_table, idx2)
            t.assert_eq("table right click selected row keeps multi selection", set(target), {pid1, pid2})
            target = win._project_context_target_ids(win.proj_table, idx3)
            t.assert_eq("table right click unselected row targets clicked project only", target, [pid3])

            win._current_filter_kind = "mcp"
            win._current_filter_value = ""
            win.refresh_projects()
            t.assert_eq("mcp filter initially shows unread projects", _titles(win), {"列表 P1", "列表 P2", "列表 P3"})
            win._on_mark_mcp_seen([pid3])
            t.assert_eq("mcp filter refresh removes cleared project", _titles(win), {"列表 P1", "列表 P2"})
        finally:
            _close_window(win)


def _make_export_source(win: MainWindow, tmp: Path, *, title: str) -> int:
    fid_author = _field(win.repo, "作者", "text", f"author_{title}")
    src = tmp / "source.txt"
    src.write_text(f"{title} content", encoding="utf-8")
    project = Project(
        title=title,
        description_md=f"{title} 描述",
        tags=["导入导出/GUI", "状态/测试"],
        field_values={fid_author: "GUI 作者"},
    )
    pid = win.repo.save_project(project)
    win.repo.add_file(FileItem(
        project_id=pid,
        path=str(src.resolve()),
        is_relative=False,
        label="源文件说明",
        kind="text",
        subfolder="docs/manual",
        origin="user",
    ))
    win.refresh_projects()
    return pid


def _import_exported_package(
    win: MainWindow,
    package_path: Path,
) -> int:
    plans = scan_folders([package_path], win.repo)
    try:
        if len(plans) != 1:
            raise AssertionError(f"expected one import plan, got {len(plans)}")
        result = import_folder_as_project(
            win.repo,
            win.library,
            plans[0],
            ImportOptions(
                storage_mode="copy",
                title_source="project_json",
                field_policy="ignore",
                field_policy_apply_all=True,
            ),
        )
        win.refresh_projects()
        return int(result.project_id)
    finally:
        cleanup_extracted_zips(plans)


def _assert_round_trip_project(
    win: MainWindow,
    t: T,
    imported_pid: int,
    *,
    title: str,
    label: str,
) -> None:
    imported = win.repo.get_project(imported_pid)
    t.assert_true(f"{label}: imported project exists", imported is not None)
    if imported is None:
        return
    t.assert_eq(f"{label}: title restored", imported.title, title)
    t.assert_eq(f"{label}: tags restored", sorted(imported.tags), ["导入导出/GUI", "状态/测试"])
    t.assert_true(f"{label}: project visible after GUI refresh", title in _titles(win))

    files = win.repo.list_files(imported_pid)
    t.assert_eq(f"{label}: one file restored", len(files), 1)
    if not files:
        return
    restored = files[0]
    t.assert_true(f"{label}: file stored as relative copy", restored.is_relative)
    t.assert_eq(f"{label}: file label restored", restored.label, "源文件说明")
    t.assert_eq(f"{label}: file subfolder restored", restored.subfolder, "docs/manual")
    t.assert_true(
        f"{label}: copied file exists",
        win.library.resolve(restored.path, restored.is_relative).is_file(),
    )


def test_directory_package_export_import_round_trip(tmp: Path, t: T) -> None:
    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            title = "GUI 目录包 RoundTrip"
            pid = _make_export_source(win, tmp, title=title)
            export_root = tmp / "export_directory"
            export_root.mkdir()
            result = export_project(
                win.repo,
                win.library,
                win.repo.get_project(pid),
                ExportOptions(target_root=export_root, copy_link_files=True),
            )
            t.assert_true("directory export project.json exists", (result.project_dir / "project.json").is_file())
            imported_pid = _import_exported_package(win, result.project_dir)
            _assert_round_trip_project(win, t, imported_pid, title=title, label="directory package")
        finally:
            _close_window(win)


def test_zip_package_export_import_round_trip(tmp: Path, t: T) -> None:
    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            title = "GUI ZIP包 RoundTrip"
            pid = _make_export_source(win, tmp, title=title)
            export_root = tmp / "export_zip"
            export_root.mkdir()
            result = export_project(
                win.repo,
                win.library,
                win.repo.get_project(pid),
                ExportOptions(
                    target_root=export_root,
                    copy_link_files=True,
                    export_format="zip",
                ),
            )
            t.assert_true("zip export file exists", result.project_dir.is_file())
            t.assert_true("zip export has zip suffix", result.project_dir.suffix.lower() == ".zip")
            imported_pid = _import_exported_package(win, result.project_dir)
            _assert_round_trip_project(win, t, imported_pid, title=title, label="zip package")
        finally:
            _close_window(win)


def test_main_splitter_sizes_persist(tmp: Path, t: T) -> None:
    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            win._main_splitter.setSizes([260, 780, 360])
            _app().processEvents()
            win._on_main_splitter_moved(0, 0)
            saved = json.loads(repo.get_setting(MainWindow.MAIN_SPLITTER_SETTING_KEY, "[]"))
            t.assert_true("splitter setting stores three sizes", len(saved) == 3)
            t.assert_true("splitter setting stores positive sizes", min(saved) >= MainWindow.MAIN_SPLITTER_MIN_SIZE)
        finally:
            _close_window(win)

    win2, repo2 = _window(tmp)
    with closing_repos(repo2):
        try:
            loaded = win2._load_main_splitter_sizes()
            t.assert_eq("new window reads saved splitter sizes", loaded, saved)
        finally:
            _close_window(win2)


def test_right_panel_width_stable_across_selection(tmp: Path, t: T) -> None:
    repo = Repository(connect(tmp / "cabinet.db"))
    with closing_repos(repo):
        pid_short = repo.save_project(Project(title="短描述", description_md="短"))
        pid_long = repo.save_project(Project(
            title="长描述",
            description_md="很长的描述 " * 200,
        ))

    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            win._main_splitter.setSizes([240, 760, 380])
            _app().processEvents()
            before = win._main_splitter_sizes()
            win._show_project(repo.get_project(pid_short))
            _app().processEvents()
            after_short = win._main_splitter_sizes()
            win._show_project(repo.get_project(pid_long))
            _app().processEvents()
            after_long = win._main_splitter_sizes()
            t.assert_eq("right pane stable after short project", after_short[2], before[2])
            t.assert_eq("right pane stable after long project", after_long[2], before[2])
        finally:
            _close_window(win)


def main() -> bool:
    _app()
    t = T()
    print("=" * 60)
    print("main window GUI regression selftest")
    print("=" * 60)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        tests = [
            ("Search menu focus guard", test_search_menu_focus_guard),
            ("Search all toggle", test_search_all_toggle_ignores_sidebar_filter),
            ("GUI broad keyword search", test_gui_keyword_searches_fields_and_files),
            ("MCP seen context targets", test_mcp_seen_context_targets),
            ("Table targets and MCP filter", test_table_context_targets_and_mcp_filter_refresh),
            ("Directory package export/import", test_directory_package_export_import_round_trip),
            ("ZIP package export/import", test_zip_package_export_import_round_trip),
            ("Main splitter persistence", test_main_splitter_sizes_persist),
            ("Right panel width stable", test_right_panel_width_stable_across_selection),
        ]
        for name, fn in tests:
            print(f"\n-> {name}")
            fn(root / name.replace(" ", "_").lower(), t)
            print("  OK")
    print("\n" + "=" * 60)
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
