# 24 · MCP 操作记录查看面板

> **状态**：⚪ 待做
>
> **工作量**：S（repo 层 + migration）+ S（UI 双 tab 对话框 + 主界面入口）+ XS（tools.py 打标）+ XS（selftest）= M
>
> **优先级**：P1（直接影响 MCP 使用透明度，用户需要看到 agent 做了什么）
>
> **依赖**：task #23 ✅（统一 `mcp_audit` 表 + tool_name 规范）

## 背景

MCP server 已经在 `mcp_audit` 表中记录了每次工具调用的：

| 列 | 内容 |
|---|---|
| `id` | 自增主键 |
| `ts` | 调用时间 |
| `client_name` | agent 客户端名称 |
| `tool_name` | 被调用的底层工具名（`create_project` 等） |
| `arguments_json` | 调用参数 JSON |
| `result_status` | `success` / `denied` / `error` |
| `error_message` | 失败原因（如有） |

目前用户完全看不到这些记录——只知道 agent 操作了库，但不知道具体调了什么、参数是什么、有没有报错。需要在 GUI 里提供一个查看面板。

## 目标

在 GUI 主窗口右下角状态栏加一个入口按钮，点击弹出 MCP 操作记录查看对话框。

### 入口位置

状态栏右侧，LLM 任务计数的 **左边**（`addPermanentWidget` 时先添加 MCP 按钮，再添加 LLM 按钮，保持视觉上从左到右先 MCP 再 LLM）。

```
sb.addPermanentWidget(self.lbl_mcp_count)   # ← 新
sb.addPermanentWidget(self.lbl_llm_count)   # ← 已有
```

### 状态栏按钮

- 文字/图标：`📋 MCP 记录: N`（N 为当日记录数）
- 样式：与 LLM 任务按钮一致
- tooltip：`点击查看 MCP 操作记录`
- 点击弹出 `MCPAuditDialog`

### 查看对话框 `MCPAuditDialog`

一个非模态或模态 `QDialog`（建议模态，与 `LLMTasksDialog` 一致）：

| 区域 | 内容 |
|---|---|
| 标题 | MCP 操作记录 |
| 工具栏 | 刷新按钮、清空按钮、筛选下拉（按 client_name / tool_name / result_status）、日期范围 |
| 表格 | 7 列：时间 / 客户端 / 操作 / 结果 / 参数摘要 / 错误信息 / ID |
| 底部 | 总记录数标签（超过上限时提示"已记录 N 条操作（最多保留 M 条），较早记录将被自动清理"）+ 翻页（每页 50 条） |

**额外统计区**（表格上方或独立 tab）：

| 统计项 | 数据来源 | 说明 |
|---|---|---|
| 总操作次数 | `COUNT(*)` | 全部记录 |
| 修改过项目的总数 | `COUNT DISTINCT project_id FROM mcp_audit WHERE tool_name IN (write 工具)` | 有写操作的项目 |
| 最近一次 MCP 操作 | `MAX(ts)` | 显示时间戳 |
| 各操作类型分布 | `GROUP BY tool_name` | 饼图或列表 |

**表格列定义**：

| 列名 | 数据 | 宽度 | 备注 |
|---|---|---|---|
| 时间 | `ts` | 160px | 格式 `YYYY-MM-DD HH:mm:ss` |
| 客户端 | `client_name` | 100px | 未知时显示 `-` |
| 操作 | `tool_name` | 140px | 中文映射：`create_project`→"创建项目" |
| 结果 | `result_status` | 70px | `success`→🟢成功 / `denied`→🟡拒绝 / `error`→🔴失败 |
| 参数 | `arguments_json` 前 60 字 | 200px | tooltip 显示完整 JSON |
| 错误信息 | `error_message` 前 40 字 | 120px | 仅 error/denied 时显示；tooltip 显示完整 |
| ID | `id` | 60px | |

**tool_name 中文映射表**（仅覆盖会出现在 audit 中的底层工具名，历史数据兼容加 `*`）：

```python
_TOOL_LABELS = {
    # --- 当前写工具 ---
    "create_project": "创建项目", "update_project": "修改项目",
    "add_tag": "添加标签", "remove_tag": "移除标签",
    "add_file": "添加文件", "remove_file": "删除文件",
    "export_project": "导出项目",
    # --- 当前只读工具（audit 中基本不出现，兜底） ---
    "get_project": "查看项目", "list_files": "列出文件",
    "search_projects": "搜索项目", "count_projects": "统计项目",
    "list_libraries": "列出库", "switch_library": "切换库",
    "get_field_definition": "查看字段定义",
    # --- task #23 已下线，历史记录兼容 ---
    "import_folder": "导入文件夹 *",
    "apply_suggestion": "应用建议 *",
    "trigger_llm_suggestion": "触发 LLM 建议 *",
    "list_pending_suggestions": "列出建议 *",
}
```

### 筛选功能

- **客户端筛选**：下拉列出所有 `DISTINCT client_name`
- **操作筛选**：下拉列出所有 `DISTINCT tool_name`（显示中文名）
- **状态筛选**：全部 / 成功 / 拒绝 / 失败
- **重置**：清除所有筛选

### 翻页

- 每页 50 条
- `SELECT COUNT(*)` 拿总数，`LIMIT 50 OFFSET ...` 分页
- 底部显示 "第 X/Y 页，共 N 条记录"
- 上一页/下一页按钮，边界时禁用

### 清空功能

- 工具栏"清空记录"按钮
- 弹出确认对话框："确定要清空所有 MCP 操作记录吗？此操作不可恢复。"
- 确认后 `DELETE FROM mcp_audit` 然后 `commit`

### 实时更新

- 对话框打开时加载全部记录
- "刷新"按钮重新加载
- 状态栏计数器定时刷新（30 秒），或每次 MCP 操作后由 `main_window` 调用 `_update_mcp_count`

## 设计方案

### Phase A：模型层 + Schema Migration

#### A.1 Schema Migration（v5 → v6）

`projects` 表加一列：

```sql
ALTER TABLE projects ADD COLUMN mcp_modified_at TEXT;
```

在 `app/db.py` 新增 `_migrate_v5_to_v6`，注册到 `MIGRATIONS` 列表。


#### A.2 repository.py 新增方法

```python
def count_mcp_audit(
    self,
    client_name: str = "",
    tool_name: str = "",
    result_status: str = "",
) -> int:
    """统计 MCP 审计记录数（支持筛选）。"""

def list_mcp_audit(
    self,
    offset: int = 0,
    limit: int = 50,
    client_name: str = "",
    tool_name: str = "",
    result_status: str = "",
) -> list[dict]:
    """分页获取 MCP 审计记录。"""

def mark_project_mcp_modified(self, project_id: int) -> None:
    """更新项目的 mcp_modified_at 为当前时间。"""

def list_mcp_modified_projects(self) -> list[dict]:
    """列出被 MCP 修改过的项目（按最近修改时间倒序）。"""
```

#### A.3 tools.py 写入时打标

每个 write 工具（`create_project` / `update_project` / `add_tag` / `remove_tag` / `add_file` / `remove_file` / `export_project`）成功后，调 `ctx.repo.mark_project_mcp_modified(project_id)`。

| 底层函数 | 打的 project_id |
|---|---|
| `create_project` | 新创建的 `pid` |
| `update_project` | 入参 `project_id` |
| `add_tag` / `remove_tag` | 入参 `project_id` |
| `add_file` | 入参 `project_id` |
| `export_project` | 入参 `project_id` |

只读工具（`query_projects.*` / `manage_libraries.list/switch/get_field/get_fields`）**不打标**。

**B.1 新建** `app/ui/mcp_audit_dialog.py`：

`MCPAuditDialog(QDialog)` — 双 tab 对话框：

- **Tab 1 "操作记录"**：审计日志表格 + 筛选 + 翻页（与原设计一致）
- **Tab 2 "MCP 修改过的项目"**：表格列 `project_id` / `title` / `最近操作时间` / `最近操作类型`，数据源 `list_mcp_modified_projects()`

**B.2 修改** `app/ui/main_window.py`：

- 状态栏加 `lbl_mcp_count`（样式参考 `lbl_llm_count`），文案 `📋 MCP 操作: N`
- 点击打开 `MCPAuditDialog`
- 侧边栏标签树下方新增 "🤖 MCP 修改过" 筛选项：与标签筛选语义一致，作为一个类标签维度。选中后主列表过滤只显示 `mcp_modified_at IS NOT NULL` 的项目（调用 `list_mcp_modified_projects()` 拿 id 列表）
- 新增 `_update_mcp_count()` 方法

### Phase C：计数器更新策略

- 对话框**打开时 + 刷新按钮**触发重新加载 → 不依赖定时器
- 状态栏计数在 `_build_statusbar` 里初始化一次
- 后续通过 MCP server 层触发：`main_window._update_mcp_count()`
  - MCP server 内调用 `main_window` 引用来更新
  - 或者简单的 30 秒 QTimer 定时刷新
  - **默认用 QTimer**，避免跨模块耦合

## 影响面 / 回归点

| 点 | 影响 |
|---|---|
| `app/db.py` | 新增 `_migrate_v5_to_v6`，`SCHEMA_VERSION` 递增至 6 |
| `app/repository.py` | 新增 4 个只读查询方法 + `mark_project_mcp_modified`，不影响现有接口 |
| `app/mcp/tools.py` | 6 个 write 函数各加一行 `mark_project_mcp_modified`，逻辑不变 |
| `app/ui/main_window.py` | 加 QLabel + 筛选入口，不影响现有功能 |
| `app/ui/mcp_audit_dialog.py` | 新文件，零回归 |
| 现有 selftest | task23 25 条需更新（tools.py 加调用），其余不触及 |

**📦 Schema v5 → v6**：`ALTER TABLE projects ADD COLUMN mcp_modified_at TEXT`，对存量项目该列为 NULL（等价于"未被 MCP 改过"）。

### Phase C：selftest

新建 `selftests/task24_mcp_audit_viewer.py`，覆盖：

| 组 | 用例数 | 内容 |
|---|---|---|
| `mark_project_mcp_modified` | 2 | 写入后 `mcp_modified_at IS NOT NULL`；未修改的项目 `mcp_modified_at IS NULL` |
| `list_mcp_modified_projects` | 2 | 标记 2 个项目后 list 返回 2 条；按时间倒序 |
| `count_mcp_audit` / `list_mcp_audit` | 6 | 全量计数、按 client/tool/status 筛选、分页边界、翻页 |
| 迁移无 crash | 1 | 打开 v5 库确认 `mcp_modified_at` 列存在 |

合计 ~11 条断言。复用 `_common.py` 的 `T` + 临时 db。

---

## 待澄清 → 已确认

1. **状态栏按钮文案**：`📋 MCP 操作: N`。
2. **对话框模态 vs 非模态**：模态。
3. **状态栏计数范围**：全部记录，但设上限并提示用户（如 "已记录 N 条操作（最多保留 M 条），较早记录将被自动清理"）。
4. **主界面筛选入口**：侧边栏，语义与标签筛选一致——把 "MCP 修改过" 作为一个类标签维度，放在标签树同级或下方。
5. **`mark_project_mcp_modified` 调用时机**：仅在 write 工具中调（`create_project` / `update_project` / `add_tag` / `remove_tag` / `add_file` / `remove_file` / `export_project`）。只读工具不打标。
6. **selftest 需求**：要。
