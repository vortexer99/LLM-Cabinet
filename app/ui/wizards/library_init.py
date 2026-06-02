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

from PySide6.QtCore import QObject, Qt, QThread, Signal
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
    QMessageBox,
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
    #   'system_required'    ：系统必有字段（标题/描述/标签）— 强制选中、只能更新 prompt_hint
    #   'existing_user_field'：现有字段（用户字段或系统非必有），LLM 未在本次提及；
    #                          默认 selected=True 表示保留；用户取消勾选 → 删除
    #   'same_type'          ：LLM 输出命中现有用户字段且类型一致；现有 hint 空
    #                          → update_hint_only；非空 → 跳过不覆盖；
    #                          用户在预览页"删除" → selected=False → 走 delete
    #   'type_conflict'      ：LLM 输出命中现有但类型不同；默认改名 <原名>_v2
    #   'system_protected'   ：保留以兼容老路径（理论上 6/1 晚迭代后不会再产生）
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
        if self.status == "system_protected":
            return "skip"
        if self.status == "type_conflict":
            return "create" if self.rename_to.strip() else "skip"
        return "create"  # status == "new"

    @property
    def effective_name(self) -> str:
        """实际写入 fields 表时使用的名字（type_conflict 路径走重命名）。"""
        if self.status == "type_conflict" and self.rename_to.strip():
            return self.rename_to.strip()
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
            # 仅当 hint 真的会被覆盖（现有 hint 空 + 新 hint 非空）时才有意义
            return (
                not (self.existing_prompt_hint or "").strip()
                and bool((self.prompt_hint or "").strip())
            )
        return False


# 系统必有字段（is_required = True 的中文名）
# 注意：这 3 个名字与 db.DEFAULT_FIELDS 中保护字段一一对应；
# "作者/日期/评分/来源" 是 is_system 但 is_required = False
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


def annotate_conflicts(
    suggestions: list[dict],
    existing_fields: list,
    suggested_deletes: Optional[list[dict]] = None,
    suggested_renames: Optional[list[dict]] = None,
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
    """
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
            if (ex.is_system or ex.key in PROTECTED_FIELD_KEYS) and ex.type != a.type:
                # 系统字段类型与建议不符 → 跳过（罕见兜底）
                a.status = "system_protected"
                a.reason = "已存在的系统字段，类型与建议不符；跳过"
                a.selected = False
            elif ex.type == a.type:
                a.status = "same_type"
                if ex.prompt_hint:
                    a.reason = "已存在（类型一致），现有 LLM 提示非空 → 跳过不覆盖"
                else:
                    a.reason = "已存在（类型一致）→ 仅写入 LLM 提示"
                a.selected = True
            else:
                a.status = "type_conflict"
                a.reason = (
                    f"已存在但类型不同（现：{ex.type} / 建议：{a.type}），"
                    f"请改名后再创建"
                )
                a.rename_to = f"{a.name}_v2"
                a.selected = False
            out.append(a)
            continue

        # 现有字段，未被 LLM 命中：先看是不是在 LLM 显式改名建议名单里
        if ex.name in renames_by_old:
            new_name, reason = renames_by_old[ex.name]
            # 如果 LLM 在 fields[] 里同时给了 new_name 那一行（这是预期的，
            # 因为 prompt 要求 fields 是改名后的完整方案），把它的
            # prompt_hint 合并到 rename ann，并把那一行从"将被当成 new"中
            # 摘除（标记为已处理）；type 不允许通过 rename 改动，type 不一致
            # 时仍以现有 ex.type 为准（视觉提示 + 不构成 type_conflict）
            new_row_in_fields = sugg_by_name.get(new_name)
            merged_hint = ex.prompt_hint
            if new_row_in_fields is not None:
                handled_suggestion.add(new_name)
                if (new_row_in_fields.get("prompt_hint") or "").strip():
                    merged_hint = new_row_in_fields["prompt_hint"]
            a = AnnotatedSuggestion(
                name=ex.name, type=ex.type, prompt_hint=merged_hint,
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
            self.finished.emit(None, f"{type(e).__name__}: {e}", [], 0, 0)
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
PAGE_PREVIEW = 3


class LibraryInitWizard(WizardPlugin):
    """库字段设计助手（对外文案；内部沿用 wizard / library_init 命名）。"""

    meta = WizardMeta(
        id="library_init",
        title="库字段设计助手",
        description=(
            "用一段话描述这个库的目的与字段偏好；可在调用 LLM 前先调整字段；"
            "LLM 会基于现状给出新增 / 修改 / 删除建议，逐条批准或驳回。"
            "适合刚建好新库或需要重整字段结构时使用。"
        ),
        category="库初始化",
        icon="🪄",
        require_empty_lib=False,
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("库字段设计助手")
        self.resize(900, 640)
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
        self.lbl_tokens = QLabel("tokens：累计 0 in / 0 out")
        self.lbl_tokens.setProperty("muted", True)
        self.lbl_tokens.setToolTip(
            "本次会话累计的 token 用量（in = 发送，out = 接收）；"
            "实际计费按所用 provider 的口径"
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
        self.stack.addWidget(self._build_preview_page())
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
            "<li>整个过程会调用 LLM，使用「设置 → API」中默认 provider；"
            "顶部会实时累计 token 用量；</li>"
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
        b_cancel = QPushButton("取消")
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
                "请先在「设置 → API」中配置一个 provider 并设置默认。"
            )
            self.btn_intro_next.setEnabled(False)
            self.btn_intro_next.setToolTip(
                "请先在「设置 → API」中配置默认 provider 与 API Key"
            )
            return
        if not (active.api_key or "").strip():
            self.lbl_api_status.setText(
                f"<span style='color:#c62828'>⚠ 默认 provider "
                f"<b>{active.label()}</b> 未配置 API Key。</span>"
                f"<br>请到「设置 → API」中填入 Key 后重新打开本助手。"
            )
            self.btn_intro_next.setEnabled(False)
            self.btn_intro_next.setToolTip(
                f"未配置 {active.label()} 的 API Key"
            )
            return
        # 一切就绪
        from ...llm.providers import PROVIDERS
        provider_cls = PROVIDERS.get(active.id)
        supports_json = getattr(provider_cls, "supports_json_mode", True) if provider_cls else True
        json_mode_str = "JSON 原生模式" if supports_json else "Prompt 强约束模式"
        self.lbl_api_status.setText(
            f"<span style='color:#2e7d32'>✓ 已配置：</span>"
            f"<b>{active.label()}</b> · model=<code>{active.model}</code>"
            f" · {json_mode_str}"
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
            "<b>当前库的字段</b>　（可在调用 LLM 之前先调整；"
            "操作语义与「设置 → 字段」一致）"
        ))
        self.tbl_existing = QTableWidget(0, 5)
        # 第 4 列原本叫"LLM 建议"，与预览页的「LLM 字段方案建议」列同名，
        # 在助手语境下容易让用户误以为"是否参与 LLM 给出修改建议"，
        # 改名"参与建议"（语义：是否纳入 LLM 元数据建议流程）。
        # 「设置 → 字段」对话框里没有这个混淆问题，沿用原列名不动。
        self.tbl_existing.setHorizontalHeaderLabels(
            ["字段名", "类型", "显示", "参与建议", "LLM 提示"]
        )
        _h_suggest = self.tbl_existing.horizontalHeaderItem(3)
        if _h_suggest is not None:
            _h_suggest.setToolTip(
                "勾选后，该字段会出现在「LLM 元数据建议」流程的提问列表里\n"
                "（与「设置 → 字段 → LLM 建议」列含义一致）；\n"
                "与本助手在预览页给出的「字段方案建议」是完全独立的概念。"
            )
        self.tbl_existing.verticalHeader().setVisible(False)
        self.tbl_existing.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_existing.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_existing.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_existing.setShowGrid(False)
        self.tbl_existing.setAlternatingRowColors(True)
        h = self.tbl_existing.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.Fixed)
        h.setSectionResizeMode(3, QHeaderView.Fixed)
        h.setSectionResizeMode(4, QHeaderView.Fixed)
        self.tbl_existing.setColumnWidth(1, 160)
        self.tbl_existing.setColumnWidth(2, 56)
        self.tbl_existing.setColumnWidth(3, 84)
        self.tbl_existing.setColumnWidth(4, 96)
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
        b_cancel = QPushButton("取消")
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
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(8)

        self.lbl_preview_hint = QLabel(
            "<b>LLM 给出的字段方案</b>　最左侧「LLM 建议」列显示本轮变化"
            "（新增 / 修改 / 不变 / 删除）；可对每条建议「批准」或「驳回」，"
            "或用下方按钮新增 / 删除 / 调序字段。点「应用」一并写入。"
            "<br/><span style='color:#666'>※ 未决策的 LLM 新增 / 修改建议会被默认接受；"
            "LLM 删除建议则需显式「批准」才会执行。</span>"
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
        # （6/1 晚去除独立勾选列：保留/删除靠 LLM 建议列右侧"批准/驳回"
        #  与底部行操作按钮"删除"实现，避免 UI 元素重复）
        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(
            ["LLM 建议", "状态", "字段名", "类型", "LLM 提示"]
        )
        self.tbl.verticalHeader().setVisible(False)
        # 行高 38：要容下 LLM 建议列里的"批准/驳回"两个按钮
        self.tbl.verticalHeader().setDefaultSectionSize(38)
        self.tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl.setSelectionMode(QTableWidget.SingleSelection)
        h = self.tbl.horizontalHeader()
        # LLM 建议列固定宽 216：标签 + 「批准」/「驳回」两个 46+ 像素按钮 + 间距
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        self.tbl.setColumnWidth(0, 216)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Interactive)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl.setColumnWidth(2, 180)
        # 双击编辑器：字段名走 LineEdit（撑满 cell）；LLM 提示双击弹独立多行对话框
        self._name_delegate = _TallLineEditDelegate(self.tbl)
        self.tbl.setItemDelegateForColumn(2, self._name_delegate)
        self.tbl.cellDoubleClicked.connect(self._on_cell_double_clicked)
        v.addWidget(self.tbl, 1)

        # 行操作按钮（增 / 删 / 上下移）
        ops = QHBoxLayout()
        b_add = QPushButton("＋ 添加字段")
        b_add.setToolTip("在表末尾追加一个空白字段；填入名字后可像 LLM 建议一样应用")
        b_add.clicked.connect(self._on_preview_row_add)
        ops.addWidget(b_add)
        b_del_row = QPushButton("🗑 删除")
        b_del_row.setProperty("danger", True)
        b_del_row.setToolTip(
            "现有字段：标记为「将删除」（取消保留）；\n"
            "LLM 新建议：直接从列表移除"
        )
        b_del_row.clicked.connect(self._on_preview_row_delete)
        ops.addWidget(b_del_row)
        ops.addStretch(1)
        b_up = QPushButton("↑ 上移")
        b_up.clicked.connect(lambda: self._on_preview_row_move(-1))
        ops.addWidget(b_up)
        b_down = QPushButton("↓ 下移")
        b_down.clicked.connect(lambda: self._on_preview_row_move(1))
        ops.addWidget(b_down)
        v.addLayout(ops)

        # 警告区
        self.lbl_warnings = QLabel("")
        self.lbl_warnings.setWordWrap(True)
        self.lbl_warnings.setStyleSheet("color: #f57c00;")
        self.lbl_warnings.setVisible(False)
        v.addWidget(self.lbl_warnings)

        # LLM 原始响应：改成弹窗（按钮触发），节省预览页的垂直空间
        self.btn_show_raw = QPushButton("📄 查看 LLM 原始响应...")
        self.btn_show_raw.setToolTip(
            "弹出窗口显示本轮 LLM 的原始 JSON 响应；"
            "窗口里可一键「再次应用 LLM 建议」（智能合并保留你的手改）"
        )
        self.btn_show_raw.clicked.connect(self._on_show_raw_dialog)
        v.addWidget(self.btn_show_raw)

        # 底部按钮区
        btns = QHBoxLayout()
        self.btn_restart = QPushButton("🔄 重新开始")
        self.btn_restart.setToolTip("清空全部状态，回到场景描述页（轮数归零）")
        self.btn_restart.clicked.connect(self._on_restart)
        btns.addWidget(self.btn_restart)

        self.btn_refine = QPushButton("✏ 在当前基础上调整...")
        self.btn_refine.setToolTip(
            "弹补充说明输入框；将上次返回 + 用户编辑 + 补充一起再问一轮"
        )
        self.btn_refine.clicked.connect(self._on_refine)
        btns.addWidget(self.btn_refine)

        # 一键批准/驳回所有
        self.btn_approve_all = QPushButton("✓ 全部批准")
        self.btn_approve_all.setToolTip(
            "把本轮所有 LLM 提议（新增 / 修改）都标为已批准"
        )
        self.btn_approve_all.clicked.connect(self._on_approve_all)
        btns.addWidget(self.btn_approve_all)

        btns.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_cancel)

        self.btn_apply = QPushButton("✅ 应用（未决策按默认处理）")
        self.btn_apply.setDefault(True)
        self.btn_apply.setToolTip(
            "点击后立刻把表中的方案写入库。\n"
            "未点「批准 / 驳回」的条目按默认语义处理：\n"
            "  • LLM 新增 / 修改建议 → 视为默认接受，会被应用；\n"
            "  • LLM 删除建议 → 视为默认保留，不会删除（需显式批准才删）；\n"
            "  • 你保留勾选的现有字段 → 保留不动。\n"
            "删除操作仍会弹二次确认。"
        )
        self.btn_apply.clicked.connect(self._on_apply)
        btns.addWidget(self.btn_apply)
        v.addLayout(btns)
        return w

    # ---- 状态管理 ----------------------------------------------------------
    def _refresh_round_label(self) -> None:
        self.lbl_round.setText(f"轮数 {self._current_round} / {self._max_rounds}")
        # token 标签：累计值 + 上一轮增量（>0 才显示）
        tokens_text = (
            f"tokens：累计 {self._tokens_in_total} in / "
            f"{self._tokens_out_total} out"
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
            QMessageBox.information(
                self, "暂无内容",
                "本轮还没有 LLM 响应可供查看。",
            )
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("LLM 原始响应（本轮）")
        dlg.resize(720, 480)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)

        info = QLabel(
            "以下是本轮 LLM 返回的**原始 JSON**。如果你之前误删 / 误改 / 误驳回了"
            "某条建议，可以点下方「🔄 再次应用 LLM 建议」按钮 — 智能合并：你手加"
            "的字段、删除标记、改过的描述等都会保留，仅 LLM 原本触达过的字段会"
            "被重置为 LLM 给出的版本。"
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
        b_reapply.setToolTip("把 LLM 触达过的字段重置为 LLM 建议；保留你手加 / 删除的字段")
        if self._llm_round_payload is None:
            b_reapply.setEnabled(False)
            b_reapply.setToolTip("缺少本轮 LLM 快照（不应发生），无法再次应用")
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
        fresh = annotate_conflicts(
            self._llm_round_payload["fields"],
            self._llm_round_existing,
            suggested_deletes=self._llm_round_payload.get("fields_to_delete"),
            suggested_renames=self._llm_round_payload.get("fields_to_rename"),
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
                ans = QMessageBox.question(
                    self, "字段名重复",
                    f"字段名「{ua.name}」既被你手加过，也是 LLM 这一轮的新建议。\n\n"
                    f"  • 你手加的：type=<b>{ua.type}</b>，"
                    f"hint=<i>{(ua.prompt_hint or '（空）')[:60]}</i>\n"
                    f"  • LLM 的：type=<b>{llm_ann.type}</b>，"
                    f"hint=<i>{(llm_ann.prompt_hint or '（空）')[:60]}</i>\n\n"
                    f"是 = 保留<b>你手加</b>的版本（丢弃 LLM 的同名建议）；"
                    f"否 = 用 <b>LLM</b> 的版本覆盖（丢弃你手加的）。",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
                )
                if ans == QMessageBox.Yes:
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
        self._render_preview([])
        if parent_dlg is not None:
            parent_dlg.accept()

    def _on_cell_double_clicked(self, row: int, col: int) -> None:
        """LLM 提示列双击 → 弹独立多行编辑对话框。"""
        if col != 4:  # LLM 提示列（5 列布局）
            return
        if not (0 <= row < len(self._suggestions)):
            return
        ann = self._suggestions[row]
        it = self.tbl.item(row, 4)
        cur = it.text() if it else ann.prompt_hint
        title = f"编辑 LLM 提示 — {ann.name}"
        new_text, ok = _ask_text(
            self, title,
            "请输入该字段的 LLM 提示（多行；告诉 LLM 该字段的格式约束、示例等）：",
            initial=cur,
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
        self._refresh_change_cell(row)

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
            tag_suffix = ""
            if f.is_title:
                tag_suffix = "  (标题)"
            elif f.is_required:
                tag_suffix = "  (必有)"
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
            QMessageBox.warning(self, "失败", str(e))
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
            QMessageBox.warning(self, "失败", str(e))
            return
        self._reload_existing_fields_table()

    def _existing_field_toggle_visible(self, fid: int, visible: bool) -> None:
        self.repo.set_field_visible(fid, visible)

    def _existing_field_toggle_suggest(self, fid: int, enabled: bool) -> None:
        self.repo.set_field_suggest_enabled(fid, enabled)

    def _existing_field_change_type(self, fid: int, ftype: str) -> None:
        try:
            self.repo.set_field_type(fid, ftype)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "失败", str(e))
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
        from ..settings_dialog import _DeleteFieldChoiceDialog

        fid = self._existing_current_field_id()
        if fid is None:
            return
        f = self.repo.get_field(fid)
        if not f:
            return
        if f.is_required:
            QMessageBox.information(self, "提示", f"『{f.name}』字段不可删除。")
            return
        cnt = self.repo.count_field_filled(f)
        dlg = _DeleteFieldChoiceDialog(f.name, cnt, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.repo.delete_field(fid, append_to_description=dlg.append_to_desc)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "失败", str(e))
            return
        self._reload_existing_fields_table()

    def _existing_field_edit_prompt_hint(
        self, fid: int, name: str, current_hint: str,
    ) -> None:
        new_text, ok = _ask_text(
            self, f"LLM 提示 — {name}",
            f"为字段「{name}」自定义 LLM 建议时的格式说明。\n"
            "留空 = 使用默认；填写 = 在 user prompt 中追加为该字段的格式要求。",
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
            QMessageBox.warning(
                self, "请填写库描述",
                "请先描述这个库的目的与字段偏好。",
            )
            return
        # 合并语义后只有一个来源：用户输入即"库描述 / 使用场景"
        self._scenario_text = text
        self._library_desc_input = text
        self._history = []
        self._dispatch_call(extra="")

    def _on_restart(self) -> None:
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
            QMessageBox.information(
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
            QMessageBox.warning(
                self, "未配置 API Key",
                "请先到「设置 → API」配置默认 provider 的 API Key。",
            )
            return
        try:
            provider = get_provider(active)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Provider 创建失败", str(e))
            return

        use_json_mode = bool(getattr(provider, "supports_json_mode", True))
        self.lbl_running_mode.setText(
            f"{active.label()} · model={active.model} · "
            f"模式={'JSON 原生' if use_json_mode else 'Prompt 强约束'}"
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
            QMessageBox.critical(self, "LLM 调用失败", raw_text)
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
    _STATUS_DISPLAY = {
        "new": ("✅ 新字段", "将创建该字段"),
        "system_required": ("⭐ 系统必有", "保护字段，将更新 LLM 提示"),
        "existing_user_field": ("📝 现有字段", "保留；点行操作的「删除」按钮可标记删除"),
        "system_protected": ("🔒 系统字段", "受保护，跳过"),
        "same_type": ("🔁 现有 · 同类型", "现有字段，将更新 LLM 提示"),
        "type_conflict": ("⚠ 类型冲突", "已存在但类型不同；请改名后再创建"),
        "llm_suggest_delete": (
            "🗑 LLM 建议删除",
            "LLM 显式建议删除该现有字段（hover 看理由）；批准 → 真删，驳回 → 保留",
        ),
        "llm_suggest_rename": (
            "✎ LLM 建议改名",
            "LLM 显式建议改名该现有字段（hover 看理由 / 新名）；"
            "批准 → 保留 fid 改名（项目历史值不丢），驳回 → 保留原名",
        ),
    }

    def _render_preview(self, warnings: list[str]) -> None:
        self.tbl.setRowCount(0)
        for row, ann in enumerate(self._suggestions):
            self.tbl.insertRow(row)

            # 0：LLM 建议（标签 + 批准/驳回按钮；只在有变化时显示按钮）
            self._make_change_cell(row, ann)

            # 1：状态
            label, default_tip = self._STATUS_DISPLAY.get(
                ann.status, (ann.status, ""),
            )
            it_status = QTableWidgetItem(label)
            it_status.setFlags(it_status.flags() & ~Qt.ItemIsEditable)
            it_status.setToolTip(ann.reason or default_tip)
            # existing_user_field 取消勾选 → 红字 "🗑 将删除"
            if ann.status == "existing_user_field" and not ann.selected:
                it_status.setText("🗑 将删除")
                from PySide6.QtGui import QColor
                it_status.setForeground(QColor("#c62828"))
            # llm_suggest_delete：默认红字（待批准）；批准后 selected=True 也保持红字
            # （此时表示"将真删"），更明确的标签由用户决策状态体现
            elif ann.status == "llm_suggest_delete":
                from PySide6.QtGui import QColor
                if ann.selected:
                    it_status.setText("🗑 将删除（已批准）")
                it_status.setForeground(QColor("#c62828"))
            # llm_suggest_rename：蓝字 + 在状态文字里直接附上新名，便于一眼看到
            elif ann.status == "llm_suggest_rename":
                from PySide6.QtGui import QColor
                tail = f" → {ann.llm_rename_new_name}" if ann.llm_rename_new_name else ""
                if ann.selected:
                    it_status.setText(f"✎ 将改名{tail}（已批准）")
                else:
                    it_status.setText(f"✎ LLM 建议改名{tail}")
                it_status.setForeground(QColor("#1565c0"))
            self.tbl.setItem(row, 1, it_status)

            # 2：字段名（type_conflict 行用 LineEdit 让用户改名；其它只读）
            if ann.status == "type_conflict":
                w = QWidget()
                hl = QHBoxLayout(w)
                hl.setContentsMargins(2, 2, 2, 2)
                hl.setSpacing(4)
                lbl_old = QLabel(ann.name + " →")
                lbl_old.setProperty("muted", True)
                hl.addWidget(lbl_old)
                ed = QLineEdit(ann.rename_to)
                ed.setMinimumWidth(120)
                ed.textChanged.connect(
                    lambda t, idx=row: self._on_rename_changed(idx, t)
                )
                hl.addWidget(ed, 1)
                self.tbl.setCellWidget(row, 2, w)
            else:
                it_name = QTableWidgetItem(ann.name)
                # 系统字段名不可改；same_type / existing_user_field 也不允许改名
                # （避免与既有数据脱钩）；新建（new）允许双击编辑；
                # llm_suggest_delete / llm_suggest_rename：原名只读（rename 的新名
                # 通过批准操作生效，不在这里编辑）
                if ann.status in (
                    "system_required", "system_protected", "same_type",
                    "existing_user_field", "llm_suggest_delete",
                    "llm_suggest_rename",
                ):
                    it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)
                self.tbl.setItem(row, 2, it_name)

            # 3：类型
            cmb = QComboBox()
            cmb.setMinimumHeight(28)
            cmb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            for t in FIELD_TYPES:
                cmb.addItem(FIELD_TYPE_LABELS.get(t, t), t)
            if ann.type == "tags":
                cmb.addItem(FIELD_TYPE_LABELS.get("tags", "标签（多值）"), "tags")
            idx_t = cmb.findData(ann.type)
            cmb.setCurrentIndex(max(0, idx_t))
            # 现有字段（含已被 LLM 命中的）类型不允许变（避免脱钩历史数据）
            if ann.status in (
                "system_required", "system_protected", "same_type",
                "existing_user_field", "llm_suggest_delete",
                "llm_suggest_rename",
            ):
                cmb.setEnabled(False)
            cmb.currentIndexChanged.connect(
                lambda _i, idx=row, c=cmb: self._on_type_changed(
                    idx, c.currentData(),
                )
            )
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
        self._refresh_desc_decision_ui()
        self._refresh_round_label()

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

        if show_buttons:
            b_ok = QPushButton("批准")
            b_ok.setMinimumWidth(46)
            b_ok.setFixedHeight(26)
            b_ok.setToolTip("批准这条 LLM 建议（立即把 LLM 的 type / hint 固化到该字段）")
            b_ok.clicked.connect(
                lambda _c=False, idx=row: self._on_decision_changed(idx, "approved")
            )
            hl.addWidget(b_ok)

            b_no = QPushButton("驳回")
            b_no.setMinimumWidth(46)
            b_no.setFixedHeight(26)
            b_no.setToolTip(
                "驳回（立即把该字段还原到 LLM 提建议之前的状态；"
                "新增字段会被移除，可以基于还原后的内容继续修改）"
            )
            b_no.clicked.connect(
                lambda _c=False, idx=row: self._on_decision_changed(idx, "rejected")
            )
            hl.addWidget(b_no)

        hl.addStretch(1)
        self.tbl.setCellWidget(row, 0, w)

    def _refresh_change_cell(self, row: int) -> None:
        if 0 <= row < len(self._suggestions):
            self._make_change_cell(row, self._suggestions[row])

    def _on_decision_changed(self, idx: int, decision: str) -> None:
        """批准 / 驳回按钮：**立即**把决定固化到 ann，并重画对应行。

        语义（6/1 晚最终版）：
        * **批准**：把 LLM 给的 type / hint 固定下来；用户后续依然可以再修改字段名 /
          类型 / hint，但 LLM 建议列只显示"已批准"
        * **驳回**：把 ann 还原到 LLM 提建议**之前**的状态：
          - ``new``           → 直接从 ``_suggestions`` 移除（库里就当 LLM 没建议过）
          - ``same_type``     → hint 还原为现有字段的旧 hint（type 本来就一致）
          - ``system_required``→ 同上，还原 hint
          - ``type_conflict`` → ``rename_to`` 清空 + ``selected=False``，等价"按现有字段不动"
          建议列显示"已驳回"，用户可以基于还原后的状态继续手改

        如果用户再次点同一个按钮 → 视为撤回决定，但**不能**自动复原 LLM 内容
        （因为 ann 已经被改写过了）；想要回到 LLM 原版 → 用「再次应用 LLM 建议」。
        """
        if not (0 <= idx < len(self._suggestions)):
            return
        ann = self._suggestions[idx]

        # toggle：再次点同一按钮 → 退回 pending（仅清掉标记；不"反向操作"，
        # 因为内容已被前一次操作改过；想恢复 LLM 建议请用弹窗里的"再次应用"）
        if ann.decision == decision:
            ann.decision = "pending"
            self._refresh_change_cell(idx)
            return

        if decision == "approved":
            ann.decision = "approved"
            # 批准 type_conflict：补 selected=True 才能进 create 路径
            if ann.status == "type_conflict":
                ann.selected = True
            # 批准 llm_suggest_delete：补 selected=True 让 action 进 delete 路径
            elif ann.status == "llm_suggest_delete":
                ann.selected = True
            # 批准 llm_suggest_rename：补 selected=True 让 action 进 rename 路径
            elif ann.status == "llm_suggest_rename":
                ann.selected = True
            self._refresh_change_cell(idx)
            # llm_suggest_delete / llm_suggest_rename 批准/驳回会改变状态列文字
            # （"将删除（已批准）" / "✎ 将改名 → ..."），整表重画一次更稳
            if ann.status in ("llm_suggest_delete", "llm_suggest_rename"):
                self._render_preview([])
            return

        # decision == "rejected"
        if ann.status == "new":
            # 直接从列表移除
            del self._suggestions[idx]
            self._render_preview([])
            new_row = min(idx, len(self._suggestions) - 1)
            if new_row >= 0:
                self.tbl.setCurrentCell(new_row, 2)
            return
        if ann.status == "llm_suggest_delete":
            # 驳回 LLM 删除建议 → 退化为普通 existing_user_field（保留）
            ann.status = "existing_user_field"
            ann.selected = True
            ann.decision = "rejected"
            ann.reason = (
                "LLM 曾建议删除此字段，已被你驳回；保留中。"
                "如果想删除，可以点行操作的「删除」按钮。"
            )
            self._render_preview([])
            return
        if ann.status == "llm_suggest_rename":
            # 驳回 LLM 改名建议 → 退化为普通 existing_user_field（保留原名）
            ann.status = "existing_user_field"
            ann.selected = True
            ann.decision = "rejected"
            ann.llm_rename_new_name = ""
            ann.reason = (
                "LLM 曾建议改名此字段，已被你驳回；保留原名。"
                "如果想改名，可以用场景页的「✎ 重命名」按钮。"
            )
            self._render_preview([])
            return

        # 其它 status：回滚 ann 内容到 LLM 建议之前的状态
        ann.decision = "rejected"
        if ann.status in ("same_type", "system_required"):
            ann.prompt_hint = ann.existing_prompt_hint or ""
            # 同步表格里的 LLM 提示单元格
            it_h = self.tbl.item(idx, 4)
            if it_h is not None:
                it_h.setText(ann.prompt_hint)
        elif ann.status == "type_conflict":
            ann.rename_to = ""
            ann.selected = False
            # 同步类型列的 ComboBox 回到现有字段类型
            if ann.existing_field_type:
                ann.type = ann.existing_field_type
        # system_protected / existing_user_field：本来就不算 LLM 建议（前者跳过，
        # 后者由用户行操作"删除"驱动），到这里只标 decision
        self._render_preview([])

    def _on_approve_all(self) -> None:
        """一键把所有未决条目标为 approved（仅影响有变化的 LLM 建议）。

        对 ``type_conflict`` / ``llm_suggest_delete`` / ``llm_suggest_rename``
        同步把 ``selected`` 提到 True。
        库描述如果 LLM 改过且尚未决策，也一并标为 approved。
        """
        for ann in self._suggestions:
            if ann.has_llm_change and ann.decision != "approved":
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
        """根据 _desc_has_llm_change() + _library_desc_decision 控制按钮/标签可见性。"""
        has_change = self._desc_has_llm_change()
        if not has_change:
            # LLM 这轮没改描述 → 全部隐藏
            self.lbl_desc_decision.setVisible(False)
            self.btn_desc_approve.setVisible(False)
            self.btn_desc_reject.setVisible(False)
            return
        if self._library_desc_decision == "pending":
            self.lbl_desc_decision.setVisible(False)
            self.btn_desc_approve.setVisible(True)
            self.btn_desc_reject.setVisible(True)
            return
        # 已决策 → 隐藏按钮，只留状态标签
        self.btn_desc_approve.setVisible(False)
        self.btn_desc_reject.setVisible(False)
        if self._library_desc_decision == "approved":
            self.lbl_desc_decision.setText(
                "<span style='color:#2e7d32; background:rgba(46,125,50,0.12); "
                "border-radius:4px; padding:1px 6px;'>已批准</span>"
            )
        else:  # rejected
            self.lbl_desc_decision.setText(
                "<span style='color:#757575; background:rgba(117,117,117,0.12); "
                "border-radius:4px; padding:1px 6px;'>已驳回</span>"
            )
        self.lbl_desc_decision.setTextFormat(Qt.RichText)
        self.lbl_desc_decision.setVisible(True)


    # ---- 预览页行操作（增 / 删 / 上下移） ---------------------------------
    def _current_preview_row(self) -> int:
        return self.tbl.currentRow()

    def _on_preview_row_add(self) -> None:
        """在末尾追加一条空白「new」字段，用户在表里直接编辑名字与类型。"""
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(
            self, "添加字段",
            "字段名（默认类型：单行文本，可在表格中改）：",
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        # 防止与现有 ann 重名
        existing_names = {a.effective_name for a in self._suggestions}
        if name in existing_names:
            QMessageBox.warning(
                self, "字段名重复",
                f"「{name}」已经在列表中存在，请换一个名字。",
            )
            return
        ann = AnnotatedSuggestion(name=name, type="text", prompt_hint="")
        ann.status = "new"
        ann.selected = True
        ann.llm_touched = False  # 用户手动加的，不是 LLM 建议
        self._suggestions.append(ann)
        # 整表重画并把光标定位到新行
        self._render_preview([])
        self.tbl.setCurrentCell(len(self._suggestions) - 1, 2)

    def _on_preview_row_delete(self) -> None:
        """删除当前选中行。

        * ``new`` / ``type_conflict`` / 用户手加（``llm_touched=False`` 的 new）：
          直接从 ``_suggestions`` 中移除
        * ``llm_suggest_delete``：等价"批准 LLM 的删除建议"（selected=True + decision=approved），
          状态列变红字"将删除（已批准）"
        * ``llm_suggest_rename``：用户表态"我连这字段都不想要了" → 退化为
          ``existing_user_field`` + ``selected=False``（标记删除），llm_rename_new_name 清空
        * ``existing_user_field`` / ``same_type`` / ``system_protected``：
          设 selected=False（标记为"将删除"）
        * ``system_required``：拒绝删除（弹消息）
        """
        r = self._current_preview_row()
        if r < 0 or r >= len(self._suggestions):
            return
        ann = self._suggestions[r]
        if ann.status == "system_required":
            QMessageBox.information(
                self, "无法删除",
                f"「{ann.name}」是系统必有字段，无法删除。",
            )
            return
        if ann.status in ("new", "type_conflict"):
            # 直接从列表移除（type_conflict 也只是 LLM 建议未落地，移除即可）
            del self._suggestions[r]
            self._render_preview([])
            new_row = min(r, len(self._suggestions) - 1)
            if new_row >= 0:
                self.tbl.setCurrentCell(new_row, 2)
            return
        if ann.status == "llm_suggest_delete":
            # 行操作"删除" = 批准 LLM 的删除建议
            ann.selected = True
            ann.decision = "approved"
            self._render_preview([])
            return
        if ann.status == "llm_suggest_rename":
            # 用户的意思是"这字段我不要了，不用改名"：先转成普通现有字段，
            # 再走标记删除路径
            ann.status = "existing_user_field"
            ann.llm_rename_new_name = ""
            ann.decision = "pending"
            ann.reason = (
                "LLM 曾建议改名此字段，被你转为删除；将在「应用」时删除该字段。"
            )
            self._apply_selected_change(r, False)
            self._render_preview([])
            return
        # existing_user_field / same_type / system_protected → 标记将删除
        self._apply_selected_change(r, False)

    def _on_preview_row_move(self, delta: int) -> None:
        """上下移当前选中行。

        约束：
        * 系统必有字段（system_required）始终保持在最前；不允许移动它们，
          也不允许其它字段越过它们（target 不能落到 system_required 区段里）。
        """
        r = self._current_preview_row()
        if r < 0 or r >= len(self._suggestions):
            return
        target = r + delta
        if target < 0 or target >= len(self._suggestions):
            return
        moving = self._suggestions[r]
        if moving.status == "system_required":
            QMessageBox.information(
                self, "无法移动",
                "系统必有字段（标题 / 描述 / 标签）固定在最前，无法重排。",
            )
            return
        if self._suggestions[target].status == "system_required":
            QMessageBox.information(
                self, "无法移动",
                "不能越过系统必有字段；其余字段必须排在其后。",
            )
            return
        self._suggestions[r], self._suggestions[target] = (
            self._suggestions[target], self._suggestions[r],
        )
        self._render_preview([])
        self.tbl.setCurrentCell(target, 2)

    def _wrap_cell(self, tbl: QTableWidget, row: int, col: int, widget) -> None:
        """把 widget 居中包进单元格。"""
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch(1)
        hl.addWidget(widget)
        hl.addStretch(1)
        tbl.setCellWidget(row, col, w)

    def _apply_selected_change(self, idx: int, on: bool) -> None:
        """同步行的 selected 变更到表格视觉（状态列文字 / 红字）。

        历史：旧版本有勾选列 + ``_on_row_checkbox_toggled`` 信号槽；6/1 晚去掉勾选
        列后，"保留 / 删除"由顶部行操作按钮（_on_preview_row_delete）触发，
        本函数被复用为视觉刷新入口。
        """
        if not (0 <= idx < len(self._suggestions)):
            return
        ann = self._suggestions[idx]
        ann.selected = on
        # existing_user_field / same_type 的"取消保留"都意味着应用时真删该字段
        # （same_type 的 action 属性现在也对 selected=False 走 delete 路径）；
        # 共享同一套视觉刷新规则
        if ann.status in ("existing_user_field", "same_type"):
            it = self.tbl.item(idx, 1)  # 状态列在新 5 列布局是第 1 列
            if it is None:
                return
            from PySide6.QtGui import QColor
            if on:
                label, _ = self._STATUS_DISPLAY[ann.status]
                it.setText(label)
                # 复用默认前景（重置颜色）
                it.setData(Qt.ForegroundRole, None)
            else:
                it.setText("🗑 将删除")
                it.setForeground(QColor("#c62828"))
        # 现有字段切到删除/保留也会改变 LLM 建议列的标签（"删除" / ""）
        self._refresh_change_cell(idx)

    def _on_type_changed(self, idx: int, new_type: str) -> None:
        if 0 <= idx < len(self._suggestions):
            self._suggestions[idx].type = new_type

    def _on_rename_changed(self, idx: int, new_name: str) -> None:
        if 0 <= idx < len(self._suggestions):
            self._suggestions[idx].rename_to = new_name

    # ---- 收集 / 应用 -------------------------------------------------------
    def _collect_user_edited_payload(self) -> dict:
        """从表格里读出当前编辑后的 fields，回灌给 history 用。

        副作用：同时把库描述编辑框的内容同步回 ``self._library_desc_suggested``，
        让下一轮 ``_dispatch_call`` 拿到用户改过的版本。
        """
        out = []
        for row, ann in enumerate(self._suggestions):
            # name（系统必有字段不允许改名；只在 type_conflict 路径走 rename_to）
            if ann.status == "type_conflict":
                name = ann.effective_name
            elif ann.status == "system_required":
                name = ann.name  # 强制不动
            else:
                it = self.tbl.item(row, 2)  # 字段名列（5 列布局）
                name = it.text().strip() if it else ann.name
                ann.name = name
            # hint
            it_h = self.tbl.item(row, 4)  # LLM 提示列（5 列布局）
            hint = it_h.text() if it_h else ann.prompt_hint
            ann.prompt_hint = hint
            out.append({"name": name, "type": ann.type, "prompt_hint": hint})

        # 库描述：从预览页编辑框收回，覆盖 _library_desc_suggested
        edited_desc = self.ed_preview_library_desc.toPlainText().strip()
        self._library_desc_suggested = edited_desc

        return {
            "fields": out,
            "library_description": edited_desc,
        }

    def _on_apply(self) -> None:
        if self.repo is None:
            return
        # 同步用户的最新编辑
        self._collect_user_edited_payload()

        to_create: list[tuple[str, str, str]] = []
        to_update_hint: list[tuple[int, str]] = []
        to_delete: list[int] = []
        to_rename: list[tuple[int, str]] = []
        rename_names: list[tuple[str, str]] = []  # (old, new)，仅用于结果消息
        delete_names: list[str] = []  # 用于二次确认对话框
        # 删除来源：'user' = 用户主动取消保留；'llm' = 批准 LLM 删除建议
        delete_sources: list[str] = []
        for ann in self._suggestions:
            act = ann.action
            if act == "create":
                name = ann.effective_name
                if not name:
                    QMessageBox.warning(
                        self, "字段名为空",
                        f"建议「{ann.name}」需要设置一个有效的名字（或重命名）后才能创建。",
                    )
                    return
                to_create.append((name, ann.type, ann.prompt_hint))
            elif act == "update_hint_only":
                if ann.existing_field_id is not None:
                    to_update_hint.append(
                        (ann.existing_field_id, ann.prompt_hint),
                    )
            elif act == "delete":
                if ann.existing_field_id is not None:
                    to_delete.append(ann.existing_field_id)
                    delete_names.append(ann.name)
                    delete_sources.append(
                        "llm" if ann.status == "llm_suggest_delete" else "user"
                    )
            elif act == "rename":
                if ann.existing_field_id is not None and ann.llm_rename_new_name:
                    to_rename.append(
                        (ann.existing_field_id, ann.llm_rename_new_name),
                    )
                    rename_names.append((ann.name, ann.llm_rename_new_name))

        # 同 batch 内可能改名后撞上别的待创建字段，做一次预检
        names = [n for n, _, _ in to_create]
        if len(set(names)) != len(names):
            dups = sorted({n for n in names if names.count(n) > 1})
            QMessageBox.warning(
                self, "字段名重复",
                f"以下字段在本次创建中重复：{', '.join(dups)}。请改名后再应用。",
            )
            return

        # 删除二次确认（仅当有删除时）
        append_for_fids: set[int] = set()
        if to_delete:
            # 统计每个待删字段的填充率 + 来源
            entries: list[tuple[int, str, int, str]] = []  # (fid, name, count, src)
            for fid, name, src in zip(to_delete, delete_names, delete_sources):
                f = self.repo.get_field(fid)
                if f is None:
                    entries.append((fid, name, 0, src))
                    continue
                try:
                    n = self.repo.count_field_filled(f)
                except Exception:
                    n = 0
                entries.append((fid, name, n, src))

            dlg = _BatchDeleteConfirmDialog(entries, parent=self)
            if dlg.exec() != QDialog.Accepted:
                return
            append_for_fids = dlg.append_for_fids

        # 事务化批量应用：改名 + 创建 + 更新 hint + 删除
        try:
            new_ids, n_deleted, n_renamed = self.repo.apply_field_plan_batch(
                to_create, to_update_hint, to_delete,
                append_for_fids=append_for_fids,
                renames=to_rename,
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(
                self, "应用失败",
                f"应用字段方案时出错，已回滚（库内字段表无变化）：\n{e}",
            )
            return  # 不关闭助手，让用户继续修改

        # 库描述：一并写入 settings（事务外，幂等）
        new_desc = self._library_desc_suggested.strip()
        n_desc_changed = 0
        if new_desc:
            cur_desc = self.repo.get_setting("library_description", "") or ""
            if new_desc != cur_desc:
                self.repo.set_setting("library_description", new_desc)
                n_desc_changed = 1

        n_new = len(new_ids)
        n_upd = len(to_update_hint)
        msg_parts = [
            f"已新建字段 {n_new} 个",
            f"更新已存在字段的 LLM 提示 {n_upd} 个",
        ]
        if n_renamed:
            renamed_pairs = "、".join(f"{o}→{n}" for o, n in rename_names)
            msg_parts.append(f"改名 {n_renamed} 个（{renamed_pairs}）")
        if n_deleted:
            msg_parts.append(f"删除字段 {n_deleted} 个")
        if n_desc_changed:
            msg_parts.append("已更新库描述")
        QMessageBox.information(
            self, "已应用", "，".join(msg_parts) + "。",
        )
        # 标记一次性（即使再开也不影响功能，仅供未来"是否运行过助手"判断）
        try:
            self.repo.set_setting("library_init_wizard_done", "1")
        except Exception:
            pass
        self._applied = True
        self.accept()


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
) -> tuple[str, bool]:
    """多行输入弹窗。返回 (text, accepted)。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(540, 320)
    v = QVBoxLayout(dlg)
    lbl = QLabel(prompt)
    lbl.setWordWrap(True)
    v.addWidget(lbl)
    ed = QPlainTextEdit()
    if initial:
        ed.setPlainText(initial)
    v.addWidget(ed, 1)
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
        scroll.setWidgetResizable(True)
        from PySide6.QtWidgets import QFrame
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
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
