"""仓储原文件回收 GUI 回归：临时库、合成文件、模拟回收目录和 offscreen Qt。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QDialog, QLabel

from app.db import connect
from app.importer import ImportOptions
from app.models import FileItem, Project
from app.repository import Repository
from app.storage_cleanup import capture_storage_source, cleanup_storage_sources
from app.ui.import_dialog import ImportDialog
from app.ui.settings import SettingsDialog
from app.ui.storage_cleanup import recycle_sources_with_progress
from app.ui.workers import FileOpWorker
from selftests._common import T
from selftests.gui_library_media_settings import _close_widget
from selftests.gui_main_window_regressions import _app, _close_window, _window


KEY = "storage_recycle_source"


class FakeTrash:
    """只移动本次合成文件；所有回收调用均禁止到达系统回收站。"""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.destination = self.root / "fake-trash"
        self.destination.mkdir()
        self.paths: list[Path] = []
        self.before_move = None

    def __call__(self, path: str | Path, *, allow_delete: bool = True) -> str:
        source = Path(path).resolve()
        if not source.is_relative_to(self.root) or source.is_relative_to(self.destination):
            raise AssertionError(f"测试禁止处理临时目录之外的文件：{source}")
        if allow_delete:
            raise AssertionError("仓储原文件回收禁止硬删除回退")
        if self.before_move is not None:
            self.before_move(source)
        shutil.move(str(source), str(self.destination / f"{len(self.paths)}-{source.name}"))
        self.paths.append(source)
        return "trash"


def _source(root: Path, name: str) -> Path:
    """构造完全独立的源文件，避免复用仓储副本作源。"""
    path = root / name
    path.write_text(f"合成测试内容：{name}", encoding="utf-8")
    return path


def _run_sync(_parent, _title, _label, worker, *, on_done, on_error) -> None:
    """只替换线程调度，仍执行真实复制及主线程落库回调。"""
    on_done(worker(lambda *_args: None, lambda: False))


def _recycle_sync(_parent, repo, library, candidates) -> list[str]:
    """大部分 GUI 用例固定线程调度，便于回收前直接断言主线程数据库状态。"""
    return cleanup_storage_sources(repo, library, candidates)


def _summary(*dialogs) -> str:
    """收集一次汇总的正文及详细信息，避免依赖展示渠道的具体选择。"""
    return "\n".join(
        str(value)
        for dialog in dialogs
        for invocation in dialog.call_args_list
        for value in (*invocation.args[1:], *invocation.kwargs.values())
    )


def test_settings(t: T, root: Path) -> None:
    """两个真实临时库分别保存设置，并通过新连接验证持久化。"""
    for index in range(2):
        library_root = root / f"library-{index}"
        library_root.mkdir(parents=True)
        db_path = library_root / "test.db"
        repo = Repository(connect(db_path))
        try:
            dialog = SettingsDialog(repo, library_root, db_path)
            try:
                checkbox = dialog.chk_storage_recycle_source
                t.assert_eq(f"库{index}默认关闭", checkbox.isChecked(), False)
                t.assert_eq(f"库{index}缺省设置关闭", repo.get_setting(KEY, "0"), "0")
                if index == 0:
                    checkbox.click()
                    t.assert_eq("设置点击立即保存开启", repo.get_setting(KEY, "0"), "1")
                    checkbox.click()
                    t.assert_eq("设置点击立即保存关闭", repo.get_setting(KEY, "1"), "0")
                    checkbox.click()
            finally:
                _close_widget(dialog)
        finally:
            repo.conn.close()

    for index in range(2):
        library_root = root / f"library-{index}"
        db_path = library_root / "test.db"
        repo = Repository(connect(db_path))
        try:
            dialog = SettingsDialog(repo, library_root, db_path)
            try:
                t.assert_eq(f"重开库{index}开关持久化且库隔离", dialog.chk_storage_recycle_source.isChecked(), index == 0)
            finally:
                _close_widget(dialog)
        finally:
            repo.conn.close()

    dialog = ImportDialog([], recycle_source=True)
    try:
        text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        t.assert_in("文件夹导入说明回收后果", "回收站", text)
        t.assert_in("文件夹导入说明保留目录", "目录", text)
    finally:
        _close_widget(dialog)


def test_add_files(t: T, root: Path, trash: FakeTrash) -> None:
    """真实添加文件入口覆盖默认关闭、链接、开启仓储和取消。"""
    root.mkdir()
    win, repo = _window(root)
    try:
        pid = repo.save_project(Project(title="添加文件回收"))
        win._current_project_id = pid
        for enabled, mode, name in (
            (False, "copy", "default-copy.txt"),
            (True, "link", "enabled-link.txt"),
            (True, "copy", "enabled-copy.txt"),
        ):
            repo.set_setting(KEY, "1" if enabled else "0")
            source = _source(root, name)
            content = source.read_bytes()
            previous = len(repo.list_files(pid))
            trash_count = len(trash.paths)

            def check_committed(path: Path) -> None:
                rows = repo.list_files(pid)
                t.assert_eq("回收前数据库已提交", repo.conn.in_transaction, False)
                t.assert_eq("回收前新增文件记录已入库", len(rows), previous + 1)
                stored = rows[-1]
                t.assert_true("回收前记录使用仓储路径", stored.is_relative)
                t.assert_eq("回收前仓储内容完整", win.library.resolve(stored.path, True).read_bytes(), content)

            trash.before_move = check_committed
            with patch("app.ui.mw_files.QFileDialog.getOpenFileNames", return_value=([str(source)], "")), patch.object(
                win, "_ask_storage_for_import", return_value=(mode, None),
            ), patch("app.ui.mw_files.warn") as warn:
                win.action_add_files()
            trash.before_move = None
            expected_recycle = enabled and mode == "copy"
            t.assert_eq(f"{name}真实添加记录", len(repo.list_files(pid)), previous + 1)
            t.assert_eq(f"{name}原文件状态", source.exists(), not expected_recycle)
            t.assert_eq(f"{name}模拟回收调用次数", len(trash.paths) - trash_count, int(expected_recycle))
            t.assert_eq(f"{name}成功不警告", warn.call_count, 0)

        source = _source(root, "cancel-add.txt")
        previous = len(repo.list_files(pid))
        with patch("app.ui.mw_files.QFileDialog.getOpenFileNames", return_value=([str(source)], "")), patch.object(
            win, "_ask_storage_for_import", return_value=(None, None),
        ), patch.object(win.library, "import_copy") as copy:
            win.action_add_files()
        t.assert_true("取消添加保留源文件", source.exists())
        t.assert_eq("取消添加不复制", copy.call_count, 0)
        t.assert_eq("取消添加不落库", len(repo.list_files(pid)), previous)
    finally:
        trash.before_move = None
        _close_window(win)
        repo.conn.close()


def test_batch_failures(t: T, root: Path, trash: FakeTrash) -> None:
    """批量复制或入库失败保留对应源文件，成功项目仍正常计数和回收。"""
    root.mkdir()
    win, repo = _window(root)
    try:
        pid = repo.save_project(Project(title="批量部分失败"))
        project = repo.get_project(pid)
        repo.set_setting(KEY, "1")
        paths = [_source(root, name) for name in ("success.txt", "copy-fail.txt", "db-fail.txt")]
        original_copy = win.library.import_copy
        original_add = repo.add_file

        def copy(project_id, source):
            if Path(source).name == "copy-fail.txt":
                raise OSError("模拟复制失败")
            return original_copy(project_id, source)

        def add(file):
            if Path(file.path).name == "db-fail.txt":
                raise RuntimeError("模拟入库失败")
            return original_add(file)

        count = len(trash.paths)
        with patch.object(win.library, "import_copy", side_effect=copy), patch.object(
            repo, "add_file", side_effect=add,
        ), patch("app.ui.mw_files.warn") as warn:
            added = win._import_files(project, paths, storage="copy")
        t.assert_eq("批量部分失败成功数准确", added, 1)
        t.assert_eq("批量部分失败仅成功项落库", len(repo.list_files(pid)), 1)
        t.assert_eq("批量部分失败仅成功项回收", trash.paths[count:], [paths[0].resolve()])
        t.assert_true("批量复制失败保留源", paths[1].exists())
        t.assert_true("批量入库失败保留源", paths[2].exists())
        t.assert_eq("批量失败仅一次结果框", warn.call_count, 1)
        t.assert_in("批量结果成功失败计数", "成功 1 个，失败 2 个", _summary(warn))
        t.assert_in("批量结果含复制错误", "模拟复制失败", _summary(warn))
        t.assert_in("批量结果含入库错误", "模拟入库失败", _summary(warn))
    finally:
        _close_window(win)
        repo.conn.close()


def test_conversion(t: T, root: Path, trash: FakeTrash) -> None:
    """整项目转换先更新全部记录再回收；共享源与回收异常以一次结果汇总。"""
    root.mkdir()
    win, repo = _window(root)
    try:
        pid = repo.save_project(Project(title="整项目转仓储"))
        other = repo.save_project(Project(title="其他链接项目"))
        repo.set_setting(KEY, "1")
        source = _source(root, "duplicate.txt")
        shared = _source(root, "shared.txt")
        duplicate_ids = [repo.add_file(FileItem(
            project_id=pid, path=str(source), subfolder=subfolder,
        )) for subfolder in ("", "子目录")]
        shared_id = repo.add_file(FileItem(project_id=pid, path=str(shared)))
        other_id = repo.add_file(FileItem(project_id=other, path=str(shared)))

        def check_all_updated(_source: Path) -> None:
            t.assert_true("整项目回收前所有重复链接已转换", all(repo.get_file(fid).is_relative for fid in duplicate_ids))
            t.assert_true("整项目回收前其他成功记录也已转换", repo.get_file(shared_id).is_relative)

        trash.before_move = check_all_updated
        count = len(trash.paths)
        with patch("app.ui.mw_files.confirm", return_value=True) as confirm, patch(
            "app.ui.mw_files.run_with_progress", side_effect=_run_sync,
        ), patch("app.ui.mw_files.info") as info, patch("app.ui.mw_files.warn") as warn:
            win.action_convert_project_to_storage(pid)
        trash.before_move = None
        t.assert_eq("重复源文件去重回收一次", trash.paths[count:], [source.resolve()])
        t.assert_true("重复源文件已移至模拟回收目录", not source.exists())
        t.assert_true("其他链接仍引用时保留共享源文件", shared.exists())
        t.assert_eq("其他项目记录仍为链接", repo.get_file(other_id).is_relative, False)
        t.assert_eq("整项目只显示一次汇总", info.call_count + warn.call_count, 1)
        t.assert_in("项目转换正确报告三条成功记录", "已复制到库内：3 个", _summary(info, warn))
        t.assert_in("共享源保留原因进入结果", "链接", _summary(info, warn))
        t.assert_in("共享源文件名进入结果", shared.name, _summary(info, warn))
        t.assert_in("开启时转换确认说明回收", "回收站", confirm.call_args.args[2])

        fail_pid = repo.save_project(Project(title="回收失败仍入库"))
        failed = _source(root, "trash-fail.txt")
        failed_id = repo.add_file(FileItem(project_id=fail_pid, path=str(failed)))
        with patch("app.storage_cleanup.move_to_trash", side_effect=OSError("模拟回收失败")), patch(
            "app.ui.mw_files.confirm", return_value=True,
        ), patch("app.ui.mw_files.run_with_progress", side_effect=_run_sync), patch(
            "app.ui.mw_files.info",
        ) as info, patch("app.ui.mw_files.warn") as warn:
            win.action_convert_project_to_storage(fail_pid)
        t.assert_true("回收失败仍保留已提交仓储记录", repo.get_file(failed_id).is_relative)
        t.assert_true("回收失败保留原文件", failed.exists())
        t.assert_in("回收失败不减成功数", "已复制到库内：1 个", _summary(info, warn))
        t.assert_in("回收失败进入结果", "模拟回收失败", _summary(info, warn))
        t.assert_eq("回收失败仅一次汇总", info.call_count + warn.call_count, 1)

        db_pid = repo.save_project(Project(title="转换写回部分失败"))
        db_sources = [_source(root, name) for name in ("update-ok.txt", "update-fail.txt")]
        db_ids = [repo.add_file(FileItem(project_id=db_pid, path=str(path))) for path in db_sources]
        original_update = repo.update_file

        def update(file):
            if file.id == db_ids[1]:
                raise RuntimeError("模拟转换写回失败")
            return original_update(file)

        count = len(trash.paths)
        with patch.object(repo, "update_file", side_effect=update), patch(
            "app.ui.mw_files.confirm", return_value=True,
        ), patch("app.ui.mw_files.run_with_progress", side_effect=_run_sync), patch(
            "app.ui.mw_files.info",
        ) as info, patch("app.ui.mw_files.warn") as warn:
            win.action_convert_project_to_storage(db_pid)
        t.assert_eq("转换写回部分失败仅成功源回收", trash.paths[count:], [db_sources[0].resolve()])
        t.assert_true("转换写回成功记录仓储路径保留", repo.get_file(db_ids[0]).is_relative)
        t.assert_true("转换写回失败源文件保留", db_sources[1].exists())
        t.assert_eq("转换写回失败记录仍为原链接", repo.get_file(db_ids[1]).path, str(db_sources[1]))
        t.assert_eq("转换写回失败记录仍为链接模式", repo.get_file(db_ids[1]).is_relative, False)
        t.assert_in("转换写回部分失败成功计数", "已复制到库内：1 个", _summary(info, warn))
        t.assert_in("转换写回部分失败错误计数", "失败/跳过：1 个", _summary(info, warn))
        t.assert_in("转换写回失败进入结果", "模拟转换写回失败", _summary(info, warn))
        t.assert_eq("转换写回部分失败仅一次汇总", info.call_count + warn.call_count, 1)

        cancel_pid = repo.save_project(Project(title="取消转换"))
        cancelled = _source(root, "cancel-convert.txt")
        cancelled_id = repo.add_file(FileItem(project_id=cancel_pid, path=str(cancelled)))
        before = sorted(win.library.root.rglob("*"))
        with patch("app.ui.mw_files.confirm", return_value=False), patch(
            "app.ui.mw_files.run_with_progress",
        ) as runner:
            win.action_convert_project_to_storage(cancel_pid)
        t.assert_eq("取消转换不启动任务", runner.call_count, 0)
        t.assert_true("取消转换原文件保留", cancelled.exists())
        t.assert_eq("取消转换不修改链接记录", repo.get_file(cancelled_id).is_relative, False)
        t.assert_eq("取消转换不产生仓储文件", sorted(win.library.root.rglob("*")), before)

        def run_cancelled(_parent, _title, _label, worker, *, on_done, on_error) -> None:
            on_done(worker(lambda *_args: None, lambda: True))

        count = len(trash.paths)
        with patch("app.ui.mw_files.confirm", return_value=True), patch(
            "app.ui.mw_files.run_with_progress", side_effect=run_cancelled,
        ), patch.object(win.library, "import_copy") as copy, patch(
            "app.ui.mw_files.info",
        ) as info, patch("app.ui.mw_files.warn") as warn:
            win.action_convert_project_to_storage(cancel_pid)
        t.assert_eq("后台取消不开始复制", copy.call_count, 0)
        t.assert_eq("后台取消不回收源", len(trash.paths), count)
        t.assert_true("后台取消源文件仍保留", cancelled.exists())
        t.assert_eq("后台取消记录保持链接", repo.get_file(cancelled_id).is_relative, False)
        t.assert_eq("后台取消不产生仓储文件", sorted(win.library.root.rglob("*")), before)
        t.assert_in("后台取消显示未处理数", "已取消：1 个未处理", _summary(info, warn))
    finally:
        trash.before_move = None
        _close_window(win)
        repo.conn.close()


def test_batch_shared_source(t: T, root: Path, trash: FakeTrash) -> None:
    """两个 plan 共享源文件，所有项目落库后统一回收而非逐项目回收。"""
    root.mkdir()
    folder = root / "shared-plan"
    folder.mkdir()
    source = _source(folder, "shared-plan-file.txt")
    content = source.read_bytes()
    win, repo = _window(root)
    try:
        repo.set_setting(KEY, "1")

        def check_projects(_source: Path) -> None:
            projects = repo.list_projects()
            t.assert_eq("批量回收前两个项目均落库", len(projects), 2)
            files = [file for project in projects for file in repo.list_files(project.id)]
            t.assert_eq("批量回收前两个同源记录均落库", len(files), 2)
            t.assert_true("批量回收前全部记录为仓储", all(file.is_relative for file in files))

        trash.before_move = check_projects
        count = len(trash.paths)
        with patch("app.ui.import_dialog.ImportDialog") as dialog, patch(
            "app.ui.mw_dnd.run_with_progress", side_effect=_run_sync,
        ), patch("app.ui.mw_dnd.recycle_sources_with_progress", side_effect=_recycle_sync) as recycle, patch(
            "app.ui.mw_dnd.info",
        ) as info, patch("app.ui.mw_dnd.warn") as warn:
            dialog.return_value.exec.return_value = QDialog.Accepted
            dialog.return_value.options.return_value = ImportOptions(storage_mode="copy")
            win._run_batch_folder_import([folder, folder])
        trash.before_move = None
        t.assert_eq("批量两个plan只调用一次回收阶段", recycle.call_count, 1)
        t.assert_eq("批量两个plan共同收集两个候选", len(recycle.call_args.args[3]), 2)
        t.assert_eq("批量同源文件只回收一次", trash.paths[count:], [source.resolve()])
        t.assert_true("批量同源文件已移至模拟回收目录", not source.exists())
        projects = repo.list_projects()
        t.assert_eq("批量两个plan各自建立项目", len(projects), 2)
        files = [file for project in projects for file in repo.list_files(project.id)]
        t.assert_eq("批量两个项目内容完整", [win.library.resolve(file.path, True).read_bytes() for file in files], [content, content])
        t.assert_eq("批量同源成功不显示警告", info.call_count + warn.call_count, 0)
        t.assert_in("批量结果成功项目和文件计数", "2 / 2 个项目，共 2 个文件", win.statusBar().currentMessage())
    finally:
        trash.before_move = None
        _close_window(win)
        repo.conn.close()


def test_background_cleanup(t: T, root: Path, trash: FakeTrash) -> None:
    """使用真实 Qt worker/事件循环，仅以无数据库访问的 fake 拦截回收动作。"""
    root.mkdir()
    win, repo = _window(root)
    main_thread = threading.get_ident()
    try:
        repo.set_setting(KEY, "1")
        pid = repo.save_project(Project(title="真实后台回收"))

        def candidate(name: str):
            source = _source(root, name)
            snapshot = capture_storage_source(source)
            relative = win.library.import_copy(pid, source)
            fid = repo.add_file(FileItem(project_id=pid, path=relative, is_relative=True))
            return source, (snapshot, relative, fid)

        source, entry = candidate("background-ok.txt")
        move_threads: list[int] = []
        read_threads: list[int] = []
        original_get_file = repo.get_file

        def get_file(fid):
            read_threads.append(threading.get_ident())
            return original_get_file(fid)

        # 回收前回调只记录线程，不读 repo；真正 SQLite 访问只出现在快照阶段。
        trash.before_move = lambda _source: move_threads.append(threading.get_ident())
        with patch.object(repo, "get_file", side_effect=get_file):
            messages = recycle_sources_with_progress(win, repo, win.library, [entry])
        trash.before_move = None
        t.assert_eq("真实后台回收正常返回无警告", messages, [])
        t.assert_true("真实后台回收源文件移走", not source.exists())
        t.assert_eq("真实后台只执行一次回收", len(move_threads), 1)
        t.assert_true("真实回收IO在线程执行", bool(move_threads) and all(tid != main_thread for tid in move_threads))
        t.assert_true("真实回收DB快照留在主线程", bool(read_threads) and all(tid == main_thread for tid in read_threads))
        t.assert_true("真实后台回收仓储副本仍存在", win.library.resolve(entry[1], True).exists())

        cancelled, cancel_entry = candidate("background-cancel.txt")
        count = len(trash.paths)
        # 保留真实 QThread 与进度框，只固定取消请求为进入 worker 前已收到。
        with patch.object(FileOpWorker, "is_cancelled", return_value=True):
            messages = recycle_sources_with_progress(win, repo, win.library, [cancel_entry])
        t.assert_in("真实后台取消返回提示", "取消", "\n".join(messages))
        t.assert_true("真实后台取消原文件保留", cancelled.exists())
        t.assert_eq("真实后台取消不回收", len(trash.paths), count)
        t.assert_true("真实后台取消不影响已入库副本", win.library.resolve(cancel_entry[1], True).exists())
    finally:
        trash.before_move = None
        _close_window(win)
        repo.conn.close()


def main() -> bool:
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-gui-storage-recycle-") as temporary:
        root = Path(temporary)
        trash = FakeTrash(root)
        # 整个测试入口始终拦截回收函数；不存在访问真实回收站的测试分支。
        with patch("app.storage_cleanup.move_to_trash", side_effect=trash):
            test_settings(t, root / "settings")
            with patch("app.ui.mw_files.recycle_sources_with_progress", side_effect=_recycle_sync):
                test_add_files(t, root / "add", trash)
                test_batch_failures(t, root / "batch", trash)
                test_conversion(t, root / "conversion", trash)
            test_batch_shared_source(t, root / "batch-shared", trash)
            test_background_cleanup(t, root / "background", trash)
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
