"""拖放导入：DropZone 显隐、拖放编排、批量文件夹导入（task #35：从 main_window.py 拆分，方法体未改动）。

Mixin：拖放导入：DropZone 显隐、拖放编排、批量文件夹导入
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


class DnDMixin:
    """拖放导入：DropZone 显隐、拖放编排、批量文件夹导入"""

    def _install_dnd(self) -> None:
        """启用拖放：

        - 卡片视图、文件表通过 ProjectViewDnD/FilesTableDnD helper 处理。
        - DropZone 显示/隐藏由 QApplication 全局事件过滤器控制。
        """
        from PySide6.QtWidgets import QApplication

        # 主窗口本身也接受 drop（兜底）
        self.setAcceptDrops(True)

        QApplication.instance().installEventFilter(self)

        # DropZone 信号
        self.drop_zone.dropped.connect(self._on_dropzone_dropped)

        # 拖动时高亮项目卡片
        self._drag_hover_pid: int | None = None
        # 防抖：防止单次 drop 触发多次新建对话框
        self._drop_busy: bool = False


    def eventFilter(self, obj, ev):  # noqa: D401
        """全局监听 drag 进出，控制 DropZone 显隐（task #41 T4：仅拖放事件）。"""
        et = ev.type()
        if et == QEvent.DragEnter:
            md = ev.mimeData() if hasattr(ev, "mimeData") else None
            if md and md.hasUrls():
                self._show_drop_zone()
        elif et == QEvent.Drop:
            # 任何位置完成 drop 都隐藏
            self._hide_drop_zone()
        elif et == QEvent.DragLeave:
            # 离开顶层窗口时隐藏（子控件之间切换不会触发顶层 leave）
            if obj is self:
                self._hide_drop_zone()
        return super().eventFilter(obj, ev)


    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            self._show_drop_zone()


    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()


    def dragLeaveEvent(self, _ev):
        self._hide_drop_zone()


    def dropEvent(self, ev):
        # 落到主窗口空白区（详情面板/工具栏等）：不做处理，仅隐藏
        self._hide_drop_zone()
        ev.ignore()


    def _show_drop_zone(self) -> None:
        if not self.drop_zone.isVisible():
            self.drop_zone.show()


    def _hide_drop_zone(self) -> None:
        self.drop_zone.set_active(False)
        self.drop_zone.hide()
        self._set_drag_hover(None)


    def _filter_library_paths(self, paths: list) -> list:
        """过滤掉库目录自身的路径，防止递归导入。返回过滤后的路径列表。"""
        lib_root = self.library.root.resolve()
        filtered: list[str] = []
        skipped = 0
        for raw in paths:
            p = Path(raw).resolve()
            try:
                p.relative_to(lib_root)
                skipped += 1  # 在库目录内，跳过
            except ValueError:
                filtered.append(str(p))
        if skipped:
            warn(
                self, "提示",
                f"已跳过 {skipped} 个位于库目录内的路径（不能导入库自身）。"
            )
        return filtered


    def _warn_if_deep_or_large(self, files: list) -> bool:
        """检查待导入文件是否过深或过多，弹确认对话框。返回 True 表示继续。"""
        from ..models import PendingFile
        max_depth = 0
        for f in files:
            sf = f.subfolder if isinstance(f, PendingFile) else ""
            if sf:
                depth = sf.count("/") + 1
                max_depth = max(max_depth, depth)

        count = len(files)
        warnings: list[str] = []
        if max_depth >= 5:
            warnings.append(f"目录层级较深（最深 {max_depth} 层）")
        if count >= 500:
            warnings.append(f"文件数量较多（共 {count} 个）")

        if not warnings:
            return True

        return confirm(
            self, "导入确认",
            "检测到以下情况，是否继续导入？",
            informative="\n".join(f"• {w}" for w in warnings),
            yes="继续导入", default_yes=True,
        )


    def _on_dropped_on_project(self, pid: int, paths: list) -> None:
        if self._drop_busy:
            return
        self._drop_busy = True
        try:
            paths = self._filter_library_paths(paths)
            if not paths:
                return
            files = self._expand_paths(paths)
            if not files:
                self._warn_empty_import(paths)
                return
            self._drop_into_project(pid, files)
            self._hide_drop_zone()
        finally:
            self._drop_busy = False


    def _on_dropped_on_files_table(self, paths: list) -> None:
        if self._drop_busy:
            return
        self._drop_busy = True
        try:
            paths = self._filter_library_paths(paths)
            if not paths:
                return
            files = self._expand_paths(paths)
            if not files:
                if len(paths) == 1 and Path(paths[0]).is_dir():
                    self._create_empty_project(Path(paths[0]).name)
                else:
                    self._warn_empty_import(paths)
                return
            if not self._warn_if_deep_or_large(files):
                return
            if self._current_project_id is not None:
                self._drop_into_project(self._current_project_id, files)
            else:
                self._drop_create_project(files, source_paths=paths)
            self._hide_drop_zone()
        finally:
            self._drop_busy = False


    def _on_dropzone_dropped(self, paths: list) -> None:
        if self._drop_busy:
            return
        self._drop_busy = True
        try:
            self._hide_drop_zone()
            paths = self._filter_library_paths(paths)
            if not paths:
                return

            # task #28 T3：检测 ZIP 文件 → 走项目包导入
            zip_paths = [p for p in paths if Path(p).suffix.lower() == ".zip"]
            if zip_paths:
                # 有 ZIP 文件，弹确认让用户选择处理方式
                from PySide6.QtWidgets import QMessageBox
                ans = ask_yes_no_cancel(
                    self, "检测到 ZIP 文件",
                    f"检测到 {len(zip_paths)} 个 ZIP 文件。\n\n"
                    "是导入为项目包，还是解压后作为普通文件夹处理？",
                    yes="导入项目包", no="解压为文件夹",
                )
                if ans == "yes":  # 导入项目包
                    self._run_batch_folder_import(zip_paths)
                    return
                elif ans == "cancel":
                    return
                # 否则继续作为文件夹处理（解压后）

            # 分支：全是目录且 ≥ 2 个 → 走批量文件夹导入流程（task #10）
            from ..importer import split_paths_by_kind
            dirs, plain_files = split_paths_by_kind(paths)
            if dirs and not plain_files and len(dirs) >= 2:
                self._handle_multi_folder_drop(dirs)
                return
            # 否则沿用旧路径
            files = self._expand_paths(paths)
            if not files:
                # 空文件夹处理：单个空目录 → 创建 0 文件项目；否则提示
                if len(paths) == 1 and Path(paths[0]).is_dir():
                    self._create_empty_project(Path(paths[0]).name)
                else:
                    self._warn_empty_import(paths)
                return
            if not self._warn_if_deep_or_large(files):
                return
            self._drop_create_project(files, source_paths=paths)
        finally:
            self._drop_busy = False


    def _create_empty_project(self, title: str) -> None:
        """创建一个 0 文件的项目（用户拖入空文件夹时）。"""
        p = Project(title=title)
        self.repo.save_project(p)
        self.refresh_projects()
        self.statusBar().showMessage(f"已创建空项目「{title}」", 4000)


    def _warn_empty_import(self, paths: list) -> None:
        """所有路径展开后为空时提示用户。"""
        from ..importer import split_paths_by_kind
        dirs, _ = split_paths_by_kind(paths)
        if dirs:
            info(
                self, "提示",
                f"拖入的 {len(dirs)} 个文件夹都是空的，没有文件可导入。"
            )
        else:
            info(self, "提示", "没有文件可导入。")


    def _handle_multi_folder_drop(self, dirs: list) -> None:
        """≥ 2 个目录拖到 DropZone：先问"单/多项目"，再走批量导入。"""
        from .folder_drop_mode_dialog import FolderDropModeDialog
        mode_dlg = FolderDropModeDialog(len(dirs), parent=self)
        if mode_dlg.exec() != QDialog.Accepted:
            return  # 用户取消

        if mode_dlg.mode() == "merge":
            # 合并为一个新项目：沿用旧路径（_drop_create_project）
            files = self._expand_paths([str(d) for d in dirs])
            if not files:
                self._warn_empty_import([str(d) for d in dirs])
                return
            if not self._warn_if_deep_or_large(files):
                return
            self._drop_create_project(files, source_paths=[str(d) for d in dirs])
            return

        # separate 模式：批量导入
        self._run_batch_folder_import(dirs)


    def _run_batch_folder_import(self, dirs: list) -> None:
        """对每个文件夹独立建项目（task #10 主流程；task #36 线程化）。

        流程：阶段 1（主线程：元数据 + 字段策略 + 建项目行）→
        阶段 2（worker：收集 + 复制文件）→ 阶段 3（主线程：写 files 行）。
        中途取消时，从未进入复制阶段的项目会删掉刚建的空项目行，
        保持与旧版"未导入"一致的语义。
        """
        from pathlib import Path as _Path
        from ..importer import (
            cleanup_extracted_zips,
            copy_files_for_import,
            prepare_project_from_plan,
            scan_folders,
            write_import_file_rows,
        )
        from .import_dialog import FieldPolicyAskDialog, ImportDialog

        plans = scan_folders([_Path(d) for d in dirs], self.repo)
        dlg = ImportDialog(plans, parent=self)
        if dlg.exec() != QDialog.Accepted:
            cleanup_extracted_zips(plans)
            return
        options = dlg.options()

        # 单项目级未匹配字段询问回调
        def _ask_field_policy(folder, fields):
            ask = FieldPolicyAskDialog(folder, fields, parent=self)
            if ask.exec() != QDialog.Accepted:
                return options.field_policy  # 用户取消 → 回退默认
            return ask.policy()

        # ---- 阶段 1（主线程）：元数据 + 字段策略 + 建项目行 ----
        prepared_projects: list = []  # (plan, project, warnings)
        all_warnings: list[str] = []
        for plan in plans:
            w: list[str] = []
            try:
                project = prepare_project_from_plan(
                    self.repo, plan, options, w, _ask_field_policy,
                )
                pid = self.repo.save_project(project)
                project.id = pid
                prepared_projects.append((plan, project, w))
            except Exception as e:
                all_warnings.append(f"[{plan.folder.name}] 导入失败：{e}")

        if not prepared_projects:
            cleanup_extracted_zips(plans)
            if all_warnings:
                warn(self, "批量导入失败", "所有项目都未能导入：",
                     detailed="\n".join(all_warnings))
            return

        n_plans = len(prepared_projects)

        # ---- 阶段 2（worker）：收集 + 复制文件 ----
        def _do(progress_cb, is_cancelled):
            results: dict = {}  # pid -> (prepared, warnings)
            for i, (plan, project, _w) in enumerate(prepared_projects):
                if is_cancelled():
                    break
                progress_cb(i, n_plans, f"导入「{plan.folder.name}」…")
                pw: list[str] = []
                prep = copy_files_for_import(
                    self.library, project.id, plan, options, pw,
                    is_cancelled=is_cancelled,
                )
                results[project.id] = (prep, pw)
            progress_cb(n_plans, n_plans, "")
            return {"results": results, "cancelled": is_cancelled()}

        # ---- 阶段 3（主线程）：写 files 行 + 收尾 ----
        def _on_done(payload):
            try:
                results = payload["results"]
                n_ok = 0
                n_files_total = 0
                last_pid: int | None = None
                for plan, project, w in prepared_projects:
                    all_warnings.extend(f"[{plan.folder.name}] {x}" for x in w)
                    if project.id in results:
                        prep, pw = results[project.id]
                        n = write_import_file_rows(
                            self.repo, project, plan, prep, pw,
                        )
                        all_warnings.extend(f"[{plan.folder.name}] {x}" for x in pw)
                        n_ok += 1
                        n_files_total += n
                        last_pid = project.id
                    else:
                        # 取消时没轮到的项目：删掉刚建的空项目行（= 未导入）
                        self.repo.delete_project(project.id)

                self.refresh_projects()
                if last_pid is not None:
                    self._select_project_by_id(last_pid)

                msg = (
                    f"批量导入完成：{n_ok} / {len(plans)} 个项目，"
                    f"共 {n_files_total} 个文件"
                )
                if payload["cancelled"]:
                    msg = "批量导入已取消（" + msg + "）"
                self.statusBar().showMessage(msg, 6000)

                if all_warnings:
                    head = msg + f"\n\n{len(all_warnings)} 条提示信息："
                    info(self, "批量导入：警告与提示", head,
                         detailed="\n".join(all_warnings))
            finally:
                cleanup_extracted_zips(plans)

        def _on_error(msg):
            # worker 异常：已建的项目行是空壳，删掉保持"未导入"语义
            try:
                for _plan, project, _w in prepared_projects:
                    if not self.repo.list_files(project.id):
                        self.repo.delete_project(project.id)
                self.refresh_projects()
            finally:
                cleanup_extracted_zips(plans)
            error(self, "批量导入失败", msg)

        run_with_progress(
            self, "批量导入文件夹", "正在导入项目…", _do,
            on_done=_on_done,
            on_error=_on_error,
        )


    def _on_drag_hover_changed(self, pid) -> None:
        self._set_drag_hover(pid)


    def _set_drag_hover(self, pid) -> None:
        if pid == self._drag_hover_pid:
            return
        self._drag_hover_pid = pid
        if pid is not None:
            idx = self.proj_model.index_of_id(int(pid))
            if idx.isValid():
                self.proj_view.setCurrentIndex(idx)
        self.proj_view.viewport().update()


    def _expand_paths(self, paths: list) -> list:
        """把混合路径展开为 [PendingFile, ...]。

        - 文件 → PendingFile(src=绝对路径, subfolder="")
        - 目录 → 递归收集所有文件，目录名作为 subfolder 前缀
          例：拖入 myfolder/sub/a.txt → subfolder="myfolder/sub"
        - import_ignore_dotfiles 设置为 "1" 时，跳过 . 开头的文件和目录
        """
        from ..models import PendingFile
        ignore_dot = self.repo.get_setting("import_ignore_dotfiles", "1") == "1"
        out: list[PendingFile] = []
        for raw in paths:
            p = Path(raw)
            if p.is_file():
                if ignore_dot and p.name.startswith("."):
                    continue
                out.append(PendingFile(src=p.resolve(), subfolder=""))
            elif p.is_dir():
                root = p.resolve()
                dir_name = root.name
                for sub in sorted(root.rglob("*")):
                    if ignore_dot and any(
                        part.startswith(".") for part in sub.relative_to(root).parts
                    ):
                        continue
                    if sub.is_file():
                        rel = sub.parent.relative_to(root)
                        if str(rel) == ".":
                            subfolder = dir_name
                        else:
                            subfolder = f"{dir_name}/{rel.as_posix()}"
                        out.append(PendingFile(src=sub.resolve(), subfolder=subfolder))
        return out


    def _drop_into_project(self, pid: int, files: list) -> None:
        if not files:
            return
        p = self.repo.get_project(pid)
        if not p:
            return
        # 拖放也走"添加文件"对话框，统一存储方式询问语义
        default_mode = self.repo.get_setting("default_storage_mode", "link") or "link"
        storage, _label = self._ask_storage_for_import(files, default_mode)
        if storage is None:
            return  # 用户取消
        added = self._import_files(p, files, ask_label=False, storage=storage)
        self._select_project_by_id(pid)
        self._show_project(self.repo.get_project(pid))
        self.statusBar().showMessage(
            f"已加入「{p.title}」：{added} / {len(files)} 个文件", 4000
        )


    def _drop_create_project(
        self, files: list, source_paths: list | None = None,
    ) -> None:
        from ..models import PendingFile
        if not files:
            return
        # 默认标题：
        # 1) 拖入的原始路径里只要有文件夹，就用第一个文件夹的名字（按目录组织时更直观）
        # 2) 否则用第一个文件的 stem
        default_title = ""
        if source_paths:
            for raw in source_paths:
                p = Path(raw)
                if p.is_dir():
                    default_title = p.name
                    break
        if not default_title:
            first = files[0]
            default_title = first.src.stem if isinstance(first, PendingFile) else Path(first).stem
        title, ok = QInputDialog.getText(
            self, "新建项目",
            f"将 {len(files)} 个文件加入新项目。请输入项目标题：",
            text=default_title,
        )
        if not ok:
            return
        title = title.strip() or default_title
        default_mode = self.repo.get_setting("default_storage_mode", "link") or "link"
        storage, _label = self._ask_storage_for_import(files, default_mode)
        if storage is None:
            return  # 用户取消（不创建项目）
        p = Project(title=title)
        pid = self.repo.save_project(p)
        p.id = pid
        added = self._import_files(p, files, ask_label=False, storage=storage)
        self.refresh_projects()
        self._select_project_by_id(pid)
        self.statusBar().showMessage(
            f"新建项目「{title}」并加入 {added} / {len(files)} 个文件", 4000
        )
