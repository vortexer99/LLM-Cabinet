"""库字段设计助手（task #11 T3 第一个 WizardPlugin）。

让用户用自然语言描述这个库的目的与字段偏好（"和 LLM 一起设计"），
LLM 据此给出**完整的字段方案修订**（在已有字段基础上增 / 改 / 删）；
用户可对每条建议「批准」或「驳回」；最后事务化批量写入 ``fields`` 表。

历史背景：
- 内部代码沿用 wizard / WizardPlugin / library_init 等命名（最初任务卡叫"向导"，
  最初的对外名字叫"库初始化助手"），对外文案当前统一为"库字段设计助手"——它们是同一物。
- 2026-06-01 多轮迭代后定名为"库字段设计助手"：
  * 场景描述与库描述合并为单个文本框（统一语义："设计这个库"）
  * 场景页内嵌"已有字段编辑面板"（行为与设置 → 字段一致），让用户在调用 LLM 前先调整
  * 标签分类策略不再单独显示，由 LLM 直接写入「标签」字段的 prompt_hint
  * 结果页新增「LLM 建议」列（新增/修改/不变/删除）+ 批准/驳回按钮

设计决策（任务卡 task #11 T3）：

* 决策 2  ：预览页提供「重新开始」/「在当前基础上调整」两个按钮；wizard_max_rounds 默认 5。
* 决策 3  ：直调 provider 不走 LLMTaskQueue（前台交互场景）。
* 决策 4  ：按 ``LLMProvider.supports_json_mode`` 静态路由（甲 / 丙），
            两路输出都过同一个 ``parse_and_validate``。
* 决策 5  ：应用前预检冲突（4 种状态），应用阶段事务化（``Repository.add_fields_batch``）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...llm.config import load_config
from ...llm.providers import get_provider
from ...models import FIELD_TYPES, FIELD_TYPE_LABELS, PROTECTED_FIELD_KEYS
from ..dialogs import confirm, error, info, warn
from .base import WizardMeta, WizardPlugin

# =============================================================================
# settings 键
# =============================================================================
SETTING_MAX_ROUNDS = "wizard_max_rounds"
DEFAULT_MAX_ROUNDS = 5


def get_max_rounds(repo) -> int:
    raw = repo.get_setting(SETTING_MAX_ROUNDS, "") or ""
    try:
        v = int(raw)
        if 1 <= v <= 20:
            return v
    except ValueError:
        pass
    return DEFAULT_MAX_ROUNDS


def set_max_rounds(repo, n: int) -> None:
    n = max(1, min(20, int(n)))
    repo.set_setting(SETTING_MAX_ROUNDS, str(n))


# =============================================================================
# 解析与冲突预检（纯函数，无 Qt 依赖，便于自检）
# =============================================================================
class WizardLLMOutputError(ValueError):
    """LLM 输出无法解析或不符合 schema。"""


@dataclass
class AnnotatedSuggestion:
    """LLM 给的字段建议在与当前库 fields 比对后的状态。"""

    name: str
    type: str
    prompt_hint: str
    # 状态分类（task #11 T3 全量规划）：
    #   'new'                ：LLM 给出的全新字段，正常 INSERT
    #   'system_required'    ：系统必有字段（标题/描述/标签）— 强制选中、只能更新 prompt_hint；
    #                          v4 起也兼任"受保护字段类型冲突跳过"的兜底
    #   'existing_user_field'：现有字段（用户字段或系统非必有），LLM 未在本次提及；
    #                          默认 selected=True 表示保留；用户取消勾选 → 删除
    #   'same_type'          ：LLM 输出命中现有用户字段且类型一致；现有 hint 空
    #                          → update_hint_only；非空 → 跳过不覆盖；
    #                          用户在预览页"删除" → selected=False → 走 delete
    #   'type_conflict'      ：LLM 输出命中现有但类型不同；批准 → 原地改类型
    #                          + 写入 LLM 新 hint + supersede pending（走
    #                          change_type 路径，task #19 Phase B）；驳回 →
    #                          字段彻底不动
    #   ('system_protected'  ：v3 时代用于"is_system 但类型与建议不符"的特殊跳过；
    #                          task #20 schema v4 起废弃。author/date/source_url/
    #                          rating 等"带 key 但非 protected"字段现在跟用户
    #                          字段同构，改类型走 type_conflict 路径。)
    #   'llm_suggest_delete' ：LLM 在 fields_to_delete 里显式建议删除该现有字段；
    #                          默认 selected=False（保守"待批准"），用户批准 → 真删，
    #                          驳回 → 退化为普通 existing_user_field 路径（保留）
    #   'llm_suggest_rename' ：LLM 在 fields_to_rename 里建议改名该现有字段；
    #                          默认 selected=False（保守"待批准"），用户批准 →
    #                          应用阶段走 rename 路径（保留 fid，UPDATE name）；
    #                          驳回 → 退化为普通 existing_user_field 路径（保留原名）
    status: str = "new"
    reason: str = ""
    existing_field_id: Optional[int] = None
    existing_field_type: str = ""
    existing_prompt_hint: str = ""
    rename_to: str = ""
    # LLM 建议改名的新字段名（仅 status='llm_suggest_rename' 时使用）；
    # 与 type_conflict 路径的 rename_to 含义不同：那个是"用户改名后创建新字段"，
    # 这个是"保留原 fid，UPDATE fields.name"
    llm_rename_new_name: str = ""
    # LLM 给出的原始"建议理由"（仅 llm_suggest_delete / llm_suggest_rename
    # 时填入）。与 `reason` 字段的区别：`reason` 在用户驳回时会被覆盖成
    # "已被你驳回..." 这种用户引导文案，且也用于 refine feedback 回灌；
    # `llm_reason` 永不覆盖，专供 Step 1 tooltip 持续展示 LLM 的原始意图，
    # 让用户即使驳回后也能回看"LLM 当时为什么这么建议"。
    llm_reason: str = ""
    # LLM 在 annotate 时给的原始 type / prompt_hint 快照（仅 llm_touched=True
    # 的分支填入，由 annotate_conflicts 设置，永不被覆盖）。
    # `_on_decision_changed` 驳回会把 ann.type / ann.prompt_hint 还原回
    # existing 值，导致 step1_changed_dimensions 算不出"LLM 原本想改哪些
    # 维度"。这两个字段保留 LLM 的原始建议值，让 Step 1 「LLM 建议」列
    # 在驳回后仍能展示完整维度（用户期望：驳回不改变 LLM 建议本身，只
    # 是用户选择不应用它）。判据：和 existing_field_type /
    # existing_prompt_hint 比较，不同即"LLM 改了这一维度"。
    llm_orig_type: str = ""
    llm_orig_prompt_hint: str = ""
    selected: bool = True

    # ---- 6/1 二次迭代新增（"LLM 建议"列）----------------------------------
    # llm_touched=True 表示本条建议是 LLM 在本轮明确给出的（含同名命中与全新字段）；
    # llm_touched=False 表示该字段在本轮 LLM 输出中没出现（existing_user_field）。
    llm_touched: bool = False
    # decision：用户对 LLM 建议的处置 — 'pending' / 'approved' / 'rejected'
    # 仅当 llm_touched=True 且建议会带来变化时才有意义；其它情况下保持 'pending'
    # （UI 层据此决定是否展示批准/驳回按钮）。
    decision: str = "pending"

    @property
    def action(self) -> str:
        """导出给应用阶段的"动作"标签。

        - ``"create"``           → 新建字段
        - ``"update_hint_only"`` → 仅写入 prompt_hint 到现有字段
        - ``"delete"``           → 删除现有字段（existing_user_field 被取消勾选时）
        - ``"rename"``           → 用户批准 LLM 改名建议（保留 fid，UPDATE name）
        - ``"keep"``             → 现有字段保留不动
        - ``"skip"``             → 不操作

        历史：6/1 晚一度引入 ``decision='rejected'`` 路径让 ``action`` 退化为 ``skip``；
        后来改为"批准/驳回立即生效（直接改 ann 内容）"，``action`` 不再依赖 decision，
        只看 ``status`` + ``selected`` + 当前 hint/type。
        """
        if self.status == "existing_user_field":
            # 唯一会触发删除的路径（用户主动取消保留）
            return "keep" if self.selected else "delete"
        if self.status == "llm_suggest_delete":
            # LLM 显式建议删除：selected=True 表示用户已批准 → 删；
            # 默认 selected=False 表示 pending/驳回，等价"保留现有字段"
            return "delete" if self.selected else "keep"
        if self.status == "llm_suggest_rename":
            # LLM 显式建议改名：selected=True 表示用户已批准 →
            # 走 rename 路径（保留 fid，UPDATE name）；
            # 默认 selected=False 表示 pending/驳回，等价"保留原名"
            return "rename" if self.selected else "keep"
        if self.status == "same_type":
            # selected=False（用户在预览页点了行操作"删除"）→ 与 existing_user_field
            # 的"取消保留"一致语义：删该现有字段
            if not self.selected:
                return "delete"
            # selected=True 走原来的路径：现有 hint 非空 → 跳过；空 → 仅写 LLM hint
            if (self.existing_prompt_hint or "").strip():
                return "skip"
            return (
                "update_hint_only"
                if (self.prompt_hint or "").strip()
                else "skip"
            )
        if not self.selected:
            return "skip"
        if self.status == "system_required":
            # 仅当 hint 与现有不同才需要写库
            return "update_hint_only" if self._hint_changed() else "skip"
        if self.status == "type_conflict":
            # task #19 Phase B：批准（selected=True）→ 原地改类型 +
            # 用 LLM 给的新 hint 覆盖旧 hint + supersede pending；
            # 驳回（selected=False）→ skip，字段彻底不动
            return "change_type" if self.selected else "skip"
        return "create"  # status == "new"

    @property
    def effective_name(self) -> str:
        """实际写入 fields 表时使用的名字。

        task #19 Phase B 起 type_conflict 路径不再走"改名建新字段"（删掉了
        ``<原名>_v2`` 逻辑），所以这个 property 仅对常规 ``new`` / ``same_type``
        等路径有意义。``rename_to`` 字段虽然保留在 dataclass 定义里，但
        type_conflict 路径下不再被读写。
        """
        return self.name

    # ---- "LLM 建议"列的标签判定 -------------------------------------------
    def _hint_changed(self) -> bool:
        return (self.prompt_hint or "") != (self.existing_prompt_hint or "")

    @property
    def llm_change_label(self) -> str:
        """对外标签。

        现在的语义（6/1 晚迭代后）：批准/驳回**立即生效改 ann 内容**，所以这里
        只反映"用户决策了什么"，而不是"LLM 提的是新增/修改/不变"：
        - ``decision='approved'`` → "已批准"
        - ``decision='rejected'`` → "已驳回"
        - 用户取消保留现有字段（existing_user_field, selected=False）→ "已删除"
        - 其它情况（pending / 未触达 / 用户手加） → "" （不显示标签，但状态列仍可看出）
        """
        if self.status == "existing_user_field" and not self.selected:
            return "已删除"
        if self.decision == "approved":
            return "已批准"
        if self.decision == "rejected":
            return "已驳回"
        return ""

    @property
    def has_llm_change(self) -> bool:
        """是否需要展示批准/驳回按钮。

        条件：LLM 在本轮触达且其建议会带来变化（新增 / 修改 type / 修改 hint）；
        系统必有字段在 hint 没变化时不显示按钮（避免无意义的 noop 决策）。
        """
        if not self.llm_touched:
            return False
        if self.status == "new":
            return True
        if self.status == "type_conflict":
            return True
        if self.status == "llm_suggest_delete":
            return True
        if self.status == "llm_suggest_rename":
            return True
        if self.status == "system_required":
            return self._hint_changed()
        if self.status == "same_type":
            # task #21：只要 LLM 给的 hint 与现有 hint 不同就要让用户决策——
            # 即便现有 hint 非空（覆盖前提示）。批准 → 用 LLM 新 hint；驳回
            # → 保留原 hint。
            return self._hint_changed()
        return False


# 系统必有字段（is_required = True 的中文名）
# 注意：这 3 个名字与 db.DEFAULT_FIELDS 中保护字段一一对应。
# task #20 schema v4 起："作者/日期/评分/来源" 虽然仍是 is_system（key 非空），
# 但其值与用户字段同构存在 project_field_values，可被改类型 / 删除 / 改名。
_SYSTEM_REQUIRED_NAMES = {"标题", "描述", "标签"}


def parse_and_validate(text: str) -> tuple[dict, list[str]]:
    """把 LLM 返回文本解析成 ``{"fields": [...], "library_description"?: str}``。

    Returns:
        ``(payload, warnings)``。
        - payload['fields']: list of {"name", "type", "prompt_hint"}；
          未知 type 已 fallback 为 ``text`` 并加 warning。
        - payload['library_description']: 可选 str。

    历史：早期版本会单独抽取 ``tag_axes`` / ``default_tags_suggestion`` 在预览页用
    一张二级表展示"标签分类策略"。6/1 晚迭代后调整为：让 LLM 直接把分类策略写进
    「标签」字段的 ``prompt_hint``，不再单独显示。本函数仍然容忍 LLM 误带这些字段
    （静默丢弃 + warning），不抛错。

    Raises:
        WizardLLMOutputError: 输出彻底无法解析（非 JSON / 顶层不是对象 / 无 fields）。
    """
    if not text or not text.strip():
        raise WizardLLMOutputError("模型返回为空")

    s = text.strip()
    # 兼容丙路径：剥 ```json ... ``` 包裹
    if s.startswith("```"):
        s = re.sub(r"^```(?:json|JSON)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    if not s.startswith("{"):
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            s = m.group(0)

    try:
        data = json.loads(s)
    except (json.JSONDecodeError, ValueError) as e:
        raise WizardLLMOutputError(f"模型输出不是合法 JSON：{e}") from e

    if not isinstance(data, dict):
        raise WizardLLMOutputError("模型输出顶层不是 JSON 对象")

    fields = data.get("fields")
    if not isinstance(fields, list):
        raise WizardLLMOutputError("模型输出缺少 fields 数组")

    warnings: list[str] = []
    cleaned: list[dict] = []
    for i, item in enumerate(fields):
        if not isinstance(item, dict):
            warnings.append(f"第 {i + 1} 条不是对象，已忽略")
            continue
        name = (item.get("name") or "").strip()
        if not name:
            warnings.append(f"第 {i + 1} 条缺少 name，已忽略")
            continue
        ftype = (item.get("type") or "text").strip()
        # 用户字段类型必须在 FIELD_TYPES 白名单；"tags" 仅允许用于系统标签字段
        if ftype == "tags" and name != "标签":
            warnings.append(
                f"字段「{name}」类型 'tags' 仅保留给系统标签字段，已 fallback 为 text"
            )
            ftype = "text"
        elif ftype not in FIELD_TYPES and ftype != "tags":
            warnings.append(f"字段「{name}」类型 {ftype!r} 不合法，已 fallback 为 text")
            ftype = "text"
        hint = (item.get("prompt_hint") or "").strip()
        cleaned.append({"name": name, "type": ftype, "prompt_hint": hint})

    if not cleaned:
        raise WizardLLMOutputError("未解析到任何有效字段建议")

    payload: dict = {"fields": cleaned}

    # LLM 误带 tag_axes / default_tags_suggestion 的兼容处理（6/1 晚迭代后已不再使用，
    # 静默丢弃；仅在它非空时给出 warning 提醒"已合并到标签字段 hint"）
    legacy_tag_keys = ("tag_axes", "default_tags_suggestion")
    if any(data.get(k) for k in legacy_tag_keys):
        warnings.append(
            "模型返回了独立的 tag_axes/default_tags_suggestion；"
            "本助手已不再单独使用，请将分类策略写入「标签」字段的 prompt_hint"
        )

    # 库级描述（task #11 T3 额外）
    lib_desc = data.get("library_description")
    if isinstance(lib_desc, str) and lib_desc.strip():
        payload["library_description"] = lib_desc.strip()

    # LLM 显式删除建议（task #16 T3）：
    # fields_to_delete: list[{"name": str, "reason": str}]
    # 系统必有字段（标题/描述/标签）即使被 LLM 误放进来也忽略
    deletes_raw = data.get("fields_to_delete")
    if isinstance(deletes_raw, list):
        cleaned_deletes: list[dict] = []
        for i, item in enumerate(deletes_raw):
            if not isinstance(item, dict):
                warnings.append(f"fields_to_delete 第 {i + 1} 条不是对象，已忽略")
                continue
            d_name = (item.get("name") or "").strip()
            if not d_name:
                warnings.append(f"fields_to_delete 第 {i + 1} 条缺少 name，已忽略")
                continue
            if d_name in _SYSTEM_REQUIRED_NAMES:
                warnings.append(
                    f"fields_to_delete 含必有字段「{d_name}」，已忽略"
                )
                continue
            d_reason = (item.get("reason") or "").strip()
            if not d_reason:
                # 没给理由也保留，只是给个默认 reason 兜底；不阻塞
                d_reason = "（LLM 未提供删除理由）"
                warnings.append(
                    f"fields_to_delete「{d_name}」缺 reason，使用默认占位"
                )
            cleaned_deletes.append({"name": d_name, "reason": d_reason})
        if cleaned_deletes:
            payload["fields_to_delete"] = cleaned_deletes

    # LLM 显式改名建议（task #16 T4）：
    # fields_to_rename: list[{"old_name": str, "new_name": str, "reason": str}]
    # 与 fields_to_delete 设计对称；目的是让 LLM 能"保留 field id（项目历史数据）"
    # 地修改字段名，而不是 delete + create 等价模拟（会丢历史值）。
    # 必有字段（标题/描述/标签）的 old_name 或 new_name 命中均忽略并 warning。
    renames_raw = data.get("fields_to_rename")
    if isinstance(renames_raw, list):
        cleaned_renames: list[dict] = []
        seen_old: set[str] = set()
        seen_new: set[str] = set()
        for i, item in enumerate(renames_raw):
            if not isinstance(item, dict):
                warnings.append(f"fields_to_rename 第 {i + 1} 条不是对象，已忽略")
                continue
            old_name = (item.get("old_name") or "").strip()
            new_name = (item.get("new_name") or "").strip()
            if not old_name or not new_name:
                warnings.append(
                    f"fields_to_rename 第 {i + 1} 条缺 old_name/new_name，已忽略"
                )
                continue
            if old_name == new_name:
                warnings.append(
                    f"fields_to_rename「{old_name}」old/new 相同，已忽略"
                )
                continue
            if old_name in _SYSTEM_REQUIRED_NAMES:
                warnings.append(
                    f"fields_to_rename 含必有字段 old_name「{old_name}」，已忽略"
                )
                continue
            if new_name in _SYSTEM_REQUIRED_NAMES:
                warnings.append(
                    f"fields_to_rename new_name「{new_name}」与必有字段重名，已忽略"
                )
                continue
            if old_name in seen_old:
                warnings.append(
                    f"fields_to_rename 同一 old_name「{old_name}」出现多次，仅保留首条"
                )
                continue
            if new_name in seen_new:
                warnings.append(
                    f"fields_to_rename 同一 new_name「{new_name}」出现多次，仅保留首条"
                )
                continue
            r_reason = (item.get("reason") or "").strip()
            if not r_reason:
                r_reason = "（LLM 未提供改名理由）"
                warnings.append(
                    f"fields_to_rename「{old_name} → {new_name}」缺 reason，使用默认占位"
                )
            seen_old.add(old_name)
            seen_new.add(new_name)
            cleaned_renames.append({
                "old_name": old_name, "new_name": new_name, "reason": r_reason,
            })
        if cleaned_renames:
            payload["fields_to_rename"] = cleaned_renames
    return payload, warnings


# =============================================================================
# task #21 两段式向导：数据模型 + 纯函数底座
# =============================================================================
# Step 1 = 审阅 LLM 建议（每行 = 一条 ann）
# Step 2 = 编辑最终字段表（每行 = 一个 FieldDraft）
#
# 数据流：
#   _suggestions: list[AnnotatedSuggestion]  ← LLM annotate 阶段产出
#         │
#         │  Step 1 用户决策（approved / rejected / pending → 视作 approved）
#         ▼
#   merge_decisions_into_drafts(suggestions, existing_fields)
#         │
#         ▼
#   _drafts: list[FieldDraft]  ← Step 2 用户编辑（增/删/改名/改类型/改 hint）
#         │
#         │  diff_drafts_to_plan(drafts, existing_fields)
#         ▼
#   FieldPlan(type_changes, renames, creates, updates_hint, deletes)
#         │
#         ▼
#   apply_field_plan_batch(...)（一次事务）

# Step 1 决策枚举（AnnotatedSuggestion.decision 取值）
DECISION_PENDING = "pending"
DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"

# FieldDraft 来源徽章
DRAFT_ORIGIN_EXISTING = "existing"          # 原本就存在的字段（含老系统字段）
DRAFT_ORIGIN_LLM_NEW = "llm_new"            # Step 1 批准的 'new' ann
DRAFT_ORIGIN_LLM_RENAMED = "llm_renamed"    # Step 1 批准的 'llm_suggest_rename'
DRAFT_ORIGIN_LLM_TYPECHANGED = "llm_typechanged"  # Step 1 批准的 'type_conflict'
DRAFT_ORIGIN_LLM_DELETED = "llm_deleted"    # Step 1 批准的 'llm_suggest_delete'（划删线）
DRAFT_ORIGIN_USER_NEW = "user_new"          # Step 2 里点 [+ 添加字段] 加的


@dataclass
class FieldDraft:
    """Step 2 字段表编辑态的一行。

    与 ``AnnotatedSuggestion`` 解耦——Step 2 不再依赖 ann.status 的复杂分类，
    只关心"应用后字段表该长什么样"。``origin`` 仅用于徽章 + 撤销路径区分，
    不驱动行的可编辑性。
    """

    origin: str
    # 关联到底层 fields.id；llm_new / user_new 为 None
    existing_field_id: Optional[int]
    # 合并时的"原始名"。用于：
    # - origin == 'llm_renamed' 撤销 LLM 后的徽章降级
    # - 重名校验时识别"自身行"
    # - origin == 'existing' 时 == name；origin == 'llm_renamed' 时 == LLM 改名前的旧名
    original_name: Optional[str]
    # 字段三元组（Step 2 内可编辑）
    name: str
    type: str
    prompt_hint: str
    # 编辑标记：True 时该行划删线展示，apply 时进入 deletes
    deleted: bool = False
    # 来自 Step 1 决策的"原始 type"快照（仅 origin == 'llm_typechanged' 时记录
    # LLM 改类型前的旧 type，用于 diff 出 type_changes）
    original_type: Optional[str] = None


@dataclass
class FieldPlan:
    """Step 2 → ``apply_field_plan_batch`` 的一次事务参数包。

    与 ``Repository.apply_field_plan_batch`` 的入参对齐：
    - ``creates``: ``[(name, type, prompt_hint), ...]``
    - ``updates_hint``: ``[(field_id, prompt_hint), ...]``
    - ``deletes``: ``[field_id, ...]``
    - ``renames``: ``[(field_id, new_name), ...]``
    - ``type_changes``: ``[(field_id, new_type, new_prompt_hint), ...]``
    """

    creates: list[tuple[str, str, str]]
    updates_hint: list[tuple[int, str]]
    deletes: list[int]
    renames: list[tuple[int, str]]
    type_changes: list[tuple[int, str, str]]

    @property
    def is_empty(self) -> bool:
        """是否完全无操作（用户没做任何变更）。

        ⚠ 是 ``@property`` —— 直接 ``plan.is_empty`` 取布尔值，**不要**写
        ``plan.is_empty()``（task #21 阶段 B 修过一个 bug：调用处忘加括号、
        bound method 永远 truthy 导致永远走"无变更"分支）。
        """
        return not (
            self.creates
            or self.updates_hint
            or self.deletes
            or self.renames
            or self.type_changes
        )


def merge_decisions_into_drafts(
    suggestions: list[AnnotatedSuggestion],
    existing_fields: list,
) -> list[FieldDraft]:
    """把 Step 1 的决策状态合并成 Step 2 的字段表草稿。

    关键规则：
    - 未决（``decision == 'pending'``）一律按"已批准"处理（含
      ``llm_suggest_delete``，不为单一状态做特例）。
    - 受保护字段（is_required: title/description/tags）始终以 origin=existing
      合并，不论 LLM 怎么提；保护字段类型固定，hint 走 system_required 的
      "更新 hint"路径，但合并时不区分——Step 2 字段表里它们就是普通行
      （只是 UI 渲染时禁用编辑）。

    返回的 list 顺序：先 existing_fields 的 ord 顺序（含被改名/改类型/删除的
    行，原位呈现），再 LLM 新建（按 suggestions 顺序）。
    """
    # 索引：字段名 → ann（便于查找 LLM 在该字段上的决策）
    # ann 的 status 可能是 system_required / same_type / type_conflict /
    # llm_suggest_rename / llm_suggest_delete / existing_user_field / new
    ann_by_name = {a.name: a for a in suggestions}
    # llm_renamed：旧名 → ann
    rename_ann_by_old_name = {
        a.name: a for a in suggestions
        if a.status == "llm_suggest_rename"
    }

    drafts: list[FieldDraft] = []
    seen_existing_ids: set[int] = set()

    # 第一遍：按现有字段 ord 顺序处理
    for f in existing_fields:
        if f.id is None:
            continue
        seen_existing_ids.add(f.id)
        ann = ann_by_name.get(f.name)

        if ann is None:
            # LLM 没碰这个字段（虽然 annotate 一定会产出 existing_user_field
            # 的 ann，但保留这条防御性分支）
            drafts.append(FieldDraft(
                origin=DRAFT_ORIGIN_EXISTING,
                existing_field_id=f.id,
                original_name=f.name,
                original_type=f.type,
                name=f.name, type=f.type, prompt_hint=f.prompt_hint or "",
            ))
            continue

        # 用户驳回的 LLM 建议 → 退化为 existing
        if ann.decision == DECISION_REJECTED:
            drafts.append(FieldDraft(
                origin=DRAFT_ORIGIN_EXISTING,
                existing_field_id=f.id,
                original_name=f.name,
                original_type=f.type,
                name=f.name, type=f.type, prompt_hint=f.prompt_hint or "",
            ))
            continue

        # 默认未决 = 已批准（含 llm_suggest_delete）
        if ann.status == "llm_suggest_delete":
            drafts.append(FieldDraft(
                origin=DRAFT_ORIGIN_LLM_DELETED,
                existing_field_id=f.id,
                original_name=f.name,
                original_type=f.type,
                name=f.name, type=f.type, prompt_hint=f.prompt_hint or "",
                deleted=True,
            ))
            continue

        if ann.status == "llm_suggest_rename":
            new_name = ann.llm_rename_new_name or f.name
            # task #22 round 6：用 ann.prompt_hint（annotate_conflicts 已把
            # LLM 在 fields[new_name] 里给的新 hint 合并到这里），不再回退
            # 到 f.prompt_hint。原代码用 f.prompt_hint 导致"批准改名 + 改
            # hint 后 Step 2 看到的还是库里原 hint" 的 bug。
            # task #19 收尾清理：rename 路径同时合并 type（之前为保留数据
            # 故意不动 type，但 Phase A/B 的 type_changes 安全路径已经具备
            # 改类型能力 → 直接合并，让 diff 自然产出 type_change）。
            # rejected 路径早在上面 DECISION_REJECTED 分支被处理掉了，
            # 不会走到这里。
            drafts.append(FieldDraft(
                origin=DRAFT_ORIGIN_LLM_RENAMED,
                existing_field_id=f.id,
                original_name=f.name,
                original_type=f.type,
                name=new_name, type=ann.type,
                prompt_hint=ann.prompt_hint or "",
            ))
            continue

        if ann.status == "type_conflict":
            drafts.append(FieldDraft(
                origin=DRAFT_ORIGIN_LLM_TYPECHANGED,
                existing_field_id=f.id,
                original_name=f.name,
                name=f.name,
                type=ann.type,                  # LLM 给的新 type
                prompt_hint=ann.prompt_hint or "",  # LLM 给的新 hint
                original_type=f.type,           # 改类型前的旧 type
            ))
            continue

        # same_type / system_required / existing_user_field：保留为 existing
        # 但 hint 可能由 LLM 更新。task #21 决策 1：pending 视作 approved，
        # 所以这里"pending 或 approved"都按"接受 LLM 新 hint"处理；rejected
        # 早在上面 DECISION_REJECTED 分支被处理掉了，不会走到这里。
        new_hint = f.prompt_hint or ""
        if ann.status in ("system_required", "same_type") and ann.selected:
            # pending（未决=接受）或 approved 都用 LLM 新 hint 覆盖
            if ann.decision in (DECISION_PENDING, DECISION_APPROVED):
                new_hint = ann.prompt_hint or ""

        drafts.append(FieldDraft(
            origin=DRAFT_ORIGIN_EXISTING,
            existing_field_id=f.id,
            original_name=f.name,
            original_type=f.type,
            name=f.name, type=f.type, prompt_hint=new_hint,
        ))

    # 第二遍：处理 LLM 新建字段（status='new' 且未驳回）
    for ann in suggestions:
        if ann.status != "new":
            continue
        if ann.decision == DECISION_REJECTED:
            continue
        drafts.append(FieldDraft(
            origin=DRAFT_ORIGIN_LLM_NEW,
            existing_field_id=None,
            original_name=None,
            name=ann.name, type=ann.type,
            prompt_hint=ann.prompt_hint or "",
        ))

    # llm_renamed 的副作用：原本 existing_user_field 的字段不应单独再出现一行
    # —— 上面第一遍已经按"name 命中 rename_ann"处理（origin=llm_renamed），
    # 不会重复产出。如果 LLM 给的旧名在 existing_fields 中找不到，rename ann
    # 会被忽略（annotate_conflicts 阶段的容错）；这里不做额外处理。
    _ = rename_ann_by_old_name

    return drafts


def diff_drafts_to_plan(
    drafts: list[FieldDraft],
    existing_fields: list,
) -> FieldPlan:
    """把 Step 2 的最终字段表草稿 diff 成 ``FieldPlan``（apply 入参）。

    规则：
    - ``deleted=True`` 行 → 进 ``deletes``（仅当对应 fid 真存在；user_new
      / llm_new 行没有 fid，被标删等于"未应用"，直接丢弃）
    - ``origin in {llm_new, user_new}`` 且非 deleted → 进 ``creates``
    - ``origin == existing`` 且 name 改了 → 进 ``renames``
    - ``origin == existing`` 且 type 改了 → 进 ``type_changes``（注意：
      Step 2 改类型可能是用户自己改的，origin 仍是 existing）
    - ``origin == llm_renamed`` 且 name 与原 LLM 改名一致 → 进 ``renames``；
      若用户在 Step 2 又改了名，以 Step 2 名为准
    - ``origin == llm_typechanged`` 且 type 与原 LLM 改类型后一致 → 进
      ``type_changes``；若用户在 Step 2 又改回原 type，diff 不出现
    - hint 改了（与现有字段 prompt_hint 比对）→ 进 ``updates_hint``，但
      不与 type_changes 重叠（type_changes 已含 hint 写入，避免重复）
    """
    creates: list[tuple[str, str, str]] = []
    updates_hint: list[tuple[int, str]] = []
    deletes: list[int] = []
    renames: list[tuple[int, str]] = []
    type_changes: list[tuple[int, str, str]] = []

    existing_by_id = {f.id: f for f in existing_fields if f.id is not None}

    for d in drafts:
        if d.deleted:
            if d.existing_field_id is not None:
                deletes.append(d.existing_field_id)
            # else: user_new / llm_new 标删 → 丢弃，不进 plan
            continue

        if d.existing_field_id is None:
            # llm_new / user_new 新建
            creates.append((d.name, d.type, d.prompt_hint))
            continue

        # 现有字段（含 origin in {existing, llm_renamed, llm_typechanged}）
        ex = existing_by_id.get(d.existing_field_id)
        if ex is None:
            # 异常：fid 在 existing_fields 里找不到（应该不会发生）；保守跳过
            continue

        type_changed = (d.type != ex.type)
        name_changed = (d.name != ex.name)
        hint_changed = ((d.prompt_hint or "") != (ex.prompt_hint or ""))

        if type_changed:
            type_changes.append((ex.id, d.type, d.prompt_hint))
            # type_changes 已写 hint，不再 updates_hint
        elif hint_changed:
            updates_hint.append((ex.id, d.prompt_hint))

        if name_changed:
            renames.append((ex.id, d.name))

    return FieldPlan(
        creates=creates,
        updates_hint=updates_hint,
        deletes=deletes,
        renames=renames,
        type_changes=type_changes,
    )


def check_undelete_name_conflict(
    drafts: list[FieldDraft],
    target_index: int,
) -> Optional[FieldDraft]:
    """检查"撤销删除第 target_index 行"是否会产生重名冲突。

    规则：扫描 drafts 中其它 ``not deleted`` 的行，若存在 ``name == target.name``
    的，返回该冲突行；否则返回 None（可安全撤销删除）。

    不修改 drafts。
    """
    if target_index < 0 or target_index >= len(drafts):
        return None
    target = drafts[target_index]
    if not target.deleted:
        return None  # 不是划删线行，不需要校验
    for i, d in enumerate(drafts):
        if i == target_index:
            continue
        if d.deleted:
            continue
        if d.name == target.name:
            return d
    return None


# 应用前汇总对话框的主按钮文案矩阵（不依赖 Qt，便于测试）
def summary_dialog_button_label(plan: FieldPlan) -> str:
    """根据 ``FieldPlan`` 内容动态返回主按钮文案。

    诚实告知用户接下来还有几道二次确认对话框：
    - 含 type_changes（改类型，task #19 风险）→ 还要弹批量类型变更确认
    - 含 deletes（删除，task #16 风险）→ 还要弹批量删除确认
    - 仅 creates / renames / updates_hint → 直接落库
    """
    has_type = bool(plan.type_changes)
    has_del = bool(plan.deletes)
    if has_type and has_del:
        return "下一步：确认变更"
    if has_type:
        return "下一步：确认类型变更"
    if has_del:
        return "下一步：确认删除"
    return "应用"


# task #21 阶段 B：drafts 列表辅助纯函数（不依赖 Qt，便于测试）
def clone_draft(d: FieldDraft) -> FieldDraft:
    """浅拷贝一个 FieldDraft（标量字段直接构造）。"""
    return FieldDraft(
        origin=d.origin,
        existing_field_id=d.existing_field_id,
        original_name=d.original_name,
        original_type=d.original_type,
        name=d.name,
        type=d.type,
        prompt_hint=d.prompt_hint,
        deleted=d.deleted,
    )


def drafts_are_dirty(
    current: list[FieldDraft], baseline: list[FieldDraft],
) -> bool:
    """判断 ``current`` 与 ``baseline`` 是否在任一字段上不一致。

    用于 Step 2 的 Back 路径：``baseline`` 是进入 Step 2 时的初始合并态、
    ``current`` 是用户编辑后的状态。
    """
    if len(current) != len(baseline):
        return True
    for a, b in zip(current, baseline):
        if (
            a.origin != b.origin
            or a.existing_field_id != b.existing_field_id
            or a.original_name != b.original_name
            or a.original_type != b.original_type
            or a.name != b.name
            or a.type != b.type
            or a.prompt_hint != b.prompt_hint
            or a.deleted != b.deleted
        ):
            return True
    return False


# task #21 阶段 B：Step 1 表的渲染过滤（哪些 ann 在 Step 1 显示；不依赖 Qt）
def step1_visible_indices(suggestions: list["AnnotatedSuggestion"]) -> list[int]:
    """task #21：Step 1 展示所有 LLM 实际触达过的字段，供用户审阅。

    过滤掉的：
    - 纯 user-only 现有字段（``existing_user_field``，即 LLM 本轮完全没在
      返回里提到的现有字段，含 LLM 删除/改名建议被驳回后退化的）—— 由
      Step 2 编辑表承担

    保留的：
    - 所有 ``llm_touched=True`` 的 ann：包括 ``system_required`` / ``same_type``
      在 LLM 没改 hint 时也显示，由 ``step1_action_label`` 给出 "✓ 保持原样"
      标签，让用户明确看到 "LLM 看了这个字段，认为不用改" 的反馈

    task #22 round 11 曾经把"无改动"过滤掉，结果第二轮"应用并继续讨论"
    时 step1 只剩三两条改动行，用户失去对完整字段表的上下文感。
    round 15 撤销该过滤——"保持原样"行不带批准/驳回按钮，不打扰用户
    （`_make_change_cell` 已用 `has_llm_change` 控制按钮可见），同时把
    完整审阅视图还给用户。
    """
    return [
        i for i, ann in enumerate(suggestions)
        if ann.status != "existing_user_field"
    ]


# task #22：Step 1 「LLM 建议」列的纯函数（无 Qt 依赖）
# ---------------------------------------------------------------------
# 把 ann.status × 实际改动维度 × 决策态映射成"普通用户能读懂的动作描述"。
# 文案矩阵见 tasks/22-wizard-status-column-redesign.md。
def step1_changed_dimensions(ann: "AnnotatedSuggestion") -> list[str]:
    """task #22：返回该 ann 实际改动的维度列表，按约定顺序：
    ``['name', 'type', 'hint']`` 的子集。

    判据（task #22 round 10：改用 ``llm_orig_*`` 与 ``existing_*`` 比较，
    而不是 ``ann.type / ann.prompt_hint``——因为 ``_on_decision_changed``
    在用户驳回时会把后者还原回 existing 值；dims 表达的是"**LLM 原本
    建议**改了哪些维度"，跟用户接没接受无关）：

    - ``llm_suggest_rename`` 必含 ``'name'``
    - ``llm_suggest_rename`` / ``type_conflict`` 且
      ``llm_orig_type != existing_field_type`` → 含 ``'type'``
    - LLM 触达分支且 ``llm_orig_prompt_hint != existing_prompt_hint``
      → 含 ``'hint'``

    顺序固定为 name → type → hint，便于文案稳定拼装。
    ``new`` / ``llm_suggest_delete`` 是单维动作，不走"维度组合"路径，
    本函数返回空列表（调用方按 status 分支处理）。
    """
    dims: list[str] = []
    if ann.status == "llm_suggest_rename":
        dims.append("name")
    if ann.status in ("llm_suggest_rename", "type_conflict"):
        if ann.existing_field_type and ann.llm_orig_type \
                and ann.llm_orig_type != ann.existing_field_type:
            dims.append("type")
    if ann.status in (
        "llm_suggest_rename", "type_conflict",
        "same_type", "system_required",
    ):
        if (ann.llm_orig_prompt_hint or "") != (ann.existing_prompt_hint or ""):
            dims.append("hint")
    return dims


def step1_action_label(
    ann: "AnnotatedSuggestion",
) -> tuple[str, str]:
    """task #22：Step 1 「LLM 建议」列的动作文案 + tooltip。

    返回 ``(label, tooltip)``。文案矩阵见 task #22 卡片。

    task #22 round 13：label 不再带"（已批准）/（已驳回）"后缀——决策
    状态由第 0 列的"已批准/已驳回"标签 + 驳回时文字变灰已经表达，再加
    后缀属于信息冗余。

    label 可能含简单 HTML 片段（``<br/>`` + ``<small>``）用于"被吞类型"
    场景的副标题；tooltip 是纯文本（多行 ``\\n`` 分隔）。
    """
    # ---- 单维动作：new / delete ----
    if ann.status == "new":
        type_label = FIELD_TYPE_LABELS.get(ann.type, ann.type)
        label = "➕ 新增字段"
        tooltip = (
            f"LLM 建议新增字段「{ann.name}」"
            f"（类型：{type_label}）。批准后会创建到库里。"
        )
        return label, tooltip

    if ann.status == "llm_suggest_delete":
        label = "🗑 删除字段"
        tooltip_parts = [f"LLM 建议删除字段「{ann.name}」。"]
        if ann.llm_reason:
            tooltip_parts.append(f"理由：{ann.llm_reason}")
        tooltip_parts.append(
            "批准后会一并清掉该字段在所有项目里的填值（此操作不可恢复）。"
        )
        tooltip = "\n".join(tooltip_parts)
        return label, tooltip

    # ---- 多维"修改"动作 ----
    dims = step1_changed_dimensions(ann)

    # dims 为空 + 非 rename → LLM 触达了但实际没改任何维度（最常见：
    # system_required / same_type 在 LLM 给的 hint 与现有相同时，或
    # type_conflict 驳回还原后）。给用户一个明确的"✓ 保持原样"标签让
    # 他知道"LLM 看了，认为不用改"或"已驳回，恢复原状"——这一行不
    # 显示批准/驳回按钮（_make_change_cell 用 has_llm_change 控制），
    # 不打扰用户但保留完整审阅视图。
    if not dims and ann.status in ("system_required", "same_type", "type_conflict"):
        label = "✓ 保持原样"
        tooltip = (
            "已驳回 LLM 的修改建议，字段恢复原状。"
            if ann.decision == "rejected"
            else "LLM 已审阅这个字段，没有修改建议。"
        )
        return label, tooltip

    # 主词组装
    parts_main: list[str] = []
    if "name" in dims:
        parts_main.append("字段名")
    if "type" in dims:
        parts_main.append("类型")
    if "hint" in dims:
        parts_main.append("提示")
    head = "、".join(parts_main) if parts_main else "（无变更）"

    # task #22 round 7：label 只显示改了哪些维度（字段名 / 类型 / 提示），
    # 具体值（新名、旧→新类型、新 hint 内容）一律放 tooltip 里——保持
    # Step 1 表格紧凑，长字段名 / 长类型不会撑爆"LLM 建议"列宽度。
    label = f"✏ 修改{head}"

    # ---- tooltip 拼装：每改一项追加一段 ----
    # task #22 round 10：tooltip 描述的是"LLM 原本建议改成什么"，所以读
    # llm_orig_type / llm_orig_prompt_hint（永不被覆盖），而不是 ann.type /
    # ann.prompt_hint（驳回时会被还原回 existing 值）。
    tooltip_lines: list[str] = []
    if "name" in dims:
        tooltip_lines.append(
            f"把字段「{ann.name}」改名为「{ann.llm_rename_new_name}」"
            f"（数据保留）。"
        )
    if "type" in dims:
        old_l = FIELD_TYPE_LABELS.get(
            ann.existing_field_type, ann.existing_field_type,
        )
        new_l = FIELD_TYPE_LABELS.get(ann.llm_orig_type, ann.llm_orig_type)
        tooltip_lines.append(
            f"把类型从「{old_l}」改为「{new_l}」"
            f"（旧值仍保留在库里，新控件可能读不出）。"
        )
    if "hint" in dims:
        new_hint = (ann.llm_orig_prompt_hint or "").strip().replace("\n", " ")
        if len(new_hint) > 30:
            new_hint = new_hint[:30] + "…"
        tooltip_lines.append(
            f"把 LLM 提示更新为「{new_hint or '（清空）'}」。"
        )
    # LLM 改名理由（只对 llm_suggest_rename 追加，让用户能看到 LLM 当时
    # 为什么这么建议；type_conflict / same_type / system_required 的
    # reason 是系统拼装的描述文案，不是 LLM 给的原始理由，不展示）
    if ann.status == "llm_suggest_rename" and ann.llm_reason:
        tooltip_lines.append(f"LLM 理由：{ann.llm_reason}")
    tooltip = "\n".join(tooltip_lines) if tooltip_lines else (ann.reason or "")
    return label, tooltip


def annotate_conflicts(
    suggestions: list[dict],
    existing_fields: list,
    suggested_deletes: Optional[list[dict]] = None,
    suggested_renames: Optional[list[dict]] = None,
    out_warnings: Optional[list[str]] = None,
) -> list[AnnotatedSuggestion]:
    """全量规划：把现有字段全部纳入预览，并叠加 LLM 的修订/新增/删除/改名建议。

    返回顺序：先按现有字段的 ``ord`` 列出（含未被 LLM 提及的），再追加 LLM 给的全新字段。
    每个 ``AnnotatedSuggestion`` 的 ``status`` 决定 UI 行为与应用动作（见类文档）。

    系统必有字段（标题/描述/标签）：``system_required`` — 强制 selected。
    LLM 显式建议改名（``suggested_renames`` 命中现有字段名）：``llm_suggest_rename``，
    默认 ``selected=False`` 待批准；批准 → 应用阶段走 rename（保留 fid，UPDATE name）；
    驳回 → 退化为 ``existing_user_field`` 等价路径（保留原名）。
    LLM 显式建议删除（``suggested_deletes`` 命中）：``llm_suggest_delete``，默认
    ``selected=False`` 待批准；用户批准 → ``selected=True`` → 真删；驳回 →
    ann.status 切回 ``existing_user_field`` 等价路径，``selected=True`` 保留。
    现有用户/系统非必有字段，LLM 未在本次输出里命中且不在删除/改名建议中：
    ``existing_user_field`` — 默认 ``selected=True`` 保留；用户取消勾选 → 删除。
    现有字段被 LLM 命中且 type 一致：``same_type`` — 走 update_hint_only。
    现有字段被 LLM 命中但 type 不同：``type_conflict``。
    LLM 全新名字：``new``。

    优先级（同一现有字段名同时出现在多个建议数组里的冲突解决）：
        在 ``fields`` 里被命中（``same_type`` / ``type_conflict``）   优先级最高
        在 ``fields_to_rename`` 里                                   次之
        在 ``fields_to_delete`` 里                                   再次
        都没出现                                                     ``existing_user_field``

    Args:
        suggested_deletes: LLM 在 ``fields_to_delete`` 中给出的"建议删除"项；
            每条 ``{"name": str, "reason": str}``。LLM 同时把字段放进 ``fields``
            （应保留）又放进 ``fields_to_delete``（应删除）的矛盾场景，以
            ``fields`` 为准（删除建议被忽略）。
        suggested_renames: LLM 在 ``fields_to_rename`` 中给出的"建议改名"项；
            每条 ``{"old_name": str, "new_name": str, "reason": str}``。
            ``new_name`` 与现有其它字段重名（除 old_name 自身）→ 该改名建议被
            忽略并加 warning（在 parse 层已经过基本校验，这里只兜底）。
            ``old_name`` 同时出现在 ``fields`` 中（保留）→ 以 ``fields`` 为准，
            改名建议被忽略。
        out_warnings: 可选的 list。若传入，函数会把发现的语义级 warning
            append 进来。``None`` 表示静默丢弃，兼容老调用方。
            （task #22 round 6 起 rename + 改类型组合不再产生 warning：
            task #19 收尾清理后该组合直接合并到 ``ann.type``，由
            type_changes 安全路径处理。）"""
    by_name = {f.name: f for f in existing_fields}
    existing_names = {f.name for f in existing_fields}
    # 同名 LLM 建议保留**第一次**出现的（避免 dict 自动用最后一个覆盖；
    # 最先列出的通常是 LLM 真正想要的版本，重名属于 LLM 误输出）
    sugg_by_name: dict[str, dict] = {}
    for s in suggestions:
        name = s.get("name") or ""
        if name and name not in sugg_by_name:
            sugg_by_name[name] = s

    # LLM 显式删除建议：name → reason；若 name 同时出现在 fields 中以保留为准
    deletes_by_name: dict[str, str] = {}
    for d in (suggested_deletes or []):
        d_name = (d.get("name") or "").strip()
        if not d_name:
            continue
        if d_name in sugg_by_name:
            # LLM 自相矛盾：同时给出"保留+建议删除"，以保留为准
            continue
        deletes_by_name[d_name] = (d.get("reason") or "").strip()

    # LLM 显式改名建议：old_name → (new_name, reason)
    # 兜底校验（parse 已做基础校验，这里防御性地再过一遍）：
    #   - old_name 必须是当前库已存在字段名（否则丢弃）
    #   - new_name 不得与现有其它字段重名（除 old_name 自身）
    #   - 同一 old_name 同时出现在 sugg_by_name（fields 数组）→ 以保留为准
    #   - 同一 old_name 同时出现在 deletes_by_name → 以改名为准（移出 deletes）
    # 注意：``new_name`` 通常会**同时**出现在 ``fields`` 数组里（因为 prompt 要求
    # ``fields`` 是改名后的完整方案），第二遍处理 new 时要跳过；如果 LLM 在
    # fields[<new_name>] 里给了新的 prompt_hint，把它合并到 rename ann 上
    # （type 不允许通过 rename 路径改动，那是 type_conflict 的职责）。
    renames_by_old: dict[str, tuple[str, str]] = {}
    for r in (suggested_renames or []):
        old_name = (r.get("old_name") or "").strip()
        new_name = (r.get("new_name") or "").strip()
        if not old_name or not new_name:
            continue
        if old_name not in existing_names:
            continue  # parse 应该已过滤；防御性兜底
        if old_name == new_name:
            continue
        if new_name in existing_names and new_name != old_name:
            continue  # 与现有其它字段冲突
        if old_name in sugg_by_name:
            # LLM 自相矛盾：同时把字段放进 fields（保留）+ fields_to_rename
            # 以"保留 + 不改名"为准
            continue
        renames_by_old[old_name] = (new_name, (r.get("reason") or "").strip())

    # rename 与 delete 同时命中同一 old_name → 改名优先（保数据更稳）
    for old_name in list(renames_by_old.keys()):
        if old_name in deletes_by_name:
            del deletes_by_name[old_name]

    out: list[AnnotatedSuggestion] = []
    handled_existing: set[str] = set()
    handled_suggestion: set[str] = set()

    # 第一遍：按现有字段顺序逐一处理（含 LLM 命中与未命中两种）
    for ex in existing_fields:
        handled_existing.add(ex.name)
        s = sugg_by_name.get(ex.name)

        # 系统必有字段
        if ex.name in _SYSTEM_REQUIRED_NAMES:
            a = AnnotatedSuggestion(
                name=ex.name, type=ex.type,
                prompt_hint=(s.get("prompt_hint", "") if s else ex.prompt_hint),
            )
            a.status = "system_required"
            a.reason = "系统必有字段；将更新其 LLM 提示，不可移除/改名/改类型"
            a.selected = True
            a.existing_field_id = ex.id
            a.existing_field_type = ex.type
            a.existing_prompt_hint = ex.prompt_hint
            if s is not None:
                handled_suggestion.add(ex.name)
                a.llm_touched = True
                # task #22 round 10：保留 LLM 原始 type / prompt_hint 快照，
                # 让 step1_changed_dimensions 在用户驳回（ann 被还原）后仍能
                # 算出"LLM 原本建议改了哪些维度"。
                a.llm_orig_type = a.type
                a.llm_orig_prompt_hint = a.prompt_hint
            out.append(a)
            continue

        # 现有字段在 LLM 输出里被命中
        if s is not None:
            handled_suggestion.add(ex.name)
            a = AnnotatedSuggestion(
                name=ex.name, type=s.get("type", ex.type),
                prompt_hint=s.get("prompt_hint", ""),
            )
            a.existing_field_id = ex.id
            a.existing_field_type = ex.type
            a.existing_prompt_hint = ex.prompt_hint
            a.llm_touched = True
            # task #20 schema v4 起：放宽"is_system 但类型不符 → 跳过"规则
            # （原 system_protected 状态废弃）。
            # author/date/source_url/rating 等"带 key 但非 protected"字段
            # 改类型走 type_conflict 路径（task #19 Phase B 接管）。
            # 受保护字段（标题/描述/标签）已在上方 _SYSTEM_REQUIRED_NAMES
            # 分支被吃掉、类型强制保持原值，不会到这里。
            if ex.type == a.type:
                a.status = "same_type"
                if ex.prompt_hint:
                    a.reason = "已存在（类型一致），现有 LLM 提示非空 → 跳过不覆盖"
                else:
                    a.reason = "已存在（类型一致）→ 仅写入 LLM 提示"
                a.selected = True
            else:
                a.status = "type_conflict"
                a.reason = (
                    f"LLM 建议把现有字段「{ex.name}」的类型从 {ex.type} 改为 {a.type}，"
                    f"并配套新的提示。批准 → 一并更新；驳回 → 保持不变。"
                )
                # task #19 Phase B：批准/驳回二态，默认 selected=True 表示
                # "未驳回即接受"（跟 same_type / 普通 new 的默认接受行为一致）
                a.selected = True
            # task #22 round 10：same_type / type_conflict 共用，保留 LLM
            # 原始 type / hint 快照，让驳回后 dims 仍能展示 LLM 改的维度
            a.llm_orig_type = a.type
            a.llm_orig_prompt_hint = a.prompt_hint
            out.append(a)
            continue

        # 现有字段，未被 LLM 命中：先看是不是在 LLM 显式改名建议名单里
        if ex.name in renames_by_old:
            new_name, reason = renames_by_old[ex.name]
            # 如果 LLM 在 fields[] 里同时给了 new_name 那一行（这是预期的，
            # 因为 prompt 要求 fields 是改名后的完整方案），把它的
            # prompt_hint / type 都合并到 rename ann，让 rename 路径在 apply
            # 时能一并改类型（task #19 Phase A 的安全护栏 + task #22 Phase B
            # 的 type_changes 三元组路径已经具备能力 → 不再"为保留数据吞类型"）。
            new_row_in_fields = sugg_by_name.get(new_name)
            merged_hint = ex.prompt_hint
            merged_type = ex.type
            if new_row_in_fields is not None:
                handled_suggestion.add(new_name)
                if (new_row_in_fields.get("prompt_hint") or "").strip():
                    merged_hint = new_row_in_fields["prompt_hint"]
                new_type_str = (new_row_in_fields.get("type") or "").strip()
                if new_type_str:
                    merged_type = new_type_str
            a = AnnotatedSuggestion(
                name=ex.name, type=merged_type, prompt_hint=merged_hint,
            )
            a.status = "llm_suggest_rename"
            a.existing_field_id = ex.id
            a.existing_field_type = ex.type
            a.existing_prompt_hint = ex.prompt_hint
            # 默认 selected=False（待批准）；批准 → selected=True 走 rename 路径
            a.selected = False
            a.llm_touched = True
            a.llm_rename_new_name = new_name
            a.reason = reason or "（LLM 未提供改名理由）"
            a.llm_reason = a.reason
            # task #22 round 10：保留 LLM 原始 type / hint 快照，让 dims
            # 在用户驳回（ann 被还原回 existing 值）后仍能展示完整维度
            a.llm_orig_type = merged_type
            a.llm_orig_prompt_hint = merged_hint
            out.append(a)
            continue

        # 现有字段，未被 LLM 命中：再看是不是在 LLM 显式删除建议名单里
        if ex.name in deletes_by_name:
            a = AnnotatedSuggestion(
                name=ex.name, type=ex.type, prompt_hint=ex.prompt_hint,
            )
            a.status = "llm_suggest_delete"
            a.existing_field_id = ex.id
            a.existing_field_type = ex.type
            a.existing_prompt_hint = ex.prompt_hint
            # 默认 selected=False（待批准）；批准按钮 → 切 selected=True 走 delete
            a.selected = False
            a.llm_touched = True
            a.reason = deletes_by_name[ex.name] or "（LLM 未提供删除理由）"
            a.llm_reason = a.reason
            out.append(a)
            continue

        # 现有字段，LLM 未在本次提及 → 默认保留，可被用户取消勾选去删除
        a = AnnotatedSuggestion(
            name=ex.name, type=ex.type, prompt_hint=ex.prompt_hint,
        )
        a.status = "existing_user_field"
        a.existing_field_id = ex.id
        a.existing_field_type = ex.type
        a.existing_prompt_hint = ex.prompt_hint
        a.selected = True
        a.llm_touched = False
        a.reason = (
            "现有字段，LLM 本次未提及。默认保留；取消勾选则在「应用」时**删除**该字段。"
        )
        out.append(a)

    # 第二遍：LLM 给的、且不在现有字段集合中的 → 全新字段
    seen_new: set[str] = set()
    for s in suggestions:
        name = s.get("name") or ""
        if not name or name in handled_suggestion or name in seen_new:
            continue
        seen_new.add(name)
        if name in _SYSTEM_REQUIRED_NAMES and name not in by_name:
            # 系统必有字段在新库里可能尚未存在；当作 system_required 处理
            a = AnnotatedSuggestion(
                name=s["name"], type=s["type"],
                prompt_hint=s.get("prompt_hint", ""),
            )
            a.status = "system_required"
            a.reason = "系统必有字段（新库尚未创建）；将创建并写入 LLM 提示"
            a.selected = True
            a.llm_touched = True
            out.append(a)
            continue
        a = AnnotatedSuggestion(
            name=s["name"], type=s["type"], prompt_hint=s.get("prompt_hint", ""),
        )
        a.status = "new"
        a.selected = True
        a.llm_touched = True
        out.append(a)

    return out


# =============================================================================
# Prompt
# =============================================================================
_SYSTEM_PROMPT = (
    "你是一位帮助用户规划本地资料库的助手。用户描述他们要管理的内容类型与字段偏好"
    "（例如：学术论文、游戏素材、菜谱、漫画……），你需要给出一份**完整**的字段方案，"
    "并在用户提供的库描述基础上做适度完善。\n\n"
    "硬性要求：\n"
    "1. **必有 3 个固定字段**：「标题」「描述」「标签」（这 3 个不可移除/改名/改类型；"
    "其它任何字段都按场景自由设计，**不要**凭空添加"
    "「作者 / 日期 / 评分 / 来源」之类的通用字段——除非用户描述的场景**真的需要**它们。\n"
    "2. 除上述 3 个必有字段外，按场景再设计 2~7 个**真正有区分度**的字段；"
    "**总数控制在 5~10 个**。宁可少而精准，也不要凑数。\n"
    "3. 输出必须是 JSON 对象（不要 markdown 代码块、不要解释文字），结构如下：\n"
    "{\n"
    '  "library_description": "对这个库整体定位的一段描述（80~250 字）；'
    '若用户已提供描述，请在其基础上完善而不是另写一份；用第二人称『你』指代用户",\n'
    '  "fields": [\n'
    '    {"name": "标题",   "type": "text",     "prompt_hint": "..."},\n'
    '    {"name": "描述",   "type": "textarea", "prompt_hint": "..."},\n'
    '    {"name": "标签",   "type": "tags",     "prompt_hint": "..."},\n'
    '    {"name": "字段名", "type": "text|textarea|date|url|rating|number",'
    ' "prompt_hint": "对该字段在 LLM 建议时的格式说明（30~150 字）"},\n'
    "    ...\n"
    "  ],\n"
    '  "fields_to_delete": [\n'
    '    {"name": "已存在但你建议删除的字段名", "reason": "为什么建议删除（一句话，30~80 字）"}\n'
    "  ],\n"
    '  "fields_to_rename": [\n'
    '    {"old_name": "现有字段的旧名", "new_name": "建议的新名", "reason": "为什么建议改名（一句话，30~80 字）"}\n'
    "  ]\n"
    "}\n"
    "4. type 必须是 text / textarea / date / url / rating / number 之一；"
    "「标签」字段固定用 tags 类型，**仅此一个字段可用 tags**。\n"
    "5. prompt_hint 用中文，给出该字段的填写格式约束（长度、风格、示例）。\n"
    "6. 「标签」字段（type=tags）的 prompt_hint **特殊**：除常规格式约束外，"
    "**必须**用 markdown 列表给出**标签分类策略**，告诉用户应该从哪些维度去打标签。"
    "格式严格按下例（每个维度 2~5 个示例标签，整段 150~300 字）：\n"
    "```\n"
    "建议从以下维度打标签，单个项目最终选取 1~3 个最相关的维度，每个维度 1~2 个标签：\n"
    "- **领域**：领域/科幻、领域/历史、领域/工具书\n"
    "- **类型**：类型/论文、类型/笔记、类型/参考资料\n"
    "- **状态**：状态/未读、状态/在读、状态/已读\n"
    "示例标签均使用 `维度/标签` 的层级形式（左侧标签树会自动按 `/` 前缀折叠分组）；"
    "也可以新造同风格的标签。\n"
    "```\n"
    "**关键约束**：示例标签**必须**使用 `<维度名>/<具体标签>` 的层级写法（与左侧标签树"
    "的折叠规则一致）；不要给出无前缀的散标签如 \"科幻\"，应写成 \"领域/科幻\"。"
    "维度名要根据用户的场景定，不要直接照抄上面的「领域 / 类型 / 状态」。\n"
    "7. fields 数组的顺序：先是 标题 / 描述 / 标签，再是用户场景里设计出来的字段。\n"
    "8. **不要**输出 tag_axes / default_tags_suggestion 等独立字段；分类策略全部写进"
    "「标签」字段的 prompt_hint。\n"
    "9. **fields_to_delete**：可选数组，仅在你认为「当前库已存在的字段」中**确实有**"
    "与场景明显无关、应该被清理的字段时才填；每条必须给出 `name`（必须是当前库已"
    "存在的字段名，不能是新名字）和 `reason`（一句话说明为什么建议删除）。如果没有"
    "想删的字段，**不要**给这个键，或给空数组。**绝对不要**把"
    "「标题 / 描述 / 标签」放进来——这是必有字段。\n"
    "10. **fields_to_rename**：可选数组，**仅当你判断现有字段只是名字不够准确**"
    "（例如「出版社」改「出版商」、「note」改「笔记」），希望**保留该字段已有的"
    "项目数据**而只换个更恰当的名字时使用。每条 `old_name` 必须是当前库已存在"
    "的字段名，`new_name` 是建议的新名（不得与现有其它字段重名，也不得是「标题 / "
    "描述 / 标签」），`reason` 一句话说明。**严禁**用 `fields_to_delete + fields` "
    "等价模拟改名 — 这会让该字段在所有项目里的历史值丢失。如不需要改名，**不要**"
    "给这个键，或给空数组。**与 fields 数组的关系**：当你建议把 A 改名为 B 时，"
    "`fields` 数组里**应该**包含 B（因为 fields 必须是改名后的完整方案），"
    "**不要**再写 A；系统会自动把 fields 里的 B 与改名建议合并显示。"
)


def build_messages(
    user_scenario: str,
    history: list[dict],
    extra_instruction: str = "",
    library_description: str = "",
    current_fields: Optional[list[dict]] = None,
) -> list[dict]:
    """组装 messages（历史以文本形式回放，跨 provider 最稳）。

    Args:
        current_fields: 当前库已有字段，可选；每项 dict 含 ``name`` / ``type`` /
            ``prompt_hint``。LLM 应基于现状给出修订建议（保留有用、补缺、避免重名）。
    """
    messages: list[dict] = [
        {"role": "system", "content": [{"type": "text", "text": _SYSTEM_PROMPT}]},
    ]
    parts: list[str] = [f"使用场景描述：\n{user_scenario.strip()}"]
    if library_description.strip():
        parts.append(
            "用户已有的库描述（请在此基础上完善输出 library_description）：\n"
            + library_description.strip()
        )
    if current_fields:
        # 按 (name, type, prompt_hint) 紧凑罗列；告诉 LLM "现状已经有这些字段"
        rows = [
            f"- {f.get('name','')} ({f.get('type','')})"
            + (f"  hint={f.get('prompt_hint','')[:80]}" if f.get('prompt_hint') else "")
            for f in current_fields
        ]
        parts.append(
            "当前库已存在的字段（请考虑保留/合并/改进它们；同名字段可在 fields "
            "里再次给出以更新 prompt_hint）：\n" + "\n".join(rows)
        )
    if history:
        parts.append("上一次的返回（你之前生成的）：\n" + "\n".join(
            h.get("content", "") for h in history
        ))
    if extra_instruction.strip():
        parts.append("请基于上次结果做以下调整：\n" + extra_instruction.strip())
    messages.append({
        "role": "user",
        "content": [{"type": "text", "text": "\n\n".join(parts)}],
    })
    return messages


# =============================================================================
# 后台 worker
# =============================================================================
def _friendly_llm_error(e: Exception) -> str:
    """把 LLM 调用抛出的异常翻译成用户能读懂的中文消息。

    `type(e).__name__: e` 这种 repr 风格只适合给开发者看；普通用户看到
    `JSONDecodeError` / `ConnectionResetError` 一类术语会一头雾水。这里按
    异常类型 / 异常消息里的关键字做粗分类，给出动作建议。原始异常消息
    会作为括号注脚附上，便于反馈。
    """
    name = type(e).__name__
    msg = str(e) or name
    low = (name + " " + msg).lower()
    if any(k in low for k in ("timeout", "timed out")):
        return (
            "LLM 平台响应超时，请检查网络后重试。\n"
            f"（错误信息：{msg}）"
        )
    if any(k in low for k in (
        "connection", "network", "dns", "resolve", "ssl",
        "unreachable", "refused",
    )):
        return (
            "无法连接到 LLM 平台。请检查网络是否通畅 / 是否需要代理 / "
            "API 地址是否正确。\n"
            f"（错误信息：{msg}）"
        )
    if any(k in low for k in ("401", "403", "unauthorized", "forbidden", "api key", "apikey")):
        return (
            "LLM 平台拒绝了请求，多半是 API Key 不对或未授权。"
            "请到「设置 → API」检查 Key 是否填写正确。\n"
            f"（错误信息：{msg}）"
        )
    if any(k in low for k in ("429", "rate limit", "quota")):
        return (
            "请求过于频繁或额度已用完。请稍后再试，或检查账户余额。\n"
            f"（错误信息：{msg}）"
        )
    if any(k in low for k in ("json", "parse", "decode")):
        return (
            "LLM 返回的内容无法解析。可以稍后重试一次；多次失败时可"
            "切换其它模型或使用「在当前基础上调整」给出更明确的提示。\n"
            f"（错误信息：{msg}）"
        )
    return (
        "调用 LLM 时出错，请稍后重试。\n"
        f"（错误信息：{msg}）"
    )


class _WizardLLMWorker(QObject):
    """后台直调 provider（决策 3：不走 LLMTaskQueue）。"""

    # finished(payload_or_None, raw_text_or_error, warnings_list, tokens_in, tokens_out)
    finished = Signal(object, str, list, int, int)

    def __init__(self, provider, messages: list[dict], use_json_mode: bool):
        super().__init__()
        self.provider = provider
        self.messages = messages
        self.use_json_mode = use_json_mode

    def run(self) -> None:
        try:
            resp = self.provider.chat(
                self.messages, json_mode=self.use_json_mode, timeout=120.0,
            )
        except Exception as e:  # noqa: BLE001
            # task #22 round 3：把异常类名 + repr 翻成用户能读懂的中文消息。
            # 内部细节（class 名 / 完整堆栈）由 logger.exception 留给开发者。
            err_friendly = _friendly_llm_error(e)
            self.finished.emit(None, err_friendly, [], 0, 0)
            return
        tin = int(getattr(resp, "tokens_in", 0) or 0)
        tout = int(getattr(resp, "tokens_out", 0) or 0)
        try:
            payload, warnings = parse_and_validate(resp.text or "")
        except WizardLLMOutputError as e:
            self.finished.emit(
                None, f"模型输出不规范：{e}\n\n原始响应：\n{resp.text}",
                [], tin, tout,
            )
            return
        self.finished.emit(payload, resp.text or "", warnings, tin, tout)


# =============================================================================
# 主助手对话框
# =============================================================================
PAGE_INTRO = 0
PAGE_SCENARIO = 1
PAGE_RUNNING = 2
# task #21：原 PAGE_PREVIEW 拆成 Step 1（审阅 LLM 建议）+ Step 2（字段表编辑）
PAGE_STEP1 = 3
PAGE_STEP2 = 4
# 旧别名保留（仅用于历史代码引用；语义上等同于 Step 1）
PAGE_PREVIEW = PAGE_STEP1


class LibraryInitWizard(WizardPlugin):
    """库字段设计助手（对外文案；内部沿用 wizard / library_init 命名）。"""

    meta = WizardMeta(
        id="library_init",
        title="库字段设计助手",
        description=(
            "用一段话描述这个库的目的与字段偏好；可以在让 LLM 给建议前先调整字段；"
            "LLM 会基于现有情况给出新增 / 修改 / 删除建议，你可以逐条批准或驳回。"
            "适合刚建好新库或需要重整字段结构时使用。"
        ),
        category="库初始化",
        icon="🪄",
        require_empty_lib=False,
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("库字段设计助手")
        # task #22 round 1：对话框高度 +10% 给 Step 1 表多腾点空间
        self.resize(900, 780)
        self.repo = None
        self.library = None
        self._max_rounds = DEFAULT_MAX_ROUNDS
        self._current_round = 0
        self._scenario_text = ""
        self._library_desc_input = ""        # 用户在场景页输入的库描述（发给 LLM 的）
        self._library_desc_suggested = ""    # LLM 在最近一轮返回的 library_description
        # 库描述的批准/驳回状态（"pending" / "approved" / "rejected"）；
        # 仅当 LLM 这一轮实际改过描述（_library_desc_input != _library_desc_suggested）
        # 时按钮才显示。语义与字段建议的 decision 一致。
        self._library_desc_decision: str = "pending"
        self._last_raw_response = ""
        self._history: list[dict] = []
        self._suggestions: list[AnnotatedSuggestion] = []
        # LLM 那一轮原始 payload + 当时的现有字段快照
        # 用于「再次应用 LLM 建议」按钮智能重建条目
        self._llm_round_payload: Optional[dict] = None
        self._llm_round_existing: list = []
        self._applied = False
        self._thread: Optional[QThread] = None
        self._worker: Optional[_WizardLLMWorker] = None
        # token 累计（每一轮 LLM 返回后追加）
        self._tokens_in_total: int = 0
        self._tokens_out_total: int = 0
        self._last_round_tokens_in: int = 0
        self._last_round_tokens_out: int = 0
        # task #21 两段式向导：Step 2 字段表草稿；进入 Step 2 时通过
        # merge_decisions_into_drafts() 重新生成；Back 时整体丢弃
        self._drafts: list[FieldDraft] = []
        # task #21：Step 2 进入时的 drafts 快照（深拷贝），用于 Back 时检测"是否
        # 有编辑"以决定要不要弹确认对话框
        self._drafts_baseline: list[FieldDraft] = []
        # task #21：受保护字段 fid 集合（_on_step1_next 时根据 repo 填充）
        self._protected_fids: set[int] = set()
        # task #21：Step 1 表的渲染行号 → _suggestions 真实索引的映射
        self._step1_visible_rows: list[int] = []

        self._build_ui()

    # ---- WizardPlugin 接口 -------------------------------------------------
    def run(self, repo, library) -> bool:
        self.repo = repo
        self.library = library
        self._max_rounds = get_max_rounds(repo)
        self._refresh_round_label()
        try:
            n = repo.count_projects_total()
        except Exception:
            n = 0
        if n > 0:
            self.lbl_warn.setText(
                f"⚠ 当前库已有 {n} 个项目。本助手会基于现状给出修订建议；"
                "勾选删除时会清掉对应项目的字段值，请谨慎操作。"
            )
            self.lbl_warn.setVisible(True)
        else:
            self.lbl_warn.setVisible(False)
        # 检查 API 配置 → 决定 lbl_api_status 文案 + 下一步按钮 enable
        self._refresh_api_status()
        # 把当前库描述（如有）灌进合并后的输入框
        cur_desc = (self.repo.get_setting("library_description", "") or "").strip()
        if cur_desc:
            self.ed_scenario.setPlainText(cur_desc)
        else:
            self.ed_scenario.setPlainText("")
        # 加载现有字段表
        self._reload_existing_fields_table()
        self.exec()
        return self._applied

    # ---- UI 构建 -----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        top = QHBoxLayout()
        ttl = QLabel("🪄  库字段设计助手")
        f = QFont(); f.setPointSize(14); f.setBold(True)
        ttl.setFont(f)
        top.addWidget(ttl)
        top.addStretch(1)
        self.lbl_tokens = QLabel("用量：累计 0 输入 / 0 输出")
        self.lbl_tokens.setProperty("muted", True)
        self.lbl_tokens.setToolTip(
            "本次会话累计的对话用量（输入 = 发送给 LLM 的字数，输出 = LLM 回复的字数）；"
            "实际计费按所选 LLM 平台的口径"
        )
        top.addWidget(self.lbl_tokens)
        sep = QLabel(" · ")
        sep.setProperty("muted", True)
        top.addWidget(sep)
        self.lbl_round = QLabel("轮数 0 / 5")
        self.lbl_round.setProperty("muted", True)
        top.addWidget(self.lbl_round)
        root.addLayout(top)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_intro_page())
        self.stack.addWidget(self._build_scenario_page())
        self.stack.addWidget(self._build_running_page())
        self.stack.addWidget(self._build_preview_page())   # PAGE_STEP1
        self.stack.addWidget(self._build_step2_page())     # PAGE_STEP2
        self.stack.setCurrentIndex(PAGE_INTRO)

    def _build_intro_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(12)

        intro = QLabel(
            "<p>本助手会带你完成以下步骤：</p>"
            "<ol>"
            "<li>用一段自然语言描述你打算管理的内容类型；</li>"
            "<li>调用 LLM 生成一份字段结构方案；</li>"
            "<li>预览、编辑、删除或重新生成；</li>"
            "<li>满意后一次性写入库的字段表。</li>"
            "</ol>"
            "<p>提示：</p>"
            "<ul>"
            "<li>整个过程会调用 LLM，使用「设置 → API」中选定的默认平台；"
            "顶部会实时累计对话用量；</li>"
            "<li>可以随时取消；<b>不点「应用」就不会修改库</b>；</li>"
            "<li>已有的标题/作者等系统字段不会被重复创建。</li>"
            "</ul>"
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)
        v.addWidget(intro)

        # API 状态 banner（在 run() / 切回引导页时刷新）
        self.lbl_api_status = QLabel("")
        self.lbl_api_status.setWordWrap(True)
        self.lbl_api_status.setTextFormat(Qt.RichText)
        v.addWidget(self.lbl_api_status)

        self.lbl_warn = QLabel("")
        self.lbl_warn.setStyleSheet("color: #c62828;")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setTextFormat(Qt.RichText)
        self.lbl_warn.setVisible(False)
        v.addWidget(self.lbl_warn)

        v.addStretch(1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        b_cancel = QPushButton("退出")
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_cancel)
        self.btn_intro_next = QPushButton("下一步 →")
        self.btn_intro_next.setDefault(True)
        self.btn_intro_next.clicked.connect(
            lambda: self.stack.setCurrentIndex(PAGE_SCENARIO)
        )
        btns.addWidget(self.btn_intro_next)
        v.addLayout(btns)
        return w

    def _refresh_api_status(self) -> None:
        """检查当前默认 provider 配置，更新引导页 banner 与下一步按钮状态。"""
        if self.repo is None:
            return
        cfg = load_config(self.repo)
        active = cfg.active()
        if active is None:
            self.lbl_api_status.setText(
                "<span style='color:#c62828'>⚠ 未选择默认 LLM 平台。</span>"
                "请先在「设置 → API」中配置一个 LLM 平台并设为默认。"
            )
            self.btn_intro_next.setEnabled(False)
            self.btn_intro_next.setToolTip(
                "请先在「设置 → API」中配置默认 LLM 平台与 API Key"
            )
            return
        if not (active.api_key or "").strip():
            self.lbl_api_status.setText(
                f"<span style='color:#c62828'>⚠ 默认 LLM 平台 "
                f"<b>{active.label()}</b> 还没填 API Key。</span>"
                f"<br>请到「设置 → API」中填入 Key 后重新打开本助手。"
            )
            self.btn_intro_next.setEnabled(False)
            self.btn_intro_next.setToolTip(
                f"{active.label()} 还没填 API Key"
            )
            return
        # 一切就绪
        # task #22 round 3：不再向用户暴露 "JSON 原生 / Prompt 强约束" 这种
        # 仅对开发者有意义的内部模式名（即原 supports_json_mode 派生文案）。
        self.lbl_api_status.setText(
            f"<span style='color:#2e7d32'>✓ 已配置：</span>"
            f"<b>{active.label()}</b> · 模型 <code>{active.model}</code>"
        )
        self.btn_intro_next.setEnabled(True)
        self.btn_intro_next.setToolTip("")

    def _build_scenario_page(self) -> QWidget:
        from PySide6.QtWidgets import QAbstractItemView

        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(8)

        lbl_intro = QLabel(
            "<b>这个库是干什么的 / 你希望它怎么管理？</b>"
            " 包括内容类型、字段偏好、特殊约定等，越具体越好。"
            "（这段会作为「库描述」一并发给 LLM，LLM 会基于它完善并给出字段建议。"
            "<br>💡 如果是基于当前字段结构进一步修改，可以把修改意见追加在库描述的"
            "最后，它会一起发送给 LLM 进行处理。）"
        )
        lbl_intro.setWordWrap(True)
        lbl_intro.setTextFormat(Qt.RichText)
        v.addWidget(lbl_intro)

        # 顶部：单一描述输入框（合并自原"场景描述"+"库描述"）
        self.ed_scenario = QPlainTextEdit()
        self.ed_scenario.setPlaceholderText(
            "示例：\n"
            "我想用这个库管理我看过的科幻小说，重点关注作者、出版年代、子流派"
            "（硬科幻/软科幻/赛博朋克）、阅读状态（未读/在读/已读）、个人评分。\n"
            "希望每本书有一段不剧透的剧情概括，以及自己的读后感。"
        )
        # 历史命名对外仍需要"库描述"作为入口；这里复用 ed_scenario 一份就够了
        self.ed_library_desc = self.ed_scenario  # 兼容老代码路径
        # 文本框给个最大高度，把垂直空间留给下方的字段表
        self.ed_scenario.setMaximumHeight(130)
        v.addWidget(self.ed_scenario, 0)

        # 中部：现有字段编辑面板（行为与「设置 → 字段」一致）
        v.addWidget(QLabel(
            "<b>当前库的字段</b>　（可以在让 LLM 给建议之前先调整；"
            "操作和「设置 → 字段」一致）"
        ))
        lbl_preadjust_hint = QLabel(
            "💡 这里的增删改只是 <b>给 LLM 的输入起点</b>，"
            "<b>点「让 LLM 给出建议」之前都不会写入当前库</b>；"
            "想直接编辑库字段请用「设置 → 字段」。"
        )
        lbl_preadjust_hint.setProperty("hint", True)
        lbl_preadjust_hint.setWordWrap(True)
        lbl_preadjust_hint.setTextFormat(Qt.RichText)
        v.addWidget(lbl_preadjust_hint)
        self.tbl_existing = QTableWidget(0, 5)
        # 第 4 列原本叫"LLM 建议"，与预览页的「LLM 字段方案建议」列同名，
        # 在助手语境下容易让用户误以为"是否参与 LLM 给出修改建议"，
        # 改名"参与元数据建议"（语义：是否纳入 LLM 元数据建议流程）。
        # 「设置 → 字段」对话框里没有这个混淆问题，沿用原列名不动。
        self.tbl_existing.setHorizontalHeaderLabels(
            ["字段名", "类型", "显示", "参与元数据建议", "LLM 提示"]
        )
        _h_suggest = self.tbl_existing.horizontalHeaderItem(3)
        if _h_suggest is not None:
            _h_suggest.setToolTip(
                "勾选后，给项目让 LLM 提取元数据时会参考这个字段。\n"
                "（与「设置 → 字段 → 元数据建议」列含义一致；）\n"
                "与本助手预览页的字段方案建议是两件事。"
            )
        self.tbl_existing.verticalHeader().setVisible(False)
        self.tbl_existing.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_existing.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_existing.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_existing.setShowGrid(False)
        self.tbl_existing.setAlternatingRowColors(True)
        h = self.tbl_existing.horizontalHeader()
        # task #22 round 5：字段名列宽缩到原来一半（Stretch → Interactive 90），
        # LLM 提示列改 Stretch 接管剩余宽度（原 Fixed 96 太窄）
        h.setSectionResizeMode(0, QHeaderView.Interactive)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.Fixed)
        h.setSectionResizeMode(3, QHeaderView.Fixed)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl_existing.setColumnWidth(0, 90)
        self.tbl_existing.setColumnWidth(1, 160)
        self.tbl_existing.setColumnWidth(2, 56)
        # 标题加长后给"参与元数据建议"列多腾点宽（84 → 130 容下中文）
        self.tbl_existing.setColumnWidth(3, 130)
        self.tbl_existing.verticalHeader().setDefaultSectionSize(36)
        v.addWidget(self.tbl_existing, 1)

        # 字段操作按钮（和设置面板一致）
        ops = QHBoxLayout()
        b_add = QPushButton("＋ 添加")
        b_add.clicked.connect(self._existing_field_add)
        b_rename = QPushButton("✎ 重命名")
        b_rename.clicked.connect(self._existing_field_rename)
        b_del = QPushButton("🗑 删除")
        b_del.setProperty("danger", True)
        b_del.clicked.connect(self._existing_field_delete)
        b_up = QPushButton("↑ 上移")
        b_up.clicked.connect(lambda: self._existing_field_move(-1))
        b_down = QPushButton("↓ 下移")
        b_down.clicked.connect(lambda: self._existing_field_move(1))
        for b in (b_add, b_rename, b_del):
            ops.addWidget(b)
        ops.addStretch(1)
        ops.addWidget(b_up)
        ops.addWidget(b_down)
        v.addLayout(ops)

        # 底部：导航按钮
        btns = QHBoxLayout()
        b_back = QPushButton("← 上一步")
        b_back.clicked.connect(lambda: self.stack.setCurrentIndex(PAGE_INTRO))
        btns.addWidget(b_back)
        btns.addStretch(1)
        b_cancel = QPushButton("退出")
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_cancel)
        self.btn_call_llm = QPushButton("🚀 让 LLM 给出建议")
        self.btn_call_llm.setDefault(True)
        self.btn_call_llm.clicked.connect(self._on_first_call)
        btns.addWidget(self.btn_call_llm)
        v.addLayout(btns)
        return w

    def _build_running_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addStretch(1)
        lbl = QLabel("⏳ 正在调用 LLM，请稍候……")
        lbl.setAlignment(Qt.AlignCenter)
        f = QFont(); f.setPointSize(13)
        lbl.setFont(f)
        v.addWidget(lbl)
        self.lbl_running_mode = QLabel("")
        self.lbl_running_mode.setAlignment(Qt.AlignCenter)
        self.lbl_running_mode.setProperty("muted", True)
        v.addWidget(self.lbl_running_mode)
        v.addStretch(1)
        return w

    def _build_preview_page(self) -> QWidget:
        """task #21：Step 1 · 审阅 LLM 建议（原 _build_preview_page）。

        UI 上只展示 LLM 实际触达的条目；纯 user-only 的现有字段（``existing_user_field``
        且 LLM 没碰）由 Step 2 编辑表承担，不在 Step 1 出现。底部按钮砍掉
        ＋ / 删除 / 上下移（迁移到 Step 2），把"应用"换成"下一步 →"。
        """
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(8)

        self.lbl_preview_hint = QLabel(
            "<b>第 1 步 · 审阅 LLM 建议</b>　每条建议都可以「批准」或「驳回」；"
            "没点过的条目，下一步会按「批准」处理。这一步只针对 LLM 给的建议，"
            "你想自己加字段、删字段会在下一步进行。"
        )
        self.lbl_preview_hint.setTextFormat(Qt.RichText)
        self.lbl_preview_hint.setWordWrap(True)
        v.addWidget(self.lbl_preview_hint)

        # 库级描述（LLM 完善后的版本；用户可在此再编辑；含批准/驳回按钮）
        desc_row = QHBoxLayout()
        desc_row.setContentsMargins(0, 0, 0, 0)
        desc_row.setSpacing(6)
        self.lbl_desc_caption = QLabel("<b>库描述</b>")
        self.lbl_desc_caption.setTextFormat(Qt.RichText)
        desc_row.addWidget(self.lbl_desc_caption)
        # 决策状态标签（"已批准 / 已驳回"或空）
        self.lbl_desc_decision = QLabel("")
        self.lbl_desc_decision.setVisible(False)
        desc_row.addWidget(self.lbl_desc_decision)
        desc_row.addStretch(1)
        # 批准 / 驳回按钮（仅在 LLM 改过描述且 decision='pending' 时可见）
        self.btn_desc_approve = QPushButton("批准")
        self.btn_desc_approve.setMinimumWidth(46)
        self.btn_desc_approve.setFixedHeight(24)
        self.btn_desc_approve.setToolTip(
            "批准 LLM 完善后的库描述（仍可继续编辑）"
        )
        self.btn_desc_approve.clicked.connect(
            lambda _c=False: self._on_desc_decision_changed("approved")
        )
        self.btn_desc_approve.setVisible(False)
        desc_row.addWidget(self.btn_desc_approve)
        self.btn_desc_reject = QPushButton("驳回")
        self.btn_desc_reject.setMinimumWidth(46)
        self.btn_desc_reject.setFixedHeight(24)
        self.btn_desc_reject.setToolTip(
            "驳回（把库描述还原成你在场景页输入的原始版本）"
        )
        self.btn_desc_reject.clicked.connect(
            lambda _c=False: self._on_desc_decision_changed("rejected")
        )
        self.btn_desc_reject.setVisible(False)
        desc_row.addWidget(self.btn_desc_reject)
        v.addLayout(desc_row)
        self.ed_preview_library_desc = QPlainTextEdit()
        self.ed_preview_library_desc.setMaximumHeight(110)
        self.ed_preview_library_desc.setPlaceholderText(
            "（LLM 未给出库描述；可手动填写）"
        )
        # 用户手动编辑 → 决策回到 pending，按钮重新显示
        self.ed_preview_library_desc.textChanged.connect(self._on_desc_text_changed)
        v.addWidget(self.ed_preview_library_desc)

        # 字段表（5 列）
        # 列布局：LLM 建议 / 状态 / 字段名 / 类型 / LLM 提示
        # task #21 起此表只承载 LLM 实际触达的条目（Step 1）；纯 user-only 现有
        # 字段移到 Step 2 编辑表
        self.tbl = QTableWidget(0, 5)
        # task #22：列名重排——第 0 列实际是操作（批准/驳回按钮），
        # 第 1 列才是 LLM 建议的内容描述（"新增/删除/修改..."）
        self.tbl.setHorizontalHeaderLabels(
            ["操作", "LLM 建议", "字段名", "类型", "LLM 提示"]
        )
        self.tbl.verticalHeader().setVisible(False)
        # task #22 round 1：行高从 38 增加到 46（+20%），让 LLM 建议列的副标题
        # （rename + 被吞类型场景）和动作描述都更清晰
        self.tbl.verticalHeader().setDefaultSectionSize(46)
        self.tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl.setSelectionMode(QTableWidget.SingleSelection)
        h = self.tbl.horizontalHeader()
        # task #22 round 1：所有列都改 Interactive 让用户可拖动调宽。
        # 默认宽度比例：操作 144（原 216 × 2/3，按钮做扁后够用） /
        # LLM 建议 360（原 ResizeToContents，现给 ×2 ≈ 360） /
        # 字段名 90（原 180 / 2） / 类型 ResizeToContents 让 ComboBox 自适应 /
        # LLM 提示用 Stretch 占满剩余
        h.setSectionResizeMode(0, QHeaderView.Interactive)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.Interactive)
        h.setSectionResizeMode(3, QHeaderView.Interactive)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl.setColumnWidth(0, 144)
        # task #22 round 5：LLM 建议列宽减半（360 → 180），把腾出来的空间
        # 让给类型列（默认 Qt 100 → 150，+50%）和 LLM 提示列（Stretch 自动接管剩余）
        self.tbl.setColumnWidth(1, 180)
        self.tbl.setColumnWidth(2, 90)
        self.tbl.setColumnWidth(3, 150)
        # 双击编辑器：字段名走 LineEdit（撑满 cell）；LLM 提示双击弹独立多行对话框
        self._name_delegate = _TallLineEditDelegate(self.tbl)
        self.tbl.setItemDelegateForColumn(2, self._name_delegate)
        self.tbl.cellDoubleClicked.connect(self._on_cell_double_clicked)
        v.addWidget(self.tbl, 1)

        # 警告区
        self.lbl_warnings = QLabel("")
        self.lbl_warnings.setWordWrap(True)
        self.lbl_warnings.setStyleSheet("color: #f57c00;")
        self.lbl_warnings.setVisible(False)
        v.addWidget(self.lbl_warnings)

        # LLM 原始响应：改成弹窗（按钮触发），节省预览页的垂直空间
        self.btn_show_raw = QPushButton("📄 查看 LLM 原始回复...")
        self.btn_show_raw.setToolTip(
            "弹出窗口显示本轮 LLM 给的原始回复内容；"
            "窗口里可以一键「再次应用 LLM 建议」"
        )
        self.btn_show_raw.clicked.connect(self._on_show_raw_dialog)
        v.addWidget(self.btn_show_raw)

        # 底部按钮区（task #21 改造：去掉 ＋/🗑/↑↓/应用，换成"下一步 →"）
        btns = QHBoxLayout()
        self.btn_restart = QPushButton("🔄 重新开始")
        self.btn_restart.setToolTip("清空全部状态，回到场景描述页（轮数归零）")
        self.btn_restart.clicked.connect(self._on_restart)
        btns.addWidget(self.btn_restart)

        self.btn_refine = QPushButton("✏ 在当前基础上调整...")
        self.btn_refine.setToolTip(
            "保留你已经做过的批准/驳回和编辑过的提示语，再补充一段说明，"
            "让 LLM 在此基础上重新给一版建议。"
        )
        self.btn_refine.clicked.connect(self._on_refine)
        btns.addWidget(self.btn_refine)

        # 一键批准所有未决策项
        self.btn_approve_all = QPushButton("✓ 全部批准未决策项")
        self.btn_approve_all.setToolTip(
            "把当前所有还没点过批准 / 驳回的 LLM 建议一次性标为「已批准」；"
            "已经驳回的不会被改回来。"
        )
        self.btn_approve_all.clicked.connect(self._on_approve_all)
        btns.addWidget(self.btn_approve_all)

        btns.addStretch(1)
        b_cancel = QPushButton("退出")
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_cancel)

        # task #21：把"应用"换成"下一步 →"；点击进入 Step 2
        self.btn_step1_next = QPushButton("下一步（自动批准未决策）")
        self.btn_step1_next.setDefault(True)
        self.btn_step1_next.setToolTip(
            "把当前批准/驳回决策合并成最终字段表草稿，进入下一步进一步编辑"
            "（增/删/改名/改类型/改提示）"
        )
        self.btn_step1_next.clicked.connect(self._on_step1_next)
        btns.addWidget(self.btn_step1_next)
        v.addLayout(btns)
        return w

    # ------------------------------------------------------------------
    # task #21：Step 2 · 字段表编辑（最终态）
    # ------------------------------------------------------------------
    def _build_step2_page(self) -> QWidget:
        """Step 2 视图：把 Step 1 决策合并后的最终字段表呈现给用户编辑。

        渲染 ``self._drafts``（``list[FieldDraft]``）；每行可改名 / 改类型 /
        改提示 / 删除；底部 ＋ 添加字段、← 放弃修改并返回、应用、应用并继续讨论。
        """
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(8)

        self.lbl_step2_hint = QLabel(
            "<b>第 2 步 · 编辑最终字段表</b>　这是合并完上一步决策后的最终字段表。"
            "你可以在这里增加、删除、改名、改类型、改提示。打了删除线的行表示"
            "「将删除」，点右边的「↩ 撤销删除」可以恢复。点「应用」一次性写入库。"
        )
        self.lbl_step2_hint.setTextFormat(Qt.RichText)
        self.lbl_step2_hint.setWordWrap(True)
        v.addWidget(self.lbl_step2_hint)

        # 字段表（5 列：状态徽章 / 字段名 / 类型 / LLM 提示 / 操作）
        # task #22：原"状态"列（第 5 列）已删——划删线视觉 + 操作列的"撤销
        # 删除"按钮 + 字段名前缀已经够指示删除态，原状态列文字纯属冗余
        # task #22 round 10：第 0 列标题"来源"→"状态"（用户视角：徽章
        # 显示的是"现有 / LLM 新建 / 改名 / 改类型 / 将删除"等状态，不是
        # 数据来源；"来源"是开发者视角的 origin 字段命名残留）
        self.tbl_step2 = QTableWidget(0, 5)
        self.tbl_step2.setHorizontalHeaderLabels(
            ["状态", "字段名", "类型", "LLM 提示", "操作"]
        )
        self.tbl_step2.verticalHeader().setVisible(False)
        # task #22 round 3：行高 34 → 48（+40%），与 Step 1 的视觉节奏对齐
        self.tbl_step2.verticalHeader().setDefaultSectionSize(48)
        self.tbl_step2.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_step2.setSelectionMode(QTableWidget.SingleSelection)
        h2 = self.tbl_step2.horizontalHeader()
        h2.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h2.setSectionResizeMode(1, QHeaderView.Interactive)
        h2.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h2.setSectionResizeMode(3, QHeaderView.Stretch)
        # task #22 round 10：操作列从 ResizeToContents 改成 Interactive + 显式
        # 列宽，让 "↩ 撤销删除" 按钮两侧多留 ~50% 的可拖动空间，避免按钮文字
        # 紧贴边框、点击体验拥挤
        h2.setSectionResizeMode(4, QHeaderView.Interactive)
        # task #22 round 3：字段名列宽 180 → 120（×2/3），把空间留给 LLM 提示列
        self.tbl_step2.setColumnWidth(1, 120)
        self.tbl_step2.setColumnWidth(4, 160)
        # 字段名列复用 Step 1 的高度撑满 delegate
        self._name_delegate_step2 = _TallLineEditDelegate(self.tbl_step2)
        self.tbl_step2.setItemDelegateForColumn(1, self._name_delegate_step2)
        self.tbl_step2.cellDoubleClicked.connect(self._on_step2_cell_double_clicked)
        self.tbl_step2.itemChanged.connect(self._on_step2_item_changed)
        v.addWidget(self.tbl_step2, 1)

        # 行操作按钮
        ops = QHBoxLayout()
        self.btn_step2_add = QPushButton("＋ 添加字段")
        self.btn_step2_add.setToolTip(
            "在表末尾追加一个 user_new 行；填入名字后随其他变更一起应用"
        )
        self.btn_step2_add.clicked.connect(self._on_step2_add_field)
        ops.addWidget(self.btn_step2_add)
        ops.addStretch(1)
        self.btn_step2_up = QPushButton("↑ 上移")
        self.btn_step2_up.setToolTip("把选中行上移一位")
        self.btn_step2_up.clicked.connect(lambda: self._on_step2_move(-1))
        ops.addWidget(self.btn_step2_up)
        self.btn_step2_down = QPushButton("↓ 下移")
        self.btn_step2_down.setToolTip("把选中行下移一位")
        self.btn_step2_down.clicked.connect(lambda: self._on_step2_move(1))
        ops.addWidget(self.btn_step2_down)
        v.addLayout(ops)

        # 警告区（重名 / 必填等校验失败时显示）
        self.lbl_step2_warnings = QLabel("")
        self.lbl_step2_warnings.setWordWrap(True)
        self.lbl_step2_warnings.setStyleSheet("color: #c62828;")
        self.lbl_step2_warnings.setVisible(False)
        v.addWidget(self.lbl_step2_warnings)

        # 底部按钮区
        btns = QHBoxLayout()
        self.btn_step2_back = QPushButton("← 放弃修改并返回")
        self.btn_step2_back.setToolTip(
            "丢弃当前在字段表里的编辑，返回上一步重新审阅；上一步的批准/驳回会保留"
        )
        self.btn_step2_back.clicked.connect(self._on_step2_back)
        btns.addWidget(self.btn_step2_back)

        btns.addStretch(1)

        b_cancel = QPushButton("退出")
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_cancel)

        self.btn_step2_apply_continue = QPushButton("💾 应用并继续讨论...")
        self.btn_step2_apply_continue.setToolTip(
            "先把当前字段表保存到库，再弹出补充说明开启新一轮和 LLM 的讨论；"
            "下一轮 LLM 看到的现有字段就是你刚保存的版本"
        )
        self.btn_step2_apply_continue.clicked.connect(
            lambda: self._on_step2_apply(continue_refine=True)
        )
        btns.addWidget(self.btn_step2_apply_continue)

        self.btn_step2_apply = QPushButton("✅ 应用")
        self.btn_step2_apply.setDefault(True)
        self.btn_step2_apply.setToolTip(
            "把当前字段表写入库；含改类型 / 删除时还会有二次确认对话框"
        )
        self.btn_step2_apply.clicked.connect(
            lambda: self._on_step2_apply(continue_refine=False)
        )
        btns.addWidget(self.btn_step2_apply)
        v.addLayout(btns)
        return w

    # ---- 状态管理 ----------------------------------------------------------
    def _refresh_round_label(self) -> None:
        self.lbl_round.setText(f"轮数 {self._current_round} / {self._max_rounds}")
        # 用量标签：累计值 + 上一轮增量（>0 才显示）
        tokens_text = (
            f"用量：累计 {self._tokens_in_total} 输入 / "
            f"{self._tokens_out_total} 输出"
        )
        if self._last_round_tokens_in or self._last_round_tokens_out:
            tokens_text += (
                f"（本轮 +{self._last_round_tokens_in}"
                f" / +{self._last_round_tokens_out}）"
            )
        self.lbl_tokens.setText(tokens_text)
        # 在预览页根据上限禁用 refine 按钮
        if hasattr(self, "btn_refine"):
            at_limit = self._current_round >= self._max_rounds
            self.btn_refine.setEnabled(not at_limit)
            if at_limit:
                self.btn_refine.setToolTip(
                    f"已达 {self._current_round}/{self._max_rounds} 轮，"
                    "请『重新开始』或采用当前结果"
                )

    def _on_show_raw_dialog(self) -> None:
        """弹出窗口展示 LLM 原始响应；窗口内含「再次应用 LLM 建议」按钮。"""
        if not self._last_raw_response:
            info(
                self, "暂无内容",
                "本轮还没有 LLM 回复内容可供查看。",
            )
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("LLM 原始回复（本轮）")
        dlg.resize(720, 480)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)

        info = QLabel(
            "以下是本轮 LLM 返回的**原始内容**。如果你之前误驳回了某条建议，"
            "可以点下方「🔄 再次应用 LLM 建议」按钮——所有 LLM 改过的条目都会"
            "重置为 LLM 给出的版本（决策清空，类型 / 提示还原），可以重新审阅。"
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.RichText)
        v.addWidget(info)

        ed = QTextEdit()
        ed.setReadOnly(True)
        ed.setPlainText(self._last_raw_response)
        ed.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")
        v.addWidget(ed, 1)

        btns = QHBoxLayout()
        b_reapply = QPushButton("🔄 再次应用 LLM 建议")
        b_reapply.setToolTip("把 LLM 改过的字段重置回 LLM 给的版本；你自己加的、删的字段保持不动")
        if self._llm_round_payload is None:
            b_reapply.setEnabled(False)
            b_reapply.setToolTip("找不到本轮 LLM 建议的存档，无法再次应用")
        b_reapply.clicked.connect(lambda _c=False: self._on_reapply_llm(dlg))
        btns.addWidget(b_reapply)
        btns.addStretch(1)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(dlg.accept)
        btns.addWidget(b_close)
        v.addLayout(btns)
        dlg.exec()

    def _on_reapply_llm(self, parent_dlg: Optional[QDialog] = None) -> None:
        """智能合并：把 LLM 那一轮的建议重新覆盖到当前预览。

        合并规则：
        * 用户手加的字段（``llm_touched=False`` 的 ``new``）→ **保留**
        * 用户标记删除的现有字段（``existing_user_field`` + ``selected=False``）→ **保留删除标记**
        * LLM 触达过的字段 → 全部重置为 LLM 那一轮的建议（清掉 decision，重置 type/hint/rename_to）
        * **重名冲突**：用户手加的字段名与 LLM 给出的 ``new`` 名字相同 →
          弹对话框让用户挑一个（保留用户版 / 用 LLM 版覆盖）

        库描述：同步用 LLM 那一轮给出的 ``library_description`` 覆盖（用户在预览
        页编辑过的描述会丢失）— 这是"重置"的明确语义。
        """
        if self._llm_round_payload is None:
            return

        # 收集"用户私有"条目（要保留的）
        user_added: list[AnnotatedSuggestion] = []
        user_deleted_existing: list[AnnotatedSuggestion] = []
        for ann in self._suggestions:
            if ann.status == "new" and not ann.llm_touched:
                user_added.append(ann)
            elif ann.status == "existing_user_field" and not ann.selected:
                user_deleted_existing.append(ann)

        # 重新走一遍 annotate_conflicts（基于 LLM 看到的当时现状）
        reapply_warnings: list[str] = []
        fresh = annotate_conflicts(
            self._llm_round_payload["fields"],
            self._llm_round_existing,
            suggested_deletes=self._llm_round_payload.get("fields_to_delete"),
            suggested_renames=self._llm_round_payload.get("fields_to_rename"),
            out_warnings=reapply_warnings,
        )

        # 重名冲突检测：fresh 里 status='new' 的名字 vs 用户手加
        fresh_new_names = {a.name for a in fresh if a.status == "new"}
        conflicts: list[AnnotatedSuggestion] = [
            ua for ua in user_added if ua.name in fresh_new_names
        ]
        keep_user_for: set[str] = set()  # 用户选择保留自己版本的字段名
        if conflicts:
            for ua in conflicts:
                # LLM 在 fresh 里同名条目（一定存在）
                llm_ann = next(a for a in fresh if a.name == ua.name)
                if confirm(
                    self, "字段名重复",
                    f"字段名「{ua.name}」既被你手加过，也是 LLM 这一轮的新建议。\n\n"
                    f"  • 你手加的：类型=<b>{ua.type}</b>，"
                    f"提示=<i>{(ua.prompt_hint or '（空）')[:60]}</i>\n"
                    f"  • LLM 的：类型=<b>{llm_ann.type}</b>，"
                    f"提示=<i>{(llm_ann.prompt_hint or '（空）')[:60]}</i>\n\n"
                    "请选择保留哪个版本：",
                    yes="保留我的版本", no="用 LLM 的版本",
                    default_yes=True,
                ):
                    keep_user_for.add(ua.name)

        # 应用合并：
        # 1) fresh 中被"用户保留自己版"占用的 LLM new 条目去掉
        merged: list[AnnotatedSuggestion] = [
            a for a in fresh
            if not (a.status == "new" and a.name in keep_user_for)
        ]
        # 2) 用户手加：未冲突的全部追加；冲突的仅保留 keep_user_for 内的
        for ua in user_added:
            if ua.name in fresh_new_names and ua.name not in keep_user_for:
                # 用户选择被 LLM 覆盖；不追加
                continue
            if ua.name in fresh_new_names and ua.name in keep_user_for:
                # 用户选择保留自己版；追加自己原 ann
                merged.append(ua)
                continue
            merged.append(ua)
        # 3) 用户标记删除的现有字段：把"删除标记"应用到 merged 里对应 existing_user_field 上
        deleted_names = {a.name for a in user_deleted_existing}
        for a in merged:
            if a.status == "existing_user_field" and a.name in deleted_names:
                a.selected = False  # 沿用用户的删除决定

        # 库描述：用 LLM 那一轮的建议覆盖（明确"重置"语义）
        new_desc = (self._llm_round_payload.get("library_description") or "").strip()
        if new_desc:
            self._library_desc_suggested = new_desc
        # 决策回到 pending（与字段 ann 重置语义一致）
        self._library_desc_decision = "pending"

        self._suggestions = merged
        self._render_preview(reapply_warnings)
        if parent_dlg is not None:
            parent_dlg.accept()

    def _on_cell_double_clicked(self, row: int, col: int) -> None:
        """LLM 提示列双击 → 弹独立多行编辑对话框。``row`` 是 Step 1 渲染行号。"""
        if col != 4:  # LLM 提示列（5 列布局）
            return
        if not (0 <= row < len(self._step1_visible_rows)):
            return
        src_idx = self._step1_visible_rows[row]
        if not (0 <= src_idx < len(self._suggestions)):
            return
        ann = self._suggestions[src_idx]
        it = self.tbl.item(row, 4)
        cur = it.text() if it else ann.prompt_hint
        title = f"编辑 LLM 提示 — {ann.name}"
        # task #21 阶段 B：如果是 LLM 触达过的现有字段（含 hint 未改的），
        # 都展示"LLM 建议前的原提示"参考区——便于用户理解 LLM 改了什么、
        # 或显式确认"本次 LLM 没改"
        ref_text = ""
        ref_label = ""
        is_existing_touched = ann.status in (
            "same_type", "system_required", "type_conflict",
            "llm_suggest_rename", "llm_suggest_delete",
        )
        if is_existing_touched:
            existing_hint = ann.existing_prompt_hint or ""
            new_hint = ann.prompt_hint or ""
            if existing_hint != new_hint:
                # LLM 改了 hint：显示原 hint 作对照
                ref_text = existing_hint or "（原本为空）"
                ref_label = "<b>LLM 建议前的原提示（只读，供参考）：</b>"
            elif existing_hint:
                # LLM 未改 hint 且原 hint 非空：标签明确"本次没修改"
                ref_text = existing_hint
                ref_label = (
                    "<b>原提示（本次 LLM 未修改此提示，仅供参考）：</b>"
                )
            # else: 双方都空 → 没什么可参考的，不显示参考区
        new_text, ok = _ask_text(
            self, title,
            "请输入该字段的 LLM 提示（多行；告诉 LLM 这个字段的格式要求、示例等）：",
            initial=cur,
            reference_label=ref_label,
            reference_text=ref_text,
        )
        if not ok:
            return
        if it is None:
            it = QTableWidgetItem(new_text)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.tbl.setItem(row, 4, it)
        else:
            it.setText(new_text)
        ann.prompt_hint = new_text
        # 用户手动编辑了 hint，相当于"接受了一个新版本"；
        # 如果之前是 rejected，提升为 pending 让用户再决定
        if ann.decision == "rejected":
            ann.decision = "pending"
        # task #22 round 3：整表重画——第 1 列「LLM 建议」文案是
        # step1_action_label 按 changed_dimensions / decision 算出来的，
        # hint 编辑后 decision / hint 维度可能都变了，单刷第 0 列不够。
        self._render_preview([])

    # ---- 现有字段编辑面板（场景页内嵌；行为对齐"设置 → 字段"） ------------
    def _reload_existing_fields_table(self) -> None:
        """把当前库字段填入场景页的字段编辑表。"""
        if self.repo is None:
            self.tbl_existing.setRowCount(0)
            return
        fields = self.repo.list_fields()
        self.tbl_existing.blockSignals(True)
        self.tbl_existing.setRowCount(len(fields))
        for r, f in enumerate(fields):
            # 受保护字段标"必有"；标题字段已经叫"标题"了，括号里不再复读
            tag_suffix = "  (必有)" if f.is_required else ""
            display_name = f.name + tag_suffix
            it_name = QTableWidgetItem(display_name)
            it_name.setData(Qt.UserRole, f.id)
            self.tbl_existing.setItem(r, 0, it_name)

            # 类型 ComboBox
            cmb = QComboBox()
            cmb.setMinimumWidth(140)
            cmb.setMinimumHeight(28)
            cmb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            for t in FIELD_TYPES:
                cmb.addItem(FIELD_TYPE_LABELS.get(t, t), t)
            ftype = f.type or "text"
            if cmb.findData(ftype) < 0:
                cmb.addItem(FIELD_TYPE_LABELS.get(ftype, ftype), ftype)
            cmb.setCurrentIndex(max(0, cmb.findData(ftype)))
            if f.is_required:
                cmb.setEnabled(False)
                cmb.setToolTip(f"『{f.name}』字段类型固定，不可修改")
            cmb.currentIndexChanged.connect(
                lambda _i, fid=f.id, box=cmb: self._existing_field_change_type(
                    fid, box.currentData(),
                )
            )
            self.tbl_existing.setCellWidget(r, 1, cmb)

            # 显示
            cb = QCheckBox()
            cb.setChecked(f.visible)
            if f.is_title:
                cb.setEnabled(False)
                cb.setToolTip("标题字段必显")
            cb.stateChanged.connect(
                lambda _s, fid=f.id, box=cb:
                    self._existing_field_toggle_visible(fid, box.isChecked())
            )
            self._wrap_cell(self.tbl_existing, r, 2, cb)

            # LLM 建议
            cb_sug = QCheckBox()
            cb_sug.setChecked(f.suggest_enabled)
            cb_sug.stateChanged.connect(
                lambda _s, fid=f.id, box=cb_sug:
                    self._existing_field_toggle_suggest(fid, box.isChecked())
            )
            self._wrap_cell(self.tbl_existing, r, 3, cb_sug)

            # LLM 提示按钮
            btn_hint = QPushButton(
                "📝 已设置" if (f.prompt_hint or "").strip() else "✎ 编辑…"
            )
            btn_hint.setFlat(True)
            if (f.prompt_hint or "").strip():
                btn_hint.setToolTip(
                    "当前提示：\n"
                    + (f.prompt_hint[:300] + ("…" if len(f.prompt_hint) > 300 else ""))
                )
            else:
                btn_hint.setToolTip("点击编辑该字段的 LLM 提示（留空使用默认）")
            btn_hint.clicked.connect(
                lambda _checked=False, fid=f.id, name=f.name,
                       hint=f.prompt_hint or "":
                    self._existing_field_edit_prompt_hint(fid, name, hint)
            )
            self.tbl_existing.setCellWidget(r, 4, btn_hint)
        self.tbl_existing.blockSignals(False)

    def _existing_current_field_id(self) -> int | None:
        r = self.tbl_existing.currentRow()
        if r < 0:
            return None
        it = self.tbl_existing.item(r, 0)
        return it.data(Qt.UserRole) if it else None

    def _existing_field_add(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        # 简化实现：单行输入字段名 + 默认 type=text；后续可改类型
        name, ok = QInputDialog.getText(
            self, "新建字段", "字段名（默认类型：单行文本，可在表格中改）：",
        )
        if not ok or not name.strip():
            return
        try:
            self.repo.add_field(name.strip(), "text")
        except Exception as e:  # noqa: BLE001
            warn(self, "失败", str(e))
            return
        self._reload_existing_fields_table()

    def _existing_field_rename(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        fid = self._existing_current_field_id()
        if fid is None:
            return
        f = self.repo.get_field(fid)
        if not f:
            return
        new_name, ok = QInputDialog.getText(
            self, "重命名", "新字段名：", text=f.name,
        )
        if not ok or not new_name.strip() or new_name.strip() == f.name:
            return
        try:
            self.repo.rename_field(fid, new_name.strip())
        except Exception as e:  # noqa: BLE001
            warn(self, "失败", str(e))
            return
        self._reload_existing_fields_table()

    def _existing_field_toggle_visible(self, fid: int, visible: bool) -> None:
        self.repo.set_field_visible(fid, visible)

    def _existing_field_toggle_suggest(self, fid: int, enabled: bool) -> None:
        self.repo.set_field_suggest_enabled(fid, enabled)

    def _existing_field_change_type(self, fid: int, ftype: str) -> None:
        """字段助手「现有字段」表里手动改类型 — 复用库设置同款护栏
        （task #19 Phase A）。"""
        from ...models import is_compatible_type_change
        from ..settings.field_dialogs import _FieldTypeChangeConfirmDialog

        f = self.repo.get_field(fid)
        if f is None or f.type == ftype:
            return

        try:
            if is_compatible_type_change(f.type, ftype):
                self.repo.set_field_type(fid, ftype)
                self._reload_existing_fields_table()
                return

            # n_values 必须走 repo.count_field_filled（系统字段读 projects 列、
            # 用户字段读 project_field_values）；只查 project_field_values
            # 会让系统字段永远算 0、误走静默路径
            n_values = self.repo.count_field_filled(f)
            m_pending = self.repo.count_pending_suggestions_for_field(fid)
            if (
                int(n_values) == 0 and int(m_pending) == 0
                and not (f.prompt_hint or "").strip()
            ):
                self.repo.set_field_type(fid, ftype)
                self._reload_existing_fields_table()
                return

            confirmed, clear_hint = _FieldTypeChangeConfirmDialog.ask(
                self, f, ftype, int(n_values), int(m_pending),
            )
            if not confirmed:
                self._reload_existing_fields_table()
                return

            self.repo.set_field_type(
                fid, ftype,
                supersede_pending_suggestions=(int(m_pending) > 0),
                clear_prompt_hint=clear_hint,
            )
        except Exception as e:  # noqa: BLE001
            warn(self, "失败", str(e))
        self._reload_existing_fields_table()

    def _existing_field_move(self, delta: int) -> None:
        r = self.tbl_existing.currentRow()
        if r < 0:
            return
        target = r + delta
        if target < 0 or target >= self.tbl_existing.rowCount():
            return
        ids: list[int] = []
        for i in range(self.tbl_existing.rowCount()):
            it = self.tbl_existing.item(i, 0)
            if it:
                ids.append(it.data(Qt.UserRole))
        ids[r], ids[target] = ids[target], ids[r]
        self.repo.reorder_fields(ids)
        self._reload_existing_fields_table()
        self.tbl_existing.setCurrentCell(target, 0)

    def _existing_field_delete(self) -> None:
        from ..settings.field_dialogs import _DeleteFieldChoiceDialog

        fid = self._existing_current_field_id()
        if fid is None:
            return
        f = self.repo.get_field(fid)
        if not f:
            return
        if f.is_required:
            info(self, "提示", f"『{f.name}』字段不可删除。")
            return
        cnt = self.repo.count_field_filled(f)
        dlg = _DeleteFieldChoiceDialog(f.name, cnt, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.repo.delete_field(fid, append_to_description=dlg.append_to_desc)
        except Exception as e:  # noqa: BLE001
            warn(self, "失败", str(e))
            return
        self._reload_existing_fields_table()

    def _existing_field_edit_prompt_hint(
        self, fid: int, name: str, current_hint: str,
    ) -> None:
        new_text, ok = _ask_text(
            self, f"LLM 提示 — {name}",
            f"为字段「{name}」自定义 LLM 建议时的格式说明。\n"
            "留空 = 使用默认；填写 = 让 LLM 提取这个字段时按这里的要求来。",
            initial=current_hint or "",
        )
        if not ok:
            return
        self.repo.set_field_prompt_hint(fid, new_text.strip())
        self._reload_existing_fields_table()

    # ---- 调用 LLM ----------------------------------------------------------
    def _on_first_call(self) -> None:
        text = self.ed_scenario.toPlainText().strip()
        if not text:
            warn(
                self, "请填写库描述",
                "请先描述这个库的目的与字段偏好。",
            )
            return
        # 合并语义后只有一个来源：用户输入即"库描述 / 使用场景"
        self._scenario_text = text
        self._library_desc_input = text
        self._history = []
        self._dispatch_call(extra="")

    def reject(self) -> None:
        """task #22 round 14：用户点"退出"按钮 / 按 Esc / 关右上角 X 都走
        这里。如果用户已经投入过内容（写了场景描述、调过 LLM、或在
        Step 2 改过字段表），弹一次确认；什么都没做的状态直接走默认 reject。
        """
        # 判据：用户已投入内容 = 场景框非空 / 已调过 LLM / Step 2 已编辑过
        scenario_text = ""
        try:
            scenario_text = self.ed_scenario.toPlainText().strip()
        except Exception:  # noqa: BLE001
            pass
        has_invested = bool(
            scenario_text or self._suggestions or self._last_raw_response
            or self._history
        )
        if has_invested:
            ret = confirm(
                self,
                "确认退出库字段设计助手",
                "退出会丢弃当前的场景描述、所有 LLM 建议与你的批准/驳回"
                "决策。\n\n"
                "确定要退出吗？",
                yes="退出", danger=True,
            )
            if not ret:
                return
        super().reject()

    def _on_restart(self) -> None:
        # 仅当已经至少调过一轮 LLM（有建议或原始回复在手）时才弹确认；
        # 没建议时点重新开始无破坏性，直接回场景页。
        if self._suggestions or self._last_raw_response:
            if not confirm(
                self,
                "确认重新开始",
                "重新开始会丢弃当前这一轮 LLM 给出的所有建议（包括你已经做过的"
                "批准 / 驳回决策），回到场景描述页重新写需求。\n\n"
                "如果只是想撤销之前的决策，可以点「📄 查看 LLM 原始回复」"
                "里的「🔄 再次应用 LLM 建议」，会把所有 LLM 触达过的字段重置为"
                "本轮 LLM 给出的版本。\n\n"
                "确认重新开始？",
                yes="重新开始", danger=True,
            ):
                return
        self._history = []
        self._suggestions = []
        self._llm_round_payload = None
        self._llm_round_existing = []
        self._last_raw_response = ""
        self._current_round = 0
        # 库描述：清掉 LLM 这一轮的建议与决策（用户在场景页输入的描述保留）
        self._library_desc_suggested = ""
        self._library_desc_decision = "pending"
        # 累计 token 保留（已经消耗过的就是花掉了）；只清掉"本轮增量"
        self._last_round_tokens_in = 0
        self._last_round_tokens_out = 0
        self._refresh_round_label()
        # 同步刷新场景页字段表（用户可能在前一轮"应用"中修改过库）
        self._reload_existing_fields_table()
        self.stack.setCurrentIndex(PAGE_SCENARIO)

    def _on_refine(self) -> None:
        if self._current_round >= self._max_rounds:
            info(
                self, "已达上限",
                f"已达 {self._current_round}/{self._max_rounds} 轮，"
                "请『重新开始』或采用当前结果。",
            )
            return
        # 用户先把当前编辑结果回灌到 history（便于 LLM 看到"用户改过的版本"）
        edited = self._collect_user_edited_payload()
        self._history.append({"content": json.dumps(edited, ensure_ascii=False)})

        text, ok = _ask_text(
            self, "补充说明",
            "请说明希望在上次结果基础上做什么调整：",
        )
        if not ok or not text.strip():
            # 取消则把刚加的 history 回滚
            self._history.pop()
            return
        self._dispatch_call(extra=text.strip())

    def _dispatch_call(self, extra: str) -> None:
        if self.repo is None:
            return
        cfg = load_config(self.repo)
        active = cfg.active()
        if active is None or not active.api_key:
            warn(
                self, "未配置 API Key",
                "请先到「设置 → API」配置默认 LLM 平台的 API Key。",
            )
            return
        try:
            provider = get_provider(active)
        except Exception as e:  # noqa: BLE001
            error(self, "LLM 平台连接失败", str(e))
            return

        use_json_mode = bool(getattr(provider, "supports_json_mode", True))
        # task #22 round 3：等待页只显示用户认得的"平台 · 模型"，不再暴露
        # JSON 原生 / Prompt 强约束 这种内部路由模式
        self.lbl_running_mode.setText(
            f"{active.label()} · 模型 {active.model}"
        )
        self.stack.setCurrentIndex(PAGE_RUNNING)

        # 库描述：第二轮起以 LLM 已建议过的版本为基线（用户在预览页若编辑了
        # ed_preview_library_desc，会被 _collect_user_edited_payload 同步到该字段）；
        # 第一轮场景文本本身 == 库描述（合并语义），给 build_messages 时去重以免冗余
        if self._library_desc_suggested:
            baseline_desc = self._library_desc_suggested
        elif self._library_desc_input == self._scenario_text:
            baseline_desc = ""  # 第一轮：场景描述本身就是库描述，不再单独再发一次
        else:
            baseline_desc = self._library_desc_input
        # 现有字段：把每个字段紧凑序列化，LLM 据此给修订建议
        current_fields = [
            {"name": f.name, "type": f.type, "prompt_hint": f.prompt_hint or ""}
            for f in (self.repo.list_fields() if self.repo else [])
        ]
        messages = build_messages(
            self._scenario_text, self._history, extra,
            library_description=baseline_desc,
            current_fields=current_fields,
        )

        # 启动后台线程
        self._thread = QThread(self)
        self._worker = _WizardLLMWorker(provider, messages, use_json_mode)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_llm_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_llm_finished(
        self, payload, raw_text: str, warnings: list,
        tokens_in: int = 0, tokens_out: int = 0,
    ) -> None:
        self._current_round += 1
        # 累计 token（无论成功失败都计入；失败时 provider 仍可能消耗了 in tokens）
        self._last_round_tokens_in = tokens_in
        self._last_round_tokens_out = tokens_out
        self._tokens_in_total += tokens_in
        self._tokens_out_total += tokens_out
        self._refresh_round_label()
        if payload is None:
            # 失败：留在预览页（如果之前已经有结果）或回到场景页
            error(self, "LLM 调用失败", raw_text)
            if self._suggestions:
                self.stack.setCurrentIndex(PAGE_PREVIEW)
            else:
                self.stack.setCurrentIndex(PAGE_SCENARIO)
            return
        self._last_raw_response = raw_text
        # 把本轮 model 输出加入历史（用于下一轮 refine）
        self._history.append({"content": raw_text})

        existing = self.repo.list_fields() if self.repo else []
        self._suggestions = annotate_conflicts(
            payload["fields"], existing,
            suggested_deletes=payload.get("fields_to_delete"),
            suggested_renames=payload.get("fields_to_rename"),
            out_warnings=warnings,
        )
        # 快照：保存 LLM 这一轮的原始 payload 与"当时的现有字段"
        # 之后用户驳回 / 手改 / 手加字段后，「再次应用 LLM 建议」可据此智能重建
        self._llm_round_payload = payload
        self._llm_round_existing = existing
        # LLM 给出的库描述（可能为空；保留之前的值以避免回退）
        new_desc = payload.get("library_description", "").strip()
        if new_desc:
            self._library_desc_suggested = new_desc
        # 新的一轮 → 库描述决策回到 pending（无论上一轮用户做过什么决定）
        self._library_desc_decision = "pending"

        self._render_preview(warnings)
        self.stack.setCurrentIndex(PAGE_PREVIEW)

    # ---- 预览页渲染 --------------------------------------------------------
    # task #22：原 _STATUS_DISPLAY 的"系统必有 / 现有 · 同类型 / 类型冲突 /
    # LLM 建议改名 / LLM 建议删除"等技术分类全部抹平，由 step1_action_label()
    # 统一输出"普通用户能直接读懂"的动作描述（新增 / 删除 / 修改 (...)）。

    def _render_preview(self, warnings: list[str]) -> None:
        # task #21：Step 1 只展示 LLM 实际触达的条目；纯 user-only 现有字段
        # （existing_user_field 状态）由 Step 2 编辑表承担，不在此显示
        # task #22：has_llm_change=False 的 ann（system_required / same_type
        # 在 LLM 没改 hint 时）也不出现，避免空动作行
        # task #22 round 12：保留滚动位置——用户点批准/驳回后表格全量
        # 重画（setRowCount(0)）会让 verticalScrollBar 复位到 0，长表格里
        # 用户下滑到中间点了一条建议，整个跳回顶部很难受。先记下当前滚动
        # 偏移，渲染完再恢复（行数若变少则被 Qt 自动 clamp 到合法范围）。
        vbar = self.tbl.verticalScrollBar()
        scroll_pos = vbar.value() if vbar is not None else 0
        self.tbl.setRowCount(0)
        self._step1_visible_rows = step1_visible_indices(self._suggestions)
        for row, src_idx in enumerate(self._step1_visible_rows):
            ann = self._suggestions[src_idx]
            self.tbl.insertRow(row)

            # 0：操作（批准/驳回按钮 + "已批准/已驳回" 标签；task #22 列名）
            self._make_change_cell(row, ann)

            # 1：LLM 建议（task #22：用 step1_action_label 给出"动作描述"）
            label_text, tooltip_text = step1_action_label(ann)
            # rejected 灰字；其它默认主文字色（rich text 里加 span 控制）
            if ann.decision == "rejected":
                label_html = f"<span style='color:#757575;'>{label_text}</span>"
            else:
                label_html = label_text
            lbl_action = QLabel(label_html)
            lbl_action.setTextFormat(Qt.RichText)
            lbl_action.setWordWrap(True)
            lbl_action.setContentsMargins(6, 2, 6, 2)
            lbl_action.setToolTip(tooltip_text or (ann.reason or ""))
            self.tbl.setCellWidget(row, 1, lbl_action)

            # 2：字段名（task #21：Step 1 全只读，所有改动迁移到 Step 2）
            # task #22 round 7：LLM 建议改名时显示**新名**（用户视角想看的是
            # "改成什么"，不是"原本叫啥"）；rejected 时回到旧名（与
            # _on_decision_changed 的"驳回 = 保留原名"语义一致）。
            # tooltip 里同时给出 "原名 → 新名" 完整信息。
            display_name = ann.name
            name_tooltip = ""
            if (
                ann.status == "llm_suggest_rename"
                and ann.llm_rename_new_name
                and ann.decision != DECISION_REJECTED
            ):
                display_name = ann.llm_rename_new_name
                name_tooltip = f"由「{ann.name}」改名而来（数据保留）"
            it_name = QTableWidgetItem(display_name)
            it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)
            if name_tooltip:
                it_name.setToolTip(name_tooltip)
            self.tbl.setItem(row, 2, it_name)

            # 3：类型（task #21：Step 1 全只读；改类型在 Step 2 进行）
            cmb = QComboBox()
            cmb.setMinimumHeight(28)
            cmb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            for t in FIELD_TYPES:
                cmb.addItem(FIELD_TYPE_LABELS.get(t, t), t)
            if ann.type == "tags":
                cmb.addItem(FIELD_TYPE_LABELS.get("tags", "标签（多值）"), "tags")
            idx_t = cmb.findData(ann.type)
            cmb.setCurrentIndex(max(0, idx_t))
            cmb.setEnabled(False)
            self.tbl.setCellWidget(row, 3, cmb)

            # 4：prompt_hint（双击弹多行对话框；移除 Editable flag 避免 Qt 默认窄编辑器）
            it_hint = QTableWidgetItem(ann.prompt_hint)
            it_hint.setFlags(it_hint.flags() & ~Qt.ItemIsEditable)
            it_hint.setToolTip(
                "双击此格弹出多行编辑器修改 LLM 提示" if ann.prompt_hint
                else "（未填）双击此格弹出多行编辑器输入 LLM 提示"
            )
            self.tbl.setItem(row, 4, it_hint)

        if warnings:
            self.lbl_warnings.setText(
                "解析告警：" + "；".join(warnings)
            )
            self.lbl_warnings.setVisible(True)
        else:
            self.lbl_warnings.setVisible(False)

        # 同步库描述到预览页编辑框（每轮 LLM 输出后用 _library_desc_suggested 覆盖；
        # 如果用户在中间编辑过，会被 _collect_user_edited_payload 同步回 state）
        # 用 blockSignals 避免触发 _on_desc_text_changed 把决策拉回 pending —
        # decision 由 _on_llm_finished 主动重置一次（pending），这里只是单纯把
        # 内容画到 UI 上
        self.ed_preview_library_desc.blockSignals(True)
        self.ed_preview_library_desc.setPlainText(self._library_desc_suggested)
        self.ed_preview_library_desc.blockSignals(False)
        # task #21 阶段 B：tooltip 展示用户在场景页输入的原库描述，方便对照
        if self._desc_has_llm_change() and (self._library_desc_input or "").strip():
            self.ed_preview_library_desc.setToolTip(
                "LLM 建议前的原库描述：\n\n"
                + (self._library_desc_input or "")
            )
        else:
            self.ed_preview_library_desc.setToolTip("")
        self._refresh_desc_decision_ui()
        self._refresh_round_label()

        # task #22 round 12：恢复滚动位置（先同步 set 一次让显示立即跟上；
        # 再用 QTimer.singleShot(0) 兜底——某些情况下表头/行高变化要在事件
        # 循环下一轮才完成布局，此时再赋一次 value 才能精确命中）。Qt 自动
        # 把超出 max 的值 clamp 回合法范围，所以行数变少时也安全。
        if vbar is not None:
            vbar.setValue(scroll_pos)
            QTimer.singleShot(
                0,
                lambda v=scroll_pos, b=vbar: b.setValue(v),
            )

    # ---- LLM 建议列辅助 ---------------------------------------------------
    _CHANGE_BG = {
        "已批准": ("#2e7d32", "rgba(46,125,50,0.12)"),   # 绿
        "已驳回": ("#757575", "rgba(117,117,117,0.12)"),  # 灰
        "已删除": ("#c62828", "rgba(198,40,40,0.12)"),    # 红
        "":      ("#9e9e9e", "transparent"),
    }

    def _make_change_cell(self, row: int, ann: AnnotatedSuggestion) -> None:
        """组装"LLM 建议"单元格：标签 + 可选的批准/驳回按钮。

        语义：
        * 标签只显示用户的"决策结果"（已批准 / 已驳回 / 已删除 / 空）
        * 批准/驳回按钮只在「LLM 触达且会带来变化」的 ann 上才有意义；
          一旦做出决策（approved/rejected）就不再渲染按钮，避免反复操作
          （想要回到 LLM 原版请用「查看 LLM 原始响应」弹窗里的「再次应用」）

        ``row`` 是 Step 1 表格的渲染行号；通过 ``self._step1_visible_rows``
        映射到 ``self._suggestions`` 的真实索引（task #21）。
        """
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(4)

        label_text = ann.llm_change_label
        # 仅当 LLM 实际带来变化、且尚未决策时才给批准/驳回按钮
        # （已决策的条目想再变 → 只能走「再次应用 LLM 建议」或手改）
        show_buttons = ann.has_llm_change and ann.decision == "pending"

        # 标签：已决策才画（"已批准/已驳回/已删除"）；pending+有按钮时不画占位 "—"
        # 避免按钮左边出现一根无意义的长破折号
        if label_text or not show_buttons:
            display = label_text if label_text else "—"
            lbl = QLabel(display)
            fg, bg = self._CHANGE_BG.get(label_text, ("#9e9e9e", "transparent"))
            lbl.setStyleSheet(
                f"color:{fg}; background:{bg}; "
                "border-radius:4px; padding:1px 6px;"
            )
            lbl.setMinimumWidth(60)
            lbl.setAlignment(Qt.AlignCenter)
            hl.addWidget(lbl)

        # 渲染行号 → 真实索引；按钮回调用真实索引调 _on_decision_changed
        src_idx = (
            self._step1_visible_rows[row]
            if 0 <= row < len(self._step1_visible_rows) else row
        )

        if show_buttons:
            b_ok = QPushButton("批准")
            # task #22 round 1：按钮做扁（22px 高），与窄了的操作列匹配
            b_ok.setMinimumWidth(42)
            b_ok.setFixedHeight(22)
            b_ok.setToolTip("批准这条 LLM 建议（立即采用 LLM 给的类型和提示）")
            b_ok.clicked.connect(
                lambda _c=False, sidx=src_idx: self._on_decision_changed(sidx, "approved")
            )
            hl.addWidget(b_ok)

            b_no = QPushButton("驳回")
            b_no.setMinimumWidth(42)
            b_no.setFixedHeight(22)
            b_no.setToolTip(
                "驳回这条 LLM 建议（保持原样不变；如果是 LLM 新建的字段，会标记为不创建）"
            )
            b_no.clicked.connect(
                lambda _c=False, sidx=src_idx: self._on_decision_changed(sidx, "rejected")
            )
            hl.addWidget(b_no)

        hl.addStretch(1)
        self.tbl.setCellWidget(row, 0, w)

    # task #22 round 3：原 `_refresh_change_cell` / `_src_idx_to_render_row`
    # 在批准 / 驳回 / hint 编辑后只刷第 0 列单元格，但 task #22 把"已批准 /
    # 已驳回"信息从第 0 列大标签迁移到第 1 列（LLM 建议列）的文案后缀
    # （由 step1_action_label 输出），单刷第 0 列会让第 1 列后缀直到下一次
    # 整表重画才更新——表现为"点了批准没反应"。所有决策变更现在统一走
    # `_render_preview([])` 全表重画，两个 helper 已不再有调用方，已删除。

    def _on_decision_changed(self, idx: int, decision: str) -> None:
        """批准 / 驳回按钮：**立即**把决定固化到 ann，并重画对应行。

        语义（6/1 晚最终版）：
        * **批准**：把 LLM 给的 type / hint 固定下来；用户后续依然可以再修改字段名 /
          类型 / hint，但 LLM 建议列只显示"已批准"
        * **驳回**：把 ann 还原到 LLM 提建议**之前**的状态：
          - ``new``           → 直接从 ``_suggestions`` 移除（库里就当 LLM 没建议过）
          - ``same_type``     → hint 还原为现有字段的旧 hint（type 本来就一致）
          - ``system_required``→ 同上，还原 hint
          - ``type_conflict`` → ``selected=False`` + ``type`` / ``prompt_hint``
            还原到旧值，action 变 skip（等价"字段彻底不动"）
          建议列显示"已驳回"，用户可以基于还原后的状态继续手改

        如果用户再次点同一个按钮 → 视为撤回决定，但**不能**自动复原 LLM 内容
        （因为 ann 已经被改写过了）；想要回到 LLM 原版 → 用「再次应用 LLM 建议」。
        """
        if not (0 <= idx < len(self._suggestions)):
            return
        ann = self._suggestions[idx]

        # toggle：再次点同一按钮 → 退回 pending（仅清掉标记；不"反向操作"，
        # 因为内容已被前一次操作改过；想恢复 LLM 建议请用弹窗里的"再次应用"）
        # task #22 round 3：toggle 也走整表重画——第 1 列的"（已批准 / 已驳回）"
        # 后缀需要由 _render_preview 重新生成。
        if ann.decision == decision:
            ann.decision = "pending"
            self._render_preview([])
            return

        if decision == "approved":
            ann.decision = "approved"
            # 批准 type_conflict：补 selected=True 让 action 进 change_type 路径
            # （task #19 Phase B）
            if ann.status == "type_conflict":
                ann.selected = True
            # 批准 llm_suggest_delete：补 selected=True 让 action 进 delete 路径
            elif ann.status == "llm_suggest_delete":
                ann.selected = True
            # 批准 llm_suggest_rename：补 selected=True 让 action 进 rename 路径
            elif ann.status == "llm_suggest_rename":
                ann.selected = True
            # task #22 round 3：批准统一走整表重画。原本只对
            # llm_suggest_delete / llm_suggest_rename 走 `_render_preview`、
            # 其余只刷第 0 列单元格——但 task #22 后"已批准 / 已驳回"已经
            # 通过 step1_action_label 写到第 1 列（LLM 建议列）作为文案后缀，
            # 单刷第 0 列会导致"修改提示（已批准）"等后缀直到下一次整表重画
            # 才更新，给用户错觉"点了批准没反应"。
            self._render_preview([])
            return

        # decision == "rejected"
        if ann.status == "new":
            # 驳回 LLM 新建建议 → 保留 ann 但标 decision='rejected'，让 Step 1
            # 仍显示该行（标签"已驳回"），与其它类型驳回行为一致；merge 第二
            # 遍处理 status='new' 时按 decision==rejected 跳过 → 不会进 drafts
            # （task #21 round 4 修复：原先 `del self._suggestions[idx]` 让行
            # 整个消失，用户失去"刚驳回了什么"的视觉反馈）
            ann.decision = "rejected"
            self._render_preview([])
            return
        if ann.status == "llm_suggest_delete":
            # 驳回 LLM 删除建议 → 保留原字段（selected=False → action 进 keep）。
            # task #21 round 4：**不**退化 ann.status 为 existing_user_field——
            # 那样会让该行从 Step 1 消失，用户失去"我刚驳回了什么"的视觉反馈。
            # 保持 status='llm_suggest_delete' + decision='rejected'，Step 1
            # 仍显示该行（标签为"已驳回"），状态列文案给出"驳回保留"提示。
            ann.selected = False
            ann.decision = "rejected"
            ann.reason = (
                "LLM 曾建议删除此字段，已被你驳回；保留中。"
                "下一步进入字段表后，可以再点行操作的「删除」按钮真删除。"
            )
            self._render_preview([])
            return
        if ann.status == "llm_suggest_rename":
            # 驳回 LLM 改名建议 → 保留原名（selected=False → action 进 keep）。
            # 同 llm_suggest_delete：不退化 status，保留 Step 1 显示与决策反馈。
            # task #19 收尾清理：rename 路径合并改类型后，驳回时把 ann.type /
            # ann.prompt_hint 也还原回 existing 值，保证 Step 1 / Step 2 看到
            # 的都是"还原后"状态（与 type_conflict 驳回对称）。
            ann.selected = False
            ann.decision = "rejected"
            if ann.existing_field_type:
                ann.type = ann.existing_field_type
            ann.prompt_hint = ann.existing_prompt_hint or ""
            ann.reason = (
                "LLM 曾建议改名此字段，已被你驳回；保留原名。"
                "下一步进入字段表后，可以在那里改名。"
            )
            self._render_preview([])
            return

        # 其它 status：还原 ann.prompt_hint / ann.type 到 LLM 触达之前的状态
        # task #22 round 6：恢复"还原"语义。round 1 之前会还原，但 round 1
        # 因为 visible 收紧到 has_llm_change 而把还原撤掉了——还原后 dims=空
        # 行就消失。round 6 已经把 visible 改成只过 existing_user_field（保留
        # 所有 llm_touched 的 ann，dims 空时显示"✓ 保持原样"），所以可以放心
        # 还原。还原后用户在 Step 1 hint 列看到的就是原 hint，与"已驳回"语义
        # 一致。merge_decisions_into_drafts 的 rejected 分支用 f.prompt_hint
        # （现有字段原 hint），与还原后的 ann.prompt_hint 一致。
        ann.decision = "rejected"
        if ann.status in ("same_type", "system_required"):
            ann.prompt_hint = ann.existing_prompt_hint or ""
        elif ann.status == "type_conflict":
            # task #19 Phase B：驳回 type_conflict → selected=False（action 变 skip），
            # 字段彻底不动。同时把 ann.type / ann.prompt_hint 还原为现有字段值。
            ann.selected = False
            if ann.existing_field_type:
                ann.type = ann.existing_field_type
            ann.prompt_hint = ann.existing_prompt_hint or ""
            ann.reason = (
                "LLM 曾建议修改此字段的类型，已被你驳回；保持不变。"
                "如果还是想改类型，可以在下一步的字段表里用类型下拉。"
            )
        # existing_user_field：本来就不算 LLM 建议（由用户行操作"删除"驱动），
        # 到这里只标 decision
        self._render_preview([])

    def _on_approve_all(self) -> None:
        """一键把所有「未决策（pending）」的 LLM 建议标为 approved。

        语义：仅影响 ``decision == "pending"`` 且确有 LLM 改动的条目；
        已驳回（rejected）的条目保持不动，避免误覆盖用户的明确选择。
        对 ``type_conflict`` / ``llm_suggest_delete`` / ``llm_suggest_rename``
        同步把 ``selected`` 提到 True。
        库描述如果 LLM 改过且尚未决策，也一并标为 approved。
        """
        for ann in self._suggestions:
            if ann.has_llm_change and ann.decision == "pending":
                ann.decision = "approved"
                if ann.status in (
                    "type_conflict",
                    "llm_suggest_delete",
                    "llm_suggest_rename",
                ):
                    ann.selected = True
        if self._desc_has_llm_change() and self._library_desc_decision == "pending":
            self._library_desc_decision = "approved"
        self._render_preview([])

    # ---- 库描述的批准/驳回 -----------------------------------------------
    def _desc_has_llm_change(self) -> bool:
        """LLM 这一轮是否实际改动了库描述（用户原始输入 vs LLM 建议不一致）。"""
        return (
            (self._library_desc_suggested or "").strip()
            != (self._library_desc_input or "").strip()
        )

    def _on_desc_decision_changed(self, decision: str) -> None:
        """库描述的批准 / 驳回。语义与字段批准/驳回一致：

        * 批准 → 把 _library_desc_suggested 固定为当前编辑框内容
        * 驳回 → 把 _library_desc_suggested 还原为 _library_desc_input
                 并同步刷新编辑框
        """
        if decision == "approved":
            # 用编辑框当前内容覆盖（用户可能已经手动改过）
            self._library_desc_suggested = (
                self.ed_preview_library_desc.toPlainText().strip()
            )
            self._library_desc_decision = "approved"
        elif decision == "rejected":
            self._library_desc_suggested = self._library_desc_input or ""
            self._library_desc_decision = "rejected"
            # 同步刷新编辑框（用 blockSignals 避免触发 textChanged 把决策又拉回 pending）
            self.ed_preview_library_desc.blockSignals(True)
            self.ed_preview_library_desc.setPlainText(self._library_desc_suggested)
            self.ed_preview_library_desc.blockSignals(False)
        self._refresh_desc_decision_ui()

    def _on_desc_text_changed(self) -> None:
        """用户手动编辑库描述 → 决策回到 pending，重新显示按钮。"""
        if self._library_desc_decision != "pending":
            self._library_desc_decision = "pending"
            self._refresh_desc_decision_ui()

    def _refresh_desc_decision_ui(self) -> None:
        """根据 _desc_has_llm_change() + _library_desc_decision 控制按钮/标签可见性。

        task #22 round 14：原代码用 `has_change` 兜底"全部隐藏"，但驳回操作
        会把 _library_desc_suggested 还原成 _library_desc_input → has_change
        变 False → 已驳回标签也被隐藏（用户失去视觉反馈）。修法：已决策
        （approved/rejected）状态优先，不论当前 has_change 都显示状态标签；
        pending 时才看 has_change 决定按钮可见。
        """
        # 已决策 → 显示状态标签，不管 has_change
        if self._library_desc_decision == "approved":
            self.btn_desc_approve.setVisible(False)
            self.btn_desc_reject.setVisible(False)
            self.lbl_desc_decision.setText(
                "<span style='color:#2e7d32; background:rgba(46,125,50,0.12); "
                "border-radius:4px; padding:1px 6px;'>已批准</span>"
            )
            self.lbl_desc_decision.setTextFormat(Qt.RichText)
            self.lbl_desc_decision.setVisible(True)
            return
        if self._library_desc_decision == "rejected":
            self.btn_desc_approve.setVisible(False)
            self.btn_desc_reject.setVisible(False)
            self.lbl_desc_decision.setText(
                "<span style='color:#757575; background:rgba(117,117,117,0.12); "
                "border-radius:4px; padding:1px 6px;'>已驳回</span>"
            )
            self.lbl_desc_decision.setTextFormat(Qt.RichText)
            self.lbl_desc_decision.setVisible(True)
            return
        # pending：按 has_change 决定按钮可见
        has_change = self._desc_has_llm_change()
        self.lbl_desc_decision.setVisible(False)
        self.btn_desc_approve.setVisible(has_change)
        self.btn_desc_reject.setVisible(has_change)


    # ---- 预览页行操作 -----------------------------------------------------
    # task #21 起 Step 1 不再承载"自主增删字段"动作（迁移到 Step 2）；以下
    # 历史方法保留为空体以兼容外部调用，不再绑定到任何按钮信号
    def _current_preview_row(self) -> int:
        return self.tbl.currentRow()

    def _wrap_cell(self, tbl: QTableWidget, row: int, col: int, widget) -> None:
        """把 widget 居中包进单元格（场景页字段表的"显示"/"参与建议"列复用）。"""
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch(1)
        hl.addWidget(widget)
        hl.addStretch(1)
        tbl.setCellWidget(row, col, w)

    def _on_type_changed(self, idx: int, new_type: str) -> None:
        """task #21 阶段 B 起 Step 1 类型 ComboBox 全只读，本方法不再被信号
        调用；仅作历史 API 保留，避免外部脚本（如 selftest 直接调）出错。

        新场景：所有改类型动作走 Step 2 ``_on_step2_type_changed``。
        """
        return  # no-op

    # 历史：``_on_rename_changed`` 槽函数曾用于 type_conflict 行的 LineEdit
    # 改名建新字段路径（写入 ann.rename_to）。task #19 Phase B 起 type_conflict
    # 改为"批准 = 原地改类型 / 驳回 = 不动"二态，LineEdit 已移除，槽函数随之删除。

    # ---- task #21：Step 1 → Step 2 切换 ----------------------------------
    def _promote_pending_to_approved(self) -> None:
        """task #21：把 Step 1 上所有 pending 的 LLM 触达建议物化成 approved。

        卡片决策 1：Step 1 未决条目下一步时一律视作"已批准"（含
        ``llm_suggest_delete``，不为单一状态做特例）。物化后 Back 回 Step 1
        看到的就是"剩余未决项全部已批准"的状态，与 Next 时的语义一致。
        库描述若 LLM 改过且未决也同步批准。
        """
        for ann in self._suggestions:
            if ann.decision != "pending":
                continue
            if not ann.has_llm_change:
                continue
            ann.decision = "approved"
            # 跟 _on_decision_changed("approved") 行为一致：补 selected=True
            if ann.status in (
                "type_conflict", "llm_suggest_delete", "llm_suggest_rename",
            ):
                ann.selected = True
        # 库描述：LLM 改过且未决 → 批准
        if self._desc_has_llm_change() and self._library_desc_decision == "pending":
            self._library_desc_decision = "approved"

    def _on_step1_next(self) -> None:
        """点 Step 1 底部"下一步 →"按钮：把 Step 1 决策 + 库描述编辑同步回
        ``self._suggestions``，然后用 ``merge_decisions_into_drafts`` 合并出
        Step 2 字段表草稿，切到 Step 2。"""
        if self.repo is None:
            return
        # 把 Step 1 表里用户对 hint 的微调收回到 ann
        self._sync_step1_edits_into_suggestions()
        # task #21 决策 1：未决一律视作已批准；物化到 ann，Back 时视觉一致
        self._promote_pending_to_approved()

        existing = self.repo.list_fields() if self.repo else []
        # 缓存：受保护字段的 fid 集合（用于 Step 2 渲染时判断不可改）
        self._protected_fids = {
            f.id for f in existing
            if (f.id is not None) and (f.key in PROTECTED_FIELD_KEYS)
        }
        self._drafts = merge_decisions_into_drafts(self._suggestions, existing)
        # 进入 Step 2 时记录基线（用于 Back 时检测是否被编辑过）
        self._drafts_baseline = [clone_draft(d) for d in self._drafts]
        self._render_step2_table()
        self.stack.setCurrentIndex(PAGE_STEP2)

    @staticmethod
    def _clone_draft(d: "FieldDraft") -> "FieldDraft":
        """task #21：浅拷贝一个 FieldDraft（转发到模块级 ``clone_draft``）。"""
        return clone_draft(d)

    def _drafts_dirty(self) -> bool:
        """task #21：Step 2 草稿是否被用户编辑过（与 ``_drafts_baseline`` 比较）。"""
        return drafts_are_dirty(self._drafts, self._drafts_baseline)

    def _sync_step1_edits_into_suggestions(self) -> None:
        """把 Step 1 表里用户的 hint 微调同步回 ``self._suggestions``。

        task #21 阶段 B 起 Step 1 字段名 / 类型全只读，所以这里只处理 hint。
        hint 的双击编辑路径已经实时同步 ann.prompt_hint，这里再兜一层防御
        （把表格 cell 的当前文本写回 ann）。同时把库描述编辑框的内容同步到
        ``_library_desc_suggested``。
        """
        for row, src_idx in enumerate(self._step1_visible_rows):
            if not (0 <= src_idx < len(self._suggestions)):
                continue
            ann = self._suggestions[src_idx]
            # hint：双击编辑路径已同步 ann.prompt_hint，这里再兜一层防御
            it_h = self.tbl.item(row, 4)
            if it_h is not None:
                ann.prompt_hint = it_h.text()
        # 库描述
        if hasattr(self, "ed_preview_library_desc"):
            self._library_desc_suggested = (
                self.ed_preview_library_desc.toPlainText().strip()
            )

    # ---- task #21：Step 2 视图渲染 ---------------------------------------
    _ORIGIN_BADGE = {
        DRAFT_ORIGIN_EXISTING: ("📋 现有", "原本就存在的字段"),
        DRAFT_ORIGIN_LLM_NEW: ("🤖 LLM 新增", "上一步批准的 LLM 新增建议"),
        DRAFT_ORIGIN_LLM_RENAMED: ("✏ LLM 改名", "上一步批准的 LLM 改名建议"),
        DRAFT_ORIGIN_LLM_TYPECHANGED: ("⚠ LLM 改类型", "上一步批准的 LLM 改类型建议"),
        DRAFT_ORIGIN_LLM_DELETED: ("🗑 LLM 标删", "上一步批准的 LLM 删除建议"),
        DRAFT_ORIGIN_USER_NEW: ("👤 新增", "你在这一步自己添加的新字段"),
    }

    def _render_step2_table(self) -> None:
        """根据 ``self._drafts`` 渲染 Step 2 字段表。"""
        from PySide6.QtGui import QColor

        tbl = self.tbl_step2
        tbl.blockSignals(True)
        try:
            tbl.setRowCount(0)
            for row, d in enumerate(self._drafts):
                tbl.insertRow(row)

                # 0：状态徽章
                badge, badge_tip = self._ORIGIN_BADGE.get(
                    d.origin, (d.origin, ""),
                )
                it_badge = QTableWidgetItem(badge)
                it_badge.setFlags(it_badge.flags() & ~Qt.ItemIsEditable)
                it_badge.setToolTip(badge_tip)
                tbl.setItem(row, 0, it_badge)

                # 1：字段名
                # task #22：划删线行字段名前加 🗑 前缀，作为状态列被删后的视觉补偿
                display_name = f"🗑 {d.name}" if d.deleted else d.name
                it_name = QTableWidgetItem(display_name)
                # 受保护字段（is_required=True）不可改名；划删线行也不可改名
                is_protected = self._draft_is_protected(d)
                if is_protected or d.deleted:
                    it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)
                tbl.setItem(row, 1, it_name)

                # 2：类型 ComboBox
                cmb = QComboBox()
                cmb.setMinimumHeight(26)
                cmb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                for t in FIELD_TYPES:
                    cmb.addItem(FIELD_TYPE_LABELS.get(t, t), t)
                # 保留未知类型（如 "tags"）
                if cmb.findData(d.type) < 0:
                    cmb.addItem(FIELD_TYPE_LABELS.get(d.type, d.type), d.type)
                cmb.setCurrentIndex(max(0, cmb.findData(d.type)))
                if is_protected or d.deleted:
                    cmb.setEnabled(False)
                cmb.currentIndexChanged.connect(
                    lambda _i, ridx=row, c=cmb:
                        self._on_step2_type_changed(ridx, c.currentData())
                )
                tbl.setCellWidget(row, 2, cmb)

                # 3：LLM 提示
                it_hint = QTableWidgetItem(d.prompt_hint)
                if d.deleted:
                    it_hint.setFlags(it_hint.flags() & ~Qt.ItemIsEditable)
                else:
                    # 双击走 _on_step2_cell_double_clicked 弹多行编辑器
                    it_hint.setFlags(it_hint.flags() & ~Qt.ItemIsEditable)
                it_hint.setToolTip(
                    "双击此格弹出多行编辑器修改 LLM 提示" if not d.deleted
                    else "（划删线行无法编辑）"
                )
                tbl.setItem(row, 3, it_hint)

                # 4：操作按钮
                op_w = QWidget()
                op_h = QHBoxLayout(op_w)
                op_h.setContentsMargins(2, 2, 2, 2)
                op_h.setSpacing(4)
                if d.deleted:
                    b_undo = QPushButton("↩ 撤销删除")
                    b_undo.setFixedHeight(24)
                    b_undo.setToolTip("恢复此字段（如撤销后会重名将弹错）")
                    b_undo.clicked.connect(
                        lambda _c=False, ridx=row: self._on_step2_undelete(ridx)
                    )
                    op_h.addWidget(b_undo)
                elif is_protected:
                    lbl = QLabel("（系统保留）")
                    lbl.setStyleSheet("color:#999;")
                    op_h.addWidget(lbl)
                else:
                    b_del = QPushButton("🗑 删除")
                    b_del.setFixedHeight(24)
                    b_del.setProperty("danger", True)
                    b_del.clicked.connect(
                        lambda _c=False, ridx=row: self._on_step2_delete(ridx)
                    )
                    op_h.addWidget(b_del)
                op_h.addStretch(1)
                tbl.setCellWidget(row, 4, op_w)

                # 划删线行：所有 cell 灰化
                if d.deleted:
                    for col in range(tbl.columnCount()):
                        c_item = tbl.item(row, col)
                        if c_item is not None:
                            font = c_item.font()
                            font.setStrikeOut(True)
                            c_item.setFont(font)
                            c_item.setForeground(QColor("#999999"))
        finally:
            tbl.blockSignals(False)

        # 渲染完后做一次唯一性 / 必填校验，更新警告区
        self._refresh_step2_warnings()

    def _draft_is_protected(self, d: "FieldDraft") -> bool:
        """task #21：判断 draft 是否对应受保护字段（``is_required=True``）。

        通过 ``self._protected_fids``（``_on_step1_next`` 时根据
        ``PROTECTED_FIELD_KEYS`` 缓存的 fid 集合）判定；保护字段类型固定，
        Step 2 渲染时禁用字段名 / 类型 / 删除按钮。
        """
        if d.origin != DRAFT_ORIGIN_EXISTING:
            return False
        if d.existing_field_id is None:
            return False
        return d.existing_field_id in getattr(self, "_protected_fids", set())

    def _refresh_step2_warnings(self) -> None:
        """检查 Step 2 字段表的合法性，把警告显示在底部。"""
        msgs: list[str] = []
        # 1) 未删除行的字段名唯一性
        seen: dict[str, int] = {}
        for i, d in enumerate(self._drafts):
            if d.deleted:
                continue
            n = (d.name or "").strip()
            if not n:
                msgs.append(f"第 {i+1} 行字段名为空")
                continue
            if n in seen:
                msgs.append(f"字段名「{n}」重复（第 {seen[n]+1} 行 与 第 {i+1} 行）")
            else:
                seen[n] = i
        if msgs:
            self.lbl_step2_warnings.setText("⚠ " + "；".join(msgs))
            self.lbl_step2_warnings.setVisible(True)
        else:
            self.lbl_step2_warnings.setVisible(False)

    def _on_step2_cell_double_clicked(self, row: int, col: int) -> None:
        """Step 2 表的 LLM 提示列双击 → 弹多行编辑器。"""
        if col != 3:
            return
        if not (0 <= row < len(self._drafts)):
            return
        d = self._drafts[row]
        if d.deleted:
            return
        new_text, ok = _ask_text(
            self, f"编辑 LLM 提示 — {d.name}",
            "请输入该字段的 LLM 提示（多行）：",
            initial=d.prompt_hint,
        )
        if not ok:
            return
        d.prompt_hint = new_text
        it = self.tbl_step2.item(row, 3)
        if it is not None:
            it.setText(new_text)

    def _on_step2_item_changed(self, item: "QTableWidgetItem") -> None:
        """Step 2 表的 itemChanged 信号：处理用户在字段名列直接编辑。"""
        if item is None:
            return
        col = item.column()
        row = item.row()
        if col != 1:  # 仅字段名列
            return
        if not (0 <= row < len(self._drafts)):
            return
        d = self._drafts[row]
        if d.deleted:
            return
        new_name = item.text().strip()
        if new_name == d.name:
            return
        d.name = new_name
        self._refresh_step2_warnings()

    def _on_step2_type_changed(self, row: int, new_type: str) -> None:
        """Step 2 表的类型 ComboBox 改动。

        如果 origin == ``existing`` 且**与 ``original_type`` 不兼容**、且字段
        已有数据 / pending 建议 / 非空 hint，弹 ``_FieldTypeChangeConfirmDialog``
        让用户确认；其它情况静默改。

        task #21 阶段 B 关键约束：兼容性 / 弹窗里"旧类型"都以
        ``d.original_type``（进入 Step 2 时的初始类型）为准——用户在 Step 2
        多次改类型（单行→多行→日期）时，对话框永远显示"单行 → 日期"，而不是
        "多行 → 日期"，确保用户参考的"原始数据语义"始终对齐库里的真值。
        """
        if not (0 <= row < len(self._drafts)):
            return
        d = self._drafts[row]
        if d.deleted:
            return
        old_type = d.type
        if old_type == new_type:
            return
        # 仅对 existing 字段（有 fid）做兼容性 & 数据保护检查
        if (
            d.origin == DRAFT_ORIGIN_EXISTING
            and d.existing_field_id is not None
            and self.repo is not None
        ):
            from ...models import is_compatible_type_change
            from ..settings.field_dialogs import _FieldTypeChangeConfirmDialog

            # 用初始类型（original_type 优先；兜底用 repo.get_field().type）
            base_type = d.original_type or old_type
            try:
                # 用户改回初始类型 → 没有变更，直接接受
                if new_type == base_type:
                    d.type = new_type
                    return
                if not is_compatible_type_change(base_type, new_type):
                    f = self.repo.get_field(d.existing_field_id)
                    if f is None:
                        d.type = new_type
                        return
                    try:
                        n_values = self.repo.count_field_filled(f)
                    except Exception:
                        n_values = 0
                    m_pending = self.repo.count_pending_suggestions_for_field(
                        d.existing_field_id,
                    )
                    has_hint = bool((d.prompt_hint or "").strip())
                    if int(n_values) == 0 and int(m_pending) == 0 and not has_hint:
                        d.type = new_type
                        return
                    # 构造一个临时 Field 让对话框看到的是"草稿当前状态"：
                    # - type 用 base_type（原始类型，让对话框文案"X → Y"诚实）
                    # - prompt_hint 用 d.prompt_hint（draft 当前 hint，可能已被
                    #   用户在 Step 2 编辑过；不是 repo 里的真值）
                    # 这样复选框是否显示、清空对象都对齐 draft 状态
                    from copy import copy as _shallow_copy
                    f_for_dialog = _shallow_copy(f)
                    f_for_dialog.type = base_type
                    f_for_dialog.prompt_hint = d.prompt_hint or ""
                    confirmed, clear_hint = _FieldTypeChangeConfirmDialog.ask(
                        self, f_for_dialog, new_type,
                        int(n_values), int(m_pending),
                    )
                    if not confirmed:
                        # 用户取消 → 把 ComboBox 视觉恢复到 old_type
                        cmb = self.tbl_step2.cellWidget(row, 2)
                        if cmb is not None:
                            cmb.blockSignals(True)
                            idx_old = cmb.findData(old_type)
                            if idx_old >= 0:
                                cmb.setCurrentIndex(idx_old)
                            cmb.blockSignals(False)
                        return
                    # 用户确认 + 勾选了"同时清空 LLM 提示" → 立即把 draft 的
                    # prompt_hint 清空，重画 Step 2 表让用户看到这一变化
                    if clear_hint:
                        d.prompt_hint = ""
                        d.type = new_type
                        self._render_step2_table()
                        return
            except Exception:  # noqa: BLE001
                pass
        d.type = new_type

    def _on_step2_delete(self, row: int) -> None:
        """点 Step 2 表"删除"按钮：``user_new`` 直接从 drafts 移除；其余设
        ``deleted=True`` 划删线展示。"""
        if not (0 <= row < len(self._drafts)):
            return
        d = self._drafts[row]
        if d.origin == DRAFT_ORIGIN_USER_NEW:
            del self._drafts[row]
        else:
            d.deleted = True
        self._render_step2_table()

    def _on_step2_undelete(self, row: int) -> None:
        """点 Step 2 表"撤销删除"按钮：实时校验重名，冲突时弹错。"""
        if not (0 <= row < len(self._drafts)):
            return
        conflict = check_undelete_name_conflict(self._drafts, row)
        if conflict is not None:
            # 描述冲突来源
            origin_desc = {
                DRAFT_ORIGIN_USER_NEW: "你新增的字段",
                DRAFT_ORIGIN_LLM_RENAMED: "LLM 建议改名后产生的字段",
                DRAFT_ORIGIN_EXISTING: (
                    f"你修改了现有字段「{conflict.original_name}」的名字"
                    if conflict.original_name and conflict.original_name != conflict.name
                    else "现有同名字段"
                ),
                DRAFT_ORIGIN_LLM_NEW: "LLM 新建的字段",
                DRAFT_ORIGIN_LLM_TYPECHANGED: "LLM 建议改类型后保留的字段",
            }.get(conflict.origin, "其它字段")
            warn(
                self, "无法撤销删除",
                f"无法撤销删除「{self._drafts[row].name}」：当前字段表里已有同名字段。\n\n"
                f"冲突来源：{origin_desc}\n\n"
                "请先调整冲突字段的名字，再撤销此删除。",
            )
            return
        self._drafts[row].deleted = False
        self._render_step2_table()

    def _on_step2_add_field(self) -> None:
        """点"＋ 添加字段"按钮：在末尾追加 user_new draft 并重画。"""
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(
            self, "添加字段",
            "字段名（默认类型：单行文本，可在表格中改）：",
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        # 防止与未删除行重名
        existing = {(d.name or "").strip() for d in self._drafts if not d.deleted}
        if name in existing:
            warn(
                self, "字段名重复",
                f"「{name}」已存在于当前字段表。请换一个名字。",
            )
            return
        self._drafts.append(FieldDraft(
            origin=DRAFT_ORIGIN_USER_NEW,
            existing_field_id=None,
            original_name=None,
            original_type=None,
            name=name,
            type="text",
            prompt_hint="",
            deleted=False,
        ))
        self._render_step2_table()

    def _on_step2_move(self, delta: int) -> None:
        """上下移当前选中行。

        task #22 round 5：移除原有受保护字段（is_required=title/description/tags）
        相关的两条移动限制，与「设置 → 字段」面板和现有字段编辑面板对齐
        （那两个面板对受保护字段没有任何排序限制）。划删线行也可以参与
        排序——它们最后会被删除，但调序不影响应用结果。
        """
        r = self.tbl_step2.currentRow()
        if r < 0 or r >= len(self._drafts):
            info(
                self, "请先选择一行",
                "请先点选一行字段，再点上移 / 下移。",
            )
            return
        target = r + delta
        if target < 0 or target >= len(self._drafts):
            return
        self._drafts[r], self._drafts[target] = (
            self._drafts[target], self._drafts[r],
        )
        self._render_step2_table()
        self.tbl_step2.setCurrentCell(target, 1)

    def _on_step2_back(self) -> None:
        """点"← 放弃修改并返回"按钮：检测 drafts 是否被编辑过；有编辑则弹确认。"""
        if self._drafts_dirty():
            ret = confirm(
                self, "确认返回",
                "返回会丢弃你在字段表里做的所有修改（增/删/改名/改类型/改提示）。\n"
                "上一步对 LLM 建议的批准/驳回会保留。\n\n"
                "确认返回？",
                yes="返回", danger=True,
            )
            if not ret:
                return
        # 丢弃 drafts，回 Step 1
        self._drafts = []
        self._drafts_baseline = []
        # task #21 round 4：Back 时必须重画 Step 1 表，让 _on_step1_next 里
        # _promote_pending_to_approved 物化的"已批准"视觉状态显示出来；否则
        # 用户看到的还是上次进 Step 2 之前的按钮态，与底层 ann.decision 脱节
        # （会导致：用户点驳回 → 该行真改 rejected，其它行因为早已 approved
        # 但按钮还在 → 误以为"驳回会自动批准其它行"）
        self._render_preview([])
        self.stack.setCurrentIndex(PAGE_STEP1)

    # ---- task #21：Step 2 应用 -------------------------------------------
    def _collect_step1_feedback_payload(self) -> dict:
        """收集 Step 1 反馈（决策 + 微调过的 hint + 库描述）作为 refine 的回灌。

        与旧 ``_collect_user_edited_payload`` 的区别：**只**回灌 Step 1 反馈，
        不含 Step 2 的字段表编辑（增 / 删 / 改名 / 改类型 / 改提示）；这些属于
        "用户对最终落库表的私人处置"，与 LLM 无关（task #21 决策 9）。

        输出结构：
        - ``fields``：用户当前的字段方案（每条 LLM 建议被批准/驳回后的最终 type
          / hint）；带上 decision 标记让 LLM 知道用户对每条建议的态度
        - ``rejected_suggestions``：被驳回的 LLM 建议摘要（new / 改类型 / 改名
          / 删除 4 类），让 LLM 在新一轮**不要**再提同样的建议
        - ``library_description``：用户编辑过的库描述
        """
        out = []
        rejected: list[str] = []
        for ann in self._suggestions:
            decision = ann.decision  # approved / rejected / pending
            out.append({
                "name": ann.name,
                "type": ann.type,
                "prompt_hint": ann.prompt_hint,
                "decision": decision,
            })
            # 显式记录驳回的建议，方便 LLM 下一轮回避
            # 注意：被驳回的 llm_suggest_delete / llm_suggest_rename 在
            # _on_decision_changed 里已经退化成 existing_user_field、
            # ann.status / reason 都改了，但我们仍能从"用户行为留痕"角度
            # 提取信息：通过 reason 字段里"已被你驳回"的关键词识别
            if decision == DECISION_REJECTED:
                if ann.status == "existing_user_field" and ann.reason:
                    # 驳回 LLM 删除 / 改名后退化的，reason 里有原因
                    rejected.append(f"对字段「{ann.name}」：{ann.reason}")
                else:
                    rejected.append(
                        f"驳回了关于「{ann.name}」的 LLM 建议（用户不希望此变更）"
                    )
        edited_desc = (
            self.ed_preview_library_desc.toPlainText().strip()
            if hasattr(self, "ed_preview_library_desc") else ""
        )
        self._library_desc_suggested = edited_desc
        return {
            "fields": out,
            "rejected_suggestions": rejected,
            "library_description": edited_desc,
        }

    # 旧名兼容：refine 路径仍调 _collect_user_edited_payload
    def _collect_user_edited_payload(self) -> dict:
        """task #21：旧 API；现在转发到 ``_collect_step1_feedback_payload``。"""
        return self._collect_step1_feedback_payload()

    def _build_field_plan_from_drafts(self) -> "FieldPlan":
        """task #21：把当前 ``self._drafts`` diff 成可应用的 FieldPlan。"""
        existing = self.repo.list_fields() if self.repo else []
        return diff_drafts_to_plan(self._drafts, existing)

    def _on_step2_apply(self, *, continue_refine: bool = False) -> None:
        """Step 2 应用按钮统一入口。

        ``continue_refine=True`` 时走"应用并继续讨论"路径：落库后弹补充说明
        启动新一轮 LLM；False 时落库后关闭对话框。
        """
        if self.repo is None:
            return

        # 校验 drafts 合法性
        self._refresh_step2_warnings()
        if self.lbl_step2_warnings.isVisible():
            warn(
                self, "字段表存在问题",
                "请先解决底部警告区列出的问题（重名 / 空字段名）后再应用。",
            )
            return

        plan = self._build_field_plan_from_drafts()
        if plan.is_empty:
            info(
                self, "无变更",
                "当前字段表与现有库一致，没有可应用的变更。",
            )
            return

        # 应用前汇总对话框（task #21 阶段 B：传 fid_resolver 让对话框显示
        # 真名而不是 #fid）
        dlg_summary = _ApplySummaryDialog(
            plan,
            fid_resolver=self._fid_to_name,
            parent=self,
        )
        if dlg_summary.exec() != QDialog.Accepted:
            return

        # 按 plan 内容串联二次确认
        # 1) 类型变更确认
        if plan.type_changes:
            type_entries: list[tuple[str, str, str, int, int]] = []
            for fid, new_type, _new_hint in plan.type_changes:
                f = self.repo.get_field(fid)
                if f is None:
                    continue
                try:
                    n_values = self.repo.count_field_filled(f)
                except Exception:
                    n_values = 0
                m_pending = self.repo.count_pending_suggestions_for_field(fid)
                type_entries.append(
                    (f.name, f.type, new_type, int(n_values), int(m_pending)),
                )
            if type_entries:
                dlg_tc = _BatchTypeChangeConfirmDialog(type_entries, parent=self)
                if dlg_tc.exec() != QDialog.Accepted:
                    return

        # 2) 删除确认
        append_for_fids: set[int] = set()
        if plan.deletes:
            entries: list[tuple[int, str, int, str]] = []
            for fid in plan.deletes:
                f = self.repo.get_field(fid)
                if f is None:
                    entries.append((fid, "(已不存在)", 0, "user"))
                    continue
                try:
                    n = self.repo.count_field_filled(f)
                except Exception:
                    n = 0
                # 判定来源：从 drafts 里反查，origin == llm_deleted → llm
                src = "user"
                for d in self._drafts:
                    if d.existing_field_id == fid and d.deleted:
                        src = "llm" if d.origin == DRAFT_ORIGIN_LLM_DELETED else "user"
                        break
                entries.append((fid, f.name, n, src))
            dlg_del = _BatchDeleteConfirmDialog(entries, parent=self)
            if dlg_del.exec() != QDialog.Accepted:
                return
            append_for_fids = dlg_del.append_for_fids

        # 3) 事务化批量应用
        try:
            new_ids, n_deleted, n_renamed, n_type_changed = (
                self.repo.apply_field_plan_batch(
                    plan.creates,
                    plan.updates_hint,
                    plan.deletes,
                    append_for_fids=append_for_fids,
                    renames=plan.renames,
                    type_changes=plan.type_changes,
                )
            )
        except Exception as e:  # noqa: BLE001
            error(
                self, "应用失败",
                f"应用字段方案时出错，已回滚（库内字段表无变化）：\n{e}",
            )
            return

        # 3.5) 把 Step 2 的字段顺序写回 fields.ord
        # 顺序源：self._drafts 中未删除行的顺序；新建字段按 plan.creates 顺序拿
        # 到的 new_ids 一一对应
        # 注意：apply_field_plan_batch 已经提交事务，reorder 单独发；失败不影响
        # 字段定义本身，仅影响显示顺序，故 try/except 兜底降级
        try:
            self._apply_step2_reorder(plan.creates, new_ids)
        except Exception:  # noqa: BLE001
            # 顺序不重要到要中断流程；静默忽略
            pass

        # 4) 库描述（事务外，幂等）
        new_desc = (self._library_desc_suggested or "").strip()
        if new_desc:
            cur_desc = self.repo.get_setting("library_description", "") or ""
            if new_desc != cur_desc:
                self.repo.set_setting("library_description", new_desc)

        # 标记一次性
        try:
            self.repo.set_setting("library_init_wizard_done", "1")
        except Exception:
            pass
        self._applied = True

        if continue_refine:
            # "应用并继续讨论"：弹补充说明 → 启动新一轮 LLM
            text, ok = _ask_text(
                self, "补充说明",
                "字段已应用。请说明希望在落库后的字段结构基础上做什么进一步调整：",
            )
            if not ok or not text.strip():
                # 用户取消补充说明 → 等价普通 "应用"，关闭对话框
                self._notify_apply_done(plan, n_deleted, n_renamed, n_type_changed, len(new_ids))
                self.accept()
                return
            # 启动新一轮：清掉 Step 1 / Step 2 状态，进 PAGE_RUNNING
            self._suggestions = []
            self._drafts = []
            self._drafts_baseline = []
            self._library_desc_decision = "pending"
            self._dispatch_call(extra=text.strip())
            return

        # 普通 "应用"：弹结果消息后关闭
        self._notify_apply_done(plan, n_deleted, n_renamed, n_type_changed, len(new_ids))
        self.accept()

    def _apply_step2_reorder(
        self,
        creates: list[tuple[str, str, str]],
        new_ids: list[int],
    ) -> None:
        """task #21 阶段 B 补丁：把 Step 2 调序的结果写回 ``fields.ord``。

        ``new_ids`` 是 ``apply_field_plan_batch`` 返回的"按 plan.creates 顺序
        分配的新 fid 列表"。这里：
        1. 建一个"原字段名 → 新 fid"的映射（用于 user_new / llm_new 行）
        2. 按 ``self._drafts`` 中未删除行的顺序，依次取出对应 fid
        3. 把这个 fid 列表交给 ``repo.reorder_fields``

        如果 drafts 顺序与库当前顺序完全一致，``reorder_fields`` 是幂等的
        （UPDATE 同样的 ord 值），不会有副作用。
        """
        if self.repo is None:
            return
        # 1) 新建字段名 → fid 映射
        name_to_new_fid: dict[str, int] = {}
        for (name, _t, _h), fid in zip(creates, new_ids):
            name_to_new_fid[name] = fid

        # 2) 按 drafts 顺序构造 fid 列表（跳过 deleted 行）
        ordered: list[int] = []
        for d in self._drafts:
            if d.deleted:
                continue
            if d.existing_field_id is not None:
                ordered.append(d.existing_field_id)
            elif d.name in name_to_new_fid:
                # user_new / llm_new：用刚分配到的 fid
                ordered.append(name_to_new_fid[d.name])
            # 其它情况（异常，比如 user_new 行的字段名没在 creates 里）：跳过

        if ordered:
            self.repo.reorder_fields(ordered)

    def _notify_apply_done(
        self, plan: "FieldPlan",
        n_deleted: int, n_renamed: int, n_type_changed: int, n_new: int,
    ) -> None:
        """弹"已应用"结果对话框。"""
        msg_parts = []
        if n_new:
            msg_parts.append(f"已新建字段 {n_new} 个")
        if plan.updates_hint:
            msg_parts.append(f"更新已存在字段的 LLM 提示 {len(plan.updates_hint)} 个")
        if n_type_changed:
            names = ", ".join(
                self._fid_to_name(fid) for fid, _, _ in plan.type_changes
            )
            msg_parts.append(f"改类型 {n_type_changed} 个（{names}）")
        if n_renamed:
            renamed_pairs = ", ".join(
                f"{self._fid_to_name(fid, prefer_old=True)}→{new_name}"
                for fid, new_name in plan.renames
            )
            msg_parts.append(f"改名 {n_renamed} 个（{renamed_pairs}）")
        if n_deleted:
            msg_parts.append(f"删除字段 {n_deleted} 个")
        if not msg_parts:
            msg_parts.append("已应用")
        info(self, "已应用", "，".join(msg_parts) + "。")

    def _fid_to_name(self, fid: int, *, prefer_old: bool = False) -> str:
        """工具：fid → 字段名（用于结果消息）。"""
        for d in self._drafts:
            if d.existing_field_id == fid:
                if prefer_old and d.original_name:
                    return d.original_name
                return d.name
        try:
            f = self.repo.get_field(fid) if self.repo else None
            return f.name if f else f"#{fid}"
        except Exception:
            return f"#{fid}"


# =============================================================================
# 小工具
# =============================================================================
class _TallLineEditDelegate(QStyledItemDelegate):
    """字段名列用的 delegate：让 QLineEdit 高度撑满 cell。

    Qt 默认在 QTableWidgetItem 双击时给的 LineEdit 高度只有 ~18 像素，
    在 34 像素行高下会显得很窄、字也偏小。
    """

    def createEditor(self, parent, option, index):  # noqa: N802
        ed = QLineEdit(parent)
        ed.setFrame(False)
        return ed

    def updateEditorGeometry(self, editor, option, index):  # noqa: N802
        # 让 editor 占满整个 cell，而不是默认的紧凑高度
        editor.setGeometry(option.rect)


# =============================================================================
# 输入对话框
# =============================================================================
def _ask_text(
    parent, title: str, prompt: str, *, initial: str = "",
    reference_label: str = "", reference_text: str = "",
) -> tuple[str, bool]:
    """多行输入弹窗。返回 (text, accepted)。

    ``reference_label`` / ``reference_text``：可选的只读参考区，显示在输入框
    下方（task #21 阶段 B 补丁：让 Step 1 hint 编辑窗口能展示"LLM 建议前的
    原 hint"作为对照）。
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(540, 380 if reference_text else 320)
    v = QVBoxLayout(dlg)
    lbl = QLabel(prompt)
    lbl.setWordWrap(True)
    v.addWidget(lbl)
    ed = QPlainTextEdit()
    if initial:
        ed.setPlainText(initial)
    v.addWidget(ed, 1)
    if reference_text:
        ref_lbl = QLabel(reference_label or "<b>参考：</b>")
        ref_lbl.setTextFormat(Qt.RichText)
        ref_lbl.setWordWrap(True)
        v.addWidget(ref_lbl)
        ref_ed = QPlainTextEdit()
        ref_ed.setPlainText(reference_text)
        ref_ed.setReadOnly(True)
        ref_ed.setMaximumHeight(100)
        ref_ed.setStyleSheet("background: rgba(0,0,0,0.04); color: #555;")
        v.addWidget(ref_ed)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    v.addWidget(bb)
    if dlg.exec() == QDialog.Accepted:
        return ed.toPlainText(), True
    return "", False


# =============================================================================
# 批量删除字段二次确认对话框
# =============================================================================
class _BatchDeleteConfirmDialog(QDialog):
    """库字段设计助手「应用」前的批量删除确认对话框。

    职责：
    * 列出所有待删字段（按"用户主动取消保留" / "批准 LLM 删除建议"分组）；
    * 对**有填充数据**的字段提供两个互斥选项 — "直接删除" / "把每个项目的
      该字段值追加到「描述」末尾再删除"；无数据的字段只列名字。
    * 默认选择"直接删除"（与旧版 QMessageBox.question 行为兼容）。

    返回：
    * ``self.append_for_fids: set[int]`` — 用户选了"追加到描述"的 fid 集合；
      `_on_apply` 把它透传给 ``Repository.apply_field_plan_batch``。
    """

    def __init__(
        self,
        entries: list[tuple[int, str, int, str]],
        *,
        parent=None,
    ) -> None:
        """
        Args:
            entries: ``[(fid, name, count, src), ...]``；
                ``src`` ∈ {"user", "llm"} 决定该条归到哪个分组。
        """
        super().__init__(parent)
        self.setWindowTitle("确认删除字段")
        self.setMinimumWidth(540)
        self.resize(620, 480)
        self.append_for_fids: set[int] = set()
        # fid → ("drop" | "append")
        self._choices: dict[int, str] = {}
        # fid → (rb_drop, rb_append)；只对 count > 0 的字段记录
        self._radios: dict[int, tuple[QRadioButton, QRadioButton]] = {}

        v = QVBoxLayout(self)
        v.setSpacing(10)

        n = len(entries)
        n_with_data = sum(1 for _, _, c, _ in entries if c > 0)
        head = QLabel(
            f"即将删除 <b>{n}</b> 个字段。"
            + (
                f"其中 <b>{n_with_data}</b> 个字段在已有项目里有数据，"
                "请选择如何处理：<br/>"
                "<span style='color:#666'>"
                "&nbsp;&nbsp;• <b>直接删除</b> — 字段定义连同所有项目的对应值一起丢；<br/>"
                "&nbsp;&nbsp;• <b>追加到描述</b> — 把每个项目的该字段值拼到 description "
                "末尾（格式 <code>**字段名**：值</code>）后再删除字段。"
                "</span>"
                if n_with_data else "<br/>"
            )
            + "<br/>该操作不可撤销，与字段创建 / 更新走同一事务（任一失败整体回滚）。"
        )
        head.setTextFormat(Qt.RichText)
        head.setWordWrap(True)
        v.addWidget(head)

        # 滚动区域：字段多时不会撑爆窗口
        scroll = QScrollArea()
        scroll.setObjectName("AppScrollArea")
        scroll.setWidgetResizable(True)
        from PySide6.QtWidgets import QFrame
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        inner.setObjectName("AppScrollHost")
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(8)

        # 按 src 分组（保留传入顺序）
        user_entries = [e for e in entries if e[3] == "user"]
        llm_entries = [e for e in entries if e[3] == "llm"]

        def _add_group(title: str, group_entries: list) -> None:
            if not group_entries:
                return
            ttl = QLabel(f"<b>{title}</b>")
            ttl.setTextFormat(Qt.RichText)
            iv.addWidget(ttl)
            for fid, name, count, _src in group_entries:
                iv.addWidget(self._build_entry_row(fid, name, count))

        _add_group("你主动取消保留的：", user_entries)
        if user_entries and llm_entries:
            iv.addSpacing(4)
        _add_group("你批准的 LLM 删除建议：", llm_entries)

        iv.addStretch(1)
        scroll.setWidget(inner)
        v.addWidget(scroll, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("确认删除")
        bb.button(QDialogButtonBox.Cancel).setText("取消")
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _build_entry_row(self, fid: int, name: str, count: int) -> QWidget:
        w = QWidget()
        wv = QVBoxLayout(w)
        wv.setContentsMargins(12, 4, 4, 4)
        wv.setSpacing(2)

        if count <= 0:
            # 没数据的字段：只显示名字
            lbl = QLabel(f"• <b>{name}</b>　<span style='color:#888'>（无数据）</span>")
            lbl.setTextFormat(Qt.RichText)
            wv.addWidget(lbl)
            return w

        head = QLabel(
            f"• <b>{name}</b>　"
            f"<span style='color:#c62828'>{count} 个项目有数据</span>"
        )
        head.setTextFormat(Qt.RichText)
        wv.addWidget(head)

        rb_drop = QRadioButton("直接删除该字段及其所有相关数据")
        rb_append = QRadioButton(
            "保留数据：把每个项目的该字段值追加到「描述」末尾，再删除字段"
        )
        rb_drop.setChecked(True)
        # QButtonGroup 父对象用 self 即可，避免 GC
        grp = QButtonGroup(self)
        grp.addButton(rb_drop)
        grp.addButton(rb_append)
        # 缩进
        for rb in (rb_drop, rb_append):
            sub = QHBoxLayout()
            sub.setContentsMargins(20, 0, 0, 0)
            sub.addWidget(rb)
            sub.addStretch(1)
            wv.addLayout(sub)
        self._radios[fid] = (rb_drop, rb_append)
        self._choices[fid] = "drop"
        return w

    def _on_ok(self) -> None:
        self.append_for_fids = {
            fid for fid, (_d, rb_a) in self._radios.items() if rb_a.isChecked()
        }
        self.accept()


# =============================================================================
# 批量字段类型变更二次确认对话框（task #19 Phase B）
# =============================================================================
class _BatchTypeChangeConfirmDialog(QDialog):
    """库字段设计助手「应用」前的批量类型变更确认对话框。

    职责：
    * 列出所有要"原地改类型"的字段（每个 = 一条批准的 type_conflict）；
    * 每条说明：旧类型 → 新类型，N 条非空值保留（更新元数据时可能被覆盖），
      M 条 pending 建议会失效；
    * **不**含"清空旧 hint"的 checkbox —— LLM 已为新类型配套给了新 hint，
      apply 时会直接覆盖；
    * 跟 ``_BatchDeleteConfirmDialog`` 风格一致：滚动区 + 顶部说明 + 确认/取消。

    Args:
        entries: ``[(name, old_type, new_type, n_values, m_pending), ...]``
    """

    def __init__(
        self,
        entries: list[tuple[str, str, str, int, int]],
        *,
        parent=None,
    ) -> None:
        super().__init__(parent)
        from ...models import FIELD_TYPE_LABELS

        self.setWindowTitle("确认字段类型变更")
        self.setMinimumWidth(540)
        self.resize(620, 420)

        v = QVBoxLayout(self)
        v.setSpacing(10)

        n = len(entries)
        head = QLabel(
            f"将原地修改 <b>{n}</b> 个字段的类型：<br/>"
            "<span style='color:#666'>"
            "&nbsp;&nbsp;• 现有项目的字段值会<b>保留</b>在数据库里，但新类型的"
            "控件可能读不出来（切回旧类型即可恢复显示；"
            "<b>更新项目元数据时</b>若提交空值会被覆盖）；<br/>"
            "&nbsp;&nbsp;• 该字段挂着的待批准 LLM 建议会失效；<br/>"
            "&nbsp;&nbsp;• LLM 已为新类型配套提供了新的提取提示，将一并写入"
            "（覆盖旧 hint）。"
            "</span><br/><br/>"
            "该操作与字段创建 / 更新 / 删除走同一事务（任一失败整体回滚）。"
        )
        head.setTextFormat(Qt.RichText)
        head.setWordWrap(True)
        v.addWidget(head)

        scroll = QScrollArea()
        scroll.setObjectName("AppScrollArea")
        scroll.setWidgetResizable(True)
        from PySide6.QtWidgets import QFrame
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        inner.setObjectName("AppScrollHost")
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(6)
        for name, old_type, new_type, n_values, m_pending in entries:
            old_label = FIELD_TYPE_LABELS.get(old_type, old_type)
            new_label = FIELD_TYPE_LABELS.get(new_type, new_type)
            extras: list[str] = []
            if n_values > 0:
                extras.append(f"{n_values} 条非空值保留")
            if m_pending > 0:
                extras.append(f"{m_pending} 条 pending 建议失效")
            extra_txt = (
                "<span style='color:#666'>（" + "；".join(extras) + "）</span>"
                if extras else ""
            )
            row_lbl = QLabel(
                f"• <b>{name}</b>：{old_label} → <b>{new_label}</b> {extra_txt}"
            )
            row_lbl.setTextFormat(Qt.RichText)
            row_lbl.setWordWrap(True)
            iv.addWidget(row_lbl)
        iv.addStretch(1)
        scroll.setWidget(inner)
        v.addWidget(scroll, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("确认改类型")
        bb.button(QDialogButtonBox.Cancel).setText("取消")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


# =============================================================================
# 应用前汇总对话框（task #21）
# =============================================================================
class _ApplySummaryDialog(QDialog):
    """task #21：Step 2 点应用后的汇总对话框（在二次确认对话框之前）。

    职责：
    * 列出 5 类变更的具体字段（创建 / 改名 / 改类型 / 删除 / 更新提示），
      所有类目都展开字段名（不只是数字）；
    * 主按钮文案随 ``FieldPlan`` 内容动态切换（"应用" / "下一步：确认类型变更"
      / "下一步：确认删除" / "下一步：确认变更"），诚实告知后续还有几道
      二次确认；
    * 点取消 → 回 Step 2；点主按钮 → 关闭后由 ``_on_step2_apply`` 串联调用
      ``_BatchTypeChangeConfirmDialog`` / ``_BatchDeleteConfirmDialog``。

    Args:
        plan: 待应用的 FieldPlan
        fid_resolver: ``(fid, *, prefer_old: bool = False) -> str`` 回调，用于
            把 ``plan.renames`` / ``plan.type_changes`` / ``plan.deletes`` /
            ``plan.updates_hint`` 里的 fid 翻译成字段名（``prefer_old=True``
            时返回原始名，用于改名行的旧名）。``prefer_old`` 必须用 keyword
            传——wizard 的 `_fid_to_name` 把它声明为 keyword-only。如果不提供
            resolver，fid 退化为 ``#fid``。
    """

    def __init__(
        self, plan: "FieldPlan",
        *,
        fid_resolver=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("即将应用以下变更")
        self.setMinimumWidth(520)

        v = QVBoxLayout(self)
        v.setSpacing(10)

        head = QLabel("<b>即将应用以下变更：</b>")
        head.setTextFormat(Qt.RichText)
        v.addWidget(head)

        def _name(fid: int, *, prefer_old: bool = False) -> str:
            if fid_resolver is not None:
                try:
                    # 用 keyword 传 prefer_old：wizard._fid_to_name 把
                    # prefer_old 声明为 keyword-only，位置传参会抛 TypeError
                    # → 被吞掉后所有名字都退化成 #fid（task #21 round 4 修复）
                    return fid_resolver(fid, prefer_old=prefer_old)
                except Exception:  # noqa: BLE001
                    pass
            return f"#{fid}"

        # 5 类变更具体列表；为 0 的类目整行不显示
        lines: list[str] = []
        if plan.creates:
            names = "、".join(name for name, _t, _h in plan.creates)
            lines.append(
                f"📦 新增 <b>{len(plan.creates)}</b> 个字段："
                f"<span style='color:#2e7d32'>{names}</span>"
            )
        if plan.renames:
            renamed_pairs = "、".join(
                f"{_name(fid, prefer_old=True)} → {new_name}"
                for fid, new_name in plan.renames
            )
            lines.append(
                f"✏ 改名 <b>{len(plan.renames)}</b> 个字段："
                f"<span style='color:#1565c0'>{renamed_pairs}</span>"
            )
        if plan.type_changes:
            type_change_names = "、".join(
                _name(fid) for fid, _new_type, _new_hint in plan.type_changes
            )
            lines.append(
                f"⚠ 改类型 <b>{len(plan.type_changes)}</b> 个字段："
                f"<span style='color:#ef6c00'>{type_change_names}</span>"
                f"<span style='color:#666'>（需二次确认）</span>"
            )
        if plan.deletes:
            delete_names = "、".join(
                _name(fid, prefer_old=True) for fid in plan.deletes
            )
            lines.append(
                f"🗑 删除 <b>{len(plan.deletes)}</b> 个字段："
                f"<span style='color:#c62828'>{delete_names}</span>"
                f"<span style='color:#666'>（需二次确认）</span>"
            )
        if plan.updates_hint:
            update_names = "、".join(
                _name(fid) for fid, _new_hint in plan.updates_hint
            )
            lines.append(
                f"📝 更新提取提示 <b>{len(plan.updates_hint)}</b> 个字段："
                f"<span style='color:#666'>{update_names}</span>"
            )
        if not lines:
            lines.append("（没有可应用的变更）")
        body = QLabel("<br/>".join(lines))
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        v.addWidget(body)

        v.addStretch(1)

        # 按钮区
        btns = QHBoxLayout()
        btns.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_cancel)
        b_ok = QPushButton(summary_dialog_button_label(plan))
        b_ok.setDefault(True)
        b_ok.clicked.connect(self.accept)
        btns.addWidget(b_ok)
        v.addLayout(btns)

