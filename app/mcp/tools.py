"""MCP Tool implementations.

Each tool is an ``async`` function that accepts a ``LibraryContext`` as
the first positional argument (injected by ``make_mcp_server`` at call time).

All return values are plain dicts / lists — no Repository or Library objects
leak across the protocol boundary.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .context import LibraryContext

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Read-only tools (T1)
# ---------------------------------------------------------------------------


async def search_projects(
    ctx: LibraryContext,
    keyword: str = "",
    tag: str = "",
    field_filter: str = "",
) -> list[dict[str, Any]]:
    """Search projects by keyword (title / description) and/or tag.

    ``field_filter`` is reserved (not yet implemented), accepted but ignored.
    """
    projects = ctx.repo.list_projects(keyword=keyword, tag=tag)

    # Bulk-fetch file counts for the result set
    file_counts: dict[int, int] = {}
    if projects:
        pids = [p.id for p in projects if p.id is not None]
        if pids:
            placeholders = ",".join("?" for _ in pids)
            rows = ctx.repo.conn.execute(
                f"SELECT project_id, COUNT(*) AS c FROM files "
                f"WHERE project_id IN ({placeholders}) GROUP BY project_id",
                pids,
            ).fetchall()
            file_counts = {r["project_id"]: r["c"] for r in rows}

    return [
        {
            "id": p.id,
            "title": p.title,
            "tags": p.tags,
            "file_count": file_counts.get(p.id or 0, 0),
            "updated_at": p.updated_at,
        }
        for p in projects
    ]


async def get_project(ctx: LibraryContext, project_id: int) -> dict[str, Any]:
    """Get full metadata for a single project."""
    p = ctx.repo.get_project(project_id)
    if p is None:
        raise ValueError(f"项目 {project_id} 不存在")
    return {
        "id": p.id,
        "title": p.title,
        "description_md": p.description_md,
        "storage_mode": p.storage_mode,
        "cover_file_id": p.cover_file_id,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "tags": p.tags,
        "field_values": p.field_values,
    }


async def list_files(
    ctx: LibraryContext, project_id: int, kind: str = "",
) -> list[dict[str, Any]]:
    """List files belonging to a project, optionally filtered by ``kind``."""
    files = ctx.repo.list_files(project_id)
    if kind:
        files = [f for f in files if f.kind == kind]
    return [
        {
            "id": f.id,
            "path": f.path,
            "label": f.label,
            "kind": f.kind,
            "added_at": f.added_at,
            "missing": f.missing,
        }
        for f in files
    ]


async def list_pending_suggestions(
    ctx: LibraryContext, project_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """List pending LLM suggestions, optionally for a specific project."""
    if project_id is not None:
        suggestions = ctx.repo.list_pending_suggestions(project_id)
    else:
        # Grab all projects with pending suggestions, then collect
        pids = ctx.repo.conn.execute(
            "SELECT DISTINCT project_id FROM project_field_suggestions "
            "WHERE status='pending'"
        ).fetchall()
        suggestions = []
        for row in pids:
            suggestions.extend(ctx.repo.list_pending_suggestions(row["project_id"]))

    # Resolve field names in one batch
    field_names: dict[int, str] = {}
    if suggestions:
        fids = list({s.field_id for s in suggestions if s.field_id})
        if fids:
            placeholders = ",".join("?" for _ in fids)
            rows = ctx.repo.conn.execute(
                f"SELECT id, name FROM fields WHERE id IN ({placeholders})",
                fids,
            ).fetchall()
            field_names = {r["id"]: r["name"] for r in rows}

    return [
        {
            "id": s.id,
            "project_id": s.project_id,
            "field_id": s.field_id,
            "field_name": field_names.get(s.field_id, ""),
            "suggested_value": s.suggested_value,
            "status": s.status,
            "created_at": s.created_at,
        }
        for s in suggestions
    ]


async def count_projects(ctx: LibraryContext, tag: str = "") -> dict[str, int]:
    """Count total projects, optionally filtered by tag."""
    if tag:
        projects = ctx.repo.list_projects(tag=tag)
        return {"total": len(projects)}
    return {"total": ctx.repo.count_projects_total()}


async def get_field_definition(
    ctx: LibraryContext, field_name: str,
) -> dict[str, Any]:
    """Get a field definition by its display name."""
    fields = ctx.repo.list_fields()
    for f in fields:
        if f.name == field_name:
            return {
                "id": f.id,
                "name": f.name,
                "type": f.type,
                "key": f.key,
                "ord": f.ord,
                "visible": f.visible,
                "suggest_enabled": getattr(f, "suggest_enabled", True),
                "prompt_hint": getattr(f, "prompt_hint", ""),
            }
    raise ValueError(f"字段 '{field_name}' 不存在")


async def list_libraries(ctx: LibraryContext) -> list[dict[str, Any]]:
    """Return all registered libraries from cabinet.json."""
    return ctx.list_libraries()


async def switch_library(
    ctx: LibraryContext, library_name: str,
) -> dict[str, Any]:
    """Switch the active library by name (no-op in T1 read-only mode)."""
    return ctx.switch(library_name)
