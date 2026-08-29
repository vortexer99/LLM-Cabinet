"""设置页 · 通用（task #35 T3：从 settings_dialog.py 拆分，方法体未改动）。

Mixin：设置页 · 通用
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
from ..theme import apply_theme


class GeneralPageMixin:
    """设置页 · 通用"""

    def _build_general_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        title = QLabel("通用")
        title.setProperty("h1", True)
        lay.addWidget(title)

        # LLM 助手入口（task #11 T3 决策 1：辅助入口；内部代码沿用 wizard 命名）
        wiz_row = QHBoxLayout()
        wiz_lbl = QLabel(
            "🪄 通过 LLM 助手让 AI 帮你规划字段结构、整理库等。"
        )
        wiz_lbl.setWordWrap(True)
        wiz_row.addWidget(wiz_lbl, 1)
        btn_wiz = QPushButton("打开 LLM 助手...")
        btn_wiz.clicked.connect(self._open_wizards)
        wiz_row.addWidget(btn_wiz)
        lay.addLayout(wiz_row)

        # task #41 T6：界面字号
        gb_font = QGroupBox("外观")
        form_font = QFormLayout(gb_font)
        form_font.setLabelAlignment(Qt.AlignLeft)
        self.cmb_font_size = QComboBox()
        for size in (11, 12, 13, 14, 16):
            self.cmb_font_size.addItem(f"{size} px", size)
        cur_fs = int(self.repo.get_setting("ui_font_size", "13") or "13")
        idx_fs = self.cmb_font_size.findData(cur_fs)
        self.cmb_font_size.setCurrentIndex(max(0, idx_fs if idx_fs >= 0 else 2))
        self.cmb_font_size.currentIndexChanged.connect(self._on_font_size_changed)
        form_font.addRow("界面字号：", self.cmb_font_size)
        lay.addWidget(gb_font)

        # 应用数据目录（软件层级属性，与库无关）
        gb_data = QGroupBox("数据位置")
        row = QHBoxLayout(gb_data)
        lbl = QLabel(f"应用数据目录：{app_data_dir()}")
        lbl.setWordWrap(True)
        row.addWidget(lbl, 1)
        b_open = QPushButton("📂")
        b_open.setFixedWidth(40)
        b_open.clicked.connect(lambda: reveal_in_explorer(app_data_dir()))
        row.addWidget(b_open)
        lay.addWidget(gb_data)

        lay.addStretch(1)
        return w


    def _on_wiz_rounds_changed(self, v: int) -> None:
        self._wiz_set_max_rounds(self.repo, v)


    def _on_font_size_changed(self, _i: int) -> None:
        """界面字号（task #41 T6）：保存并立即应用。"""
        size = int(self.cmb_font_size.currentData() or 13)
        self.repo.set_setting("ui_font_size", str(size))
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, font_size=size)


    def _open_wizards(self) -> None:
        """从设置 → 通用 打开 LLM 助手列表对话框。"""
        from ..wizard_list_dialog import WizardListDialog
        # library 不在 SettingsDialog 上下文里，助手可能不需要它，传 None 即可
        # （当前唯一一个助手 LibraryInitWizard 不使用 library，仅用 repo）。
        dlg = WizardListDialog(self.repo, library=None, parent=self)
        dlg.exec()
        if dlg.any_applied():
            self.fields_changed.emit()
