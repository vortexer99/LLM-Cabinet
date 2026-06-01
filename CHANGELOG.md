# Changelog

本项目沿用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

`__version__`（应用版本）和 `SCHEMA_VERSION`（数据库 schema 版本）独立递增。
schema 变化的发布需要在条目里显式标注 `📦 schema vX → vY` 并附迁移说明。

## [Unreleased]

📦 schema v2 → v3 — 一次合并迁移：`fields` 表新增 `prompt_hint` 列（task #11 T1）、
`files` 表新增 `missing` 列（task #14 T1 库一致性检查）；
打开旧库会自动生成 `cabinet.vN.<时间戳>.bak` 备份后再迁移。

### Added
- **库管理增强（task #14）**：新增主菜单 **「工具」**：
  - **🔍 检查库一致性...**：扫描所有文件物理位置；失效项报告含项目/文件名/存储模式/原始路径，三档处理（仅查看 / 标记为缺失 / 从项目移除）。被标记的文件在文件表里显示 ⚠ 图标
  - **📦 备份此库...**：把整个库目录打成 zip（自动 WAL checkpoint、记忆上次备份目录）
  - **📥 从备份恢复库...**：从 zip 解到空目录，确认后自动切换到新库
- **多项目库并存与切换（task #08）**：每个"库"是一个完整的目录（含 `cabinet.db` + `library/` + `.llm-cabinet` 标记）。新增主菜单 **「库」**：
  - 切换库... (Ctrl+Shift+O) / 新建库... (Ctrl+Shift+N) / 当前库信息... / 从其它库导入 API 配置...
  - 「最近打开」子菜单（默认 5 个，默认库永驻），含「管理列表...」对话框：右键支持「从列表移除 / 删除整个库... / 改名...」
  - 切换走应用重启（`os.execv`），稳定且简单
  - 跨库全局配置存于 `%APPDATA%/LLMCabinet/cabinet.json`；损坏自动备份重建
  - 当前库 label 显示在标题栏；当前活动库与默认库的"删除/移除"菜单项强制 disabled
- **字段级 LLM 提示（task #11 T1/T2/T4）**：
  - 「设置 → 字段」每个字段加一列「LLM 提示」按钮，点击弹文本编辑器，自定义该字段在 LLM 建议时的格式说明（如"标题不超过 30 字"、"描述 200~400 字分段说明"）。留空 = 使用默认。单条最大 500 字超限自动截断
  - prompt 拼装时把每个字段的 `prompt_hint` 注入到 user prompt 中"字段格式要求"区段；「查看 Prompt」对话框可见拼接结果
  - 项目导出包 schema 升级到 `llm-cabinet/project-export@2`，`fields_snapshot` 携带 `prompt_hint`；导入端在"自动创建字段"路径下还原。`@1` 老包仍兼容（缺失字段视为空）
  - `Repository` 新增 `add_fields_batch(fields_data)` 事务化批量接口，为 #11 T3 库初始化向导预备
- **标签层级折叠（task #06）**：约定 `/` 为标签层级分隔符（如 `领域/科幻`、`领域/工具书`），左侧标签树自动按第一段做前缀分组，父节点可折叠/展开。点击父节点 = 同时筛选父标签自身 + 该前缀下所有子标签的项目。折叠状态持久化在 `settings.tag_tree_collapsed_prefixes`。零数据迁移。
- **批量文件夹导入（task #10）**：把多个文件夹拖到底部 DropZone，先选「单/多项目」模式；
  选「分别建立」时弹出批量导入对话框，可：
  - 识别文件夹根目录下的 `project.json`（task #09 导出物）并恢复元数据 / 字段值 / 标签
  - 选择文件存储模式（🔗 链接 / 📦 复制到仓储）和标题来源（project.json / 文件夹名）
  - 对**库内不存在的字段**选择处理策略：自动创建 / 追加到描述（默认）/ 忽略；
    可勾选"应用到本次所有项目"批量决策，否则逐项目询问
  - 兼容**未来版本**生成的 `project.json`（schema `@N` 大于本机已知最高版本时仍尝试恢复核心字段，
    并在状态列标注"更新版本生成"）
- 标签自动创建：导入项目时碰到库内不存在的标签会**直接创建**（沿用 Repository 现有行为）。

### Changed
- 拖到 DropZone 的对象**全是目录且 ≥ 2 个**时，行为由"全部并入一个新项目"改为
  先弹模式选择对话框（默认"分别建立"）；旧的合并行为通过对话框中的「合并为同一项目」保留。
  单个目录与含散文件的拖入行为不变。

### Fixed
-

### Deprecated
-

### Removed
-

---

## [0.2.0] - 2026-05-31

📦 schema v1 → v2 — 仅 `DROP TABLE IF EXISTS custom_fields`，不影响任何有效数据。

⚠️ **BREAKING**：应用数据目录与默认数据库文件名变更。详见下方 Changed 段。

### Added
- **项目导出（基础版，task #09）**：工具栏 `📤 导出项目` + 项目右键菜单
  `📤 导出项目…` 入口；导出对话框含路径选择器与"复制链接模式（🔗）原始文件"
  开关；产物为目录形式（`project.json` / `files.json` / `README.md` / `files/`），
  含字段定义 snapshot 与应用/schema 版本号，作为未来导入功能的标准结构。
- 数据库迁移注册表首次启用：新增 `_migrate_v1_to_v2`，删除 v0.1.0 前残留
  的空 `custom_fields` 表。打开旧 v1 库会自动生成 `cabinet.v1.<时间戳>.bak`
  备份后再迁移。
- 关于页新增"免责声明"行；`README` 顶部"注意"段、`PRIVACY` 末尾新增「7. 免责声明」。
- `PRIVACY` 新增「3.A 关于导出项目功能」小节，描述导出物的结构与敏感性提示。

### Changed
- ⚠️ **BREAKING**：应用数据目录由 `%APPDATA%/Fileman/` 改为 `%APPDATA%/LLMCabinet/`，
  默认数据库文件名由 `fileman.db` 改为 `cabinet.db`，自动备份命名相应改为
  `cabinet.vN.<时间戳>.bak`。环境变量 `FILEMAN_DND_DEBUG` 改名为 `LLMCABINET_DND_DEBUG`。
  **不再保留向旧路径的兜底**——升级后应用启动时若新路径不存在数据将视为全新库。
  **手动迁移步骤**（仅 v0.1.0 用户需要）：
  1. 关闭应用
  2. 把 `%APPDATA%/Fileman/` 整个目录改名为 `%APPDATA%/LLMCabinet/`
  3. 进入该目录，把 `fileman.db` 改名为 `cabinet.db`
  4. 自动备份文件（如 `fileman.v1.<时间戳>.bak`）若需保留，可手动改名前缀为 `cabinet.`
- **默认主题改为浅色**（Light）。已有用户存过 `theme` 设置不受影响；
  设置页下拉选项顺序调整为「浅色 / 深色」。
- LLM 元数据建议对话框：明确提示"无论是否勾选参考文件，**所有文件名**都会作为
  项目结构上下文发送给 LLM"。`PRIVACY` 相应段落（§2.2 / §5）同步强调。
- 工具栏简化：移除冗余的「▶ 打开 / 📂 在资源管理器中显示」两个按钮（文件区底部
  按钮、文件双击、文件右键菜单仍可访问相同功能）；`Ctrl+Return` 快捷键随之移除。
- 应用图标统一改用 `icon.ico`（多分辨率 16/32/48/64/128/256，32-bit RGBA），
  CI 构建与本地打包命令一并切换。新增 `run.py` 作为 PyInstaller 顶层入口，
  规避 `app/main.py` 相对导入在 frozen 模式下的 `ImportError`。

### Fixed
- 关于页应用图标在多分辨率 ico 下变模糊：改用 `QIcon.pixmap(target_size)`
  让 Qt 从 ico 容器挑/合成最合适尺寸的子图；同时按 `devicePixelRatio` 适配高 DPI。
- 文件表表头最后一列右侧的空白区在深色主题下显示为白色：新增 `QHeaderView`
  顶层 `background` 规则（浅色主题同步修复）。
- 工具栏 `📤 导出项目` / 右键菜单 `✨ LLM 元数据建议…` 点击无反应：
  `QAction.triggered` 会传 `bool(checked)` 实参，而 Python 中 `bool` 是 `int` 的
  子类（`isinstance(False, int) == True`），导致 `False` 被当作合法 `pid` 进入
  `repo.get_project(False)`。修法：在判 `int` 之前先排除 `bool`。

### Removed
- 移除针对 v0.1.0 之前未发布 schema 的兼容兜底：`custom_fields` 旧表定义、
  `_migrate_custom_fields`、`_migrate_add_columns`、`_backfill_system_field_keys`
  中"空 key 回填"逻辑、以及 `_run_migrations` 中 `user_version=0` 但非 fresh 库
  的兜底分支。保留的"保护字段（title/description/tags）自愈"逻辑迁入新函数
  `_ensure_protected_fields`。后续 schema 变更一律走 `MIGRATIONS` 注册表。
- 移除 `app/ui/theme.py` 末尾的死代码 `QSS = QSS_DARK`。

---

## [0.1.0] - 2026-05-31

初始版本。

- 项目化文件管理（卡片墙 / 列表两种视图）
- 字段系统（系统字段 + 用户自定义字段，可改顺序、可见性、类型）
- 标签筛选（左栏树）
- 文件预览（图片 / 视频 / PDF 内嵌；其它调用系统默认）
- 拖放新建项目 / 加入项目
- LLM 元数据助手（DeepSeek / OpenAI / Gemini / Grok）
- 文件级存储方式（🔗 链接 / 📦 仓储），可同项目混合
- 数据库 schema v1

📦 schema v1 — 初始 schema，无需迁移。

[Unreleased]: https://github.com/vortexer99/llm-cabinet/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.2.0
[0.1.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.1.0
