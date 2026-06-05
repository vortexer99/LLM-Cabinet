# 任务 #27 — Provenance 来源链：让 Agent 写入的元数据可追溯

## 来源

来自 2026-06-05 与用户的产品定位讨论。把 Cabinet 重新定位为
「以文件为载体、以元数据为记忆、以 Agent Contract 为协作协议的 AI Team Workspace」
之后，分析了 GPT 给的整套设计草案（三层信息 / Agent Contract / 多种 Metadata 类型），
结论是：**绝大部分概念现阶段过度设计**，但其中 **Provenance（来源链）** 是
当前 Cabinet 完全缺失、代价极小、但价值巨大的能力——它能回答用户最常问的一个问题：

> "这个字段值/这个标签是谁加的？依据是什么？"

这是把"AI Team Workspace"愿景落地的**第一块、也是最便宜的一块基石**。
后续阶段（参见末尾"后续阶段（远期参考）"）保留作为路线图，**本任务卡仅落地阶段 1**。

## 目标

让 Agent 通过 MCP 写入项目元数据（描述、字段值、标签）时，可以**附带一段来源说明**，
说明"这个值是基于哪些文件 / 哪些片段得出的"。用户在 UI 里看到这些值时，
能一眼识别它来自 Agent 还是自己手填，并能展开查看依据。

非目标：
- **不**新增 schema（来源信息塞进已有 `mcp_audit.arguments_json`）
- **不**强制 Agent 必填来源（保持向后兼容，老调用全部仍然有效）
- **不**做"置信度 / 任务 ID / Agent Contract"等更激进的字段
- **不**改 `project_field_values` 的存储结构（origin/source 不进字段值表）

## 约束

- 严格向后兼容：`source` 是 `manage_project` / `manage_files` 的新增可选字符串参数（JSON），
  老 client 不传也能正常工作。
- 来源信息只用于**展示与审计**，**不影响**字段值的等值比较 / 搜索 / 导出格式。
- UI 文案面向普通用户，避免"Provenance / 溯源链 / 持久化"等技术黑话——
  按钮叫「来源」，提示叫「这个值的依据」，符合项目用语约定。
- MCP 写权限的现有逻辑不变（disabled / session / permanent）。

## 实现要点

### 1. MCP 协议层：新增 `source` 可选参数

`app/mcp/server.py` 中 `manage_project`（action=create / update / add_tag / remove_tag）
与 `manage_files`（action=add / remove）增加可选参数：

```python
source: str = ""   # JSON 字符串：{"file_ids": [3, 7], "note": "前言第2页提取"}
```

工具描述里加一段（保持简洁）：

> 可选 `source`：JSON 字符串，说明此次写入依据的文件和理由，结构 `{"file_ids": [int...], "note": "中文说明"}`。
> 任一字段可省略。Agent 在做基于具体文件的总结、提取、推断时建议提供，便于用户回溯。

`tools.py` 的相关函数把 `source` 透传给 `_audit_log`，存进 `arguments_json` 的
顶层 `source` key（不嵌套到 `arguments` 里去），便于 UI 直接 `json_extract`。

约定的 source schema（仅展示层契约，不进 SQL）：

```json
{
  "file_ids": [3, 7],
  "note": "从前言第 2 页提取，作者明确署名为刘慈欣"
}
```

`file_ids` 不存在的文件 ID 也允许（不校验）—— Agent 可能引用临时路径，UI 容错显示即可。

### 2. UI 展示层：字段值/标签旁的「来源」入口

仅在**项目编辑对话框**中加，不动主窗口字段表（避免列宽爆炸）。

- `app/ui/project_edit_dialog.py`（或同等文件，按实际确认）：
  - 字段值行右侧加一个**轻量小图标**（🔗 或 🤖，挑一个不喧宾夺主的）。
  - **只有该字段值最近一次是被 MCP 写入且带了 source 时才显示**，否则不占位。
  - 鼠标悬停 tooltip：「由 {client_name} 在 {ts} 写入；依据：{note}；引用文件：{n} 个」。
  - 点击图标弹出小对话框，列出引用的文件（可点击跳到文件表选中），
    以及 Agent 的备注。

- 数据来源：实现一个 `Repository.get_latest_field_source(project_id, field_id)` ——
  在 `mcp_audit` 里按时间倒序找第一条满足
  `tool_name IN ('update_project','create_project')` 且 `arguments_json` 里
  对应 `field_id` 出现的成功记录，返回其顶层 `source`。

- "字段值" 颗粒度做不到（一次 update_project 可能带多个字段），所以**实现上采用项目级"最近一次有 source 的写入"**，
  对所有受这次写入影响的字段统一显示同一个 source。这是工程取舍，task 内的 `## 待澄清` 节有讨论。

### 3. 项目右键菜单：「查看 AI 写入历史」

现有"未读 MCP 修改"菜单项旁边，增加一项「查看 AI 写入历史」，打开一个简化版
`MCPAuditDialog`，**仅过滤当前项目 + 仅 success + 仅写工具**，按时间倒序展示。
每行展开后能看到 source（如有）。

复用 `app/ui/mcp_audit_dialog.py`，加构造参数 `project_id_filter`，传入则在
查询里加 `AND json_extract(arguments_json, '$.project_id') = ?`。

### 4. 文档 & 自检

- `docs/mcp.md`：在 `manage_project` / `manage_files` 章节末尾各加一段
  「Provenance（来源链）」小节，给 1 个完整调用示例。
- `selftests/`：新增 `selftest_mcp_provenance.py`，覆盖
  - 不传 source 仍能正常写入
  - 传 source 后能从 `mcp_audit` 取回
  - UI 入口可打开（用 offscreen 模式）
- `CHANGELOG.md` 的 `[Unreleased]` 段：记录新增 source 参数 + UI 入口。

## 依赖 / 风险

- 依赖 #24（MCP 操作记录查看面板）—— **已完工**，本卡只是在它之上加项目级筛选与 source 展示。
- 风险点：
  - "项目级最近一次写入"颗粒度对**多字段并发写入**场景有歧义（Agent 一次更新 5 个字段，
    其中 2 个有依据、3 个是顺手填的，但 source 会一起挂上去）。先接受这个粗粒度，
    实测出问题再改成"字段级 source"（届时需要新建表）。
  - source `note` 长度无上限可能被滥用 —— UI 显示截断 200 字、tooltip 截断 80 字，
    完整内容点击图标看。
  - 老的 mcp_audit 记录没有 source —— 不回填，自然过渡。

## 状态 / 完成时间

待做 / —

## 待澄清

1. **"来源"入口图标选哪个**？默认用 🔗，理由是 source 的语义是"链接到依据"。
   如果你觉得 🤖 更直观（强调"是 AI 写的"），告诉我。
2. **颗粒度做项目级还是字段级**？默认项目级（如上所述零 schema 改动）。如果你坚持
   字段级精准溯源，需要新建 `field_value_sources` 表，工作量从 S 升到 M，告诉我。
3. **是否在主窗口字段表也显示来源图标**？默认**只在项目编辑对话框**里显示，避免主表
   太挤。如果你想在主表也露出（比如标题列前加个 🤖 表示"这个项目最近被 AI 改过"），
   告诉我——但这个其实跟现有的"未读 MCP 修改"标记重叠了。

---

## 后续阶段（远期参考，不在本卡范围内）

记录在此仅为路线图，**本任务卡只做阶段 1**。任何阶段在动手前都需要重新评估，
不要因为这里写了就视为已批准。

### 阶段 2 — 区分"客观元数据 vs Agent 提炼"

给 `project_field_values` 加 `origin` 列（`'human' / 'agent:xxx' / 'imported'`），
UI 上 Agent 写入的字段用淡色 + 小机器人图标区分。让用户一眼看出
"哪些是我自己填的，哪些是 AI 帮我填的"。规模 S~M，需要 schema v6→v7。

### 阶段 3 — Agent Contract 文档化（不强制）

写一份 `docs/agent-contract.md`，约定 Agent 在 description / note 里按
`agent / task / artifact_type / content` 结构写。**作为约定文档而不是强制 schema**，
在 MCP 工具描述里以"建议格式"形式提到，允许自由形式。
触发条件：线上观察到 ≥3 个不同 Agent 在同一个库里写入。

### 阶段 4 — Process 层强化

`mcp_audit` 现在只记录"调用了什么工具"，未来扩展记录"这次调用属于哪个 task"。
但需要先看真实使用模式再设计，避免凭空设计 task 模型。
