"""后台文件操作 worker 与进度对话框编排（task #36）。

线程边界（硬约束）：
- worker 线程只做**纯文件 IO**（复制 / 移动 / 打包 / stat）
- sqlite 连接（``repo.conn``）不得跨线程使用：DB 读写在主线程完成
  （启动前快照数据传给 worker；worker 产出结果清单，主线程收尾落库）
- 进度经 Qt 信号送达主线程；取消是协作式的（``is_cancelled`` 轮询，
  任务内抛出 ``OperationCancelled`` 或带着"已完成部分"提前返回）

防重入：进度对话框是 WindowModal 的，运行期间主窗口输入被挡住，
不需要额外的操作互斥锁。
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QProgressDialog, QWidget

from ..utils import OperationCancelled

__all__ = ["FileOpWorker", "run_with_progress", "ExportSnapshotRepo", "OperationCancelled"]


class FileOpWorker(QThread):
    """在后台线程执行一个"纯文件 IO"任务。

    任务签名：``fn(progress_cb, is_cancelled) -> result``
    - ``progress_cb(done, total, name)``：报告进度（信号转发主线程）
    - ``is_cancelled()``：用户是否请求取消；任务应在每项之间检查，
      抛 ``OperationCancelled``（→ cancelled 信号）或提前返回部分结果
      （→ finished_ok，由调用方在 result 里自行标注"未完成"）
    """

    progress = Signal(int, int, str)
    finished_ok = Signal(object)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def is_cancelled(self) -> bool:
        return self._cancel_requested

    def run(self) -> None:
        try:
            result = self._fn(self.progress.emit, self.is_cancelled)
        except OperationCancelled:
            self.cancelled.emit()
            return
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")
            return
        self.finished_ok.emit(result)


def run_with_progress(
    parent: QWidget,
    title: str,
    label: str,
    fn,
    *,
    on_done: Callable[[Any], None] | None = None,
    on_cancel: Callable[[], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    cancellable: bool = True,
) -> FileOpWorker:
    """在后台线程跑 ``fn``，并用模态进度对话框展示进度。

    对话框 WindowModal：运行期间主窗口输入被挡住（防重入）。
    ``cancellable=False`` 时不提供取消按钮（如备份打包，中途取消会留残包）。
    """
    prog = QProgressDialog(label, "取消" if cancellable else None, 0, 0, parent)
    prog.setWindowTitle(title)
    prog.setWindowModality(Qt.WindowModal)
    prog.setMinimumDuration(0)
    prog.setAutoClose(False)
    prog.setAutoReset(False)
    prog.show()

    worker = FileOpWorker(fn, parent)
    if cancellable:
        prog.canceled.connect(worker.request_cancel)

    def _on_progress(done: int, total: int, name: str) -> None:
        if total > 0:
            prog.setMaximum(total)
        prog.setValue(min(done, prog.maximum()))
        if name:
            prog.setLabelText(name)

    def _close() -> None:
        prog.reset()
        prog.close()
        worker.deleteLater()

    worker.progress.connect(_on_progress)

    def _done(result) -> None:
        _close()
        if on_done is not None:
            on_done(result)

    def _cancel() -> None:
        _close()
        if on_cancel is not None:
            on_cancel()

    def _fail(msg: str) -> None:
        _close()
        if on_error is not None:
            on_error(msg)

    worker.finished_ok.connect(_done)
    worker.cancelled.connect(_cancel)
    worker.failed.connect(_fail)
    worker.start()
    return worker


class ExportSnapshotRepo:
    """``export_project`` 的只读 repo 替身。

    数据在主线程快照（``list_files`` / ``list_fields``），worker 线程里
    替代真正的 ``Repository``，避免 sqlite 连接跨线程。
    """

    def __init__(self, files, fields):
        self._files = list(files)
        self._fields = list(fields)

    def list_files(self, _pid):
        return list(self._files)

    def list_fields(self):
        return list(self._fields)
