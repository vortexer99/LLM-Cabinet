# Changelog

本项目沿用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

`__version__`（应用版本）和 `SCHEMA_VERSION`（数据库 schema 版本）独立递增。
schema 变化的发布需要在条目里显式标注 `📦 schema vX → vY` 并附迁移说明。

## [Unreleased]

📦 schema v6 → v7 — `files` 表新增 `subfolder` 列（逻辑子目录路径，驱动 UI 树形展示）。

### Added
- **子文件夹导入 + 文件树形展示（task #17）**：拖入含子目录的文件夹时，子目录文件全部导入并保留逻辑目录结构。
  - `files` 表新增 `subfolder` 字段，UI 文件表从 `QTableWidget` 改为 `QTreeWidget`，按 `subfolder` 折叠展示。
  - 物理存储与 UI 组织解耦：仓储模式继续拍平，`subfolder` 纯粹是数据库字段。
  - 删除文件后空逻辑目录自动消失；选中目录节点可连带删除整棵子树。
- **导入深度/文件数检查**：目录层级 ≥ 5 或文件数 ≥ 500 时弹确认对话框。
- **空文件夹处理**：单个空文件夹拖入创建 0 文件项目；多个空文件夹弹提示告知用户。
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

[Unreleased]: https://github.com/vortexer99/llm-cabinet/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/vortexer99/llm-cabinet/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.4.0
[0.3.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.3.0
[0.2.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.2.0
[0.1.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.1.0
