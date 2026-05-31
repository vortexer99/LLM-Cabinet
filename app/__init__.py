"""LLM Cabinet - 带 AI 元数据助手的轻量级项目化文件管理器。

版本管理两个独立维度：
- ``__version__``：应用版本（语义化），显示在 UI 与发布物中。
- ``SCHEMA_VERSION``（位于 ``app/db.py``）：数据库 schema 版本，独立递增；
  每次需要 DB 迁移时 +1，并在 ``MIGRATIONS`` 注册表里增加迁移函数。

详情见 ``docs/migrations.md``。
"""
__version__ = "0.2.0"

# 项目主页（GitHub）。在「关于」页、README、打包元信息里统一引用此常量。
HOMEPAGE_URL = "https://github.com/vortexer99/llm-cabinet"
