# 28 · 导出/导入闭环（扩展版）

**工作量**：L
**优先级**：P1
**状态**：待做

## 来源

用户期望「导入-管理-导出」功能闭环（TODO「📦 项目 & 文件管理」段 #28）。

**现状盘点（动手前务必先读现有代码，不要当白纸规划）**：

| 能力 | 现状 | 涉及文件 |
|------|------|----------|
| 单项目导出 | ✅ 已完成（task #09），仅"目录形式 + 复制链接文件"两个选项 | `app/exporter.py`、`app/ui/export_dialog.py` |
| 文件夹批量导入 | ✅ 已完成（task #10），能识别导出包并按 **field_name** 匹配字段、三种未匹配字段策略 | `app/importer.py`、`app/ui/import_dialog.py` |
| subfolder 还原 | ✅ 已完成（task #17），但**靠物理目录结构反推**，不读 json | `importer._collect_files_to_import` |
| ZIP 导出/导入 | ❌ 缺 | — |
| 仅元数据 导出模式 | ❌ 缺 | — |
| 批量导出 | ❌ 缺 | — |
| 封面图导入还原 | ❌ 缺（导入后 file id 变了，`cover_file_id` 仍指旧 id → 封面失效） | — |

**结论**：导入功能**已存在**，本卡是"在现有 exporter/importer 上扩展"，**不是新建模块**。

---

## 目标

1. 扩展导出选项（模式、格式、结构、内容）
2. 批量导出多项目
3. 在现有导入基础上补齐：ZIP 包导入、封面还原、拍平结构下的目录树还原
4. 完整闭环：导出 → 修改/备份 → 再导入

---

## 功能拆解

### T1 · 导出选项扩展

#### A. 导出模式（二选一）

```
导出模式：
◉ 导出为独立包（project.json + files/）
○ 仅导出项目元数据（project.json，不含 files/）
```

| 模式 | 说明 | 输出 |
|------|------|------|
| **导出为独立包** | 生成自包含的项目包，可分享/备份 | project.json + files.json + files/ |
| **仅导出项目元数据** | 只生成结构化元数据，不含文件内容 | project.json |

> 「移动文件到新位置 / 链接文件转仓储」不属于导出范畴，已从本卡剥离，归到 [task #29 文件存储位置管理](./29-file-storage-location-management.md)。

#### B. 导出格式（仅"导出为独立包"）

```
导出格式：
◉ 目录形式（可读、便于手动调整）
○ ZIP 打包（便于分享、单一文件）
```

#### C. 文件目录结构（仅"导出为独立包"）

```
文件目录结构：
◉ 保留项目内目录结构（按 UI 文件树建子目录）
○ 拍平到 files/（所有文件平铺，用后缀 _<id> 防冲突）
```

- **保留目录结构**：`files/<subfolder>/foo.pdf`，依赖 `files.subfolder`（task #17）
- **拍平**：`files/foo_1.pdf`，文件名后缀 `<id>` 避免冲突

> ⚠️ 两种结构都必须把每个文件的 `subfolder` 写进 `files.json`（见「导出包格式 v3」）。
> 现状 `files.json` 的 file_entries **根本没有 `subfolder` 字段**——这是本卡必须新增的。
> 拍平模式下导入端无法从物理路径反推目录，只能靠 json 的 `subfolder`（见 T3-E）。

#### D. 内容选项（仅"导出为独立包"）

```
包含内容：
☑ 项目元数据（project.json + files.json）
☑ README.md（人类可读摘要）
□ LLM 任务历史（近 N 条审计日志，仅用于调试/审计）
```

- 前两项默认勾选
- LLM 任务历史默认不勾（导出包会变大，且含 API 上下文敏感信息）
- 封面图是项目元数据的一部分，始终包含

#### E. 封面图处理

> 封面有两种：用户自己的图片（`origin='user'`）和软件生成的快照（`origin='generated'`，如截 PDF 首页/视频帧/剪贴板）。两者都登记在 `files` 表，靠 task #30 的 `files.origin` 区分。导出/导入都要忠实保留这个来源标记（见下方 files.json 的 `origin` 字段）。

| 导出模式 | 封面图处理 |
|----------|------------|
| 导出为独立包 | 必须复制到导出包（即使该文件是🔗链接模式且用户没勾"复制链接文件"），并在 files.json 标 `is_cover` + 保留其 `origin` |
| 仅导出项目元数据 | 仅在 project.json 记录 `cover_file_id`，不复制文件 |

---

### T2 · 批量导出

#### A. 入口

1. **多选项目后工具栏**：新增「📤 批量导出」按钮（依赖 task #25 项目多选；#25 未完成时本子项可后置）
2. **右键菜单**：选中 ≥2 个项目时显示「📤 导出选中项目（X个）…」

#### B. 对话框

```
┌─────────────────────────────────────────────────────────┐
│ 批量导出（X 个项目）                                     │
│                                                         │
│ 导出位置：                                              │
│ ┌────────────────────────────────┐ ┌──────────┐         │
│ │ D:\Backups\                    │ │ 📂 浏览…│         │
│ └────────────────────────────────┘ └──────────┘         │
│                                                         │
│ 导出模式：◉ 导出为独立包  ○ 仅导出元数据                 │
│ 导出格式：◉ 目录形式  ○ ZIP 打包                        │
│ 文件结构：◉ 保留目录  ○ 拍平                            │
│ ☑ 复制链接(🔗)文件  ☑ 包含 README.md                    │
│                                                         │
│ 选中项目：                                              │
│ ☑ 项目 A (3 个文件)                                     │
│ ☑ 项目 B (5 个文件)                                     │
│ ☐ 项目 C (0 个文件)  ← 空项目不勾选，灰显               │
│                                                         │
│                         [ 取消 ]   [ 📤 导出 ]           │
└─────────────────────────────────────────────────────────┘
```

- 每个项目一行 CheckBox，文件数显示在侧
- 空项目（0 文件）默认不勾选，灰显
- ⚠️ 勾选框文案用「复制链接(🔗)文件」，**不要写"复制所有文件"**：现状 `copy_link_files` 只控制是否复制🔗链接文件，📦仓储文件本就总是复制；写"复制所有文件"会误导用户以为不勾就什么都不复制。

#### C. 输出结构

**目录形式**：
```
<选定目录>/
├── project_a/
│   ├── project.json
│   ├── files.json
│   ├── README.md
│   └── files/
│       ├── sub/
│       │   └── foo.pdf        ← 保留结构
│       └── bar_2.pdf          ← 拍平时的样子（带 _<id>）
├── project_b/
│   └── ...
└── ...
```

**ZIP 形式**：在选定目录下生成单个 `projects_export_<日期>.zip`，内含各项目子目录（结构同上）。

- 实现复用 `export_project`：循环对每个勾选项目导出到 `<target>/<safe_title>/`，再按需打包成 ZIP。

---

### T3 · 导入增强（在现有 importer 上扩展，非新建）

> 现有 `importer.scan_folders` + `import_folder_as_project` 已能导入"导出包文件夹"，
> 字段按 **field_name** 匹配（**沿用，不改成 id 映射**，理由见「待澄清」）。
> 本子项只补三个缺口：ZIP 解包、封面还原、拍平结构的目录树还原。

#### A. ZIP 包导入（新增）

- 入口：菜单「文件 → 导入项目包…」、工具栏「📥 导入」、拖放
- 用户选 `.zip` 或目录：
  - **目录** → 直接走现有 `scan_folders([dir])`
  - **ZIP** → 先解压到临时目录，再走现有 `scan_folders`，导入完成后清理临时目录
- ⚠️ **拖放消歧**：现状拖**文件夹**已触发 task #10 的批量导入。新增"拖 ZIP"时：拖入是 `.zip` → 解包后导入；拖入是文件夹 → 维持现有批量导入。两者入口不冲突，但需在 `dnd` 层显式分流。

#### B. 封面图还原（新增，修既有缺口）

现状导入完全不处理 `cover_file_id`，导致导入后封面失效。需：
1. 导入文件时记录「源 file id（来自 files.json 的 `id`）→ 新 file id」映射
2. 读 `project.json` 的 `cover_file_id`（旧 id），用映射换成新 id
3. 回写 `project.cover_file_id` 并 `save_project`

#### C. 拍平结构的目录树还原（新增，修既有缺口）

现状 `_collect_files_to_import` 从**物理目录结构**反推 `subfolder`，拍平包里子目录已不存在 → 还不了树。改为：
- 若 `files.json` 提供了每个文件的 `subfolder`，**优先用 json 的 subfolder**
- 否则回退到现有"物理结构反推"逻辑（保持对 task #10 旧包/普通文件夹的兼容）

#### C2. 文件来源标记还原（新增，依赖 task #30）

`files.json` 每条带 `origin`（`user`/`generated`，task #30）。导入时把它写回新 file 行的 `files.origin`，让"软件生成的封面快照"在新库里仍被正确归类（而不是退化成普通用户文件）。
- 旧包（@1/@2，无 `origin`）：缺省按 `'user'` 处理（与 task #30 列默认值一致）。

#### D. 字段映射（沿用现状，不改）

- 继续按 `field_name` 在目标库匹配（task #10 既有逻辑，工作良好）
- 未匹配字段策略：自动创建 / 追加到描述 / 忽略（既有三选一，不动）
- 标签：合并到现有标签（既有逻辑）

#### E. schema 版本同步（不要漏）

导出端 schema 升 `@3` 后，**必须同步** `importer.SUPPORTED_SCHEMA_VERSION` 从 `2` 升到 `3`；否则现有导入端会把新包判成 `is_future_schema=True`，弹"由更新版本生成"警告。@1/@2 旧包保持兼容（缺失字段视为空/回退物理结构）。

---

## 实现要点

### A. `ExportOptions` 扩展（注意字段顺序！）

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

@dataclass
class ExportOptions:
    # ⚠️ 无默认值的字段必须排在所有有默认值字段之前，否则 dataclass 报
    #    "non-default argument follows default argument"
    target_root: Path

    mode: Literal["package", "metadata_only"] = "package"
    overwrite: bool = False
    copy_link_files: bool = True            # 沿用现状字段

    # 仅 mode="package" 时有效
    export_format: Literal["directory", "zip"] = "directory"  # 不用 `format`，避免遮蔽内置名
    preserve_structure: bool = True
    include_readme: bool = True
    include_llm_history: bool = False
    history_limit: int = 10
```

### B. `export_project` 扩展

```python
def export_project(repo, library, project, options, *, progress=None) -> ExportResult:
    """根据 options.mode 决定行为：
    - "package":       现有逻辑 + 结构/格式/内容选项
    - "metadata_only": 仅写 project.json，不建 files/、不写 files.json
    """
```

**拍平结构的文件名**（从现状 `<id>__name` 改为 `<name>_<id>`）：
```python
stem = Path(f.path).stem
dst_name = f"{sanitize_filename(stem)}_{f.id}{Path(f.path).suffix}"
```

**保留结构**：`files/<f.subfolder>/<原文件名>`（subfolder 为空则直接放 files/ 下）。

**封面图必复制**（无论 copy_link_files）：
```python
if project.cover_file_id == f.id:
    should_copy = True
```

### C. `files.json` 新增字段

每个 file entry 增加 `subfolder`、`is_cover` 与 `origin`（见下方 v3 结构）。`origin` 来自 task #30 的 `files.origin`，导出端直读、导入端写回。

### D. ZIP 打包

```python
import zipfile  # 标准库即可，不引第三方
# 目录形式导出完成后，把 <target>/<safe_title>/ 整个目录打进 zip
```

### E. importer.py 扩展

- `SUPPORTED_SCHEMA_VERSION = 3`
- `_collect_files_to_import`：优先读 `files.json` 的 `subfolder`
- `import_folder_as_project`：导入后建立 `旧file_id → 新file_id` 映射，回写 `cover_file_id`；同时把每个文件的 `origin` 写回 `files.origin`（依赖 task #30）
- 新增 ZIP 解包入口（解压到临时目录 → 复用 `scan_folders`）

### F. UI 改造

- **`ExportDialog` 扩展**：导出模式 RadioButton（二选一）；格式/结构/内容选项随模式动态显隐
- **导入入口**：`MainWindow` 工具栏「📥 导入」+ 菜单「文件 → 导入项目包…」+ 拖放分流
- **是否新建 `ImportPackageDialog`**：现有 `ImportDialog`（批量文件夹）已可复用；单包导入是否需要专门的、更简洁的对话框，见「待澄清」。默认先**复用现有** `ImportDialog`（单元素 plans 列表）。

---

## 导出包格式 v3（向后兼容 @1/@2）

```json
{
  "schema": "llm-cabinet/project-export@3",
  "exported_at": "2026-06-10T12:00:00+08:00",
  "exporter_app_version": "0.5.0",
  "exporter_schema_version": 7,
  "export_options": {
    "mode": "package",
    "export_format": "directory",
    "preserve_structure": true,
    "include_readme": true,
    "include_llm_history": false
  },
  "project": {
    "title": "...",
    "cover_file_id": 3,
    "...": "（其余字段沿用现状 v2：author/date/source_url/rating 兼容兜底 + description_md/storage_mode/created_at/updated_at）"
  },
  "tags": ["科幻", "翻译"],
  "fields_snapshot": [],
  "field_values": [],
  "llm_history": [],
  "files": []
}
```

`files.json` 结构（仅"导出为独立包"时生成，**新增 `subfolder` / `is_cover` / `origin`**）：

```json
{
  "preserve_structure": true,
  "files": [
    {
      "id": 1,
      "subfolder": "sub",
      "origin": "user",
      "original_storage": "copy",
      "original_path": "project_3/foo.pdf",
      "is_relative": true,
      "label": "中文版",
      "kind": "pdf",
      "ord": 0,
      "added_at": "...",
      "exported_to": "files/sub/foo.pdf",
      "exported_size": 123456,
      "is_cover": true
    }
  ]
}
```

> - `is_cover` 是冗余标记（封面真值在 project.json 的 `cover_file_id`），仅作导入时便捷判断；以 `cover_file_id` 为准。
> - `origin`（`user`/`generated`，task #30）：标识用户原始文件还是软件生成的衍生物（如封面快照）；导入时写回 `files.origin`。旧包无此字段时按 `'user'`。

---

## 校验

### T1 导出选项
- [ ] 「导出为独立包」→ 正常生成 project.json + files.json + files/
- [ ] 「仅导出项目元数据」→ 仅 project.json，无 files/、无 files.json
- [ ] 「ZIP 打包」→ 生成 .zip，内含完整目录结构
- [ ] 「保留目录结构」→ files/ 下出现子目录，文件名无 `_<id>` 后缀
- [ ] 「拍平」→ files/ 下全部平铺，文件名带 `<name>_<id>` 后缀，且 files.json 的 subfolder 仍正确
- [ ] 不勾 README → 无 README.md
- [ ] 勾 LLM 历史 → project.json 含 llm_history
- [ ] 🔗链接模式封面文件，即使不勾"复制链接文件"，封面仍被复制进包

### T2 批量导出
- [ ] 选中多项目 → 工具栏出现「批量导出」按钮
- [ ] 对话框列出所有选中项目，空项目灰显且默认不勾
- [ ] 目录形式 → 每项目一个子目录
- [ ] ZIP 形式 → 单个 ZIP 内含多个项目子目录

### T3 导入增强
- [ ] 拖 ZIP 到主窗口 → 解包并弹导入对话框；拖文件夹仍走原批量导入
- [ ] 菜单/工具栏「导入项目包」可选 ZIP 或目录
- [ ] 导入后封面正确显示（cover_file_id 已按新 id 重映射）
- [ ] 软件生成的封面快照导入后 `origin='generated'` 被保留（不退化成普通用户文件）
- [ ] 拍平结构的包导入后，目录树按 files.json 的 subfolder 正确还原
- [ ] 保留结构 / task #10 旧包 / 普通文件夹 三种来源 subfolder 均不回归
- [ ] @1/@2 旧导出包仍可正常导入（无 future-schema 误警）

---

## 依赖

- **强依赖**：task #17（subfolder 字段）✅、task #09（基础导出）✅、task #10（导入器/导入对话框）✅
- **强依赖**：task #30（`files.origin` 来源标记）—— 封面快照等软件衍生物的导出/导入归类靠它；**#30 应先于本卡的封面/origin 子项**
- **软依赖**：task #25（项目多选）—— T2 批量导出入口依赖其多选；#25 未完成时 T2 可后置或临时用右键单选入口
- **联动**：`export_project` 同时是 MCP 工具（task #23）。schema 升 v3 影响 MCP 输出，完工后需同步 `docs/mcp.md` 与 `CHANGELOG.md [Unreleased]`

---

## 工作量拆分

| 子项 | 估算 |
|---|---|
| T1：ExportDialog 扩展（模式/格式/结构/内容动态显隐） | 0.4 天 |
| T1：exporter 适配（拍平 `<name>_<id>` / 保留结构 / 封面必复制 / 仅元数据 / ZIP / files.json 加 subfolder+is_cover） | 0.6 天 |
| T2：批量导出 UI + 循环导出 + ZIP 打包 | 0.5 天 |
| T3：importer 扩展（schema→3 / ZIP 解包 / 封面重映射 / subfolder 优先读 json） | 0.6 天 |
| T3：MainWindow 导入入口 + 拖放分流 | 0.3 天 |
| 文档同步（mcp.md / CHANGELOG）+ 验收测试（selftest #28） | 0.4 天 |
| **合计** | **~2.8 天（L）** |

---

## 后续扩展

- **增量导出**：只导出修改过的文件
- **加密导出**：AES 加密导出包
- **导入预览/冲突检测**：导入前预览字段映射结果、与现有项目冲突检测
- **文件存储位置管理**：项目内「链接文件转仓储（直接复制）」+「移动文件到新位置」已拆到 [task #29](./29-file-storage-location-management.md)，不在本卡范围

---

## 待澄清

> 以下为动手前的开放决策。卡片正文已按"默认决定"写成可执行状态；**若不同意，请在我编码前告知**。

1. **字段映射方式**
   - 默认决定：**沿用现状的 `field_name` 匹配**（task #10 既有、改动小、与批量导入一致）。
   - 前一版卡片提议的 `dict[int, int]`（源 field_id → 目标 field_id）有问题：导出包里的 field_id 在目标库无意义，且与现有逻辑冲突。故不采用。
   - 若你确实想要"导入时人工逐字段确认映射"的交互，告诉我，会另起子项设计（成本更高）。

2. **单包导入是否需要专门对话框**
   - 默认决定：**复用现有 `ImportDialog`**（传入单元素 plans 列表），不新建。
   - 若希望单包导入有更简洁的专用界面（如直接展示标题/文件数/存储方式选择，而非批量树），告诉我，会新增 `ImportPackageDialog`（增加 ~0.3 天）。
