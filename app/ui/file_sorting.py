"""文件列表的自然名称排序，展示图标不参与比较。"""
from __future__ import annotations

import re

from PySide6.QtCore import QCollator, Qt
from PySide6.QtWidgets import QTreeWidgetItem


RAW_NAME_ROLE = Qt.UserRole + 3


def natural_name_key(text: str) -> tuple:
    """按数字段的数值排序；同值时按完整名称确定顺序。"""
    parts = tuple(
        (1, int(part)) if part.isdecimal() else (0, part.casefold())
        for part in re.split(r"(\d+)", text)
    )
    return parts, text.casefold(), text


class FileTreeItem(QTreeWidgetItem):
    """平铺视图按文件名自然排序，其他列沿用 Qt 的比较规则。"""

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        tree = self.treeWidget()
        if tree is not None and tree.sortColumn() == 0:
            name = self.data(0, RAW_NAME_ROLE)
            other_name = other.data(0, RAW_NAME_ROLE)
            return natural_name_key(name if name is not None else self.text(0)) < natural_name_key(
                other_name if other_name is not None else other.text(0)
            )
        # 显式比较展示文本，避免 PySide6 调用基类虚方法时再次进入本方法。
        column = tree.sortColumn() if tree is not None else 0
        return QCollator().compare(self.text(column), other.text(column)) < 0
