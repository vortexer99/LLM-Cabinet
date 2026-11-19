"""当前预览文件的轻量可访问性检测；阻塞 I/O 永远留在有界守护线程中。"""
from __future__ import annotations

import ctypes
import logging
import ntpath
import os
import queue
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal


@dataclass(frozen=True)
class ProbeResult:
    """保留原始错误信息，供预览详情和复制报告使用。"""

    path: str
    accessible: bool
    stage: str = "读取检查"
    error_type: str = ""
    message: str = ""
    device_offline: bool = False


def _drive_missing(path: str) -> bool:
    """仅在 Windows 明确未分配盘符时报告设备离线；UNC/权限错误不推断。"""
    drive, _ = ntpath.splitdrive(path)
    if os.name != "nt" or len(drive) != 2 or drive[1] != ":":
        return False
    index = ord(drive[0].upper()) - ord("A")
    if not 0 <= index < 26:
        return False
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return bool(mask) and not bool(mask & (1 << index))


def probe_file(path: str) -> ProbeResult:
    """后台执行 stat 和单字节读取，区分盘符离线、读取失败及媒体解码失败。"""
    stage = "检查文件信息"
    try:
        if _drive_missing(path):
            return ProbeResult(path, False, stage, "DeviceOfflineError", "文件所在盘符当前不存在", True)
        metadata = os.stat(path)
        if not stat.S_ISREG(metadata.st_mode):
            raise IsADirectoryError("所选路径不是普通文件")
        stage = "打开文件"
        with open(path, "rb") as stream:
            stage = "读取文件"
            stream.read(1)
        return ProbeResult(path, True)
    except Exception as exc:
        logging.debug("预览可访问性检测失败：%s (%s)", path, stage, exc_info=True)
        return ProbeResult(path, False, stage, type(exc).__name__, str(exc), _drive_missing(path))


@dataclass
class _Job:
    owner: object
    operation: Callable[[], ProbeResult]
    output: queue.Queue
    cancelled: threading.Event = field(default_factory=threading.Event)


class _DaemonPool:
    """至多四个阻塞任务；每个监视器只保留最新待执行项，不为离线盘无限建线程。"""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._pending: dict[object, _Job] = {}
        for index in range(4):
            threading.Thread(target=self._run, name=f"preview-access-{index}", daemon=True).start()

    def submit(self, job: _Job) -> bool:
        with self._condition:
            previous = self._pending.pop(job.owner, None)
            if previous is not None:
                previous.cancelled.set()
            if len(self._pending) >= 16:
                return False
            self._pending[job.owner] = job
            self._condition.notify()
            return True

    def cancel(self, job: _Job) -> None:
        job.cancelled.set()
        with self._condition:
            if self._pending.get(job.owner) is job:
                self._pending.pop(job.owner)

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending:
                    self._condition.wait()
                owner = next(iter(self._pending))
                job = self._pending.pop(owner)
            if job.cancelled.is_set():
                continue
            try:
                result = job.operation()
            except Exception as exc:
                logging.exception("预览后台任务异常")
                result = ProbeResult("", False, "后台任务", type(exc).__name__, str(exc))
            if not job.cancelled.is_set():
                job.output.put(result)


_pool: _DaemonPool | None = None


class FileAccessMonitor(QObject):
    """Qt 主线程仅收取结果和超时；销毁对象不会等待磁盘 I/O 或终止 QThread。"""

    result_ready = Signal(int, str, object)
    TIMEOUT_MS = 4000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._owner = object()
        self._job: _Job | None = None
        self._generation = 0
        self._path = ""
        self._deadline = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._poll)

    @property
    def busy(self) -> bool:
        return self._job is not None

    def check(self, path: str, generation: int, *, operation: Callable[[], ProbeResult] | None = None) -> None:
        global _pool
        self.cancel()
        if _pool is None:
            _pool = _DaemonPool()
        self._path, self._generation = path, generation
        self._job = _Job(self._owner, operation or (lambda: probe_file(path)), queue.Queue(maxsize=1))
        self._deadline = time.monotonic() + self.TIMEOUT_MS / 1000
        if not _pool.submit(self._job):
            self._job.output.put(ProbeResult(path, False, "等待检测", "BusyError", "后台检测繁忙，请稍后重试"))
        self._timer.start()

    def cancel(self) -> None:
        self._timer.stop()
        if self._job is not None and _pool is not None:
            _pool.cancel(self._job)
        self._job = None

    def _poll(self) -> None:
        job = self._job
        if job is None:
            return
        try:
            result = job.output.get_nowait()
        except queue.Empty:
            if time.monotonic() < self._deadline:
                return
            result = ProbeResult(self._path, False, "等待文件响应", "TimeoutError", "文件检查超时，设备可能尚未就绪")
        generation, path = self._generation, self._path
        self.cancel()
        self.result_ready.emit(generation, path, result)
