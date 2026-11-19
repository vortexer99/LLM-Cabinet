"""项目首文件自动选择 GUI 回归；只使用显式临时库和自行构造的文件。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QItemSelectionModel, QModelIndex, Qt

from app.db import connect
from app.models import Project
from app.repository import Repository
from selftests._common import T, closing_repos
from selftests.gui_main_window_regressions import _app, _close_window, _window
from selftests.gui_preview_file_navigation import _case, _file, _select, _show


def test_default_and_switch(tmp: Path, t: T) -> None:
    with _case(tmp) as (win, repo, calls):
        first = repo.save_project(Project(title="第一个项目"))
        second = repo.save_project(Project(title="第二个项目"))
        f1 = _file(repo, tmp, first, "file1.txt")
        f2 = _file(repo, tmp, second, "file2.mp4")
        _show(win, repo, first)
        t.assert_eq("默认关闭不选文件", win._selected_file_ids(), [])
        repo.set_setting("auto_select_first_file", "1")
        _show(win, repo, first)
        t.assert_eq("开启设置不抢占已显示项目的选择", win._selected_file_ids(), [])
        _show(win, repo, second)
        t.assert_eq("切换项目自动选择首文件", win._current_file_row_id(), f2)
        t.assert_eq("自动选视频仍默认不播放", calls[-1][1], False)
        repo.set_setting("video_autoplay", "1")
        _show(win, repo, first)
        t.assert_eq("切回项目自动选择首文件", win._current_file_row_id(), f1)
        _show(win, repo, second)
        t.assert_eq("自动选择沿用开启的视频自动播放", calls[-1][1], True)
        repo.set_setting("auto_select_first_file", "0")
        _show(win, repo, first)
        t.assert_eq("关闭设置后下次切换立即生效", win._selected_file_ids(), [])


def test_sort_filter_and_empty(tmp: Path, t: T) -> None:
    with _case(tmp) as (win, repo, _calls):
        repo.set_setting("auto_select_first_file", "1")
        tree = repo.save_project(Project(title="树形排序"))
        _file(repo, tmp, tree, "root1.txt")
        f10 = _file(repo, tmp, tree, "file10.txt", subfolder="dir2/nested")
        f2 = _file(repo, tmp, tree, "file2.txt", subfolder="dir2/nested")
        _file(repo, tmp, tree, "file1.txt", subfolder="dir10")
        win._save_explicit_subfolders(tree, {"dir1", "dir2/nested", "dir10"})
        _show(win, repo, tree)
        t.assert_eq("树形自然排序跳过空目录并遍历嵌套目录", win._current_file_row_id(), f2)
        selected = win.tbl_files.currentItem()
        t.assert_true("目标父目录均已展开", selected.parent().isExpanded() and selected.parent().parent().isExpanded())
        empty = repo.save_project(Project(title="仅空目录"))
        win._save_explicit_subfolders(empty, {"empty/nested"})
        _show(win, repo, empty)
        t.assert_eq("仅目录项目不自动选中目录", win._selected_file_ids(), [])
        t.assert_eq("仅目录项目保持空预览选择", win._current_file_row_id(), None)
        _show(win, repo, tree)
        win._set_tree_sort_state("name", True)
        _show(win, repo, empty)
        _show(win, repo, tree)
        t.assert_eq("树形目录仍优先自然排序且目录内文件降序", win._current_file_row_id(), f10)

        flat = repo.save_project(Project(title="平铺来源过滤"))
        _file(repo, tmp, flat, "file20.txt", origin="generated")
        user10 = _file(repo, tmp, flat, "file10.txt", subfolder="a")
        user2 = _file(repo, tmp, flat, "file2.txt", subfolder="z")
        repo.set_project_setting(flat, "files_view_mode", "flat")
        repo.set_project_setting(flat, "files_flat_sort_col", "0")
        repo.set_project_setting(flat, "files_flat_sort_order", "1")
        repo.set_project_setting(flat, "files_view_origin_filter", "user")
        _show(win, repo, flat)
        t.assert_eq("平铺降序先过滤衍生文件再自动选择", win._current_file_row_id(), user10)
        t.assert_eq("平铺过滤后仅两个真实文件", len(win._preview_file_items()), 2)
        _show(win, repo, empty)
        repo.set_project_setting(flat, "files_flat_sort_order", "0")
        _show(win, repo, flat)
        t.assert_eq("平铺升序遵循数字自然排序", win._current_file_row_id(), user2)
        only_generated = repo.save_project(Project(title="筛选后为空"))
        _file(repo, tmp, only_generated, "only.txt", origin="generated")
        repo.set_project_setting(only_generated, "files_view_origin_filter", "user")
        _show(win, repo, only_generated)
        t.assert_eq("筛选后空项目不选文件", win._current_file_row_id(), None)
        _show(win, repo, None)
        t.assert_eq("取消选择项目保持空选择", win._selected_file_ids(), [])


def test_refresh_and_multi_selection(tmp: Path, t: T) -> None:
    with _case(tmp) as (win, repo, calls):
        repo.set_setting("auto_select_first_file", "1")
        pid = repo.save_project(Project(title="刷新保留选择"))
        first = _file(repo, tmp, pid, "file1.mp4")
        second = _file(repo, tmp, pid, "file2.mp4")
        other = repo.save_project(Project(title="其他项目"))
        _file(repo, tmp, other, "other.txt")
        win.refresh_projects()
        index = win.proj_model.index_of_id(pid)
        win.proj_view.setCurrentIndex(index)
        win._on_project_selected(index, QModelIndex())
        _select(win, second)
        calls.clear()
        for _ in range(2):
            win.refresh_projects()
            win._on_project_selected(win.proj_model.index_of_id(pid), QModelIndex())
        t.assert_eq("同项目重复刷新保留用户所选第二个视频", win._selected_file_ids(), [second])
        t.assert_eq("同项目重复刷新不清空或重新通知播放器", calls, [])

        win.tbl_files.clearSelection()
        win.tbl_files.setCurrentItem(None)
        calls.clear()
        win.refresh_projects()
        t.assert_eq("同项目主动取消文件选择后刷新不抢选", win._selected_file_ids(), [])
        t.assert_eq("无选择同项目刷新仍不触发预览", calls, [])
        _select(win, second)
        repo.delete_file(second)
        calls.clear()
        win.refresh_projects()
        t.assert_eq("删除当前文件后刷新不抢选首文件", win._selected_file_ids(), [])
        t.assert_eq("删除当前文件后清除过期预览", calls[-1][0], None)

        selection = win.proj_view.selectionModel()
        selection.select(win.proj_model.index_of_id(other), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        calls.clear()
        win._on_project_selected(win.proj_model.index_of_id(pid), QModelIndex())
        t.assert_eq("项目多选不自动选择任何文件", win._selected_file_ids(), [])
        t.assert_eq("多选仅清空预览", calls, [(None, False)])
        selection.setCurrentIndex(win.proj_model.index_of_id(pid), QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        win._on_project_selected(win.proj_model.index_of_id(pid), QModelIndex())
        t.assert_eq("多选恢复单选重新自动选择首文件", win._current_file_row_id(), first)


def test_startup_and_libraries(tmp: Path, t: T) -> None:
    for label, enabled in (("甲库", True), ("乙库", False)):
        library_root = tmp / label
        library_root.mkdir(parents=True)
        repo = Repository(connect(library_root / "cabinet.db"))
        with closing_repos(repo):
            repo.set_setting("auto_select_first_file", "1" if enabled else "0")
            pid = repo.save_project(Project(title=label))
            fid = _file(repo, library_root, pid, "first.txt")
        win, repo = _window(library_root)
        with closing_repos(repo):
            try:
                t.assert_eq(f"{label}启动自动选择按独立设置", win._current_file_row_id(), fid if enabled else None)
            finally:
                _close_window(win)


def main() -> bool:
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-auto-select-") as directory:
        root = Path(directory)
        for name, test in (
            ("default", test_default_and_switch),
            ("sort", test_sort_filter_and_empty),
            ("refresh", test_refresh_and_multi_selection),
            ("libraries", test_startup_and_libraries),
        ):
            test(root / name, t)
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
