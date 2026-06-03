"""项目编辑/新建对话框。

特性：
- 字段按 fields schema 渲染（标题强制第一）
- 显示当前 pending 的 LLM 建议；可单条应用/驳回 或 全部接受/全部驳回
- 顶部"✨ LLM 建议"按钮触发 LLM 建议生成（执行后关闭本对话框）
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QCalendarWidget,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ..models import Field, FieldSuggestion, Project
from ..repository import Repository
from .widgets import StarRating


# =============================================================================
# Date editor —— 项目编辑里 date 字段的"可空"输入控件（B1，2026-06-02）
# =============================================================================
# 设计要点：
#   - 真正存值的是一个 ``QLineEdit``（支持空字符串，置空就是"未填"）；
#   - 旁边一个 📅 按钮弹出 ``QCalendarWidget``，选完写回 ``yyyy-MM-dd``；
#   - 一个 ✕ 按钮一键清空。
# 选这套实现而不是 ``QDateEdit + setSpecialValueText`` 的原因：QDateEdit 一旦
# 用户拨过日期就回不到 "special value" 那一档，没法真正回到"空"。
class _DateEditor(QFrame):
    """``yyyy-MM-dd`` 字符串编辑器；支持空值；text() 永远返回当前可见文本。"""

    def __init__(self, value: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        self._edit = QLineEdit()
        self._edit.setPlaceholderText("yyyy-MM-dd（留空表示未填）")
        # 保留输入合法性的轻量校验：用户手动输入时只做格式提示，不强制阻止
        # （保存阶段在 _read_editor 兜底处理）
        self._edit.setText(value or "")
        h.addWidget(self._edit, 1)

        self._b_pick = QToolButton()
        self._b_pick.setText("📅")
        self._b_pick.setToolTip("从日历选取日期")
        self._b_pick.clicked.connect(self._open_calendar)
        h.addWidget(self._b_pick)

        self._b_clear = QToolButton()
        self._b_clear.setText("✕")
        self._b_clear.setToolTip("清空（置为未填）")
        self._b_clear.clicked.connect(lambda: self._edit.setText(""))
        h.addWidget(self._b_clear)

    def text(self) -> str:
        return (self._edit.text() or "").strip()

    def setText(self, v: str) -> None:
        self._edit.setText(v or "")

    def _open_calendar(self) -> None:
        # 用 QMenu 包一层 QCalendarWidget，弹一个临时浮层
        menu = QMenu(self)
        cal = QCalendarWidget()
        cal.setGridVisible(True)
        cur = QDate.fromString(self.text(), "yyyy-MM-dd")
        if cur.isValid():
            cal.setSelectedDate(cur)
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(cal)
        menu.addAction(wa)

        def _picked(d: QDate) -> None:
            self._edit.setText(d.toString("yyyy-MM-dd"))
            menu.close()

        cal.clicked.connect(_picked)
        # 弹在 📅 按钮下方
        menu.exec(self._b_pick.mapToGlobal(self._b_pick.rect().bottomLeft()))


class ProjectDialog(QDialog):
    # 用户点了"✨ LLM 建议"，调用方应弹出 LLMSuggestDialog 并入队
    request_llm_suggest = Signal()

    def __init__(self, project: Project | None = None,
                 repo: Repository | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("项目元数据")
        self.resize(620, 720)

        self._project = project or Project()
        self._repo = repo
        # 新建模式 = 还没存进 db（id 为 None）：此时项目内没有任何文件、也没历史
        # 数据可参考，弹 LLM 建议得到的就是空内容，意义不大。按 TODO M1 决策：
        # 整块「✨ LLM 建议 / 全部接受 / 全部驳回」操作条直接隐藏，并在表单底部加一
        # 行引导提示，告诉用户保存后可以再来这里点 ✨。
        self._is_new_project = self._project.id is None

        # 字段编辑器：field_id -> (Field, widget, suggestion_row_widget_or_None)
        self._editors: dict[int, tuple[Field, QWidget, Optional[QWidget]]] = {}
        # 建议：field_id -> FieldSuggestion
        self._suggestions: dict[int, FieldSuggestion] = {}
        if self._repo is not None and self._project.id is not None:
            for s in self._repo.list_pending_suggestions(self._project.id):
                self._suggestions[s.field_id] = s

        body = QWidget()
        body.setObjectName("AppScrollHost")  # 跟随 dialog 背景，避免滚动区出现白条
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(10)

        # 顶部操作条：✨ LLM 建议 + 全部接受 / 全部驳回（仅当有 pending 时）
        # M1（2026-06-02）：新建模式下整块隐藏，因为新项目还没文件、没历史，
        # 此时调 LLM 得不到有意义的建议；改在表单末尾用一行 hint 引导用户
        # 保存项目后再来。
        if not self._is_new_project:
            top_row = QHBoxLayout()
            self.btn_llm = QPushButton("✨ LLM 建议")
            self.btn_llm.setProperty("primary", True)
            self.btn_llm.clicked.connect(self._on_request_llm)
            top_row.addWidget(self.btn_llm)
            top_row.addStretch(1)
            self.btn_accept_all = QPushButton("✓ 全部接受")
            self.btn_accept_all.setProperty("flat", True)
            self.btn_accept_all.clicked.connect(self._accept_all)
            self.btn_reject_all = QPushButton("✗ 全部驳回")
            self.btn_reject_all.setProperty("flat", True)
            self.btn_reject_all.setProperty("danger", True)
            self.btn_reject_all.clicked.connect(self._reject_all)
            top_row.addWidget(self.btn_accept_all)
            top_row.addWidget(self.btn_reject_all)
            body_l.addLayout(top_row)
            self._update_bulk_buttons()
        else:
            # 新建模式：把按钮引用置 None，避免后续代码引用时 AttributeError
            self.btn_llm = None  # type: ignore[assignment]
            self.btn_accept_all = None  # type: ignore[assignment]
            self.btn_reject_all = None  # type: ignore[assignment]

        # 字段表单
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        fields: list[Field] = []
        if self._repo is not None:
            try:
                fields = self._repo.list_fields()
            except Exception:
                fields = []

        title_field = next((f for f in fields if f.is_title), None)
        rest = [f for f in fields if not f.is_title]
        ordered = ([title_field] if title_field else []) + rest
        if not ordered:
            ordered = [Field(name="标题", type="text", key="title")]

        for f in ordered:
            value = self._repo.get_field_value(self._project, f) if self._repo else ""
            editor = self._make_editor(f, value)
            sug_widget = None
            sug = self._suggestions.get(f.id) if f.id is not None else None
            if sug is not None:
                sug_widget = self._make_suggestion_row(f, sug)

            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(2)
            wl.addWidget(editor)
            if sug_widget is not None:
                wl.addWidget(sug_widget)

            label = f.name + (" *" if f.is_title else "")
            form.addRow(label, wrap)
            if f.id is not None:
                self._editors[f.id] = (f, editor, sug_widget)

        body_l.addLayout(form)

        # 新建模式末尾的引导文案（M1：替代被去掉的 ✨ LLM 建议按钮）
        if self._is_new_project:
            llm_hint = QLabel(
                "💡 项目创建完成后，可在项目列表右键「✨ LLM 元数据建议…」"
                "或在编辑对话框点 ✨ 让 LLM 帮你补全字段。"
            )
            llm_hint.setProperty("hint", True)
            llm_hint.setWordWrap(True)
            body_l.addWidget(llm_hint)

        body_l.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("AppScrollArea")  # 让 theme.py 给它上深/浅色背景
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(body)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(scroll, 1)
        root.addWidget(bb)

    # ---------------------------------------------------------------- editors
    def _make_editor(self, f: Field, value: str) -> QWidget:
        t = f.type or "text"
        if t == "textarea":
            w = QPlainTextEdit()
            w.setPlainText(value or "")
            w.setMinimumHeight(80)
            w.setMaximumHeight(180)
            w.setPlaceholderText("（支持 Markdown）" if f.key == "description" else "")
            return w
        if t == "tags":
            w = QLineEdit(value or "")
            w.setPlaceholderText("逗号分隔，例如：科幻, 翻译")
            return w
        if t == "date":
            # B1（2026-06-02）：date 字段默认空、可清空。
            # 旧实现用 ``QDateEdit`` 强制有值，且新建时默认今天，无法表达"未填"
            # 语义；改用自定义 ``_DateEditor``（文本框 + 📅 选取 + ✕ 清空）。
            return _DateEditor(value or "")
        if t == "rating":
            try:
                v = int(value) if value else 0
            except ValueError:
                v = 0
            return StarRating(v)
        if t == "number":
            w = QSpinBox()
            w.setRange(-(10**9), 10**9)
            try:
                w.setValue(int(value) if value else 0)
            except ValueError:
                w.setValue(0)
            return w
        if t == "url":
            w = QLineEdit(value or "")
            w.setPlaceholderText("https://…")
            return w
        return QLineEdit(value or "")

    def _read_editor(self, f: Field, w: QWidget) -> str:
        t = f.type or "text"
        if t == "textarea":
            return w.toPlainText()
        if t == "date":
            # _DateEditor.text() 返回当前文本（可能为空）；轻量校验日期格式，
            # 非法字符串保存为空（避免库里出现 "abc" 这样的脏值）。
            txt = w.text() if hasattr(w, "text") else ""
            txt = (txt or "").strip()
            if not txt:
                return ""
            d = QDate.fromString(txt, "yyyy-MM-dd")
            return d.toString("yyyy-MM-dd") if d.isValid() else ""
        if t == "rating":
            return str(w.value())
        if t == "number":
            return str(w.value())
        return w.text().strip()

    def _set_editor_value(self, f: Field, w: QWidget, value: str) -> None:
        t = f.type or "text"
        if t == "textarea":
            w.setPlainText(value or "")
        elif t == "date":
            # _DateEditor.setText 接受任意字符串（含空）；非法日期作为字符串
            # 直接保留，由用户决定是否更正
            if hasattr(w, "setText"):
                w.setText(value or "")
        elif t == "rating":
            try:
                w.set_value(int(value) if value else 0)
            except (ValueError, AttributeError):
                pass
        elif t == "number":
            try:
                w.setValue(int(value) if value else 0)
            except ValueError:
                pass
        else:
            w.setText(value or "")

    # ------------------------------------------------------------ suggestion row
    def _make_suggestion_row(self, f: Field, sug: FieldSuggestion) -> QWidget:
        wrap = QFrame()
        wrap.setStyleSheet(
            "QFrame { background: rgba(77,171,247,0.10); "
            "border: 1px solid rgba(77,171,247,0.35); border-radius: 6px; }"
        )
        h = QHBoxLayout(wrap)
        h.setContentsMargins(8, 4, 6, 4)
        h.setSpacing(6)
        lbl = QLabel(f"💡 建议：{sug.suggested_value}")
        lbl.setStyleSheet("color:#74c0fc;")
        lbl.setWordWrap(True)
        h.addWidget(lbl, 1)
        b_apply = QPushButton("✓ 应用")
        b_apply.setProperty("flat", True)
        b_apply.clicked.connect(lambda: self._apply_one(f.id))
        b_reject = QPushButton("✗ 驳回")
        b_reject.setProperty("flat", True)
        b_reject.setProperty("danger", True)
        b_reject.clicked.connect(lambda: self._reject_one(f.id))
        h.addWidget(b_apply); h.addWidget(b_reject)
        return wrap

    def _apply_one(self, fid: Optional[int]) -> None:
        if fid is None or fid not in self._suggestions or self._repo is None:
            return
        sug = self._suggestions[fid]
        f, w, sug_w = self._editors[fid]
        self._set_editor_value(f, w, sug.suggested_value)
        if sug.id is not None:
            self._repo.resolve_suggestion(sug.id, "applied")
        self._dismiss_suggestion_ui(fid)

    def _reject_one(self, fid: Optional[int]) -> None:
        if fid is None or fid not in self._suggestions or self._repo is None:
            return
        sug = self._suggestions[fid]
        if sug.id is not None:
            self._repo.resolve_suggestion(sug.id, "rejected")
        self._dismiss_suggestion_ui(fid)

    def _dismiss_suggestion_ui(self, fid: int) -> None:
        if fid not in self._editors:
            return
        f, w, sug_w = self._editors[fid]
        if sug_w is not None:
            sug_w.setParent(None)
            sug_w.deleteLater()
        self._editors[fid] = (f, w, None)
        self._suggestions.pop(fid, None)
        self._update_bulk_buttons()

    def _accept_all(self) -> None:
        for fid in list(self._suggestions.keys()):
            self._apply_one(fid)

    def _reject_all(self) -> None:
        for fid in list(self._suggestions.keys()):
            self._reject_one(fid)

    def _update_bulk_buttons(self) -> None:
        # 新建模式整块顶部条不存在；按钮引用为 None 时直接跳过
        if self.btn_accept_all is None or self.btn_reject_all is None:
            return
        has = bool(self._suggestions)
        self.btn_accept_all.setVisible(has)
        self.btn_reject_all.setVisible(has)

    # ---------------------------------------------------------------- LLM 触发
    def _on_request_llm(self) -> None:
        # 让上层处理（弹 LLMSuggestDialog + 入队 + 关闭本对话框）
        self.request_llm_suggest.emit()

    # ---------------------------------------------------------------- accept
    def _accept(self) -> None:
        if self._repo is None:
            self.accept()
            return

        for fid, (f, w, _sw) in self._editors.items():
            v = self._read_editor(f, w)
            if f.is_title and not (v or "").strip():
                QMessageBox.warning(self, "提示", "标题不能为空")
                return
            self._repo.set_field_value_on_project(self._project, f, v)

        self.accept()

    def project(self) -> Project:
        return self._project
