"""解压双进度 GUI 回归：仅自造压缩包、显式临时库及 offscreen Qt。

运行：.venv/Scripts/python.exe -X utf8 selftests/gui_archive_progress.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QThread, Qt, qInstallMessageHandler
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QProgressBar, QWidget
from shiboken6 import isValid

from app.archives import ArchiveCancelled, ArchiveProgress, extract_archive
from app.models import FileItem, Project
from app.ui import archive_progress
from app.ui.archive_progress import ArchiveProgressDialog, run_archive_with_progress
from app.utils import OperationCancelled
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _find_file_tree_item, _window


def _spin_until(predicate, timeout: float = 8) -> None:
    """以主线程事件循环等待信号，超时即报错而非永久挂起。"""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("等待解压后台任务超时")
        _app().processEvents()
        QTest.qWait(1)
    _app().processEvents()


def _flush_deleted() -> None:
    _app().sendPostedEvents(None, QEvent.DeferredDelete)
    _app().processEvents()


def test_dialog(t: T) -> None:
    """字节比例使用固定刻度，并覆盖切换文件、未知总量及空包完成。"""
    dialog = ArchiveProgressDialog(None, "测试解压", "准备")
    dialog.show()
    t.assert_eq("恰有总进度和当前文件两个进度条", len(dialog.findChildren(QProgressBar)), 2)
    state = ArchiveProgress(1, 4, 75, 100, "中文/首个.bin", 25, 100)
    dialog.update_progress(state)
    t.assert_eq("总进度按字节显示75%", (dialog.overall_bar.maximum(), dialog.overall_bar.value()), (1000, 750))
    t.assert_eq("当前文件显示25%", (dialog.current_bar.maximum(), dialog.current_bar.value()), (1000, 250))
    t.assert_in("总标签显示文件数量", "1 / 4", dialog.overall_label.text())
    t.assert_in("当前标签显示文件名", "首个.bin", dialog.current_label.text())
    dialog.update_progress(ArchiveProgress(1, 4, 75, 100, "下一个.bin", 0, 100))
    t.assert_eq("切换文件当前进度归零", dialog.current_bar.value(), 0)
    big = 8 * 1024 ** 3
    dialog.update_progress(ArchiveProgress(0, 1, big * 3 // 4, big, "大文件.bin", big // 2, big))
    t.assert_eq("超过2GB总进度不溢出", dialog.overall_bar.value(), 750)
    t.assert_eq("超过2GB当前进度不溢出", dialog.current_bar.value(), 500)
    dialog.update_progress(ArchiveProgress(0, None, 123, None, "未知.gz", 123, None))
    t.assert_eq("总大小未知时不确定进度", (dialog.overall_bar.minimum(), dialog.overall_bar.maximum()), (0, 0))
    t.assert_eq("当前大小未知时不确定进度", (dialog.current_bar.minimum(), dialog.current_bar.maximum()), (0, 0))
    dialog.update_progress(ArchiveProgress(0, 0, 0, 0, "", 0, None, True))
    t.assert_eq("空包完成总进度100%", dialog.overall_bar.value(), 1000)
    t.assert_eq("空包完成当前进度100%", dialog.current_bar.value(), 1000)
    dialog.finish()
    t.assert_true("finish关闭进度窗口", not dialog.isVisible())
    _flush_deleted()

    for gesture in ("按钮", "Esc", "关闭"):
        dialog = ArchiveProgressDialog(None, "测试取消", "准备")
        requests: list[bool] = []
        dialog.cancel_requested.connect(lambda: requests.append(True))
        dialog.show()
        if gesture == "按钮":
            dialog.cancel_button.click()
        elif gesture == "Esc":
            QTest.keyClick(dialog, Qt.Key_Escape)
        else:
            dialog.close()
        t.assert_eq(f"{gesture}发出取消请求", len(requests), 1)
        t.assert_true(f"{gesture}等待后台结束保持显示", dialog.isVisible())
        dialog.reject()
        dialog.close()
        QTest.keyClick(dialog, Qt.Key_Escape)
        t.assert_eq(f"{gesture}重复操作只请求一次", len(requests), 1)
        t.assert_true(f"{gesture}取消按钮禁用", not dialog.cancel_button.isEnabled())
        dialog.finish()
        t.assert_true(f"{gesture}后台完成才关闭", not dialog.isVisible())
        _flush_deleted()


def _zip(path: Path) -> dict[str, bytes]:
    contents = {"中文/大文件.bin": b"a" * (3 * 1024 ** 2 + 17), "小文件.txt": b"test", "空文件": b""}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    return contents


def test_runner(t: T, root: Path, mode: str) -> None:
    """实际线程、流式解压及 Qt 信号，取消通过分块回调门控保证可重复。"""
    root.mkdir()
    source = root / "合成.zip"
    contents = _zip(source)
    if mode == "失败":
        source.write_bytes(b"invalid zip fixture")
    output = root / "output"
    states: list[ArchiveProgress] = []
    ui_threads: list[QThread] = []
    work_threads: list[QThread] = []
    dialogs: list[ArchiveProgressDialog] = []
    callbacks: list[tuple[str, object, QThread]] = []

    class ObservedDialog(ArchiveProgressDialog):
        def __init__(self, *args) -> None:
            super().__init__(*args)
            dialogs.append(self)

        def update_progress(self, state: ArchiveProgress) -> None:
            states.append(state)
            ui_threads.append(QThread.currentThread())
            super().update_progress(state)
            if mode == "取消" and state.current_bytes > 0:
                self.cancel_button.click()

    def work(progress, is_cancelled):
        work_threads.append(QThread.currentThread())

        def detail(state: ArchiveProgress) -> None:
            progress.detail(state)
            if mode == "取消" and state.current_bytes > 0:
                deadline = time.monotonic() + 5
                while not is_cancelled() and time.monotonic() < deadline:
                    time.sleep(0.001)
                if not is_cancelled():
                    raise AssertionError("GUI没有收到分块进度并请求取消")

        try:
            return extract_archive(source, output, progress=progress,
                                   detail_progress=detail, is_cancelled=is_cancelled)
        except ArchiveCancelled as exc:
            raise OperationCancelled() from exc

    def complete(kind: str, result=None) -> None:
        callbacks.append((kind, result, QThread.currentThread()))

    parent = QWidget()
    try:
        with patch.object(archive_progress, "ArchiveProgressDialog", ObservedDialog), patch(
            "app.archives._PROGRESS_INTERVAL", 0,
        ):
            worker = run_archive_with_progress(
                parent, "测试", "准备", work,
                on_done=lambda result: complete("成功", result),
                on_cancel=lambda: complete("取消"),
                on_error=lambda message: complete("失败", message),
            )
            _spin_until(lambda: bool(callbacks))
            # 完成回调可先于QThread.finished到达，父窗口销毁前明确等待线程退出。
            if isValid(worker):
                t.assert_true(f"{mode}后台线程退出", worker.wait(5000))
            t.assert_eq(f"{mode}恰好一次对应回调", [item[0] for item in callbacks], [mode])
            t.assert_true(f"{mode}回调在GUI主线程", all(item[2] == _app().thread() for item in callbacks))
            t.assert_true(f"{mode}文件IO在后台线程", bool(work_threads) and all(thread != _app().thread() for thread in work_threads))
            t.assert_true(f"{mode}窗口关闭", all(not isValid(dialog) or not dialog.isVisible() for dialog in dialogs))
            if mode != "失败":
                t.assert_true(f"{mode}收到详情快照", bool(states))
                t.assert_true(f"{mode}详情在GUI主线程", all(thread == _app().thread() for thread in ui_threads))
                t.assert_true(f"{mode}收到文件内分块进度", any(0 < state.current_bytes < (state.current_total or 0) for state in states))
            if mode == "成功":
                t.assert_true("成功收到最终完成快照", any(state.finished for state in states))
                t.assert_eq("成功路径清单", {path.as_posix() for path in callbacks[0][1]}, set(contents))
                t.assert_true("成功文件内容完整", all((output / name).read_bytes() == data for name, data in contents.items()))
            else:
                t.assert_true(f"{mode}清理本次输出", not output.exists())
            t.assert_true(f"{mode}原压缩包保留", source.is_file())
            _flush_deleted()
    finally:
        parent.close()
        parent.deleteLater()
        _flush_deleted()


def test_action(t: T, root: Path) -> None:
    """从真实MainWindow入口运行新runner，SQLite只访问显式临时库。"""
    root.mkdir()
    win, repo = _window(root)
    source = root / "入口.zip"
    contents = _zip(source)
    pid = repo.save_project(Project(title="临时双进度测试"))
    fid = repo.add_file(FileItem(project_id=pid, path=str(source), kind="archive"))
    win.refresh_projects()
    win._select_project_by_id(pid)
    win.tbl_files.setCurrentItem(_find_file_tree_item(win, fid))
    try:
        with patch("app.ui.archive_actions.confirm", return_value=True), patch("app.ui.archive_actions.warn") as warning:
            win.action_extract_archive()
            _spin_until(lambda: len(repo.list_files(pid)) > 1 or warning.called)
            _spin_until(lambda: all(not thread.isRunning() for thread in win.findChildren(QThread)))
            t.assert_eq("真实入口无失败提示", warning.call_count, 0)
            t.assert_eq("真实入口完整登记解压结果", len(repo.list_files(pid)), 1 + len(contents))
            t.assert_true("真实入口进度窗口关闭", all(not dialog.isVisible() for dialog in win.findChildren(ArchiveProgressDialog)))
            t.assert_in("真实入口完成反馈", "已解压 3 个文件", win.statusBar().currentMessage())
            t.assert_true("真实入口保留压缩包", source.is_file())
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    app: QApplication = _app()
    t = T()
    qt_errors: list[str] = []

    def qt_message(_kind, _context, message: str) -> None:
        if "QThread: Destroyed" in message or "different thread" in message or "QObject::" in message:
            qt_errors.append(message)

    previous = qInstallMessageHandler(qt_message)
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            test_dialog(t)
            for mode in ("成功", "取消", "失败"):
                test_runner(t, root / mode, mode)
            test_action(t, root / "action")
        _flush_deleted()
        t.assert_eq("无QThread销毁或跨线程Qt警告", qt_errors, [])
    finally:
        qInstallMessageHandler(previous)
    sys.exit(0 if t.report() else 1)
