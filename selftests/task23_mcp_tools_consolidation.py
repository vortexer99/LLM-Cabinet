"""selftest for task #23: MCP tools consolidation (17 → 5 aggregate tools).

Verifies:
- ``make_mcp_server()`` registers exactly 5 tools with correct names
- Each dispatch action routes to the right underlying tools.py function
- Unknown action returns structured error, not exception
- Removed tools are no longer registered
- Audit log still records fine-grained tool_name
- ``switch_library`` description no longer says "no-op"
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

# Ensure we can import app.*
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from _common import T


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    description_md  TEXT,
    storage_mode    TEXT NOT NULL DEFAULT 'link',
    cover_file_id   INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS project_tags (
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id)     ON DELETE CASCADE,
    PRIMARY KEY (project_id, tag_id)
);
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    is_relative INTEGER NOT NULL DEFAULT 0,
    label       TEXT,
    kind        TEXT NOT NULL,
    ord         INTEGER NOT NULL DEFAULT 0,
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    missing     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fields (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL DEFAULT 'text',
    key             TEXT DEFAULT '',
    ord             INTEGER DEFAULT 0,
    visible         INTEGER DEFAULT 1,
    suggest_enabled INTEGER DEFAULT 1,
    prompt_hint     TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS project_field_values (
    project_id INTEGER NOT NULL,
    field_id   INTEGER NOT NULL,
    value      TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mcp_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_name     TEXT DEFAULT '',
    tool_name       TEXT DEFAULT '',
    arguments_json  TEXT DEFAULT '',
    result_status   TEXT DEFAULT '',
    error_message   TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now'))
);
INSERT INTO projects (id, title, description_md) VALUES (1, 'Test Project', 'test');
INSERT INTO fields (id, name, type, key) VALUES (1, 'rating', 'number', 'rating');
"""


def _init_temp_db(db_path: Path) -> None:
    """Create minimal schema in a temporary ``cabinet.db`` for selftest context."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def _make_test_context(db_path: Path):
    """Create a minimal LibraryContext with an in-memory SQLite db."""
    from app.cabinet import CabinetConfig
    from app.mcp.context import LibraryContext

    config = CabinetConfig(active_library=None, recent_libraries=[])
    ctx = LibraryContext(config, db_override=db_path)
    ctx.write_permission = "session"
    return ctx


def _collect_tool_names(mcp_server) -> set[str]:
    """Extract registered tool names from a FastMCP instance."""
    tm = getattr(mcp_server, "_tool_manager", None)
    if tm is not None:
        tools_dict = getattr(tm, "_tools", {})
        if tools_dict:
            return set(tools_dict.keys())
    return set()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_tool_count_and_names():
    t = T()

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "cabinet.db"
        _init_temp_db(db_path)
        ctx = _make_test_context(db_path)

        from app.mcp.server import make_mcp_server
        mcp = make_mcp_server(ctx)
        names = _collect_tool_names(mcp)

        expected = {"query_projects", "manage_project", "manage_files", "manage_libraries", "export_project"}
        t.assert_eq("tool count", len(names), 5)
        t.assert_true("all expected tools registered",
                      expected.issubset(names),
                      f"missing: {expected - names}, extra: {names - expected}")

        # verify removed tools are gone
        removed = {"search_projects", "get_project", "count_projects",
                   "list_files", "list_pending_suggestions",
                   "create_project", "update_project", "add_tag", "remove_tag",
                   "add_file", "remove_file",
                   "apply_suggestion", "trigger_llm_suggestion",
                   "list_libraries", "switch_library", "get_field_definition",
                   "import_folder"}
        still_present = removed & names
        t.assert_true("old tools removed", len(still_present) == 0,
                      f"still registered: {still_present}")

        ctx.repo.conn.close()

        # verify switch_library description no longer contains "no-op"
        server_py = Path(__file__).resolve().parent.parent / "app" / "mcp" / "server.py"
        source = server_py.read_text(encoding="utf-8")
        t.assert_true("switch_library desc not no-op",
                      "只读模式，切换功能暂不可用（no-op）" not in source)
        t.assert_true("switch_library desc is correct",
                      "单库模式" in source)

    return t.report()


def test_dispatch():
    """Verify each aggregate tool's action dispatch routes correctly."""
    t = T()

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "cabinet.db"
        _init_temp_db(db_path)
        ctx = _make_test_context(db_path)

        from app.mcp.server import make_mcp_server
        mcp = make_mcp_server(ctx)
        tools_dict = mcp._tool_manager._tools

        qp_fn = tools_dict["query_projects"].fn
        mp_fn = tools_dict["manage_project"].fn
        mf_fn = tools_dict["manage_files"].fn
        ml_fn = tools_dict["manage_libraries"].fn
        ep_fn = tools_dict["export_project"].fn

        async def run():
            import json as _json

            try:
                # --- query_projects ---
                r = _json.loads(await qp_fn(action="search", keyword=""))
                t.assert_true("qp search returns list", isinstance(r, list))

                r = _json.loads(await qp_fn(action="get", project_id=1))
                t.assert_eq("qp get project_id", r.get("id"), 1)

                r = _json.loads(await qp_fn(action="count"))
                t.assert_true("qp count total in result", "total" in r)

                r = _json.loads(await qp_fn(action="bad_action"))
                t.assert_eq("qp unknown action", r.get("ok"), False)

                # --- manage_project ---
                r = _json.loads(await mp_fn(action="create", title="New"))
                t.assert_eq("mp create ok", r.get("ok"), True)

                r = _json.loads(await mp_fn(action="update", project_id=1, title="Updated"))
                t.assert_eq("mp update ok", r.get("ok"), True)

                r = _json.loads(await mp_fn(action="add_tag", project_id=1, tag="test"))
                t.assert_eq("mp add_tag ok", r.get("ok"), True)

                r = _json.loads(await mp_fn(action="remove_tag", project_id=1, tag="test"))
                t.assert_eq("mp remove_tag ok", r.get("ok"), True)

                r = _json.loads(await mp_fn(action="bad_action"))
                t.assert_eq("mp unknown action", r.get("ok"), False)

                # --- manage_files ---
                r = _json.loads(await mf_fn(action="list", project_id=1))
                t.assert_true("mf list returns list", isinstance(r, list))

                r = _json.loads(await mf_fn(action="bad_action"))
                t.assert_eq("mf unknown action", r.get("ok"), False)

                # --- manage_libraries ---
                r = _json.loads(await ml_fn(action="list"))
                t.assert_true("ml list returns list", isinstance(r, list))

                r = _json.loads(await ml_fn(action="get_field", field_name="rating"))
                t.assert_eq("ml get_field found", r.get("name"), "rating")

                r = _json.loads(await ml_fn(action="get_fields"))
                t.assert_true("ml get_fields returns list", isinstance(r, list))
                t.assert_true("ml get_fields includes rating", any(
                    f.get("name") == "rating" for f in r
                ))

                r = _json.loads(await ml_fn(action="bad_action"))
                t.assert_eq("ml unknown action", r.get("ok"), False)

                # --- export_project ---
                r = _json.loads(await ep_fn(project_id=99999, target_dir=str(tmpdir)))
                t.assert_eq("ep missing project", r.get("ok"), False)

            finally:
                ctx.repo.conn.close()

        asyncio.run(run())

    return t.report()


def test_audit_log_fine_grained():
    """Verify mcp_audit records fine-grained tool_name, not aggregate name."""
    t = T()

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "cabinet.db"
        _init_temp_db(db_path)
        ctx = _make_test_context(db_path)

        from app.mcp.server import make_mcp_server
        mcp = make_mcp_server(ctx)
        mp_fn = mcp._tool_manager._tools["manage_project"].fn

        async def run():
            import json as _json
            try:
                result = _json.loads(await mp_fn(action="create", title="Audit Test"))
                t.assert_eq("create succeeded", result.get("ok"), True)

                ctx.repo.conn.commit()
                rows = ctx.repo.conn.execute(
                    "SELECT tool_name FROM mcp_audit ORDER BY id DESC LIMIT 1"
                ).fetchall()
                t.assert_true("audit has entries", len(rows) > 0)
                if rows:
                    t.assert_eq("audit records create_project (not manage_project)",
                                rows[0]["tool_name"], "create_project")
            finally:
                ctx.repo.conn.close()

        asyncio.run(run())

    return t.report()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("task23_mcp_tools_consolidation selftest")
    print("=" * 60)

    results = []
    for name, fn in [
        ("test_tool_count_and_names", test_tool_count_and_names),
        ("test_dispatch", test_dispatch),
        ("test_audit_log_fine_grained", test_audit_log_fine_grained),
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
