"""task #11 T1/T2/T4 自检：字段级 prompt 提示 + 跨库迁移携带。

T1 验证：
  - schema v3 的 fields.prompt_hint 列存在；新建库默认空
  - Repository.add_field / set_field_prompt_hint / list_fields 能读写 prompt_hint
  - add_fields_batch 事务化：成功路径 + 失败 ROLLBACK
  - prompt 拼装时 prompt_hint 被注入到字段说明行

T4 验证：
  - exporter 输出 schema = ``llm-cabinet/project-export@2``，fields_snapshot 含 prompt_hint
  - importer 在 "create" 策略下把 prompt_hint 一起写到新字段
  - 老 @1 schema 包导入时 prompt_hint fallback 为空（向后兼容）
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T

from app.db import connect, SCHEMA_VERSION
from app.exporter import EXPORT_SCHEMA, ExportOptions, export_project
from app.importer import (
    ImportOptions, import_folder_as_project, scan_folders,
    SUPPORTED_SCHEMA_VERSION,
)
from app.library import Library
from app.llm.context import build_messages
from app.models import FileItem, Project
from app.repository import Repository


def main() -> int:
    t = T()
    repos: list[Repository] = []
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpstr:
            tmp = Path(tmpstr)
            _run_all(tmp, t, repos)
            for r in repos:
                try:
                    r.conn.close()
                except Exception:
                    pass
    finally:
        ok = t.report()
    return 0 if ok else 1


def _run_all(tmp: Path, t: T, repos: list[Repository]) -> None:
    # ----------------------------------------------------------------
    # 阶段 1：T1 数据层 — schema v3 + prompt_hint CRUD
    # ----------------------------------------------------------------
    db_a = tmp / "a.db"
    repo_a = Repository(connect(db_a))
    repos.append(repo_a)

    t.assert_eq("SCHEMA_VERSION = 3", SCHEMA_VERSION, 3)

    # 全新库：fields 表含 prompt_hint 列且默认空
    cols = {r[1] for r in repo_a.conn.execute("PRAGMA table_info(fields)").fetchall()}
    t.assert_in("fields 表含 prompt_hint 列", "prompt_hint", cols)

    fields = repo_a.list_fields()
    t.assert_true(
        "新库系统字段 prompt_hint 默认空",
        all((f.prompt_hint or "") == "" for f in fields),
    )

    # add_field 接受 prompt_hint
    fid_isbn = repo_a.add_field("ISBN", "text", prompt_hint="13 位数字，含连字符")
    f_isbn = repo_a.get_field(fid_isbn)
    t.assert_eq("add_field(prompt_hint=...) 落库", f_isbn.prompt_hint, "13 位数字，含连字符")

    # set_field_prompt_hint
    repo_a.set_field_prompt_hint(fid_isbn, "新提示")
    t.assert_eq("set_field_prompt_hint 生效",
                repo_a.get_field(fid_isbn).prompt_hint, "新提示")

    # set 为空
    repo_a.set_field_prompt_hint(fid_isbn, "")
    t.assert_eq("set 为空恢复默认",
                repo_a.get_field(fid_isbn).prompt_hint, "")

    # ----------------------------------------------------------------
    # 阶段 2：T1 add_fields_batch 事务化
    # ----------------------------------------------------------------
    fid_a = repo_a.add_field("作者2", "text")
    before_count = len(repo_a.list_fields())

    # 成功路径
    new_ids = repo_a.add_fields_batch([
        ("评分A", "rating", ""),
        ("摘要A", "textarea", "200 字以内"),
    ])
    t.assert_eq("add_fields_batch: 返回 id 列表长度", len(new_ids), 2)
    after = repo_a.list_fields()
    t.assert_eq("add_fields_batch 成功 → 新增 2 字段",
                len(after), before_count + 2)
    by_name = {f.name: f for f in after}
    t.assert_eq("摘要A 的 prompt_hint 落库", by_name["摘要A"].prompt_hint, "200 字以内")

    # 失败路径：第二条与已有字段同名 → UNIQUE 冲突 → 全部 ROLLBACK
    before_count = len(repo_a.list_fields())
    threw = False
    try:
        repo_a.add_fields_batch([
            ("唯一新字段", "text", ""),
            ("ISBN", "text", ""),     # 已存在 → 冲突
        ])
    except Exception:
        threw = True
    t.assert_true("add_fields_batch 冲突时抛异常", threw)
    t.assert_eq("add_fields_batch 失败 → 完全回滚（数量不变）",
                len(repo_a.list_fields()), before_count)
    t.assert_eq("回滚后『唯一新字段』不应存在",
                "唯一新字段" in {f.name for f in repo_a.list_fields()}, False)

    # ----------------------------------------------------------------
    # 阶段 3：T1 prompt 拼装注入
    # ----------------------------------------------------------------
    repo_a.set_field_prompt_hint(fid_isbn, "13 位数字，含连字符")
    p = Project(title="样本项目")
    p.id = repo_a.save_project(p)

    fields_now = repo_a.list_fields()
    isbn_field = next(f for f in fields_now if f.name == "ISBN")
    title_field = next(f for f in fields_now if f.is_title)

    msgs = build_messages(
        project=p,
        context_fields=fields_now,
        target_fields=[isbn_field, title_field],
        files=[],
        user_note="",
        language="中文",
        all_files=[],
    )
    user_text = ""
    for m in msgs:
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                user_text = "\n".join(x.get("text", "") for x in c if x.get("type") == "text")
            elif isinstance(c, str):
                user_text = c
            break

    t.assert_in("prompt 含 ISBN hint 注入", "13 位数字", user_text)
    t.assert_in("prompt 含『格式要求』标识", "格式要求", user_text)

    # 标题字段无 hint → 不应出现"格式要求"邻接到标题
    # （粗略验证：去掉 ISBN 行后还应有标题行，但不带"格式要求"）
    lines_with_title = [ln for ln in user_text.split("\n") if "标题" in ln]
    t.assert_true(
        "无 hint 字段不被注入",
        not any("格式要求" in ln for ln in lines_with_title[:1]),
    )

    # ----------------------------------------------------------------
    # 阶段 4：T4 导出 schema 升级到 @2 + 含 prompt_hint
    # ----------------------------------------------------------------
    t.assert_eq("EXPORT_SCHEMA = @2",
                EXPORT_SCHEMA, "llm-cabinet/project-export@2")
    t.assert_eq("SUPPORTED_SCHEMA_VERSION = 2", SUPPORTED_SCHEMA_VERSION, 2)

    # 准备一份"复制模式"项目以走通完整导出/导入
    lib_a_root = tmp / "lib_a"
    lib_a_root.mkdir()
    library_a = Library(lib_a_root)
    src_file = tmp / "ch1.txt"
    src_file.write_text("c1", encoding="utf-8")

    p2 = Project(title="出口测试", storage_mode="copy")
    p2.id = repo_a.save_project(p2)
    rel = library_a.import_copy(p2.id, src_file)
    repo_a.add_file(FileItem(
        project_id=p2.id, path=rel, is_relative=True, kind="doc", ord=0,
    ))
    p2.tags = []
    p2.field_values = {fid_isbn: "978-XXX"}
    repo_a.save_project(p2)

    export_root = tmp / "export"
    export_root.mkdir()
    res = export_project(
        repo_a, library_a, p2,
        ExportOptions(target_root=export_root, copy_link_files=True),
    )
    pj_path = res.project_dir / "project.json"
    pj_data = json.loads(pj_path.read_text(encoding="utf-8"))
    t.assert_eq("project.json schema = @2",
                pj_data.get("schema"), "llm-cabinet/project-export@2")
    snap_by_name = {s["name"]: s for s in pj_data.get("fields_snapshot", [])}
    t.assert_in("fields_snapshot 含 ISBN", "ISBN", snap_by_name)
    t.assert_eq("ISBN 的 prompt_hint 被导出",
                snap_by_name["ISBN"].get("prompt_hint"), "13 位数字，含连字符")

    # ----------------------------------------------------------------
    # 阶段 5：T4 导入端把 prompt_hint 一起写到新字段（"create"策略）
    # ----------------------------------------------------------------
    db_b = tmp / "b.db"
    repo_b = Repository(connect(db_b))
    repos.append(repo_b)
    library_b = Library(tmp / "lib_b")

    plans = scan_folders([res.project_dir], repo_b)
    plan = plans[0]
    t.assert_eq("scan: schema_version 解析", plan.schema_version, 2)
    t.assert_eq("scan: 不是 future schema", plan.is_future_schema, False)

    import_folder_as_project(
        repo_b, library_b, plan,
        ImportOptions(
            storage_mode="copy",
            title_source="project_json",
            field_policy="create",
            field_policy_apply_all=True,
        ),
    )
    fields_b = {f.name: f for f in repo_b.list_fields()}
    t.assert_in("库 B 已自动创建 ISBN 字段", "ISBN", fields_b)
    t.assert_eq("库 B ISBN 的 prompt_hint 还原",
                fields_b["ISBN"].prompt_hint, "13 位数字，含连字符")

    # ----------------------------------------------------------------
    # 阶段 6：T4 老 @1 schema 兼容 — 没 prompt_hint 字段时回退为空
    # ----------------------------------------------------------------
    legacy_dir = tmp / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "project.json").write_text(json.dumps({
        "schema": "llm-cabinet/project-export@1",
        "project": {"title": "Legacy"},
        "tags": [],
        "field_values": [{"field_name": "OldField", "value": "v"}],
        "fields_snapshot": [
            # 故意不带 prompt_hint
            {"name": "OldField", "type": "text"},
        ],
    }), encoding="utf-8")

    db_c = tmp / "c.db"
    repo_c = Repository(connect(db_c))
    repos.append(repo_c)
    library_c = Library(tmp / "lib_c")

    plans_c = scan_folders([legacy_dir], repo_c)
    plan_c = plans_c[0]
    t.assert_eq("legacy scan: schema_version", plan_c.schema_version, 1)

    import_folder_as_project(
        repo_c, library_c, plan_c,
        ImportOptions(
            storage_mode="link",
            title_source="project_json",
            field_policy="create",
            field_policy_apply_all=True,
        ),
    )
    fields_c = {f.name: f for f in repo_c.list_fields()}
    t.assert_in("legacy 包：OldField 已建", "OldField", fields_c)
    t.assert_eq("legacy 包：OldField prompt_hint 默认空（兼容）",
                fields_c["OldField"].prompt_hint, "")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
