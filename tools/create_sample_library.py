"""生成用于手工测试的 LLM Cabinet 样例库。

用法：

    python tools/create_sample_library.py --target sample-library --force

输出目录是一个完整库根：

    sample-library/
      .llm-cabinet
      cabinet.db
      library/
      external_sources/

脚本可重复运行；使用 --force 时会先删除目标目录。
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.cabinet import mark_as_library, resolve_library_paths
from app.db import OPTIONAL_DEFAULT_FIELDS, connect
from app.library import Library
from app.models import FileItem, Project
from app.repository import Repository
from app.search_history import (
    HISTORY_SETTING_KEY,
    SAVED_SEARCHES_SETTING_KEY,
    add_history,
    upsert_saved_search,
)


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 LLM Cabinet 样例库")
    parser.add_argument(
        "--target",
        default="sample-library",
        help="输出库根目录，默认 sample-library",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="若目标目录已存在，先删除再重建",
    )
    args = parser.parse_args()

    target = Path(args.target).resolve()
    if target.exists():
        if not args.force:
            print(f"目标目录已存在：{target}")
            print("如需重建，请加 --force")
            return 2
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    build_sample_library(target)
    print(f"样例库已生成：{target}")
    print(f"数据库：{target / 'cabinet.db'}")
    print("在应用中使用「库 → 切换库...」选择该目录即可。")
    return 0


def build_sample_library(root: Path) -> None:
    mark_as_library(root)
    db_path, library_root = resolve_library_paths(root)
    library_root.mkdir(parents=True, exist_ok=True)

    conn = connect(db_path)
    repo = Repository(conn)
    library = Library(library_root)
    try:
        field_ids = ensure_sample_fields(repo)
        ensure_sample_support_tables(repo)
        external = root / "external_sources"
        external.mkdir(parents=True, exist_ok=True)
        files = create_source_files(external)

        seed_settings(repo)
        seed_projects(repo, library, files, field_ids)
        seed_audit_rows(repo)
    finally:
        conn.close()


def ensure_sample_fields(repo: Repository) -> dict[str, int]:
    """确保可选字段和测试字段存在，返回 key/name 到 field_id 的映射。"""
    existing = repo.list_fields()
    key_to_id = {f.key: f.id for f in existing if f.key and f.id is not None}
    max_ord = repo.conn.execute(
        "SELECT COALESCE(MAX(ord), -1) AS m FROM fields"
    ).fetchone()["m"]
    next_ord = int(max_ord) + 1
    for name, ftype, key, visible in OPTIONAL_DEFAULT_FIELDS:
        if key in key_to_id:
            continue
        cur = repo.conn.execute(
            "INSERT INTO fields(name, type, ord, visible, key) VALUES(?,?,?,?,?)",
            (name, ftype, next_ord, visible, key),
        )
        key_to_id[key] = int(cur.lastrowid)
        next_ord += 1

    extra_fields = [
        ("状态", "text", None, 1),
        ("优先级", "number", None, 1),
        ("负责人", "text", None, 1),
        ("备注", "textarea", None, 0),
    ]
    name_to_id = {f.name: f.id for f in repo.list_fields() if f.id is not None}
    for name, ftype, key, visible in extra_fields:
        if name in name_to_id:
            continue
        cur = repo.conn.execute(
            "INSERT INTO fields(name, type, ord, visible, key) VALUES(?,?,?,?,?)",
            (name, ftype, next_ord, visible, key),
        )
        name_to_id[name] = int(cur.lastrowid)
        next_ord += 1
    repo.conn.commit()

    fields = repo.list_fields()
    out: dict[str, int] = {}
    for f in fields:
        if f.id is None:
            continue
        if f.key:
            out[f.key] = f.id
        out[f.name] = f.id
    return out


def ensure_sample_support_tables(repo: Repository) -> None:
    """补齐样例库需要的辅助表。

    当前主 schema 对部分历史迁移表采取运行时兼容策略；样例库为了覆盖
    MCP audit 面板，需要显式确保 ``mcp_audit`` 存在。
    """
    repo.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_audit (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT NOT NULL DEFAULT (datetime('now')),
            client_name     TEXT,
            tool_name       TEXT NOT NULL,
            arguments_json  TEXT,
            result_status   TEXT NOT NULL DEFAULT 'success',
            error_message   TEXT
        )
        """
    )
    repo.conn.commit()


def create_source_files(external: Path) -> dict[str, Path]:
    """创建一批外链/导入用源文件。"""
    files: dict[str, Path] = {}
    samples = {
        "three_body_pdf": ("三体-阅读笔记.pdf", "%PDF-1.4\n% sample pdf placeholder\n"),
        "three_body_md": ("三体-人物关系.md", "# 三体人物关系\n\n- 叶文洁\n- 罗辑\n"),
        "foundation_txt": ("foundation-quotes.txt", "Foundation sample notes\npsychohistory\n"),
        "ai_plan": ("ai-team-workspace-plan.md", "# AI Team Workspace\n\n共享记忆、任务状态、来源链。\n"),
        "dataset_csv": ("experiment-data.csv", "case,score\nA,5\nB,3\n"),
        "image_png": ("cover-source.png", PNG_1X1),
        "video_note": ("demo-video-notes.txt", "视频素材说明：用于测试 video 标签和外链。\n"),
        "archive_note": ("archive-readme.txt", "归档包说明，占位用于导出测试。\n"),
    }
    for key, (name, content) in samples.items():
        path = external / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        files[key] = path
    return files


def seed_settings(repo: Repository) -> None:
    repo.set_setting("library_description", "用于测试 LLM Cabinet 全功能的样例库")
    repo.set_setting("default_storage_mode", "copy")
    repo.set_setting("default_view_mode", "grid")
    repo.set_setting("import_ignore_dotfiles", "1")
    repo.set_setting(HISTORY_SETTING_KEY, add_history("[]", "tag:科幻 AND rating:>=4"))
    hist = repo.get_setting(HISTORY_SETTING_KEY, "[]")
    hist = add_history(hist, "author:刘慈欣")
    hist = add_history(hist, "date:>=2024-01-01")
    repo.set_setting(HISTORY_SETTING_KEY, hist)
    saved, _ = upsert_saved_search("[]", "高分科幻", "tag:科幻 AND rating:>=4")
    saved, _ = upsert_saved_search(saved, "近期项目", "date:>=2024-01-01")
    saved, _ = upsert_saved_search(saved, "待整理", "状态:待整理")
    repo.set_setting(SAVED_SEARCHES_SETTING_KEY, saved)


def seed_projects(
    repo: Repository,
    library: Library,
    files: dict[str, Path],
    field_ids: dict[str, int],
) -> None:
    status = field_ids["状态"]
    priority = field_ids["优先级"]
    owner = field_ids["负责人"]
    remark = field_ids["备注"]
    author = field_ids["author"]
    date = field_ids["date"]
    rating = field_ids["rating"]
    source_url = field_ids["source_url"]

    p1 = save_project(
        repo,
        "三体研究资料",
        "用于测试中文标题、描述搜索、作者字段、评分过滤、多标签 AND。",
        ["领域/科幻", "科幻", "翻译", "待整理"],
        {
            author: "刘慈欣",
            date: "2024-03-01",
            rating: "5",
            source_url: "https://example.com/three-body",
            status: "待整理",
            priority: "1",
            owner: "Hou",
            remark: "含 copy 文件、generated 封面、多层 subfolder。",
        },
    )
    add_copy_file(repo, library, p1, files["three_body_pdf"], "原文 PDF", "document", "source/pdf")
    add_copy_file(repo, library, p1, files["three_body_md"], "人物关系笔记", "document", "notes")
    add_generated_cover(repo, library, p1)
    repo.set_project_setting(p1, "explicit_subfolders", json.dumps(["notes/drafts", "figures"], ensure_ascii=False))

    p2 = save_project(
        repo,
        "银河帝国整理",
        "英文资料，测试 author:阿西莫夫、tag:科幻、rating:>=4。",
        ["领域/科幻", "科幻", "英文"],
        {
            author: "阿西莫夫",
            date: "2023-01-01",
            rating: "4",
            source_url: "https://example.com/foundation",
            status: "已整理",
            priority: "2",
            owner: "AI",
        },
    )
    add_link_file(repo, p2, files["foundation_txt"], "外链摘录", "document", "quotes")
    add_copy_file(repo, library, p2, files["archive_note"], "归档说明", "archive", "archive")

    p3 = save_project(
        repo,
        "AI Team Workspace 方案",
        "用于测试 MCP 修改标记、待审阅建议、来源 URL、近期搜索。",
        ["AI", "MCP", "协作", "待审阅"],
        {
            author: "LLM Cabinet Team",
            date: "2026-07-07",
            rating: "5",
            source_url: "https://example.com/ai-team-workspace",
            status: "待审阅",
            priority: "1",
            owner: "Agent",
        },
    )
    add_copy_file(repo, library, p3, files["ai_plan"], "方案草稿", "document", "docs")
    add_link_file(repo, p3, files["dataset_csv"], "实验数据", "document", "data/raw")
    repo.mark_project_mcp_modified(p3)
    task_id = repo.create_llm_task(
        p3,
        "AI Team Workspace 方案",
        "meta_suggest",
        json.dumps({"sample": True}, ensure_ascii=False),
        "sample-provider",
        "sample-model",
    )
    repo.update_llm_task_status(task_id, "done", result_json="{}", tokens_in=120, tokens_out=80)
    repo.add_suggestions(
        p3,
        task_id,
        [
            (status, "可发布"),
            (remark, "建议补充 agent 写入来源链。"),
        ],
    )

    p4 = save_project(
        repo,
        "未分类草稿",
        "没有标签，用于测试未分类筛选和纯关键词搜索。",
        [],
        {
            author: "匿名",
            date: "2024-05-20",
            rating: "2",
            status: "待整理",
            priority: "3",
            owner: "User",
        },
    )
    add_link_file(repo, p4, files["video_note"], "视频说明外链", "video", "")

    p5 = save_project(
        repo,
        "缺失链接修复样例",
        "包含一个不存在的链接文件，用于测试一致性检查、missing 标记、重关联。",
        ["维护", "文件缺失"],
        {
            author: "系统",
            date: "2022-12-12",
            rating: "1",
            status: "需修复",
            priority: "5",
            owner: "User",
        },
    )
    add_missing_file(repo, p5, files["foundation_txt"].with_name("missing-target.pdf"), "缺失 PDF", "document", "broken")

    p6 = save_project(
        repo,
        "导出导入闭环样例",
        "含 copy/link/generated/user origin，适合测试批量导出、ZIP 导入、目录结构还原。",
        ["导出", "导入", "回归测试"],
        {
            author: "QA",
            date: "2025-06-01",
            rating: "4",
            status: "已整理",
            priority: "2",
            owner: "Tester",
        },
    )
    add_copy_file(repo, library, p6, files["dataset_csv"], "数据表", "document", "data")
    add_link_file(repo, p6, files["image_png"], "外部图片", "image", "images")
    add_generated_cover(repo, library, p6)


def save_project(
    repo: Repository,
    title: str,
    desc: str,
    tags: list[str],
    values: dict[int, str],
) -> int:
    p = Project(title=title, description_md=desc, tags=tags, field_values=values)
    return repo.save_project(p)


def add_copy_file(
    repo: Repository,
    library: Library,
    project_id: int,
    src: Path,
    label: str,
    kind: str,
    subfolder: str,
) -> int:
    rel = library.import_copy(project_id, src)
    return repo.add_file(
        FileItem(
            project_id=project_id,
            path=rel,
            is_relative=True,
            label=label,
            kind=kind,
            subfolder=subfolder,
            origin="user",
        )
    )


def add_link_file(
    repo: Repository,
    project_id: int,
    src: Path,
    label: str,
    kind: str,
    subfolder: str,
) -> int:
    return repo.add_file(
        FileItem(
            project_id=project_id,
            path=str(src),
            is_relative=False,
            label=label,
            kind=kind,
            subfolder=subfolder,
            origin="user",
        )
    )


def add_missing_file(
    repo: Repository,
    project_id: int,
    path: Path,
    label: str,
    kind: str,
    subfolder: str,
) -> int:
    fid = add_link_file(repo, project_id, path, label, kind, subfolder)
    repo.set_file_missing(fid, True)
    return fid


def add_generated_cover(repo: Repository, library: Library, project_id: int) -> int:
    cover_path = library.project_dir(project_id) / "__cover_sample.png"
    cover_path.write_bytes(PNG_1X1)
    rel = cover_path.relative_to(library.root).as_posix()
    fid = repo.add_file(
        FileItem(
            project_id=project_id,
            path=rel,
            is_relative=True,
            label="生成封面",
            kind="image",
            subfolder="generated",
            origin="generated",
        )
    )
    p = repo.get_project(project_id)
    if p is not None:
        p.cover_file_id = fid
        repo.save_project(p)
    return fid


def seed_audit_rows(repo: Repository) -> None:
    rows = [
        (
            "sample-agent",
            "query_projects",
            {"action": "search", "keyword": "tag:科幻 AND rating:>=4"},
            "success",
            "",
        ),
        (
            "sample-agent",
            "manage_project",
            {"action": "update", "project_id": 3, "field_values": {"状态": "待审阅"}},
            "success",
            "",
        ),
        (
            "sample-agent",
            "manage_files",
            {"action": "add", "project_id": 3, "path": "sample.md"},
            "error",
            "示例错误：路径不存在",
        ),
    ]
    for client, tool, args, status, error in rows:
        repo.conn.execute(
            "INSERT INTO mcp_audit(client_name, tool_name, arguments_json, result_status, error_message) "
            "VALUES(?,?,?,?,?)",
            (client, tool, json.dumps(args, ensure_ascii=False), status, error),
        )
    repo.conn.commit()


if __name__ == "__main__":
    raise SystemExit(main())
