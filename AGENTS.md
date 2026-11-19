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
  ui/             # PySide6 UI（main_window.py 是主窗口；task #35 起按职责拆为
                #   mw_library/mw_projects/mw_files/mw_dnd/mw_search 五个 mixin，
                #   设置对话框为 settings/ 包：dialog.py + page_*.py + field_dialogs.py）
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

### 子代理协作

- 对于具有明确规划的任务，可以交给 `gpt-6.1-sol` 子代理执行，推理强度使用 `high`。

### 用户数据库与测试隔离

- 进行功能开发时，除非用户明确授权，否则不得读取用户数据库内容；只读查询同样需要明确授权，不得将一般的开发、调试或测试请求视为授权。
- 需要测试时，必须使用独立测试库或临时数据库，并自行构造测试数据；不得通过复制、导出或备份用户数据库来绕过上述限制。
- 测试程序及子代理必须显式使用测试库路径，避免默认配置意外连接用户数据库。

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

## 操作反馈策略（task #37）

| 场景 | 渠道 |
|---|---|
| 即时小反馈（已复制、已重命名） | statusBar `showMessage` |
| 需要用户知晓后果 / 做决定 | `app/ui/dialogs.py` 的 `confirm` / `ask_yes_no_cancel` |
| 批量操作结果 | 一次汇总框（`info`/`warn` + `detailed`），**禁止**在循环里弹模态框 |
| 后台任务完成/失败 | statusBar + 任务面板 |

- 全 UI 的确认/警告/提示对话框一律走 `app/ui/dialogs.py`（中文按钮），不要直接调 `QMessageBox.question/information/warning/critical`
- 删除用户文件一律走 `utils.move_to_trash`（进系统回收站，失败回退硬删除并汇报）
- 关键路径禁止 `except Exception: pass` 静默吞错，至少 `logging` 记录
- 文件操作失败必须提供完整的失败文件列表与详细原因：包含文件名称／记录 ID、源路径、目标路径（如有）、失败阶段及异常类型与消息。批量结果使用一次汇总框，摘要可截断，但 `detailed` 必须保留全部失败项；明确区分成功、失败、取消未处理及回滚结果，回滚失败时注明文件实际保留位置。
