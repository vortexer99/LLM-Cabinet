"""项目体积列、数值排序与真实后台线程回归；只使用显式临时库。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QItemSelectionModel, QPoint, QPersistentModelIndex, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu

from app.models import FileItem, Project
from app.project_sizes import ProjectSize, files_fingerprint, measure_project_sizes
from app.ui.project_card import EXTRA_COL_SIZE, ProjectModel
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _window


def run(root: Path, t: T) -> None:
    app = _app()
    win, repo = _window(root)
    try:
        ids: list[int] = []
        for title, size in (("合成小项目", 900), ("合成大项目", 2048), ("合成未统计", 30)):
            pid = repo.save_project(Project(title=title))
            ids.append(pid)
            path = root / f"file-{pid}.bin"
            path.write_bytes(b"x" * size)
            repo.add_file(FileItem(project_id=pid, path=str(path)))
        first, second, unknown = ids
        entries = repo.project_size_files([first, second])
        sizes = {pid: ProjectSize(size, 0, "2026-11-16T00:00:00+00:00", files_fingerprint(entries[pid]))
                 for pid, size in ((first, 900), (second, 2048))}
        repo.save_project_sizes(sizes)
        win.refresh_projects()
        win._set_view_mode("list")
        app.processEvents()
        model = win.proj_model
        column = next(col for col in range(model.columnCount()) if model.column_key(col) == EXTRA_COL_SIZE)
        t.assert_eq("体积列中文标题", model.headerData(column, Qt.Horizontal), "项目体积")
        t.assert_eq("未统计不显示0", model.index(model.index_of_id(unknown).row(), column).data(), "未统计")
        t.assert_eq("未知原始字节为None", model.index_of_id(unknown).data(model.RoleSizeBytes), None)
        t.assert_eq("显示单位不改变原始字节", model.index_of_id(second).data(model.RoleSizeBytes), 2048)
        selected = win.proj_view.selectionModel()
        selected.setCurrentIndex(model.index_of_id(first), QItemSelectionModel.ClearAndSelect)
        selected.select(model.index_of_id(second), QItemSelectionModel.Select)
        before_ids = set(win._selected_project_ids())
        current_id = selected.currentIndex().data(model.RoleId)
        persistent = QPersistentModelIndex(model.index_of_id(second))

        def ordered() -> list[int]:
            return [model.index(row, 0).data(model.RoleId) for row in range(model.rowCount())]

        def click_size_header() -> None:
            win.proj_table.scrollTo(model.index(0, column))
            app.processEvents()
            header = win.proj_table.horizontalHeader()
            point = QPoint(header.sectionViewportPosition(column) + header.sectionSize(column) // 2,
                           header.height() // 2)
            QTest.mouseClick(header.viewport(), Qt.LeftButton, pos=point)

        with patch.object(win, "_show_project", wraps=win._show_project) as preview:
            click_size_header()
            t.assert_eq("升序按真实字节且未知最后", ordered(), [first, second, unknown])
            t.assert_eq("升序保持多选", set(win._selected_project_ids()), before_ids)
            t.assert_eq("升序保持当前项目", selected.currentIndex().data(model.RoleId), current_id)
            t.assert_eq("持久索引保持项目", persistent.data(model.RoleId), second)
            click_size_header()
            t.assert_eq("降序未知仍最后", ordered(), [second, first, unknown])
            t.assert_eq("降序保持多选", set(win._selected_project_ids()), before_ids)
            t.assert_eq("排序不重载预览", preview.call_count, 0)
            previous = ordered()
            win._on_project_header_clicked(-1)
            t.assert_eq("无效列不影响体积排序", ordered(), previous)

        win.refresh_projects(filter_only=True)
        t.assert_eq("刷新后保持体积排序", ordered(), [second, first, unknown])
        model.update_project_sizes({first: replace(sizes[first], missing=2, stale=True)})
        text = model.index(model.index_of_id(first).row(), column).data()
        tooltip = model.index(model.index_of_id(first).row(), column).data(Qt.ToolTipRole)
        t.assert_true("缺失显示已知大小", "已知" in text and "缺失 2" in text)
        t.assert_true("过期缓存显示待更新", "待更新" in text)
        t.assert_true("tooltip解释手动缓存", "磁盘" in tooltip and "统计时间" in tooltip)
        project = repo.get_project(second)
        model.update_project(project, None, 2)
        t.assert_true("局部文件计数改变标记过期", model._project_sizes[second].stale)

        # 通过真实 run_with_progress 和 QThread 执行，SQLite 写入回调必须回到主线程。
        main_thread = threading.get_ident()
        worker_threads: list[int] = []
        save_threads: list[int] = []
        ticks: list[bool] = []
        saved = threading.Event()
        original_save = repo.save_project_sizes

        def measured(snapshots, progress, is_cancelled):
            worker_threads.append(threading.get_ident())
            time.sleep(0.12)
            return measure_project_sizes(snapshots, progress, is_cancelled)

        def save(results):
            save_threads.append(threading.get_ident())
            original_save(results)
            saved.set()

        selected.setCurrentIndex(model.index_of_id(first), QItemSelectionModel.ClearAndSelect)
        app.processEvents()
        file_node = win.tbl_files.topLevelItem(0)
        timer = QTimer(win)
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start()
        with patch("app.ui.mw_projects.measure_project_sizes", side_effect=measured), \
                patch.object(repo, "save_project_sizes", side_effect=save), \
                patch.object(win, "_show_project", wraps=win._show_project) as preview, \
                patch("app.ui.mw_projects.warn") as warning, patch("app.ui.mw_projects.info"):
            win.action_update_project_sizes([first, second])
            deadline = time.monotonic() + 5
            while not saved.is_set() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            app.processEvents()
            t.assert_true("真实后台统计完成", saved.is_set())
            t.assert_true("stat在独立worker线程", bool(worker_threads) and all(i != main_thread for i in worker_threads))
            t.assert_eq("SQLite保存仅在主线程", save_threads, [main_thread])
            t.assert_true("统计时GUI事件循环继续响应", len(ticks) >= 3)
            t.assert_eq("更新体积不重载预览", preview.call_count, 0)
            t.assert_true("更新体积保留文件节点", win.tbl_files.topLevelItem(0) is file_node)
            t.assert_eq("体积更新没有警告", warning.call_count, 0)
        timer.stop()
        t.assert_eq("真实后台缓存正确", repo.project_sizes(ids)[first].bytes, 900)
        t.assert_true("重新统计清除失效状态", not model._project_sizes[first].stale)

        # 项目文件登记新增后只同步缓存列即可出现过期提示。
        repo.add_file(FileItem(project_id=first, path=str(root / "new-missing.bin")))
        win._update_cached_project_sizes([first])
        t.assert_true("登记变化后的局部缓存更新标过期", model._project_sizes[first].stale)

        cancelled: list[bool] = []
        cache_before = repo.project_sizes([first])[first]
        from app.ui.workers import FileOpWorker

        with patch("app.ui.mw_projects.measure_project_sizes", side_effect=measured), \
                patch.object(repo, "save_project_sizes", wraps=repo.save_project_sizes) as writes:
            win.action_update_project_sizes([first])
            worker = win.findChildren(FileOpWorker)[-1]
            worker.cancelled.connect(lambda: cancelled.append(True))
            worker.request_cancel()
            deadline = time.monotonic() + 5
            while not cancelled and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            app.processEvents()
            t.assert_true("真实后台取消完成", bool(cancelled))
            t.assert_eq("取消不保存半批缓存", writes.call_count, 0)
            t.assert_eq("取消保持原缓存", repo.project_sizes([first])[first], cache_before)

        # 右键入口固定本次菜单的多选项目，不因其他当前项变化而改目标。
        selected.setCurrentIndex(model.index_of_id(first), QItemSelectionModel.ClearAndSelect)
        selected.select(model.index_of_id(second), QItemSelectionModel.Select)
        win.proj_table.scrollTo(model.index_of_id(first))
        app.processEvents()

        def menu_factory(parent):
            menu = QMenu(parent)
            def execute(*_args):
                next(action for action in menu.actions() if action.text() == "更新项目体积").trigger()
            menu.exec = execute
            return menu

        with patch("app.ui.mw_projects.QMenu", side_effect=menu_factory), \
                patch.object(win, "action_update_project_sizes") as update:
            position = win.proj_table.visualRect(model.index_of_id(first)).center()
            win.proj_table.customContextMenuRequested.emit(position)
            t.assert_eq("项目右键体积入口覆盖多选", set(update.call_args.args[0]), {first, second})
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-size-gui-") as directory:
        run(Path(directory), t)
    sys.exit(0 if t.report() else 1)
