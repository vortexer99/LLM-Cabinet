"""复用左侧标签树的条目样式，筛选项目包含的文件。"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (
    QAbstractItemView, QHeaderView, QMenu, QSizePolicy, QTreeWidget, QTreeWidgetItem,
)

from ..search import AndNode, OrNode, SearchNode, TermNode


class FileTypeFilter(QTreeWidget):
    """点击条目切换筛选，多选无需 Ctrl；高级组合放在右键菜单中。"""

    changed = Signal()
    LABELS = {"video": "有视频", "archive": "有压缩包", "external": "有外部链接文件"}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # 与 TagTree 使用同一份 QSS，间距、高亮、悬停效果均保持一致。
        self.setObjectName("TagTree")
        self.setHeaderHidden(True)
        self.header().setSectionResizeMode(QHeaderView.Stretch)
        self.setIndentation(12)
        self.setRootIsDecorated(True)
        self.setSelectionMode(QAbstractItemView.MultiSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._match_mode = "any"
        self.group_item = QTreeWidgetItem(["文件筛选"])
        self.group_item.setFlags(self.group_item.flags() & ~Qt.ItemIsSelectable)
        font = self.group_item.font(0)
        font.setBold(True)
        self.group_item.setFont(0, font)
        self.group_item.setForeground(0, self.palette().mid())
        self.addTopLevelItem(self.group_item)
        self.items: dict[str, QTreeWidgetItem] = {}
        icons = {"video": "🎬", "archive": "🗜", "external": "🔗"}
        for kind, label in self.LABELS.items():
            item = QTreeWidgetItem([f"{icons[kind]}  {label}"])
            item.setData(0, Qt.UserRole, kind)
            item.setToolTip(0, f"{label}：点击选中，再次点击取消；可多选。\n右键切换任一／全部条件，Esc 清除。")
            self.group_item.addChild(item)
            self.items[kind] = item
        self.group_item.setExpanded(True)
        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.itemExpanded.connect(self._sync_height)
        self.itemCollapsed.connect(self._sync_height)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self._sync_height()

    @property
    def selected_kinds(self) -> set[str]:
        return {kind for kind, item in self.items.items() if item.isSelected()}

    @property
    def match_mode(self) -> str:
        return self._match_mode

    def set_selected_kinds(self, kinds: set[str]) -> None:
        """一次更新选择并通知，避免清除时逐条触发项目刷新。"""
        before = self.selected_kinds
        blocked = self.blockSignals(True)
        try:
            for kind, item in self.items.items():
                item.setSelected(kind in kinds)
        finally:
            self.blockSignals(blocked)
        if before != self.selected_kinds:
            self._on_selection_changed()

    def set_match_mode(self, mode: str) -> None:
        if mode not in {"any", "all"} or mode == self._match_mode:
            return
        self._match_mode = mode
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        suffix = ""
        if len(self.selected_kinds) > 1:
            suffix = " · 全部" if self._match_mode == "all" else " · 任一"
        self.group_item.setText(0, "文件筛选" + suffix)
        self.changed.emit()

    def clear(self) -> None:
        """只清除文件筛选，保留树节点、标签和关键词。"""
        self.set_selected_kinds(set())

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.clear()
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            item = self.currentItem()
            if item in self.items.values():
                item.setSelected(not item.isSelected())
                event.accept()
                return
        super().keyPressEvent(event)

    def _context_menu(self, pos) -> None:
        menu = QMenu(self)
        group = QActionGroup(menu)
        group.setExclusive(True)
        for mode, label in (("any", "满足任一条件"), ("all", "同时满足全部条件")):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self._match_mode == mode)
            action.setEnabled(len(self.selected_kinds) > 1)
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, value=mode: self.set_match_mode(value))
        menu.addSeparator()
        action = menu.addAction("清除文件筛选", self.clear)
        action.setEnabled(bool(self.selected_kinds))
        menu.exec(self.viewport().mapToGlobal(pos))

    def _sync_height(self, *_args) -> None:
        """仅占实际条目所需高度，主题字号变化或折叠时同步。"""
        if not hasattr(self, "items"):
            return
        visible = [self.group_item]
        if self.group_item.isExpanded():
            visible.extend(self.items.values())
        height = sum(max(1, self.sizeHintForIndex(self.indexFromItem(item)).height()) for item in visible)
        self.setFixedHeight(height + self.frameWidth() * 2 + 4)

    def event(self, event) -> bool:
        result = super().event(event)
        if event.type() in (QEvent.Polish, QEvent.FontChange, QEvent.StyleChange):
            self._sync_height()
        return result

    def search_node(self) -> SearchNode | None:
        selected = self.selected_kinds
        items = [TermNode("has", ":", kind) for kind in self.LABELS if kind in selected]
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        return AndNode(items) if self._match_mode == "all" else OrNode(items)

    def summary(self) -> str:
        names = [label for kind, label in self.LABELS.items() if kind in self.selected_kinds]
        return (" 且 " if self._match_mode == "all" else " 或 ").join(names)
