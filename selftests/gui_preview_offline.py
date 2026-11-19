"""外置媒体离线预览回归；只访问显式临时库与自行合成的媒体。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterator
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QItemSelectionModel, QPointF, QTimer, Qt, qInstallMessageHandler
from PySide6.QtGui import QImage, QPainter, QPdfWriter
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from app.models import FileItem, Project
from app.repository import Repository
from app.ui.preview import PreviewPanel
from app.ui.preview_access import FileAccessMonitor, ProbeResult, probe_file
from selftests._common import T
from selftests.gui_main_window_regressions import (
    _app, _close_window, _find_file_tree_item, _window,
)


def _wait(predicate: Callable[[], bool], timeout: float = 6.0) -> bool:
    """处理 Qt 真实异步事件，失败时返回 False，避免固定等待掩盖竞态。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _app().processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return bool(predicate())


def _image(path: Path) -> None:
    """仅在临时目录合成真实 PNG，不读取任何外部样本。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(64, 48, QImage.Format_RGB32)
    image.fill(0x3366AA)
    assert image.save(str(path))


@contextmanager
def _panel() -> Iterator[PreviewPanel]:
    panel = PreviewPanel()
    panel.resize(700, 500)
    panel.show()
    try:
        yield panel
    finally:
        panel.shutdown()
        panel.close()
        panel.deleteLater()
        _app().sendPostedEvents(None, QEvent.DeferredDelete)
        _app().processEvents()


def _select(win, repo: Repository, pid: int, fid: int) -> None:
    win._current_project_id = pid
    win._show_project(repo.get_project(pid))
    item = _find_file_tree_item(win, fid)
    assert item is not None, f"临时文件行不存在：{fid}"
    win.tbl_files.setCurrentItem(item, 0, QItemSelectionModel.ClearAndSelect)
    win._on_file_selected()
    _app().processEvents()


def _sample_video(root: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("本自测需要 ffmpeg 合成临时视频；产品本身不依赖 ffmpeg")
    path = root / "synthetic.mp4"
    subprocess.run(
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i",
         "color=c=blue:s=160x90:r=10:d=8", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, timeout=30,
    )
    return path


def _show(panel: PreviewPanel, path: Path, fid: int = 123, label: str = "临时文件") -> None:
    panel.set_file_context(fid, label)
    panel.show_file(str(path))


def test_probe_errors(root: Path, _sample: Path, t: T) -> None:
    """读取失败保留原始阶段和异常；仅明确盘符消失才报告设备离线。"""
    path = root / "probe.png"
    _image(path)
    t.assert_true("实际合成图可读取", probe_file(str(path)).accessible)
    missing = probe_file(str(root / "missing.png"))
    t.assert_eq("缺失文件保留异常类型", missing.error_type, "FileNotFoundError")
    t.assert_true("缺失文件具有阶段及原因", bool(missing.stage and missing.message))
    with patch("builtins.open", side_effect=PermissionError("测试拒绝读取")):
        denied = probe_file(str(path))
    t.assert_eq("权限错误保留类型", denied.error_type, "PermissionError")
    t.assert_eq("权限错误保留失败阶段", denied.stage, "打开文件")
    t.assert_in("权限错误保留原始原因", "测试拒绝读取", denied.message)
    t.assert_true("权限错误不误报盘符离线", not denied.device_offline)
    t.assert_eq("目录不是可预览普通文件", probe_file(str(root)).error_type, "IsADirectoryError")


def test_missing_reconnect(root: Path, _sample: Path, t: T) -> None:
    """首次缺失保留当前记录与路径，五秒轮询仅在离线时工作。"""
    path = root / "reconnected.png"
    with _panel() as panel:
        statuses: list[str] = []
        relinks: list[bool] = []
        panel.status_changed.connect(statuses.append)
        panel.relink_requested.connect(lambda: relinks.append(True))
        _show(panel, path, 321, "外置图片")
        t.assert_true("首次访问异步进入离线", _wait(lambda: panel._access_state == "offline"))
        t.assert_eq("首次缺失保留当前路径", panel._current, str(path))
        t.assert_eq("首次缺失保留记录 ID", panel._file_id, 321)
        t.assert_eq("首次缺失保留路径提示", panel._lbl_path.toolTip(), str(path))
        t.assert_true("首次缺失与未选择提示区分", "未选择" not in panel._other.text())
        t.assert_eq("离线检查默认五秒", panel._offline_timer.interval(), 5000)
        t.assert_true("离线状态启动重连检查", panel._offline_timer.isActive())
        t.assert_true("离线提示可手动重试", panel._btn_retry.isEnabled())
        details = panel._error_details()
        for value in ("外置图片", "321", str(path), "FileNotFoundError", panel._last_error.stage,
                      panel._last_error.message):
            t.assert_in("错误详情保留 " + value[:30], value, details)
        with patch("app.ui.preview.dialogs.info") as info:
            panel._btn_details.click()
            t.assert_eq("详情弹窗保留完整原始报告", info.call_args.kwargs["detailed"], details)
        panel._btn_copy_error.click()
        t.assert_eq("复制错误详情保留完整报告", _app().clipboard().text(), details)
        for button in panel.findChildren(QPushButton):
            if "重关联" in button.text():
                button.click()
                break
        t.assert_eq("重关联按钮发出入口信号", relinks, [True])
        _image(path)
        # 发出真实 timer 连接的 timeout，避免等待完整五秒拖慢回归。
        panel._offline_timer.timeout.emit()
        t.assert_true("设备重连后自动恢复真实图", _wait(lambda: panel._access_state == "ready"))
        t.assert_true("真实图可以抓取", panel.capture_pixmap() is not None)
        t.assert_true("恢复成功继续轻量健康检查", panel._offline_timer.isActive())
        t.assert_true("离线与恢复信号可连接状态栏", len(statuses) >= 2)
        panel.show_file(None)
        t.assert_eq("清空选择回到 empty", panel._access_state, "empty")
        t.assert_true("清空选择不留离线 timer", not panel._offline_timer.isActive())


def test_permission_and_decode(root: Path, _sample: Path, t: T) -> None:
    """权限失败与媒体损坏分开；可读但解码失败的文件不无限自动解码。"""
    path = root / "denied.png"
    _image(path)
    denied = ProbeResult(str(path), False, "打开文件", "PermissionError", "合成权限失败")
    with _panel() as panel:
        with patch("app.ui.preview_access.probe_file", return_value=denied):
            _show(panel, path)
            t.assert_true("权限拒绝进入读取失败页", _wait(lambda: panel._access_state == "offline"))
            t.assert_eq("权限拒绝保留原始异常", panel._last_error.error_type, "PermissionError")
            t.assert_true("权限错误不写设备离线提示", "设备离线" not in panel._other.text())
        panel.retry_current()
        t.assert_true("解除权限后手动重试恢复图", _wait(lambda: panel._access_state == "ready"))

        invalid = root / "corrupt.png"
        invalid.write_bytes(b"deliberately invalid synthetic image")
        with patch.object(panel._image, "load", wraps=panel._image.load) as decode:
            _show(panel, invalid)
            t.assert_true("可读坏图进入 decode_error", _wait(lambda: panel._access_state == "decode_error"))
            t.assert_eq("初次坏图只解码一次", decode.call_count, 1)
            panel._offline_timer.setInterval(20)
            QTest.qWait(130)
            t.assert_eq("损坏文件不重复自动解码", decode.call_count, 1)
            details = panel._error_details()
            t.assert_in("解码错误详情包含源路径", str(invalid), details)
            t.assert_true("解码错误详情包含失败原因", bool(panel._last_error.message))
            _image(invalid)
            panel._btn_retry.click()
            t.assert_true("修复图片后手动重试成功", _wait(lambda: panel._access_state == "ready"))
            t.assert_eq("显式重试只追加一次解码", decode.call_count, 2)


def test_probe_races(root: Path, _sample: Path, t: T) -> None:
    """后台慢盘检测不能阻塞 UI、覆盖新文件或在关闭后更新控件。"""
    slow = root / "slow.png"
    good = root / "good.png"
    _image(good)
    entered = threading.Event()
    release = threading.Event()
    original = probe_file

    def delayed(path: str) -> ProbeResult:
        if path == str(slow):
            entered.set()
            release.wait(2)
            return ProbeResult(path, False, "测试慢盘", "TimeoutError", "迟到失败")
        return original(path)

    with _panel() as panel, patch("app.ui.preview_access.probe_file", side_effect=delayed):
        beats: list[bool] = []
        timer = QTimer(panel)
        timer.setInterval(10)
        timer.timeout.connect(lambda: beats.append(True))
        timer.start()
        started = time.monotonic()
        _show(panel, slow)
        t.assert_true("show_file 不等待慢盘", time.monotonic() - started < 0.25)
        t.assert_true("慢探测实际已在后台开始", _wait(entered.is_set))
        t.assert_true("慢盘期间 UI 定时器仍响应", _wait(lambda: len(beats) >= 3, 0.5))
        _show(panel, good, 456, "新图")
        t.assert_true("切到可访问文件无需等待旧慢盘", _wait(lambda: panel._access_state == "ready", 1))
        release.set()
        QTest.qWait(100)
        t.assert_eq("迟到 probe 不覆盖当前新图", panel._current, str(good))
        t.assert_eq("迟到 probe 不覆盖 ready", panel._access_state, "ready")
        panel._image.failed.emit(str(slow), "迟到旧图片错误")
        panel._video.failed.emit(str(slow), "迟到旧视频错误")
        panel._pdf.failed.emit(str(slow), "迟到旧 PDF 错误")
        _app().processEvents()
        t.assert_eq("旧媒体失败不覆盖新图", panel._access_state, "ready")

    for action in ("shutdown", "close"):
        entered.clear()
        release.clear()
        panel = PreviewPanel()
        panel.show()
        with patch("app.ui.preview_access.probe_file", side_effect=delayed):
            _show(panel, slow)
            t.assert_true(action + " 场景慢 probe 开始", _wait(entered.is_set))
            started = time.monotonic()
            getattr(panel, action)()
            t.assert_true(action + " 不等待阻塞 I/O", time.monotonic() - started < 0.25)
            state = (panel._current, panel._access_state, panel._other.text())
            release.set()
            QTest.qWait(100)
            panel._image.failed.emit(str(slow), "关闭后的迟到错误")
            _app().processEvents()
            t.assert_eq(action + " 后迟到结果不更新页面",
                        (panel._current, panel._access_state, panel._other.text()), state)
            t.assert_true(action + " 后停止离线 timer", not panel._offline_timer.isActive())
            panel.close()
            panel.deleteLater()
            _app().sendPostedEvents(None, QEvent.DeferredDelete)

    entered.clear()
    release.clear()
    with _panel() as panel, patch("app.ui.preview_access.probe_file", side_effect=delayed), \
            patch.object(FileAccessMonitor, "TIMEOUT_MS", 70):
        _show(panel, slow)
        t.assert_true("后台超时转换为离线错误", _wait(lambda: panel._access_state == "offline", 1))
        t.assert_eq("后台超时保留异常类型", panel._last_error.error_type, "TimeoutError")
        release.set()
        QTest.qWait(100)
        t.assert_eq("超时后的旧任务结果无效", panel._access_state, "offline")


def test_real_video_resume(root: Path, sample: Path, t: T) -> None:
    """真实 QMediaPlayer 验证 seek 等待加载、手动播放意图和自动重连暂停。"""
    with _panel() as panel:
        video = panel._video
        player = video._player
        finished: list[str] = []
        panel.video_finished.connect(finished.append)
        _show(panel, sample, 777, "合成短视频")
        t.assert_true("真实短视频加载成功", _wait(lambda: panel._access_state == "ready" and player.duration() > 0))
        t.assert_true("视频后端实际可 seek", _wait(player.isSeekable))
        player.setPosition(1400)
        player.play()
        t.assert_true("真实视频播放并推进位置", _wait(lambda: player.playbackState() == QMediaPlayer.PlayingState
                                                and player.position() >= 1500))
        with patch.object(video, "load", wraps=video.load) as load:
            before = player.position()
            panel.retry_current()
            t.assert_true("手动重试播放态恢复", _wait(lambda: panel._access_state == "ready"
                                                  and video._pending_resume is None
                                                  and player.playbackState() == QMediaPlayer.PlayingState))
            kwargs = load.call_args.kwargs
            t.assert_true("手动重试沿用播放意图", kwargs["autoplay"])
            t.assert_true("手动重试传入原位置", abs(kwargs["resume"]["position"] - before) <= 250)
            t.assert_true("真实加载完成后恢复原位置", player.position() >= before - 100)

        player.pause()
        player.setPosition(2600)
        t.assert_true("暂停位置实际设置成功", _wait(lambda: abs(player.position() - 2600) <= 100))
        with patch.object(video, "load", wraps=video.load) as load:
            panel.retry_current()
            t.assert_true("手动重试暂停态加载完成", _wait(lambda: panel._access_state == "ready"
                                                    and video._pending_resume is None))
            t.assert_true("手动暂停重试不自动播放", not load.call_args.kwargs["autoplay"]
                          and player.playbackState() != QMediaPlayer.PlayingState)
            t.assert_true("手动暂停重试保持位置", abs(player.position() - 2600) <= 150)

        player.play()
        t.assert_true("离线前确实播放", _wait(lambda: player.playbackState() == QMediaPlayer.PlayingState))
        offline = ProbeResult(str(sample), False, "打开文件", "OSError", "测试设备断开", True)
        with patch("app.ui.preview_access.probe_file", return_value=offline):
            panel._offline_timer.timeout.emit()
            t.assert_true("播放中检测设备离线", _wait(lambda: panel._access_state == "offline"))
        saved = dict(panel._resume)
        t.assert_true("离线记录原播放意图", saved.get("playing") is True)
        t.assert_true("离线后停止继续播放", player.playbackState() != QMediaPlayer.PlayingState)
        with patch.object(player, "mediaStatus", return_value=QMediaPlayer.EndOfMedia):
            video._on_media_status(QMediaPlayer.EndOfMedia)
            _app().processEvents()
        t.assert_eq("设备错误不误触视频播完动作", finished, [])
        with patch.object(video, "load", wraps=video.load) as load:
            panel._offline_timer.timeout.emit()
            t.assert_true("自动重连真实视频恢复加载", _wait(lambda: panel._access_state == "ready"
                                                      and video._pending_resume is None))
            t.assert_true("自动重连明确禁用播放", not load.call_args.kwargs["autoplay"])
            t.assert_true("自动重连保留中断位置", abs(player.position() - saved["position"]) <= 150)
            t.assert_true("自动重连播放器保持停止或暂停", player.playbackState() != QMediaPlayer.PlayingState)
        t.assert_eq("重试及重连都不误发播完", finished, [])

        # 与 ready 状态重试分别覆盖：已经离线时使用中断前的 playing/position。
        for playing in (True, False):
            player.setPosition(3400 if playing else 4200)
            player.play() if playing else player.pause()
            t.assert_true("再次离线前确认播放状态 " + str(playing), _wait(
                lambda: (player.playbackState() == QMediaPlayer.PlayingState) == playing))
            with patch("app.ui.preview_access.probe_file", return_value=offline):
                panel._offline_timer.timeout.emit()
                t.assert_true("手动重试场景先进入离线 " + str(playing),
                              _wait(lambda: panel._access_state == "offline"))
            interrupted = dict(panel._resume)
            with patch.object(video, "load", wraps=video.load) as load:
                panel._btn_retry.click()
                t.assert_true("离线手动重试完成 " + str(playing), _wait(
                    lambda: panel._access_state == "ready" and video._pending_resume is None))
                t.assert_eq("离线手动重试恢复原播放意图 " + str(playing),
                            load.call_args.kwargs["autoplay"], playing)
                t.assert_eq("离线手动重试传入中断位置 " + str(playing),
                            load.call_args.kwargs["resume"]["position"], interrupted["position"])
                t.assert_true("离线手动重试实际播放器状态 " + str(playing), _wait(
                    lambda: (player.playbackState() == QMediaPlayer.PlayingState) == playing))
                t.assert_true("离线手动重试实际恢复位置 " + str(playing),
                              abs(player.position() - interrupted["position"]) <= 250)

        # 旧播放器 connection 闭包携带 generation；同路径再次载入也须失效。
        callbacks = [callback for _signal, callback in video._player_connections]
        video.load(str(sample), resume={"position": 3000}, autoplay=False)
        failures: list[tuple[str, str]] = []
        video.failed.connect(lambda path, reason: failures.append((path, reason)))
        for callback in callbacks:
            handler = callback.__kwdefaults__.get("_handler")
            if handler is not None and handler.__name__ == "_on_error":
                callback(QMediaPlayer.ResourceError, "过期的同源视频错误")
        t.assert_true("同源重载真实后端加载完成", _wait(lambda: video._pending_resume is None))
        t.assert_eq("旧播放器回调不能污染同源重载", failures, [])

        other = root / "new.png"
        _image(other)
        video._on_error(QMediaPlayer.ResourceError, "待派发的旧媒体错误")
        _show(panel, other, 778, "新选图片")
        t.assert_true("排队旧媒体错误不覆盖新选图片", _wait(lambda: panel._access_state == "ready"))
        t.assert_eq("旧媒体错误后仍保留新路径", panel._current, str(other))
        t.assert_eq("旧媒体错误没有向外传播", failures, [])


def test_record_and_ui_integration(root: Path, _sample: Path, t: T) -> None:
    """主窗口保留已登记的缺失文件，并把反馈及重关联限定到当前记录。"""
    win, repo = _window(root)
    path = root / "external" / "record.png"
    try:
        pid = repo.save_project(Project(title="临时离线项目"))
        fid = repo.add_file(FileItem(project_id=pid, path=str(path), is_relative=False,
                                     kind="image", label="缺失外置图", missing=True))
        original = asdict(repo.get_file(fid))
        _select(win, repo, pid, fid)
        t.assert_true("主窗缺失文件可进入离线预览", _wait(lambda: win.preview._access_state == "offline"))
        t.assert_eq("主窗缺失文件仍传入源路径", win.preview._current, str(path))
        t.assert_eq("主窗缺失文件仍传入记录 ID", win.preview._file_id, fid)
        t.assert_in("主窗状态栏收到离线提示", "文件无法访问", win.statusBar().currentMessage())
        t.assert_eq("首次失败不修改文件记录", asdict(repo.get_file(fid)), original)
        with patch.object(win, "_relink_files_to_directory") as relink:
            win.preview._btn_relink.click()
            t.assert_eq("重关联只调用一次", relink.call_count, 1)
            t.assert_eq("重关联限定当前记录", [file.id for file in relink.call_args.args[0]], [fid])
        _image(path)
        win.preview._offline_timer.timeout.emit()
        t.assert_true("主窗重连恢复真实图", _wait(lambda: win.preview._access_state == "ready"))
        t.assert_eq("重连不会自动清除 missing 标记或改记录", asdict(repo.get_file(fid)), original)
        path.unlink()
        win.preview._offline_timer.timeout.emit()
        t.assert_true("加载后源文件消失自动显示离线", _wait(lambda: win.preview._access_state == "offline"))
        t.assert_true("源文件消失可保留缓存画面", win.preview.capture_pixmap() is not None)
        t.assert_eq("文件消失检查仍不修改记录", asdict(repo.get_file(fid)), original)
        repo.set_setting("video_end_action", "next")
        with patch.object(win, "_navigate_preview_file") as navigate:
            win.preview.video_finished.emit(str(path))
            t.assert_eq("离线状态不连播下一文件", navigate.call_count, 0)
    finally:
        _close_window(win)
        repo.conn.close()


def test_pdf_resume(root: Path, _sample: Path, t: T) -> None:
    """用 Qt 自行生成多页 PDF，重试恢复页码，损坏 PDF 走解码错误分类。"""
    path = root / "synthetic.pdf"
    writer = QPdfWriter(str(path))
    painter = QPainter(writer)
    for page in range(3):
        if page:
            writer.newPage()
        painter.drawText(100, 100, f"Synthetic page {page + 1}")
    painter.end()
    del writer
    with _panel() as panel:
        _show(panel, path)
        t.assert_true("真实合成 PDF 加载成功", _wait(lambda: panel._access_state == "ready"))
        pdf = panel._pdf
        t.assert_eq("合成 PDF 具有三页", pdf._doc.pageCount(), 3)
        pdf._view.pageNavigator().jump(1, QPointF(), 0)
        t.assert_true("PDF 页码变更进入恢复状态", _wait(lambda: pdf.capture_resume_state()["page"] == 1))
        with patch.object(pdf, "load", wraps=pdf.load) as load:
            panel.retry_current()
            t.assert_true("PDF 重试后正常就绪", _wait(lambda: panel._access_state == "ready"))
            t.assert_eq("PDF 重试携带原页码", load.call_args.kwargs["resume"]["page"], 1)
            t.assert_eq("PDF 实际恢复到原页码", pdf._view.pageNavigator().currentPage(), 1)
        invalid = root / "invalid.pdf"
        invalid.write_bytes(b"deliberately invalid synthetic PDF")
        _show(panel, invalid)
        t.assert_true("可读坏 PDF 进入解码错误", _wait(lambda: panel._access_state == "decode_error"))
        t.assert_eq("坏 PDF 不误报设备离线", panel._last_error.error_type, "MediaDecodeError")
        t.assert_in("PDF 失败报告包含底层异常", "QPdfDocument", panel._last_error.message)


def main() -> bool:
    _app()
    t = T()
    exceptions: list[str] = []
    qt_errors: list[str] = []
    previous_hook = sys.excepthook

    def exception_hook(kind, value, traceback) -> None:
        exceptions.append(f"{kind.__name__}: {value}")
        previous_hook(kind, value, traceback)

    def qt_message(_kind, _context, message: str) -> None:
        # offscreen 的尺寸／音频提示是预期噪音；线程及已销毁对象错误不可忽略。
        if any(text in message for text in (
            "QThread: Destroyed", "already deleted", "Cannot queue arguments",
            "Timers cannot be", "QObject::connect", "QObject::killTimer",
        )):
            qt_errors.append(message)

    previous_qt_handler = qInstallMessageHandler(qt_message)
    sys.excepthook = exception_hook
    try:
        with tempfile.TemporaryDirectory(prefix="cabinet-preview-offline-", ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            sample = _sample_video(root)
            for name, test in TESTS:
                print("->", name, flush=True)
                case = root / name
                case.mkdir()
                test(case, sample, t)
        t.assert_eq("Qt 异步槽不抛 Python 异常", exceptions, [])
        t.assert_eq("异步监视器无 Qt 线程或对象生命周期错误", qt_errors, [])
    finally:
        sys.excepthook = previous_hook
        qInstallMessageHandler(previous_qt_handler)
    return t.report()


TESTS: list[tuple[str, Callable[[Path, Path, T], None]]] = [
    ("probe_errors", test_probe_errors),
    ("missing_reconnect", test_missing_reconnect),
    ("permission_decode", test_permission_and_decode),
    ("probe_races", test_probe_races),
    ("real_video_resume", test_real_video_resume),
    ("record_ui", test_record_and_ui_integration),
    ("pdf_resume", test_pdf_resume),
]


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
