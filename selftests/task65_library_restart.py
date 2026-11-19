"""库切换重启回归：只模拟进程替换，不读取用户配置或数据库。

运行：.venv\\Scripts\\python.exe selftests/task65_library_restart.py
"""
from __future__ import annotations

from contextlib import ExitStack
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class LibraryRestartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.resources = ExitStack()
        cls.temporary = Path(cls.resources.enter_context(
            tempfile.TemporaryDirectory(prefix="cabinet-restart-test-")))
        cls.resources.enter_context(patch.dict(os.environ, {
            "APPDATA": str(cls.temporary / "appdata"),
            "XDG_DATA_HOME": str(cls.temporary / "xdg-data"),
            "LLM_CABINET_DISABLE_KEYRING": "1",
        }))
        previous_hook = sys.excepthook
        cls.resources.callback(setattr, sys, "excepthook", previous_hook)
        cls.application = importlib.import_module("app.main")
        # 重启测试不应走启动主流程；意外访问配置或数据库立即失败。
        cls.resources.enter_context(patch.object(
            cls.application.CabinetConfig, "load",
            side_effect=AssertionError("测试禁止读取默认配置")))
        cls.resources.enter_context(patch.object(
            cls.application, "connect",
            side_effect=AssertionError("测试禁止连接默认数据库")))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.resources.close()

    def capture_restart(
        self, argv: list[str], *, frozen: bool = False,
        executable: str | None = None, source: Path | None = None,
        extra_args: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        """截获 execv 参数，避免替换正在运行的自测进程。"""
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", argv))
            stack.enter_context(patch.object(sys, "frozen", frozen, create=True))
            stack.enter_context(patch.object(sys, "executable", executable or sys.executable))
            if source is not None:
                stack.enter_context(patch.object(self.application, "__file__", str(source)))
            execute = stack.enter_context(patch.object(self.application.os, "execv"))
            warning = stack.enter_context(patch.object(self.application, "warn"))
            self.application._restart_self(extra_args=extra_args)
            warning.assert_not_called()
            execute.assert_called_once()
            return execute.call_args.args

    def test_module_launch_uses_absolute_package_entry(self) -> None:
        executable, argv = self.capture_restart([str(ROOT / "app" / "main.py"), "--debug"])
        self.assertEqual(executable, sys.executable)
        self.assertEqual(argv, [sys.executable, str(ROOT / "run.py"), "--debug"])

    def test_top_level_launch_does_not_repeat_entry(self) -> None:
        _, argv = self.capture_restart(["run.py", "--debug"])
        self.assertEqual(argv, [sys.executable, str(ROOT / "run.py"), "--debug"])

    def test_frozen_launch_does_not_repeat_executable(self) -> None:
        executable = str(self.temporary / "program with spaces" / "LLM Cabinet.exe")
        program, argv = self.capture_restart(
            [executable, "--debug", "--welcome"], frozen=True, executable=executable)
        self.assertEqual(program, executable)
        self.assertEqual(argv, [executable, "--debug"])

    def test_welcome_is_one_time_and_extra_arguments_are_deduplicated(self) -> None:
        _, normal = self.capture_restart(["run.py", "--welcome", "--debug", "--welcome"])
        self.assertEqual(normal[2:], ["--debug"])
        _, welcome = self.capture_restart(
            ["run.py", "--welcome", "--debug"],
            extra_args=["--debug", "--welcome", "--welcome", "--trace", "--trace"])
        self.assertEqual(welcome[2:], ["--debug", "--welcome", "--trace"])

    def test_source_and_interpreter_paths_with_spaces_remain_single_arguments(self) -> None:
        source = self.temporary / "source with spaces" / "app" / "main.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        entry = source.parent.parent / "run.py"
        entry.write_text((ROOT / "run.py").read_text(encoding="utf-8"), encoding="utf-8")
        executable = str(self.temporary / "Python with spaces" / "python.exe")
        program, argv = self.capture_restart(
            [str(source), "value with spaces"], executable=executable, source=source)
        self.assertEqual(program, executable)
        self.assertEqual(argv, [executable, str(entry), "value with spaces"])

    def test_restart_failure_reports_full_details_and_logs_traceback(self) -> None:
        with patch.object(self.application.os, "execv", side_effect=PermissionError("模拟启动被拒绝")), \
                patch.object(self.application, "warn") as warning, \
                patch.object(self.application.log, "exception") as log_exception, \
                patch.object(sys, "argv", [str(ROOT / "app" / "main.py")]), \
                patch.object(sys, "frozen", False, create=True):
            self.application._restart_self()
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[1], "需要手动重启")
        self.assertIn("手动", warning.call_args.args[2])
        details = warning.call_args.kwargs["detailed"]
        self.assertIn("PermissionError", details)
        self.assertIn("模拟启动被拒绝", details)
        self.assertIn(sys.executable, details)
        # 参数使用 repr 展示时，Windows 路径中的反斜线会被转义。
        self.assertIn(repr(str(ROOT / "run.py")), details)
        log_exception.assert_called_once()

    def test_missing_source_entry_reports_failure_without_exec(self) -> None:
        missing_source = self.temporary / "missing-source" / "app" / "main.py"
        with patch.object(self.application, "__file__", str(missing_source)), \
                patch.object(self.application.os, "execv") as execute, \
                patch.object(self.application, "warn") as warning, \
                patch.object(self.application.log, "exception"), \
                patch.object(sys, "argv", [str(missing_source)]), \
                patch.object(sys, "frozen", False, create=True):
            self.application._restart_self()
        execute.assert_not_called()
        self.assertIn("FileNotFoundError", warning.call_args.kwargs["detailed"])
        self.assertIn("找不到", warning.call_args.kwargs["detailed"])

    def test_real_child_process_imports_entry_from_unrelated_working_directory(self) -> None:
        """真实解释器运行重启入口；替换 main，保证不会读取真实配置或打开窗口。"""
        _, command = self.capture_restart([str(ROOT / "app" / "main.py"), "--trace"])
        child_root = self.temporary / "child with spaces"
        child_root.mkdir(exist_ok=True)
        bootstrap = child_root / "bootstrap.py"
        bootstrap.write_text(
            "import json, runpy, sys\n"
            "import app.main as application\n"
            "def forbidden(*args, **kwargs):\n"
            "    raise AssertionError('禁止读取默认配置或数据库')\n"
            "application.CabinetConfig.load = forbidden\n"
            "application.connect = forbidden\n"
            "def synthetic_main():\n"
            "    print('RESTART_OK:' + json.dumps(sys.argv))\n"
            "    return 0\n"
            "application.main = synthetic_main\n"
            "sys.argv = sys.argv[1:]\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.update({
            "APPDATA": str(child_root / "appdata"),
            "XDG_DATA_HOME": str(child_root / "xdg-data"),
            "PYTHONPATH": str(ROOT),
            "PYTHONIOENCODING": "utf-8",
        })
        child = subprocess.run(
            [command[0], str(bootstrap), *command[1:]], cwd=child_root,
            env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        marker = next(line for line in child.stdout.splitlines() if line.startswith("RESTART_OK:"))
        self.assertEqual(json.loads(marker.removeprefix("RESTART_OK:")), command[1:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
