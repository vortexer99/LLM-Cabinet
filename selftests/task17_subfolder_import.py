"""task #17 自检：子文件夹导入 + subfolder 字段。

流程：
  1. 建临时数据库 + 临时 library 目录
  2. 创建含子目录结构的源文件
  3. 用 PendingFile + _import_one 逻辑导入，验证 subfolder 正确填充
  4. 用 importer._collect_files_to_import 验证递归收集 + subfolder
  5. 验证 schema v7 迁移（files 表有 subfolder 列）
  6. 验证删除文件后 subfolder 逻辑清理（空目录自动消失）
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T, closing_repos

from app.db import connect, SCHEMA_VERSION
from app.library import Library
from app.models import FileItem, PendingFile, Project
from app.repository import Repository


def make_src_tree(tmp: Path) -> Path:
    """创建源文件目录树：

    src/
    ├── top.txt              (subfolder="")
    ├── sub/
    │   ├── a.txt            (subfolder="sub")
    │   └── deep/
    │       └── b.txt        (subfolder="sub/deep")
    └── other/
        └── c.txt            (subfolder="other")
    """
    tmp.mkdir(parents=True, exist_ok=True)
    src = tmp / "src"
    src.mkdir()

    (src / "top.txt").write_text("top level")

    sub = src / "sub"
    sub.mkdir()
    (sub / "a.txt").write_text("in sub")

    deep = sub / "deep"
    deep.mkdir()
    (deep / "b.txt").write_text("in sub/deep")

    other = src / "other"
    other.mkdir()
    (other / "c.txt").write_text("in other")

    return src


# =============================================================================
# Test 1: Schema v7 有 subfolder 列
# =============================================================================
def test_schema_v7_has_subfolder(tmp: Path, t: T) -> None:
    db_path = tmp / "test_schema.db"
    repo = Repository(connect(db_path))
    try:
        t.assert_eq("schema version", SCHEMA_VERSION, 8)

        # 检查 files 表有 subfolder 列
        cols = {r[1] for r in repo.conn.execute("PRAGMA table_info(files)").fetchall()}
        t.assert_true("files has subfolder column", "subfolder" in cols,
                       f"columns={cols}")
    finally:
        repo.conn.close()


# =============================================================================
# Test 2: PendingFile + subfolder 填充
# =============================================================================
def test_pending_file_subfolder(tmp: Path, t: T) -> None:
    src = make_src_tree(tmp)
    db_path = tmp / "test_pending.db"
    lib_root = tmp / "lib"
    lib_root.mkdir()

    repo = Repository(connect(db_path))
    library = Library(lib_root)
    try:
        p = Project(title="Test")
        pid = repo.save_project(p)
        p.id = pid

        # 模拟 _expand_paths 逻辑：递归收集 + subfolder
        root = src.resolve()
        pending: list[PendingFile] = []
        for sub in sorted(root.rglob("*")):
            if sub.is_file():
                rel = sub.parent.relative_to(root)
                sf = rel.as_posix() if str(rel) != "." else ""
                pending.append(PendingFile(src=sub.resolve(), subfolder=sf))

        t.assert_eq("pending file count", len(pending), 4)

        # 验证 subfolder 值
        sf_map = {pf.src.name: pf.subfolder for pf in pending}
        t.assert_eq("top.txt subfolder", sf_map["top.txt"], "")
        t.assert_eq("a.txt subfolder", sf_map["a.txt"], "sub")
        t.assert_eq("b.txt subfolder", sf_map["b.txt"], "sub/deep")
        t.assert_eq("c.txt subfolder", sf_map["c.txt"], "other")

        # 导入（copy 模式）
        for pf in pending:
            rel = library.import_copy(pid, pf.src)
            fi = FileItem(
                project_id=pid,
                path=rel,
                is_relative=True,
                kind="other",
                subfolder=pf.subfolder,
            )
            repo.add_file(fi)

        # 验证数据库中的 subfolder
        files = repo.list_files(pid)
        t.assert_eq("files count", len(files), 4)

        db_sf_map = {Path(f.path).stem: f.subfolder for f in files}
        t.assert_eq("db top subfolder", db_sf_map["top"], "")
        t.assert_eq("db a subfolder", db_sf_map["a"], "sub")
        t.assert_eq("db b subfolder", db_sf_map["b"], "sub/deep")
        t.assert_eq("db c subfolder", db_sf_map["c"], "other")
    finally:
        repo.conn.close()


# =============================================================================
# Test 3: importer._collect_files_to_import 返回 PendingFile
# =============================================================================
def test_importer_collect_with_subfolder(tmp: Path, t: T) -> None:
    from app.importer import ImportPlan, _collect_files_to_import

    src = make_src_tree(tmp)
    plan = ImportPlan(folder=src)

    pending, _file_id_map = _collect_files_to_import(plan)
    t.assert_eq("collected count", len(pending), 4)

    sf_map = {pf.src.name: pf.subfolder for pf in pending}
    t.assert_eq("collect top.txt subfolder", sf_map["top.txt"], "")
    t.assert_eq("collect a.txt subfolder", sf_map["a.txt"], "sub")
    t.assert_eq("collect b.txt subfolder", sf_map["b.txt"], "sub/deep")
    t.assert_eq("collect c.txt subfolder", sf_map["c.txt"], "other")


# =============================================================================
# Test 4: 删除文件后空目录自动消失（数据库层面）
# =============================================================================
def test_delete_clears_empty_subfolder(tmp: Path, t: T) -> None:
    src = make_src_tree(tmp)
    db_path = tmp / "test_delete.db"
    lib_root = tmp / "lib"
    lib_root.mkdir()

    repo = Repository(connect(db_path))
    library = Library(lib_root)
    try:
        p = Project(title="Delete Test")
        pid = repo.save_project(p)
        p.id = pid

        # 导入所有文件
        root = src.resolve()
        for sub in sorted(root.rglob("*")):
            if sub.is_file():
                rel = sub.parent.relative_to(root)
                sf = rel.as_posix() if str(rel) != "." else ""
                rel_path = library.import_copy(pid, sub)
                fi = FileItem(
                    project_id=pid, path=rel_path, is_relative=True,
                    kind="other", subfolder=sf,
                )
                repo.add_file(fi)

        files = repo.list_files(pid)
        t.assert_eq("initial files", len(files), 4)

        # 找到 sub/deep/b.txt 并删除
        b_file = next(f for f in files if Path(f.path).stem == "b")
        repo.delete_file(b_file.id)
        library.remove_relative(b_file.path)

        # 验证 sub/deep 目录下已无文件
        remaining = repo.list_files(pid)
        t.assert_eq("after delete b", len(remaining), 3)

        deep_files = [f for f in remaining if f.subfolder == "sub/deep"]
        t.assert_eq("sub/deep files after delete", len(deep_files), 0)

        # 但 sub/ 目录下还有 a.txt
        sub_files = [f for f in remaining if f.subfolder == "sub"]
        t.assert_eq("sub/ files still exist", len(sub_files), 1)
        t.assert_eq("sub/ file is a.txt", Path(sub_files[0].path).stem, "a")
    finally:
        repo.conn.close()


# =============================================================================
# Test 5: 历史数据兼容 — subfolder 默认空串
# =============================================================================
def test_legacy_files_default_empty_subfolder(tmp: Path, t: T) -> None:
    db_path = tmp / "test_legacy.db"
    repo = Repository(connect(db_path))
    try:
        p = Project(title="Legacy")
        pid = repo.save_project(p)

        # 直接往 files 表插一条不含 subfolder 的记录（模拟历史数据）
        cur = repo.conn.execute(
            "INSERT INTO files(project_id, path, is_relative, kind, ord) "
            "VALUES(?, ?, 0, 'other', 0)",
            (pid, "/some/old/file.txt"),
        )
        repo.conn.commit()

        # 读出来应该 subfolder=""
        f = repo.get_file(cur.lastrowid)
        t.assert_true("legacy file exists", f is not None)
        t.assert_eq("legacy subfolder default", f.subfolder, "")
    finally:
        repo.conn.close()


# =============================================================================
# Main
# =============================================================================
def main() -> bool:
    t = T()
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        test_schema_v7_has_subfolder(tmp / "1", t)
        test_pending_file_subfolder(tmp / "2", t)
        test_importer_collect_with_subfolder(tmp / "3", t)
        test_delete_clears_empty_subfolder(tmp / "4", t)
        test_legacy_files_default_empty_subfolder(tmp / "5", t)
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
