# 19 · 字段类型变更的安全护栏（库设置 + 字段助手 type_conflict）

> **Phase A 完成（2026-06-02）**：库设置 + 字段助手现有字段表都已走护栏。
> repo 层 + UI 层全部落地，58 条 selftest 通过。
>
> **Phase B 完成（2026-06-02）**：字段助手 `type_conflict` 行改为「批准 = 原地改类型 / 驳回 = 不动」二态语义。删掉 `<原名>_v2` 改名路径；apply 时弹批量类型变更确认对话框；`apply_field_plan_batch` 加 `type_changes` 参数（4-tuple 返回 breaking change）；全套 9 个 selftest 共 580 条断言通过。

## 背景

目前「设置 → 字段」里改字段类型走的是 `set_field_type` 一条 SQL，无确认、
无校验、无迁移。三件后果：

1. **`project_field_values.value`（每个项目里该字段的值）**：列只是 `TEXT`，
   切类型本身不动它；但新类型的控件读不动旧值 → 显示为空 → 用户在「项目元数据
   编辑」对话框里**更新元数据**时，控件的空状态会把原值无声覆盖 → 数据丢失
2. **`fields.prompt_hint`（库级 LLM 提示）**：原文里"格式：YYYY-MM-DD"这种
   描述切到 `rating` 后语义直接报废，但仍被带去喂 LLM
3. **`project_field_suggestions.status='pending'`（项目级待批准建议）**：当时
   按旧类型生成的字符串还挂在 pending；用户进「LLM 建议」面板可能直接接受
   → 把垃圾值写进 `project_field_values`

字段助手「新建库 / 字段助手」里的 `type_conflict` 路径之所以要绕路"改名 + 新建"，
**根本原因就是缺一个能安全改字段类型的入口**。本卡把这件事先解决，字段助手那边
随后能用同一个入口把 type_conflict 的默认行为改简单（详见 § Phase B）。

涉及代码：

* `app/repository.py::set_field_type` — 真正改类型的入口
* `app/ui/settings_dialog.py::_field_change_type` — 库设置 ComboBox 触发点
* `app/ui/wizards/library_init.py` — 字段助手 type_conflict 路径

参考竞品：Notion / Airtable / Baserow 都是"弹确认 + 尽力兼容 + 不兼容值清空 /
保留"的方案 B 模型。Calibre 是反例（禁止改类型，被用户骂十几年）。

## 目标

**Phase A（库设置改类型加护栏）** — 主体工作：

* 类型不兼容时弹确认对话框，明示三件事会发生什么
* `project_field_values.value` **保留不动**（切回旧类型即可恢复显示；语义对齐
  Notion）
* `fields.prompt_hint` 提供"是否同时清空"选项，默认勾选清空
* `project_field_suggestions` 该 fid 所有 pending → `superseded`（与现有
  `add_suggestions` 的 supersede 路径一致）
* 兼容切换（text ↔ textarea）直接静默执行，不打扰用户

**Phase B（字段助手 type_conflict 简化）** — A 完成后做：

* 给 `type_conflict` 行加默认动作"**原地改类型**"按钮，点了走 Phase A 同款流程
* 现有的"改名为 `<原名>_v2` 创建新字段"降级成备选，仍保留但不再是默认
* 砍掉原本计划的"两行展开 + view-row 索引映射 + `old_field_kept` 字段"等一堆
  复杂度（这套方案在草稿期被新方案替代，详见 § 历史决策）

## 设计方案

### Phase A.1：兼容性矩阵

| 旧 → 新 | 兼容性 | 弹窗 |
|---|---|---|
| `text` ↔ `textarea` | **完全兼容** | 跳过弹窗，静默切 |
| 任意 → `text` / `textarea` | 兼容（任何字符串都能显示） | 跳过弹窗 |
| `text` → `date` / `number` / `rating` / `url` | 不兼容（旧值是任意字符串） | 弹窗 |
| `number` → `date` / `rating` / `url` | 不兼容 | 弹窗 |
| `date` → `number` / `rating` / `url` | 不兼容 | 弹窗 |
| `rating` → `number` | 兼容（`"4"` 在 number 控件里能读） | 跳过弹窗 |
| `number` → `rating` | 半兼容（超出 1-5 范围会显示 0 星） | 弹窗 |
| 任何 → `tags` / `tags` → 任何 | 受 `is_required` 保护，已拦在 repo 层 | 不会触发 |

实现集中在一个独立函数：

```python
# app/models.py 或新文件 app/field_type_compat.py
def is_compatible_type_change(old: str, new: str) -> bool: ...
```

把矩阵表硬编码进去；后续加新字段类型只改这一处。

### Phase A.2：弹窗内容

非兼容切换时，由 `_field_change_type` 弹一个自建对话框（不要用
`QMessageBox.question`，因为要塞一个 checkbox）：

```
将「<字段名>」从「<旧类型显示名>」改为「<新类型显示名>」？

· 已有 N 条非空记录的字段值会保留在数据库里，但新类型的控件读不出来
  （切回旧类型即可恢复显示；更新项目元数据时若提交空值会被覆盖）
· 待批准的 LLM 建议 M 条会失效
· 该字段的 LLM 提示「<前 30 字>...」：[☑] 同时清空（推荐）

[取消]                    [确认改类型]
```

文案细节：

* `N` 通过 `SELECT COUNT(*) FROM project_field_values WHERE field_id=? AND value IS NOT NULL AND value!=''`
  得到；N=0 时这一行整条不显示
* `M` 通过 `SELECT COUNT(*) FROM project_field_suggestions WHERE field_id=? AND status='pending'`
  得到；M=0 时这一行整条不显示
* `<前 30 字>` 用 `f.prompt_hint`；为空时这一行整条不显示（因为没东西可清空）
* 三条全为空（N=0 且 M=0 且 hint 空）→ 即使技术上不兼容，也跳过弹窗静默切
  （没什么可保护的）
* 复选框默认勾选；用户取消勾选 = 保留 prompt_hint

### Phase A.3：repository 改造

`Repository.set_field_type` 扩成可选地接受两个开关：

```python
def set_field_type(
    self,
    fid: int,
    ftype: str,
    *,
    supersede_pending_suggestions: bool = False,
    clear_prompt_hint: bool = False,
) -> None:
    f = self.get_field(fid)
    if f is not None and f.is_required:
        return
    cur = self.conn.cursor()
    try:
        cur.execute("BEGIN")
        cur.execute("UPDATE fields SET type=? WHERE id=?", (ftype, fid))
        if clear_prompt_hint:
            cur.execute("UPDATE fields SET prompt_hint='' WHERE id=?", (fid,))
        if supersede_pending_suggestions:
            cur.execute(
                "UPDATE project_field_suggestions "
                "SET status='superseded', resolved_at=datetime('now') "
                "WHERE field_id=? AND status='pending'",
                (fid,),
            )
        self.conn.commit()
    except Exception:
        self.conn.rollback()
        raise
```

**三件事在同一事务**，避免中途崩溃留下不一致状态。

UI 层（`_field_change_type`）按弹窗结果调用：

```python
def _field_change_type(self, fid: int, ftype: str) -> None:
    f = self.repo.get_field(fid)
    if f is None or f.type == ftype:
        return
    if is_compatible_type_change(f.type, ftype):
        self.repo.set_field_type(fid, ftype, supersede_pending_suggestions=False)
        self.fields_changed.emit()
        return

    n_values, m_pending = self._count_field_impact(fid)
    if n_values == 0 and m_pending == 0 and not f.prompt_hint:
        # 没东西可保护，直接切
        self.repo.set_field_type(fid, ftype, supersede_pending_suggestions=False)
        self.fields_changed.emit()
        return

    # 弹窗
    confirmed, clear_hint = FieldTypeChangeConfirmDialog.ask(
        self, f, ftype, n_values, m_pending,
    )
    if not confirmed:
        # 用户取消 → ComboBox 视觉要回滚到旧值（信号回拨）
        self._reload_fields_table()
        return

    self.repo.set_field_type(
        fid, ftype,
        supersede_pending_suggestions=(m_pending > 0),
        clear_prompt_hint=clear_hint,
    )
    self.fields_changed.emit()
```

**注意 ComboBox 回滚**：用户在弹窗里点取消时，`currentIndexChanged` 已经把
ComboBox 选中项改到了新类型；要么调 `_reload_fields_table` 整体重画，要么用
`blockSignals` + `setCurrentIndex(旧)` 精准回退。卡片里用前者（简单稳）。

### Phase A.4：FieldTypeChangeConfirmDialog

新增类（放 `settings_dialog.py` 内或新文件均可）：

```python
class FieldTypeChangeConfirmDialog(QDialog):
    @classmethod
    def ask(cls, parent, field, new_type, n_values, m_pending) -> tuple[bool, bool]:
        """返回 (confirmed, clear_hint)。"""
```

- 用 `QVBoxLayout` 堆三条说明 + 一个 `QCheckBox`（clear hint）+ `QDialogButtonBox`
- 兜底：`field.prompt_hint == ""` 时不画那个 checkbox 那一整块；返回 `clear_hint=False`
- 视觉风格沿用项目里其他自建对话框（参考 `FolderDropModeDialog`、
  `_collect_pending_deletes_dialog`）

### Phase A.5：selftest

在 `selftests/` 加 `task19_field_type_change.py`（参考 `task11_t3_library_init_wizard.py`
的结构，纯 repo 层断言不依赖 Qt）：

* 构造库 + 给某 fid 写若干 `project_field_values` + 若干 pending suggestions
  + 非空 prompt_hint
* 调 `set_field_type(fid, new_type, supersede_pending_suggestions=True, clear_prompt_hint=True)`
* 断言：
  - `fields.type` 已改
  - `project_field_values.value` 一个都没动（原字符串还在）
  - 原 pending 全变 superseded、`resolved_at` 非 NULL
  - `fields.prompt_hint` 变成空串
* 反向用例：`clear_prompt_hint=False` 时 hint 不变；`supersede_pending_suggestions=False`
  时 pending 不变
* 兼容性矩阵函数 `is_compatible_type_change` 的纯函数单测

UI 层弹窗的交互不在 selftest 范围内（项目惯例：UI 弹窗靠手测）。

## Phase B：字段助手 type_conflict 改为「原地改类型」单一路径

A 完成后，`type_conflict` 的语义直接对齐其他 LLM 建议路径，复用现成的
**批准 / 驳回** 两按钮，不引入新 UI 元素：

| 操作 | 实际行为 |
|---|---|
| **批准** | apply 阶段对 `existing_field_id` 改类型 + supersede pending + **用 LLM 给的新 hint 覆盖旧 hint**（三件事进同一事务） |
| **驳回** | 沿用现有 `_on_decision_changed` 的回滚（`rename_to=''` / `selected=False` / `type` 回到 `existing_field_type`） → 字段彻底不动 |
| **未决策（默认接受）** | 跟其他 LLM 建议保持一致：未驳回视为接受，走批准的同款流程 |

「我就是想保留旧字段、另建一个新名字的字段」的需求不再由 type_conflict 路径
承担——用户驳回后，用预览页的「＋ 添加字段」自己加一个新名字的字段即可。
理由：

* 这种需求出现频率低（实际用例里，LLM 给 type_conflict 几乎都意味着"你这字段
  类型本来就不该是那个"——用户绝大多数选项是接受）
* 在表里挂个二选一开关会让全表 5 列布局更挤、给所有用户增加阅读负担
* 「想要新名字字段」是用户主动需求，让用户自己加一行心智更直白

### Phase B 的 prompt_hint 处理（与 Phase A 的关键区别）

Phase A（库设置改类型）的弹窗为什么有「☑ 清空旧 LLM 提示」选项？因为那里
用户只动了 `type`，hint 还是旧的——跟新类型的语义大概率不匹配，所以默认推荐
清空。

Phase B（字段助手 type_conflict）**不存在这个问题**：LLM 的 `fields[i]` 输出
本身就是 `{name, type, prompt_hint}` 三元组，给出新类型时**已经配套给了适配
新类型的 prompt_hint**。`annotate_conflicts` 在第 559 行也把它正确存进了
`ann.prompt_hint`（旧 hint 在 `ann.existing_prompt_hint` 里备份）：

```python
# library_init.py:557-563（现有代码，无需改）
a = AnnotatedSuggestion(
    name=ex.name, type=s.get("type", ex.type),
    prompt_hint=s.get("prompt_hint", ""),   # ← LLM 给的新 hint
)
a.existing_field_id = ex.id
a.existing_field_type = ex.type
a.existing_prompt_hint = ex.prompt_hint     # ← 旧 hint 备份
```

→ 批准 type_conflict 时，apply 的正确动作是 **把 `ann.prompt_hint` 写进
`fields.prompt_hint`**（覆盖旧 hint），跟其他 ann 的常规 `update_hint_only`
路径一致。**绝对不要**默认清空，也不要给用户加 checkbox 选——LLM 给的新 hint
就是答案。

**唯一边界**：LLM 偶尔会在 type_conflict 时给空 hint（`s.get("prompt_hint", "") == ""`）。
这时按现有 `update_fields_batch` 的语义直接写空串，等价于清空旧 hint。这跟
"LLM 让用户改类型但没说提取规则"是一致的：旧规则跟新类型已经不匹配，写空比
留着旧的更对。**不做特殊处理**。

### Phase B 的 UI 改动

预览表里 type_conflict 行的呈现简化为：

* 字段名列：纯只读，显示原名（**移除**现在的「老名 → [LineEdit]」结构）
* 类型列：ComboBox 显示 LLM 建议的新类型，**禁用**（用户改类型的入口在库设置，
  不在这里——保持 Phase B 跟 Phase A 入口单一）
* 状态列：`⚠ 类型冲突 · 改类型`，reason 文案：
  `LLM 建议把现有字段「<name>」从 <旧type> 改为 <新type>，并配套新的提取提示。批准 → 一并更新；驳回 → 保持不变。`
* LLM 提示列：照常显示 LLM 给的新 hint（双击可编辑——跟 `new` / `same_type`
  路径一致）
* LLM 建议列：照常显示「批准 / 驳回」按钮（沿用 `_make_change_cell`）

### apply 流程

字段助手现有的 apply 是一次性事务（`update_fields_batch`）。新增工作：

1. apply 入口的最前面扫一遍 `_suggestions`，收集所有「批准或未驳回的
   type_conflict」 ann → `type_changes: list[(fid, new_type, new_hint)]`
2. 如果非空，弹**一次**类型变更确认对话框（参考 Phase A 风格，但**不含
   清空 hint 的 checkbox**）：
   - 标题：`确认 N 个字段的类型变更`
   - 每个字段列两件事（N 条值保留 / M 条 pending 失效）；**hint 部分一句说明
     "LLM 提供了配套的新提取规则，将一并写入"**
   - 按钮：`[取消]` 回到预览页 / `[确认]` 继续
3. 确认后，在字段助手原事务里**先**执行类型变更三件套（改 type + supersede
   pending + 写新 hint），**再**走原本的 creates / updates_hint / deletes /
   renames（注意：type_conflict 的 hint 已经在第一步里写完了，第二步的
   `updates_hint` 不要再处理这些 fid，避免重复 UPDATE）
4. `repository.update_fields_batch` 加一个新参数 `type_changes: list[tuple[int, str, str]]`
   含义 `(fid, new_type, new_prompt_hint)`，事务里执行：
   ```sql
   UPDATE fields SET type=?, prompt_hint=? WHERE id=?;
   UPDATE project_field_suggestions SET status='superseded',
       resolved_at=datetime('now')
       WHERE field_id=? AND status='pending';
   ```

### 数据层小调整

`AnnotatedSuggestion.action` 在 `type_conflict` 分支的返回值需要改：

```python
# 原来：
if self.status == "type_conflict":
    return "create" if self.rename_to.strip() else "skip"

# 新：
if self.status == "type_conflict":
    if not self.selected:
        return "skip"  # 用户驳回 → 不动
    return "change_type"  # 批准 → 原地改类型 + 更新 hint
```

新增 action `"change_type"`，apply 入口里识别后归到上面 § apply 流程的扫表步骤。
`rename_to` / `effective_name` 字段在 type_conflict 路径下不再使用，但保留
字段定义不动（避免动 dataclass 引发别的地方报错；只是路径上不再走它）。

### Phase B 砍掉的复杂度

原本 #18 草案里的这些东西**全部不做**：

- 两行展开 + view-row 索引映射 (`_view_rows` / `_row_to_sugg`)
- `AnnotatedSuggestion.old_field_kept` 字段
- 行操作"删除"在 old_keep 行上的语义、上下移禁用
- `_smart_reapply_llm` 里 old_field_kept 的重应用合并
- 中间方案里设想的「原地改 / 改名建新」radio 二选一

理由：Phase A 让"改字段类型"本身变安全后，`type_conflict` 只需要复用现有的
"批准/驳回"二态语义即可表达全部意图。

### Phase B 的 selftest

延伸 `task11_t3_library_init_wizard.py`：

* **批准路径**：构造 LLM 输出包含一个跟现有字段同名但类型不同的项，并配上
  适配新类型的 prompt_hint；ann 默认 selected=True、decision pending → 走 apply。
  断言：
  - 旧字段 fid 不变，`fields.type` 改成新值
  - `fields.prompt_hint` = LLM 给的新 hint（不是空，也不是旧 hint）
  - `project_field_values` 一字未动
  - 该 fid 的 pending suggestions superseded
  - 不会出现 `<原名>_v2` 新字段
* **LLM 给了空 hint 的边界**：同上构造但 `prompt_hint=""` → apply 后
  `fields.prompt_hint` 为空（写空覆盖旧 hint），其它断言同上
* **驳回路径**：同样输入，模拟 `_on_decision_changed(idx, "rejected")` → 走
  apply。断言：`fields.type` 不变，pending 不变，hint 仍是旧值；新字段也不创建
* **未决策（默认接受）路径**：等价批准

## 实施步骤

**Phase A**：

1. `app/models.py`（或新文件）写 `is_compatible_type_change` + 单测
2. `app/repository.py::set_field_type` 加两个 kwargs，三件事进同一事务
3. `app/ui/settings_dialog.py` 加 `FieldTypeChangeConfirmDialog` + `_count_field_impact`
   + 改 `_field_change_type`
4. `selftests/task19_field_type_change.py`
5. `CHANGELOG.md` + `TODO.md`

**Phase B**（A merge 后再开工）：

6. `app/ui/wizards/library_init.py`：
   - 改 `AnnotatedSuggestion.action`：type_conflict + selected=True → 返回新
     action `"change_type"`；selected=False → `"skip"`
   - 改 `_render_preview` type_conflict 行：字段名列纯只读（去掉 LineEdit），
     类型列 ComboBox 禁用
   - 状态列文案与 reason 调整
   - 删 `_on_rename_changed` 在 type_conflict 路径下的逻辑（已无 LineEdit 信号）
7. `app/repository.py`：字段助手 apply 入口（`update_fields_batch` 或同名）
   加 `type_changes: list[tuple[int, str, str]]` 参数，含义 `(fid, new_type, new_hint)`，
   在事务最前面对这些 fid 执行三条 SQL（UPDATE type + UPDATE hint + supersede pending）。
   **不要**调 Phase A 的 `Repository.set_field_type` 方法——那会单独 commit；
   直接在字段助手主事务的 cursor 里执行同样的 SQL
8. 字段助手 apply 入口前：扫表收集 type_changes，弹一次确认对话框（参考
   Phase A 风格，**不含**清空 hint 的 checkbox——LLM 已给配套新 hint，直接覆盖）
9. 延伸 `task11_t3_library_init_wizard.py` selftest（批准/空 hint 边界/驳回/
   未决四条路径）
10. 文档同步

## 风险与边界

* **`project_field_values.value` 保留不动**是核心保护。任何"顺手清空一下不兼容
  值"的优化都不要做——Notion 的实践证明"切回去原值还在"是用户的安心感来源
* **真正的数据丢失点是"更新项目元数据"**：项目编辑器加载脏值后控件显示为空，
  用户点保存就会把 NULL 写回去。这个修复**不在本卡范围内**，弹窗文案只承担
  "提醒"职责。后续如需更强保护，可在项目编辑器加载时检测到不兼容值
  → 在该字段控件旁边显示"原值：xxx（与当前类型不兼容，保存将清空）"
  → 另开一张卡
* **并发风险**：用户改类型期间有 `meta_suggest` 任务正在跑，回调写新 pending
  时字段类型已变 → 写进去的还是按旧类型语义的字符串。**本卡暂不处理**，
  作为已知限制记下；后续可在 `add_suggestions` 里加一道"按当前字段类型校验
  suggested_value"的护栏
* **任务面板"重新应用建议"后门**：用户改完类型后进任务面板点"重新应用"，
  会把旧 superseded 的建议重新转 pending → 又是按旧类型的字符串。**本卡暂不堵**，
  作为已知限制记下
* **Phase B 不动 `annotate_conflicts` 的输出契约**，所有改造在 UI 层 + apply 层
* `set_field_type` 现在被 `library_init.py` apply 阶段间接使用过没？要事先扫一遍
  ```bash
  rg "set_field_type" app/
  ```
  把所有调用点确认一遍，避免新加的 kwargs 漏处理（默认值已经向后兼容，但
  Phase B 要主动把 supersede / clear hint 传上）

## 历史决策

最初草稿（编号曾用 #18，已删除）是另一套方案：把 `type_conflict` 行在 UI 上
**展开成两行**（旧字段保留行 + 新字段创建行），让用户分别操作两份。讨论中
发现：

* 这套方案要做大量 view-row 索引映射、新 status、reapply 合并
* 而且 UI 上"按删除按钮在两行上语义不同"会让用户混淆
* 根因不是 UI 缺一行，而是**底层缺一个安全的"改字段类型"操作**

→ 把工作重心调整到"修底层"（本卡 Phase A），UI 顺势变简单（Phase B），双方
都受益。两行展开方案被废弃。

中间还讨论过 Phase B 给用户提供「原地改类型 / 改名建一个新字段」二选一（radio
或双按钮），后来发现完全没必要：

* 「批准 = 接受 LLM 的判断（原地改类型）」「驳回 = 维持原状」二态语义跟其他
  LLM 建议路径完全对称
* 「想保留旧字段、再造一个新名的字段」是低频需求，用户驳回后用预览页的
  「＋ 添加字段」自己加即可，比给所有用户加一个全表 5 列里的开关更直白

→ Phase B 最终落到"只用批准/驳回二态、彻底删掉 `<原名>_v2` 路径"。

## 待澄清

（已全部确认，开工 Phase A）

> 历史确认：Phase B 驳回 type_conflict 时，沿用现有 `llm_suggest_delete` /
> `llm_suggest_rename` 驳回的引导风格 — 把 ann.reason 改成
> `"已驳回；如果想另建一个新名字的字段，用预览表底部的「＋ 添加字段」按钮"`。
