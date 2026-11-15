# 命令参考

以下命令均接在 `python <技能目录>/scripts/cabinet.py [--app-root <源码目录>]` 后。
在源码环境也可使用 `python -m app.cli`，此时不传 `--app-root`。

## 全局参数

全局参数放在 `projects`、`files`、`library` 或 `libraries` 前。

| 参数 | 含义 |
|---|---|
| `--library 名称` | 选择已注册的库；每次调用都传 |
| `--db 路径` | 指定已有数据库，与 `--library`、`--config` 互斥 |
| `--config 路径` | 指定柜体配置，否则使用应用默认 cabinet.json |
| `--library-root 路径` | 仅配 `--db`；优先于库中保存的仓储设置；两者均无可用值时默认数据库同目录下的 `library/` |
| `--write` | 允许本次调用写入，包括导出 |
| `--allow-file-read` | 允许本次读取文件内容 |
| `--client-name 名称` | 审计调用方，默认 `skill-cli` |

只有 `libraries list` 可以不选目标库。名称有歧义时使用 `--db`。

## 查询

| 命令 | 参数 |
|---|---|
| `libraries list` | 列出注册库的名称、路径等信息 |
| `library info` | 项目数、字段数、标签数和默认存储方式 |
| `library stats` | 项目数、未分类项目数、标签分布、待处理建议数 |
| `library tags` | 标签及使用次数 |
| `library fields` | 字段定义，含 id、类型、suggest_enabled、prompt_hint |
| `library field` | `--name 字段名称` |
| `projects search` | 可选 `--keyword`、`--tag`、`--tag-prefix`、`--field-filter` |
| `projects get` | `--id 项目ID`，包含描述、标签和字段值 |
| `projects count` | 可选 `--tag` |
| `files list` | `--project-id 项目ID`，可选 `--kind document/image/video/audio/archive/other` |
| `files read` | `--id 文件ID`，同时需要全局 `--allow-file-read` |

搜索支持应用现有语法，例如 `tag:科幻`、`author:刘慈欣`、`rating:>=4`、AND/OR/NOT 和括号。
搜索定位项目后再列出文件；它不搜索文件正文。未传查询条件会返回全部项目。
文件内容结果含 `encoding`；`utf-8` 表示文本，`base64` 表示二进制编码，不是正文抽取结果。

## 写操作

全部需要全局 `--write`。ID 均为正整数。

| 命令 | 参数 |
|---|---|
| `projects create` | 必填 `--title`；可选 `--description`、`--tags`、字段值参数 |
| `projects update` | 必填 `--id`；可选项同 create |
| `projects add-tag` / `projects remove-tag` | `--id 项目ID --tag 标签` |
| `projects export` | `--id 项目ID --target-dir 已有目录` |
| `files add` | `--project-id 项目ID --path 路径`；可选 `--label 描述 --storage-mode link或copy` |
| `files remove` | `--id 文件ID`；仅删除记录 |

`--tags` 用逗号分隔；update 提供 tags 会替换整个标签列表，增减单个标签优先使用对应命令。
update 的空字符串与未传参数都表示保留旧值，不能用空串清空标题、描述或标签。
字段值通过 `--field-values JSON` 或 `--field-values-file 文件路径` 提供，两者互斥。
文件内容示例：`{"19":"张三","20":"2026"}`。ID 必须来自当前库，全部验证通过才会写入。标题、描述、标签的内置字段 ID 不接受此方式写入，请使用 `--title`、`--description`、`--tags`。

导出会在目标目录中生成项目子目录，返回实际 `target_dir`、`files_exported` 和 `warnings`；报告警告。
`copy` 模式的相对路径基于仓储根目录，绝对路径也必须位于仓储内；外部路径会报错。

## 返回与故障

成功退出码 0：`{"ok":true,"data":...}`。data 是列表或对象，写操作会包含对应 `project_id` / `file_id` 等结果。
操作失败退出码 1，参数错误退出码 2，均为 `{"ok":false,"error":"原因"}`。
`--help` 是普通帮助文本。不要把 stderr 日志拼入 JSON。
目标库不存在时先核对路径；不得通过另建空库来绕过错误。
