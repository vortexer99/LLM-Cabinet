"""可随 Skill 安装的轻量入口；在指定的 Cabinet 源码中运行 CLI。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def main() -> int:
    """解析安装路径，再把业务参数完整交给应用 CLI。"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    arguments = sys.argv[1:]
    root_hint = os.environ.get("LLM_CABINET_HOME", "")
    # 此选项只在脚本入口处理，并要求位于业务命令前，避免吃掉用户文本参数。
    if arguments and arguments[0] == "--app-root":
        if len(arguments) < 2 or arguments[1].startswith("--"):
            print(json.dumps({"ok": False, "error": "--app-root 后需要 Cabinet 源码目录"}, ensure_ascii=False))
            return 2
        root_hint = arguments[1]
        arguments = arguments[2:]
    elif arguments and arguments[0].startswith("--app-root="):
        root_hint = arguments.pop(0).split("=", 1)[1]
    root = Path(root_hint).expanduser().resolve() if root_hint else Path(__file__).resolve().parents[3]
    if not (root / "app" / "cli.py").is_file():
        print(json.dumps({"ok": False, "error": "找不到 Cabinet 源码。请在命令最前方传 --app-root 路径，或设置 LLM_CABINET_HOME；并使用项目 Python 环境运行。"}, ensure_ascii=False))
        return 2
    sys.path.insert(0, str(root))
    try:
        from app.cli import main as cli_main
    except ImportError as exc:
        print(json.dumps({"ok": False, "error": f"无法加载 Cabinet：{exc}。请使用项目的 Python 环境。"}, ensure_ascii=False))
        return 1
    return cli_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
