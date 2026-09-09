"""项目列表：刷新/筛选/选择、项目操作、导出、LLM 入口（task #35：从 main_window.py 拆分，方法体未改动）。

Mixin：项目列表：刷新/筛选/选择、项目操作、导出、LLM 入口
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


class ProjectsMixin:
    """项目列表：刷新/筛选/选择、项目操作、导出、LLM 入口"""

    DEFAULT_COL_WIDTHS = {
        "title": 240, "author": 140, "date": 110,
        "rating": 90, "source_url": 200, "description": 260,
        "tags": 200, "__files__": 70, "__updated__": 150,
    }

    def _set_view_mode(self, mode: str) -> None:
        self.btn_view_grid.setChecked(mode == "grid")
        self.btn_view_list.setChecked(mode == "list")
        self.view_stack.setCurrentIndex(1 if mode == "list" else 0)


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


    def refresh_projects(self) -> None:
        prev_project_id = self._current_project_id
        # 0) 同步列定义（字段可能改了）
        self._rebuild_columns()
        # 1) 重建标签树（计数会变）
        self._refresh_tag_tree()

        # 2) 根据当前过滤条件取项目
        kind = self._current_filter_kind
        value = self._current_filter_value
        keyword = self.search_box.text().strip() if hasattr(self, "search_box") else ""
        search_all = (
            bool(keyword)
            and hasattr(self, "btn_search_all")
            and self.btn_search_all.isChecked()
        )
        if search_all:
            kind = "all"
            value = ""
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
                return
            query_ast = parsed.ast
            self.search_box.setStyleSheet("")
            self.search_box.setToolTip("")

        filter_ast = None
        if kind == "untagged":
            filter_ast = field_term("__untagged", "1", "=")
            projects = self.repo.list_projects_query(combine_and(query_ast, filter_ast))
            title_text = "未分类"
        elif kind == "review":
            review_pids = {
                p.id for p in self.repo.list_projects_pending_review()
                if p.id is not None
            }
            projects = [
                p for p in self.repo.list_projects_query(query_ast)
                if p.id in review_pids
            ]
            title_text = "⚡ 待审阅 LLM 建议"
        elif kind == "tag" and value:
            filter_ast = field_term("tag", value, "=")
            projects = self.repo.list_projects_query(combine_and(query_ast, filter_ast))
            title_text = f"#{value}"
        elif kind == "tag_prefix" and value:
            filter_ast = field_term("__tag_prefix", value)
            projects = self.repo.list_projects_query(combine_and(query_ast, filter_ast))
            title_text = f"📁 {value} / *"
        elif kind == "mcp":
            mcp_pids = {p["id"] for p in self.repo.list_mcp_modified_projects()}
            all_projects = self.repo.list_projects_query(query_ast)
            projects = [p for p in all_projects if p.id in mcp_pids]
            title_text = "🤖 未读MCP修改"
        else:
            projects = self.repo.list_projects_query(query_ast)
            title_text = "全部项目"

        if keyword:
            title_text = f"{title_text} · 搜索「{keyword}」"
            self._record_search_history(keyword)
        self.lbl_filter_title.setText(title_text)

        covers: dict[int, QPixmap] = {}
        for p in projects:
            pix = self._cover_pix(p)
            if pix is not None and p.id is not None:
                covers[p.id] = pix
        # task #33：文件数一条 GROUP BY 查完，替代逐项目 list_files 的 N+1
        file_counts = self.repo.count_files_by_project()
        self.proj_model.set_data(projects, covers, file_counts)
        self.lbl_count.setText(f"{len(projects)} 个项目")

        if projects:
            target_idx = self.proj_model.index(0, 0)
            if prev_project_id is not None:
                idx = self.proj_model.index_of_id(prev_project_id)
                if idx.isValid():
                    target_idx = idx
            self.proj_view.setCurrentIndex(target_idx)
            # task #15 T2 D4：库里有项目 → 永久隐藏首次引导横幅
            self._on_user_action_dismiss_banner()
            if keyword:
                self.statusBar().showMessage(f"搜索命中 {len(projects)} 个项目", 3000)
        else:
            self._current_project_id = None
            self._show_project(None)
            if keyword:
                self.statusBar().showMessage("搜索命中 0 个项目", 3000)


    def _refresh_tag_tree(self) -> None:
        self.tag_tree.populate(
            tag_counts=self.repo.tag_counts(),
            total=self.repo.count_projects_total(),
            untagged=self.repo.count_projects_untagged(),
            pending_review=self.repo.count_projects_with_pending_suggestions(),
            mcp_modified=len(self.repo.list_mcp_modified_projects()),
        )


    def _on_tag_filter_changed(self, kind: str, value: str) -> None:
        self._current_filter_kind = kind
        self._current_filter_value = value
        self.refresh_projects()


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
        rows = self.proj_view.selectionModel().selectedRows()
        return [idx.data(ProjectModel.RoleId) for idx in rows if idx.isValid()]


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
        self._show_project(self.repo.get_project(pid))


    def _show_multi_selection(self, count: int) -> None:
        """多选时显示选中数量 + 批量操作面板（task #39 T1）。"""
        splitter_sizes = self._main_splitter_sizes()
        # 清空文件表 & 预览
        self.tbl_files.blockSignals(True)
        self.tbl_files.clear()
        self.tbl_files.blockSignals(False)
        self.preview.show_file(None)

        # 更新预览区显示选中数量 + 批量操作入口
        self.lbl_meta_title.setText(f"已选 {count} 个项目")
        self.lbl_meta_desc.setText("")
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


    def _show_project(self, p: Project | None) -> None:
        splitter_sizes = self._main_splitter_sizes()
        # 清空文件表 & 预览
        self.tbl_files.blockSignals(True)
        self.tbl_files.clear()
        self.tbl_files.blockSignals(False)
        self.preview.show_file(None)
        # 单选/无选中时隐藏批量面板（task #39 T1）
        self._batch_panel.setVisible(False)

        if p is None:
            self.lbl_meta_title.setText("（未选择项目）")
            self.lbl_meta_desc.setText("")
            self.lbl_files_hint.setText("")
            self.statusBar().showMessage("")
            self._restore_main_splitter_sizes(splitter_sizes)
            return

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
        has_generated = any((f.origin or "user") == "generated" for f in files)
        self._btn_origin_filter.setVisible(has_generated)
        origin_filter = self.repo.get_project_setting(p.id, "files_view_origin_filter", "all")
        is_user_only = origin_filter == "user"
        self._btn_origin_filter.setChecked(is_user_only)
        self._btn_origin_filter.setText("👤" if is_user_only else "🌐")
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
        self.tbl_files.blockSignals(False)

        # 应用项目级列偏好（可见性 + 列宽）
        self._apply_files_columns_prefs(p.id)

        # 显示所有文件的数量（不过滤原始数量）
        self.lbl_files_hint.setText(f"共 {all_files_count} 个文件 · 双击说明列可编辑")
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
        if self._current_filter_kind == "mcp":
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

        if is_multi:
            # 多选菜单
            menu.addAction(f"已选 {len(selected_ids)} 个项目")
            menu.addSeparator()
            menu.addAction("✨  LLM 元数据建议…", self.action_llm_suggest_for_project)
            menu.addSeparator()
            menu.addAction("📤  导出选中项目…", self.action_export_project)
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
