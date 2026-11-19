"""标签切换性能回归：仅使用临时库和程序生成的项目、文件及封面。"""
from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QImage

from app.db import connect
from app.models import Project
from app.repository import Repository
from app.ui.project_card import ProjectModel
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _window


def seed(root: Path, repo) -> None:
    """批量写入合成记录，不连接或复制真实资料库。"""
    image = QImage(400, 500, QImage.Format_RGB32)
    with repo.conn:
        repo.conn.executemany("INSERT INTO projects(id,title) VALUES(?,?)",
                              [(i, f"合成项目 {i}") for i in range(1, 1201)])
        repo.conn.executemany("INSERT INTO tags(id,name) VALUES(?,?)",
                              [(i, f"标签{i}") for i in range(1, 201)])
        repo.conn.executemany("INSERT INTO project_tags(project_id,tag_id) VALUES(?,?)",
                              [(i, tag) for i in range(1, 1201)
                               for tag in {1, 2, 3 + i % 198}])
        records = []
        for i in range(1, 1201):
            image.fill(QColor.fromHsv(i % 360, 100, 180))
            path = root / f"cover-{i}.png"
            image.save(str(path))
            records.extend((i * 100 + j, i, str(path) if j == 0 else f"synthetic-{i}-{j}.txt",
                            "image" if j == 0 else "other", 0)
                           for j in range(15))
        repo.conn.executemany(
            "INSERT INTO files(id,project_id,path,kind,is_relative) VALUES(?,?,?,?,?)", records,
        )
        repo.conn.execute("UPDATE projects SET cover_file_id=id*100")


def benchmark(win) -> None:
    """记录实际刷新与一轮绘制时间，不用不稳定的绝对耗时作为断言。"""
    times = []
    with patch.object(win, "_cover_pix", wraps=win._cover_pix) as covers:
        for tag in ("标签1", "标签2", "标签1", "标签2"):
            started = time.perf_counter()
            win.tag_tree.setCurrentItem(win.tag_tree._find_tag_item(tag))
            _app().processEvents()
            times.append((time.perf_counter() - started) * 1000)
        print(f"1200 projects / 18000 files / 200 tags: "
              f"median={statistics.median(times):.1f} ms; "
              f"switches={[round(ms, 1) for ms in times]}; cover_calls={covers.call_count}")


def regression(root: Path, win, repo, t: T) -> None:
    """用调用边界断言性能预算，同时检查缓存失效和原有选择行为。"""
    first = win.tag_tree._find_tag_item("标签1")
    second = win.tag_tree._find_tag_item("标签2")
    previous = win._current_project_id
    file_item = win.tbl_files.topLevelItem(0)
    with patch.object(win.tag_tree, "populate", wraps=win.tag_tree.populate) as tree, \
            patch.object(win, "_show_project", wraps=win._show_project) as preview, \
            patch.object(win, "_cover_pix", wraps=win._cover_pix) as covers, \
            patch.object(repo, "count_files_by_project", wraps=repo.count_files_by_project) as counts:
        win.tag_tree.setCurrentItem(first)
        _app().processEvents()
        t.assert_eq("切标签不重建标签树", tree.call_count, 0)
        t.assert_true("标签节点原对象保留", win.tag_tree._find_tag_item("标签2") is second)
        t.assert_eq("当前项目仍命中则保留预览", preview.call_count, 0)
        t.assert_eq("保留当前项目", win._current_project_id, previous)
        t.assert_true("保留右侧文件节点", win.tbl_files.topLevelItem(0) is file_item)
        t.assert_true("只加载可见封面", 0 < covers.call_count < 100)
        t.assert_eq("统计限定命中项目", set(counts.call_args.args[0]), set(range(1, 1201)))
        t.assert_eq("跨分批统计的文件数正确", win.proj_model._file_counts, {i: 15 for i in range(1, 1201)})

    win._set_view_mode("list")
    _app().processEvents()
    with patch.object(win, "_cover_pix", wraps=win._cover_pix) as covers:
        win.tag_tree.setCurrentItem(second)
        _app().processEvents()
        t.assert_eq("列表模式不加载封面", covers.call_count, 0)
    win._set_view_mode("grid")
    _app().processEvents()
    # 窄范围筛选排除原项目，只加载一次新项目，不先清空再加载。
    tag = "标签4" if previous != 1 else "标签5"
    with patch.object(win, "_show_project", wraps=win._show_project) as preview:
        win.tag_tree.setCurrentItem(win.tag_tree._find_tag_item(tag))
        _app().processEvents()
        t.assert_eq("换项目只加载一次预览", preview.call_count, 1)
        t.assert_true("新项目匹配筛选", tag in repo.get_project(win._current_project_id).tags)
    t.assert_true("文件统计仅含窄范围结果", len(win.proj_model._file_counts) < 10)

    # 本连接修改后即使走筛选入口，也不能复用旧侧栏和预览。
    local_id = repo.save_project(Project(title="本连接新增", tags=["新标签"]))
    with patch.object(win.tag_tree, "populate", wraps=win.tag_tree.populate) as tree:
        win._on_tag_filter_changed("all", "")
        t.assert_eq("本连接写入使轻量路径失效", tree.call_count, 1)
        t.assert_true("本连接新增项目可见", win.proj_model.index_of_id(local_id).isValid())
        t.assert_true("本连接新增标签可见", win.tag_tree._find_tag_item("新标签") is not None)
    external = Repository(connect(root / "cabinet.db"))
    try:
        remote_id = external.save_project(Project(title="其他连接新增", tags=["外部标签"]))
        with patch.object(win.tag_tree, "populate", wraps=win.tag_tree.populate) as tree:
            win._on_tag_filter_changed("all", "")
            t.assert_eq("其他连接提交使轻量路径失效", tree.call_count, 1)
            t.assert_true("其他连接新增项目可见", win.proj_model.index_of_id(remote_id).isValid())
            t.assert_true("其他连接新增标签可见", win.tag_tree._find_tag_item("外部标签") is not None)
    finally:
        external.conn.close()
    with patch.object(win.tag_tree, "populate", wraps=win.tag_tree.populate) as tree:
        win.refresh_projects()
        t.assert_eq("显式刷新始终同步标签计数", tree.call_count, 1)

    t.assert_eq("空集合文件统计为空", repo.count_files_by_project([]), {})
    t.assert_eq("限定文件统计去重且忽略不存在的项目", repo.count_files_by_project([1, 1, -1]), {1: 15})
    t.assert_eq("不传参数仍可统计全库", len(repo.count_files_by_project()), 1200)
    win._on_tag_filter_changed("tag", "不存在的标签")
    t.assert_eq("空筛选清空项目", win.proj_model.rowCount(), 0)
    t.assert_eq("空筛选清空右侧", win._current_project_id, None)
    t.assert_true("空筛选不能随机", not win.btn_random_project.isEnabled())


def main() -> int:
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-filter-perf-") as tmp:
        root = Path(tmp)
        win, repo = _window(root)
        try:
            seed(root, repo)
            win.refresh_projects()
            _app().processEvents()
            benchmark(win)
            regression(root, win, repo, t)
        finally:
            _close_window(win)
            repo.conn.close()
    return 0 if t.report() else 1


if __name__ == "__main__":
    raise SystemExit(main())
