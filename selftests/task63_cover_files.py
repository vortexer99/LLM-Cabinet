"""封面替换安全性与故障回滚；仅使用显式临时数据库和自造文件。"""
from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cover_files import (
    CoverFileError, discard_cover_snapshot, replace_project_cover, save_cover_snapshot,
)
from app.db import connect
from app.library import Library
from app.models import FileItem, Project
from app.repository import Repository


class SyntheticPixmap:
    """只模拟落盘，不启动 Qt 或读取用户媒体。"""

    def __init__(self, result: bool = True):
        self.result = result

    def save(self, path: str, _format: str) -> bool:
        Path(path).write_bytes(b"synthetic-png")
        return self.result


@contextmanager
def fixture():
    with tempfile.TemporaryDirectory(prefix="cabinet-cover-files-") as temporary:
        root = Path(temporary)
        repo = Repository(connect(root / "test-only.db"))
        library = Library(root / "test-only-library")
        project = Project(title="测试封面")
        repo.save_project(project)
        try:
            yield root, repo, library, project
        finally:
            repo.conn.close()


def image(repo: Repository, library: Library, project: Project,
          name: str, origin: str = "generated") -> int:
    full = library.project_dir(project.id) / name
    full.write_bytes(b"synthetic-image")
    return repo.add_file(FileItem(project_id=project.id, path=full.relative_to(library.root).as_posix(),
                                 is_relative=True, kind="image", origin=origin))


def recycle_into(root: Path):
    def recycle(path: Path, *, allow_delete: bool) -> str:
        assert allow_delete is False
        trash = root / "synthetic-recycle-bin"
        trash.mkdir(exist_ok=True)
        Path(path).rename(trash / Path(path).name)
        return "trash"
    return recycle


class CoverFilesTests(unittest.TestCase):
    def test_default_keeps_previous_and_history(self):
        with fixture() as (root, repo, library, project):
            older = image(repo, library, project, "older.png")
            old = image(repo, library, project, "old.png")
            new = image(repo, library, project, "new.png")
            project.cover_file_id = old
            repo.save_project(project)
            with patch("app.cover_files.move_to_trash") as trash:
                self.assertEqual(replace_project_cover(repo, library, project, new), [])
                trash.assert_not_called()
            self.assertEqual(repo.get_project(project.id).cover_file_id, new)
            self.assertIsNotNone(repo.get_file(older))
            self.assertIsNotNone(repo.get_file(old))

    def test_replaces_only_previous_after_reference_saved(self):
        with fixture() as (root, repo, library, project):
            ids = [image(repo, library, project, name) for name in ("older.png", "old.png", "new.png")]
            project.cover_file_id = ids[1]
            repo.save_project(project)
            repo.set_setting("cover_replace_generated", "1")
            recycle = recycle_into(root)

            def checked_recycle(path, *, allow_delete):
                self.assertEqual(repo.get_project(project.id).cover_file_id, ids[2])
                return recycle(path, allow_delete=allow_delete)

            with patch("app.cover_files.move_to_trash", checked_recycle):
                self.assertEqual(replace_project_cover(repo, library, project, ids[2]), [])
            self.assertIsNotNone(repo.get_file(ids[0]))
            self.assertIsNone(repo.get_file(ids[1]))
            self.assertTrue((library.project_dir(project.id) / "older.png").exists())

    def test_user_image_is_never_recycled(self):
        with fixture() as (root, repo, library, project):
            old = image(repo, library, project, "user.png", "user")
            new = image(repo, library, project, "new.png")
            project.cover_file_id = old
            repo.save_project(project)
            repo.set_setting("cover_replace_generated", "1")
            with patch("app.cover_files.move_to_trash") as trash:
                self.assertEqual(replace_project_cover(repo, library, project, new), [])
                trash.assert_not_called()
            self.assertIsNotNone(repo.get_file(old))

    def test_unsafe_paths_shared_records_and_cover_references_are_retained(self):
        for case in ("absolute", "outside", "other-project", "shared", "referenced", "hardlink", "reparse"):
            with self.subTest(case=case), fixture() as (root, repo, library, project):
                fid = image(repo, library, project, "unsafe.png")
                file = repo.get_file(fid)
                full = library.resolve(file.path, True)
                if case == "absolute":
                    file.path, file.is_relative = str(full), False
                elif case == "outside":
                    outside = root / "outside.png"
                    outside.write_bytes(b"outside")
                    file.path = "../outside.png"
                elif case == "other-project":
                    file.path = "project_999/unsafe.png"
                elif case == "shared":
                    repo.add_file(FileItem(project_id=project.id, path=str(full), kind="image"))
                elif case == "referenced":
                    repo.save_project(Project(title="其他项目引用", cover_file_id=fid))
                elif case == "hardlink":
                    os.link(full, root / "another-hard-link.png")
                with patch("app.cover_files.move_to_trash") as trash:
                    if case == "reparse":
                        original = Path.lstat

                        def with_reparse(path, *args, **kwargs):
                            value = original(path, *args, **kwargs)
                            if path == full.parent:
                                class ReparseStat:
                                    st_mode = value.st_mode
                                    st_file_attributes = 0x400
                                return ReparseStat()
                            return value

                        with patch.object(Path, "lstat", with_reparse):
                            warnings = discard_cover_snapshot(repo, library, file)
                    else:
                        warnings = discard_cover_snapshot(repo, library, file)
                    self.assertTrue(warnings)
                    trash.assert_not_called()
                self.assertTrue(full.exists())
                self.assertIsNotNone(repo.get_file(fid))

    def test_recycle_failure_keeps_file_and_record(self):
        with fixture() as (root, repo, library, project):
            fid = image(repo, library, project, "locked.png")
            file = repo.get_file(fid)
            with patch("app.cover_files.move_to_trash", side_effect=PermissionError("被占用")):
                warnings = discard_cover_snapshot(repo, library, file)
            self.assertIn("PermissionError: 被占用", warnings[0])
            self.assertIn(str(library.resolve(file.path, True)), warnings[0])
            self.assertIsNotNone(repo.get_file(fid))
            self.assertTrue(library.resolve(file.path, True).exists())

    def test_record_delete_failure_reports_actual_recycled_state(self):
        with fixture() as (root, repo, library, project):
            fid = image(repo, library, project, "db-locked.png")
            file = repo.get_file(fid)
            repo.conn.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON files BEGIN SELECT RAISE(ABORT,'拒绝删除'); END")
            repo.conn.commit()
            with patch("app.cover_files.move_to_trash", recycle_into(root)):
                warnings = discard_cover_snapshot(repo, library, file)
            self.assertIn("文件已移入系统回收站", warnings[0])
            self.assertIsNotNone(repo.get_file(fid))
            self.assertFalse(library.resolve(file.path, True).exists())
            self.assertFalse(repo.conn.in_transaction)

    def test_failed_snapshot_save_or_registration_removes_only_new_file(self):
        for failure in ("save", "register", "after-commit"):
            with self.subTest(failure=failure), fixture() as (root, repo, library, project):
                old = image(repo, library, project, "old.png")
                project.cover_file_id = old
                repo.save_project(project)
                real_add = repo.add_file

                def fail_add(file):
                    if failure == "after-commit":
                        real_add(file)
                    raise RuntimeError("登记失败")

                with patch("app.cover_files.move_to_trash", recycle_into(root)):
                    if failure == "save":
                        with self.assertRaises(CoverFileError):
                            save_cover_snapshot(repo, library, project, SyntheticPixmap(False), "video")
                    else:
                        with patch.object(repo, "add_file", fail_add), self.assertRaises(CoverFileError):
                            save_cover_snapshot(repo, library, project, SyntheticPixmap(), "video")
                self.assertEqual(repo.get_project(project.id).cover_file_id, old)
                self.assertEqual([file.id for file in repo.list_files(project.id)], [old])
                self.assertEqual([path.name for path in library.project_dir(project.id).iterdir()], ["old.png"])

    def test_project_save_failure_rolls_back_without_cleaning_previous(self):
        with fixture() as (root, repo, library, project):
            old = image(repo, library, project, "old.png")
            new = image(repo, library, project, "new.png")
            project.cover_file_id = old
            repo.save_project(project)
            repo.set_setting("cover_replace_generated", "1")

            def fail_save(project):
                repo.conn.execute("UPDATE projects SET cover_file_id=? WHERE id=?", (new, project.id))
                raise RuntimeError("保存引用失败")

            with patch.object(repo, "save_project", fail_save), patch("app.cover_files.move_to_trash") as trash:
                with self.assertRaises(CoverFileError) as caught:
                    replace_project_cover(repo, library, project, new)
                trash.assert_not_called()
            self.assertFalse(caught.exception.rollback_failed)
            self.assertEqual(repo.get_project(project.id).cover_file_id, old)
            self.assertEqual(project.cover_file_id, old)
            self.assertIsNotNone(repo.get_file(old))

    def test_existing_transaction_is_not_committed_or_rolled_back(self):
        with fixture() as (root, repo, library, project):
            new = image(repo, library, project, "new.png")
            repo.conn.execute("UPDATE projects SET title='外部未提交变更' WHERE id=?", (project.id,))
            with self.assertRaises(CoverFileError):
                replace_project_cover(repo, library, project, new)
            with self.assertRaises(CoverFileError):
                save_cover_snapshot(repo, library, project, SyntheticPixmap(), "video")
            self.assertTrue(repo.conn.in_transaction)
            self.assertEqual(repo.get_project(project.id).title, "外部未提交变更")
            self.assertIsNone(project.cover_file_id)
            repo.conn.rollback()
            self.assertEqual(repo.get_project(project.id).title, "测试封面")

    def test_lookup_failure_and_invalid_record_include_structured_details(self):
        with fixture() as (root, repo, library, project):
            with patch.object(repo, "get_file", side_effect=RuntimeError("查询失败")):
                with self.assertRaises(CoverFileError) as caught:
                    replace_project_cover(repo, library, project, 123)
            self.assertIn("记录 ID：123", str(caught.exception))
            self.assertIn("新封面路径未能读取", str(caught.exception))
            self.assertIn("RuntimeError: 查询失败", str(caught.exception))
            with self.assertRaises(CoverFileError) as missing:
                replace_project_cover(repo, library, project, 456)
            self.assertIn("读取并校验新封面文件记录", str(missing.exception))

    def test_rollback_failure_keeps_files_and_explicitly_reports_state(self):
        with fixture() as (root, repo, library, project):
            new = image(repo, library, project, "new.png")
            real_connection = repo.conn

            class BrokenRollbackConnection:
                def __getattr__(self, key):
                    return getattr(real_connection, key)

                def rollback(self):
                    raise OSError("回滚故障")

            repo.conn = BrokenRollbackConnection()
            with patch.object(repo, "save_project", side_effect=RuntimeError("保存故障")):
                with self.assertRaises(CoverFileError) as caught:
                    replace_project_cover(repo, library, project, new)
            self.assertTrue(caught.exception.rollback_failed)
            self.assertIn("OSError: 回滚故障", str(caught.exception))
            self.assertIn("保留新旧文件及记录", str(caught.exception))
            self.assertTrue((library.project_dir(project.id) / "new.png").exists())

    def test_failed_new_snapshot_cleanup_reports_its_actual_location(self):
        with fixture() as (root, repo, library, project):
            with patch("app.cover_files.move_to_trash", side_effect=PermissionError("回收失败")):
                with self.assertRaises(CoverFileError) as caught:
                    save_cover_snapshot(repo, library, project, SyntheticPixmap(False), "clipboard")
            remaining = list(library.project_dir(project.id).glob("__cover_*.png"))
            self.assertEqual(len(remaining), 1)
            self.assertIn(str(remaining[0]), str(caught.exception))
            self.assertIn("PermissionError: 回收失败", str(caught.exception))
            self.assertEqual(repo.list_files(project.id), [])


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)  # 故障注入属于预期结果，避免刷屏。
    unittest.main()
