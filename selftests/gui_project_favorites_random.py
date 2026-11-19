"""项目收藏与随便看看：临时库、合成记录、真实 Qt 控件交互。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu

from app.models import FileItem, Project
from app.ui.project_card import ProjectCardDelegate, ProjectModel
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _window


def _ids(win) -> set[int]:
    return {win.proj_model.index(row, 0).data(ProjectModel.RoleId)
            for row in range(win.proj_model.rowCount())}


def _filter(win, kind: str, value: str = "") -> None:
    if kind == "tag":
        item = win.tag_tree._find_tag_item(value)
    else:
        item = next(win.tag_tree.topLevelItem(i) for i in range(win.tag_tree.topLevelItemCount())
                    if win.tag_tree.topLevelItem(i).data(0, win.tag_tree.KIND_ROLE) == kind)
    win.tag_tree.setCurrentItem(item)
    _app().processEvents()


def _search(win, text: str) -> None:
    win.search_box.setText(text)
    win._search_timer.stop()
    win._completion.hide()
    win.refresh_projects()
    _app().processEvents()


def _select(win, ids: list[int]) -> None:
    selection = win.proj_view.selectionModel()
    selection.clearSelection()
    for pid in ids:
        selection.select(win.proj_model.index_of_id(pid), QItemSelectionModel.Select | QItemSelectionModel.Rows)
    if ids:
        selection.setCurrentIndex(win.proj_model.index_of_id(ids[0]), QItemSelectionModel.NoUpdate)
    _app().processEvents()


def run(root: Path, t: T) -> None:
    win, repo = _window(root)
    first = repo.save_project(Project(title="alpha 视频", tags=["集合"]))
    second = repo.save_project(Project(title="alpha 压缩", tags=["集合"]))
    third = repo.save_project(Project(title="beta 视频"))
    for pid, kind in ((first, "video"), (second, "archive"), (third, "video")):
        fid = repo.add_file(FileItem(project_id=pid, path=str(root / f"missing-{pid}"), kind=kind))
        repo.set_file_missing(fid, True)
    win.refresh_projects()
    _app().processEvents()
    try:
        t.assert_eq("默认无收藏", repo.favorite_project_ids(), set())
        t.assert_true("有项目时可随便看看", win.btn_random_project.isEnabled())
        t.assert_eq("自动选中网格卡片可识别", win._selected_project_ids(), [win._current_project_id])
        t.assert_true("自动选中时收藏按钮可用", win.btn_favorite.isEnabled())
        _select(win, [first])
        index = win.proj_model.index_of_id(second)
        win.proj_view.scrollTo(index)
        _app().processEvents()
        point = ProjectCardDelegate.favorite_rect(win.proj_view.visualRect(index)).center()
        t.assert_true("卡片星标可见", win.proj_view.viewport().rect().contains(point))
        with patch.object(win.preview, "show_file") as preview, patch.object(win, "action_edit_project") as edit:
            QTest.mouseClick(win.proj_view.viewport(), Qt.LeftButton, pos=point)
            _app().processEvents()
            t.assert_eq("单击星标收藏点中项目", repo.favorite_project_ids(), {second})
            t.assert_eq("星标不改变当前项目", win._current_project_id, first)
            t.assert_eq("星标不重载预览", preview.call_count, 0)
            QTest.mouseDClick(win.proj_view.viewport(), Qt.LeftButton, pos=point)
            _app().processEvents()
            t.assert_eq("双击星标不打开编辑", edit.call_count, 0)
        win.action_set_projects_favorite([second], True)
        t.assert_true("模型包含收藏角色", win.proj_model.index_of_id(second).data(ProjectModel.RoleFavorite))
        title_col = next(i for i in range(win.proj_model.columnCount()) if win.proj_model.column_key(i) == "title")
        t.assert_true("列表标题显示星标", win.proj_model.index(index.row(), title_col).data().startswith("★ "))

        for mode in ("grid", "list"):
            win._set_view_mode(mode)
            _select(win, [first])
            with patch.object(win.preview, "show_file") as preview:
                QTest.mouseClick(win.btn_favorite, Qt.LeftButton)
                _app().processEvents()
                t.assert_true(f"{mode}快捷按钮收藏", first in repo.favorite_project_ids())
                t.assert_eq(f"{mode}按钮不重载预览", preview.call_count, 0)
                QTest.mouseClick(win.btn_favorite, Qt.LeftButton)
                t.assert_true(f"{mode}快捷按钮取消", first not in repo.favorite_project_ids())
            win.activateWindow()
            (win.proj_table if mode == "list" else win.proj_view).setFocus()
            _app().processEvents()
            QTest.keyClick(win, Qt.Key_D, Qt.ControlModifier)
            _app().processEvents()
            t.assert_true(f"{mode}Ctrl+D收藏", first in repo.favorite_project_ids())
            win.action_set_projects_favorite([first], False)

        _select(win, [first, second])
        QTest.mouseClick(win.btn_favorite, Qt.LeftButton)
        t.assert_eq("混合状态多选统一收藏", repo.favorite_project_ids(), {first, second})
        QTest.mouseClick(win.btn_favorite, Qt.LeftButton)
        t.assert_eq("全部收藏多选统一取消", repo.favorite_project_ids(), set())

        # 菜单打开后选择变化不得更换批量目标。
        for mode in ("grid", "list"):
            win._set_view_mode(mode)
            _select(win, [first, second])
            view = win.proj_table if mode == "list" else win.proj_view
            target = win.proj_model.index_of_id(first)
            view.scrollTo(target)
            _app().processEvents()

            def menu_factory(parent):
                menu = QMenu(parent)
                def execute(*_args):
                    action = next(a for a in menu.actions() if "收藏项目" in a.text())
                    _select(win, [third])
                    action.trigger()
                menu.exec = execute
                return menu

            with patch("app.ui.mw_projects.QMenu", side_effect=menu_factory):
                view.customContextMenuRequested.emit(view.visualRect(target).center())
            t.assert_eq(f"{mode}右键固定所选组", repo.favorite_project_ids(), {first, second})
            win.action_set_projects_favorite([first, second], False)

        win.action_set_projects_favorite([first, second], True)
        _filter(win, "favorite")
        t.assert_eq("收藏节点只显示收藏项目", _ids(win), {first, second})
        t.assert_in("收藏节点计数", "2", win.tag_tree.currentItem().text(0))
        win.file_type_filter.set_selected_kinds({"video"})
        t.assert_eq("收藏与类型交集", _ids(win), {first})
        _search(win, "alpha")
        t.assert_eq("手动编辑是完整新查询", _ids(win), {first, second})
        _search(win, 'alpha AND favorite:"true" AND has:"video"')
        t.assert_eq("显式收藏类型关键词组合", _ids(win), {first})
        win.btn_search_all.click()
        t.assert_eq("全库一次清空全部条件", _ids(win), {first, second, third})
        win.btn_search_all.click()
        t.assert_eq("重复全库不恢复旧条件", _ids(win), {first, second, third})
        _search(win, 'alpha AND favorite:"true" AND has:"video"')
        win.action_set_projects_favorite([first], False)
        t.assert_eq("取消后从收藏筛选立即移除", _ids(win), set())
        t.assert_true("空结果随机按钮禁用", not win.btn_random_project.isEnabled())
        with patch("app.ui.mw_projects.random.choice") as choice:
            win.action_random_project()
            t.assert_eq("空结果不抽取", choice.call_count, 0)

        win.file_type_filter.clear()
        _filter(win, "all")
        _search(win, "favorite:true")
        t.assert_eq("收藏搜索", _ids(win), {second})
        win.action_set_projects_favorite([second], False)
        t.assert_eq("取消后收藏搜索立即更新", _ids(win), set())
        win.action_set_projects_favorite([first, second], True)
        _search(win, "")
        _filter(win, "tag", "集合")

        for mode in ("grid", "list"):
            win._set_view_mode(mode)
            _select(win, [first, second])
            with patch("app.ui.mw_projects.random.choice", return_value=second) as choice:
                QTest.mouseClick(win.btn_random_project, Qt.LeftButton)
                _app().processEvents()
                t.assert_eq(f"{mode}随机仅包含当前标签筛选", set(choice.call_args.args[0]), {first, second})
                t.assert_eq(f"{mode}随机改为单选", win._selected_project_ids(), [second])
                t.assert_eq(f"{mode}当前详情同步随机目标", win._current_project_id, second)
                t.assert_eq(f"{mode}不改变标签筛选", win._current_filter_kind, "tag")
                t.assert_eq(f"{mode}两视图共用当前选择", win.proj_view.currentIndex(), win.proj_table.currentIndex())

        win.file_type_filter.set_selected_kinds({"video"})
        with patch("app.ui.mw_projects.random.choice", return_value=first) as choice:
            win.action_random_project()
            t.assert_eq("单个结果仍能选择", choice.call_args.args[0], [first])
            t.assert_eq("单个结果被选中", win._current_project_id, first)
        with patch("app.ui.mw_projects.random.choice", return_value=first), patch.object(win.preview, "show_file") as preview:
            win.action_random_project()
            t.assert_eq("再次抽中当前项目不重载预览", preview.call_count, 0)

        win.file_type_filter.clear()
        _filter(win, "all")
        win.search_box.setText("beta")
        t.assert_true("搜索防抖尚未执行", win._search_timer.isActive())
        with patch("app.ui.mw_projects.random.choice", return_value=third) as choice:
            win.action_random_project()
            t.assert_eq("随机先应用未完成搜索", choice.call_args.args[0], [third])
            t.assert_eq("防抖刷新后仅选中命中项目", win._current_project_id, third)
        win.search_box.setText("(")
        with patch("app.ui.mw_projects.random.choice") as choice:
            win.action_random_project()
            t.assert_eq("语法错误不随机旧结果", choice.call_count, 0)
        _search(win, "(")
        t.assert_true("错误查询禁用按钮", not win.btn_random_project.isEnabled())
        _search(win, "favorite:true AND has:archive")
        with patch("app.ui.mw_projects.random.choice", return_value=second) as choice:
            win.action_random_project()
            t.assert_eq("随机遵循收藏和has表达式", choice.call_args.args[0], [second])
        win.search_box.setText("favorite:")
        _app().processEvents()
        candidates = [win._completion.item(i).text() for i in range(win._completion.count())]
        t.assert_true("收藏搜索补全", any("favorite:true" in value for value in candidates))
        win._search_timer.stop()
    finally:
        _close_window(win)
        repo.conn.close()

    reopened, repo = _window(root)
    try:
        t.assert_eq("GUI重开收藏仍存在", repo.favorite_project_ids(), {first, second})
        t.assert_true("GUI重开恢复收藏星标", reopened.proj_model.index_of_id(first).data(ProjectModel.RoleFavorite))
    finally:
        _close_window(reopened)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    t = T()
    with tempfile.TemporaryDirectory() as directory:
        run(Path(directory), t)
    sys.exit(0 if t.report() else 1)
