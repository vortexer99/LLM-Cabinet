# MCP 集成指南

LLM Cabinet 通过 [MCP（Model Context Protocol）](https://modelcontextprotocol.io/) 把项目库暴露给外部 AI agent。支持 Claude Desktop、Cursor、Cline 等任何 MCP 兼容客户端。

## 快速开始

### Claude Desktop 配置

打开 Claude Desktop 的配置文件：

| 平台 | 路径 |
|------|------|
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |

在 `mcpServers` 下添加以下条目：

**多库模式（推荐，零配置）**——agent 自动发现所有注册的库，可按名称切换：

```json
{
  "mcpServers": {
    "llm-cabinet": {
      "command": "python",
      "args": ["-m", "app.mcp.standalone"]
    }
  }
}
```

**单库模式（安全锁定）**——agent 只能访问指定的一个库：

```json
{
  "mcpServers": {
    "llm-cabinet-work": {
      "command": "python",
      "args": [
        "-m", "app.mcp.standalone",
        "--db", "D:/Projects/work/cabinet.db"
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

agent 调用 list_libraries() → 返回：
  - 论文（D:\Documents\Calibre-libs\论文库）
  - 工作文档（D:\Projects\work）
  - 游戏设计（D:\Projects\game_design）
```

### 示例 2：切换到指定库并搜索

```
用户：切换到"工作文档"库，找一下上个月的周报

agent:
  1. switch_library("工作文档")   → {"ok": true}
  2. search_projects("周报")     → [{"id": 15, "title": "2026年5月周报汇总", ...}]
  3. list_files(project_id=15)  → 列出周报项目下的全部文件
```

### 示例 3：浏览项目详情

```
用户：打开第 15 号项目看看

agent:
  1. get_project(15) → 返回完整元数据（tags / fields / description）
```

### 示例 4：只读浏览

```
用户：列出所有项目，看看有多少

agent: count_projects()      → {"total": 42}
agent: search_projects("")   → 返回 42 个项目的摘要列表
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
| `--log-level LEVEL` | 日志等级：DEBUG / INFO（默认） / WARNING / ERROR |

不传 `--db` 时为多库模式，自动从 `%APPDATA%/LLMCabinet/cabinet.json` 读取已注册库列表。

---

## Cursor 配置

Cursor 通过 `.cursor/mcp.json` 配置 MCP 服务器：

```json
{
  "mcpServers": {
    "llm-cabinet": {
      "command": "python",
      "args": ["-m", "app.mcp.standalone"]
    }
  }
}
```

---

## 可用能力

### 工具（Tools）

| 工具 | 说明 |
|------|------|
| `search_projects` | 按关键词或标签搜索项目 |
| `get_project` | 获取单个项目的完整元数据 |
| `list_files` | 列出项目下的文件（可按类型过滤） |
| `list_pending_suggestions` | 列出待审阅的 LLM 建议 |
| `count_projects` | 统计项目总数 |
| `get_field_definition` | 获取字段定义 |
| `list_libraries` | 列出所有注册的库 |
| `switch_library` | 切换到指定库 |

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

---

## 注意事项

- MCP 服务器通过 **stdio** 与客户端通信，不开放网络端口
- 多库模式时 agent 能发现 `cabinet.json` 中注册的所有库
- 文件内容资源默认关闭，需 `--allow-file-read` 显式开启
- 与 GUI 同时运行时靠 SQLite WAL 模式保证并发安全
