# 06 · 标签层级分类折叠

**工作量**：S
**优先级**：P2
**状态**：✅ 2026-06-01

## 来源
`TODO.md → 🏷️ 字段 / 标签 / 元数据` 第 1 条

## 目标
左栏标签树支持二级分类（如 `领域/科幻`、`领域/工具书`），可折叠。

## 实现要点

### 数据模型选项

**A. 名称约定（推荐，零迁移）**
- 沿用现有 `tags(name)` 表，约定 `/` 作为分隔符
- UI 渲染时按 `/` 切分自动建树
- 不影响数据库 schema

**B. 显式 parent_tag_id**
- `ALTER TABLE tags ADD COLUMN parent_tag_id INTEGER`
- 数据迁移：现有标签默认 parent=NULL
- 改动较大，但语义更干净

**建议 A**：零迁移成本，且用户在输入框写 `领域/科幻` 就能用。

### UI
- `TagTree.populate` 解析 `/` 分隔的标签名，按层级建 QTreeWidgetItem
- 父节点显示总计数（=子节点之和）
- 父节点点击 = 筛选所有子标签（即"领域/*"）
- 折叠状态持久化

### 与现有"未使用的标签"组的关系
- 未使用组保留为顶层节点
- 在"标签"组内做层级展开

## 依赖
- 无

## 风险
- `/` 字符在标签名里若用户原本就在用（如 `读书/2024`），需提示这是个保留字符
- 标签管理界面（如有）需要同步支持层级编辑

## 落地记录（2026-06-01）

### 设计决策

- **单层折叠**：仅切第一个 `/` 之前作为前缀；不递归再切。`专辑/2024/夏` 与 `专辑/2024` 共享父节点 `专辑`。理由：实际用例不需要更深层级，且 UI 越深越乱
- **同名父标签 + 子标签合并**：如果 `领域` 既作为独立标签又作为前缀，UI 显示为父节点 `📁 领域 (N)` 下挂一个 `#领域（自身）` 子项 + 各子标签
- **未使用标签区不做层级展开**：保留原有平铺，避免视觉混乱

### Repository 改动

`list_projects(tag, tag_prefix)` 增加可选 `tag_prefix` 参数：
- 精确匹配 `<prefix>` 自身 OR `LIKE '<prefix>/%'`
- 与 `tag` 同传时 `tag` 优先（向后兼容）

### UI 改动

- `app/ui/tag_tree.py` 重写 `populate` 逻辑：按 `/` 单层分桶 → 父节点 + 子节点
- 父节点点击 → 发 `filter_changed("tag_prefix", prefix)` 新 kind
- 折叠状态持久化到 `settings.tag_tree_collapsed_prefixes`（注入式 setter/getter，不硬连 Repository）
- `main_window._on_tag_filter_changed` 处理新 kind

### 自检

`selftests/task06_tags_hierarchy.py`，13 个断言全过：
- 精确匹配回归（旧行为不变）
- 前缀匹配（含同名父标签 + 子标签）
- `tag` 优先于 `tag_prefix`
- keyword + tag_prefix 组合
- 父标签删除后前缀仍能匹配子标签
