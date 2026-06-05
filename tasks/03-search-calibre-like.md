# 03 · 类 Calibre 的搜索

> **2026-06-04 更新**：拆为两段——
> **Phase A（基础，XS~S）**：激活灰色搜索框，连 `repo.list_projects(keyword, tag)` 做关键词+标签搜索。与 MCP `query_projects(action="search")` 共享同一后端。
> **Phase B（Calibre 级，M）**：字段过滤 + 布尔逻辑 + 解析器 + 语法提示。agent 也可用更精确的查询。
>
> **2026-06-05 更新**：新增 Phase C
> **Phase C（近期的搜索 + 收藏的搜索表达式，XS）**：搜索历史下拉 + 用户收藏常用表达式。

**工作量**：M（Phase A XS~S + Phase B M + Phase C XS）
**优先级**：P0
**状态**：待做

## 来源
`TODO.md → 📦 项目 & 文件管理` 第 1 条

## 目标
顶部工具栏加搜索框，支持组合查询：

- **关键词搜索**：标题 / 描述 / 作者
- **标签筛选**：`tag:科幻 AND tag:翻译`
- **字段过滤**：`author:刘慈欣`、`rating:>=4`、`date:>=2024-01-01`、`title:三体`
- **布尔逻辑**：AND / OR / NOT、括号
- **大小写不敏感**、中文分词不做（先按 LIKE 模糊匹配）
- **搜索历史**：自动保存最近 N 条搜索（默认 20），搜索框获得焦点时以下拉列表展示
- **收藏表达式**：用户可将常用搜索命名并保存，下次一键选择

## 实现要点

### 解析器 `app/search.py`
- 单独模块，输入字符串 → 输出查询 AST
- 推荐手写递归下降（避免引入 pyparsing 重型依赖）
- AST 节点类型：`AndNode / OrNode / NotNode / TermNode(field?, op, value)`

### Repository 接口
- 新增 `list_projects_query(ast) -> list[Project]`
- 动态构造 SQL：递归 AST 生成 `WHERE` 子句 + 必要的 `LEFT JOIN tags / project_field_values`
- 用参数化绑定，禁止字符串拼接 value
- 兜底：`field:value` 中的 `field` 名解析到 system field key 或 user field id；非法字段名警告但不报错

### UI
- 主窗口顶部加搜索框 `QLineEdit`，加 placeholder 提示语法
- 200ms 防抖；按 Esc 清空
- 搜索时左栏过滤可选：默认是 AND（搜索范围限定到当前标签）；右上加一个"搜索全部"切换
- 错误的语法显示红字提示，但不阻断输入
- **搜索历史下拉**：焦点进入搜索框时，自动弹出最近搜索列表（存储在 `settings` 表 `key='search_history'`，JSON 数组，最多 20 条）。点击历史条目直接填入并触发搜索。每条历史右侧有 ✕ 删除按钮。
- **收藏表达式**：搜索框右侧加 ⭐ 按钮。当前搜索内容非空时点击弹出命名对话框 → 存入 `settings` 表 `key='saved_searches'`，JSON 格式 `[{"name": "高分科幻", "query": "tag:科幻 AND rating:>=4"}, ...]`。收藏在下拉列表顶部用 ⭐ 前缀区分，右侧有 ✕ 删除。

## 不做范围
- 全文搜索文件内容（成本高，留给 T9）
- 模糊纠错
- 排序定制（保持现行 created_at desc）

## 依赖
- 无（不需要新 schema 字段；搜索历史/收藏存在现有 `settings` 表）

## 风险
- 解析器边界要充分测试：嵌套括号、未闭合引号、特殊字符
- 性能：包含 LIKE 全表扫描的查询在大库下可能慢；先不优化，必要时加 FTS5 虚表

## 验收
- 复合查询：`tag:科幻 AND author:刘慈欣 AND rating:>=4` 能正确过滤
- 纯关键词：`三体` 等价于 `title:三体 OR description:三体 OR author:三体`
- 搜索历史：执行搜索后自动保存，再次点开搜索框显示最近 5 条
- 收藏表达式：保存"高分科幻"对应 `tag:科幻 AND rating:>=4`，下次从下拉选中直接执行
