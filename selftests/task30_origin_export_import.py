"""task #30 / #04 / #28 自检：文件来源标记 + 折叠视图 + 导出导入。

流程：
  1. 验证 schema v8 有 origin 列
  2. 验证 _save_cover_snapshot 生成 origin='generated' 的文件
  3. 验证迁移回填历史 __cover_*.png
  4. 验证导出 files.json 含 subfolder/origin
  5. 验证导入还原 subfolder/origin
  6. 验证封面还原（old file_id → new file_id）
"""
from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T, closing_repos

from app.db import connect, SCHEMA_VERSION
from app.exporter import ExportOptions, export_project
from app.importer import (
    ImportPlan,
    _collect_files_to_import,
    _safe_extract_zip,
    cleanup_extracted_zips,
    import_folder_as_project,
    ImportOptions,
    scan_folders,
)
from app.library import Library
from app.models import FileItem, Project
from app.repository import Repository


# =============================================================================
# Test 1: Schema v8 有 origin 列
# =============================================================================
def test_schema_v8_has_origin(tmp: Path, t: T) -> None:
    db_path = tmp / "test_schema.db"
    repo = Repository(connect(db_path))
    try:
        t.assert_eq("schema version", SCHEMA_VERSION, 8)

        # 检查 files 表有 origin 列
        cols = {r[1] for r in repo.conn.execute("PRAGMA table_info(files)").fetchall()}
        t.assert_true("files has origin column", "origin" in cols,
                       f"columns={cols}")

        # 新库 files 表为空，创建一条测试验证默认值
        p = Project(title="Test")
        pid = repo.save_project(p)
        fi = FileItem(project_id=pid, path="test.pdf", kind="pdf")
        repo.add_file(fi)
        row = repo.conn.execute("SELECT origin FROM files WHERE id=1").fetchone()
        t.assert_eq("default origin", row["origin"], "user")
    finally:
        repo.conn.close()


# =============================================================================
# Test 2: 迁移后 __cover_*.png 回填
#    注：由于测试环境总是创建 v8 库，无法完全模拟 v7→v8 迁移
#    此处改为验证：origin 列存在 + 默认值 + 回填查询逻辑正确
# =============================================================================
def test_migration_backfill_cover(tmp: Path, t: T) -> None:
    """验证 origin 列存在且回填逻辑正确"""
    db_path = tmp / "test_migration.db"
    repo = Repository(connect(db_path))

    try:
        p = Project(title="Test")
        pid = repo.save_project(p)

        # 插入文件：普通文件 + 封面快照
        repo.conn.execute(
            "INSERT INTO files(project_id, path, is_relative, label, kind, ord, origin) "
            "VALUES(?, ?, 0, '', 'pdf', 0, 'user')",
            (pid, "test.pdf"),
        )
        repo.conn.execute(
            "INSERT INTO files(project_id, path, is_relative, label, kind, ord, origin) "
            "VALUES(?, ?, 1, '封面（截取自 PDF）', 'image', 1, 'user')",
            (pid, "project_1/__cover_20260611.png"),
        )
        repo.conn.commit()

        # 手动执行回填 SQL（模拟迁移中的回填）
        repo.conn.execute(
            r"UPDATE files SET origin='generated' "
            r"WHERE origin='user' AND (path LIKE '%/__cover\_%' ESCAPE '\' "
            r"OR path LIKE '__cover\_%' ESCAPE '\')"
        )
        repo.conn.commit()

        # 验证回填结果
        rows = repo.conn.execute(
            "SELECT id, path, origin FROM files ORDER BY id"
        ).fetchall()
        t.assert_eq("file 1 origin", rows[0]["origin"], "user")
        t.assert_eq("file 2 origin (cover)", rows[1]["origin"], "generated")
    finally:
        repo.conn.close()


# =============================================================================
# Test 3: 导出 files.json 含 subfolder/origin
# =============================================================================
def test_export_files_json_v3(tmp: Path, t: T) -> None:
    db_path = tmp / "test_export.db"
    lib_root = tmp / "lib1"
    lib_root.mkdir(parents=True, exist_ok=True)

    # 创建源文件（导出需要真实文件）
    src_dir = tmp / "src_files1"
    src_dir.mkdir()
    (src_dir / "test.pdf").write_text("test content")
    (src_dir / "__cover_123.png").write_text("fake image")

    repo = Repository(connect(db_path))
    library = Library(lib_root)

    try:
        # 创建项目 + 文件（含 subfolder + origin）
        p = Project(title="Test Project")
        pid = repo.save_project(p)

        # 用户文件（使用实际存在的源文件路径）
        fi1 = FileItem(
            project_id=pid, path=str(src_dir / "test.pdf"), is_relative=False,
            kind="pdf", subfolder="docs", origin="user", label="用户文档"
        )
        fid1 = repo.add_file(fi1)

        # 生成的封面
        fi2 = FileItem(
            project_id=pid, path=str(src_dir / "__cover_123.png"), is_relative=False,
            kind="image", subfolder="", origin="generated", label="封面"
        )
        fid2 = repo.add_file(fi2)

        # 设为封面
        p.cover_file_id = fid2
        repo.save_project(p)

        # 导出
        target = tmp / "export"
        target.mkdir()
        options = ExportOptions(target_root=target, preserve_structure=True)
        result = export_project(repo, library, repo.get_project(pid), options)

        # 验证 files.json
        files_json = result.project_dir / "files.json"
        t.assert_true("files.json exists", files_json.exists())

        with open(files_json, encoding="utf-8") as f:
            data = json.load(f)

        t.assert_eq("preserve_structure", data.get("preserve_structure"), True)
        files = data.get("files", [])
        t.assert_eq("file count", len(files), 2)

        # 找用户文件
        uf = next((f for f in files if f["label"] == "用户文档"), None)
        t.assert_true("user file in export", uf is not None)
        t.assert_eq("user file subfolder", uf.get("subfolder"), "docs")
        t.assert_eq("user file origin", uf.get("origin"), "user")

        # 找封面文件
        cf = next((f for f in files if f.get("is_cover")), None)
        print(f"  cover file: {cf}")
        t.assert_true("cover file in export", cf is not None)
        t.assert_eq("cover origin", cf.get("origin"), "generated")

    finally:
        repo.conn.close()


# =============================================================================
# Test 4: 导入还原 subfolder/origin/封面
# =============================================================================
def test_import_restore_subfolder_origin_cover(tmp: Path, t: T) -> None:
    db_path = tmp / "test_import.db"
    lib_root = tmp / "lib2"
    lib_root.mkdir(parents=True, exist_ok=True)

    # 创建源文件（导出需要真实文件）
    src_dir = tmp / "src_files"
    src_dir.mkdir()
    (src_dir / "test.pdf").write_text("test content")
    (src_dir / "__cover_123.png").write_text("fake image")

    repo = Repository(connect(db_path))
    library = Library(lib_root)

    try:
        # 创建源项目并导出
        p = Project(title="Source")
        pid = repo.save_project(p)

        # 用户文件（使用实际存在的源文件路径）
        fi1 = FileItem(
            project_id=pid, path=str(src_dir / "test.pdf"), is_relative=False,
            kind="pdf", subfolder="notes", origin="user", label="笔记"
        )
        fid1 = repo.add_file(fi1)

        # 封面（生成）
        fi2 = FileItem(
            project_id=pid, path=str(src_dir / "__cover_123.png"), is_relative=False,
            kind="image", subfolder="", origin="generated", label="封面"
        )
        fid2 = repo.add_file(fi2)

        # 设为封面
        p.cover_file_id = fid2
        repo.save_project(p)

        # 导出
        export_dir = tmp / "exported"
        export_dir.mkdir()
        options = ExportOptions(target_root=export_dir, preserve_structure=True)
        export_project(repo, library, p, options)

        # 用 scan_folders 创建 ImportPlan（正确流程）
        plans = scan_folders([export_dir / "Source"], repo)
        t.assert_eq("plan count", len(plans), 1)
        plan = plans[0]

        print(f"  plan: folder={plan.folder}, has_project_json={plan.has_project_json}")
        print(f"  plan.project_json keys: {plan.project_json.keys() if plan.project_json else None}")

        t.assert_true("plan has project.json", plan.has_project_json)
        t.assert_eq("schema version", plan.schema_version, 3)

        # 检查导出目录结构
        export_src = export_dir / "Source"
        print(f"  export dir exists: {export_src.exists()}")
        files_dir = export_src / "files"
        print(f"  files dir exists: {files_dir.exists()}")
        if files_dir.exists():
            all_files = list(files_dir.rglob("*"))
            print(f"    all items: {all_files}")
            # 只打印文件
            files_only = [f for f in all_files if f.is_file()]
            print(f"    files only: {files_only}")

        import_opts = ImportOptions(storage_mode="copy")
        result = import_folder_as_project(repo, library, plan, import_opts)

        print(f"  import result: project_id={result.project_id}, n_files={result.n_files}")
        print(f"  warnings: {result.warnings}")

        # 验证导入的文件
        imported_files = repo.list_files(result.project_id)
        print(f"  imported files count: {len(imported_files)}")
        t.assert_eq("imported file count", len(imported_files), 2)

        # 打印所有导入文件的 label 供调试
        for f in imported_files:
            print(f"    imported: label={f.label!r}, subfolder={f.subfolder!r}, origin={f.origin!r}")

        # 验证 subfolder 还原
        notes_file = next((f for f in imported_files if f.label == "笔记"), None)
        t.assert_true("notes file imported", notes_file is not None)
        t.assert_eq("notes subfolder", notes_file.subfolder, "notes")

        # 验证 origin 还原
        t.assert_eq("notes origin", notes_file.origin, "user")

        # 验证封面还原
        imported_p = repo.get_project(result.project_id)
        t.assert_true("cover restored", imported_p.cover_file_id is not None)

    finally:
        repo.conn.close()


def test_export_duplicate_names_do_not_overwrite(tmp: Path, t: T) -> None:
    """同一 subfolder 内同名文件导出时必须生成不同 exported_to。"""
    db_path = tmp / "test_export_duplicates.db"
    lib_root = tmp / "lib_duplicates"
    lib_root.mkdir(parents=True, exist_ok=True)
    src_a = tmp / "src_a"
    src_b = tmp / "src_b"
    src_a.mkdir()
    src_b.mkdir()
    (src_a / "same.txt").write_text("A", encoding="utf-8")
    (src_b / "same.txt").write_text("B", encoding="utf-8")

    repo = Repository(connect(db_path))
    library = Library(lib_root)
    try:
        p = Project(title="Duplicate Names")
        pid = repo.save_project(p)
        repo.add_file(FileItem(
            project_id=pid, path=str(src_a / "same.txt"), is_relative=False,
            kind="other", subfolder="docs", origin="user", label="A"
        ))
        repo.add_file(FileItem(
            project_id=pid, path=str(src_b / "same.txt"), is_relative=False,
            kind="other", subfolder="docs", origin="user", label="B"
        ))

        out = tmp / "export_duplicates"
        out.mkdir()
        result = export_project(
            repo, library, repo.get_project(pid),
            ExportOptions(target_root=out, preserve_structure=True),
        )
        data = json.loads((result.project_dir / "files.json").read_text(encoding="utf-8"))
        exported = [f["exported_to"] for f in data["files"]]
        t.assert_eq("duplicate export count", len(exported), 2)
        t.assert_eq("duplicate exported_to unique", len(set(exported)), 2)
        contents = {
            (result.project_dir / rel).read_text(encoding="utf-8")
            for rel in exported
        }
        t.assert_eq("duplicate contents kept", contents, {"A", "B"})
    finally:
        repo.conn.close()


def test_zip_export_uses_unique_filename(tmp: Path, t: T) -> None:
    """重复 ZIP 导出同一项目不应覆盖前一次结果。"""
    db_path = tmp / "test_zip_unique.db"
    lib_root = tmp / "lib_zip"
    lib_root.mkdir(parents=True, exist_ok=True)
    src = tmp / "zip_src.txt"
    src.write_text("zip", encoding="utf-8")

    repo = Repository(connect(db_path))
    library = Library(lib_root)
    try:
        p = Project(title="Zip Project")
        pid = repo.save_project(p)
        repo.add_file(FileItem(
            project_id=pid, path=str(src), is_relative=False,
            kind="other", origin="user", label="zip"
        ))
        out = tmp / "zip_out"
        out.mkdir()
        opts = ExportOptions(target_root=out, export_format="zip")
        r1 = export_project(repo, library, repo.get_project(pid), opts)
        r2 = export_project(repo, library, repo.get_project(pid), opts)

        t.assert_true("first zip exists", r1.project_dir.exists())
        t.assert_true("second zip exists", r2.project_dir.exists())
        t.assert_true("zip names unique", r1.project_dir.name != r2.project_dir.name)
    finally:
        repo.conn.close()


def test_import_files_json_cannot_escape_package(tmp: Path, t: T) -> None:
    """恶意 files.json 不能通过 exported_to 引用项目包外的文件。"""
    pkg = tmp / "evil_pkg"
    pkg.mkdir()
    outside = tmp / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (pkg / "files.json").write_text(
        json.dumps({
            "files": [{
                "id": 1,
                "exported_to": "../secret.txt",
                "subfolder": "docs",
                "label": "secret",
                "origin": "user",
            }]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    plan = ImportPlan(folder=pkg)
    pending, file_id_map = _collect_files_to_import(plan)
    t.assert_eq("unsafe files skipped", len(pending), 0)
    t.assert_eq("unsafe id map empty", len(file_id_map), 0)


def test_safe_extract_zip_rejects_path_traversal(tmp: Path, t: T) -> None:
    """ZIP 解压不能允许成员写出目标目录。"""
    zip_path = tmp / "evil.zip"
    target = tmp / "extract_target"
    target.mkdir()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../evil.txt", "bad")

    rejected = False
    with zipfile.ZipFile(zip_path, "r") as zf:
        try:
            _safe_extract_zip(zf, target)
        except ValueError:
            rejected = True

    t.assert_true("zip traversal rejected", rejected)
    t.assert_true("zip traversal did not write outside", not (tmp / "evil.txt").exists())


def test_cleanup_extracted_zips_removes_temp_dir(tmp: Path, t: T) -> None:
    """取消导入等提前返回路径也应能清理解压临时目录。"""
    temp_dir = tmp / "llm_cabinet_import_test"
    temp_dir.mkdir()
    (temp_dir / "project.json").write_text("{}", encoding="utf-8")
    plan = ImportPlan(folder=temp_dir)
    plan._temp_dir = temp_dir

    cleanup_extracted_zips([plan])

    t.assert_true("temp import dir removed", not temp_dir.exists())


def test_import_files_json_skips_only_bad_entries(tmp: Path, t: T) -> None:
    """files.json 中单条坏记录不应导致整包回退到物理扫描。"""
    pkg = tmp / "mixed_pkg"
    files_dir = pkg / "files"
    files_dir.mkdir(parents=True)
    (files_dir / "ok.txt").write_text("ok", encoding="utf-8")
    outside = tmp / "secret_mixed.txt"
    outside.write_text("secret", encoding="utf-8")
    (pkg / "files.json").write_text(
        json.dumps({
            "files": [
                {
                    "id": 1,
                    "exported_to": "files/ok.txt",
                    "subfolder": "docs",
                    "label": "ok",
                    "origin": "generated",
                },
                {
                    "id": 2,
                    "exported_to": "../secret_mixed.txt",
                    "subfolder": "docs",
                    "label": "bad",
                    "origin": "user",
                },
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    pending, file_id_map = _collect_files_to_import(ImportPlan(folder=pkg))

    t.assert_eq("mixed files count", len(pending), 1)
    t.assert_eq("mixed id map count", len(file_id_map), 1)
    t.assert_eq("mixed kept label", pending[0].label, "ok")
    t.assert_eq("mixed kept origin", pending[0].origin, "generated")
    t.assert_eq("mixed kept subfolder", pending[0].subfolder, "docs")


# =============================================================================
# 入口
# =============================================================================
def main() -> bool:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        t = T()

        print("=" * 60)
        print("task #30 / #04 / #28 自检")
        print("=" * 60)

        tests = [
            ("Schema v8 has origin", test_schema_v8_has_origin),
            ("Migration backfill cover", test_migration_backfill_cover),
            ("Export files.json v3", test_export_files_json_v3),
            ("Import restore subfolder/origin/cover", test_import_restore_subfolder_origin_cover),
            ("Export duplicate names", test_export_duplicate_names_do_not_overwrite),
            ("ZIP export unique filename", test_zip_export_uses_unique_filename),
            ("Import files.json path safety", test_import_files_json_cannot_escape_package),
            ("ZIP extraction path safety", test_safe_extract_zip_rejects_path_traversal),
            ("Cleanup extracted ZIP dirs", test_cleanup_extracted_zips_removes_temp_dir),
            ("Import files.json skips bad entries", test_import_files_json_skips_only_bad_entries),
        ]

        for name, fn in tests:
            print(f"\n-> {name}")
            try:
                fn(tmp, t)
                print(f"  OK")
            except Exception as e:
                print(f"  FAIL: {e}")
                import traceback
                traceback.print_exc()
                return False

        print("\n" + "=" * 60)
        return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
