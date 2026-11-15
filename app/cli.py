"""供 Skill 和本地脚本使用的 JSON 命令行入口，共用 MCP 的业务处理函数。"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .cabinet import CabinetConfig
from .db import SCHEMA_VERSION
from .mcp import resources, tools
from .mcp.context import LibraryContext

log = logging.getLogger(__name__)


class ArgumentError(ValueError):
    """用于输出结构化命令行参数错误。"""


class JsonArgumentParser(argparse.ArgumentParser):
    """保留文本帮助，将解析错误交给主入口统一输出 JSON。"""

    def error(self, message: str) -> None:
        raise ArgumentError(message)


def _positive_id(value: str) -> int:
    """项目、文件 ID 必须是正整数。"""
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ID 必须是正整数") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("ID 必须是正整数")
    return number


def build_parser() -> argparse.ArgumentParser:
    """定义命令及参数；全局选项放在命令前。"""
    parser = JsonArgumentParser(description="LLM Cabinet：供 Agent 使用的 JSON 命令行接口")
    parser.add_argument("--config", type=Path, help="cabinet.json 路径")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--library", help="已注册的库名称，每次调用显式指定")
    target.add_argument("--db", type=Path, help="已有 cabinet.db 路径")
    parser.add_argument("--library-root", type=Path, help="文件仓储目录，仅用于 --db")
    parser.add_argument("--write", action="store_true", help="允许本次调用执行写操作")
    parser.add_argument("--allow-file-read", action="store_true", help="允许读取文件内容")
    parser.add_argument("--client-name", default="skill-cli", help="审计记录的调用方名称")
    groups = parser.add_subparsers(dest="group", required=True)

    libraries = groups.add_parser("libraries", help="发现项目库").add_subparsers(dest="action", required=True)
    libraries.add_parser("list", help="列出注册库")
    library = groups.add_parser("library", help="查询库信息").add_subparsers(dest="action", required=True)
    for action, help_text in (("info", "库信息"), ("stats", "统计"), ("tags", "标签"), ("fields", "字段定义")):
        library.add_parser(action, help=help_text)
    library.add_parser("field", help="按名称查询字段").add_argument("--name", required=True)

    projects = groups.add_parser("projects", help="查询和管理项目").add_subparsers(dest="action", required=True)
    search = projects.add_parser("search", help="搜索项目")
    for option in ("keyword", "tag", "tag-prefix", "field-filter"):
        search.add_argument(f"--{option}", default="")
    projects.add_parser("count", help="统计项目数").add_argument("--tag", default="")
    for action in ("get", "create", "update", "add-tag", "remove-tag", "export"):
        command = projects.add_parser(action)
        if action != "create":
            command.add_argument("--id", type=_positive_id, required=True)
        if action in ("create", "update"):
            command.add_argument("--title", required=action == "create", default="")
            command.add_argument("--description", default="")
            command.add_argument("--tags", default="", help="逗号分隔的标签")
            fields = command.add_mutually_exclusive_group()
            fields.add_argument("--field-values", default="", help='JSON 对象，例如 {"3":"张三"}')
            fields.add_argument("--field-values-file", type=Path, help="UTF-8 JSON 文件，适合 Windows 和复杂文本")
        if action in ("add-tag", "remove-tag"):
            command.add_argument("--tag", required=True)
        if action == "export":
            command.add_argument("--target-dir", required=True, help="已有导出目录")

    files = groups.add_parser("files", help="管理文件记录和读取内容").add_subparsers(dest="action", required=True)
    listing = files.add_parser("list")
    listing.add_argument("--project-id", type=_positive_id, required=True)
    listing.add_argument("--kind", default="")
    add = files.add_parser("add", help="登记文件，不移动或复制实体")
    add.add_argument("--project-id", type=_positive_id, required=True)
    add.add_argument("--path", required=True)
    add.add_argument("--storage-mode", choices=("link", "copy"), default="link")
    add.add_argument("--label", default="")
    for action in ("remove", "read"):
        files.add_parser(action).add_argument("--id", type=_positive_id, required=True)
    return parser


def _load_config(path: Path | None) -> CabinetConfig:
    """加载前校验配置，避免 CLI 读取损坏配置时触发 GUI 的备份重建逻辑。"""
    actual = path.resolve() if path else CabinetConfig.config_path()
    if not actual.is_file():
        if path:
            raise ValueError(f"配置文件不存在：{actual}")
        return CabinetConfig(None, [])
    data = json.loads(actual.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("recent_libraries", []), list):
        raise ValueError("cabinet.json 必须是含 recent_libraries 列表的对象")
    return CabinetConfig.load(actual)


def _validate_database(path: Path) -> None:
    """只读探测目标，避免把误选的空文件或其它 SQLite 数据库初始化成项目库。"""
    if not path.is_file():
        raise ValueError(f"数据库不存在：{path}")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"projects", "files", "fields", "settings"}.issubset(tables):
            raise ValueError(f"不是有效的 Cabinet 项目库：{path}，请先用 GUI 创建或打开库")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError(f"数据库 schema v{version} 高于当前支持的 v{SCHEMA_VERSION}，请更新应用")
    finally:
        conn.close()


def _make_context(args: argparse.Namespace) -> LibraryContext:
    """绑定明确的目标，拒绝误建库和重名库。"""
    if args.library_root and not args.db:
        raise ArgumentError("--library-root 必须与 --db 一起使用")
    if args.config and args.db:
        raise ArgumentError("--config 与 --db 不能同时使用")
    if args.group != "libraries" and not (args.db or args.library):
        raise ArgumentError("请用 --library 库名或 --db 路径明确指定目标库")
    if args.db:
        _validate_database(args.db)
        return LibraryContext.from_single_db(args.db, args.library_root)
    config = _load_config(args.config)
    if args.library:
        matches = [item for item in config.recent_libraries if item.display_name == args.library]
        if not matches:
            raise ValueError(f"未找到库：{args.library}，请先运行 libraries list")
        if len(matches) > 1:
            raise ValueError(f"库名称重复：{args.library}，请改用 --db 指定路径")
        db_path = matches[0].path.resolve() / "cabinet.db"
        _validate_database(db_path)
        # 只调整本次上下文，既不保存配置，也不改变 GUI 的当前库。
        ctx = LibraryContext(config)
        result = ctx.switch(args.library)
        if not result["ok"]:
            ctx.close()
            raise ValueError(result["error"])
        return ctx
    return LibraryContext(config)


def _field_values(args: argparse.Namespace, ctx: LibraryContext) -> str:
    """写入前验证全部字段 ID，避免底层宽松解析造成部分成功。"""
    raw = args.field_values_file.read_text(encoding="utf-8-sig") if args.field_values_file else args.field_values
    if not raw:
        return ""
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArgumentError(f"field-values JSON 无效：{exc}") from exc
    if not isinstance(values, dict):
        raise ArgumentError("field-values 必须是 JSON 对象，键为数字 field.id")
    definitions = {field.id: field for field in ctx.repo.list_fields()}
    for key, value in values.items():
        if not key.isdecimal() or int(key) not in definitions:
            raise ArgumentError(f"无效字段 ID：{key}，请先运行 library fields")
        definition = definitions[int(key)]
        if definition.is_required:
            raise ArgumentError(f"字段 {key} 是内置字段，请使用 --{definition.key} 参数")
        if not isinstance(value, str):
            raise ArgumentError(f"字段 {key} 的值必须是字符串")
    return json.dumps(values, ensure_ascii=False)


async def dispatch(args: argparse.Namespace, ctx: LibraryContext) -> Any:
    """将 CLI 命令映射到既有业务函数，不启动 MCP 服务。"""
    action = args.action
    if args.group == "libraries":
        return await tools.list_libraries(ctx)
    if args.group == "library":
        readers = {"info": resources.read_library_info, "stats": resources.read_library_stats,
                   "tags": resources.read_tags, "fields": tools.get_all_fields}
        if action == "field":
            return await tools.get_field_definition(ctx, args.name)
        return await readers[action](ctx)
    if args.group == "projects":
        if action == "search":
            return await tools.search_projects(ctx, args.keyword, args.tag, args.tag_prefix, args.field_filter)
        if action == "get":
            return await tools.get_project(ctx, args.id)
        if action == "count":
            return await tools.count_projects(ctx, args.tag)
        if action in ("create", "update"):
            if action == "create" and not args.title.strip():
                raise ArgumentError("项目标题不能为空")
            fields = _field_values(args, ctx)
            if action == "create":
                return await tools.create_project(ctx, args.title, args.tags, args.description, fields)
            return await tools.update_project(ctx, args.id, args.title, args.description, args.tags, fields)
        if action in ("add-tag", "remove-tag"):
            if not args.tag.strip():
                raise ArgumentError("标签不能为空")
            handler = tools.add_tag if action == "add-tag" else tools.remove_tag
            return await handler(ctx, args.id, args.tag)
        if action == "export":
            return await tools.export_project(ctx, args.id, args.target_dir)
    if args.group == "files":
        if action == "list":
            await tools.get_project(ctx, args.project_id)
            return await tools.list_files(ctx, args.project_id, args.kind)
        if action == "remove":
            return await tools.remove_file(ctx, args.id)
        if action == "read":
            if not args.allow_file_read:
                raise ValueError("文件内容读取已禁用，请使用 --allow-file-read")
            result = await resources.read_file_content(ctx, args.id, allow=True)
            if result.get("error") or result.get("note"):
                raise ValueError(result.get("error") or result["note"])
            return result
        if action == "add":
            path = Path(args.path).expanduser()
            if args.storage_mode == "copy":
                root = ctx.library.root.resolve()
                full_path = (root / path).resolve()
                if not full_path.is_relative_to(root):
                    raise ValueError("copy 模式只能登记仓储目录内已有文件，不会复制实体；外部文件请用 link")
                stored_path = full_path.relative_to(root).as_posix()
            else:
                full_path = path.resolve()
                stored_path = str(full_path)
            if not full_path.is_file():
                raise ValueError(f"文件不存在或不是普通文件：{full_path}")
            return await tools.add_file(ctx, args.project_id, stored_path, args.storage_mode, args.label)
    raise ArgumentError("未知命令")


def main(argv: list[str] | None = None) -> int:
    """输出 UTF-8 JSON；成功 0、操作失败 1、参数错误 2。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    ctx: LibraryContext | None = None
    exit_code = 0
    try:
        args = build_parser().parse_args(argv)
        ctx = _make_context(args)
        ctx.client_name = args.client_name
        ctx.write_permission = "session" if args.write else "disabled"
        result = asyncio.run(dispatch(args, ctx))
        if isinstance(result, dict) and result.get("ok") is False:
            output = {"ok": False, "error": result.get("error", "操作失败")}
            exit_code = 1
        else:
            output = {"ok": True, "data": result}
    except ArgumentError as exc:
        output = {"ok": False, "error": str(exc)}
        exit_code = 2
    except Exception as exc:
        log.error("CLI 调用失败：%s", exc)
        output = {"ok": False, "error": str(exc)}
        exit_code = 1
    finally:
        if ctx is not None:
            ctx.close()
    print(json.dumps(output, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
