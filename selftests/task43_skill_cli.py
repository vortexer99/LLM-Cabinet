"""task #43：在临时双库中通过真实子进程验证 CLI 与可移植 skill 包。"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from selftests._common import T, closing_repos
from app.db import connect
from app.models import FileItem, Project
from app.repository import Repository


class CLI:
    """每次启动独立进程，并统一检查退出码和 UTF-8 JSON 协议。"""

    def __init__(self, t: T, config: Path) -> None:
        self.t = t
        self.config = config
        self.env = os.environ.copy()
        self.env["PYTHONIOENCODING"] = "utf-8"
        self.env["LLM_CABINET_DISABLE_KEYRING"] = "1"
        self.env.pop("LLM_CABINET_HOME", None)

    def run(
        self, label: str, args: list[str], *, code: int = 0,
        prefix: list[str] | None = None, cwd: Path = ROOT,
        env: dict[str, str] | None = None,
        error_contains: str | None = None,
    ) -> Any:
        result = subprocess.run(
            [sys.executable, *(prefix or ["-m", "app.cli"]), *args],
            cwd=cwd, env=env or self.env, capture_output=True, timeout=40,
        )
        stdout = result.stdout.decode("utf-8", errors="strict")
        stderr = result.stderr.decode("utf-8", errors="replace")
        self.t.assert_eq(f"{label}：退出码（{stderr.strip()}）", result.returncode, code)
        try:
            payload = json.loads(stdout)
        except (ValueError, TypeError):
            payload = {}
        self.t.assert_true(f"{label}：stdout 为单一 JSON 对象", isinstance(payload, dict) and "ok" in payload, stdout)
        self.t.assert_eq(f"{label}：ok", payload.get("ok"), code == 0)
        if code:
            self.t.assert_true(f"{label}：包含错误说明", bool(payload.get("error")), stdout)
            if error_contains:
                self.t.assert_in(f"{label}：提示专用参数", error_contains, payload.get("error", ""))
        else:
            self.t.assert_in(f"{label}：包含 data", "data", payload)
        return payload.get("data")

    def db(self, root: Path, command: list[str], *, write: bool = False, extra: list[str] | None = None) -> list[str]:
        return ["--db", str(root / "cabinet.db"),
                *(["--write"] if write else []), *(extra or []), *command]


def main(t: T) -> None:
    """所有副作用均限于临时目录，种子连接在 Windows 清理前关闭。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        tmp = Path(temp)
        first, second = tmp / "第一库", tmp / "第二库"
        repo = Repository(connect(first / "cabinet.db"))
        other = Repository(connect(second / "cabinet.db"))
        with closing_repos(repo, other):
            store = first / "library"
            store.mkdir()
            (second / "library").mkdir()
            field = repo.add_field("试验字段")
            seed = repo.save_project(Project(title="中文种子", tags=["领域/测试"], field_values={field: "初始值"}))
            other.save_project(Project(title="另一个库"))
            for state in ("running", "queued"):
                repo.conn.execute("INSERT INTO llm_tasks(project_id, type, status) VALUES (?, 'meta_suggest', ?)", (seed, state))
            repo.conn.commit()
            config = tmp / "cabinet.json"
            config.write_text(json.dumps({
                "active_library": str(second),
                "recent_libraries": [{"path": str(first), "label": "甲库"}, {"path": str(second), "label": "乙库"}],
            }, ensure_ascii=False), encoding="utf-8")
            cli = CLI(t, config)
            config_before = config.read_bytes()

            libraries = cli.run("发现双库", ["--config", str(config), "libraries", "list"])
            t.assert_eq("库名为 UTF-8 中文", sorted(x["name"] for x in (libraries or [])), ["乙库", "甲库"])
            for name, title in (("甲库", "中文种子"), ("乙库", "另一个库")):
                found = cli.run(f"按名称读{name}", ["--config", str(config), "--library", name, "projects", "search"])
                t.assert_eq(f"{name}隔离", [p["title"] for p in (found or [])], [title])
            for command in ("info", "stats", "tags", "fields"):
                data = cli.run(f"library {command}", cli.db(first, ["library", command]))
                t.assert_true(f"library {command}非空", bool(data))
            definition = cli.run("按显示名取字段", cli.db(first, ["library", "field", "--name", "试验字段"])) or {}
            t.assert_eq("字段 ID", definition.get("id"), field)
            for label, options in (("关键词", ["--keyword", "中文种子"]), ("精确标签", ["--tag", "领域/测试"]),
                                   ("标签前缀", ["--tag-prefix", "领域"]), ("字段过滤", ["--field-filter", "试验字段:初始值"])):
                found = cli.run(label, cli.db(first, ["projects", "search", *options]))
                t.assert_eq(f"{label}结果", [p["id"] for p in (found or [])], [seed])
            count = cli.run("标签计数", cli.db(first, ["projects", "count", "--tag", "领域/测试"])) or {}
            t.assert_eq("标签项目数", count.get("total"), 1)
            cli.run("只读拒绝创建", cli.db(first, ["projects", "create", "--title", "禁止创建"]), code=1)
            t.assert_eq("只读项目总数不变", repo.count_projects_total(), 1)

            create = cli.run("创建项目", cli.db(first, ["projects", "create", "--title", "CLI 新项目", "--description", "描述正文",
                "--tags", "领域/CLI,待处理", "--field-values", json.dumps({str(field): "创建值"}, ensure_ascii=False)], write=True)) or {}
            pid = create.get("project_id", seed)
            details = cli.run("读取项目", cli.db(first, ["projects", "get", "--id", str(pid)])) or {}
            t.assert_eq("创建标题", details.get("title"), "CLI 新项目")
            t.assert_eq("创建描述", details.get("description_md"), "描述正文")
            t.assert_eq("创建字段", details.get("field_values", {}).get(str(field)), "创建值")
            values_file = tmp / "字段.json"
            values_file.write_text(json.dumps({str(field): "文件提供的字段值"}, ensure_ascii=False), encoding="utf-8")
            cli.run("更新项目", cli.db(first, ["projects", "update", "--id", str(pid), "--title", "更新标题", "--description", "更新描述",
                "--tags", "领域/CLI", "--field-values-file", str(values_file)], write=True, extra=["--client-name", "端到端审计"]))
            updated = repo.get_project(pid)
            t.assert_eq("更新写入标题", updated.title, "更新标题")
            t.assert_eq("更新写入描述", updated.description_md, "更新描述")
            t.assert_eq("文件字段写入", updated.field_values.get(field), "文件提供的字段值")
            cli.run("添加标签", cli.db(first, ["projects", "add-tag", "--id", str(pid), "--tag", "临时标签"], write=True))
            t.assert_in("标签已添加", "临时标签", repo.get_project(pid).tags)
            cli.run("删除标签", cli.db(first, ["projects", "remove-tag", "--id", str(pid), "--tag", "临时标签"], write=True))
            t.assert_true("标签已移除", "临时标签" not in repo.get_project(pid).tags)

            count_before = repo.count_projects_total()
            invalid_values = ["{", "[]", '{"试验字段":"错误键"}', '{"999999999":"不存在"}', '{"0":"零"}', '{"1.5":"小数"}',
                              json.dumps({str(field): 10}), json.dumps({str(field): None})]
            for n, value in enumerate(invalid_values):
                cli.run(f"非法字段输入{n}", cli.db(first, ["projects", "create", "--title", "不得半写", "--field-values", value], write=True), code=2)
                t.assert_eq(f"非法字段{n}没有创建项目", repo.count_projects_total(), count_before)
            cli.run("更新非法字段不半写", cli.db(first, ["projects", "update", "--id", str(pid), "--title", "不得保存",
                "--field-values", json.dumps({str(field): "不得部分保存", "999999999": "无效"})], write=True), code=2)
            t.assert_eq("无效更新保留标题", repo.get_project(pid).title, "更新标题")
            t.assert_eq("无效更新保留字段", repo.get_project(pid).field_values.get(field), "文件提供的字段值")
            for protected in (f for f in repo.list_fields() if f.key in ("title", "description", "tags")):
                values = json.dumps({str(field): "不得部分写入", str(protected.id): "不得写入保护字段"}, ensure_ascii=False)
                snapshot = repo.get_project(pid)
                cli.run(f"拒绝创建时写保护字段{protected.key}", cli.db(first, ["projects", "create", "--title", "不得保存保护字段",
                    "--field-values", values], write=True), code=2, error_contains=f"--{protected.key}")
                t.assert_eq(f"保护字段{protected.key}创建无半写", repo.count_projects_total(), count_before)
                cli.run(f"拒绝更新时写保护字段{protected.key}", cli.db(first, ["projects", "update", "--id", str(pid), "--title", "不得保存保护字段",
                    "--field-values", values], write=True), code=2, error_contains=f"--{protected.key}")
                t.assert_eq(f"保护字段{protected.key}更新无半写", repo.get_project(pid), snapshot)
                t.assert_eq(f"保护字段{protected.key}无错误值记录", repo.conn.execute(
                    "SELECT COUNT(*) FROM project_field_values WHERE field_id=?", (protected.id,)).fetchone()[0], 0)

            text_file = tmp / "外链中文.txt"
            text_body = "这是 UTF-8 文本\n第二行"
            text_file.write_text(text_body, encoding="utf-8")
            binary_file = store / "二进制.bin"
            binary_body = bytes(range(256))
            binary_file.write_bytes(binary_body)
            text_result = cli.run("添加外链", cli.db(first, ["files", "add", "--project-id", str(pid), "--path", str(text_file), "--label", "外链标签"], write=True)) or {}
            text_id = text_result.get("file_id", -1)
            item = repo.get_file(text_id)
            t.assert_true("link 记录绝对路径", item is not None and Path(item.path).is_absolute() and not item.is_relative)
            t.assert_eq("link 记录目标路径", Path(item.path) if item else None, text_file)
            t.assert_eq("link 标签", item.label if item else None, "外链标签")
            copy_result = cli.run("登记库内相对文件", cli.db(first, ["files", "add", "--project-id", str(pid), "--path", binary_file.name,
                "--storage-mode", "copy"], write=True)) or {}
            binary_id = copy_result.get("file_id", -1)
            item = repo.get_file(binary_id)
            t.assert_true("copy 记录相对路径", item is not None and item.is_relative and item.path == binary_file.name)
            absolute_copy = store / "绝对库内.txt"
            absolute_copy.write_text("库内绝对路径", encoding="utf-8")
            cli.run("库内绝对路径可登记", cli.db(first, ["files", "add", "--project-id", str(pid), "--path", str(absolute_copy), "--storage-mode", "copy"], write=True))
            before_files = len(repo.list_files(pid))
            for label, path in (("copy拒绝库外绝对路径", str(text_file)), ("copy拒绝越界相对路径", "../cabinet.db"), ("copy拒绝不存在路径", "missing.txt")):
                cli.run(label, cli.db(first, ["files", "add", "--project-id", str(pid), "--path", path, "--storage-mode", "copy"], write=True), code=1)
            t.assert_eq("失败添加未留下记录", len(repo.list_files(pid)), before_files)
            files = cli.run("文件清单", cli.db(first, ["files", "list", "--project-id", str(pid)])) or []
            t.assert_eq("文件清单数量", len(files), 3)
            documents = cli.run("文件类型筛选", cli.db(first, ["files", "list", "--project-id", str(pid), "--kind", "document"])) or []
            t.assert_eq("文档数量", len(documents), 2)
            cli.run("默认禁止正文", cli.db(first, ["files", "read", "--id", str(text_id)]), code=1)
            content = cli.run("授权读取文本", cli.db(first, ["files", "read", "--id", str(text_id)], extra=["--allow-file-read"])) or {}
            t.assert_eq("UTF-8 正文", content.get("content"), text_body)
            t.assert_eq("文本编码", content.get("encoding"), "utf-8")
            content = cli.run("授权读取二进制", cli.db(first, ["files", "read", "--id", str(binary_id)], extra=["--allow-file-read"])) or {}
            t.assert_eq("二进制编码", content.get("encoding"), "base64")
            t.assert_eq("二进制内容", content.get("content"), base64.b64encode(binary_body).decode("ascii"))
            cli.run("正文授权不持久化", cli.db(first, ["files", "read", "--id", str(text_id)]), code=1)

            export_dir = tmp / "导出"
            export_dir.mkdir()
            cli.run("只读拒绝导出", cli.db(first, ["projects", "export", "--id", str(pid), "--target-dir", str(export_dir)]), code=1)
            cli.run("写权限导出", cli.db(first, ["projects", "export", "--id", str(pid), "--target-dir", str(export_dir)], write=True))
            t.assert_true("导出产生实际文件", any(p.is_file() for p in export_dir.rglob("*")))
            for label, file_id, entity in (("外链", text_id, text_file), ("copy", binary_id, binary_file)):
                cli.run(f"移除{label}记录", cli.db(first, ["files", "remove", "--id", str(file_id)], write=True))
                t.assert_eq(f"{label}记录已删除", repo.get_file(file_id), None)
                t.assert_true(f"{label}实体保留", entity.exists())

            audits = repo.conn.execute("SELECT client_name, tool_name, result_status FROM mcp_audit").fetchall()
            t.assert_true("默认审计 client_name", any(row["client_name"] == "skill-cli" and row["result_status"] == "success" for row in audits))
            t.assert_true("自定义审计 client_name", any(row["client_name"] == "端到端审计" and row["tool_name"] == "update_project" for row in audits))
            t.assert_true("MCP 修改标记", bool(repo.get_project(pid).mcp_modified_at))
            t.assert_eq("另一库项目数未变", other.count_projects_total(), 1)
            t.assert_eq("另一库无审计", other.conn.execute("SELECT COUNT(*) FROM mcp_audit").fetchone()[0], 0)
            t.assert_eq("GUI 后台任务状态未被重置", [row[0] for row in repo.conn.execute("SELECT status FROM llm_tasks ORDER BY id")], ["running", "queued"])
            t.assert_eq("库选择未持久化全局配置", config.read_bytes(), config_before)

            missing_db = tmp / "不得创建" / "cabinet.db"
            cli.run("错误库路径", ["--db", str(missing_db), "projects", "count"], code=1)
            t.assert_true("错误路径不创建数据库", not missing_db.exists())
            empty_db = tmp / "空文件.db"
            empty_db.touch()
            unrelated_db = tmp / "其他应用.db"
            unrelated = sqlite3.connect(unrelated_db)
            try:
                unrelated.execute("CREATE TABLE notes (body TEXT)")
                unrelated.execute("INSERT INTO notes VALUES ('不得改变')")
                unrelated.commit()
            finally:
                unrelated.close()
            future_db = tmp / "高版本库" / "cabinet.db"
            future = connect(future_db)
            try:
                future.execute("PRAGMA user_version=999")
                future.commit()
            finally:
                future.close()
            for label, path in (("空数据库", empty_db), ("无关SQLite数据库", unrelated_db), ("未来schema数据库", future_db)):
                original = path.read_bytes()
                cli.run(f"拒绝{label}", ["--db", str(path), "projects", "count"], code=1)
                t.assert_eq(f"{label}原始字节未变", path.read_bytes(), original)
                probe = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
                try:
                    tables = {row[0] for row in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if path == empty_db:
                        t.assert_eq("空数据库未初始化任何表", tables, set())
                    elif path == unrelated_db:
                        t.assert_eq("其他数据库表未改变", tables, {"notes"})
                        t.assert_eq("其他数据库正文未改变", probe.execute("SELECT body FROM notes").fetchone()[0], "不得改变")
                    else:
                        t.assert_eq("未来schema未降级", probe.execute("PRAGMA user_version").fetchone()[0], 999)
                        t.assert_eq("未来schema未创建迁移备份", list(path.parent.glob("*.bak")), [])
                finally:
                    probe.close()
            cli.run("无库选择", ["--config", str(config), "projects", "count"], code=2)
            cli.run("选择参数互斥", ["--config", str(config), "--db", str(first / "cabinet.db"), "--library", "甲库", "projects", "count"], code=2)
            cli.run("配置与数据库参数互斥", ["--config", str(config), "--db", str(first / "cabinet.db"), "projects", "count"], code=2)
            cli.run("library-root需要db", ["--config", str(config), "--library", "甲库", "--library-root", str(store), "projects", "count"], code=2)
            cli.run("未知库名", ["--config", str(config), "--library", "不存在的库", "projects", "count"], code=1)
            cli.run("非法项目ID", cli.db(first, ["projects", "get", "--id", "abc"]), code=2)
            cli.run("项目不存在", cli.db(first, ["projects", "get", "--id", "999999999"]), code=1)
            cli.run("写权限仅限一次", cli.db(first, ["projects", "create", "--title", "权限不得持久化"]), code=1)

            custom = tmp / "自定义仓储"
            custom.mkdir()
            (custom / "override.txt").write_text("自定义仓储内容", encoding="utf-8")
            custom_result = cli.run("显式library-root", cli.db(first, ["files", "add", "--project-id", str(pid), "--path", "override.txt", "--storage-mode", "copy"], write=True, extra=["--library-root", str(custom)])) or {}
            custom_content = cli.run("显式仓储读取", cli.db(first, ["files", "read", "--id", str(custom_result.get("file_id", -1))], extra=["--library-root", str(custom), "--allow-file-read"])) or {}
            t.assert_eq("覆盖仓储路径生效", custom_content.get("content"), "自定义仓储内容")

            # 同名不同内容可揭示错误回退到默认目录或误用另一库的仓储。
            configured = tmp / "甲库设置仓储"
            configured.mkdir()
            (configured / "override.txt").write_text("甲库设置中的仓储内容", encoding="utf-8")
            (store / "override.txt").write_text("甲库默认根的错误内容", encoding="utf-8")
            repo.set_setting("library_root", str(configured))
            configured_id = str(custom_result.get("file_id", -1))
            configured_read = cli.run("db遵循仓储设置", cli.db(first, ["files", "read", "--id", configured_id], extra=["--allow-file-read"])) or {}
            t.assert_eq("db正文来自设置根", configured_read.get("content"), "甲库设置中的仓储内容")
            configured_read = cli.run("库名遵循仓储设置", ["--config", str(config), "--library", "甲库", "--allow-file-read", "files", "read", "--id", configured_id]) or {}
            t.assert_eq("库名正文来自设置根", configured_read.get("content"), "甲库设置中的仓储内容")
            overridden = cli.run("显式仓储覆盖已有设置", cli.db(first, ["files", "read", "--id", configured_id], extra=["--library-root", str(custom), "--allow-file-read"])) or {}
            t.assert_eq("显式参数优先于设置根", overridden.get("content"), "自定义仓储内容")
            t.assert_eq("显式参数未持久化仓储设置", repo.get_setting("library_root"), str(configured))

            other_store = tmp / "乙库设置仓储"
            other_store.mkdir()
            (other_store / "override.txt").write_text("乙库独立仓储内容", encoding="utf-8")
            (second / "library" / "override.txt").write_text("乙库默认根的错误内容", encoding="utf-8")
            other.set_setting("library_root", str(other_store))
            other_file = other.add_file(FileItem(project_id=1, path="override.txt", is_relative=True, kind="document"))
            for label, args in (("乙库名称", ["--config", str(config), "--library", "乙库", "--allow-file-read", "files", "read", "--id", str(other_file)]),
                                ("乙库db", cli.db(second, ["files", "read", "--id", str(other_file)], extra=["--allow-file-read"]))):
                read = cli.run(f"{label}独立仓储设置", args) or {}
                t.assert_eq(f"{label}正文隔离", read.get("content"), "乙库独立仓储内容")

            configured_export = tmp / "设置仓储导出"
            configured_export.mkdir()
            cli.run("按库名导出配置仓储", ["--config", str(config), "--library", "甲库", "--write", "projects", "export", "--id", str(pid), "--target-dir", str(configured_export)])
            t.assert_true("导出实体来自设置根", any(p.read_bytes() == "甲库设置中的仓储内容".encode("utf-8") for p in configured_export.rglob("*.txt")))
            repo.set_setting("library_root", str(tmp / "不存在的仓储"))
            fallback = cli.run("仓储设置失效回退默认根", cli.db(first, ["files", "read", "--id", configured_id], extra=["--allow-file-read"])) or {}
            t.assert_eq("失效设置使用默认根", fallback.get("content"), "甲库默认根的错误内容")

            package = ROOT / "skills" / "llm-cabinet"
            t.assert_true("skill包存在", package.is_dir())
            if package.is_dir():
                copied = tmp / "安装后的skill"
                shutil.copytree(package, copied)
                wrapper = copied / "scripts" / "cabinet.py"
                t.assert_true("可移植入口存在", wrapper.is_file())
                if wrapper.is_file():
                    cli.run("异地skill显式定位应用", ["--app-root", str(ROOT), *cli.db(first, ["projects", "count"])], prefix=[str(wrapper)], cwd=tmp)
                    relative = cli.run("异地skill相对外链", ["--app-root", str(ROOT), *cli.db(first, ["files", "add", "--project-id", str(pid), "--path", text_file.name], write=True)], prefix=[str(wrapper)], cwd=tmp) or {}
                    relative_item = repo.get_file(relative.get("file_id", -1))
                    t.assert_eq("link 相对路径按调用目录解析", Path(relative_item.path) if relative_item else None, text_file)
                    wrapper_env = cli.env.copy()
                    wrapper_env["LLM_CABINET_HOME"] = str(ROOT)
                    cli.run("异地skill环境变量定位应用", cli.db(first, ["projects", "count"]), prefix=[str(wrapper)], cwd=tmp, env=wrapper_env)
            help_result = subprocess.run([sys.executable, "-m", "app.cli", "--help"], cwd=ROOT, env=cli.env, capture_output=True, timeout=40)
            t.assert_eq("help正常退出", help_result.returncode, 0)
            t.assert_true("help为帮助文本", "--write" in help_result.stdout.decode("utf-8"))


if __name__ == "__main__":
    checks = T()
    try:
        main(checks)
    except Exception:
        traceback.print_exc(file=sys.stdout)
        checks.report()
        sys.exit(2)
    sys.exit(0 if checks.report() else 1)
