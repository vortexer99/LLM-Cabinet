"""项目整批转仓储及右键目标回归：仅临时库、合成文件和 offscreen Qt。"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication, QMenu, QTreeWidgetItem

from selftests._common import T, closing_repos
from app.db import connect
from app.library import Library
from app.models import FileItem, Project
from app.repository import Repository
from app.ui.main_window import MainWindow


def _find_item(win: MainWindow, fid: int) -> QTreeWidgetItem | None:
    """查找实际文件树节点，兼容树形和平铺。"""
    def walk(item: QTreeWidgetItem) -> QTreeWidgetItem | None:
        if item.data(0, Qt.UserRole) == fid:
            return item
        for i in range(item.childCount()):
            if found := walk(item.child(i)):
                return found
        return None

    for i in range(win.tbl_files.topLevelItemCount()):
        if found := walk(win.tbl_files.topLevelItem(i)):
            return found
    return None


def test_project_menu(win: MainWindow, pids: list[int], t: T) -> None:
    """通过真实信号区分列表/网格，执行菜单时再改变当前项目。"""
    target, other = pids
    for mode in ("grid", "list"):
        win._set_view_mode(mode)
        QApplication.processEvents()
        view = win.proj_view if mode == "grid" else win.proj_table
        idx = win.proj_model.index_of_id(target)
        other_idx = win.proj_model.index_of_id(other)
        for selected in (True, False):
            view.selectionModel().select(
                idx if selected else other_idx,
                QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
            )
            view.selectionModel().setCurrentIndex(other_idx, QItemSelectionModel.NoUpdate)
            win._current_project_id = other
            win._show_project(win.repo.get_project(other))
            view.scrollTo(idx)
            QApplication.processEvents()
            actions = []

            def execute(menu: QMenu, *_args) -> None:
                actions.extend(a for a in menu.actions() if "项目内所有链接文件转为仓储" in a.text())
                # 模态菜单中的操作必须固定创建菜单时的右键项目。
                win._current_project_id = other
                win._show_project(win.repo.get_project(other))
                for action in actions:
                    action.trigger()

            def menu_factory(parent) -> QMenu:
                menu = QMenu(parent)
                menu.exec = lambda *_args: execute(menu)
                return menu

            with patch("app.ui.mw_projects.QMenu", side_effect=menu_factory), patch.object(
                win, "action_convert_project_to_storage",
            ) as convert:
                view.customContextMenuRequested.emit(view.visualRect(idx).center())
            label = f"{mode} {'已选' if selected else '未选'}右键项目"
            t.assert_eq(f"{label}包含单个项目转仓储入口", len(actions), 1)
            t.assert_eq(f"{label}固定右键目标", convert.call_args_list, [((target,), {})])


def test_conversion(win: MainWindow, root: Path, pids: list[int], t: T) -> list[int]:
    """真实复制和 DB 写回，过滤/子目录/当前其他项目均不得缩小范围。"""
    target, other = pids
    repo = win.repo
    source = root / "external"
    source.mkdir()
    ids = []
    originals = {}
    expected_content = {}
    for name, subfolder, origin in (
        ("top.txt", "", "user"),
        ("nested.txt", "目录/子目录", "user"),
        ("generated.txt", "衍生/更深", "generated"),
    ):
        path = source / name
        path.write_text(f"合成内容 {name}", encoding="utf-8")
        originals[path] = path.read_bytes()
        ids.append(repo.add_file(FileItem(
            project_id=target, path=str(path), subfolder=subfolder,
            origin=origin, label=name,
        )))
        expected_content[ids[-1]] = originals[path]
    missing_ids = [repo.add_file(FileItem(
        project_id=target, path=str(source / name), label=name,
    )) for name in ("missing1.txt", "missing2.txt")]
    stored_path = win.library.import_copy(target, source / "top.txt")
    stored_id = repo.add_file(FileItem(
        project_id=target, path=stored_path, is_relative=True,
    ))
    other_id = repo.add_file(FileItem(project_id=other, path=str(source / "top.txt")))
    other_before = asdict(repo.get_file(other_id))
    stored_before = asdict(repo.get_file(stored_id))
    repo.set_project_setting(target, "files_view_origin_filter", "user")
    win._current_project_id = target
    win._show_project(repo.get_project(target))
    win.tbl_files.collapseAll()
    top = _find_item(win, ids[0])
    if top is not None:
        win.tbl_files.setCurrentItem(top)
    t.assert_true("generated 文件被当前过滤器隐藏", _find_item(win, ids[2]) is None)
    t.assert_eq("只选一个文件的前置条件", len(win._selected_files()), 1)
    win._current_project_id = other
    win._show_project(repo.get_project(other))

    # 拒绝确认不得写 DB 或产生新仓储文件。
    before = [asdict(f) for f in repo.list_files(target)]
    before_paths = sorted(p.relative_to(win.library.root) for p in win.library.root.rglob("*"))
    with patch("app.ui.mw_files.confirm", return_value=False) as confirm, patch(
        "app.ui.mw_files.run_with_progress",
    ) as runner:
        win.action_convert_project_to_storage(target)
    t.assert_eq("取消确认一次", confirm.call_count, 1)
    t.assert_eq("取消不启动复制任务", runner.call_count, 0)
    t.assert_eq("取消不修改数据库", [asdict(f) for f in repo.list_files(target)], before)
    t.assert_eq("取消不新增仓储文件", sorted(
        p.relative_to(win.library.root) for p in win.library.root.rglob("*")
    ), before_paths)

    def run_sync(_parent, _title, _label, worker, *, on_done, on_error) -> None:
        # 只替换线程调度，执行原 worker 和主线程写回逻辑。
        on_done(worker(lambda *_args: None, lambda: False))

    with patch("app.ui.mw_files.confirm", return_value=True) as confirm, patch(
        "app.ui.mw_files.run_with_progress", side_effect=run_sync,
    ) as runner, patch("app.ui.mw_files.info") as info, patch(
        "app.ui.mw_files.warn",
    ) as warn:
        win.action_convert_project_to_storage(target)
    t.assert_eq("整个项目只启动一个任务", runner.call_count, 1)
    t.assert_eq("整个项目只确认一次", confirm.call_count, 1)
    t.assert_in("确认说明已仓储文件跳过", "已仓储文件 1 个将跳过", confirm.call_args.args[2])
    t.assert_eq("完成和缺失源文件统一汇总一次", info.call_count, 1)
    t.assert_eq("无逐文件警告", warn.call_count, 0)
    summary = info.call_args.args[2]
    t.assert_in("汇总成功数", "已复制到库内：3 个", summary)
    t.assert_in("汇总失败数", "失败/跳过：2 个", summary)
    for fid in missing_ids:
        file = repo.get_file(fid)
        t.assert_true(f"缺失文件{fid}保留链接", not file.is_relative)
        t.assert_in(f"缺失文件{fid}包含在汇总", Path(file.path).name, summary)
    for fid in ids:
        file = repo.get_file(fid)
        t.assert_true(f"文件{fid}链接转相对路径", file.is_relative and not Path(file.path).is_absolute())
        copied = win.library.resolve(file.path, file.is_relative)
        t.assert_eq(f"文件{fid}复制内容一致", copied.read_bytes(), expected_content[fid])
    for path, content in originals.items():
        t.assert_eq(f"外部原文件{path.name}保留", path.read_bytes(), content)
    t.assert_eq("已有仓储条目不变", asdict(repo.get_file(stored_id)), stored_before)
    t.assert_eq("已有仓储文件不覆盖", (win.library.root / stored_path).read_bytes(), originals[source / "top.txt"])
    t.assert_eq("其他项目文件不改", asdict(repo.get_file(other_id)), other_before)
    t.assert_eq("当前项目仍是另一项目", win._current_project_id, other)
    return ids


def test_file_menu_selection(win: MainWindow, pid: int, ids: list[int], t: T) -> None:
    """右键已选项保留多选，未选项清空旧选择并选择右键目标。"""
    win.repo.set_project_setting(pid, "files_view_origin_filter", "all")
    win.repo.set_project_setting(pid, "files_view_mode", "flat")
    win._current_project_id = pid
    win._show_project(win.repo.get_project(pid))
    items = [_find_item(win, fid) for fid in ids]
    t.assert_true("多选回归的三个文件可见", all(item is not None for item in items))
    if not all(item is not None for item in items):
        return
    win.tbl_files.setCurrentItem(items[0], 0, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
    items[1].setSelected(True)
    for index, expected in ((1, set(ids[:2])), (2, {ids[2]})):
        win.tbl_files.scrollToItem(items[index])
        QApplication.processEvents()
        execute = Mock()

        def menu_factory(parent) -> QMenu:
            menu = QMenu(parent)
            menu.exec = execute
            return menu

        with patch("app.ui.mw_files.QMenu", side_effect=menu_factory):
            win._file_context_menu(win.tbl_files.visualItemRect(items[index]).center())
        t.assert_eq(f"文件菜单{index}不弹真实菜单", execute.call_count, 1)
        t.assert_eq(f"文件菜单{index}选择范围", {f.id for f in win._selected_files()}, expected)
        t.assert_eq(f"文件菜单{index}当前项为右键项", win.tbl_files.currentItem(), items[index])


def main() -> bool:
    app = QApplication.instance() or QApplication(["project-storage-selftest"])
    app.setQuitOnLastWindowClosed(False)
    t = T()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        db_path = root / "test.db"
        repo = Repository(connect(db_path))
        with closing_repos(repo):
            pids = [repo.save_project(Project(title=title)) for title in ("目标项目", "其他项目")]
            win = MainWindow(repo, Library(root / "library"), db_path=db_path, library_root=root)
            win.show()
            app.processEvents()
            try:
                test_project_menu(win, pids, t)
                ids = test_conversion(win, root, pids, t)
                test_file_menu_selection(win, pids[0], ids, t)
            finally:
                win._mcp_timer.stop()
                win._search_timer.stop()
                win.close()
                win.deleteLater()
                app.sendPostedEvents(None, QEvent.DeferredDelete)
                app.processEvents()
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
