"""搜索框即时补全（task #38 T2）。

自绘轻量下拉（``Qt.Popup`` 的 QListWidget），挂在搜索框下方；候选分三区：
- 字段语法（``tag:`` / 字段 key 或显示名 + ``:``）
- 标签值（处于 ``tag:`` 语境时，列库中真实标签；rating 类型字段列 1~5）
- 收藏 / 最近搜索（与当前输入前缀匹配时）

键盘：↑↓ 移动、Tab/Enter 补全、Esc 关闭；鼠标可点。
补全只做"提示"：选中才改文本；与 200ms 防抖搜索并存，互不干扰。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QAbstractItemView, QListWidget, QListWidgetItem, QWidget

# 候选条目在 UserRole 里存 (insert_text, mode)：
#   mode == "token"：替换当前 token（字段语法 / 标签值）
#   mode == "all"：整句替换（历史 / 收藏）
_MODE_ROLE = Qt.UserRole + 1


class SearchCompletionPopup(QListWidget):
    """搜索框补全下拉。"""

    #: (insert_text, mode)；mode 见 _MODE_ROLE 说明
    item_chosen = Signal(str, str)

    def __init__(self, parent=None):
        # 主题样式可能在 QWidget 构造过程中调用 eventFilter，先准备它读取的状态。
        self._shown = False
        self._anchor: QWidget | None = None
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setMouseTracking(True)
        self.itemClicked.connect(self._on_clicked)
        # Qt.Popup 的原生自动关闭在不同平台上有差异，显式监听窗口内点击和焦点。
        QApplication.instance().installEventFilter(self)

    # ---------------------------------------------------------- 内容
    def show_candidates(
        self,
        anchor,
        sections: list[tuple[str, list[tuple[str, str, str]]]],
    ) -> None:
        """填充并显示。

        Args:
            anchor: 搜索框（定位 + 宽度参考）
            sections: ``[(区名, [(显示文本, 插入文本, mode)], ...)]``；
                全空的区自动跳过；没有任何候选时隐藏。
        """
        self._anchor = anchor
        self.clear()
        first: QListWidgetItem | None = None
        for sec_name, items in sections:
            if not items:
                continue
            header = QListWidgetItem(sec_name)
            header.setFlags(Qt.NoItemFlags)
            header.setForeground(self.palette().mid())
            self.addItem(header)
            for disp, ins, mode in items:
                it = QListWidgetItem(disp)
                it.setData(Qt.UserRole, ins)
                it.setData(_MODE_ROLE, mode)
                self.addItem(it)
                if first is None:
                    first = it
        if first is None:
            self.hide()
            return

        row_h = max(20, self.sizeHintForRow(0))
        visible_rows = min(self.count(), 10)
        w = max(anchor.width(), 260)
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        self.setGeometry(pos.x(), pos.y(), w, row_h * visible_rows + 6)
        self.setCurrentItem(first)
        self._shown = True
        if not self.isVisible():
            self.show()

    def hide(self) -> None:
        self._shown = False
        super().hide()

    def hideEvent(self, event) -> None:  # noqa: N802
        """原生弹窗关闭不一定经过 Python hide，仍须同步候选状态。"""
        self._shown = False
        super().hideEvent(event)

    def has_candidates(self) -> bool:
        return self._shown and self.count() > 0

    def _owns_focus(self, widget: QWidget | None) -> bool:
        return widget is not None and (
            widget is self or self.isAncestorOf(widget)
            or widget is self._anchor
            or (self._anchor is not None and self._anchor.isAncestorOf(widget))
        )

    def _dismiss_if_focus_outside(self) -> None:
        """等待焦点落定，避免搜索框转入补全窗口时抢先关闭候选。"""
        if not self.has_candidates():
            return
        focused = QApplication.focusWidget()
        if self._owns_focus(focused):
            return
        self.hide()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if not self.has_candidates():
            return False
        kind = event.type()
        if kind == QEvent.MouseButtonPress:
            point = event.globalPosition().toPoint()
            over_popup = self.rect().contains(self.mapFromGlobal(point))
            over_anchor = self._anchor is not None and self._anchor.rect().contains(
                self._anchor.mapFromGlobal(point)
            )
            if not over_popup and not over_anchor:
                # 不消费点击，让本次侧栏/按钮操作继续执行。
                self.hide()
        elif kind == QEvent.FocusIn and isinstance(obj, QWidget):
            if not self._owns_focus(obj):
                self.hide()
        elif kind == QEvent.FocusOut and obj is self._anchor:
            if event.reason() != Qt.PopupFocusReason:
                QTimer.singleShot(0, self._dismiss_if_focus_outside)
        elif kind == QEvent.ApplicationDeactivate:
            self.hide()
        return False

    # ---------------------------------------------------------- 键盘
    def handle_key(self, key: int) -> bool:
        """处理搜索框转发来的按键。返回 True = 已消费。"""
        if not self.has_candidates():
            return False
        cur = self.currentRow()
        if key == Qt.Key_Down:
            self._move(1)
            return True
        if key == Qt.Key_Up:
            self._move(-1)
            return True
        if key in (Qt.Key_Tab, Qt.Key_Return, Qt.Key_Enter):
            it = self.currentItem()
            if it is not None:
                self._choose(it)
            return True
        if key == Qt.Key_Escape:
            self.hide()
            return True
        return False

    def _move(self, delta: int) -> None:
        """在行间移动，跳过区标题行。"""
        n = self.count()
        if n == 0:
            return
        row = self.currentRow()
        for _ in range(n):
            row = (row + delta) % n
            it = self.item(row)
            if it is not None and it.flags() & Qt.ItemIsSelectable:
                self.setCurrentRow(row)
                return

    # ---------------------------------------------------------- 选中
    def _on_clicked(self, item: QListWidgetItem) -> None:
        self._choose(item)

    def _choose(self, item: QListWidgetItem) -> None:
        ins = item.data(Qt.UserRole)
        mode = item.data(_MODE_ROLE) or "token"
        if ins:
            self.item_chosen.emit(ins, mode)
        self.hide()


def current_token(text: str, cursor: int) -> tuple[int, str]:
    """取光标所在的 token（被空白 / 括号分隔）。返回 ``(起点下标, token)``。"""
    cursor = max(0, min(cursor, len(text)))
    start = cursor
    while start > 0 and not text[start - 1].isspace() and text[start - 1] not in "()":
        start -= 1
    return start, text[start:cursor]


class SearchBoxKeyFilter(QObject):
    """搜索框按键过滤器（task #41 T4：从主窗口全局 eventFilter 迁出）。

    只挂在搜索框上：
    - 补全可见时 ↑↓/Tab/Enter/Esc 先给补全消费
    - 补全不可见时 Esc 清空文本
    """

    def __init__(self, popup: SearchCompletionPopup, line_edit, parent=None):
        super().__init__(parent)
        self._popup = popup
        self._le = line_edit

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if ev.type() == QEvent.KeyPress:
            if self._popup.has_candidates() and self._popup.handle_key(ev.key()):
                return True
            if ev.key() == Qt.Key_Escape and self._le.text():
                self._le.clear()
                return True
        return False
