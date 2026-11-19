"""项目网址的校验与系统浏览器入口。"""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget

from .dialogs import warn


def web_url(value: str) -> QUrl | None:
    """仅接受完整 HTTP(S) 网址，避免将元数据当作本地路径打开。"""
    url = QUrl(value.strip(), QUrl.StrictMode)
    if url.isValid() and url.scheme().lower() in ("http", "https") and url.host():
        return url
    return None


def open_web_url(parent: QWidget, value: str) -> bool:
    """在默认浏览器中打开网址；失败时给出中文反馈。"""
    url = web_url(value)
    if url is None:
        warn(parent, "无法打开链接", "请输入完整的 http:// 或 https:// 网址。")
        return False
    if not QDesktopServices.openUrl(url):
        warn(parent, "无法打开链接", "系统未能打开浏览器，请检查默认浏览器设置。")
        return False
    return True
