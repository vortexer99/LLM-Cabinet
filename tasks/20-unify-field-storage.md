# 20 · 废弃系统字段的 projects 列分流，统一用 project_field_values

> **状态**：⚪ 待开工（卡片挂起；等 task #19 Phase B 落地后再考虑动手）
>
> **依赖**：建议在 task #19 Phase B 完成后再开工，避免多个动字段层的改动并行。

## 背景

项目里的"字段"长期处于**二元世界观**：

| 字段类 | 值的存储位置 | 例子 |
|---|---|---|
| **系统字段**（`fields.key IS NOT NULL`） | `projects` 表对应列 | `projects.author` / `projects.date` / `projects.source_url` / `projects.rating` / `projects.description_md` |
| **用户字段**（`fields.key IS NULL`） | `project_field_values(project_id, field_id, value)` | 任意用户添加的字段 |

这种二元分流在产品早期是合理的——最早期只有 6 个固定字段，schema 直接平铺。但
后来引入字段抽象后，**语义上**这些「系统字段」已经退化成普通用户字段：

* 用户能删（除了 `title/description/tags`，其它 system 字段 `is_required=False`）
* 用户能改名
* 用户能改类型（task #19 Phase A 刚加了护栏）
* 任务 #15 T1 已经把 `作者/日期/评分/来源` 这 4 个 system 字段降级成「新建库向导
  里可选勾选」，跟可选用户字段没区别

**唯一遗留**就是「值还存在 `projects` 表的 5 个固定列里」这个存储后端分流。
`db.py:70` 的注释也明承认这一点：

```
--   key 非空 → 系统字段（对应 projects 表中的某列），不可删除
--             目前固定使用 'title' 这一个不可删字段；
--             其它系统字段 key 仅作内部存储后端标识
```

## 当前代码债

`f.is_system` 这个条件渗透到 12+ 处分支判断（grep `is_system`）：

| 位置 | 用途 |
|---|---|
| `app/repository.py::SYSTEM_FIELD_COLUMNS` | key → projects 列名映射表 |
| `app/repository.py::get_field_value` | 读：系统字段读 projects 列、用户字段读 dict |
| `app/repository.py::set_field_value_on_project` | 写：同上分流 |
| `app/repository.py::_collect_field_values_for_all_projects` | 跨项目读：同上分流 |
| `app/repository.py::delete_field` | 删字段时系统字段额外清 projects 列 |
| `app/repository.py::update_fields_batch` | 字段助手 apply：删系统字段时清 projects 列 |
| `app/repository.py::count_field_filled` | task #19 修复就靠这一支正确分流 |
| `app/ui/project_card.py::_field_data` | 项目卡片渲染：读字段值时分流 |
| `app/ui/llm_tasks_panel.py::_current_value` | LLM 建议对比当前值：分流 |
| `app/llm/queue.py::_current_value` | LLM worker 拿当前值：分流（与上一处重复） |
| `app/llm/context.py::_build_metadata_snapshot` | LLM prompt 上下文构造：分流 |
| `app/exporter.py` | 导出 project.json：系统字段单独走一段 |
| `app/importer.py` | 导入 project.json：系统字段单独 reconstruct |
| `app/ui/wizards/library_init.py` | annotate_conflicts 里 `is_system` 守卫（产生 `system_protected` ann），UI 状态列显示「🔒 系统字段」 |
| `app/db.py::_seed_fields` 兼容迁移段 | 把 projects 列值"迁到合适位置"（早期历史） |

每个分支判断都是技术债。Phase B 落地后，#19 留下的「已知限制 #1」（项目编辑器
加载脏值后保存覆盖）如果继续按现有架构修，又得加一支
`if f.is_system: read projects.<col> else: read field_values` —— 债务继续扩张。

**越早拍平越好**。本卡就是这件事。

## 目标

- 删掉「系统字段值存 `projects` 列」这条存储路径
- 所有非保护字段值统一存 `project_field_values`
- `Project` dataclass 顶层只保留**真·身份属性**（`title`、`description_md`、
  `tags`、`storage_mode`、`cover_file_id` 等系统真正依赖的字段），其它降级
- 12+ 处 `if f.is_system` 分支删干净
- `is_system` 这个 property **保留**作为"种入时是否带 key"的元数据标记
  （UI 上仍能展示「这是新建库时勾选的预设字段」），但**不再决定存储路径**

## 设计方案

### 关键问题：哪几个字段保留在 projects 表？

> "系统字段"现在有 6 个：`title` / `description_md` / `tags` / `author` /
> `date` / `source_url` / `rating`。

| 字段 | 现状 | 该不该留在 projects 表 |
|---|---|---|
| `title` | `projects.title NOT NULL` | **必须留**。它是项目身份；外键、UI 列表、文件夹名都依赖它非空 |
| `description_md` | `projects.description_md TEXT` | **必须留**。多处代码（项目导出、LLM 上下文长字段、删字段时"追加到 description"路径）都把它当"项目的长文本备注"用，跟 `title` 同级 |
| `tags` | 走独立的 `tags` 表（多对多） | **必须留独立 schema**。tags 早就不在 projects 列里，没事 |
| `author` / `date` / `source_url` / `rating` | `projects.<col>` | **迁到 project_field_values**。它们就是普通字段 |
| `storage_mode` / `cover_file_id` / `created_at` / `updated_at` | `projects.<col>` | **必须留**。这些不是"字段"，是项目级元数据 |

结论：

* 保留 projects 列：`title`、`description_md`、`storage_mode`、`cover_file_id`、
  `created_at`、`updated_at`（+ `tags` 走独立表不变）
* 迁出 projects 列：`author`、`date`、`source_url`、`rating`

### Schema 迁移（v3 → v4）

新增 `_migrate_v3_to_v4(conn)`：

```python
def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """把 projects.{author,date,source_url,rating} 4 列值迁到 project_field_values。

    幂等：用 PRAGMA table_info 探测列是否还存在；列已被 DROP 则跳过整段迁移。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    cols_to_migrate = [c for c in ("author", "date", "source_url", "rating") if c in cols]
    if not cols_to_migrate:
        return  # 已经迁过

    cur = conn.cursor()
    # 1) 找出每个待迁列对应的 fields.id（按 key 查；这些字段在 fields 表里可能
    #    存在也可能不存在 —— 取决于用户建库时勾选了哪些可选字段）
    key_to_fid: dict[str, int] = {}
    for key in cols_to_migrate:
        row = cur.execute(
            "SELECT id FROM fields WHERE key=?", (key,),
        ).fetchone()
        if row is not None:
            key_to_fid[key] = row[0]

    # 2) 把 projects.<col> 非空值搬进 project_field_values
    #    rating 是 INTEGER，要 CAST 成 TEXT；其它本来就是 TEXT
    for key, fid in key_to_fid.items():
        col = key  # 列名等同 key（约定）
        if key == "rating":
            cur.execute(
                f"INSERT OR REPLACE INTO project_field_values(project_id, field_id, value) "
                f"SELECT id, ?, CAST({col} AS TEXT) FROM projects "
                f"WHERE {col} IS NOT NULL AND {col} != 0",
                (fid,),
            )
        else:
            cur.execute(
                f"INSERT OR REPLACE INTO project_field_values(project_id, field_id, value) "
                f"SELECT id, ?, {col} FROM projects "
                f"WHERE {col} IS NOT NULL AND {col} != ''",
                (fid,),
            )

    # 3) DROP COLUMN：SQLite >= 3.35 (Python 3.12+ 自带) 支持 ALTER TABLE DROP COLUMN
    #    旧版需要"建新表 + 拷数据 + 改名"。项目要求 Python 3.14，SQLite 一定够新
    for col in cols_to_migrate:
        cur.execute(f"ALTER TABLE projects DROP COLUMN {col}")

    # 4) 注意：fields 表里这 4 个 key 的记录**不动**，它们还要继续暴露给用户
    #    （只是值改去 project_field_values 找）。fields.key 仍非空。
```

### `fields.key` 的语义重新定义

迁移后 `fields.key` 不再代表"对应 projects 列名"，而是**降级为"种入时的稳定标识"**：

* `title` / `description` / `tags`：保留 key 用于"受保护字段"判定（`is_required`）
* `author` / `date` / `source_url` / `rating`：保留 key 用于：
  - 新建库向导 / 导入器识别"这是预设字段"
  - `OPTIONAL_DEFAULT_FIELDS` 默认勾选状态
  - 但**不再**决定值的存储位置

注释里 db.py:70 那段也得改。

### Repository 层清理

删除：

* `SYSTEM_FIELD_COLUMNS` dict
* `get_field_value` 里 `if f.is_system: if f.key == ...` 一长串分支 → 全部走
  `project.field_values.get(f.id, "")`，外加 `title` / `description_md` /
  `tags` 三个 special case（这三个真还在 projects 列里 / 独立 tags 表里）
* `set_field_value_on_project` 同上
* `_collect_field_values_for_all_projects` 里 `is_system` 分支删；
  `count_field_filled` 自动跟着对
* `delete_field` 里 "清空 projects 对应列" 一段删
* `update_fields_batch` 里同样的清空段删

### `Project` dataclass

```python
@dataclass
class Project:
    id: Optional[int] = None
    title: str = ""
    # author / date / source_url / rating  ← 全部删掉
    description_md: str = ""
    storage_mode: str = "link"
    cover_file_id: Optional[int] = None
    created_at: str = ""
    updated_at: str = ""
    tags: list[str] = field(default_factory=list)
    field_values: dict[int, str] = field(default_factory=dict)
```

**取舍**：是否给 `Project` 加 `author` / `date` / `rating` / `source_url` 的
**兼容性 property**（读 `field_values` 然后转类型返回）？

* **优点**：避免 12+ 处调用方都得改；老代码 `p.author` 继续工作
* **缺点**：定义循环（property 要查 field_values，field_values 是 `dict[int, str]`
  需要 fid，又要从 `Repository.list_fields` 找——`Project` dataclass 不应依赖
  repo）

**推荐做法**：不加兼容 property，老调用方一律改成 `repo.get_field_value(project, f)`，
那个 helper 早就有了。受影响处用 grep 找：

```bash
rg "\.author\b|\.date\b|\.source_url\b|\.rating\b" --type py -g 'app/**'
```

逐个改。

### UI 层清理

* `project_card._field_data`：`if f.is_system: if f.key == "author" ...` 一长串
  全删，统一走 `field_values.get(f.id)`
* `llm_tasks_panel._current_value` / `queue._current_value`：同上
* `context._build_metadata_snapshot`：同上
* `project_dialog`：项目编辑对话框现在如何渲染 `author/date/source_url/rating`？
  得看 —— 它现在按 `Field.type` 选控件，所以**应该自动跑通**（迁移后这些字段
  通过 `field_values` 进 form），但要 **逐个验证**

### 字段助手的 `system_protected` 状态彻底废除

现在 `app/ui/wizards/library_init.py:565` 有一支：

```python
if (ex.is_system or ex.key in PROTECTED_FIELD_KEYS) and ex.type != a.type:
    a.status = "system_protected"
    a.reason = "已存在的系统字段，类型与建议不符；跳过"
```

实际触发条件 = `is_system=True 但 key not in PROTECTED_FIELD_KEYS` = 仅 `author /
date / source_url / rating` 4 个字段类型跟 LLM 建议不一致时跳过。语义上是
"系统字段类型固定不能改"——但 task #19 Phase A 已经把"改字段类型"做安全了，
这条"系统字段不许改类型"的规则就**没意义了**。

迁移后这 4 个字段不再是 `is_system`，自然走 `type_conflict` 路径（被 task #19
Phase B 的"批准=原地改 / 驳回=不动"接管），用户体验更自然。

**清理动作**：

1. 删 `library_init.py:565-569` 这一支（让上述 4 个字段直接走 `same_type` /
   `type_conflict` 分支）
2. 删 `AnnotatedSuggestion` 注释里 `'system_protected'` 那一行
3. 删 `_STATUS_DISPLAY` 字典里 `"system_protected"` 一项
4. 删 `action` 方法里 `if self.status == "system_protected"` 分支
5. 删 `_render_preview` 里所有 `"system_protected" in ann.status` 守卫
6. 删 `_on_preview_row_delete` 注释里 `system_protected` 的提及

剩下的 `system_required` 状态（标题/描述/标签三个保护字段）**保留**——它的
语义是 `is_required=True`，跟"projects 列存储"无关，迁移后仍然成立。

### 导入导出

* `exporter`：现在的代码里有 `if f.is_system` 一段单独把 4 个系统字段名值
  对一遍。迁移后这一段全删，所有字段统一走 `field_values` 循环
* `importer`：同样把 system 分支删；保留按字段名 / key 双重匹配的容错（导入
  v3 的 project.json 时 `meta.author` 这种顶层 key 仍能识别 → 写进对应 fid 的
  `field_values`）

**注意**：旧 v3 的 project.json 导出格式里 `{"meta": {"author": "鲁迅", ...}}`
顶层就有这些 key，导入 v4 库时需要兼容兜底：检测到 `meta.author` 等老 key →
查 `fields.key='author'` 的 fid → 写进 `field_values`。这是 task #10 已有的
"宽松匹配"思路的延伸。

### `_seed_fields` 兼容段

`db.py::_seed_fields` 早年有一段"把 projects 表里现有的列值迁到合适位置"的
代码。v4 schema 没了这 4 列，那段兼容代码也可以删——但**留着也无害**
（迁移段已经把数据搬走，源列也 DROP 了，兼容段 SELECT 不到东西就 noop）。
**保守做法**：留着，只删调用它的注释指向，避免动 task #15 的种子逻辑。

## 实施步骤

按依赖排序：

1. **schema**：`app/db.py` 加 `_migrate_v3_to_v4`，`SCHEMA_VERSION = 4`，
   `MIGRATIONS` 注册表追加一项；测一遍现有 v3 库能正确迁
2. **CREATE TABLE**：`app/db.py::SCHEMA` 字符串里 `projects` 表去掉 4 列。
   注意全新库走 `executescript(SCHEMA)` 不经迁移，必须 SCHEMA 里就没那 4 列
3. **repo 层**（破坏性改动最大，先做）：
   - 删 `SYSTEM_FIELD_COLUMNS`
   - 改 `get_field_value` / `set_field_value_on_project` /
     `_collect_field_values_for_all_projects` / `delete_field` /
     `update_fields_batch` 的 system 分支
   - 跑 task19 / task11 / task10 selftest 回归
4. **models**：`Project` dataclass 删 4 个字段
5. **导入导出**：exporter / importer 删 system 分支 + 加 v3 兼容兜底
6. **UI 调用方**：grep `.author` / `.date` / `.source_url` / `.rating` 在 app/ 下
   逐个改成 `repo.get_field_value(p, f)` 或 `p.field_values.get(fid)`
7. **字段助手清理**：删 `library_init.py` 里 `system_protected` 状态相关全部
   代码（详见 § UI 层清理 子节）；任何被 LLM 命中且类型不一致的"老系统字段"
   现在自然走 `type_conflict`（task #19 Phase B 接管）
8. **selftest**：
   - 新增 `selftests/task20_unify_field_storage.py`：建一个模拟 v3 库（手写
     `executescript` 弄出含 4 列的 projects + 一些数据）→ open → 验证
     `project_field_values` 里出现迁移数据、`projects` 4 列消失
   - 回归所有现有 selftest：task07 / task10 / task11 / task11_t3 / task14 /
     task15 / task19
9. **手测**：
   - 用一个真实 v3 库（含数据）启动新版，确认数据无丢失、UI 正常
   - 全新建库 → 走向导勾选所有可选字段 → 编辑项目 → 保存 → 关闭重开 → 验证
   - 导出旧 v3 库 → 在新 v4 库里导入 → 验证 `meta.author` 等老 key 落对位置
   - 字段助手里跑一个会让 LLM 给"日期"字段建议改类型的场景，确认不再出现
     「🔒 系统字段」状态，而是走 `type_conflict` 路径
10. **代码规范化（为未来多 tags 字段做预留）**：详见 § 为未来的多 tags 字段
    做的代码规范化。9 处 `f.key == "tags"` / `f.type == "tags"` 条件按"读单一
    内置 tags 字段"vs"判断 tags 类型"分别归类，不引入 schema 变化
11. `CHANGELOG.md` 里标 📦 schema v3 → v4 + 详细说明
12. `docs/migrations.md` 加一节

### 为未来的多 tags 字段做的代码规范化

未来可能允许多个 `type="tags"` 的字段（"主题标签 / 难度标签 / 来源标签"等），
属于另一张大卡（"标签维度系统"，远期）。**#20 不动 tags 的 schema**，但顺手
把现有 9 处 `f.key == "tags"` / `f.type == "tags"` 的混用清理一下，避免未来
做"多 tags"时被现存代码假设阻碍。

判断逻辑分两类：

| 写法 | 语义 | 示例位置 |
|---|---|---|
| `f.key == "tags"` | 这是**那一个**内置 tags 字段，走 `Project.tags` + 独立 tags 多对多表 | `project_card._field_data` 读取、`exporter` / `importer` 的 tags 字段处理、`llm_tasks_panel._current_value` |
| `f.type == "tags"` | **任何** tags 类型字段，做 UI 渲染 / LLM prompt 描述 / 类型判断 | `project_dialog._build_form` 选控件、`project_card._field_data` 渲染、`llm/context._build_metadata_snapshot` 写 tags 提示 |

清理动作：grep 上述 9 处使用，根据语义改写：

- 涉及"读 / 写 `Project.tags` 这条单一字段值"→ 用 `f.key == "tags"`，**不**
  假设"只有一个 type=tags 字段"
- 涉及"渲染 tags UI 控件 / 写 tags 提示文本到 LLM prompt"→ 用 `f.type == "tags"`，
  做成不依赖"全库只有一个 tags 字段"的假设（即使现在循环里只会撞到一次）

**不做**：

- 不扩展 `FIELD_TYPES` 让 `tags` 出现在普通"添加字段"对话框里（这要配合
  schema 的"维度"概念才有意义，留给未来卡）
- 不改 `Project.tags: list[str]` dataclass 字段（仍代表"那一个内置 tags
  字段的值"）
- 不动 task #06 的标签层级折叠逻辑

**收益**：未来做"多 tags 字段"时，扩展 tags 表 schema 加入维度概念 + 让
add_field_dialog 允许选 `type="tags"` 即可，不需要回头清现有代码里的"硬假设
只有一个 tags"。

## 风险与边界

* **schema 迁移是高风险动作**：必须先在自己机器跑一个含真实数据的 v3 库验证；
  迁移前的 `_backup_db` 已经会备份一份 `cabinet.v3.<时间戳>.bak`，但仍要
  人工确认备份能恢复
* **`Project.author` 等属性的代码访问点遍布全代码库**：grep + 逐个改，**别**
  搞自动重写脚本（容易误伤变量名相同但语义无关的地方）
* **导入器的兼容兜底是 v3 → v4 的关键**：旧版备份恢复或跨库迁移都靠它，
  selftest 必须覆盖
* **task #15 新建库向导的"勾选可选字段"路径**：迁移后向导逻辑不应受影响
  （向导只往 `fields` 表插记录，不写值），但要回归测一次
* **task #07 / #10 / #11 / #14 / #19 全部 selftest 必须绿**：动到了字段层的
  基础设施，任何一个回归都是真问题
* **`field_values` dict 的 fid 键值映射**：迁移后所有 UI / 调用方都得通过
  `fields` 表查 fid，会增加一次查表开销。**不构成性能问题**——`Repository`
  本来就缓存了 `list_fields`
* **「is_system」语义的悄悄变化**：不再决定存储路径，但仍标识"种入时带 key"。
  代码注释 / docstring 必须同步更新；否则后人会被"系统字段"这词迷惑
* **多 tags 字段是未来的另一张大卡**（"标签维度系统"，远期）：#20 仅做代码
  规范化预留，不预设 schema。未来真要做时需要的工作至少包括：扩展 tags 表加
  入"维度"概念、让 `add_field_dialog` 允许选 `type="tags"`、给 LLM 协议加多
  tags 字段抓取语义、改 task #06 的标签折叠逻辑支持多维度
  - **不在本卡的 `tasks/README.md` 索引里登记占位**（远期想法尚未成形为可执行
    工作包，登记反而是噪音）；用户提出做的时候再开新卡

## 已澄清决策（卡片正文按这些决策落笔，无开放问题）

1. **不保留 `Project.author` 等的兼容性 property**。所有调用方一律改成
   `repo.get_field_value(project, f)`。理由：dataclass 上加 property 会要求
   它依赖 `Repository.list_fields()` 拿 fid，破坏纯数据语义、形成模块依赖
   倒置。代价是 grep + 逐个改 12+ 处访问点，但这是一次性体力活，改完代码
   更干净。

2. **`rating` 字段值在 `project_field_values.value` 里存为 TEXT**（CAST，如
   `"4"`）。这是事实约束（`project_field_values.value` 列定义就是 TEXT），
   且与用户字段里的 rating 行为一致。

3. **不动 tags 架构**。tags 仍走独立的 `tags` + `project_tags` 多对多表，
   `Project.tags: list[str]` 顶层字段保留。`project_field_values` 里**不**
   存 tags 值。理由：tags 多值语义与 `project_field_values.value: TEXT` 不
   天然契合，且改了会破坏 task #06 标签层级折叠的整套 SQL JOIN 逻辑。

4. **#20 顺手做代码规范化预留多 tags 未来扩展**。详见 § 为未来的多 tags
   字段做的代码规范化。仅清理 `f.key == "tags"` / `f.type == "tags"` 条件
   的语义混用，不引入 schema 变化、不改 `Project.tags` 字段。未来真要做"多
   tags 字段"时另开卡，本卡不预设 schema。

