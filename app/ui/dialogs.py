"""统一的中文对话框封装（task #37）。

全 UI 的确认 / 警告 / 提示都走这里，保证：
- 按钮文案是中文（Qt 默认标准按钮未加载中文翻译时是英文）
- 危险操作的默认焦点在「取消」上，确认按钮可套 danger 样式
- 批量结果支持详细文本（detailed）

约定（与 AGENTS.md「操作反馈策略」一致）：
- 即时小反馈 → statusBar，不用这里的对话框
- 需要用户知晓后果 / 做决定 → confirm / ask_yes_no_cancel
- 批量操作结果 → 一次汇总（info/warn + detailed），不在循环里弹窗
"""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox


def _build(parent, icon, title: str, text: str, detailed: str | None) -> QMessageBox:
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    if detailed:
        box.setDetailedText(detailed)
    return box


def _polish(btn) -> None:
    """动态 property 改动后刷新样式。"""
    st = btn.style()
    if st is not None:
        st.unpolish(btn)
        st.polish(btn)


def confirm(
    parent,
    title: str,
    text: str,
    *,
    yes: str = "确定",
    no: str = "取消",
    danger: bool = False,
    default_yes: bool = False,
    informative: str | None = None,
    detailed: str | None = None,
) -> bool:
    """中文二选一确认框。返回 True = 用户点了 yes 侧按钮。

    - ``danger=True``：yes 按钮套危险色样式，且默认焦点强制在 no 上
    - 默认焦点：danger 或 default_yes=False 时在 no；否则在 yes
    """
    box = _build(parent, QMessageBox.Question, title, text, detailed)
    if informative:
        box.setInformativeText(informative)
    btn_yes = box.addButton(yes, QMessageBox.YesRole)
    btn_no = box.addButton(no, QMessageBox.NoRole)
    if danger:
        btn_yes.setProperty("danger", True)
        _polish(btn_yes)
    box.setDefaultButton(btn_yes if (default_yes and not danger) else btn_no)
    box.exec()
    return box.clickedButton() is btn_yes


def ask_yes_no_cancel(
    parent,
    title: str,
    text: str,
    *,
    yes: str,
    no: str,
    cancel: str = "取消",
    default: str = "yes",
    informative: str | None = None,
) -> str:
    """三选一对话框。返回 ``"yes"`` / ``"no"`` / ``"cancel"``。"""
    box = _build(parent, QMessageBox.Question, title, text, None)
    if informative:
        box.setInformativeText(informative)
    btn_yes = box.addButton(yes, QMessageBox.YesRole)
    btn_no = box.addButton(no, QMessageBox.NoRole)
    btn_cancel = box.addButton(cancel, QMessageBox.RejectRole)
    box.setDefaultButton(
        {"yes": btn_yes, "no": btn_no}.get(default, btn_cancel)
    )
    box.exec()
    clicked = box.clickedButton()
    if clicked is btn_yes:
        return "yes"
    if clicked is btn_no:
        return "no"
    return "cancel"


def info(parent, title: str, text: str, *, detailed: str | None = None) -> None:
    """提示框（单「好的」按钮）。"""
    box = _build(parent, QMessageBox.Information, title, text, detailed)
    btn = box.addButton("好的", QMessageBox.AcceptRole)
    box.setDefaultButton(btn)
    box.exec()


def warn(parent, title: str, text: str, *, detailed: str | None = None) -> None:
    """警告框（单「好的」按钮）。"""
    box = _build(parent, QMessageBox.Warning, title, text, detailed)
    btn = box.addButton("好的", QMessageBox.AcceptRole)
    box.setDefaultButton(btn)
    box.exec()


def error(parent, title: str, text: str, *, detailed: str | None = None) -> None:
    """错误框（单「好的」按钮）。"""
    box = _build(parent, QMessageBox.Critical, title, text, detailed)
    btn = box.addButton("好的", QMessageBox.AcceptRole)
    box.setDefaultButton(btn)
    box.exec()
