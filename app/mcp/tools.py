"""MCP Tool implementations.

Each tool is an ``async`` function that accepts a ``LibraryContext`` as
the first positional argument (injected by ``make_mcp_server`` at call time).

All return values are plain dicts / lists — no Repository or Library objects
leak across the protocol boundary.
"""

from __future__ import annotations

import logging
from typing import Any

from .context import LibraryContext

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Read-only tools (T1)
# ---------------------------------------------------------------------------


async def search_projects(
    ctx: LibraryContext,
    keyword: str = "",
    tag: str = "",
    tag_prefix: str = "",
    field_filter: str = "",
) -> list[dict[str, Any]]:
    """Search projects by keyword (title / description) and/or tag.

    ``field_filter`` is reserved (not yet implemented), accepted but ignored.
    """
    projects = ctx.repo.list_projects(
        keyword=keyword,
        tag=tag,
        tag_prefix=tag_prefix,
    )

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


async def get_all_fields(ctx: LibraryContext) -> list[dict[str, Any]]:
    """Return all field definitions in the current library."""
    fields = ctx.repo.list_fields()
    return [
        {
            "id": f.id,
            "name": f.name,
            "type": f.type,
            "key": f.key,
            "ord": f.ord,
            "visible": f.visible,
            "suggest_enabled": getattr(f, "suggest_enabled", True),
            "prompt_hint": getattr(f, "prompt_hint", ""),
        }
        for f in fields
    ]


async def list_libraries(ctx: LibraryContext) -> list[dict[str, Any]]:
    """Return all registered libraries from cabinet.json."""
    return ctx.list_libraries()


async def switch_library(
    ctx: LibraryContext, library_name: str,
) -> dict[str, Any]:
    """Switch the active library by name. Returns an error in single-DB mode."""
    return ctx.switch(library_name)


# ---------------------------------------------------------------------------
# Write tools (T3)
# ---------------------------------------------------------------------------

# ---- Audit logging ------------------------------------------------------


def _audit_log(ctx: LibraryContext, tool_name: str, arguments: dict, status: str, error: str = ""):
    """Record a tool invocation in the mcp_audit table."""
    import json as _json

    try:
        ctx.repo.conn.execute(
            "INSERT INTO mcp_audit (client_name, tool_name, arguments_json, result_status, error_message) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                ctx.client_name,
                tool_name,
                _json.dumps(arguments, ensure_ascii=False, default=str),
                status,
                error,
            ),
        )
        ctx.repo.conn.commit()
    except Exception:
        pass  # audit failure should not block the tool


# ---- Confirmation --------------------------------------------------------


def _check_write_permission(ctx: LibraryContext, tool_name: str) -> tuple[bool, str]:
    """Check whether a write tool is allowed to proceed.

    Returns:
        (allowed, reason) — ``allowed=False`` means the tool should be denied.
    """
    permission = ctx.write_permission
    if permission == "disabled":
        return False, "MCP 写操作已禁用（可在设置中调整）"
    return True, ""


def _confirm(ctx: LibraryContext, tool_name: str, message: str, danger: bool = False) -> tuple[bool, str]:
    """Check permission and return (allowed, reason).

    ``danger=True`` means the tool is high-risk and needs extra scrutiny.
    Currently always returns True if permission is not disabled.
    Future: full MCP elicitation (sampling) for text-based confirmation.
    """
    allowed, reason = _check_write_permission(ctx, tool_name)
    return allowed, reason


# ---- create_project ------------------------------------------------------


async def create_project(
    ctx: LibraryContext,
    title: str,
    tags: str = "",
    description: str = "",
    field_values: str = "",
) -> dict[str, Any]:
    """Create a new project (optionally with field values)."""
    allowed, reason = _confirm(ctx, "create_project", f"创建项目「{title}」")
    if not allowed:
        _audit_log(ctx, "create_project", {"title": title}, "denied", reason)
        return {"ok": False, "error": reason}

    try:
        from app.models import Project
        import json as _json

        tag_list = [t.strip() for t in tags.split(",")] if tags else []
        p = Project(title=title, description_md=description, tags=tag_list)

        if field_values:
            try:
                fv_dict = _json.loads(field_values)
                if isinstance(fv_dict, dict):
                    for fid_str, val in fv_dict.items():
                        try:
                            p.field_values[int(fid_str)] = str(val)
                        except (ValueError, TypeError):
                            log.warning(
                                "create_project: 跳过非数字 field_id %r（请使用 field.id，不是 field.name）",
                                fid_str,
                            )
            except _json.JSONDecodeError:
                log.warning("create_project: field_values JSON 解析失败")

        pid = ctx.repo.save_project(p)
        ctx.repo.mark_project_mcp_modified(pid)
        _audit_log(ctx, "create_project", {"title": title, "tags": tags}, "success")
        return {"ok": True, "project_id": pid}
    except Exception as e:
        _audit_log(ctx, "create_project", {"title": title}, "error", str(e))
        return {"ok": False, "error": str(e)}


# ---- update_project -------------------------------------------------------


async def update_project(
    ctx: LibraryContext,
    project_id: int,
    title: str = "",
    description: str = "",
    tags: str = "",
    field_values: str = "",
) -> dict[str, Any]:
    """Update an existing project's metadata."""
    allowed, reason = _confirm(ctx, "update_project", f"修改项目 #{project_id}")
    if not allowed:
        _audit_log(ctx, "update_project", {"project_id": project_id}, "denied", reason)
        return {"ok": False, "error": reason}

    try:
        p = ctx.repo.get_project(project_id)
        if p is None:
            _audit_log(ctx, "update_project", {"project_id": project_id}, "error", "项目不存在")
            return {"ok": False, "error": f"项目 {project_id} 不存在"}

        if title:
            p.title = title
        if description:
            p.description_md = description
        if tags:
            p.tags = [t.strip() for t in tags.split(",")]

        # Parse field_values if provided: JSON {"field_id": "value"}
        # keys MUST be numeric field IDs; field names will be silently skipped
        if field_values:
            import json as _json
            try:
                fv_dict = _json.loads(field_values)
                if isinstance(fv_dict, dict):
                    for fid_str, val in fv_dict.items():
                        try:
                            p.field_values[int(fid_str)] = str(val)
                        except (ValueError, TypeError):
                            log.warning(
                                "update_project #%d: 跳过非数字 field_id %r（请使用 get_fields 返回的 field.id，不是 field.name）",
                                project_id, fid_str,
                            )
            except _json.JSONDecodeError:
                log.warning("update_project #%d: field_values JSON 解析失败", project_id)

        ctx.repo.save_project(p)
        ctx.repo.mark_project_mcp_modified(project_id)
        _audit_log(ctx, "update_project", {
            "project_id": project_id,
            "title": title or "(不变)",
            "description": description or "(不变)",
            "tags": tags or "(不变)",
            "field_values": field_values or "(不变)",
        }, "success")
        return {"ok": True, "project_id": project_id}
    except Exception as e:
        _audit_log(ctx, "update_project", {"project_id": project_id}, "error", str(e))
        return {"ok": False, "error": str(e)}


# ---- add_tag / remove_tag -------------------------------------------------


async def add_tag(ctx: LibraryContext, project_id: int, tag: str) -> dict[str, Any]:
    """Add a tag to a project."""
    allowed, reason = _confirm(
        ctx, "add_tag", f"给项目 #{project_id} 添加标签「{tag}」"
    )
    if not allowed:
        _audit_log(ctx, "add_tag", {"project_id": project_id, "tag": tag}, "denied", reason)
        return {"ok": False, "error": reason}

    try:
        p = ctx.repo.get_project(project_id)
        if p is None:
            return {"ok": False, "error": f"项目 {project_id} 不存在"}
        if tag not in p.tags:
            p.tags.append(tag)
            ctx.repo.save_project(p)
        ctx.repo.mark_project_mcp_modified(project_id)
        _audit_log(ctx, "add_tag", {"project_id": project_id, "tag": tag}, "success")
        return {"ok": True}
    except Exception as e:
        _audit_log(ctx, "add_tag", {"project_id": project_id}, "error", str(e))
        return {"ok": False, "error": str(e)}


async def remove_tag(ctx: LibraryContext, project_id: int, tag: str) -> dict[str, Any]:
    """Remove a tag from a project."""
    allowed, reason = _confirm(
        ctx, "remove_tag", f"移除项目 #{project_id} 的标签「{tag}」"
    )
    if not allowed:
        _audit_log(ctx, "remove_tag", {"project_id": project_id, "tag": tag}, "denied", reason)
        return {"ok": False, "error": reason}

    try:
        p = ctx.repo.get_project(project_id)
        if p is None:
            return {"ok": False, "error": f"项目 {project_id} 不存在"}
        if tag in p.tags:
            p.tags.remove(tag)
            ctx.repo.save_project(p)
        ctx.repo.mark_project_mcp_modified(project_id)
        _audit_log(ctx, "remove_tag", {"project_id": project_id, "tag": tag}, "success")
        return {"ok": True}
    except Exception as e:
        _audit_log(ctx, "remove_tag", {"project_id": project_id}, "error", str(e))
        return {"ok": False, "error": str(e)}


# ---- add_file / remove_file -----------------------------------------------


async def add_file(
    ctx: LibraryContext,
    project_id: int,
    path: str,
    storage_mode: str = "link",
    label: str = "",
) -> dict[str, Any]:
    """Add a file to a project."""
    allowed, reason = _confirm(
        ctx, "add_file", f"给项目 #{project_id} 添加文件「{path}」"
    )
    if not allowed:
        _audit_log(ctx, "add_file", {"project_id": project_id, "path": path}, "denied", reason)
        return {"ok": False, "error": reason}

    try:
        from app.models import FileItem
        from pathlib import Path as _Path

        fp = _Path(path)
        kind = _guess_file_kind(fp.suffix)

        f = FileItem(
            project_id=project_id,
            path=str(fp),
            is_relative=(storage_mode == "copy"),
            label=label or fp.name,
            kind=kind,
        )
        fid = ctx.repo.add_file(f)
        ctx.repo.mark_project_mcp_modified(project_id)
        _audit_log(ctx, "add_file", {"project_id": project_id, "path": path}, "success")
        return {"ok": True, "file_id": fid}
    except Exception as e:
        _audit_log(ctx, "add_file", {"project_id": project_id}, "error", str(e))
        return {"ok": False, "error": str(e)}


async def remove_file(ctx: LibraryContext, file_id: int) -> dict[str, Any]:
    """Remove a file from its project (double-confirmation in tool description)."""
    allowed, reason = _confirm(
        ctx, "remove_file", f"删除文件 #{file_id}", danger=True,
    )
    if not allowed:
        _audit_log(ctx, "remove_file", {"file_id": file_id}, "denied", reason)
        return {"ok": False, "error": reason}

    try:
        f = ctx.repo.get_file(file_id)
        if f is None:
            return {"ok": False, "error": f"文件 {file_id} 不存在"}
        pid = f.project_id  # capture before deletion
        ctx.repo.delete_file(file_id)
        if pid:
            ctx.repo.mark_project_mcp_modified(pid)
        _audit_log(ctx, "remove_file", {"file_id": file_id}, "success")
        return {"ok": True}
    except Exception as e:
        _audit_log(ctx, "remove_file", {"file_id": file_id}, "error", str(e))
        return {"ok": False, "error": str(e)}


# ---- export ----------------------------------------------------------------


async def export_project(
    ctx: LibraryContext, project_id: int, target_dir: str,
) -> dict[str, Any]:
    """Export a project to a local directory."""
    allowed, reason = _confirm(
        ctx, "export_project", f"导出项目 #{project_id} 到 {target_dir}"
    )
    if not allowed:
        _audit_log(ctx, "export_project", {"project_id": project_id}, "denied", reason)
        return {"ok": False, "error": reason}

    try:
        from pathlib import Path as _Path
        from app.exporter import ExportOptions, export_project as _export  # type: ignore[import]

        p = ctx.repo.get_project(project_id)
        if p is None:
            return {"ok": False, "error": f"项目 {project_id} 不存在"}

        target = _Path(target_dir)
        options = ExportOptions(target_root=target, copy_link_files=True)
        result = _export(ctx.repo, ctx.library, p, options)
        ctx.repo.mark_project_mcp_modified(project_id)
        _audit_log(ctx, "export_project", {"project_id": project_id, "target_dir": target_dir}, "success")
        return {
            "ok": True,
            "target_dir": str(target),
            "files_exported": len(result.files_exported),
        }
    except Exception as e:
        _audit_log(ctx, "export_project", {"project_id": project_id}, "error", str(e))
        return {"ok": False, "error": str(e)}


# ---- helper ----------------------------------------------------------------


def _guess_file_kind(suffix: str) -> str:
    """Map file extension to a file kind."""
    suffix = suffix.lower()
    doc_exts = {".pdf", ".txt", ".md", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv"}
    if suffix in doc_exts:
        return "document"
    img_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}
    if suffix in img_exts:
        return "image"
    vid_exts = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"}
    if suffix in vid_exts:
        return "video"
    aud_exts = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma"}
    if suffix in aud_exts:
        return "audio"
    arch_exts = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"}
    if suffix in arch_exts:
        return "archive"
    return "other"
