# 10 · 文件夹批量导入 + 项目配置文件识别

**工作量**：S+S
**优先级**：P1
**状态**：待做

## 来源

`TODO.md → 📦 项目 & 文件管理`：

- 「批量按文件夹导入，每个文件夹作为一个项目。作为导入功能的子分支（检测到导入多个文件夹且仅有文件夹时触发）」
- 「导入文件夹时创建项目时检测是否已经有导出的项目配置文件」

与 `tasks/09`（项目导出基础版）形成**导出/导入闭环**。

## 目标

让用户能通过把多个文件夹拖到主界面**底部 DropZone**，一次性导入为多个新项目；
若文件夹根目录有 `tasks/09` 输出的 `project.json`，则识别后自动恢复元数据/字段/标签。

## 范围与边界

**做**：

- 拖到 DropZone 的对象**全是目录**且 ≥ 2 个 → 弹「单项目 vs 多项目」模式选择对话框
  - 选「合并为同一项目」→ **不走本 task**，沿用现有 DropZone → `_drop_create_project` 逻辑
  - 选「各自建项目」→ 进入本 task 的批量导入流程
- 拖到 DropZone 的对象全是目录且 = 1 个 → 沿用现状（单项目），不弹模式选择
- 文件夹根目录下存在 `project.json`（schema = `llm-cabinet/project-export@*`）→ 识别并恢复
- 标签：库内已有则复用，不存在则**直接创建**（无需问用户；标签本身就是从项目里增量产生的）
- 字段：项目中若有库内不存在的字段，按「未匹配字段策略」处理（见下）
- 导入结果对话框：每个文件夹一行，显示「✅ 已识别配置 / ⚪ 普通文件夹 / ⚠ N 字段未匹配」

**不做**：

- 散文件 + 文件夹混合拖入（沿用现状：DropZone 收到后按"散文件并入当前项目"路径）
- ZIP 包导入（先做目录形式，与 `#09` 对称）
- 跨库 ID 漂移修复（cover_file_id、ord 等沿用导出值即可）

### 未匹配字段策略

`project.json` 中存在库内 `fields` 表里没有的字段名时，按用户选项处理：

| 策略 | 行为 |
|---|---|
| 自动创建（在库内新建该字段） | 用 `fields_snapshot[i]` 里的 `type / key / name`（甚至 `prompt_hint`，若是 `@2+` schema）INSERT 到 `fields` 表；type 走白名单校验（非法 type → fallback `text`）；新字段 `ord` 追加到末尾 |
| 追加到描述（默认） | 字段不建，把 `字段名: 值` 追加到该项目的 `description_md`，避免数据丢失 |
| 忽略 | 字段不建、值丢弃、warning 记录 |

UI：ImportDialog 给一个单选三档 + 「☑ 应用到本次所有项目」复选框。

- **默认**：`追加到描述` + 勾选「应用到全部」（保守、不污染库 schema、不丢数据，一键完成）
- 不勾选「应用到全部」时，每个含未匹配字段的项目导入前都会**单独弹问**一次，可逐项目决策
- 「追加到描述」时在项目 `description_md` 末尾追加：

  ```
  > 库内不存在的字段（已保留原值）:
  > - 字段名 A: 值 A
  > - 字段名 B: 值 B
  ```

### schema 版本兼容策略

`project.json` 的 `schema` 字段格式：`llm-cabinet/project-export@<N>`。

| 情况 | 处理 |
|---|---|
| 字段缺失或前缀不是 `llm-cabinet/project-export@` | 当作未识别（普通文件夹） |
| `N` ≤ 当前导入器已知最高版本 | 按对应版本路径完整解析 |
| `N` > 当前导入器已知最高版本 | 仍尝试识别**向前兼容核心字段**（title / tags / field_values by name），其它未知字段忽略；状态栏标 `⚠ 此包由更新版本生成，可能有未识别字段；建议升级 LLM-Cabinet` |
| JSON 解析失败 / 必填字段缺失 | 当作未识别，warning 记录原因 |

当前导入器实现版本：`@1`（与 `#09` 输出一致）。

## 触发条件

DropZone 的现有 `dropped` 信号已经收到完整路径列表。在 `_on_dropzone_dropped` 里加判定：

```
paths：
  - 全是目录   且   数量 ≥ 2   → 弹「模式选择」对话框
      - 用户选「各自建项目」  → 进入本 task 流程
      - 用户选「合并为同一项目」→ 沿用 _drop_create_project
      - 用户取消             → 什么都不做
  - 全是目录   且   数量 = 1   → 沿用 _drop_create_project（单项目）
  - 含散文件                    → 沿用 _drop_create_project（散文件并入新项目）
```

> 卡片/列表区的 drop（`ProjectViewDnD`）以及文件表的 drop（`FilesTableDnD`）**不受影响**——它们语义就是"合并到已有项目"，永远不进本流程。

文件菜单 / 工具栏入口：本期**不加**，仅通过拖拽触发；显式入口留待 `#08`（多库切换） / `#05` T7 一起设计。

## 模式选择对话框（轻量）

```
┌─────────────────────────────────────────┐
│ 检测到拖入 4 个文件夹                   │
│                                         │
│ ○ 合并为同一个新项目                    │
│ ● 每个文件夹分别建立一个项目            │
│                                         │
│        [ 取消 ]   [ 下一步 → ]          │
└─────────────────────────────────────────┘
```

- 默认选「分别建立」（更安全、可后悔；合并后拆分麻烦）
- 选「合并」→ 直接复用现有路径，不进 ImportDialog
- 选「分别建立」→ 进 ImportDialog

## 导入对话框

新增 `app/ui/import_dialog.py`：

```
┌─────────────────────────────────────────────────────────────┐
│ 批量导入文件夹                                              │
│                                                             │
│ 共 4 个文件夹将被导入为新项目：                             │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ 文件夹                       │ 状态                     │ │
│ │ project_A/                   │ ✅ 已识别 project.json   │ │
│ │ project_B/                   │ ⚪ 普通文件夹            │ │
│ │ project_C/                   │ ✅ 已识别（2 字段未匹配）│ │
│ │ project_D/                   │ ⚪ 普通文件夹            │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ 选项：                                                      │
│  文件存储模式：[● 链接到原位置  ○ 复制到仓储]               │
│  项目标题：  [● 沿用 project.json / 文件夹名             ] │
│       (识别到 project.json 时优先用其中的 title；否则用    │
│        文件夹名)                                           │
│  未匹配字段：[○ 自动创建  ● 追加到描述  ○ 忽略]             │
│              ☑ 应用到本次所有项目                           │
│                                                             │
│                              [ 取消 ]   [ 📥 开始导入 ]     │
└─────────────────────────────────────────────────────────────┘
```

- 点击文件夹行可展开看到「`project.json` 中的字段值预览」与「未匹配字段名列表」
- **未匹配字段**默认「追加到描述」+ 勾选「应用到本次所有项目」（保守、一键完成）
- 若用户**取消勾选**「应用到本次所有项目」，则每个含未匹配字段的项目导入前都会**单独弹问**一次（同一三选一对话框），允许逐项目决策
- 「自动创建」会真的写 `fields` 表，影响整个库；选择前对话框会用小灰字提示这一点

## 实现要点

### A. 新模块 `app/importer.py`

与 `app/exporter.py` 对称：

```python
FieldPolicy = Literal["create", "append_to_desc", "ignore"]

@dataclass
class ImportPlan:
    folder: Path
    has_project_json: bool
    project_json: dict | None        # 解析后；None = 无或损坏
    schema_version: int | None       # @N 中的 N；None = 未识别
    is_future_schema: bool           # schema_version > 导入器已知
    unmatched_fields: list[str]      # 库内不存在的字段名

@dataclass
class ImportOptions:
    storage_mode: Literal["link", "copy"]
    title_source: Literal["project_json", "folder_name"]
    field_policy: FieldPolicy
    field_policy_apply_all: bool     # 不勾选时遇到未匹配字段会回调要求决策

@dataclass
class ImportResult:
    project_id: int
    n_files: int
    warnings: list[str]

def scan_folders(folders: list[Path], repo: Repository) -> list[ImportPlan]: ...

def import_folder_as_project(
    repo: Repository,
    library: Library,
    plan: ImportPlan,
    options: ImportOptions,
    *,
    progress: Callable[[int, int, str], None] | None = None,
    ask_field_policy: Callable[[list[str]], FieldPolicy] | None = None,
) -> ImportResult: ...
```

`ask_field_policy` 仅在 `field_policy_apply_all=False` 且该项目有未匹配字段时被调用（UI 弹三选一对话框）。

### B. project.json 识别

按上文「schema 版本兼容策略」表执行。识别成功后 `ImportPlan.project_json` 是解析后的 dict；`unmatched_fields` 通过对比 `field_values[].field_name` 与库内 `fields` 表得出。

`files.json` 在本 task **可选**：

- 有 + storage_mode=link + `original_path` 仍存在 → 还原链接
- 有 + storage_mode=link + `original_path` 失效 → fallback 到文件夹内的实际副本
- 无 → 按文件夹实际内容扫一遍

### C. main_window / DropZone 改造

- `_on_dropzone_dropped`：先用 `extract_kind(paths)` 判定"全目录且数量"
  - 全目录 ≥ 2 → 弹模式选择 → 选"分别建立"才进本 task
  - 否则沿用 `_drop_create_project`
- 进入本 task 后：`ImportDialog(plans=scan_folders(paths, repo))` → 工作线程跑批量导入（沿用 `#09` 的 `QProgressDialog`）
- 完成后刷新项目列表，选中**最后一个**新建项目；状态栏汇总「成功 N，warning M」

### D. Repository 不动 / 几乎不动

沿用 `add_project / add_files / set_field_value / link_tag` 等现有接口，无需新增方法。

## 校验

- DropZone 收到散文件 → 不进本流程
- DropZone 收到单个目录 → 不进本流程
- DropZone 收到 ≥ 2 目录 → 弹模式选择 → 取消则什么都不做
- `project.json` 解析失败 → 状态显示「⚪ 普通文件夹」，warning 记录原因
- `project.json` schema 版本超前 → 状态显示「⚠ 更新版本生成」，仍能用核心字段建项目
- 仓储模式：检查目标盘空间足够再开始拷贝

## 验收

- [ ] 从资源管理器拖 3 个目录到主窗口底部 DropZone → 弹模式选择 → 选「分别建立」→ 进入 ImportDialog
- [ ] 选「合并为同一项目」→ 走旧路径，不弹 ImportDialog
- [ ] 仅拖 1 个目录到 DropZone → 直接走旧单项目路径，不弹模式选择
- [ ] 含 1 个合法 `project.json` 的目录 → 状态「✅ 已识别」并预览字段值正确
- [ ] 故意改坏 `project.json` 一个字符 → 状态「⚪ 普通文件夹」，warning 提及解析失败
- [ ] `project.json` schema 改为 `llm-cabinet/project-export@99` → 状态「⚠ 更新版本生成」，仍能建项目并填入 title/tags
- [ ] 选「链接到原位置」→ 导入后文件以 `is_relative=False` 链接进项目
- [ ] 选「复制到仓储」→ 文件复制进 library/，原目录未变动
- [ ] **标签**：库内已有的复用，不存在的自动创建（无弹窗）
- [ ] **未匹配字段策略**：
  - [ ] 「追加到描述」+ 应用到全部（默认）→ 未匹配字段值出现在 `description_md` 末尾的 blockquote 区域；`fields` 表不变
  - [ ] 「自动创建」+ 应用到全部 → 库内 `fields` 表新增对应字段；导入完成后在「设置 → 字段」可见；未来项目也能用
  - [ ] 「忽略」+ 应用到全部 → 字段不建、值丢弃、warning 记录
  - [ ] 不勾选"应用到全部" → 每个含未匹配字段的项目导入前单独弹三选一
- [ ] `project.json` 中字段 type 是非法值 → fallback 到 `text`，warning 记录
- [ ] 导入完成后项目列表刷新，最后一个新项目被选中

## 风险

- **海量小文件复制慢**：进度条按文件计数；后续可改为按字节
- **link 模式恢复后路径在新机器上失效**：在导入对话框小灰字提示"链接到原位置仅在原机器有效；跨机器请选『复制到仓储』"
- **schema 演进**：以 `schema` 字段判断；后续若变 `@2`，导入器加分支即可，不污染主流程
- **「自动创建」字段会污染库 schema**：默认值刻意选「追加到描述」+ 应用全部；UI 上对「自动创建」加小灰字提示
- **逐项目弹字段对话框打断流程**：默认勾选"应用到全部"避免；用户主动取消该勾选后承担弹框次数

## 依赖

- **强依赖** `tasks/09`（已完成）：复用 `project.json` / `files.json` schema
- 与 `tasks/05` T7：本 task 是 T7「项目导入」的最小可用版本；将来做完整 T7 时合并
- 与 `tasks/08`（多库切换）：完成后可与 `#09` 一起作为"跨库搬项目"的完整路径
- 与 `tasks/11`（字段 prompt + 库向导）：
  - T4 会让 `project.json` 升到 `@2`（`fields_snapshot[i].prompt_hint`），本任务的"超前 schema 容忍"策略提前准备好这种场景
  - 「自动创建字段」与 #11 T3「向导生成字段」是**两条独立入口**，最终都写 `fields` 表；导入器创建出的字段没有 `prompt_hint`，用户后续运行 #11 T3 或手动在「设置 → 字段」补上即可

## 工作量拆分

| 子项 | 估算 |
|---|---|
| `app/importer.py` + project.json 识别/解析 + 版本兼容 | 0.4 天 |
| `app/ui/import_dialog.py`（含三档字段策略 + 逐项目弹问） | 0.4 天 |
| 模式选择对话框 + `_on_dropzone_dropped` 改造 | 0.2 天 |
| main_window 集成 + 进度对话框复用 | 0.2 天 |
| 验收测试 + README/CHANGELOG 同步 | 0.1 天 |
| **合计** | ~1.3 天（S 偏上） |

## 后续扩展

- ZIP 包导入（与 #09 后续扩展对称）
- 跨库搬迁向导：选源库 → 选项目 → 选目标库 → 自动导出 + 导入（依赖 `#08`）
- 与 `#11`（库初始化向导）联动：导入时若库为空，提示运行向导先规划字段
- 文件菜单显式入口 `📥 导入文件夹…`（与 #08/#05 T7 一起规划）
