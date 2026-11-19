"""项目简介下方的可点击标签，按可用宽度换行并限制占用高度。"""
from __future__ import annotations

from html import escape

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QResizeEvent
from PySide6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QWidget

from .widgets import FlowLayout, TagChip


class ProjectTagChip(TagChip):
    """保留标签完整值，省略显示不影响鼠标或键盘筛选。"""

    clicked = Signal(str)

    def __init__(self, tag: str, parent: QWidget | None = None) -> None:
        super().__init__(tag, parent)
        self.full_text = tag
        self.setTextFormat(Qt.PlainText)
        self.setProperty("tag", tag)
        self.setAccessibleName(tag)
        self.setAccessibleDescription("点击或按回车、空格筛选同标签项目")
        self.setToolTip(f"<qt>点击筛选标签：{escape(tag)}</qt>")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        # 焦点轮廓叠加到全局 TagChip 样式，保持现有胶囊外观。
        self.setStyleSheet("QLabel:focus { border: 1px dotted palette(highlight); }")

    def sizeHint(self) -> QSize:  # noqa: N802
        size = super().sizeHint()
        size.setWidth(min(self.maximumWidth(), self.fontMetrics().horizontalAdvance(self.full_text) + 30))
        return size

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, self.sizeHint().height())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.ElideRight, max(0, self.width() - 28)))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.full_text)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            if not event.isAutoRepeat():
                self.clicked.emit(self.full_text)
            event.accept()
            return
        super().keyPressEvent(event)


class ProjectTagsWidget(QScrollArea):
    """有限高度的标签流；无标签隐藏，专注预览期间暂停显示。"""

    tag_clicked = Signal(str)
    MAX_HEIGHT = 108

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tags: list[str] = []
        self.chips: list[ProjectTagChip] = []
        self._suspended = False
        self._laying_out = False
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        self._content = QWidget()
        self._content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._flow = FlowLayout(self._content, spacing=4)
        self.setWidget(self._content)
        # 标签直接铺在简介下，避免滚动区域默认填充一块灰底。
        self._content.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)
        self.viewport().installEventFilter(self)
        self.hide()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, 0)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, self.height())

    def set_tags(self, tags: list[str]) -> None:
        """只在完整标签列表变化时重建，保留重复刷新时的焦点和滚动。"""
        if self.tags != tags:
            self.tags = list(tags)
            while self._flow.count():
                item = self._flow.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()
            self.chips = []
            for tag in self.tags:
                chip = ProjectTagChip(tag, self._content)
                chip.clicked.connect(self.tag_clicked)
                self.chips.append(chip)
                self._flow.addWidget(chip)
                # 在可见面板里新建的控件尚未自动显示，先显示再计算行高。
                chip.show()
            self.verticalScrollBar().setValue(0)
            self._relayout()
        self.setVisible(bool(self.tags) and not self._suspended)

    def set_suspended(self, suspended: bool) -> None:
        """退出专注时以最新标签决定可见性，不复活过时的标签区。"""
        self._suspended = suspended
        self.setVisible(bool(self.tags) and not suspended)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.viewport() and event.type() == QEvent.Resize:
            self._relayout()
        return super().eventFilter(watched, event)

    def _relayout(self) -> None:
        """宽度由视口决定，长标签不能撑宽右栏，过多标签允许纵向滚动。"""
        if self._laying_out:
            return
        self._laying_out = True
        try:
            # 滚动条出现会再次缩窄视口，第二次计算使用最终可用宽度。
            for _ in range(2):
                width = max(1, self.viewport().width())
                for chip in self.chips:
                    chip.setMaximumWidth(width)
                self._flow.invalidate()
                height = self._flow.heightForWidth(width)
                self.setFixedHeight(min(height, self.MAX_HEIGHT))
                self._content.resize(width, height)
                self._flow.setGeometry(self._content.rect())
        finally:
            self._laying_out = False
