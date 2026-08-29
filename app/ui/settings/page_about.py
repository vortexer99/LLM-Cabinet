"""设置页 · 关于（task #35 T3：从 settings_dialog.py 拆分，方法体未改动）。

Mixin：设置页 · 关于
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
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
from ...utils import app_data_dir, app_icon_path, reveal_in_explorer
from ..dialogs import info, warn


class AboutPageMixin:
    """设置页 · 关于"""

    def _build_about_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)

        title = QLabel("关于")
        title.setProperty("h1", True)
        lay.addWidget(title)

        # 顶部品牌区：图标 + 应用名 + 版本 + 副标题
        brand = QHBoxLayout()
        brand.setSpacing(14)

        # 高 DPI 友好的目标尺寸（逻辑像素 96，物理像素按 devicePixelRatio 放大）
        target_logical = 96
        dpr = self.devicePixelRatioF() or 1.0
        target_phys = int(round(target_logical * dpr))

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(target_logical, target_logical)
        icon_lbl.setAlignment(Qt.AlignCenter)
        ip = app_icon_path()
        if ip is not None:
            pix: QPixmap | None = None
            suffix = ip.suffix.lower()
            if suffix == ".ico":
                # ico 是多尺寸容器；QPixmap 直接加载只取第一帧（通常 16×16 → 拉伸糊）
                # 用 QIcon 加载，再让它按目标尺寸挑/合成最合适的子图
                icon = QIcon(str(ip))
                pix = icon.pixmap(target_phys, target_phys)
            else:
                pix = QPixmap(str(ip))
                if not pix.isNull():
                    pix = pix.scaled(
                        target_phys, target_phys,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
            if pix is not None and not pix.isNull():
                pix.setDevicePixelRatio(dpr)
                icon_lbl.setPixmap(pix)
        brand.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        name_lbl = QLabel("<b style='font-size:18pt;'>LLM Cabinet</b>")
        name_lbl.setTextFormat(Qt.RichText)
        text_col.addWidget(name_lbl)
        ver_lbl = QLabel(
            f"应用版本 v{__version__}　·　数据库 schema v{SCHEMA_VERSION}"
        )
        ver_lbl.setProperty("muted", True)
        ver_lbl.setToolTip(
            "应用版本（__version__）和数据库 schema 版本独立递增。\n"
            "升级新版应用打开旧 db 时，会自动备份并应用迁移脚本。"
        )
        text_col.addWidget(ver_lbl)
        text_col.addSpacing(4)
        sub_lbl = QLabel("带 AI 元数据助手的轻量级项目化文件管理器")
        sub_lbl.setWordWrap(True)
        text_col.addWidget(sub_lbl)
        text_col.addStretch(1)
        brand.addLayout(text_col, 1)

        brand_wrap = QWidget()
        brand_wrap.setLayout(brand)
        lay.addWidget(brand_wrap)

        # 数据隐私
        lay.addSpacing(18)
        privacy_row = QHBoxLayout()
        privacy_lbl = QLabel("数据隐私：")
        privacy_lbl.setFixedWidth(110)
        privacy_lbl.setProperty("muted", True)
        privacy_btn = QPushButton("📄 查看《数据隐私声明》")
        privacy_btn.setProperty("flat", True)
        privacy_btn.clicked.connect(self._open_privacy_doc)
        privacy_row.addWidget(privacy_lbl)
        privacy_row.addWidget(privacy_btn)
        privacy_row.addStretch(1)
        lay.addLayout(privacy_row)

        # License
        lic_row = QHBoxLayout()
        lic_lbl = QLabel("许可证：")
        lic_lbl.setFixedWidth(110)
        lic_lbl.setProperty("muted", True)
        lic_text = QLabel("MIT License")
        lic_row.addWidget(lic_lbl)
        lic_row.addWidget(lic_text)
        lic_row.addStretch(1)
        lay.addLayout(lic_row)

        # 免责声明
        disc_row = QHBoxLayout()
        disc_lbl = QLabel("免责声明：")
        disc_lbl.setFixedWidth(110)
        disc_lbl.setProperty("muted", True)
        disc_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        disc_text = QLabel(
            "本人将持续维护本软件，但不对任何使用过程中（包括但不限于异常使用、"
            "误操作、系统故障、第三方 LLM 服务异常）导致的文件丢失、数据损坏或"
            "其他损失承担责任。请通过定期备份保护重要数据。本软件按"
            "「原样」提供，详见 MIT License。"
        )
        disc_text.setWordWrap(True)
        disc_text.setProperty("muted", True)
        disc_row.addWidget(disc_lbl)
        disc_row.addWidget(disc_text, 1)
        lay.addLayout(disc_row)

        # 项目主页（GitHub）
        gh_row = QHBoxLayout()
        gh_lbl = QLabel("项目主页：")
        gh_lbl.setFixedWidth(110)
        gh_lbl.setProperty("muted", True)
        gh_link = QLabel(
            f"<a href='{HOMEPAGE_URL}'>{HOMEPAGE_URL}</a>"
        )
        gh_link.setTextFormat(Qt.RichText)
        gh_link.setOpenExternalLinks(True)
        gh_link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        gh_link.setToolTip("在浏览器中打开 GitHub 仓库")
        gh_row.addWidget(gh_lbl)
        gh_row.addWidget(gh_link)
        gh_row.addStretch(1)
        lay.addLayout(gh_row)

        lay.addStretch(1)
        return w


    def _open_privacy_doc(self) -> None:
        """打开 PRIVACY 文件。UI 是中文，优先打开中文版；找不到再退回英文版。"""
        # 源码运行模式：仓库根目录下；打包后：可执行文件目录 / _MEIPASS
        # （本文件位于 app/ui/settings/，parents[3] 才是仓库根）
        roots: list[Path] = [Path(__file__).resolve().parents[3], Path.cwd()]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass))
        if getattr(sys, "frozen", False):
            roots.append(Path(sys.executable).parent)
        names = ("PRIVACY.zh-CN.md", "PRIVACY.md")
        for root in roots:
            for n in names:
                p = root / n
                if p.is_file():
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
                    return
        # 兜底：弹一个对话框直接展示在线版/本地缺失提示
        info(
            self, "数据隐私声明",
            "未找到本地 PRIVACY 文件。请到项目仓库根目录查看。",
        )
