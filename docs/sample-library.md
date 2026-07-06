# 样例库设计

`tools/create_sample_library.py` 用来生成一个完整的 LLM Cabinet 测试库目录。它不提交二进制数据库，而是按当前 schema 现场生成：

```powershell
python tools/create_sample_library.py --target sample-library --force
```

生成后用应用里的「库 → 切换库...」选择 `sample-library/` 目录即可。

## 目录内容

```text
sample-library/
  .llm-cabinet
  cabinet.db
  library/              # copy 模式仓储文件
  external_sources/     # link 模式外部源文件
```

## 字段

脚本会保留默认保护字段，并补齐可选字段：

- 作者 `author`
- 日期 `date`
- 评分 `rating`
- 来源 `source_url`

额外测试字段：

- 状态
- 优先级
- 负责人
- 备注

## 项目样本

| 项目 | 主要用途 |
|---|---|
| 三体研究资料 | 中文搜索、`author:刘慈欣`、`rating:>=4`、多标签 `tag:科幻 AND tag:翻译`、copy 文件、generated 封面、多层 subfolder |
| 银河帝国整理 | 英文资料、外链文件、`author:阿西莫夫`、科幻标签交集 |
| AI Team Workspace 方案 | MCP 修改标记、待审阅 LLM 建议、MCP audit、近期日期搜索 |
| 未分类草稿 | 未分类筛选、纯关键词搜索、外链文件 |
| 缺失链接修复样例 | missing 文件、一致性检查、重关联/替换链接目标 |
| 导出导入闭环样例 | copy/link/generated/user origin、导出/ZIP 导入、目录结构还原 |

## 推荐测试点

- 搜索表达式：
  - `author:刘慈欣`
  - `tag:科幻 AND rating:>=4`
  - `(tag:生活 OR tag:翻译) AND NOT rating:<4`
  - `date:>=2024-01-01`
  - `状态:待整理`
- 搜索历史与收藏：
  - 打开搜索框应看到预置历史。
  - 点 `☆` 收藏当前表达式，重名时应询问覆盖。
- 左侧筛选：
  - 标签父节点 `领域` 应包含 `领域/科幻`。
  - 未分类应只显示「未分类草稿」。
  - 待审阅应包含「AI Team Workspace 方案」。
  - 未读 MCP 修改应包含「AI Team Workspace 方案」。
- 文件表：
  - 树形目录应显示 `source/pdf`、`notes`、`data/raw`、`generated` 等 subfolder。
  - 「仅用户文件 / 显示所有」应能隐藏 generated 封面文件。
  - 「缺失链接修复样例」里有一个已标记 missing 的外链文件。
- 导出/导入：
  - 对「导出导入闭环样例」测试独立包/ZIP、保留目录结构、文件来源标记还原。
- MCP：
  - MCP audit 面板有成功和失败样例记录。
  - `query_projects(action="search", keyword="tag:科幻 AND rating:>=4")` 应命中科幻高分项目。

