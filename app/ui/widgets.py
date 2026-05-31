"""通用小部件：StarRating / TagChip / FlowLayout / DropZone。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QToolButton,
    QWidget,
)


class StarRating(QWidget):
    changed = Signal(int)

    def __init__(self, value: int = 0, parent=None):
        super().__init__(parent)
        self._value = value
        self._buttons: list[QToolButton] = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        for i in range(1, 6):
            b = QToolButton()
            b.setCheckable(True)
            b.setAutoRaise(True)
            b.setText("★")
            b.setStyleSheet(
                "QToolButton{font-size:18px;color:#495057;border:none;background:transparent;}"
                "QToolButton:checked{color:#f5a623;}"
                "QToolButton:hover{color:#fcc419;}"
            )
            b.clicked.connect(lambda _=False, v=i: self.set_value(v))
            self._buttons.append(b)
            lay.addWidget(b)
        lay.addStretch(1)
        self.set_value(value)

    def value(self) -> int:
        return self._value

    def set_value(self, v: int) -> None:
        v = max(0, min(5, int(v)))
        self._value = v
        for i, b in enumerate(self._buttons, 1):
            b.setChecked(i <= v)
        self.changed.emit(self._value)

    def mouseDoubleClickEvent(self, _ev):
        self.set_value(0)


class TagChip(QLabel):
    """标签 chip（圆角胶囊）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setProperty("chip", True)
        self.setAlignment(Qt.AlignCenter)


class FlowLayout(QLayout):
    """自动换行布局（用于标签流式排列）。"""

    def __init__(self, parent=None, margin: int = 0, spacing: int = 6):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = spacing
        self._items: list = []

    def __del__(self):
        while self.count():
            self.takeAt(0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        x = rect.x()
        y = rect.y()
        line_height = 0
        for it in self._items:
            sp = self._spacing
            next_x = x + it.sizeHint().width() + sp
            if next_x - sp > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + sp
                next_x = x + it.sizeHint().width() + sp
                line_height = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), it.sizeHint()))
            x = next_x
            line_height = max(line_height, it.sizeHint().height())
        return y + line_height - rect.y()


class DropZone(QLabel):
    """蓝色虚线放置区。默认隐藏；拖动文件进入主窗口时由 MainWindow 显示。

    - 鼠标进入时（active=True）使用更亮的高亮样式
    - 接受拖放后通过 `dropped(paths)` 信号通知
    """

    dropped = Signal(list)  # list[str]

    STYLE_NORMAL = (
        "QLabel#DropZone{"
        "  background: rgba(77,171,247,0.08);"
        "  color: #74c0fc;"
        "  border: 2px dashed #4dabf7;"
        "  border-radius: 10px;"
        "  padding: 14px;"
        "  font-size: 13px;"
        "}"
    )
    STYLE_ACTIVE = (
        "QLabel#DropZone{"
        "  background: rgba(77,171,247,0.22);"
        "  color: #ffffff;"
        "  border: 2px dashed #74c0fc;"
        "  border-radius: 10px;"
        "  padding: 14px;"
        "  font-size: 13px;"
        "  font-weight: 600;"
        "}"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAlignment(Qt.AlignCenter)
        self.setText("＋  拖放到此处以新建项目")
        self.setMinimumHeight(72)
        self.setAcceptDrops(True)
        self.setStyleSheet(self.STYLE_NORMAL)
        self.hide()

    def set_active(self, active: bool) -> None:
        self.setStyleSheet(self.STYLE_ACTIVE if active else self.STYLE_NORMAL)

    # ---- 拖放事件 ----
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            self.set_active(True)

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dragLeaveEvent(self, _ev):
        self.set_active(False)

    def dropEvent(self, ev):
        if not ev.mimeData().hasUrls():
            return
        paths: list[str] = []
        for url in ev.mimeData().urls():
            if url.isLocalFile():
                paths.append(url.toLocalFile())
        self.set_active(False)
        ev.acceptProposedAction()
        if paths:
            self.dropped.emit(paths)

