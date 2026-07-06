# 23 · MCP 工具收敛（17 → 5 个聚合工具，下线 AI 套 AI 链路）

> **状态**：✅ 完成 2026-06-04
>
> **工作量**：S（dispatch 重构）+ XS（文档/UI 文案）= S
>
> **优先级**：P1（直接影响 agent 接入体验；当前 17 个工具的工具栏对 LLM 很不友好）
>
> **依赖**：task #13 ✅（MCP server 基础设施 + 17 个工具的底层实现已就绪）

## 背景

`app/mcp/server.py` 当前注册了 **17 个 MCP tool**：

```
search_projects / get_project / list_files / count_projects
list_pending_suggestions / get_field_definition
list_libraries / switch_library
create_project / update_project / add_tag / remove_tag
add_file / remove_file
apply_suggestion / trigger_llm_suggestion
export_project / import_folder
```

实际使用中暴露出三类问题：

1. **工具数量过多 → LLM 工具选择能力下降**。同一类操作（如标签的 add/remove）拆成两个独立 tool，LLM 反复在工具列表里翻找。
2. **`trigger_llm_suggestion` 是 AI 套 AI 反模式**。
   调用 MCP 的 agent 本身就是 LLM；它再调一次 cabinet 内部 LLM 生成 pending suggestion，本质多此一举：
   - agent 完全可以根据 `get_project` 信息**直接**给字段建议，再走 `update_project` 写入；
   - 内部 LLM 用的是 cabinet 配的 provider/key/prompt，agent 看不见也控制不了；
   - 双层 LLM 调用 token 翻倍，不可控。
3. **`apply_suggestion` / `list_pending_suggestions` 在 agent 场景下没有承载场景**。
   pending suggestion 是 **UI 视角**的产物（人类不想自己填字段 → 让 cabinet AI 生成 → 人类审核 → 应用）。
   agent 场景下没有人类审核环节，"生成 → 应用"两步本来就该塌缩成 agent 自己 reason 后直接 update。
4. **`import_folder` 是复合操作**。
   它 = `create_project` + 多次 `add_file`，agent 可以自己组合实现；保留它反而引入"导入逻辑两套"的风险。
5. **`switch_library` 描述过时**。
   `tools.py:174` docstring 和 `server.py:124` description 都写"T1 read-only mode no-op"，但 `LibraryContext.switch()` 早已是完整切库实现（关旧 conn → 开新 db → 切 repo/library/current_handle，`context.py:129-206`）。只在单库模式（`--db` 启动）下返回错误是合理限制——不属于切换不可用。

## 目标

把 17 个 tool 收敛为 **5 个聚合 tool（共 14 个 action）**，并下线"agent 套 cabinet AI"的工具链路。

### 收敛后的工具清单

| 工具 | actions | 共享参数 | 说明 |
|---|---|---|---|
| `query_projects` | `search` / `get` / `count` | `project_id` / `keyword` / `tag` / `field_filter` | 纯查询，不写库 |
| `manage_project` | `create` / `update` / `add_tag` / `remove_tag` | `project_id` / `title` / `description` / `tags` / `tag` / `field_values` | project 元数据写操作 |
| `manage_files` | `list` / `add` / `remove` | `project_id` / `file_id` / `path` / `storage_mode` / `kind` / `label` | 文件 CRUD（list 是纯读，但跟 add/remove 同一资源域内聚） |
| `manage_libraries` | `list` / `switch` / `get_field` | `library_name` / `field_name` | 库级元数据 |
| `export_project` | — | `project_id` / `target_dir` | 单一职责，独立保留 |

### 下线的工具（彻底删除注册 + 删除底层实现）

- `list_pending_suggestions`（agent 场景下 pending 队列里不会有内容）
- `apply_suggestion`（同上，没有 pending 可应用）
- `trigger_llm_suggestion`（AI 套 AI 反模式）
- `import_folder`（agent 自己用 create + add_file 组合）

> **底层 `tools.py` 中对应的 4 个 async 函数也一起删掉**（`list_pending_suggestions` / `apply_suggestion` / `trigger_llm_suggestion` / `import_folder`），不留死代码。
> 这些功能在 GUI 内已有专门入口（字段助手、批量导入对话框），跟 MCP tool 解耦不影响 GUI。

## 设计方案

### 阶段 A：dispatch 层重构（`app/mcp/server.py` + `app/mcp/tools.py`）

**核心原则**：`tools.py` 中**保留下来的 13 个底层 async 函数签名一律不动**，只在 `server.py` 注册层新增聚合 wrapper 做 dispatch。这样改动半径最小，对未来可能的 selftest / 直接调用路径零影响。

#### A.1 聚合 wrapper 模式

每个聚合 tool 长这样：

```python
@mcp.tool(
    name="manage_project",
    description=(
        "对项目元数据的写操作。通过 action 参数选择子操作：\n"
        "  action=\"create\": 必传 title；可选 tags（逗号分隔）/ description。\n"
        "  action=\"update\": 必传 project_id；title/description/tags/field_values 选填，不传则保持。\n"
        "  action=\"add_tag\" / \"remove_tag\": 必传 project_id 和 tag。"
    ),
)
async def manage_project(
    action: str,
    project_id: int = 0,
    title: str = "",
    description: str = "",
    tags: str = "",
    tag: str = "",
    field_values: str = "",
) -> str:
    if action == "create":
        return _json_result(await tools.create_project(ctx, title, tags, description))
    if action == "update":
        return _json_result(await tools.update_project(ctx, project_id, title, description, tags, field_values))
    if action == "add_tag":
        return _json_result(await tools.add_tag(ctx, project_id, tag))
    if action == "remove_tag":
        return _json_result(await tools.remove_tag(ctx, project_id, tag))
    return _json_result({"ok": False, "error": f"未知 action：{action}"})
```

未知 action 走统一兜底 `{"ok": False, "error": "未知 action：..."}`，**不抛异常**（保持 MCP tool 协议层稳定）。

#### A.2 5 个聚合 tool 的描述模板（关键，LLM 选 action 全靠它）

description 写法统一遵循下列结构（例 `manage_project` 见 A.1）：

```
<一句话总述>
  action="X1": 必传 ...；可选 ...。
  action="X2": 必传 ...；可选 ...。
```

具体 5 个工具的 description 草稿见本卡末尾「附录 A · 工具描述全文」。

#### A.3 audit log 写啥

底层 `tools.py` 里 `_audit_log(ctx, "create_project", ...)` 这种已经写好 tool_name 了，**保留原 tool_name**（即 audit 表里仍然记录细粒度的 `create_project` / `update_project`，不记录 `manage_project`）。理由：

- audit 是给人/管理员看的，细粒度更有用；
- 如果改成 `manage_project`，再查"具体改了啥"还得解 arguments_json。

但聚合层可以**额外**在调用前后加一条 dispatch 级 audit（可选，不强求）。本卡范围内**不加**，保持改动最小。

### 阶段 B：清理废弃实现（`tools.py`）

删除以下 4 个底层 async 函数及其辅助代码：
- `tools.list_pending_suggestions` （`tools.py:99-138`）
- `tools.apply_suggestion`（`tools.py:425-438`）
- `tools.trigger_llm_suggestion`（`tools.py:444-483`）— 包括 `from app.llm.queue import LLMTaskQueue` 等内部 import
- `tools.import_folder`（`tools.py:522-561`）— 包括 `from app.importer import ImportOptions, import_folder_as_project, scan_folders` 等内部 import

清理后 `tools.py` 只剩 13 个 async 函数（含 `switch_library`、`list_libraries` 等只读工具）。

### 阶段 C：修正过时描述（`switch_library` 三处）

```diff
# server.py:122-124
- description="切换到指定名称的库。当前为只读模式，切换功能暂不可用（no-op）。",
+ description=(
+     "切换当前活动库（仅多库模式生效）。"
+     "单库模式（--db 启动）下会返回 ok=false 并附错误说明。"
+ ),
```

```diff
# tools.py:177
-    """Switch the active library by name (no-op in T1 read-only mode)."""
+    """Switch the active library by name. Returns an error in single-DB mode."""
```

```diff
# context.py 顶部 / switch() docstring
（删除残留的 "T1 read-only" 注释行）
```

具体行号以最新 master 为准，编码时再次 grep 确认。

### 阶段 D：UI / 文档同步

1. **`app/ui/settings_dialog.py:52-92`**
   `_MCP_CAPABILITIES_HTML` 整体重写工具区：
   - 标题"🔧 工具（共 10 个）"改为"🔧 工具（共 5 个）"
   - "浏览与搜索"/"编辑与管理"两个分组按新工具结构重列：

```
🔧 工具（共 5 个）
- query_projects — 搜索 / 查看 / 统计项目
- manage_project — 创建项目、修改信息、增减标签
- manage_files — 列出 / 添加 / 移除项目文件
- manage_libraries — 列出 / 切换库、查看字段定义
- export_project — 导出项目到本地目录
```

文案要符合「记忆约定」（不出现"落库""持久化"等技术黑话）。

2. **`docs/mcp.md`**
   - 「可用能力 → 工具」表（约 152-163 行）整体重写为 5 行
   - 「调用示例」里 `switch_library("工作文档")` 等示例改用聚合工具调用：
     ```
     manage_libraries(action="switch", library_name="工作文档")
     query_projects(action="search", keyword="周报")
     query_projects(action="get", project_id=15)
     ```
   - 删除「示例 4」中 `count_projects()` 改为 `query_projects(action="count")`

3. **`tasks/README.md`** 加索引行（#23）

4. **`TODO.md`** 在「LLM 工作流」分类加一条 `[#23 · ⚪] MCP 工具收敛...` 并在 task 完成后打 ✅

5. **`CHANGELOG.md`** 在合适版本（建议 1.x.x）下 added/changed/removed 三段：
   - **Changed**：MCP 工具列表从 17 个收敛为 5 个聚合工具（`query_projects` / `manage_project` / `manage_files` / `manage_libraries` / `export_project`）
   - **Removed**：`list_pending_suggestions` / `apply_suggestion` / `trigger_llm_suggestion`（agent 场景下 AI 套 AI 反模式）+ `import_folder`（agent 自行用 create+add_file 组合）
   - **Fixed**：`switch_library` 工具描述过时（早已是完整实现，不是 no-op）

> ⚠️ 这是 **breaking change**：已经接入 cabinet MCP 的 agent 配置会因为工具名变化失效。CHANGELOG 必须显式标注 BREAKING。考虑到 task #13 完成时间不久、外部用户基数小，不做兼容层。

### 阶段 E：selftest

**新增** `selftests/task23_mcp_tools_consolidation.py`，覆盖：

| 组 | 用例数 | 内容 |
|---|---|---|
| 工具数量校验 | 1 | `make_mcp_server(ctx)` 后枚举注册 tool 名集合，断言 == `{"query_projects", "manage_project", "manage_files", "manage_libraries", "export_project"}` |
| `query_projects` dispatch | 4 | search / get / count / 未知 action |
| `manage_project` dispatch | 5 | create / update / add_tag / remove_tag / 未知 action |
| `manage_files` dispatch | 4 | list / add / remove / 未知 action |
| `manage_libraries` dispatch | 4 | list / switch / get_field / 未知 action |
| 已删除工具不再注册 | 1 | 断言注册表里**不包含** `list_pending_suggestions` / `apply_suggestion` / `trigger_llm_suggestion` / `import_folder` |
| audit log 仍记录细粒度 tool_name | 2 | 调 `manage_project(action="create", ...)` 后查 mcp_audit 表，断言 tool_name = `create_project` |
| `switch_library` 描述更新 | 1 | grep server.py 不出现 "no-op" / "只读模式，切换功能暂不可用" |

合计 ~22 条断言。

复用 `selftests/_common.py` 里的 `make_temp_library_ctx()` 等基础设施（如未提供则就地新建 in-memory db）。

## 影响面 / 回归点

| 点 | 影响 |
|---|---|
| `tools.py` 中保留下来的 13 个底层函数 | 签名 / 实现完全不动，零回归 |
| `app/mcp/resources.py` | 不受本卡影响（resource 不变） |
| `app/mcp/prompts.py` | 不受本卡影响（prompt 不变） |
| `app/mcp/standalone.py` | 不受本卡影响（启动入口不变） |
| 其它 selftest（task14 / task20 等引用了 mcp/tools 名字的） | 用 grep 确认；如有断言旧 tool 名的需要相应更新（应该没有，task14 是库管理增强，与 mcp tool 名无关） |
| GUI 内的 LLM 队列 / pending suggestions 流程 | **零影响**——这些走的是 `app/llm/queue.py` 直接路径，不经过 MCP tool wrapper |

## 已澄清决策

1. **5 个聚合工具，14 个 action**：见上表。`manage_project` 不再塞 suggestion 相关 action。
2. **`trigger_llm_suggestion` / `apply_suggestion` / `list_pending_suggestions` 整体下线**：包括底层 async 函数一起删，不留死代码。下线理由：agent 场景没有"人类审核 pending"环节；agent 自己就是 LLM，没必要套娃。
3. **`import_folder` 下线**：agent 用 `manage_project(action="create")` + 多次 `manage_files(action="add")` 组合实现；保留它反而存在"两套导入逻辑"的维护负担。
4. **`switch_library` 保留并修正描述**：本来就能用，只是 docstring 过时。多库模式下生效；单库模式下返回结构化 error。
5. **`tools.py` 底层函数签名不动**：只在 `server.py` 注册层做 dispatch。回归风险最低。
6. **audit log 仍按细粒度 tool_name 记录**（即写 `create_project` 而非 `manage_project`），不在 dispatch 层加额外 audit。
7. **未知 action 不抛异常，返回 `{"ok": false, "error": "未知 action：X"}`**：保持 MCP 协议稳定，避免 agent 因为打错 action 拿到 transport 异常。
8. **breaking change 直说**：CHANGELOG 显式标注，不做兼容层。
9. **UI 文案符合"非黑话"约定**：settings 页 / docs/mcp.md 都用普通用户能读懂的中文描述，不出现"落库""序列化"等。
10. **selftest 不依赖 Qt**：纯 server / repo 层注册校验和 dispatch 校验；与 task #21/#22 selftest 风格一致。

## 附录 A · 5 个工具的 description 全文（编码时复制粘贴）

### `query_projects`

```
对项目库的纯查询操作。通过 action 参数选择子操作：
  action="search": 按 keyword（标题、描述、标签、自定义字段值、文件名/文件说明/逻辑目录名模糊匹配）和/或 tag 搜索项目，返回 id/title/tags/file_count/updated_at 摘要。两个参数都为空时返回全部项目。
  action="get": 必传 project_id，返回完整元数据（含 description/storage_mode/cover_file_id/field_values 等）。
  action="count": 统计项目总数；可传 tag 过滤。
注意：本工具不搜索文件内容；找特定文件请先 search 定位项目，再用 manage_files action="list" 看文件清单。
```

参数：`action`（必）, `project_id` (=0), `keyword` (=""), `tag` (=""), `field_filter` (="")

### `manage_project`

```
对单个项目的元数据写操作。通过 action 参数选择子操作：
  action="create": 必传 title；可选 tags（逗号分隔）/ description。返回新项目 project_id。
  action="update": 必传 project_id；title/description/tags（逗号分隔）/field_values（JSON 对象 {field_id: value}）任填，不传则保持原值。
  action="add_tag" / "remove_tag": 必传 project_id 和 tag。
需要写权限（启动时 --write-permission session 或 permanent）。
```

参数：`action`（必）, `project_id` (=0), `title` (=""), `description` (=""), `tags` (=""), `tag` (=""), `field_values` (="")

### `manage_files`

```
对项目文件的 CRUD 操作。通过 action 参数选择子操作：
  action="list": 必传 project_id；可选 kind 过滤（document / image / video / audio / archive / other）。返回该项目下的文件清单。
  action="add": 必传 project_id 和 path；storage_mode 选 link（链接，默认）或 copy（复制到库内）；可选 label。返回新 file_id。
  action="remove": 必传 file_id。此操作不可逆，建议执行前先与用户确认。
add/remove 需要写权限。
```

参数：`action`（必）, `project_id` (=0), `file_id` (=0), `path` (=""), `storage_mode` ("link"), `kind` (=""), `label` (="")

### `manage_libraries`

```
查询库级元数据 / 切换当前活动库。通过 action 参数选择子操作：
  action="list": 列出所有注册的库（来自 cabinet.json）。每个库含 name/path/label/description/is_current。
  action="switch": 必传 library_name。切换到指定库（仅多库模式生效；单库模式 --db 启动会返回 ok=false 并附错误说明）。
  action="get_field": 必传 field_name。按字段名返回字段定义（id/name/type/key/ord/visible/prompt_hint）。
本工具中三个 action 都是只读 / 上下文切换，不需要写权限。
```

参数：`action`（必）, `library_name` (=""), `field_name` (="")

### `export_project`

```
导出指定项目到本地目录。在 target_dir 下生成 project.json + 文件副本（即使是 link 模式的文件也会拷贝到导出目录，便于打包传递）。
必传 project_id 和 target_dir。需要写权限（涉及外部磁盘写入）。
```

参数：`project_id`（必）, `target_dir`（必）

## 待澄清

1. **聚合工具命名**：当前选择动词前缀 `query_` / `manage_` 区分纯读和写操作。考虑过的替代：
   - 全部用 `manage_*`（包括 `manage_projects`）— 不区分读写直觉差
   - 全部用名词（`projects` / `files`）— 太短，描述时容易和 resource 混
   默认按本卡执行；如倾向另一种，编码前告诉我。
2. **`manage_files` 里 `list` action 是否拆出去到 `query_projects`**：
   `list_files` 严格说也是查询。但参数（`project_id` / `kind` / `file_id`）跟项目查询差别大，且和 add/remove 共享 `project_id`，留在 `manage_files` 内聚性更好。
   默认留 `manage_files.list`；如倾向拆，编码前告诉我。
3. **是否给 `query_projects` 加一个 `action="list_pending_suggestions"`** 把"列出建议"（纯读）保留下来：
   讨论结论是 agent 场景下 pending 队列空（没有人类触发），保留也没意义 → 默认**不**保留。
   如果你认为某些工作流（GUI 触发后 agent 来 apply）有用，编码前说，可以加回来（仍然不加 `apply` / `trigger`）。
