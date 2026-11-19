"""单击字段编辑：真实鼠标事件、显式临时库和合成项目。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from app.db import connect
from app.models import Project
from app.repository import Repository
from selftests.gui_main_window_regressions import _app, _close_window, _window


def run(root: Path) -> None:
    win, repo = _window(root)
    try:
        fid = repo.add_field("作者测试", "text")
        first = repo.save_project(Project(title="甲", field_values={fid: "原作者"}))
        second = repo.save_project(Project(title="乙"))
        win.refresh_projects()
        win._set_view_mode("list")
        win._select_project_by_id(second)
        table = win.proj_table
        column = next(i for i in range(win.proj_model.columnCount())
                      if win.proj_model.column_key(i) == f"user:{fid}")

        def click(pid: int, col: int) -> None:
            index = win.proj_model.index(win.proj_model.index_of_id(pid).row(), col)
            table.scrollTo(index)
            _app().processEvents()
            QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=table.visualRect(index).center())
            QTest.qWait(_app().doubleClickInterval() + 100)

        def editor() -> QLineEdit | None:
            return next((w for w in table.findChildren(QLineEdit) if w.isVisible()), None)

        click(second, column)
        assert editor() is not None
        QTest.keyClick(editor(), Qt.Key_Escape)
        click(first, 0)
        assert win._current_project_id == first and editor() is None
        click(first, column)
        edit = editor()
        assert edit is not None and edit.text() == "原作者"
        edit.setText("已保存")
        QTest.keyClick(edit, Qt.Key_Return)
        QTest.qWait(40)
        assert repo.get_project(first).field_values[fid] == "已保存"
        click(first, column)
        edit = editor()
        assert edit is not None
        edit.setText("取消")
        QTest.keyClick(edit, Qt.Key_Escape)
        assert repo.get_project(first).field_values[fid] == "已保存"
        click(first, column)
        edit = editor()
        assert edit is not None
        edit.setText("离开时保存")
        click(second, 0)
        assert repo.get_project(first).field_values[fid] == "离开时保存"
        assert fid not in repo.get_project(second).field_values
        assert win._current_project_id == second
        click(second, win.proj_model.columnCount() - 1)
        assert editor() is None

        # 另一连接在编辑期间写入同字段，拒绝覆盖；项目其它字段独立保存。
        click(second, column)
        edit = editor()
        assert edit is not None
        other = Repository(connect(root / "cabinet.db", recover_tasks=False))
        try:
            field = other.get_field(fid)
            other.update_project_field(second, field, "", "来自另一连接")
            edit.setText("过期编辑")
            QTest.keyClick(edit, Qt.Key_Return)
            QTest.qWait(40)
            assert repo.get_project(second).field_values[fid] == "来自另一连接"
            assert "其他操作修改" in win.statusBar().currentMessage()
            click(second, column)
            edit = editor()
            assert edit is not None
            title = next(f for f in other.list_fields() if f.is_title)
            other.update_project_field(second, title, "乙", "更新后的标题")
            edit.setText("新作者")
            QTest.keyClick(edit, Qt.Key_Return)
            QTest.qWait(40)
            saved = repo.get_project(second)
            assert saved.title == win.lbl_meta_title.text() == "更新后的标题"
            assert saved.field_values[fid] == "新作者"
        finally:
            other.conn.close()
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    with tempfile.TemporaryDirectory(prefix="cabinet-field-edit-") as folder:
        run(Path(folder))
    print("单击字段、保存、取消、切换项目与只读列验证通过")
