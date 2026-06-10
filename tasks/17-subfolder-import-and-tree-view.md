# 17 · 子文件夹导入 + 文件树形展示（数据库驱动）

**工作量**：M+M+S
**优先级**：T1 = P0, T2 = P1, T3 = P1
**状态**：✅ T1 2026-06-10 · ✅ T2 2026-06-10 · ✅ T3 2026-06-10

## 来源

1. 用户反馈："子文件夹目前是没有被导入的"
2. 定位结果：`_expand_paths()` 只递归一层，子目录文件静默丢弃
3. 仓储模式 `import_copy` 拍平所有文件到项目根，目录结构丢失

## 核心设计：三层解耦

```
┌───────────────────────────────────────────────┐
│  UI 层 — 用户看到的树                          │
│  由 files.subfolder 字段驱动，与物理位置无关    │
├───────────────────────────────────────────────┤
│  数据库层                                      │
│  files.subfolder = "设计素材/UI"               │
│  files.path = "project_3/abc123.png" (仓储)    │
│            = "D:\x.pdf" (链接)                 │
├───────────────────────────────────────────────┤
│  物理层 — 磁盘上的实际文件                      │
│  仓储模式：library/<pid>/file.pdf（拍平）       │
│  链接模式：原位不动                             │
└───────────────────────────────────────────────┘
```

**物理存储与 UI 组织完全解耦**：
- 仓储模式物理上继续拍平（简单、可预测），不需要 `import_copy` 支持 subpath
- 链接模式原位不变
- 用户在 UI 里看到的目录树来自数据库的 `subfolder` 字段
- 未来用户拖拽改目录结构 = 改数据库字段，物理文件纹丝不动

参考：Eagle、Zotero、DEVONthink 等成熟软件均采用此模式——UI 树由数据库维护，不映射文件系统目录。

## 目标

1. **数据层**：`files` 表新增 `subfolder` 字段，存逻辑子路径（如 `"ML/NLP"`）；导入时从源目录结构自动初始化
2. **UI 层**：文件表从 `QTableWidget` 改 `QTreeWidget`，按 `subfolder` 分组折叠展示
3. **闭环验证**：拖入 `a/sub/x.pdf + a/y.pdf` → 项目里看到树 `📁 sub/ → 📄 x.pdf` + `📄 y.pdf`；切到仓储模式看物理目录仍然是平的

## 范围与边界

| 子任务 | 内容 | 优先级 | 工作量 |
|---|---|---|---|
| **T1** | 数据层：schema v7 加 `subfolder` 列 + 迁移 + 导入流程填充 + 模型/仓储适配 | P0 | M |
| **T2** | UI：`QTableWidget` → `QTreeWidget`，按 `subfolder` 建树，拖放/列偏好适配 | P1 | M |
| **T3** | 清理：删文件后清空逻辑空目录；删目录节点二次确认 | P1 | S |

**不做（本任务内）**：
- **手动拖拽改文件目录归属**：本期 `subfolder` 只由导入自动填充，用户不可编辑
- **物理层保留子目录**：仓储模式继续拍平；未来如果需要可作为独立优化
- **task #04 系统文件折叠**：依赖 T2 的 QTreeWidget，独立排期
- **历史数据迁移**：已导入的拍平文件 `subfolder` 为空字符串，自然落在顶层

---

## T1：数据层

### A. Schema v7 迁移

`files` 表新增列：

```sql
ALTER TABLE files ADD COLUMN subfolder TEXT NOT NULL DEFAULT '';
```

- `subfolder` = 文件在项目内的逻辑目录路径，POSIX 格式，不含文件名
- 空字符串 `""` = 项目顶层
- 示例：`"ML"`, `"ML/NLP"`, `"设计素材/UI/按钮"`
- 不含前导/尾随斜杠

**迁移时不需要回填**：已有文件 `subfolder` 默认 `""`，自然显示在顶层。

### B. 模型层

`app/models.py` 新增 `PendingFile` dataclass + `FileItem` 新增 `subfolder` 字段：

```python
@dataclass
class PendingFile:
    """待导入的文件，从用户拖入路径到写入 files 表之间的中间载体。"""
    src: Path       # 源文件绝对路径
    subfolder: str  # 逻辑子路径，如 "ML/NLP"；"" = 顶层

@dataclass
class FileItem:
    ...  # 现有字段不变
    subfolder: str = ""   # 逻辑子路径，如 "ML/NLP"
```

### C. Repository 层

`app/repository.py` 适配：
- `add_file()` 接受 `subfolder` 参数，写入 `files.subfolder`
- `list_files()` 返回的 `FileItem` 包含 `subfolder`
- SQL INSERT/SELECT 加上 `subfolder` 列

### D. 导入流程填充 subfolder

#### D1. `MainWindow._expand_paths` 递归 + 返回子路径

```python
@staticmethod
def _expand_paths(paths: list) -> list[PendingFile]:
    """把混合路径展开为 [PendingFile, ...]。

    PendingFile:
        src: Path          — 源文件绝对路径
        subfolder: str     — 相对导入根目录的逻辑子路径（POSIX）
    """
```

- 文件 → `PendingFile(src=绝对路径, subfolder="")`
- 目录 → 递归收集所有文件，`subfolder` = 相对该目录的 POSIX 子路径
  - `a/sub/x.pdf` → `subfolder="sub"`
  - `a/y.pdf` → `subfolder=""`

#### D2. `_import_files_for_project`（task #10 批量导入）

`_collect_files_to_import` 返回值从 `list[Path]` 改为 `list[PendingFile]`，`subfolder` 从 `src.parent.relative_to(folder)` 的 POSIX 形式得来。

#### D3. `_drop_create_project` / `_drop_into_project` / `_on_dropped_on_files_table`

接收 `list[PendingFile]`，调 `_import_one` 时把 `subfolder` 透传给 `add_file()`。

#### D4. `Library.import_copy` **不改**

物理层继续拍平，不加 `subpath` 参数。`subfolder` 纯粹是数据库字段，与物理存储无关。

### E. 导入导出 round-trip

导出：`exporter.py` 的 `project.json` 中每条文件记录增加 `subfolder` 字段。
导入：`importer.py` 读取 `subfolder` 写入 `files` 表 → 项目内的目录树完整还原。
前提是导出时选了 task #28 的"保留目录结构"（`preserve_structure=true`），否则 `files/` 是拍平的，但 `subfolder` 元数据仍在 `project.json` 里，导入时照样能重建树。

---

## T2：文件表改树形

### A. 控件替换

`MainWindow.tbl_files: QTableWidget` → `QTreeWidget`：

- 列定义保持 `FILES_COLUMNS`（文件名 / 说明 / 类型 / 存储），文件名列天然支持折叠箭头
- 列可见性 / 列宽偏好（task #01）直接迁移（`QTreeWidget` 也有 `header()`）
- 拖放：`FilesTableDnD` 改造为支持 `QTreeWidget`

### B. 树形数据组装

```python
def _populate_files_tree(self, files: list[FileItem]) -> None:
    """按 files.subfolder 分组建树。

    - subfolder="" → 顶层文件节点
    - subfolder="ML" → 📁 ML/ 下挂文件
    - subfolder="ML/NLP" → 📁 ML/ → 📁 NLP/ 下挂文件
    - 两种模式（仓储/链接）展示方式完全一致
    """
```

**组装算法**：
1. 收集所有唯一的 `subfolder` 值
2. 对每个 `subfolder` 按 `/` 分段，逐级创建/复用 `QTreeWidgetItem` 目录节点
3. 将文件挂到对应目录节点下

目录节点（`QTreeWidgetItem`）：
- 文件名列显示 `📁 目录名/`
- 其它列留空
- 不可选中执行；可展开/折叠

文件节点：
- 文件名列显示 `<icon> <basename>`
- 其它列展示同现在（kind / storage / label）
- 双击 / 右键行为不变

### C. 默认展开策略

- 项目首次显示：默认全部展开
- 不记忆折叠状态（避免额外持久化键）

### D. 排序

- 同级内：目录先（按名字），文件后（按 `ord` / 名字）
- 不允许全局按列排序

### E. 拖放

- 外部文件拖到树任意位置 → `subfolder=""`（加到顶层）
- 不实现"拖到目录节点下"（留给未来手动整理 task）

### F. 右键「添加文件」的 subfolder 归属

- 无选中 / 选中的是文件节点 → 新文件 `subfolder=""`（顶层）
- 选中的是目录节点 → 新文件 `subfolder=` 该目录节点对应的 subfolder 路径
  - 例：选中 `📁 NLP/`（subfolder="ML/NLP"）→ 添加的文件 `subfolder="ML/NLP"`

---

## T3：删除 / 清理

### A. 删文件后清理逻辑空目录

删除文件后，检查同一项目内是否还有其它文件的 `subfolder` 以该目录为前缀。若无，则视为逻辑空目录，在 UI 树上自动消失（数据库里没有额外的目录记录需要清理）。

### B. 目录节点删除

- 选中目录节点 → 二次确认 → 删除该目录下所有文件的数据库记录
- 仓储模式：物理文件连带删除（`Library.remove_relative`）
- 链接模式：只删数据库记录，不动原文件

### C. 混合选择

- 仅文件 / 仅目录：正常处理
- 混合选中：禁止（T2/E 已限制）

---

## 现状审计（要修的点）

| 文件 | 行号 | 现象 |
|---|---|---|
| `app/db.py` | schema v6 | 需新增 v7 迁移，加 `files.subfolder` 列 |
| `app/models.py` | FileItem | 需新增 `subfolder` 字段 |
| `app/repository.py` | add_file/list_files | 需适配 `subfolder` |
| `app/ui/main_window.py::_expand_paths` | ~2410 | 只 `iterdir()` 一层，需返回 `PendingFile` |
| `app/ui/main_window.py::_drop_create_project` | ~2441 | 调用方式需同步改造 |
| `app/ui/main_window.py::_drop_into_project` | ~2422 | 同上 |
| `app/ui/main_window.py::_on_dropped_on_files_table` | ~2252 | 同上 |
| `app/importer.py::_collect_files_to_import` | 437 | 返回值需包含 subfolder |
| `app/importer.py::_import_files_for_project` | 384 | 传 subfolder 给 add_file |
| `app/ui/main_window.py::tbl_files` | 1071 | `QTableWidget` 需换 `QTreeWidget` |
| `app/ui/dnd.py::FilesTableDnD` | - | 类型适配 |
| `app/exporter.py` | - | 导出加 subfolder |
| `app/importer.py` | - | 导入读 subfolder |

## 校验

### T1
- 拖入 `a/sub/x.pdf + a/y.pdf` → `files` 表中 `subfolder` 分别为 `"sub"` 和 `""`
- 仓储模式下物理目录仍是 `library/<pid>/x.pdf` + `library/<pid>/y.pdf`（拍平）
- 链接模式下 `subfolder` 也被正确填充
- 散文件拖入 → `subfolder=""`
- task #10 批量导入 → 每个文件的 `subfolder` 正确反映源目录结构

### T2
- 单文件项目（`subfolder` 全为 `""`）：树根下直接列文件，体验等同表格
- 多目录项目：根下出现 `📁` 节点，可展开
- 列宽 / 列可见性切换正常
- 外部文件拖到树任意位置 → 加到顶层（`subfolder=""`）
- 切项目时清空树

### T3
- 删除某目录下最后一个文件 → UI 树上该目录节点自动消失
- 选中目录节点删除 → 弹确认 → 整棵删除

## 风险

- **Schema 迁移**：新增列是 `NOT NULL DEFAULT ''`，对现有数据零影响，不需要回填
- **QTreeWidget 性能**：几千文件 + 深层嵌套时可能慢；本期不加虚拟化，实测再议
- **跨平台路径分隔符**：`subfolder` 统一用 POSIX `/`，导入时 `Path.as_posix()` 转换
- **历史项目兼容**：已有文件 `subfolder=""`，全部落在顶层，无需迁移
- **导出格式变更**：`project.json` 新增 `subfolder` 字段，旧版导入时忽略未知字段，向后兼容

## 依赖

- **强依赖**：task #10（已完成）—— 现有导入流程是 T1 的修改基础
- **弱依赖**：task #01（已完成）—— 列可见性框架沿用，T2 改控件后适配
- **服务于**：task #04（系统/配置文件折叠）—— T2 完成后 #04 只需对 `_meta`/`.git` 等目录设特殊 subfolder
- **不冲突**：task #14 备份恢复 —— `library/<pid>/` 仓储目录树照常 zip

## 工作量拆分

| 子项 | 估算 |
|---|---|
| T1 数据层（schema + model + repo + 导入填充 + 导出兼容） | 0.5 天 |
| T2 控件替换 + 树组装 + 拖放/列偏好适配 | 0.5 天 |
| T3 目录节点删除确认 + 逻辑空目录清理 | 0.2 天 |
| selftest（task17_subfolder_import.py） | 0.2 天 |
| 验收测试 + README/CHANGELOG 同步 | 0.1 天 |
| **合计** | ~1.5 天 |

## 验收

- [x] T1：拖入含子目录的文件夹，`files.subfolder` 正确填充
- [x] T1：散文件、单层目录、task #10 批量导入四条路径 `subfolder` 都正确
- [x] T1：物理存储不变（仓储拍平、链接原位）
- [x] T2：有子目录的项目显示 `📁` 折叠节点
- [x] T2：只有顶层文件的项目展示等同表格
- [x] T2：列宽/列可见性持久化正常
- [x] T2：外部文件拖到树任意位置都加到顶层
- [x] T3：删除目录下最后一个文件后目录节点消失
- [x] T3：删除目录节点弹确认，整棵删除
- [x] selftest 全绿（24 断言）
- [x] CHANGELOG / TODO / tasks/README 同步
