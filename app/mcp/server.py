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
from mcp.types import Icon, PromptMessage

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
        instructions=(
            f"LLM Cabinet v{__version__} — AI 文件中枢。"
            "通过 MCP 协议暴露项目库的检索与浏览能力。"
        ),
        website_url="https://github.com/vortexer99/LLM-Cabinet",
        icons=[
            Icon(
                src="https://raw.githubusercontent.com/vortexer99/LLM-Cabinet/main/icon.jpg",
                sizes=["128x128"],
            )
        ],
    )

    # ---- query_projects (search / get / count) --------------------------------

    @mcp.tool(
        name="query_projects",
        description=(
            "对项目库的纯查询操作。通过 action 参数选择子操作：\n"
            "  action=\"search\": 按 keyword / field_filter（支持 tag:科幻、author:刘慈欣、rating:>=4、AND/OR/NOT/括号）和/或 tag/tag_prefix 搜索项目，"
            "返回 id/title/tags/file_count/updated_at 摘要。两个参数都为空时一次性返回全部项目——无需分页或逐个 get。\n"
            "  action=\"get\": 必传 project_id，返回完整元数据（含 description/storage_mode/cover_file_id/field_values 等）。\n"
            "  action=\"count\": 统计项目总数；可传 tag 过滤。\n"
            "注意：本工具不搜索文件内容；找特定文件请先 search 定位项目，再用 manage_files action=\"list\" 看文件清单。"
        ),
    )
    async def query_projects(
        action: str,
        project_id: int = 0,
        keyword: str = "",
        tag: str = "",
        tag_prefix: str = "",
        field_filter: str = "",
    ) -> str:
        if action == "search":
            return _json_result(await tools.search_projects(ctx, keyword, tag, tag_prefix, field_filter))
        if action == "get":
            return _json_result(await tools.get_project(ctx, project_id))
        if action == "count":
            return _json_result(await tools.count_projects(ctx, tag))
        return _json_result({"ok": False, "error": f"未知 action：{action}"})

    # ---- manage_project (create / update / add_tag / remove_tag) ------------

    @mcp.tool(
        name="manage_project",
        description=(
            "对单个项目的元数据写操作。通过 action 参数选择子操作：\n"
            "  action=\"create\": 必传 title；可选 tags（逗号分隔）/ description / field_values（JSON {field_id: value}，key 为数字 ID）。返回新项目 project_id。\n"
            "  action=\"update\": 必传 project_id；title/description/tags（逗号分隔）/field_values 任填，不传则保持原值。field_values 是 JSON 对象，**key 必须是数字 field_id**（从 get_fields 获取），不可使用字段名。例：{\"3\":\"张三\",\"5\":\"2024\"}\n"
            "  action=\"add_tag\" / \"remove_tag\": 必传 project_id 和 tag。\n"
            "需要写权限（启动时 --write-permission session 或 permanent）。"
        ),
    )
    async def manage_project(
        action: str,
        project_id: int = 0,
        title: str = "",
        description: str = "",
        tags: str = "",
        tag: str = "",
        field_values: str = "",
    ) -> str:
        if action == "create":
            return _json_result(await tools.create_project(ctx, title, tags, description, field_values))
        if action == "update":
            return _json_result(await tools.update_project(ctx, project_id, title, description, tags, field_values))
        if action == "add_tag":
            return _json_result(await tools.add_tag(ctx, project_id, tag))
        if action == "remove_tag":
            return _json_result(await tools.remove_tag(ctx, project_id, tag))
        return _json_result({"ok": False, "error": f"未知 action：{action}"})

    # ---- manage_files (list / add / remove) -----------------------------------

    @mcp.tool(
        name="manage_files",
        description=(
            "对项目文件的 CRUD 操作。通过 action 参数选择子操作：\n"
            "  action=\"list\": 必传 project_id；可选 kind 过滤（document / image / video / audio / archive / other）。返回该项目下的文件清单。\n"
            "  action=\"add\": 必传 project_id 和 path；建议传 label（一句中文文件描述）；storage_mode 选 link（引用原路径）或 copy（路径记录为库内相对路径）。返回新 file_id。注：copy 模式仅改变路径记录方式，不实际搬移文件。用户未指定时，先通过 cabinet://library/info 查看 default_storage_mode 作为依据，添加后向用户说明使用了哪种方式。\n"
            "  action=\"remove\": 必传 file_id。此操作不可逆，建议执行前先与用户确认。\n"
            "add/remove 需要写权限。"
        ),
    )
    async def manage_files(
        action: str,
        project_id: int = 0,
        file_id: int = 0,
        path: str = "",
        storage_mode: str = "link",
        kind: str = "",
        label: str = "",
    ) -> str:
        if action == "list":
            return _json_result(await tools.list_files(ctx, project_id, kind))
        if action == "add":
            return _json_result(await tools.add_file(ctx, project_id, path, storage_mode, label))
        if action == "remove":
            return _json_result(await tools.remove_file(ctx, file_id))
        return _json_result({"ok": False, "error": f"未知 action：{action}"})

    # ---- manage_libraries (list / switch / get_field / get_fields) -----------

    @mcp.tool(
        name="manage_libraries",
        description=(
            "查询库级元数据 / 切换当前活动库。通过 action 参数选择子操作：\n"
            "  action=\"list\": 列出所有注册的库（来自 cabinet.json）。每个库含 name/path/label/description/is_current。\n"
            "  action=\"switch\": 必传 library_name。切换到指定库（仅多库模式生效；单库模式 --db 启动会返回 ok=false 并附错误说明）。\n"
            "  action=\"get_field\": 必传 field_name。按字段名返回字段定义（id/name/type/key/ord/visible/prompt_hint）。\n"
            "  action=\"get_fields\": 列出当前库的所有字段定义。返回数组，每项含 id（数字）/ name（字段名）/ type（类型）/ prompt_hint（填写提示）。字段值写入时 key 必须用 id 数字，不是 name 中文名。\n"
            "本工具中所有 action 都是只读 / 上下文切换，不需要写权限。"
        ),
    )
    async def manage_libraries(
        action: str,
        library_name: str = "",
        field_name: str = "",
    ) -> str:
        if action == "list":
            return _json_result(await tools.list_libraries(ctx))
        if action == "switch":
            return _json_result(await tools.switch_library(ctx, library_name))
        if action == "get_field":
            return _json_result(await tools.get_field_definition(ctx, field_name))
        if action == "get_fields":
            return _json_result(await tools.get_all_fields(ctx))
        return _json_result({"ok": False, "error": f"未知 action：{action}"})

    # ---- export_project (standalone) -----------------------------------------

    @mcp.tool(
        name="export_project",
        description=(
            "导出指定项目到本地目录。在 target_dir 下生成 project.json + 文件副本"
            "（即使是 link 模式的文件也会拷贝到导出目录，便于打包传递）。"
            "必传 project_id 和 target_dir。需要写权限（涉及外部磁盘写入）。"
        ),
    )
    async def export_project(project_id: int, target_dir: str) -> str:
        return _json_result(await tools.export_project(ctx, project_id, target_dir))

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
    and ``Annotated`` parameter descriptions so that ``Prompt.from_function``
    extracts proper argument documentation for the MCP client.
    """
    handler = entry["handler"]
    name = entry["name"]
    arg_descs = entry.get("arguments", [])

    import inspect
    from typing import Annotated

    # can't import pydantic.Field at module level without the dep, but pydantic
    # is already a FastMCP dependency so it's safe to import here.
    from pydantic import Field

    sig = inspect.signature(handler)
    param_names = [p for p in sig.parameters.keys() if p != "return"]

    # Build annotations dict with Field(description=...) for each param
    annotations: dict[str, Any] = {}
    defaults: dict[str, Any] = {}
    for pname in param_names:
        desc = ""
        for a in arg_descs:
            if a.get("name") == pname:
                desc = a.get("description", "")
                required = a.get("required", False)
                break

        param = sig.parameters[pname]
        if desc:
            if param.default is not inspect.Parameter.empty:
                defaults[pname] = param.default
                annotations[pname] = Annotated[str, Field(default=param.default, description=desc)]
            else:
                annotations[pname] = Annotated[str, Field(description=desc)]
        elif param.default is not inspect.Parameter.empty:
            defaults[pname] = param.default

    # Build a wrapper function with the right annotations
    pass_str = ", ".join(f"{p}={p}" for p in param_names)
    params_str = ", ".join(
        f"{p}={repr(defaults[p])}" if p in defaults else p
        for p in param_names
    )
    code = f"def {name}({params_str}):\n    return _handler({pass_str})\n"

    ns: dict[str, Any] = {"_handler": handler}
    exec(code, ns)
    fn = ns[name]

    if annotations:
        fn.__annotations__ = annotations

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
