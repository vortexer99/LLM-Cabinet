"""task #46：仅使用临时库及自造文件，模拟回收站验证仓储源文件回收。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import connect
from app.importer import (
    ImportOptions, ImportPlan, cleanup_extracted_zips, copy_files_for_import,
    import_folder_as_project, scan_folders, write_import_file_rows,
)
from app.library import Library
from app.models import FileItem, Project
from app.repository import Repository
from app.storage_cleanup import (
    STORAGE_RECYCLE_SOURCE_KEY, capture_storage_source, cleanup_storage_sources,
    snapshot_cleanup_repo,
)
from app.utils import move_to_trash


class StorageRecycleTests(unittest.TestCase):
    """每个用例独立构造临时数据库，绝不连接默认用户库。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="cabinet_task46_")
        self.root = Path(self.temp.name)
        self.repo = Repository(connect(self.root / "test.db"))
        self.library = Library(self.root / "library")
        self.source = self.root / "source"
        self.source.mkdir()
        self.project = Project(title="测试仓储", storage_mode="copy")
        self.project.id = self.repo.save_project(self.project)
        self.options = ImportOptions(storage_mode="copy")
        self.trash_calls: list[Path] = []
        self.trash_patch = patch("app.storage_cleanup.move_to_trash", self.fake_trash)
        self.trash_patch.start()

    def tearDown(self) -> None:
        self.trash_patch.stop()
        self.repo.conn.close()
        self.temp.cleanup()

    def fake_trash(self, path: Path, *, allow_delete: bool = True) -> str:
        """只操作当前用例生成的临时文件，不触碰系统回收站。"""
        self.assertFalse(allow_delete)
        self.assertTrue(Path(path).is_relative_to(self.root))
        self.assertFalse(self.repo.conn.in_transaction)
        self.trash_calls.append(Path(path))
        Path(path).unlink()
        return "trash"

    def enable(self) -> None:
        self.repo.set_setting(STORAGE_RECYCLE_SOURCE_KEY, "1")

    def make_source(self, name: str = "a.txt") -> Path:
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test source", encoding="utf-8")
        return path

    def candidate(self, source: Path) -> tuple:
        snapshot = capture_storage_source(source)
        rel = self.library.import_copy(self.project.id, source)
        file_id = self.repo.add_file(FileItem(
            project_id=self.project.id, path=rel, is_relative=True, kind="doc",
        ))
        return snapshot, rel, file_id

    def test_default_and_explicit_off_keep_source(self) -> None:
        source = self.make_source()
        item = self.candidate(source)
        self.assertEqual(cleanup_storage_sources(self.repo, self.library, [item]), [])
        self.repo.set_setting(STORAGE_RECYCLE_SOURCE_KEY, "0")
        self.assertEqual(cleanup_storage_sources(self.repo, self.library, [item]), [])
        self.assertTrue(source.exists())
        self.assertEqual(self.trash_calls, [])

    def test_success_retains_directories_and_unimported_files(self) -> None:
        self.enable()
        source = self.make_source("nested/a.txt")
        (self.source / "empty").mkdir()
        metadata = self.source / "README.md"
        metadata.write_text("metadata")
        hidden = self.source / ".hidden"
        hidden.write_text("not imported")
        result = import_folder_as_project(
            self.repo, self.library, ImportPlan(self.source), self.options,
        )
        self.assertEqual(result.n_files, 1)
        self.assertEqual(result.warnings, [])
        self.assertFalse(source.exists())
        self.assertTrue(source.parent.is_dir())
        self.assertTrue((self.source / "empty").is_dir())
        self.assertTrue(metadata.exists())
        self.assertTrue(hidden.exists())

    def test_shared_link_preserves_source(self) -> None:
        self.enable()
        source = self.make_source()
        item = self.candidate(source)
        self.repo.add_file(FileItem(
            project_id=self.project.id, path=str(source), is_relative=False, kind="doc",
        ))
        warnings = cleanup_storage_sources(self.repo, self.library, [item])
        self.assertIn("链接记录", warnings[0])
        self.assertTrue(source.exists())

    def test_library_internal_preserves_source(self) -> None:
        self.enable()
        source = self.library.root / "internal.txt"
        source.write_text("internal")
        warnings = cleanup_storage_sources(self.repo, self.library, [self.candidate(source)])
        self.assertIn("当前仓库内", warnings[0])
        self.assertTrue(source.exists())

    def test_changed_source_and_wrong_target_preserve_source(self) -> None:
        self.enable()
        source = self.make_source()
        item = self.candidate(source)
        os.utime(source, ns=(item[0].mtime_ns, item[0].mtime_ns + 1_000_000))
        warnings = cleanup_storage_sources(self.repo, self.library, [item])
        self.assertIn("发生变化", warnings[0])
        self.assertTrue(source.exists())
        second = self.make_source("b.txt")
        second_item = self.candidate(second)
        self.library.resolve(second_item[1], True).write_text("wrong")
        warnings = cleanup_storage_sources(self.repo, self.library, [second_item])
        self.assertIn("大小", warnings[0])
        self.assertTrue(second.exists())

    def test_duplicate_source_recycled_only_once(self) -> None:
        self.enable()
        source = self.make_source()
        entries = [self.candidate(source), self.candidate(source)]
        self.assertEqual(cleanup_storage_sources(self.repo, self.library, entries), [])
        self.assertEqual(self.trash_calls, [source])

    def test_transaction_and_changed_record_preserve_source(self) -> None:
        self.enable()
        source = self.make_source()
        item = self.candidate(source)
        self.repo.conn.execute("UPDATE files SET label='事务中' WHERE id=?", (item[2],))
        warnings = cleanup_storage_sources(self.repo, self.library, [item])
        self.assertIn("未提交", warnings[0])
        self.repo.conn.rollback()
        self.repo.delete_file(item[2])
        warnings = cleanup_storage_sources(self.repo, self.library, [item])
        self.assertIn("记录", warnings[0])
        self.assertTrue(source.exists())

    def test_recycle_failure_does_not_fail_import(self) -> None:
        self.enable()
        source = self.make_source()
        with patch("app.storage_cleanup.move_to_trash", side_effect=OSError("回收站不可用")):
            result = import_folder_as_project(
                self.repo, self.library, ImportPlan(self.source), self.options,
            )
        self.assertEqual(result.n_files, 1)
        self.assertIn("回收源文件失败", result.warnings[0])
        self.assertTrue(source.exists())

    def test_copy_database_failure_and_cancel_keep_unfinished_sources(self) -> None:
        self.enable()
        first = self.make_source("a.txt")
        second = self.make_source("b.txt")
        plan = ImportPlan(self.source)
        warnings: list[str] = []
        with patch.object(self.library, "import_copy", side_effect=OSError("copy failed")):
            prepared = copy_files_for_import(
                self.library, self.project.id, plan, self.options, warnings,
            )
        self.assertEqual(prepared, [])
        self.assertTrue(first.exists() and second.exists())
        prepared = copy_files_for_import(
            self.library, self.project.id, plan, self.options, warnings,
            is_cancelled=iter([False, True]).__next__,
        )
        self.assertEqual(len(prepared), 1)
        with patch.object(self.repo, "add_file", side_effect=OSError("db failed")):
            self.assertEqual(write_import_file_rows(
                self.repo, self.project, plan, prepared, warnings, library=self.library,
            ), 0)
        self.assertTrue(first.exists() and second.exists())
        self.assertEqual(write_import_file_rows(
            self.repo, self.project, plan, prepared, warnings, library=self.library,
        ), 1)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())

    def test_zip_and_link_import_never_recycle(self) -> None:
        self.enable()
        source = self.make_source()
        archive = self.root / "source.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.write(source, "a.txt")
        plans = scan_folders([archive], self.repo)
        try:
            extracted = plans[0].folder / "a.txt"
            result = import_folder_as_project(self.repo, self.library, plans[0], self.options)
            self.assertEqual(result.n_files, 1)
            self.assertTrue(archive.exists() and extracted.exists())
            self.assertEqual(self.trash_calls, [])
        finally:
            cleanup_extracted_zips(plans)
        self.assertFalse(extracted.exists())
        result = import_folder_as_project(
            self.repo, self.library, ImportPlan(self.source), ImportOptions(storage_mode="link"),
        )
        self.assertEqual(result.n_files, 1)
        self.assertTrue(source.exists())
        self.assertEqual(self.trash_calls, [])

    def test_missing_library_keeps_source(self) -> None:
        self.enable()
        source = self.make_source()
        plan = ImportPlan(self.source)
        warnings: list[str] = []
        prepared = copy_files_for_import(
            self.library, self.project.id, plan, self.options, warnings,
        )
        self.assertEqual(write_import_file_rows(
            self.repo, self.project, plan, prepared, warnings,
        ), 1)
        self.assertIn("未提供当前仓库", warnings[0])
        self.assertTrue(source.exists())

    def test_strict_trash_failure_never_unlinks(self) -> None:
        source = self.make_source()
        failing_send = types.SimpleNamespace(send2trash=lambda path: (_ for _ in ()).throw(OSError("失败")))
        with patch.dict(sys.modules, {"send2trash": failing_send}):
            with self.assertRaises(OSError):
                move_to_trash(source, allow_delete=False)
            self.assertTrue(source.exists())
            self.assertEqual(move_to_trash(source), "deleted")
            self.assertFalse(source.exists())

    def test_deferred_batch_cleanup_deduplicates_across_projects(self) -> None:
        self.enable()
        source = self.make_source()
        plan = ImportPlan(self.source)
        warnings: list[str] = []
        candidates: list[tuple] = []
        for _ in range(2):
            project = Project(title="批量项目", storage_mode="copy")
            project.id = self.repo.save_project(project)
            prepared = copy_files_for_import(
                self.library, project.id, plan, self.options, warnings,
            )
            self.assertEqual(write_import_file_rows(
                self.repo, project, plan, prepared, warnings,
                library=self.library, cleanup_candidates=candidates,
            ), 1)
            self.assertTrue(source.exists())
        self.assertEqual(len(candidates), 2)
        self.assertEqual(cleanup_storage_sources(self.repo, self.library, candidates), [])
        self.assertEqual(self.trash_calls, [source])
        self.assertEqual(warnings, [])

    def test_snapshot_worker_never_reads_live_repository(self) -> None:
        self.enable()
        source = self.make_source()
        candidate = self.candidate(source)
        live_file = self.repo.get_file(candidate[2])
        with patch.object(self.repo, "get_file", return_value=live_file):
            snapshot = snapshot_cleanup_repo(self.repo, [candidate])
        live_file.path = "changed-after-snapshot"
        result: list[list[str]] = []
        failures: list[BaseException] = []
        calls: list[Path] = []

        def worker_trash(path: Path, *, allow_delete: bool = True) -> str:
            self.assertFalse(allow_delete)
            self.assertNotEqual(threading.get_ident(), main_thread)
            calls.append(Path(path))
            Path(path).unlink()
            return "trash"

        def worker() -> None:
            try:
                result.append(cleanup_storage_sources(snapshot, self.library, [candidate]))
            except BaseException as exc:
                failures.append(exc)

        main_thread = threading.get_ident()
        # 真正在线程中使用快照；禁止所有真实 repo 访问，SQLite 线程检查也会兜底。
        with patch.object(self.repo, "get_file", side_effect=AssertionError("访问真实仓库")), \
                patch.object(self.repo, "get_setting", side_effect=AssertionError("访问真实设置")), \
                patch("app.storage_cleanup.move_to_trash", side_effect=worker_trash):
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(result, [[]])
        self.assertEqual(calls, [source])

    def test_cancellation_keeps_remaining_sources_with_progress(self) -> None:
        self.enable()
        first = self.make_source("a.txt")
        second = self.make_source("b.txt")
        candidates = [self.candidate(first), self.candidate(second)]
        snapshot = snapshot_cleanup_repo(self.repo, candidates)
        progress: list[tuple[int, int, str]] = []
        warnings = cleanup_storage_sources(
            snapshot, self.library, candidates,
            is_cancelled=iter([False, True]).__next__,
            progress=lambda *values: progress.append(values),
        )
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual(self.trash_calls, [first])
        self.assertIn("剩余 1 个源文件已保留", warnings[0])
        self.assertEqual(progress[0][:2], (0, 2))
        self.assertEqual(progress[-1][:2], (2, 2))


if __name__ == "__main__":
    unittest.main()
