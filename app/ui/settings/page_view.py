"""设置页 · 视图（task #35 T3：从 settings_dialog.py 拆分，方法体未改动）。

Mixin：设置页 · 视图
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


class ViewPageMixin:
    """设置页 · 视图"""

    def _build_view_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        title = QLabel("视图")
        title.setProperty("h1", True)
        lay.addWidget(title)

        gb1 = QGroupBox("默认视图")
        form = QFormLayout(gb1)
        self.cmb_view = QComboBox()
        self.cmb_view.addItem("网格（封面墙）", "grid")
        self.cmb_view.addItem("列表（表格）", "list")
        cur = self.repo.get_setting("default_view_mode", "grid") or "grid"
        idx = self.cmb_view.findData(cur)
        self.cmb_view.setCurrentIndex(max(0, idx))
        self.cmb_view.currentIndexChanged.connect(self._on_view_changed)
        form.addRow("启动时视图：", self.cmb_view)
        lay.addWidget(gb1)

        hint = QLabel(
            "列表视图显示的字段及其顺序，请到『字段』页管理：勾选字段的『显示』即可显示在列表中。"
        )
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        lay.addWidget(hint)

        lay.addStretch(1)
        return w


    def _on_view_changed(self, _i: int) -> None:
        v = self.cmb_view.currentData()
        self.repo.set_setting("default_view_mode", v)
        self.default_view_changed.emit(v)
