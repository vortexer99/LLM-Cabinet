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

from .context import LibraryContext
from .server import make_mcp_server

__all__ = [
    "LibraryContext",
    "make_mcp_server",
]
