"""拖动文件不回退入库顺序：只用显式临时库与合成登记记录。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt

from app.models import FileItem, Project
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _window


def run(root: Path, t: T) -> None:
    win, repo = _window(root)
    try:
        pid = repo.save_project(Project(title="拖动顺序测试"))
        ids = {}
        for folder in ("", "目录2", "目录10"):
            for order, number in enumerate((10, 2, 1)):
                fid = repo.add_file(FileItem(project_id=pid, path=f"missing/{folder}/file{number}.txt",
                                             subfolder=folder, ord=order, missing=True))
                ids[folder, number] = fid
        win.refresh_projects()

        def shown(folder: str) -> list[int]:
            return [item.data(0, Qt.UserRole) for item in win._preview_file_items()
                    if repo.get_file(item.data(0, Qt.UserRole)).subfolder == folder]

        def reset() -> None:
            win._set_tree_sort_state("name", False)
            win._refresh_files_table()

        first = ids["", 1]
        reset()
        before = {folder: shown(folder) for folder in ("", "目录2", "目录10")}
        # 无效果的拖动不能落库，也不重建预览。
        for target, preceding in ((first, True), (None, False), (ids["", 2], True)):
            with patch.object(repo, "reorder_files", wraps=repo.reorder_files) as write, \
                    patch.object(win, "_refresh_files_table", wraps=win._refresh_files_table) as refresh:
                win._on_files_moved([first], "", target, preceding)
                t.assert_eq("无效移动不写顺序", write.call_count, 0)
                t.assert_eq("无效移动不重建预览", refresh.call_count, 0)
            t.assert_eq("无效移动保留自然排序", win._tree_sort_state(), ("name", False))

        win._on_files_moved([ids["目录2", 2]], "", ids["", 10], False)
        t.assert_eq("跨目录仍为自然排序", win._tree_sort_state(), ("name", False))
        t.assert_eq("跨目录改变逻辑归属", repo.get_file(ids["目录2", 2]).subfolder, "")
        t.assert_eq("原目录剩余顺序正常", shown("目录2"), [ids["目录2", 1], ids["目录2", 10]])
        t.assert_eq("无关目录不打乱", shown("目录10"), before["目录10"])
        t.assert_eq("目标目录仍数字排序", [Path(repo.get_file(fid).path).name for fid in shown("")],
                    ["file1.txt", "file2.txt", "file2.txt", "file10.txt"])

        win._set_tree_sort_state("name", True)
        win._refresh_files_table()
        win._on_files_moved([ids["目录2", 1]], "目录10", None, False)
        t.assert_eq("跨目录保留降序", win._tree_sort_state(), ("name", True))
        t.assert_eq("降序目标目录", [Path(repo.get_file(fid).path).name for fid in shown("目录10")],
                    ["file10.txt", "file2.txt", "file1.txt", "file1.txt"])

        reset()
        unrelated = shown("目录10")
        current = shown("")
        win._on_files_moved([ids["", 10]], "", first, True)
        t.assert_eq("明确同级插入切换自定义", win._tree_sort_state(), ("custom", False))
        t.assert_eq("同级插入按屏幕顺序移动", shown(""), [ids["", 10]] + [fid for fid in current if fid != ids["", 10]])
        t.assert_eq("切自定义也不打乱无关目录", shown("目录10"), unrelated)
        t.assert_true("调序提示恢复方式", "文件名" in win.statusBar().currentMessage())
        win._refresh_files_table()
        t.assert_eq("自定义重载保持无关目录", shown("目录10"), unrelated)
        # 自定义模式下跨目录仍可插入到指定位置。
        moving = ids["目录2", 10]
        win._on_files_moved([moving], "", first, True)
        t.assert_eq("自定义跨目录保留模式", win._tree_sort_state(), ("custom", False))
        t.assert_eq("自定义跨目录指定位置", shown("").index(moving) + 1, shown("").index(first))
        reset()
        t.assert_eq("恢复名称排序不改归属", repo.get_file(moving).subfolder, "")
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-drag-order-") as tmp:
        run(Path(tmp), t)
    raise SystemExit(0 if t.report() else 1)
