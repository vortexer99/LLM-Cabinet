# Changelog

本项目沿用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

`__version__`（应用版本）和 `SCHEMA_VERSION`（数据库 schema 版本）独立递增。
schema 变化的发布需要在条目里显式标注 `📦 schema vX → vY` 并附迁移说明。

## [Unreleased]

### Added
- 数据库迁移注册表首次启用：新增 `_migrate_v1_to_v2`，删除 v0.1.0 前残留
  的空 `custom_fields` 表。打开旧 v1 库会自动生成 `fileman.v1.<时间戳>.bak`
  备份后再迁移。

### Changed
- 📦 schema v1 → v2 — 仅 `DROP TABLE IF EXISTS custom_fields`，不影响任何有效数据。

### Fixed
-

### Deprecated
-

### Removed
- 移除针对 v0.1.0 之前未发布 schema 的兼容兜底：`custom_fields` 旧表定义、
  `_migrate_custom_fields`、`_migrate_add_columns`、`_backfill_system_field_keys`
  中"空 key 回填"逻辑、以及 `_run_migrations` 中 `user_version=0` 但非 fresh 库
  的兜底分支。保留的"保护字段（title/description/tags）自愈"逻辑迁入新函数
  `_ensure_protected_fields`。后续 schema 变更一律走 `MIGRATIONS` 注册表。

---

## [0.1.0] - 2026-05-31

初始版本。

- 项目化文件管理（卡片墙 / 列表两种视图）
- 字段系统（系统字段 + 用户自定义字段，可改顺序、可见性、类型）
- 标签筛选（左栏树）
- 文件预览（图片 / 视频 / PDF 内嵌；其它调用系统默认）
- 拖放新建项目 / 加入项目https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
- LLM 元数据助手（DeepSeek / OpenAI / Gemini / Grok）
- 文件级存储方式（🔗 链接 / 📦 仓储），可同项目混合
- 数据库 schema v1

📦 schema v1 — 初始 schema，无需迁移。

[Unreleased]: https://github.com/vortexer99/llm-cabinet/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.1.0
