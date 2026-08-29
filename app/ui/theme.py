"""全局 QSS 主题（浅色单主题）。

task #34 起废弃深色主题：受精力所限不再维护双主题，仅保留浅色。
Python 侧颜色统一走 ``palette.py``；本文件只维护 QSS。

色板：
  --bg-0  #ffffff   --bg-1  #f8f9fa   --bg-2  #e9ecef   --bg-3  #dee2e6
  --fg-0  #212529   --fg-1  #495057   --fg-2  #868e96
  --accent #228be6  --accent-h #1c7ed6
  --warn   #f59f00  --danger #fa5252
"""
from __future__ import annotations

QSS_LIGHT = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 13px;
    color: #212529;
}
QMainWindow, QDialog, QWidget#CentralRoot { background: #ffffff; }
QStatusBar { background: #f8f9fa; color: #495057; border-top: 1px solid #dee2e6; }
QStatusBar::item { border: none; }

/* 工具栏 */
QToolBar { background: #f8f9fa; border: none; spacing: 4px; padding: 6px 8px; border-bottom: 1px solid #dee2e6; }
QToolBar QToolButton {
    background: transparent; color: #212529;
    border: 1px solid transparent; border-radius: 6px;
    padding: 5px 10px; margin: 0 2px;
}
QToolBar QToolButton:hover { background: #e9ecef; border-color: #dee2e6; }
QToolBar QToolButton:pressed { background: #dee2e6; }
QToolBar::separator { background: #dee2e6; width: 1px; margin: 6px 6px; }

QWidget#SidePanel, QWidget#CenterPanel, QWidget#DetailPanel { background: #ffffff; }
QFrame#Card { background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 8px; }

/* GroupBox（避免 fusion 默认 panel 引发的视觉错位） */
QGroupBox {
    background: transparent;
    border: 1px solid #dee2e6;
    border-radius: 6px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    color: #212529;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    background: #ffffff;
    color: #495057;
}
QGroupBox > QLabel,
QGroupBox QLabel {
    color: #212529;
    background: transparent;
}

/* 设置 → API ScrollArea 规则 */
QScrollArea#AppScrollArea {
    background: #ffffff;
    border: none;
}
QWidget#ApiScrollHost {
    background: #ffffff;
}
QWidget#AppScrollHost {
    background: #ffffff;
}

/* 输入 */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {
    background: #ffffff; border: 1px solid #ced4da;
    border-radius: 6px; padding: 6px 8px;
    selection-background-color: #228be6; selection-color: #ffffff;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus { border: 1px solid #228be6; }
QLineEdit#SearchBox {
    padding: 7px 10px 7px 28px;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    border: none;
    width: 22px;
}
QComboBox::down-arrow {
    image: url("__ARROW_LIGHT__");
    width: 10px; height: 6px;
    margin-right: 8px;
}
QComboBox::down-arrow:disabled {
    image: url("__ARROW_DISABLED__");
}
QComboBox:disabled {
    color: #adb5bd;
    background: #f1f3f5;
    border: 1px solid #dee2e6;
}
QComboBox QAbstractItemView {
    background: #ffffff; border: 1px solid #dee2e6;
    selection-background-color: #228be6; selection-color: #ffffff; outline: 0;
}

/* 按钮 */
QPushButton {
    background: #ffffff; color: #212529;
    border: 1px solid #ced4da; border-radius: 6px;
    padding: 6px 14px; min-height: 18px;
}
QPushButton:hover { background: #f1f3f5; border-color: #adb5bd; }
QPushButton:pressed { background: #e9ecef; }
QPushButton:disabled { color: #adb5bd; background: #f8f9fa; }

QPushButton[primary="true"] {
    background: #228be6; color: #ffffff; border: none; font-weight: 600;
}
QPushButton[primary="true"]:hover { background: #1c7ed6; }
QPushButton[primary="true"]:pressed { background: #1971c2; }
QPushButton[danger="true"]:hover { background: #fa5252; color: #fff; border-color: #fa5252; }
QPushButton[flat="true"] { background: transparent; border: none; padding: 4px 8px; }
QPushButton[flat="true"]:hover { background: #e9ecef; }

QToolButton {
    background: transparent; border: 1px solid transparent;
    border-radius: 6px; padding: 4px 8px; color: #212529;
}
QToolButton:hover { background: #e9ecef; border-color: #dee2e6; }
QToolButton:checked { background: #dee2e6; border-color: #228be6; }

/* 列表 */
QListWidget, QListView { background: #ffffff; border: none; outline: 0; }
QListWidget::item { padding: 8px 10px; border-radius: 6px; margin: 2px 4px; color: #212529; }
QListWidget::item:hover { background: #f1f3f5; }
QListWidget::item:selected { background: #d0ebff; color: #1971c2; }

/* 网格视图（卡片；卡片本体由 ProjectCardDelegate 自绘，这里控制项间距与选中底） */
QListView#ProjectGrid { background: #ffffff; border: none; padding: 8px; }
QListView#ProjectGrid::item {
    background: #f8f9fa;
    border: 1px solid #dee2e6;
    border-radius: 8px;
    padding: 0;
    margin: 6px;
}
QListView#ProjectGrid::item:hover { border-color: #228be6; }
QListView#ProjectGrid::item:selected {
    border-color: #228be6;
    background: #d0ebff;
}

/* 表格 */
QTableWidget, QTableView {
    background: #ffffff;
    alternate-background-color: #f8f9fa;
    border: 1px solid #dee2e6; border-radius: 6px;
    gridline-color: #e9ecef;
    selection-background-color: #d0ebff; selection-color: #1971c2;
    outline: 0;
}
QTableWidget::item, QTableView::item { padding: 6px 8px; border: none; }
QHeaderView::section {
    background: #f8f9fa; color: #495057;
    border: none; border-right: 1px solid #dee2e6; border-bottom: 1px solid #dee2e6;
    padding: 6px 8px; font-weight: 600;
}
/* 表头本身的背景（最后一段 section 后面的空白区） */
QHeaderView { background: #f8f9fa; border: none; }
QTableCornerButton::section { background: #f8f9fa; border: none; }

/* 滚动条 */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #ced4da; min-height: 30px; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #adb5bd; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #ced4da; min-width: 30px; border-radius: 5px; }
QScrollBar::handle:horizontal:hover { background: #adb5bd; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }

/* Splitter */
QSplitter::handle { background: #ffffff; }
QSplitter::handle:horizontal { width: 1px; background: #dee2e6; }
QSplitter::handle:vertical   { height: 1px; background: #dee2e6; }
QSplitter::handle:hover { background: #228be6; }

/* TextBrowser */
QTextBrowser {
    background: #f8f9fa; border: 1px solid #dee2e6;
    border-radius: 8px; padding: 12px 14px;
}
QTextBrowser a { color: #228be6; }

/* Slider */
QSlider::groove:horizontal { background: #dee2e6; height: 4px; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #228be6; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #495057; width: 12px; height: 12px;
    margin: -5px 0; border-radius: 6px;
}
QSlider::handle:horizontal:hover { background: #228be6; }

/* MenuBar（主窗口顶部菜单栏） */
QMenuBar {
    background: #f8f9fa;
    color: #212529;
    border-bottom: 1px solid #dee2e6;
    padding: 2px 4px;
}
QMenuBar::item {
    background: transparent;
    padding: 4px 10px;
    border-radius: 4px;
}
QMenuBar::item:selected { background: #e9ecef; }
QMenuBar::item:pressed  { background: #dee2e6; }

/* Menu */
QMenu { background: #ffffff; border: 1px solid #dee2e6; border-radius: 6px; padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 4px; }
QMenu::item:selected { background: #d0ebff; color: #1971c2; }
QMenu::separator { background: #dee2e6; height: 1px; margin: 4px 6px; }

QToolTip {
    background: #ffffff; color: #212529;
    border: 1px solid #ced4da; padding: 4px 8px; border-radius: 4px;
}

QLabel[chip="true"] { background: #d0ebff; color: #1971c2; border-radius: 10px; padding: 2px 10px; margin: 2px; }
QLabel[h1="true"] { font-size: 20px; font-weight: 700; color: #212529; }
QLabel[h2="true"] { font-size: 15px; font-weight: 600; color: #212529; }
QLabel[muted="true"] { color: #495057; }
QLabel[hint="true"] { color: #868e96; }

QLabel#CoverLarge {
    background: #f8f9fa; border: 1px solid #dee2e6;
    border-radius: 8px; color: #adb5bd;
}

/* TagTree（左栏） */
QTreeWidget#TagTree {
    background: #f8f9fa; border: none; outline: 0; padding: 6px 4px;
}
QTreeWidget#TagTree::item {
    padding: 6px 8px; border-radius: 6px; margin: 1px 4px; color: #495057;
}
QTreeWidget#TagTree::item:hover { background: #e9ecef; color: #212529; }
QTreeWidget#TagTree::item:selected { background: #d0ebff; color: #1971c2; }
"""


def _assets_dir() -> "Path":
    """返回 ui/assets 目录的绝对路径。
    PyInstaller 单文件模式下 __file__ 解析到 _MEIPASS 临时目录，也能命中。"""
    from pathlib import Path
    return Path(__file__).resolve().parent / "assets"


def _qss_with_assets(qss: str) -> str:
    """把 QSS 中的资源占位符替换为绝对 URL。
    Qt QSS url() 在 Windows 下要求正斜杠路径。"""
    base = _assets_dir().as_posix()
    return (
        qss
        .replace("__ARROW_LIGHT__",    f"{base}/arrow-down-light.svg")
        .replace("__ARROW_DISABLED__", f"{base}/arrow-down-disabled.svg")
    )


def apply_theme(app, name: str = "light", font_size: int | None = None) -> None:
    """应用全局主题。task #34 起仅浅色单主题，``name`` 参数保留兼容（忽略）。

    ``font_size``：基础字号（task #41 T6），缺省 13；只替换全局 ``*`` 规则的
    字号，不影响 QSS 里其它相对尺寸。
    """
    qss = QSS_LIGHT
    if font_size is not None and font_size != 13:
        qss = qss.replace("font-size: 13px", f"font-size: {font_size}px", 1)
    app.setStyleSheet(_qss_with_assets(qss))
