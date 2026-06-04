"""左侧栏的标签筛选树。

提供五类节点：
- 全部
- 未分类
- 待审阅 LLM 建议
- 标签（按字母排序，括号显示项目数；支持 ``/`` 分隔的层级折叠 — task #06）
- 未使用的标签

发出 `filter_changed(filter_kind, value)` 信号：
  filter_kind: "all" | "untagged" | "review" | "tag" | "tag_prefix"
  value: tag 名称（kind=="tag"）或前缀（kind=="tag_prefix"）

层级折叠（task #06）：
- 标签名约定 ``/`` 为层级分隔符（如 ``领域/科幻``）
- 仅做**单层折叠**：第一个 ``/`` 之前为前缀；不递归再切（避免 UI 过深、且实际用例不需要）
- 父节点点击 → 发 ``filter_changed("tag_prefix", "<prefix>")``，匹配 ``<prefix>`` 自身或
  ``<prefix>/...`` 任一标签的项目
- 折叠状态持久化到 ``settings.tag_tree_collapsed_prefixes`` （逗号分隔的前缀集合）
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem


# 标签层级分隔符（约定）
TAG_HIERARCHY_SEP = "/"

# 折叠状态持久化的 setting key
SETTING_COLLAPSED_PREFIXES = "tag_tree_collapsed_prefixes"


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
        # 含层级父节点后高度可能不一致，关掉 uniform
        self.setUniformRowHeights(False)
        self.setColumnCount(1)
        self.header().setSectionResizeMode(QHeaderView.Stretch)
        self.itemSelectionChanged.connect(self._on_selection)
        self.itemExpanded.connect(self._on_item_toggle)
        self.itemCollapsed.connect(self._on_item_toggle)

        self._suspend_signal = False
        # 持久化标签前缀的折叠状态：{prefix: collapsed_bool}
        # 默认 False（展开）；只把"用户折叠过"的前缀写出来
        self._collapsed_prefixes: set[str] = set()
        self._setting_io: Optional[_SettingIO] = None  # 注入式，避免硬依赖 Repository

    # ============================================================
    # 折叠状态持久化（轻量依赖注入，避免硬连 Repository）
    # ============================================================
    def attach_setting_io(self, setter, getter) -> None:
        """注入读写 setting 的回调。

        Args:
            setter: ``(key: str, value: str) -> None``
            getter: ``(key: str, default: str = "") -> str``
        """
        self._setting_io = _SettingIO(setter=setter, getter=getter)
        # 加载已持久化的折叠前缀
        raw = getter(SETTING_COLLAPSED_PREFIXES, "") or ""
        self._collapsed_prefixes = {
            s.strip() for s in raw.split(",") if s.strip()
        }

    def _save_collapsed_state(self) -> None:
        if self._setting_io is None:
            return
        self._setting_io.setter(
            SETTING_COLLAPSED_PREFIXES,
            ",".join(sorted(self._collapsed_prefixes)),
        )

    # ============================================================
    # populate
    # ============================================================
    def populate(
        self,
        tag_counts: list[tuple[str, int]],
        total: int,
        untagged: int,
        pending_review: int = 0,
        mcp_modified: int = 0,
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

        item_mcp = self._make_item("🤖  MCP 修改过", mcp_modified, "mcp", "")
        self.addTopLevelItem(item_mcp)

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
                self._populate_used_tags(grp, used)
                grp.setExpanded(True)

            if unused:
                grp2 = QTreeWidgetItem([f"未使用的标签    {len(unused)}"])
                grp2.setFlags(grp2.flags() & ~Qt.ItemIsSelectable)
                f2 = grp2.font(0)
                f2.setBold(True)
                grp2.setFont(0, f2)
                grp2.setForeground(0, self.palette().mid())
                self.addTopLevelItem(grp2)
                # 未使用区不做层级展开（清单功能：用户只想看"哪些标签没人用"），
                # 与"used"区分开避免视觉混乱
                for name, count in unused:
                    child = self._make_item(f"#{name}", count, "tag", name)
                    child.setForeground(0, self.palette().mid())
                    grp2.addChild(child)
                grp2.setExpanded(False)

        # 恢复选中
        target = root_all
        if cur_kind == "untagged":
            target = item_untagged
        elif cur_kind == "review":
            target = item_review
        elif cur_kind == "mcp":
            target = item_mcp
        elif cur_kind == "tag":
            target = self._find_tag_item(cur_value) or root_all
        elif cur_kind == "tag_prefix":
            target = self._find_prefix_item(cur_value) or root_all
        target.setSelected(True)
        self.setCurrentItem(target)
        self._suspend_signal = False

    def _populate_used_tags(
        self, parent: QTreeWidgetItem, used: list[tuple[str, int]],
    ) -> None:
        """把 used 标签按 ``/`` 单层分组放到 ``parent`` 下。

        - 含 ``/`` 的标签按第一段做前缀分组，前缀作为可点击节点
        - 不含 ``/`` 但与某前缀同名的标签，**与该前缀合并**（项目计数取并集对应的标签数 + 1）
        - 顶层无前缀的标签作为 parent 的直接子节点
        """
        # 按前缀分桶；prefix == "" 表示不带 /
        buckets: dict[str, list[tuple[str, int]]] = {}
        # 标签名→count，便于"前缀本身存在为标签"的合并
        by_name: dict[str, int] = dict(used)

        for name, count in used:
            if TAG_HIERARCHY_SEP in name:
                prefix = name.split(TAG_HIERARCHY_SEP, 1)[0]
                buckets.setdefault(prefix, []).append((name, count))
            else:
                # 留到下面处理；如果它同时是别人的前缀，会合并
                buckets.setdefault("", []).append((name, count))

        # 平铺：先放层级前缀，再放无前缀的散标签（按字母）
        # 但要小心"无前缀但同时是某前缀"的情况 → 不重复显示散标签
        prefixes_with_children = {p for p in buckets if p}
        flat_tags: list[tuple[str, int]] = []
        for name, count in buckets.get("", []):
            if name in prefixes_with_children:
                # 这个标签本身就是某个前缀（如 "领域"），不再做散标签显示
                continue
            flat_tags.append((name, count))

        # 1) 层级前缀节点
        for prefix in sorted(prefixes_with_children):
            children_tags = buckets[prefix]
            # 父节点项目计数 = 命中"prefix 自身"或"prefix/*"的 distinct 项目数。
            # 这里没有项目级数据，只能近似：count_sum = 子标签 count 之和（去重做不到，
            # 但考虑标签数量有限、用户体验为主，足够；命中后真正筛项目走 list_projects 不影响）
            sub_count = sum(c for _, c in children_tags)
            self_count = by_name.get(prefix, 0)
            total_count = sub_count + self_count

            prefix_item = self._make_item(
                f"📁  {prefix}", total_count, "tag_prefix", prefix,
            )
            parent.addChild(prefix_item)

            # 子节点 1: 前缀本身（如果它作为独立标签存在）
            if self_count > 0:
                self_item = self._make_item(
                    f"#{prefix}（自身）", self_count, "tag", prefix,
                )
                prefix_item.addChild(self_item)

            # 子节点 2: 各子标签（按字母）
            for name, count in sorted(children_tags):
                # 显示去掉前缀的剩余部分，更紧凑（"领域/科幻" → "#科幻"）
                short = name[len(prefix) + 1:]
                child = self._make_item(f"#{short}", count, "tag", name)
                prefix_item.addChild(child)

            # 折叠状态：按持久化恢复
            prefix_item.setExpanded(prefix not in self._collapsed_prefixes)

        # 2) 无前缀的散标签
        for name, count in sorted(flat_tags):
            child = self._make_item(f"#{name}", count, "tag", name)
            parent.addChild(child)

    # ============================================================
    # public
    # ============================================================
    def current_filter(self) -> tuple[str, str]:
        it = self.currentItem()
        if it is None:
            return ("all", "")
        kind = it.data(0, self.KIND_ROLE) or "all"
        value = it.data(0, self.VALUE_ROLE) or ""
        return (kind, value)

    # ============================================================
    # helpers
    # ============================================================
    def _make_item(self, text: str, count: int, kind: str, value: str) -> QTreeWidgetItem:
        it = QTreeWidgetItem([f"{text}    {count}"])
        it.setData(0, self.KIND_ROLE, kind)
        it.setData(0, self.VALUE_ROLE, value)
        return it

    def _find_tag_item(self, name: str) -> QTreeWidgetItem | None:
        return self._find_item_by_kind_value("tag", name)

    def _find_prefix_item(self, prefix: str) -> QTreeWidgetItem | None:
        return self._find_item_by_kind_value("tag_prefix", prefix)

    def _find_item_by_kind_value(
        self, kind: str, value: str,
    ) -> QTreeWidgetItem | None:
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            found = self._dfs_find(top, kind, value)
            if found is not None:
                return found
        return None

    def _dfs_find(
        self, node: QTreeWidgetItem, kind: str, value: str,
    ) -> QTreeWidgetItem | None:
        if (
            node.data(0, self.KIND_ROLE) == kind
            and node.data(0, self.VALUE_ROLE) == value
        ):
            return node
        for j in range(node.childCount()):
            r = self._dfs_find(node.child(j), kind, value)
            if r is not None:
                return r
        return None

    def _on_selection(self) -> None:
        if self._suspend_signal:
            return
        kind, value = self.current_filter()
        if kind:
            self.filter_changed.emit(kind, value)

    def _on_item_toggle(self, item: QTreeWidgetItem) -> None:
        """记住用户对"前缀节点"的折叠偏好。"""
        if self._suspend_signal:
            return
        kind = item.data(0, self.KIND_ROLE)
        if kind != "tag_prefix":
            return
        prefix = item.data(0, self.VALUE_ROLE) or ""
        if not prefix:
            return
        if item.isExpanded():
            self._collapsed_prefixes.discard(prefix)
        else:
            self._collapsed_prefixes.add(prefix)
        self._save_collapsed_state()


# =============================================================================
# 内部：注入式 setting 读写器
# =============================================================================
class _SettingIO:
    __slots__ = ("setter", "getter")

    def __init__(self, setter, getter):
        self.setter = setter
        self.getter = getter
