# Skill + CLI 使用指南

`skills/llm-cabinet/` 为支持技能和本地命令执行的 AI 客户端提供库管理指令，通过 `app.cli` 查询库、维护项目元数据、登记文件和导出项目。调用直接复用现有工具层，不需要运行 GUI 或 MCP 服务。MCP 客户端仍可使用原有入口，见 [MCP 指南](mcp.md)。

## 安装与运行环境

1. 准备项目源码、Python 3.10+ 和已安装项目依赖的 Python 环境；建议使用项目的 `.venv\Scripts\python.exe`。
2. 将整个 `skills/llm-cabinet/` 文件夹复制到客户端支持的技能目录，保持 `SKILL.md`、`scripts/` 和 `references/` 的相对结构。技能目录位置及重新加载方式以客户端为准。
3. 为安装后的脚本指定项目根目录：在脚本参数最前方传入 `--app-root PATH`，或设置环境变量 `LLM_CABINET_HOME`。路径应指向包含 `app/` 的源码目录。

源码运行的 GUI 可从「设置 → MCP → 打开 Skill 文件夹」找到技能。发布流程另行生成 `llm-cabinet-cli-skill.zip`（含脚本），原 `llm-cabinet-skills.zip` 继续提供依赖 MCP 的四个工作流。

技能包可独立复制，但仍依赖项目源码与其 Python 环境；仅复制技能或只有桌面 exe 均不足以运行 CLI。脚本在仓库内使用时会自动找到项目根目录。

在项目根目录先查看已注册库：

```powershell
.\.venv\Scripts\python.exe skills/llm-cabinet/scripts/cabinet.py libraries list
.\.venv\Scripts\python.exe skills/llm-cabinet/scripts/cabinet.py --library "论文库" library info
```

安装后的脚本可从其它工作目录执行，例如：

```powershell
& "D:\SoftWare\LLM-Cabinet-dev\.venv\Scripts\python.exe" "C:\path\to\skills\llm-cabinet\scripts\cabinet.py" --app-root "D:\SoftWare\LLM-Cabinet-dev" --library "论文库" projects search --keyword "注意力"
```

也可在项目根目录直接运行模块入口：

```powershell
.\.venv\Scripts\python.exe -m app.cli --library "论文库" projects count
```

## 选库与权限

全局选项必须位于 `libraries` / `library` / `projects` / `files` 子命令前。`--app-root` 是技能脚本的定位选项；其余选项也适用于 `python -m app.cli`。

| 全局选项 | 含义 |
|---|---|
| `--config PATH` | 指定库注册配置 `cabinet.json`；省略时读取应用默认配置 |
| `--library NAME` | 按注册名称选择库 |
| `--db PATH` | 直接打开已有的 `cabinet.db`；与 `--library`、`--config` 互斥 |
| `--library-root PATH` | 指定实体文件存储目录，仅可与 `--db` 一起使用；优先于库中保存的有效仓储设置，否则回退到数据库同目录下的 `library/` |
| `--write` | 仅为本次调用开放写操作，包括项目导出 |
| `--allow-file-read` | 为本次调用开放 `files read` 文件内容读取 |
| `--client-name NAME` | 审计中的客户端名称，默认 `skill-cli` |

除 `libraries list` 外，每次调用必须显式指定 `--library` 或 `--db`。CLI 不保留会话内的库切换状态，也不根据上次调用推断默认库，避免多个 agent 同时操作时混淆目标。需要发现库时先执行 `libraries list`；需要访问未注册库时使用 `--db`。

默认关闭业务写操作和文件内容读取。用户已明确要求的写入可在相应调用中加 `--write`；读取实体文件内容需加 `--allow-file-read`。查询项目元数据和文件清单不需要文件内容权限。

CLI 会先只读校验数据库，拒绝空文件、非 Cabinet 数据库及高于当前支持的 schema，避免误初始化其它文件。标题、描述和标签需使用专用参数，不能通过自定义字段 JSON 的内置字段 ID 写入。

## 命令概览

| 命令 | 参数与作用 |
|---|---|
| `libraries list` | 列出注册库 |
| `library info` / `stats` / `tags` / `fields` | 库信息、统计、标签、字段定义 |
| `library field --name NAME` | 查询单个字段定义 |
| `projects search` | 可选 `--keyword`、`--tag`、`--tag-prefix`、`--field-filter`；支持现有搜索表达式 |
| `projects get --id N` | 获取项目完整元数据 |
| `projects count` | 统计项目数，可选 `--tag TAG` |
| `projects create --title TITLE` | 创建项目；可选 `--description`、`--tags`、`--field-values` 或 `--field-values-file`；需 `--write` |
| `projects update --id N` | 修改项目；可选 `--title` 及上述元数据参数；需 `--write` |
| `projects add-tag` / `remove-tag` | 必填 `--id N --tag TAG`；需 `--write` |
| `projects export --id N --target-dir PATH` | 导出项目到已有目录；需 `--write` |
| `files list --project-id N` | 列出项目文件，可选 `--kind KIND` |
| `files add --project-id N --path PATH` | 登记文件；可选 `--storage-mode link\|copy`、`--label`；需 `--write` |
| `files remove --id N` | 仅移除数据库记录；需 `--write` |
| `files read --id N` | 读取文件内容；需 `--allow-file-read` |

`--tags` 接受逗号分隔的标签。`--field-values` 接受 JSON 对象，键为字段的数字 ID（JSON 中写为字符串），值必须为字符串，例如 `{"4":"张三"}`；先查 `library fields` 确认 ID。也可将 JSON 保存为 UTF-8 文件，改用 `--field-values-file PATH`，避免命令行转义。两个字段值选项互斥。update 的标题、描述、标签参数传空字符串表示保留旧值；其 tags 参数会替换完整标签列表，单个标签增减优先使用 add-tag/remove-tag。子命令的具体参数可用 `--help` 查看。

```powershell
.\.venv\Scripts\python.exe -m app.cli --library "论文库" library fields
.\.venv\Scripts\python.exe -m app.cli --library "论文库" --write projects create --title "阅读笔记" --tags "研究,待读" --field-values-file "D:\notes\fields.json"
.\.venv\Scripts\python.exe -m app.cli --library "论文库" --write files add --project-id 42 --path "D:\papers\paper.pdf" --storage-mode link
.\.venv\Scripts\python.exe -m app.cli --library "论文库" --allow-file-read files read --id 18
```

文件登记沿用现有 MCP 语义：`link` 记录外部文件路径；`copy` 仅登记已在该库实体文件目录内的文件并保存相对路径，**不会执行物理复制**。`files remove` 只移除记录，不会删除、回收或移动实体文件。需要先将外部文件复制入库时，可使用 GUI 导入或按用户要求先准备文件，再登记。

## 输出与审计

正常命令在 stdout 输出 UTF-8 JSON：成功为 `{"ok":true,"data":...}`，失败为 `{"ok":false,"error":...}`。失败返回非零退出码，日志写入 stderr；`--help` 为常规文本。自动化应检查退出码和顶层 `ok`，再读取 `data`，不要根据日志判断成功。

CLI 写操作复用现有 `mcp_audit` 审计记录，默认客户端名称为 `skill-cli`。GUI 现有「MCP 操作记录」及修改标记入口继续显示这些操作，不需要新建数据库 schema。此权限开关约束本次 CLI 调用，不能代替客户端自身的本地文件访问权限设置。

技能指令见 [`SKILL.md`](../skills/llm-cabinet/SKILL.md)，完整调用约定见 [`references/commands.md`](../skills/llm-cabinet/references/commands.md)，整理与审核流程见 [`references/workflows.md`](../skills/llm-cabinet/references/workflows.md)。
