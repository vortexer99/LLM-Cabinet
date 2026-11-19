"""自定义筛选侧栏：显式临时库、合成记录，验证保存／点击／覆盖／删除。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.models import FileItem, Project
from app.search import parse_search
from app.search_history import SAVED_SEARCHES_SETTING_KEY, upsert_saved_search
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _window


def run(root: Path, t: T) -> None:
    win, repo = _window(root)
    query = "ext:mp4 OR ext:webp OR ext:gif"
    name = "有视频类型"
    try:
        for title, suffix in (("甲", "mp4"), ("乙", "webp"), ("丙", "txt")):
            pid = repo.save_project(Project(title=title, tags=["测试标签"]))
            repo.add_file(FileItem(project_id=pid, path=f"missing/{title}.{suffix}", missing=True))
        win.refresh_projects()

        def execute(text: str) -> None:
            win.search_box.setText(text)
            win._search_timer.stop()
            win._completion.hide()
            win.refresh_projects()

        def save(label: str) -> None:
            with patch("app.ui.mw_search.QInputDialog.getText", return_value=(label, True)), \
                    patch("app.ui.mw_search.confirm", return_value=True):
                win.btn_save_search.click()

        def item(label: str):
            return win.tag_tree._find_item_by_kind_value("saved_search", label)

        execute(query)
        with patch.object(win, "_show_project") as preview:
            save(name)
            t.assert_eq("保存不重载预览", preview.call_count, 0)
        t.assert_true("保存立即出现左侧条目", item(name) is not None)
        t.assert_eq("分组名称", item(name).parent().text(0), "自定义筛选")
        t.assert_eq("悬停显示完整查询", item(name).toolTip(0), query)
        t.assert_eq("保存当前条件后高亮", win.tag_tree.current_filter(), ("saved_search", name))
        execute('title="丙"')
        win.tag_tree.scrollToItem(item(name))
        _app().processEvents()
        with patch.object(win, "refresh_projects", wraps=win.refresh_projects) as refresh:
            QTest.mouseClick(win.tag_tree.viewport(), Qt.LeftButton,
                             pos=win.tag_tree.visualItemRect(item(name)).center())
            QTest.qWait(250)
            t.assert_eq("点击仅刷新一次", refresh.call_count, 1)
        t.assert_eq("点击替换旧查询", parse_search(win.search_box.text()).ast, parse_search(query).ast)
        t.assert_eq("点击命中两项目", win.proj_model.rowCount(), 2)
        t.assert_eq("执行后保留条目高亮", win.tag_tree.current_filter(), ("saved_search", name))
        win.refresh_projects()
        t.assert_eq("完整刷新保留高亮", win.tag_tree.current_filter(), ("saved_search", name))
        execute('ext:gif')
        t.assert_true("编辑条件后取消原高亮", win.tag_tree.current_filter() != ("saved_search", name))
        save(name)
        t.assert_eq("同名覆盖立即更新", item(name).toolTip(0), 'ext:gif')
        t.assert_eq("同名不产生重复项", len(win.tag_tree.saved_searches), 1)

        save("另一名称")
        win.tag_tree.setCurrentItem(item(name))
        t.assert_eq("相同查询多个名称保留点击项", win.tag_tree.current_filter(), ("saved_search", name))
        current = win.search_box.text()
        with patch.object(win, "_show_project") as preview:
            win._delete_saved_search(name)
            t.assert_eq("删除收藏不重载预览", preview.call_count, 0)
        t.assert_eq("删除立即移除侧栏项", item(name), None)
        t.assert_eq("删除保留当前查询", win.search_box.text(), current)
        t.assert_true("同查询其他收藏保留", item("另一名称") is not None)

        # 模拟已有收藏，重开窗口自动展示，无需重新保存。
        raw, _ = upsert_saved_search(repo.get_setting(SAVED_SEARCHES_SETTING_KEY), "旧收藏", query)
        repo.set_setting(SAVED_SEARCHES_SETTING_KEY, raw)
        clone, clone_repo = _window(root)
        try:
            t.assert_true("重开恢复旧收藏", clone.tag_tree._find_item_by_kind_value("saved_search", "旧收藏") is not None)
        finally:
            _close_window(clone)
            clone_repo.conn.close()
        other_root = root / "other"
        other_root.mkdir()
        other, other_repo = _window(other_root)
        try:
            t.assert_eq("其他库不混入收藏", other.tag_tree.saved_searches, [])
        finally:
            _close_window(other)
            other_repo.conn.close()
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-saved-sidebar-") as tmp:
        run(Path(tmp), t)
    raise SystemExit(0 if t.report() else 1)
