# 09 · 项目导出（基础版）

**工作量**：S  
**优先级**：P1  
**状态**：✅ 2026-05-31

## 来源

`TODO.md → 📦 项目 & 文件管理` 第 4 条 / 与 `tasks/05` T7（项目导入导出）的"导出半"对应；
本 task 是 T7 的**最小可用版本**：先把"导出一个项目到本地目录"打通，导入留到后续。

## 目标

让用户能从主界面快速把一个项目（元数据 + 文件）导出到本地文件夹，
用于备份、跨设备搬迁、或在 task #08 多库环境下从一个库挪到另一个库的中转。

**本期不做**：

- 多选批量导出（先做单项目，UI 一致再批量）
- ZIP 打包（先用目录形式，结构透明可读，方便用户手动微调）
- 导入功能（留给后续 task）
- 字段定义 snapshot 跨库匹配（默认导入端按 key/name 兜底匹配，跨库导入由后续 task 处理）

## 入口

两个：

1. **顶部工具栏**：在"删除项目"按钮右侧追加一个 `📤 导出` 按钮
2. **项目卡片/列表右键菜单**：在「✎ 编辑…」「✨ LLM 元数据建议…」之后追加分隔符 + 「📤 导出项目…」

> 当前 `app/ui/main_window.py` **没有** `QMenuBar`，工具栏（`tb.addAction`）就是顶部唯一的入口栏。本 task 沿用同一处。

快捷键：本期不分配（避免与未来导入 `Ctrl+E` 之类冲突）。

## 导出对话框

新增 `app/ui/export_dialog.py`，组件如下（自上而下）：

```
┌─────────────────────────────────────────────────────────┐
│ 导出项目                                                │
│                                                         │
│ 项目：「<标题>」（<n> 个文件）                          │
│                                                         │
│ 导出位置：                                              │
│ ┌────────────────────────────────┐ ┌──────────┐         │
│ │ D:\Backups\                    │ │ 📂 浏览…│         │
│ └────────────────────────────────┘ └──────────┘         │
│                                                         │
│ 选项：                                                  │
│  ☑ 复制链接模式（🔗）的原始文件到导出目录                │
│       (未勾选时，链接模式文件仅在 files.json 里          │
│        记录其原绝对路径，便于在原机器上恢复)            │
│                                                         │
│                          [ 取消 ]   [ 📤 执行导出 ]      │
└─────────────────────────────────────────────────────────┘
```

### 字段说明

| 元素 | 类型 | 行为 |
|---|---|---|
| 项目标题与文件数 | QLabel | 只读 |
| 导出位置文本框 | QLineEdit | 可手动输入或粘贴 |
| 浏览按钮 | QPushButton | 弹 `QFileDialog.getExistingDirectory`；默认初始路径取设置项 `last_export_dir`（首次为用户主目录） |
| 复制链接模式文件 | QCheckBox | **默认勾选**（最安全：保证导出包自包含） |
| 执行 | QPushButton (primary) | 校验 → 执行 → 完成后弹结果对话框 + 关闭 |
| 取消 | QPushButton | 关闭对话框 |

### 默认行为说明（写在 checkbox 的 tooltip 与对话框底部小灰字里）

- **复制链接模式文件 = 勾选**：导出目录自包含，可以拷贝走、可以在另一台机器上恢复
- **复制链接模式文件 = 不勾选**：导出包体积小，但依赖原机器上的原文件路径仍存在；适合"快速做项目元数据快照"

仓储模式（📦）文件**总是会被复制**（它们本来就在 library/，不复制就丢了）。

## 导出包结构

选定的目录下创建一个**以项目标题安全化后**命名的子目录（**不**就地写入，避免污染用户选的根目录）：

```
<选定目录>/
└── <safe_title>/                  ← 项目根，由 sanitize_filename(title) 派生
    ├── project.json               ← 项目元数据 + 字段定义 snapshot
    ├── files.json                 ← 文件清单（path / label / kind / storage_mode）
    ├── README.md                  ← 自动生成：人类可读的项目摘要 + 导出时间 + 应用版本
    └── files/                     ← 实际复制进来的文件
        ├── 1__<原文件名>          ← 前缀为 file.id 避免同名冲突
        ├── 2__<原文件名>
        └── ...
```

> 如果 `<选定目录>/<safe_title>/` 已存在：
> - 弹确认对话框「目录已存在，是否覆盖？」→ 用户确认才继续
> - 若不覆盖，在标题后加 `(2)`、`(3)` 递增

### project.json 结构（草案）

```json
{
  "schema": "llm-cabinet/project-export@1",
  "exported_at": "2026-06-01T10:30:00",
  "exporter_app_version": "0.1.0",
  "exporter_schema_version": 2,
  "project": {
    "title": "...",
    "author": "...",
    "date": "...",
    "source_url": "...",
    "rating": 0,
    "description_md": "...",
    "storage_mode": "link",
    "cover_file_id": 3,
    "created_at": "...",
    "updated_at": "..."
  },
  "tags": ["科幻", "翻译"],
  "fields_snapshot": [
    {"id": 1, "name": "标题", "type": "text", "key": "title", "ord": 0},
    ...
  ],
  "field_values": [
    {"field_id": 8, "field_name": "ISBN", "value": "..."},
    ...
  ]
}
```

> `field_values` 同时记录 `field_id` 和 `field_name`：导入端先按 name 匹配，避免库间 id 漂移。

### files.json 结构

```json
{
  "files": [
    {
      "id": 1,
      "original_storage": "copy",       ← 在源库的存储模式
      "original_path": "project_3/foo.pdf",
      "is_relative": true,
      "label": "中文版",
      "kind": "pdf",
      "ord": 0,
      "exported_to": "files/1__foo.pdf",   ← 相对 project 根；未复制则为 null
      "exported_size": 1234567               ← 字节数；未复制为 null
    },
    ...
  ]
}
```

## 实现要点

### A. 新模块 `app/exporter.py`

纯逻辑层，与 UI 解耦，方便后续做 ZIP 版 / 批量版时复用：

```python
@dataclass
class ExportOptions:
    target_root: Path          # 用户选的"导出位置"
    copy_link_files: bool      # 是否复制 link 模式文件
    overwrite: bool = False    # 同名目录是否覆盖

@dataclass
class ExportResult:
    project_dir: Path          # 实际写入的目录
    n_files_copied: int
    n_files_referenced: int    # 未复制（link 模式 + 用户未勾选）
    total_bytes: int
    warnings: list[str]        # 例如某 link 文件路径已失效

def export_project(
    repo: Repository,
    library: Library,
    project: Project,
    options: ExportOptions,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> ExportResult: ...
```

辅助：

- `sanitize_filename(name: str) -> str`：去掉 Windows 文件名非法字符 `< > : " / \ | ? *`、控制字符、首尾空格点
- `unique_dirname(base: Path, name: str) -> str`：返回 `name` / `name (2)` / `name (3)`...
- `copy_file_safely(src: Path, dst: Path)`：用 `shutil.copy2` 保留 mtime，捕获 OSError 不让单文件失败拖垮整个导出

### B. UI 模块 `app/ui/export_dialog.py`

```python
class ExportDialog(QDialog):
    def __init__(
        self,
        project: Project,
        n_files: int,
        last_export_dir: str,
        parent=None,
    ):
        ...

    # 用户确认后用 getters 取值
    def target_root(self) -> Path: ...
    def copy_link_files(self) -> bool: ...
```

对话框**不直接调用** `exporter.export_project`，由 main_window 负责。这样：

- 对话框可独立测试
- 导出执行时可以从 main_window 显示进度（status bar 或简单的 QProgressDialog）

### C. `app/ui/main_window.py` 改造

1. 工具栏追加按钮（在 `tb.addAction(... "🗑", "删除项目"...)` 之后）：

   ```python
   tb.addAction(make_action("📤", "导出项目", self.action_export_project))
   ```

2. 右键菜单 `_project_context_menu`，在「✨ LLM 元数据建议…」之后插入：

   ```python
   menu.addSeparator()
   menu.addAction("📤  导出项目…", self.action_export_project)
   ```

3. 新方法 `action_export_project(pid: int | None = None)`：

   ```python
   def action_export_project(self, pid=None):
       pid = pid or self._current_project_id
       if pid is None: return
       project = self.repo.get_project(pid)
       n_files = len(self.repo.list_files(pid))
       last_dir = self.repo.get_setting("last_export_dir", str(Path.home()))
       dlg = ExportDialog(project, n_files, last_dir, parent=self)
       if dlg.exec() != QDialog.Accepted: return
       opts = ExportOptions(
           target_root=dlg.target_root(),
           copy_link_files=dlg.copy_link_files(),
       )
       # 简单进度：用 QProgressDialog；导出大文件时 reload UI 不卡死
       result = export_project(self.repo, self.library, project, opts, progress=...)
       self.repo.set_setting("last_export_dir", str(opts.target_root))
       # 完成后展示统计 + "📂 打开导出目录" 按钮
       ...
   ```

### D. Repository 不动

导出走只读路径，沿用现有 `get_project / list_files / list_tags_of / list_fields / get_field_values` 等接口，无需新增方法。

## 校验

执行前在 `ExportDialog._on_accept` 里校验，不通过给红字提示：

- 导出位置必填、必须是已存在的目录、必须可写
- 项目标题 sanitize 后非空（防止全是非法字符）
- 估算空间（仓储文件总大小 + 复制链接文件可选）vs 目标盘可用空间，不够时弹警告允许继续

## 验收

- [ ] 工具栏出现"📤 导出项目"按钮，未选中项目时禁用
- [ ] 项目卡片右键 → 「📤 导出项目…」可触发对话框
- [ ] 对话框初始路径来自 `last_export_dir` 设置（首次为 `Path.home()`）
- [ ] 选目录 → 勾选"复制链接模式文件" → 执行：
  - 导出目录下产生 `<safe_title>/` 子目录
  - 含 `project.json / files.json / README.md / files/`
  - 所有 `is_relative=True`（仓储）+ `is_relative=False`（链接）的文件都在 `files/` 里
- [ ] 不勾选"复制链接模式文件" → 仅仓储文件被复制；`files.json` 里链接文件的 `exported_to` 为 null、`original_path` 是绝对路径
- [ ] 同名目录冲突：弹确认；不覆盖时自动加 `(2)` 后缀
- [ ] 标题含非法字符（如 `项目/x:y?`）→ 安全化后正常导出
- [ ] 导出后 `last_export_dir` 被记忆，下次对话框默认指向同一目录
- [ ] 单文件复制失败（权限/打开中）→ 进 warnings 列表展示，其它文件继续

## 风险

- **大文件复制阻塞 UI**：用 `QProgressDialog` + 简单分块拷贝，或在工作线程跑。本期可接受同步阻塞（弹一个"正在导出..."的不可关闭对话框即可），优化留后续
- **跨盘路径长度限制**：Windows 默认 260 字符。本期不处理，文件名前缀 `<id>__` 已经在帮忙缩短；未来可加 `\\?\` 长路径前缀
- **link 模式文件源已失效**：复制时跳过、记录 warning，不阻塞流程
- **safe_title 撞库**：用 `unique_dirname` 加序号，不静默覆盖

## 依赖

- 不强依赖任何其它 task
- 与 `tasks/05` T7（项目导入导出）的关系：本 task 是 T7 的"导出半"的最小版本；将来做 T7（含导入）时复用 `app/exporter.py` 与 `project.json` schema
- 与 `tasks/08`（多库切换）：本 task 完成后立即可作为"跨库搬项目"的临时手段，先导出再到另一库手动放文件夹（**手动**导入会很麻烦，所以导入功能仍要做）

## 工作量拆分

| 子项 | 估算 |
|---|---|
| `app/exporter.py` + 基本错误处理 | 0.4 天 |
| `app/ui/export_dialog.py` | 0.3 天 |
| main_window 集成（工具栏 + 右键 + action） | 0.2 天 |
| README.md 自动生成模板 + 校验逻辑 | 0.1 天 |
| 验收测试 + 文档（PRIVACY 提一笔：导出物里包含字段定义/元数据） | 0.1 天 |
| **合计** | ~1.1 天（S 偏上） |

## 后续扩展

- **ZIP 模式**：选项加"打包成 ZIP"复选框（用 `zipfile` 流式写）
- **批量导出**：多选项目 → 每个项目一个子目录，共享同一个根
- **导入**：见 `tasks/05` T7，复用 `project.json` schema 反向解析
- **包含 LLM 历史**：选项加"包含 LLM 任务历史"复选框（导出最近 N 条相关任务的 result_json，作为审计/调试材料）
- **加密导出包**：对包含 API 上下文敏感信息的导出物可选 AES 加密
