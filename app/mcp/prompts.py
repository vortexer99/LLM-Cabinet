"""MCP Prompt templates — structured task instructions for agents.

Prompts are NOT executed code. They are SOP scripts that tell the agent
which Tools to call and in what order. All actual execution happens
through the agent calling the registered Tools.
"""

from __future__ import annotations

from mcp.types import PromptMessage, TextContent

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
    path_hint = (
        f"\n请从 {directory} 读取待整理的文件列表（如果有的话）。"
        if directory
        else ""
    )

    return [
        PromptMessage(
            role="user",
            content=TextContent(
                type="text",
                text=f"""你的任务是帮助用户整理新入库的文件。请按以下步骤操作：

1. 确认目标库：
   - 先调用 list_libraries() 查看可用库
   - 如果用户有指定目标库名，调用 switch_library(name) 切换

2. 发现待整理的文件：{path_hint}
   如果没有指定目录，跳过此步，直接进入第 3 步。

3. 分析文件内容：
   - 对于每个文件，调用 search_projects(keyword=文件名中的关键词) 查找匹配项目
   - 查看 search_projects 返回的 title 和 tags，判断是否存在合适的现有项目

4. 处理文件：
   - 找到匹配项目 → 直接 add_file(project_id=..., path=...)
   - 找不到匹配 → create_project(title=合适的项目名) 然后 add_file
   - 不确定 → 列出候选项目，让用户选择

5. 补充元数据：
   - 为新创建的项目添加合适的 tags
   - 调用 get_field_definition 了解库有哪些可用字段
   - 如有合适的字段（如 author、year、source），设置到项目中

6. 总结：
   - 报告本次入库了多少文件，新建了多少项目
   - 如有未处理的文件，说明原因

注意：
- search_projects 只搜项目标题和描述，不会搜文件内容；找不到时请创建新项目
- list_files(project_id, kind) 可按文档/图片/视频等类型过滤
- 不确定标签时，使用现有标签树中的标签（通过 cabinet://tags 查看可用标签）""",
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Prompt: audit_metadata
# ---------------------------------------------------------------------------


def audit_metadata() -> list[PromptMessage]:
    """Task: audit metadata quality across the library.

    Checks for:
    - Missing descriptions
    - Missing tags
    - Empty field values
    - Stale suggestions
    """
    return [
        PromptMessage(
            role="user",
            content=TextContent(
                type="text",
                text="""你的任务是审核当前库的元数据质量。请按以下步骤操作：

1. 获取库概况：
   - 调用 cabinet://library/stats（Resource）获取标签分布和待处理建议数

2. 检查空描述项目：
   - 调用 search_projects(keyword="") 获取全部项目
   - 标记 description_md 为空的项目

3. 检查缺少标签的项目：
   - 查看 search_projects 返回的 tags 字段
   - 列出 tags 为空的项目

4. 检查待处理的 LLM 建议：
   - 调用 list_pending_suggestions()（不传 project_id）获取全部待处理建议
   - 统计数量，列出涉及的项目

5. 检查字段填充率：
   - 调用 get_field_definition(field_name) 了解库有哪些自定义字段
   - 对每个字段，检查 key 非空的字段（如 author、date 等）在各项目中的值是否为空

6. 生成审核报告：
   - 用表格列出问题项目数和占比
   - 按严重程度排序：缺描述 > 缺标签 > 缺字段值 > 待处理建议
   - 对每个问题给出具体的修改建议（哪些标签适合、哪个字段该填什么）

注意：
- 报告应以项目为单位组织，每个项目列出全部问题
- 不要实际修改数据，只做审阅和报告""",
            ),
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
            content=TextContent(
                type="text",
                text="""你的任务是生成当前库的概览报告。请按以下步骤操作：

1. 基本信息：
   - 调用 cabinet://library/info 获取项目总数、字段数等
   - 调用 cabinet://library/stats 获取标签分布、待处理建议数

2. 标签分布：
   - 调用 cabinet://tags 获取所有标签及其项目计数
   - 按计数降序排列前 10 个标签

3. 字段概况：
   - 调用 cabinet://fields 获取所有字段定义
   - 区分系统字段（key 非空）和用户自定义字段（key 为空）

4. 近期活动：
   - 调用 search_projects(keyword="") 获取全部项目，按 updated_at 排序
   - 列出最近更新的 5 个项目

5. 生成报告：
   - 用清晰的标题和段落组织内容
   - 格式：
     # 库概览
     - 项目总数：N
     - 标签总数：M
     - 自定义字段：X 个
     - 待处理建议：Y 条

     ## 热门标签
     （标签列表）

     ## 最近更新
     （项目列表）

     ## 数据质量
     （标签分布是否均匀、是否有大量未分类项目等）

注意：
- 报告应简洁易读，用中文表达
- 不使用技术术语，用普通用户能理解的词语""",
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Prompt: suggest_tags
# ---------------------------------------------------------------------------


def suggest_tags(project_id: int = 0) -> list[PromptMessage]:
    """Task: suggest tags for untagged or poorly tagged projects."""
    project_clause = (
        f"查看项目 #{project_id}：先调用 get_project({project_id}) 获取项目详情"
        if project_id
        else "列出缺少标签的项目：调用 search_projects(keyword='') 并筛选 tags 为空的项"
    )

    return [
        PromptMessage(
            role="user",
            content=TextContent(
                type="text",
                text=f"""你的任务是为项目推荐合适的标签。请按以下步骤操作：

1. 了解可用标签：
   - 调用 cabinet://tags（Resource）获取库中所有已存在的标签及其使用计数
   - 优先使用已有的标签，避免重复创建

2. 分析目标项目：
   - {project_clause}

3. 推荐标签：
   - 根据项目 title 和 description_md 的内容，推荐合适的标签
   - 优先推荐已在库中使用的标签（从第 1 步获取的列表中选）
   - 如果没有合适的已有标签，可以建议新建标签

4. 输出建议：
   - 列出每个项目的推荐标签
   - 标注标签是"已有"还是"新建"
   - 给出推荐理由（为什么这个标签适合）

注意：
- 不要自动应用标签，只做推荐
- 如果有需要确认的歧义（如内容可能是 A 也可能是 B），向用户提问
- 标签建议应基于项目实际内容，不要臆测""",
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PROMPT_REGISTRY: list[dict] = [
    {
        "name": "organize_new_files",
        "title": "整理新入库文件",
        "description": "引导 agent 按流程发现、匹配、导入新文件到合适的项目",
        "handler": organize_new_files,
        "arguments": [
            {
                "name": "directory",
                "description": "待整理文件所在的目录路径（可选）",
                "required": False,
            },
        ],
    },
    {
        "name": "audit_metadata",
        "title": "审核元数据质量",
        "description": "检查库中项目的描述、标签、字段填充率，生成质量报告",
        "handler": audit_metadata,
        "arguments": [],
    },
    {
        "name": "summarize_library",
        "title": "生成库概览",
        "description": "统计项目数、标签分布、近期活动，生成可读的库总结报告",
        "handler": summarize_library,
        "arguments": [],
    },
    {
        "name": "suggest_tags",
        "title": "推荐标签",
        "description": "分析项目内容，推荐合适的标签（可指定单个项目或全部未分类项目）",
        "handler": suggest_tags,
        "arguments": [
            {
                "name": "project_id",
                "description": "目标项目 ID（0 表示全部未分类项目）",
                "required": False,
            },
        ],
    },
]
