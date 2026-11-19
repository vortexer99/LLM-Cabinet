"""解压专用双进度窗口：总进度与当前文件进度。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout

from ..archives import ArchiveProgress
from ..utils import OperationCancelled, human_size
from .workers import FileOpWorker


class ArchiveProgressDialog(QDialog):
    """取消或关闭只发出取消请求，等后台清理完成后再关闭。"""

    cancel_requested = Signal()

    def __init__(self, parent, title: str, label: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModal)
        self.resize(520, 240)
        self._finished = False
        self._cancelled = False
        layout = QVBoxLayout(self)
        self.overall_label = QLabel("总进度：正在读取压缩包目录…")
        self.overall_bar = QProgressBar()
        self.current_label = QLabel(label)
        self.current_label.setWordWrap(True)
        self.current_bar = QProgressBar()
        self.bytes_label = QLabel("当前文件：等待解压")
        self.cancel_button = QPushButton("取消")
        for widget in (self.overall_label, self.overall_bar, self.current_label,
                       self.current_bar, self.bytes_label, self.cancel_button):
            layout.addWidget(widget)
        self.overall_bar.setRange(0, 0)
        self.current_bar.setRange(0, 0)
        self.cancel_button.clicked.connect(self.reject)

    @staticmethod
    def _set_progress(bar: QProgressBar, done: int, total: int | None, finished: bool) -> None:
        # 百分比刻度避免 Qt int32 溢出；字节数保留在标签中。
        if finished:
            bar.setRange(0, 1000)
            bar.setValue(1000)
        elif total is None:
            bar.setRange(0, 0)
        else:
            bar.setRange(0, 1000)
            bar.setValue(min(1000, done * 1000 // total) if total else 0)

    def update_progress(self, state: ArchiveProgress) -> None:
        if self._cancelled:
            return
        count = (f"{state.completed_files} / {state.total_files} 个文件"
                 if state.total_files is not None else f"已完成 {state.completed_files} 个文件")
        size = (f"{human_size(state.completed_bytes)} / {human_size(state.total_bytes)}"
                if state.total_bytes is not None else f"已解压 {human_size(state.completed_bytes)}")
        self.overall_label.setText(f"总进度：{count} · {size}")
        self._set_progress(self.overall_bar, state.completed_bytes, state.total_bytes, state.finished)
        name = state.current_name or ("解压完成" if state.finished else "正在读取压缩包目录…")
        self.current_label.setText(name if len(name) <= 160 else "…" + name[-159:])
        self.current_label.setToolTip(state.current_name)
        self._set_progress(self.current_bar, state.current_bytes, state.current_total,
                           state.finished or (state.current_total == 0 and bool(state.current_name)))
        current_size = (f"{human_size(state.current_bytes)} / {human_size(state.current_total)}"
                        if state.current_total is not None else f"已解压 {human_size(state.current_bytes)}")
        self.bytes_label.setText(f"当前文件：{current_size}")

    def reject(self) -> None:
        if self._finished:
            super().reject()
        elif not self._cancelled:
            self._cancelled = True
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("正在取消…")
            self.current_label.setText("正在停止解压并清理本次输出，请稍候…")
            self.cancel_requested.emit()

    def closeEvent(self, event) -> None:
        if self._finished:
            event.accept()
        else:
            event.ignore()
            self.reject()

    def finish(self) -> None:
        self._finished = True
        self.accept()
        self.deleteLater()


class _ArchiveWorker(FileOpWorker):
    detailed_progress = Signal(object)

    def run(self) -> None:
        def progress(done: int, total: int, name: str) -> None:
            # 旧回调不再驱动 UI，但保留函数接口供通用 worker 测试复用。
            return
        progress.detail = self.detailed_progress.emit
        try:
            result = self._fn(progress, self.is_cancelled)
        except OperationCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.finished_ok.emit(result)


def run_archive_with_progress(parent, title: str, label: str, fn, *,
                              on_done=None, on_cancel=None, on_error=None):
    """沿用文件任务回调接口，只替换解压窗口与详情信号。"""
    dialog = ArchiveProgressDialog(parent, title, label)
    worker = _ArchiveWorker(fn, parent)
    dialog.cancel_requested.connect(worker.request_cancel)
    worker.detailed_progress.connect(dialog.update_progress)

    def complete(callback, *args) -> None:
        dialog.finish()
        if callback is not None:
            callback(*args)

    # QThread 完全退出后才销毁，避免完成信号到达时线程尚未退出。
    worker.finished.connect(worker.deleteLater)
    worker.finished_ok.connect(lambda result: complete(on_done, result))
    worker.cancelled.connect(lambda: complete(on_cancel))
    worker.failed.connect(lambda message: complete(on_error, message))
    dialog.show()
    worker.start()
    return worker
