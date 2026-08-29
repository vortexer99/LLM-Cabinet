"""字段相关小对话框（task #35 T3：从 settings_dialog.py 拆分，方法体未改动）。

删除字段的数据处理选择、新建字段、字段类型变更确认。供设置页与建库向导共用。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ... import HOMEPAGE_URL, __version__
from ...db import SCHEMA_VERSION
from ...models import FIELD_TYPE_LABELS, FIELD_TYPES
from ...repository import Repository
from ...utils import app_data_dir, reveal_in_explorer
from ..dialogs import info, warn


class _DeleteFieldChoiceDialog(QDialog):
    """删除字段时让用户选择如何处理已有项目的值。"""

    def __init__(self, field_name: str, project_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("删除字段")
        self.setMinimumWidth(440)
        self.append_to_desc = False

        v = QVBoxLayout(self)
        v.setSpacing(10)

        lbl = QLabel(
            f"即将删除字段 「<b>{field_name}</b>」。<br>"
            f"当前有 <b>{project_count}</b> 个项目填写了该字段的值。"
            f"<br><br>请选择如何处理已有数据："
        )
        lbl.setTextFormat(Qt.RichText)
        lbl.setWordWrap(True)
        v.addWidget(lbl)

        self.rb_drop = QRadioButton("直接删除该字段及其所有相关数据")
        self.rb_append = QRadioButton(
            "保留数据：把每个项目的该字段值追加到 描述（description）末尾，再删除字段"
        )
        self.rb_drop.setChecked(True)

        grp = QButtonGroup(self)
        grp.addButton(self.rb_drop)
        grp.addButton(self.rb_append)

        v.addWidget(self.rb_drop)
        v.addWidget(self.rb_append)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _on_ok(self) -> None:
        self.append_to_desc = self.rb_append.isChecked()
        self.accept()


class _AddFieldDialog(QDialog):
    """添加新字段：字段名 + 类型。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建字段")
        self.setMinimumWidth(360)

        self.name = ""
        self.type = "text"

        form = QFormLayout()
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("例如：译者、ISBN")
        form.addRow("字段名：", self.ed_name)

        self.cmb_type = QComboBox()
        for t in FIELD_TYPES:
            self.cmb_type.addItem(FIELD_TYPE_LABELS.get(t, t), t)
        form.addRow("类型：", self.cmb_type)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _on_ok(self) -> None:
        name = self.ed_name.text().strip()
        if not name:
            warn(self, "提示", "字段名不能为空")
            return
        self.name = name
        self.type = self.cmb_type.currentData() or "text"
        self.accept()


class _FieldTypeChangeConfirmDialog(QDialog):
    """字段类型变更的确认对话框（task #19 Phase A）。

    类型切换不会动 ``project_field_values.value``（保留原字符串，切回旧类型即可
    恢复显示），但新控件读不动旧值，更新元数据时可能被空状态覆盖；同时该字段
    挂着的 LLM pending 建议也都按旧类型生成，应失效；prompt_hint 大概率跟新
    类型不匹配，提供"是否同时清空"的选项（默认不勾，避免误删用户写的 hint，
    文案上仍标注"（推荐）"提示用户多数场景下该清空）。
    """

    def __init__(
        self,
        field,
        new_type: str,
        n_values: int,
        m_pending: int,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("确认更改字段类型")
        self.setMinimumWidth(480)
        self.clear_hint = False

        v = QVBoxLayout(self)
        v.setSpacing(10)

        old_label = FIELD_TYPE_LABELS.get(field.type, field.type)
        new_label = FIELD_TYPE_LABELS.get(new_type, new_type)
        head = QLabel(
            f"将「<b>{field.name}</b>」从 <b>{old_label}</b> 改为 "
            f"<b>{new_label}</b>？"
        )
        head.setTextFormat(Qt.RichText)
        head.setWordWrap(True)
        v.addWidget(head)

        # 三条说明（按需显示）
        bullets: list[str] = []
        if n_values > 0:
            bullets.append(
                f"已有 <b>{n_values}</b> 条非空记录的字段值会保留在数据库里，"
                "但新类型的控件可能读不出来（切回旧类型即可恢复显示；"
                "<b>更新项目元数据时</b>若提交空值会被覆盖）"
            )
        if m_pending > 0:
            bullets.append(
                f"待批准的 LLM 建议 <b>{m_pending}</b> 条会失效"
            )
        has_hint = bool((field.prompt_hint or "").strip())
        if has_hint:
            hint_preview = (field.prompt_hint or "").strip().replace("\n", " ")
            if len(hint_preview) > 30:
                hint_preview = hint_preview[:30] + "…"
            bullets.append(
                f"该字段的 LLM 提示「<i>{hint_preview}</i>」可能跟新类型不匹配"
            )

        if bullets:
            body = QLabel(
                "<ul style='margin-left:-20px'>"
                + "".join(f"<li>{b}</li>" for b in bullets)
                + "</ul>"
            )
            body.setTextFormat(Qt.RichText)
            body.setWordWrap(True)
            v.addWidget(body)

        # 清空 hint 的 checkbox（仅当 hint 非空时显示）
        # 默认不勾选以避免误删用户精心写的 hint；文字保留"（推荐）"
        # 提示用户大多数情况下应该清空（旧 hint 通常和新类型不匹配）。
        if has_hint:
            self.cb_clear_hint = QCheckBox("同时清空该字段的 LLM 提示（推荐）")
            self.cb_clear_hint.setChecked(False)
            v.addWidget(self.cb_clear_hint)
        else:
            self.cb_clear_hint = None

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("确认更改")
        bb.button(QDialogButtonBox.Cancel).setText("取消")
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _on_ok(self) -> None:
        self.clear_hint = bool(
            self.cb_clear_hint is not None and self.cb_clear_hint.isChecked()
        )
        self.accept()

    @classmethod
    def ask(
        cls, parent, field, new_type: str, n_values: int, m_pending: int,
    ) -> tuple[bool, bool]:
        """返回 ``(confirmed, clear_hint)``。"""
        dlg = cls(field, new_type, n_values, m_pending, parent=parent)
        ok = dlg.exec() == QDialog.Accepted
        return ok, (dlg.clear_hint if ok else False)
