# 43 · Skill + CLI 入口

**工作量**：M
**优先级**：P1
**状态**：✅ 完成（2026-11-15，Unreleased）

## 来源与目标

为支持技能和本地命令执行的外部 AI 客户端提供 Skill + CLI 入口，直接使用现有库与项目能力；继续保留 MCP，两个入口共用工具层、审计记录和 GUI 修改标记。

## 范围与约束

- 新增 `python -m app.cli`，提供库发现与信息查询、项目查询与元数据修改、文件清单与登记、文件内容读取、项目导出。
- 新增独立技能包 `skills/llm-cabinet/`：`SKILL.md`、脚本入口、命令与工作流参考。安装后仍需项目源码和 Python 环境；脚本支持 `--app-root` / `LLM_CABINET_HOME`。
- 除 `libraries list` 外必须显式 `--library` 或 `--db`；全局选项放在子命令前。写操作由每次调用的 `--write` 开放，文件内容读取由 `--allow-file-read` 开放。
- stdout 输出统一 UTF-8 JSON，失败退出码非零，日志输出 stderr，帮助保持文本格式。
- 复用 `app/mcp/tools.py`、`context.py`、`resources.py`；默认审计客户端名 `skill-cli`，不增加 schema、不调整应用版本。
- 文件 `add` 仅登记，`copy` 要求文件已在库内且不物理复制；`remove` 只移除记录，不删除实体文件。
- 修复 MCP prompts 的旧路径；文档明确 prompt 是否自动选择取决于客户端。
- 默认沿用 GUI 保存的有效仓储路径，显式 `--library-root` 优先；拒绝通过自定义字段 JSON 写入标题、描述与标签。
- 只读预检目标数据库，拒绝空文件、非 Cabinet 库及未来 schema；外部连接不恢复 GUI 正在执行的后台任务。
- 设置页增加 Skill 文件夹入口，发布流程新增脚本技能 zip，MCP SDK 约束为兼容的 1.x。

## 交付与校验

- [x] CLI 与技能脚本的帮助、UTF-8 JSON、失败退出码符合约定
- [x] 库发现、显式选库、单库路径与 `--library-root` 行为通过验证
- [x] 默认拒绝写入与文件内容读取，显式授权后可执行
- [x] 项目与字段查询、元数据写入、标签修改、文件登记/移除、导出通过验证
- [x] 写操作进入现有审计与 GUI 修改标记，文件实体保持预期行为
- [x] 技能包复制到其它位置后可定位项目并运行，技能结构校验通过
- [x] MCP prompts 可读取，现有 MCP 能力回归通过
- [x] Python 语法与相关 selftests 通过
- [x] README 两份、CHANGELOG、MCP 与技能文档同步完成

## 依赖与风险

依赖现有 MCP 工具实现与 SQLite 库上下文；沿用原有操作语义。多进程调用不能依赖可变的默认库；安装后的技能脚本不能依赖源码仓库的相对位置。验收时使用临时测试库，避免修改用户库。

## 验证记录

- `selftests/task43_skill_cli.py`：389 项通过，真实子进程与临时双库；含保护字段、仓储路径优先级、误选数据库无修改拒绝。
- `selftests/task23_mcp_tools_consolidation.py`：25 项通过；`task24_mcp_audit_viewer.py`：16 项通过；`task03_search_phase_b.py`：11 项通过。
- `selftests/gui_release_regressions.py`：13 项通过；新增 Skill 文件夹按钮另用 offscreen 冒烟验证绑定与目标路径。
- 四个 MCP prompts 均成功加载；Skill Creator `quick_validate.py` 通过（Windows 使用 UTF-8 模式）。
- 本次修改的 Python 文件通过 `py_compile`；`git diff --check` 与发布 workflow YAML 解析通过。
- 未运行远端发布或完整 EXE 构建；未操作用户项目库。
