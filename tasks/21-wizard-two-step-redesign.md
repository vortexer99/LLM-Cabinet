# 21 · 字段助手两段式重构（审阅 LLM ↔ 编辑字段表分离）

> **状态**：✅ 完成 2026-06-03（阶段 A：纯函数底座 commit `ab38b51`；
> 阶段 B：UI 重构 + 应用前汇总对话框 + refine 双入口）
>
> **selftest**：`task21_wizard_two_step.py` 86 条断言（阶段 A 69 条 +
> 阶段 B 17 条），全套 11 个 selftest 共 725 条全绿。
>
> **依赖**：
> - task #19 Phase A/B 已完成（提供安全的"改字段类型"原语）
> - task #20 已完成（系统字段分流；本卡 Step 1 表格不再为 system_protected
>   留视觉规则）

## 背景

task #19 Phase B 落地后，字段助手「预览页」用一张表同时承载两种性质完全不同的
操作语义：

| 语义 | 主体 | 决策颗粒 | 用户心智 |
|---|---|---|---|
| **A. 审阅 LLM 建议** | LLM 提出，用户点 批准/驳回 | 一行 = 一条建议 | "我在做选择题" |
| **B. 自主编辑字段** | 用户主动新增/改名/改类型/删除 | 一行 = 一个字段 | "我在做表格编辑" |

两种语义共用同一张表 → 每一列（字段名 / 类型 / 决策列）的可编辑性都得**按行
的 ann.status 分类讨论**，规则呈"矩阵"式（粗略表）：

```
                          字段名可改？     类型可改？           决策列可用？
new (LLM 建议)              ✓                ✓                    ✓
same_type                   △ LLM 命中只读  △ 自己加的想可改     ✗
                              / 自己加的可改   / LLM 命中要锁
type_conflict               ✗                △ 在 Phase B 锁     ✓
                                              改自己加的又解开
llm_suggest_rename          ✓                ✓                    ✓
llm_suggest_delete          ✗                ✗                    ✓
existing_user_field         ✗                走护栏可改          ✗
system_required             ✗                ✗                    ✗
system_protected            ✗                ✗                    ✗   ← #20 会删掉
```

**每加一个交互需求就要在这张矩阵里再切一刀**，bug 自然源源不断。最近这一轮的
表现就是：

- task #19 Phase B 想把 type_conflict 的类型 ComboBox 锁住"保持入口单一" →
  导致用户自己新加的、被识别为 same_type 的字段也跟着锁了
- 加 `_on_type_changed` 实现 same_type ↔ type_conflict 双向自动升降级救回来 →
  又引入新的隐式状态迁移
- 而 type_conflict 的字段名只读 / 决策列可用，与 new 行的字段名可改 / 决策列
  可用，混在同一张表里需要用户在每一行重新建立心智

**根因不是规则不对，是两种语义不该共用一张表**。

## 目标

把字段助手从「一张大表」拆成两段式向导（同一对话框、Next/Back）：

* **Step 1 · 审阅 LLM 建议**：只展示 LLM 提的条目，每行就一件事——批准 / 驳回
* **Step 2 · 字段表编辑**：把 Step 1 的批准结果合并进当前字段列表，呈现"应用
  后的最终字段表"，用户在此做纯字段管理（增 / 删 / 改名 / 改类型 / 改 hint）

效果：

- 每一步的 UI 规则塌缩成**线性单一**，不再有"按 ann.status 分类讨论"的矩阵
- 用户对"LLM 到底建议了几条"有清晰的全局感（现在被自加字段稀释）
- type_conflict 不再需要"自动升降级"这种隐式迁移——它只活在 Step 1，Step 2
  看到的就是"最终类型"
- task #20 落地后，Step 2 本质就等同于「设置 → 字段」的预览态，未来可以**与
  设置对话框共用同一个编辑组件**

## 设计方案

### Step 1 · 审阅 LLM 建议

只列 LLM 实际提出的条目，**user-only 的现有字段（`existing_user_field` 且 LLM
没碰）一律不显示**——它们属于 Step 2 的领域。

涉及的 ann 状态：

| 状态 | Step 1 呈现 | 默认决策 |
|---|---|---|
| `new` | 新字段：name / type / hint | 默认批准 |
| `same_type` | 现有字段，LLM 重申同类型 + 可能更新 hint | 默认批准 |
| `type_conflict` | 现有字段，LLM 建议改类型 + 新 hint | 默认批准 |
| `llm_suggest_rename` | LLM 建议把字段 A 改名为 B | 默认批准 |
| `llm_suggest_delete` | LLM 建议删除某字段 | 默认批准 |
| `system_required` | 受保护字段，LLM 提了 hint 更新 | 默认批准（不可改 type/name） |
| `system_protected` | （task #20 后此状态消失，本卡不需为它设计） |

**未决条目 Next 时一律视作已批准，包括 `llm_suggest_delete`**——语义统一，
不为单一状态做特例。删除场景的兜底由 Step 2 划删线展示 + 应用前汇总对话框 +
现有的批量删除确认对话框（task #16）三道护栏共同承担，详见 § 应用流程。

**布局**：表格 5 列

```
[操作]   [类型]      [字段名]              [LLM 建议的提示]                [说明]
批/驳    text        author                "提取作者姓名..."                ✅ 新字段
批/驳    text→date   date                  "格式 YYYY-MM-DD"                ⚠ 类型变更
批/驳    —           rating → 评分          (无变化)                         ✏ 改名
批/驳    text        notes                 (清空 hint)                      🗑 删除
```

- **操作列**：每行一对单选 `[批准] [驳回]`，与现有 `_make_change_cell` 一致
- **字段名列**：纯只读展示
  - `new`：显示新名
  - `same_type` / `type_conflict` / `system_required`：显示现有名
  - `llm_suggest_rename`：显示 `<旧名> → <新名>`
  - `llm_suggest_delete`：显示 `<原名>`（划删线视觉）
- **类型列**：纯只读展示
  - `new` / `same_type`：显示目标 type
  - `type_conflict`：显示 `<旧 type> → <新 type>`（高亮）
  - 其它：留空或显示当前 type
- **LLM 提示列**：双击可编辑（与现有 `new` / `same_type` 路径一致；允许用户
  在批准前微调 hint 文本）。编辑弹窗下半部**显示 LLM 给出的原始 hint**作为
  对照，方便用户参考。
- **说明列**：状态徽章 + reason 文案

Step 1 **没有**「+ 添加字段」按钮——添加字段是 Step 2 的事，与 LLM 建议无关。

底部："共 N 条 LLM 建议，已批准 K 条 / 驳回 R 条 / 未决 P 条" + `[下一步 →]`

### Step 2 · 字段表编辑

Step 1 的批准结果在内存里**合并进当前字段列表**，得到"应用后的最终字段表"，
Step 2 展示并允许编辑这张表。

**关键概念**：Step 2 的数据源是 `list[FieldDraft]`（新 dataclass），不是
`AnnotatedSuggestion`。`FieldDraft` 是"合并后字段表"的统一抽象：

```python
@dataclass
class FieldDraft:
    # 来源标记（仅用于徽章 + 应用前汇总，不驱动编辑性）
    origin: Literal[
        "existing",         # 原本就存在的字段（含老系统字段）
        "llm_new",          # Step 1 批准的 new ann
        "llm_renamed",      # Step 1 批准的 llm_suggest_rename
        "llm_typechanged",  # Step 1 批准的 type_conflict
        "llm_deleted",      # Step 1 批准的 llm_suggest_delete（划删线展示）
        "user_new",         # Step 2 里点「+ 添加字段」加的
    ]
    # 关联到底层 fields.id；llm_new / user_new 为 None
    existing_field_id: Optional[int]
    # 合并时的"原始名"，用于 origin == "llm_renamed" 撤销 / 重名校验
    original_name: Optional[str]
    # 字段三元组（在 Step 2 内可被编辑）
    name: str
    type: str
    prompt_hint: str
    # 编辑标记：True 时该行划删线展示，apply 时进入 deletes
    deleted: bool = False
```

**布局**：表格 5 列

```
[来源徽章]    [字段名]      [类型]      [LLM 提示]               [操作]
📋 现有        title        text        "..."                    (受保护)
📋 现有        author       text        "..."                    [删除]
🤖 LLM 新增    date         date        "格式 YYYY-MM-DD"        [删除]
✏ LLM 改名    评分          number      "..."                    [删除]
⚠ LLM 改类型  rating       number      "1-5 整数评分"           [删除]
🗑 LLM 标删   ~~obsolete~~ ~~text~~    ~~"..."~~                [撤销删除]
👤 新增        tags         tags        ""                       [删除]

[+ 添加字段]
```

- **所有未删除行的字段名 / 类型 / 提示**：**默认可编辑**（不再按 origin 分类讨论）
  - 字段名：QLineEdit；唯一性校验（与"未删除"行比对，被划删线行允许同名占位）
  - 类型：QComboBox；改了走 Phase A 兼容性矩阵 → 不兼容时弹 `_FieldTypeChangeConfirmDialog`（仅对 `origin == "existing"` 且已有数据的字段；其它情况无数据可保护，静默改）
  - 提示：QLineEdit / 双击编辑
- **划删线行**（`deleted == True`）：所有内容只读 + 视觉灰化 + 划删线；只剩
  `[撤销删除]` 按钮
- **受保护字段**（`is_required=True`，即 `title` / `description` / `tags`）：
  字段名 / 类型 / 删除按钮均禁用；提示可改
- **来源徽章**：仅显示，不驱动可编辑性
  - `📋 现有` = 原本就存在的用户字段（含 #20 后的 author/date 等老系统字段）
  - `🤖 LLM 新增` = Step 1 批准的 `new` ann
  - `✏ LLM 改名` = Step 1 批准的 `llm_suggest_rename`
  - `⚠ LLM 改类型` = Step 1 批准的 `type_conflict`
  - `🗑 LLM 标删` = Step 1 批准的 `llm_suggest_delete`（划删线行）
  - `👤 新增` = Step 2 里点「+ 添加字段」加的
- **操作列**：
  - 普通行：`[删除]` —— 标记 `deleted=True`，行变灰划删线
  - 划删线行：`[撤销删除]` —— 标记 `deleted=False`，恢复正常显示。**点击时
    实时校验重名**（详见下节）
- **底部**：`[+ 添加字段]` 加 `origin="user_new"` 空行 + `[← 放弃修改并返回]`
  + `[应用]`

**没有**「↩ 撤销 LLM」按钮。反悔 Step 1 的批准 / 驳回决策，统一走 Back 路径
返回 Step 1 重新决策——配合 Back 的"丢弃当前 Step 2 修改"语义，路径单一。
**唯一例外**是删除（划删线行的 `[撤销删除]`）：因为删除会让字段从最终态消失，
Step 2 必须保留一个"还能反悔"的入口，否则用户看不到的东西就再也找不回来了。

#### 撤销删除的重名冲突处理

点 `[撤销删除]` 时，Step 2 实时扫描 drafts：若存在另一个 `not deleted` 且
`name == 当前划删线行.name` 的 draft → **拒绝撤销，弹错误对话框**：

```
无法撤销删除「<name>」：当前字段表里已有同名字段。

冲突来源：<根据冲突行 origin 给具体描述>
  · 你新增的字段
  · LLM 建议改名后产生的字段
  · 你修改了现有字段「<原名>」的名字
  · LLM 新建的字段

请先调整冲突字段的名字，再撤销此删除。

[确定]
```

**不做自动改名**（违反"用户编辑的字段不该被系统悄悄改"原则）；**不推迟到
应用时校验**（让 Step 2 进入"看似没事但坏掉"的中间态，违反两段式自洽原则）。
就近报错，让用户自己整理冲突字段。

### Step 1 ↔ Step 2 的数据流

```
原 _suggestions: list[AnnotatedSuggestion]  ← LLM annotate 阶段产出
       │
       │  Step 1 用户决策（approved / rejected / pending → 视作 approved）
       │  + Step 1 内可微调 ann.prompt_hint
       ▼
_suggestions（带 decision 状态）                    ┐
       │                                            │ Step 1 [✏ 在当前基础上调整...]
       │  Next 触发 merge_decisions_into_drafts()   │   收集 step1 反馈 + 补充说明
       │  （每次 Next 都重新合并）                   │ → LLM → 新一轮 _suggestions
       ▼                                            ┘  （决策归零、草稿丢弃）
_drafts: list[FieldDraft]                   ← Step 2 用户编辑
       │
       │  Step 2 编辑（改 name / type / hint / 增 / 删 / 撤销删除）
       ▼
_drafts（最终态）                                   ┐
       │                                            │ Step 2 [💾 应用并继续讨论...]
       │  diff_drafts_to_plan()                     │   先走应用流程落库
       ▼                                            │ → 落库后弹补充说明输入框
FieldPlan(type_changes, renames, creates,           │ → LLM（current_fields 取
          updates_hint, deletes)                    │   repo.list_fields()，即落库
       │                                            │   后的真实状态）
       ▼                                            │ → 新一轮 _suggestions
应用前汇总对话框 → 现有的批量类型变更确认 /          │   （决策归零、草稿丢弃）
                  批量删除确认 → apply_field_plan_batch
                                                    ┘
```

#### Back 语义：丢弃 Step 2 修改

`[← 放弃修改并返回]` 的语义是**完全丢弃 Step 2 的所有编辑**（增 / 删 / 改名 /
改类型 / 改 hint），返回 Step 1 重新审阅 LLM 建议。理由：

- 如果 Back 保留 Step 2 编辑，Step 1 改决策再 Next 时 merge 函数要面对"用户在
  Step 2 改了 LLM 改名后的字段名"等等组合，合并逻辑爆炸
- "丢弃修改" + "Step 1 决策保留" 是更可控的语义：**Step 1 是决策态、Step 2 是
  编辑态，两态独立**

具体行为：

- Step 1 的 `approved` / `rejected` 决策**保留**（用户在 Step 1 做的选择不丢）
- Step 2 的 drafts 编辑**全部丢弃**：再次 Next 时重新走一遍
  `merge_decisions_into_drafts()` 产生新的 drafts
- 点击 Back 时如果检测到 Step 2 已有编辑（drafts 与初始合并态不一致），**弹
  确认**：
  ```
  返回会丢弃当前在字段表里做的所有修改（增/删/改名/改类型/改 hint）。
  Step 1 里对 LLM 建议的批准/驳回决策会保留。
  
  确认返回？
  
  [取消]   [放弃修改并返回]
  ```
- 没编辑时点击 → 不弹，直接返回

#### 应用流程

点 Step 2 底部的 `[应用]` → 不直接落库，先弹**应用前汇总对话框**：

```
即将应用以下变更：

📦 新增 3 个字段：date · source_url · notes
✏ 改名 1 个字段：作者 → author
⚠ 改类型 1 个字段：rating（text → rating）
🗑 删除 2 个字段：obsolete_field · deprecated_tag
📝 更新提取提示 4 个字段

[取消]   [<动态文案>]
```

各类目数为 0 时整行不显示（参考 task #19 弹窗的兜底约定）。

**主按钮文案随内容动态切换**（"诚实告知接下来还有几步"）：

| 即将变更含有 | 主按钮文案 | 点击后行为 |
|---|---|---|
| 仅创建 / 改名 / 更新 hint | `[应用]` | 直接落库（一次 `apply_field_plan_batch` 事务） |
| 含改类型（无删除） | `[下一步：确认类型变更]` | 弹批量类型变更确认对话框（复用 task #19 Phase B 现有的 `_BatchTypeChangeConfirmDialog`），确认后落库 |
| 含删除（无改类型） | `[下一步：确认删除]` | 弹批量删除确认对话框（复用 task #16 现有的 `_collect_pending_deletes_dialog`），确认后落库 |
| 含改类型 + 含删除 | `[下一步：确认变更]` | 依次弹批量类型变更确认 → 批量删除确认 → 落库 |

> 改名不需要二次确认（不丢数据、不改语义）；更新 hint 同理。会触发风险提示
> 的只有改类型和删除，因为这两类操作影响数据。

任意一道确认对话框点取消 → 回到 Step 2 表（不回 Step 1），用户可继续编辑或
再次 `[应用]`。

汇总对话框是**最终的"diff 对照页"**，承担三件事：

1. 让用户在落库前看到完整变更清单
2. 主按钮文案诚实告知"接下来还有 X 道确认"，避免用户点了应用突然又冒出弹窗
3. 删除场景的最后一道兜底（即使 Step 1 默认批准了 LLM 删除、Step 2 又被划删线
   一直挂着，用户在这里仍能看到"将删除 K 个字段"，点取消回退）

整个 apply 仍是一次事务（`apply_field_plan_batch` 内 BEGIN/COMMIT），各确认
对话框只在 UI 层串联，不打散事务。

### 多轮对话（refine）的归属

字段助手是**多轮交互**：用户可在每轮预览结果后点"在当前基础上调整..."补一段
说明，让 LLM 在前一轮基础上再来一版。两段式重构后必须明确：refine 入口放
哪一步、回灌什么。

#### 设计决策：双入口 = Step 1 refine + Step 2 应用并继续讨论

**Step 1 底部** 保留 `[✏ 在当前基础上调整...]` 按钮：

- 行为：弹补充说明输入框 → 收集 Step 1 反馈（决策 + 微调过的 hint + 库描述
  编辑）→ 追加到 `_history` → 调 LLM → 回到运行页等结果 → 新一轮 Step 1
- 回灌内容仅含 Step 1 反馈，**不含** Step 2 的字段表编辑（增/删/改名/改类型/
  改 hint）。Step 2 编辑属于"用户对最终落库表的私人处置"，与 LLM 无关
- 拆分函数：`_collect_user_edited_payload` → `_collect_step1_feedback_payload`，
  语义明确化

**Step 2 底部** 新增 `[💾 应用并继续讨论...]` 按钮（在 `[应用]` 旁边）：

- 行为：先走完整应用流程（汇总对话框 + 必要的二次确认 + `apply_field_plan_batch`
  落库）→ 落库成功后**不关闭对话框**，弹补充说明输入框 → 用户提交 → 调 LLM
  （`current_fields` 取 `repo.list_fields()`，即落库后的真实状态）→ 拿到回复
  后回到 Step 1（新一轮，决策归零、Step 2 草稿丢弃）
- 用户在补充说明框点取消 → 应用已落库，对话框正常关闭（等价普通 `[应用]`）
- 与 `[应用]` 的唯一区别在于"应用后是否启动下一轮"

#### 为什么 Step 2 不直接 refine（不带挂起态）

讨论中考虑过让 Step 2 的 refine 直接把字段表编辑回灌给 LLM、不落库就开下一轮。
否决理由：

1. **Step 2 编辑信息量大且语义重叠**：划删线行回灌时 LLM 看到"这字段还在但
   要删"会语义混乱；llm_typechanged 行用户 Step 2 又改回原类型回灌时，等于
   "Step 1 决策 = approved 但 Step 2 实际反悔" —— 同一件事在两层有矛盾态度，
   回灌优先信哪个无解
2. **挂起态让用户对"我现在到底改了什么"失去感知**：Step 2 草稿一旦回灌进新
   一轮，旧草稿就再也找不回来了，用户只能祈祷 LLM 把自己的编辑保留下来
3. **task #19 已经让落库变安全**（改类型有护栏 / 删除有二次确认 / 改名不丢
   数据）→ 落库即承诺的成本极低，不需要靠"挂起态"来给用户试探空间

→ 落库即"承诺当前结构"，下一轮 LLM 看到的是真实库状态而非歧义挂起态，数据
流单向、清晰。

#### 不在 Step 2 提供 `[← 返回 Step 1 后再 refine]` 的快捷入口

用户在 Step 2 想 refine，标准路径是：`[← 放弃修改并返回]` → Step 1 →
`[✏ 在当前基础上调整...]`。多一步点击的代价小于多一个按钮的认知负担。

#### refine 后的状态归零

无论从 Step 1 还是 Step 2 触发 refine，新一轮 LLM 返回后：

- `_suggestions` 完全替换为新一版（旧的批准/驳回挂在新建议上没意义）
- Step 1 决策归零（默认未决 = 接受）
- Step 2 草稿丢弃（如果之前进过 Step 2）
- `_history` 累积（多轮上下文保留）

→ 与现有 refine 行为一致，本卡不改。

### 不动 LLM 调用流

LLM 抓字段、`annotate_conflicts` 等所有后端逻辑都不动。本卡只重构 UI 层 +
Step 1↔Step 2 的本地状态管理。

`AnnotatedSuggestion` dataclass 保留不动（Step 1 仍以它为底）；新增
`FieldDraft` 作为 Step 2 的数据源。

### 字段助手的进入点

LLM 抓完字段后，对话框先展示 Step 1（如果有 LLM 建议）→ Next 进 Step 2 → 应用。
如果没有 LLM 建议（极端场景：解析失败 / 空响应 / 用户跳过 LLM），**直接进
Step 2**，下半段当成"纯字段表编辑器"用，等价于现在的「设置 → 字段」。

### 兼容旧 selftest

`task11_t3_library_init_wizard.py` 当前的纯 Python 断言**几乎全部直接走 repo
层 + `apply_field_plan_batch` 接口**，UI 层重构不影响。需要更新的是：

- 涉及 `_render_preview` / `_on_decision_changed` / `_on_apply` 的 UI 集成
  测试（如果有）→ 改成走新的 `Step1View` / `Step2View`
- 既然底层 `apply_field_plan_batch` 4-tuple 契约不变，repo 层断言全部沿用

## 实施步骤

按依赖排序：

1. **新数据模型**：在 `library_init.py` 加 `FieldDraft` dataclass + 决策枚举
2. **拆 Step 1 视图**：新建 `_Step1ReviewView(QWidget)`，从现有 `_render_preview`
   切出 LLM 建议行的渲染 + 决策信号；不显示 user-only 的 existing_user_field
3. **拆 Step 2 视图**：新建 `_Step2FieldsEditView(QWidget)`，专注字段表编辑：
   - 渲染 `list[FieldDraft]`
   - 行编辑信号（改名 / 改 type / 改 hint / 删 / 撤销删除）
   - 复用 `_FieldTypeChangeConfirmDialog`（Phase A）做不兼容类型实时确认
   - 撤销删除时实时校验重名 → 冲突时弹错对话框拒绝
4. **顶层 wizard**：把 `LibraryInitDialog` 改成 `QStackedWidget` 套 Step1 / Step2，
   `[下一步 →]` 触发 `merge_decisions_into_drafts()` 切到 Step 2，
   `[← 放弃修改并返回]` 时若 drafts 已被编辑则弹确认、未编辑则直接切回
5. **合并函数**：`merge_decisions_into_drafts(suggestions, existing_fields) -> list[FieldDraft]`，
   纯函数，便于测试
6. **diff 函数**：`diff_drafts_to_plan(drafts, original_fields) -> FieldPlan`，
   纯函数；FieldPlan 与现有 `apply_field_plan_batch` 入参对齐
7. **删旧路径**：
   - `_on_type_changed` 里的 same_type ↔ type_conflict 自动升降级 → 删
   - `_on_preview_row_delete` 的 type_conflict 退化逻辑 → 删（Step 2 编辑表
     自然涵盖）
   - `_on_rename_changed` / `_on_decision_changed` 现有的预览页特例分支 → 删
   - **保留**复用：`_BatchTypeChangeConfirmDialog`（task #19 Phase B 加的）
     和 `_collect_pending_deletes_dialog`（task #16 加的）—— 应用前汇总对话框
     按主按钮文案串联调用它们
8. **应用前汇总对话框**：新增 `_ApplySummaryDialog` 类（放 `library_init.py`
   内）；输入 `FieldPlan` → 输出汇总文本 + 主按钮文案；按钮点击后串联调用
   既有的批量类型变更确认 / 批量删除确认对话框
9. **多轮对话（refine）改造**：
   - 拆 `_collect_user_edited_payload` → `_collect_step1_feedback_payload`，
     语义明确化为"只回灌 Step 1 反馈"（决策 + 微调过的 ann.prompt_hint +
     库描述编辑）
   - Step 1 底部 `[✏ 在当前基础上调整...]` 按钮挂在 `_Step1ReviewView` 上，
     行为不变（仍走 `_dispatch_call(extra=...)`）
   - Step 2 底部新增 `[💾 应用并继续讨论...]` 按钮：先走 `[应用]` 同款的
     汇总 → 二次确认 → `apply_field_plan_batch` 落库流程，落库成功后**不
     关闭对话框**，弹补充说明输入框（复用 `_ask_text`）→ 用户提交则触发
     `_dispatch_call(extra=...)` 进入新一轮（`current_fields` 取
     `repo.list_fields()` 即落库后真实状态）；用户取消补充说明则等价普通
     `[应用]`，正常关闭
   - refine 完成回到 Step 1 时：`_suggestions` 替换为新版、Step 1 决策归零、
     Step 2 草稿丢弃、`_history` 累积（与现有行为一致，无需新写）
10. **selftest**：
    - 新增 `selftests/task21_wizard_two_step.py`：
      - 测 `merge_decisions_into_drafts` 各 ann 状态的合并正确性（含 `llm_suggest_delete`
        默认未决 → approved → 产出 `origin="llm_deleted"` 的划删线 draft）
      - 测 `diff_drafts_to_plan` 各 origin × 编辑组合产出的 plan 正确
      - 测"Step 1 批准 type_conflict → Step 2 默认类型为新 type → diff 走 type_changes"
      - 测"Step 1 驳回 type_conflict → Step 2 该字段类型仍是旧 type → 不出现
        在 type_changes"
      - 测"Step 1 批准 llm_suggest_delete + Step 2 用户 user_new 同名字段 +
        撤销删除 → 弹冲突错；冲突字段先改名后撤销删除 → 成功"
      - 测"应用前汇总对话框的主按钮文案"：5 种内容组合（仅创建 / 含改类型 /
        含删除 / 含改类型+删除 / 含全部）→ 文案断言
      - 测 Back 行为：Step 2 改了 drafts → Back 弹确认、确认后 drafts 重置 +
        Step 1 决策保留；未改 drafts → Back 不弹直接切
      - 测 `_collect_step1_feedback_payload` 的输出契约：只含 Step 1 反馈、
        不含 Step 2 编辑（Mock 一个进了 Step 2 又改了字段表的场景，断言 payload
        里看不到 Step 2 的编辑）
    - 改造 `task11_t3_library_init_wizard.py`：UI 集成测试改走 Step1View /
      Step2View；repo 层断言不动
11. **手测**：跑通新建库向导、设置→字段助手两条入口，各做以下场景：
    - 全批准 LLM、Next、应用 → 数据正确
    - 全驳回 LLM、Next（Step 2 看到纯现有字段）、再加 user_new 字段、应用
    - Step 2 改字段类型不兼容 → 弹 Phase A 确认对话框 → 取消 → 类型回滚
    - Step 1 批准 type_conflict → Step 2 又把类型改回去 → apply 不产生 type_change
    - Step 1 批准 llm_suggest_delete → Step 2 划删线行 → 撤销删除 → 字段恢复
    - Step 1 批准 llm_suggest_delete + Step 2 加 user_new 同名字段 + 撤销删除
      → 弹冲突错
    - Step 1 ↔ Step 2 反复 Back / Next，Step 1 决策不丢、Step 2 编辑被丢弃
    - 应用前汇总对话框主按钮文案随内容动态切换；点取消回 Step 2 表
    - **多轮 refine**：Step 1 点 `[✏ 在当前基础上调整...]` 走完 → 新一轮
      Step 1 决策归零；Step 2 点 `[💾 应用并继续讨论...]` 走完 → 字段已落库
      + 新一轮 Step 1 出现，且 LLM 这轮看到的现有字段是落库后状态
12. **文档同步**：`CHANGELOG.md` + `TODO.md` + `tasks/README.md`

## 风险与边界

* **本卡是字段助手的大重构**，工作量上限 M，下限 S+M，取决于"Step 2 的字段表
  组件是否复刻设置对话框 / 还是抽公共组件"。卡片默认 Step 2 自己写一份，**不
  与设置对话框抽公共组件**（避免本卡 scope 失控）；公共组件可作为 task #22 另
  开一卡
* **Back 的"丢弃修改"语义必须用确认对话框兜底**——无对话框直接丢弃用户编辑
  会让人崩溃。selftest 必须覆盖"有编辑 → 弹 → 取消 → 编辑保留"和"有编辑 →
  弹 → 确认 → 编辑丢弃 + Step 1 决策保留"两条路径
* **task #20 与本卡有重叠**：#20 砍掉 `system_protected` 状态、降级老系统字段
  到普通字段；本卡的 Step 1 表格设计已经预期 system_protected 不存在。**强烈
  建议 #20 先做、本卡跟上**，否则 Step 1 还得为 system_protected 留一行视觉
  规则
* **`apply_field_plan_batch` 4-tuple 契约**：本卡不动这个接口；Step 2 的 diff
  函数直接产出符合契约的 plan。如果 plan 里某些组合 repo 层尚未覆盖（例如
  "对同一 fid 同时改名 + 改类型"），需要先确认 repo 层是否正确处理 → 不行
  就先在 repo 层补一支，或者在 diff 函数里拆成两步
* **「+ 添加字段」与现有字段同名**：Step 2 必须做唯一性校验，包括与"被划删线
  的字段对比时应该允许同名"（这正是允许"删旧建新同名"场景的 escape hatch）。
  唯一冲突场景是"先批准 LLM 删除 + 加 user_new 同名 + 又撤销删除"——这条路径
  由撤销删除时的实时校验拦截
* **应用前汇总对话框的"主按钮文案动态化"**实现要点：按钮文案绑定到 FieldPlan
  的内容（type_changes 非空 / deletes 非空），而不是硬编码到调用点；这样将来
  新增风险类操作（比如 #19 已知限制中提到的"meta_suggest 写脏值"防护）只需要
  改动文案规则一处

## 历史决策

本卡的方案 2（两段式）是从三档建议里选出来的：

* **方案 1（轻量改良）**：列加来源徽章，规则按徽章收敛而非按 ann.status →
  改动小但治标不治本，矩阵规则只是从 8 维降到 3 维，仍是矩阵
* **方案 3（同屏分区）**：上半 LLM 建议区 + 下半字段总览区，联动 → 视觉上
  拥挤，窄屏不友好；联动复杂度跟方案 2 差不多
* **方案 2（两段式向导）**：✅ 治本，每一步规则塌缩成线性单一；用户对 LLM
  建议有全局感；与 task #20 + 未来"设置/助手 Step 2 共用组件"自然衔接

→ 选定方案 2 作为本卡方向。

## 已澄清决策（卡片正文按这些决策落笔，无开放问题）

1. **Step 1 未决条目 Next 时一律视作"已批准"**，包括 `llm_suggest_delete`。
   不为单一状态做特例。删除场景的兜底由"Step 2 划删线展示 + 应用前汇总对话框
   + 现有的批量删除确认对话框"三道护栏共同承担。

2. **Step 1 的 hint 双击行内可编辑**。编辑弹窗下半部显示 LLM 给出的原始 hint
   作为对照，方便用户参考。

3. **Back 语义 = 丢弃 Step 2 全部修改 + 保留 Step 1 决策**。按钮文案为
   `[← 放弃修改并返回]`。检测到 drafts 已被编辑时弹确认对话框，未编辑时直接
   切回。理由：保留 Step 2 编辑会让 Step 1 改决策再 Next 时面对"用户在 Step 2
   改了 LLM 改名后的字段名"等组合，合并逻辑爆炸。

4. **Step 2 不提供「↩ 撤销 LLM」按钮**。反悔 Step 1 决策走 Back 路径，路径
   单一。**唯一例外**是删除：划删线行有 `[撤销删除]` 按钮，因为删除会让字段
   从最终态消失，必须保留可见的反悔入口。

5. **撤销删除遇重名 → 弹错拒绝**。不自动改名（违反"用户编辑不该被悄悄改"），
   不推迟到应用时校验（违反"每一步自洽"）。

6. **应用前必经汇总对话框**，列出 5 类变更的统计（创建 / 改名 / 改类型 /
   删除 / 更新 hint）；主按钮文案随内容动态切换：
   - 仅创建 / 改名 / 更新 hint → `[应用]`
   - 含改类型 → `[下一步：确认类型变更]`
   - 含删除 → `[下一步：确认删除]`
   - 含改类型 + 删除 → `[下一步：确认变更]`
   按钮文案"诚实告知"接下来还有几道确认对话框（复用 task #19 的批量类型变更
   确认 + task #16 的批量删除确认）。

7. **不保留旧的"单步预览"模式**。全员走两段式。理由：保留两套 UI 维护成本
   翻倍，两段式仅多一次 Next 点击。

8. **Step 2 不与设置对话框抽公共组件**。本卡只做字段助手内部的两段式重构；
   助手 Step 2 与设置对话框共用组件作为 task #22 另开一卡（如有需要）。

9. **多轮对话（refine）双入口、不引入挂起态回灌**：
   - **Step 1 底部**保留 `[✏ 在当前基础上调整...]`，回灌内容仅含 Step 1
     反馈（决策 + 微调过的 hint + 库描述编辑），**不含** Step 2 的字段表编辑
   - **Step 2 底部**新增 `[💾 应用并继续讨论...]`：先走完整应用流程落库 →
     落库后弹补充说明输入框 → 调 LLM（`current_fields` 取 `repo.list_fields()`
     即落库后真实状态）→ 新一轮 Step 1
   - **不做** Step 2 直接回灌字段表编辑给 LLM 的"挂起态回灌"方案。理由：
     Step 2 编辑信息量大且与 Step 1 决策有语义重叠（例：llm_typechanged 用户
     在 Step 2 又改回原类型 = Step 1 approved 但 Step 2 反悔，回灌优先信哪个
     无解）；task #19 让落库变安全后，"落库即承诺"的成本极低，不需要靠挂起态
     给用户试探空间
   - 拆函数：`_collect_user_edited_payload` → `_collect_step1_feedback_payload`，
     语义明确化


