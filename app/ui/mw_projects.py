"""项目列表：刷新/筛选/选择、项目操作、导出、LLM 入口（task #35：从 main_window.py 拆分，方法体未改动）。

Mixin：项目列表：刷新/筛选/选择、项目操作、导出、LLM 入口
"""
from __future__ import annotations

import json
import logging
import random
import shutil
import warnings
from collections.abc import Iterator
from pathlib import Path

from PySide6.QtCore import (
    QEvent, QItemSelectionModel, QModelIndex, QSize, Qt, QTimer,
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
from ..project_sizes import ProjectSizeSnapshot, files_fingerprint, measure_project_sizes
from ..repository import Repository
from ..search import parse_search
from ..search_filters import sidebar_node
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
from .project_field_filter import populate_project_field_filter_menu
from .search_completion import SearchBoxKeyFilter, SearchCompletionPopup, current_token
from .settings import SettingsDialog
from .workers import ExportSnapshotRepo, run_with_progress
from .web_links import open_web_url

logger = logging.getLogger("llm_cabinet.ui")


class ProjectsMixin:
    """项目列表：刷新/筛选/选择、项目操作、导出、LLM 入口"""

    DEFAULT_COL_WIDTHS = {
        "title": 240, "author": 140, "date": 110,
        "rating": 90, "source_url": 200, "description": 260,
        "tags": 200, "__files__": 70, "__updated__": 150, "__size__": 190,
    }

    def _on_project_header_clicked(self, column: int) -> None:
        """各列表头切换升降序；切换到另一列时从升序开始。"""
        key = self.proj_model.column_key(column)
        if not key:
            return
        order = (Qt.DescendingOrder
                 if self.proj_model.sort_key == key and self.proj_model.sort_order == Qt.AscendingOrder
                 else Qt.AscendingOrder)
        selection = self.proj_view.selectionModel()
        blocked = selection.blockSignals(True)
        try:
            self.proj_model.sort(column, order)
        finally:
            selection.blockSignals(blocked)
        self._sync_project_sort_indicator()

    def _sync_project_sort_indicator(self) -> None:
        """字段调整顺序后按稳定键定位排序箭头，字段隐藏／删除后取消箭头。"""
        header = self.proj_table.horizontalHeader()
        column = next((c for c in range(self.proj_model.columnCount())
                       if self.proj_model.column_key(c) == self.proj_model.sort_key), None)
        if column is not None:
            header.setSortIndicator(column, self.proj_model.sort_order)
        header.setSortIndicatorShown(column is not None)

    def _update_cached_project_sizes(self, project_ids: list[int]) -> None:
        """只同步缓存列，不重建文件树或中断正在播放的预览。"""
        sizes = self.repo.project_sizes(project_ids)
        selection = self.proj_view.selectionModel()
        blocked = selection.blockSignals(True)
        try:
            self.proj_model.update_project_sizes({pid: sizes.get(pid) for pid in project_ids})
        finally:
            selection.blockSignals(blocked)

    def action_update_project_sizes(self, project_ids: list[int] | None = None) -> None:
        """主线程快照登记路径，后台只 stat，完整完成后主线程保存缓存。"""
        ids = list(dict.fromkeys(project_ids if project_ids is not None else self._selected_project_ids()))
        if not ids:
            return
        files = self.repo.project_size_files(ids)
        snapshots = [ProjectSizeSnapshot(pid, files_fingerprint(items),
                                         tuple(self._resolve(item) for item in items))
                     for pid, items in files.items()]
        self.statusBar().showMessage(f"正在更新 {len(snapshots)} 个项目的体积…")

        def work(progress_cb, is_cancelled):
            return measure_project_sizes(snapshots, progress_cb, is_cancelled)

        def done(results) -> None:
            try:
                self.repo.save_project_sizes({result.project_id: result.size for result in results})
                self._update_cached_project_sizes(ids)
            except Exception:
                logger.exception("保存项目体积失败")
                self.statusBar().showMessage("项目体积保存失败", 5000)
                warn(self, "体积保存失败", "统计已完成，但缓存未能保存，请重试。")
                return
            missing = sum(result.size.missing for result in results)
            message = f"已更新 {len(results)} 个项目的体积"
            if missing:
                message += f"；{missing} 个文件缺失或不可访问，显示已知大小"
            self.statusBar().showMessage(message, 7000)
            if missing or len(results) > 1:
                details = "\n".join(f"项目 #{result.project_id}：{line}"
                                    for result in results for line in result.errors)
                (warn if missing else info)(self, "项目体积更新完成", message,
                                            detailed=details or None)

        def failed(message: str) -> None:
            logger.error("项目体积统计失败：%s", message)
            self.statusBar().showMessage("项目体积统计失败", 5000)
            warn(self, "体积统计失败", message)

        run_with_progress(
            self, "更新项目体积", "正在统计已登记的文件…", work,
            on_done=done, on_error=failed,
            on_cancel=lambda: self.statusBar().showMessage("已取消体积统计，原缓存保留", 5000),
        )

    def _update_favorite_button(self, *_args) -> None:
        """选择变化仅同步按钮，不刷新项目或打断预览。"""
        ids = self._selected_project_ids()
        favorites = getattr(self, "_favorite_ids", set())
        all_favorite = bool(ids) and all(pid in favorites for pid in ids)
        self.btn_favorite.setEnabled(bool(ids))
        self.btn_favorite.setText(
            ("★ 取消收藏" if all_favorite else "☆ 收藏")
            + (f"（{len(ids)}）" if len(ids) > 1 else "")
        )

    def action_toggle_project_favorite(self, project_id: int | None = None) -> None:
        """星标操作固定单个项目；按钮与快捷键操作当前选中项目。"""
        ids = [project_id] if project_id is not None else self._selected_project_ids()
        if not ids:
            return
        favorites = self.repo.favorite_project_ids()
        self.action_set_projects_favorite(ids, not all(pid in favorites for pid in ids))

    def action_set_projects_favorite(self, project_ids: list[int], favorite: bool) -> None:
        """批量保存收藏；普通视图局部更新，避免收藏动作重载正在播放的预览。"""
        try:
            count = self.repo.set_projects_favorite(project_ids, favorite)
        except Exception:
            logger.exception("保存项目收藏失败")
            warn(self, "收藏失败", "未能保存项目收藏，请重试。")
            return
        self._favorite_ids = self.repo.favorite_project_ids()
        self.proj_model.set_favorite_ids(self._favorite_ids)
        if self._current_filter_kind == "favorite" or "favorite" in self.search_box.text().casefold():
            self.refresh_projects()
        else:
            self._refresh_tag_tree()
            self._update_favorite_button()
        self.statusBar().showMessage(
            f"{'已收藏' if favorite else '已取消收藏'} {count} 个项目", 3000,
        )

    def action_set_projects_hidden(self, project_ids: list[int], hidden: bool) -> None:
        """批量切换隐藏状态，并保留仍在当前结果中的选择与预览。"""
        ids = list(dict.fromkeys(project_ids))
        if not ids:
            return
        action = "隐藏" if hidden else "取消隐藏"
        try:
            count = self.repo.set_projects_hidden(ids, hidden)
        except Exception as exc:
            logger.exception("%s项目失败：%s", action, ids)
            warn(
                self, f"{action}失败", f"未能{action}项目，请重试。",
                detailed=(f"操作：{action}项目\n项目 ID：{', '.join(map(str, ids))}\n"
                          f"失败阶段：保存隐藏状态\n异常：{type(exc).__name__}: {exc}"),
            )
            return
        self.refresh_projects(preserve_selection=True)
        self.statusBar().showMessage(f"已{action} {count} 个项目", 3000)

    def action_random_project(self) -> None:
        """从当前筛选结果等概率选一个项目；待执行的搜索先刷新再抽取。"""
        parsed = parse_search(self.search_box.text().strip())
        if not parsed.ok:
            self.statusBar().showMessage("请先修正搜索表达式，再随便看看。", 4000)
            return
        if self._search_timer.isActive():
            self._search_timer.stop()
            self.refresh_projects()
        ids = [self.proj_model.index(row, 0).data(ProjectModel.RoleId)
               for row in range(self.proj_model.rowCount())]
        if not ids:
            self.statusBar().showMessage("当前筛选中没有项目。", 3000)
            return
        pid = random.choice(ids)
        index = self.proj_model.index_of_id(pid)
        selection = self.proj_view.selectionModel()
        same_project = self._current_project_id == pid and self._selected_project_ids() == [pid]
        selection.blockSignals(True)
        try:
            selection.setCurrentIndex(index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        finally:
            selection.blockSignals(False)
        if not same_project:
            self._on_project_selected(index, QModelIndex())
        self._update_favorite_button()
        view = self.proj_table if self.view_stack.currentIndex() == 1 else self.proj_view
        view.scrollTo(index)
        view.setFocus()
        project = self.proj_model.project_at(index.row())
        self.statusBar().showMessage(f"随便看看：{project.title}", 3000)

    def _set_view_mode(self, mode: str) -> None:
        self.btn_view_grid.setChecked(mode == "grid")
        self.btn_view_list.setChecked(mode == "list")
        self.view_stack.setCurrentIndex(1 if mode == "list" else 0)

    def _on_project_table_double_clicked(self, index: QModelIndex) -> None:
        """网址列双击打开链接，其余单元格沿用项目编辑入口。"""
        if not index.isValid():
            return
        column = self.proj_model.column_kind(index.column())
        if column is not None:
            kind, field = column
            if kind == "field" and field.type == "url":
                value = str(index.data(Qt.DisplayRole) or "").strip()
                if value:
                    open_web_url(self, value)
                    return
        self.action_edit_project()


    def _rebuild_columns(self) -> None:
        """根据当前 fields schema + 附加列 重建 QTableView 的列。
        仅显示 visible=True 的字段；列顺序完全跟随 fields.ord（task #22 round 5：
        取消"标题强制第一列"的硬约束，与 Step 2 / 设置 → 字段 / 现有字段编辑面板
        对齐——那几个面板都允许标题字段任意排序）。

        task #33：字段定义指纹没变就跳过重设（避免每次 refresh 都 model reset）。
        """
        all_fields = self.repo.list_fields()
        fp = tuple(
            (f.id, f.name, f.type, f.visible, f.is_title, f.ord)
            for f in all_fields
        )
        if fp == self._fields_fingerprint:
            return
        self._fields_fingerprint = fp
        # 仅取可见的；title 即使不可见也强制保留
        visible_fields = [f for f in all_fields if f.visible or f.is_title]
        # 按 ord / id 排序，标题字段不再特判
        cols = sorted(
            visible_fields,
            key=lambda f: (f.ord, f.id or 0),
        )
        self.proj_model.set_columns(cols, include_extras=True)
        self._apply_col_widths()
        self._sync_project_sort_indicator()


    def _apply_col_widths(self) -> None:
        h = self.proj_table.horizontalHeader()
        h.setStretchLastSection(False)
        n = self.proj_model.columnCount()
        for c in range(n):
            h.setSectionResizeMode(c, QHeaderView.Interactive)
            key = self.proj_model.column_key(c)
            # 仅在用户尚未拖宽时设默认值
            w = self.proj_table.columnWidth(c)
            if w <= 0 or w == 100:
                self.proj_table.setColumnWidth(c, self.DEFAULT_COL_WIDTHS.get(key, 140))


    def _cover_pix(self, p: Project) -> QPixmap | None:
        if not p.cover_file_id:
            return None
        f = self.repo.get_file(p.cover_file_id)
        if not f:
            return None
        path = self._resolve(f)
        if not path.exists() or detect_kind(path) != "image":
            return None
        # task #33：走缩略图缓存（解码阶段即缩放 + QPixmapCache），
        # 目标逻辑尺寸 = 卡片封面区（CARD_W-2*PAD × COVER_H）
        try:
            dpr = float(self.proj_view.devicePixelRatioF())
        except Exception:
            dpr = 1.0
        return get_cover(path, QSize(_CARD_W - 2 * _CARD_PAD, _COVER_H), dpr)


    def _refresh_filtered_projects(self) -> None:
        """只改变筛选条件；底层记录发生变化时自动回退完整刷新。"""
        self.refresh_projects(filter_only=True)

    def refresh_projects(self, *, filter_only: bool = False, preserve_selection: bool = False) -> None:
        prev_project_id = self._current_project_id
        selected_ids = self._selected_project_ids() if preserve_selection else []
        token = (self.repo, self.repo.change_token())
        filter_only = filter_only and token == getattr(self, "_filter_refresh_token", None)
        if not filter_only:
            self._favorite_ids = self.repo.favorite_project_ids()
            self._rebuild_columns()
            self._refresh_tag_tree()

        # 2) 根据当前过滤条件取项目
        keyword = self.search_box.text().strip() if hasattr(self, "search_box") else ""
        query_ast = None
        if hasattr(self, "search_box"):
            self.search_box.setStyleSheet("")
            self.search_box.setToolTip("")
        if keyword:
            parsed = parse_search(keyword)
            if not parsed.ok:
                err = parsed.error
                msg = f"搜索语法错误：{err.message}（位置 {err.position + 1}）" if err else "搜索语法错误"
                self.search_box.setStyleSheet(
                    f"QLineEdit#SearchBox {{ color: {_current_palette().danger}; }}"
                )
                self.search_box.setToolTip(msg)
                self.statusBar().showMessage(msg, 5000)
                self.btn_random_project.setEnabled(False)
                return
            query_ast = parsed.ast
            self.search_box.setStyleSheet("")
            self.search_box.setToolTip("")

        self._last_filter_ast = query_ast
        self._sync_sidebar_query(query_ast)
        kind, value = self._current_filter_kind, self._current_filter_value
        projects = self.repo.list_projects_query(query_ast)
        title_text = {"favorite": "★ 收藏项目", "untagged": "未分类", "review": "⚡ 待审阅 LLM 建议",
                      "mcp": "🤖 未读MCP修改", "hidden": "隐藏项目", "tag": f"#{value}",
                      "tag_prefix": f"📁 {value} / *", "not_tag": f"不包含 #{value}",
                      "not_tag_prefix": f"不包含 📁 {value} / *"}.get(kind, "全部项目" if not keyword else "搜索结果")
        type_summary = self.file_type_filter.summary()

        if type_summary:
            title_text = f"{title_text} · {type_summary}"
        if keyword:
            title_text = f"{title_text} · 搜索「{keyword}」"
            self._record_search_history(keyword)
        self.lbl_filter_title.setText(title_text)

        # 封面只在卡片绘制时按需读取；列表模式和屏幕外项目不产生图片 I/O。
        file_counts = self.repo.count_files_by_project([p.id for p in projects if p.id is not None])
        self.proj_model.set_favorite_ids(self._favorite_ids)
        selection = self.proj_view.selectionModel()
        signals_blocked = selection.blockSignals(True)
        try:
            self.proj_model.set_data(
                projects, {}, file_counts, cover_loader=self._cover_pix,
                project_sizes=self.repo.project_sizes([p.id for p in projects if p.id is not None]),
            )
            target_idx = self.proj_model.index_of_id(prev_project_id)
            retained = [self.proj_model.index_of_id(pid) for pid in selected_ids]
            retained = [index for index in retained if index.isValid()]
            if not target_idx.isValid() and retained:
                target_idx = retained[0]
            if not target_idx.isValid() and projects:
                target_idx = self.proj_model.index(0, 0)
            self.proj_view.setCurrentIndex(target_idx)
            if retained:
                selection.clearSelection()
                for index in retained:
                    selection.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        finally:
            selection.blockSignals(signals_blocked)
        self.lbl_count.setText(f"{len(projects)} 个项目")
        self.btn_random_project.setEnabled(bool(projects))

        if projects:
            # 同一项目仍在结果中时保留文件树、选中文件和播放位置。
            if (not filter_only and not preserve_selection) or target_idx.data(ProjectModel.RoleId) != prev_project_id:
                self._on_project_selected(target_idx, QModelIndex())
            # task #15 T2 D4：库里有项目 → 永久隐藏首次引导横幅
            self._on_user_action_dismiss_banner()
            if keyword:
                self.statusBar().showMessage(f"搜索命中 {len(projects)} 个项目", 3000)
        else:
            self._current_project_id = None
            self._show_project(None)
            if keyword:
                self.statusBar().showMessage("搜索命中 0 个项目", 3000)
        self._update_favorite_button()
        # 选中项目时可能写入已读等设置，完成后再记录令牌。
        final_token = self.repo.change_token()
        self._filter_refresh_token = (
            (self.repo, final_token) if final_token[1] == token[1][1] else None
        )


    def _refresh_tag_tree(self) -> None:
        # 只在记录变化或显式侧栏更新时读取计数；纯筛选继续复用已有树。
        counts = self.repo.visible_sidebar_counts()
        self.tag_tree.populate(
            tag_counts=self.repo.visible_tag_counts(),
            total=counts["total"],
            untagged=counts["untagged"],
            pending_review=counts["pending_review"],
            mcp_modified=counts["mcp_modified"],
            favorites=counts["favorites"],
            hidden=counts["hidden"],
            saved_searches=load_saved_searches(self.repo.get_setting(SAVED_SEARCHES_SETTING_KEY, "[]")),
        )
        if hasattr(self, "search_box"):
            parsed = parse_search(self.search_box.text().strip())
            self._sync_sidebar_query(parsed.ast if parsed.ok else getattr(self, "_last_filter_ast", None))


    def _on_tag_filter_changed(self, kind: str, value: str) -> None:
        if kind == "saved_search":
            saved = next((item for item in self.tag_tree.saved_searches if item["name"] == value), None)
            if saved is not None:
                parsed = parse_search(saved["query"])
                if parsed.ok:
                    self._set_filter_query(parsed.ast)
                else:
                    self.statusBar().showMessage("该自定义筛选的表达式无效，请从搜索菜单删除后重新保存。", 5000)
                    self._sync_sidebar_query(getattr(self, "_last_filter_ast", None))
            return
        self._replace_sidebar_query("tag", sidebar_node(kind, value))


    def _on_tag_action(self, action: str, kind: str, value: str) -> None:
        """标签树右键：重命名 / 合并 / 删除（全库范围，含 ``value/...`` 子标签）。"""
        from PySide6.QtWidgets import QInputDialog

        n_projects = self.repo.count_projects_with_tag(value)
        if action == "rename":
            new, ok = QInputDialog.getText(
                self, "重命名标签", f"把「{value}」重命名为：", text=value,
            )
            new = new.strip()
            if not ok or not new or new == value:
                return
            n = self.repo.rename_tag(value, new)
            msg = f"已把标签「{value}」重命名为「{new}」（影响 {n} 个项目）"
        elif action == "merge":
            new, ok = QInputDialog.getText(
                self, "合并标签", f"把「{value}」合并到标签：",
            )
            new = new.strip()
            if not ok or not new or new == value:
                return
            if not confirm(
                self, "合并标签",
                f"将把标签「{value}」合并进「{new}」"
                f"（影响 {n_projects} 个项目），原标签消失。\n\n继续？",
                yes="合并",
            ):
                return
            n = self.repo.merge_tag(value, new)
            msg = f"已把标签「{value}」合并进「{new}」（影响 {n} 个项目）"
        elif action == "delete":
            if not confirm(
                self, "删除标签",
                f"将从所有项目移除标签「{value}」"
                f"（含其子标签，影响 {n_projects} 个项目）。\n"
                "项目本身不受影响。\n\n继续？",
                yes="删除", danger=True,
            ):
                return
            n = self.repo.remove_tag_everywhere(value)
            msg = f"已删除标签「{value}」（影响 {n} 个项目）"
        else:
            return
        self.refresh_projects()
        self.statusBar().showMessage(msg, 4000)


    def _on_projects_dropped_on_tag(self, project_ids: list[int], tag: str) -> None:
        """把拖到标签树上的项目批量追加标签。"""
        n_changed = self.repo.batch_add_tag(project_ids, tag)
        self.refresh_projects()
        valid_ids: list[int] = []
        for raw in project_ids:
            try:
                pid = int(raw)
            except (TypeError, ValueError):
                continue
            if pid > 0 and pid not in valid_ids:
                valid_ids.append(pid)
        total = len(valid_ids)
        if n_changed:
            self.statusBar().showMessage(
                f"已为 {n_changed} / {total} 个项目添加标签「{tag}」", 4000,
            )
        else:
            self.statusBar().showMessage(
                f"选中项目已拥有标签「{tag}」", 3000,
            )


    def _selected_project_ids(self) -> list[int]:
        """返回当前选中的项目 id 列表（支持多选）。"""
        # 网格只选择 modelColumn(0)，并不选中模型的整行所有字段；selectedRows 会漏掉它。
        indexes = self.proj_view.selectionModel().selectedIndexes()
        return list(dict.fromkeys(idx.data(ProjectModel.RoleId) for idx in indexes if idx.isValid()))


    def _on_project_selected(self, cur, _prev) -> None:
        # task #25: 多选模式下，若选中多个项目，显示选中数量而非单个项目
        selected = self._selected_project_ids()
        if len(selected) > 1:
            self._current_project_id = None
            self._show_multi_selection(len(selected))
            return

        if not cur.isValid():
            self._current_project_id = None
            self._show_project(None)
            return
        pid = cur.data(ProjectModel.RoleId)
        self._current_project_id = pid
        self._show_project(
            self.repo.get_project(pid),
            preserve_preview=getattr(self, "_displayed_project_key", None) == (self.repo, pid),
        )


    def _show_multi_selection(self, count: int) -> None:
        """多选时显示选中数量 + 批量操作面板（task #39 T1）。"""
        self._displayed_project_key = None
        splitter_sizes = self._main_splitter_sizes()
        # 清空文件表 & 预览
        self.tbl_files.blockSignals(True)
        self.tbl_files.clear()
        self.tbl_files.blockSignals(False)
        self.preview.show_file(None)
        self._update_preview_file_navigation()

        # 更新预览区显示选中数量 + 批量操作入口
        self.lbl_meta_title.setText(f"已选 {count} 个项目")
        self.lbl_meta_desc.setText("")
        self.project_tags.set_tags([])
        self._batch_panel.setVisible(True)
        self.lbl_files_hint.setText("多选模式下不显示文件列表")
        self.statusBar().showMessage(f"已选中 {count} 个项目")
        self._restore_main_splitter_sizes(splitter_sizes)


    def _batch_add_tag_dialog(self) -> None:
        ids = self._selected_project_ids()
        if not ids:
            return
        from PySide6.QtWidgets import QInputDialog
        tag, ok = QInputDialog.getText(
            self, "批量加标签", f"为 {len(ids)} 个项目添加标签：",
        )
        tag = tag.strip()
        if not ok or not tag:
            return
        n = self.repo.batch_add_tag(ids, tag)
        self.refresh_projects()
        self.statusBar().showMessage(
            f"已为 {n} / {len(ids)} 个项目添加标签「{tag}」", 4000,
        )


    def _batch_llm_suggest(self) -> None:
        ids = self._selected_project_ids()
        if not ids or self.llm_queue is None:
            return
        if not self._llm_check_configured_or_prompt():
            return
        from ..llm import load_config
        from .llm_suggest_dialog import LLMSuggestDialog
        cfg = load_config(self.repo)
        fields = self.repo.list_fields()
        # 批量场景不带参考文件（各项目文件不同），只收集补充说明 + 目标字段
        dlg = LLMSuggestDialog(f"{len(ids)} 个项目", [], cfg, fields, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        n = 0
        for pid in ids:
            p = self.repo.get_project(pid)
            if p is None:
                continue
            try:
                self.llm_queue.enqueue_meta_suggest(
                    p, [], dlg.user_note, dlg.target_field_ids,
                )
                n += 1
            except Exception:
                logger.warning("批量 LLM 任务入队失败: pid=%s", pid, exc_info=True)
        self.statusBar().showMessage(f"已提交 {n} 个 LLM 任务", 4000)


    def _batch_mark_mcp_seen(self) -> None:
        self._on_mark_mcp_seen(self._selected_project_ids())


    def _show_project(self, p: Project | None, *, preserve_preview: bool = False) -> None:
        # Repository 也参与身份判定，避免不同库中相同项目 ID 被当成重复选择。
        project_key = (self.repo, p.id) if p is not None else None
        project_changed = project_key != getattr(self, "_displayed_project_key", None)
        self._displayed_project_key = project_key
        splitter_sizes = self._main_splitter_sizes()
        selected_ids = set(self._selected_file_ids()) if preserve_preview else set()
        current_id = self._current_file_row_id() if preserve_preview else None
        expanded: set[str] = set()

        def walk(parent: QTreeWidgetItem) -> Iterator[QTreeWidgetItem]:
            """遍历文件节点，以稳定文件 ID 恢复刷新前的选择。"""
            for i in range(parent.childCount()):
                child = parent.child(i)
                yield child
                yield from walk(child)
        if preserve_preview:
            expanded = {
                self._subfolder_from_tree_node(item)
                for item in walk(self.tbl_files.invisibleRootItem())
                if item.data(0, Qt.UserRole) == -1 and item.isExpanded()
            }
        scroll = self.tbl_files.verticalScrollBar().value()
        # 重建文件表；截图刷新时保留播放器及其当前帧。
        self.tbl_files.blockSignals(True)
        self.tbl_files.clear()
        self.tbl_files.blockSignals(False)
        if not preserve_preview:
            self.preview.show_file(None)
        self._update_preview_file_navigation()
        # 单选/无选中时隐藏批量面板（task #39 T1）
        self._batch_panel.setVisible(False)

        self.project_tags.set_tags(p.tags if p is not None else [])

        if p is None:
            self.lbl_meta_title.setText("（未选择项目）")
            self.lbl_meta_desc.setText("")
            self.lbl_files_hint.setText("")
            self.statusBar().showMessage("")
            self._restore_main_splitter_sizes(splitter_sizes)
            return

        self._update_cached_project_sizes([p.id])
        # 标题
        self.lbl_meta_title.setText(p.title or "(未命名)")

        # 描述（取 description_md 的前几行 / 前 ~160 字符；空则提示）
        desc = (p.description_md or "").strip()
        if not desc:
            desc = "（暂无描述）"
        # 把 Markdown 简化成纯文本（去掉常见标记），单段显示
        desc = self._desc_plain(desc)
        self.lbl_meta_desc.setText(desc)

        # 文件表（task #17：按 subfolder 建树；task #04：按 origin 过滤）
        # task #33：只查一次，总数在过滤前先记下
        all_files = self.repo.list_files(p.id)  # type: ignore[arg-type]
        all_files_count = len(all_files)
        files = all_files

        # task #04：按 origin 过滤
        generated_count = sum((f.origin or "user") == "generated" for f in files)
        has_generated = generated_count > 0
        self._btn_origin_filter.setVisible(has_generated)
        # 封面快照等生成文件默认收起；尊重项目已保存的显式来源选择。
        origin_filter = self.repo.get_project_setting(p.id, "files_view_origin_filter", "user")
        is_user_only = origin_filter == "user"
        self._btn_origin_filter.setChecked(is_user_only)
        self._btn_origin_filter.setText("👤" if is_user_only else "🌐")
        self._btn_origin_filter.setToolTip(
            f"仅显示用户文件，已隐藏 {generated_count} 个生成文件（含封面快照）；点击显示全部文件"
            if is_user_only else
            f"显示全部文件（含 {generated_count} 个生成文件）；点击仅显示用户文件"
        )
        if is_user_only:
            files = [f for f in files if (f.origin or "user") == "user"]

        # task #31b：加载视图模式（tree/flat）
        view_mode = self.repo.get_project_setting(p.id, "files_view_mode", "tree")
        self._files_view_mode = view_mode
        self._btn_view_mode.setText("📋" if view_mode == "flat" else "🌲")

        self.tbl_files.blockSignals(True)
        if view_mode == "flat":
            self._populate_files_flat(files)
        else:
            self.tbl_files.setSortingEnabled(False)
            self._populate_files_tree(files)
            self.tbl_files.expandAll()
        if preserve_preview:
            for item in walk(self.tbl_files.invisibleRootItem()):
                fid = item.data(0, Qt.UserRole)
                if fid == -1:
                    item.setExpanded(self._subfolder_from_tree_node(item) in expanded)
                else:
                    item.setSelected(fid in selected_ids)
                    if fid == current_id:
                        self.tbl_files.setCurrentItem(item, 0, QItemSelectionModel.NoUpdate)
            self.tbl_files.verticalScrollBar().setValue(scroll)
        self.tbl_files.blockSignals(False)
        if preserve_preview and current_id is not None and self._current_file_row_id() != current_id:
            # 刷新后文件已被移除时清理旧画面，不把选择抢到首文件。
            self._current_file_id = None
            self.preview.show_file(None)
        self._update_preview_file_navigation()

        if project_changed and self.repo.get_setting("auto_select_first_file", "0") == "1":
            self._navigate_preview_file(1)

        # 应用项目级列偏好（可见性 + 列宽）
        self._apply_files_columns_prefs(p.id)

        # 同时说明总数和隐藏数量，便于用户找回封面快照等生成文件。
        files_hint = f"共 {all_files_count} 个文件"
        if is_user_only and generated_count:
            files_hint = (
                f"显示 {len(files)} / {all_files_count} 个文件"
                f" · 已隐藏 {generated_count} 个生成文件"
            )
        self.lbl_files_hint.setText(files_hint)
        self.lbl_files_hint.setToolTip(
            "双击说明列可编辑；点击来源按钮可切换用户文件／全部文件。"
        )
        self.statusBar().showMessage(
            f"项目 #{p.id}  ·  {p.title}  ·  {len(files)} 文件"
        )
        self._restore_main_splitter_sizes(splitter_sizes)


    @staticmethod
    def _desc_plain(md: str) -> str:
        """把 Markdown 简化为单段纯文本（用于 2~3 行省略显示）。"""
        import re
        text = md
        # 去掉代码块
        text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
        # 去掉行内代码 / 加粗 / 斜体标记符
        text = re.sub(r"[`*_>#~]+", "", text)
        # 链接 [text](url) → text
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        # 多空白合并
        text = re.sub(r"\s+", " ", text).strip()
        return text


    def action_open_settings(self) -> None:
        dlg = SettingsDialog(
            self.repo,
            library_root=self.library.root,
            db_path=self.db_path,
            parent=self,
        )
        dlg.default_view_changed.connect(self._set_view_mode)
        dlg.default_storage_changed.connect(lambda _v: None)  # 仅持久化
        # 字段定义变化后刷新项目列表（描述可能被追加；字段值/列会变）
        dlg.fields_changed.connect(self.refresh_projects)
        # task #15 T2 D4 一次性标志：在设置页加过非系统字段 → 触发；
        # 同步把横幅隐藏掉（避免再次出现）
        dlg.fields_changed.connect(self._on_user_action_dismiss_banner)
        dlg.exec()


    def action_open_settings_fields(self) -> None:
        """直接跳到设置 → 字段（task #15 T2 横幅按钮入口）。"""
        dlg = SettingsDialog(
            self.repo,
            library_root=self.library.root,
            db_path=self.db_path,
            parent=self,
        )
        dlg.default_view_changed.connect(self._set_view_mode)
        dlg.default_storage_changed.connect(lambda _v: None)
        dlg.fields_changed.connect(self.refresh_projects)
        dlg.fields_changed.connect(self._on_user_action_dismiss_banner)
        dlg.set_active_category("字段")
        dlg.exec()


    def _on_user_action_dismiss_banner(self) -> None:
        """task #15 T2 D4：用户做过任何"成长性动作"后永久隐藏首次引导横幅。

        触发点：跑过库字段设计助手 / 在设置加过非系统字段 / 创建过第一个项目 /
        主动点了横幅的"不再显示"。本方法幂等。
        """
        from .first_run_banner import dismiss_banner
        dismiss_banner(self.repo)
        if hasattr(self, "first_run_banner") and self.first_run_banner is not None:
            self.first_run_banner.hide()


    def action_open_mcp_audit(self) -> None:
        from .mcp_audit_dialog import MCPAuditDialog
        dlg = MCPAuditDialog(self.repo, parent=self)
        dlg.exec()
        self._check_mcp_activity()


    def _check_mcp_activity(self) -> None:
        """轻量轮询：只查 audit 最新 id，有变化才刷新（task #33：精准刷新）。"""
        latest = self.repo.max_mcp_audit_id()
        if latest <= self._mcp_last_audit_id:
            return
        self._mcp_last_audit_id = latest
        total = self.repo.count_mcp_audit()
        self.lbl_mcp_count.setText(f"📋 MCP 操作: {total}")
        if self._current_filter_kind == "mcp" or "mcp:" in self.search_box.text().casefold() or "mcp=" in self.search_box.text().casefold():
            # 正在看「未读 MCP 修改」筛选：整刷以更新列表内容
            self.refresh_projects()
        else:
            # 只更新标签树上的 MCP 计数，不动项目列表（避免每 10s 整屏重建）
            self._refresh_tag_tree()


    def _on_mark_mcp_seen(self, project_ids: list[int] | None = None) -> None:
        """右键菜单：标记已了解该项目的 MCP 修改（支持多选）。"""
        selected_ids = project_ids if project_ids is not None else self._selected_project_ids()
        if not selected_ids:
            return
        for pid in selected_ids:
            self.repo.clear_project_mcp_modified(pid)
        self.refresh_projects()
        self.statusBar().showMessage(f"已标记 {len(selected_ids)} 个项目的 MCP 修改为已读", 3000)


    def action_open_llm_tasks(self) -> None:
        from .llm_tasks_panel import LLMTasksDialog
        dlg = LLMTasksDialog(self.repo, parent=self)
        dlg.suggestions_reapplied.connect(self._on_llm_suggestions_added)
        dlg.exec()


    def action_llm_suggest_for_project(self, pid: int | None = None) -> None:
        """从右键菜单触发：跳过项目编辑对话框，直接弹 LLMSuggestDialog。"""
        # QAction.triggered 会传 bool；Python 里 bool 是 int 的子类，
        # 必须先把 bool 显式排除再判 int。
        if isinstance(pid, bool) or not isinstance(pid, int):
            pid = self._current_project_id
        if pid is None or self.llm_queue is None:
            return
        # 未配置 API → 提示用户去设置但不主动跳转（按用户偏好：文字引导即可）
        if not self._llm_check_configured_or_prompt():
            return
        p = self.repo.get_project(pid)
        if not p:
            return
        files = self.repo.list_files(pid)
        from ..llm import load_config
        from .llm_suggest_dialog import LLMSuggestDialog
        cfg = load_config(self.repo)
        fields = self.repo.list_fields()
        dlg = LLMSuggestDialog(p.title, files, cfg, fields, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        self._enqueue_meta_suggest(
            p, dlg.ref_file_ids, dlg.user_note, dlg.target_field_ids,
        )


    def _launch_llm_from_dialog(self, edit_dlg, project) -> None:
        """从项目编辑对话框中点 ✨ 触发：弹 LLMSuggestDialog；执行后关闭编辑对话框。"""
        if self.llm_queue is None or project.id is None:
            return
        # 与右键入口一致：未配置 API 时给文字提示，不强行跳转
        if not self._llm_check_configured_or_prompt(parent=edit_dlg):
            return
        files = self.repo.list_files(project.id)
        from ..llm import load_config
        from .llm_suggest_dialog import LLMSuggestDialog
        cfg = load_config(self.repo)
        fields = self.repo.list_fields()
        dlg = LLMSuggestDialog(project.title, files, cfg, fields, parent=edit_dlg)
        if dlg.exec() != QDialog.Accepted:
            return
        # 执行 → 入队 + 关闭项目编辑对话框（不保存当前修改）
        self._enqueue_meta_suggest(
            project, dlg.ref_file_ids, dlg.user_note, dlg.target_field_ids,
        )
        edit_dlg.reject()


    def _enqueue_meta_suggest(
        self, project, ref_file_ids, user_note, target_field_ids=None,
    ) -> None:
        try:
            self.llm_queue.enqueue_meta_suggest(
                project, ref_file_ids, user_note, target_field_ids,
            )
            self.statusBar().showMessage(
                f"已提交 LLM 任务：{project.title}", 4000,
            )
        except Exception as e:
            warn(self, "失败", str(e))


    def _llm_check_configured_or_prompt(self, *, parent=None) -> bool:
        """检查默认 provider 是否配好 API Key；未配则弹提示并返回 False。

        提示走「文字引导、不主动跳转」路线（用户偏好：避免帮用户决定下一步）：
        - 默认 provider 不存在 / api_key 为空 → 弹 information 框，文案明确指向
          「设置 → API」，但不自动打开该页。
        """
        from ..llm import load_config
        cfg = load_config(self.repo)
        active = cfg.active()
        if active is not None and (active.api_key or "").strip():
            return True
        info(
            parent or self,
            "未配置 API",
            "尚未配置 LLM API Key，无法生成元数据建议。\n\n"
            "请打开「设置 → API」页填入默认 provider 的 API Key 后再试。",
        )
        return False


    def _on_llm_counts(self, n: int) -> None:
        self.lbl_llm_count.setText(f"⚡ LLM 任务: {n}")


    def _on_llm_suggestions_added(self, project_id: int, count: int) -> None:
        # 刷新 TagTree 和列表（待审阅计数变化、可能影响列表）
        self.refresh_projects()
        if count > 0:
            p = self.repo.get_project(project_id)
            title = p.title if p else f"#{project_id}"
            self.statusBar().showMessage(
                f"✨ 项目「{title}」获得 {count} 条 LLM 建议", 6000,
            )


    def _on_llm_task_failed(self, _tid: int, err: str) -> None:
        self.statusBar().showMessage(f"❌ LLM 任务失败: {err[:80]}", 6000)


    def action_new_project(self) -> None:
        initial = Project()
        dlg = ProjectDialog(initial, repo=self.repo, parent=self)
        # 新建对话框不再展示 ✨ LLM 建议按钮（M1 决策，2026-06-02）：
        # 此时项目还没文件、没历史，建议无意义；统一改为引导用户先保存再请 LLM。
        # request_llm_suggest 信号在新建模式下永远不会触发，所以这里也不再连接。
        if dlg.exec() == ProjectDialog.Accepted:
            p = dlg.project()
            pid = self.repo.save_project(p)
            self.refresh_projects()
            self._select_project_by_id(pid)


    def action_edit_project(self) -> None:
        if self._current_project_id is None:
            return
        p = self.repo.get_project(self._current_project_id)
        if not p:
            return
        dlg = ProjectDialog(p, repo=self.repo, parent=self)
        # 用户在编辑对话框中点 ✨ → 弹 LLMSuggestDialog → 入队 → 关闭编辑对话框
        dlg.request_llm_suggest.connect(
            lambda d=dlg, pp=p: self._launch_llm_from_dialog(d, pp)
        )
        if dlg.exec() == ProjectDialog.Accepted:
            self.repo.save_project(dlg.project())
            self.refresh_projects()
            self._select_project_by_id(p.id)  # type: ignore[arg-type]
        else:
            # 即便用户 Cancel，建议状态可能已被独立修改（applied/rejected）→ 也刷新
            self.refresh_projects()


    def action_delete_project(self) -> None:
        """删除项目（支持多选）。"""
        selected_ids = self._selected_project_ids()
        if not selected_ids:
            return

        # 收集所有选中项目的信息
        total_copy = 0
        total_link = 0
        titles = []
        for pid in selected_ids:
            p = self.repo.get_project(pid)
            if p:
                titles.append(p.title)
                files = self.repo.list_files(pid)  # type: ignore[arg-type]
                total_copy += sum(1 for f in files if f.is_relative)
                total_link += sum(1 for f in files if not f.is_relative)

        # 构建确认消息
        if len(titles) == 1:
            lines = [f"确定删除项目「{titles[0]}」？"]
        else:
            lines = [f"确定删除这 {len(selected_ids)} 个项目？"]
            lines.append(f"项目：{', '.join(titles[:3])}" + ("..." if len(titles) > 3 else ""))

        if total_copy == 0 and total_link == 0:
            lines.append("（这些项目目前没有文件）")
        else:
            if total_link:
                lines.append(f"🔗 链接 · {total_link} 个：原文件不受影响。")
            if total_copy:
                lines.append(
                    f"📦 仓储 · {total_copy} 个：物理文件将移入系统回收站。"
                    "\n⚠ 回收站不可用时将直接永久删除，无法从回收站恢复。"
                )

        if not confirm(self, "确认删除", "\n".join(lines), yes="删除", danger=True):
            return

        # 逐个删除（task #37：仓储文件进回收站；失败收集后统一汇报）
        delete_failures: list[str] = []
        permanently_deleted: list[str] = []
        for pid in selected_ids:
            p = self.repo.get_project(pid)
            if not p:
                continue

            files = self.repo.list_files(pid)  # type: ignore[arg-type]
            copy_files = [f for f in files if f.is_relative]

            # 删除仓储文件
            if copy_files:
                for f in copy_files:
                    try:
                        if move_to_trash(self.library.resolve(f.path, True)) == "deleted":
                            permanently_deleted.append(Path(f.path).name)
                    except OSError as e:
                        delete_failures.append(f"{Path(f.path).name}：{e}")
                pdir = self.library.project_dir(pid)  # type: ignore[arg-type]
                try:
                    # 目录里可能还有未登记的残留（如旧封面快照），一并进回收站
                    registered = {self.library.resolve(f.path, True) for f in copy_files}
                    for child in pdir.iterdir():
                        if child in registered:
                            continue  # 已失败的文件不再次删除或重复报告
                        if child.is_file():
                            try:
                                if move_to_trash(child) == "deleted":
                                    permanently_deleted.append(child.name)
                            except OSError as e:
                                delete_failures.append(f"{child.name}：{e}")
                    pdir.rmdir()
                except OSError:
                    logger.warning("项目目录未能清理：%s", pdir, exc_info=True)
            self.repo.delete_project(pid)

        self.refresh_projects()
        self.statusBar().showMessage(f"已删除 {len(selected_ids)} 个项目", 3000)
        if permanently_deleted:
            delete_failures.append("回收站不可用，以下文件已永久删除：\n" + "\n".join(permanently_deleted))
        if delete_failures:
            warn(
                self, "项目删除结果",
                "项目已删除。以下磁盘操作失败或回退为永久删除，请查看明细。",
                detailed="\n".join(delete_failures),
            )


    def action_export_project(self, pid: int | None = None) -> None:
        """导出当前/指定项目到本地目录。支持单项目和批量导出。"""
        from ..exporter import ExportOptions, export_project
        from ..utils import open_with_default_app

        # task #28 T2: 检查是否是多选导出
        selected_ids = self._selected_project_ids()

        # QAction.triggered 会传 bool(checked) 进来
        is_multi = len(selected_ids) > 1
        is_explicit_single = isinstance(pid, int) and pid > 0

        if is_multi:
            # 批量导出模式
            self._export_batch(selected_ids)
            return

        # 单项目导出模式
        if isinstance(pid, bool) or not isinstance(pid, int):
            pid = self._current_project_id
        if pid is None:
            info(self, "提示", "请先选择一个项目")
            return
        project = self.repo.get_project(pid)
        if project is None:
            warn(self, "提示", f"项目 id={pid} 不存在")
            return
        n_files = len(self.repo.list_files(pid))

        last_dir = self.repo.get_setting("last_export_dir", "") or str(Path.home())
        dlg = ExportDialog(project, n_files, last_dir, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        opts = ExportOptions(
            target_root=dlg.target_root(),
            copy_link_files=dlg.copy_link_files(),
        )

        # task #36：DB 数据先在主线程快照，复制阶段放后台线程
        snap = ExportSnapshotRepo(self.repo.list_files(pid), self.repo.list_fields())

        def _do(progress_cb, is_cancelled):
            def _cb(done, total, name):
                if is_cancelled():
                    raise OperationCancelled()
                progress_cb(done, total, f"正在复制（{done}/{total}）：{name}")
            return export_project(snap, self.library, project, opts, progress=_cb)

        def _on_done(result):
            # 记忆下次默认目录 + 结果摘要
            self.repo.set_setting("last_export_dir", str(opts.target_root))
            self._show_export_result(result)

        run_with_progress(
            self, "导出项目", "正在导出…", _do,
            on_done=_on_done,
            on_cancel=lambda: info(self, "已取消", "导出已被取消。"),
            on_error=lambda msg: warn(self, "导出失败", msg),
        )


    def _export_batch(self, selected_ids: list[int]) -> None:
        """批量导出多个项目（task #28 T2）。"""
        from ..exporter import ExportOptions, export_project
        from ..utils import open_with_default_app

        # 收集项目信息
        projects_info: list[tuple[int, str, int]] = []
        for pid in selected_ids:
            p = self.repo.get_project(pid)
            if p:
                n_files = len(self.repo.list_files(pid))
                projects_info.append((pid, p.title, n_files))

        if not projects_info:
            info(self, "提示", "没有可导出的项目")
            return

        last_dir = self.repo.get_setting("last_export_dir", "") or str(Path.home())
        dlg = ExportDialog(projects=projects_info, last_export_dir=last_dir, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        target_root = dlg.target_root()
        selected_indices = dlg.selected_projects()

        # task #36：所有 DB 数据在主线程快照，导出循环放后台线程
        snapshots: list = []
        for idx in selected_indices:
            pid, title, _n = projects_info[idx]
            project = self.repo.get_project(pid)
            if not project:
                continue
            snap = ExportSnapshotRepo(self.repo.list_files(pid), self.repo.list_fields())
            opts = ExportOptions(
                target_root=target_root,
                copy_link_files=dlg.copy_link_files(),
                mode=dlg.mode(),
                export_format=dlg.export_format(),
                preserve_structure=dlg.preserve_structure(),
                include_readme=dlg.include_readme(),
                include_llm_history=dlg.include_llm_history(),
            )
            snapshots.append((title, project, snap, opts))

        total_projects = len(snapshots)

        def _do(progress_cb, is_cancelled):
            results: list = []
            for i, (title, project, snap, opts) in enumerate(snapshots):
                if is_cancelled():
                    break
                progress_cb(i, total_projects, f"正在导出：{title}")
                try:
                    results.append((
                        title,
                        export_project(snap, self.library, project, opts, progress=None),
                    ))
                except Exception as e:
                    results.append((title, f"导出失败: {e}"))
            progress_cb(total_projects, total_projects, "")
            return {"results": results, "cancelled": is_cancelled()}

        def _on_done(payload):
            # 记忆下次默认目录
            self.repo.set_setting("last_export_dir", str(target_root))

            results = payload["results"]
            success_count = sum(1 for _, r in results if hasattr(r, 'n_files_copied'))
            msg = f"批量导出完成：\n\n成功 {success_count}/{total_projects} 个项目\n\n"
            if payload["cancelled"]:
                msg = "（已取消，仅完成部分内容）\n\n" + msg
            for title, r in results:
                if hasattr(r, 'n_files_copied'):
                    msg += f"✓ {title}: {r.n_files_copied} 文件 → {r.project_dir.name}\n"
                else:
                    msg += f"✗ {title}: {r}\n"

            box = QMessageBox(QMessageBox.Information, "批量导出完成", msg, parent=self)
            btn_open = box.addButton("📂 打开导出目录", QMessageBox.AcceptRole)
            box.addButton("关闭", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is btn_open:
                try:
                    open_with_default_app(target_root)
                except Exception:
                    logger.warning("打开导出目录失败: %s", target_root, exc_info=True)

        run_with_progress(
            self, "批量导出", f"正在批量导出 {total_projects} 个项目…", _do,
            on_done=_on_done,
            on_error=lambda msg: warn(self, "导出失败", msg),
        )


    def _show_export_result(self, result) -> None:
        """显示导出结果。"""
        from ..utils import open_with_default_app

        msg = (
            f"导出成功：\n\n"
            f"目录：{result.project_dir}\n"
            f"复制文件：{result.n_files_copied} 个\n"
            f"仅记录（未复制）：{result.n_files_referenced} 个\n"
        )
        if result.warnings:
            msg += f"\n⚠️ 警告 {len(result.warnings)} 条：\n"
            msg += "\n".join(f"- {w}" for w in result.warnings[:5])
            if len(result.warnings) > 5:
                msg += f"\n…（共 {len(result.warnings)} 条，详情见导出包内 README.md）"

        box = QMessageBox(QMessageBox.Information, "导出完成", msg, parent=self)
        btn_open = box.addButton("📂 打开导出目录", QMessageBox.AcceptRole)
        box.addButton("关闭", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is btn_open:
            try:
                open_with_default_app(result.project_dir)
            except Exception:
                logger.warning("打开导出目录失败: %s", result.project_dir, exc_info=True)


    def _select_project_by_id(self, pid: int) -> None:
        idx = self.proj_model.index_of_id(pid)
        if idx.isValid():
            self.proj_view.setCurrentIndex(idx)


    def _project_context_menu(self, pos) -> None:
        sender = self.sender()
        view = sender if sender in (self.proj_view, self.proj_table) else self.proj_view
        idx = view.indexAt(pos)
        if not idx.isValid():
            # task #39 T3：空白处右键 → 新建项目 / 添加文件（与文件表空白菜单对齐）
            menu = QMenu(self)
            menu.addAction("＋  新建项目", self.action_new_project)
            act_add = menu.addAction("📥  添加文件", self.action_add_files)
            act_add.setEnabled(self._current_project_id is not None)
            menu.exec(view.viewport().mapToGlobal(pos))
            return

        # task #25: 右键点在已有多选范围内时使用整组；点到未选项目时只操作该项目。
        selected_ids = self._project_context_target_ids(view, idx)
        is_multi = len(selected_ids) > 1

        menu = QMenu(self)

        # 使用被右键项目的原始值，多选和菜单内焦点变化均不改变筛选目标。
        project = idx.data(ProjectModel.RoleProject)
        column = self.proj_model.column_kind(idx.column())
        field = column[1] if column is not None and column[0] == "field" else None
        if project is not None and populate_project_field_filter_menu(
            menu, self, project, field, all_fields=view is self.proj_view,
        ):
            menu.addSeparator()

        favorites = self.repo.favorite_project_ids()
        all_favorite = all(pid in favorites for pid in selected_ids)
        menu.addAction(
            "★  取消收藏" if all_favorite else "☆  收藏项目",
            lambda _checked=False, ids=list(selected_ids), value=not all_favorite:
                self.action_set_projects_favorite(ids, value),
        )
        hidden_ids = self.repo.hidden_project_ids()
        if any(pid not in hidden_ids for pid in selected_ids):
            menu.addAction(
                "隐藏项目" if not is_multi else "隐藏项目（全部选中）",
                lambda _checked=False, ids=list(selected_ids): self.action_set_projects_hidden(ids, True),
            )
        if any(pid in hidden_ids for pid in selected_ids):
            menu.addAction(
                "取消隐藏" if not is_multi else "取消隐藏选中项目",
                lambda _checked=False, ids=list(selected_ids): self.action_set_projects_hidden(ids, False),
            )
        menu.addAction(
            "更新项目体积",
            lambda _checked=False, ids=list(selected_ids): self.action_update_project_sizes(ids),
        )
        menu.addSeparator()
        if is_multi:
            # 多选菜单
            menu.addAction(f"已选 {len(selected_ids)} 个项目")
            menu.addSeparator()
            menu.addAction("✨  LLM 元数据建议…", self.action_llm_suggest_for_project)
            menu.addSeparator()
            menu.addAction("📤  导出选中项目…", self.action_export_project)
            menu.addAction(
                "📦  所选项目内所有链接文件转为仓储…",
                lambda _checked=False, ids=list(selected_ids):
                    self.action_convert_projects_to_storage(ids),
            )
            menu.addSeparator()
            menu.addAction(
                "👁  已读MCP修改",
                lambda _checked=False, ids=list(selected_ids): self._on_mark_mcp_seen(ids),
            )
            menu.addSeparator()
            menu.addAction("🗑  删除", self.action_delete_project)
        else:
            # 单选菜单
            menu.addAction("✎  编辑…", self.action_edit_project)
            menu.addSeparator()
            menu.addAction("✨  LLM 元数据建议…", self.action_llm_suggest_for_project)
            menu.addSeparator()
            menu.addAction("📤  导出项目…", self.action_export_project)
            menu.addAction(
                "📦  项目内所有链接文件转为仓储…",
                lambda _checked=False, pid=int(idx.data(ProjectModel.RoleId)):
                    self.action_convert_project_to_storage(pid),
            )
            menu.addSeparator()
            act_paste_cover = menu.addAction(
                "📋  从剪切板设为封面", self.action_set_cover_from_clipboard,
            )
            # 没有图片就禁用
            from PySide6.QtWidgets import QApplication
            cb = QApplication.clipboard()
            has_img = False
            try:
                md = cb.mimeData()
                has_img = md is not None and (md.hasImage() or md.hasUrls())
            except Exception:
                pass
            act_paste_cover.setEnabled(has_img)
            if not has_img:
                act_paste_cover.setToolTip("剪切板中没有图片")
            menu.addSeparator()
            menu.addAction(
                "👁  已读MCP修改",
                lambda _checked=False, ids=list(selected_ids): self._on_mark_mcp_seen(ids),
            )
            menu.addSeparator()
            menu.addAction("🗑  删除", self.action_delete_project)

        global_pos = view.viewport().mapToGlobal(pos) if hasattr(view, "viewport") else view.mapToGlobal(pos)
        menu.exec(global_pos)


    def _project_context_target_ids(self, view, idx) -> list[int]:
        """返回本次项目右键菜单实际要操作的项目 id。"""
        clicked_id = idx.data(ProjectModel.RoleId)
        selected_ids = self._selected_project_ids()
        if clicked_id is None:
            return selected_ids
        try:
            clicked_id = int(clicked_id)
        except (TypeError, ValueError):
            return selected_ids
        if clicked_id in selected_ids:
            return selected_ids

        view.setCurrentIndex(idx)
        selection = view.selectionModel()
        if selection is not None:
            selection.select(
                idx,
                QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
            )
        return [clicked_id]
