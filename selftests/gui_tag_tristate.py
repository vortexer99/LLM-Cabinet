"""侧栏标签真实点击三态回归；仅连接显式临时库并构造合成文件。"""
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
from PySide6.QtWidgets import QMenu, QScrollArea

from app.models import FileItem, Project
from app.search import NotNode, TermNode, parse_search
from app.search_filters import tag_selection
from app.search_history import SAVED_SEARCHES_SETTING_KEY, load_saved_searches
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _window


def run(root: Path, t: T) -> None:
    """所有轮切都走真实鼠标或键盘事件，查询结果与独立预期交叉验证。"""
    app = _app()
    win, repo = _window(root)
    try:
        win.resize(1280, 820)
        tree = win.tag_tree
        tree.setAnimated(False)
        special = ['带 空格', '引"号', r'反\斜杠', '括号(1):= AND OR', 'NOT']
        a = repo.save_project(Project(title="alpha 甲", tags=["A", *special]))
        b = repo.save_project(Project(title="alpha 乙", tags=["B"]))
        c = repo.save_project(Project(title="alpha 子标签", tags=["A/子标签"]))
        d = repo.save_project(Project(title="alpha 无标签"))
        e = repo.save_project(Project(title="beta 另一个A", tags=["A"]))
        all_ids = {a, b, c, d, e}
        for pid, kind in ((a, "video"), (b, "video"), (c, "video"),
                          (d, "video"), (e, "archive")):
            path = root / f"synthetic-{pid}.txt"
            path.write_text("临时自测占位文件", encoding="utf-8")
            repo.add_file(FileItem(project_id=pid, path=str(path.resolve()),
                                   kind=kind, is_relative=False))
        repo.set_projects_favorite([a, b], True)
        # 合成视频只用于筛选，不交给媒体解码器。
        with patch.object(win.preview, "show_file"):
            win.refresh_projects()
            app.processEvents()

            def ids() -> set[int]:
                return {win.proj_model.project_at(row).id for row in range(win.proj_model.rowCount())}

            def execute(query: str) -> None:
                win.search_box.setText(query)
                win._search_timer.stop()
                win._completion.hide()
                win.refresh_projects(filter_only=True)
                app.processEvents()

            def item(kind: str, value: str = ""):
                found = tree._find_item_by_kind_value(kind, value)
                assert found is not None, (kind, value)
                return found

            def position(kind: str, value: str = "") -> QPoint:
                node = item(kind, value)
                parent = node.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                tree.scrollToItem(node)
                app.processEvents()
                return tree.visualItemRect(node).center()

            def click(kind: str, value: str = "", button=Qt.LeftButton) -> None:
                QTest.mouseClick(tree.viewport(), button, pos=position(kind, value))
                app.processEvents()

            def icon_image(kind: str, value: str):
                icon = item(kind, value).icon(0)
                t.assert_true(f"{kind} {value} 状态图标非空", not icon.isNull())
                image = icon.pixmap(16, 16).toImage()
                t.assert_true(f"{kind} {value} 状态图标可栅格化", not image.isNull())
                return image

            def state(label: str, kind: str, value: str, expected: set[int]) -> None:
                parsed = parse_search(win.search_box.text())
                t.assert_true(label + " 查询有效", parsed.ok, repr(parsed.error))
                t.assert_eq(label + " 结果", ids(), expected)
                t.assert_eq(label + " 侧栏状态", tree.current_filter(), (kind, value))
                t.assert_eq(label + " 查询投影", tag_selection(parsed.ast), (kind, value))
                t.assert_eq(label + " 仅按完整文本查询", ids(),
                            {project.id for project in repo.list_projects_query(parsed.ast)})

            execute("")
            for name in ("A", "A/子标签", "B", *special):
                icon_image("tag", name)
            exact_icons = [icon_image("tag", "A")]
            for cycle, kind, expected in ((1, "tag", {a, e}), (2, "not_tag", {b, c, d}),
                                          (3, "all", all_ids), (4, "tag", {a, e})):
                with patch.object(win, "refresh_projects", wraps=win.refresh_projects) as refresh:
                    click("tag", "A")
                    QTest.qWait(230)
                    t.assert_eq(f"第{cycle}次真实点击只刷新一次", refresh.call_count, 1)
                state(f"第{cycle}次真实点击", kind, "" if kind == "all" else "A", expected)
                t.assert_true("轮切不遗留搜索定时器", not win._search_timer.isActive())
                exact_icons.append(icon_image("tag", "A"))
            t.assert_true("标签包含与不包含图标像素不同", exact_icons[1] != exact_icons[2])
            t.assert_true("标签无筛选与包含图标像素不同", exact_icons[0] != exact_icons[1])
            t.assert_true("标签无筛选与不包含图标像素不同", exact_icons[0] != exact_icons[2])
            t.assert_eq("标签第三次恢复无筛选图标", exact_icons[3], exact_icons[0])
            t.assert_eq("标签第四次恢复包含图标", exact_icons[4], exact_icons[1])
            click("tag", "A")
            parsed = parse_search(win.search_box.text())
            t.assert_eq("精确排除序列化为NOT单个精确标签", parsed.ast,
                        NotNode(TermNode("tag", "=", "A")))
            t.assert_true("排除包含无标签项目", d in ids())
            click("tag", "B")
            state("切换不同标签重新包含", "tag", "B", {b})
            t.assert_eq("切换标签恢复旧标签无筛选图标", icon_image("tag", "A"), exact_icons[0])

            execute('alpha AND has:"video"')
            click("tag", "A")
            state("保留关键词文件条件包含", "tag", "A", {a})
            click("tag", "A")
            state("保留关键词文件条件排除", "not_tag", "A", {b, c, d})
            click("tag", "A")
            state("取消只移除标签条件", "all", "", {a, b, c, d})
            t.assert_eq("取消标签仍保留文件筛选", win.file_type_filter.selected_kinds, {"video"})

            execute("")
            prefix_neutral = icon_image("tag_prefix", "A")
            click("tag_prefix", "A")
            state("前缀包含自身和子标签", "tag_prefix", "A", {a, c, e})
            prefix_included = icon_image("tag_prefix", "A")
            click("tag_prefix", "A")
            state("前缀排除自身和子标签", "not_tag_prefix", "A", {b, d})
            prefix_excluded = icon_image("tag_prefix", "A")
            t.assert_true("前缀三态图标像素互不相同", prefix_neutral != prefix_included
                          and prefix_neutral != prefix_excluded and prefix_included != prefix_excluded)
            parsed = parse_search(win.search_box.text())
            t.assert_eq("前缀排除序列化为NOT前缀", parsed.ast,
                        NotNode(TermNode("tag_prefix", ":", "A")))
            before = win.search_box.text()
            # 点击展开箭头不能被当成前缀标签激活。
            node = item("tag_prefix", "A")
            rect = tree.visualItemRect(node)
            QTest.mouseClick(tree.viewport(), Qt.LeftButton,
                             pos=QPoint(rect.left() - tree.indentation() // 2, rect.center().y()))
            app.processEvents()
            t.assert_true("真实折叠箭头收起子标签", not node.isExpanded())
            t.assert_eq("折叠不轮切查询", win.search_box.text(), before)
            with patch.object(QMenu, "exec", return_value=None):
                click("tag", "B", Qt.RightButton)
            t.assert_eq("右键不同标签不轮切查询", win.search_box.text(), before)
            t.assert_eq("右键不改变生效排除状态", tree.current_filter(), ("not_tag_prefix", "A"))
            click("tag_prefix", "A")
            state("前缀第三次取消", "all", "", all_ids)
            t.assert_eq("前缀取消恢复无筛选图标", icon_image("tag_prefix", "A"), prefix_neutral)

            for name in special:
                execute("")
                click("tag", name)
                state(f"特殊字符包含 {name}", "tag", name, {a})
                click("tag", name)
                state(f"特殊字符排除 {name}", "not_tag", name, all_ids - {a})
                t.assert_eq(f"特殊字符NOT原样往返 {name}", parse_search(win.search_box.text()).ast,
                            NotNode(TermNode("tag", "=", name)))
                click("tag", name)
                state(f"特殊字符取消 {name}", "all", "", all_ids)

            execute('NOT (tag="A")')
            state("手动NOT反向标记", "not_tag", "A", {b, c, d})
            t.assert_eq("手动NOT恢复排除图标", icon_image("tag", "A"), exact_icons[2])
            exclusion_hint = item("tag", "A").text(0) + item("tag", "A").toolTip(0)
            t.assert_true("排除节点有可识别文字标记", any(word in exclusion_hint for word in ("排除", "不包含")))
            win.refresh_projects()
            app.processEvents()
            state("完整刷新保持NOT", "not_tag", "A", {b, c, d})
            t.assert_eq("完整刷新保持排除图标", icon_image("tag", "A"), exact_icons[2])
            click("tag", "A")
            state("手动NOT刷新后继续取消", "all", "", all_ids)

            execute('NOT (tag_prefix:"A")')
            state("手动前缀NOT反向标记", "not_tag_prefix", "A", {b, d})
            click("tag_prefix", "A")
            state("手动前缀NOT继续取消", "all", "", all_ids)

            # 复杂 NOT 的内部条件不能被当成可替换的侧栏单条件。
            for query in ('NOT (tag="A" AND title="alpha 甲")', 'NOT (tag="A" OR tag="B")',
                          'NOT tag:"A"'):
                execute(query)
                original_ast = parse_search(query).ast
                original_ids = ids()
                click("tag", "B")
                t.assert_eq("复杂NOT追加标签保持原逻辑 " + query, ids(), original_ids & {b})
                click("tag", "B")
                t.assert_eq("复杂NOT排除标签保持原逻辑 " + query, ids(), original_ids - {b})
                click("tag", "B")
                t.assert_eq("复杂NOT轮切取消恢复完整AST " + query,
                            parse_search(win.search_box.text()).ast, original_ast)
                t.assert_eq("复杂NOT轮切取消恢复结果 " + query, ids(), original_ids)

            execute("")
            click("tag", "B")
            tree.setFocus()
            for key, kind, value, expected in ((Qt.Key_Return, "not_tag", "B", all_ids - {b}),
                                                (Qt.Key_Space, "all", "", all_ids),
                                                (Qt.Key_Return, "tag", "B", {b}),
                                                (Qt.Key_Enter, "not_tag", "B", all_ids - {b}),
                                                (Qt.Key_Space, "all", "", all_ids)):
                with patch.object(win, "refresh_projects", wraps=win.refresh_projects) as refresh:
                    QTest.keyClick(tree, key)
                    QTest.qWait(230)
                    t.assert_eq("键盘激活只刷新一次 " + str(key), refresh.call_count, 1)
                state("键盘轮切 " + str(key), kind, value, expected)

            execute('NOT (tag="B")')
            last_ids = ids()
            win.search_box.setText('tag="未闭合')
            win._search_timer.stop()
            win._completion.hide()
            for _ in range(2):
                click("tag", "B")
                t.assert_eq("无效搜索不改写文本", win.search_box.text(), 'tag="未闭合')
                t.assert_eq("无效搜索不推进排除状态", tree.current_filter(), ("not_tag", "B"))
                t.assert_eq("无效搜索保留已执行结果", ids(), last_ids)
            execute('NOT (tag="B")')
            click("tag", "B")
            state("修正无效文本后仍从排除到取消", "all", "", all_ids)

            execute('alpha AND NOT (tag="B")')
            saved_query = win.search_box.text()
            with patch("app.ui.mw_search.QInputDialog.getText", return_value=("三态排除", True)):
                win._save_current_search()
            saved = load_saved_searches(repo.get_setting(SAVED_SEARCHES_SETTING_KEY, "[]"))
            t.assert_true("保存完整NOT查询", any(record["query"] == saved_query for record in saved))
            execute('tag="B"')
            click("saved_search", "三态排除")
            t.assert_eq("保存搜索恢复NOT完整表达式", parse_search(win.search_box.text()).ast,
                        parse_search(saved_query).ast)
            click("tag", "B")
            state("收藏恢复后标签继续取消排除", "all", "", {a, b, c, d})

            execute('NOT (tag="A") AND has:"video"')
            QTest.mouseClick(win.btn_search_all, Qt.LeftButton)
            app.processEvents()
            state("全库清空恢复所有项目", "all", "", all_ids)
            t.assert_eq("全库清空文件条件", win.file_type_filter.selected_kinds, set())
            click("favorite")
            first = win.search_box.text()
            click("favorite")
            t.assert_eq("其它状态节点重复点击不轮切", win.search_box.text(), first)
            t.assert_eq("收藏项目筛选保持原义", ids(), {a, b})

            # 简介 chip 始终是明确的正向筛选入口；同标签连点仍保持包含。
            execute("")
            win._select_project_by_id(a)
            app.processEvents()
            for _ in range(3):
                chip = next(chip for chip in win.project_tags.chips if chip.full_text == "A")
                if isinstance(win.project_tags, QScrollArea):
                    win.project_tags.ensureWidgetVisible(chip)
                QTest.mouseClick(chip, Qt.LeftButton)
                app.processEvents()
                state("简介chip重复点击仍正向", "tag", "A", {a, e})
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-tag-tristate-") as directory:
        run(Path(directory), t)
    raise SystemExit(0 if t.report() else 1)
