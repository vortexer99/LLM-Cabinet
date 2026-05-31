"""左侧栏的标签筛选树。

提供三类节点：
- 全部
- 未分类
- 标签（按字母排序，括号显示项目数）

发出 `filter_changed(filter_kind, value)` 信号：
  filter_kind: "all" | "untagged" | "tag"
  value: tag 名称（仅 kind=="tag" 时有意义）
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem


class TagTree(QTreeWidget):
    filter_changed = Signal(str, str)  # kind, value

    KIND_ROLE = Qt.UserRole + 1
    VALUE_ROLE = Qt.UserRole + 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TagTree")
        self.setHeaderHidden(True)
        self.setIndentation(12)
        # 显示顶层节点的展开/折叠三角形（用于"标签 / 未使用的标签"分组）
        self.setRootIsDecorated(True)
        self.setAnimated(True)
        self.setUniformRowHeights(True)
        self.setColumnCount(1)
        self.header().setSectionResizeMode(QHeaderView.Stretch)
        self.itemSelectionChanged.connect(self._on_selection)

        self._suspend_signal = False

    def populate(
        self,
        tag_counts: list[tuple[str, int]],
        total: int,
        untagged: int,
        pending_review: int = 0,
    ) -> None:
        """重建整棵树，尽量保留当前选中项。"""
        cur_kind, cur_value = self.current_filter()
        self._suspend_signal = True
        self.clear()

        root_all = self._make_item("📚  全部项目", total, "all", "")
        self.addTopLevelItem(root_all)

        item_untagged = self._make_item("⊘  未分类", untagged, "untagged", "")
        self.addTopLevelItem(item_untagged)

        item_review = self._make_item("⚡  待审阅 LLM 建议", pending_review, "review", "")
        self.addTopLevelItem(item_review)

        # 标签分组节点
        if tag_counts:
            used = [(n, c) for n, c in tag_counts if c > 0]
            unused = [(n, c) for n, c in tag_counts if c <= 0]

            if used:
                grp = QTreeWidgetItem([f"标签    {len(used)}"])
                grp.setFlags(grp.flags() & ~Qt.ItemIsSelectable)
                f = grp.font(0)
                f.setBold(True)
                grp.setFont(0, f)
                grp.setForeground(0, self.palette().mid())
                self.addTopLevelItem(grp)
                for name, count in used:
                    child = self._make_item(f"#{name}", count, "tag", name)
                    grp.addChild(child)
                grp.setExpanded(True)

            if unused:
                grp2 = QTreeWidgetItem([f"未使用的标签    {len(unused)}"])
                grp2.setFlags(grp2.flags() & ~Qt.ItemIsSelectable)
                f2 = grp2.font(0)
                f2.setBold(True)
                grp2.setFont(0, f2)
                grp2.setForeground(0, self.palette().mid())
                self.addTopLevelItem(grp2)
                for name, count in unused:
                    child = self._make_item(f"#{name}", count, "tag", name)
                    # 灰一点，提示该标签当前没有任何项目
                    child.setForeground(0, self.palette().mid())
                    grp2.addChild(child)
                grp2.setExpanded(False)

        # 恢复选中
        target = root_all
        if cur_kind == "untagged":
            target = item_untagged
        elif cur_kind == "review":
            target = item_review
        elif cur_kind == "tag":
            target = self._find_tag_item(cur_value) or root_all
        target.setSelected(True)
        self.setCurrentItem(target)
        self._suspend_signal = False

    def current_filter(self) -> tuple[str, str]:
        it = self.currentItem()
        if it is None:
            return ("all", "")
        kind = it.data(0, self.KIND_ROLE) or "all"
        value = it.data(0, self.VALUE_ROLE) or ""
        return (kind, value)

    # ----- helpers -----
    def _make_item(self, text: str, count: int, kind: str, value: str) -> QTreeWidgetItem:
        it = QTreeWidgetItem([f"{text}    {count}"])
        it.setData(0, self.KIND_ROLE, kind)
        it.setData(0, self.VALUE_ROLE, value)
        return it

    def _find_tag_item(self, name: str) -> QTreeWidgetItem | None:
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            for j in range(top.childCount()):
                c = top.child(j)
                if c.data(0, self.KIND_ROLE) == "tag" and c.data(0, self.VALUE_ROLE) == name:
                    return c
        return None

    def _on_selection(self) -> None:
        if self._suspend_signal:
            return
        kind, value = self.current_filter()
        if kind:
            self.filter_changed.emit(kind, value)
