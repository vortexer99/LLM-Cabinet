"""搜索框行为：补全、历史/收藏菜单、保存搜索（task #35：从 main_window.py 拆分，方法体未改动）。

Mixin：搜索框行为：补全、历史/收藏菜单、保存搜索
"""
from __future__ import annotations

import json
import logging
import shutil
import warnings
from pathlib import Path

from PySide6.QtCore import (
    QEvent, QItemSelectionModel, QSize, Qt, QTimer,
)
from PySide6.QtGui import QAction, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyledItemDelegate,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models import FileItem, Project
from ..repository import Repository
from ..search import combine_and, field_term, parse_search
from ..search_history import (
    HISTORY_SETTING_KEY,
    SAVED_SEARCHES_SETTING_KEY,
    add_history,
    load_history,
    load_saved_searches,
    remove_history,
    remove_saved_search,
    upsert_saved_search,
)
from ..utils import (
    OperationCancelled,
    detect_kind,
    human_size as _human_size,
    move_to_trash,
    open_with_default_app,
    reveal_in_explorer,
)
from .cover_cache import get_cover
from .dialogs import ask_yes_no_cancel, confirm, error, info, warn
from .dnd import FilesTableDnD, ProjectViewDnD
from .export_dialog import ExportDialog
from .files_table_columns import (
    COLUMNS as FILES_COLUMNS,
    SETTING_KEY as FILES_COLUMNS_SETTING_KEY,
    column_by_key as files_column_by_key,
    dump_prefs as files_dump_prefs,
    load_prefs as files_load_prefs,
    resolve_pref as files_resolve_pref,
)
from .palette import current as _current_palette
from .preview import PreviewPanel
from .project_card import (
    CARD_W as _CARD_W,
    COVER_H as _COVER_H,
    PAD as _CARD_PAD,
    ProjectCardDelegate,
    ProjectModel,
)
from .project_dialog import ProjectDialog
from .search_completion import SearchBoxKeyFilter, SearchCompletionPopup, current_token
from .settings import SettingsDialog
from .workers import ExportSnapshotRepo, run_with_progress

logger = logging.getLogger("llm_cabinet.ui")


class SearchMixin:
    """搜索框行为：补全、历史/收藏菜单、保存搜索"""

    def _update_search_completion(self, _text: str = "") -> None:
        """根据当前输入计算补全候选（字段语法 / 标签值 / 历史收藏）。

        不做 hasFocus 判断（offscreen 自检/某些平台焦点时序不可靠）；
        弹出后由 FocusOut / Esc / 选中候选 / 外部显式 hide 关闭。
        """
        text = self.search_box.text()
        cursor = self.search_box.cursorPosition()
        _start, token = current_token(text, cursor)
        low = token.lower()

        # 1) tag: 语境 → 标签值候选
        if low.startswith("tag:"):
            prefix = token[4:]
            items: list[tuple[str, str, str]] = []
            for name, cnt in self.repo.tag_counts():
                if not prefix or prefix.lower() in name.lower():
                    items.append((f"#{name}    ({cnt})", f"tag:{name}", "token"))
                if len(items) >= 8:
                    break
            self._completion.show_candidates(self.search_box, [("标签", items)])
            return

        # 2) rating 字段语境 → 1~5 取值候选
        all_fields = self.repo.list_fields()
        for f in all_fields:
            if f.type != "rating":
                continue
            ident = f.key or f.name
            if low.startswith((ident + ":").lower()):
                items = [
                    (f"{ident}:{v}    {'★' * v}", f"{ident}:{v}", "token")
                    for v in range(1, 6)
                ]
                self._completion.show_candidates(
                    self.search_box, [(f"{f.name} 的取值", items)],
                )
                return

        # 3) 字段语法候选（按 token 前缀过滤）
        field_items: list[tuple[str, str, str]] = []
        if not token or "tag".startswith(low):
            field_items.append(("tag:    按标签筛选", "tag:", "token"))
        for f in all_fields:
            ident = f.key or f.name
            if not ident:
                continue
            if not token or ident.lower().startswith(low):
                field_items.append((f"{ident}:    按「{f.name}」筛选", f"{ident}:", "token"))
            if len(field_items) >= 8:
                break

        # 4) 历史 / 收藏（整句替换）
        hist_items: list[tuple[str, str, str]] = []
        whole = text.strip()
        if whole:
            saved = load_saved_searches(
                self.repo.get_setting(SAVED_SEARCHES_SETTING_KEY, "[]")
            )
            for item in saved:
                q, n = item["query"], item["name"]
                if whole.lower() in q.lower() or whole.lower() in n.lower():
                    hist_items.append((f"☆ {n}：{q}", q, "all"))
            history = load_history(self.repo.get_setting(HISTORY_SETTING_KEY, "[]"))
            for q in history:
                if q != whole and whole.lower() in q.lower():
                    hist_items.append((f"🕘 {q}", q, "all"))
            hist_items = hist_items[:6]

        self._completion.show_candidates(
            self.search_box,
            [("字段", field_items), ("收藏 / 最近搜索", hist_items)],
        )


    def _apply_completion(self, ins: str, mode: str) -> None:
        """应用补全：token 替换（字段/标签）或整句替换（历史/收藏）。"""
        if mode == "all":
            self.search_box.setText(ins)
            self._search_timer.start()
            return
        text = self.search_box.text()
        cursor = self.search_box.cursorPosition()
        start, _tok = current_token(text, cursor)
        self.search_box.setText(text[:start] + ins + text[cursor:])
        self.search_box.setCursorPosition(start + len(ins))
        # 字段补全（如 tag:）后立刻出下一级候选（标签值）
        self._update_search_completion()


    def _focus_search(self) -> None:
        """Ctrl+F：聚焦搜索框并全选（task #38 T3）。"""
        self.search_box.setFocus()
        self.search_box.selectAll()


    def _apply_search_query(self, query: str) -> None:
        """从历史/收藏菜单选择表达式后填入并触发搜索。"""
        self._completion.hide()  # 菜单选词不弹补全
        query = (query or "").strip()
        self.search_box.setText(query)
        self._search_timer.start()


    def _record_search_history(self, query: str) -> None:
        """保存成功执行的非空查询。语法错误路径不会调用到这里。"""
        query = (query or "").strip()
        if not query:
            return
        raw = self.repo.get_setting(HISTORY_SETTING_KEY, "[]")
        items = load_history(raw)
        if items and items[0] == query:
            return
        self.repo.set_setting(HISTORY_SETTING_KEY, add_history(raw, query))


    def _show_search_menu(self) -> None:
        """显示收藏表达式与最近搜索（仅由 ⌄ 按钮触发，task #38 T1）。"""
        menu = QMenu(self)
        saved = load_saved_searches(
            self.repo.get_setting(SAVED_SEARCHES_SETTING_KEY, "[]")
        )
        history = load_history(self.repo.get_setting(HISTORY_SETTING_KEY, "[]"))

        if saved:
            m_saved = menu.addMenu("收藏")
            for item in saved:
                name = item["name"]
                query = item["query"]
                act = m_saved.addAction(f"☆ {name}：{query}")
                act.triggered.connect(
                    lambda _checked=False, q=query: self._apply_search_query(q)
                )
            m_del_saved = menu.addMenu("删除收藏")
            for item in saved:
                name = item["name"]
                act = m_del_saved.addAction(f"✕ {name}")
                act.triggered.connect(
                    lambda _checked=False, n=name: self._delete_saved_search(n)
                )

        if history:
            if saved:
                menu.addSeparator()
            m_hist = menu.addMenu("最近搜索")
            for query in history:
                act = m_hist.addAction(query)
                act.triggered.connect(
                    lambda _checked=False, q=query: self._apply_search_query(q)
                )
            m_del_hist = menu.addMenu("删除历史")
            for query in history:
                label = query if len(query) <= 48 else query[:45] + "..."
                act = m_del_hist.addAction(f"✕ {label}")
                act.triggered.connect(
                    lambda _checked=False, q=query: self._delete_search_history(q)
                )

        if not saved and not history:
            act = menu.addAction("暂无搜索历史")
            act.setEnabled(False)

        anchor = self.btn_search_menu if hasattr(self, "btn_search_menu") else self.search_box
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))


    def _delete_search_history(self, query: str) -> None:
        raw = self.repo.get_setting(HISTORY_SETTING_KEY, "[]")
        self.repo.set_setting(HISTORY_SETTING_KEY, remove_history(raw, query))
        self.statusBar().showMessage("已删除一条搜索历史", 3000)


    def _delete_saved_search(self, name: str) -> None:
        raw = self.repo.get_setting(SAVED_SEARCHES_SETTING_KEY, "[]")
        self.repo.set_setting(SAVED_SEARCHES_SETTING_KEY, remove_saved_search(raw, name))
        self.statusBar().showMessage(f"已删除收藏「{name}」", 3000)


    def _save_current_search(self) -> None:
        query = self.search_box.text().strip()
        if not query:
            info(self, "收藏搜索", "请先输入搜索表达式。")
            return
        parsed = parse_search(query)
        if not parsed.ok:
            err = parsed.error
            msg = f"{err.message}（位置 {err.position + 1}）" if err else "语法错误"
            warn(self, "收藏搜索", f"当前表达式有语法错误：{msg}")
            return
        name, ok = QInputDialog.getText(
            self,
            "收藏搜索",
            "给这条搜索起个名字：",
            text=query[:24],
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            warn(self, "收藏搜索", "收藏名称不能为空。")
            return

        raw = self.repo.get_setting(SAVED_SEARCHES_SETTING_KEY, "[]")
        exists = any(item["name"] == name for item in load_saved_searches(raw))
        if exists:
            if not confirm(
                self,
                "覆盖收藏",
                f"已存在名为「{name}」的收藏，是否覆盖？",
                yes="覆盖",
            ):
                return
        new_raw, overwritten = upsert_saved_search(raw, name, query)
        self.repo.set_setting(SAVED_SEARCHES_SETTING_KEY, new_raw)
        action = "已覆盖收藏" if overwritten else "已收藏搜索"
        self.statusBar().showMessage(f"{action}「{name}」", 3000)
