"""task #22 字段助手"LLM 建议"列语义重组 — 阶段 A 纯函数 自检。

不依赖 Qt，仅测试 task #22 阶段 A 引入的纯函数 / dataclass 字段：
- ``annotate_conflicts`` 在改名 + 改类型组合时把 LLM 给的新 type 合并到
  ``ann.type``（task #19 收尾清理后，rename 路径不再吞类型）
- ``step1_changed_dimensions`` 给出 ['name', 'type', 'hint'] 子集
- ``step1_action_label`` 把 status × 维度组合 × 决策态映射成 (label, tooltip)
- ``step1_visible_indices`` 收紧：has_llm_change=False 不出现在 Step 1
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T

from app.models import Field
from app.ui.wizards.library_init import (
    DECISION_APPROVED,
    DECISION_PENDING,
    DECISION_REJECTED,
    AnnotatedSuggestion,
    annotate_conflicts,
    step1_action_label,
    step1_changed_dimensions,
    step1_visible_indices,
)


# =============================================================================
# 辅助构造
# =============================================================================
def _f(fid: int, name: str, ftype: str = "text", *, key: str = None,
       prompt_hint: str = "", ord_: int = 0) -> Field:
    return Field(
        id=fid, name=name, type=ftype, ord=ord_, visible=True,
        key=key, suggest_enabled=True, prompt_hint=prompt_hint,
    )


def _ann(name: str, status: str, *, ftype: str = "text",
         prompt_hint: str = "", existing_field_id: int = None,
         existing_field_type: str = "", existing_prompt_hint: str = "",
         llm_rename_new_name: str = "",
         llm_reason: str = "",
         llm_orig_type: str | None = None,
         llm_orig_prompt_hint: str | None = None,
         selected: bool = True,
         decision: str = DECISION_PENDING,
         llm_touched: bool = True) -> AnnotatedSuggestion:
    a = AnnotatedSuggestion(name=name, type=ftype, prompt_hint=prompt_hint)
    a.status = status
    a.existing_field_id = existing_field_id
    a.existing_field_type = existing_field_type
    a.existing_prompt_hint = existing_prompt_hint
    a.llm_rename_new_name = llm_rename_new_name
    a.llm_reason = llm_reason
    # task #22 round 10：在 LLM 触达分支默认镜像 ftype / prompt_hint 到
    # llm_orig_*，模拟 annotate_conflicts 创建 ann 时的快照行为。显式传参
    # 则覆盖（用于模拟"驳回后 ann.type 被还原但 llm_orig_type 仍是 LLM 原值"
    # 的场景）。
    _llm_touched_status = status in (
        "llm_suggest_rename", "type_conflict",
        "same_type", "system_required",
    )
    if llm_orig_type is not None:
        a.llm_orig_type = llm_orig_type
    elif _llm_touched_status:
        a.llm_orig_type = ftype
    if llm_orig_prompt_hint is not None:
        a.llm_orig_prompt_hint = llm_orig_prompt_hint
    elif _llm_touched_status:
        a.llm_orig_prompt_hint = prompt_hint
    a.selected = selected
    a.decision = decision
    a.llm_touched = llm_touched
    return a


# =============================================================================
# step1_changed_dimensions
# =============================================================================
def test_dims_new_empty(t: T) -> None:
    """new 是单维动作，dims 返回空（调用方按 status 分支处理）。"""
    a = _ann("作者", "new", ftype="text")
    t.assert_eq("new dims 空", step1_changed_dimensions(a), [])


def test_dims_delete_empty(t: T) -> None:
    a = _ann("旧字段", "llm_suggest_delete", existing_field_id=1)
    t.assert_eq("delete dims 空", step1_changed_dimensions(a), [])


def test_dims_rename_only(t: T) -> None:
    """改名（type/hint 都没变）→ ['name']。"""
    a = _ann("出版社", "llm_suggest_rename", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="", existing_prompt_hint="",
             llm_rename_new_name="出版商")
    t.assert_eq("rename only", step1_changed_dimensions(a), ["name"])


def test_dims_rename_with_hint(t: T) -> None:
    """改名 + 改 hint → ['name', 'hint']。"""
    a = _ann("出版社", "llm_suggest_rename", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="新提示", existing_prompt_hint="旧提示",
             llm_rename_new_name="出版商")
    t.assert_eq("rename + hint", step1_changed_dimensions(a),
                ["name", "hint"])


def test_dims_rename_with_type(t: T) -> None:
    """改名 + 改类型 → ['name', 'type']。
    task #19 收尾清理后，rename 路径合并 LLM 新 type 到 ann.type，与
    type_conflict 同构判断；不再走"被吞类型"专用分支。"""
    a = _ann("出版社", "llm_suggest_rename", ftype="multiline",  # 已合并的新 type
             existing_field_id=1, existing_field_type="text",
             prompt_hint="", existing_prompt_hint="",
             llm_rename_new_name="出版商")
    t.assert_eq("rename + type", step1_changed_dimensions(a),
                ["name", "type"])


def test_dims_rename_with_all(t: T) -> None:
    """改名 + 改类型 + hint → ['name', 'type', 'hint']。"""
    a = _ann("出版社", "llm_suggest_rename", ftype="multiline",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="新提示", existing_prompt_hint="旧提示",
             llm_rename_new_name="出版商")
    t.assert_eq("rename + all", step1_changed_dimensions(a),
                ["name", "type", "hint"])


def test_dims_type_conflict_only(t: T) -> None:
    """type_conflict 仅类型 → ['type']（hint 没变）。"""
    a = _ann("评分", "type_conflict", ftype="number",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="提示", existing_prompt_hint="提示")
    t.assert_eq("type only", step1_changed_dimensions(a), ["type"])


def test_dims_type_conflict_no_actual_diff_empty(t: T) -> None:
    """task #22 round 10：type_conflict 但 LLM 实际给的 type / hint 都跟
    existing 一致（罕见兜底）→ dims 空。注意现在判据是 llm_orig_* vs
    existing_*，所以"驳回还原"不再让 dims 变空，只有"LLM 本来就没改"才空。"""
    a = _ann("评分", "type_conflict", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="同提示", existing_prompt_hint="同提示")
    t.assert_eq("LLM 实际未改 → dims 空", step1_changed_dimensions(a), [])


def test_dims_type_conflict_with_hint(t: T) -> None:
    """type_conflict + hint 也变 → ['type', 'hint']。"""
    a = _ann("评分", "type_conflict", ftype="number",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="新提示", existing_prompt_hint="旧提示")
    t.assert_eq("type + hint", step1_changed_dimensions(a),
                ["type", "hint"])


def test_dims_same_type_only_hint(t: T) -> None:
    a = _ann("评分", "same_type", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="新提示", existing_prompt_hint="旧提示")
    t.assert_eq("same_type hint", step1_changed_dimensions(a), ["hint"])


def test_dims_system_required_hint(t: T) -> None:
    a = _ann("标题", "system_required", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="新提示", existing_prompt_hint="旧提示")
    t.assert_eq("system_required hint",
                step1_changed_dimensions(a), ["hint"])


# =============================================================================
# step1_action_label
# =============================================================================
def test_label_new_pending(t: T) -> None:
    a = _ann("作者", "new", ftype="text")
    label, tooltip = step1_action_label(a)
    t.assert_eq("new pending label", label, "➕ 新增字段")
    t.assert_in("new pending tooltip 含字段名", "作者", tooltip)


def test_label_new_approved(t: T) -> None:
    a = _ann("作者", "new", decision=DECISION_APPROVED)
    label, _ = step1_action_label(a)
    # task #22 round 13：label 不再带"（已批准）/（已驳回）"后缀（决策态
    # 已由第 0 列标签 + 文字变灰表达，避免冗余）
    t.assert_eq("new approved label 与 pending 相同", label, "➕ 新增字段")


def test_label_new_rejected(t: T) -> None:
    a = _ann("作者", "new", decision=DECISION_REJECTED)
    label, _ = step1_action_label(a)
    t.assert_eq("new rejected label 与 pending 相同", label, "➕ 新增字段")


def test_label_delete_pending(t: T) -> None:
    a = _ann("旧字段", "llm_suggest_delete", existing_field_id=1,
             llm_reason="场景里已不再使用")
    label, tooltip = step1_action_label(a)
    t.assert_eq("delete pending", label, "🗑 删除字段")
    t.assert_in("delete tooltip 含字段名", "旧字段", tooltip)
    # task #22 round 9：tooltip 含 LLM 删除理由
    t.assert_in("delete tooltip 含 LLM 删除理由",
                "场景里已不再使用", tooltip)
    t.assert_in("delete tooltip 含 理由 前缀", "理由：", tooltip)


def test_label_delete_rejected(t: T) -> None:
    a = _ann("旧字段", "llm_suggest_delete",
             existing_field_id=1, decision=DECISION_REJECTED,
             llm_reason="场景里已不再使用")
    label, tooltip = step1_action_label(a)
    # task #22 round 13：label 与 pending 完全相同
    t.assert_eq("delete rejected label 与 pending 相同",
                label, "🗑 删除字段")
    # 驳回后 tooltip 仍展示原始 LLM 删除理由（llm_reason 不被覆盖）
    t.assert_in("驳回后 tooltip 仍含 LLM 理由",
                "场景里已不再使用", tooltip)


def test_label_delete_no_reason(t: T) -> None:
    """LLM 没给删除理由 → tooltip 不出现"理由："前缀，整段省略。"""
    a = _ann("旧字段", "llm_suggest_delete", existing_field_id=1)
    _, tooltip = step1_action_label(a)
    t.assert_eq("无 llm_reason 时不出现 理由：前缀",
                "理由：" in tooltip, False)
    # 但 "批准后会..." 兜底文案仍在
    t.assert_in("仍含 批准后会清掉填值 文案", "批准后", tooltip)


def test_label_rename_only(t: T) -> None:
    a = _ann("出版社", "llm_suggest_rename", ftype="text",
             existing_field_id=1, existing_field_type="text",
             llm_rename_new_name="出版商",
             llm_reason="行业惯用更准确")
    label, tooltip = step1_action_label(a)
    # task #22 round 7：label 只显示改了哪些维度，具体值在 tooltip
    t.assert_eq("rename only label", label, "✏ 修改字段名")
    t.assert_in("rename tooltip 含旧名", "出版社", tooltip)
    t.assert_in("rename tooltip 含新名", "出版商", tooltip)
    # task #22 round 9：tooltip 含 LLM 改名理由
    t.assert_in("rename tooltip 含 LLM 改名理由",
                "行业惯用更准确", tooltip)


def test_label_rename_with_hint(t: T) -> None:
    a = _ann("出版社", "llm_suggest_rename", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="新提示", existing_prompt_hint="旧提示",
             llm_rename_new_name="出版商")
    label, tooltip = step1_action_label(a)
    t.assert_eq("rename + hint label", label, "✏ 修改字段名、提示")
    t.assert_in("tooltip 含新名", "出版商", tooltip)
    t.assert_in("tooltip 含新 hint", "新提示", tooltip)


def test_label_rename_with_type(t: T) -> None:
    """改名 + 改类型 → label 仅 "修改字段名、类型"；具体值在 tooltip。"""
    a = _ann("出版社", "llm_suggest_rename", ftype="multiline",
             existing_field_id=1, existing_field_type="text",
             llm_rename_new_name="出版商")
    label, tooltip = step1_action_label(a)
    t.assert_eq("rename + type label", label, "✏ 修改字段名、类型")
    # 具体值（新名 / 旧→新类型）在 tooltip 里
    t.assert_in("tooltip 含新名", "出版商", tooltip)
    t.assert_in("tooltip 含旧类型 label", "单行文本", tooltip)
    # 不应再出现"被吞类型"副标题或主行 tail
    t.assert_eq("不再含 <small> 副标题", "<small>" in label, False)
    t.assert_eq("label 不再含 → 字符", "→" in label, False)


def test_label_rename_with_type_approved(t: T) -> None:
    a = _ann("出版社", "llm_suggest_rename", ftype="multiline",
             existing_field_id=1, existing_field_type="text",
             llm_rename_new_name="出版商",
             decision=DECISION_APPROVED)
    label, _ = step1_action_label(a)
    # task #22 round 13：label 与 pending 完全相同
    t.assert_eq("rename + type approved label",
                label, "✏ 修改字段名、类型")
    t.assert_eq("不再含 <small>", "<small>" in label, False)


def test_label_type_conflict_only(t: T) -> None:
    a = _ann("评分", "type_conflict", ftype="number",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="同提示", existing_prompt_hint="同提示")
    label, tooltip = step1_action_label(a)
    # task #22 round 7：label 仅 "✏ 修改类型"；具体类型对在 tooltip
    t.assert_eq("type_conflict label", label, "✏ 修改类型")
    # 旧→新 类型对挪到 tooltip：含旧类型 label 与新类型 label
    t.assert_in("tooltip 含旧类型 label", "单行文本", tooltip)
    t.assert_in("tooltip 含新类型 label", "数字", tooltip)
    t.assert_in("tooltip 含数据保留提示", "保留", tooltip)


def test_label_type_conflict_with_hint(t: T) -> None:
    a = _ann("评分", "type_conflict", ftype="number",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="新提示", existing_prompt_hint="旧提示")
    label, tooltip = step1_action_label(a)
    t.assert_eq("type_conflict + hint label",
                label, "✏ 修改类型、提示")
    t.assert_in("tooltip 含新 hint", "新提示", tooltip)


def test_label_type_conflict_rejected(t: T) -> None:
    """task #22 round 13：label 不再加"（已驳回）"后缀，驳回状态由 UI
    渲染（第 0 列标签 + 文字变灰）承担，纯函数 label 与 pending 一致。"""
    a = _ann("评分", "type_conflict", ftype="number",
             existing_field_id=1, existing_field_type="text",
             decision=DECISION_REJECTED)
    label, _ = step1_action_label(a)
    t.assert_eq("rejected 与 pending label 一致",
                label, "✏ 修改类型")


def test_label_type_conflict_no_actual_change(t: T) -> None:
    """LLM 在 type_conflict 路径上但实际 type / hint 都跟 existing 一样
    （罕见兜底场景，例如 LLM 给的 type 字符串经规整化后等于 existing）→
    dims 空 → label 走"保持原样"分支。

    task #22 round 10 起 dims 用 llm_orig_* vs existing_* 判断，所以这条
    路径只在 LLM 真的什么都没改时触发；以前"驳回还原后" dims 也变空，现在
    驳回不再让 dims 变空（llm_orig_* 永不被覆盖）。
    task #22 round 13：label 不再加"（已驳回）"后缀。
    """
    a = _ann("评分", "type_conflict", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="同提示", existing_prompt_hint="同提示",
             decision=DECISION_REJECTED)
    label, tooltip = step1_action_label(a)
    t.assert_eq("LLM 实际未改 → 保持原样", label, "✓ 保持原样")
    # tooltip 仍由 step1_action_label 内部根据 decision 区分两种文案
    t.assert_in("tooltip 含已驳回 说明", "已驳回", tooltip)


def test_label_same_type_no_hint_change(t: T) -> None:
    """same_type 且 LLM 给的 hint 与 existing 一致 → dims 空 → 保持原样。"""
    a = _ann("评分", "same_type", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="原提示", existing_prompt_hint="原提示",
             decision=DECISION_REJECTED)
    label, _ = step1_action_label(a)
    t.assert_eq("same_type LLM 未改 → 保持原样", label, "✓ 保持原样")


# task #22 round 10：用户驳回**不**让 dims 变空，LLM 建议列展示的维度
# 与 LLM 原始建议一致（驳回 = 选择不应用，不是"LLM 没建议过"）
def test_dims_type_conflict_rejected_keeps_type_dim(t: T) -> None:
    """LLM 改了 type，用户驳回 → ann.type 已被 _on_decision_changed 还原回
    existing，但 llm_orig_type 仍是 LLM 原值 → dims 仍含 type。"""
    # 模拟驳回后状态：ann.type 已还原 = existing，但 llm_orig_type 保留
    a = _ann("评分", "type_conflict", ftype="text",  # 已还原
             existing_field_id=1, existing_field_type="text",
             prompt_hint="同提示", existing_prompt_hint="同提示",
             llm_orig_type="number",          # LLM 原值
             llm_orig_prompt_hint="同提示",     # LLM hint 跟旧一致
             decision=DECISION_REJECTED)
    dims = step1_changed_dimensions(a)
    t.assert_eq("驳回后 dims 仍含 type", dims, ["type"])


def test_label_type_conflict_rejected_keeps_modify_label(t: T) -> None:
    """LLM 改了 type，驳回 → label 仍是"修改类型"，不是"保持原样"。
    task #22 round 13：label 不再带"（已驳回）"后缀。"""
    a = _ann("评分", "type_conflict", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="同提示", existing_prompt_hint="同提示",
             llm_orig_type="number", llm_orig_prompt_hint="同提示",
             decision=DECISION_REJECTED)
    label, tooltip = step1_action_label(a)
    t.assert_eq("驳回后仍显示修改类型", label, "✏ 修改类型")
    # tooltip 仍展示 LLM 原本想改成什么
    t.assert_in("tooltip 含 LLM 原本想改的新类型", "数字", tooltip)


def test_label_rename_with_full_modify_rejected(t: T) -> None:
    """LLM 改了字段名 + 类型 + 提示，驳回后 label 应仍显示三项。"""
    a = _ann("出版社", "llm_suggest_rename", ftype="text",  # 已还原 type
             existing_field_id=1, existing_field_type="text",
             prompt_hint="原提示",                # 已还原 hint
             existing_prompt_hint="原提示",
             llm_rename_new_name="出版商",
             llm_orig_type="number",
             llm_orig_prompt_hint="新提示",
             decision=DECISION_REJECTED)
    label, tooltip = step1_action_label(a)
    t.assert_eq("驳回后仍显示完整三项",
                label, "✏ 修改字段名、类型、提示")
    t.assert_in("tooltip 含新名", "出版商", tooltip)
    t.assert_in("tooltip 含新类型", "数字", tooltip)
    t.assert_in("tooltip 含新提示", "新提示", tooltip)


def test_label_same_type_with_hint_change_rejected(t: T) -> None:
    """same_type + LLM 改了 hint，驳回后 label 仍显示"修改提示"。
    task #22 round 13：label 不再带"（已驳回）"后缀。"""
    a = _ann("评分", "same_type", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="原提示",                # 已还原
             existing_prompt_hint="原提示",
             llm_orig_prompt_hint="新提示",
             decision=DECISION_REJECTED)
    label, _ = step1_action_label(a)
    t.assert_eq("same_type 驳回后仍显示修改提示", label, "✏ 修改提示")


def test_label_same_type_hint(t: T) -> None:
    a = _ann("评分", "same_type", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="新提示", existing_prompt_hint="旧提示")
    label, _ = step1_action_label(a)
    t.assert_eq("same_type hint", label, "✏ 修改提示")


def test_label_system_required_hint(t: T) -> None:
    a = _ann("标题", "system_required", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="新提示", existing_prompt_hint="旧提示")
    label, _ = step1_action_label(a)
    t.assert_eq("system_required hint approved 文案",
                label, "✏ 修改提示")


# =============================================================================
# annotate_conflicts: rename + 类型差异 → 把新 type 合并到 ann.type
# （task #19 收尾清理：rename 路径不再吞类型）
# =============================================================================
def test_annotate_rename_with_diff_type_merges(t: T) -> None:
    """LLM fields[<新名>].type 与现有不同 → ann.type 取 LLM 新值。"""
    existing = [_f(1, "出版社", "text")]
    suggestions = [
        # 注意：rename 的"新名"行也在 fields 里，并给了不同 type
        {"name": "出版商", "type": "multiline", "prompt_hint": "新提示"},
    ]
    renames = [{"old_name": "出版社", "new_name": "出版商",
                "reason": "更准确"}]
    out = annotate_conflicts(suggestions, existing, suggested_renames=renames)
    # 找到 status='llm_suggest_rename' 的那条
    rename_ann = [a for a in out if a.status == "llm_suggest_rename"]
    t.assert_eq("有 1 条 rename ann", len(rename_ann), 1)
    t.assert_eq("ann.type=multiline（已合并 LLM 新 type）",
                rename_ann[0].type, "multiline")
    t.assert_eq("existing_field_type 仍是旧值（备份）",
                rename_ann[0].existing_field_type, "text")
    # task #22 round 9：LLM 改名理由存入 llm_reason
    t.assert_eq("llm_reason=更准确",
                rename_ann[0].llm_reason, "更准确")


def test_annotate_delete_fills_llm_reason(t: T) -> None:
    """LLM 显式删除建议带 reason → ann.llm_reason 非空。"""
    existing = [_f(1, "旧字段", "text")]
    deletes = [{"name": "旧字段", "reason": "场景里已不再使用"}]
    out = annotate_conflicts([], existing, suggested_deletes=deletes)
    del_ann = [a for a in out if a.status == "llm_suggest_delete"]
    t.assert_eq("有 1 条 delete ann", len(del_ann), 1)
    t.assert_eq("llm_reason=场景里已不再使用",
                del_ann[0].llm_reason, "场景里已不再使用")


def test_annotate_rename_same_type_no_change(t: T) -> None:
    """LLM fields[<新名>].type 与现有一致 → ann.type 与 existing 同。"""
    existing = [_f(1, "出版社", "text")]
    suggestions = [
        {"name": "出版商", "type": "text", "prompt_hint": "新提示"},
    ]
    renames = [{"old_name": "出版社", "new_name": "出版商"}]
    out = annotate_conflicts(suggestions, existing, suggested_renames=renames)
    rename_ann = [a for a in out if a.status == "llm_suggest_rename"]
    t.assert_eq("type 一致 → ann.type=text",
                rename_ann[0].type, "text")


def test_annotate_rename_no_new_row_keeps_existing_type(t: T) -> None:
    """LLM 没在 fields 里给新名行 → ann.type 沿用 existing 旧值。"""
    existing = [_f(1, "出版社", "text")]
    suggestions = []  # 没有 fields[<新名>] 行
    renames = [{"old_name": "出版社", "new_name": "出版商"}]
    out = annotate_conflicts(suggestions, existing, suggested_renames=renames)
    rename_ann = [a for a in out if a.status == "llm_suggest_rename"]
    t.assert_eq("无 fields[新名] → ann.type=text",
                rename_ann[0].type, "text")


# =============================================================================
# step1_visible_indices 收紧
# =============================================================================
def test_visible_filters_existing_user_field(t: T) -> None:
    suggestions = [
        _ann("a", "existing_user_field", existing_field_id=1, llm_touched=False),
        _ann("b", "new"),
    ]
    t.assert_eq("过滤 existing_user_field",
                step1_visible_indices(suggestions), [1])


def test_visible_keeps_system_required_no_hint_change(t: T) -> None:
    """task #22 round 15：system_required 即使 LLM 没改 hint 也要显示，
    让用户审阅时看到完整字段表（"LLM 看了认为不用改"也是有效审阅结果）。
    该行不显示批准/驳回按钮（has_llm_change=False），不打扰用户。"""
    suggestions = [
        _ann("标题", "system_required", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="同提示", existing_prompt_hint="同提示"),
    ]
    t.assert_eq("system_required 无变更 → 仍显示",
                step1_visible_indices(suggestions), [0])


def test_visible_keeps_system_required_with_hint_change(t: T) -> None:
    suggestions = [
        _ann("标题", "system_required", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="新提示", existing_prompt_hint="旧提示"),
    ]
    t.assert_eq("system_required hint 改 → 出现",
                step1_visible_indices(suggestions), [0])


def test_visible_keeps_same_type_no_hint_change(t: T) -> None:
    """task #22 round 15：same_type 无变更也保留显示。"""
    suggestions = [
        _ann("评分", "same_type", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="同提示", existing_prompt_hint="同提示"),
    ]
    t.assert_eq("same_type 无变更 → 仍显示",
                step1_visible_indices(suggestions), [0])


def test_visible_keeps_same_type_with_hint_change(t: T) -> None:
    suggestions = [
        _ann("评分", "same_type", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="新提示", existing_prompt_hint="旧提示"),
    ]
    t.assert_eq("same_type hint 改 → 出现",
                step1_visible_indices(suggestions), [0])


def test_visible_keeps_rejected_decision_even_after_revert(t: T) -> None:
    """rejected ann：用户驳回 same_type + hint 改动后，`_on_decision_changed`
    会把 ann.prompt_hint 还原回 existing_prompt_hint，但 llm_orig_prompt_hint
    保留 LLM 原值——visible 仍显示该行（dims 仍有 'hint'，且已决定）。"""
    suggestions = [
        _ann("评分", "same_type", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="原提示",            # 已被驳回还原
             existing_prompt_hint="原提示",
             llm_orig_prompt_hint="LLM 新提示",   # 永不覆盖
             decision=DECISION_REJECTED),
    ]
    t.assert_eq("rejected ann（有 LLM 改动）→ 显示",
                step1_visible_indices(suggestions), [0])


def test_visible_keeps_approved_decision_with_change(t: T) -> None:
    """approved + LLM 改了 hint 的 same_type → 显示（视觉一致性）。"""
    suggestions = [
        _ann("评分", "same_type", ftype="text",
             existing_field_id=1, existing_field_type="text",
             prompt_hint="新提示", existing_prompt_hint="旧提示",
             decision=DECISION_APPROVED),
    ]
    t.assert_eq("approved + 有改动 → 显示",
                step1_visible_indices(suggestions), [0])


# =============================================================================
# 边界
# =============================================================================
def test_label_rename_no_new_name(t: T) -> None:
    """llm_rename_new_name 空时 tail 也空（兜底，不应抛错）。"""
    a = _ann("出版社", "llm_suggest_rename", ftype="text",
             existing_field_id=1, existing_field_type="text",
             llm_rename_new_name="")
    label, _ = step1_action_label(a)
    # 没有 → 后缀；不抛错就行
    t.assert_in("仍含 修改字段名", "✏ 修改字段名", label)


def test_dims_rename_type_no_double_count(t: T) -> None:
    """rename + 改类型组合时 'type' 只出现一次（防御性边界）。"""
    a = _ann("出版社", "llm_suggest_rename", ftype="multiline",
             existing_field_id=1, existing_field_type="text",
             llm_rename_new_name="出版商")
    dims = step1_changed_dimensions(a)
    t.assert_eq("type 只出现一次", dims.count("type"), 1)
    t.assert_eq("name 只出现一次", dims.count("name"), 1)


# =============================================================================
# 入口
# =============================================================================
def main() -> int:
    t = T()

    # step1_changed_dimensions
    test_dims_new_empty(t)
    test_dims_delete_empty(t)
    test_dims_rename_only(t)
    test_dims_rename_with_hint(t)
    test_dims_rename_with_type(t)
    test_dims_rename_with_all(t)
    test_dims_type_conflict_only(t)
    test_dims_type_conflict_no_actual_diff_empty(t)
    test_dims_type_conflict_with_hint(t)
    test_dims_same_type_only_hint(t)
    test_dims_system_required_hint(t)

    # step1_action_label
    test_label_new_pending(t)
    test_label_new_approved(t)
    test_label_new_rejected(t)
    test_label_delete_pending(t)
    test_label_delete_rejected(t)
    test_label_delete_no_reason(t)
    test_label_rename_only(t)
    test_label_rename_with_hint(t)
    test_label_rename_with_type(t)
    test_label_rename_with_type_approved(t)
    test_label_type_conflict_only(t)
    test_label_type_conflict_with_hint(t)
    test_label_type_conflict_rejected(t)
    test_label_type_conflict_no_actual_change(t)
    test_label_same_type_no_hint_change(t)
    # round 10：驳回不让 dims 变空
    test_dims_type_conflict_rejected_keeps_type_dim(t)
    test_label_type_conflict_rejected_keeps_modify_label(t)
    test_label_rename_with_full_modify_rejected(t)
    test_label_same_type_with_hint_change_rejected(t)
    test_label_same_type_hint(t)
    test_label_system_required_hint(t)

    # annotate_conflicts rename + type 合并
    test_annotate_rename_with_diff_type_merges(t)
    test_annotate_rename_same_type_no_change(t)
    test_annotate_rename_no_new_row_keeps_existing_type(t)
    test_annotate_delete_fills_llm_reason(t)

    # step1_visible_indices 收紧
    test_visible_filters_existing_user_field(t)
    test_visible_keeps_system_required_no_hint_change(t)
    test_visible_keeps_system_required_with_hint_change(t)
    test_visible_keeps_same_type_no_hint_change(t)
    test_visible_keeps_same_type_with_hint_change(t)
    test_visible_keeps_rejected_decision_even_after_revert(t)
    test_visible_keeps_approved_decision_with_change(t)

    # 边界
    test_label_rename_no_new_name(t)
    test_dims_rename_type_no_double_count(t)

    return 0 if t.report() else 1


if __name__ == "__main__":
    sys.exit(main())
