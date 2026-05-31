"""task #10 自检：批量文件夹导入 + project.json 识别。

流程：
  1. 建临时数据库（库 A）+ 临时 library 目录
  2. 在库 A 里造 3 个项目（含字段值/标签/文件，覆盖 link 与 copy 两种存储模式）
  3. 用 ``app.exporter`` 把 3 个项目导出到 export_root
  4. 建另一个临时数据库（库 B），故意留一个未匹配字段
  5. 用 ``app.importer`` 把 export_root 下的目录批量导入到库 B
  6. 验证库 B 的项目元数据/字段值/标签/文件被正确还原

也覆盖：
  - schema @99（未来版本）→ 仍能识别核心字段
  - 损坏的 project.json → 当作普通文件夹（标题 fallback 到文件夹名）
  - 三档字段策略：append_to_desc / create / ignore
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

# 让 selftests 脚本可以单独跑（不需要先 cd 到根目录）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T

from app.db import connect
from app.exporter import ExportOptions, export_project
from app.importer import (
    ImportOptions, import_folder_as_project, scan_folders,
    SUPPORTED_SCHEMA_VERSION,
)
from app.library import Library
from app.models import FileItem, Project
from app.repository import Repository


# =============================================================================
# 数据准备
# =============================================================================
def setup_lib_a(tmp: Path) -> tuple[Repository, Library, list[Project]]:
    """库 A：含字段定义、3 个项目。"""
    db_a = tmp / "lib_a.db"
    lib_a_root = tmp / "lib_a_files"
    lib_a_root.mkdir()

    repo = Repository(connect(db_a))
    library = Library(lib_a_root)

    fid_isbn = repo.add_field("ISBN", "text")
    fid_pages = repo.add_field("pages", "number")

    src_dir = tmp / "src"
    src_dir.mkdir()
    file1 = src_dir / "ch1.txt"
    file2 = src_dir / "ch2.txt"
    file3 = src_dir / "cover.png"
    file1.write_text("chapter 1 content", encoding="utf-8")
    file2.write_text("chapter 2 content", encoding="utf-8")
    file3.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fakebytes" * 100)

    projects: list[Project] = []

    # 项目 1：纯 link 模式
    p1 = Project(
        title="科幻小说A", author="Alice", date="2024-01",
        rating=5, description_md="一段描述", storage_mode="link",
    )
    p1.tags = ["科幻", "翻译"]
    pid1 = repo.save_project(p1)
    p1.id = pid1
    p1.field_values = {fid_isbn: "978-111", fid_pages: "320"}
    repo.save_project(p1)
    repo.add_file(FileItem(
        project_id=pid1, path=str(file1.resolve()),
        is_relative=False, kind="doc", ord=0,
    ))
    repo.add_file(FileItem(
        project_id=pid1, path=str(file2.resolve()),
        is_relative=False, kind="doc", ord=1,
    ))
    projects.append(repo.get_project(pid1))

    # 项目 2：纯 copy 模式 + 标题含非法字符
    p2 = Project(title="项目B/危险:字符?", author="Bob", storage_mode="copy")
    p2.tags = ["技术"]
    pid2 = repo.save_project(p2)
    p2.id = pid2
    p2.field_values = {fid_isbn: "978-222"}
    repo.save_project(p2)
    rel1 = library.import_copy(pid2, file1)
    rel2 = library.import_copy(pid2, file3)
    repo.add_file(FileItem(
        project_id=pid2, path=rel1, is_relative=True, kind="doc", ord=0,
    ))
    repo.add_file(FileItem(
        project_id=pid2, path=rel2, is_relative=True, kind="image", ord=1,
    ))
    projects.append(repo.get_project(pid2))

    # 项目 3：最小项目
    p3 = Project(title="Empty", storage_mode="link")
    pid3 = repo.save_project(p3)
    p3.id = pid3
    repo.add_file(FileItem(
        project_id=pid3, path=str(file2.resolve()),
        is_relative=False, kind="doc", ord=0,
    ))
    projects.append(repo.get_project(pid3))

    return repo, library, projects


def setup_lib_b(tmp: Path) -> tuple[Repository, Library]:
    """库 B：故意只有 ISBN 字段（pages 字段缺失，模拟未匹配字段）。"""
    db_b = tmp / "lib_b.db"
    lib_b_root = tmp / "lib_b_files"
    lib_b_root.mkdir()
    repo = Repository(connect(db_b))
    library = Library(lib_b_root)
    repo.add_field("ISBN", "text")
    return repo, library


# =============================================================================
# 边缘 case：构造非 #09 输出的特殊文件夹
# =============================================================================
def make_edge_cases(export_root: Path) -> tuple[Path, Path, Path]:
    case_future = export_root / "case_future"
    case_future.mkdir()
    (case_future / "project.json").write_text(json.dumps({
        "schema": "llm-cabinet/project-export@99",
        "project": {"title": "From Future", "author": "X"},
        "tags": ["future-only"],
        "field_values": [{"field_name": "ISBN", "value": "future-isbn"}],
        "fields_snapshot": [{"name": "ISBN", "type": "text"}],
    }), encoding="utf-8")
    (case_future / "files").mkdir()
    (case_future / "files" / "x.txt").write_text("hi", encoding="utf-8")

    case_corrupt = export_root / "case_corrupt"
    case_corrupt.mkdir()
    (case_corrupt / "project.json").write_text("{not valid", encoding="utf-8")
    (case_corrupt / "data.txt").write_text("data", encoding="utf-8")

    case_plain = export_root / "case_plain"
    case_plain.mkdir()
    (case_plain / "a.txt").write_text("plain", encoding="utf-8")
    (case_plain / "b.txt").write_text("plain2", encoding="utf-8")

    return case_future, case_corrupt, case_plain


# =============================================================================
# 主流程
# =============================================================================
def main() -> int:
    t = T()
    repos: list[Repository] = []

    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpstr:
            tmp = Path(tmpstr)
            _run_all(tmp, t, repos)
            # 退出 with 之前关闭所有 SQLite 连接（Windows 上 tempdir 清理时
            # 若仍有句柄持有 .db 会触发 PermissionError）
            for r in repos:
                try:
                    r.conn.close()
                except Exception:
                    pass
    finally:
        ok = t.report()
    return 0 if ok else 1


def _run_all(tmp: Path, t: T, repos: list[Repository]) -> None:
    """所有断言的实际执行体，便于 main() 控制资源生命周期。"""
    # ----------------------------------------------------------------
    # 阶段 1：库 A 建数据 → 导出
    # ----------------------------------------------------------------
    repo_a, lib_a, projects_a = setup_lib_a(tmp)
    repos.append(repo_a)

    export_root = tmp / "export"
    export_root.mkdir()

    export_dirs: list[Path] = []
    for p in projects_a:
        res = export_project(
            repo_a, lib_a, p,
            ExportOptions(target_root=export_root, copy_link_files=True),
        )
        export_dirs.append(res.project_dir)
        t.assert_true(
            f"export[{p.title}]: project_dir 存在", res.project_dir.is_dir()
        )
        t.assert_true(
            f"export[{p.title}]: project.json 存在",
            (res.project_dir / "project.json").is_file(),
        )

    case_future, case_corrupt, case_plain = make_edge_cases(export_root)
    all_dirs = export_dirs + [case_future, case_corrupt, case_plain]

    # ----------------------------------------------------------------
    # 阶段 2：库 B 扫描
    # ----------------------------------------------------------------
    repo_b, lib_b = setup_lib_b(tmp)
    repos.append(repo_b)
    plans = scan_folders(all_dirs, repo_b)

    t.assert_eq("scan: plan 数量", len(plans), len(all_dirs))

    for ed in export_dirs:
        plan = next(p for p in plans if p.folder == ed)
        t.assert_true(
            f"scan[{ed.name}]: has_project_json", plan.has_project_json,
        )
        t.assert_eq(
            f"scan[{ed.name}]: schema_version", plan.schema_version, SUPPORTED_SCHEMA_VERSION,
        )
        t.assert_eq(
            f"scan[{ed.name}]: is_future_schema", plan.is_future_schema, False,
        )

    plan_p1 = next(p for p in plans if "科幻" in p.folder.name)
    t.assert_in("scan[p1]: 'pages' 未匹配", "pages", plan_p1.unmatched_fields)

    plan_fut = next(p for p in plans if p.folder.name == "case_future")
    t.assert_true("scan[future]: has_project_json", plan_fut.has_project_json)
    t.assert_eq("scan[future]: schema_version", plan_fut.schema_version, 99)
    t.assert_true("scan[future]: is_future_schema", plan_fut.is_future_schema)

    plan_corr = next(p for p in plans if p.folder.name == "case_corrupt")
    t.assert_eq(
        "scan[corrupt]: has_project_json", plan_corr.has_project_json, False,
    )
    t.assert_true(
        "scan[corrupt]: parse_error 非空", bool(plan_corr.parse_error),
    )

    plan_plain = next(p for p in plans if p.folder.name == "case_plain")
    t.assert_eq(
        "scan[plain]: has_project_json", plan_plain.has_project_json, False,
    )
    t.assert_eq("scan[plain]: parse_error 为空", plan_plain.parse_error, "")

    # ----------------------------------------------------------------
    # 阶段 3：三档字段策略
    # ----------------------------------------------------------------

    # ---- A: append_to_desc ----
    opts_append = ImportOptions(
        storage_mode="copy",
        title_source="project_json",
        field_policy="append_to_desc",
        field_policy_apply_all=True,
    )
    res_p1_a = import_folder_as_project(repo_b, lib_b, plan_p1, opts_append)
    proj_p1_a = repo_b.get_project(res_p1_a.project_id)
    t.assert_eq(
        "import[append][p1]: title 还原",
        proj_p1_a.title, projects_a[0].title,
    )
    t.assert_eq(
        "import[append][p1]: author 还原",
        proj_p1_a.author, projects_a[0].author,
    )
    t.assert_eq(
        "import[append][p1]: rating 还原",
        proj_p1_a.rating, projects_a[0].rating,
    )
    t.assert_eq(
        "import[append][p1]: tags 还原（库 B 自动创建）",
        sorted(proj_p1_a.tags), sorted(projects_a[0].tags),
    )
    isbn_field = next(f for f in repo_b.list_fields() if f.name == "ISBN")
    t.assert_eq(
        "import[append][p1]: ISBN 字段值还原",
        proj_p1_a.field_values.get(isbn_field.id), "978-111",
    )
    t.assert_in(
        "import[append][p1]: pages 值出现在描述",
        "320", proj_p1_a.description_md,
    )
    t.assert_in(
        "import[append][p1]: pages 字段名出现在描述",
        "pages", proj_p1_a.description_md,
    )
    b_field_names = {f.name for f in repo_b.list_fields()}
    t.assert_eq(
        "import[append][p1]: 库 B 字段表不变",
        "pages" in b_field_names, False,
    )

    # ---- B: create ----
    repo_b.delete_project(res_p1_a.project_id)
    opts_create = ImportOptions(
        storage_mode="copy",
        title_source="project_json",
        field_policy="create",
        field_policy_apply_all=True,
    )
    plans_b = scan_folders([plan_p1.folder], repo_b)
    res_p1_c = import_folder_as_project(repo_b, lib_b, plans_b[0], opts_create)
    proj_p1_c = repo_b.get_project(res_p1_c.project_id)
    b_field_names_c = {f.name for f in repo_b.list_fields()}
    t.assert_in(
        "import[create][p1]: 库 B 自动创建了 pages 字段",
        "pages", b_field_names_c,
    )
    pages_field = next(f for f in repo_b.list_fields() if f.name == "pages")
    t.assert_eq(
        "import[create][p1]: pages 字段值还原",
        proj_p1_c.field_values.get(pages_field.id), "320",
    )
    t.assert_eq(
        "import[create][p1]: 描述未被追加 pages",
        "pages" in proj_p1_c.description_md, False,
    )

    # ---- C: ignore ----
    repo_b.delete_project(res_p1_c.project_id)
    repo_b.delete_field(pages_field.id)
    opts_ignore = ImportOptions(
        storage_mode="copy",
        title_source="project_json",
        field_policy="ignore",
        field_policy_apply_all=True,
    )
    plans_c = scan_folders([plan_p1.folder], repo_b)
    res_p1_i = import_folder_as_project(repo_b, lib_b, plans_c[0], opts_ignore)
    proj_p1_i = repo_b.get_project(res_p1_i.project_id)
    b_field_names_i = {f.name for f in repo_b.list_fields()}
    t.assert_eq(
        "import[ignore][p1]: 字段表无 pages",
        "pages" in b_field_names_i, False,
    )
    t.assert_eq(
        "import[ignore][p1]: 描述无 pages",
        "pages" in proj_p1_i.description_md, False,
    )

    # ---- 文件还原（copy 模式）----
    files_p1_b = repo_b.list_files(res_p1_i.project_id)
    t.assert_eq(
        "import[ignore][p1]: 文件数量还原",
        len(files_p1_b), len(repo_a.list_files(projects_a[0].id)),
    )
    t.assert_true(
        "import[ignore][p1]: 文件全部为 relative（copy 模式）",
        all(f.is_relative for f in files_p1_b),
    )
    for f in files_p1_b:
        t.assert_true(
            f"import[ignore][p1]: 文件 {f.path} 实际存在",
            lib_b.resolve(f.path, f.is_relative).is_file(),
        )

    # ----------------------------------------------------------------
    # 阶段 4：未来版本 case
    # ----------------------------------------------------------------
    plans_fut = scan_folders([case_future], repo_b)
    res_fut = import_folder_as_project(
        repo_b, lib_b, plans_fut[0],
        ImportOptions(
            storage_mode="link",
            title_source="project_json",
            field_policy="ignore",
            field_policy_apply_all=True,
        ),
    )
    proj_fut = repo_b.get_project(res_fut.project_id)
    t.assert_eq(
        "import[future]: 标题恢复",
        proj_fut.title, "From Future",
    )
    t.assert_in(
        "import[future]: warning 提到更新版本",
        "更新版本", " ".join(res_fut.warnings),
    )

    # ----------------------------------------------------------------
    # 阶段 5：损坏 case 仍能落地
    # ----------------------------------------------------------------
    plans_corr = scan_folders([case_corrupt], repo_b)
    res_corr = import_folder_as_project(
        repo_b, lib_b, plans_corr[0],
        ImportOptions(
            storage_mode="link",
            title_source="project_json",
            field_policy="ignore",
            field_policy_apply_all=True,
        ),
    )
    proj_corr = repo_b.get_project(res_corr.project_id)
    t.assert_eq(
        "import[corrupt]: 标题 fallback 到文件夹名",
        proj_corr.title, "case_corrupt",
    )
    files_corr = repo_b.list_files(res_corr.project_id)
    t.assert_true(
        "import[corrupt]: data.txt 被识别",
        any("data.txt" in f.path for f in files_corr),
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
