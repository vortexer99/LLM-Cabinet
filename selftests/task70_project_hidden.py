"""隐藏项目的持久化、原子更新与可见搜索回归；仅使用显式临时数据库。"""
from __future__ import annotations

import random
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SCHEMA_VERSION, connect
from app.models import FileItem, Project
from app.repository import Repository
from app.search import AndNode, NotNode, OrNode, TermNode, parse_search
from selftests._common import T


def run(root: Path, t: T) -> None:
    db = root / "hidden.db"
    repo = Repository(connect(db))
    other = Repository(connect(root / "other.db"))
    try:
        first = repo.save_project(Project(title="课程视频", description_md="共同描述", tags=["课程"]))
        second = repo.save_project(Project(title="课程压缩", description_md="共同描述", tags=["课程", "仅隐藏"]))
        third = repo.save_project(Project(title="其他可见"))
        fourth = repo.save_project(Project(title="其他隐藏"))
        all_ids = {first, second, third, fourth}
        visible_ids = {first, third}
        hidden_ids = {second, fourth}
        other.save_project(Project(title="另一库同ID"))
        repo.add_file(FileItem(project_id=first, path="missing/one.mp4", kind="video"))
        repo.add_file(FileItem(project_id=second, path="missing/two.zip", kind="archive"))
        fid = repo.add_field("隐藏关键词字段")
        repo.conn.execute("INSERT INTO project_field_values VALUES(?,?,?)", (second, fid, "隐藏专属词"))
        repo.conn.execute("INSERT INTO tags(name) VALUES(?)", ("无人使用",))
        # 重复待审记录不得让侧栏按建议条数而非项目数累计。
        for pid in (first, first, second, fourth):
            repo.conn.execute(
                "INSERT INTO project_field_suggestions(project_id,field_id,suggested_value) VALUES(?,?,?)",
                (pid, fid, "建议"),
            )
        repo.conn.execute("UPDATE projects SET mcp_modified_at='2020-01-01' WHERE id IN (?,?)", (first, second))
        repo.conn.commit()
        repo.set_projects_favorite([first, second], True)
        tables = ("projects", "files", "project_tags", "project_field_values", "project_field_suggestions")
        before = {table: [tuple(row) for row in repo.conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                  for table in tables}
        t.assert_eq("初始隐藏为空", repo.hidden_project_ids(), set())
        t.assert_eq("批量去重且忽略不存在ID", repo.set_projects_hidden([second, fourth, second, -1], True), 2)
        t.assert_eq("隐藏项目集", repo.hidden_project_ids(), hidden_ids)
        t.assert_eq("重复隐藏不计变化", repo.set_projects_hidden([second, fourth], True), 0)
        t.assert_eq("空批次无变化", repo.set_projects_hidden([], True), 0)
        t.assert_eq("另一库不受影响", other.hidden_project_ids(), set())
        t.assert_eq("收藏保持独立", repo.favorite_project_ids(), {first, second})
        for table in tables:
            t.assert_eq(f"隐藏不修改{table}",
                        [tuple(row) for row in repo.conn.execute(f"SELECT * FROM {table} ORDER BY rowid")], before[table])
        t.assert_eq("无需数据库迁移", repo.conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

        def query(text: str) -> set[int]:
            parsed = parse_search(text)
            assert parsed.ok, parsed.error
            return {p.id for p in repo.list_projects_query(parsed.ast)}

        cases = (
            ("", visible_ids), ("课程", {first}), ("共同描述", {first}),
            ("隐藏专属词", set()), ("tag:课程", {first}), ("tag:仅隐藏", set()),
            ("tag_prefix:课程", {first}), ("__tag_prefix:课程", {first}),
            ("__untagged:true", {third}), ("untagged:true", {third}),
            ("review:true", {first}), ("mcp:true", {first}),
            ("has:video", {first}), ("has:archive", set()), ("ext:zip", set()),
            ("favorite:true", {first}), ("favorite:false", {third}),
            ("hidden:true", hidden_ids), ("HIDDEN=TRUE", hidden_ids), ("hidden:1", hidden_ids),
            ("hidden:false", visible_ids), ("hidden=0", visible_ids), ("hidden:all", all_ids),
            ("HiDdEn=ALL", all_ids), ("NOT hidden:true", visible_ids),
            ("NOT hidden:false", hidden_ids), ("NOT hidden:all", set()),
            ("hidden:true AND tag:课程 AND has:archive", {second}),
            ("hidden:all AND 隐藏专属词", {second}),
            ("hidden:all AND favorite:true", {first, second}),
            ("hidden:false OR has:archive", {first, second, third}),
            ("hidden:true OR title:其他可见", {second, third, fourth}),
            ("课程 OR (title:其他可见 AND NOT hidden:all)", {first, second}),
            ("NOT (hidden:false OR has:archive)", {fourth}),
            ("NOT (NOT (hidden:true))", hidden_ids),
            ("hidden:all OR title:不存在", all_ids),
            ("hidden:unknown", set()), ("hidden:>true", set()),
            ("NOT hidden:unknown", visible_ids), ("NOT hidden:>all", visible_ids),
            ("hidden:unknown OR tag:课程", {first}),
            ("hidden:unknown OR has:archive", set()),
            ("hidden:unknown OR hidden:true", hidden_ids),
        )
        # 所有搜索均只查登记元数据，路径不存在也不得触发磁盘检查。
        with patch.object(Path, "stat", side_effect=AssertionError("搜索不得读取文件")):
            for text, expected in cases:
                t.assert_eq(f"搜索 {text!r}", query(text), expected)
            t.assert_eq("侧栏计数", repo.visible_sidebar_counts(), {
                "total": 2, "untagged": 1, "pending_review": 1,
                "mcp_modified": 1, "favorites": 1, "hidden": 2,
            })
            t.assert_eq("可见标签保留零计数", dict(repo.visible_tag_counts()), {"课程": 1, "仅隐藏": 0, "无人使用": 0})
            t.assert_eq("维护标签仍包含隐藏", dict(repo.tag_counts()), {"课程": 2, "仅隐藏": 1, "无人使用": 0})

        malformed_nodes = (
            TermNode("hidden", ":", ""), TermNode("hidden", ">", "true"),
            TermNode("hidden", "<=", "all"),
        )
        for node in malformed_nodes:
            t.assert_eq(f"非法hidden AST不放宽范围 {node}",
                        {p.id for p in repo.list_projects_query(NotNode(node))}, visible_ids)
        nested = OrNode([TermNode("title", ":", "课程"), AndNode([NotNode(TermNode("hidden", ":", "all"))])])
        t.assert_eq("深层AST保留整棵逻辑", {p.id for p in repo.list_projects_query(nested)}, {first, second})
        sampled = {random.choice(repo.list_projects_query(None)).id for _ in range(40)}
        t.assert_true("随机候选底层范围只有可见项目", sampled <= visible_ids)
        t.assert_eq("维护list_projects保留全库", {p.id for p in repo.list_projects()}, all_ids)
        t.assert_eq("维护关键词保留隐藏项目", {p.id for p in repo.list_projects(keyword="课程")}, {first, second})
        t.assert_true("隐藏项目仍能直接读取", repo.get_project(second) is not None)
        t.assert_eq("维护项目总数不变", repo.count_projects_total(), 4)

        repo.conn.close()
        repo = Repository(connect(db))
        t.assert_eq("重启持久化隐藏", repo.hidden_project_ids(), hidden_ids)
        repo.set_projects_favorite([second], False)
        t.assert_eq("取消收藏不取消隐藏", repo.hidden_project_ids(), hidden_ids)
        repo.set_projects_favorite([second], True)

        repo.conn.execute("BEGIN")
        repo.conn.execute("INSERT INTO settings(key,value) VALUES('test_pending','keep')")
        failed = False
        try:
            repo.set_projects_hidden([first], True)
        except RuntimeError:
            failed = True
        t.assert_true("拒绝已有事务", failed)
        t.assert_true("拒绝后保持调用方事务", repo.conn.in_transaction)
        t.assert_eq("拒绝后无额外隐藏", repo.hidden_project_ids(), hidden_ids)
        repo.conn.rollback()
        t.assert_eq("调用方写入仍可回滚", repo.get_setting("test_pending", "absent"), "absent")

        # 第二项写入失败时，首项写入必须一并回滚；隐藏和恢复分别验证。
        for hidden, ids, stage, value_ref in (
            (True, [first, third], "INSERT", "NEW"),
            (False, [second, fourth], "DELETE", "OLD"),
        ):
            repo.conn.execute(
                f"CREATE TRIGGER fail_hidden BEFORE {stage} ON project_settings "
                f"WHEN {value_ref}.key='project_hidden' AND {value_ref}.project_id={ids[1]} "
                "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
            )
            repo.conn.commit()
            failed = False
            try:
                repo.set_projects_hidden(ids, hidden)
            except sqlite3.IntegrityError:
                failed = True
            t.assert_true(f"{stage}故障上抛", failed)
            t.assert_eq(f"{stage}整批回滚", repo.hidden_project_ids(), hidden_ids)
            repo.conn.execute("DROP TRIGGER fail_hidden")
            repo.conn.commit()

        t.assert_eq("批量恢复去重", repo.set_projects_hidden([second, second, -1], False), 1)
        t.assert_eq("恢复删除隐藏键", repo.get_project_setting(second, "project_hidden", "absent"), "absent")
        t.assert_eq("恢复不影响收藏", repo.favorite_project_ids(), {first, second})
        t.assert_eq("恢复后侧栏重新计数", repo.visible_sidebar_counts(), {
            "total": 3, "untagged": 1, "pending_review": 2,
            "mcp_modified": 2, "favorites": 2, "hidden": 1,
        })
        repo.delete_project(fourth)
        t.assert_eq("删除隐藏项目级联移除标记", repo.hidden_project_ids(), set())
        t.assert_eq("级联删除实际设置行", repo.conn.execute(
            "SELECT COUNT(*) FROM project_settings WHERE project_id=?", (fourth,)
        ).fetchone()[0], 0)
        repo.set_projects_hidden([first, second, third], True)
        t.assert_eq("全部隐藏可见计数为零", repo.visible_sidebar_counts(), {
            "total": 0, "untagged": 0, "pending_review": 0,
            "mcp_modified": 0, "favorites": 0, "hidden": 3,
        })
        t.assert_true("全部隐藏标签计数均为零", all(count == 0 for _, count in repo.visible_tag_counts()))
        for pid in (first, second, third):
            repo.delete_project(pid)
        t.assert_eq("空库计数全为零", repo.visible_sidebar_counts(), {
            "total": 0, "untagged": 0, "pending_review": 0,
            "mcp_modified": 0, "favorites": 0, "hidden": 0,
        })
    finally:
        repo.conn.close()
        other.conn.close()


if __name__ == "__main__":
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-hidden-test-") as directory:
        run(Path(directory), t)
    sys.exit(0 if t.report() else 1)
