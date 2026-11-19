"""封面快照默认隐藏 GUI 回归；只使用显式临时库及自行构造的文件。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Project
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _find_file_tree_item
from selftests.gui_preview_file_navigation import (
    _case, _file, _next, _previous, _select, _show,
)


def test_default_and_navigation(tmp: Path, t: T, mode: str) -> None:
    """树形和平铺都在选择首文件、导航之前排除生成封面。"""
    with _case(tmp) as (win, repo, calls):
        pid = repo.save_project(Project(title=f"{mode} 默认封面可见性"))
        snapshot = _file(repo, tmp, pid, "00-snapshot.png", subfolder="00-generated", origin="generated")
        image = _file(repo, tmp, pid, "01-original.png")
        video = _file(repo, tmp, pid, "02-video.mp4")
        project = repo.get_project(pid)
        assert project is not None
        project.cover_file_id = snapshot
        repo.save_project(project)
        repo.set_project_setting(pid, "files_view_mode", mode)
        repo.set_setting("auto_select_first_file", "1")
        _show(win, repo, pid)

        t.assert_eq(f"{mode} 默认首文件跳过排序靠前的生成封面", win._current_file_row_id(), image)
        t.assert_true(f"{mode} 默认隐藏生成封面行", _find_file_tree_item(win, snapshot) is None)
        t.assert_eq(f"{mode} 导航范围只含原始文件", len(win._preview_file_items()), 2)
        t.assert_true(f"{mode} 来源按钮默认仅用户文件", win._btn_origin_filter.isChecked())
        t.assert_true(f"{mode} 有生成文件时来源按钮可见", not win._btn_origin_filter.isHidden())
        t.assert_in(f"{mode} 数量提示说明隐藏项", "已隐藏 1 个生成文件", win.lbl_files_hint.text())
        t.assert_in(f"{mode} 来源提示说明如何展开", "点击显示全部文件", win._btn_origin_filter.toolTip())
        t.assert_eq(f"{mode} 默认状态不强行写项目偏好", repo.get_project_setting(pid, "files_view_origin_filter", "unset"), "unset")
        t.assert_eq(f"{mode} 导航下一项跳过生成文件", _next(win), video)
        t.assert_eq(f"{mode} 反向导航只到原始图片", _previous(win), image)

        # 原始图片被设为封面后仍按 user 来源显示，不能按 cover_file_id 过滤。
        project.cover_file_id = image
        repo.save_project(project)
        win._show_project(project, preserve_preview=True)
        t.assert_true(f"{mode} 原始图片作为封面仍显示", _find_file_tree_item(win, image) is not None)
        _select(win, video)
        calls.clear()
        win._btn_origin_filter.click()
        _app().processEvents()
        t.assert_eq(f"{mode} 展开生成文件保存显式 all", repo.get_project_setting(pid, "files_view_origin_filter", ""), "all")
        t.assert_true(f"{mode} 展开后可见生成封面", _find_file_tree_item(win, snapshot) is not None)
        t.assert_eq(f"{mode} 切换来源保留当前视频选择", win._current_file_row_id(), video)
        t.assert_eq(f"{mode} 展开来源不重新载入视频", calls, [])
        win._btn_origin_filter.click()
        _app().processEvents()
        t.assert_eq(f"{mode} 收起来源保存显式 user", repo.get_project_setting(pid, "files_view_origin_filter", ""), "user")
        t.assert_eq(f"{mode} 收起来源不重新载入视频", calls, [])

        win._btn_origin_filter.click()
        _select(win, snapshot)
        calls.clear()
        win._btn_origin_filter.click()
        _app().processEvents()
        t.assert_eq(f"{mode} 隐藏当前生成项后清空选择", win._current_file_row_id(), None)
        t.assert_eq(f"{mode} 隐藏当前生成项后清理预览一次", calls, [(None, False)])


def test_explicit_preferences(tmp: Path, t: T, mode: str) -> None:
    """已保存的全部/用户来源偏好在打开项目时保持有效。"""
    with _case(tmp) as (win, repo, _calls):
        for origin_filter in ("all", "user"):
            pid = repo.save_project(Project(title=f"{mode}-{origin_filter}"))
            snapshot = _file(repo, tmp, pid, "00-snapshot.png", origin="generated")
            image = _file(repo, tmp, pid, "01-original.png")
            repo.set_project_setting(pid, "files_view_mode", mode)
            repo.set_project_setting(pid, "files_view_origin_filter", origin_filter)
            repo.set_setting("auto_select_first_file", "1")
            _show(win, repo, pid)
            t.assert_eq(f"{mode} 尊重显式 {origin_filter} 来源", _find_file_tree_item(win, snapshot) is not None, origin_filter == "all")
            t.assert_eq(f"{mode} 显式 {origin_filter} 首文件来自当前范围", win._current_file_row_id(), snapshot if origin_filter == "all" else image)
            t.assert_eq(f"{mode} 显式 {origin_filter} 按钮状态", win._btn_origin_filter.isChecked(), origin_filter == "user")

        pid = repo.save_project(Project(title="只有生成封面"))
        _file(repo, tmp, pid, "snapshot.png", origin="generated")
        repo.set_project_setting(pid, "files_view_mode", mode)
        _show(win, repo, pid)
        t.assert_eq(f"{mode} 只有生成文件默认无首项", win._current_file_row_id(), None)
        t.assert_eq(f"{mode} 只有生成文件默认无导航项", win._preview_file_items(), [])
        t.assert_in(f"{mode} 空范围仍提示隐藏文件", "显示 0 / 1 个文件", win.lbl_files_hint.text())
        win._btn_origin_filter.click()
        t.assert_eq(f"{mode} 空范围可展开生成文件", len(win._preview_file_items()), 1)


def main() -> bool:
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-cover-visibility-", ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        for mode in ("tree", "flat"):
            test_default_and_navigation(root / mode / "default", t, mode)
            test_explicit_preferences(root / mode / "explicit", t, mode)
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
