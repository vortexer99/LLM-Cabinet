"""预览区上一个/下一个文件 GUI 回归自测；仅访问临时库和自行创建的文件。"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QItemSelectionModel, Qt

from app.models import FileItem, Project
from app.repository import Repository
from app.ui.main_window import MainWindow
from selftests._common import T, closing_repos
from selftests.gui_main_window_regressions import (
    _app, _close_window, _find_file_tree_item, _window,
)


@contextmanager
def _case(tmp: Path) -> Iterator[tuple[MainWindow, Repository, list]]:
    """显式传入临时库路径，截获预览调用，禁止实际解码媒体。"""
    win, repo = _window(tmp)
    calls: list[tuple[str | None, bool]] = []

    def show_file(path: str | None, *, autoplay: bool = False) -> None:
        calls.append((path, autoplay))

    with closing_repos(repo), patch.object(win.preview, "show_file", side_effect=show_file):
        try:
            yield win, repo, calls
        finally:
            _close_window(win)


def _file(
    repo: Repository, tmp: Path, pid: int, name: str, *,
    subfolder: str = "", origin: str = "user", missing: bool = False,
) -> int:
    """所有测试媒体都是临时目录中的占位文件，不使用用户文件。"""
    path = tmp / "source" / str(pid) / subfolder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not missing:
        path.write_text("仅用于导航测试，不解码", encoding="utf-8")
    return repo.add_file(FileItem(
        project_id=pid, path=str(path.resolve()), is_relative=False,
        kind="video" if path.suffix == ".mp4" else "doc",
        subfolder=subfolder, origin=origin, missing=missing,
    ))


def _show(win: MainWindow, repo: Repository, pid: int | None) -> None:
    win._current_project_id = pid
    win._show_project(repo.get_project(pid) if pid is not None else None)
    _app().processEvents()


def _select(win: MainWindow, fid: int) -> None:
    item = _find_file_tree_item(win, fid)
    assert item is not None, f"测试文件行不存在：{fid}"
    win.tbl_files.setCurrentItem(item, 0, QItemSelectionModel.ClearAndSelect)
    _app().processEvents()


def _buttons(win: MainWindow) -> tuple[bool, bool]:
    return win.preview._btn_previous.isEnabled(), win.preview._btn_next.isEnabled()


def _next(win: MainWindow) -> int | None:
    win.preview._btn_next.click()
    _app().processEvents()
    return win._current_file_row_id()


def _previous(win: MainWindow) -> int | None:
    win.preview._btn_previous.click()
    _app().processEvents()
    return win._current_file_row_id()


def test_tree_navigation(tmp: Path, t: T) -> None:
    with _case(tmp) as (win, repo, _calls):
        pid = repo.save_project(Project(title="树形导航"))
        # 故意反向入库，验证真实树的自然排序，而非数据库顺序。
        root10 = _file(repo, tmp, pid, "root10.txt")
        a10 = _file(repo, tmp, pid, "file10.txt", subfolder="dir2")
        b1 = _file(repo, tmp, pid, "file1.txt", subfolder="dir10/nested")
        root2 = _file(repo, tmp, pid, "root2.txt")
        a2 = _file(repo, tmp, pid, "file2.txt", subfolder="dir2")
        win._current_project_id = pid
        win._set_tree_sort_state("name", False)
        _show(win, repo, pid)
        t.assert_eq("未选择时仅允许前往首文件", _buttons(win), (False, True))
        t.assert_eq("首文件遵循目录自然排序", _next(win), a2)
        t.assert_eq("首文件禁用上一个", _buttons(win), (False, True))
        t.assert_eq("同目录文件自然排序 file2 在 file10 前", _next(win), a10)

        nested = _find_file_tree_item(win, b1)
        assert nested is not None and nested.parent() is not None
        nested.parent().setExpanded(False)
        nested.parent().parent().setExpanded(False)
        t.assert_eq("跨目录包含折叠子树文件", _next(win), b1)
        t.assert_true("导航展开目标所有父目录", nested.parent().isExpanded()
                      and nested.parent().parent().isExpanded())
        t.assert_eq("目录后继续访问根文件", _next(win), root2)
        t.assert_eq("根文件同样遵循自然排序", _next(win), root10)
        t.assert_eq("末文件禁用下一个", _buttons(win), (True, False))
        t.assert_eq("末尾不循环", _next(win), root10)
        t.assert_eq("反向跨目录", [_previous(win), _previous(win)], [root2, b1])

        # 多选导航后只保留目标一个文件，防止后续批量操作误伤原选中项。
        _select(win, a2)
        root_item = _find_file_tree_item(win, root2)
        assert root_item is not None
        root_item.setSelected(True)
        t.assert_true("测试前确实存在多选", len(win.tbl_files.selectedItems()) > 1)
        t.assert_eq("多选按当前文件前往下一项", _next(win), a10)
        t.assert_eq("导航将多选收束到目标", win._selected_file_ids(), [a10])

        # 隐藏行不属于导航范围，隐藏目录连同其后代一起跳过。
        nested.parent().parent().setHidden(True)
        root_item.setHidden(True)
        win._update_preview_file_navigation()
        t.assert_eq("跳过隐藏文件及隐藏目录后代", _next(win), root10)


def test_flat_sort_and_selection(tmp: Path, t: T) -> None:
    with _case(tmp) as (win, repo, _calls):
        pid = repo.save_project(Project(title="平铺降序"))
        f1 = _file(repo, tmp, pid, "file1.txt", subfolder="z")
        f2 = _file(repo, tmp, pid, "file2.txt", subfolder="a")
        f10 = _file(repo, tmp, pid, "file10.txt")
        repo.set_project_setting(pid, "files_view_mode", "flat")
        _show(win, repo, pid)
        _select(win, f1)
        t.assert_eq("平铺升序首项边界", _buttons(win), (False, True))
        win.tbl_files.sortByColumn(0, Qt.DescendingOrder)
        _app().processEvents()
        t.assert_eq("排序后立即更新边界按钮", _buttons(win), (True, False))
        t.assert_eq("平铺逆序遵循当前表格排序", [_previous(win), _previous(win)], [f2, f10])
        t.assert_eq("平铺降序首项边界", _buttons(win), (False, True))
        t.assert_eq("首项不循环", _previous(win), f10)

        win.tbl_files.clearSelection()
        win.tbl_files.setCurrentItem(None)
        _app().processEvents()
        t.assert_eq("清空选择后上一个禁用", _buttons(win), (False, True))
        t.assert_eq("清空后下一个从当前排序首项开始", _next(win), f10)


def test_origin_and_projects(tmp: Path, t: T) -> None:
    with _case(tmp) as (win, repo, _calls):
        pid = repo.save_project(Project(title="来源过滤"))
        repo.set_project_setting(pid, "files_view_origin_filter", "all")
        f1 = _file(repo, tmp, pid, "file1.txt")
        generated = _file(repo, tmp, pid, "file2.txt", origin="generated")
        f3 = _file(repo, tmp, pid, "file3.txt")
        other = repo.save_project(Project(title="另一个项目"))
        solo = _file(repo, tmp, other, "solo.txt")
        empty = repo.save_project(Project(title="空项目"))
        _show(win, repo, pid)
        _select(win, f1)
        t.assert_eq("全部来源包含衍生文件", _next(win), generated)
        win._btn_origin_filter.click()
        _app().processEvents()
        t.assert_eq("过滤后重新同步未选中边界", _buttons(win), (False, True))
        t.assert_true("过滤后衍生行不在列表", _find_file_tree_item(win, generated) is None)
        t.assert_eq("过滤内导航不越界到衍生文件", [_next(win), _next(win)], [f1, f3])
        t.assert_eq("过滤范围尾部禁用下一个", _buttons(win), (True, False))
        _show(win, repo, other)
        t.assert_eq("切项目清除旧边界状态", _buttons(win), (False, True))
        t.assert_eq("切项目后导航只使用新项目文件", _next(win), solo)
        t.assert_eq("单文件项目两方向均禁用", _buttons(win), (False, False))
        _show(win, repo, empty)
        t.assert_eq("空项目两方向均禁用", _buttons(win), (False, False))
        win._navigate_preview_file(1)
        t.assert_eq("空项目直接调用导航安全", win._current_file_row_id(), None)
        _show(win, repo, None)
        t.assert_eq("未选择项目两方向均禁用", _buttons(win), (False, False))


def test_missing_and_autoplay(tmp: Path, t: T) -> None:
    with _case(tmp) as (win, repo, calls):
        pid = repo.save_project(Project(title="缺失与视频"))
        missing = _file(repo, tmp, pid, "file1.txt", missing=True)
        video = _file(repo, tmp, pid, "file2.mp4")
        repo.set_setting("video_autoplay", "1")
        _show(win, repo, pid)
        t.assert_eq("缺失文件仍可导航选中", _next(win), missing)
        t.assert_eq("缺失文件保留路径以显示离线详情", calls[-1], (repo.get_file(missing).path, True))
        t.assert_eq("缺失文件后可继续导航", _next(win), video)
        expected = str((tmp / "source" / str(pid) / "file2.mp4").resolve())
        t.assert_eq("导航沿用视频自动播放设置", calls[-1], (expected, True))
        repo.set_setting("video_autoplay", "0")
        _previous(win)
        _next(win)
        t.assert_eq("导航实时读取关闭自动播放设置", calls[-1], (expected, False))


def main() -> bool:
    _app()
    t = T()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        for name, test in [
            ("tree", test_tree_navigation),
            ("flat", test_flat_sort_and_selection),
            ("origin_projects", test_origin_and_projects),
            ("missing_autoplay", test_missing_and_autoplay),
        ]:
            print(f"-> {name}")
            test(root / name, t)
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
