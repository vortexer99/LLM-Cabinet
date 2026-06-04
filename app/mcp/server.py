"""MCP Server factory.

``make_mcp_server(ctx)`` creates a FastMCP server instance
with all read-only Tools and Resources registered against the given
``LibraryContext``.

Usage::

    ctx = LibraryContext.from_default()
    server = make_mcp_server(ctx)
    server.run(transport="stdio")   # T2: standalone entry point
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import PromptMessage

from ..__init__ import __version__
from . import prompts as pr
from . import resources as res
from . import tools
from .context import LibraryContext

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCP Server factory
# ---------------------------------------------------------------------------


def make_mcp_server(ctx: LibraryContext) -> FastMCP:
    """Create a FastMCP server with read-only Tools and Resources.

    Args:
        ctx: The ``LibraryContext`` providing repo/library access.

    Returns:
        A configured ``FastMCP`` server ready for transport binding.
    """
    mcp = FastMCP(
        "llm-cabinet",
        instructions=f"LLM Cabinet v{__version__} — AI 文件中枢。通过 MCP 协议暴露项目库的检索与浏览能力。",
    )

    # ---- Read-only Tools ----------------------------------------------------

    @mcp.tool(
        name="search_projects",
        description=(
            "按关键词或标签搜索项目。返回 id / title / tags / file_count / updated_at。"
            "file_count 不为 0 时可调 list_files 查看项目文件。"
            "注意：此工具不会搜索文件内容；要找特定文件请先定位项目再调 list_files。"
        ),
    )
    async def search_projects(
        keyword: str = "", tag: str = "", field_filter: str = "",
    ) -> str:
        return _json_result(await tools.search_projects(ctx, keyword, tag, field_filter))

    @mcp.tool(
        name="get_project",
        description="获取单个项目的完整元数据，包含 tags、field_values 等。",
    )
    async def get_project(project_id: int) -> str:
        return _json_result(await tools.get_project(ctx, project_id))

    @mcp.tool(
        name="list_files",
        description=(
            "列出指定项目下的所有文件。可按 kind 过滤（document / image / video / audio / archive / other）。"
            "不做关键词搜索——文件名通常是无意义串，内容向搜索请先用 search_projects 定位项目。"
        ),
    )
    async def list_files(project_id: int, kind: str = "") -> str:
        return _json_result(await tools.list_files(ctx, project_id, kind))

    @mcp.tool(
        name="list_pending_suggestions",
        description="列出待审阅的 LLM 建议。不传 project_id 时返回全部项目的待处理建议。",
    )
    async def list_pending_suggestions(project_id: int = 0) -> str:
        pid = project_id if project_id > 0 else None
        return _json_result(await tools.list_pending_suggestions(ctx, pid))

    @mcp.tool(
        name="count_projects",
        description="统计项目总数（可按标签过滤）。",
    )
    async def count_projects(tag: str = "") -> str:
        return _json_result(await tools.count_projects(ctx, tag))

    @mcp.tool(
        name="get_field_definition",
        description="按字段名获取字段定义（id / name / type / key / ord / visible / prompt_hint）。",
    )
    async def get_field_definition(field_name: str) -> str:
        return _json_result(await tools.get_field_definition(ctx, field_name))

    @mcp.tool(
        name="list_libraries",
        description="列出所有注册的库（来自 cabinet.json）。每个库包含 name / path / label / is_current。",
    )
    async def list_libraries() -> str:
        return _json_result(await tools.list_libraries(ctx))

    @mcp.tool(
        name="switch_library",
        description="切换到指定名称的库。当前为只读模式，切换功能暂不可用（no-op）。",
    )
    async def switch_library(library_name: str) -> str:
        return _json_result(await tools.switch_library(ctx, library_name))

    # ---- Write tools (T3) -----------------------------------------------------

    @mcp.tool(
        name="create_project",
        description="创建新项目。title 必填，tags 用逗号分隔（可选），description 可选。",
    )
    async def create_project(title: str, tags: str = "", description: str = "") -> str:
        return _json_result(await tools.create_project(ctx, title, tags, description))

    @mcp.tool(
        name="update_project",
        description=(
            "修改项目元数据。可修改 title / description / tags（逗号分隔）/ field_values（JSON 对象）。"
            "不传的字段保持原值不变。"
        ),
    )
    async def update_project(
        project_id: int, title: str = "", description: str = "",
        tags: str = "", field_values: str = "",
    ) -> str:
        return _json_result(await tools.update_project(ctx, project_id, title, description, tags, field_values))

    @mcp.tool(
        name="add_tag",
        description="给项目添加一个标签。",
    )
    async def add_tag(project_id: int, tag: str) -> str:
        return _json_result(await tools.add_tag(ctx, project_id, tag))

    @mcp.tool(
        name="remove_tag",
        description="移除项目的一个标签。",
    )
    async def remove_tag(project_id: int, tag: str) -> str:
        return _json_result(await tools.remove_tag(ctx, project_id, tag))

    @mcp.tool(
        name="add_file",
        description="给项目添加文件。path 为文件路径，storage_mode 为 link（链接）或 copy（复制到库内）。",
    )
    async def add_file(
        project_id: int, path: str, storage_mode: str = "link", label: str = "",
    ) -> str:
        return _json_result(await tools.add_file(ctx, project_id, path, storage_mode, label))

    @mcp.tool(
        name="remove_file",
        description="删除项目的文件。此操作不可逆，请谨慎使用。建议在执行前先向用户确认。",
    )
    async def remove_file(file_id: int) -> str:
        return _json_result(await tools.remove_file(ctx, file_id))

    @mcp.tool(
        name="apply_suggestion",
        description="应用一条待处理的 LLM 建议。会更新项目字段值。",
    )
    async def apply_suggestion(suggestion_id: int) -> str:
        return _json_result(await tools.apply_suggestion(ctx, suggestion_id))

    @mcp.tool(
        name="trigger_llm_suggestion",
        description="为指定项目触发 LLM 建议任务。会消耗 API token。target_fields 为逗号分隔的字段名。",
    )
    async def trigger_llm_suggestion(project_id: int, target_fields: str = "") -> str:
        return _json_result(await tools.trigger_llm_suggestion(ctx, project_id, target_fields))

    @mcp.tool(
        name="export_project",
        description="导出项目到本地目录。会创建 project.json + 文件副本。",
    )
    async def export_project(project_id: int, target_dir: str) -> str:
        return _json_result(await tools.export_project(ctx, project_id, target_dir))

    @mcp.tool(
        name="import_folder",
        description="导入本地目录为一个新项目。storage_mode 可选 link（链接）或 copy（复制）。",
    )
    async def import_folder(folder_path: str, storage_mode: str = "link") -> str:
        return _json_result(await tools.import_folder(ctx, folder_path, storage_mode))

    # ---- Resources -----------------------------------------------------------

    @mcp.resource("cabinet://library/info", name="库元信息", description="路径、项目数、字段数等", mime_type="application/json")
    async def resource_library_info() -> str:
        return _json_result(await res.read_library_info(ctx))

    @mcp.resource("cabinet://library/stats", name="统计概览", description="标签分布、字段填充率等", mime_type="application/json")
    async def resource_library_stats() -> str:
        return _json_result(await res.read_library_stats(ctx))

    @mcp.resource("cabinet://tags", name="标签列表", description="所有标签及项目计数", mime_type="application/json")
    async def resource_tags() -> str:
        return _json_result(await res.read_tags(ctx))

    @mcp.resource("cabinet://fields", name="字段定义", description="所有字段的定义信息", mime_type="application/json")
    async def resource_fields() -> str:
        return _json_result(await res.read_fields(ctx))

    @mcp.resource("cabinet://projects", name="项目摘要列表", description="所有项目的 id / title / tags / updated_at", mime_type="application/json")
    async def resource_projects() -> str:
        return _json_result(await res.read_projects(ctx))

    @mcp.resource("cabinet://project/{project_id}", name="项目详情", description="单项目完整元数据", mime_type="application/json")
    async def resource_project(project_id: int) -> str:
        return _json_result(await res.read_project(ctx, project_id))

    @mcp.resource("cabinet://project/{project_id}/files", name="项目文件清单", description="单项目下所有文件（不含内容）", mime_type="application/json")
    async def resource_project_files(project_id: int) -> str:
        return _json_result(await res.read_project_files(ctx, project_id))

    @mcp.resource("cabinet://file/{file_id}", name="文件内容", description="单个文件内容（默认禁用）", mime_type="application/octet-stream")
    async def resource_file_content(file_id: int) -> str:
        # Default: file content reading is disabled
        return _json_result(await res.read_file_content(ctx, file_id, allow=False))

    # ---- Prompts ------------------------------------------------------------

    for entry in pr.PROMPT_REGISTRY:
        _register_prompt(mcp, entry)

    return mcp


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _register_prompt(mcp: FastMCP, entry: dict) -> None:
    """Register a single Prompt from the registry entry.

    We build the handler function dynamically with the right function name
    so that the FastMCP decorator introspects it correctly.
    """
    handler = entry["handler"]
    name = entry["name"]

    # Build a function with the exact signature the handler expects.
    # Most handlers take no args; some take `project_id` or `directory`.
    import inspect

    sig = inspect.signature(handler)
    param_names = list(sig.parameters.keys())

    # Create a closure that matches the handler's signature
    ns: dict[str, Any] = {"_handler": handler}
    args_str = ", ".join(p for p in param_names if p != "return")

    if args_str:
        code = f"def {name}({args_str}):\n    return _handler({args_str})"
    else:
        code = f"def {name}():\n    return _handler()"

    exec(code, ns)
    fn = ns[name]

    mcp.prompt(
        name=name,
        title=entry.get("title"),
        description=entry["description"],
    )(fn)


def _json_result(obj: Any) -> str:
    """Serialize tool/resource output to compact JSON string."""
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, ensure_ascii=False, default=str)
