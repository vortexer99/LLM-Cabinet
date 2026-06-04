# MCP 集成指南

LLM Cabinet 通过 [MCP（Model Context Protocol）](https://modelcontextprotocol.io/) 把项目库暴露给外部 AI agent。支持 Claude Desktop、Cursor、Cline 等任何 MCP 兼容客户端。

## 快速开始

在 LLM Cabinet 中打开「设置 → MCP 集成」，点击「导出 JSON」，选择模式和权限后复制配置。

也可以手动编辑配置文件：

| 平台 | Claude Desktop 配置路径 |
|------|------------------------|
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |

在 `mcpServers` 下添加以下条目（名称后缀由导出对话框自动生成）：

**多库模式，可读写**（默认）：

```json
{
  "mcpServers": {
    "llm-cabinet": {
      "command": "python",
      "args": ["-m", "app.mcp.standalone", "--write-permission", "session"],
      "env": {
        "PYTHONPATH": "/path/to/LLM-Cabinet"
      }
    }
  }
}
```

``PYTHONPATH`` 是 LLM Cabinet 的安装目录（包含 ``app/`` 文件夹的目录），**必须填**。

不同选项会生成不同的名称，避免冲突：

| 模式 | 只读 | 名称 |
|------|------|------|
| 多库 | 关闭（默认） | `llm-cabinet` |
| 多库 | 开启 | `llm-cabinet-ro` |
| 单库 | 关闭（默认） | `llm-cabinet-<库名>` |
| 单库 | 开启 | `llm-cabinet-<库名>-ro` |

> JSON 原生支持中文键名，中文库名不会造成问题。

**单库模式示例**：

```json
{
  "mcpServers": {
    "llm-cabinet-论文库": {
      "command": "python",
      "args": [
        "-m", "app.mcp.standalone",
        "--db", "/path/to/your-library/cabinet.db"
      ]
    }
  }
}
```

配置完成后重启 Claude Desktop，在工具栏看到 `llm-cabinet` 标志即可使用。

---

## 调用示例

### 示例 1：列出所有库

```
用户：我有哪些库？

agent 调用 manage_libraries(action="list") → 返回：
  - 论文库（/path/to/papers-library）— 存放学术论文和参考文献
  - 工作文档（/path/to/work-library）
  - 游戏设计（/path/to/game-library）
```

### 示例 2：切换到指定库并搜索

```
用户：切换到"工作文档"库，找一下上个月的周报

agent:
  1. manage_libraries(action="switch", library_name="工作文档")   → {"ok": true}
  2. query_projects(action="search", keyword="周报")              → [{"id": 15, "title": "2026年5月周报汇总", ...}]
  3. manage_files(action="list", project_id=15)                  → 列出周报项目下的全部文件
```

### 示例 3：浏览项目详情

```
用户：打开第 15 号项目看看

agent:
  1. query_projects(action="get", project_id=15) → 返回完整元数据（tags / fields / description）
```

### 示例 4：只读浏览

```
用户：列出所有项目，看看有多少

agent: query_projects(action="count")      → {"total": 42}
agent: query_projects(action="search")    → 返回 42 个项目的摘要列表
```

---

## 命令行参数

独立进程支持以下参数：

```
python -m app.mcp.standalone [选项]
```

| 参数 | 说明 |
|------|------|
| `--db PATH` | 单库模式：指定 cabinet.db 路径 |
| `--library PATH` | 单库模式：library 子目录（默认与 db 同目录） |
| `--config PATH` | 自定义 cabinet.json 路径 |
| `--allow-file-read` | 开启文件内容读取（默认关闭） |
| `--write-permission disabled|session|permanent` | 写操作权限。设置面板默认生成 `session`（本次连接可读写）；选「只读模式」时不带此参数（等效 `disabled`）。 |
| `--log-level LEVEL` | 日志等级：DEBUG / INFO（默认） / WARNING / ERROR |

不传 `--db` 时为多库模式，自动从 `%APPDATA%/LLMCabinet/cabinet.json` 读取已注册库列表。

---

## Cursor 配置

Cursor 通过 `.cursor/mcp.json` 配置 MCP 服务器（JSON 内容由导出对话框生成）：

---

## Cherry Studio 配置

1. 右上角齿轮 → **设置** → **MCP服务器** → **添加**
2. 选择 **从JSON导入**，粘贴导出的 JSON
3. 保存后，点击开关图标 **启用**
4. 进入对应智能体的 **设置 → 工具页**，启用 **LLM Cabinet**
5. 按需设置工具预授权（写操作建议开启审批）

---

## 可用能力

### 工具（Tools）

| 工具 | actions | 说明 |
|------|------|------|
| `query_projects` | `search` / `get` / `count` | 搜索项目、查看详情、统计数量 |
| `manage_project` | `create` / `update` / `add_tag` / `remove_tag` | 创建项目、修改信息、增减标签 |
| `manage_files` | `list` / `add` / `remove` | 列出文件、添加文件、删除文件 |
| `manage_libraries` | `list` / `switch` / `get_field` / `get_fields` | 列出库、切换库、查看字段定义 |
| `export_project` | — | 导出项目到本地目录 |

### 资源（Resources）

| URI | 内容 |
|-----|------|
| `cabinet://library/info` | 库元信息 |
| `cabinet://library/stats` | 统计概览 |
| `cabinet://tags` | 标签列表 |
| `cabinet://fields` | 字段定义 |
| `cabinet://projects` | 项目摘要列表 |
| `cabinet://project/{id}` | 项目详情 |
| `cabinet://project/{id}/files` | 项目文件清单 |
| `cabinet://file/{id}` | 文件内容（需开启 `--allow-file-read`） |

### 技能（Prompts）

Prompts 是给 agent 的结构化任务指令，告诉它调哪些 Tool、按什么顺序。用户只需说"帮我整理新论文"，agent 就会按技能里的 SOP 执行。

| Prompt | 说明 |
|--------|------|
| `organize_new_files` | 整理新入库文件：发现 → 匹配项目 → 导入 → 补元数据 |
| `audit_metadata` | 审核元数据质量：查缺失描述/标签/字段值，生成报告 |
| `summarize_library` | 库概览：统计项目数、标签分布、近期活动 |
| `suggest_tags` | 推荐标签：分析项目内容，推荐已有或新建标签 |

---

## 注意事项

- MCP 服务器通过 **stdio** 与客户端通信，不开放网络端口
- 多库模式时 agent 能发现 `cabinet.json` 中注册的所有库
- 文件内容资源默认关闭，需 `--allow-file-read` 显式开启
- 与 GUI 同时运行时靠 SQLite WAL 模式保证并发安全
