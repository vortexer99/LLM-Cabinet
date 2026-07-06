# Changelog

本项目沿用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

`__version__`（应用版本）和 `SCHEMA_VERSION`（数据库 schema 版本）独立递增。
schema 变化的发布需要在条目里显式标注 `📦 schema vX → vY` 并附迁移说明。

## [Unreleased]

📦 schema v7 → v8 — `files` 表新增 `origin` 列（`user`=用户原始文件 / `generated`=软件衍生物）。

### Added
- **基础搜索（task #03 Phase A）**：启用主窗口顶部搜索框，按标题/描述关键词过滤项目，支持与左侧标签、标签父节点、未分类、待审阅和 MCP 修改筛选叠加为 AND；MCP `query_projects(action="search")` 同步支持 `tag_prefix`。
- 新增 `selftests/task03_search_phase_a.py`，覆盖标题/描述关键词、keyword + tag/tag_prefix、未分类 keyword 与 MCP 搜索入口。
- 新增 `selftests/task_status_consistency.py`，检查任务卡头部状态与 `tasks/README.md` 索引表完成度类别是否一致，并纳入 selftests 索引。
- 新增 `selftests/task31a_files_tree_interactions.py`，覆盖文件树 `subfolder` 更新、递归重命名与显式空文件夹设置。
- **文件来源标记（task #30）**：`files` 表新增 `origin` 列，区分用户原始文件与软件衍生物（如封面快照）。
  - 新生成的封面快照自动标记 `origin='generated'`。
  - 迁移时自动回填历史封面快照 `__cover_*.png` 为 `generated`。
  - 为后续 #28 导出/导入闭环、#04 文件折叠提供数据基础。
- **文件折叠二态视图（task #04）**：文件表上方新增 👤/🌐 toggle，切换「仅用户文件」/「显示所有」。无 generated 文件时自动隐藏，按项目持久化。
- **导出选项扩展（task #28 T1）**：
  - 导出模式：导出为独立包 / 仅导出项目元数据
  - 导出格式：目录形式 / ZIP 打包
  - 文件目录结构：保留目录结构 / 拍平到 files/
  - 内容选项：包含 README.md、LLM 任务历史
  - 封面图始终复制（即使不勾选"复制链接文件"）
  - files.json 新增 subfolder / is_cover / origin 字段（schema @3）
- **批量导出（task #28 T2）**：
  - 项目列表多选后导出，自动进入批量模式
  - 批量对话框显示项目列表（含文件数）
  - 可选择导出的项目
  - 进度条显示批量导出进度
- **导入增强（task #28 T3）**：
  - ZIP 包导入支持（自动解压到临时目录）
  - 封面图还原（cover_file_id 按新 file id 重映射）
  - 拍平结构目录树还原（优先用 files.json 的 subfolder）
  - 文件来源标记还原（origin: user/generated）
  - 菜单「工具 → 导入项目包...」入口
  - 拖放 ZIP 时可选择导入项目包或解压为文件夹
- **文件存储位置管理（task #29）**：
  - T1 链接转仓储：文件表右键「📦 转为仓储文件」，复制外部文件进库
  - T2 移动文件到新位置：文件表右键「📂 移动文件到...」，物理移动并更新路径
  - T3a 重关联到外部文件：文件表右键「🔧 重关联到外部文件...」，按文件名匹配修复 missing
  - T3b 替换链接目标：文件表右键「🔗 替换链接目标...」，单选链接文件替换目标
- **文件视图增强（task #31a / #31b）**：
  - #31b 扁平视图模式：文件表新增「🌲/📋」按钮切换树形/扁平视图，按项目记忆
  - #31b 新增列：文件「大小」「添加时间」列（自动 stat 物理文件获取大小）
  - #31b 扁平视图排序：点击列头可排序（按项目记忆）
  - #31a 新建文件夹：文件表空白处/目录节点右键「📁 新建文件夹」
  - #31a 重命名：文件表右键「✏️ 重命名」修改文件说明（label）
  - #31a 树形视图列排序：同级内文件可按文件名/说明/类型/大小/添加时间/存储排序，文件夹始终置顶，按项目记忆排序状态
  - #31a 树形视图内部拖动：支持同级改序、拖入文件夹、拖回顶层，多选移动后写回 `files.ord` / `files.subfolder`
  - #31a 空文件夹持久化：显式新建的空 subfolder 存入 `project_settings.explicit_subfolders`，可右键删除空文件夹
  - #31a F2 进入说明列编辑，Shift+F2 可重命名仓储/链接文件的物理文件名；目录节点 F2 可重命名 subfolder
- **项目列表多选（task #25 Phase A/B/C）**：
  - 卡片视图/表格视图支持 Ctrl+点击多选、Shift+范围选择
  - 多选时预览区显示"已选 X 个项目"
  - 右键菜单区分单选/多选
  - 批量标记已读 MCP 修改
  - 批量删除（显示项目数量和文件统计）
  - 支持从项目列表拖动一个或多个项目到左侧标签/标签父节点，为项目批量追加标签

### Changed
- 开发约定改由 `AGENTS.md` 作为单一来源，`CLAUDE.md` 仅保留到该文件的导入指针；同步修正任务卡与 `tasks/README.md` 中已完成/进行中任务状态。
- 任务规划重组（基于 `docs/file-handling.md` 评审）：
  - 新增 `tasks/32-cross-project-file-reference.md`：跨项目链接引用最小方案（路径共享 + 多引用警告 + #14 跨项目引用报告 + Windows path `normcase` 归一化 + 文件表角标提示），零 schema 改动。
  - 拆分 `tasks/31-...`：原卡保留为指针，新增 `tasks/31a-files-tree-interactions.md`（树形视图拖动 / 同级排序 / 新建空 subfolder / F2 重命名）+ `tasks/31b-files-table-flat-view.md`（扁平视图模式 + 大小/添加时间列 + Qt 原生列排序）。排序持久化按视图分键 `files_table_sort_tree` / `files_table_sort_flat`。
  - 扩展 `tasks/29-file-storage-location-management.md`：原 T1/T2 之外新增 T3a 重关联到外部文件（修复 missing）/ T3b 替换链接目标（单选）/ T3c 文件夹粒度批量入口。
  - 收尾 `tasks/04-project-system-files-folding.md`：从"待澄清"敲定为"二态视图（仅用户文件 / 显示所有）"，默认显示所有 + 无 generated 时 toggle 自动隐藏，消费 #30 origin。
  - 同步 `docs/file-handling.md` 任务地图、推荐执行顺序、`tasks/README.md` 索引表、`TODO.md` 条目。

## [0.5.0] - 2026-06-10

📦 schema v6 → v7 — `files` 表新增 `subfolder` 列（逻辑子目录路径，驱动 UI 树形展示）。

### Added
- **子文件夹导入 + 文件树形展示（task #17）**：拖入含子目录的文件夹时，子目录文件全部导入并保留逻辑目录结构。
  - `files` 表新增 `subfolder` 字段，UI 文件表从 `QTableWidget` 改为 `QTreeWidget`，按 `subfolder` 折叠展示。
  - 物理存储与 UI 组织解耦：仓储模式继续拍平，`subfolder` 纯粹是数据库字段。
  - 删除文件后空逻辑目录自动消失；选中目录节点可连带删除整棵子树。
- **文件列表独立窗口（task #02）**：文件区标题行新增 ⇱ 按钮，点击后文件列表脱离主窗口成为独立 700×500 窗口；关闭窗口或再次点击（变 ⇲）自动收回到主窗口。独立窗口里仍能选文件、右键菜单、拖入新文件，预览面板与主窗口同步。
- **导入深度/文件数检查**：目录层级 ≥ 5 或文件数 ≥ 500 时弹确认对话框。
- **空文件夹处理**：单个空文件夹拖入创建 0 文件项目；多个空文件夹弹提示告知用户。
- **导入安全防护**：禁止导入库目录自身，防止递归导入。
- **隐藏文件过滤**：设置项「忽略 . 开头的文件和文件夹」（默认开启），导入时跳过 `.gitignore`、`.env`、`.git/` 等隐藏文件和目录。
- README 新增 "Looking Further: AI Team Workspace" 章节（英文/中文），引入 `gallery/AI-Team-Workspace-Concep.jpg` 概念图，说明 Cabinet 作为多 Agent 共享记忆中枢的远期定位。`gallery/README.md` 增加 Concept artwork 索引段。
- 新增任务卡 `tasks/27-provenance-source-link.md`：MCP 写入可附带 `source`（依据文件 + 备注），UI 显示「来源」入口；作为推向 AI Team Workspace 定位的第一步。
- 新增任务卡 `tasks/28-export-structure-option.md`：导出项目时可选保留目录结构或拍平（依赖 #17）。

### Changed
- 合并模式导入目录时始终保留目录名作为 `subfolder` 前缀（如拖入 `myfolder/a.txt` → `📁 myfolder/`）。
- README 中新增 1.0.0 前尝鲜提醒（英文/中文），不建议投入重度使用。

## [0.4.1] - 2026-06-05

### Added
- MCP 集成截图（Agent 端 + Cabinet 端），放入 `gallery/` 并在 README 中引用。
- 项目右键菜单新增「已读MCP修改」选项：清除项目的 MCP 修改标记。对所有项目常驻显示，方便以后多选批量操作。

### Changed
- `TODO.md` 清理：修正过期状态标记、简化索引表（单一链接指向 `tasks/README.md`）、
  更新版本规划。
- "MCP 修改过"标签改为「未读 MCP 修改」，语义更准确。

### Fixed
- 标签从项目移除后仍然残留于 `tags` 表（孤儿标签）；现 `_set_tags` 末尾自动清理。
- 关闭 MCP 审计对话框后调用不存在的方法名 `_update_mcp_count` 导致闪退；修正为 `_check_mcp_activity`。

## [0.4.0] - 2026-06-04

📦 schema v4 → v5 — 添加 `mcp_audit` 审计日志表。
📦 schema v5 → v6 — `projects` 加 `mcp_modified_at` 列，MCP 写操作自动打标。

### Added
- **MCP Server**：通过 Model Context Protocol 把库暴露给外部 AI agent（Claude Desktop /
  Cursor / Cline / Cherry Studio）。5 个聚合工具 + 8 个数据资源 + 4 个任务提示，支持三种写权限控制。
  独立进程 `python -m app.mcp.standalone` 与 GUI 并行运行，SQLite WAL 保证并发安全。
- **MCP 操作记录查看面板**：状态栏 `📋 MCP 操作` 入口，双 Tab 对话框（审计日志 +
  被 MCP 修改过的项目），支持筛选、翻页、清空。标签树新增"🤖 MCP 修改过"筛选项。
- **Agent 技能系统**：4 个内置 SOP（整理入库 / 审核质量 / 生成概览 / 推荐标签），
  YAML frontmatter + 目录结构，`prompts.py` 从文件加载。Release 页可单独下载
  `llm-cabinet-skills.zip`。
- **MCP 增强**：`get_fields` action / `default_storage_mode` / `field_values` 参数 /
  audit 日志补全 / `label` 强制要求 / `storage_mode` 遵循库设置。
- **CI 技能打包**：每次构建同时产出技能 zip 包。
- **UI 优化**：隐藏未使用标签、应用数据目录移至通用设置页、MCP 设置页更新。

### Changed
- **BREAKING · MCP 工具收敛**：17 个细粒度工具 → 5 个聚合工具（`query_projects` /
  `manage_project` / `manage_files` / `manage_libraries` / `export_project`）。
  已接入的 MCP 客户端需更新配置，重新导出 JSON。

### Removed
- `trigger_llm_suggestion` / `apply_suggestion` / `list_pending_suggestions`（agent
  套 AI 反模式，agent 场景无意义）
- `import_folder`（`create_project` + 多次 `add_file` 组合替代）

---
📎 0.3.0 起的完整提交历史见 [备份](CHANGELOG.backup.md)。

## [0.3.0] - 2026-06-04

📦 schema v2 → v3 — `fields.prompt_hint` + `files.missing` 列。
📦 schema v3 → v4 — 废弃系统字段的 `projects` 列（author/date/source_url/rating），
统一存入 `project_field_values`。

### Added
- **字段助手两段式重构**：Step 1 审阅 LLM 建议 ↔ Step 2 编辑字段表，消除一张表两种语义混乱。
- **字段助手 UI 优化**："LLM 建议"列文案用户友好化（新增/删除/修改 × 维度）、
  场景页描述编辑、库描述批准/驳回、LLM 显式删除/改名建议、类型变更安全护栏。
- **字段存储统一**：4 个系统字段值从 `projects` 列分流到 `project_field_values`，
  所有非保护字段值存储完全统一。
- **库字段设计助手打磨**：场景页提示 · 库描述批准/驳回 · LLM 显式删除/改名建议 ·
  改名+改类型组合处理 · 多次交互修复（10+ rounds）。
- **库管理增强**：一致性检查（扫描失效文件 + 标记缺失）· 备份/恢复（zip 打包）·
  搬家指引。
- **多库并存与切换**：完整的库注册表（最近 / 切换 / 新建 / 删除 / 从列表移除）。
  当前库也可删除/移除，事后重启走 Welcome 重新选库。
- **新建库 onboarding**：多页向导（目录+名称 / 库描述 / 默认字段 / API 迁移）+
  首次进入引导横幅 + Welcome 对话框三档选择。
- **字段级 LLM 提示**：每个字段可单独配置格式说明（如"标题不超过 30 字"），
  prompt 拼装时作为上下文注入。
- **更多文档格式提取**：pptx / odt / odp / ods / epub / html / rtf 的内容提取，
  纯标准库零依赖。
- **标签层级折叠**：`/` 为分隔符，标签树按前缀分组，折叠状态持久化。
- **批量文件夹导入**：多文件夹拖入识别 `project.json`，支持 auto-create/append 字段策略。
- **启动期 crash logger**：全局 `sys.excepthook` 写 `crash.log`，弹 QMessageBox 提示。

### Changed
- **类型变更加安全护栏**：改类型时弹确认（列出受影响的记录/pending 建议/旧提示），
  project_field_values 原值保留不动。
- 新建库默认仅创建标题/标签/描述三个保护字段（可选预置作者/日期/评分/来源）。
- 多文件夹拖入默认行为改为"分别建一个项目"（可切换为合并）。
- 字段助手 type_conflict 改为批准=原地改/驳回=不动（不再绕路创建新字段）。
- LLM 建议列去掉决策态后缀，驳回不改变维度展示。

### Fixed
- 文件表表头最后一列空白区深色模式发白。
- 工具栏导出/右键菜单 LLM 建议因 `bool` 被当 `int` 传入导致无反应。
- 项目编辑 date 字段默认今天且无法置空（改为可选留空 + 📅 弹日历）。
- 深色模式下设置页 GroupBox 白条遮标题、新建库向导 hint 文字不可见。
- `human_size()` 重复定义。

### Notes
- 深色模式从 v0.3.x 起不再维护（保留现有样式但不修新增功能的适配 bug）。

---
📎 0.3.0 详细的 round-by-round 修复记录见 [备份](CHANGELOG.backup.md)。

## [0.2.0] - 2026-05-31

📦 schema v1 → v2 — 移除残留 `custom_fields` 旧表。

⚠️ BREAKING：应用数据目录由 `%APPDATA%/Fileman/` 改为 `%APPDATA%/LLMCabinet/`，
默认数据库由 `fileman.db` 改为 `cabinet.db`。v0.1.0 用户需手动搬移目录。

### Added
- 项目导出（`project.json` + 文件副本 + README.md）
- 数据库迁移注册表首次启用
- 关于页新增免责声明

### Fixed
- 关于页图标在多分辨率 ico 下模糊
- 工具栏导出/右键菜单 LLM 建议点击无反应（`bool` 被误判为 `int`）

---

## [0.1.0] - 2026-05-31

初始版本。项目化文件管理（卡片/列表双视图）、字段系统、标签筛选、
文件预览、拖放、LLM 元数据助手（DeepSeek / OpenAI / Gemini / Grok）。

📦 schema v1 — 初始 schema。

[Unreleased]: https://github.com/vortexer99/llm-cabinet/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/vortexer99/llm-cabinet/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/vortexer99/llm-cabinet/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.4.0
[0.3.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.3.0
[0.2.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.2.0
[0.1.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.1.0
