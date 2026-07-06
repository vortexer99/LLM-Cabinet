"""task #03 Phase A 自检：基础搜索闭环的数据层与 MCP 入口。

覆盖：
- ``list_projects(keyword=...)`` 只搜标题 / 描述
- keyword 与精确 tag、tag_prefix 组合为 AND
- 未分类项目列表支持 keyword
- MCP ``search_projects`` 与 Repository 基础搜索结果一致，并接受 ``tag_prefix``
"""
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
from app.models import Project
from app.repository import Repository


def _save(repo: Repository, title: str, desc: str = "", tags: list[str] | None = None) -> int:
    p = Project(title=title, description_md=desc, tags=tags or [])
    return repo.save_project(p)


def _titles(projects) -> list[str]:
    return sorted(p.title for p in projects)


def test_repository_search_phase_a(tmp: Path, t: T) -> None:
    repo = Repository(connect(tmp / "cabinet.db"))
    with closing_repos(repo):
        _save(repo, "三体 设定集", "黑暗森林与宇宙社会学", ["领域/科幻", "翻译"])
        _save(repo, "银河帝国", "机器人与基地", ["领域/科幻"])
        _save(repo, "厨房手册", "三体锅不是科幻", ["生活/烹饪"])
        _save(repo, "未分类草稿", "三体阅读笔记", [])
        _save(repo, "作者刘慈欣", "字段搜索留给 Phase B", ["人物"])

        t.assert_eq(
            "keyword searches title and description",
            _titles(repo.list_projects(keyword="三体")),
            ["三体 设定集", "厨房手册", "未分类草稿"],
        )
        t.assert_eq(
            "keyword + exact tag is AND",
            _titles(repo.list_projects(keyword="三体", tag="领域/科幻")),
            ["三体 设定集"],
        )
        t.assert_eq(
            "keyword + tag_prefix is AND",
            _titles(repo.list_projects(keyword="银河", tag_prefix="领域")),
            ["银河帝国"],
        )
        t.assert_eq(
            "untagged + keyword",
            _titles(repo.list_projects_untagged(keyword="三体")),
            ["未分类草稿"],
        )

        ctx = SimpleNamespace(repo=repo)
        mcp_res = asyncio.run(search_projects(ctx, keyword="三体", tag_prefix="领域"))
        t.assert_eq(
            "MCP search matches repository tag_prefix search",
            [r["title"] for r in mcp_res],
            ["三体 设定集"],
        )


if __name__ == "__main__":
    print("=" * 60)
    print("task03_search_phase_a selftest")
    print("=" * 60)
    t = T()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        test_repository_search_phase_a(Path(d), t)
    ok = t.report()
    sys.exit(0 if ok else 1)
