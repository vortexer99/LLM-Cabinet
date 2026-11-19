"""文件操作失败详情回归：显式临时数据库、合成文件及同步后台任务。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import connect
from app.library import Library
from app.models import FileItem, Project
from app.repository import Repository
from app.ui.mw_files import FilesPanelMixin


class Window(FilesPanelMixin):
    """仅执行文件动作，避免窗口初始化及默认配置访问。"""

    def __init__(self, root: Path):
        self.repo = Repository(connect(root / "explicit-test.db"))
        self.library = Library(root / "library")
        self.refreshes = 0
        self.pid = self.repo.save_project(Project(title="测试项目"))

    def _refresh_files_table(self):
        self.refreshes += 1


def run_sync(_parent, _title, _label, worker, *, on_done, on_error):
    """不吞异常，测试直接检查正常回调中的完整结果。"""
    on_done(worker(lambda *_: None, lambda: False))


class FileErrorDetailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cabinet-file-errors-")
        self.root = Path(self.tmp.name)
        self.win = Window(self.root)
        self.target = self.root / "target"
        self.target.mkdir()
        self.stack = ExitStack()
        self.stack.enter_context(patch("app.ui.mw_files.QFileDialog.getExistingDirectory", return_value=str(self.target)))
        self.stack.enter_context(patch("app.ui.mw_files.confirm", return_value=True))
        self.stack.enter_context(patch("app.ui.mw_files.run_with_progress", side_effect=run_sync))
        self.info = self.stack.enter_context(patch("app.ui.mw_files.info"))
        self.warn = self.stack.enter_context(patch("app.ui.mw_files.warn"))

    def tearDown(self):
        self.stack.close()
        self.win.repo.conn.close()
        self.tmp.cleanup()

    def file(self, name: str, *, exists: bool = True) -> FileItem:
        path = self.root / name
        if exists:
            path.write_text(name, encoding="utf-8")
        fid = self.win.repo.add_file(FileItem(project_id=self.win.pid, path=str(path), label=name))
        return self.win.repo.get_file(fid)

    def details(self) -> str:
        self.assertEqual(self.info.call_count, 1)
        return self.info.call_args.kwargs["detailed"]

    def assert_file_detail(self, detail: str, file: FileItem, target: Path):
        for value in (f"文件 ID：{file.id}", file.label, file.path, str(target), "失败阶段：", "异常："):
            self.assertIn(value, detail)

    def test_move_all_errors_are_complete_and_logged(self):
        files = [self.file(f"missing-{i:02}.txt", exists=False) for i in range(12)]
        with self.assertLogs("llm_cabinet.ui", level="ERROR") as logs:
            self.win._move_files_to_location(files)
        detail = self.details()
        self.assertEqual(len(logs.records), 12)
        self.assertIn("失败：12 个", self.info.call_args.args[2])
        for file in files:
            self.assert_file_detail(detail, file, self.target / Path(file.path).name)
        self.assertIn("FileNotFoundError", detail)

    def test_move_partial_db_failure_rolls_back_and_reports_actual_location(self):
        bad, good = self.file("db-fail.txt"), self.file("move-ok.txt")
        update = self.win.repo.update_file

        def fail_after_write(file):
            if file.id == bad.id:
                self.win.repo.conn.execute("UPDATE files SET path=? WHERE id=?", (file.path, file.id))
                raise RuntimeError("模拟数据库提交失败")
            update(file)

        with patch.object(self.win.repo, "update_file", side_effect=fail_after_write), self.assertLogs("llm_cabinet.ui"):
            self.win._move_files_to_location([bad, good])
        detail = self.details()
        self.assertIn("已移动：1 个", self.info.call_args.args[2])
        self.assertIn("失败：1 个", self.info.call_args.args[2])
        self.assertIn("物理文件已移动", detail)
        self.assertIn("实际位置", detail)
        self.assertIn("RuntimeError: 模拟数据库提交失败", detail)
        self.assertNotIn("原文件保留", detail)
        self.assertTrue((self.target / "db-fail.txt").exists())
        self.assertFalse(Path(bad.path).exists())
        self.assertEqual(self.win.repo.get_file(bad.id).path, bad.path)
        self.assertEqual(self.win.repo.get_file(good.id).path, str((self.target / "move-ok.txt").resolve()))
        self.assertFalse(self.win.repo.conn.in_transaction)

    def test_relink_all_unmatched_paths_are_complete(self):
        files = [self.file(f"unmatched-{i:02}.txt", exists=False) for i in range(12)]
        with self.assertLogs("llm_cabinet.ui"):
            self.win._relink_files_to_directory(files)
        self.assertEqual(self.warn.call_count, 1)
        detail = self.warn.call_args.kwargs["detailed"]
        for file in files:
            self.assert_file_detail(detail, file, self.target / Path(file.path).name)

    def test_relink_partial_success_and_unmatched(self):
        good, missing = self.file("relink-ok.txt"), self.file("not-found.txt", exists=False)
        (self.target / "relink-ok.txt").write_text("新目标", encoding="utf-8")
        with self.assertLogs("llm_cabinet.ui"):
            self.win._relink_files_to_directory([good, missing])
        detail = self.details()
        self.assertIn("已重关联：1 个", self.info.call_args.args[2])
        self.assertIn("未找到同名文件：1 个", self.info.call_args.args[2])
        self.assert_file_detail(detail, missing, self.target / "not-found.txt")

    def test_copy_complete_failure_details(self):
        files = [self.file(f"copy-{i:02}.txt") for i in range(12)]
        with patch.object(self.win.library, "import_copy", side_effect=PermissionError("模拟拒绝访问")), self.assertLogs("llm_cabinet.ui"):
            self.win._convert_files_to_storage(files)
        detail = self.details()
        self.assertIn("失败/跳过：12 个", self.info.call_args.args[2])
        for file in files:
            self.assert_file_detail(detail, file, self.win.library.root / f"project_{self.win.pid}" / Path(file.path).name)
        self.assertIn("PermissionError: 模拟拒绝访问", detail)

    def test_relink_db_failure_preserves_path_and_missing_flag(self):
        file = self.file("relink-db-fail.txt")
        self.win.repo.set_file_missing(file.id, True)
        (self.target / "relink-db-fail.txt").write_text("新目标", encoding="utf-8")

        def fail_after_write(updated):
            self.win.repo.conn.execute("UPDATE files SET path=? WHERE id=?", (updated.path, updated.id))
            raise RuntimeError("模拟重关联写回失败")

        with patch.object(self.win.repo, "update_file", side_effect=fail_after_write), self.assertLogs("llm_cabinet.ui"):
            self.win._relink_files_to_directory([file])
        self.assert_file_detail(self.details(), file, self.target / "relink-db-fail.txt")
        self.assertIn("RuntimeError: 模拟重关联写回失败", self.details())
        stored = self.win.repo.get_file(file.id)
        self.assertEqual(stored.path, file.path)
        self.assertTrue(stored.missing)

    def test_background_errors_include_entire_batch(self):
        files = [self.file(f"worker-{i:02}.txt") for i in range(12)]

        def interrupted(_parent, _title, _label, worker, *, on_done, on_error):
            on_error("ValueError: 模拟后台整体异常")

        for method in (self.win._move_files_to_location, self.win._convert_files_to_storage):
            self.warn.reset_mock()
            with patch("app.ui.mw_files.run_with_progress", side_effect=interrupted), self.assertLogs("llm_cabinet.ui"):
                method(files)
            self.assertEqual(self.warn.call_count, 1)
            detail = self.warn.call_args.kwargs["detailed"]
            for file in files:
                self.assertIn(f"文件 ID：{file.id}", detail)
                self.assertIn(file.path, detail)
            self.assertIn("ValueError: 模拟后台整体异常", detail)
            self.assertIn("状态未确认" if method == self.win._convert_files_to_storage else "位置未确认", detail)

    def test_move_cancelled_unprocessed_are_not_failures(self):
        files = [self.file(f"cancel-{i}.txt") for i in range(3)]

        def cancelled(_parent, _title, _label, worker, *, on_done, on_error):
            on_done(worker(lambda *_: None, lambda: True))

        with patch("app.ui.mw_files.run_with_progress", side_effect=cancelled):
            self.win._move_files_to_location(files)
        self.assertIsNone(self.details())
        self.assertIn("已取消：3 个未处理", self.info.call_args.args[2])
        self.assertNotIn("失败", self.info.call_args.args[2])

    def test_rename_db_failure_reports_actual_location(self):
        file = self.file("rename-before.txt")
        self.win._current_file_row_id = lambda: file.id
        with patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("rename-after.txt", True)), patch.object(
            self.win.repo, "update_file", side_effect=RuntimeError("模拟重命名写回失败"),
        ), self.assertLogs("llm_cabinet.ui"):
            self.win.action_rename_physical_file()
        self.assertEqual(self.warn.call_count, 1)
        detail = self.warn.call_args.kwargs["detailed"]
        self.assert_file_detail(detail, file, self.root / "rename-after.txt")
        self.assertIn("物理文件已重命名", detail)
        self.assertTrue((self.root / "rename-after.txt").exists())
        self.assertEqual(self.win.repo.get_file(file.id).path, file.path)


if __name__ == "__main__":
    unittest.main()
