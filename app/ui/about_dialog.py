"""轻量"关于"对话框（不依赖 Repository / 已打开的库）。

与「设置 → 关于」页内容保持一致，但作为独立 ``QDialog`` 可在 Welcome 期间被
点开（那时还没有库 / repo）。设置页的 ``_build_about_page`` 仍保留作为分类页面，
两者使用同一份信息源（``__version__`` / ``HOMEPAGE_URL`` / `app_icon_path()`），
内容若有调整两边同步即可。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import HOMEPAGE_URL, __version__
from ..db import SCHEMA_VERSION
from ..utils import app_icon_path


class AboutDialog(QDialog):
    """独立的关于对话框。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("关于 LLM Cabinet")
        self.setMinimumSize(560, 360)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 14)
        outer.setSpacing(10)

        # 顶部品牌区：图标 + 应用名 + 版本 + 副标题
        brand = QHBoxLayout()
        brand.setSpacing(14)

        target_logical = 96
        dpr = self.devicePixelRatioF() or 1.0
        target_phys = int(round(target_logical * dpr))

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(target_logical, target_logical)
        icon_lbl.setAlignment(Qt.AlignCenter)
        ip = app_icon_path()
        if ip is not None:
            pix: QPixmap | None = None
            if ip.suffix.lower() == ".ico":
                # ico 是多尺寸容器；用 QIcon 加载再让它按目标尺寸挑/合成
                pix = QIcon(str(ip)).pixmap(target_phys, target_phys)
            else:
                pix = QPixmap(str(ip))
                if not pix.isNull():
                    pix = pix.scaled(
                        target_phys, target_phys,
                        Qt.KeepAspectRatio, Qt.SmoothTransformation,
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
        outer.addWidget(brand_wrap)

        outer.addSpacing(10)

        # 数据隐私
        privacy_row = QHBoxLayout()
        privacy_lbl = QLabel("数据隐私：")
        privacy_lbl.setFixedWidth(110)
        privacy_lbl.setProperty("muted", True)
        privacy_btn = QPushButton("📄 查看《数据隐私声明》")
        privacy_btn.clicked.connect(self._open_privacy_doc)
        privacy_row.addWidget(privacy_lbl)
        privacy_row.addWidget(privacy_btn)
        privacy_row.addStretch(1)
        outer.addLayout(privacy_row)

        # License
        lic_row = QHBoxLayout()
        lic_lbl = QLabel("许可证：")
        lic_lbl.setFixedWidth(110)
        lic_lbl.setProperty("muted", True)
        lic_text = QLabel("MIT License")
        lic_row.addWidget(lic_lbl)
        lic_row.addWidget(lic_text)
        lic_row.addStretch(1)
        outer.addLayout(lic_row)

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
        outer.addLayout(disc_row)

        # 项目主页
        gh_row = QHBoxLayout()
        gh_lbl = QLabel("项目主页：")
        gh_lbl.setFixedWidth(110)
        gh_lbl.setProperty("muted", True)
        gh_link = QLabel(f"<a href='{HOMEPAGE_URL}'>{HOMEPAGE_URL}</a>")
        gh_link.setTextFormat(Qt.RichText)
        gh_link.setOpenExternalLinks(True)
        gh_link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        gh_link.setToolTip("在浏览器中打开 GitHub 仓库")
        gh_row.addWidget(gh_lbl)
        gh_row.addWidget(gh_link)
        gh_row.addStretch(1)
        outer.addLayout(gh_row)

        outer.addStretch(1)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        outer.addWidget(bb)

    def _open_privacy_doc(self) -> None:
        """打开 PRIVACY 文件。UI 是中文，优先打开中文版；找不到再退回英文版。"""
        roots: list[Path] = [Path(__file__).resolve().parents[2], Path.cwd()]
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
        QMessageBox.information(
            self, "数据隐私声明",
            "未找到本地 PRIVACY 文件。请到项目仓库根目录查看。",
        )
