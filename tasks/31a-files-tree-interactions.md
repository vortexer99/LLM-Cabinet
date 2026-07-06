# 31a · 树形视图：拖动改位置 + 同级排序 + 新建文件夹 + F2 重命名

**工作量**：M
**优先级**：P1
**状态**：🚧 新建文件夹/重命名 完成（v0.6 未发布）

## 来源

`TODO.md → 🎨 UI / 交互`：让项目内的文件表用起来更像 Windows 资源管理器——**点列头排序**、**拖动改变文件位置**（同级改顺序 / 拖进别的文件夹）、**新建文件夹**、**F2 重命名**。要求在**正常视图**和**独立窗口（展开）视图**里都生效。

> 原 task #31 拆分：**本卡（#31a）只做"树形视图内的资源管理器式交互"**。
> "扁平视图模式 + 大小/时间列 + 列任意排序"已拆到 [task #31b](./31b-files-table-flat-view.md)。
> 视图模式切换 + 持久化由 #31b 负责（因为只有 #31b 引入第二种视图，本卡只动树形）。

**现状盘点（动手前先读现有代码）**：

| 能力 | 现状 | 涉及 |
|------|------|------|
| 文件表控件 | `self.tbl_files = QTreeWidget()`，按 `subfolder` 分组成 📁文件夹节点 + 文件叶子（task #17） | `main_window._populate_files_tree` |
| 排序 | **固定**：`_sort_files_tree` 让文件夹在前（按名）、文件在后（按 `ord`）；**没开列点击排序** | `main_window._sort_files_tree` |
| 拖动 | `FilesTableDnD` 是 `DropOnly` + `setDragEnabled(False)`，**只用于从外部拖文件进来添加**，没有内部拖动改序 | `app/ui/dnd.py` |
| 顺序存储 | `files.ord` 列；新加文件给 `ord=10_000` | `files` 表 / `repo.update_file` |
| 列 | 固定 4 列：文件名 / 说明 / 类型 / 存储 | `files_table_columns.COLUMNS` |
| 独立窗口 | task #02：把**同一个** `_files_panel` 控件搬进 `QDialog`，不是另建控件 | `main_window._detach_files_panel` |
| 重命名 label | ✅ 表格行内编辑（双击说明列） | 现有 QTreeWidget 编辑 |
| 物理文件名重命名 | ❌ 无 | — |
| 新建空 subfolder | ❌ 无（subfolder 只能靠导入/未来的拖动产生） | — |

> **关键利好**：独立窗口复用同一个 `tbl_files` 实例，所以排序/拖动只要做在这个控件上，**两个视图天然都生效**——不需要写两份。只需验证 detach 前后拖放/信号仍正常。

> **关键约束**：`repo.update_file` 现状**只更新** `path/is_relative/label/kind/ord`，**不更新 `subfolder`**。"拖进别的文件夹"要改 `subfolder`，需扩展 `update_file` 或新增 `repo.set_file_subfolder`（见实现要点）。

---

## 目标

1. 文件表支持**点列头排序**（升/降序切换），且不破坏 #17 的文件夹分组语义（仅同级文件排序）。
2. 文件表支持**拖动改位置**：同一文件夹内改顺序、拖到另一个文件夹节点、拖到顶层。
3. 顺序/分组的改动**落到数据库**（`ord` / `subfolder`），刷新后保持。
4. 支持**新建空文件夹**（subfolder）和**F2 重命名**（label / 物理文件名两种）。
5. 正常视图与独立窗口视图行为一致。

---

## 功能拆解

### T1 · 列点击排序（同级）

#### 交互
- 点列头 → 按该列排序；再点同列 → 升/降序切换（表头显示排序箭头）。
- 可排序列：**文件名 / 说明 / 类型 / 存储**（现有 4 列）。
- 额外保留一个**「自定义顺序」**状态（= 按 `files.ord`，也就是用户拖出来的顺序）；这是默认状态，也是拖动后回归的状态。

#### 与文件夹分组的关系（重要）
- **文件夹节点恒在前、按名称排列**，不受列排序影响（与资源管理器一致：文件夹永远在文件上面）。
- 列排序**只对同一层级的文件叶子**生效，逐层应用。
- 实现上**不直接用** `QTreeWidget.setSortingEnabled(True)`（它会把文件夹和文件按列值混排）；而是扩展现有 `_sort_files_tree(column, order)`：每个父节点内先放文件夹（按名），再放文件（按指定列 + 升降序）。

#### 持久化
- 排序状态（列 key + 升/降 或「自定义」）按项目存到 `project_settings`，键 **`files_table_sort_tree`**（与 #31b 的 `files_table_sort_flat` 各自独立）。

> **若 #31b 已落地**：本卡只负责"树形视图"下的列排序状态；切到扁平视图时，由 #31b 用 `files_table_sort_flat` 独立管理。

### T2 · 拖动改位置

#### 交互（资源管理器式）
- **同一文件夹内上下拖** → 改变该文件在本层的顺序。
- **拖到某个 📁 文件夹节点上** → 移动到该文件夹（改 `subfolder`）。
- **拖到顶层空白 / 顶层文件之间** → 移到顶层（`subfolder=""`）。
- 多选拖动：一次移动多个文件（保持它们相对顺序）。

#### 落库
- 改顺序：重算受影响层级里各文件的 `ord`（重新编号 0,1,2…），逐个 `repo.update_file`。
- 跨文件夹移动：同时更新 `subfolder`（需 `update_file` 支持，见实现要点）+ 目标层级的 `ord`。
- 拖动一旦发生 → 排序状态切回「自定义顺序」（因为用户在手动定序）。

#### 与外部拖入的共存（关键）
现状 `FilesTableDnD` 是 `DropOnly`，负责"从资源管理器拖文件进来添加"。本卡要让控件**同时**支持内部移动：
- 把拖放模式改成 `DragDrop` + `setDragEnabled(True)`。
- 在 `dropEvent` 里分流：
  - `mimeData().hasUrls()`（来自外部）→ 走现有"添加文件"逻辑（`files_dropped`）。
  - 否则（内部 item 移动）→ 走本卡的"改顺序/移动文件夹"逻辑。

### T3 · 新建空 subfolder

#### 入口
文件表空白处右键 / 文件夹节点右键 → **📁 新建文件夹…**

#### 交互
1. 弹输入框（QInputDialog）输入文件夹名
2. 名称校验：非空、不含 `/`（subfolder 内部不允许嵌套层级 / 在 #17 设计里 `/` 是路径分隔符）、同级不重名
3. 校验通过 → 创建一个"占位 file"？ → **不创建**。subfolder 是依附于文件的字符串，**没有文件的空 subfolder 在 DB 层无意义**。

#### 落库决策（关键）

两种实现：

**A. 临时虚拟节点**（不落库）
- 新建的空文件夹只是 UI 层的 `QTreeWidgetItem`，刷新文件表（如切到别的项目再切回来）就消失
- 用户必须立刻把文件拖进去才能"固化"
- 实现简单，零 schema 改动

**B. 项目设置里存"显式 subfolder 列表"**
- 加 `project_settings` 键 `explicit_subfolders`，存用户主动新建的空 subfolder 名
- 渲染文件表时合并：来自文件的 subfolder ∪ 显式 subfolder
- 删除该 subfolder 下最后一个文件时，是否自动清除？需要 UI 提示

**默认决定：B**。理由：用户的"新建文件夹"操作如果刷新就消失，体验崩。代价是要加一个 `project_settings` 键，但比 schema 改动便宜得多。

#### 校验
- 新建后 subfolder 出现在文件树
- 切到别的项目再切回来，空 subfolder 仍在
- 拖文件进去 → 文件 `subfolder=新文件夹`
- 把空 subfolder 下唯一文件拖走 → 文件夹仍在（B 方案下保留，与"必须显式删除"一致）
- 文件夹节点右键 → **🗑 删除空文件夹**（仅在 0 文件时启用，从 `explicit_subfolders` 移除）

### T4 · F2 重命名

#### 交互（资源管理器式）
- 选中文件 → 按 F2 → 进入重命名编辑模式
- 默认改 **label**（备注名）—— 跟双击说明列一致
- **Shift+F2** → 改 **物理文件名**（仓储/链接都支持）
- 文件夹节点 F2 → 重命名 subfolder（批量更新该层所有文件的 subfolder 字段）

#### 物理文件名重命名（Shift+F2）

| 文件类型 | 操作 |
|----------|------|
| 📦 仓储 | `library.root / 旧 rel` → `library.root / 新 rel`；改 `f.path` |
| 🔗 链接 | `Path(old_path).rename(new_path)`；改 `f.path` |
| 跨项目引用（#32） | 警告：物理改名会影响所有引用此路径的项目 → 选"全部同步"或"仅本项目"；后者实质上 = 替换链接目标（#29 T3b） |

- 新名校验：非空、合法字符、同目录无重名
- 失败（权限/占用）→ 撤销 UI 改动，弹错误
- `kind` 不自动改（与 #29 T3b 一致）

#### subfolder 重命名

- 文件夹节点 F2 → 输入新名 → 校验（同级不重名 / 不含 `/`）
- 落库：`UPDATE files SET subfolder=? WHERE project_id=? AND subfolder=?`（事务）
- 若该 subfolder 在 `explicit_subfolders` 里也要同步改名

---

## 实现要点

### A. 排序：扩展 `_sort_files_tree`

```python
def _sort_files_tree(self, *, column_key: str | None = None, descending: bool = False):
    """每个父节点内：文件夹在前（按名），文件在后。
    column_key=None → 文件按 files.ord（自定义顺序，默认）。
    column_key 指定 → 文件按该列文本排序（升/降）。
    """
```
- 表头 `sectionClicked` 信号 → 切换 `column_key`/`descending` → 重排 + 存 `project_settings`。
- 文件节点要能取到对应 `FileItem`（现有树节点已用 `setData(UserRole, ...)` 存了 file id / 对象，复用）。

### B. 拖动：QTreeWidget 拖放

- `self.tbl_files.setDragEnabled(True)`；`setDragDropMode(QAbstractItemView.DragDrop)`；`setSelectionMode(ExtendedSelection)`（支持多选拖）。
- `FilesTableDnD` 改造或在 `tbl_files` 子类里重写 `dropEvent`：先判 `hasUrls` 分流（见上）。
- 内部移动落库后**重新 `_populate_files_tree`**（最稳，避免手工挪 QTreeWidgetItem 出错）。

### C. Repository：支持改 `subfolder`

现状 `update_file` 不写 `subfolder`。二选一：
- **改 `update_file`**：SQL 加 `subfolder=?`（注意排查所有调用方，确保它们传的 `FileItem.subfolder` 是期望值——避免别处调用无意中清空 subfolder）；或
- **新增 `repo.set_file_subfolder(file_id, subfolder)`**（更安全、零副作用，推荐）。
- T4 subfolder 改名批量操作：加 `repo.rename_subfolder(project_id, old, new)`，事务一次性更新。

### D. 项目设置：explicit_subfolders

```python
# project_settings 表新键 "explicit_subfolders"，值是 JSON list
# 读：repo.get_project_setting(pid, "explicit_subfolders", "[]")
# 写：用户新建空 subfolder / 删除空 subfolder / subfolder 改名时同步
```

### E. F2 与现有编辑的冲突

- 现状 `tbl_files` 的"说明"列允许双击编辑 → 改 label
- F2 默认进入"说明"列编辑模式，与双击行为一致
- Shift+F2 拦截 keypress → 弹自定义对话框（QInputDialog 不够，需要校验提示）改物理文件名

### F. 两个视图一致性

- 因为独立窗口（#02）搬的是同一个 `_files_panel`，排序/拖动/F2 **自动**在两处生效。
- 需验证：detach → attach 往返后，表头点击信号、拖放、F2 仍正常。

---

## 校验

### T1 排序
- [ ] 点「文件名」列头 → 同级文件按名升序；再点 → 降序；表头显示箭头
- [ ] 点「类型 / 存储 / 说明」列头 → 各自正确排序
- [ ] 任何排序下，📁 文件夹节点都恒在同级文件之前、按名称排列
- [ ] 切回「自定义顺序」→ 文件恢复按 `ord` 排列
- [ ] 排序状态按项目持久化：切到别的项目再切回来，排序状态保留
- [ ] 独立窗口视图里排序行为与主窗口一致

### T2 拖动
- [ ] 同文件夹内拖动改顺序 → 刷新后顺序保持（`ord` 已更新）
- [ ] 拖文件到 📁 文件夹节点 → 文件进入该文件夹（`subfolder` 已更新）
- [ ] 拖文件到顶层 → `subfolder` 变 `""`
- [ ] 多选拖动 → 多个文件一起移动，相对顺序保持
- [ ] 从资源管理器拖外部文件进来 → 仍是"添加文件"（不被误判为内部移动）
- [ ] 拖动后排序状态自动切到「自定义顺序」
- [ ] 独立窗口视图里拖动行为与主窗口一致；detach/attach 往返后拖放仍正常

### T3 新建空文件夹
- [ ] 空白处右键 → 新建文件夹 → 输入名 → 出现在文件树
- [ ] 切到别的项目再切回来 → 空文件夹仍在（B 方案）
- [ ] 同级不重名校验生效
- [ ] 含 `/` 的名字被拒绝
- [ ] 文件夹下唯一文件拖走 → 文件夹仍在
- [ ] 空文件夹右键 → 删除空文件夹 → 从 `explicit_subfolders` 移除，文件树消失
- [ ] 非空文件夹的"删除"项灰显（不允许误删带文件的目录）

### T4 F2 重命名
- [ ] 选中文件按 F2 → 进入 label 编辑（与双击一致）
- [ ] Shift+F2 → 弹物理文件名重命名对话框，仓储/链接都能改名
- [ ] 改名失败（权限/同名）→ 弹错误，UI 不脏
- [ ] 文件夹节点 F2 → 输入新名 → 该层所有文件 `subfolder` 同步更新
- [ ] 跨项目引用（#32 完成后）的物理改名 → 弹"影响多项目"警告

---

## 依赖

- **强依赖**：task #17（`subfolder` + 树形控件）✅ —— 排序/拖动/新建文件夹都建立在这棵树上
- **联动**：task #02（文件列表独立窗口）✅ —— 共享同一控件，需保证两视图一致
- **协同**：task #31b（扁平视图模式）—— 视图切换的持久化键由 #31b 落地，本卡只负责树形视图内的状态
- **协同**：task #32（跨项目引用）—— T4 物理改名要警告多引用
- **相邻**：task #01（列可见性/列宽偏好）✅ —— 排序偏好与列偏好都存 `project_settings`，可参考其持久化写法
- **触及**：`repository.update_file` / `files` 表（需支持改 `subfolder`）

---

## 工作量拆分

| 子项 | 估算 |
|---|---|
| T1 列点击排序（扩展 _sort_files_tree + 表头交互 + 持久化） | 0.4 天 |
| T2 拖动改位置（DragDrop 分流 + 同级改序 + 跨文件夹移动） | 0.6 天 |
| T3 新建/删除空 subfolder（UI + explicit_subfolders 持久化） | 0.3 天 |
| T4 F2 重命名（label / 物理名 / subfolder 三种） | 0.5 天 |
| Repository 改 subfolder/ord 支持 + rename_subfolder + 落库 | 0.3 天 |
| 两视图一致性验证 + 验收测试 | 0.4 天 |
| **合计** | ~2.5 天（M+） |

---

## 后续扩展

- **键盘可达性**：方向键移动 + Enter 重命名 + Delete 删除（资源管理器全套键盘操作）
- **复制粘贴 ord/subfolder**：复用系统剪贴板的 mime 类型
- **拖出到外部**：拖文件到资源管理器 = 导出（#28 的轻量入口）

---

## 待澄清

> 卡片正文已按"默认决定"写成可执行状态；**若不同意，请在我编码前告知**。

1. **空文件夹是否持久化**
   - 默认决定：**B 方案**（`explicit_subfolders` 存到 `project_settings`，刷新仍在）。
   - 若觉得太重，可以走 A 方案（虚拟节点，下次刷新消失，用户必须立刻拖文件固化），但体验差。

2. **F2 默认改哪个**
   - 默认决定：F2 = label，Shift+F2 = 物理文件名。
   - 资源管理器里 F2 就是改物理文件名，若你希望对齐资源管理器，告诉我调换两个快捷键。

3. **subfolder 重命名后跨项目引用的影响**
   - 默认决定：subfolder 只影响本项目（subfolder 本来就是项目维度的），无跨项目影响。
   - 物理文件改名才有跨项目影响，由 T4 的多引用警告处理。
