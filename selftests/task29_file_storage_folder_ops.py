"""task #29 T3c 自检：文件夹粒度存储操作的数据层范围。

T3c 的 UI 入口会对某个逻辑 subfolder 下的所有文件批量执行 T1/T2/T3a。
这里验证 Repository 收集范围：
- 命中当前 subfolder 及其子层级
- 不误包含同名前缀但非子层级的目录
- missing_only 只返回缺失文件
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T, closing_repos

from app.db import connect
from app.models import FileItem, Project
from app.repository import Repository


def _add_file(
    repo: Repository,
    project_id: int,
    path: str,
    subfolder: str,
    *,
    missing: bool = False,
) -> int:
    fid = repo.add_file(
        FileItem(
            project_id=project_id,
            path=path,
            is_relative=False,
            label=Path(path).name,
            kind="document",
            subfolder=subfolder,
        )
    )
    if missing:
        repo.set_file_missing(fid, True)
    return fid


def test_folder_scope(tmp: Path, t: T) -> None:
    repo = Repository(connect(tmp / "cabinet.db"))
    with closing_repos(repo):
        pid = repo.save_project(Project(title="Folder Ops"))
        fid_root = _add_file(repo, pid, "root.txt", "")
        fid_docs = _add_file(repo, pid, "docs/a.txt", "docs")
        fid_child = _add_file(repo, pid, "docs/sub/b.txt", "docs/sub", missing=True)
        fid_sibling_prefix = _add_file(repo, pid, "docs-old/c.txt", "docs-old", missing=True)
        fid_other = _add_file(repo, pid, "other/d.txt", "other")

        docs_ids = [f.id for f in repo.list_files_under_subfolder(pid, "docs")]
        t.assert_eq(
            "subfolder scope includes descendants only",
            docs_ids,
            [fid_docs, fid_child],
        )

        missing_ids = [f.id for f in repo.list_files_under_subfolder(pid, "docs", missing_only=True)]
        t.assert_eq("missing_only filters recursive scope", missing_ids, [fid_child])

        empty_ids = [f.id for f in repo.list_files_under_subfolder(pid, "")]
        t.assert_eq("empty subfolder is not a folder-level operation", empty_ids, [])

        all_ids = [f.id for f in repo.list_files(pid)]
        t.assert_eq(
            "all files remain present",
            all_ids,
            [fid_root, fid_docs, fid_child, fid_sibling_prefix, fid_other],
        )


if __name__ == "__main__":
    print("=" * 60)
    print("task29_file_storage_folder_ops selftest")
    print("=" * 60)
    t = T()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        test_folder_scope(Path(d), t)
    ok = t.report()
    sys.exit(0 if ok else 1)
