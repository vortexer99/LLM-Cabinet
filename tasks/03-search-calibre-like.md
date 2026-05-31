# 03 · 类 Calibre 的搜索

**工作量**：M  
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

## 不做范围
- 全文搜索文件内容（成本高，留给 T9）
- 模糊纠错
- 排序定制（保持现行 created_at desc）

## 依赖
- 无（不需要新 schema 字段）

## 风险
- 解析器边界要充分测试：嵌套括号、未闭合引号、特殊字符
- 性能：包含 LIKE 全表扫描的查询在大库下可能慢；先不优化，必要时加 FTS5 虚表

## 验收
- 复合查询：`tag:科幻 AND author:刘慈欣 AND rating:>=4` 能正确过滤
- 纯关键词：`三体` 等价于 `title:三体 OR description:三体 OR author:三体`
