"""工具函数自检：系统打开/定位路径的调用方式。

覆盖 Windows 上 ``reveal_in_explorer`` 不应通过 ``os.system`` 调 shell，
避免 GUI 中弹出命令行窗口并拖慢响应。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T

import app.utils as utils


def test_reveal_in_explorer_windows_uses_popen(t: T) -> None:
    calls: list[tuple[list[str], dict]] = []
    os_system_calls: list[str] = []

    old_platform = utils.sys.platform
    old_popen = utils.subprocess.Popen
    old_system = utils.os.system
    old_create_no_window = getattr(utils.subprocess, "CREATE_NO_WINDOW", None)

    def fake_popen(args, **kwargs):
        calls.append((list(args), dict(kwargs)))

        class _Proc:
            pass

        return _Proc()

    def fake_system(cmd: str) -> int:
        os_system_calls.append(cmd)
        return 0

    try:
        utils.sys.platform = "win32"
        utils.subprocess.Popen = fake_popen  # type: ignore[assignment]
        utils.os.system = fake_system  # type: ignore[assignment]
        if old_create_no_window is None:
            utils.subprocess.CREATE_NO_WINDOW = 0x08000000  # type: ignore[attr-defined]

        utils.reveal_in_explorer(Path("C:/tmp/hello world.txt"))

        t.assert_eq("windows reveal does not call os.system", os_system_calls, [])
        t.assert_eq("windows reveal launches explorer directly", calls[0][0][0], "explorer.exe")
        t.assert_true("windows reveal uses /select argument", calls[0][0][1].startswith("/select,"))
        t.assert_true("windows reveal avoids inheriting handles", calls[0][1].get("close_fds") is True)
        t.assert_true("windows reveal sets CREATE_NO_WINDOW", bool(calls[0][1].get("creationflags")))
    finally:
        utils.sys.platform = old_platform
        utils.subprocess.Popen = old_popen  # type: ignore[assignment]
        utils.os.system = old_system  # type: ignore[assignment]
        if old_create_no_window is None and hasattr(utils.subprocess, "CREATE_NO_WINDOW"):
            delattr(utils.subprocess, "CREATE_NO_WINDOW")


if __name__ == "__main__":
    print("=" * 60)
    print("utils opening selftest")
    print("=" * 60)
    t = T()
    test_reveal_in_explorer_windows_uses_popen(t)
    sys.exit(0 if t.report() else 1)
