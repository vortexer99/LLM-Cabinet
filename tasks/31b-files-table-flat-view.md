# 31b · 扁平视图模式 + 大小/时间列 + 列任意排序

**工作量**：M
**优先级**：P1
**状态**：✅ 完成（v0.6 未发布）

## 来源

`docs/file-handling.md` 评审：用户希望在树形视图（带 subfolder 分组）之外，还能切到**扁平视图**——所有文件平铺，可按任意列排序（含大小、添加时间），用于"我要找最大的文件""我要看最近加的文件"这类跨 subfolder 的浏览任务。

> 原 task #31 拆分：**本卡（#31b）只做"扁平视图 + 新增列 + 视图切换"**。
> "树形视图内的拖动 / 排序 / 新建文件夹 / F2"已拆到 [task #31a](./31a-files-tree-interactions.md)。

## 现状盘点

| 能力 | 现状 |
|------|------|
| 视图模式 | 只有一种：`QTreeWidget` 按 subfolder 分组（#17） |
| 文件大小 | DB **无大小列**；需 stat 物理文件得到 |
| 添加时间 | `files.added_at` 已有数据，但**未做列** |
| 列定义 | `app/files_table_columns.py` 的 `COLUMNS` 数组固定 4 列 |

## 目标

1. 文件表新增**视图模式切换**：树形 ↔ 扁平。
2. 扁平视图：所有文件平铺，**忽略 subfolder 分组**，可按任意列（含大小、添加时间）排序。
3. 文件表新增两列：**大小**、**添加时间**（在两种视图下都可显示/隐藏，列定义统一）。
4. 视图模式持久化（按项目记忆）。

---

## 功能拆解

### T1 · 新增列

在 `files_table_columns.COLUMNS` 加两列：

| 列 key | 标题 | 数据来源 | 备注 |
|---|---|---|---|
| `size` | 大小 | `Path(library.resolve(f.path, f.is_relative)).stat().st_size`，格式化为 KB/MB | 物理文件不存在 → 显示 "—"；不阻塞 |
| `added_at` | 添加时间 | `f.added_at`（已有 DB 列） | 格式化为 `YYYY-MM-DD HH:MM` |

#### 性能考虑

- `stat` 调用走文件系统 → 上千文件时可能卡顿
- **方案**：列渲染时 stat（按需）+ 缓存到 file row 的 UserData；列隐藏时不 stat
- 大库（10000+ 文件）开"大小"列：进度感知—先显示 "—"，后台 stat 完替换文本（与 #14 一致性检查同类思路）

#### 默认可见性

- `size` 默认隐藏（避免新用户首次打开就触发全表 stat）
- `added_at` 默认隐藏（信息密度低，需要时再开）
- 用户开过一次 → `project_settings` 的 `files_table_columns` 记住，后续保持

### T2 · 扁平视图模式

#### 入口

文件表上方加视图切换按钮（图标 toggle）：
```
[ 🌲 树形 | 📋 扁平 ]
```
- 默认 🌲 树形
- 切到 📋 扁平：所有文件平铺，无 📁 节点，subfolder 字段作为一个**普通列**（可选）显示
- 切回 🌲 → 恢复 #17 的树形

#### 列定义在两种视图下统一

- 同一份 `COLUMNS` 配置
- 扁平视图下可选额外显示 `subfolder` 列（让用户在排序时仍能知道"这个文件原本属于哪个文件夹"）
- 列可见性和列宽**两种视图共享同一配置**（避免维护两份）

#### 排序

- 扁平视图下**直接用 `QTreeWidget.setSortingEnabled(True)`**（无文件夹分组干扰，可放心用 Qt 原生排序）
- 大小/添加时间列要绑定数值排序（用 `setData(Qt.UserRole, raw_int_size)`）
- 排序状态持久化：**树形与扁平各自独立的键**，避免切视图时排序意图被串改
  - 树形：`files_table_sort_tree`（#31a 用）
  - 扁平：`files_table_sort_flat`（本卡用）

#### 拖动改位置在扁平视图下的行为

- 扁平视图下**禁用内部拖动**：因为没有"文件夹"概念可以拖进去，"改 ord"在按列排序时也意义不大
- 仅保留外部拖入（添加文件）
- 想要排序 / 改组：切回树形视图（#31a 那套）

### T3 · 视图模式持久化

- `project_settings` 加新键 `files_view_mode`（值 `tree` / `flat`）
- 默认 `tree`
- 切换 → 立即写库 + 刷新文件表

---

## 实现要点

### A. `_populate_files_tree` 重构

提取出两个分支函数：
```python
def _populate_files_tree_mode(self):
    """根据 files_view_mode 调用 _populate_files_tree_tree / _populate_files_tree_flat"""
    mode = self.repo.get_project_setting(pid, "files_view_mode", "tree")
    if mode == "flat":
        self._populate_files_tree_flat()
    else:
        self._populate_files_tree_tree()  # 现有 _populate_files_tree 改名
```

### B. 大小/时间列的延迟 stat

```python
class FileRowStatCache:
    """避免每次刷新都 stat。失效条件：path 变 / is_relative 变。"""
    cache: dict[int, tuple[str, int]]  # file_id -> (path, size)
```

- 列渲染时查缓存 → 命中显示，未命中加入后台队列
- 后台 worker（QThread）批量 stat → 完成后 `dataChanged` 信号刷新对应行

### C. 列定义扩展

`files_table_columns.py` 的 `COLUMNS` 加两条：

```python
{"key": "size",       "title": "大小",     "default_visible": False, "default_width": 90},
{"key": "added_at",   "title": "添加时间", "default_visible": False, "default_width": 140},
{"key": "subfolder",  "title": "子文件夹", "default_visible": False, "default_width": 120, "flat_only": True},  # 仅扁平视图可见
```

新增 `flat_only` 字段标记"仅扁平视图可见"的列。

### D. 视图切换 toggle 按钮

放在文件表左上角（与列设置按钮邻近）：
- `QToolButton` + 两个状态图标
- 点击 → 写 setting → 重 populate

### E. 与 #31a 的协同

- 树形视图下：完全走 #31a 那套（按 ord、拖动、新建文件夹、F2）
- 扁平视图下：走 Qt 原生排序、禁用内部拖动、F2 仍可用（改 label / 物理文件名，跟树形一致）
- 视图切换发生时：保留选中状态（按 file_id 重新定位）

---

## 校验

### T1 新增列
- [ ] 列设置对话框出现"大小"、"添加时间"两项
- [ ] 开启"大小"列 → 显示文件物理大小（格式化为 KB/MB）
- [ ] 物理文件不存在 → 大小列显示 "—"，不报错
- [ ] 大库（5000+ 文件）开大小列 → 不卡 UI，后台逐步填充

### T2 扁平视图
- [ ] 切到扁平 → 所有 📁 节点消失，文件平铺
- [ ] 在扁平视图点列头 → Qt 原生升降序，按大小 / 添加时间 / 文件名都正确
- [ ] 切回树形 → 恢复 subfolder 分组 + 按 ord
- [ ] 扁平视图下"子文件夹"列可选显示
- [ ] 扁平视图下从资源管理器拖文件进来 → 仍能添加（外部拖入保留）
- [ ] 扁平视图下文件之间拖动 → **不响应**（内部拖动禁用）

### T3 视图模式持久化
- [ ] 切到扁平 → 切别的项目再切回来 → 仍是扁平
- [ ] 不同项目独立记忆（项目 A 扁平、项目 B 树形互不干扰）
- [ ] 列宽 / 列可见性在两种视图共享（同一项目从树形改列宽 → 切扁平 → 列宽保留）

---

## 依赖

- **强依赖**：task #17（subfolder + 树形控件）✅
- **协同**：task #31a（树形视图内的交互）—— 本卡只接管扁平视图，树形视图的行为完全由 #31a 决定
- **联动**：task #02（独立窗口）✅ —— 视图切换按钮和模式都跟随同一控件
- **相邻**：task #14 T1（一致性检查的进度感知模式）—— 大库 stat 的后台 worker 思路一致

---

## 工作量拆分

| 子项 | 估算 |
|---|---|
| T1 新增大小/添加时间列 + 延迟 stat 缓存 + 后台 worker | 0.6 天 |
| T2 扁平视图模式 + Qt 原生排序 + 内部拖动禁用 | 0.5 天 |
| T3 视图模式持久化 + 切换按钮 UI | 0.2 天 |
| 与 #31a 的协同测试（视图切换保留选中、列设置共享） | 0.3 天 |
| 验收测试（selftest #31b） | 0.3 天 |
| **合计** | ~1.9 天（M） |

---

## 后续扩展

- **MIME 类型列**：用 `mimetypes.guess_type` 显示更精细的类型（pdf/image 太粗）
- **修改时间列**：除了 added_at 还能显示文件系统的 mtime
- **筛选条件**：扁平视图下加搜索框（按文件名 substring 过滤），未来与 task #03 类 Calibre 搜索打通
- **导出当前视图**：选中行 → 导出为 CSV（"文件清单")

---

## 待澄清

> 卡片正文已按"默认决定"写成可执行状态；**若不同意，请在我编码前告知**。

1. **排序状态持久化是共享还是分开**
   - 默认决定：**分开**，两个键 `files_table_sort_tree` / `files_table_sort_flat`。理由：树形天然按 ord（自定义顺序），扁平更常用按大小/时间，意图不一致。
   - 若你希望共享一个键（切换视图保留同一排序意图），告诉我，改回单键 + view_mode 字段。

2. **大小列的格式**
   - 默认决定：动态选择单位（< 1 MB 显示 KB，< 1 GB 显示 MB，否则 GB）。
   - 若你希望统一显示为 MB（便于排序对齐），告诉我。

3. **扁平视图下是否完全隐藏 subfolder**
   - 默认决定：subfolder 列默认隐藏，但用户可在列设置里打开。
   - 若你希望扁平视图永远不显示 subfolder（强制"扁平 = 忽略组织"），告诉我，移除 flat_only 列。
