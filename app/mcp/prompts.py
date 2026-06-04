"""MCP Prompt templates — structured task instructions for agents.

Prompts are NOT executed code. They are SOP scripts that tell the agent
which Tools to call and in what order. All actual execution happens
through the agent calling the registered Tools.

Skill content is loaded from ``app/mcp/skills/*.md`` files so you can edit
the instructions without touching Python code.
"""

from __future__ import annotations

from pathlib import Path

from mcp.types import PromptMessage, TextContent

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _load_skill(name: str) -> str:
    """Load a skill's full markdown content from the skills directory."""
    path = _SKILLS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Skill file not found: {path}")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Prompt: organize_new_files
# ---------------------------------------------------------------------------


def organize_new_files(directory: str = "") -> list[PromptMessage]:
    """Task: organize newly downloaded files into the library.

    This prompt guides the agent through a workflow of:
    1. Discovering unorganized files
    2. Matching them to existing projects (or creating new ones)
    3. Importing them with appropriate metadata
    """
    content = _load_skill("organize_new_files")

    if directory:
        content = content.replace(
            "如果用户通过参数传递了 `directory`，列出该目录下的文件。",
            f"如果用户通过参数传递了 `directory`，列出该目录下的文件。\n\n"
            f"**当前 directory = `{directory}`**，请直接从该目录读取待整理的文件。",
        )

    return [
        PromptMessage(
            role="user",
            content=TextContent(type="text", text=content),
        ),
    ]


# ---------------------------------------------------------------------------
# Prompt: audit_metadata
# ---------------------------------------------------------------------------


def audit_metadata() -> list[PromptMessage]:
    """Task: audit metadata quality across the library."""
    return [
        PromptMessage(
            role="user",
            content=TextContent(type="text", text=_load_skill("audit_metadata")),
        ),
    ]


# ---------------------------------------------------------------------------
# Prompt: summarize_library
# ---------------------------------------------------------------------------


def summarize_library() -> list[PromptMessage]:
    """Task: generate a human-readable summary of the entire library."""
    return [
        PromptMessage(
            role="user",
            content=TextContent(type="text", text=_load_skill("summarize_library")),
        ),
    ]


# ---------------------------------------------------------------------------
# Prompt: suggest_tags
# ---------------------------------------------------------------------------


def suggest_tags(project_id: int = 0) -> list[PromptMessage]:
    """Task: suggest tags for untagged or poorly tagged projects."""
    content = _load_skill("suggest_tags")

    if project_id:
        # Replace the generic "列出全部缺少标签的项目" instruction with
        # a targeted one for the specific project
        content = content.replace(
            "**如果未指定（`project_id` 为 0 或空）**：",
            f"**当前指定了 `project_id={project_id}`**，请直接分析该项目。\n\n"
            "**如果未指定（`project_id` 为 0 或空）**：",
        )

    return [
        PromptMessage(
            role="user",
            content=TextContent(type="text", text=content),
        ),
    ]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PROMPT_REGISTRY: list[dict] = [
    {
        "name": "organize_new_files",
        "title": "整理新入库文件",
        "description": "引导 agent 按流程发现、匹配、导入新文件到合适的项目，并自动填写字段元数据",
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
        "description": "审视库中所有项目的描述、标签、字段填充率和文件完整性，生成结构化审核报告",
        "handler": audit_metadata,
        "arguments": [],
    },
    {
        "name": "summarize_library",
        "title": "生成库概览",
        "description": "统计项目数、标签分布、字段概况、近期活动，生成可读的库总结报告",
        "handler": summarize_library,
        "arguments": [],
    },
    {
        "name": "suggest_tags",
        "title": "推荐标签",
        "description": "分析项目内容与库中已有标签体系，推荐合适的标签（可指定单项目或全部未分类项目）",
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
