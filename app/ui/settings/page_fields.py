"""设置页 · 字段（库级字段管理）（task #35 T3：从 settings_dialog.py 拆分，方法体未改动）。

Mixin：设置页 · 字段（库级字段管理）
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
    QPlainTextEdit,
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
from ...models import (
    FIELD_TYPE_LABELS,
    FIELD_TYPES,
    is_compatible_type_change,
)
from ...repository import Repository
from ...utils import app_data_dir, reveal_in_explorer
from ..dialogs import info, warn
from .field_dialogs import (
    _AddFieldDialog,
    _DeleteFieldChoiceDialog,
    _FieldTypeChangeConfirmDialog,
)


class FieldsPageMixin:
    """设置页 · 字段（库级字段管理）"""

    def _build_fields_page(self) -> QWidget:
        self._FIELD_TYPES = FIELD_TYPES
        self._FIELD_TYPE_LABELS = FIELD_TYPE_LABELS

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)

        title = QLabel("字段")
        title.setProperty("h1", True)
        lay.addWidget(title)

        tip = QLabel(
            "管理所有字段及其类型与顺序。表中顺序即为『项目编辑』对话框和列表视图中显示的顺序。"
            "『标题』『描述』『标签』为必有字段，不可删除或修改类型。"
        )
        tip.setProperty("hint", True)
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # 字段表（5 列：字段名 / 类型 / 显示 / 元数据建议 / LLM 提示）
        self.tbl_fields = QTableWidget(0, 5)
        self.tbl_fields.setHorizontalHeaderLabels(
            ["字段名", "类型", "显示", "元数据建议", "LLM 提示"]
        )
        self.tbl_fields.verticalHeader().setVisible(False)
        self.tbl_fields.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_fields.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_fields.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_fields.setShowGrid(False)
        self.tbl_fields.setAlternatingRowColors(True)
        h = self.tbl_fields.horizontalHeader()
        # 字段名占主，类型给固定较宽列以容纳类型名（如"评分（1~5 星）"）
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.Fixed)
        h.setSectionResizeMode(3, QHeaderView.Fixed)
        h.setSectionResizeMode(4, QHeaderView.Fixed)
        self.tbl_fields.setColumnWidth(1, 180)
        self.tbl_fields.setColumnWidth(2, 60)
        self.tbl_fields.setColumnWidth(3, 90)
        self.tbl_fields.setColumnWidth(4, 110)
        # 行高要足够容纳带 padding 的 QComboBox，否则文字会被上下裁掉只剩一条
        self.tbl_fields.verticalHeader().setDefaultSectionSize(40)
        lay.addWidget(self.tbl_fields, 1)

        # 操作按钮
        ops = QHBoxLayout()
        b_add = QPushButton("＋ 添加")
        b_add.clicked.connect(self._field_add)
        b_rename = QPushButton("✎ 重命名")
        b_rename.clicked.connect(self._field_rename)
        b_del = QPushButton("🗑 删除")
        b_del.setProperty("danger", True)
        b_del.clicked.connect(self._field_delete)
        b_up = QPushButton("↑ 上移")
        b_up.clicked.connect(lambda: self._field_move(-1))
        b_down = QPushButton("↓ 下移")
        b_down.clicked.connect(lambda: self._field_move(1))
        for b in (b_add, b_rename, b_del):
            ops.addWidget(b)
        ops.addStretch(1)
        ops.addWidget(b_up)
        ops.addWidget(b_down)
        lay.addLayout(ops)

        self._reload_fields_table()
        return w


    def _reload_fields_table(self) -> None:
        fields = self.repo.list_fields()
        self.tbl_fields.blockSignals(True)
        self.tbl_fields.setRowCount(len(fields))
        for r, f in enumerate(fields):
            # 字段名 — 受保护字段标"必有"（不可删除 / 类型固定）。
            # 不区分 is_title vs is_required：用户已经能从字段名看出它是
            # "标题"，括号里再写一遍"标题"是冗余的；只需要告知"必有"。
            tag_suffix = "  (必有)" if f.is_required else ""
            display_name = f.name + tag_suffix
            it_name = QTableWidgetItem(display_name)
            it_name.setData(Qt.UserRole, f.id)
            self.tbl_fields.setItem(r, 0, it_name)

            # 类型 ComboBox
            cmb = QComboBox()
            # 防止 cell widget 在某些 DPI / 缩放下被压成"竖排字"宽度
            cmb.setMinimumWidth(140)
            # 行高足够，但 cell widget 默认会被拉满 cell；显式给个最小高度，
            # 避免文字被裁切只剩一条横线
            cmb.setMinimumHeight(28)
            cmb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            for t in self._FIELD_TYPES:
                cmb.addItem(self._FIELD_TYPE_LABELS.get(t, t), t)
            # 受保护字段可能有 FIELD_TYPES 之外的固定类型（如 tags），单独追加并选中
            ftype = f.type or "text"
            if cmb.findData(ftype) < 0:
                cmb.addItem(self._FIELD_TYPE_LABELS.get(ftype, ftype), ftype)
            idx = cmb.findData(ftype)
            cmb.setCurrentIndex(max(0, idx))
            if f.is_required:
                cmb.setEnabled(False)
                cmb.setToolTip(f"『{f.name}』字段类型固定，不可修改")
            cmb.currentIndexChanged.connect(
                lambda _i, fid=f.id, box=cmb: self._field_change_type(fid, box.currentData())
            )
            self.tbl_fields.setCellWidget(r, 1, cmb)

            # 显示复选框
            cb = QCheckBox()
            cb.setChecked(f.visible)
            if f.is_title:
                cb.setEnabled(False)
                cb.setToolTip("标题字段必显")
            cb.stateChanged.connect(
                lambda _s, fid=f.id, box=cb: self._field_toggle_visible(fid, box.isChecked())
            )
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addStretch(1); hl.addWidget(cb); hl.addStretch(1)
            self.tbl_fields.setCellWidget(r, 2, holder)

            # LLM 建议复选框（标题字段也允许用户自由开关）
            cb_sug = QCheckBox()
            cb_sug.setChecked(f.suggest_enabled)
            cb_sug.stateChanged.connect(
                lambda _s, fid=f.id, box=cb_sug: self._field_toggle_suggest(fid, box.isChecked())
            )
            holder2 = QWidget()
            hl2 = QHBoxLayout(holder2)
            hl2.setContentsMargins(0, 0, 0, 0)
            hl2.addStretch(1); hl2.addWidget(cb_sug); hl2.addStretch(1)
            self.tbl_fields.setCellWidget(r, 3, holder2)

            # LLM 提示按钮（task #11 T1）：点击弹文本编辑器编辑该字段的 prompt_hint
            btn_hint = QPushButton(
                "📝 已设置" if (f.prompt_hint or "").strip() else "✎ 编辑…"
            )
            btn_hint.setFlat(True)
            if (f.prompt_hint or "").strip():
                btn_hint.setToolTip(
                    "当前提示：\n" + (f.prompt_hint[:300] + ("…" if len(f.prompt_hint) > 300 else ""))
                )
            else:
                btn_hint.setToolTip("点击编辑该字段的 LLM 提示（留空使用默认）")
            btn_hint.clicked.connect(
                lambda _checked=False, fid=f.id, name=f.name, hint=f.prompt_hint or "":
                    self._field_edit_prompt_hint(fid, name, hint)
            )
            self.tbl_fields.setCellWidget(r, 4, btn_hint)
        self.tbl_fields.blockSignals(False)


    def _current_field_id(self) -> int | None:
        r = self.tbl_fields.currentRow()
        if r < 0:
            return None
        it = self.tbl_fields.item(r, 0)
        return it.data(Qt.UserRole) if it else None


    def _field_add(self) -> None:
        dlg = _AddFieldDialog(parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.repo.add_field(dlg.name, dlg.type)
        except Exception as e:
            warn(self, "失败", str(e))
            return
        self._reload_fields_table()
        self.fields_changed.emit()


    def _field_rename(self) -> None:
        fid = self._current_field_id()
        if fid is None:
            return
        f = self.repo.get_field(fid)
        if not f:
            return
        new_name, ok = QInputDialog.getText(self, "重命名", "新字段名：", text=f.name)
        if not ok or not new_name.strip() or new_name.strip() == f.name:
            return
        try:
            self.repo.rename_field(fid, new_name.strip())
        except Exception as e:
            warn(self, "失败", str(e))
            return
        self._reload_fields_table()
        self.fields_changed.emit()


    def _field_toggle_visible(self, fid: int, visible: bool) -> None:
        self.repo.set_field_visible(fid, visible)
        self.fields_changed.emit()


    def _field_toggle_suggest(self, fid: int, enabled: bool) -> None:
        self.repo.set_field_suggest_enabled(fid, enabled)


    def _field_edit_prompt_hint(self, fid: int, name: str, current_hint: str) -> None:
        """编辑字段的 LLM 提示（task #11 T1）。"""
        dlg = QDialog(self)
        dlg.setWindowTitle(f"LLM 提示 — {name}")
        dlg.resize(560, 360)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)

        head = QLabel(
            f"为字段「<b>{name}</b>」自定义 LLM 建议时的格式说明。<br>"
            "留空 = 使用默认（仅按类型说明）；填写 = 在 user prompt 中追加为该字段的「格式要求」。"
        )
        head.setWordWrap(True)
        v.addWidget(head)

        ed = QPlainTextEdit()
        ed.setPlaceholderText(
            "示例（标题）：不超过 30 字；不要带书名号；体现作品类型与核心主题。\n"
            "示例（描述）：200~400 字；先一句概括，再分段说明背景/亮点/适用人群；不使用 emoji。"
        )
        ed.setPlainText(current_hint or "")
        v.addWidget(ed, 1)

        char_lbl = QLabel()
        char_lbl.setProperty("hint", True)

        def _update_count() -> None:
            n = len(ed.toPlainText())
            char_lbl.setText(
                f"  当前 {n} 字（建议 ≤ 500；超过会被自动截断）"
            )
        ed.textChanged.connect(_update_count)
        _update_count()
        v.addWidget(char_lbl)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("保存")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        if dlg.exec() != QDialog.Accepted:
            return
        new_hint = ed.toPlainText().strip()
        self.repo.set_field_prompt_hint(fid, new_hint)
        self._reload_fields_table()


    def _field_change_type(self, fid: int, ftype: str) -> None:
        """改字段类型（task #19 Phase A）：兼容切静默执行；不兼容走确认弹窗。"""
        f = self.repo.get_field(fid)
        if f is None or f.type == ftype:
            return
        if is_compatible_type_change(f.type, ftype):
            self.repo.set_field_type(fid, ftype)
            self.fields_changed.emit()
            return

        n_values, m_pending = self._count_field_impact(fid)
        # 三条都没东西可保护 → 静默切（即使技术上不兼容）
        if n_values == 0 and m_pending == 0 and not (f.prompt_hint or "").strip():
            self.repo.set_field_type(fid, ftype)
            self.fields_changed.emit()
            return

        confirmed, clear_hint = _FieldTypeChangeConfirmDialog.ask(
            self, f, ftype, n_values, m_pending,
        )
        if not confirmed:
            # 用户取消：ComboBox 的 currentIndex 已经被切到新类型；整表重画
            # 把视觉拽回去
            self._reload_fields_table()
            return

        try:
            self.repo.set_field_type(
                fid, ftype,
                supersede_pending_suggestions=(m_pending > 0),
                clear_prompt_hint=clear_hint,
            )
        except Exception as e:  # noqa: BLE001
            warn(self, "失败", str(e))
            self._reload_fields_table()
            return
        self.fields_changed.emit()


    def _count_field_impact(self, fid: int) -> tuple[int, int]:
        """统计改类型会影响多少 (非空字段值数, pending 建议数)。

        - 非空值数走 ``repo.count_field_filled``：它会按字段类型分流——系统字段
          读 ``projects`` 表对应列、用户字段读 ``project_field_values``。绝对
          不要只查 ``project_field_values``，那样对系统字段（如 author / date /
          source_url / rating）永远返回 0，会导致"明明有数据却静默切类型不弹窗"。
        - pending 建议数走 ``project_field_suggestions``（这张表系统字段 / 用户
          字段都用同一份 schema）。
        """
        f = self.repo.get_field(fid)
        if f is None:
            return 0, 0
        n_values = self.repo.count_field_filled(f)
        m_pending = self.repo.count_pending_suggestions_for_field(fid)
        return int(n_values), int(m_pending)


    def _field_move(self, delta: int) -> None:
        r = self.tbl_fields.currentRow()
        if r < 0:
            return
        target = r + delta
        if target < 0 or target >= self.tbl_fields.rowCount():
            return
        ids: list[int] = []
        for i in range(self.tbl_fields.rowCount()):
            it = self.tbl_fields.item(i, 0)
            if it:
                ids.append(it.data(Qt.UserRole))
        ids[r], ids[target] = ids[target], ids[r]
        self.repo.reorder_fields(ids)
        self._reload_fields_table()
        self.tbl_fields.setCurrentCell(target, 0)
        self.fields_changed.emit()


    def _field_delete(self) -> None:
        fid = self._current_field_id()
        if fid is None:
            return
        f = self.repo.get_field(fid)
        if not f:
            return
        if f.is_required:
            info(self, "提示", f"『{f.name}』字段不可删除。")
            return

        # 统计影响项目数（系统字段读 projects 列，用户字段读 project_field_values）
        cnt = self.repo.count_field_filled(f)

        dlg = _DeleteFieldChoiceDialog(f.name, cnt, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.repo.delete_field(fid, append_to_description=dlg.append_to_desc)
        except Exception as e:
            warn(self, "失败", str(e))
            return
        self._reload_fields_table()
        self.fields_changed.emit()
