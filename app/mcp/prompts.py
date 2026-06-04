"""MCP Prompt templates — structured task instructions for agents.

Prompts are NOT executed code. They are SOP scripts that tell the agent
which Tools to call and in what order. All actual execution happens
through the agent calling the registered Tools.

Skill content is loaded from ``app/mcp/skills/*.md`` files so you can edit
the instructions without touching Python code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _load_skill(dir_name: str) -> str:
    """Load skill content from ``skills/llm-cabinet/<dir_name>/SKILL.md``, stripping YAML frontmatter."""
    path = _SKILLS_DIR / "llm-cabinet" / dir_name / "SKILL.md"
    if not path.is_file():
        raise FileNotFoundError(f"Skill file not found: {path}")
    raw = path.read_text(encoding="utf-8")

    # Strip YAML frontmatter (between --- markers)
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end != -1:
            raw = raw[end + 5:]

    return raw.strip() + "\n"


def _msg(text: str) -> dict[str, Any]:
    """Build a standard MCP user message dict with plain-text content."""
    return {"role": "user", "content": text}


# ---------------------------------------------------------------------------
# Prompt: organize_new_files
# ---------------------------------------------------------------------------


def organize_new_files(directory: str = "") -> list[dict[str, Any]]:
    content = _load_skill("organize-new-files")

    if directory:
        content = content.replace(
            "如果用户通过参数传递了 `directory`，列出该目录下的文件。",
            f"如果用户通过参数传递了 `directory`，列出该目录下的文件。\n\n"
            f"**当前 directory = `{directory}`**，请直接从该目录读取待整理的文件。",
        )

    return [_msg(content)]


# ---------------------------------------------------------------------------
# Prompt: audit_metadata
# ---------------------------------------------------------------------------


def audit_metadata() -> list[dict[str, Any]]:
    return [_msg(_load_skill("audit-metadata"))]


# ---------------------------------------------------------------------------
# Prompt: summarize_library
# ---------------------------------------------------------------------------


def summarize_library() -> list[dict[str, Any]]:
    return [_msg(_load_skill("summarize-library"))]


# ---------------------------------------------------------------------------
# Prompt: suggest_tags
# ---------------------------------------------------------------------------


def suggest_tags(project_id: int = 0) -> list[dict[str, Any]]:
    content = _load_skill("suggest-tags")

    if project_id:
        content = content.replace(
            "**如果未指定（`project_id` 为 0 或空）**：",
            f"**当前指定了 `project_id={project_id}`**，请直接分析该项目。\n\n"
            "**如果未指定（`project_id` 为 0 或空）**：",
        )

    return [_msg(content)]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PROMPT_REGISTRY: list[dict] = [
    {
        "name": "organize_new_files",
        "title": "整理新入库文件",
        "description": "将文件导入库：扫描目录→匹配已有项目/创建新项目→自动填写标题/描述/标签/字段值。适用于'帮我把论文入库''导入文件夹''这个 PDF 加到库里'等请求。",
        "handler": organize_new_files,
        "arguments": [
            {
                "name": "directory",
                "description": "待整理文件所在的目录路径（可选，留空则跳过文件发现步骤）",
                "required": False,
            },
        ],
    },
    {
        "name": "audit_metadata",
        "title": "审核元数据质量",
        "description": "全库元数据体检：检查缺失描述/标签/字段填充率/标签分布/文件完整性，生成结构化报告。适用于'检查数据质量''审核元数据''有哪些项目缺标签'等请求。",
        "handler": audit_metadata,
        "arguments": [],
    },
    {
        "name": "summarize_library",
        "title": "生成库概览",
        "description": "库全景报告：项目总数、标签分布、字段概况、最近更新、未分类项目。适用于'给我一个库概览''库里都有什么''最近加了什么'等请求。",
        "handler": summarize_library,
        "arguments": [],
    },
    {
        "name": "suggest_tags",
        "title": "推荐标签",
        "description": "为项目推荐标签：分析标题/描述，匹配已有标签体系，优先复用。适用于'推荐标签''这个论文该打什么标签''给未分类项目补标签'等请求。",
        "handler": suggest_tags,
        "arguments": [
            {
                "name": "project_id",
                "description": "目标项目 ID（0 或不传表示全部未分类项目）",
                "required": False,
            },
        ],
    },
]
