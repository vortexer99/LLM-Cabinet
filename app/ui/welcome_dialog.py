"""首次启动 Welcome 对话框（task #15 T3）。

应用启动时若 ``cabinet.json`` 不存在（=首次安装），先弹本对话框让用户选择
建库方式，而非直接打开默认库。

三个选项：
1. 在自定义位置新建库（→ 走 task #15 T1 的多页向导）
2. 使用默认位置（``%APPDATA%/LLMCabinet``，最快上手；D5 不补描述）
3. 打开已有的库目录（→ 调系统目录选择器，目录必须含 ``.llm-cabinet`` 标记）

「退出」 → 直接退出应用，不进主窗口。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..cabinet import CabinetConfig, is_library_dir


# 对话框返回值（QDialog.DialogCode 自定义复用）
RESULT_NEW_CUSTOM = 100      # 新建（自定义位置） → 走 T1 向导
RESULT_NEW_DEFAULT = 101     # 新建（默认位置） → 直接走默认库初始化
RESULT_OPEN_EXISTING = 102   # 打开已有目录


class WelcomeDialog(QDialog):
    """三选一的欢迎对话框。

    用法：
        dlg = WelcomeDialog(cabinet_config, parent=...)
        rc = dlg.exec()
        if rc == RESULT_OPEN_EXISTING:
            path = dlg.opened_path  # 用户选的现有库目录
        elif rc == RESULT_NEW_CUSTOM:
            ...  # 主程序后续走 NewLibraryWizard
        elif rc == RESULT_NEW_DEFAULT:
            ...  # 主程序后续走默认库初始化
        else:
            sys.exit(0)
    """

    def __init__(
        self,
        cabinet_config: CabinetConfig,
        *,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("欢迎使用 LLM Cabinet")
        self.setMinimumSize(560, 480)
        self.cabinet_config = cabinet_config
        self.opened_path: Optional[Path] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(14)

        # 标题
        ttl = QLabel("🗄️  LLM Cabinet")
        f = QFont(); f.setPointSize(20); f.setBold(True)
        ttl.setFont(f)
        ttl.setAlignment(Qt.AlignCenter)
        outer.addWidget(ttl)

        sub = QLabel("你的本地资料库 / AI 元数据助理")
        sub.setAlignment(Qt.AlignCenter)
        sub.setProperty("muted", True)
        outer.addWidget(sub)

        intro = QLabel("这是你第一次使用，先来建一个库吧：")
        intro.setAlignment(Qt.AlignCenter)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        outer.addSpacing(6)
        outer.addWidget(self._make_card(
            "📁  在自定义位置新建库",
            "选择目录 → 自定义字段 → 立即可用",
            on_click=lambda: self.done(RESULT_NEW_CUSTOM),
        ))
        outer.addWidget(self._make_card(
            "⚡  使用默认位置（最快上手）",
            "应用数据目录（%APPDATA%/LLMCabinet 等），等用熟了再考虑自定义位置",
            on_click=lambda: self.done(RESULT_NEW_DEFAULT),
        ))
        outer.addWidget(self._make_card(
            "📂  打开已有的库目录",
            "从备份恢复、或迁移自其它机器（目录需包含 .llm-cabinet 标记）",
            on_click=self._on_open_existing,
        ))

        outer.addStretch(1)

        # 底部退出
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        b_quit = QPushButton("退出")
        b_quit.clicked.connect(self.reject)
        bottom.addWidget(b_quit)
        outer.addLayout(bottom)

    def _make_card(
        self,
        title: str,
        subtitle: str,
        *,
        on_click,
    ) -> QWidget:
        """造一张可点击的卡片（用 QPushButton 包一个 QWidget）。"""
        card = QPushButton()
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        card.setFlat(False)
        card.setMinimumHeight(72)
        card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet(
            "QPushButton { text-align: left; padding: 10px 14px; "
            "  border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; "
            "  background-color: rgba(0,0,0,0.02); }"
            "QPushButton:hover { background-color: rgba(33,150,243,0.08); "
            "  border-color: rgba(33,150,243,0.5); }"
        )
        wrap = QVBoxLayout(card)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(2)
        lbl_t = QLabel(f"<b>{title}</b>")
        lbl_t.setTextFormat(Qt.RichText)
        wrap.addWidget(lbl_t)
        lbl_s = QLabel(subtitle)
        lbl_s.setProperty("muted", True)
        lbl_s.setWordWrap(True)
        wrap.addWidget(lbl_s)
        card.clicked.connect(on_click)
        return card

    # ---- 选项 3：打开已有库目录 ------------------------------------------
    def _on_open_existing(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "选择已有的库目录（必须含 .llm-cabinet 标记）",
        )
        if not d:
            return  # 用户取消选择，不关闭 Welcome
        path = Path(d)
        if not is_library_dir(path):
            QMessageBox.warning(
                self, "目录无效",
                f"目录 {path} 不是一个有效的 LLM Cabinet 库（缺少 .llm-cabinet 标记）。\n"
                "请选择由本应用之前创建过的目录，或选「在自定义位置新建库」走新建流程。",
            )
            return
        self.opened_path = path
        self.done(RESULT_OPEN_EXISTING)
