# 08 · 多项目库并存与切换（Calibre 风格）

**工作量**：M  
**优先级**：P1  
**状态**：待做

## 来源

`TODO.md → 📦 项目 & 文件管理` 的演进（与 `tasks/05` T6『迁移单一库』正交，不是同一回事）。

## 目标

支持**同时存在多个完全独立的项目库**，每个库有自己的 SQLite + library/ 目录 + 字段定义 + LLM 配置，
用户可以通过菜单在它们之间切换。典型场景：

- 「工作资料库」「个人收藏库」「实验沙盒」三库并存，互不污染
- 把库目录放在 OneDrive / NAS 上，在不同机器上打开同一个库
- 给项目按重要性分库（敏感资料用本地库，其它放云盘库）

## 与已有 task 的关系

| task | 关系 |
|---|---|
| `05` T6『迁移单一库』 | 正交。05 解决"现有库搬家"，本 task 解决"多库并存切换"。05 可后做或不做（多库下用户可直接新建一个库再导入项目，替代迁移） |
| `05` T7『项目导入导出』 | 强相关。多库的"跨库搬项目"完全靠 T7 实现，本 task 不重复造轮子 |
| 数据库迁移机制（`docs/migrations.md`） | 复用现有的 `SCHEMA_VERSION` + 自动备份 + `MIGRATIONS`。打开任何库都走同一套逻辑 |

## 设计决策（已确认）

| 维度 | 决策 |
|---|---|
| 库语义 | **多库并存**（A 方案）。不是路径迁移 |
| 最近库列表 | **保留最近 5 个**，菜单展示；启动默认打开上次活动的库 |
| 切换方式 | **重启进程**。规避热切换 LLM worker / db connection / 主窗口状态的复杂度，工程量小，稳定性优先 |
| LLM 配置 | **默认每库独立**（落 db 的 `settings.llm_config`，与现状一致）；额外提供「从其它库导入 API 配置」菜单项 |

## 库的物理形态

一个"项目库"就是一个目录，目录里有：

```
<library-root>/
├── cabinet.db                    # 该库的 SQLite（schema 与现有完全一致）
├── library/                      # 该库的"copy 模式"文件仓储根
│   └── project_<id>/...
├── cabinet.v*.bak                # 自动迁移备份（已有机制，无需改）
└── .llm-cabinet                  # 标记文件（空文件 / 含元数据 JSON），用于识别"这是 LLM Cabinet 的库目录"
```

> 关键约束：**db 文件 + library/ 同根**。这样备份/迁移/同步只需要拷一个目录；
> 也消解了 `tasks/05` T6 中"db 和 library 可分别选目录"的复杂度 —— 本 task 直接强约束在一起。

历史的 `%APPDATA%/LLMCabinet/cabinet.db` 与 `%APPDATA%/LLMCabinet/library/` 视作"默认库"，
不需要迁移，直接登记进库列表即可。

## 全局配置（跨库）

新增一个**全局配置文件**（与任何库都解耦）：

```
%APPDATA%/LLMCabinet/cabinet.json
```

内容大致：

```json
{
  "active_library": "C:/Users/me/Documents/CabinetLibs/work",
  "recent_libraries": [
    {"path": "C:/.../work",     "label": "工作资料",   "last_opened": "2026-06-01T10:30:00"},
    {"path": "C:/.../personal", "label": "个人收藏",   "last_opened": "2026-05-30T22:15:00"},
    {"path": "%APPDATA%/LLMCabinet", "label": "(默认库)", "last_opened": "2026-05-28T08:00:00"}
  ]
}
```

为什么不用 SQLite 的 `settings` 表存：**它属于库，跨库就读不到了**。必须用一个跨库的位置。

## 实现要点

### A. 数据层

1. **新模块 `app/cabinet.py`**：管理「库目录」「全局配置」抽象。
   - `LibraryHandle(path: Path, label: str, last_opened: datetime | None)` 数据类
   - `CabinetConfig`：读写 `%APPDATA%/LLMCabinet/cabinet.json`；维护 `active_library` / `recent_libraries` 列表（去重、按 `last_opened` 倒序、限 5 条）
   - `resolve_library_paths(root: Path) -> (db_path, library_subdir)`：从根目录派生出 `cabinet.db` 与 `library/`
   - `is_valid_library(root: Path) -> bool`：根目录存在且含 `cabinet.db` 或 `.llm-cabinet`，或者目录为空（用于"新建库"）
   - `mark_as_library(root: Path) -> None`：在新建库时写入 `.llm-cabinet` 标记文件

2. **`app/main.py` 改造启动序列**：
   ```python
   cabinet = CabinetConfig.load()
   root = resolve_startup_root(cabinet)   # 优先 active_library；失败则降级到默认 %APPDATA%/LLMCabinet
   db_path, lib_root = resolve_library_paths(root)
   ```
   不再从 `app_data_dir()` 直接拼路径。

3. **`Library` 类无需改动**：它只接受一个 root 路径，本来就解耦得好。

4. **不需要 schema 迁移**。本 task 不动 `cabinet.db` 的 schema —— 每个库的 db 都还是 v2。

### B. UI / 入口

主窗口菜单栏新增 **「库」** 顶级菜单（或者归入 文件 子菜单）：

```
库
├── 切换库...                          Ctrl+Shift+O   → 弹目录选择器
├── 新建库...                          Ctrl+Shift+N   → 选空目录，初始化 cabinet.db + library/
├── ─────
├── 最近打开
│   ├── 工作资料  (C:/.../work)
│   ├── 个人收藏  (C:/.../personal)
│   ├── (默认库)  (%APPDATA%/LLMCabinet)
│   ├── ─────
│   └── 清空列表
├── ─────
├── 当前库信息...                                     → 展示路径、项目数、db 大小
└── 从其它库导入 API 配置...                          → 选择另一个库的 db 路径，读出 settings.llm_config，写到当前库
```

#### 切换流程

1. 用户点击「切换库 → ...选目录」
2. 校验目录合法性：
   - 已存在 `cabinet.db` → 直接当库打开（顺便校验 `user_version <= SCHEMA_VERSION`，若大于则警告）
   - 目录为空 → 询问"该目录还没有库，是否在此**新建**？"
   - 既不合法又非空 → 报错"目录中有其它文件，不适合作为新库目录"
3. 校验通过 → 弹**确认对话框**，提示"应用将重启以切换到新库，是否继续？"
4. 用户确认 → 写入 `cabinet.json`（更新 `active_library` + 推入 `recent_libraries`）
5. 触发应用重启：
   ```python
   QApplication.quit() + os.execv(sys.executable, sys.argv)
   ```
   PyInstaller onefile 下 `sys.executable` 是 exe 路径，`os.execv` 正常工作

#### 新建库流程

类似切换，但额外步骤：
- 让用户给库起一个**label**（如"工作资料"），仅作显示用
- 在目标目录创建 `library/` 子目录 + `.llm-cabinet` 标记
- 调用 `db.connect()` 创建空 db（自动种子 fields、打 user_version=SCHEMA_VERSION）
- 然后走切换流程的"重启"步骤

#### 「从其它库导入 API 配置」

应对场景：用户在库 A 配好 API Key，新建库 B 后不想重填。

1. 选 B 库时不弹此项（无 db 可读）；选**当前活动库**时此项可用
2. 弹文件选择器，选另一个库的 `cabinet.db`
3. **以只读方式**临时打开：`sqlite3.connect("file:?mode=ro&...", uri=True)`
4. 读 `SELECT value FROM settings WHERE key='llm_config'`
5. 二次确认（展示要写入的 provider 列表，让用户勾选），写入当前库
6. 关临时连接

### C. 设置页改造

「设置 → 项目库」页：

- 顶部新增一行：**当前库**：`<label>` → `<path>`（带"📂 打开"按钮）
- 已有的"库根目录"路径文本框 → 改为展示**当前库内**的 `library/` 子目录（仍只读）
- 加一个按钮 **🔀 切换到其它库...** → 触发主菜单中的"切换库"流程

### D. 启动期降级 / 异常路径

| 异常情况 | 降级策略 |
|---|---|
| `cabinet.json` 不存在 | 视作首次启动：用默认库（`%APPDATA%/LLMCabinet/`）创建一个 entry，正常启动 |
| `cabinet.json` 损坏 / JSON parse 失败 | 备份为 `cabinet.json.bak.<时间戳>` → 重建为默认；不阻塞启动 |
| `active_library` 路径不存在（用户删了/移动了目录） | 启动时弹对话框：「上次的库 X 不可用，请选择库」→ 默认按钮"打开默认库"，备选"选择其它目录" |
| `active_library` 的 db 是更高 `user_version`（用户用新版客户端建库后又退回旧版） | 拒绝打开，弹对话框引导用户升级客户端 / 选其它库 |
| 用户在不同机器上路径不同（OneDrive 同步场景） | 文档中说明：`cabinet.json` 是机器本地的；同一个库目录在新机器上要手动"切换库 → 选目录"一次 |

## 数据迁移（对老用户）

老用户首次启动新版本时：

1. 检查 `cabinet.json` 不存在
2. 检查 `%APPDATA%/LLMCabinet/cabinet.db` 存在
3. 自动生成 `cabinet.json`，其中 `active_library = %APPDATA%/LLMCabinet/`，并往 `recent_libraries` 推入一条
4. 用户**无感**地继续使用，菜单里出现「库」选项可探索新功能

无需 schema 迁移。

## 验收

- [ ] 启动应用：默认打开 `%APPDATA%/LLMCabinet` 库（对老用户保持行为不变）
- [ ] 菜单「库 → 新建库」选空目录 X，确认；应用重启后主窗口空白、`X/cabinet.db` 与 `X/library/` 存在
- [ ] 在 X 库里添加几个项目，关闭应用
- [ ] 再次启动：默认打开 X 库（被记忆）
- [ ] 「库 → 最近打开」中看到 X、默认库两项
- [ ] 切换回默认库 → 看到旧项目，X 库的数据不可见
- [ ] 在 X 库菜单「从其它库导入 API 配置」→ 选默认库 db → API Key 复用
- [ ] 把 X 的目录手动删掉，再启动应用 → 弹"X 不可用"对话框，能优雅降级
- [ ] `cabinet.json` 被手动破坏成乱码 → 启动备份后重建，不崩溃
- [ ] 默认库目录 `%APPDATA%/LLMCabinet` 不能从最近列表里"删除"（或允许删但会被自动补回，避免找不到根）

## 风险与权衡

- **重启切换的用户体验**：会闪烁、丢失主窗口当前选中状态。可接受，因为切换库本来就是低频操作；如果将来用户量大且呼声高再考虑热切换
- **PyInstaller onefile + `os.execv`**：理论上工作，但 onefile 模式下进程重启会重新解包 `_MEIPASS`，多 1-2 秒。验收时实机验证一遍
- **OneDrive / 网盘库**：跨设备同步 db 文件可能引发 sqlite 锁冲突（同时打开两个客户端）。本 task 不解决，但文档提示
- **"从其它库导入 API 配置"的安全性**：用户可能误选了别人的库 → 弹窗里必须明确显示"将覆盖当前库的所有 provider 配置"，并默认不勾选任何项

## 工作量拆分

| 子项 | 估算 |
|---|---|
| `app/cabinet.py` + 单测 | 0.5 天 |
| `app/main.py` 启动序列改造 | 0.5 天 |
| 「库」菜单 + 各对话框（切换/新建/导入 API） | 0.5 天 |
| 设置页改造 | 0.25 天 |
| 异常路径处理 + 老用户迁移 | 0.25 天 |
| 验收脚手架 / 文档（PRIVACY 提一笔、README 补一句） | 0.25 天 |
| **合计** | ~2.25 天（M 偏上） |

## 后续可延伸

- 库标签/颜色（让"工作资料"显示成红色徽章）
- 库级别的密码保护（库目录内加密的 `.lock` + 启动时输入密码 → 暂不实现）
- 跨库搜索（聚合多库的项目元数据）
- 与 task 05 T7 联动：「在最近打开的另一个库中查找同名项目」
