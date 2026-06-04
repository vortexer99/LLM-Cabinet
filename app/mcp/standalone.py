"""Standalone MCP server entry point (Path A: stdio transport).

Usage::

    # Multi-library mode (recommended, zero config)
    python -m app.mcp.standalone

    # Single-library mode (security isolation)
    python -m app.mcp.standalone --db /path/to/cabinet.db

    # Custom cabinet.json location
    python -m app.mcp.standalone --config /path/to/cabinet.json

    # With file content reading enabled
    python -m app.mcp.standalone --allow-file-read

Intended to be spawned by MCP clients (Claude Desktop / Cursor / Cline)
via stdio transport. No GUI dependency — only `app.db`, `app.repository`,
and `app.library` are required.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure the project root is importable (especially for ``-m`` invocation)
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def main() -> None:
    args = _parse_args()

    # Logging
    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,  # stdio transport uses stdout for MCP protocol
    )
    # Suppress verbose per-request INFO logs from the MCP SDK's lowlevel server
    logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)
    log = logging.getLogger("app.mcp.standalone")
    log.info("LLM Cabinet MCP server starting (v%s)", _get_version())

    # Build LibraryContext
    from app.mcp.context import LibraryContext
    from app.mcp.server import make_mcp_server

    if args.db:
        # Single-library mode
        db_path = Path(args.db).resolve()
        library_root = Path(args.library).resolve() if args.library else db_path.parent / "library"
        log.info("Single-library mode: db=%s library=%s", db_path, library_root)
        ctx = LibraryContext.from_single_db(db_path, library_root)
    elif args.config:
        # Multi-library mode with custom cabinet.json
        config_path = Path(args.config).resolve()
        if not config_path.exists():
            log.error("cabinet.json 不存在: %s", config_path)
            sys.exit(1)
        log.info("Multi-library mode (custom config): %s", config_path)
        ctx = LibraryContext.from_default(config_path)
    else:
        # Default: load from app_data_dir()
        log.info("Multi-library mode (default config)")
        ctx = LibraryContext.from_default()

    # Apply write permission
    ctx.write_permission = args.write_permission
    log.info("Write permission: %s", ctx.write_permission)

    # Create and run the MCP server
    server = make_mcp_server(ctx)
    log.info("MCP server ready, listening on stdio")
    server.run(transport="stdio")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.mcp.standalone",
        description="LLM Cabinet MCP Server — 通过 MCP 协议暴露项目库给外部 agent",
    )
    parser.add_argument(
        "--config",
        help="cabinet.json 路径（默认 %s）" % _default_config_path(),
        default=None,
    )
    parser.add_argument(
        "--db",
        help="单库模式：指定 cabinet.db 路径",
        default=None,
    )
    parser.add_argument(
        "--library",
        help="单库模式：library 根目录（默认与 --db 同目录下的 library/）",
        default=None,
    )
    parser.add_argument(
        "--allow-file-read",
        action="store_true",
        help="开启 cabinet://file/{id} 文件内容资源",
    )
    parser.add_argument(
        "--write-permission",
        choices=["disabled", "session", "permanent"],
        default="disabled",
        help="写操作权限（默认 disabled）。session 仅本次有效；permanent 永久允许。",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="日志等级（默认 INFO）",
    )
    return parser.parse_args()


def _default_config_path() -> str:
    from app.utils import app_data_dir
    return str(app_data_dir() / "cabinet.json")


def _get_version() -> str:
    try:
        from app import __version__
        return __version__
    except ImportError:
        return "unknown"


if __name__ == "__main__":
    main()
