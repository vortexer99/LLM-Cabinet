"""task #20 v3 → v4 schema 迁移 + 系统字段值统一存储 自检。

验证范围：
- v3 → v4 迁移：projects 表 author/date/source_url/rating 4 列搬到
  project_field_values + DROP COLUMN
- rating "未填" 语义保护：v3 里 rating=0 表示未填，迁移不写
- 已存在的 project_field_values 行不被覆盖（INSERT OR IGNORE 行为）
- 没有对应 fields.key 字段时跳过迁移（fid 找不到 → 不写）
- 二次跑迁移幂等（已 DROP 列后 cols_to_migrate 为空，整段跳过）
- v4 新库走 SCHEMA executescript 直接拿到 v4 表结构，不带 4 列
- v4 全新库 connect 后 fields 表能正常 seed 受保护字段
- v4 后 Project 顶层属性不再有 author/date/source_url/rating
- v4 后所有"老系统字段"值通过 field_values dict 读写
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T, closing_repos

from app.db import (
    SCHEMA_VERSION,
    _migrate_v3_to_v4,
    _set_user_version,
    connect,
)
from app.models import Project
from app.repository import Repository


# v3 schema 的 projects 表完整定义（用于在测试里构造一个 v3 库）
V3_PROJECTS_DDL = """
CREATE TABLE projects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    author          TEXT,
    date            TEXT,
    source_url      TEXT,
    rating          INTEGER DEFAULT 0,
    description_md  TEXT,
    storage_mode    TEXT NOT NULL DEFAULT 'link',
    cover_file_id   INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# v3 fields / project_field_values 表（迁移函数需要它们存在）
V3_FIELDS_DDL = """
CREATE TABLE fields (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL DEFAULT 'text',
    ord             INTEGER NOT NULL DEFAULT 0,
    visible         INTEGER NOT NULL DEFAULT 1,
    key             TEXT,
    suggest_enabled INTEGER NOT NULL DEFAULT 1,
    prompt_hint     TEXT NOT NULL DEFAULT ''
);
"""

V3_PFV_DDL = """
CREATE TABLE project_field_values (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    field_id   INTEGER NOT NULL REFERENCES fields(id)   ON DELETE CASCADE,
    value      TEXT,
    PRIMARY KEY (project_id, field_id)
);
"""


def _make_v3_db(path: Path) -> sqlite3.Connection:
    """造一个最小可用的 v3 库（仅含 projects/fields/project_field_values）。"""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(V3_PROJECTS_DDL + V3_FIELDS_DDL + V3_PFV_DDL)
    _set_user_version(conn, 3)
    conn.commit()
    return conn


# =============================================================================
# 测试用例
# =============================================================================
def test_migrate_v3_to_v4_happy_path(t: T, tmp: Path) -> None:
    """全量迁移：4 个老系统字段都有 fields 记录，4 个项目各有不同填值组合。"""
    db = tmp / "v3.db"
    conn = _make_v3_db(db)

    # 注入 4 个老系统字段（模拟 v3 库里用户通过新建库向导勾选过）
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO fields(name, type, ord, visible, key) "
        "VALUES('标题', 'text', 0, 1, 'title')"
    )
    cur.execute(
        "INSERT INTO fields(name, type, ord, visible, key) "
        "VALUES('作者', 'text', 1, 1, 'author')"
    )
    fid_author = cur.lastrowid
    cur.execute(
        "INSERT INTO fields(name, type, ord, visible, key) "
        "VALUES('日期', 'date', 2, 1, 'date')"
    )
    fid_date = cur.lastrowid
    cur.execute(
        "INSERT INTO fields(name, type, ord, visible, key) "
        "VALUES('评分', 'rating', 3, 1, 'rating')"
    )
    fid_rating = cur.lastrowid
    cur.execute(
        "INSERT INTO fields(name, type, ord, visible, key) "
        "VALUES('来源', 'url', 4, 1, 'source_url')"
    )
    fid_source = cur.lastrowid

    # 4 个项目
    cur.execute(
        "INSERT INTO projects(title, author, date, source_url, rating) "
        "VALUES(?,?,?,?,?)",
        ("P1 全填", "Alice", "2024-01-01", "https://a.com", 5),
    )
    p1 = cur.lastrowid
    cur.execute(
        "INSERT INTO projects(title, author, date, source_url, rating) "
        "VALUES(?,?,?,?,?)",
        ("P2 部分", "Bob", None, "", 0),  # rating=0 视为未填
    )
    p2 = cur.lastrowid
    cur.execute(
        "INSERT INTO projects(title, author, date, source_url, rating) "
        "VALUES(?,?,?,?,?)",
        ("P3 全空", None, None, None, 0),
    )
    p3 = cur.lastrowid
    cur.execute(
        "INSERT INTO projects(title, author, date, source_url, rating) "
        "VALUES(?,?,?,?,?)",
        ("P4 仅 rating", None, None, None, 4),
    )
    p4 = cur.lastrowid
    conn.commit()

    # 执行迁移
    _migrate_v3_to_v4(conn)
    conn.commit()

    # 验证 4 列已 DROP
    cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    t.assert_true("projects 表不再有 author 列", "author" not in cols)
    t.assert_true("projects 表不再有 date 列", "date" not in cols)
    t.assert_true(
        "projects 表不再有 source_url 列", "source_url" not in cols
    )
    t.assert_true("projects 表不再有 rating 列", "rating" not in cols)
    t.assert_true("projects 表保留 title 列", "title" in cols)
    t.assert_true(
        "projects 表保留 description_md 列", "description_md" in cols
    )

    # 验证值搬进 project_field_values（按 (pid, fid) 查）
    def _pfv(pid: int, fid: int) -> str:
        row = conn.execute(
            "SELECT value FROM project_field_values "
            "WHERE project_id=? AND field_id=?",
            (pid, fid),
        ).fetchone()
        return row["value"] if row else ""

    # P1：全填 → 4 字段都有值
    t.assert_eq("P1.author", _pfv(p1, fid_author), "Alice")
    t.assert_eq("P1.date", _pfv(p1, fid_date), "2024-01-01")
    t.assert_eq(
        "P1.source_url", _pfv(p1, fid_source), "https://a.com"
    )
    t.assert_eq("P1.rating CAST TEXT", _pfv(p1, fid_rating), "5")

    # P2：author="Bob"，date=NULL，source_url=""，rating=0
    t.assert_eq("P2.author", _pfv(p2, fid_author), "Bob")
    t.assert_eq("P2.date 为 NULL 不迁", _pfv(p2, fid_date), "")
    t.assert_eq("P2.source_url 为空串不迁", _pfv(p2, fid_source), "")
    t.assert_eq(
        "P2.rating=0 视为未填不迁（v3 \"未填\" 语义保护）",
        _pfv(p2, fid_rating), "",
    )

    # P3：全空 → 一条 pfv 都不应存在
    cnt_p3 = conn.execute(
        "SELECT COUNT(*) FROM project_field_values WHERE project_id=?", (p3,)
    ).fetchone()[0]
    t.assert_eq("P3 全空：project_field_values 无记录", int(cnt_p3), 0)

    # P4：仅 rating=4 → 只 rating 一条
    t.assert_eq("P4.rating=4 CAST 后落地", _pfv(p4, fid_rating), "4")
    t.assert_eq("P4.author 为 NULL 不迁", _pfv(p4, fid_author), "")
    cnt_p4 = conn.execute(
        "SELECT COUNT(*) FROM project_field_values WHERE project_id=?", (p4,)
    ).fetchone()[0]
    t.assert_eq("P4 只迁了 rating 一条", int(cnt_p4), 1)

    conn.close()


def test_migrate_idempotent(t: T, tmp: Path) -> None:
    """二次跑迁移不应出错也不应重复写值（4 列已 DROP，整段早返回）。"""
    db = tmp / "v3_twice.db"
    conn = _make_v3_db(db)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO fields(name, type, ord, visible, key) "
        "VALUES('作者', 'text', 0, 1, 'author')"
    )
    fid_author = cur.lastrowid
    cur.execute(
        "INSERT INTO projects(title, author) VALUES('P', 'Z')"
    )
    pid = cur.lastrowid
    conn.commit()

    _migrate_v3_to_v4(conn)
    conn.commit()
    # 二次跑：不应该崩、不应该报错
    _migrate_v3_to_v4(conn)
    conn.commit()

    val = conn.execute(
        "SELECT value FROM project_field_values "
        "WHERE project_id=? AND field_id=?",
        (pid, fid_author),
    ).fetchone()
    t.assert_eq("二次迁移后值仍正确", val["value"], "Z")
    cnt = conn.execute(
        "SELECT COUNT(*) FROM project_field_values"
    ).fetchone()[0]
    t.assert_eq("二次迁移不重复插入（仍只 1 条）", int(cnt), 1)
    conn.close()


def test_migrate_no_field_record(t: T, tmp: Path) -> None:
    """fields 表里没有对应 key 时跳过迁移（fid 找不到 → 不写）。"""
    db = tmp / "v3_nofield.db"
    conn = _make_v3_db(db)
    cur = conn.cursor()
    # 故意只 seed title，不 seed author/date/source_url/rating
    cur.execute(
        "INSERT INTO fields(name, type, ord, visible, key) "
        "VALUES('标题', 'text', 0, 1, 'title')"
    )
    cur.execute(
        "INSERT INTO projects(title, author, date, source_url, rating) "
        "VALUES(?,?,?,?,?)",
        ("P", "Alice", "2024", "u", 3),
    )
    conn.commit()

    _migrate_v3_to_v4(conn)
    conn.commit()

    # 列仍被 DROP
    cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    t.assert_true("仍 DROP 4 列", "author" not in cols)
    # project_field_values 应为空（没有 fid 可写）
    cnt = conn.execute(
        "SELECT COUNT(*) FROM project_field_values"
    ).fetchone()[0]
    t.assert_eq(
        "fields 无对应 key 时不写 field_values（数据无声丢弃）", int(cnt), 0
    )
    conn.close()


def test_migrate_existing_pfv_preserved(t: T, tmp: Path) -> None:
    """如果 project_field_values 里已有 (pid, fid) 行（用户手工动过），
    迁移用 INSERT OR IGNORE 保留已有值不覆盖。"""
    db = tmp / "v3_pfv_existing.db"
    conn = _make_v3_db(db)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO fields(name, type, ord, visible, key) "
        "VALUES('作者', 'text', 0, 1, 'author')"
    )
    fid_author = cur.lastrowid
    cur.execute(
        "INSERT INTO projects(title, author) VALUES('P', 'projects 列值')"
    )
    pid = cur.lastrowid
    # 预先在 pfv 里放一条不同的值
    cur.execute(
        "INSERT INTO project_field_values(project_id, field_id, value) "
        "VALUES(?, ?, 'pfv 已有值')",
        (pid, fid_author),
    )
    conn.commit()

    _migrate_v3_to_v4(conn)
    conn.commit()

    val = conn.execute(
        "SELECT value FROM project_field_values "
        "WHERE project_id=? AND field_id=?",
        (pid, fid_author),
    ).fetchone()
    t.assert_eq(
        "已有 pfv 行不被 projects 列值覆盖（INSERT OR IGNORE 语义）",
        val["value"], "pfv 已有值",
    )
    conn.close()


def test_fresh_v4_db_via_connect(t: T, tmp: Path) -> None:
    """全新库走 connect() 直接拿到 v4 schema。"""
    db = tmp / "fresh.db"
    repo = Repository(connect(db))
    with closing_repos(repo):
        # v4 schema：projects 表不该有 4 列
        cols = {
            r[1] for r in repo.conn.execute(
                "PRAGMA table_info(projects)"
            ).fetchall()
        }
        t.assert_true(
            "全新库 projects 表无 author 列", "author" not in cols
        )
        t.assert_true(
            "全新库 projects 表无 date 列", "date" not in cols
        )
        t.assert_true(
            "全新库 projects 表无 source_url 列",
            "source_url" not in cols,
        )
        t.assert_true(
            "全新库 projects 表无 rating 列", "rating" not in cols
        )
        # SCHEMA_VERSION 应该已被打 v4
        v = repo.conn.execute("PRAGMA user_version").fetchone()[0]
        t.assert_eq("全新库 user_version = 4", int(v), 4)
        t.assert_eq("常量 SCHEMA_VERSION = 4", SCHEMA_VERSION, 4)


def test_v3_db_full_migration_via_connect(t: T, tmp: Path) -> None:
    """端到端：v3 老库走 connect() 触发 _run_migrations，迁移完整完成。"""
    db = tmp / "v3_e2e.db"
    # 先造一个完整的 v3 库（手写 + user_version=3）
    conn0 = _make_v3_db(db)
    cur = conn0.cursor()
    # seed 一些字段和项目
    cur.execute(
        "INSERT INTO fields(name, type, ord, visible, key) "
        "VALUES('标题', 'text', 0, 1, 'title')"
    )
    cur.execute(
        "INSERT INTO fields(name, type, ord, visible, key) "
        "VALUES('作者', 'text', 1, 1, 'author')"
    )
    cur.execute(
        "INSERT INTO fields(name, type, ord, visible, key) "
        "VALUES('评分', 'rating', 2, 1, 'rating')"
    )
    cur.execute(
        "INSERT INTO projects(title, author, rating) "
        "VALUES('老项目 A', '鲁迅', 5)"
    )
    cur.execute(
        "INSERT INTO projects(title, author, rating) "
        "VALUES('老项目 B', '巴金', 0)"
    )
    conn0.commit()
    conn0.close()

    # 用 connect() 打开（应走 v3→v4 迁移路径）
    repo = Repository(connect(db))
    with closing_repos(repo):
        # 验证 user_version 推到 4
        v = repo.conn.execute("PRAGMA user_version").fetchone()[0]
        t.assert_eq("connect 后 user_version 推到 4", int(v), 4)

        # 验证 projects 表 4 列已 DROP
        cols = {
            r[1] for r in repo.conn.execute(
                "PRAGMA table_info(projects)"
            ).fetchall()
        }
        t.assert_true("迁移后无 author 列", "author" not in cols)
        t.assert_true("迁移后无 rating 列", "rating" not in cols)

        # 通过 repo 读项目（应能正确拿到 field_values）
        projects = repo.list_projects()
        t.assert_eq("项目数 = 2", len(projects), 2)
        # 按 title 找
        by_title = {p.title: p for p in projects}
        p_a = by_title["老项目 A"]
        p_b = by_title["老项目 B"]

        # 通过 fields 找 author/rating 的 fid
        f_by_key = {f.key: f for f in repo.list_fields() if f.key}
        fid_author = f_by_key["author"].id
        fid_rating = f_by_key["rating"].id

        t.assert_eq(
            "老项目 A.author 通过 field_values 还原",
            p_a.field_values.get(fid_author), "鲁迅",
        )
        t.assert_eq(
            "老项目 A.rating 通过 field_values 还原（CAST TEXT）",
            p_a.field_values.get(fid_rating), "5",
        )
        t.assert_eq(
            "老项目 B.author 还原", p_b.field_values.get(fid_author), "巴金"
        )
        t.assert_true(
            "老项目 B.rating=0 不迁（未填语义保护）",
            fid_rating not in p_b.field_values,
        )


def test_project_dataclass_no_legacy_attrs(t: T) -> None:
    """v4 后 Project dataclass 顶层不再有 author/date/source_url/rating。"""
    p = Project(title="X")
    t.assert_true("Project 无 author 属性", not hasattr(p, "author"))
    t.assert_true("Project 无 date 属性", not hasattr(p, "date"))
    t.assert_true(
        "Project 无 source_url 属性", not hasattr(p, "source_url")
    )
    t.assert_true("Project 无 rating 属性", not hasattr(p, "rating"))
    # 但保留 title / description_md / tags / field_values
    t.assert_true("Project 保留 title", hasattr(p, "title"))
    t.assert_true(
        "Project 保留 description_md", hasattr(p, "description_md")
    )
    t.assert_true("Project 保留 tags", hasattr(p, "tags"))
    t.assert_true(
        "Project 保留 field_values", hasattr(p, "field_values")
    )


def test_v4_repo_read_write_legacy_fields(t: T, tmp: Path) -> None:
    """v4 后老系统字段（author/date/...）的读写完全走 field_values dict。"""
    db = tmp / "v4_rw.db"
    repo = Repository(connect(db))
    with closing_repos(repo):
        # 注入 author 字段
        cur = repo.conn.cursor()
        cur.execute(
            "INSERT INTO fields(name, type, ord, visible, key) "
            "VALUES('作者', 'text', 5, 1, 'author')"
        )
        fid_author = cur.lastrowid
        repo.conn.commit()

        # 通过 field_values 写
        p = Project(title="测试", field_values={fid_author: "鲁迅"})
        pid = repo.save_project(p)

        # 读回来：author 值应在 field_values dict 里
        p2 = repo.get_project(pid)
        t.assert_eq(
            "v4 后老系统字段值通过 field_values 读出",
            p2.field_values.get(fid_author), "鲁迅",
        )

        # get_field_value 也应通过 field_values 路径返回
        f_author = repo.get_field(fid_author)
        t.assert_eq(
            "get_field_value 走 field_values 路径",
            repo.get_field_value(p2, f_author), "鲁迅",
        )

        # set_field_value_on_project 也走 field_values dict
        repo.set_field_value_on_project(p2, f_author, "巴金")
        t.assert_eq(
            "set_field_value_on_project 修改 field_values",
            p2.field_values.get(fid_author), "巴金",
        )


# =============================================================================
# 主入口
# =============================================================================
def main() -> int:
    t = T()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        tmp = Path(td)
        # 每个测试自带不同 db 文件名，共用同一个 tmp 目录
        test_migrate_v3_to_v4_happy_path(t, tmp)
        test_migrate_idempotent(t, tmp)
        test_migrate_no_field_record(t, tmp)
        test_migrate_existing_pfv_preserved(t, tmp)
        test_fresh_v4_db_via_connect(t, tmp)
        test_v3_db_full_migration_via_connect(t, tmp)
        test_project_dataclass_no_legacy_attrs(t)
        test_v4_repo_read_write_legacy_fields(t, tmp)
    return 0 if t.report() else 1


if __name__ == "__main__":
    sys.exit(main())
