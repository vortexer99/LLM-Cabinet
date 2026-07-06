# 13 · MCP Server（AI 文件中枢接口）

**工作量**：M + S + M（拆为 T1~T3，可分批发版）
**优先级**：T1/T2 = P1，T3 = P2
**状态**：✅ 2026-06-03

## 来源

`README → 灵感来源` 末段「未来设想」的具体落地方案。
通过 [MCP（Model Context Protocol）](https://modelcontextprotocol.io/) 把 Cabinet 的业务能力暴露给外部 agent，让 LLM Cabinet 真正成为"AI 文件中枢"。

## 目标

让任何 MCP 兼容客户端（Claude Desktop / Cursor / Cline / 自建 agent）的 agent 可以：

- **读**：检索项目、读元数据、读字段定义、读标签树、按需读单文件内容
- **写**：创建项目、加文件、更新元数据、调用 LLM 建议、导出/导入
- **触发预制工作流**：「整理新下载的文件」「审核元数据质量」等

并保证：

- 所有写操作走 Repository 业务层（与 GUI 同一套校验/事务路径）
- 危险操作有显式确认（elicitation）
- 所有 agent 调用进审计日志，可追溯

## 范围与边界

本任务拆为 3 个子任务，可分批落地：

| 子任务 | 内容 | 优先级 | 工作量 |
|---|---|---|---|
| **T1** | 协议层 `app/mcp/server.py`：`LibraryContext` + 只读 Resources + 7 个 Tools（含 `list_libraries` / `switch_library`） | P1 | M |
| **T2** | 独立进程入口 `python -m app.mcp.standalone` + 多库感知 + Claude Desktop 配置说明文档 | P1 | S |
| **T3** | 写操作 Tools + 审计日志（`mcp_audit` 表）+ elicitation 抽象（文本确认） | P2 | M |

T1/T2 可独立发版（read-only MCP server），T3 之后才允许写操作。

**不做（本任务内）**：

- 把 Cabinet 自己变成 MCP **client**（去调外部 agent 的 server）：那是反向集成，受众小，留作远期
- 跨进程的实时同步推送（agent 改了 → 通知所有打开的 GUI 进程）：单机单库即可，多进程同步走 SQLite 句柄即可
- 多用户/远程访问：MCP server 默认仅 localhost / stdio，不暴露公网

### 前置重构：库注册表（`recent_libraries` → `libraries`）

当前 `cabinet.json` 里的 `recent_libraries` 是"最近打开过的 5 个库"，存在两个问题：

1. **上限 5 个**：打开第 6 个库会挤掉最早的，多库用户无法稳定看到全部库
2. **语义是历史记录**：不是用户能主动管理的清单

MCP 需要的是一个**稳定的库注册表**——用户明确知道 agent 能在哪些库里操作。因此本 task 的前置工作是把 `recent_libraries` 重构为 `libraries`（库注册表）：

| 改动项 | 说明 |
|--------|------|
| `cabinet.json` 字段 | `recent_libraries` → `libraries`（向后兼容：读取时优先 `libraries`，不存在则从 `recent_libraries` 迁移） |
| 取消上限 | 不再限制列表长度，用户显式增删 |
| `CabinetConfig.touch()` | 打开新库自动注册；已存在的更新 `last_opened`，不再驱逐旧条目 |
| `CabinetConfig.remove()` | 用户显式从注册表移除（不动磁盘文件） |
| UI 标签 | Welcome 对话框「最近使用的库」→「我的库」；设置页「最近库」→「库列表」 |

这项重构作为本 task 的前置小 task 独立完成（可视为 task #08 后续演进），在 T1 开始前做完。`CabinetConfig.libraries` 将成为 MCP 独立进程发现多库的唯一数据源。

## 协议层架构

独立进程通过 `cabinet.json` 发现所有注册库，架构简洁：

```
┌─────────────────────────────────────┐
│  %APPDATA%/LLMCabinet/cabinet.json   │
│  libraries: [{path, label, ...}]     │  ← 库注册表
└──────────────┬──────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  app/mcp/standalone.py               │
│  reads cabinet.json → LibraryContext  │
│  list_libraries / switch_library      │
│  stdio 协议 (MCP client 通过 stdin   │
│  与 server 通信，无需网络端口)         │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  app/mcp/server.py                    │
│  make_mcp_server(ctx) -> Server       │
│  ↑↑↑ 协议层（不依赖 GUI）             │
│  ctx 提供当前激活的 repo + library    │
│  支持运行时切换库（switch_library）    │
└──────────────────────────────────────┘
```

独立进程不需要 GUI 运行：Claude Desktop 等 MCP 客户端启动时会 spawn 子进程 `python -m app.mcp.standalone`，通过 stdio 通信。

## T1：协议层 + 只读能力

### 模块结构

```
app/mcp/
├── __init__.py
├── server.py          make_mcp_server(repo, library) 工厂
├── tools.py           工具函数注册（高内聚）
├── resources.py       Resource URI handlers
├── prompts.py         预制 Prompt 模板
└── standalone.py      路径 A 入口（stdio）
```

### 暴露的 Resources（读）

| URI 模板 | 内容 |
|---|---|
| `cabinet://library/info` | 库元信息：路径、项目数、字段数、schema 版本、应用版本 |
| `cabinet://library/stats` | 统计概览：每个标签的项目计数、字段填充率 |
| `cabinet://tags` | 标签列表（含每个标签的项目数） |
| `cabinet://fields` | 字段定义（id / name / type / key / ord / visible） |
| `cabinet://projects` | 项目摘要列表（id / title / tags / updated_at） |
| `cabinet://project/{id}` | 单项目完整元数据 + 字段值 + 标签 |
| `cabinet://project/{id}/files` | 单项目文件清单（不含内容） |
| `cabinet://file/{id}` | 单个文件**内容**（默认禁用，需 settings 开关；图像/二进制按 base64） |

### 暴露的 Tools（T1 只读子集）

| Tool | 入参 | 出参 | 说明 |
|---|---|---|---|
| `search_projects` | `keyword?, tag?, field_filter?` | `[{id, title, tags, file_count, updated_at}]` | 按关键词/标签搜项目。出参含 `file_count`，agent 可据此判断是否有子文件需要进一步查看 |
| `get_project` | `project_id` | 完整元数据 dict（含 tags、field_values） | 拿单个项目的全部信息 |
| `list_files` | `project_id, kind?` | `[{id, path, label, kind, added_at, missing}]` | 列出指定项目下的文件，可按类型过滤。**不做关键词搜索**——文件名往往是 `论文.pdf` 这类无意义串，内容向搜索请用 `search_projects` 定位项目后从这里取文件列表 |
| `list_pending_suggestions` | `project_id?` | LLM 建议待审阅列表 | |
| `count_projects` | `tag?` | int | |
| `get_field_definition` | `field_name` | Field dict | |
| **`list_libraries`** | 无 | `[{name, path, label, last_opened, project_count}]` | 来自 `cabinet.json` 的 `libraries` 列表 |
| **`switch_library`** | `library_name` | `{ok, library_name, path}` | 切换到指定库，重建 repo 连接 |

`list_libraries` 和 `switch_library` 是 MCP 多库体验的核心：用户在客户端对话中说"帮我把论文加到学术研究库"，agent 先 `list_libraries` 发现可用库，再 `switch_library("学术研究")` 切换到目标库后执行操作。

`list_files` 的典型调用链路：

```
用户："找量子计算的论文"
  agent: search_projects("量子计算")      → [{id: 7, title: "量子计算阅读", file_count: 3}]
  agent: list_files(project_id=7)         → [{path: "qc_intro.pdf", kind: "document"}, ...]
  agent: list_files(project_id=7, kind="document") → 只拿文档类文件
```

不做独立 `search_files(keyword)` 的原因：文件仍作为项目的组成部分管理；`search_projects` 可用文件名/文件说明辅助定位项目，文件正文全文搜索则留给后续索引任务。

### 协议层设计：`LibraryContext`

`make_mcp_server` 不直接持有 `(repo, library)`，而是持有一个 `LibraryContext`：

```python
class LibraryContext:
    """管理当前激活的库，支持运行时切换。"""
    def __init__(self, cabinet_config: "CabinetConfig"):
        self._config = cabinet_config
        self._repo: Optional[Repository] = None
        self._library: Optional[Library] = None
        self._current_handle: Optional[LibraryHandle] = None

    @property
    def repo(self) -> Repository: ...
    @property
    def library(self) -> Library: ...
    def list_libraries(self) -> list[dict]: ...
    def switch(self, name: str) -> dict: ...
    def load_default(self) -> None: ...  # 打开第一个可用库
```

T1 阶段先实现单库模式（`load_default()` 打开默认库，`switch` 为空操作），接口留好。T2 再做完整的 `switch_library`。这样 T1 的发版不受阻塞。

### 为什么不全部用 Resources？

MCP 里 Resources 偏向"agent 可订阅、可缓存的稳定数据"，Tools 是"调用即变化或参数化查询"。`search_projects` 带参数 → Tool；`cabinet://tags` 整表读 → Resource。**符合 MCP 设计哲学，让 agent 缓存命中率更高。**

### 验收（T1）

- [ ] `python -c "from app.mcp.server import make_mcp_server; print(make_mcp_server)"` 可导入
- [ ] 用 MCP test client（如 `mcp inspect`）连上能列出所有 Resources / Tools
- [ ] `search_projects(keyword="科幻")` 返回正确结果，每项含 `file_count`
- [ ] `list_files(project_id=1)` 返回该项目全部文件
- [ ] `list_files(project_id=1, kind="document")` 仅返回文档类文件
- [ ] `cabinet://project/1` 返回的 dict 与 `repo.get_project(1)` 完全一致
- [ ] 文件内容资源默认 403（settings 开关关闭时）
- [ ] 没有 Tool 写操作（防止 T3 之前误用）
- [ ] `list_libraries` 返回的列表与 `cabinet.json` 的 `libraries` 一致
- [ ] `LibraryContext` 可导入并正常初始化

## T2：路径 A 独立进程入口 + 文档

### 命令行入口

```bash
python -m app.mcp.standalone [--config /path/to/cabinet.json] [--db /path/to/cabinet.db]
```

参数（三种启动模式，按优先级）：

| 模式 | 说明 |
|------|------|
| **多库模式**（推荐） | 不传 `--db`，从 `cabinet.json` 的 `libraries` 列表自动发现所有库。`list_libraries` / `switch_library` 可切换 |
| **单库模式** | `--db /path/to/cabinet.db`，显式绑定一个库。`libraries` 列表仅含该项，`switch_library` 不可切换 |
| **默认库兜底** | 两者都不传 → 从 `app_data_dir()` 找 `cabinet.json`；如果也不存在 → 报错提示用户先创建库 |

可选参数：

- `--config`：指定 `cabinet.json` 路径（默认 `app_data_dir() / "cabinet.json"`）
- `--allow-file-read`：开启 `cabinet://file/{id}` 资源
- `--log-level`：默认 INFO

### 多库模式的工作流程

```
1. standalone 启动 → 读取 cabinet.json → 拿到 libraries 列表
2. agent 问"我有哪些库？" → list_libraries() 返回完整列表
3. agent 说"切到学术研究库" → switch_library("学术研究")
   → 内部重建 repo (connect 新 db) + library (新 library 目录)
4. 后续所有 Tool 自动操作当前激活的库
```

### Claude Desktop 配置示例（写进 `docs/mcp.md`）

**多库模式（推荐，零配置）：**

```json
{
  "mcpServers": {
    "llm-cabinet": {
      "command": "python",
      "args": ["-m", "app.mcp.standalone"]
    }
  }
}
```

**单库模式（安全锁定）：**

```json
{
  "mcpServers": {
    "llm-cabinet-work": {
      "command": "python",
      "args": [
        "-m", "app.mcp.standalone",
        "--db", "D:/Projects/work/cabinet.db"
      ]
    }
  }
}
```

单库模式保持了原有的安全隔离属性：agent 只能访问这一个库。多库模式下可通过 settings 的 `mcp_library_discovery` 开关控制是否允许跨库切换（默认开）。

PyInstaller 打包后的 exe 也提供 standalone 模式入口（参数同上）。

### 验收（T2）

- [ ] `python -m app.mcp.standalone`（无参数）能正确读取 `cabinet.json` 并初始化
- [ ] `list_libraries` 返回 `cabinet.json` 中注册的全部库
- [ ] `switch_library("xxx")` 切换成功后 `search_projects` 返回新库的数据
- [ ] `python -m app.mcp.standalone --db ...` 单库模式下 `list_libraries` 仅返回该项
- [ ] 缺 `cabinet.json` 且无 `--db` → 友好报错提示"请先在 LLM Cabinet 中创建或打开一个库"
- [ ] Claude Desktop 加配置后能在工具栏看到 `llm-cabinet`，并可在对话中调用
- [ ] `docs/mcp.md` 含至少 4 个调用示例（"列出所有库"、"切换到某库"、"找项目"、"读项目详情"）

## T3：写操作 + 审计 + Elicitation

### 新增 Tools（写）

| Tool | elicitation 必要性 |
|---|---|
| `create_project(title, tags?, description?)` | ✅ 创建新项目 |
| `update_project(project_id, fields)` | ✅ 改元数据 |
| `add_tag(project_id, tag)` | ⚠️ 仅当 tag 为新建时确认 |
| `remove_tag(project_id, tag)` | ✅ |
| `add_file(project_id, path, storage_mode)` | ✅ 涉及文件落地 |
| `remove_file(file_id)` | ✅✅ 双重确认（删文件） |
| `apply_suggestion(suggestion_id)` | ⚠️ 默认开 |
| `trigger_llm_suggestion(project_id, target_fields?)` | ⚠️（消耗 token） |
| `export_project(project_id, target_dir)` | ✅ 涉及外部目录写 |
| `import_folder(folder_path, options)` | ✅ 复用 #10 importer |

### Elicitation 抽象

```python
class ConfirmHandler(Protocol):
    def confirm(self, message: str, danger: bool = False) -> bool: ...

# T3 默认实现：MCP 协议的 elicitation（文本确认）
```

每个写操作进 `make_mcp_server` 时都会拿到 `ConfirmHandler`；Tool 在执行前调 `confirm()`；返回 False 则操作被拒。

### 审计日志：`mcp_audit` 表

```sql
CREATE TABLE mcp_audit (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  client_name TEXT,           -- 'claude-desktop' / 'cursor' / ...
  tool_name TEXT NOT NULL,    -- 'create_project'
  arguments_json TEXT,
  result_status TEXT,          -- 'success' / 'denied' / 'error'
  error_message TEXT
);
```

`schema v2 → v3` 迁移：纯加表，零数据风险。

GUI 设置页加一个「MCP 审计日志」查看入口（最近 200 条）。

### 验收（T3）

- [ ] 所有写 Tool 在 `confirm() == False` 时**不**执行任何 SQL
- [ ] 每次调用（不论成败）落进 `mcp_audit` 表
- [ ] settings 加「MCP 写操作权限：禁用 / 仅本会话 / 永久允许」三档
- [ ] `remove_file` 必须双重确认，单次确认无效
- [ ] 数据库迁移 v2 → v3 自检通过（参考 `selftests/task00_db_migration.py`，task #12 计划项）

## 隐私与安全

- **localhost / stdio only**：MCP server 通过 stdio 与客户端通信，不开放网络端口，不暴露公网
- **文件读权限默认关**：`cabinet://file/{id}` 必须显式开启
- **写权限分级**：禁用 / 仅本会话 / 永久允许（粒度到 Tool）
- **库级隔离**：单库模式（`--db`）下 agent 只能访问指定库。多库模式下 `list_libraries` 暴露所有注册库，可通过 settings 的 `mcp_library_discovery` 开关禁用跨库切换（关闭后仅操作启动时的默认库）
- **审计日志不可清除**：UI 只显示，不提供"清除按钮"（有需要时手动改 db）
- **PRIVACY 同步**：新增「§8 MCP / agent 集成」段，说明 agent 通过 MCP 看到什么、写什么，以及多库场景下 agent 能看到哪些库

## 风险

- **MCP SDK 成熟度**：Anthropic 的 Python `mcp` SDK 仍在演进；锁定一个稳定版本，requirements.txt 加版本号
- **SQLite 并发**（standalone + GUI 同时跑）：开 WAL 模式（`PRAGMA journal_mode=WAL`），事务保持短小；Repository 已经是短事务，问题不大
- **PyInstaller 打包**：MCP SDK 通常用 `anyio` / `asyncio`，需检查打包后是否完整；T2 收尾前在 CI 跑一次 standalone exe 测试
- **Tool 设计错误难撤回**：Tools 一旦发布，签名变更对 agent 不友好；T1 设计阶段多花时间，宁可少而稳。特别注意 `list_libraries` 和 `switch_library` 的多库语义是用户最频繁接触的接口，签名字段必须从第一版就定好
- **过度拟人化**：写卡片时避免"agent 像人一样可信"的措辞；UI 始终强调"agent 不是用户，写操作要确认"

## 依赖

- **强依赖** `tasks/10`（已完成）：`import_folder` Tool 复用 importer
- **强依赖** `tasks/09`（已完成）：`export_project` Tool 复用 exporter
- **强依赖**「库注册表重构」（本 task 前置）：`cabinet.json` 的 `recent_libraries` → `libraries`，取消 5 条上限，作为 MCP 多库发现的数据源。见上文「前置重构」节
- **软依赖** `tasks/12`（自检体系）：必须配 `selftests/task13_mcp_server.py`，至少覆盖 read-only Resources + list_libraries + switch_library + 一个写 Tool 的 elicitation 路径
- **可选依赖** `tasks/11` T3（库初始化向导）：可暴露为 Prompt `plan_library_schema`

## 后续扩展

- **MCP client**：让 Cabinet 反向调用外部 agent（如本地 ollama）
- **GUI 原生确认**：远期为 GUI 内嵌 MCP client，写操作弹 QDialog 而不是纯文本确认（当前 T3 的文本确认够用）
- **GUI 实时刷新**：agent 修改数据后自动刷新 GUI 列表，无需手动 F5
- **Prompts 模板库**：随版本附带常见工作流（"整理新文件"、"找重复项目"等）
- **多 agent 并发**：审计表加 `session_id` 字段，区分不同 agent 实例
- **Web UI**：远期对接一个本地 Web 控制台（不上公网）
- **能力发现**：根据当前库的 schema 动态生成 Tool 列表（如自建字段 → agent 自动知道能 set 哪些字段）

## 验收（整体）

整体 task 完工时：

- [ ] T1~T3 各自验收清单全过
- [ ] `selftests/task13_mcp_server.py` 跑通（覆盖 Read-only Resources + list_libraries + switch_library + 一个写 Tool 的 elicitation 路径）
- [ ] `docs/mcp.md` 含 Claude Desktop / Cursor 至少两种客户端配置示例，包含多库模式和单库模式
- [ ] CHANGELOG 记录 `mcp_audit` 表（schema vN → vN+1）及 `cabinet.json` `recent_libraries` → `libraries` 迁移
- [ ] PRIVACY 加 §8（含多库可见性说明）
- [ ] 在 README "特性"段加一行：「**MCP 集成**：通过 MCP 协议把库暴露给 Claude Desktop / Cursor / Cline 等 agent 客户端」
