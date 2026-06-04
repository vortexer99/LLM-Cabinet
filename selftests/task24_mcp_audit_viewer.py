"""selftest for task #24: MCP audit log viewer + project modified marker."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from _common import T

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    description_md TEXT DEFAULT '',
    storage_mode TEXT DEFAULT 'link',
    mcp_modified_at TEXT
);
CREATE TABLE IF NOT EXISTS mcp_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL DEFAULT (datetime('now')),
    client_name     TEXT,
    tool_name       TEXT NOT NULL,
    arguments_json  TEXT,
    result_status   TEXT NOT NULL DEFAULT 'success',
    error_message   TEXT
);
INSERT INTO projects (id, title) VALUES (1, 'Alpha'), (2, 'Beta'), (3, 'Gamma');
INSERT INTO mcp_audit (id, ts, client_name, tool_name, arguments_json, result_status, error_message)
VALUES (1, '2026-06-04 10:00:00', 'claude', 'create_project', '{"title":"X"}', 'success', NULL),
       (2, '2026-06-04 10:01:00', 'cursor', 'add_file', '{"project_id":1}', 'success', NULL),
       (3, '2026-06-04 10:02:00', 'claude', 'remove_file', '{"file_id":99}', 'denied', '写操作已禁用');
"""


@contextmanager
def _temp_repo() -> Iterator:
    from app.repository import Repository
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "cabinet.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(SCHEMA)
        conn.commit()
        repo = Repository(conn)
        try:
            yield repo
        finally:
            repo.conn.close()


def test_mark_and_list_modified():
    t = T()
    with _temp_repo() as repo:
        modified = repo.list_mcp_modified_projects()
        t.assert_eq("initially empty", len(modified), 0)

        repo.mark_project_mcp_modified(1)
        modified = repo.list_mcp_modified_projects()
        t.assert_eq("one after mark", len(modified), 1)
        t.assert_eq("is project 1", modified[0]["id"], 1)
        t.assert_true("has timestamp", bool(modified[0].get("mcp_modified_at")))

        repo.mark_project_mcp_modified(2)
        modified = repo.list_mcp_modified_projects()
        t.assert_eq("two after mark", len(modified), 2)
        ids = {m["id"] for m in modified}
        t.assert_true("contains both", ids == {1, 2})
    return t.report()


def test_audit_count_and_list():
    t = T()
    with _temp_repo() as repo:
        t.assert_eq("total count", repo.count_mcp_audit(), 3)
        t.assert_eq("by client", repo.count_mcp_audit(client_name="cursor"), 1)
        t.assert_eq("by status", repo.count_mcp_audit(result_status="denied"), 1)
        t.assert_eq("by tool", repo.count_mcp_audit(tool_name="add_file"), 1)

        page1 = repo.list_mcp_audit(offset=0, limit=2)
        t.assert_eq("page1 size", len(page1), 2)
        t.assert_eq("page1 first (latest)", page1[0]["id"], 3)

        page2 = repo.list_mcp_audit(offset=2, limit=2)
        t.assert_eq("page2 size", len(page2), 1)
        t.assert_eq("page2 first", page2[0]["id"], 1)
    return t.report()


def test_migration_column_exists():
    t = T()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "cabinet.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(SCHEMA)
        conn.commit()

        cols = conn.execute("PRAGMA table_info(projects)").fetchall()
        col_names = [r[1] for r in cols]
        t.assert_true("mcp_modified_at exists", "mcp_modified_at" in col_names)

        conn.close()
    return t.report()


if __name__ == "__main__":
    print("=" * 60)
    print("task24_mcp_audit_viewer selftest")
    print("=" * 60)

    results = []
    for name, fn in [
        ("test_mark_and_list_modified", test_mark_and_list_modified),
        ("test_audit_count_and_list", test_audit_count_and_list),
        ("test_migration_column_exists", test_migration_column_exists),
    ]:
        print(f"\n--- {name} ---")
        ok = fn()
        results.append(ok)
        sys.stdout.flush()

    print("\n" + "=" * 60)
    all_passed = all(results)
    print("ALL PASSED" if all_passed else "SOME FAILED")
    print("=" * 60)
    sys.exit(0 if all_passed else 1)
