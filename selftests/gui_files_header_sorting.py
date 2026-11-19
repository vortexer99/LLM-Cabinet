"""真实主窗口文件表头点击回归；仅使用显式临时数据库和自造文件。

运行：``.venv/Scripts/python.exe -B selftests/gui_files_header_sorting.py``。
排序必须由 QTest 鼠标点击触发，不能直接调用排序处理器或发射信号。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from app.db import connect
from app.models import FileItem, Project
from app.repository import Repository
from app.ui.file_sorting import RAW_NAME_ROLE
from app.ui.main_window import MainWindow
from selftests._common import T, closing_repos
from selftests.gui_main_window_regressions import _app, _close_window, _window


def _seed(tmp: Path) -> tuple[int, int]:
    """写入与自然名称排序不同的自定义顺序，类型顺序也故意不同。"""
    repo = Repository(connect(tmp / "cabinet.db"))
    with closing_repos(repo):
        pid = repo.save_project(Project(title="表头点击测试"))
        # 本用例验证从显式自定义顺序切换列排序；无偏好的默认排序另行覆盖。
        repo.set_project_setting(pid, MainWindow.FILES_TREE_SORT_SETTING_KEY,
                                 json.dumps({"key": "custom", "descending": False}))
        other_pid = repo.save_project(Project(title="用于切换的空项目"))
        for order, (name, kind) in enumerate((
            ("10.txt", "code"), ("2.txt", "other"), ("1.txt", "doc"),
        )):
            path = tmp / name
            path.write_text(f"测试文件 {name}", encoding="utf-8")
            repo.add_file(FileItem(project_id=pid, path=str(path), kind=kind, ord=order))
        return pid, other_pid


def _select(win: MainWindow, pid: int) -> None:
    win._select_project_by_id(pid)
    _app().processEvents()


def _names(win: MainWindow) -> list[str]:
    return [win.tbl_files.topLevelItem(i).data(0, RAW_NAME_ROLE)
            for i in range(win.tbl_files.topLevelItemCount())]


def _click(win: MainWindow, col: int, t: T, label: str) -> None:
    """点击可见列中央，经过 viewport 的真实鼠标事件链。"""
    header = win.tbl_files.header()
    # 使类型列在窄文件面板也可见，避免把测试错误归因于表头被遮挡。
    win.tbl_files.horizontalScrollBar().setValue(0)
    if col:
        win.tbl_files.horizontalScrollBar().setValue(header.sectionPosition(col))
    _app().processEvents()
    x = header.sectionViewportPosition(col) + header.sectionSize(col) // 2
    point = QPoint(x, header.height() // 2)
    t.assert_true(f"{label}点击坐标可见", header.viewport().rect().contains(point))
    QTest.mouseClick(header.viewport(), Qt.LeftButton, Qt.NoModifier, point)
    _app().processEvents()


def test_real_header_clicks(tmp: Path, t: T) -> None:
    pid, other_pid = _seed(tmp)
    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            _select(win, pid)
            t.assert_eq("首次打开为树形", win._files_view_mode, "tree")
            t.assert_eq("未排序时保留自定义顺序", _names(win), ["10.txt", "2.txt", "1.txt"])
            _click(win, 0, t, "首次文件名")
            t.assert_eq("首次树形真实点击文件名自然升序", _names(win), ["1.txt", "2.txt", "10.txt"])
            t.assert_eq("首次点击保存名称升序", win._tree_sort_state(), ("name", False))
            _click(win, 0, t, "第二次文件名")
            t.assert_eq("第二次真实点击文件名降序", _names(win), ["10.txt", "2.txt", "1.txt"])
            t.assert_eq("第二次点击保存名称降序", win._tree_sort_state(), ("name", True))

            _select(win, other_pid)
            _select(win, pid)
            t.assert_eq("重新选择项目保留降序偏好", win._tree_sort_state(), ("name", True))
            t.assert_eq("重新选择项目保留降序结果", _names(win), ["10.txt", "2.txt", "1.txt"])
            _click(win, 0, t, "重选后文件名")
            t.assert_eq("重新选择后仍能点击切换升序", _names(win), ["1.txt", "2.txt", "10.txt"])

            QTest.mouseClick(win._btn_view_mode, Qt.LeftButton)
            _app().processEvents()
            t.assert_eq("视图按钮进入平铺", win._files_view_mode, "flat")
            _click(win, 0, t, "平铺文件名")
            t.assert_eq("平铺真实点击切到降序", _names(win), ["10.txt", "2.txt", "1.txt"])
            t.assert_eq("平铺点击保存列偏好", repo.get_project_setting(pid, "files_flat_sort_col", ""), "0")
            t.assert_eq("平铺降序保存数值偏好", repo.get_project_setting(pid, "files_flat_sort_order", ""), "1")
            QTest.mouseClick(win._btn_view_mode, Qt.LeftButton)
            _app().processEvents()
            t.assert_eq("视图按钮返回树形", win._files_view_mode, "tree")
            t.assert_eq("返回树形恢复独立升序偏好", _names(win), ["1.txt", "2.txt", "10.txt"])
            _click(win, 0, t, "返回树形文件名")
            t.assert_eq("平铺返回树形后仍可点击降序", win._tree_sort_state(), ("name", True))
            t.assert_eq("平铺返回树形点击降序结果", _names(win), ["10.txt", "2.txt", "1.txt"])

            _click(win, 2, t, "类型列")
            t.assert_eq("类型列真实点击保存升序", win._tree_sort_state(), ("kind", False))
            t.assert_eq("类型列真实点击改变顺序", _names(win), ["10.txt", "1.txt", "2.txt"])
            saved = json.loads(repo.get_project_setting(pid, win.FILES_TREE_SORT_SETTING_KEY, "{}"))
            t.assert_eq("类型排序偏好实际落库", saved, {"key": "kind", "descending": False})
            _select(win, other_pid)
            _select(win, pid)
            t.assert_eq("重选项目恢复类型排序", _names(win), ["10.txt", "1.txt", "2.txt"])

            QTest.mouseClick(win._btn_view_mode, Qt.LeftButton)
            _app().processEvents()
            t.assert_eq("再次进入平铺恢复名称降序", _names(win), ["10.txt", "2.txt", "1.txt"])
            _click(win, 0, t, "再次平铺文件名")
            t.assert_eq("平铺再次点击恢复升序", _names(win), ["1.txt", "2.txt", "10.txt"])
            t.assert_eq("平铺升序保存数值偏好", repo.get_project_setting(pid, "files_flat_sort_order", ""), "0")

            # 模拟旧版 Qt 枚举字符串设置，验证真实视图加载时兼容且仍可点击。
            QTest.mouseClick(win._btn_view_mode, Qt.LeftButton)
            _app().processEvents()
            repo.set_project_setting(pid, "files_flat_sort_order", "SortOrder.DescendingOrder")
            QTest.mouseClick(win._btn_view_mode, Qt.LeftButton)
            _app().processEvents()
            t.assert_eq("平铺兼容旧枚举字符串降序偏好", _names(win), ["10.txt", "2.txt", "1.txt"])
            _click(win, 0, t, "旧偏好加载后文件名")
            t.assert_eq("加载旧偏好后真实点击仍响应", _names(win), ["1.txt", "2.txt", "10.txt"])
            t.assert_eq("旧偏好点击后保存为数值", repo.get_project_setting(pid, "files_flat_sort_order", ""), "0")
        finally:
            _close_window(win)


if __name__ == "__main__":
    app = _app()
    t = T()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        test_real_header_clicks(Path(directory), t)
    sys.exit(0 if t.report() else 1)
