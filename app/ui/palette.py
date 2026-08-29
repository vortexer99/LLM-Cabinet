"""统一色板（task #34）。

v0.6 起废弃深色主题，仅保留浅色单主题。Python 侧（delegate 绘制、
内联 stylesheet 的小组件）的颜色一律从本模块取，不再写死十六进制；
QSS 侧颜色在 ``theme.py`` 单文件维护。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    bg0: str          # 窗口背景
    bg1: str          # 面板/卡片背景
    bg2: str          # 控件背景
    bg3: str          # hover / 深一档
    border: str
    fg0: str          # 主文字
    fg1: str          # 次文字
    fg2: str          # 弱文字
    accent: str       # 主色（链接/选中边框）
    accent_hover: str
    select_bg: str    # 选中背景
    select_fg: str    # 选中文字
    warn: str
    danger: str


LIGHT = Palette(
    bg0="#ffffff",
    bg1="#f8f9fa",
    bg2="#e9ecef",
    bg3="#dee2e6",
    border="#dee2e6",
    fg0="#212529",
    fg1="#495057",
    fg2="#868e96",
    accent="#228be6",
    accent_hover="#1c7ed6",
    select_bg="#d0ebff",
    select_fg="#1971c2",
    warn="#f59f00",
    danger="#fa5252",
)


def current() -> Palette:
    """返回当前色板。单主题时代恒为 LIGHT；保留函数形态以便调用点不感知。"""
    return LIGHT
