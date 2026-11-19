"""侧栏查询转换与后台等价性：仅显式临时库和合成元数据。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import connect
from app.models import Project
from app.repository import Repository
from app.search import AndNode, NotNode, OrNode, TermNode, parse_search
from app.search_filters import file_selection, format_search, replace_filter, sidebar_node, tag_selection
from selftests._common import T


def run(root: Path, t: T) -> None:
    special = ['空 格', 'AND', 'OR', 'NOT', 'a"b', "a'b", r'a\b', 'a(1):=x', '前缀%_/子标签']
    for value in special:
        for node in (TermNode("tag", "=", value), TermNode(None, ":", value), TermNode("字段 名", ">=", value)):
            parsed = parse_search(format_search(node))
            t.assert_true(f"特殊值能解析 {value}", parsed.ok)
            t.assert_eq(f"特殊值语义不变 {value}", parsed.ast, node)
    mixed = OrNode([TermNode("tag", "=", "甲"), NotNode(TermNode("title", ":", "乙"))])
    t.assert_eq("混合布尔语义往返", parse_search(format_search(mixed)).ast, mixed)
    replaced = replace_filter(mixed, "tag", sidebar_node("tag", "丙"))
    t.assert_eq("混合OR整体保留", replaced, AndNode([mixed, TermNode("tag", "=", "丙")]))
    t.assert_eq("复杂逻辑不伪造树选择", tag_selection(mixed), ("query", ""))
    node = AndNode([TermNode("tag", "=", "甲"), TermNode("title", ":", "乙"), TermNode("has", ":", "video")])
    changed = replace_filter(node, "tag", sidebar_node("tag", "丙"))
    t.assert_eq("切标签替换旧条件且保留其余", changed,
                AndNode([TermNode("title", ":", "乙"), TermNode("has", ":", "video"), TermNode("tag", "=", "丙")]))
    t.assert_eq("重复点击不叠加", replace_filter(changed, "tag", sidebar_node("tag", "丙")), changed)
    t.assert_eq("全部标签仅清标签组", replace_filter(node, "tag", None),
                AndNode([TermNode("title", ":", "乙"), TermNode("has", ":", "video")]))
    for cls, mode in ((AndNode, "all"), (OrNode, "any")):
        types = cls([TermNode("has", ":", "video"), TermNode("has", ":", "archive")])
        t.assert_eq(f"文件组合投影 {mode}", file_selection(types), ({"video", "archive"}, mode))
    t.assert_eq("NOT不伪造文件选择", file_selection(NotNode(TermNode("has", ":", "video"))), (set(), "any"))

    repo = Repository(connect(root / "cabinet.db"))
    try:
        ids = [repo.save_project(Project(title=f"合成{i}", tags=[name])) for i, name in enumerate(special)]
        empty = repo.save_project(Project(title="无标签"))
        for pid, name in zip(ids, special):
            query = parse_search(format_search(sidebar_node("tag", name))).ast
            t.assert_eq(f"特殊标签精确查询 {name}", [p.id for p in repo.list_projects_query(query)], [pid])
        similar = repo.save_project(Project(title="不应匹配通配符", tags=["前缀xxx/子标签"]))
        query = parse_search(format_search(sidebar_node("tag_prefix", "前缀%_"))).ast
        t.assert_eq("层级前缀通配符按字面值", [p.id for p in repo.list_projects_query(query)], [ids[-1]])
        repo.set_projects_favorite([ids[0]], True)
        with repo.conn:
            repo.conn.execute("UPDATE projects SET mcp_modified_at='2026-11-16' WHERE id=?", (ids[1],))
            fid = repo.list_fields()[0].id
            repo.conn.execute("INSERT INTO project_field_suggestions(project_id,field_id,status) VALUES(?,?,'pending')", (ids[2], fid))
        all_ids = set(ids + [empty, similar])
        for kind, wanted in (("untagged", {empty}), ("favorite", {ids[0]}), ("mcp", {ids[1]}), ("review", {ids[2]})):
            node = sidebar_node(kind, "")
            t.assert_eq(f"{kind}点击条件可独立执行", {p.id for p in repo.list_projects_query(node)}, wanted)
            negative = TermNode(kind, ":", "false")
            t.assert_eq(f"{kind}否定条件", {p.id for p in repo.list_projects_query(negative)}, all_ids - wanted)
            t.assert_eq(f"{kind}无效布尔值", repo.list_projects_query(TermNode(kind, ":", "invalid")), [])
    finally:
        repo.conn.close()


if __name__ == "__main__":
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-search-sync-") as tmp:
        run(Path(tmp), t)
    raise SystemExit(0 if t.report() else 1)
