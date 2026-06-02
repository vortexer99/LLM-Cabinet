"""首次进入引导横幅（task #15 T2）。

显示在主窗口中央区域顶部，引导刚建好库的用户接下来该做什么。

D4 一次性标志：
* 用户成功应用一次「库字段设计助手」 → 永久隐藏
* 用户加过非系统字段                    → 永久隐藏
* 用户成功创建过第一个项目              → 永久隐藏
* 用户在横幅上点「不再显示 ✕」          → 永久隐藏

任一触发 → ``settings.library_first_run_dismissed = "1"``。
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)


SETTING_KEY = "library_first_run_dismissed"


def should_show_banner(repo) -> bool:
    """T2 横幅显示条件 — 全部满足才显示。

    * ``library_first_run_dismissed != "1"``
    * ``count_projects_total() == 0``
    * ``count_user_added_fields() == 0``
    """
    if repo is None:
        return False
    try:
        if (repo.get_setting(SETTING_KEY, "0") or "0") == "1":
            return False
        if repo.count_projects_total() > 0:
            return False
        if repo.count_user_added_fields() > 0:
            return False
    except Exception:
        return False
    return True


def dismiss_banner(repo) -> None:
    """D4 一次性标志触发入口（任意来源调用）。

    幂等：已经置 1 时再调一次也无副作用。失败静默（不应阻塞业务流）。
    """
    if repo is None:
        return
    try:
        repo.set_setting(SETTING_KEY, "1")
    except Exception:
        pass


class FirstRunBanner(QFrame):
    """主窗口顶部的引导横幅。

    Signals:
        run_wizard_requested: 用户点「🪄 库字段设计助手」
        open_settings_fields_requested: 用户点「📋 设置 → 字段」
        dismissed: 用户点「不再显示」（外部应同步调用 ``dismiss_banner``）
    """

    run_wizard_requested = Signal()
    open_settings_fields_requested = Signal()
    dismissed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("FirstRunBanner")
        self.setFrameShape(QFrame.NoFrame)
        # 圆角浅蓝背景 — 与全局主题相容（不写死颜色，靠 stylesheet）
        self.setStyleSheet(
            "#FirstRunBanner {"
            "  background-color: #e8f4fd;"
            "  border: 1px solid #b3d9f5;"
            "  border-radius: 6px;"
            "}"
            "#FirstRunBanner QLabel {"
            "  color: #0d47a1;"
            "}"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        head = QHBoxLayout()
        ttl = QLabel("🎉 <b>新库已创建！下一步可以…</b>")
        ttl.setTextFormat(Qt.RichText)
        head.addWidget(ttl)
        head.addStretch(1)
        b_dismiss = QPushButton("不再显示 ✕")
        b_dismiss.setFlat(True)
        b_dismiss.setCursor(Qt.PointingHandCursor)
        b_dismiss.clicked.connect(self._on_dismiss)
        head.addWidget(b_dismiss)
        outer.addLayout(head)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        b_wiz = QPushButton("🪄 库字段设计助手")
        b_wiz.setToolTip("让 LLM 根据你的库描述帮你规划字段方案")
        b_wiz.clicked.connect(self.run_wizard_requested)
        actions.addWidget(b_wiz)
        b_fields = QPushButton("📋 设置 → 字段")
        b_fields.setToolTip("自己手动加几个字段")
        b_fields.clicked.connect(self.open_settings_fields_requested)
        actions.addWidget(b_fields)
        hint = QLabel(
            "<span style='color:#1565c0'>或者把第一份资料拖到下方 DropZone 试试</span>"
        )
        hint.setTextFormat(Qt.RichText)
        actions.addWidget(hint)
        actions.addStretch(1)
        outer.addLayout(actions)

    def _on_dismiss(self) -> None:
        self.dismissed.emit()
        self.hide()

    # ---- 静态工具：把横幅装到一个 QLayout 里并管控显隐 ----
    @staticmethod
    def install(
        parent_layout,
        repo,
        *,
        on_run_wizard: Callable[[], None],
        on_open_settings_fields: Callable[[], None],
        index: int = -1,
    ) -> "FirstRunBanner":
        """把横幅插到 layout 中（默认追加到末尾），按显示条件初始 visible。

        返回横幅实例供外部 refresh 用。点击「不再显示」会自动写
        ``library_first_run_dismissed=1`` 并隐藏。
        """
        banner = FirstRunBanner()
        banner.run_wizard_requested.connect(on_run_wizard)
        banner.open_settings_fields_requested.connect(on_open_settings_fields)

        def _on_dismiss():
            dismiss_banner(repo)
        banner.dismissed.connect(_on_dismiss)

        if index >= 0:
            parent_layout.insertWidget(index, banner)
        else:
            parent_layout.addWidget(banner)
        banner.setVisible(should_show_banner(repo))
        return banner
