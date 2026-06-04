# 整理新入库文件

你的任务是将文件导入到 LLM Cabinet 项目库。每个文件应归属到合适的项目，并补充完整的元数据。

---

## 全局约束

- **不要猜测用户意图**。遇到歧义时（如文件可能属于 A 项目也可能属于 B 项目），列出候选让用户选择。
- **每一步必须成功才能继续**。如果某步返回 `ok: false`，停止并报告给用户，说明哪一步失败及原因。
- **file_count 不为 0 的项目不等于匹配**。`query_projects(action="search")` 的 file_count 只是项目下已有文件数，不代表文件内容与当前文件相关。
- **field_values 的 key 必须是数字 field.id**。从 `get_fields` 返回的 `id` 字段获取。用 field.name（中文名）做 key 会静默失败。

---

## 可用工具速查

| 工具 / Resource | 用途 |
|---|---|
| `manage_libraries(action="list")` | 列出所有可用库 |
| `manage_libraries(action="switch", library_name=...)` | 切换到指定库 |
| `manage_libraries(action="get_fields")` | 获取当前库的全部字段定义（含 id/name/type/prompt_hint） |
| `query_projects(action="search", keyword=..., tag=...)` | 搜索项目 |
| `query_projects(action="get", project_id=...)` | 获取单个项目详情 |
| `manage_project(action="create", title=..., tags=..., description=..., field_values=...)` | 创建新项目 |
| `manage_project(action="add_tag", project_id=..., tag=...)` | 添加标签 |
| `manage_files(action="add", project_id=..., path=..., storage_mode=..., label=...)` | 添加文件 |
| `manage_files(action="list", project_id=..., kind=...)` | 列出项目下文件 |
| `cabinet://tags` | 库中全部标签及使用计数 |

---

## 操作流程

### 第 1 步：确认目标库

```
manage_libraries(action="list")
→ [{name: "论文库", is_current: true}, {name: "工作文档", is_current: false}]
```

如果用户指定了库名且不是当前库，执行：
```
manage_libraries(action="switch", library_name="工作文档")
→ {"ok": true}
```

**如果返回 `ok: false`**（如单库模式无法切换），告知用户原因并停止。

---

### 第 2 步：发现待整理文件

如果用户通过参数传递了 `directory`，列出该目录下的文件。跳过系统文件（`desktop.ini`、`Thumbs.db`、`.` 开头的隐藏文件）。

如果没有指定目录，由用户口头提供文件路径，进入第 3 步。

---

### 第 3 步：分析文件并匹配项目

对每个文件执行：

1. **提取搜索关键词**：从文件名中提取有意义的部分。去掉扩展名、数字编号、日期前缀等噪音。
   - 例：`attention-is-all-you-need.pdf` → 关键词 `attention`
   - 例：`2403.05632.pdf` → 关键词留空（数字编号无意义），改用文件所在目录名

2. **搜索现有项目**：
   ```
   query_projects(action="search", keyword="attention")
   → [{id: 15, title: "Attention Is All You Need", tags: ["NLP"], file_count: 1}]
   ```

3. **判断匹配**：
   - `keyword` 搜索结果不为空，且 `title` 与文件名高度相关 → **匹配，跳到第 4 步添加文件**
   - 结果为空或完全不相关 → **新建项目**
   - 多个候选难以判断 → **列出候选，让用户选**

---

### 第 4 步：添加文件到已有项目

```
manage_files(action="add", project_id=15, path="D:\\papers\\attention-is-all-you-need.pdf")
→ {"ok": true, "file_id": 27}
```

`storage_mode` 默认为 `link`（不拷贝文件，只记录路径）。如果库设置为 `copy` 模式可传 `storage_mode="copy"`。

---

### 第 5 步：新建项目（无匹配时）

**5a) 创建项目壳**：

```
manage_project(action="create",
    title="Attention Is All You Need",
    description="Vaswani 等人在 2017 年提出的 Transformer 架构奠基论文。引入自注意力机制，完全摒弃循环和卷积结构。",
    tags="学科领域/计算机科学, 文献类型/论文, 阅读状态/未读"
)
→ {"ok": true, "project_id": 42}
```

**5b) 获取字段定义**：

```
manage_libraries(action="get_fields")
→ [
    {id: 19, name: "作者", type: "text", prompt_hint: "请填作者全名，多个作者用英文分号分隔"},
    {id: 20, name: "出版日期", type: "date", prompt_hint: "请填年份或完整日期，格式：YYYY 或 YYYY-MM-DD"},
    ...
]
```

**5c) 推断字段值**：

仔细阅读每个字段的 `prompt_hint`。根据项目的 `title` 和 `description` 为每个有 `suggest_enabled: true` 的字段推断值。

按文件类型推断规则：

| 文件类型 | 常见字段 | 推断来源 |
|---|---|---|
| PDF / 论文 | 作者、出版年份、来源、DOI、核心概念 | 文件名关键词 + description |
| 代码 / 软件 | 版本、平台、语言、许可证 | 文件名/目录名 + description |
| 图片 / 媒体 | 来源、日期、主题 | 文件名 + 目录上下文 |
| 电子书 | 作者、出版社、ISBN | 文件名 + description |
| 通用文档 | 来源、日期、主题 | 文件名 + description |

**5d) 写入字段值**：

```
manage_project(action="update",
    project_id=42,
    field_values='{"19":"Ashish Vaswani; Noam Shazeer; Niki Parmar", "20":"2017-06", "22":"10.5555/3295222"}'
)
```

**关键**：`field_values` 的 key 必须是 `get_fields` 返回的 `id`（数字），**严禁用 field.name（中文名）**。
- ✅ `{"19": "张三", "20": "2024"}` — 19 和 20 是 `get_fields` 看到的 field.id
- ❌ `{"作者": "张三"}` — 会静默失败，数据不保存

**5e) 添加文件**：

```
manage_files(action="add", project_id=42, path="D:\\papers\\attention-is-all-you-need.pdf")
→ {"ok": true, "file_id": 28}
```

---

### 第 6 步：质量自检

完成所有文件后，对自己执行以下检查：

1. ☐ 每个新项目都有 `description`（不为空）
2. ☐ 每个新项目至少有 1 个 tag
3. ☐ `get_fields` 返回的字段中，`suggest_enabled: true` 的字段是否已填写
4. ☐ 每个 field_values 的 key 都是数字 ID（不是中文名）
5. ☐ 所有文件的 `add` 操作都返回了 `ok: true`

---

### 第 7 步：总结报告

向用户报告：

```
✅ 本次入库完成
   - 新建项目：3 个
   - 添加文件：5 个
   - 匹配到已有项目：2 个文件

新建项目：
  1. #42 "Attention Is All You Need"（论文，NLP）
  2. #43 "ResNet"（论文，计算机视觉）
  3. #44 "GPT-4 Technical Report"（论文，NLP）

未处理：无
```

如有失败的文件，逐项说明原因（文件不存在、权限不足等）。

---

## 标签使用规范

- 优先使用 `cabinet://tags` 中已有的标签，新建前检查是否已有等价标签
- 标签格式：`分类/子分类`（如 `学科领域/计算机科学`、`文献类型/论文`、`阅读状态/未读`）
- 常见标签分类：
  - `学科领域/`：计算机科学、物理学、生物学、数学……
  - `文献类型/`：论文、书籍、专利、报告、文档……
  - `阅读状态/`：未读、在读、已读
  - `发表状态/`：预印本、已发表、已接收
  - `项目类型/`：软件、数据集、实验、课程
- 每项目至少包含 2-3 个标签

---

## 错误恢复

| 错误 | 处理 |
|---|---|
| `manage_files add` 返回 `ok: false`（文件不存在） | 跳过该文件，在总结中报告 |
| `manage_libraries switch` 返回 `ok: false` | 单库模式，使用当前库继续 |
| `get_fields` 返回空列表 | 该库尚未配置自定义字段，跳过 5b-5d |
| `query_projects search` 返回大量结果 | 不要全量遍历；用更精确的关键词重搜或让用户选 |
