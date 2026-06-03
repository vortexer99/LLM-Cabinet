# 22 · 库字段助手"状态/操作"列语义重组（用户友好化文案）

> **状态**：✅ 完成 2026-06-03
>
> **selftest**：`task22_wizard_status_redesign.py` 44 条断言；task21 91 条 /
> task11_t3 222 条无回归。
>
> **依赖**：task #21 ✅ 已完成（提供 Step 1 / Step 2 双段视图与
> `AnnotatedSuggestion` / `FieldDraft` 数据契约；本卡只改"展示层" + 在 ann
> 上加一个只读字段，不动任何已有纯函数契约）
>
> **工作量**：S（阶段 A 纯函数 + selftest）+ S（阶段 B UI 渲染） = M
>
> **2026-06-03 round 7 事后清理**：本卡引入的
> `AnnotatedSuggestion.llm_pending_type_change` 字段已**废弃**。原始动机
> （task #19 临时把"既改名又改类型"组合的类型部分吞掉，在 ann 上中转给
> Step 1 文案显示）随 task #19 Phase A/B 的安全改类型路径完成而失去
> 意义——`annotate_conflicts` rename 分支现在直接把 LLM 给的新 type 合并到
> `ann.type`，与 `prompt_hint` 走同一套合并逻辑；apply 阶段交给现有的
> `type_changes` 三元组 + 类型变更确认对话框处理。本卡其它结论（"LLM 建议"
> 列文案矩阵、`step1_changed_dimensions` / `step1_action_label` / Step 2
> 删冗余状态列、`step1_visible_indices` 收紧）全部仍然有效；下文涉及
> `llm_pending_type_change` 的描述请参考 round 7 解读。详见 CHANGELOG。


## 背景

task #21 阶段 B 落地后，Step 1 表格的列布局 / 文案仍然带着 task #19 之前
的"开发视角"残留：

**Step 1 当前现状**

| 列 | 当前列名 | 实际承载 | 问题 |
|---|---|---|---|
| 0 | "LLM 建议" | 批准/驳回**按钮** + "已批准/已驳回" 标签 | 列名误导：实际是**操作列** |
| 1 | "状态" | `_STATUS_DISPLAY` 给出的**技术分类** | 实际是 LLM 建议的内容描述；分类还是面向开发者（`系统必有 / 现有同类型 / 类型冲突 / LLM 建议改名`）|
| 2-4 | 字段名 / 类型 / LLM 提示 | 不变 | OK |

`_STATUS_DISPLAY` 8 个分类按 `ann.status` 一对一映射：

```
new                   "✅ 新字段"
system_required       "⭐ 系统必有"
existing_user_field   "📝 现有字段"  （已被 step1_visible_indices 过滤掉）
same_type             "🔁 现有 · 同类型"
type_conflict         "⚠ 类型冲突 · 改类型"
llm_suggest_delete    "🗑 LLM 建议删除"
llm_suggest_rename    "✎ LLM 建议改名 → ..."
```

普通用户看到这些技术词汇会困惑：
- "现有 · 同类型" 是什么意思？我又没问类型
- "系统必有" 看起来像出错了
- "类型冲突" 听起来像 git merge conflict
- 同样是"LLM 建议修改"，新增 / 改类型 / 改名 / 改 hint 没有统一动词

**Step 2 当前现状**

| 列 | 用途 | 问题 |
|---|---|---|
| 0 | 来源徽章 | 受保护字段在操作列显示"（受保护）" — 用户不知道"受保护" = "系统级别字段" |
| 4 | 操作（删除/撤销删除按钮） | 文案 "（受保护）" |
| 5 | 状态（"🗑 将删除"） | 划删线行已经有视觉提示，本列**冗余**——其他行此列空白显示 |

## 目标

把字段助手两段表格的列语义从"开发者分类"重写为"普通用户能直接读懂的动作描述"。

### Step 1 重组

**列名互换 + 重新填充**：

| 列 | 新列名 | 内容 |
|---|---|---|
| 0 | **操作** | 批准/驳回按钮（pending）；"已批准 / 已驳回" 标签（已决策）— 与现行一致 |
| 1 | **LLM 建议** | 用户友好动词："新增"、"删除"、"修改 (...)" — 见下方文案矩阵 |
| 2-4 | 字段名 / 类型 / LLM 提示 | 不动 |

**新文案矩阵**（按 `ann.status` + "改了哪些维度" 组合 + 决策状态映射）：

统一规则：
- 动词永远是 `修改`，后面跟所有改了的维度，逗号分隔（"字段名"、"类型"、"提示"）
- 已批准 → 主文案后接 `（已批准）`
- 已驳回 → 主文案后接 `（已驳回）`，**不**重复 reason 文字（reason 还在 tooltip 里）

| ann.status | 改了哪些维度 | 待决（pending） |
|---|---|---|
| `new` | （新增是单维动作） | ➕ 新增字段 |
| `llm_suggest_delete` | （删除是单维动作） | 🗑 删除字段 |
| `llm_suggest_rename` | 仅字段名 | ✏ 修改字段名 → `<新名>` |
| `llm_suggest_rename` | 字段名 + 提示 | ✏ 修改字段名、提示 → `<新名>` |
| `llm_suggest_rename` | 字段名 + 被吞类型 ⚠ | ✏ 修改字段名 → `<新名>`<br/><small>⚠ LLM 还想改类型为 `<T>`，因保留数据需要批准本次改名后单独操作</small> |
| `llm_suggest_rename` | 字段名 + 提示 + 被吞类型 ⚠ | ✏ 修改字段名、提示 → `<新名>`<br/><small>⚠ LLM 还想改类型为 `<T>`...</small> |
| `type_conflict` | 仅类型 | ✏ 修改类型 `<旧>→<新>` |
| `type_conflict` | 类型 + 提示 | ✏ 修改类型、提示 `<旧>→<新>` |
| `same_type` / `system_required` | 仅提示 | ✏ 修改提示 |

**已批准**：上述每条主文案后追加 `（已批准）`
**已驳回**：上述每条主文案后追加 `（已驳回）`

判据（全部基于 ann 字段，无需引入新逻辑）：
- 改字段名：`ann.status == "llm_suggest_rename"`（必为真）
- 改类型：`ann.status == "type_conflict"` 或 `ann.llm_pending_type_change != ""`（**新字段，见下文**）
- 改提示：`ann._hint_changed()`（已存在）

\* "改字段名 + 改类型" 这个组合在现行 `annotate_conflicts:1028-1040`
被静默吞掉（保留数据 → rename 路径不动 type，仅写 `out_warnings`）。
task #22 不改这个根因决策（保留 task #19 行为），但**必须让用户在 Step 1
看到**——为此新增 `AnnotatedSuggestion.llm_pending_type_change: str = ""`
字段，由 `annotate_conflicts` 在检测到时填入新类型，文案层取它生成
小字提示。Step 1 文案到此为止，单独改类型的入口仍然是
"批准本次改名 → Step 2 字段表 → 改类型"。

废弃：原"系统必有"、"现有 · 同类型"、"类型冲突"等技术分类**不再呈现**。
保护字段的 hint 改建议照样走"修改提示"路径，反正用户改不动 type/name。

### Step 2 重组

| 列 | 改动 |
|---|---|
| 操作列 | "（受保护）"提示 → "（系统保留）" |
| 状态列（第 5 列） | **整列删除**；划删线行的"🗑 将删除"改用字段名右侧的徽章承载（已有"操作"列的"撤销删除"按钮够指示） |

布局：6 列 → 5 列。

### tooltip 文案

每行 LLM 建议列的 tooltip 用人类语言描述本次变更**所有**维度，按 ann
计算出的"改了哪些"动态拼装。模板：

- `➕ 新增字段` → `LLM 建议在库里新增字段「<name>」（类型：<type-label>）。批准后会创建到库里。`
- `🗑 删除字段` → `LLM 建议删除字段「<name>」。批准后会一并清掉该字段在所有项目里的填值（此操作不可恢复）。`
- `✏ 修改...`（动态版）：基础句 + 每改一项追加一段：
  - 改字段名 → `把字段「<旧>」改名为「<新>」（数据保留）`
  - 改类型 → `把类型从「<旧 label>」改为「<新 label>」（旧值仍保留在库里，新控件可能读不出）`
  - 改提示 → `把 LLM 提示更新为「<新提示前 30 字>...」`
  - 被吞类型场景追加 ⚠ 行 → `LLM 还建议把类型改为「<T>」，但 rename 路径为保留数据不动类型，需批准本次改名后到「设置 → 字段」单独改。`

tooltip **不**重复批准/驳回标签的内容；rejected 时仍展示 LLM 原始建议
内容（让用户记得"我刚才驳的是什么"）。

## 设计方案

### 阶段 A：数据契约扩展 + 纯函数 + selftest（不动 UI）

#### A.1 `AnnotatedSuggestion` 新字段

```python
@dataclass
class AnnotatedSuggestion:
    ...
    # task #22：当 LLM 同时建议改名 + 改类型时，rename 路径会吞掉类型变更
    # （annotate_conflicts 的"保留数据"决策），这里记下被吞的新类型，让
    # Step 1 文案 / tooltip 能给用户看到"还想改类型为 X"
    llm_pending_type_change: str = ""
```

`annotate_conflicts:1028-1040` 在写 `out_warnings` 的同时填这个字段：

```python
if new_row_type and new_row_type != ex.type:
    a.llm_pending_type_change = new_row_type   # 新增
    if out_warnings is not None:
        out_warnings.append(...)               # 现有
```

#### A.2 新增 2 个纯函数（无 Qt 依赖）

```python
def step1_changed_dimensions(ann: AnnotatedSuggestion) -> list[str]:
    """返回该 ann 实际改动的维度列表，按约定顺序：
    ['name', 'type', 'hint']（子集）。

    - llm_suggest_rename 必含 'name'
    - type_conflict 必含 'type'
    - llm_pending_type_change 非空 → 也含 'type'（被吞的类型）
    - _hint_changed() 真 → 含 'hint'
    顺序固定为 name → type → hint，便于文案稳定拼装。
    """


def step1_action_label(ann: AnnotatedSuggestion) -> tuple[str, str]:
    """Step 1 「LLM 建议」列的动作文案 + tooltip。

    返回 (label, tooltip)。文案矩阵见 task #22 卡。
    内部调 step1_changed_dimensions(ann) 拿"改了哪些"。
    决策状态由 ann.decision 决定 pending → 不带后缀；
    approved → 主文案后接 "（已批准）"；rejected → 后接 "（已驳回）"。
    被吞类型场景在 label 里追加 "<br/><small>⚠ ...</small>" 行。
    """
```

放在 `library_init.py` 现有 `step1_visible_indices` 等纯函数附近。

#### A.2.1 `step1_visible_indices` 强化过滤

当前实现只过滤 `existing_user_field`，但 `system_required` / `same_type`
两类在"LLM 没改 hint"时 `has_llm_change` 已经返回 False，理应也不出现在
Step 1（否则 Step 1 会有"空动作行"——状态列不显示任何修改、操作列也没
按钮，纯属视觉噪音）。

修改：

```python
def step1_visible_indices(suggestions: list[AnnotatedSuggestion]) -> list[int]:
    return [
        i for i, ann in enumerate(suggestions)
        if ann.status != "existing_user_field"
        and ann.has_llm_change   # 新增：LLM 实际无任何变更的不出现在 Step 1
    ]
```

注意：`new` / `type_conflict` / `llm_suggest_delete` / `llm_suggest_rename`
的 `has_llm_change` 都恒为 True；`system_required` / `same_type` 仅在
`_hint_changed()` 时为 True。所以这条收紧只会过滤掉
"system_required + hint 一致" / "same_type + hint 一致"两类无意义行。

#### A.3 selftest

`selftests/task22_wizard_status_redesign.py`：

| 测试组 | 用例数 | 覆盖 |
|---|---|---|
| `step1_changed_dimensions` | 8 | new / delete / rename only / rename+hint / rename+pending_type / rename+all / type_conflict only / type_conflict+hint / same_type / system_required |
| `step1_action_label` × 3 决策态 | ~30 | 上述 8 类 × pending/approved/rejected 主文案；不需要每条断言 tooltip 全文，挑代表性的几条 |
| 边界 | 5 | 空字段名 / `llm_pending_type_change` 不影响 type_conflict（互斥）/ `_hint_changed()` 在 existing_user_field 上不影响（existing 不进 Step 1）等 |
| `annotate_conflicts` 的新字段 | 3 | rename + 类型差异 → `llm_pending_type_change=新type`；rename + 类型相同 → 空；rename 无 fields[new_name] → 空 |
| `step1_visible_indices` 强化 | 4 | system_required + hint 一致 → 不出现；system_required + hint 改 → 出现；same_type + hint 一致 → 不出现；same_type + hint 改 → 出现 |

合计 ~50 条断言。

selftest **不**测 Qt 渲染（与 task #21 selftest 风格一致）。

### 阶段 B：UI 渲染改造

`library_init.py`：

1. **Step 1 列重排**
   - `_render_preview` 中表头：`["LLM 建议", "状态", ...]` → `["操作", "LLM 建议", ...]`
   - **不动** `tbl.setColumnCount(5)` 等列数
   - `_make_change_cell(row, ann)` 仍渲染到第 0 列（功能不变，只是列名变了）
   - 第 1 列改用 `step1_action_label(ann)` 出文案；旧 `_STATUS_DISPLAY` 删除
   - 颜色规则简化：未决/已批准用主文字色；已驳回 / `🗑 删除` 类用灰字 / 红字

2. **Step 1 渲染相关函数清理**
   - `_STATUS_DISPLAY` 字典删除
   - `_render_preview` 里"llm_suggest_delete / llm_suggest_rename 特殊渲染分支"
     合并进 `step1_action_label`

3. **Step 2 删除状态列**
   - `tbl_step2 = QTableWidget(0, 6)` → `QTableWidget(0, 5)`
   - 列索引常量：`_render_step2_table` 里 `setItem(row, 5, ...)` 删除；
     列宽 / setColumnWidth 同步重排
   - 划删线行的"🗑 将删除"标记改用字段名前缀小图标（如 `🗑 <name>`），
     避免完全失去视觉指示

4. **"（系统保留）"文案**：操作列小字提示
   - 现 `_render_step2_table` 中保护字段操作列文案 "（受保护）" → "（系统保留）"

### 影响面 / 回归点

| 点 | 影响 |
|---|---|
| `task21_wizard_two_step.py` 的 91 条断言 | 不直接断言列文案，纯函数（merge / diff / undelete）契约不变 → 无回归 |
| `task11_t3_library_init_wizard.py` 222 条 | 同上，repo 层断言；如果其中"改名+类型组合"用例曾经断言 `out_warnings` 而**没**断言 ann 上的属性，本卡不会破坏；新建议加 1-2 条断言验证 `llm_pending_type_change` 字段 |
| `_collect_step1_feedback_payload` | 不影响（refine 回灌依赖 ann 字段，不依赖文案）；可考虑把 `llm_pending_type_change` 也回灌让 LLM 知道用户看到了 |
| 应用前汇总对话框（`_ApplySummaryDialog`） | 不影响（已用字段名而非 #fid，task #21 round 4） |

### 阶段 C：文档收尾

- CHANGELOG.md
- TODO.md 把 #22 状态打 ✅
- tasks/README.md 加索引
- 如果 task #21 卡里提到的"两段式向导"截图还在用，更新一下（可选）

## 已澄清决策

1. **状态列分类塌缩**：`new / type_conflict / llm_suggest_rename /
   llm_suggest_delete / same_type / system_required` 6 类塌缩成"新增 / 删除 /
   修改"3 大类。
2. **"修改"展示具体维度**：动词永远用 `修改`，紧跟所有改了的维度（字段名 /
   类型 / 提示），逗号分隔。判据基于 ann 字段 + 新增 `llm_pending_type_change`
   字段（无需引入"组合 status"）。
3. **底层 `ann.status` 不动**：merge / diff / undelete 等纯函数都按 status
   分支，本卡只改 UI 展示层文案 + 在 ann 上加一个只读字段。
4. **"已驳回"不啰嗦**：rejected 行就是主文案后接 `（已驳回）`，不重复 reason
   文字（reason 还在 tooltip 里）；approved 同理后接 `（已批准）`。
5. **"改名 + 改类型"被吞场景**：保留 `annotate_conflicts` 现行决策（rename
   路径不动 type，加 warning），但**让用户在 Step 1 看到**——为此新增
   `AnnotatedSuggestion.llm_pending_type_change: str = ""`，文案层取它生成
   `<small>⚠ LLM 还想改类型为 X，需批准本次改名后单独操作</small>` 副标题。
6. **"系统必有"提示**：完全不再呈现——保护字段反正改不了 type/name，hint 改
   建议用"修改提示"统一文案承载。
7. **Step 2 状态列删除**：划删线视觉 + 字段名前缀小图标 + 操作列的"撤销删除"
   按钮三者足够指示删除态。
8. **"系统保留"文案**：替换"受保护"。考虑过"系统级"、"内置"，最终选"系统保留"
   ——既表达"保留不让删 / 不让改基本属性"又不会让用户误以为是某种保护机制。
9. **tooltip 写法**：人类语言描述本次变更**所有**维度（动态拼装）；rejected
   时也写完整内容（让用户记得"我刚才驳的是什么"）；不重复批准/驳回标签字面。
10. **`step1_visible_indices` 收紧**：除了原有的过滤 `existing_user_field`，
    再加一条 `ann.has_llm_change` —— `system_required` / `same_type` 在
    LLM 没改 hint 的情况下不出现在 Step 1（避免空动作行）。判据复用现有
    `has_llm_change` property，不引入新条件。

## 待澄清

无显式开放问题；以上 10 条决策 + 文案矩阵都有默认值。如对文案 / 图标 /
tooltip 措辞有异议，**编码前**告诉我，否则按本卡实施。
 