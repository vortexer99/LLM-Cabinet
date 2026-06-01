# 17 · 子文件夹导入修复 + 文件表改树形展示

**工作量**：S+S+S
**优先级**：T1 = P0（数据丢失级 bug 修复）, T2 = P1, T3 = P1
**状态**：T1 待做 · T2 待做 · T3 待做

## 来源

用户反馈："子文件夹目前是没有被导入的"。

定位结果（task #10 已闭环但有遗漏）：

1. **bug 1（数据丢失）**：`MainWindow._expand_paths()` 只 `iterdir()` 一层，
   拖入"含子目录的文件夹"时**子目录里的文件全部静默丢弃**，无任何提示。
   覆盖路径：单目录拖入、多目录拖入选「合并」、目录拖到现有项目卡片
   （`_drop_into_project`）。
2. **bug 2（结构丢失）**：task #10 的 `_import_files_for_project` 走
   `folder.rglob("*")` 能拿到子目录里的文件，但仓储模式下 `library.import_copy`
   把所有文件**拍平**到 `<project>/` 一层，靠重名加 `_1` 防撞，原始目录结构被
   丢弃；用户看到的就是"几十个文件堆在同一项目里"，无法对应回原始组织方式。
3. **UI 配套缺失**：文件表现在用 `QTableWidget`，无树形概念；即使数据层支持
   子路径，用户也看不出层级。

物理层面上，仓储模式（`Library.import_copy`）的本意就是按项目目录树存储；
现在的拍平实现是**对物理结构的浪费**，且和未来 task #04（项目内系统/配置文件
折叠）天然冲突。

## 目标

把"导入一个含子目录的文件夹 → 项目里完整保留目录结构"做透：

1. **数据层**：导入路径递归收集子目录文件；仓储模式保留相对子路径写入磁盘
   （`<project>/sub/foo.pdf`）；`files.path` 列存带子路径的相对路径
   （已经支持，本期不动 schema）
2. **UI 层**：文件表从 `QTableWidget` 改为 `QTreeWidget`，按目录折叠展示；
   单文件项目（无任何子目录）仍然像扁平表一样使用，零额外操作成本
3. **闭环验证**：拖入 `a/sub/x.pdf + a/y.pdf` → 项目里看到树
   `📁 sub/ → 📄 x.pdf` + `📄 y.pdf`，仓储模式下 `library/<pid>/` 物理目录
   也保持同结构

## 范围与边界

| 子任务 | 内容 | 优先级 | 工作量 |
|---|---|---|---|
| **T1** | **数据层修复**：`_expand_paths` 递归 + 给所有"目录展开"路径加上保留相对路径的能力；`Library.import_copy` 接受可选 `subpath: str=""` 参数；批量导入路径（`_import_files_for_project`）直传子路径 | P0 | S |
| **T2** | **UI 改树形**：`tbl_files` 由 `QTableWidget` 换 `QTreeWidget`；按 `files.path` 中的目录前缀分组折叠；列定义、拖放、右键菜单、列可见性偏好（task #01）保留 | P1 | S |
| **T3** | **删除 / 移动操作的目录联动**：删文件后清理空父目录；项目移动 / 删除时整棵子树连带；`Library.remove_relative` 增 `prune_empty_dirs=True` 选项（默认开） | P1 | S |

T1 单独可上线（哪怕 UI 仍扁平，至少不丢数据）；T2 / T3 依赖 T1 的数据层。

**不做（本任务内）**：

- **手动整理项目内文件结构**：在文件表里拖动文件改子目录归属、新建/重命名子目录
  等。本期数据层支持就够了，UI 操作留给单独 task
- **历史项目数据迁移**：已经导入过的拍平文件保持现状（重新导入即可恢复结构）
- **改 `files.path` 列语义**：仍是"相对仓库根的 POSIX 路径"或绝对路径
  （`is_relative` 决定），没有 schema 变更
- **task #04（系统/配置文件折叠）**：是树形展示的更高级用法，独立排期

## T1：数据层修复

### A. `MainWindow._expand_paths` 递归

```python
@staticmethod
def _expand_paths(paths: list) -> list[tuple[str, str]]:
    """把混合路径展开为 [(absolute_src, relative_subpath), ...]。

    - 文件 → (绝对路径, "")  # 顶层散文件
    - 目录 → 递归收集所有文件，subpath 为相对该目录的 POSIX 子路径
            （如 "sub/foo.pdf"；目录本身的文件 subpath="foo.pdf"
            也保持文件名，但加入项目时会落到 <project>/foo.pdf）
    """
```

⚠ **接口签名改了**：从 `list[str]` 变成 `list[tuple[str, str]]`。
所有调用方（`_drop_create_project` / `_drop_into_project` /
`_handle_multi_folder_drop` 的合并分支）都要同步改造。

简化方案：返回一个新数据类 `PendingFile(src: Path, rel_subpath: str)`，
比裸 tuple 易读。

### B. `Library.import_copy` 接受可选 subpath

```python
def import_copy(self, project_id: int, src: Path, *, subpath: str = "") -> str:
    """把 src 复制进 project 目录，返回相对仓库根的 POSIX 路径。

    Args:
        subpath: 相对项目根的子路径（不含文件名），如 "sub" / "a/b"。
            默认 "" = 顶层（兼容旧调用）。
            会自动 mkdir(parents=True, exist_ok=True)。
    """
```

同名冲突策略：
- 同一 subpath 下文件重名 → 仍加 `_1` 序号（兜底；正常导入路径不会触发，
  因为子路径已经隔开了大多数同名场景）
- 跨 subpath 的同名文件 → 物理上是两个不同路径，**不算冲突**

### C. `_import_files_for_project`（task #10 路径）传 subpath

`_collect_files_to_import` 现在返回 `list[Path]`，改为返回
`list[tuple[Path, str]]`（`(src, subpath_rel)`），其中 `subpath_rel` 是
`src.parent.relative_to(folder)` 的 POSIX 形式。

链接模式不需要 subpath（仍存绝对路径）。

### D. `_drop_create_project` 路径同步

接收 `list[PendingFile]` 而不是 `list[str]`；调 `_import_files` 时把每条的
subpath 传给 `_import_one`；后者在 storage="copy" 时把 subpath 透给
`library.import_copy`。

### E. 默认标题逻辑保持

`_drop_create_project` 现在用第一个目录的名字作默认标题，本期不变。

## T2：文件表改树形

### A. 控件替换

`MainWindow.tbl_files: QTableWidget` → `QTreeWidget`：

- 列定义保持 `FILES_COLUMNS`（文件名 / 说明 / 类型 / 存储），文件名列
  天然支持目录折叠箭头
- 列可见性 / 列宽偏好（task #01）的持久化逻辑直接迁移（`QTreeWidget` 也有
  `header()` / `setColumnHidden()` / `setColumnWidth()`）
- 拖放：`FilesTableDnD` 改造为支持 `QTreeWidget`（基类 `QAbstractItemView`
  接口一致；只需改类型注解 + drop event 兼容）

### B. 树形数据组装

```python
def _populate_files_tree(self, files: list[FileItem]) -> None:
    """按 files.path 中的目录前缀分组成树。

    - 链接模式（is_relative=False）的文件：取绝对路径的 dirname 作为 group
      （但跨项目可能有不同前缀，统一归到顶层；不为链接文件造目录节点）
      → 决策：链接模式文件**全部归到顶层**，不参与目录折叠（它们物理上不
      在仓储里，没有"项目内子目录"的概念）
    - 仓储模式（is_relative=True）的文件：path 形如 "<pid>/sub/foo.pdf"，
      去掉 "<pid>/" 前缀后按 "/" 分段组装成树节点
    """
```

目录节点（`QTreeWidgetItem`）：
- 文件名列显示 `📁 子目录名/`
- 其它列留空（说明 / 类型 / 存储）；type icon 用文件夹
- 不可选中执行（双击不会触发 open）；可展开/折叠
- 右键菜单：在该目录下"添加文件"快捷入口（本期可省，T3 再加）

文件节点：
- 文件名列显示 `<icon> <basename>`，跟现状一致
- 其它列展示同现在（kind / storage / label）
- 双击 / 右键打开行为不变

### C. 默认展开策略

- 项目首次显示：默认全部展开（一眼看清结构）
- 用户折叠某个目录后，切换走再回来记住状态？本期不做（每次进入都全展开），
  避免引入额外持久化键

### D. 排序

- 同级内：目录先（按名字），文件后（按 `ord` / 名字）
- 不允许全局按列排序（QTreeWidget 的内置 sort 会打散树结构）；表头点击列改成
  "调整列宽辅助"语义即可（现状本来也没启用排序）

### E. 选择 / 多选

- 单选：跟现状一致
- 多选：仅跨**同级**；不允许混合目录与文件（避免"删除选中"语义模糊）。
  本期实现可以简化为：选中目录节点时清空文件多选

## T3：删除 / 清理目录联动

### A. `Library.remove_relative` 加 prune

```python
def remove_relative(self, rel_path: str, *, prune_empty_dirs: bool = True) -> None:
    """删除仓库内文件；忽略不存在错误。

    prune_empty_dirs: 删完文件后，若父目录变空则向上递归 rmdir
        直到遇到非空目录或项目根 <pid>/。默认 True。
    """
```

### B. `Repository.delete_project` 一并清空

现在 `delete_project` 已经把整个项目目录删掉（task #14），无需改动。

### C. 文件表"删除选中"

- 只选了文件 → 删文件 + 上溯清空目录
- 只选了目录节点 → 二次确认对话框，列出该目录下所有文件，确认后整棵删
- 跨混合 → T2/E 已经禁了

## 现状审计（要修的点）

| 文件 | 行号 | 现象 |
|---|---|---|
| `app/ui/main_window.py::_expand_paths` | ~2410 | 只 `iterdir()` 一层 |
| `app/ui/main_window.py::_drop_create_project` | ~2441 | 调 `_expand_paths` 后传 `list[str]` |
| `app/ui/main_window.py::_drop_into_project` | ~2422 | 同上 |
| `app/ui/main_window.py::_on_dropped_on_files_table` | ~2252 | 同上 |
| `app/library.py::import_copy` | 28 | 无 subpath 参数 |
| `app/importer.py::_collect_files_to_import` | 437 | 返回 `list[Path]` 无相对路径信息 |
| `app/importer.py::_import_files_for_project` | 384 | 调 `import_copy` 不传 subpath |
| `app/ui/main_window.py::tbl_files` | 1071 | `QTableWidget` 需换 `QTreeWidget` |
| `app/ui/dnd.py::FilesTableDnD` | - | 类型适配 |
| `app/ui/files_table_columns.py` | 32 | `default_width` 可能要为文件名列加大点 |

## 校验

### T1
- 把 `<dir>/sub/x.pdf` + `<dir>/y.pdf` 的目录拖到 DropZone（合并模式）
  → 项目里有 2 个文件（之前是 1 个）
- 仓储模式下，`library/<pid>/` 实际目录里能看到 `sub/x.pdf` + `y.pdf`
- 链接模式下，两个文件都在 `files` 表，`is_relative=False`，绝对路径正确
- 把目录拖到**已有项目**卡片上（`_drop_into_project`）→ 子目录文件被加进项目
- task #10 批量导入：每个父目录里的子目录结构在仓储里被保留

### T2
- 单文件项目：树根下直接列文件，体验等同表格
- 多目录项目：根下出现 `📁` 节点，可展开
- 列拖宽：所有列保持可调；列可见性切换正常
- 拖放：把外部文件拖到树中央 → 加到项目顶层（不进任何子目录节点）
- 切项目时记得清空树（避免上一项目的节点残留）

### T3
- 删除子目录里的最后一个文件 → 仓储里对应空目录被自动清理
- 选中目录节点 → 删除 → 弹确认 → 整棵删除，仓储侧物理目录消失

## 风险

- **接口签名变更扩散**：`_expand_paths` 从 `list[str]` → `list[PendingFile]`
  会触达多个调用点；要逐一审计避免遗漏
- **QTreeWidget 性能**：项目里有几千文件且嵌套很深时构建慢；本期不做虚拟化，
  若实测有问题再加
- **跨平台路径分隔符**：Windows 上 `Path.relative_to(...)` 默认输出 `\`；
  存到 `files.path` 前一律转 POSIX `/`（`Path.as_posix()`），与现有代码
  约定一致
- **历史项目兼容**：已经导入的拍平文件 path 形如 `<pid>/foo.pdf`（无子路径），
  树形组装时它们落在顶层；不需要数据迁移
- **拖放的层级语义**：把外部文件拖到某个目录节点上是"加到该目录"还是
  "加到项目顶层"？本期统一**忽略 drop 位置，全部加到顶层**，避免歧义；
  未来若做"手动整理结构" task 再细化

## 依赖

- **强依赖** task #10（已完成）：`Library.import_copy` / `_import_files_for_project`
  的当前实现是 T1 的修改基础
- **弱依赖** task #01：列可见性框架沿用，T2 改控件后接口要适配
- **服务于** task #04（项目内系统/配置文件折叠）：T2 完成后 #04 只需在树
  组装阶段对 `_meta` / `.git` 等目录加折叠规则
- **不冲突** task #14 备份恢复：`library/<pid>/` 仓储目录树照常 zip

## 工作量拆分

| 子项 | 估算 |
|---|---|
| T1 数据层（`_expand_paths` + `import_copy.subpath` + 批量路径） | 0.4 天 |
| T2 控件替换 + 树组装 + 拖放/列偏好适配 | 0.5 天 |
| T3 prune 空目录 + 删除目录节点的二次确认 | 0.2 天 |
| selftest（task17_subfolder_import.py） | 0.2 天 |
| 验收测试 + README/CHANGELOG 同步 | 0.1 天 |
| **合计** | ~1.4 天（S+S 偏上） |

## 验收

- [ ] T1 数据层：拖入含子目录的文件夹，子目录文件**全部**进入项目；
      仓储模式下物理目录树结构与源目录一致
- [ ] T1 兼容：散文件拖入、单层目录拖入、task #10 批量导入（合并/分别）
      四条路径都不丢文件
- [ ] T2 树形：项目内有子目录时显示 `📁` 折叠节点，可展开；
      只有顶层文件的项目展示等同表格
- [ ] T2 列偏好：列宽 / 列可见性持久化与现状一致
- [ ] T2 拖放：外部文件拖到树任意位置 → 都加到顶层（不进子目录节点）
- [ ] T3 删除文件后空目录被清理；删除目录节点弹确认
- [ ] selftest `task17_subfolder_import.py` 全绿
- [ ] CHANGELOG / TODO / tasks/README 同步

## 待澄清（写代码前需要敲定）

> 这些问题不影响 task 卡的整体形状，但写代码前需要明确：

1. **目录节点的多选与文件混选**：T2/E 暂定"禁混选"。如果你倾向"多选目录
   就等价于多选目录下所有文件"，写代码前告诉我。
2. **链接模式文件的归属**：T2/B 暂定"链接模式文件全部归到顶层"。如果你
   觉得链接文件也应按其原始路径的某个层级折叠（如按盘符 / 一级目录分组），
   告诉我。
3. **现存项目的回填**：本期默认"已导入的拍平文件保持现状"。如果你想给
   一个"重新导入此项目"的右键菜单触发一次性整理，可以纳入 T3。
