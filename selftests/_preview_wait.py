"""预览异步可访问性检查的测试等待工具，仅操作传入的测试控件。"""
from __future__ import annotations

import time

from PySide6.QtTest import QTest


def wait_preview_probe(panel, timeout: float = 6.0) -> None:
    """等待后台检查及其同步加载回调，不替代真实媒体解码就绪断言。"""
    deadline = time.monotonic() + timeout
    while panel._monitor.busy and time.monotonic() < deadline:
        QTest.qWait(10)
    if panel._monitor.busy:
        raise AssertionError("测试预览文件检查未在期限内完成")
