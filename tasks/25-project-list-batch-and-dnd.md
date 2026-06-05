# 25 · 项目列表多选 + 标签拖放赋值 + 批量操作

> **状态**：⚪ 待做
>
> **工作量**：M（多选改造 S + 批量操作适配 S + 拖放赋值 M = 总 S+M）
>
> **优先级**：P1（单项目操作效率低，处理几十个项目时逐一编辑很痛苦）
>
> **依赖**：无硬依赖。与 #17（文件表改树形）无耦合。

## 背景

当前项目列表（卡片 / 表格双视图）是 `SingleSelection`：一次只能操作一个项目。
日常使用中常见以下场景无法高效完成：

1. **批量加标签**：拖入 10 个新项目后想统一加"待整理"标签，需要逐个右键编辑；
2. **批量导出 / 删除**：选中一批过时项目清理或打包，只能一个一个来；
3. **标签拖放**：当前标签只能通过编辑对话框填写，"把项目直接拖到标签树上打标"是 Calibre 式交互的核心体验；
4. **批量「已读 MCP 修改」**：#24 审计面板 + 刚刚加入的右键菜单虽能逐个标已读，但 MCP agent 批量操作后需要一次性清掉所有标记。

## 目标

把项目列表改造成**支持多选 + 拖放打标签 + 批量操作**的交互层。

### Phase A：项目列表多选（S）

- 卡片视图 (`proj_view`) 和表格视图 (`proj_table`): `SingleSelection` → `ExtendedSelection`
- 保持两视图共享同一个 `selectionModel`（当前已有 `self.proj_table.setSelectionModel(self.proj_view.selectionModel())`，只需改 `proj_view` 的 mode）
- Ctrl+点击 追加/取消单项，Shift+点击 范围选择
- 选中多个项目时：
  - 右侧预览区显示选中数量（如「已选 5 个项目」）而非单个项目详情
  - 文件表清空（多选状态下不展示某个项目的文件列表）

### Phase B：批量操作适配（S）

现有单项目 action 改造为感知多选：

| Action | 单选行为（不变） | 多选行为 |
|---|---|---|
| 编辑 | 弹单个项目编辑对话框 | 禁用（无法批量编辑不同项目的不同字段） |
| LLM 元数据建议 | 弹 LLMSuggestDialog | 弹确认框后批量排队提交 |
| 导出 | 弹 ExportDialog 选目标目录 | 弹目录选择 → 逐个导出到同一父目录下各自子文件夹 |
| 已读 MCP 修改 | 清除当前项目的 `mcp_modified_at` | 清除所有选中项目的 `mcp_modified_at` |
| 删除 | 弹确认 → 删除当前项目 | 弹确认（展示数量 + 仓储文件统计）→ 逐个删除 |
| 设置封面 | 弹文件选择或从剪切板粘贴 | 禁用 |

改造方式：
- 在各 action 入口处加 `_selected_project_ids()` helper，返回 `list[int]`
- 在 `_project_context_menu` 里对不适用多选的 action 做 `setEnabled(False)`

### Phase C：拖放标签赋值（M）

**核心交互**：从项目列表拖动选中项目 → 放到左侧标签树的某个标签节点上 → 为所有选中项目追加该标签。

**实现要点**：

1. **`ProjectView` 支持拖出**（`QAbstractItemView` 已有 `DragEnabled`，设 `setDragEnabled(True)` + `setDragDropMode(QAbstractItemView.DragOnly)`）：
   - `mimeData()` 返回自定义 MIME 类型 `application/x-llmcabinet-project-ids`，存放选中项目 id 列表（逗号分隔的字符串）
   - 拖出时显示项目数量提示（如 "5 个项目"）

2. **`TagTree` 支持放入**（`setAcceptDrops(True)` + `setDragDropMode(QAbstractItemView.DropOnly)`）：
   - `dragEnterEvent` / `dragMoveEvent`：检查 MIME 类型，允许时高亮目标节点
   - `dropEvent`：
     - 解析项目 id 列表
     - 找到当前 hover 的标签节点，读取其 value（即标签名）
     - 对每个项目调用 `repo._set_tags()` 追加该标签（保留已有标签，只增量加）
     - 刷新项目列表 + 标签树
   - 特殊节点处理：
     - "全部项目""未分类""待审阅""未读 MCP 修改"：拒绝 drop（`ignore()`）
     - "标签"分组节点（不可选中那个）：拒绝 drop
     - 标签前缀节点（`tag_prefix`）：允许 drop，效果等同于打上前缀自身标签

3. **撤销支持**（nice-to-have，P2）：
   - 拖放打标签后，状态栏短暂显示"已为 N 个项目添加标签「X」— 撤销"
   - 点击"撤销"调 `_set_tags` 移除刚加的标签

**风险点**：
- `project_tags` 写入不走 GUI 的字段编辑路径，只需调 repo 层，不与字段系统耦合
- 多选拖出时需确保 `mousePressEvent` / `mouseMoveEvent` 正确计算起始位置（与点击选中区分）
- 表格视图 (`proj_table`) 的行高较小，拖出手感可能不如卡片视图

## 影响面

| 点 | 影响 |
|---|---|
| `main_window.py` `proj_view` / `proj_table` selection mode | 只改属性，不动数据模型 |
| 所有 `action_*` 方法 + `_project_context_menu` | 需要感知 `_selected_project_ids()` |
| `tag_tree.py` | 新增 `dragEnterEvent` / `dragMoveEvent` / `dropEvent` 三个 override |
| `repository.py` | 可能需要一个 `batch_add_tag(pids, tag)` 便利方法（减少 commit 次数） |
| 预览面板 / 文件表 | 多选时清空并显示选中数量 |
| 已有 selftest | 不涉及（selftest 测 repo 层，不改 query 逻辑） |

## 实现顺序建议

1. Phase A：改 selection mode → 适配预览区多选提示
2. Phase B：加 `_selected_project_ids()` → 逐个改造 action
3. Phase C：ProjectView drag out → TagTree drop in → 标签写入

Phase C 依赖 Phase A（多选是拖放的前置），但 Phase A+B 可以独立交付价值。
