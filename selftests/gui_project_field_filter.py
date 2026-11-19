"""项目右键同字段筛选回归：真实菜单入口、显式临时库及合成数据。

菜单执行和剪贴板均被拦截，不读取用户数据库、用户文件或系统剪贴板。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from app.models import Project
from app.search import AndNode, NotNode, OrNode, TermNode, parse_search
from app.ui.project_card import EXTRA_COL_FILES, EXTRA_COL_SIZE, EXTRA_COL_UPDATED
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _field, _window


def _nodes(node):
    """遍历 AST，避免把格式化空白与括号作为行为约定。"""
    if node is None:
        return
    yield node
    if isinstance(node, (AndNode, OrNode)):
        for child in node.items:
            yield from _nodes(child)
    elif isinstance(node, NotNode):
        yield from _nodes(node.item)


def _actions(menu: QMenu):
    """递归返回菜单项；只在测试里观察真实生成的菜单。"""
    for action in menu.actions():
        yield action
        if action.menu() is not None:
            yield from _actions(action.menu())


def run(root: Path, t: T) -> None:
    app = _app()
    win, repo = _window(root)
    try:
        # 所有字段均来自这个临时库；自定义字段刻意包含搜索语法字符。
        repo.conn.execute("UPDATE fields SET visible=1")
        repo.conn.commit()
        author = _field(repo, "作者", "text", "author")
        number = repo.add_field("测试数字", "number")
        rating = repo.add_field("测试评分", "rating")
        hidden = repo.add_field("隐藏备注", "text")
        special_name = '字段 空格"\\(AND):=<>\n第二行'
        special = repo.add_field(special_name, "textarea")
        empty = repo.add_field("空字段", "text")
        reserved = repo.add_field("has", "text")
        # key 和 name 都冲突，不能把此字段值误筛到标题或其它字段。
        ambiguous = _field(repo, "title", "text", "tag")
        repo.conn.execute("UPDATE fields SET visible=0 WHERE id=?", (hidden,))
        repo.conn.commit()
        description = "第一行\n" + "完整描述" * 40 + "结尾标记"
        special_value = '字面 空格"\\(1):= AND OR NOT\n第二行\t末尾'
        tags = ["分类", "分类/子项", '带 空格"\\():= AND OR']
        common = {author: "王五", number: "-2.50", rating: "4", hidden: "隐藏的完整值",
                  special: special_value, empty: "", reserved: "video", ambiguous: "危险值"}
        a = repo.save_project(Project(title="目标项目", description_md=description,
                                     tags=tags, field_values=common))
        b = repo.save_project(Project(title="同作者", tags=["分类"], field_values={author: "王五"}))
        c = repo.save_project(Project(title="包含作者名", tags=["分类/子项"],
                                     field_values={author: "王五甲"}))
        d = repo.save_project(Project(title="另一作者", field_values={author: "赵六"}))
        blank = repo.save_project(Project(title="全部空字段"))
        repo.set_projects_favorite([a], True)
        win.refresh_projects()
        win._set_view_mode("list")
        app.processEvents()
        model = win.proj_model

        def ids() -> set[int]:
            return {model.project_at(row).id for row in range(model.rowCount())}

        def execute(query: str) -> None:
            win.search_box.setText(query)
            win._search_timer.stop()
            win._completion.hide()
            win.refresh_projects(filter_only=True)
            app.processEvents()

        def tree():
            result = parse_search(win.search_box.text())
            t.assert_true("菜单生成查询有效", result.ok, repr(result.error))
            return result.ast

        def terms() -> list[TermNode]:
            return [node for node in _nodes(tree()) if isinstance(node, TermNode)]

        def capture(pid: int, key: str = "title", *, grid: bool = False) -> QMenu:
            """走真实 customContextMenuRequested 信号，确保 sender 是所点视图。"""
            view = win.proj_view if grid else win.proj_table
            row = model.index_of_id(pid).row()
            col = 0 if grid else next(col for col in range(model.columnCount())
                                      if model.column_key(col) == key)
            index = model.index(row, col)
            view.scrollTo(index)
            app.processEvents()
            pos = view.visualRect(index).center()
            t.assert_eq("菜单坐标命中目标项目", view.indexAt(pos).data(model.RoleId), pid)
            captured: list[QMenu] = []

            def menu_factory(parent):
                menu = QMenu(parent)

                def stop_menu(*_args, **_kwargs):
                    captured.append(menu)
                    return None

                menu.exec = stop_menu
                return menu

            clipboard = Mock()
            clipboard.mimeData.return_value = None
            with patch("app.ui.mw_projects.QMenu", side_effect=menu_factory), \
                    patch.object(QApplication, "clipboard", return_value=clipboard):
                view.customContextMenuRequested.emit(pos)
            if len(captured) != 1:
                raise AssertionError(f"真实菜单入口应执行一次，实际 {len(captured)}")
            return captured[0]

        def same_actions(menu: QMenu):
            return [action for action in _actions(menu) if "筛选同" in action.text()]

        def field_action(menu: QMenu, label: str | None = None):
            candidates = [action for action in same_actions(menu)
                          if action.menu() is None and (label is None or label in action.text())]
            if len(candidates) != 1:
                raise AssertionError(f"期望唯一同字段动作 {label!r}，实际 {[a.text() for a in candidates]}")
            return candidates[0]

        def click_field(key: str, expected: set[int], value: str) -> None:
            execute("")
            field_action(capture(a, key)).trigger()
            t.assert_eq(f"{key}原始值筛选结果", ids(), expected)
            t.assert_eq(f"{key}使用原始值与精确等号", [(term.op, term.value) for term in terms()],
                        [("=", value)])

        # 当前项目是另一行，且目标仍属于多选范围：必须取右键的单元格。
        selection = win.proj_table.selectionModel()
        selection.setCurrentIndex(model.index_of_id(d), QItemSelectionModel.ClearAndSelect)
        selection.select(model.index_of_id(a), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        field_action(capture(a, "author")).trigger()
        t.assert_eq("多选右键作者只取所点行且排除部分同名", ids(), {a, b})
        t.assert_eq("作者精确等号", [(term.op, term.value) for term in terms()], [("=", "王五")])

        # 菜单闭包必须固定值，之后换当前行、修改模型对象也不能改变动作目标。
        execute("")
        frozen = field_action(capture(a, "author"))
        win.proj_table.setCurrentIndex(model.index_of_id(d))
        model.project_at(model.index_of_id(a).row()).field_values[author] = "动作后变化"
        frozen.trigger()
        t.assert_eq("菜单动作冻结创建时原始值", ids(), {a, b})

        click_field(f"user:{number}", {a}, "-2.50")
        click_field(f"user:{rating}", {a}, "4")
        execute("")
        title_col = next(i for i in range(model.columnCount()) if model.column_key(i) == "title")
        t.assert_true("合成标题显示包含收藏修饰", model.index(model.index_of_id(a).row(), title_col).data() != "目标项目")
        click_field("title", {a}, "目标项目")
        click_field("description", {a}, description)
        click_field(f"user:{special}", {a}, special_value)

        # 每个标签分别生成精确动作，不能把多标签显示串整体当成标签。
        for tag in tags:
            execute("")
            menu = capture(a, "tags")
            leaf = [action for action in _actions(menu)
                    if action.menu() is None and tag == action.text() and action.isEnabled()]
            if len(leaf) != 1:
                raise AssertionError(f"缺少独立标签动作 {tag!r}：{[a.text() for a in _actions(menu)]}")
            leaf[0].trigger()
            expected = {a, b} if tag == "分类" else ({a, c} if tag == "分类/子项" else {a})
            t.assert_eq(f"标签逐项精确筛选 {tag}", ids(), expected)
            t.assert_eq(f"标签逐项精确AST {tag}", [(term.field, term.op, term.value) for term in terms()],
                        [("tag", "=", tag)])

        for key in (f"user:{empty}", EXTRA_COL_FILES, EXTRA_COL_SIZE, EXTRA_COL_UPDATED):
            execute("")
            menu = capture(a, key)
            t.assert_true(f"{key}不伪造可用同字段动作",
                          not any(action.isEnabled() and action.menu() is None for action in same_actions(menu)))
        execute("")
        t.assert_true("空作者无可用筛选动作", not any(action.isEnabled() and action.menu() is None
                      for action in same_actions(capture(blank, "author"))))

        # 网格菜单覆盖隐藏字段，并保留对每个标签的单独入口。
        execute("")
        win._set_view_mode("grid")
        app.processEvents()
        grid_menu = capture(a, grid=True)
        parents = [action for action in grid_menu.actions() if action.text() == "筛选同字段值"]
        t.assert_eq("网格存在同字段值子菜单", len(parents), 1)
        for label in ("标题", "描述", "作者", "测试数字", "测试评分", special_name):
            t.assert_true(f"网格包含非空字段 {label}", any(action.toolTip().startswith(label + "：")
                          for action in same_actions(grid_menu)))
        grid_tag_menus = [action.menu() for action in _actions(grid_menu)
                          if action.menu() is not None and action.text() == "筛选同标签"]
        t.assert_eq("网格标签保留独立子菜单", len(grid_tag_menus), 1)
        if grid_tag_menus:
            t.assert_eq("网格标签逐项保留完整值", [action.text() for action in grid_tag_menus[0].actions()], tags)
        hidden_action = field_action(grid_menu, "隐藏备注")
        hidden_action.trigger()
        t.assert_eq("网格隐藏自定义字段正常筛选", ids(), {a})
        t.assert_eq("隐藏字段完整值进入查询", [term.value for term in terms()], ["隐藏的完整值"])
        t.assert_true("网格菜单排除空自定义字段", not any("空字段" in action.text()
                      for action in _actions(grid_menu)))
        win._set_view_mode("list")

        # 先捕获真实动作，再施加查询；目标可以在当前表达式结果之外。
        def apply_on(query: str):
            execute("")
            action = field_action(capture(a, "author"))
            execute(query)
            action.trigger()
            return tree()

        apply_on('title:"项目" AND author:"赵六" AND 作者="王五甲" AND favorite:true')
        t.assert_eq("替换重复同字段条件并保留其它条件", ids(), {a})
        result = terms()
        t.assert_eq("同字段别名重复条件合并为一个", [(term.op, term.value) for term in result
                    if term.field in ("author", "作者")], [("=", "王五")])
        t.assert_true("其它标题与收藏条件保留", {term.field for term in result} >= {"title", "favorite"})
        original_or = parse_search('author="赵六" OR title="目标项目"').ast
        original_not = parse_search('NOT (author="王五甲" OR title="全部空字段")').ast
        complex_ast = apply_on('(author="赵六" OR title="目标项目") AND '
                               'NOT (author="王五甲" OR title="全部空字段") AND author:"赵六"')
        t.assert_true("复杂OR整体保留", original_or in list(_nodes(complex_ast)))
        t.assert_true("复杂NOT整体保留", original_not in list(_nodes(complex_ast)))
        t.assert_eq("复杂表达式追加精确作者后结果", ids(), {a})

        execute("")
        action = field_action(capture(a, "author"))
        with patch.object(win, "refresh_projects", wraps=win.refresh_projects) as refresh:
            action.trigger()
            QTest.qWait(250)
            t.assert_eq("同字段动作只刷新一次", refresh.call_count, 1)
            t.assert_true("同字段动作无待执行搜索定时器", not win._search_timer.isActive())

        execute("")
        action = field_action(capture(a, "author"))
        invalid = 'title:"未闭合'
        execute(invalid)
        before = ids()
        with patch.object(win, "refresh_projects", wraps=win.refresh_projects) as refresh:
            action.trigger()
            t.assert_eq("非法当前表达式保持文本", win.search_box.text(), invalid)
            t.assert_eq("非法表达式不推进筛选", ids(), before)
            t.assert_eq("非法表达式不触发刷新", refresh.call_count, 0)

        for fid in (reserved, ambiguous):
            execute("")
            menu = capture(a, f"user:{fid}" if fid == reserved else "tag")
            candidates = [action for action in same_actions(menu) if action.menu() is None]
            t.assert_true(f"保留字及name/key冲突字段{fid}无可用错误筛选",
                          not any(action.isEnabled() for action in candidates))
            if candidates:
                t.assert_true(f"冲突字段{fid}提供禁用原因", all(action.toolTip() for action in candidates))
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-project-field-filter-") as directory:
        run(Path(directory), t)
    sys.exit(0 if t.report() else 1)
