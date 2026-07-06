"""拖放支持：通过 viewport 事件过滤器实现，比 QListView/QTableWidget 子类更可靠。

两个 helper 类各自管理一个目标 view：
- ProjectViewDnD: 卡片墙；落在某张卡片上 → files_dropped_on_item(pid, paths)
- FilesTableDnD:  文件表；落在表内任意位置 → files_dropped(paths)

两者都同时在 view 自己 + view.viewport() 上装事件过滤器，因为不同 Qt 版本/不同视图，
DragEnter/Drop 事件的目标可能是 view 也可能是 viewport。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QObject, Qt, Signal

DEBUG = os.environ.get("LLMCABINET_DND_DEBUG") == "1"


def _log(*args):
    if DEBUG:
        print("[DnD]", *args)


def extract_local_paths(mime) -> list[str]:
    if not mime.hasUrls():
        return []
    out: list[str] = []
    for url in mime.urls():
        if url.isLocalFile():
            out.append(url.toLocalFile())
    return out


class ProjectViewDnD(QObject):
    files_dropped_on_item = Signal(int, list)
    drag_hover_changed = Signal(object)  # int | None

    def __init__(self, view, role_id: int):
        super().__init__(view)
        self.view = view
        self.role_id = role_id
        self._hover_pid: int | None = None

        from PySide6.QtWidgets import QAbstractItemView
        view.setDragDropMode(QAbstractItemView.DropOnly)
        view.setAcceptDrops(True)
        view.viewport().setAcceptDrops(True)
        view.setDragEnabled(False)
        view.setDropIndicatorShown(False)

        view.installEventFilter(self)
        view.viewport().installEventFilter(self)

    def _pid_at_event(self, ev) -> int | None:
        try:
            pos = ev.position().toPoint()
        except AttributeError:
            pos = ev.pos()
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return None
        return idx.data(self.role_id)

    def _set_hover(self, pid):
        if pid != self._hover_pid:
            self._hover_pid = pid
            self.drag_hover_changed.emit(pid)

    def eventFilter(self, obj, ev):
        et = ev.type()
        if et not in (
            QEvent.DragEnter, QEvent.DragMove, QEvent.DragLeave, QEvent.Drop
        ):
            return False

        target = "view" if obj is self.view else "viewport"
        _log(f"proj/{target} {et}")

        if et == QEvent.DragEnter:
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()
            else:
                ev.ignore()
            return True

        if et == QEvent.DragMove:
            if not ev.mimeData().hasUrls():
                ev.ignore()
                return True
            pid = self._pid_at_event(ev)
            self._set_hover(pid)
            ev.acceptProposedAction()
            return True

        if et == QEvent.DragLeave:
            self._set_hover(None)
            return False

        if et == QEvent.Drop:
            if not ev.mimeData().hasUrls():
                ev.ignore()
                return True
            paths = extract_local_paths(ev.mimeData())
            pid = self._pid_at_event(ev)
            self._set_hover(None)
            _log(f"proj drop pid={pid} paths={len(paths)}")
            ev.acceptProposedAction()
            if pid is not None and paths:
                self.files_dropped_on_item.emit(int(pid), paths)
            # 落在空白区直接吞掉（不创建项目，用户应拖到下方 DropZone）
            return True

        return False


class FilesTableDnD(QObject):
    files_dropped = Signal(list)
    files_moved = Signal(list, str, object, bool)  # file_ids, target_subfolder, target_file_id, before

    def __init__(self, table):
        super().__init__(table)
        self.table = table
        from PySide6.QtWidgets import QAbstractItemView
        table.setDragDropMode(QAbstractItemView.DragDrop)
        table.setAcceptDrops(True)
        table.viewport().setAcceptDrops(True)
        table.setDragEnabled(True)
        table.setDefaultDropAction(Qt.MoveAction)
        table.setDropIndicatorShown(True)
        table.installEventFilter(self)
        table.viewport().installEventFilter(self)

    def _selected_file_ids(self) -> list[int]:
        ids: list[int] = []
        for item in self.table.selectedItems():
            fid = item.data(0, Qt.UserRole)
            if fid is not None and fid > 0 and fid not in ids:
                ids.append(int(fid))
        return ids

    def _subfolder_for_item(self, item) -> str:
        if item is None:
            return ""
        fid = item.data(0, Qt.UserRole)
        if fid is not None and fid < 0:
            return item.data(0, Qt.UserRole + 1) or ""
        sf = item.data(0, Qt.UserRole + 2)
        if sf is not None:
            return sf or ""
        parent = item.parent()
        if parent is not None:
            return parent.data(0, Qt.UserRole + 1) or ""
        return ""

    def eventFilter(self, obj, ev):
        et = ev.type()
        if et not in (
            QEvent.DragEnter, QEvent.DragMove, QEvent.DragLeave, QEvent.Drop
        ):
            return False
        target = "table" if obj is self.table else "viewport"
        _log(f"files/{target} {et}")
        if et in (QEvent.DragEnter, QEvent.DragMove):
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()
            elif self._selected_file_ids():
                ev.setDropAction(Qt.MoveAction)
                ev.accept()
            else:
                ev.ignore()
            return True
        if et == QEvent.Drop:
            if ev.mimeData().hasUrls():
                paths = extract_local_paths(ev.mimeData())
                _log(f"files drop paths={len(paths)}")
                ev.acceptProposedAction()
                if paths:
                    self.files_dropped.emit(paths)
                return True

            file_ids = self._selected_file_ids()
            if not file_ids:
                ev.ignore()
                return True
            try:
                pos = ev.position().toPoint()
            except AttributeError:
                pos = ev.pos()
            target = self.table.itemAt(pos)
            target_fid = None
            before = False
            target_subfolder = self._subfolder_for_item(target)
            if target is not None:
                fid = target.data(0, Qt.UserRole)
                if fid is not None and fid > 0:
                    target_fid = int(fid)
                    rect = self.table.visualItemRect(target)
                    before = pos.y() < rect.center().y()
            _log(
                "files move",
                f"ids={file_ids}",
                f"target_sf={target_subfolder!r}",
                f"target_fid={target_fid}",
                f"before={before}",
            )
            ev.setDropAction(Qt.MoveAction)
            ev.accept()
            self.files_moved.emit(file_ids, target_subfolder, target_fid, before)
            return True
        return False

