# AGENTS.md — 项目约定

## 项目简介

LLM Cabinet：带 AI 元数据助手的轻量级项目化文件管理器。
- Python + PySide6 桌面应用
- SQLite 数据库，schema 版本独立于应用版本
- MCP Server 集成（暴露给外部 AI agent）

## 版本管理

两个独立维度，互不依赖：
- `__version__`（`app/__init__.py`）：应用版本，语义化，显示在 UI 和发布物中
- `SCHEMA_VERSION`（`app/db.py`）：数据库 schema 版本，每次需要 DB 迁移时 +1

## 目录结构

```
app/
  ui/             # PySide6 UI（main_window.py 是主窗口）
  db.py           # 数据库初始化 + 迁移（MIGRATIONS 注册表）
  models.py       # 数据模型（Project, PendingFile, Field 等）
  repository.py   # 数据访问层（Repository）
  importer.py     # 导入逻辑（scan_folders, import_folder_as_project）
  llm/            # LLM 集成（provider, config, suggestion）
  mcp/            # MCP Server
  utils.py        # 工具函数
tasks/            # 任务卡（编号 + 简述）
selftests/        # 自测脚本
docs/             # 文档（file-handling.md, migrations.md, release.md 等）
```

## 代码风格

- 中文注释和 docstring
- 类型注解（Python 3.10+ 语法）
- UI 字符串用中文
- git commit 用中文，格式：`feat/fix/refactor/docs: 简述`

## 工作流规则

### 每次完成一个功能/修复后，必须检查：

1. **CHANGELOG.md** — 在 `[Unreleased]` 下更新（Added/Changed/Fixed）
2. **README.md** — 如果涉及用户可见的功能变化，同步更新
3. **任务卡** — 如果对应 tasks/ 下的任务卡，标记完成状态

### 提交前

- `python -c "import py_compile; py_compile.compile('文件路径', doraise=True)"` 验证语法
- 如有 selftest，运行验证

### 文档归档

- 被取代的版本钉死文档（如 `manual-test-checklist-vX.Y.md`）和一次性历史导出统一移到 `docs/archive/`，不要堆在仓库根目录或 `docs/` 顶层。

## 数据库迁移

新增迁移时：
1. `SCHEMA_VERSION += 1`（`app/db.py`）
2. 写迁移函数 `_migrate_vN_to_vM(conn)`
3. 在 `MIGRATIONS` 列表追加 `(N, M, _migrate_vN_to_vM)`
4. 在 `CHANGELOG.md` 标注 `📦 schema vX → vY`

## 设置项约定

设置存储在 `repo.set_setting(key, value)` / `repo.get_setting(key, default)`，值都是字符串。
布尔值用 `"1"` / `"0"` 表示。
