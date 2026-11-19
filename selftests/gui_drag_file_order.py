"""拖动文件不回退入库顺序：只用显式临时库与合成登记记录。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDrag, QDropEvent
from PySide6.QtTest import QSignalSpy

from app.models import FileItem, Project
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _find_file_tree_item, _window


def test_drag_out(root: Path, t: T) -> None:
    """从真实文件控件开始拖动，在系统交接处检查文件 URL 和收到拖放后的分流。"""
    win, repo = _window(root)
    try:
        pid = repo.save_project(Project(title="拖出文件测试"))
        source = root / "中文 空格#100%.txt"
        source.write_bytes(b"test attachment")
        stored = win.library.import_copy(pid, source)
        linked_id = repo.add_file(FileItem(project_id=pid, path=str(source), subfolder="文件夹"))
        stored_id = repo.add_file(FileItem(project_id=pid, path=stored, is_relative=True))
        win.refresh_projects()
        win._select_project_by_id(pid)
        table = win.tbl_files
        win._files_dnd.files_dropped.disconnect(win._on_dropped_on_files_table)
        imported = QSignalSpy(win._files_dnd.files_dropped)
        moved = QSignalSpy(win._files_dnd.files_moved)
        for mode in ("tree", "flat"):
            win._set_files_view_mode(mode, pid)
            table.clearSelection()
            linked = _find_file_tree_item(win, linked_id)
            linked.setSelected(True)
            if mode == "tree":
                linked.parent().setSelected(True)
            _find_file_tree_item(win, stored_id).setSelected(True)

            def receive(drag: QDrag, actions: Qt.DropAction) -> Qt.DropAction:
                t.assert_eq(f"{mode} 系统仅可复制", actions, Qt.CopyAction)
                t.assert_eq(f"{mode} 拖动源为文件控件", drag.source(), table)
                t.assert_eq(f"{mode} 内部 ID 快照", set(table.dragged_file_ids), {linked_id, stored_id})
                # 模拟接收方跨进程只拿到序列化数据，不能访问源控件或 Python 对象。
                received = QMimeData()
                received.setData("text/uri-list", drag.mimeData().data("text/uri-list"))
                paths = [Path(url.toLocalFile()) for url in received.urls()]
                t.assert_eq(f"{mode} 链接和仓储路径可区分", set(paths), {source, win.library.root / stored})
                t.assert_eq(f"{mode} 接收方能读两个附件", [p.read_bytes() for p in paths],
                            [b"test attachment", b"test attachment"])
                if mode == "tree":
                    # 拖回文件树同样携带 URL，必须走内部逻辑移动。使用当前行自身作无效落点。
                    pos = QPointF(table.visualItemRect(linked).center())
                    drop = QDropEvent(pos, Qt.CopyAction, received, Qt.LeftButton, Qt.NoModifier)
                    with patch.object(drop, "source", return_value=table):
                        t.assert_true("本表拖出不在主窗口打开导入区", win.eventFilter(win, drop))
                        table.clearSelection()
                        win._files_dnd.eventFilter(table.viewport(), drop)
                        t.assert_eq("本表回落不导入", imported.count(), 0)
                        t.assert_eq("选择变化后仍移动原始文件", set(moved.at(0)[0]), {linked_id, stored_id})
                        t.assert_true("本表回落接收逻辑移动", drop.isAccepted())
                    external = QDropEvent(pos, Qt.CopyAction, received, Qt.LeftButton, Qt.NoModifier)
                    win._files_dnd.eventFilter(table.viewport(), external)
                    t.assert_eq("外部拖入仍导入收到的路径", {Path(p) for p in imported.at(0)[0]}, set(paths))
                return Qt.CopyAction

            with patch.object(QDrag, "exec", new=receive):
                table.startDrag(Qt.CopyAction | Qt.MoveAction)
            t.assert_eq(f"{mode} 拖动结束清空快照", table.dragged_file_ids, [])
            t.assert_eq(f"{mode} 记录仍在", len(repo.list_files(pid)), 2)
        win._set_files_view_mode("tree", pid)
        table.clearSelection()
        _find_file_tree_item(win, linked_id).parent().setSelected(True)
        with patch.object(QDrag, "exec") as execute:
            table.startDrag(Qt.CopyAction | Qt.MoveAction)
            t.assert_eq("单独拖逻辑目录不暴露物理目录", execute.call_count, 0)
    finally:
        _close_window(win)
        repo.conn.close()


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
        test_drag_out(Path(tmp) / "attachments", t)
    raise SystemExit(0 if t.report() else 1)
