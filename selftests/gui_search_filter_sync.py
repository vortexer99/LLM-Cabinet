"""查询与侧栏双向同步：只连接显式临时库，使用合成登记记录。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu

from app.models import FileItem, Project
from app.search import AndNode, NotNode, OrNode, TermNode, parse_search
from app.search_history import HISTORY_SETTING_KEY, SAVED_SEARCHES_SETTING_KEY, load_history, load_saved_searches
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _window


def quoted(value: str) -> str:
    """用独立编码产生含特殊字符的完整测试查询。"""
    return json.dumps(value, ensure_ascii=False)


def nodes(node):
    """遍历解析树，断言时不依赖格式化空白与冗余括号。"""
    if node is None:
        return
    yield node
    if isinstance(node, (AndNode, OrNode)):
        for child in node.items:
            yield from nodes(child)
    elif isinstance(node, NotNode):
        yield from nodes(node.item)


def run(root: Path, t: T) -> None:
    app = _app()
    win, repo = _window(root)
    try:
        special = ['带 空格', '引"号', r'反\斜杠', '括号(1):= AND OR', 'AND', 'OR']
        a = repo.save_project(Project(title="alpha 视频", tags=["A", *special]))
        b = repo.save_project(Project(title="alpha 压缩", tags=["B"]))
        c = repo.save_project(Project(title="beta 双类型", tags=["A/子标签"]))
        d = repo.save_project(Project(title="alpha 普通"))
        e = repo.save_project(Project(title="beta 外部", tags=["外部"]))
        all_ids = {a, b, c, d, e}
        for pid, kinds in ((a, ["video"]), (b, ["archive"]), (c, ["video", "archive"]),
                           (d, ["pdf"]), (e, ["pdf"])):
            for kind in kinds:
                repo.add_file(FileItem(project_id=pid, path=str(root / f"synthetic-{pid}-{kind}"),
                                       kind=kind, is_relative=pid != e))
        repo.set_projects_favorite([a, b], True)
        field_id = repo.add_field("同步测试建议", "text")
        repo.add_suggestions(c, None, [(field_id, "合成待审阅值")])
        repo.mark_project_mcp_modified(b)
        repo.mark_project_mcp_modified(e)
        win.refresh_projects()
        app.processEvents()

        def ids(window=win) -> set[int]:
            return {window.proj_model.project_at(row).id for row in range(window.proj_model.rowCount())}

        def execute(text: str) -> None:
            win.search_box.setText(text)
            win._search_timer.stop()
            win._completion.hide()
            win.refresh_projects(filter_only=True)
            app.processEvents()

        def ast():
            parsed = parse_search(win.search_box.text())
            t.assert_true("生成的查询语法有效", parsed.ok, repr(parsed.error))
            return parsed.ast

        def assert_matches_query(label: str) -> None:
            parsed = parse_search(win.search_box.text())
            if parsed.ok:
                t.assert_eq(label, ids(), {project.id for project in repo.list_projects_query(parsed.ast)})
            else:
                t.assert_true(label, False, repr(parsed.error))

        def tag(kind: str, value: str = "") -> None:
            win._on_tag_filter_changed(kind, value)
            app.processEvents()

        execute('alpha')
        tag("tag", "A")
        t.assert_eq("标签追加到现有关键词", ids(), {a})
        t.assert_eq("标签完整出现在查询中", [node.value for node in nodes(ast())
                                          if isinstance(node, TermNode) and node.field == "tag"], ["A"])
        t.assert_eq("标签使用精确匹配运算符", [node.op for node in nodes(ast())
                                            if isinstance(node, TermNode) and node.field == "tag"], ["="])
        tag("tag", "B")
        t.assert_eq("切换标签替换旧标签而不叠加", ids(), {b})
        t.assert_true("旧标签条件已移除", all(node.value != "A" for node in nodes(ast())
                                             if isinstance(node, TermNode) and node.field == "tag"))
        tag("all")
        t.assert_eq("全部标签只移除标签组保留关键词", ids(), {a, b, d})
        t.assert_eq("移除标签后关键词仍在查询", [node.value for node in nodes(ast())
                                                if isinstance(node, TermNode) and node.field is None], ["alpha"])

        execute("")
        tag("tag", "A")
        t.assert_eq("精确标签不混入子标签", ids(), {a})
        tag("tag_prefix", "A")
        t.assert_eq("前缀标签覆盖自身和子标签", ids(), {a, c})
        t.assert_true("前缀条件可见且可复制", any(isinstance(node, TermNode)
                     and node.field == "tag_prefix" and node.value == "A" for node in nodes(ast())))

        # 标签名称中的运算符、引号、反斜杠和空格必须作为文字往返。
        for name in special:
            tag("tag", name)
            t.assert_eq(f"特殊字符标签精确命中 {name}", ids(), {a})
            terms = [node for node in nodes(ast()) if isinstance(node, TermNode) and node.field == "tag"]
            t.assert_eq(f"标签转义往返 {name}", [(term.op, term.value) for term in terms], [("=", name)])
            t.assert_eq(f"查询反向选中侧栏 {name}", win.tag_tree.current_filter(), ("tag", name))

        # 手动编辑是全新的完整查询，不保留隐藏的旧标签与文件条件。
        win.file_type_filter.set_selected_kinds({"video"})
        t.assert_eq("文件条件进入文本后与标签相交", ids(), {a})
        execute('has:"archive"')
        t.assert_eq("手动编辑不残留标签或旧类型", ids(), {b, c})
        t.assert_eq("手动has查询反向选择文件控件", win.file_type_filter.selected_kinds, {"archive"})
        t.assert_eq("手动查询没有标签则侧栏回到全部", win.tag_tree.current_filter(), ("all", ""))
        execute("")
        t.assert_eq("清空文本恢复全部结果", ids(), all_ids)
        t.assert_eq("清空文本取消类型高亮", win.file_type_filter.selected_kinds, set())
        t.assert_eq("清空文本取消标签高亮", win.tag_tree.current_filter(), ("all", ""))

        execute('alpha')
        win.file_type_filter.set_selected_kinds({"video", "archive"})
        t.assert_eq("任一类型在查询中以OR组合", ids(), {a, b})
        t.assert_true("任一组合保留OR节点", any(isinstance(node, OrNode) for node in nodes(ast())))
        t.assert_true("关键词仍是完整查询的一部分", any(isinstance(node, TermNode)
                      and node.field is None and node.value == "alpha" for node in nodes(ast())))
        win.file_type_filter.set_match_mode("all")
        t.assert_eq("全部模式以AND替换类型组合", ids(), set())
        execute('has:"video" AND has:"archive"')
        t.assert_eq("手动AND查询反向投影全部模式", win.file_type_filter.match_mode, "all")
        t.assert_eq("手动AND查询反向投影两类型", win.file_type_filter.selected_kinds, {"video", "archive"})
        t.assert_eq("手动类型AND命中双类型项目", ids(), {c})
        execute('has:"video" OR has:"archive"')
        t.assert_eq("纯类型OR可投影任一模式", win.file_type_filter.match_mode, "any")
        t.assert_eq("纯类型OR投影所有条目", win.file_type_filter.selected_kinds, {"video", "archive"})

        # 可表达的顶层条件同步；混合 OR / NOT 不被拆解成有误导性的侧栏条件。
        complex_query = 'tag="A" OR has:"archive"'
        execute(complex_query)
        t.assert_eq("混合OR按原始完整表达式执行", ids(), {a, b, c})
        t.assert_eq("混合OR标为完整表达式而不假装单个标签", win.tag_tree.current_filter(), ("query", ""))
        t.assert_eq("混合OR不假装单个文件类型", win.file_type_filter.selected_kinds, set())
        t.assert_eq("手动复杂查询文本保持不变", win.search_box.text(), complex_query)
        tag("tag", "B")
        t.assert_eq("混合OR整体加标签AND而不改内部语义", ids(), {b})
        t.assert_true("追加侧栏条件仍保留混合OR", any(isinstance(node, OrNode) for node in nodes(ast())))
        tag("tag", "A")
        t.assert_eq("切换只替换顶层标签不破坏OR中的标签", ids(), {a})
        tag("all")
        t.assert_eq("全部只移除追加的顶层标签", ids(), {a, b, c})
        execute('NOT tag="B" AND has:"video"')
        t.assert_eq("单个NOT标签投影排除状态", win.tag_tree.current_filter(), ("not_tag", "B"))
        t.assert_eq("NOT旁边可表达类型正常投影", win.file_type_filter.selected_kinds, {"video"})
        tag("tag", "B")
        t.assert_eq("正向标签替换排除条件并保留文件条件", ids(), set())
        t.assert_true("原排除条件已移除", not any(isinstance(node, NotNode) for node in nodes(ast())))
        execute('NOT (tag="B" OR title:"beta") AND has:"video"')
        tag("tag", "B")
        t.assert_true("混合NOT内部语义保持", any(isinstance(node, NotNode) for node in nodes(ast())))

        for kind, expected, field in (("favorite", {a, b}, "favorite"), ("untagged", {d}, "untagged"),
                                      ("review", {c}, "review"), ("mcp", {b, e}, "mcp")):
            execute("")
            tag(kind)
            t.assert_eq(f"{kind}侧栏可见表达式结果", ids(), expected)
            t.assert_true(f"{kind}条件完整写入查询", any(isinstance(node, TermNode)
                          and node.field == field and node.value == "true" for node in nodes(ast())))
            assert_matches_query(f"{kind}结果只由文本决定")

        # 保存/历史里保存的是完整表达式，复制到新窗口也能复现。
        execute('alpha')
        tag("favorite")
        win.file_type_filter.set_selected_kinds({"video"})
        full_query = win.search_box.text()
        t.assert_eq("复制前组合结果", ids(), {a})
        t.assert_true("历史保存完整组合查询", full_query in load_history(repo.get_setting(HISTORY_SETTING_KEY, "[]")))
        with patch("app.ui.mw_search.QInputDialog.getText", return_value=("同步组合测试", True)):
            win._save_current_search()
        saved = load_saved_searches(repo.get_setting(SAVED_SEARCHES_SETTING_KEY, "[]"))
        t.assert_true("收藏保存完整组合查询", any(item["query"] == full_query for item in saved))
        clone, clone_repo = _window(root)
        try:
            clone.search_box.setText(full_query)
            clone._search_timer.stop()
            clone._completion.hide()
            clone.refresh_projects()
            t.assert_eq("复制查询到新窗口得到同结果", ids(clone), {a})
            t.assert_eq("复制查询同步新窗口自定义筛选高亮", clone.tag_tree.current_filter(), ("saved_search", "同步组合测试"))
            t.assert_eq("复制查询同步新窗口文件高亮", clone.file_type_filter.selected_kinds, {"video"})
        finally:
            _close_window(clone)
            clone_repo.conn.close()

        execute('tag="B"')
        def menu_factory(parent):
            menu = QMenu(parent)
            def choose(*_args):
                saved_menu = next(action.menu() for action in menu.actions() if action.text() == "收藏")
                next(action for action in saved_menu.actions() if "同步组合测试" in action.text()).trigger()
            menu.exec = choose
            return menu
        with patch("app.ui.mw_search.QMenu", side_effect=menu_factory):
            win._show_search_menu()
        QTest.qWait(250)
        t.assert_eq("收藏菜单恢复完整文本", win.search_box.text(), full_query)
        t.assert_eq("收藏菜单不叠加旧标签", ids(), {a})

        execute('tag="B"')
        def history_factory(parent):
            menu = QMenu(parent)
            def choose(*_args):
                history = next(action.menu() for action in menu.actions() if action.text() == "最近搜索")
                next(action for action in history.actions() if action.text() == full_query).trigger()
            menu.exec = choose
            return menu
        with patch("app.ui.mw_search.QMenu", side_effect=history_factory):
            win._show_search_menu()
        QTest.qWait(250)
        t.assert_eq("历史菜单恢复完整文本", win.search_box.text(), full_query)
        t.assert_eq("历史菜单不叠加旧标签", ids(), {a})

        # 程序写框和反向投影不应造成信号循环或额外的防抖刷新。
        execute('alpha')
        with patch.object(win, "refresh_projects", wraps=win.refresh_projects) as refresh, \
                patch.object(win.tag_tree, "populate", wraps=win.tag_tree.populate) as rebuild:
            tag("tag", "A")
            QTest.qWait(250)
            t.assert_eq("点标签只刷新一次且不产生定时器回环", refresh.call_count, 1)
            t.assert_eq("仅筛选变化仍复用标签树", rebuild.call_count, 0)
            t.assert_true("侧栏写查询后无待执行搜索", not win._search_timer.isActive())
        execute('alpha')
        with patch.object(win, "refresh_projects", wraps=win.refresh_projects) as refresh:
            win.file_type_filter.set_selected_kinds({"video"})
            QTest.qWait(250)
            t.assert_eq("文件条件一次操作只刷新一次", refresh.call_count, 1)
        with patch.object(win, "refresh_projects", wraps=win.refresh_projects) as refresh:
            QTest.mouseClick(win.btn_search_all, Qt.LeftButton)
            QTest.qWait(250)
            t.assert_eq("全库一次操作只刷新一次", refresh.call_count, 1)
        t.assert_eq("全库动作清空查询", win.search_box.text(), "")
        t.assert_eq("全库动作恢复所有项目", ids(), all_ids)
        t.assert_eq("全库动作清空文件选择", win.file_type_filter.selected_kinds, set())
        t.assert_eq("全库动作清空标签选择", win.tag_tree.current_filter(), ("all", ""))
        QTest.mouseClick(win.btn_search_all, Qt.LeftButton)
        t.assert_eq("全库按钮重复点击不恢复旧查询", win.search_box.text(), "")
        t.assert_true("全库按钮是一次动作", not win.btn_search_all.isCheckable())
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-search-sync-") as directory:
        run(Path(directory), t)
    sys.exit(0 if t.report() else 1)
