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
        # 横幅配色按当前主题取色（B4，2026-06-02）：
        # 之前写死浅蓝底 + 深蓝字，深色模式下蓝底碰到深色窗口边缘很扎眼。
        # 现在按 ``palette().window()`` 亮度判定深浅：浅色保持原配色；深色
        # 改成低饱和深蓝底 + 高对比浅蓝字，避免"白方块挖洞"观感。
        try:
            self._apply_palette_styles()
        except Exception:  # 兜底：palette/stylesheet 解析挂掉不应阻塞主窗口
            pass

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
        # 不再写死 color，让 #FirstRunBanner QLabel 选择器统一接管文字颜色
        # （在 _apply_palette_styles() 里随主题切换）
        hint = QLabel("或者把第一份资料拖到下方 DropZone 试试")
        actions.addWidget(hint)
        actions.addStretch(1)
        outer.addLayout(actions)

    # -------------------------------------------------------------- palette
    def _apply_palette_styles(self) -> None:
        """按当前 palette 选浅 / 深两套配色，并写入 stylesheet。

        注意：这里**只在构造时调用一次**。曾经实现过 ``changeEvent`` 自动跟随
        主题切换，但 ``setStyleSheet`` 在 PySide6 6.11 + Python 3.14 上会触发
        QEvent.StyleChange，重入 ``changeEvent`` → 无限递归 → 进程 abort。
        简单退一步：主题切换走全局 ``apply_theme(app, …)`` 重 apply 主 QSS 时，
        本 banner 通过 palette 继承拿到新底色已经够看；不再追求"banner 内部
        颜色精确响应主题切换"，避免那条危险的递归路径。
        """
        from PySide6.QtGui import QPalette
        win = self.palette().color(QPalette.Window)
        dark = win.lightness() < 128
        if dark:
            bg, border, fg = "#1c2c44", "#2d4a73", "#9ec5fe"
        else:
            bg, border, fg = "#e8f4fd", "#b3d9f5", "#0d47a1"
        self.setStyleSheet(
            f"#FirstRunBanner {{"
            f"  background-color: {bg};"
            f"  border: 1px solid {border};"
            f"  border-radius: 6px;"
            f"}}"
            f"#FirstRunBanner QLabel {{ color: {fg}; }}"
        )

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
