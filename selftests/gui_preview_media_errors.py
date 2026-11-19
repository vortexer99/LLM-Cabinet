"""媒体错误和恢复状态回归；仅生成临时媒体，不导入或连接任何数据库。"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent
from PySide6.QtGui import QImage, QImageReader, QPainter, QPdfWriter
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.ui.preview import ImagePreview, PdfPreview, VideoPreview
from selftests._common import T
from selftests.gui_animated_images import GIF


def wait_for(predicate, timeout: float = 3.0) -> bool:
    """等待 Qt 后端信号，媒体不需要网络或外部文件。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return bool(predicate())


def run(root: Path, t: T) -> None:
    image = ImagePreview()
    video = VideoPreview()
    pdf = PdfPreview()
    image_errors: list[tuple[str, str]] = []
    video_errors: list[tuple[str, str]] = []
    pdf_errors: list[tuple[str, str]] = []
    finished: list[str] = []
    image.failed.connect(lambda *args: image_errors.append(args))
    video.failed.connect(lambda *args: video_errors.append(args))
    video.finished.connect(finished.append)
    pdf.failed.connect(lambda *args: pdf_errors.append(args))
    corrupt_image = root / "corrupt.png"
    corrupt_video = root / "corrupt.mp4"
    corrupt_pdf = root / "corrupt.pdf"
    for path in (corrupt_image, corrupt_video, corrupt_pdf):
        path.write_bytes(b"synthetic corrupt file")
    still = root / "still.png"
    pix = QImage(8, 8, QImage.Format_RGB32)
    pix.fill(0x123456)
    assert pix.save(str(still))
    animation = root / "animation.gif"
    animation.write_bytes(base64.b64decode(GIF))
    try:
        image.load(str(corrupt_image))
        t.assert_eq("损坏图像报告真实路径", image_errors[0][0], str(corrupt_image))
        t.assert_in("损坏图像标记解码阶段", "解码", image_errors[0][1])
        image.load(str(animation))
        movie = image._movie
        t.assert_true("生成动画正常加载", movie is not None)
        if movie is not None:
            last_frame = image._pix
            movie.error.emit(QImageReader.InvalidDataError)
            t.assert_eq("QMovie.error被传递", len(image_errors), 2)
            t.assert_in("QMovie错误包含具体类型", "InvalidDataError", image_errors[-1][1])
            image._on_animation_frame(0)
            t.assert_true("动画故障保留最后可见画面", image._pix is last_frame)
            image.load(str(still))
            movie.error.emit(QImageReader.InvalidDataError)
            t.assert_eq("换源丢弃旧动画错误", len(image_errors), 2)

        video.load(str(corrupt_video))
        t.assert_true("真实视频加载失败被捕获", wait_for(lambda: bool(video_errors)))
        t.assert_eq("真实无效视频只报告一次", len(video_errors), 1)
        t.assert_in("视频错误包含阶段", "解码", video_errors[0][1])
        video._on_media_status(QMediaPlayer.EndOfMedia)
        QApplication.processEvents()
        t.assert_eq("错误视频不能触发播完", finished, [])

        video.load(str(corrupt_video))
        old_error_callback = next(callback for signal, callback in video._player_connections
                                  if signal == video._player.errorOccurred)
        # 在同一事件轮次注入位置与播放，再模拟后端先 stop/reset 后报错。
        with patch.object(video._player, "playbackState", return_value=QMediaPlayer.PlayingState):
            video._on_state(QMediaPlayer.PlayingState)
            video._on_pos(1234)
        with patch.object(video._player, "playbackState", return_value=QMediaPlayer.StoppedState):
            video._on_state(QMediaPlayer.StoppedState)
            video._on_pos(0)
            video._on_error(QMediaPlayer.ResourceError, "synthetic offline error")
            QApplication.processEvents()
            t.assert_eq("错误前播放状态和位置保留", video.capture_resume_state(),
                        {"position": 1234, "playing": True})
        video.stop()
        before = len(video_errors)
        old_error_callback(QMediaPlayer.ResourceError, "late old source error")
        QApplication.processEvents()
        t.assert_eq("clear后旧错误失效", len(video_errors), before)
        t.assert_eq("显式停止清空恢复意图", video.capture_resume_state(),
                    {"position": 0, "playing": False})

        pdf.load(str(corrupt_pdf))
        t.assert_true("真实PDF解码错误被捕获", wait_for(lambda: bool(pdf_errors)))
        t.assert_in("PDF错误保留具体Qt类型", "InvalidFileFormat", pdf_errors[0][1])
        pdf.clear_doc()
        before = len(pdf_errors)
        valid_pdf = root / "three-pages.pdf"
        writer = QPdfWriter(str(valid_pdf))
        painter = QPainter(writer)
        for number in range(3):
            if number:
                writer.newPage()
            painter.drawText(20, 20, str(number + 1))
        painter.end()
        del painter, writer
        pdf.load(str(valid_pdf), resume={"page": 99})
        t.assert_true("PDF Ready后恢复并夹紧到最后一页",
                      wait_for(lambda: pdf._pdf_ready and pdf.capture_resume_state() == {"page": 2}))
        t.assert_eq("正常close/load不报告错误", len(pdf_errors), before)
    finally:
        image.clear_image()
        video.stop()
        pdf.clear_doc()
        for widget in (image, video, pdf):
            widget.close()
            widget.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QApplication.processEvents()


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    checks = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-preview-media-") as directory:
        run(Path(directory), checks)
    raise SystemExit(0 if checks.report() else 1)
