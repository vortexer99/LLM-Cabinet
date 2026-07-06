"""task #03 Phase B 自检：宽关键词、字段过滤、布尔逻辑与 MCP 精确搜索。"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T, closing_repos

from app.db import connect
from app.mcp.tools import search_projects
from app.models import FileItem, Project
from app.repository import Repository
from app.search import parse_search


def _field(repo: Repository, name: str, ftype: str, key: str) -> int:
    row = repo.conn.execute(
        "SELECT COALESCE(MAX(ord), -1) AS m FROM fields"
    ).fetchone()
    cur = repo.conn.execute(
        "INSERT INTO fields(name, type, ord, visible, key) VALUES(?,?,?,?,?)",
        (name, ftype, int(row["m"]) + 1, 1, key),
    )
    repo.conn.commit()
    return int(cur.lastrowid)


def _save(
    repo: Repository,
    title: str,
    desc: str,
    tags: list[str],
    values: dict[int, str],
) -> int:
    p = Project(title=title, description_md=desc, tags=tags, field_values=values)
    return repo.save_project(p)


def _query(repo: Repository, expr: str) -> list[str]:
    parsed = parse_search(expr)
    if not parsed.ok:
        raise AssertionError(parsed.error)
    return sorted(p.title for p in repo.list_projects_query(parsed.ast))


def test_repository_search_phase_b(tmp: Path, t: T) -> None:
    repo = Repository(connect(tmp / "cabinet.db"))
    with closing_repos(repo):
        fid_author = _field(repo, "作者", "text", "author")
        fid_date = _field(repo, "日期", "date", "date")
        fid_rating = _field(repo, "评分", "rating", "rating")

        pid_santi = _save(
            repo,
            "三体 设定集",
            "黑暗森林与宇宙社会学",
            ["科幻", "翻译"],
            {fid_author: "刘慈欣", fid_date: "2024-03-01", fid_rating: "5"},
        )
        pid_foundation = _save(
            repo,
            "银河帝国",
            "基地故事",
            ["科幻"],
            {fid_author: "阿西莫夫", fid_date: "2023-01-01", fid_rating: "4"},
        )
        pid_kitchen = _save(
            repo,
            "厨房手册",
            "三体锅不是科幻小说",
            ["生活"],
            {fid_author: "刘慈欣", fid_date: "2024-05-01", fid_rating: "3"},
        )
        _save(
            repo,
            "作者字段命中",
            "标题和描述不含目标词",
            ["人物"],
            {fid_author: "刘慈欣", fid_date: "2022-01-01", fid_rating: "5"},
        )
        repo.add_file(FileItem(
            project_id=pid_foundation,
            path="foundation/timeline.pdf",
            label="基地年表",
            kind="pdf",
            subfolder="资料/年表",
        ))
        repo.add_file(FileItem(
            project_id=pid_santi,
            path="santi/dark-forest-notes.md",
            label="黑暗森林笔记",
            kind="markdown",
        ))
        repo.add_file(FileItem(
            project_id=pid_kitchen,
            path="cookbook.txt",
            label="菜谱",
            kind="text",
        ))

        t.assert_eq(
            "plain keyword searches custom field values",
            _query(repo, "刘慈欣"),
            ["三体 设定集", "作者字段命中", "厨房手册"],
        )
        t.assert_eq(
            "plain keyword searches tags",
            _query(repo, "翻译"),
            ["三体 设定集"],
        )
        t.assert_eq(
            "plain keyword searches file labels and subfolders",
            _query(repo, "年表"),
            ["银河帝国"],
        )
        t.assert_eq(
            "author key field contains",
            _query(repo, "author:刘慈欣"),
            ["三体 设定集", "作者字段命中", "厨房手册"],
        )
        t.assert_eq(
            "display field name exact match",
            _query(repo, "作者:阿西莫夫"),
            ["银河帝国"],
        )
        t.assert_eq(
            "rating comparison",
            _query(repo, "rating:>=4"),
            ["三体 设定集", "作者字段命中", "银河帝国"],
        )
        t.assert_eq(
            "date comparison",
            _query(repo, "date:>=2024-01-01"),
            ["三体 设定集", "厨房手册"],
        )
        t.assert_eq(
            "multi tag AND uses EXISTS",
            _query(repo, "tag:科幻 AND tag:翻译"),
            ["三体 设定集"],
        )
        t.assert_eq(
            "parentheses OR NOT",
            _query(repo, "(tag:生活 OR tag:翻译) AND NOT rating:<4"),
            ["三体 设定集"],
        )
        bad = parse_search("tag:(科幻")
        t.assert_true(
            "invalid syntax returns structured error",
            (not bad.ok) and bad.error is not None,
        )

        ctx = SimpleNamespace(repo=repo)
        mcp_res = asyncio.run(
            search_projects(ctx, keyword="tag:科幻", field_filter="rating:>=4")
        )
        t.assert_eq(
            "MCP combines keyword and field_filter",
            sorted(r["title"] for r in mcp_res),
            ["三体 设定集", "银河帝国"],
        )


if __name__ == "__main__":
    print("=" * 60)
    print("task03_search_phase_b selftest")
    print("=" * 60)
    t = T()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        test_repository_search_phase_b(Path(d), t)
    ok = t.report()
    sys.exit(0 if ok else 1)
