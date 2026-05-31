# 13 · MCP Server（AI 文件中枢接口）

**工作量**：M+M（拆为 T1~T4，可分批发版）
**优先级**：T1/T2 = P1，T3/T4 = P2
**状态**：待做

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

本任务拆为 4 个子任务，可分批落地：

| 子任务 | 内容 | 优先级 | 工作量 |
|---|---|---|---|
| **T1** | 协议层 `app/mcp/server.py`：`make_mcp_server(repo, library)` 工厂；只读 Resources + 5 个安全 Tools | P1 | M |
| **T2** | 路径 **A** 独立进程入口 `python -m app.mcp.standalone` + Claude Desktop 配置说明文档 | P1 | S |
| **T3** | 写操作 Tools + 审计日志（`mcp_audit` 表）+ elicitation 抽象（先用文本确认） | P2 | M |
| **T4** | 路径 **B** GUI 内嵌 server + 原生 QDialog 确认 + 实时刷新（agent 改动 → 主窗口列表自动更新） | P2 | M |

T1/T2 可独立发版（read-only MCP server），T3 之后才允许写操作。

**不做（本任务内）**：

- 把 Cabinet 自己变成 MCP **client**（去调外部 agent 的 server）：那是反向集成，受众小，留作远期
- 跨进程的实时同步推送（agent 改了 → 通知所有打开的 GUI 进程）：单机单库即可，多进程同步走 SQLite 句柄即可
- 多用户/远程访问：MCP server 默认仅 localhost / stdio，不暴露公网

## 路径 A vs 路径 B：协同关系

两条路径**不互斥，共享同一份业务逻辑**，差别仅在宿主形式：

```
                ┌────────────────────────────────────────────────┐
                │  app/mcp/server.py                              │
                │  make_mcp_server(repo, library) -> Server       │
                │  ↑↑↑ 协议层（不依赖 GUI、不依赖具体宿主）        │
                └────────────┬───────────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
   ┌────────────────────┐        ┌────────────────────────────┐
   │ 路径 A：独立进程    │        │ 路径 B：GUI 内嵌            │
   │ python -m app.mcp.  │        │ MainWindow 后台线程跑       │
   │   standalone        │        │ HTTP/WebSocket 端口          │
   │ stdio 协议          │        │ elicitation 走 QDialog      │
   │ GUI 无关            │        │ agent 改完 → emit 信号刷 UI │
   └────────────────────┘        └────────────────────────────┘
```

**何时用哪个**：

| 场景 | 推荐路径 |
|---|---|
| 用户睡觉/笔记本待机时让 agent 自动整理新文件 | A（不依赖 GUI） |
| 命令行 / 服务器场景，没装 PySide6 | A |
| 用户在 GUI 里手动操作的同时 agent 也在改 | B（共享 Repository，UI 实时刷新） |
| 写操作要弹原生确认框 | B |

实施顺序：**先 A 后 B**。A 把核心通了，B 是体验加强。

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

| Tool | 入参 | 出参 |
|---|---|---|
| `search_projects` | `keyword?, tag?, field_filter?` | `[{id, title, tags, updated_at}]` |
| `get_project` | `project_id` | 完整元数据 dict |
| `list_pending_suggestions` | `project_id?` | LLM 建议待审阅列表 |
| `count_projects` | `tag?` | int |
| `get_field_definition` | `field_name` | Field dict |

### 为什么不全部用 Resources？

MCP 里 Resources 偏向"agent 可订阅、可缓存的稳定数据"，Tools 是"调用即变化或参数化查询"。`search_projects` 带参数 → Tool；`cabinet://tags` 整表读 → Resource。**符合 MCP 设计哲学，让 agent 缓存命中率更高。**

### 验收（T1）

- [ ] `python -c "from app.mcp.server import make_mcp_server; print(make_mcp_server)"` 可导入
- [ ] 用 MCP test client（如 `mcp inspect`）连上能列出所有 Resources / Tools
- [ ] `search_projects(keyword="科幻")` 返回正确结果
- [ ] `cabinet://project/1` 返回的 dict 与 `repo.get_project(1)` 完全一致
- [ ] 文件内容资源默认 403（settings 开关关闭时）
- [ ] 没有 Tool 写操作（防止 T3 之前误用）

## T2：路径 A 独立进程入口 + 文档

### 命令行入口

```bash
python -m app.mcp.standalone --db /path/to/cabinet.db [--library /path/to/library]
```

参数：

- `--db`：必填，cabinet.db 路径
- `--library`：可选，library 根目录（默认与 db 同目录）
- `--allow-file-read`：开启 `cabinet://file/{id}` 资源
- `--log-level`：默认 INFO

如未提供 `--db`，从 `app_data_dir() / "cabinet.db"` 兜底（与 GUI 默认一致）。

### Claude Desktop 配置示例（写进 `docs/mcp.md`）

```json
{
  "mcpServers": {
    "llm-cabinet": {
      "command": "python",
      "args": [
        "-m", "app.mcp.standalone",
        "--db", "C:/Users/you/AppData/Roaming/LLMCabinet/cabinet.db"
      ]
    }
  }
}
```

PyInstaller 打包后的 exe 也提供 standalone 模式入口（参数同上）。

### 验收（T2）

- [ ] `python -m app.mcp.standalone --db ...` 在 stdio 上正确响应 `initialize` / `tools/list` / `resources/list`
- [ ] Claude Desktop 加配置后能在工具栏看到 `llm-cabinet` 标志，并可在对话中调用
- [ ] 缺 `--db` 且默认路径不存在 → 友好报错退出
- [ ] `docs/mcp.md` 含至少 3 个调用示例（"列出所有标签"、"找科幻项目"、"读项目 N 的详情"）

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

# T3 默认实现：MCP 协议的 sampling/elicitation
# T4 在 GUI 里用 QDialog 实现一个 GuiConfirmHandler
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

## T4：路径 B GUI 内嵌

### 启动方式

`MainWindow` 启动时如设置开关 `mcp_embedded_enabled = true` → 后台线程起 HTTP server，端口可配（默认 7711，0 表示禁用）。

任务栏 / 状态栏显示一个小图标，反映状态：
- 灰：未启用
- 绿：运行中
- 黄：有 agent 正在调用
- 红：错误

### Confirm 走 QDialog

```python
class GuiConfirmHandler:
    def confirm(self, message: str, danger: bool = False) -> bool:
        # 在主线程弹 QMessageBox，Worker 线程等待
        return ask_in_main_thread(message, danger)
```

弹窗样式：

```
┌──────────────────────────────────────────┐
│ ⚠️ MCP 写操作请求                         │
│                                          │
│ Agent「claude-3.5-sonnet」想：            │
│ 创建项目「新下载的论文集」并加入 5 个文件 │
│                                          │
│ 详情 ▼                                    │
│   tool: create_project                    │
│   args: {"title": "新下载的论文集", ...}  │
│                                          │
│ □ 本会话中所有同名 Tool 自动允许           │
│                                          │
│      [拒绝]   [允许]                      │
└──────────────────────────────────────────┘
```

### 实时刷新

agent 通过 Tool 改动 db 后，server 在工作线程发出信号 → `MainWindow.refresh_projects()` / `_show_project()` 在主线程刷新。

### 验收（T4）

- [ ] settings 开 MCP → 状态栏出现绿点；关 → 灰色
- [ ] agent `create_project` → 主窗口项目列表自动出现新项（无需 F5）
- [ ] 写操作弹原生 QDialog；用户点"拒绝"则 Tool 返回 denied
- [ ] 勾选"本会话允许" → 同名 Tool 后续不再弹（关闭 GUI 后失效）
- [ ] `mcp_audit` 表里 `client_name` 能区分 A 路径和 B 路径来源

## 隐私与安全

- **localhost only**：路径 B 的 HTTP server 绑定 `127.0.0.1`，不监听公网；路径 A 走 stdio，无网络
- **文件读权限默认关**：`cabinet://file/{id}` 必须显式开启
- **写权限分级**：禁用 / 仅本会话 / 永久允许（粒度到 Tool）
- **库级隔离**：server 启动时绑定的库就是 agent 能访问的全部，不能跨库
- **审计日志不可清除**：UI 只显示，不提供"清除按钮"（有需要时手动改 db）
- **PRIVACY 同步**：新增「§8 MCP / agent 集成」段，说明 agent 通过 MCP 看到什么、写什么

## 风险

- **MCP SDK 成熟度**：Anthropic 的 Python `mcp` SDK 仍在演进；锁定一个稳定版本，requirements.txt 加版本号
- **SQLite 并发**（A + B 同时跑）：开 WAL 模式（`PRAGMA journal_mode=WAL`），事务保持短小；Repository 已经是短事务，问题不大
- **PyInstaller 打包**：MCP SDK 通常用 `anyio` / `asyncio`，需检查打包后是否完整；T2 收尾前在 CI 跑一次 standalone exe 测试
- **Tool 设计错误难撤回**：Tools 一旦发布，签名变更对 agent 不友好；T1 设计阶段多花时间，宁可少而稳
- **过度拟人化**：写卡片时避免"agent 像人一样可信"的措辞；UI 始终强调"agent 不是用户，写操作要确认"

## 依赖

- **强依赖** `tasks/10`（已完成）：`import_folder` Tool 复用 importer
- **强依赖** `tasks/09`（已完成）：`export_project` Tool 复用 exporter
- **软依赖** `tasks/08`（多库切换）：standalone 入口的 `--db` 参数对应"多库环境下选择库"
- **软依赖** `tasks/12`（自检体系）：必须配 `selftests/task13_mcp_server.py`，至少覆盖 read-only Resources + 一个写 Tool 的 elicitation 路径
- **可选依赖** `tasks/11` T3（库初始化向导）：可暴露为 Prompt `plan_library_schema`

## 后续扩展

- **MCP client**：让 Cabinet 反向调用外部 agent（如本地 ollama）
- **Prompts 模板库**：随版本附带常见工作流（"整理新文件"、"找重复项目"等）
- **多 agent 并发**：审计表加 `session_id` 字段，区分不同 agent 实例
- **Web UI**：远期把路径 B 的端口对接一个本地 Web 控制台（不上公网）
- **能力发现**：根据当前 schema 动态生成 Tool 列表（如新建字段 → agent 立刻能 set 它）

## 验收（整体）

整体 task 完工时：

- [ ] T1~T4 各自验收清单全过
- [ ] `selftests/task13_mcp_server.py` 跑通
- [ ] `docs/mcp.md` 含 Claude Desktop / Cursor 至少两种客户端配置示例
- [ ] CHANGELOG 记录 `mcp_audit` 表（schema vN → vN+1）
- [ ] PRIVACY 加 §8
- [ ] 在 README "特性"段加一行：「**MCP 集成**：通过 MCP 协议把库暴露给 Claude Desktop / Cursor / Cline 等 agent 客户端」
