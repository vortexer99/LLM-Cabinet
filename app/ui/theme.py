"""全局 QSS 主题（深色，扁平现代）。

色板：
  --bg-0   #1a1b1e  最深背景（窗口）
  --bg-1   #25262b  次深（面板、卡片）
  --bg-2   #2c2e33  控件背景
  --bg-3   #373a40  hover
  --bd     #373a40  边框
  --fg-0   #e9ecef  主文字
  --fg-1   #adb5bd  次文字
  --fg-2   #6c757d  弱文字
  --accent #4dabf7  主色（链接 / 选中）
  --accent-h #74c0fc
  --warn   #f5a623  星星
  --danger #fa5252
"""
from __future__ import annotations

QSS_DARK = """
/* ===== 全局 ===== */
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 13px;
    color: #e9ecef;
}
QMainWindow, QDialog, QWidget#CentralRoot {
    background: #1a1b1e;
}
QStatusBar {
    background: #1a1b1e;
    color: #adb5bd;
    border-top: 1px solid #2c2e33;
}
QStatusBar::item { border: none; }

/* ===== 工具栏 ===== */
QToolBar {
    background: #1a1b1e;
    border: none;
    spacing: 4px;
    padding: 6px 8px;
}
QToolBar QToolButton {
    background: transparent;
    color: #e9ecef;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 10px;
    margin: 0 2px;
}
QToolBar QToolButton:hover {
    background: #2c2e33;
    border-color: #373a40;
}
QToolBar QToolButton:pressed { background: #373a40; }
QToolBar::separator {
    background: #2c2e33;
    width: 1px;
    margin: 6px 6px;
}

/* ===== 面板 / 卡片 (用 objectName 区分) ===== */
QWidget#SidePanel, QWidget#CenterPanel, QWidget#DetailPanel {
    background: #1a1b1e;
}
QFrame#Card {
    background: #25262b;
    border: 1px solid #2c2e33;
    border-radius: 8px;
}

/* ===== 输入框 ===== */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {
    background: #25262b;
    border: 1px solid #373a40;
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: #4dabf7;
    selection-color: #1a1b1e;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {
    border: 1px solid #4dabf7;
}
QLineEdit#SearchBox {
    padding: 7px 10px 7px 28px;
    background: #25262b url(none) left center no-repeat;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    border: none;
    width: 22px;
}
QComboBox::down-arrow {
    image: url("__ARROW_DARK__");
    width: 10px;
    height: 6px;
    margin-right: 8px;
}
QComboBox::down-arrow:disabled {
    image: url("__ARROW_DISABLED__");
}
QComboBox:disabled {
    color: #6c757d;
    background: #1f2024;
    border: 1px solid #2c2e33;
}
QComboBox QAbstractItemView {
    background: #25262b;
    border: 1px solid #373a40;
    selection-background-color: #4dabf7;
    selection-color: #1a1b1e;
    outline: 0;
}

/* ===== 按钮 ===== */
QPushButton {
    background: #2c2e33;
    color: #e9ecef;
    border: 1px solid #373a40;
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 18px;
}
QPushButton:hover { background: #373a40; border-color: #495057; }
QPushButton:pressed { background: #25262b; }
QPushButton:disabled { color: #6c757d; background: #25262b; }

QPushButton[primary="true"] {
    background: #4dabf7;
    color: #0b1726;
    border: none;
    font-weight: 600;
}
QPushButton[primary="true"]:hover { background: #74c0fc; }
QPushButton[primary="true"]:pressed { background: #339af0; }

QPushButton[danger="true"]:hover {
    background: #fa5252;
    color: #fff;
    border-color: #fa5252;
}

QPushButton[flat="true"] {
    background: transparent;
    border: none;
    padding: 4px 8px;
}
QPushButton[flat="true"]:hover { background: #2c2e33; }

QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 8px;
    color: #e9ecef;
}
QToolButton:hover { background: #2c2e33; border-color: #373a40; }
QToolButton:checked { background: #373a40; border-color: #4dabf7; }

/* ===== 列表 ===== */
QListWidget, QListView {
    background: #1a1b1e;
    border: none;
    outline: 0;
}
QListWidget::item {
    padding: 8px 10px;
    border-radius: 6px;
    margin: 2px 4px;
    color: #e9ecef;
}
QListWidget::item:hover { background: #25262b; }
QListWidget::item:selected {
    background: #2b3a55;
    color: #ffffff;
}

/* 网格视图（卡片） */
QListView#ProjectGrid {
    background: #1a1b1e;
    border: none;
    padding: 8px;
}
QListView#ProjectGrid::item {
    background: #25262b;
    border: 1px solid #2c2e33;
    border-radius: 8px;
    padding: 0;
    margin: 6px;
}
QListView#ProjectGrid::item:hover { border-color: #4dabf7; }
QListView#ProjectGrid::item:selected {
    border-color: #4dabf7;
    background: #2b3a55;
}

/* ===== 表格 ===== */
QTableWidget, QTableView {
    background: #1a1b1e;
    alternate-background-color: #1f2024;
    border: 1px solid #2c2e33;
    border-radius: 6px;
    gridline-color: #2c2e33;
    selection-background-color: #2b3a55;
    selection-color: #ffffff;
    outline: 0;
}
QTableWidget::item, QTableView::item {
    padding: 6px 8px;
    border: none;
}
QHeaderView::section {
    background: #25262b;
    color: #adb5bd;
    border: none;
    border-right: 1px solid #2c2e33;
    border-bottom: 1px solid #2c2e33;
    padding: 6px 8px;
    font-weight: 600;
}
QHeaderView::section:last { border-right: none; }
QTableCornerButton::section { background: #25262b; border: none; }

/* ===== 滚动条 ===== */
QScrollBar:vertical {
    background: transparent; width: 10px; margin: 2px;
}
QScrollBar::handle:vertical {
    background: #373a40; min-height: 30px; border-radius: 5px;
}
QScrollBar::handle:vertical:hover { background: #495057; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QScrollBar:horizontal {
    background: transparent; height: 10px; margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #373a40; min-width: 30px; border-radius: 5px;
}
QScrollBar::handle:horizontal:hover { background: #495057; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }

/* ===== Splitter ===== */
QSplitter::handle { background: #1a1b1e; }
QSplitter::handle:horizontal { width: 1px; background: #2c2e33; }
QSplitter::handle:vertical   { height: 1px; background: #2c2e33; }
QSplitter::handle:hover { background: #4dabf7; }

/* ===== TextBrowser（详情 Markdown） ===== */
QTextBrowser {
    background: #25262b;
    border: 1px solid #2c2e33;
    border-radius: 8px;
    padding: 12px 14px;
}
QTextBrowser a { color: #4dabf7; }

/* ===== Slider ===== */
QSlider::groove:horizontal {
    background: #373a40; height: 4px; border-radius: 2px;
}
QSlider::sub-page:horizontal { background: #4dabf7; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #e9ecef; width: 12px; height: 12px;
    margin: -5px 0; border-radius: 6px;
}
QSlider::handle:horizontal:hover { background: #74c0fc; }

/* ===== Menu ===== */
QMenu {
    background: #25262b;
    border: 1px solid #373a40;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 18px;
    border-radius: 4px;
}
QMenu::item:selected { background: #2b3a55; }
QMenu::separator { background: #373a40; height: 1px; margin: 4px 6px; }

/* ===== ToolTip ===== */
QToolTip {
    background: #25262b;
    color: #e9ecef;
    border: 1px solid #373a40;
    padding: 4px 8px;
    border-radius: 4px;
}

/* ===== Tag tree（左栏） ===== */
QTreeWidget#TagTree {
    background: #18191c;
    border: none;
    outline: 0;
    padding: 6px 4px;
}
QTreeWidget#TagTree::item {
    padding: 6px 8px;
    border-radius: 6px;
    margin: 1px 4px;
    color: #adb5bd;
}
QTreeWidget#TagTree::item:hover { background: #25262b; color: #e9ecef; }
QTreeWidget#TagTree::item:selected {
    background: #2b3a55;
    color: #ffffff;
}

/* ===== Tag chip (用 QLabel + property) ===== */
QLabel[chip="true"] {
    background: #2b3a55;
    color: #74c0fc;
    border-radius: 10px;
    padding: 2px 10px;
    margin: 2px;
}

/* ===== Headline / 标题样式 ===== */
QLabel[h1="true"] { font-size: 20px; font-weight: 700; color: #e9ecef; }
QLabel[h2="true"] { font-size: 15px; font-weight: 600; color: #e9ecef; }
QLabel[muted="true"] { color: #adb5bd; }
QLabel[hint="true"] { color: #6c757d; }

/* ===== Cover placeholder ===== */
QLabel#CoverLarge {
    background: #25262b;
    border: 1px solid #2c2e33;
    border-radius: 8px;
    color: #6c757d;
}
"""


# =============================================================================
# Light theme
# 色板:
#   --bg-0  #ffffff   --bg-1  #f8f9fa   --bg-2  #e9ecef   --bg-3  #dee2e6
#   --fg-0  #212529   --fg-1  #495057   --fg-2  #868e96
#   --accent #228be6  --accent-h #1c7ed6
#   --warn   #f59f00  --danger #fa5252
# =============================================================================
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

/* 输入 */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {
    background: #ffffff; border: 1px solid #ced4da;
    border-radius: 6px; padding: 6px 8px;
    selection-background-color: #228be6; selection-color: #ffffff;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus { border: 1px solid #228be6; }
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

QListView#ProjectGrid { background: #ffffff; border: none; padding: 8px; }

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


THEMES = {
    "dark": QSS_DARK,
    "light": QSS_LIGHT,
}


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
        .replace("__ARROW_DARK__",     f"{base}/arrow-down.svg")
        .replace("__ARROW_LIGHT__",    f"{base}/arrow-down-light.svg")
        .replace("__ARROW_DISABLED__", f"{base}/arrow-down-disabled.svg")
    )


def apply_theme(app, name: str) -> None:
    """切换全局主题。name 接受: dark / light。其它值按 dark 处理。"""
    qss = THEMES.get(name, QSS_DARK)
    app.setStyleSheet(_qss_with_assets(qss))


# 兼容旧引用
QSS = QSS_DARK
