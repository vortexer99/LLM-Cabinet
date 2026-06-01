"""库初始化向导（task #11 T3 第一个 WizardPlugin）。

让用户用自然语言描述使用场景，LLM 给出"该库适合什么字段结构 + 每个字段的 prompt_hint"
建议；用户可编辑、上下移、删除、重新生成；最后事务化批量写入 ``fields`` 表。

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
    QSpinBox,
    QStackedWidget,
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
    # 状态分类：'new' / 'system_protected' / 'same_type' / 'type_conflict'
    status: str = "new"
    reason: str = ""
    existing_field_id: Optional[int] = None
    existing_field_type: str = ""
    existing_prompt_hint: str = ""
    rename_to: str = ""
    selected: bool = True

    @property
    def action(self) -> str:
        """导出给应用阶段的"动作"标签。"""
        if not self.selected:
            return "skip"
        if self.status == "system_protected":
            return "skip"
        if self.status == "same_type":
            return "update_hint_only" if not self.existing_prompt_hint else "skip"
        if self.status == "type_conflict":
            return "create" if self.rename_to.strip() else "skip"
        return "create"  # status == "new"

    @property
    def effective_name(self) -> str:
        """实际写入 fields 表时使用的名字（type_conflict 路径走重命名）。"""
        if self.status == "type_conflict" and self.rename_to.strip():
            return self.rename_to.strip()
        return self.name


_SYSTEM_FIELD_NAMES = {"标题", "描述", "标签"}


def parse_and_validate(text: str) -> tuple[dict, list[str]]:
    """把 LLM 返回文本解析成 ``{"fields": [...]}``。

    Returns:
        ``(payload, warnings)``。
        - payload['fields']: list of {"name", "type", "prompt_hint"}；
          未知 type 已 fallback 为 ``text`` 并加 warning。
        - payload['default_tags_suggestion']: 可选 list[str]。

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
        if ftype not in FIELD_TYPES:
            warnings.append(f"字段「{name}」类型 {ftype!r} 不合法，已 fallback 为 text")
            ftype = "text"
        hint = (item.get("prompt_hint") or "").strip()
        cleaned.append({"name": name, "type": ftype, "prompt_hint": hint})

    if not cleaned:
        raise WizardLLMOutputError("未解析到任何有效字段建议")

    payload: dict = {"fields": cleaned}
    tags = data.get("default_tags_suggestion")
    if isinstance(tags, list):
        payload["default_tags_suggestion"] = [
            t.strip() for t in tags if isinstance(t, str) and t.strip()
        ]
    return payload, warnings


def annotate_conflicts(
    suggestions: list[dict],
    existing_fields: list,
) -> list[AnnotatedSuggestion]:
    """给每条建议打冲突状态标签。"""
    by_name = {f.name: f for f in existing_fields}
    out: list[AnnotatedSuggestion] = []
    for s in suggestions:
        a = AnnotatedSuggestion(
            name=s["name"], type=s["type"], prompt_hint=s.get("prompt_hint", ""),
        )
        # 1) 系统字段中文名硬命中
        if a.name in _SYSTEM_FIELD_NAMES:
            ex = by_name.get(a.name)
            a.status = "system_protected"
            a.reason = "系统保护字段，由应用内置维护，不可重复创建"
            a.selected = False
            if ex is not None:
                a.existing_field_id = ex.id
                a.existing_field_type = ex.type
                a.existing_prompt_hint = ex.prompt_hint
            out.append(a)
            continue

        ex = by_name.get(a.name)
        if ex is None:
            a.status = "new"
            a.selected = True
        else:
            a.existing_field_id = ex.id
            a.existing_field_type = ex.type
            a.existing_prompt_hint = ex.prompt_hint
            if ex.is_system or ex.key in PROTECTED_FIELD_KEYS:
                a.status = "system_protected"
                a.reason = "已存在的系统字段，不可覆盖"
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
    return out


# =============================================================================
# Prompt
# =============================================================================
_SYSTEM_PROMPT = (
    "你是一位帮助用户规划本地资料库字段结构的助手。"
    "用户会描述他们要管理的内容类型（例如：学术论文、游戏素材、菜谱、漫画……），"
    "你的任务是给出一份精炼的字段方案。\n\n"
    "硬性要求：\n"
    "1. 用户库已经内置了「标题 / 作者 / 日期 / 评分 / 来源 / 标签 / 描述」7 个系统字段，"
    "**不要**在 fields 数组里重复出现这些名字。\n"
    "2. 输出必须是 JSON 对象（不要 markdown 代码块、不要解释文字），结构如下：\n"
    "{\n"
    '  "fields": [\n'
    '    {"name": "字段名", "type": "text|textarea|date|url|rating|number", '
    '"prompt_hint": "对该字段在 LLM 建议时的格式说明（30~120 字）"},\n'
    "    ...\n"
    "  ],\n"
    '  "default_tags_suggestion": ["建议常用标签 1", "建议常用标签 2"]\n'
    "}\n"
    "3. type 必须是 text / textarea / date / url / rating / number 之一。\n"
    "4. 总字段数控制在 3~10 个，仅添加该领域真正常用且高区分度的字段。\n"
    "5. prompt_hint 用中文，给出该字段的填写格式约束（长度、风格、示例），不超过 200 字。"
)


def build_messages(
    user_scenario: str,
    history: list[dict],
    extra_instruction: str = "",
) -> list[dict]:
    """组装 messages（历史以文本形式回放，跨 provider 最稳）。"""
    messages: list[dict] = [
        {"role": "system", "content": [{"type": "text", "text": _SYSTEM_PROMPT}]},
    ]
    parts: list[str] = [f"使用场景描述：\n{user_scenario.strip()}"]
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

    finished = Signal(object, str, list)
    # finished(payload_or_None, raw_text_or_error, warnings_list)

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
            self.finished.emit(None, f"{type(e).__name__}: {e}", [])
            return
        try:
            payload, warnings = parse_and_validate(resp.text or "")
        except WizardLLMOutputError as e:
            self.finished.emit(None, f"模型输出不规范：{e}\n\n原始响应：\n{resp.text}", [])
            return
        self.finished.emit(payload, resp.text or "", warnings)


# =============================================================================
# 主向导对话框
# =============================================================================
PAGE_INTRO = 0
PAGE_SCENARIO = 1
PAGE_RUNNING = 2
PAGE_PREVIEW = 3


class LibraryInitWizard(WizardPlugin):
    """库初始化向导。"""

    meta = WizardMeta(
        id="library_init",
        title="库初始化向导",
        description=(
            "用自然语言描述使用场景，让 LLM 推荐字段结构与每个字段的格式说明。"
            "适合刚建好新库时使用。"
        ),
        category="库初始化",
        icon="🪄",
        require_empty_lib=False,
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("库初始化向导")
        self.resize(900, 640)
        self.repo = None
        self.library = None
        self._max_rounds = DEFAULT_MAX_ROUNDS
        self._current_round = 0
        self._scenario_text = ""
        self._last_raw_response = ""
        self._history: list[dict] = []
        self._suggestions: list[AnnotatedSuggestion] = []
        self._tags_suggestion: list[str] = []
        self._applied = False
        self._thread: Optional[QThread] = None
        self._worker: Optional[_WizardLLMWorker] = None

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
                f"⚠ 当前库已有 {n} 个项目；运行向导仍会<b>追加</b>字段而非替换，"
                "已有数据不受影响。"
            )
            self.lbl_warn.setVisible(True)
        else:
            self.lbl_warn.setVisible(False)
        self.exec()
        return self._applied

    # ---- UI 构建 -----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        top = QHBoxLayout()
        ttl = QLabel("🪄  库初始化向导")
        f = QFont(); f.setPointSize(14); f.setBold(True)
        ttl.setFont(f)
        top.addWidget(ttl)
        top.addStretch(1)
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
            "<p>本向导会带你完成以下步骤：</p>"
            "<ol>"
            "<li>用一段自然语言描述你打算管理的内容类型；</li>"
            "<li>调用 LLM 生成一份字段结构方案；</li>"
            "<li>预览、编辑、删除或重新生成；</li>"
            "<li>满意后一次性写入库的字段表。</li>"
            "</ol>"
            "<p>提示：</p>"
            "<ul>"
            "<li>整个过程会调用 LLM，使用「设置 → API」中默认 provider；</li>"
            "<li>可以随时取消；<b>不点「应用」就不会修改库</b>；</li>"
            "<li>已有的标题/作者等系统字段不会被重复创建。</li>"
            "</ul>"
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)
        v.addWidget(intro)

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
        b_next = QPushButton("下一步 →")
        b_next.setDefault(True)
        b_next.clicked.connect(lambda: self.stack.setCurrentIndex(PAGE_SCENARIO))
        btns.addWidget(b_next)
        v.addLayout(btns)
        return w

    def _build_scenario_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)

        v.addWidget(QLabel(
            "<b>请描述你的使用场景</b>（越具体越好；可包含特殊偏好、字段需求等）"
        ))

        self.ed_scenario = QPlainTextEdit()
        self.ed_scenario.setPlaceholderText(
            "例如：\n"
            "我想用这个库管理我看过的科幻小说，重点关注作者、出版年代、子流派"
            "（硬科幻/软科幻/赛博朋克）、阅读状态（未读/在读/已读）、个人评分。\n"
            "希望每本书有一段不剧透的剧情概括，以及自己的读后感。"
        )
        v.addWidget(self.ed_scenario, 1)

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
            "<b>LLM 给出的字段建议</b>　每行可勾选/编辑/删除；底部可重新生成。"
        )
        self.lbl_preview_hint.setTextFormat(Qt.RichText)
        v.addWidget(self.lbl_preview_hint)

        # 字段表
        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(
            ["", "状态", "字段名", "类型", "LLM 提示"]
        )
        self.tbl.verticalHeader().setVisible(False)
        h = self.tbl.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Interactive)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl.setColumnWidth(2, 160)
        v.addWidget(self.tbl, 1)

        # 警告区
        self.lbl_warnings = QLabel("")
        self.lbl_warnings.setWordWrap(True)
        self.lbl_warnings.setStyleSheet("color: #f57c00;")
        self.lbl_warnings.setVisible(False)
        v.addWidget(self.lbl_warnings)

        # 标签建议（折叠到一个 LineEdit-only 行）
        tag_row = QHBoxLayout()
        tag_row.addWidget(QLabel("建议标签："))
        self.lbl_tags = QLabel("（无）")
        self.lbl_tags.setWordWrap(True)
        self.lbl_tags.setProperty("muted", True)
        tag_row.addWidget(self.lbl_tags, 1)
        v.addLayout(tag_row)

        # 原始响应可折叠
        self.btn_toggle_raw = QPushButton("▶ 查看 LLM 原始响应")
        self.btn_toggle_raw.setCheckable(True)
        self.btn_toggle_raw.toggled.connect(self._on_toggle_raw)
        v.addWidget(self.btn_toggle_raw)
        self.ed_raw = QTextEdit()
        self.ed_raw.setReadOnly(True)
        self.ed_raw.setMaximumHeight(160)
        self.ed_raw.setVisible(False)
        v.addWidget(self.ed_raw)

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

        btns.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_cancel)

        self.btn_apply = QPushButton("✅ 应用到库")
        self.btn_apply.setDefault(True)
        self.btn_apply.clicked.connect(self._on_apply)
        btns.addWidget(self.btn_apply)
        v.addLayout(btns)
        return w

    # ---- 状态管理 ----------------------------------------------------------
    def _refresh_round_label(self) -> None:
        self.lbl_round.setText(f"轮数 {self._current_round} / {self._max_rounds}")
        # 在预览页根据上限禁用 refine 按钮
        if hasattr(self, "btn_refine"):
            at_limit = self._current_round >= self._max_rounds
            self.btn_refine.setEnabled(not at_limit)
            if at_limit:
                self.btn_refine.setToolTip(
                    f"已达 {self._current_round}/{self._max_rounds} 轮，"
                    "请『重新开始』或采用当前结果"
                )

    def _on_toggle_raw(self, on: bool) -> None:
        self.ed_raw.setVisible(on)
        self.btn_toggle_raw.setText(
            ("▼" if on else "▶") + " 查看 LLM 原始响应"
        )

    # ---- 调用 LLM ----------------------------------------------------------
    def _on_first_call(self) -> None:
        text = self.ed_scenario.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "请填写场景描述", "请先描述你的使用场景。")
            return
        self._scenario_text = text
        self._history = []
        self._dispatch_call(extra="")

    def _on_restart(self) -> None:
        self._history = []
        self._suggestions = []
        self._tags_suggestion = []
        self._last_raw_response = ""
        self._current_round = 0
        self._refresh_round_label()
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

        messages = build_messages(self._scenario_text, self._history, extra)

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
    ) -> None:
        self._current_round += 1
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
        self._suggestions = annotate_conflicts(payload["fields"], existing)
        self._tags_suggestion = payload.get("default_tags_suggestion", []) or []

        self._render_preview(warnings)
        self.stack.setCurrentIndex(PAGE_PREVIEW)

    # ---- 预览页渲染 --------------------------------------------------------
    _STATUS_DISPLAY = {
        "new": ("✅ 新字段", "将创建该字段"),
        "system_protected": ("🔒 系统字段", "受保护，跳过"),
        "same_type": ("🔁 同名同类型", "已存在，仅更新 LLM 提示"),
        "type_conflict": ("⚠ 类型冲突", "已存在但类型不同；请改名后再创建"),
    }

    def _render_preview(self, warnings: list[str]) -> None:
        self.tbl.setRowCount(0)
        for row, ann in enumerate(self._suggestions):
            self.tbl.insertRow(row)

            # 0：勾选
            cb = QCheckBox()
            cb.setChecked(ann.selected)
            if ann.status == "system_protected":
                cb.setEnabled(False)
            cb.toggled.connect(
                lambda on, idx=row: self._set_selected(idx, on)
            )
            self._wrap_cell(self.tbl, row, 0, cb)

            # 1：状态
            label, default_tip = self._STATUS_DISPLAY.get(
                ann.status, (ann.status, ""),
            )
            it_status = QTableWidgetItem(label)
            it_status.setFlags(it_status.flags() & ~Qt.ItemIsEditable)
            it_status.setToolTip(ann.reason or default_tip)
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
                ed.textChanged.connect(
                    lambda t, idx=row: self._on_rename_changed(idx, t)
                )
                hl.addWidget(ed, 1)
                self.tbl.setCellWidget(row, 2, w)
            else:
                it_name = QTableWidgetItem(ann.name)
                if ann.status in ("system_protected", "same_type"):
                    it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)
                self.tbl.setItem(row, 2, it_name)

            # 3：类型
            cmb = QComboBox()
            for t in FIELD_TYPES:
                cmb.addItem(FIELD_TYPE_LABELS.get(t, t), t)
            idx_t = cmb.findData(ann.type)
            cmb.setCurrentIndex(max(0, idx_t))
            if ann.status in ("system_protected", "same_type"):
                cmb.setEnabled(False)
            cmb.currentIndexChanged.connect(
                lambda _i, idx=row, c=cmb: self._on_type_changed(
                    idx, c.currentData(),
                )
            )
            self.tbl.setCellWidget(row, 3, cmb)

            # 4：prompt_hint
            it_hint = QTableWidgetItem(ann.prompt_hint)
            self.tbl.setItem(row, 4, it_hint)

        if warnings:
            self.lbl_warnings.setText(
                "解析告警：" + "；".join(warnings)
            )
            self.lbl_warnings.setVisible(True)
        else:
            self.lbl_warnings.setVisible(False)

        if self._tags_suggestion:
            self.lbl_tags.setText("、".join(self._tags_suggestion))
        else:
            self.lbl_tags.setText("（无）")

        self.ed_raw.setPlainText(self._last_raw_response)
        self._refresh_round_label()

    def _wrap_cell(self, tbl: QTableWidget, row: int, col: int, widget) -> None:
        """把 widget 居中包进单元格。"""
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch(1)
        hl.addWidget(widget)
        hl.addStretch(1)
        tbl.setCellWidget(row, col, w)

    def _set_selected(self, idx: int, on: bool) -> None:
        if 0 <= idx < len(self._suggestions):
            self._suggestions[idx].selected = on

    def _on_type_changed(self, idx: int, new_type: str) -> None:
        if 0 <= idx < len(self._suggestions):
            self._suggestions[idx].type = new_type

    def _on_rename_changed(self, idx: int, new_name: str) -> None:
        if 0 <= idx < len(self._suggestions):
            self._suggestions[idx].rename_to = new_name

    # ---- 收集 / 应用 -------------------------------------------------------
    def _collect_user_edited_payload(self) -> dict:
        """从表格里读出当前编辑后的 fields，回灌给 history 用。"""
        out = []
        for row, ann in enumerate(self._suggestions):
            # name
            if ann.status == "type_conflict":
                # rename_to 已经实时同步到 ann.rename_to
                name = ann.effective_name
            else:
                it = self.tbl.item(row, 2)
                name = it.text().strip() if it else ann.name
                ann.name = name
            # hint
            it_h = self.tbl.item(row, 4)
            hint = it_h.text() if it_h else ann.prompt_hint
            ann.prompt_hint = hint
            out.append({"name": name, "type": ann.type, "prompt_hint": hint})
        return {"fields": out, "default_tags_suggestion": self._tags_suggestion}

    def _on_apply(self) -> None:
        if self.repo is None:
            return
        # 同步用户的最新编辑
        self._collect_user_edited_payload()

        to_create: list[tuple[str, str, str]] = []
        to_update_hint: list[tuple[int, str]] = []
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

        # 同 batch 内可能改名后撞上别的待创建字段，做一次预检
        names = [n for n, _, _ in to_create]
        if len(set(names)) != len(names):
            dups = sorted({n for n in names if names.count(n) > 1})
            QMessageBox.warning(
                self, "字段名重复",
                f"以下字段在本次创建中重复：{', '.join(dups)}。请改名后再应用。",
            )
            return

        # 事务化批量创建
        try:
            new_ids = self.repo.add_fields_batch(to_create)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(
                self, "应用失败",
                f"创建字段时出错，已回滚（库内字段表无变化）：\n{e}",
            )
            return  # 不关闭向导，让用户继续修改

        # update_hint 单独走（非事务，但每条幂等）
        for fid, hint in to_update_hint:
            self.repo.set_field_prompt_hint(fid, hint)

        n_new = len(new_ids)
        n_upd = len(to_update_hint)
        QMessageBox.information(
            self, "已应用",
            f"已新建字段 {n_new} 个，更新已存在字段的 LLM 提示 {n_upd} 个。",
        )
        # 标记一次性（即使再开也不影响功能，仅供未来"是否运行过向导"判断）
        try:
            self.repo.set_setting("library_init_wizard_done", "1")
        except Exception:
            pass
        self._applied = True
        self.accept()


# =============================================================================
# 小工具
# =============================================================================
def _ask_text(parent, title: str, prompt: str) -> tuple[str, bool]:
    """多行输入弹窗。返回 (text, accepted)。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(480, 220)
    v = QVBoxLayout(dlg)
    v.addWidget(QLabel(prompt))
    ed = QPlainTextEdit()
    v.addWidget(ed, 1)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    v.addWidget(bb)
    if dlg.exec() == QDialog.Accepted:
        return ed.toPlainText(), True
    return "", False
