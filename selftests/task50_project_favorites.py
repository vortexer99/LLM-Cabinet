"""收藏持久化与搜索回归：显式临时库，不访问用户数据库。"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import connect
from app.models import FileItem, Project
from app.repository import Repository
from app.search import parse_search
from selftests._common import T


def run(root: Path, t: T) -> None:
    db = root / "favorites.db"
    repo = Repository(connect(db))
    other = Repository(connect(root / "other.db"))
    try:
        first = repo.save_project(Project(title="课程视频", tags=["课程"]))
        second = repo.save_project(Project(title="课程压缩", tags=["课程"]))
        third = repo.save_project(Project(title="其他"))
        other.save_project(Project(title="另一库同ID"))
        repo.add_file(FileItem(project_id=first, path="missing/one.mp4", kind="video"))
        repo.add_file(FileItem(project_id=second, path="missing/two.zip", kind="archive"))
        before = [tuple(row) for row in repo.conn.execute("SELECT * FROM projects ORDER BY id")]
        t.assert_eq("初始收藏为空", repo.favorite_project_ids(), set())
        t.assert_eq("批量去重忽略无效项目", repo.set_projects_favorite([first, second, first, -1], True), 2)
        t.assert_eq("收藏两个项目", repo.favorite_project_ids(), {first, second})
        t.assert_eq("重复收藏不计变化", repo.set_projects_favorite([first, second], True), 0)
        t.assert_eq("库间隔离", other.favorite_project_ids(), set())
        t.assert_eq("收藏不改元数据和更新时间", [tuple(row) for row in repo.conn.execute(
            "SELECT * FROM projects ORDER BY id")], before)

        def query(text: str) -> set[int]:
            parsed = parse_search(text)
            assert parsed.ok, parsed.error
            return {p.id for p in repo.list_projects_query(parsed.ast)}

        for text, expected in (
            ("favorite:true", {first, second}), ("FAVORITE=TRUE", {first, second}),
            ("favorite:1", {first, second}), ("favorite:false", {third}),
            ("favorite=0", {third}), ("NOT favorite:true", {third}),
            ("favorite:true AND has:video AND tag:课程", {first}),
            ("favorite:true AND has:archive", {second}),
            ("favorite:false OR has:archive", {second, third}),
            ("favorite:unknown", set()), ("favorite:>true", set()),
        ):
            t.assert_eq(text, query(text), expected)
        repo.conn.close()
        repo = Repository(connect(db))
        t.assert_eq("重开仍有收藏", repo.favorite_project_ids(), {first, second})

        # 第二项写入失败时，第一项也必须回滚，不能出现半批成功。
        repo.conn.execute(f"CREATE TRIGGER fail_favorite BEFORE DELETE ON project_settings "
                          f"WHEN OLD.project_id={second} BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
        repo.conn.commit()
        failed = False
        try:
            repo.set_projects_favorite([first, second], False)
        except sqlite3.IntegrityError:
            failed = True
        t.assert_true("故障触发异常", failed)
        t.assert_eq("故障整批回滚", repo.favorite_project_ids(), {first, second})
        repo.conn.execute("DROP TRIGGER fail_favorite")
        repo.conn.commit()
        t.assert_eq("批量取消去重", repo.set_projects_favorite([first, first], False), 1)
        t.assert_eq("取消删除收藏键", repo.get_project_setting(first, "project_favorite", "absent"), "absent")
        repo.delete_project(second)
        t.assert_eq("删除项目级联删除收藏", repo.favorite_project_ids(), set())
        t.assert_eq("空批次", repo.set_projects_favorite([], True), 0)
    finally:
        repo.conn.close()
        other.conn.close()


if __name__ == "__main__":
    t = T()
    with tempfile.TemporaryDirectory() as directory:
        run(Path(directory), t)
    sys.exit(0 if t.report() else 1)
