"""动态 WebP/GIF 预览：合成双色夹具与临时文件，不连接数据库。"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage, QMovie
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.ui.preview import PreviewPanel
from app.utils import detect_kind
from selftests._common import T
from selftests._preview_wait import wait_preview_probe

# 用 Pillow 从 12×12 纯红／纯蓝图生成，测试运行不依赖 Pillow 或网络。
WEBP = "UklGRoQAAABXRUJQVlA4WAoAAAACAAAACwAACwAAQU5JTQYAAAAAAAAAAABBTk1GKAAAAAAAAAAAAAsAAAsAAGQAAAJWUDhMDwAAAC8LwAIABxD9j/4HIqL/AQBBTk1GKAAAAAAAAAAAAAsAAAsAAGQAAABWUDhMDwAAAC8LwAIABxDR//4HIqL/AQA="
GIF = "R0lGODlhDAAMAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACgAAACwAAAAADAAMAAAIFgABCBxIsKDBgwgTKlzIsKHDhxARBgQAIfkEAQoAAQAsAAAAAAwADACBAAD/AAAAAAAAAAAACBYAAQgcSLCgwYMIEypcyLChw4cQEQYEADs="


def wait_for(predicate) -> bool:
    """允许慢机器的事件循环延迟，不依赖精确睡眠落在哪一帧。"""
    end = time.monotonic() + 2
    while time.monotonic() < end:
        if predicate():
            return True
        QTest.qWait(15)
    return False


def run(root: Path, t: T) -> None:
    panel = PreviewPanel()
    panel.resize(640, 480)
    panel.show()
    still = root / "static.webp"
    image = QImage(12, 12, QImage.Format_RGB32)
    image.fill(QColor("green"))
    t.assert_true("生成静态 WebP", image.save(str(still)))
    try:
        for suffix, data in (("webp", WEBP), ("gif", GIF)):
            path = root / f"animated.{suffix}"
            path.write_bytes(base64.b64decode(data))
            panel.show_file(str(path))
            wait_preview_probe(panel)
            preview = panel._image
            movie = preview._movie
            t.assert_eq(f"{suffix} 类型为图片", detect_kind(path), "image")
            t.assert_true(f"{suffix} 自动播放", movie is not None and movie.state() == QMovie.Running)
            if movie is None:
                continue
            t.assert_eq(f"{suffix} 不缓存全部帧", movie.cacheMode(), QMovie.CacheNone)
            t.assert_true(f"{suffix} 有播放控制", preview._btn_animation.isVisible())
            first_frame = movie.currentFrameNumber()
            t.assert_true(f"{suffix} 实际换帧", wait_for(lambda: movie.currentFrameNumber() != first_frame))
            preview._btn_animation.click()
            t.assert_eq(f"{suffix} 暂停", movie.state(), QMovie.Paused)
            paused_frame = movie.currentFrameNumber()
            QTest.qWait(250)
            t.assert_eq(f"{suffix} 暂停不换帧", movie.currentFrameNumber(), paused_frame)
            panel.show_file(str(path), autoplay=True)
            t.assert_true(f"{suffix} 重复选择不重建播放器", preview._movie is movie)
            t.assert_eq(f"{suffix} 重复选择保留暂停", movie.state(), QMovie.Paused)
            captured = panel.capture_pixmap()
            t.assert_true(f"{suffix} 截图为当前帧", captured is not None
                          and captured.toImage() == movie.currentImage())
            preview._canvas.set_zoom_100()
            preview._canvas._offset = QPointF(13, 17)
            preview._btn_animation.click()
            t.assert_true(f"{suffix} 恢复换帧", wait_for(lambda: movie.currentFrameNumber() != paused_frame))
            t.assert_eq(f"{suffix} 换帧保留缩放", preview._canvas.current_scale(), 1.0)
            t.assert_eq(f"{suffix} 换帧保留偏移", preview._canvas._offset, QPointF(13, 17))
            panel.show_file(str(still))
            t.assert_eq(f"{suffix} 切换停止旧动画", movie.state(), QMovie.NotRunning)
            t.assert_eq(f"{suffix} 切换释放旧文件", movie.fileName(), "")
            wait_preview_probe(panel)
            t.assert_eq(f"{suffix} 静态无动画实例", preview._movie, None)
            t.assert_true(f"{suffix} 静态隐藏播放按钮", preview._btn_animation.isHidden())
            t.assert_true(f"{suffix} 静态正确显示", preview._pix is not None and not preview._pix.isNull())
            t.assert_true(f"{suffix} 切换重置缩放", preview._canvas._fit_mode)
            panel.show_file(str(path))
            panel.show_file(None)
            t.assert_eq(f"{suffix} 清空停止动画", preview._movie, None)
            t.assert_eq(f"{suffix} 清空画布", preview._pix, None)
        corrupt = root / "corrupt.webp"
        corrupt.write_bytes(b"invalid webp")
        panel.show_file(str(corrupt))
        wait_preview_probe(panel)
        t.assert_eq("损坏图片无动画", panel._image._movie, None)
        t.assert_eq("损坏图片无画面", panel._image._pix, None)
    finally:
        panel.show_file(None)
        panel.close()


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-animated-images-") as tmp:
        run(Path(tmp), t)
    raise SystemExit(0 if t.report() else 1)
