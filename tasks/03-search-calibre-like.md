# 03 · 类 Calibre 的搜索

> **2026-06-04 更新**：拆为两段——
> **Phase A（基础，XS~S）**：激活灰色搜索框，连 `repo.list_projects(keyword, tag, tag_prefix)` 做标题/描述关键词 + 当前左侧筛选搜索。与 MCP `query_projects(action="search")` 共享同一后端；不实现 `field_filter`。
> **Phase B（Calibre 级，M）**：字段过滤 + 布尔逻辑 + 解析器 + 语法提示。agent 也可用更精确的查询。
>
> **2026-06-05 更新**：新增 Phase C
> **Phase C（近期的搜索 + 收藏的搜索表达式，XS）**：C1 搜索历史下拉 + C2 用户收藏常用表达式。C1 可随 Phase A 先做，C2 建议等 Phase B 语法稳定后做。

**工作量**：M（Phase A XS~S + Phase B M + Phase C XS）
**优先级**：P0
**状态**：🚧 Phase A/B 完成（Phase C 待做）

## 来源
`TODO.md → 📦 项目 & 文件管理` 第 1 条

## 目标
顶部工具栏加搜索框，支持组合查询：

- **关键词搜索**：Phase A 搜标题 / 描述；Phase B 通过字段表达式搜索作者等字段
- **标签筛选**：`tag:科幻 AND tag:翻译`
- **字段过滤**：`author:刘慈欣`、`rating:>=4`、`date:>=2024-01-01`、`title:三体`
- **布尔逻辑**：AND / OR / NOT、括号
- **大小写不敏感**、中文分词不做（先按 LIKE 模糊匹配）
- **搜索历史**：自动保存最近 N 条搜索（默认 20），搜索框获得焦点时以下拉列表展示
- **收藏表达式**：用户可将常用搜索命名并保存，下次一键选择

## 实现要点

### Phase A：基础搜索闭环
- [x] 启用主窗口顶部现有 `search_box`，placeholder 改为“搜索标题 / 描述”。
- [x] 200ms 防抖；按 Esc 清空搜索；清空后恢复当前左侧筛选结果。
- [x] 搜索与左侧筛选组合为 AND：
  - 普通标签节点：`repo.list_projects(keyword=kw, tag=tag)`
  - 标签父节点：`repo.list_projects(keyword=kw, tag_prefix=prefix)`
  - 未标记 / MCP 修改过：在现有筛选逻辑基础上叠加 keyword
- [x] 当前阶段关键词只搜 `projects.title` / `projects.description_md`。
  “作者”等字段值已统一存入 `project_field_values`，留给 Phase B 字段过滤。
- [x] 搜索结果刷新后尽量保留当前选中项目；若当前项目不在结果中，选中第一项或清空详情。
- [x] 状态栏显示命中数量，例如“搜索命中 12 个项目”。
- [x] MCP `query_projects(action="search")` 中 `keyword` / `field_filter` 均接入 Phase B 解析器；`tag` / `tag_prefix` 作为额外 AND 条件透传到 Repository 后端。

### 解析器 `app/search.py`
- 单独模块，输入字符串 → 输出查询 AST
- 推荐手写递归下降（避免引入 pyparsing 重型依赖）
- AST 节点类型：`AndNode / OrNode / NotNode / TermNode(field?, op, value)`
- 字段名解析顺序：
  1. 保护字段 key：`title` / `description` / `tags`
  2. `fields.key`，如 `author` / `date` / `rating` / `source_url`
  3. 字段显示名，如 `作者` / `评分`
- 字段名大小写不敏感；字段显示名按原文匹配。
- 操作符：
  - 文本 / URL / 标签：`:` 表示 contains，`=` 表示精确匹配
  - number / rating：支持 `:`, `=`, `>`, `>=`, `<`, `<=`
  - date：支持 `=`, `>`, `>=`, `<`, `<=`，值按 ISO 日期字符串 `YYYY-MM-DD` 比较
- 语法错误返回结构化错误对象，不直接抛到 UI；UI 用红字提示但保留输入内容。

### Repository 接口
- 新增 `list_projects_query(ast) -> list[Project]`
- 动态构造 SQL：递归 AST 生成 `WHERE` 子句 + 必要的 `LEFT JOIN tags / project_field_values`
- 用参数化绑定，禁止字符串拼接 value
- 兜底：`field:value` 中的 `field` 名解析到 system field key 或 user field id；非法字段名警告但不报错
- 多标签 AND 必须用 `EXISTS` 子查询表达，避免单个 join 行无法同时匹配 `tag:科幻 AND tag:翻译`。
- `NOT` 也用 `NOT EXISTS` / 参数化子句生成，避免 join 扩行后误排除。
- 纯关键词 `三体` 等价于 `title:三体 OR description:三体`；不再把 `author` 放进 Phase A 的纯关键词范围，Phase B 若存在作者字段再通过字段查询命中。

### UI
- [x] 主窗口顶部加搜索框 `QLineEdit`，加 placeholder 提示语法
- [x] 200ms 防抖；按 Esc 清空
- [x] 搜索时左栏过滤可选：默认是 AND（搜索范围限定到当前标签）；右上加一个"搜索全部"切换
- [x] 错误的语法显示红字提示，但不阻断输入
- **搜索历史下拉**：焦点进入搜索框时，自动弹出最近搜索列表（存储在 `settings` 表 `key='search_history'`，JSON 数组，最多 20 条）。点击历史条目直接填入并触发搜索。每条历史右侧有 ✕ 删除按钮。
- **收藏表达式**：搜索框右侧加 ⭐ 按钮。当前搜索内容非空时点击弹出命名对话框 → 存入 `settings` 表 `key='saved_searches'`，JSON 格式 `[{"name": "高分科幻", "query": "tag:科幻 AND rating:>=4"}, ...]`。收藏在下拉列表顶部用 ⭐ 前缀区分，右侧有 ✕ 删除。

### Phase C：历史与收藏规则
- 搜索历史：
  - 只保存非空且成功执行的查询；语法错误不保存。
  - 去重后置顶，最多 20 条。
  - 设置键：`search_history`，JSON 数组，例如 `["三体", "tag:科幻 AND rating:>=4"]`。
- 收藏表达式：
  - 设置键：`saved_searches`，JSON 数组。
  - 格式建议：`[{"name": "高分科幻", "query": "tag:科幻 AND rating:>=4", "created_at": "...", "updated_at": "..."}]`
  - 名称不可为空；同名时弹确认覆盖，不静默新增重名。
  - 收藏只保存表达式，不保存当前左侧标签筛选状态。

## 不做范围
- 全文搜索文件内容（成本高，留给 T9）
- 模糊纠错
- 排序定制（保持现行 created_at desc）

## 依赖
- 无（不需要新 schema 字段；搜索历史/收藏存在现有 `settings` 表）

## 风险
- 解析器边界要充分测试：嵌套括号、未闭合引号、特殊字符
- 性能：包含 LIKE 全表扫描的查询在大库下可能慢；先不优化，必要时加 FTS5 虚表
- 字段名冲突：`fields.key` 与显示名可能指向不同字段时，按“key 优先、显示名其次”处理，并在文档中说明。
- 标签层级：左侧 `tag_prefix` 与搜索表达式里的 `tag:` 语义不同，避免混用造成误解。

## 验收
- Phase A：
  - [x] 顶部搜索框可输入，`三体` 能按标题 / 描述过滤项目
  - [x] 当前选中左侧标签时搜索，结果为“标签筛选 AND 关键词”
  - [x] 当前选中层级父标签时搜索，结果为“tag_prefix AND 关键词”
  - [x] Esc 清空搜索后仍保留左侧筛选
  - [x] `query_projects(action="search", keyword="三体")` 与 UI 基础搜索结果一致
- Phase B：
  - [x] 复合查询：`tag:科幻 AND author:刘慈欣 AND rating:>=4` 能正确过滤
  - [x] 纯关键词：`三体` 等价于 `title:三体 OR description:三体`
  - [x] 字段关键词：`author:刘慈欣` 能命中作者字段值
  - [x] 多标签：`tag:科幻 AND tag:翻译` 能命中同时拥有两个标签的项目
  - [x] 错误语法：`tag:(科幻` 显示错误提示，不清空输入，不崩溃
- Phase C：
  - 搜索历史：执行搜索后自动保存，再次点开搜索框显示最近 5 条
  - 收藏表达式：保存"高分科幻"对应 `tag:科幻 AND rating:>=4`，下次从下拉选中直接执行
