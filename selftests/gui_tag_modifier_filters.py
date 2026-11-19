"""侧栏标签修饰键组合回归：真实 Qt 事件，且只使用显式临时库。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QScrollArea

from app.models import FileItem, Project
from app.search import parse_search
from app.search_filters import quote_value, tag_states
from app.search_history import SAVED_SEARCHES_SETTING_KEY, load_saved_searches
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _window


def run(root: Path, t: T) -> None:
    """用合成项目结果区分布尔分组，避免仅断言实现生成的 AST。"""
    app = _app()
    win, repo = _window(root)
    try:
        win.resize(1280, 820)
        tree = win.tag_tree
        tree.setAnimated(False)
        special = ['带 空格', '引"号', r'反\斜杠', '括号(1):= AND OR', 'NOT']
        fixtures = {
            "a": ("alpha A", ["A", *special], "video", True),
            "b": ("alpha B", ["B"], "video", True),
            "ab": ("alpha AB", ["A", "B"], "video", True),
            "c": ("alpha C", ["C"], "video", True),
            "ac": ("alpha AC", ["A", "C"], "video", True),
            "abc": ("alpha ABC", ["A", "B", "C"], "video", True),
            "child": ("alpha 子标签", ["A/子标签", "B"], "video", True),
            "none": ("alpha 无标签", [], "video", True),
            "outside": ("beta C", ["C"], "video", True),
            "archive": ("alpha 压缩包C", ["C"], "archive", True),
            "unfav": ("alpha 非收藏C", ["C"], "video", False),
        }
        projects: dict[str, int] = {}
        for name, (title, tags, kind, favorite) in fixtures.items():
            pid = repo.save_project(Project(title=title, tags=tags))
            projects[name] = pid
            file_path = root / f"synthetic-{name}.txt"
            file_path.write_text("自测合成文件", encoding="utf-8")
            repo.add_file(FileItem(project_id=pid, path=str(file_path.resolve()),
                                   kind=kind, is_relative=False))
            if favorite:
                repo.set_projects_favorite([pid], True)
        all_names = set(projects)
        names_a = {"a", "ab", "ac", "abc"}
        names_b = {"b", "ab", "abc", "child"}
        names_c = {"c", "ac", "abc", "outside", "archive", "unfav"}
        names_prefix = names_a | {"child"}
        base_query = 'alpha AND has:"video" AND favorite:true'
        base_names = {"a", "b", "ab", "c", "ac", "abc", "child", "none"}

        # 文件类别仅用于查询，不让视频预览尝试解码占位文本。
        with patch.object(win.preview, "show_file"):
            win.refresh_projects()
            app.processEvents()

            def ids() -> set[int]:
                return {win.proj_model.project_at(row).id
                        for row in range(win.proj_model.rowCount())}

            def execute(query: str) -> None:
                win.search_box.setText(query)
                win._search_timer.stop()
                win._completion.hide()
                win.refresh_projects(filter_only=True)
                app.processEvents()

            def node(kind: str, value: str):
                result = tree._find_item_by_kind_value(kind, value)
                assert result is not None, (kind, value)
                return result

            def position(kind: str, value: str) -> QPoint:
                result = node(kind, value)
                ancestor = result.parent()
                while ancestor is not None:
                    ancestor.setExpanded(True)
                    ancestor = ancestor.parent()
                tree.scrollToItem(result)
                app.processEvents()
                return tree.visualItemRect(result).center()

            def click(kind: str, value: str, modifiers=Qt.NoModifier) -> None:
                QTest.mouseClick(tree.viewport(), Qt.LeftButton, modifiers,
                                 pos=position(kind, value))
                app.processEvents()

            def checked_click(label: str, kind: str, value: str,
                              modifiers=Qt.NoModifier) -> None:
                with patch.object(win, "refresh_projects", wraps=win.refresh_projects) as refresh:
                    click(kind, value, modifiers)
                    QTest.qWait(230)
                    t.assert_eq(label + "仅刷新一次", refresh.call_count, 1)
                t.assert_true(label + "无遗留搜索定时器", not win._search_timer.isActive())

            def result(label: str, names: set[str]) -> None:
                parsed = parse_search(win.search_box.text())
                t.assert_true(label + "有效查询", parsed.ok, repr(parsed.error))
                expected = {projects[name] for name in names}
                t.assert_eq(label + "实际项目", ids(), expected)
                if parsed.ok:
                    t.assert_eq(label + "文本重跑结果", {p.id for p in repo.list_projects_query(parsed.ast)}, expected)

            def states(label: str, expected: dict[tuple[str, str], str]) -> None:
                parsed = parse_search(win.search_box.text())
                t.assert_eq(label + "全部标签投影", tag_states(parsed.ast), expected)
                for (kind, value), state in expected.items():
                    actual = node(kind, value).icon(0).pixmap(16, 16).toImage()
                    t.assert_eq(label + f" {kind}:{value} 图标", actual, icons[state])

            execute("")
            neutral = node("tag", "A").icon(0).pixmap(16, 16).toImage()
            click("tag", "A")
            included = node("tag", "A").icon(0).pixmap(16, 16).toImage()
            click("tag", "A")
            excluded = node("tag", "A").icon(0).pixmap(16, 16).toImage()
            icons = {"include": included, "exclude": excluded}
            t.assert_true("三态图标可区分", neutral != included and neutral != excluded and included != excluded)

            for modifiers, label in ((Qt.ShiftModifier, "Shift"), (Qt.ControlModifier, "Ctrl"),
                                      (Qt.ShiftModifier | Qt.ControlModifier, "Shift+Ctrl")):
                execute("")
                checked_click(label + "首次", "tag", "A", modifiers)
                result(label + "无标签条件从包含开始", names_a)
                states(label + "首次", {("tag", "A"): "include"})

            execute(base_query)
            checked_click("Shift首次A保留其它条件", "tag", "A", Qt.ShiftModifier)
            checked_click("Shift B", "tag", "B", Qt.ShiftModifier)
            result("A AND B保留关键词文件收藏", {"ab", "abc"})
            checked_click("Ctrl C", "tag", "C", Qt.ControlModifier)
            result("逐步组合为(A AND B) OR C", {"ab", "c", "ac", "abc"})
            expected_states = {("tag", value): "include" for value in ("A", "B", "C")}
            states("组合三个图标同时标记", expected_states)
            t.assert_eq("文件条件投影保持", win.file_type_filter.selected_kinds, {"video"})

            # 正项已出现在任意嵌套分支时，重复两种修饰都不能扩张表达式。
            before = win.search_box.text()
            for modifiers in (Qt.ControlModifier, Qt.ShiftModifier,
                              Qt.ShiftModifier | Qt.ControlModifier):
                for value in ("A", "B", "C"):
                    click("tag", value, modifiers)
                    t.assert_eq("重复修饰不增长表达式 " + str((modifiers, value)), win.search_box.text(), before)
            result("重复修饰结果幂等", {"ab", "c", "ac", "abc"})

            saved_query = win.search_box.text()
            win.refresh_projects()
            app.processEvents()
            result("完整刷新组合保持", {"ab", "c", "ac", "abc"})
            states("完整刷新组合图标", expected_states)
            with patch("app.ui.mw_search.QInputDialog.getText", return_value=("修饰键组合", True)):
                win._save_current_search()
            saved = load_saved_searches(repo.get_setting(SAVED_SEARCHES_SETTING_KEY, "[]"))
            t.assert_true("收藏完整组合文本", any(record["query"] == saved_query for record in saved))
            execute('tag="B"')
            click("saved_search", "修饰键组合")
            result("收藏恢复布尔查询", {"ab", "c", "ac", "abc"})
            states("收藏恢复全部图标", expected_states)

            # 普通点击沿用原侧栏替换语义，切回单标签并取消收藏状态。
            # 关键词与文件条件仍保留，随后继续原有三态。
            ordinary_names = base_names | {"unfav"}
            for label, names, expected in (
                ("组合回单包含", ordinary_names & names_a, {("tag", "A"): "include"}),
                ("单标签继续排除", ordinary_names - names_a, {("tag", "A"): "exclude"}),
                ("单标签继续取消", ordinary_names, {}),
            ):
                checked_click(label, "tag", "A")
                result(label, names)
                states(label, expected)
            t.assert_eq("取消后其它标签图标回中立", node("tag", "B").icon(0).pixmap(16, 16).toImage(), neutral)

            execute('tag="A"')
            checked_click("Shift Ctrl优先AND", "tag", "B", Qt.ShiftModifier | Qt.ControlModifier)
            result("Shift Ctrl不退化为OR", names_a & names_b)

            execute('tag="C"')
            checked_click("Shift前缀", "tag_prefix", "A", Qt.ShiftModifier)
            result("精确标签AND前缀", names_c & names_prefix)
            execute('tag="C"')
            checked_click("Ctrl前缀", "tag_prefix", "A", Qt.ControlModifier)
            result("精确标签OR前缀", names_c | names_prefix)
            states("前缀和精确标签同时标记", {("tag", "C"): "include", ("tag_prefix", "A"): "include"})
            checked_click("前缀组合普通点击", "tag_prefix", "A")
            result("前缀普通点击恢复单包含", names_prefix)
            click("tag_prefix", "A")
            result("前缀普通点击排除", all_names - names_prefix)
            click("tag_prefix", "A")
            result("前缀普通点击取消", all_names)

            for modifiers, expected in ((Qt.ShiftModifier, (all_names - names_a) & names_b),
                                        (Qt.ControlModifier, (all_names - names_a) | names_b)):
                execute('NOT (tag="A")')
                checked_click("旧NOT组合" + str(modifiers), "tag", "B", modifiers)
                result("旧NOT保留语义" + str(modifiers), expected)
                states("旧NOT排除与包含同时显示", {("tag", "A"): "exclude", ("tag", "B"): "include"})
            execute('NOT (tag_prefix:"A")')
            click("tag", "B", Qt.ControlModifier)
            result("旧NOT前缀可OR组合", (all_names - names_prefix) | names_b)

            for value in special:
                execute('tag="B"')
                click("tag", value, Qt.ControlModifier)
                result("特殊字符OR " + value, names_b | {"a"})
                states("特殊字符准确投影 " + value, {("tag", "B"): "include", ("tag", value): "include"})
                before = win.search_box.text()
                click("tag", value, Qt.ShiftModifier)
                t.assert_eq("特殊字符重复不增长 " + value, win.search_box.text(), before)
                execute('NOT (tag=' + quote_value(value) + ')')
                click("tag", "B", Qt.ShiftModifier)
                result("特殊字符NOT保留 " + value, names_b)

            # 手动文本与侧栏一致；混合 OR 和复杂 NOT 必须保留为不可拆整体。
            execute('alpha AND (tag="A" OR NOT tag="B")')
            result("手动组合初始结果", (names_a | (all_names - names_b)) - {"outside"})
            states("手动组合正反图标", {("tag", "A"): "include", ("tag", "B"): "exclude"})
            click("tag", "C", Qt.ShiftModifier)
            result("手动组合继续AND", ((names_a | (all_names - names_b)) - {"outside"}) & names_c)
            for query, original in (
                ('tag="A" OR title="beta C"', names_a | {"outside"}),
                ('NOT (tag="A" OR tag="B")', all_names - names_a - names_b),
            ):
                execute(query)
                states("混合条件不伪造标签状态 " + query, {})
                click("tag", "C", Qt.ControlModifier)
                result("混合逻辑保留外层AND " + query, original & names_c)
                states("仅独立追加项显示状态 " + query, {("tag", "C"): "include"})

            # 键盘聚焦不触发筛选，用真实 Return/Enter/Space 验证组合路径。
            for key, modifiers, expected in (
                (Qt.Key_Return, Qt.ShiftModifier, names_a & names_b),
                (Qt.Key_Enter, Qt.ControlModifier, names_a | names_b),
                (Qt.Key_Space, Qt.ShiftModifier | Qt.ControlModifier, names_a & names_b),
            ):
                execute('tag="A"')
                tree.blockSignals(True)
                tree.setCurrentItem(node("tag", "B"))
                tree.blockSignals(False)
                tree.setFocus()
                with patch.object(win, "refresh_projects", wraps=win.refresh_projects) as refresh:
                    QTest.keyClick(tree, key, modifiers)
                    QTest.qWait(230)
                    t.assert_eq("键盘组合仅刷新一次 " + str(key), refresh.call_count, 1)
                result("键盘修饰 " + str(key), expected)

            execute('tag="A" AND NOT tag="B"')
            last_ids = ids()
            last_icons = {value: node("tag", value).icon(0).pixmap(16, 16).toImage() for value in ("A", "B", "C")}
            invalid_query = 'tag="未闭合'
            win.search_box.setText(invalid_query)
            win._search_timer.stop()
            win._completion.hide()
            for modifiers in (Qt.ShiftModifier, Qt.ControlModifier):
                click("tag", "C", modifiers)
                t.assert_eq("无效查询不改写", win.search_box.text(), invalid_query)
                t.assert_eq("无效查询不改结果", ids(), last_ids)
                for value, previous in last_icons.items():
                    t.assert_eq("无效查询不改图标 " + value, node("tag", value).icon(0).pixmap(16, 16).toImage(), previous)

            # 项目详情 chip 始终是明确的单标签正向入口。
            for modifiers in (Qt.ShiftModifier, Qt.ControlModifier,
                              Qt.ShiftModifier | Qt.ControlModifier):
                execute('tag="A" OR tag="B"')
                win._select_project_by_id(projects["ac"])
                app.processEvents()
                chip = next(chip for chip in win.project_tags.chips if chip.full_text == "C")
                if isinstance(win.project_tags, QScrollArea):
                    win.project_tags.ensureWidgetVisible(chip)
                QTest.mouseClick(chip, Qt.LeftButton, modifiers)
                app.processEvents()
                result("chip修饰键仍单正向 " + str(modifiers), names_c)
                states("chip清除其它组合图标", {("tag", "C"): "include"})
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-tag-modifier-") as directory:
        run(Path(directory), t)
    raise SystemExit(0 if t.report() else 1)
