"""用独立的 Qt 播放器提取视频首帧，不触碰预览播放器。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QTimer, Qt, QUrl
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import QApplication, QProgressDialog


# 同步命令行调用也需要事件分发器，并需保证 Python 引用存活。
_core_application: QCoreApplication | None = None


def save_first_video_frame(
    source: Path, target: Path, *, timeout_ms: int = 15000
) -> None:
    """将视频第一个有效且可转换的解码帧保存为 PNG；失败抛出中文错误。"""
    global _core_application

    source, target = Path(source), Path(target)
    if not source.is_file():
        raise RuntimeError(f"视频文件不存在：{source}")
    if timeout_ms <= 0:
        raise RuntimeError("提取视频首帧超时，请重试或选择其他视频。")

    application = QCoreApplication.instance()
    if application is None:
        _core_application = QCoreApplication([])
        application = _core_application

    owner = QObject()
    player = QMediaPlayer(owner)
    sink = QVideoSink(owner)
    # 不连接 QAudioOutput，整个提取过程不输出声音。
    player.setVideoSink(sink)
    loop = QEventLoop(owner)
    timer = QTimer(owner)
    timer.setSingleShot(True)
    progress: QProgressDialog | None = None
    completed = False
    failure = ""
    frame_failure = ""
    captured = QImage()

    def finish(message: str = "") -> None:
        nonlocal completed, failure
        if completed:
            return
        completed = True
        failure = message
        timer.stop()
        # 在退出事件循环前停止解码；首帧回调也不会再接收后续有效帧。
        player.pause()
        loop.quit()

    def on_frame(frame: QVideoFrame) -> None:
        nonlocal captured, frame_failure, failure
        if completed or not frame.isValid():
            return
        try:
            image = frame.toImage()
            if image.isNull():
                frame_failure = "视频帧无法转换为图像。"
                return
            # 先封锁重入并暂停，随后复制像素，避免继续播放覆盖首帧。
            finish()
            captured = image.copy()
        except Exception as exc:
            frame_failure = f"视频帧转换失败：{exc}"
            if completed:
                # finish 已执行时仍应将复制图像失败报告给调用者。
                failure = frame_failure

    def on_error(_error: QMediaPlayer.Error, message: str) -> None:
        finish(f"无法解码视频首帧：{message or player.errorString() or '视频损坏或格式不受支持'}")

    def on_status(status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            finish(f"无法解码视频首帧：{player.errorString() or '视频损坏或格式不受支持'}")
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            finish(frame_failure or "视频中没有可读取的视频帧。")

    def on_timeout() -> None:
        finish("提取视频首帧超时，请重试或选择其他视频。")

    def on_cancel() -> None:
        finish("已取消提取视频首帧。")

    sink.videoFrameChanged.connect(on_frame)
    player.errorOccurred.connect(on_error)
    player.mediaStatusChanged.connect(on_status)
    timer.timeout.connect(on_timeout)
    try:
        if isinstance(application, QApplication):
            progress = QProgressDialog(
                "正在提取视频首帧…", "取消", 0, 0, application.activeWindow()
            )
            progress.setWindowTitle("提取视频封面")
            progress.setWindowModality(Qt.WindowModality.ApplicationModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            progress.canceled.connect(on_cancel)
            progress.show()
        timer.start(timeout_ms)
        player.setSource(QUrl.fromLocalFile(str(source.resolve())))
        if not completed:
            player.play()
        if not completed:
            loop.exec()
        if failure:
            raise RuntimeError(failure)
        if captured.isNull():
            raise RuntimeError(frame_failure or "未能获取视频首帧。")
        if not captured.save(str(target), "PNG"):
            raise RuntimeError(f"视频首帧保存失败，请检查输出目录和写入权限：{target}")
    finally:
        # 先断开回调，再清空媒体，避免清理产生的状态信号覆盖结果。
        completed = True
        timer.stop()
        sink.videoFrameChanged.disconnect(on_frame)
        player.errorOccurred.disconnect(on_error)
        player.mediaStatusChanged.disconnect(on_status)
        timer.timeout.disconnect(on_timeout)
        player.stop()
        player.setSource(QUrl())
        player.setVideoSink(None)
        if progress is not None:
            progress.canceled.disconnect(on_cancel)
            progress.close()
            progress.deleteLater()
            QCoreApplication.sendPostedEvents(progress, QEvent.Type.DeferredDelete)
        owner.deleteLater()
        # CLI 可能不再进入事件循环，立即处理本次对象的延迟删除。
        QCoreApplication.sendPostedEvents(owner, QEvent.Type.DeferredDelete)
