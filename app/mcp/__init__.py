"""MCP Server module.

Exposes Cabinet business logic to external MCP-compatible clients
(Claude Desktop / Cursor / Cline / custom agents) via the
Model Context Protocol (https://modelcontextprotocol.io/).

Architecture overlay:

    app/mcp/
    ├── __init__.py
    ├── context.py     LibraryContext — manages active repo/library, supports switch
    ├── server.py      make_mcp_server(ctx) → mcp.server.Server
    ├── tools.py       Tool handler implementations
    └── resources.py   Resource URI handlers
"""

from typing import TYPE_CHECKING

from .context import LibraryContext

if TYPE_CHECKING:
    from .server import make_mcp_server


def __getattr__(name: str):
    """按需导入 MCP server，避免工具层测试依赖 FastMCP 运行时。"""
    if name == "make_mcp_server":
        from .server import make_mcp_server

        return make_mcp_server
    raise AttributeError(name)

__all__ = [
    "LibraryContext",
    "make_mcp_server",
]
