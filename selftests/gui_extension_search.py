"""扩展名组合搜索与命名收藏：显式临时库，仅合成登记路径，不读媒体。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import FileItem, Project
from app.search import parse_search
from app.search_history import SAVED_SEARCHES_SETTING_KEY, load_saved_searches
from app.ui.project_card import ProjectModel
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _window


def run(root: Path, t: T) -> None:
    win, repo = _window(root)
    query = "ext:mp4 OR ext:webp OR ext:gif"
    ids = {}
    try:
        paths = {
            "mp4": ["missing/film.MP4"], "webp": ["missing/still.webp"],
            "gif": [r"missing\animation.GIF"], "all": ["x/a.mp4", "x/a.webp", "x/a.gif"],
            "wrong": ["missing/webp.jpg", "missing/gif/other.png", "missing/test.mp4.bak", "missing/mygif"],
            "empty": [], "compound": ["x/a.TAR.GZ"],
        }
        for title, files in paths.items():
            pid = repo.save_project(Project(title=title, tags=["组合"] if title == "all" else []))
            ids[title] = pid
            for index, path in enumerate(files):
                repo.add_file(FileItem(project_id=pid, path=path, kind="other", missing=True,
                                       is_relative=bool(index % 2), origin="generated" if index % 2 else "user"))

        def search(expression: str) -> set[int]:
            parsed = parse_search(expression)
            assert parsed.ok, parsed.error
            return {p.id for p in repo.list_projects_query(parsed.ast)}

        wanted = {ids[name] for name in ("mp4", "webp", "gif", "all")}
        with patch.object(Path, "stat", side_effect=AssertionError("扩展名查询禁止访问磁盘")):
            t.assert_eq("任一格式命中且项目去重", search(query), wanted)
            t.assert_eq("同时三种格式按项目判断", search("ext:mp4 AND ext:webp AND ext:gif"), {ids["all"]})
            t.assert_eq("大小写及点号兼容", search("ext=.MP4"), {ids["mp4"], ids["all"]})
            t.assert_eq("NOT组合", search("ext:mp4 AND NOT ext:gif"), {ids["mp4"]})
            t.assert_eq("标签组合保持括号语义", search(f"tag=组合 AND ({query})"), {ids["all"]})
            t.assert_eq("复合扩展名", search("ext:tar.gz"), {ids["compound"]})
            t.assert_eq("不匹配目录里的扩展名", search("ext:png"), {ids["wrong"]})
            t.assert_eq("不把后缀当LIKE通配符", search('ext:"%"'), set())
            t.assert_eq("非法比较符无结果", search("ext:>mp4"), set())
            t.assert_eq("目录路径不是扩展名", search('ext:"folder/mp4"'), set())
        win.search_box.setText(query)
        win._search_timer.stop()
        win._completion.hide()
        win.refresh_projects()
        t.assert_eq("GUI完整查询结果", {win.proj_model.index(r, 0).data(ProjectModel.RoleId)
                                         for r in range(win.proj_model.rowCount())}, wanted)
        with patch("app.ui.mw_search.QInputDialog.getText", return_value=("有视频类型", True)):
            win.btn_save_search.click()
        saved = load_saved_searches(repo.get_setting(SAVED_SEARCHES_SETTING_KEY, "[]"))
        t.assert_eq("保存自定义名称和完整查询", [(item["name"], item["query"]) for item in saved], [("有视频类型", query)])
        win._set_filter_query(None)
        win._apply_search_query(saved[0]["query"])
        win._search_timer.stop()
        win.refresh_projects()
        t.assert_eq("恢复收藏结果相同", win.proj_model.rowCount(), 4)
        win.search_box.setText("ext:w")
        win._search_timer.stop()
        with patch.object(win._completion, "show_candidates") as popup:
            win._update_search_completion()
            t.assert_eq("扩展名补全", popup.call_args.args[1][0][1][0][1], "ext:webp")
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-extension-search-") as tmp:
        run(Path(tmp), t)
    raise SystemExit(0 if t.report() else 1)
