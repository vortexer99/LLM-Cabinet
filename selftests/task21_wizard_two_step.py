"""task #21 字段助手两段式重构 — 阶段 A 纯函数底座 自检。

不依赖 Qt，仅测试纯函数 / dataclass：
- ``FieldDraft`` / ``FieldPlan`` dataclass 基本契约
- ``merge_decisions_into_drafts`` 各 ann 状态合并正确性
- ``diff_drafts_to_plan`` 各 origin × 编辑组合产出的 plan 正确
- ``check_undelete_name_conflict`` 重名冲突校验
- ``summary_dialog_button_label`` 主按钮文案矩阵

阶段 B（UI 重构）落地后会再加 Step 1 / Step 2 视图集成测试，但那部分依赖 Qt
不在 selftest 范围内（项目惯例：UI 弹窗靠手测）。
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
    DRAFT_ORIGIN_EXISTING,
    DRAFT_ORIGIN_LLM_DELETED,
    DRAFT_ORIGIN_LLM_NEW,
    DRAFT_ORIGIN_LLM_RENAMED,
    DRAFT_ORIGIN_LLM_TYPECHANGED,
    DRAFT_ORIGIN_USER_NEW,
    AnnotatedSuggestion,
    FieldDraft,
    FieldPlan,
    check_undelete_name_conflict,
    clone_draft,
    diff_drafts_to_plan,
    drafts_are_dirty,
    merge_decisions_into_drafts,
    step1_visible_indices,
    summary_dialog_button_label,
)


# =============================================================================
# 辅助构造
# =============================================================================
def _f(fid: int, name: str, ftype: str = "text", *, key: str = None,
       prompt_hint: str = "", ord_: int = 0) -> Field:
    """快速构造 Field 对象。"""
    return Field(
        id=fid, name=name, type=ftype, ord=ord_, visible=True,
        key=key, suggest_enabled=True, prompt_hint=prompt_hint,
    )


def _ann(name: str, status: str, *, ftype: str = "text",
         prompt_hint: str = "", existing_field_id: int = None,
         existing_field_type: str = "", existing_prompt_hint: str = "",
         llm_rename_new_name: str = "", selected: bool = True,
         decision: str = DECISION_PENDING,
         llm_touched: bool = True) -> AnnotatedSuggestion:
    """快速构造 AnnotatedSuggestion 对象。"""
    a = AnnotatedSuggestion(name=name, type=ftype, prompt_hint=prompt_hint)
    a.status = status
    a.existing_field_id = existing_field_id
    a.existing_field_type = existing_field_type
    a.existing_prompt_hint = existing_prompt_hint
    a.llm_rename_new_name = llm_rename_new_name
    a.selected = selected
    a.decision = decision
    a.llm_touched = llm_touched
    return a


# =============================================================================
# merge_decisions_into_drafts 测试
# =============================================================================
def test_merge_existing_user_field_no_llm(t: T) -> None:
    """existing_user_field（LLM 未触及）→ origin=existing。"""
    existing = [_f(1, "ISBN", "text", prompt_hint="13 位")]
    suggestions = [
        _ann("ISBN", "existing_user_field",
             ftype="text", prompt_hint="13 位",
             existing_field_id=1, existing_field_type="text",
             existing_prompt_hint="13 位",
             llm_touched=False),
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    t.assert_eq("existing_user_field 产出 1 条 draft", len(drafts), 1)
    d = drafts[0]
    t.assert_eq("origin=existing", d.origin, DRAFT_ORIGIN_EXISTING)
    t.assert_eq("name=ISBN", d.name, "ISBN")
    t.assert_eq("type=text", d.type, "text")
    t.assert_eq("hint 保留", d.prompt_hint, "13 位")
    t.assert_eq("existing_field_id=1", d.existing_field_id, 1)
    t.assert_true("not deleted", not d.deleted)


def test_merge_llm_new(t: T) -> None:
    """status='new' 默认未决（视作批准）→ origin=llm_new。"""
    existing = []
    suggestions = [
        _ann("作者", "new", ftype="text", prompt_hint="提取作者姓名"),
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    t.assert_eq("llm_new 产出 1 条 draft", len(drafts), 1)
    d = drafts[0]
    t.assert_eq("origin=llm_new", d.origin, DRAFT_ORIGIN_LLM_NEW)
    t.assert_eq("name=作者", d.name, "作者")
    t.assert_true("existing_field_id=None", d.existing_field_id is None)


def test_merge_llm_new_rejected(t: T) -> None:
    """status='new' 但用户驳回 → 不产出 draft。"""
    existing = []
    suggestions = [
        _ann("作者", "new", ftype="text",
             decision=DECISION_REJECTED, selected=False),
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    t.assert_eq("驳回的 new 不产出", len(drafts), 0)


def test_merge_type_conflict_approved(t: T) -> None:
    """type_conflict 默认未决（视作批准）→ origin=llm_typechanged，
    type 取 LLM 新值，original_type 记录旧值。"""
    existing = [_f(1, "评分", "text", prompt_hint="旧 hint")]
    suggestions = [
        _ann("评分", "type_conflict",
             ftype="rating", prompt_hint="1-5 整数",
             existing_field_id=1, existing_field_type="text",
             existing_prompt_hint="旧 hint"),
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    t.assert_eq("type_conflict 产出 1 条 draft", len(drafts), 1)
    d = drafts[0]
    t.assert_eq("origin=llm_typechanged", d.origin,
                DRAFT_ORIGIN_LLM_TYPECHANGED)
    t.assert_eq("type 取 LLM 新值", d.type, "rating")
    t.assert_eq("original_type 记录旧 type", d.original_type, "text")
    t.assert_eq("hint 取 LLM 新值", d.prompt_hint, "1-5 整数")
    t.assert_eq("name 保持原名", d.name, "评分")


def test_merge_type_conflict_rejected(t: T) -> None:
    """type_conflict 用户驳回 → 退化为 origin=existing，type/hint 保持原值。"""
    existing = [_f(1, "评分", "text", prompt_hint="旧 hint")]
    suggestions = [
        _ann("评分", "type_conflict",
             ftype="rating", prompt_hint="1-5 整数",
             existing_field_id=1, existing_field_type="text",
             existing_prompt_hint="旧 hint",
             decision=DECISION_REJECTED, selected=False),
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    t.assert_eq("驳回 type_conflict 仍产出 1 条 draft", len(drafts), 1)
    d = drafts[0]
    t.assert_eq("origin 退化为 existing", d.origin, DRAFT_ORIGIN_EXISTING)
    t.assert_eq("type 保持原值", d.type, "text")
    t.assert_eq("hint 保持原值", d.prompt_hint, "旧 hint")


def test_merge_llm_suggest_delete_approved(t: T) -> None:
    """llm_suggest_delete 默认未决（视作批准）→ origin=llm_deleted，划删线。"""
    existing = [_f(1, "废弃字段", "text")]
    suggestions = [
        _ann("废弃字段", "llm_suggest_delete",
             ftype="text",
             existing_field_id=1, existing_field_type="text"),
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    t.assert_eq("llm_suggest_delete 产出 1 条 draft", len(drafts), 1)
    d = drafts[0]
    t.assert_eq("origin=llm_deleted", d.origin, DRAFT_ORIGIN_LLM_DELETED)
    t.assert_true("deleted=True（划删线）", d.deleted)
    t.assert_eq("existing_field_id 保留", d.existing_field_id, 1)


def test_merge_llm_suggest_delete_rejected(t: T) -> None:
    """llm_suggest_delete 用户驳回 → 退化为 existing。"""
    existing = [_f(1, "废弃字段", "text")]
    suggestions = [
        _ann("废弃字段", "llm_suggest_delete",
             ftype="text",
             existing_field_id=1,
             decision=DECISION_REJECTED, selected=False),
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    d = drafts[0]
    t.assert_eq("驳回 delete → origin=existing", d.origin,
                DRAFT_ORIGIN_EXISTING)
    t.assert_true("not deleted", not d.deleted)


def test_merge_llm_suggest_rename_approved(t: T) -> None:
    """llm_suggest_rename 默认未决（视作批准）→ origin=llm_renamed，
    name 取 LLM 新名，original_name 记录旧名。"""
    existing = [_f(1, "出版社", "text")]
    suggestions = [
        _ann("出版社", "llm_suggest_rename",
             ftype="text",
             existing_field_id=1,
             llm_rename_new_name="出版商"),
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    d = drafts[0]
    t.assert_eq("origin=llm_renamed", d.origin, DRAFT_ORIGIN_LLM_RENAMED)
    t.assert_eq("name 取 LLM 新名", d.name, "出版商")
    t.assert_eq("original_name 记录旧名", d.original_name, "出版社")


def test_merge_llm_suggest_rename_approved_with_new_hint(t: T) -> None:
    """task #22 round 6：llm_suggest_rename 批准时 hint 取 ann.prompt_hint
    （annotate_conflicts 已把 LLM 在 fields[new_name] 里给的 hint 合并到
    这里），不再用 f.prompt_hint。原 bug：批准改名 + 改 hint 后 Step 2 看到
    的还是库里原 hint。"""
    existing = [_f(1, "出版社", "text", prompt_hint="原提示")]
    suggestions = [
        _ann("出版社", "llm_suggest_rename",
             ftype="text",
             existing_field_id=1,
             prompt_hint="新提示",                 # annotate_conflicts 合并后的 hint
             existing_prompt_hint="原提示",
             llm_rename_new_name="出版商"),
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    d = drafts[0]
    t.assert_eq("批准 rename 后 Step 2 看到 LLM 新 hint",
                d.prompt_hint, "新提示")


def test_merge_llm_suggest_rename_rejected(t: T) -> None:
    """llm_suggest_rename 用户驳回 → 退化为 existing 保留原名。"""
    existing = [_f(1, "出版社", "text")]
    suggestions = [
        _ann("出版社", "llm_suggest_rename",
             ftype="text",
             existing_field_id=1,
             llm_rename_new_name="出版商",
             decision=DECISION_REJECTED, selected=False),
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    d = drafts[0]
    t.assert_eq("驳回 rename → origin=existing", d.origin,
                DRAFT_ORIGIN_EXISTING)
    t.assert_eq("name 保持原名", d.name, "出版社")


def test_merge_same_type_hint_update(t: T) -> None:
    """same_type 现有 hint 为空 + LLM 给非空 hint → hint 被更新。"""
    existing = [_f(1, "子流派", "text", prompt_hint="")]
    suggestions = [
        _ann("子流派", "same_type",
             ftype="text", prompt_hint="硬科幻 / 软科幻",
             existing_field_id=1, existing_field_type="text",
             existing_prompt_hint=""),
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    d = drafts[0]
    t.assert_eq("origin=existing", d.origin, DRAFT_ORIGIN_EXISTING)
    t.assert_eq("hint 被 LLM 更新", d.prompt_hint, "硬科幻 / 软科幻")


def test_merge_same_type_hint_kept(t: T) -> None:
    """same_type 现有 hint 非空 + decision=rejected → 跳过不覆盖。

    task #21 阶段 B 改造：旧规则是"现有 hint 非空 → 静默跳过"；新规则是
    "未决=接受 → 覆盖；驳回 → 保留"。让用户对 LLM 的 hint 改动有显式控制权
    （即使原 hint 非空，也要让用户看到差异并决定）。
    """
    existing = [_f(1, "阅读状态", "text", prompt_hint="已有 hint")]
    suggestions = [
        _ann("阅读状态", "same_type",
             ftype="text", prompt_hint="新 hint",
             existing_field_id=1, existing_field_type="text",
             existing_prompt_hint="已有 hint",
             decision=DECISION_REJECTED),
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    d = drafts[0]
    t.assert_eq("rejected → hint 保持现有", d.prompt_hint, "已有 hint")


def test_merge_same_type_hint_replaces_when_pending(t: T) -> None:
    """task #21 新增：same_type 现有 hint 非空 + decision=pending → 仍覆盖。

    pending 视作"接受 LLM 新 hint"（卡片决策 1：未决=已批准）。
    """
    existing = [_f(1, "阅读状态", "text", prompt_hint="已有 hint")]
    suggestions = [
        _ann("阅读状态", "same_type",
             ftype="text", prompt_hint="新 hint",
             existing_field_id=1, existing_field_type="text",
             existing_prompt_hint="已有 hint"),
        # decision 默认 pending
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    d = drafts[0]
    t.assert_eq("pending → hint 被覆盖", d.prompt_hint, "新 hint")


def test_merge_system_required_hint_update(t: T) -> None:
    """system_required（标题/描述/标签）→ hint 总是被 LLM 覆盖。"""
    existing = [_f(1, "标题", "text", key="title", prompt_hint="旧标题 hint")]
    suggestions = [
        _ann("标题", "system_required",
             ftype="text", prompt_hint="LLM 给的新标题 hint",
             existing_field_id=1,
             existing_prompt_hint="旧标题 hint"),
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    d = drafts[0]
    t.assert_eq("system_required hint 被覆盖", d.prompt_hint,
                "LLM 给的新标题 hint")


def test_merge_order_existing_first_then_new(t: T) -> None:
    """合并顺序：先 existing_fields 按 ord，再 LLM 新建按 suggestions 顺序。"""
    existing = [
        _f(1, "标题", "text", key="title", ord_=0),
        _f(2, "作者", "text", key="author", ord_=1),
    ]
    suggestions = [
        _ann("作者", "existing_user_field",
             existing_field_id=2, llm_touched=False),
        _ann("标题", "system_required",
             ftype="text", prompt_hint="标题 hint",
             existing_field_id=1),
        _ann("出版年", "new", ftype="date"),
        _ann("ISBN", "new", ftype="text"),
    ]
    drafts = merge_decisions_into_drafts(suggestions, existing)
    names = [d.name for d in drafts]
    t.assert_eq("顺序：标题/作者/出版年/ISBN",
                names, ["标题", "作者", "出版年", "ISBN"])


# =============================================================================
# diff_drafts_to_plan 测试
# =============================================================================
def test_diff_no_change(t: T) -> None:
    """drafts 与 existing 完全一致 → empty plan。"""
    existing = [_f(1, "ISBN", "text", prompt_hint="13 位")]
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_EXISTING,
            existing_field_id=1, original_name="ISBN",
            name="ISBN", type="text", prompt_hint="13 位",
        ),
    ]
    plan = diff_drafts_to_plan(drafts, existing)
    t.assert_true("empty plan", plan.is_empty)


def test_diff_user_new_create(t: T) -> None:
    """user_new draft → 进 creates。"""
    existing = []
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_USER_NEW,
            existing_field_id=None, original_name=None,
            name="自加字段", type="text", prompt_hint="",
        ),
    ]
    plan = diff_drafts_to_plan(drafts, existing)
    t.assert_eq("creates 含 1 条", len(plan.creates), 1)
    t.assert_eq("create tuple", plan.creates[0], ("自加字段", "text", ""))
    t.assert_eq("其它都空", len(plan.deletes) + len(plan.renames)
                + len(plan.type_changes) + len(plan.updates_hint), 0)


def test_diff_llm_new_create(t: T) -> None:
    """llm_new draft → 进 creates。"""
    existing = []
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_LLM_NEW,
            existing_field_id=None, original_name=None,
            name="LLM 字段", type="date", prompt_hint="格式 YYYY-MM-DD",
        ),
    ]
    plan = diff_drafts_to_plan(drafts, existing)
    t.assert_eq("creates 含 1 条", len(plan.creates), 1)
    t.assert_eq("type=date", plan.creates[0][1], "date")


def test_diff_existing_deleted(t: T) -> None:
    """existing draft 被标删 → 进 deletes。"""
    existing = [_f(1, "废字段", "text")]
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_EXISTING,
            existing_field_id=1, original_name="废字段",
            name="废字段", type="text", prompt_hint="",
            deleted=True,
        ),
    ]
    plan = diff_drafts_to_plan(drafts, existing)
    t.assert_eq("deletes 含 fid=1", plan.deletes, [1])


def test_diff_user_new_deleted_dropped(t: T) -> None:
    """user_new + deleted → 既不 create 也不 delete，直接丢弃。"""
    existing = []
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_USER_NEW,
            existing_field_id=None, original_name=None,
            name="临时字段", type="text", prompt_hint="",
            deleted=True,
        ),
    ]
    plan = diff_drafts_to_plan(drafts, existing)
    t.assert_true("plan empty（标删的新建被丢弃）", plan.is_empty)


def test_diff_llm_renamed_normal(t: T) -> None:
    """llm_renamed draft 且 name 与 LLM 改名一致 → 进 renames。"""
    existing = [_f(1, "出版社", "text", prompt_hint="hint")]
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_LLM_RENAMED,
            existing_field_id=1, original_name="出版社",
            name="出版商", type="text", prompt_hint="hint",
        ),
    ]
    plan = diff_drafts_to_plan(drafts, existing)
    t.assert_eq("renames 含 (1, '出版商')",
                plan.renames, [(1, "出版商")])
    t.assert_eq("无其它操作", len(plan.creates) + len(plan.deletes)
                + len(plan.type_changes) + len(plan.updates_hint), 0)


def test_diff_llm_typechanged_normal(t: T) -> None:
    """llm_typechanged draft 且 type 与 LLM 改类型后一致 → 进 type_changes。"""
    existing = [_f(1, "评分", "text", prompt_hint="旧 hint")]
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_LLM_TYPECHANGED,
            existing_field_id=1, original_name="评分",
            name="评分", type="rating", prompt_hint="1-5 整数",
            original_type="text",
        ),
    ]
    plan = diff_drafts_to_plan(drafts, existing)
    t.assert_eq("type_changes 含 (1, 'rating', '1-5 整数')",
                plan.type_changes, [(1, "rating", "1-5 整数")])
    t.assert_eq("无 updates_hint（已包含在 type_changes）",
                len(plan.updates_hint), 0)


def test_diff_llm_typechanged_user_reverted(t: T) -> None:
    """Step 1 批准 type_conflict，但 Step 2 用户又把 type 改回原值 →
    diff 不出现 type_changes。"""
    existing = [_f(1, "评分", "text", prompt_hint="旧 hint")]
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_LLM_TYPECHANGED,
            existing_field_id=1, original_name="评分",
            name="评分", type="text",  # 改回了原 type
            prompt_hint="旧 hint",
            original_type="text",
        ),
    ]
    plan = diff_drafts_to_plan(drafts, existing)
    t.assert_true("plan empty（用户改回原 type）", plan.is_empty)


def test_diff_existing_user_changed_type(t: T) -> None:
    """existing draft（非 LLM 触发）但用户在 Step 2 改了 type → 进 type_changes。

    这是 Step 2 编辑权完全交给用户的体现：origin 不驱动可编辑性，name/type/
    hint 都按当前 draft 与 existing 的 diff 计算。
    """
    existing = [_f(1, "字段", "text", prompt_hint="hint")]
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_EXISTING,
            existing_field_id=1, original_name="字段",
            name="字段", type="number", prompt_hint="hint",
        ),
    ]
    plan = diff_drafts_to_plan(drafts, existing)
    t.assert_eq("type_changes 含 (1, 'number', 'hint')",
                plan.type_changes, [(1, "number", "hint")])


def test_diff_hint_only_change(t: T) -> None:
    """仅 hint 变了 → 进 updates_hint。"""
    existing = [_f(1, "字段", "text", prompt_hint="旧 hint")]
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_EXISTING,
            existing_field_id=1, original_name="字段",
            name="字段", type="text", prompt_hint="新 hint",
        ),
    ]
    plan = diff_drafts_to_plan(drafts, existing)
    t.assert_eq("updates_hint 含 (1, '新 hint')",
                plan.updates_hint, [(1, "新 hint")])


def test_diff_rename_and_hint(t: T) -> None:
    """同时改名 + 改 hint → renames + updates_hint 都有。"""
    existing = [_f(1, "字段", "text", prompt_hint="旧")]
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_EXISTING,
            existing_field_id=1, original_name="字段",
            name="新名", type="text", prompt_hint="新 hint",
        ),
    ]
    plan = diff_drafts_to_plan(drafts, existing)
    t.assert_eq("renames", plan.renames, [(1, "新名")])
    t.assert_eq("updates_hint", plan.updates_hint, [(1, "新 hint")])


# =============================================================================
# check_undelete_name_conflict 测试
# =============================================================================
def test_undelete_no_conflict(t: T) -> None:
    """没有同名 not deleted 行 → 可以撤销删除（返回 None）。"""
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_LLM_DELETED,
            existing_field_id=1, original_name="字段A",
            name="字段A", type="text", prompt_hint="", deleted=True,
        ),
        FieldDraft(
            origin=DRAFT_ORIGIN_EXISTING,
            existing_field_id=2, original_name="字段B",
            name="字段B", type="text", prompt_hint="",
        ),
    ]
    conflict = check_undelete_name_conflict(drafts, 0)
    t.assert_true("无冲突 → None", conflict is None)


def test_undelete_conflict_with_user_new(t: T) -> None:
    """撤销删除「字段A」时，user_new 行也叫「字段A」→ 返回 user_new 行。"""
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_LLM_DELETED,
            existing_field_id=1, original_name="字段A",
            name="字段A", type="text", prompt_hint="", deleted=True,
        ),
        FieldDraft(
            origin=DRAFT_ORIGIN_USER_NEW,
            existing_field_id=None, original_name=None,
            name="字段A", type="text", prompt_hint="",
        ),
    ]
    conflict = check_undelete_name_conflict(drafts, 0)
    t.assert_true("有冲突 → 返回 user_new 行", conflict is not None)
    t.assert_eq("冲突行 origin=user_new",
                conflict.origin, DRAFT_ORIGIN_USER_NEW)


def test_undelete_conflict_with_existing_renamed(t: T) -> None:
    """撤销删除「字段A」时，另一现有字段被改名为「字段A」→ 返回该 existing 行。"""
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_LLM_DELETED,
            existing_field_id=1, original_name="字段A",
            name="字段A", type="text", prompt_hint="", deleted=True,
        ),
        FieldDraft(
            origin=DRAFT_ORIGIN_EXISTING,
            existing_field_id=2, original_name="字段B",
            name="字段A", type="text", prompt_hint="",  # 改名为 字段A
        ),
    ]
    conflict = check_undelete_name_conflict(drafts, 0)
    t.assert_true("有冲突", conflict is not None)
    t.assert_eq("冲突行 fid=2", conflict.existing_field_id, 2)


def test_undelete_no_conflict_when_other_also_deleted(t: T) -> None:
    """另一同名行也是 deleted → 不算冲突。"""
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_LLM_DELETED,
            existing_field_id=1, original_name="字段A",
            name="字段A", type="text", prompt_hint="", deleted=True,
        ),
        FieldDraft(
            origin=DRAFT_ORIGIN_USER_NEW,
            existing_field_id=None, original_name=None,
            name="字段A", type="text", prompt_hint="",
            deleted=True,  # 也是划删线
        ),
    ]
    conflict = check_undelete_name_conflict(drafts, 0)
    t.assert_true("两个都 deleted → 无冲突",
                  conflict is None)


def test_undelete_target_not_deleted(t: T) -> None:
    """target 自己不是 deleted → 立即返回 None。"""
    drafts = [
        FieldDraft(
            origin=DRAFT_ORIGIN_EXISTING,
            existing_field_id=1, original_name="字段A",
            name="字段A", type="text", prompt_hint="",
        ),
    ]
    conflict = check_undelete_name_conflict(drafts, 0)
    t.assert_true("target 不是 deleted → None", conflict is None)


# =============================================================================
# summary_dialog_button_label 测试
# =============================================================================
def test_button_label_only_creates(t: T) -> None:
    plan = FieldPlan(
        creates=[("字段", "text", "")],
        updates_hint=[], deletes=[], renames=[], type_changes=[],
    )
    t.assert_eq("仅创建 → [应用]",
                summary_dialog_button_label(plan), "应用")


def test_button_label_only_renames(t: T) -> None:
    plan = FieldPlan(
        creates=[], updates_hint=[], deletes=[],
        renames=[(1, "新名")], type_changes=[],
    )
    t.assert_eq("仅改名 → [应用]",
                summary_dialog_button_label(plan), "应用")


def test_button_label_only_updates_hint(t: T) -> None:
    plan = FieldPlan(
        creates=[], updates_hint=[(1, "hint")],
        deletes=[], renames=[], type_changes=[],
    )
    t.assert_eq("仅更新 hint → [应用]",
                summary_dialog_button_label(plan), "应用")


def test_button_label_with_type_changes(t: T) -> None:
    plan = FieldPlan(
        creates=[], updates_hint=[], deletes=[],
        renames=[], type_changes=[(1, "rating", "")],
    )
    t.assert_eq("含改类型 → 下一步：确认类型变更",
                summary_dialog_button_label(plan), "下一步：确认类型变更")


def test_button_label_with_deletes(t: T) -> None:
    plan = FieldPlan(
        creates=[], updates_hint=[], deletes=[1],
        renames=[], type_changes=[],
    )
    t.assert_eq("含删除 → 下一步：确认删除",
                summary_dialog_button_label(plan), "下一步：确认删除")


def test_button_label_with_both(t: T) -> None:
    plan = FieldPlan(
        creates=[], updates_hint=[], deletes=[1],
        renames=[], type_changes=[(2, "rating", "")],
    )
    t.assert_eq("含改类型 + 删除 → 下一步：确认变更",
                summary_dialog_button_label(plan), "下一步：确认变更")


def test_button_label_full_combination(t: T) -> None:
    """所有 5 类都有 → 仍按 type_changes + deletes 决定主按钮文案。"""
    plan = FieldPlan(
        creates=[("新字段", "text", "")],
        updates_hint=[(1, "hint")],
        deletes=[2],
        renames=[(3, "新名")],
        type_changes=[(4, "rating", "")],
    )
    t.assert_eq("含全部 → 下一步：确认变更",
                summary_dialog_button_label(plan), "下一步：确认变更")


# =============================================================================
# 阶段 B：clone_draft / drafts_are_dirty / step1_visible_indices
# =============================================================================
def test_clone_draft_independent(t: T) -> None:
    """clone_draft 返回的对象与原对象内容相等但互相独立。"""
    d = FieldDraft(
        origin=DRAFT_ORIGIN_LLM_NEW, existing_field_id=None,
        original_name=None, original_type=None,
        name="A", type="text", prompt_hint="hint",
    )
    c = clone_draft(d)
    t.assert_eq("clone 内容相等：name", c.name, "A")
    t.assert_eq("clone 内容相等：origin", c.origin, DRAFT_ORIGIN_LLM_NEW)
    t.assert_true("clone 是不同对象", c is not d)
    # 修改 clone 不影响原对象
    c.name = "B"
    c.deleted = True
    t.assert_eq("修改 clone 不影响原对象 name", d.name, "A")
    t.assert_true("修改 clone 不影响原对象 deleted", d.deleted is False)


def test_drafts_dirty_unchanged(t: T) -> None:
    """drafts_are_dirty：current 与 baseline 内容一致 → False。"""
    d = FieldDraft(
        origin=DRAFT_ORIGIN_EXISTING, existing_field_id=1,
        original_name="A", original_type="text",
        name="A", type="text", prompt_hint="",
    )
    baseline = [clone_draft(d)]
    current = [clone_draft(d)]
    t.assert_true("内容一致 → 非 dirty", not drafts_are_dirty(current, baseline))


def test_drafts_dirty_added_row(t: T) -> None:
    """drafts_are_dirty：添加行 → True。"""
    baseline: list[FieldDraft] = []
    current = [FieldDraft(
        origin=DRAFT_ORIGIN_USER_NEW, existing_field_id=None,
        original_name=None, original_type=None,
        name="新", type="text", prompt_hint="",
    )]
    t.assert_true("添加行 → dirty", drafts_are_dirty(current, baseline))


def test_drafts_dirty_name_changed(t: T) -> None:
    """drafts_are_dirty：改名 → True。"""
    d_base = FieldDraft(
        origin=DRAFT_ORIGIN_EXISTING, existing_field_id=1,
        original_name="A", original_type="text",
        name="A", type="text", prompt_hint="",
    )
    baseline = [d_base]
    d_cur = clone_draft(d_base)
    d_cur.name = "B"
    t.assert_true("改名 → dirty", drafts_are_dirty([d_cur], baseline))


def test_drafts_dirty_deleted_flag(t: T) -> None:
    """drafts_are_dirty：deleted 翻转 → True。"""
    d_base = FieldDraft(
        origin=DRAFT_ORIGIN_EXISTING, existing_field_id=1,
        original_name="A", original_type="text",
        name="A", type="text", prompt_hint="",
    )
    baseline = [d_base]
    d_cur = clone_draft(d_base)
    d_cur.deleted = True
    t.assert_true("deleted 翻转 → dirty", drafts_are_dirty([d_cur], baseline))


def test_drafts_dirty_type_changed(t: T) -> None:
    """drafts_are_dirty：type 改了 → True。"""
    d_base = FieldDraft(
        origin=DRAFT_ORIGIN_LLM_NEW, existing_field_id=None,
        original_name=None, original_type=None,
        name="rating", type="text", prompt_hint="",
    )
    baseline = [d_base]
    d_cur = clone_draft(d_base)
    d_cur.type = "rating"
    t.assert_true("type 改了 → dirty", drafts_are_dirty([d_cur], baseline))


def test_drafts_dirty_hint_changed(t: T) -> None:
    """drafts_are_dirty：prompt_hint 改了 → True。"""
    d_base = FieldDraft(
        origin=DRAFT_ORIGIN_LLM_NEW, existing_field_id=None,
        original_name=None, original_type=None,
        name="X", type="text", prompt_hint="",
    )
    baseline = [d_base]
    d_cur = clone_draft(d_base)
    d_cur.prompt_hint = "新提示"
    t.assert_true("hint 改了 → dirty", drafts_are_dirty([d_cur], baseline))


def test_step1_visible_indices_filters_existing_user_field(t: T) -> None:
    """step1_visible_indices：existing_user_field 行被过滤掉。

    task #22：visible 同时收紧到 ``has_llm_change``，所以这里要给 LLM 触达
    的 ann 显式设 llm_touched=True；same_type 还要让 hint 实际改了才算变更。
    """
    s_new = AnnotatedSuggestion(name="new", type="text", prompt_hint="")
    s_new.status = "new"
    s_new.llm_touched = True
    s_existing = AnnotatedSuggestion(name="existing", type="text", prompt_hint="")
    s_existing.status = "existing_user_field"
    s_same = AnnotatedSuggestion(name="same", type="text", prompt_hint="新提示")
    s_same.status = "same_type"
    s_same.llm_touched = True
    s_same.existing_prompt_hint = "旧提示"   # hint 改了 → has_llm_change=True
    s_del = AnnotatedSuggestion(name="del", type="text", prompt_hint="")
    s_del.status = "llm_suggest_delete"
    s_del.llm_touched = True
    visible = step1_visible_indices([s_new, s_existing, s_same, s_del])
    t.assert_eq("过滤后剩 3 行", len(visible), 3)
    t.assert_eq("过滤后保留新增", visible[0], 0)
    t.assert_eq("过滤后跳过 existing_user_field", visible[1], 2)
    t.assert_eq("过滤后保留 llm_suggest_delete", visible[2], 3)


def test_step1_visible_indices_all_existing_returns_empty(t: T) -> None:
    """step1_visible_indices：全是 existing_user_field → 返回空列表。"""
    s1 = AnnotatedSuggestion(name="A", type="text", prompt_hint="")
    s1.status = "existing_user_field"
    s2 = AnnotatedSuggestion(name="B", type="text", prompt_hint="")
    s2.status = "existing_user_field"
    visible = step1_visible_indices([s1, s2])
    t.assert_eq("全部过滤掉 → 空列表", len(visible), 0)


def test_step1_visible_indices_empty_input(t: T) -> None:
    """step1_visible_indices：空输入 → 空列表。"""
    t.assert_eq("空输入 → 空列表", step1_visible_indices([]), [])


def test_field_plan_is_empty_is_property_not_method(t: T) -> None:
    """task #21 阶段 B 回归保护：``FieldPlan.is_empty`` 必须是 ``@property``。

    曾经一个 bug：``is_empty`` 是普通方法、调用处 ``if plan.is_empty:`` 漏写
    括号 → bound method 永远 truthy → 永远走"无变更"分支。改成 ``@property``
    后无论写 ``plan.is_empty`` 还是 ``plan.is_empty()`` 都不会安静失败。
    """
    plan_empty = FieldPlan(
        creates=[], updates_hint=[], deletes=[], renames=[], type_changes=[],
    )
    plan_with_create = FieldPlan(
        creates=[("X", "text", "")],
        updates_hint=[], deletes=[], renames=[], type_changes=[],
    )
    plan_with_delete = FieldPlan(
        creates=[], updates_hint=[], deletes=[1], renames=[], type_changes=[],
    )
    # property 直接取布尔值：True / False，**不是** bound method
    t.assert_eq("空 plan.is_empty 是 True", plan_empty.is_empty, True)
    t.assert_eq("有 creates → is_empty 是 False",
                plan_with_create.is_empty, False)
    t.assert_eq("有 deletes → is_empty 是 False",
                plan_with_delete.is_empty, False)
    # 类型必须是 bool，不能是 method
    t.assert_true("is_empty 类型是 bool",
                  isinstance(plan_empty.is_empty, bool))


# =============================================================================
# 主入口
# =============================================================================
def main() -> int:
    t = T()
    # merge_decisions_into_drafts
    test_merge_existing_user_field_no_llm(t)
    test_merge_llm_new(t)
    test_merge_llm_new_rejected(t)
    test_merge_type_conflict_approved(t)
    test_merge_type_conflict_rejected(t)
    test_merge_llm_suggest_delete_approved(t)
    test_merge_llm_suggest_delete_rejected(t)
    test_merge_llm_suggest_rename_approved(t)
    test_merge_llm_suggest_rename_approved_with_new_hint(t)
    test_merge_llm_suggest_rename_rejected(t)
    test_merge_same_type_hint_update(t)
    test_merge_same_type_hint_kept(t)
    test_merge_same_type_hint_replaces_when_pending(t)
    test_merge_system_required_hint_update(t)
    test_merge_order_existing_first_then_new(t)
    # diff_drafts_to_plan
    test_diff_no_change(t)
    test_diff_user_new_create(t)
    test_diff_llm_new_create(t)
    test_diff_existing_deleted(t)
    test_diff_user_new_deleted_dropped(t)
    test_diff_llm_renamed_normal(t)
    test_diff_llm_typechanged_normal(t)
    test_diff_llm_typechanged_user_reverted(t)
    test_diff_existing_user_changed_type(t)
    test_diff_hint_only_change(t)
    test_diff_rename_and_hint(t)
    # check_undelete_name_conflict
    test_undelete_no_conflict(t)
    test_undelete_conflict_with_user_new(t)
    test_undelete_conflict_with_existing_renamed(t)
    test_undelete_no_conflict_when_other_also_deleted(t)
    test_undelete_target_not_deleted(t)
    # summary_dialog_button_label
    test_button_label_only_creates(t)
    test_button_label_only_renames(t)
    test_button_label_only_updates_hint(t)
    test_button_label_with_type_changes(t)
    test_button_label_with_deletes(t)
    test_button_label_with_both(t)
    test_button_label_full_combination(t)
    # 阶段 B：clone_draft / drafts_are_dirty / step1_visible_indices
    test_clone_draft_independent(t)
    test_drafts_dirty_unchanged(t)
    test_drafts_dirty_added_row(t)
    test_drafts_dirty_name_changed(t)
    test_drafts_dirty_deleted_flag(t)
    test_drafts_dirty_type_changed(t)
    test_drafts_dirty_hint_changed(t)
    test_step1_visible_indices_filters_existing_user_field(t)
    test_step1_visible_indices_all_existing_returns_empty(t)
    test_step1_visible_indices_empty_input(t)
    test_field_plan_is_empty_is_property_not_method(t)
    return 0 if t.report() else 1


if __name__ == "__main__":
    sys.exit(main())
