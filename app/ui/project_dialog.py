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
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..models import Field, FieldSuggestion, Project
from ..repository import Repository
from .widgets import StarRating


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

        # 字段编辑器：field_id -> (Field, widget, suggestion_row_widget_or_None)
        self._editors: dict[int, tuple[Field, QWidget, Optional[QWidget]]] = {}
        # 建议：field_id -> FieldSuggestion
        self._suggestions: dict[int, FieldSuggestion] = {}
        if self._repo is not None and self._project.id is not None:
            for s in self._repo.list_pending_suggestions(self._project.id):
                self._suggestions[s.field_id] = s

        body = QWidget()
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(10)

        # 顶部操作条：✨ LLM 建议 + 全部接受 / 全部驳回（仅当有 pending 时）
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
        body_l.addStretch(1)

        scroll = QScrollArea()
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
            w = QDateEdit()
            w.setCalendarPopup(True)
            w.setDisplayFormat("yyyy-MM-dd")
            d = QDate.fromString(value, "yyyy-MM-dd")
            if not d.isValid():
                d = QDate.currentDate()
            w.setDate(d)
            return w
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
            return w.date().toString("yyyy-MM-dd")
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
            d = QDate.fromString(value, "yyyy-MM-dd")
            if d.isValid():
                w.setDate(d)
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
