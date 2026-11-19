"""多选项目批量转仓储回归：显式临时库、合成文件及模拟回收，Qt offscreen。"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QApplication, QMenu

from app.models import FileItem, Project
from app.ui.main_window import MainWindow
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _window
from selftests.gui_project_storage_conversion import _find_item
from selftests.gui_storage_recycle import FakeTrash, _recycle_sync, _run_sync, _summary


def _projects(win: MainWindow) -> list[int]:
    """所有项目均写入调用方显式创建的临时数据库。"""
    ids = [win.repo.save_project(Project(title=title)) for title in ("选中甲", "选中乙", "未选丙")]
    win.refresh_projects()
    QApplication.processEvents()
    return ids


def _source(root: Path, name: str) -> Path:
    path = root / name
    path.write_text(f"批量转仓储合成内容：{name}", encoding="utf-8")
    return path


def test_menu(t: T, root: Path) -> None:
    """通过真实右键信号覆盖网格/列表，并在菜单打开后改变选择。"""
    root.mkdir()
    win, repo = _window(root)
    try:
        first, second, other = _projects(win)
        for mode in ("grid", "list"):
            win._set_view_mode(mode)
            QApplication.processEvents()
            view = win.proj_view if mode == "grid" else win.proj_table
            indices = [win.proj_model.index_of_id(pid) for pid in (first, second, other)]
            for clicked, expected in ((0, {first, second}), (2, {other})):
                selection = view.selectionModel()
                selection.select(indices[0], QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
                selection.select(indices[1], QItemSelectionModel.Select | QItemSelectionModel.Rows)
                selection.setCurrentIndex(indices[1], QItemSelectionModel.NoUpdate)
                view.scrollTo(indices[clicked])
                QApplication.processEvents()
                captured = []

                def menu_factory(parent) -> QMenu:
                    menu = QMenu(parent)

                    def execute(*_args) -> None:
                        actions = [action for action in menu.actions() if "项目内所有链接文件转为仓储" in action.text()]
                        captured.extend(actions)
                        t.assert_eq(f"{mode}/{clicked}打开菜单时保留目标选择", set(win._selected_project_ids()), expected)
                        # 模态菜单存活期间选择和当前项目都改变，动作仍需使用打开时的 ID。
                        selection.select(indices[2], QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
                        win._current_project_id = other
                        win._show_project(repo.get_project(other))
                        for action in actions:
                            action.trigger()

                    menu.exec = execute
                    return menu

                with patch("app.ui.mw_projects.QMenu", side_effect=menu_factory), patch.object(
                    win, "action_convert_projects_to_storage",
                ) as batch, patch.object(win, "action_convert_project_to_storage") as single:
                    view.customContextMenuRequested.emit(view.visualRect(indices[clicked]).center())
                label = f"{mode}/{clicked}"
                t.assert_eq(f"{label}转换菜单仅一个", len(captured), 1)
                if clicked == 0:
                    t.assert_eq(f"{label}批量动作仅调用一次", batch.call_count, 1)
                    t.assert_eq(f"{label}不调用单项目动作", single.call_count, 0)
                    if batch.call_args is not None:
                        t.assert_eq(f"{label}批量ID固定为原多选", set(batch.call_args.args[0]), {first, second})
                        t.assert_eq(f"{label}批量ID无重复", len(batch.call_args.args[0]), 2)
                    if captured:
                        t.assert_in(f"{label}多选菜单文案", "所选项目", captured[0].text())
                else:
                    t.assert_eq(f"{label}右键未选项走单项目", single.call_args_list, [((other,), {})])
                    t.assert_eq(f"{label}右键未选项不走批量", batch.call_count, 0)
    finally:
        _close_window(win)
        repo.conn.close()


def test_conversion(t: T, root: Path) -> None:
    """真实复制/落库覆盖过滤隐藏、深层文件、重复和不存在 ID、缺失源与取消。"""
    root.mkdir()
    win, repo = _window(root)
    try:
        first, second, other = _projects(win)
        ids = []
        originals = {}
        expected_content = {}
        for pid, name, subfolder, origin in (
            (first, "visible.txt", "", "user"),
            (first, "hidden.txt", "深层/子目录", "generated"),
            (second, "second.txt", "另一层/深层", "user"),
        ):
            source = _source(root, name)
            originals[source] = source.read_bytes()
            ids.append(repo.add_file(FileItem(project_id=pid, path=str(source), subfolder=subfolder, origin=origin)))
            expected_content[ids[-1]] = originals[source]
        missing = root / "missing.txt"
        missing_id = repo.add_file(FileItem(project_id=second, path=str(missing)))
        stored = win.library.import_copy(first, root / "visible.txt")
        stored_id = repo.add_file(FileItem(project_id=first, path=stored, is_relative=True))
        other_id = repo.add_file(FileItem(project_id=other, path=str(root / "visible.txt")))
        untouched = {fid: asdict(repo.get_file(fid)) for fid in (stored_id, other_id)}
        repo.set_project_setting(first, "files_view_origin_filter", "user")
        win._current_project_id = first
        win._show_project(repo.get_project(first))
        visible = _find_item(win, ids[0])
        t.assert_true("衍生文件确实被当前过滤隐藏", _find_item(win, ids[1]) is None)
        if visible is not None:
            win.tbl_files.setCurrentItem(visible)
        t.assert_eq("当前仅选中一个文件", [file.id for file in win._selected_files()], [ids[0]])
        win._current_project_id = other
        win._show_project(repo.get_project(other))
        targets = [first, second, first, 999999]
        before = {pid: [asdict(file) for file in repo.list_files(pid)] for pid in (first, second, other)}
        before_paths = sorted(win.library.root.rglob("*"))
        with patch("app.ui.mw_files.confirm", return_value=False) as confirm, patch(
            "app.ui.mw_files.run_with_progress",
        ) as runner:
            win.action_convert_projects_to_storage(targets)
        t.assert_eq("批量取消只确认一次", confirm.call_count, 1)
        t.assert_eq("批量取消不执行任务", runner.call_count, 0)
        t.assert_eq("批量取消所有项目记录不变", {
            pid: [asdict(file) for file in repo.list_files(pid)] for pid in before
        }, before)
        t.assert_eq("批量取消不产生副本", sorted(win.library.root.rglob("*")), before_paths)

        with patch("app.ui.mw_files.confirm", return_value=True) as confirm, patch(
            "app.ui.mw_files.run_with_progress", side_effect=_run_sync,
        ) as runner, patch.object(win.library, "import_copy", wraps=win.library.import_copy) as copy, patch(
            "app.ui.mw_files.info",
        ) as info, patch("app.ui.mw_files.warn") as warn:
            win.action_convert_projects_to_storage(targets)
        t.assert_eq("多个项目只确认一次", confirm.call_count, 1)
        t.assert_eq("多个项目只执行一个复制任务", runner.call_count, 1)
        t.assert_eq("ID去重后仅复制三个有效文件", copy.call_count, 3)
        t.assert_eq("部分失败只汇总一次", info.call_count + warn.call_count, 1)
        t.assert_in("确认有效项目数不含重复和不存在ID", "所选 2 个项目", confirm.call_args.args[2])
        t.assert_in("确认含全部链接数量", "4 个", confirm.call_args.args[2])
        t.assert_in("确认跳过已有仓储", "已仓储文件 1 个将跳过", confirm.call_args.args[2])
        t.assert_in("汇总三个成功", "已复制到库内：3 个", _summary(info, warn))
        t.assert_in("缺失源只失败一项", "失败/跳过：1 个", _summary(info, warn))
        t.assert_in("缺失源名出现在汇总", missing.name, _summary(info, warn))
        for fid in ids:
            file = repo.get_file(fid)
            t.assert_true(f"文件{fid}已转相对仓储路径", file.is_relative and not Path(file.path).is_absolute())
            t.assert_eq(f"文件{fid}仓储副本内容一致", win.library.resolve(file.path, True).read_bytes(), expected_content[fid])
        t.assert_eq("缺失源仍为原链接", repo.get_file(missing_id).path, str(missing))
        t.assert_eq("缺失源仍为链接模式", repo.get_file(missing_id).is_relative, False)
        for fid in untouched:
            t.assert_eq(f"未处理文件{fid}记录不变", asdict(repo.get_file(fid)), untouched[fid])
        for source, content in originals.items():
            t.assert_eq(f"默认不回收{source.name}", source.read_bytes(), content)
        t.assert_eq("批量不切换当前项目", win._current_project_id, other)

        with patch.object(win, "_convert_files_to_storage") as convert:
            win.action_convert_projects_to_storage([999999, 999999])
            win.action_convert_projects_to_storage([])
        t.assert_eq("全不存在或空ID不启动转换", convert.call_count, 0)
    finally:
        _close_window(win)
        repo.conn.close()


def test_recycle(t: T, root: Path, trash: FakeTrash) -> None:
    """跨项目共享源整批提交后回收一次，未选项目保有链接时必须保留。"""
    root.mkdir()
    win, repo = _window(root)
    try:
        first, second, other = _projects(win)
        repo.set_setting("storage_recycle_source", "1")
        shared = _source(root, "selected-shared.txt")
        retained = _source(root, "unselected-shared.txt")
        contents = {shared: shared.read_bytes(), retained: retained.read_bytes()}
        ids = [repo.add_file(FileItem(project_id=pid, path=str(path), subfolder=str(index)))
               for index, (pid, path) in enumerate(((first, shared), (second, shared), (first, retained), (second, retained)))]
        other_id = repo.add_file(FileItem(project_id=other, path=str(retained)))
        other_before = asdict(repo.get_file(other_id))
        observed = []

        def before_recycle(path: Path) -> None:
            observed.append(path)
            t.assert_eq("回收前数据库已提交", repo.conn.in_transaction, False)
            t.assert_true("回收前两个项目全部成功记录已更新", all(repo.get_file(fid).is_relative for fid in ids))
            for fid in ids:
                file = repo.get_file(fid)
                t.assert_true(f"回收前文件{fid}副本存在", win.library.resolve(file.path, True).exists())

        trash.before_move = before_recycle
        count = len(trash.paths)
        with patch("app.ui.mw_files.confirm", return_value=True) as confirm, patch(
            "app.ui.mw_files.run_with_progress", side_effect=_run_sync,
        ) as runner, patch("app.ui.mw_files.recycle_sources_with_progress", side_effect=_recycle_sync) as recycle, patch(
            "app.ui.mw_files.info",
        ) as info, patch("app.ui.mw_files.warn") as warn:
            win.action_convert_projects_to_storage([first, second, first])
        trash.before_move = None
        t.assert_eq("启用回收仍只确认一次", confirm.call_count, 1)
        t.assert_eq("启用回收仍只执行一个复制任务", runner.call_count, 1)
        t.assert_eq("多个项目只执行一个回收阶段", recycle.call_count, 1)
        t.assert_eq("所有四项在同一批回收候选中", len(recycle.call_args.args[3]), 4)
        t.assert_eq("共享源只模拟回收一次", trash.paths[count:], [shared.resolve()])
        t.assert_eq("回收时确实执行了落库检查", observed, [shared.resolve()])
        t.assert_true("已回收源不再存在", not shared.exists())
        t.assert_eq("未选项目仍引用时源内容不变", retained.read_bytes(), contents[retained])
        t.assert_eq("未选项目记录不变", asdict(repo.get_file(other_id)), other_before)
        t.assert_eq("回收结果仅一个汇总", info.call_count + warn.call_count, 1)
        t.assert_in("所有项目成功数合并汇总", "已复制到库内：4 个", _summary(info, warn))
        t.assert_in("汇总说明未选项目共享源保留", retained.name, _summary(info, warn))
        t.assert_in("汇总说明链接引用", "链接", _summary(info, warn))
        for fid in ids:
            file = repo.get_file(fid)
            expected = contents[shared if fid in ids[:2] else retained]
            t.assert_eq(f"回收后文件{fid}副本内容完整", win.library.resolve(file.path, True).read_bytes(), expected)
    finally:
        trash.before_move = None
        _close_window(win)
        repo.conn.close()


def main() -> bool:
    _app()
    t = T()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        trash = FakeTrash(root)
        # 覆盖所有可能的回收入口；任何测试都绝不调用真实系统回收站。
        with patch("app.storage_cleanup.move_to_trash", side_effect=trash), patch(
            "app.ui.mw_files.move_to_trash", side_effect=trash,
        ), patch("app.utils.move_to_trash", side_effect=trash):
            test_menu(t, root / "menu")
            test_conversion(t, root / "conversion")
            test_recycle(t, root / "recycle", trash)
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
