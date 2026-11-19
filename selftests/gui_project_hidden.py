"""隐藏项目 GUI 集成：显式临时数据库、合成记录，不接触用户文件。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu

from app.models import FileItem, Project
from app.search_history import SAVED_SEARCHES_SETTING_KEY
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _window
from selftests.gui_project_favorites_random import _filter, _ids, _search, _select


def _count(win, kind: str) -> int:
    item = win.tag_tree._find_item_by_kind_value(kind, "")
    assert item is not None, f"缺少侧栏节点 {kind}"
    return int(item.text(0).split()[-1])


def _menu(win, pid: int, text: str, *, change_to: int | None = None) -> list[str]:
    """从真实视图发出菜单事件，菜单显示后可改变选择以验证固定目标。"""
    view = win.proj_table if win.view_stack.currentIndex() == 1 else win.proj_view
    index = win.proj_model.index_of_id(pid)
    view.scrollTo(index)
    _app().processEvents()
    labels: list[str] = []

    def factory(parent):
        menu = QMenu(parent)

        def execute(*_args):
            labels.extend(a.text() for a in menu.actions())
            action = next(a for a in menu.actions() if text in a.text()
                          and (text != "隐藏项目" or "取消" not in a.text()))
            if change_to is not None:
                _select(win, [change_to])
            action.trigger()

        menu.exec = execute
        return menu

    with patch("app.ui.mw_projects.QMenu", side_effect=factory):
        view.customContextMenuRequested.emit(view.visualRect(index).center())
    return labels


def run(root: Path, t: T) -> None:
    win, repo = _window(root)
    first = repo.save_project(Project(title="alpha 可见", tags=["集合"]))
    second = repo.save_project(Project(title="alpha 隐藏", tags=["集合"]))
    third = repo.save_project(Project(title="beta 可见"))
    for pid in (first, second):
        repo.add_file(FileItem(project_id=pid, path=str(root / f"synthetic-{pid}.mp4"),
                               kind="video", is_relative=False, missing=True))
    repo.set_projects_favorite([first, second], True)
    win.refresh_projects()
    try:
        _select(win, [second])
        win.action_set_projects_hidden([second], True)
        t.assert_eq("隐藏后默认列表立即移除", _ids(win), {first, third})
        t.assert_true("当前详情不会停留在已隐藏项目", win._current_project_id != second)
        t.assert_eq("默认全部计数排除隐藏", _count(win, "all"), 2)
        t.assert_eq("收藏计数排除隐藏", _count(win, "favorite"), 1)
        t.assert_eq("隐藏入口显示实际隐藏数量", _count(win, "hidden"), 1)
        t.assert_eq("隐藏不取消收藏", repo.favorite_project_ids(), {first, second})
        t.assert_eq("可见标签计数", int(win.tag_tree._find_tag_item("集合").text(0).split()[-1]), 1)
        for expression in ("alpha", 'tag="集合"', "has:video", "favorite:true"):
            _search(win, expression)
            t.assert_eq(f"默认筛选排除隐藏 {expression}", _ids(win), {first})
            with patch("app.ui.mw_projects.random.choice", return_value=first) as choice:
                win.action_random_project()
                t.assert_eq(f"随便看看不抽隐藏 {expression}",
                            set(choice.call_args.args[0]) if choice.called else set(), {first})

        _search(win, "")
        _filter(win, "hidden")
        t.assert_eq("隐藏入口可找回项目", _ids(win), {second})
        t.assert_in("隐藏入口同步搜索栏", "hidden:", win.search_box.text())
        t.assert_eq("隐藏入口状态同步", win._current_filter_kind, "hidden")
        with patch("app.ui.mw_projects.random.choice", return_value=second) as choice:
            win.action_random_project()
            t.assert_eq("明确浏览隐藏时随便看看遵循当前范围", choice.call_args.args[0], [second])
        tag = win.tag_tree._find_tag_item("集合")
        win.tag_tree.scrollToItem(tag)
        QTest.mouseClick(win.tag_tree.viewport(), Qt.LeftButton, Qt.ShiftModifier,
                         win.tag_tree.visualItemRect(tag).center())
        t.assert_eq("Shift标签在隐藏范围内AND", _ids(win), {second})
        t.assert_in("Shift标签保留hidden条件", "hidden:", win.search_box.text())
        win.btn_search_all.click()
        t.assert_eq("全库清空恢复普通可见范围", _ids(win), {first, third})
        t.assert_eq("全库清空搜索文本", win.search_box.text(), "")
        _search(win, "hidden:all")
        t.assert_eq("显式all显示所有记录", _ids(win), {first, second, third})
        _search(win, "hidden:false")
        t.assert_eq("显式false显示可见记录", _ids(win), {first, third})

        repo.set_setting(SAVED_SEARCHES_SETTING_KEY, json.dumps([
            {"name": "隐藏视频", "query": "hidden:true AND has:video"},
        ]))
        win.refresh_projects()
        saved = win.tag_tree._find_item_by_kind_value("saved_search", "隐藏视频")
        win.tag_tree.setCurrentItem(saved)
        _app().processEvents()
        t.assert_eq("左侧保存搜索支持隐藏条件", _ids(win), {second})

        _search(win, "hidden:")
        win._update_search_completion(win.search_box.text())
        _app().processEvents()
        candidates = [win._completion.item(i).text() for i in range(win._completion.count())]
        for value in ("true", "false", "all"):
            t.assert_true(f"隐藏补全{value}", any(f"hidden:{value}" in item for item in candidates))
        win._search_timer.stop()
        win._completion.hide()

        for mode in ("grid", "list"):
            repo.set_projects_hidden([first, second, third], False)
            _search(win, "")
            win._set_view_mode(mode)
            _select(win, [first, second])
            _menu(win, first, "隐藏项目", change_to=third)
            t.assert_eq(f"{mode}菜单目标在选择变化后保持固定", repo.hidden_project_ids(), {first, second})
            t.assert_eq(f"{mode}多选隐藏后结果", _ids(win), {third})
            _filter(win, "hidden")
            _select(win, [first, second])
            _menu(win, first, "取消隐藏")
            t.assert_eq(f"{mode}批量取消隐藏", repo.hidden_project_ids(), set())
            t.assert_eq(f"{mode}取消隐藏后移出隐藏结果", _ids(win), set())
            t.assert_eq(f"{mode}空结果清除详情", win._current_project_id, None)
            t.assert_true(f"{mode}空结果禁用随便看看", not win.btn_random_project.isEnabled())
            _search(win, "")
            _select(win, [first, second])
            _menu(win, third, "隐藏项目")
            t.assert_eq(f"{mode}右键未选中项目只隐藏该项", repo.hidden_project_ids(), {third})

        repo.set_projects_hidden([first, second, third], False)
        repo.set_projects_hidden([second], True)
        _search(win, "hidden:all")
        _select(win, [first, second])
        labels = _menu(win, first, "取消隐藏")
        t.assert_true("混合选择提供隐藏动作", any("隐藏项目" in s and "取消" not in s for s in labels))
        t.assert_true("混合选择提供取消隐藏动作", any("取消隐藏" in s for s in labels))
        t.assert_eq("混合批量取消仅改变隐藏状态", repo.hidden_project_ids(), set())
        t.assert_eq("all视图取消隐藏仍显示全部", _ids(win), {first, second, third})
        t.assert_eq("仍在结果中保留多选", set(win._selected_project_ids()), {first, second})
        _select(win, [third])
        _select(win, [first])
        t.assert_eq("测试前当前详情已载入单个项目", win._current_project_id, first)
        with patch.object(win.preview, "show_file") as preview:
            win.action_set_projects_hidden([first], True)
            t.assert_eq("包含隐藏视图保留当前项目", win._current_project_id, first)
            t.assert_eq("仅改隐藏标记不重载仍可见预览", preview.call_count, 0)
        win.action_set_projects_hidden([first], False)

        _search(win, "")
        before = _ids(win)
        with patch.object(repo, "set_projects_hidden", side_effect=RuntimeError("合成写入失败")), \
                patch("app.ui.mw_projects.warn") as warning:
            win.action_set_projects_hidden([first], True)
            t.assert_eq("失败只汇总一次", warning.call_count, 1)
            t.assert_in("失败提供异常原因", "RuntimeError: 合成写入失败", str(warning.call_args))
            t.assert_eq("保存失败不从模型隐藏项目", _ids(win), before)
        win.action_set_projects_hidden([second], True)
        _search(win, "hidden:true")
        win.action_set_projects_favorite([second], False)
        t.assert_eq("隐藏视图取消收藏不改变隐藏状态", repo.hidden_project_ids(), {second})
        t.assert_eq("局部收藏刷新可见计数一致", _count(win, "favorite"), 1)
        _search(win, "")
    finally:
        _close_window(win)
        repo.conn.close()

    reopened, repo = _window(root)
    try:
        t.assert_eq("重启保留隐藏状态", repo.hidden_project_ids(), {second})
        _search(reopened, "")
        t.assert_eq("重启普通视图排除隐藏", _ids(reopened), {first, third})
        _filter(reopened, "hidden")
        t.assert_eq("重启可通过入口找回隐藏项", _ids(reopened), {second})
    finally:
        _close_window(reopened)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-project-hidden-") as directory:
        run(Path(directory), t)
    raise SystemExit(0 if t.report() else 1)
