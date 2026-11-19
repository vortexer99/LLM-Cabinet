"""左侧栏的标签筛选树。

提供五类节点：
- 全部
- 未分类、隐藏项目
- 待审阅 LLM 建议
- 标签（按字母排序，括号显示项目数；支持 ``/`` 分隔的层级折叠 — task #06）
- 未使用的标签

发出 `filter_changed(filter_kind, value)` 信号：
  filter_kind: "all" | "favorite" | "hidden" | "untagged" | "review" | "tag" | "tag_prefix" | "not_tag" | "not_tag_prefix"
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
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem

from .dnd import PROJECT_IDS_MIME, decode_project_ids


# 标签层级分隔符（约定）
TAG_HIERARCHY_SEP = "/"

# 折叠状态持久化的 setting key
SETTING_COLLAPSED_PREFIXES = "tag_tree_collapsed_prefixes"


class TagTree(QTreeWidget):
    filter_changed = Signal(str, str)  # kind, value
    filter_combined = Signal(str, str, str)  # kind, value, all/any
    projects_dropped_on_tag = Signal(list, str)  # project_ids, tag
    # task #39：标签管理右键动作（action: rename/merge/delete, kind: tag/tag_prefix, value）
    tag_action_requested = Signal(str, str, str)

    KIND_ROLE = Qt.UserRole + 1
    VALUE_ROLE = Qt.UserRole + 2
    DISPLAY_ROLE = Qt.UserRole + 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TagTree")
        self.setHeaderHidden(True)
        self.setIndentation(12)
        # 显示顶层节点的展开/折叠三角形（用于"标签 / 未使用的标签"分组）
        self.setRootIsDecorated(True)
        self.setAnimated(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setDropIndicatorShown(True)
        # 含层级父节点后高度可能不一致，关掉 uniform
        self.setUniformRowHeights(False)
        self.setColumnCount(1)
        self.header().setSectionResizeMode(QHeaderView.Stretch)
        self.itemSelectionChanged.connect(self._on_selection)
        self.itemClicked.connect(self._on_item_clicked)
        self.itemExpanded.connect(self._on_item_toggle)
        self.itemCollapsed.connect(self._on_item_toggle)
        # task #39：标签节点右键菜单（重命名/合并/删除）
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        self._suspend_signal = False
        self._mouse_selecting = False
        self._click_allowed = True
        self._click_modifiers = Qt.NoModifier
        self._tag_states: dict[tuple[str, str], str] = {}
        self._tag_items: dict[tuple[str, str], QTreeWidgetItem] = {}
        self._filter_state: tuple[str, str] = ("all", "")
        self._active_tag_item: QTreeWidgetItem | None = None
        self._tag_state_icons = self._make_filter_icons()
        # 持久化标签前缀的折叠状态：{prefix: collapsed_bool}
        # 默认 False（展开）；只把"用户折叠过"的前缀写出来
        self._collapsed_prefixes: set[str] = set()
        self._setting_io: Optional[_SettingIO] = None  # 注入式，避免硬依赖 Repository
        self.saved_searches: list[dict[str, str]] = []

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
        favorites: int = 0,
        saved_searches: list[dict[str, str]] | None = None,
        hidden: int = 0,
    ) -> None:
        """重建整棵树，尽量保留当前选中项。"""
        cur_kind, cur_value = self.current_filter()
        tag_states = dict(self._tag_states)
        self._suspend_signal = True
        self._active_tag_item = None
        self._modifier_click_item = None
        self._tag_states = {}
        self._tag_items = {}
        self.clear()
        self.saved_searches = list(saved_searches or [])

        root_all = self._make_item("📚  全部项目", total, "all", "")
        self.addTopLevelItem(root_all)

        item_favorite = self._make_item("★  收藏项目", favorites, "favorite", "")
        self.addTopLevelItem(item_favorite)

        item_untagged = self._make_item("⊘  未分类", untagged, "untagged", "")
        self.addTopLevelItem(item_untagged)

        item_review = self._make_item("⚡  待审阅 LLM 建议", pending_review, "review", "")
        self.addTopLevelItem(item_review)

        item_mcp = self._make_item("🤖  未读MCP修改", mcp_modified, "mcp", "")
        self.addTopLevelItem(item_mcp)

        item_hidden = self._make_item("隐藏项目", hidden, "hidden", "")
        item_hidden.setToolTip(0, "仅显示隐藏项目；在项目右键菜单中取消隐藏。普通分类和搜索默认排除隐藏项目。")
        self.addTopLevelItem(item_hidden)

        if self.saved_searches:
            group = QTreeWidgetItem(["自定义筛选"])
            group.setFlags(group.flags() & ~Qt.ItemIsSelectable)
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            group.setForeground(0, self.palette().mid())
            self.addTopLevelItem(group)
            for saved in self.saved_searches:
                item = QTreeWidgetItem([f"☆  {saved['name']}"])
                item.setData(0, self.KIND_ROLE, "saved_search")
                item.setData(0, self.VALUE_ROLE, saved["name"])
                item.setToolTip(0, saved["query"])
                group.addChild(item)
            group.setExpanded(True)

        # 标签分组节点
        if tag_counts:
            used = [(n, c) for n, c in tag_counts if c > 0]
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

            # NOTE: 未使用的标签不再显示（2026-06-04）
            # 用户很少关心孤立标签，且占用侧边栏空间

        # 恢复选中
        target = root_all
        if cur_kind == "favorite":
            target = item_favorite
        elif cur_kind == "untagged":
            target = item_untagged
        elif cur_kind == "review":
            target = item_review
        elif cur_kind == "mcp":
            target = item_mcp
        elif cur_kind == "hidden":
            target = item_hidden
        elif cur_kind in {"tag", "not_tag"}:
            target = self._find_tag_item(cur_value) or root_all
        elif cur_kind in {"tag_prefix", "not_tag_prefix"}:
            target = self._find_prefix_item(cur_value) or root_all
        elif cur_kind == "saved_search":
            target = self._find_item_by_kind_value(cur_kind, cur_value) or root_all
        target.setSelected(True)
        self.setCurrentItem(target)
        self.sync_filter_state(cur_kind, cur_value, tag_states)
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
            return self._filter_state
        if not it.isSelected():
            # 键盘轮切到不筛选后仍让标签保有焦点，供下一次 Enter/空格继续轮切。
            return self._filter_state
        kind = it.data(0, self.KIND_ROLE) or "all"
        value = it.data(0, self.VALUE_ROLE) or ""
        if self._filter_state == ("not_" + kind, value):
            return self._filter_state
        return (kind, value)

    def sync_filter_state(
        self, kind: str, value: str, states: dict[tuple[str, str], str] | None = None,
    ) -> None:
        """从搜索表达式恢复状态；角色保留原标签，排除只改变展示。"""
        blocked = self.blockSignals(True)
        try:
            self._active_tag_item = None
            self._filter_state = (kind, value)
            projected = {} if states is None else states
            item = self._find_item_by_kind_value(kind.removeprefix("not_"), value)
            self.clearSelection()
            self.setCurrentItem(item)
            if item is not None:
                item.setSelected(True)
                if kind.removeprefix("not_") in {"tag", "tag_prefix"}:
                    excluded = kind.startswith("not_")
                    state = "exclude" if excluded else "include"
                    if states is None:
                        projected = {(kind.removeprefix("not_"), value): state}
                    self._active_tag_item = item
            self.set_tag_states(projected)
        finally:
            self.blockSignals(blocked)

    def set_tag_states(self, states: dict[tuple[str, str], str]) -> None:
        """为组合表达式同时显示各标签的包含/排除标记，不改变查询选择。"""
        if states == self._tag_states:
            return
        previous = self._tag_states
        self._tag_states = dict(states)
        for selection in previous.keys() | states.keys():
            if previous.get(selection) == states.get(selection):
                continue
            item = self._tag_items.get(selection)
            if item is None:
                continue
            state = states.get(selection, "all")
            item.setIcon(0, self._tag_state_icons[state])
            text = item.data(0, self.DISPLAY_ROLE)
            item.setText(0, text + ("  · 不包含" if state == "exclude" else ""))
            item.setToolTip(0, self._tag_tooltip(state))

    # ============================================================
    # helpers
    # ============================================================
    def _make_filter_icons(self) -> dict[str, QIcon]:
        """绘制真实 Qt 状态图标，避免依赖系统字体中的符号字形。"""
        icons: dict[str, QIcon] = {}
        for state, color in (("all", self.palette().mid().color()),
                             ("include", QColor("#16815e")),
                             ("exclude", QColor("#be3345"))):
            pixmap = QPixmap(18, 18)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(color, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            if state == "all":
                painter.drawEllipse(4, 4, 10, 10)
            elif state == "include":
                painter.drawLine(3, 9, 7, 13)
                painter.drawLine(7, 13, 15, 5)
            else:
                painter.drawLine(4, 9, 14, 9)
            painter.end()
            icons[state] = QIcon(pixmap)
        return icons

    @staticmethod
    def _tag_tooltip(state: str) -> str:
        current = {"all": "不筛选", "include": "包含", "exclude": "不包含"}[state]
        return (f"当前：{current}。点击或按 Enter / 空格：包含 → 不包含 → 不筛选。"
                "Shift+点击：AND（同时满足）；Ctrl+点击：OR（满足任一）。"
                "Enter / 空格也支持修饰键；具体组合关系见搜索框。")

    def _make_item(self, text: str, count: int, kind: str, value: str) -> QTreeWidgetItem:
        it = QTreeWidgetItem([f"{text}    {count}"])
        it.setData(0, self.KIND_ROLE, kind)
        it.setData(0, self.VALUE_ROLE, value)
        it.setData(0, self.DISPLAY_ROLE, it.text(0))
        if kind in {"tag", "tag_prefix"}:
            self._tag_items[(kind, value)] = it
            it.setIcon(0, self._tag_state_icons["all"])
            it.setToolTip(0, self._tag_tooltip("all"))
        return it

    def _find_tag_item(self, name: str) -> QTreeWidgetItem | None:
        return self._find_item_by_kind_value("tag", name)

    def _find_prefix_item(self, prefix: str) -> QTreeWidgetItem | None:
        return self._find_item_by_kind_value("tag_prefix", prefix)

    def _find_item_by_kind_value(
        self, kind: str, value: str,
    ) -> QTreeWidgetItem | None:
        if kind in {"tag", "tag_prefix"}:
            return self._tag_items.get((kind, value))
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
        if self._suspend_signal or self._mouse_selecting:
            return
        # 方向键与程序选择仍按原逻辑正向定位；轮切只来自明确激活。
        item = self.currentItem()
        if item is None:
            return
        kind = item.data(0, self.KIND_ROLE)
        value = item.data(0, self.VALUE_ROLE) or ""
        if kind:
            self.sync_filter_state(kind, value)
            self.filter_changed.emit(kind, value)

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._suspend_signal or not self._click_allowed:
            return
        self._activate_filter_item(item, self._click_modifiers)

    def _activate_filter_item(self, item: QTreeWidgetItem, modifiers=Qt.NoModifier) -> None:
        """标签激活按包含→不包含→不筛选轮切，其余节点维持单态。"""
        kind = item.data(0, self.KIND_ROLE)
        value = item.data(0, self.VALUE_ROLE) or ""
        if not kind:
            return
        if kind in {"tag", "tag_prefix"}:
            if modifiers & (Qt.ShiftModifier | Qt.ControlModifier):
                mode = "all" if modifiers & Qt.ShiftModifier else "any"
                self.filter_combined.emit(kind, value, mode)
                return
            if self._filter_state == (kind, value):
                kind = "not_" + kind
            elif self._filter_state == ("not_" + kind, value):
                kind, value = "all", ""
        self.sync_filter_state(kind, value)
        self.filter_changed.emit(kind, value)

    def mousePressEvent(self, event):  # noqa: N802
        # 点击标签时 selectionChanged 在 itemClicked 之前发生，只在激活时刷新一次。
        item = self.itemAt(event.position().toPoint())
        self._click_allowed = (
            event.button() == Qt.LeftButton and item is not None
            and event.position().x() >= self.visualItemRect(item).left()
        )
        self._click_modifiers = event.modifiers()
        if event.button() != Qt.LeftButton:
            event.accept()
            return
        if (self._click_allowed and item.data(0, self.KIND_ROLE) in {"tag", "tag_prefix"}
                and self._click_modifiers & (Qt.ShiftModifier | Qt.ControlModifier)):
            # 修饰激活由 release 一次发出；不让 Qt 的范围选择/切换选择先改动条件。
            self.setFocus(Qt.MouseFocusReason)
            self._modifier_click_item = item
            event.accept()
            return
        self._mouse_selecting = True
        try:
            super().mousePressEvent(event)
        finally:
            self._mouse_selecting = False

    def mouseReleaseEvent(self, event):  # noqa: N802
        item = getattr(self, "_modifier_click_item", None)
        self._modifier_click_item = None
        if item is not None and event.button() == Qt.LeftButton:
            if self.itemAt(event.position().toPoint()) is item:
                self._activate_filter_item(item, self._click_modifiers)
            event.accept()
            return
        # 单次鼠标激活只在 itemClicked 中处理，屏蔽 release 引起的额外选择通知。
        self._mouse_selecting = True
        try:
            super().mouseReleaseEvent(event)
        finally:
            self._mouse_selecting = False

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in {Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space}:
            item = self.currentItem()
            if item is not None and item.data(0, self.KIND_ROLE) in {"tag", "tag_prefix"}:
                if event.isAutoRepeat():
                    event.accept()
                    return
                kind = item.data(0, self.KIND_ROLE)
                value = item.data(0, self.VALUE_ROLE) or ""
                self._activate_filter_item(item, event.modifiers())
                if self._filter_state[0] in {"all", "query"}:
                    # 查询同步会选中全库；只恢复键盘焦点，不把标签重新加入查询。
                    target = self._find_item_by_kind_value(kind, value)
                    blocked = self.blockSignals(True)
                    try:
                        self.setCurrentItem(target)
                        self.clearSelection()
                    finally:
                        self.blockSignals(blocked)
                event.accept()
                return
        super().keyPressEvent(event)

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

    # ============================================================
    # 标签管理右键菜单（task #39）
    # ============================================================
    def _on_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        kind = item.data(0, self.KIND_ROLE) or ""
        value = item.data(0, self.VALUE_ROLE) or ""
        if kind not in ("tag", "tag_prefix") or not value:
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        act_rename = menu.addAction(f"✏️  重命名标签「{value}」…")
        act_merge = menu.addAction(f"🔀  把「{value}」合并到…")
        act_del = menu.addAction(f"🗑  删除标签「{value}」")
        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen is act_rename:
            self.tag_action_requested.emit("rename", kind, value)
        elif chosen is act_merge:
            self.tag_action_requested.emit("merge", kind, value)
        elif chosen is act_del:
            self.tag_action_requested.emit("delete", kind, value)

    # ============================================================
    # 项目拖放到标签（task #25 Phase C）
    # ============================================================
    def _drop_target_tag(self, pos) -> str:
        item = self.itemAt(pos)
        if item is None:
            return ""
        kind = item.data(0, self.KIND_ROLE) or ""
        value = item.data(0, self.VALUE_ROLE) or ""
        if kind == "tag":
            return value
        if kind == "tag_prefix":
            return value
        return ""

    def dragEnterEvent(self, ev):  # noqa: N802
        if ev.mimeData().hasFormat(PROJECT_IDS_MIME):
            ev.setDropAction(Qt.CopyAction)
            ev.accept()
            return
        super().dragEnterEvent(ev)

    def dragMoveEvent(self, ev):  # noqa: N802
        if not ev.mimeData().hasFormat(PROJECT_IDS_MIME):
            super().dragMoveEvent(ev)
            return
        try:
            pos = ev.position().toPoint()
        except AttributeError:
            pos = ev.pos()
        tag = self._drop_target_tag(pos)
        if tag:
            ev.setDropAction(Qt.CopyAction)
            ev.accept()
        else:
            ev.ignore()

    def dropEvent(self, ev):  # noqa: N802
        if not ev.mimeData().hasFormat(PROJECT_IDS_MIME):
            super().dropEvent(ev)
            return
        try:
            pos = ev.position().toPoint()
        except AttributeError:
            pos = ev.pos()
        tag = self._drop_target_tag(pos)
        project_ids = decode_project_ids(ev.mimeData())
        if not tag or not project_ids:
            ev.ignore()
            return
        ev.setDropAction(Qt.CopyAction)
        ev.accept()
        self.projects_dropped_on_tag.emit(project_ids, tag)


# =============================================================================
# 内部：注入式 setting 读写器
# =============================================================================
class _SettingIO:
    __slots__ = ("setter", "getter")

    def __init__(self, setter, getter):
        self.setter = setter
        self.getter = getter
