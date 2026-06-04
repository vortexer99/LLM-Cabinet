"""MCP Resource URI handlers.

Resources expose read-only, cacheable data through URI templates.
The MCP client can subscribe to and cache these URIs.

URI design:
    cabinet://library/info       → library metadata
    cabinet://library/stats      → aggregate statistics
    cabinet://tags               → tag list with counts
    cabinet://fields             → field definitions
    cabinet://projects           → project summary list
    cabinet://project/{id}       → single project full metadata
    cabinet://project/{id}/files → files in a project (paths only, no content)
    cabinet://file/{id}          → file content (disabled by default)
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from .context import LibraryContext

log = logging.getLogger(__name__)

# File content resource is disabled by default; callers must pass
# ``allow_file_read=True`` to ``read_file_content()``.
_ALLOW_FILE_READ_DEFAULT = False


# ---------------------------------------------------------------------------
# Helper: build Resource entries for server registration
# ---------------------------------------------------------------------------

def _with_content(uri: str, name: str, description: str, mime_type: str) -> dict:
    return {
        "uri": uri,
        "name": name,
        "description": description,
        "mimeType": mime_type,
    }


INVENTORY: list[dict] = [
    _with_content(
        "cabinet://library/info",
        "库元信息",
        "路径、项目数、字段数、schema 版本、应用版本",
        "application/json",
    ),
    _with_content(
        "cabinet://library/stats",
        "统计概览",
        "每个标签的项目计数、字段填充率",
        "application/json",
    ),
    _with_content(
        "cabinet://tags",
        "标签列表",
        "所有标签及其关联的项目数",
        "application/json",
    ),
    _with_content(
        "cabinet://fields",
        "字段定义",
        "所有字段的 id / name / type / key / ord / visible",
        "application/json",
    ),
    _with_content(
        "cabinet://projects",
        "项目摘要列表",
        "所有项目的 id / title / tags / updated_at",
        "application/json",
    ),
    _with_content(
        "cabinet://project/{id}",
        "项目详情",
        "单个项目的完整元数据 + 字段值 + 标签",
        "application/json",
    ),
    _with_content(
        "cabinet://project/{id}/files",
        "项目文件清单",
        "单个项目下所有文件（不含内容）",
        "application/json",
    ),
    _with_content(
        "cabinet://file/{id}",
        "文件内容",
        "单个文件内容（默认禁用，需 settings 开启）",
        "application/octet-stream",
    ),
]


# ---------------------------------------------------------------------------
# Resource readers
# ---------------------------------------------------------------------------


async def read_library_info(ctx: LibraryContext) -> dict[str, Any]:
    """``cabinet://library/info``"""
    repo = ctx.repo
    fields = repo.list_fields()
    return {
        "total_projects": repo.count_projects_total(),
        "total_fields": len(fields),
        "tag_count": len(repo.list_tag_counts()),
        "default_storage_mode": repo.get_setting("default_storage_mode", "link") or "link",
    }


async def read_library_stats(ctx: LibraryContext) -> dict[str, Any]:
    """``cabinet://library/stats``"""
    repo = ctx.repo
    return {
        "total_projects": repo.count_projects_total(),
        "untagged_projects": repo.count_projects_untagged(),
        "tag_distribution": {name: c for name, c in repo.list_tag_counts()},
        "pending_suggestions": repo.count_projects_with_pending_suggestions(),
    }


async def read_tags(ctx: LibraryContext) -> list[dict[str, Any]]:
    """``cabinet://tags``"""
    return [
        {"name": name, "count": count}
        for name, count in ctx.repo.list_tag_counts()
    ]


async def read_fields(ctx: LibraryContext) -> list[dict[str, Any]]:
    """``cabinet://fields``"""
    return [
        {
            "id": f.id,
            "name": f.name,
            "type": f.type,
            "key": f.key,
            "ord": f.ord,
            "visible": f.visible,
        }
        for f in ctx.repo.list_fields()
    ]


async def read_projects(ctx: LibraryContext) -> list[dict[str, Any]]:
    """``cabinet://projects``"""
    projects = ctx.repo.list_projects()
    return [
        {
            "id": p.id,
            "title": p.title,
            "tags": p.tags,
            "updated_at": p.updated_at,
        }
        for p in projects
    ]


async def read_project(ctx: LibraryContext, project_id: int) -> dict[str, Any]:
    """``cabinet://project/{id}``"""
    from .tools import get_project as _get_project

    return await _get_project(ctx, project_id)


async def read_project_files(
    ctx: LibraryContext, project_id: int,
) -> list[dict[str, Any]]:
    """``cabinet://project/{id}/files`` — paths only, no content."""
    files = ctx.repo.list_files(project_id)
    return [
        {
            "id": f.id,
            "path": f.path,
            "label": f.label,
            "kind": f.kind,
            "added_at": f.added_at,
        }
        for f in files
    ]


async def read_file_content(
    ctx: LibraryContext, file_id: int, *, allow: bool = False,
) -> dict[str, Any]:
    """``cabinet://file/{id}`` — file content (disabled by default).

    Returns:
        ``{"allowed": false, "reason": "文件内容读取已禁用"}`` when disallowed.
        ``{"allowed": true, "content": "<base64>", "mime_type": "..."}`` when allowed.
    """
    if not allow:
        return {"allowed": False, "reason": "文件内容读取已禁用"}

    f = ctx.repo.get_file(file_id)
    if f is None:
        raise ValueError(f"文件 {file_id} 不存在")

    full_path = ctx.library.resolve(f.path) if f.is_relative else f.path
    import pathlib

    fp = pathlib.Path(full_path)
    if not fp.exists():
        return {"allowed": True, "content": "", "mime_type": "", "note": "文件不存在（missing）"}

    # Detect MIME type heuristically
    suffix = fp.suffix.lower()
    mime_map = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".json": "application/json",
        ".html": "text/html",
        ".css": "text/css",
        ".js": "text/javascript",
        ".py": "text/x-python",
        ".zip": "application/zip",
    }
    mime = mime_map.get(suffix, "application/octet-stream")
    is_binary = not mime.startswith("text/") and mime != "application/json"

    try:
        if is_binary:
            content = base64.b64encode(fp.read_bytes()).decode("ascii")
        else:
            content = fp.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {
            "allowed": True,
            "content": "",
            "mime_type": mime,
            "error": str(exc),
        }

    return {
        "allowed": True,
        "content": content,
        "mime_type": mime,
        "encoding": "base64" if is_binary else "utf-8",
    }
