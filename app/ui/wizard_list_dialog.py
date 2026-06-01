"""向导列表对话框（task #11 T3）。

主菜单「工具 → 🪄 向导...」入口，按 ``meta.category`` 分组列出 ``WIZARDS`` 注册表中
所有向导，点击「启动」运行选中向导。前置条件不满足的向导会 disable 并附 tooltip。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .wizards import WIZARDS


class WizardListDialog(QDialog):
    """按 category 分组展示所有可用向导。"""

    def __init__(self, repo, library, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.library = library
        self.setWindowTitle("向导")
        self.resize(640, 540)
        self._any_applied = False

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(10)

        ttl = QLabel("🪄  向导")
        f = QFont(); f.setPointSize(14); f.setBold(True)
        ttl.setFont(f)
        v.addWidget(ttl)

        v.addWidget(QLabel(
            "按场景选择一个向导。每个向导都是引导式多步流程，"
            "不会在最终「应用」前修改库。"
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        v.addWidget(scroll, 1)
        host = QWidget()
        scroll.setWidget(host)
        self.lay = QVBoxLayout(host)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(14)

        self._build_groups()
        self.lay.addStretch(1)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        v.addWidget(bb)

    def _build_groups(self) -> None:
        # 按 category 分组，按出现顺序保留
        groups: dict[str, list[type]] = {}
        for cls in WIZARDS:
            cat = cls.meta.category
            groups.setdefault(cat, []).append(cls)

        for cat, items in groups.items():
            cat_lbl = QLabel(cat)
            f = QFont(); f.setBold(True); f.setPointSize(11)
            cat_lbl.setFont(f)
            self.lay.addWidget(cat_lbl)

            for cls in items:
                self.lay.addWidget(self._make_card(cls))

    def _make_card(self, cls) -> QWidget:
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card.setStyleSheet(
            "QFrame { background: rgba(0,0,0,0.03); border-radius: 6px; }"
        )
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(10)

        # 图标
        ico = QLabel(cls.meta.icon or "•")
        f = QFont(); f.setPointSize(20)
        ico.setFont(f)
        ico.setFixedWidth(36)
        ico.setAlignment(Qt.AlignCenter)
        h.addWidget(ico)

        # 标题 + 描述
        info = QVBoxLayout()
        info.setSpacing(2)
        ttl = QLabel(cls.meta.title)
        f2 = QFont(); f2.setBold(True); f2.setPointSize(11)
        ttl.setFont(f2)
        info.addWidget(ttl)
        desc = QLabel(cls.meta.description)
        desc.setWordWrap(True)
        desc.setProperty("muted", True)
        info.addWidget(desc)
        h.addLayout(info, 1)

        # 启动按钮
        btn = QPushButton("启动 →")
        ok, reason = cls.is_available(self.repo)
        if not ok:
            btn.setEnabled(False)
            btn.setToolTip(reason)
            cap = QLabel(f"⚠ {reason}")
            cap.setStyleSheet("color: #c62828;")
            cap.setWordWrap(True)
            info.addWidget(cap)
        btn.clicked.connect(lambda _c=False, c=cls: self._launch(c))
        h.addWidget(btn)
        return card

    def _launch(self, cls) -> None:
        try:
            wiz = cls(self)
        except Exception as e:  # noqa: BLE001
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "向导加载失败", f"{type(e).__name__}: {e}")
            return
        applied = wiz.run(self.repo, self.library)
        if applied:
            self._any_applied = True

    def any_applied(self) -> bool:
        """供调用方判断是否需要刷新主界面。"""
        return self._any_applied
