"""搜索补全关闭与内置清空按钮回归；纯控件和显式临时库，不读用户库。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget

from app.search import parse_search
from app.ui.search_completion import SearchBoxKeyFilter, SearchCompletionPopup
from app.ui.theme import apply_theme
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _window


def test_popup(t: T) -> None:
    """使用真实鼠标事件覆盖不获取焦点的空白区、候选及键盘路径。"""
    app = _app()
    host = QWidget()
    layout = QVBoxLayout(host)
    anchor, other = QLineEdit(), QLineEdit()
    blank = QLabel("不获取焦点的空白区域")
    layout.addWidget(anchor)
    layout.addSpacing(160)
    layout.addWidget(other)
    layout.addWidget(blank)
    popup = SearchCompletionPopup(host)
    key_filter = SearchBoxKeyFilter(popup, anchor, host)
    anchor.installEventFilter(key_filter)
    chosen: list[tuple[str, str]] = []
    popup.item_chosen.connect(lambda text, mode: (chosen.append((text, mode)), anchor.setText(text)))
    host.resize(480, 360)
    host.show()
    app.processEvents()

    def show() -> None:
        anchor.setFocus()
        popup.show_candidates(anchor, [("字段", [("标签", "tag:", "token"), ("扩展名", "ext:", "token")])])
        app.processEvents()

    try:
        show()
        QTest.mouseClick(blank, Qt.LeftButton)
        app.processEvents()
        t.assert_true("点击无焦点空白区域关闭补全", not popup.isVisible() and not popup.has_candidates())
        show()
        other.setFocus()
        app.processEvents()
        t.assert_true("焦点转到其他输入框关闭补全", not popup.isVisible() and not popup.has_candidates())
        show()
        anchor.clearFocus()
        app.processEvents()
        t.assert_true("主动移除搜索焦点关闭补全", not popup.has_candidates())
        show()
        QTest.mouseClick(anchor, Qt.LeftButton)
        app.processEvents()
        t.assert_true("点击搜索框保留补全", popup.has_candidates())
        QApplication.sendEvent(anchor, QFocusEvent(QEvent.FocusOut, Qt.PopupFocusReason))
        app.processEvents()
        t.assert_true("弹出补全引起的焦点变化不提前关闭", popup.has_candidates())
        point = popup.visualItemRect(popup.item(2)).center()
        QTest.mouseClick(popup.viewport(), Qt.LeftButton, pos=point)
        app.processEvents()
        t.assert_eq("鼠标候选填入正确文本", anchor.text(), "ext:")
        t.assert_eq("候选只发出一次选择信号", chosen, [("ext:", "token")])
        t.assert_true("选择候选后关闭补全", not popup.has_candidates())
        show()
        QTest.keyClick(anchor, Qt.Key_Down)
        QTest.keyClick(anchor, Qt.Key_Return)
        t.assert_eq("下方向键和回车仍可补全", chosen[-1], ("ext:", "token"))
        show()
        QTest.keyClick(anchor, Qt.Key_Up)
        QTest.keyClick(anchor, Qt.Key_Tab)
        t.assert_eq("上方向键跳过标题且Tab仍可补全", chosen[-1], ("ext:", "token"))
        show()
        QTest.keyClick(anchor, Qt.Key_Escape)
        t.assert_eq("第一次Esc保留输入", anchor.text(), "ext:")
        t.assert_true("第一次Esc关闭补全", not popup.has_candidates())
        QTest.keyClick(anchor, Qt.Key_Escape)
        t.assert_eq("第二次Esc清空输入", anchor.text(), "")
        show()
        # 绕开 Python hide 覆盖，模拟 Qt 原生弹窗自动隐藏。
        QWidget.hide(popup)
        t.assert_true("原生隐藏同步候选状态", not popup.has_candidates())
        show()
        QApplication.sendEvent(app, QEvent(QEvent.ApplicationDeactivate))
        t.assert_true("应用失去激活关闭补全", not popup.has_candidates())
    finally:
        popup.hide()
        host.close()
        host.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)


def test_clear_button(root: Path, t: T) -> None:
    """侧栏程序填入不能阻断 QLineEdit 自身的 clear action 状态更新。"""
    app = _app()
    win, repo = _window(root)
    try:
        def clear_button() -> QToolButton:
            return win.search_box.findChild(QToolButton)

        def settle() -> None:
            app.processEvents()
            QTest.qWait(200)
            # 清空按钮的动画和搜索防抖是独立定时器，等待搜索真正刷新。
            for _ in range(100):
                if not win._search_timer.isActive():
                    return
                QTest.qWait(10)
            raise AssertionError("等待搜索防抖定时器超时")

        with patch.object(win, "_refresh_filtered_projects", wraps=win._refresh_filtered_projects) as refresh:
            win._set_filter_query(parse_search("tag=测试标签 AND has:video").ast)
            settle()
            t.assert_eq("侧栏条件只刷新一次", refresh.call_count, 1)
            t.assert_true("侧栏填入后搜索清空按钮可见", clear_button().isVisible())
            t.assert_true("侧栏填入后不弹补全", not win._completion.has_candidates())
            t.assert_eq("组合查询选中左侧文件类型", win.file_type_filter.selected_kinds, {"video"})
        QTest.mouseClick(clear_button(), Qt.LeftButton)
        settle()
        t.assert_eq("点击清空按钮清空侧栏查询", win.search_box.text(), "")
        t.assert_eq("清空查询恢复全部标签", win.tag_tree.current_filter(), ("all", ""))
        t.assert_eq("清空查询移除文件筛选", win.file_type_filter.selected_kinds, set())
        t.assert_true("空输入隐藏清空按钮", not clear_button().isVisible())
        win._apply_search_query("has:video")
        win._completion.hide()
        settle()
        t.assert_true("恢复收藏查询仍显示清空按钮", clear_button().isVisible())
        win._set_filter_query(None)
        settle()
        t.assert_true("程序清空后隐藏按钮", not clear_button().isVisible())
        QTest.keyClicks(win.search_box, "ext:mp4")
        win._completion.hide()
        settle()
        t.assert_true("手动输入仍显示清空按钮", clear_button().isVisible())
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    # 使用实际主题，覆盖样式在构造早期回调 eventFilter 的时序。
    apply_theme(_app())
    checks = T()
    test_popup(checks)
    with tempfile.TemporaryDirectory(prefix="cabinet-search-popup-") as tmp:
        test_clear_button(Path(tmp), checks)
    raise SystemExit(0 if checks.report() else 1)
