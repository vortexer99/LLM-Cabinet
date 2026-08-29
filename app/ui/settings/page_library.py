"""设置页 · 项目库（task #35 T3：从 settings_dialog.py 拆分，方法体未改动）。

Mixin：设置页 · 项目库
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


class LibraryPageMixin:
    """设置页 · 项目库"""

    def _build_library_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        title = QLabel("项目库")
        title.setProperty("h1", True)
        lay.addWidget(title)

        gb1 = QGroupBox("默认导入行为")
        form = QFormLayout(gb1)
        self.cmb_storage = QComboBox()
        self.cmb_storage.addItem("🔗 链接（仅记录路径，不动用户文件）", "link")
        self.cmb_storage.addItem("📦 仓储（复制到统一仓库目录）", "copy")
        cur = self.repo.get_setting("default_storage_mode", "link") or "link"
        idx = self.cmb_storage.findData(cur)
        self.cmb_storage.setCurrentIndex(max(0, idx))
        self.cmb_storage.currentIndexChanged.connect(self._on_storage_changed)
        form.addRow("默认存储方式：", self.cmb_storage)
        hint = QLabel(
            "拖放新建项目、以及向项目里添加文件时使用此默认值。\n"
            "每次添加文件仍可在弹出的对话框中临时改选；"
            "存储方式是文件级属性，同一项目内可混合存在。"
        )
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        form.addRow("", hint)

        self.chk_ignore_dotfiles = QCheckBox("忽略 . 开头的文件和文件夹")
        cur_idf = self.repo.get_setting("import_ignore_dotfiles", "1")
        self.chk_ignore_dotfiles.setChecked(cur_idf == "1")
        self.chk_ignore_dotfiles.toggled.connect(self._on_ignore_dotfiles_changed)
        form.addRow("隐藏文件：", self.chk_ignore_dotfiles)
        idf_hint = QLabel(
            "开启（默认）：跳过 .gitignore、.env、.git/ 等以 . 开头的文件和目录。\n"
            "关闭：导入所有文件，包括隐藏文件。"
        )
        idf_hint.setProperty("hint", True)
        idf_hint.setWordWrap(True)
        form.addRow("", idf_hint)

        lay.addWidget(gb1)

        gb2 = QGroupBox("库目录")
        v = QVBoxLayout(gb2)
        path_row = QHBoxLayout()
        self.ed_lib = QLineEdit(str(self.library_root))
        self.ed_lib.setReadOnly(True)
        btn_open = QPushButton("📂  打开")
        btn_open.clicked.connect(lambda: reveal_in_explorer(self.library_root))
        path_row.addWidget(self.ed_lib, 1)
        path_row.addWidget(btn_open)
        v.addLayout(path_row)
        tip = QLabel("仓库目录用于存放『复制』模式下导入的文件。")
        tip.setProperty("hint", True)
        tip.setWordWrap(True)
        v.addWidget(tip)
        lay.addWidget(gb2)

        # 数据位置（只读 + 打开按钮）
        gb3 = QGroupBox("数据位置")
        gv = QVBoxLayout(gb3)
        for label, path in (
            ("数据库", self.db_path),
        ):
            row = QHBoxLayout()
            lbl = QLabel(f"{label}：")
            lbl.setFixedWidth(110)
            lbl.setProperty("muted", True)
            ed = QLineEdit(str(path))
            ed.setReadOnly(True)
            b_open = QPushButton("📂")
            b_open.setToolTip("在资源管理器中打开")
            b_open.setProperty("flat", True)
            # 数据库定位到所在目录；目录直接打开
            target = path if Path(path).is_dir() else Path(path).parent
            b_open.clicked.connect(lambda _=False, t=target: reveal_in_explorer(t))
            row.addWidget(lbl)
            row.addWidget(ed, 1)
            row.addWidget(b_open)
            gv.addLayout(row)

        # Schema 版本 + 备份状态
        ver_row = QHBoxLayout()
        ver_cap = QLabel("Schema 版本：")
        ver_cap.setFixedWidth(110)
        ver_cap.setProperty("muted", True)
        ver_val = QLabel(f"v{SCHEMA_VERSION}")
        ver_val.setToolTip(
            "数据库 schema 版本号（独立于应用版本号）。\n"
            "升级新版应用打开旧 db 时，会自动备份并应用迁移脚本。\n"
            "备份文件落在数据库同目录，文件名形如 cabinet.vN.时间戳.bak"
        )
        # 顺手统计同目录下的 .bak 数量
        try:
            bak_dir = Path(self.db_path).parent
            n_bak = sum(
                1 for p in bak_dir.glob(f"{Path(self.db_path).stem}.v*.bak")
            )
        except OSError:
            n_bak = 0
        bak_info = QLabel(
            f"·  自动备份：{n_bak} 份" if n_bak else "·  自动备份：暂无"
        )
        bak_info.setProperty("muted", True)
        ver_row.addWidget(ver_cap)
        ver_row.addWidget(ver_val)
        ver_row.addSpacing(8)
        ver_row.addWidget(bak_info)
        ver_row.addStretch(1)
        gv.addLayout(ver_row)

        lay.addWidget(gb3)

        lay.addStretch(1)
        return w


    def _on_storage_changed(self, _i: int) -> None:
        v = self.cmb_storage.currentData()
        self.repo.set_setting("default_storage_mode", v)
        self.default_storage_changed.emit(v)


    def _on_ignore_dotfiles_changed(self, checked: bool) -> None:
        self.repo.set_setting("import_ignore_dotfiles", "1" if checked else "0")
