"""task #31a 自检：文件树交互的数据层底座。

覆盖不依赖 Qt GUI 的关键行为：
- ``Repository.update_file`` 会保留并写回 ``subfolder``
- ``set_file_subfolder`` 支持拖动文件到其它逻辑目录
- ``rename_subfolder`` 会递归更新子层级
- ``explicit_subfolders`` 项目设置可保存空文件夹列表
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T, closing_repos

from app.db import connect
from app.models import FileItem, Project
from app.repository import Repository


def test_file_subfolder_update_and_rename(tmp: Path, t: T) -> None:
    repo = Repository(connect(tmp / "cabinet.db"))
    with closing_repos(repo):
        pid = repo.save_project(Project(title="Tree Ops"))

        a_id = repo.add_file(FileItem(
            project_id=pid, path="a.txt", label="A", kind="other", subfolder="docs",
        ))
        b_id = repo.add_file(FileItem(
            project_id=pid, path="b.txt", label="B", kind="other", subfolder="docs/deep",
        ))
        c_id = repo.add_file(FileItem(
            project_id=pid, path="c.txt", label="C", kind="other", subfolder="",
        ))

        a = repo.get_file(a_id)
        assert a is not None
        a.label = "A renamed"
        a.subfolder = "notes"
        repo.update_file(a)
        t.assert_eq("update_file writes subfolder", repo.get_file(a_id).subfolder, "notes")
        t.assert_eq("update_file writes label", repo.get_file(a_id).label, "A renamed")

        repo.set_file_subfolder(c_id, "docs")
        t.assert_eq("set_file_subfolder moves file", repo.get_file(c_id).subfolder, "docs")

        changed = repo.rename_subfolder(pid, "docs", "archive")
        t.assert_eq("rename_subfolder changed count", changed, 2)
        t.assert_eq("direct child renamed", repo.get_file(c_id).subfolder, "archive")
        t.assert_eq("nested child renamed", repo.get_file(b_id).subfolder, "archive/deep")
        t.assert_eq("unrelated folder unchanged", repo.get_file(a_id).subfolder, "notes")


def test_explicit_subfolders_setting_roundtrip(tmp: Path, t: T) -> None:
    repo = Repository(connect(tmp / "cabinet.db"))
    with closing_repos(repo):
        pid = repo.save_project(Project(title="Empty Folders"))
        subfolders = ["drafts", "drafts/figures", "notes"]
        repo.set_project_setting(
            pid,
            "explicit_subfolders",
            json.dumps(subfolders, ensure_ascii=False),
        )
        raw = repo.get_project_setting(pid, "explicit_subfolders", "[]")
        t.assert_eq("explicit_subfolders json", json.loads(raw), subfolders)


if __name__ == "__main__":
    print("=" * 60)
    print("task31a_files_tree_interactions selftest")
    print("=" * 60)
    t = T()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        test_file_subfolder_update_and_rename(root / "subfolder", t)
        test_explicit_subfolders_setting_roundtrip(root / "explicit", t)
    ok = t.report()
    sys.exit(0 if ok else 1)
