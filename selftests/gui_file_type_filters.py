"""文件类型筛选 GUI 自测：只用显式临时库和登记记录，不读取用户库或媒体。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QCheckBox, QComboBox, QMenu, QPushButton, QTreeWidget

from app.db import connect
from app.models import FileItem, Project
from app.repository import Repository
from app.search import AndNode, OrNode, TermNode
from selftests._common import T, closing_repos
from selftests.gui_main_window_regressions import _app, _close_window, _titles, _window


VIDEO = {"dawn 视频", "dawn 双类型", "night 旧视频", "dawn 其他标签双类型"}
ARCHIVE = {"dawn 压缩", "dawn 双类型", "night 旧压缩", "dawn 其他标签双类型"}
BOTH = VIDEO & ARCHIVE
EXTERNAL = {"dawn 视频", "dawn 双类型", "night 旧压缩", "dawn 普通"}
ALL = VIDEO | ARCHIVE | {"dawn 普通", "dawn 空项目"}


def _seed(tmp: Path) -> None:
    """只登记不存在的临时路径；缺失标记也不影响“拥有类型”的语义。"""
    repo = Repository(connect(tmp / "cabinet.db"))
    with closing_repos(repo):
        url_field = repo.add_field("参考链接", "url")
        fixtures = [
            ("dawn 视频", "保留", [("video", "video.mp4")]),
            ("dawn 压缩", "保留", [("archive", "archive.zip")]),
            ("dawn 双类型", "保留", [("video", "both.mkv"), ("archive", "both.7z")]),
            ("night 旧视频", "保留", [("other", "legacy.MP4")]),
            ("night 旧压缩", "保留", [("other", "legacy.ZIP")]),
            ("dawn 其他标签双类型", "其他", [("video", "other.mp4"), ("archive", "other.zip")]),
            ("dawn 普通", "保留", [("pdf", "plain.pdf")]),
            ("dawn 空项目", "", []),
        ]
        for title, tag, files in fixtures:
            pid = repo.save_project(Project(
                title=title, tags=[tag] if tag else [],
                storage_mode="storage" if title in {"dawn 视频", "dawn 双类型"} else "link",
                field_values={url_field: "https://example.com/reference"} if title == "dawn 空项目" else {},
            ))
            for kind, name in files:
                # 双类型项目的视频在库中，压缩包在库外，验证条件可跨文件满足。
                external = title in EXTERNAL and not (title == "dawn 双类型" and kind == "video")
                fid = repo.add_file(FileItem(
                    project_id=pid, path=str(tmp / "missing-media" / title / name),
                    kind=kind, is_relative=not external,
                ))
                repo.set_file_missing(fid, True)


def _click(widget) -> None:
    """点击复选框左侧指示器，避免布局较宽时中心落在可点击文字之外。"""
    QTest.mouseClick(widget, Qt.LeftButton, pos=QPoint(8, widget.height() // 2))
    _app().processEvents()


def _click_kind(controls, kind: str) -> None:
    """真实点击树形条目，验证无需 Ctrl 的多选与再次点击取消。"""
    point = controls.visualItemRect(controls.items[kind]).center()
    assert controls.viewport().rect().contains(point)
    QTest.mouseClick(controls.viewport(), Qt.LeftButton, pos=point)
    _app().processEvents()


def _menu_action(controls, key: str | None = None) -> dict[str, bool]:
    """打开实际菜单构建路径，拦截 exec 防阻塞，触发指定动作。"""
    enabled = {}
    names = {"any": "满足任一条件", "all": "同时满足全部条件", "clear": "清除文件筛选"}
    def factory(parent):
        menu = QMenu(parent)
        def execute(*_args):
            actions = {action.text(): action for action in menu.actions()}
            enabled.update({k: actions[label].isEnabled() for k, label in names.items()})
            if key is not None:
                actions[names[key]].trigger()
        menu.exec = execute
        return menu
    with patch("app.ui.file_type_filter.QMenu", side_effect=factory):
        controls.customContextMenuRequested.emit(QPoint(10, 10))
    _app().processEvents()
    return enabled


def _query(win, text: str) -> None:
    """经真实搜索框输入；同步执行以免依赖防抖定时器的调度速度。"""
    win._completion.hide()
    win.search_box.clear()
    if text:
        QTest.keyClicks(win.search_box, text)
    win._search_timer.stop()
    win._completion.hide()
    win.refresh_projects()
    _app().processEvents()


def _select_filter(win, kind: str, value: str = "") -> None:
    tree = win.tag_tree
    if kind == "tag":
        item = tree._find_tag_item(value)
    else:
        item = tree.topLevelItem(0)
    if item is None:
        raise RuntimeError(f"临时库缺少筛选节点：{kind}/{value}")
    tree.setCurrentItem(item)
    _app().processEvents()


def test_controls(tmp: Path, t: T) -> None:
    """真实控件的零选、单选、双选、模式切换与清除都应立即更新列表。"""
    _seed(tmp)
    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            controls = win.file_type_filter
            changes = QSignalSpy(controls.changed)
            t.assert_eq("无类型选择恢复全部项目", _titles(win), ALL)
            t.assert_eq("无选择不生成搜索节点", controls.search_node(), None)
            t.assert_eq("无选择摘要为空", controls.summary(), "")
            t.assert_eq("默认满足任一", controls.match_mode, "any")
            t.assert_true("复用标签树控件样式", isinstance(controls, QTreeWidget) and controls.objectName() == win.tag_tree.objectName())
            t.assert_true("不再显示复选框和下拉按钮", not any(controls.findChildren(cls) for cls in (QCheckBox, QComboBox, QPushButton)))
            t.assert_true("类型筛选在标签树上方", controls.mapTo(win, QPoint()).y() < win.tag_tree.mapTo(win, QPoint()).y())

            _click_kind(controls, "video")
            t.assert_eq("点击视频立即筛选含旧记录", _titles(win), VIDEO)
            t.assert_true("单选生成 TermNode", isinstance(controls.search_node(), TermNode))
            t.assert_in("视频摘要出现在标题", controls.summary(), win.lbl_filter_title.text())

            _click_kind(controls, "archive")
            t.assert_eq("双选默认 OR", _titles(win), VIDEO | ARCHIVE)
            t.assert_true("任一生成 OrNode", isinstance(controls.search_node(), OrNode))
            t.assert_in("双选摘要出现在标题", controls.summary(), win.lbl_filter_title.text())

            _menu_action(controls, "all")
            _app().processEvents()
            t.assert_eq("右键切换同时满足", controls.match_mode, "all")
            t.assert_in("组标题说明组合方式", "全部", controls.group_item.text(0))
            t.assert_eq("双选同时满足为交集", _titles(win), BOTH)
            t.assert_true("同时满足生成 AndNode", isinstance(controls.search_node(), AndNode))
            t.assert_in("交集模式摘要出现在标题", controls.summary(), win.lbl_filter_title.text())

            _click_kind(controls, "video")
            t.assert_eq("同时满足模式单选压缩", _titles(win), ARCHIVE)
            _click_kind(controls, "archive")
            t.assert_eq("逐个取消全部选择恢复全部", _titles(win), ALL)
            t.assert_eq("空选择不残留搜索节点", controls.search_node(), None)

            _click_kind(controls, "video")
            _click_kind(controls, "archive")
            _menu_action(controls, "clear")
            t.assert_true("右键清除全部选择", not controls.selected_kinds)
            t.assert_eq("清除按钮恢复全部项目", _titles(win), ALL)
            t.assert_eq("清除按钮移除摘要", controls.summary(), "")
            t.assert_true("控件交互发出 changed", changes.count() >= 8)
        finally:
            _close_window(win)


def test_combined_filters(tmp: Path, t: T) -> None:
    """侧栏写入完整查询；手动输入替换查询，全库按钮一次清空全部条件。"""
    _seed(tmp)
    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            controls = win.file_type_filter
            _query(win, "dawn")
            _click_kind(controls, "video")
            _select_filter(win, "tag", "保留")
            t.assert_eq("关键词与标签类型共同 AND", _titles(win), {"dawn 视频", "dawn 双类型"})
            summary = controls.summary()
            t.assert_in("叠加筛选标题保留标签", "保留", win.lbl_filter_title.text())
            t.assert_in("叠加筛选标题保留类型", summary, win.lbl_filter_title.text())

            _click(win.btn_search_all)
            t.assert_eq("全库清空全部查询条件", _titles(win), ALL)
            t.assert_eq("全库清空搜索框", win.search_box.text(), "")
            t.assert_true("全库取消类型选择", not controls.selected_kinds)
            t.assert_true("全库标题不误称类型生效", summary not in win.lbl_filter_title.text())
            t.assert_true("全库标题不误称标签生效", "保留" not in win.lbl_filter_title.text())

            _click(win.btn_search_all)
            t.assert_eq("再次全库不恢复旧条件", _titles(win), ALL)
            _query(win, "dawn")
            _select_filter(win, "tag", "保留")
            _click_kind(controls, "video")
            _menu_action(controls, "clear")
            t.assert_eq("清除类型保留关键词和标签", _titles(win), {"dawn 视频", "dawn 压缩", "dawn 双类型", "dawn 普通"})
            t.assert_true("清除类型保留完整搜索文本", "dawn" in win.search_box.text() and "tag" in win.search_box.text())
            t.assert_eq("清除类型保留标签", win._current_filter_value, "保留")
            _query(win, "dawn")
            t.assert_eq("手动输入不残留侧栏条件", _titles(win), {title for title in ALL if "dawn" in title})
            _select_filter(win, "all")
            _query(win, "")
            t.assert_eq("取消全部筛选恢复完整项目列表", _titles(win), ALL)
        finally:
            _close_window(win)


def test_external_controls(tmp: Path, t: T) -> None:
    """外部链接支持单选、任一、全部和三选；清除只刷新一次。"""
    _seed(tmp)
    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            controls = win.file_type_filter
            t.assert_eq("文件分组与标签分组一致", controls.group_item.text(0), "文件筛选")
            t.assert_true("无选择禁用模式", not _menu_action(controls)["all"])
            _click_kind(controls, "external")
            t.assert_eq("外部链接单选按登记筛选", _titles(win), EXTERNAL)
            t.assert_eq("单选外部链接生成 has 条件", controls.search_node(), TermNode("has", ":", "external"))
            t.assert_true("单选外部链接禁用模式", not _menu_action(controls)["all"])
            t.assert_in("外部链接摘要", "外部链接", controls.summary())
            t.assert_in("外部链接摘要进入标题", controls.summary(), win.lbl_filter_title.text())

            _click_kind(controls, "video")
            t.assert_true("两项选择启用模式", _menu_action(controls)["all"])
            t.assert_eq("视频和外部链接任一", _titles(win), VIDEO | EXTERNAL)
            t.assert_true("外部链接任一生成 OR", isinstance(controls.search_node(), OrNode))
            _menu_action(controls, "all")
            _app().processEvents()
            t.assert_eq("视频和外部链接全部可跨文件", _titles(win), VIDEO & EXTERNAL)
            t.assert_true("外部链接全部生成 AND", isinstance(controls.search_node(), AndNode))

            _click_kind(controls, "archive")
            t.assert_eq("三选全部为三项交集", _titles(win), VIDEO & ARCHIVE & EXTERNAL)
            t.assert_true("三选摘要包含三个条件", all(word in controls.summary() for word in ("视频", "压缩包", "外部链接")))
            _menu_action(controls, "any")
            _app().processEvents()
            t.assert_eq("三选任一为三项并集", _titles(win), VIDEO | ARCHIVE | EXTERNAL)

            changes = QSignalSpy(controls.changed)
            _menu_action(controls, "clear")
            t.assert_true("清除三项高亮", not controls.selected_kinds)
            t.assert_eq("三选清除只发一次刷新", changes.count(), 1)
            t.assert_eq("三选清除恢复全部", _titles(win), ALL)
            t.assert_eq("三选清除移除条件", controls.search_node(), None)
            t.assert_eq("三选清除移除摘要", controls.summary(), "")
            t.assert_true("清除后禁用模式及清除菜单", not any(_menu_action(controls).values()))
            _click_kind(controls, "video")
            controls.setFocus()
            QTest.keyClick(controls, Qt.Key_Space)
            t.assert_eq("空格切换当前条目", controls.selected_kinds, set())
            QTest.keyClick(controls, Qt.Key_Space)
            t.assert_eq("空格再次选中", controls.selected_kinds, {"video"})
            QTest.keyClick(controls, Qt.Key_Escape)
            t.assert_eq("Esc清除文件筛选", controls.selected_kinds, set())
            expanded_height = controls.height()
            controls.group_item.setExpanded(False)
            t.assert_true("折叠释放侧栏空间", controls.height() < expanded_height)
            controls.group_item.setExpanded(True)
            t.assert_eq("展开恢复条目高度", controls.height(), expanded_height)
        finally:
            _close_window(win)


def test_search_syntax_and_completion(tmp: Path, t: T) -> None:
    """经 GUI 搜索语法和补全路径使用 has，包含旧 other 和缺失登记记录。"""
    _seed(tmp)
    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            for query, expected in [
                ("has:video", VIDEO),
                ("has:archive", ARCHIVE),
                ("has:external", EXTERNAL),
                ("HAS=EXTERNAL", EXTERNAL),
                ("has:video AND has:external", VIDEO & EXTERNAL),
                ("has:archive OR has:external", ARCHIVE | EXTERNAL),
                ("NOT has:external", ALL - EXTERNAL),
                ("has:video OR has:archive", VIDEO | ARCHIVE),
                ("has:video AND has:archive", BOTH),
                ("NOT has:video", ALL - VIDEO),
                ("has:video AND NOT has:archive", VIDEO - ARCHIVE),
                ("has:unknown", set()),
            ]:
                _query(win, query)
                t.assert_eq(f"GUI 搜索 {query}", _titles(win), expected)
                t.assert_eq(f"搜索语法有效 {query}", win.search_box.toolTip(), "")

            _query(win, "")
            for prefix, expected in [("ha", {"has:video", "has:archive", "has:external"}), ("has:", {"has:video", "has:archive", "has:external"}), ("has:v", {"has:video"}), ("has:e", {"has:external"})]:
                win.search_box.clear()
                QTest.keyClicks(win.search_box, prefix)
                win._search_timer.stop()
                _app().processEvents()
                candidates = {
                    win._completion.item(i).data(Qt.UserRole)
                    for i in range(win._completion.count())
                    if win._completion.item(i).data(Qt.UserRole)
                }
                t.assert_true(f"补全 {prefix} 提供内置 has", expected.issubset(candidates), repr(candidates))
                win._completion.hide()

            win.search_box.clear()
            QTest.keyClicks(win.search_box, "has:")
            win._search_timer.stop()
            for i in range(win._completion.count()):
                if win._completion.item(i).data(Qt.UserRole) == "has:archive":
                    win._completion.setCurrentRow(i)
                    break
            QTest.keyClick(win.search_box, Qt.Key_Tab)
            win._search_timer.stop()
            t.assert_eq("Tab 应用压缩类型补全", win.search_box.text(), "has:archive")
            win.refresh_projects()
            t.assert_eq("应用补全后命中压缩项目", _titles(win), ARCHIVE)

            win.file_type_filter.set_match_mode("any")
            _click_kind(win.file_type_filter, "video")
            t.assert_eq("显式has查询投影到侧栏后添加类型使用任一", _titles(win), VIDEO | ARCHIVE)
            _menu_action(win.file_type_filter, "clear")
            win.search_box.clear()
            QTest.keyClicks(win.search_box, "has:e")
            win._search_timer.stop()
            for i in range(win._completion.count()):
                if win._completion.item(i).data(Qt.UserRole) == "has:external":
                    win._completion.setCurrentRow(i)
                    break
            QTest.keyClick(win.search_box, Qt.Key_Tab)
            win._search_timer.stop()
            t.assert_eq("Tab 应用外部链接补全", win.search_box.text(), "has:external")
            win.refresh_projects()
            t.assert_eq("外部链接补全筛选结果", _titles(win), EXTERNAL)
        finally:
            _close_window(win)


def main() -> bool:
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-file-type-gui-", ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        for name, test in [
            ("controls", test_controls),
            ("combined", test_combined_filters),
            ("external_controls", test_external_controls),
            ("syntax_completion", test_search_syntax_and_completion),
        ]:
            print(f"\n-> {name}")
            test(root / name, t)
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
