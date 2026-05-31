# 01 · 文件表列可见性 + 列宽自定义 + 存储方式列

**工作量**：S（含 T2 的 XS）  
**优先级**：P1  
**状态**：✅ 2026-05-31

包含两个强耦合子任务：

## T2 · 文件列表新增"存储方式"列 ✅ 2026-05-31

**来源**：`TODO.md → 🎨 UI / 交互` 第 2 条

**目标**：在文件表里展示每条文件是 `链接` 还是 `仓储`，**只读不可编辑**，默认可见但可隐藏。

**实现要点**：
- 列数据：`file.is_relative` → True ⇒ `📦 仓储`，False ⇒ `🔗 链接`
- tooltip 解释含义
- 已纳入 T1 的列可见性体系，可隐藏

---

## T1 · 文件列表列可见性 + 列宽自定义 ✅ 2026-05-31

**来源**：`TODO.md → 🎨 UI / 交互` 第 1 条

**目标**：右下文件表的列宽可拖拽调整、列可在表头右键菜单里勾选显示/隐藏。

**约束**：`文件名` 列必须保留且强制显示，不可隐藏。

### 实现概要

- **新模块 `app/ui/files_table_columns.py`**：集中放列定义（key/label/default_width/mandatory）、偏好序列化/反序列化
- **DB schema 新增 `project_settings` 表**：`(project_id, key, value)`，按项目独立存储
- **Repository 新增 `get_project_setting / set_project_setting`** 接口
- **MainWindow**：
  - 文件名列 `Stretch`；其余列 `Interactive`（可拖拽）
  - 表头右键菜单嵌 `QCheckBox` 切换列可见性（必显列禁用 + tooltip）；菜单底部加"↺ 恢复默认列宽"
  - `sectionResized` 信号 → 保存到当前项目的 `project_settings`
  - 切换项目时 `_apply_files_columns_prefs(project_id)` 应用偏好（用 `_files_columns_loading` 标记避免初始化时触发保存）
- 所有用列索引的地方改用 `INDEX_BY_KEY[col_key]`（不再硬编码 0/1/2）

### 列定义（顺序即左→右）

| key | label | 默认宽 | mandatory |
|---|---|---|---|
| `name` | 文件名 | 320（实际 Stretch） | ✅ |
| `label` | 说明 | 240 | |
| `kind` | 类型 | 80 | |
| `storage` | 存储 | 80 | |

### 存储格式

`project_settings.value` 是 JSON：
```json
{"prefs": {"label": {"visible": true, "width": 240}, "storage": {"visible": false, "width": 80}}}
```

### 验收
- 切换项目时列宽/可见性能正确恢复 ✅
- 不同项目互不影响 ✅
- 表头右键菜单中"文件名"行 disable 且带 tooltip ✅
- "↺ 恢复默认列宽"恢复初始状态并持久化 ✅

### 与后续任务的关系
- 与 #02（文件列表独立窗口）兼容：抽出文件表组件时这套偏好逻辑仍可用
- 与 #04（系统文件折叠）兼容：折叠改变的是行层级，不动列定义
