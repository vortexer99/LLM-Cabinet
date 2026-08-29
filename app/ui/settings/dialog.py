"""设置对话框框架（task #35 T3：从 settings_dialog.py 拆分，方法体未改动）。

左类别 + 右内容（QListWidget + QStackedWidget）。各页实现见同包 page_*.py mixin；字段小对话框见 field_dialogs.py。
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



from .page_about import AboutPageMixin
from .page_api import ApiPageMixin
from .page_fields import FieldsPageMixin
from .page_general import GeneralPageMixin
from .page_library import LibraryPageMixin
from .page_mcp import McpPageMixin
from .page_view import ViewPageMixin


class SettingsDialog(
    GeneralPageMixin,
    LibraryPageMixin,
    ViewPageMixin,
    FieldsPageMixin,
    ApiPageMixin,
    McpPageMixin,
    AboutPageMixin,
    QDialog,
):
    """设置面板。变更通过信号通知，调用方决定是否立即应用。"""

    default_storage_changed = Signal(str)        # "link" | "copy"
    default_view_changed = Signal(str)           # "grid" | "list"
    fields_changed = Signal()                    # 字段定义变化（增删改顺序可见性类型）

    def __init__(self, repo: Repository, library_root: Path, db_path: Path, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.library_root = library_root
        self.db_path = db_path

        self.setWindowTitle("设置")
        self.resize(720, 520)

        # ---- 类别栏 ----
        self.cat_list = QListWidget()
        self.cat_list.setObjectName("SettingsCategories")
        self.cat_list.setFixedWidth(160)
        self.cat_list.setSpacing(2)
        self._categories: list[str] = ["通用", "项目库", "视图", "字段", "API", "MCP", "关于"]
        for name in self._categories:
            QListWidgetItem(name, self.cat_list)
        self.cat_list.setCurrentRow(0)

        # ---- 内容栈 ----
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_general_page())
        self.stack.addWidget(self._build_library_page())
        self.stack.addWidget(self._build_view_page())
        self.stack.addWidget(self._build_fields_page())
        self.stack.addWidget(self._build_api_page())
        self.stack.addWidget(self._build_mcp_page())
        self.stack.addWidget(self._build_about_page())

        self.cat_list.currentRowChanged.connect(self.stack.setCurrentIndex)

        # ---- 底部按钮 ----
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.accept)

        # ---- 拼装 ----
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.cat_list)
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        # 不再写死颜色：之前用 #373a40 仅适配深色，浅色模式下会显得"黑棒"。
        # 让 Qt 用当前 palette 的默认 frame 颜色，浅 / 深都自然。
        sep.setFrameShadow(QFrame.Sunken)
        body.addWidget(sep)
        body.addWidget(self.stack, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        wrap = QWidget()
        wrap.setLayout(body)
        root.addWidget(wrap, 1)
        bb_wrap = QHBoxLayout()
        bb_wrap.setContentsMargins(12, 8, 12, 12)
        bb_wrap.addStretch(1)
        bb_wrap.addWidget(bb)
        root.addLayout(bb_wrap)


    def set_active_category(self, name: str) -> None:
        """切到指定类目页（task #15 T2 横幅"📋 设置 → 字段"按钮用）。

        ``name`` 不在已知类目里时静默忽略（保持当前页）。
        """
        try:
            idx = self._categories.index(name)
        except ValueError:
            return
        self.cat_list.setCurrentRow(idx)
