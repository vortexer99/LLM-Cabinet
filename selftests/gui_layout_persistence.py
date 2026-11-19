"""主窗口布局持久化回归：真实显示、拖动、关闭和重开，仅使用显式临时库。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSplitter

from selftests._common import T
from app.db import connect
from app.library import Library
from app.repository import Repository
from app.ui.main_window import MainWindow


def _events() -> None:
    """处理显示与布局请求，避免只检查数据库值而漏掉实际控件布局。"""
    app = QApplication.instance()
    for _ in range(3):
        app.processEvents()


@contextmanager
def _window(root: Path) -> Iterator[tuple[MainWindow, Repository]]:
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "cabinet.db"
    repo = Repository(connect(db_path))
    win = MainWindow(repo, Library(root / "library"), db_path=db_path, library_root=root)
    try:
        win.show()
        _events()
        yield win, repo
    finally:
        win._mcp_timer.stop()
        win._search_timer.stop()
        win.close()
        win.deleteLater()
        QApplication.instance().sendPostedEvents(None, QEvent.DeferredDelete)
        _events()
        repo.conn.close()


def _seed(root: Path, settings: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    repo = Repository(connect(root / "cabinet.db"))
    try:
        for key, value in settings.items():
            repo.set_setting(key, value)
    finally:
        repo.conn.close()


def _sizes(win: MainWindow) -> tuple[list[int], list[int]]:
    return win._main_splitter.sizes(), win._right_v_split.sizes()


def _assert_sizes(t: T, label: str, win: MainWindow, expected: tuple[list[int], list[int]]) -> None:
    actual = _sizes(win)
    for name, got, want in zip(("左右", "上下"), actual, expected):
        t.assert_true(
            f"{label}：{name}实际尺寸",
            len(got) == len(want) and all(abs(a - b) <= 1 for a, b in zip(got, want)),
            f"expected={want}, actual={got}",
        )


def _drag(splitter: QSplitter, delta: int) -> None:
    """通过真实鼠标事件拖动句柄，验证 splitterMoved 信号与保存连接。"""
    handle = splitter.handle(1)
    start = handle.rect().center()
    offset = QPoint(delta, 0) if splitter.orientation() == Qt.Horizontal else QPoint(0, delta)
    end = start + offset
    QTest.mousePress(handle, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(handle, end, 10)
    QTest.mouseRelease(handle, Qt.LeftButton, Qt.NoModifier, end)
    _events()


def test_normal_reopen_and_drag(root: Path, t: T) -> None:
    with _window(root) as (win, repo):
        win.resize(1200, 780)
        win.move(50, 40)
        _events()
        before_drag = _sizes(win)
        _drag(win._main_splitter, 45)
        _drag(win._right_v_split, 105)
        dragged = _sizes(win)
        t.assert_true("鼠标拖动确实改变左右分栏", dragged[0] != before_drag[0])
        t.assert_true("鼠标拖动确实改变上下分栏", dragged[1] != before_drag[1])
        t.assert_eq("左右拖动在关闭前保存", json.loads(repo.get_setting(MainWindow.MAIN_SPLITTER_SETTING_KEY)), dragged[0])
        t.assert_eq("上下拖动在关闭前保存", json.loads(repo.get_setting(MainWindow.RIGHT_SPLITTER_SETTING_KEY)), dragged[1])

    with _window(root) as (win, repo):
        _assert_sizes(t, "鼠标拖动后重启", win, dragged)
        t.assert_eq("普通窗口尺寸恢复", win.size().toTuple(), (1200, 780))
        t.assert_true("普通窗口状态恢复", not win.isMaximized())
        # setSizes 不会发出 splitterMoved，覆盖关闭时补保存的路径。
        win._main_splitter.setSizes([230, 595, 373])
        win._right_v_split.setSizes([455, 275])
        _events()
        expected = _sizes(win)
        t.assert_true("关闭补保存覆盖不同左右布局", expected[0] != dragged[0])
        t.assert_true("关闭补保存覆盖不同上下布局", expected[1] != dragged[1])
        t.assert_eq("编程调整没有提前覆盖左右设置", json.loads(repo.get_setting(MainWindow.MAIN_SPLITTER_SETTING_KEY)), dragged[0])
        t.assert_eq("编程调整没有提前覆盖上下设置", json.loads(repo.get_setting(MainWindow.RIGHT_SPLITTER_SETTING_KEY)), dragged[1])

    for restart in range(3):
        with _window(root) as (win, _repo):
            _assert_sizes(t, f"关闭补保存及连续重启{restart + 1}", win, expected)
            t.assert_eq(f"连续重启{restart + 1}普通窗口尺寸", win.size().toTuple(), (1200, 780))


def test_maximized(root: Path, t: T) -> None:
    with _window(root) as (win, _repo):
        win.resize(1240, 810)
        win.move(80, 60)
        _events()
        normal_size = win.size().toTuple()
        win.showMaximized()
        _events()
        win._main_splitter.setSizes([275, 1080, 563])
        win._right_v_split.setSizes([560, 470])
        _events()
        expected = _sizes(win)
        maximized_size = win.size().toTuple()

    for restart in range(2):
        with _window(root) as (win, _repo):
            t.assert_true(f"重启{restart + 1}恢复最大化", win.isMaximized())
            t.assert_eq(f"重启{restart + 1}最大化尺寸", win.size().toTuple(), maximized_size)
            _assert_sizes(t, f"最大化重启{restart + 1}", win, expected)

    with _window(root) as (win, _repo):
        win.showNormal()
        _events()
        t.assert_eq("取消最大化恢复普通尺寸", win.size().toTuple(), normal_size)
        normal_splitters = _sizes(win)
    with _window(root) as (win, _repo):
        t.assert_true("取消最大化后重启保持普通状态", not win.isMaximized())
        t.assert_eq("取消最大化后重启保持普通尺寸", win.size().toTuple(), normal_size)
        _assert_sizes(t, "取消最大化后重启", win, normal_splitters)


def test_library_isolation(root: Path, t: T) -> None:
    expected: dict[str, tuple[list[int], list[int]]] = {}
    for name, main_sizes, right_sizes in (
        ("a", [200, 650, 448], [480, 280]),
        ("b", [320, 750, 228], [320, 440]),
    ):
        with _window(root / name) as (win, _repo):
            win.resize(1300, 810)
            _events()
            win._main_splitter.setSizes(main_sizes)
            win._right_v_split.setSizes(right_sizes)
            _events()
            expected[name] = _sizes(win)
    t.assert_true("两个临时库使用不同实际布局", expected["a"] != expected["b"])
    for name in ("a", "b", "a"):
        with _window(root / name) as (win, _repo):
            _assert_sizes(t, f"库{name}独立恢复", win, expected[name])


def test_invalid_settings(root: Path, t: T) -> None:
    with _window(root / "reference") as (win, _repo):
        default = _sizes(win)
    invalid = [
        "broken-json", "{}", "[]", "null", "[100]",
        "[true, 100, 100]", "[true, 100]", "[-1, 100, 100]", "[-1, 100]",
        "[0, 0]", "[0, 0, 0]", "[1.5, 100]", "[100, 100, 100.5]",
        "[2147483648, 100]", "[2147483648, 100, 100]",
    ]
    for i, raw in enumerate(invalid):
        location = root / str(i)
        _seed(location, {
            MainWindow.MAIN_SPLITTER_SETTING_KEY: raw,
            MainWindow.RIGHT_SPLITTER_SETTING_KEY: raw,
            MainWindow.WINDOW_GEOMETRY_SETTING_KEY: "invalid-geometry",
        })
        with _window(location) as (win, _repo):
            _assert_sizes(t, f"损坏设置{raw}回退默认", win, default)


def test_detach_and_collapsed(root: Path, t: T) -> None:
    with _window(root) as (win, repo):
        win.resize(1250, 820)
        _events()
        win._right_v_split.setSizes([485, 285])
        _events()
        expected = _sizes(win)
        win._detach_files_panel()
        _events()
        t.assert_true("文件面板已独立弹出", win._files_detach_window is not None)
        t.assert_eq("弹出保存原上下布局", json.loads(repo.get_setting(MainWindow.RIGHT_SPLITTER_SETTING_KEY)), expected[1])
        win._right_v_split.setSizes([350, 420])
        win._right_v_split.splitterMoved.emit(350, 1)
        _events()
        t.assert_eq("占位分栏不会覆盖上下布局", json.loads(repo.get_setting(MainWindow.RIGHT_SPLITTER_SETTING_KEY)), expected[1])
        win._attach_files_panel()
        _events()
        _assert_sizes(t, "收回文件面板", win, expected)
        win._detach_files_panel()
        _events()
        win._right_v_split.setSizes([350, 420])
        _events()
    with _window(root) as (win, _repo):
        _assert_sizes(t, "弹出期间关闭后重启", win, expected)
        win._right_v_split.setSizes([0, 770])
        _events()
        collapsed = _sizes(win)
        t.assert_eq("允许收起预览区", collapsed[1][0], 0)
    with _window(root) as (win, _repo):
        _assert_sizes(t, "收起预览区后重启", win, collapsed)
        win._right_v_split.setSizes([770, 0])
        _events()
        collapsed = _sizes(win)
        t.assert_eq("允许收起文件区", collapsed[1][1], 0)
    with _window(root) as (win, _repo):
        _assert_sizes(t, "收起文件区后重启", win, collapsed)


def main() -> bool:
    t = T()
    # Windows 盘符中的冒号会被 Qt 当作平台参数分隔符，配置文件需传相对路径。
    # 固定宽屏避免默认 800×800 虚拟屏幕把正常窗口和左右分栏夹到最小尺寸。
    with tempfile.TemporaryDirectory(prefix=".layout-selftest-", dir=Path.cwd()) as d:
        root = Path(d)
        config = root / "screen.json"
        config.write_text(json.dumps({"screens": [{"name": "layout-selftest", "x": 0, "y": 0, "width": 1920, "height": 1080}]}), encoding="utf-8")
        app = QApplication(["layout-selftest", "-platform", f"offscreen:configfile={config.relative_to(Path.cwd()).as_posix()}"])
        app.setQuitOnLastWindowClosed(False)
        t.assert_eq("使用隔离宽屏虚拟屏幕", app.primaryScreen().availableGeometry().size().toTuple(), (1920, 1080))
        for name, test in (
            ("普通窗口、真实拖动及连续重启", test_normal_reopen_and_drag),
            ("最大化及恢复普通窗口", test_maximized),
            ("不同库隔离", test_library_isolation),
            ("损坏设置回退", test_invalid_settings),
            ("弹出收回与折叠分栏", test_detach_and_collapsed),
        ):
            print(f"\n-> {name}", flush=True)
            test(root / name, t)
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
