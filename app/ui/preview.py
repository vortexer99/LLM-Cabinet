"""图片 / 视频 / PDF 内嵌预览组件。

task #40 增强：
- 图片：滚轮缩放（以光标为中心）/ 拖拽平移 / 双击 适应窗口↔100%，底部控制条
- 视频：音量滑条 + 倍速 + 空格播放暂停
- PDF：页码跳转 + 缩放模式 + 页码指示
"""
from __future__ import annotations

import os
import ntpath
import logging
from collections.abc import Callable

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QImageReader, QKeySequence, QMovie, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..utils import detect_kind, open_with_default_app
from .palette import current as _current_palette
from . import dialogs
from .preview_access import FileAccessMonitor, ProbeResult


# ---------- 图片 ----------------------------------------------------------------
class _ImageCanvas(QWidget):
    """图片画布：缩放 + 平移 + 双击切换。由 ImagePreview 托管。"""

    view_changed = Signal()

    MIN_SCALE = 0.1
    MAX_SCALE = 8.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 160)
        self._pix: QPixmap | None = None
        self._zoom = 1.0            # 自由模式缩放
        self._offset = QPointF(0, 0)  # 自由模式平移（屏幕像素）
        self._fit_mode = True       # True = 适应窗口
        self._drag_pos: QPointF | None = None

    # ---- 视图状态 ----
    def set_pixmap(self, pix: QPixmap | None) -> None:
        self._pix = pix
        self.reset_view()

    def reset_view(self) -> None:
        self._fit_mode = True
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self.update()
        self.view_changed.emit()

    def set_zoom_100(self) -> None:
        self._fit_mode = False
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self.update()
        self.view_changed.emit()

    def current_scale(self) -> float:
        return self._fit_scale() if self._fit_mode else self._zoom

    def _fit_scale(self) -> float:
        if self._pix is None or self._pix.isNull():
            return 1.0
        w = max(1, self.width())
        h = max(1, self.height())
        return min(w / self._pix.width(), h / self._pix.height())

    def zoom_step(self, factor: float, center: QPointF | None = None) -> None:
        """按 factor 缩放；center 为缩放锚点（缺省画布中心）。"""
        if self._pix is None:
            return
        if center is None:
            center = QPointF(self.width() / 2, self.height() / 2)
        old_s = self.current_scale()
        new_s = max(self.MIN_SCALE, min(self.MAX_SCALE, old_s * factor))
        if new_s == old_s:
            return
        # 锚点下的图像点保持不动：x0' = c - ip*s2
        x0 = (self.width() - self._pix.width() * old_s) / 2 + self._offset.x()
        y0 = (self.height() - self._pix.height() * old_s) / 2 + self._offset.y()
        ipx = (center.x() - x0) / old_s
        ipy = (center.y() - y0) / old_s
        self._fit_mode = False
        self._zoom = new_s
        self._offset = QPointF(
            center.x() - ipx * new_s - (self.width() - self._pix.width() * new_s) / 2,
            center.y() - ipy * new_s - (self.height() - self._pix.height() * new_s) / 2,
        )
        self.update()
        self.view_changed.emit()

    # ---- 事件 ----
    def paintEvent(self, _ev) -> None:  # noqa: N802
        pal = _current_palette()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(pal.bg1))
        if self._pix is None or self._pix.isNull():
            painter.setPen(QColor(pal.fg2))
            painter.drawText(self.rect(), Qt.AlignCenter, "（无预览）")
            painter.end()
            return
        s = self.current_scale()
        dw = self._pix.width() * s
        dh = self._pix.height() * s
        x0 = (self.width() - dw) / 2 + self._offset.x()
        y0 = (self.height() - dh) / 2 + self._offset.y()
        painter.setRenderHint(QPainter.SmoothPixmapTransform, s < 1.0)
        painter.drawPixmap(
            QRectF(x0, y0, dw, dh),
            self._pix,
            QRectF(self._pix.rect()),
        )
        painter.end()

    def wheelEvent(self, ev) -> None:  # noqa: N802
        if self._pix is None:
            return
        factor = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
        self.zoom_step(factor, ev.position())

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton and self._pix is not None:
            self._drag_pos = ev.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._drag_pos is not None and self._pix is not None:
            delta = ev.position() - self._drag_pos
            self._drag_pos = ev.position()
            if self._fit_mode:
                # 适应模式下拖动即进入自由模式
                self._zoom = self._fit_scale()
                self._fit_mode = False
                self._offset = QPointF(0, 0)
            self._offset += delta
            self.update()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton:
            self._drag_pos = None
            self.unsetCursor()

    def mouseDoubleClickEvent(self, _ev) -> None:  # noqa: N802
        if self._pix is None:
            return
        if self._fit_mode:
            self.set_zoom_100()
        else:
            self.reset_view()

    def resizeEvent(self, ev) -> None:  # noqa: N802
        # 适应模式下 resize 自动重算（paint 时取 _fit_scale，无需处理）；
        # 自由模式保持偏移，不做额外修正
        super().resizeEvent(ev)
        self.update()


class ImagePreview(QWidget):
    """图片预览 + 底部控制条（task #40 T1）。

    对外保持 ``load`` / ``clear_image`` / ``_pix`` 接口不变。
    """

    failed = Signal(str, str)
    loaded = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._canvas = _ImageCanvas(self)
        self._canvas.view_changed.connect(self._sync_zoom_label)
        self._movie: QMovie | None = None
        self._image_generation = 0
        self._image_path = ""
        self._image_failed = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._canvas, 1)

        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        btn_out = QPushButton("−")
        btn_out.setFixedWidth(32)
        btn_out.setToolTip("缩小")
        btn_out.clicked.connect(lambda: self._canvas.zoom_step(1 / 1.25))
        btn_in = QPushButton("＋")
        btn_in.setFixedWidth(32)
        btn_in.setToolTip("放大")
        btn_in.clicked.connect(lambda: self._canvas.zoom_step(1.25))
        btn_fit = QPushButton("适应窗口")
        btn_fit.clicked.connect(self._on_fit)
        btn_100 = QPushButton("1:1")
        btn_100.setFixedWidth(44)
        btn_100.setToolTip("实际大小")
        btn_100.clicked.connect(self._on_100)
        self._lbl_zoom = QLabel("—")
        self._lbl_zoom.setStyleSheet(f"color:{_current_palette().fg2};")
        for w in (btn_out, btn_in, btn_fit, btn_100, self._lbl_zoom):
            bar.addWidget(w)
        self._btn_animation = QPushButton("暂停")
        self._btn_animation.setToolTip("暂停／继续播放动图")
        self._btn_animation.clicked.connect(self._toggle_animation)
        self._btn_animation.hide()
        bar.addWidget(self._btn_animation)
        bar.addStretch(1)
        lay.addLayout(bar)

    # ---- 对外接口 ----
    @property
    def _pix(self) -> QPixmap | None:
        return self._canvas._pix

    def load(self, path: str) -> None:
        self.clear_image()
        self._image_path = path
        generation = self._image_generation
        try:
            self._load_image(path, generation)
        except Exception as exc:
            self._fail_image(path, generation, f"图片加载／解码：{type(exc).__name__}: {exc}")

    def _load_image(self, path: str, generation: int) -> None:
        """Qt 图像解码仅在 UI 线程执行，失败交给统一状态面板。"""
        reader = QImageReader(path)
        animated = reader.supportsAnimation() and reader.imageCount() > 1
        decode_reason = reader.errorString()
        # 释放探测器的文件句柄后再交给 QMovie，不缓存整段动画到内存。
        del reader
        if animated:
            movie = QMovie(path, parent=self)
            if movie.isValid():
                self._movie = movie
                movie.frameChanged.connect(
                    lambda frame: self._on_animation_frame(frame)
                    if generation == self._image_generation and movie is self._movie else None
                )
                movie.stateChanged.connect(
                    lambda state: self._sync_animation_button(state)
                    if generation == self._image_generation and movie is self._movie else None
                )
                def on_movie_error(error) -> None:
                    if generation == self._image_generation and movie is self._movie:
                        self._fail_image(
                            path, generation,
                            f"动画解码：QImageReader.{getattr(error, 'name', error)}: {movie.lastErrorString()}",
                        )

                movie.error.connect(on_movie_error)
                self._btn_animation.show()
                movie.start()
                if not self._image_failed:
                    self.loaded.emit(path)
                return
            decode_reason = movie.lastErrorString()
            movie.deleteLater()
            self._fail_image(path, generation, f"动画解码：QMovie.Invalid: {decode_reason}")
            return
        pix = QPixmap(path)
        self._canvas.set_pixmap(pix if not pix.isNull() else None)
        if pix.isNull():
            self._fail_image(path, generation, f"图片解码：QImageReader.DecodeError: {decode_reason}")
        else:
            self.loaded.emit(path)

    def _fail_image(self, path: str, generation: int, reason: str) -> None:
        if generation != self._image_generation or path != self._image_path or self._image_failed:
            return
        self._image_failed = True
        self.failed.emit(path, reason)

    def clear_image(self) -> None:
        self._image_generation += 1
        self._image_path = ""
        self._image_failed = False
        if self._movie is not None:
            movie, self._movie = self._movie, None
            movie.stop()
            movie.setFileName("")
            movie.deleteLater()
        self._btn_animation.hide()
        self._canvas.set_pixmap(None)

    # ---- 控制条动作 ----
    def _on_animation_frame(self, _frame: int) -> None:
        """换帧只更新画布内容，不重置用户的缩放和平移。"""
        if self._movie is None or self._image_failed:
            return
        pix = self._movie.currentPixmap()
        if pix.isNull():
            return
        self._canvas._pix = pix
        self._canvas.update()
        self._sync_zoom_label()

    def _sync_animation_button(self, _state: QMovie.MovieState) -> None:
        running = self._movie is not None and self._movie.state() == QMovie.Running
        self._btn_animation.setText("暂停" if running else "播放")

    def _toggle_animation(self) -> None:
        if self._movie is None:
            return
        if self._movie.state() == QMovie.NotRunning:
            self._movie.start()
        else:
            self._movie.setPaused(self._movie.state() == QMovie.Running)

    def _on_fit(self) -> None:
        self._canvas.reset_view()

    def _on_100(self) -> None:
        self._canvas.set_zoom_100()

    def _sync_zoom_label(self) -> None:
        if self._canvas._pix is None:
            self._lbl_zoom.setText("—")
        else:
            self._lbl_zoom.setText(f"{self._canvas.current_scale() * 100:.0f}%")


# ---------- 视频 ----------------------------------------------------------------
class VideoPreview(QWidget):
    finished = Signal(str)
    failed = Signal(str, str)
    loaded = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self._player = QMediaPlayer(self)
        self._media_generation = 0
        self._media_path = ""
        self._media_failed = False
        self._media_ready = False
        self._switching_media = False
        self._resume_position = 0
        self._resume_playing = False
        self._error_resume_position = 0
        self._error_resume_playing = False
        self._pending_resume: int | None = None
        self._resume_autoplay = False
        self._player_connections: list[tuple[object, Callable]] = []
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._video = QVideoWidget(self)
        self._video.setStyleSheet("background:#000;")
        self._player.setVideoOutput(self._video)

        self._btn_play = QPushButton("播放")
        self._btn_play.clicked.connect(self._toggle)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.sliderMoved.connect(self._player.setPosition)

        self._lbl_time = QLabel("00:00 / 00:00")

        # task #40 T2：音量 + 倍速
        self._audio.setVolume(0.8)
        self._vol = QSlider(Qt.Horizontal)
        self._vol.setRange(0, 100)
        self._vol.setValue(80)
        self._vol.setFixedWidth(70)
        self._vol.setToolTip("音量")
        self._vol.valueChanged.connect(lambda v: self._audio.setVolume(v / 100))

        self._cmb_rate = QComboBox()
        for label, rate in (
            ("0.5x", 0.5), ("0.75x", 0.75), ("1x", 1.0),
            ("1.25x", 1.25), ("1.5x", 1.5), ("2x", 2.0),
        ):
            self._cmb_rate.addItem(label, rate)
        self._cmb_rate.setCurrentIndex(2)
        self._cmb_rate.setToolTip("倍速")
        self._cmb_rate.currentIndexChanged.connect(
            lambda _i: self._player.setPlaybackRate(self._cmb_rate.currentData())
        )

        ctrl = QHBoxLayout()
        ctrl.addWidget(self._btn_play)
        ctrl.addWidget(self._slider, 1)
        ctrl.addWidget(self._lbl_time)
        ctrl.addWidget(QLabel("🔊"))
        ctrl.addWidget(self._vol)
        ctrl.addWidget(self._cmb_rate)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._video, 1)
        lay.addLayout(ctrl)

        self._bind_player_callbacks()

    def _bind_player_callbacks(self) -> None:
        """每次换源绑定代际；已排队的旧 Qt 信号也不能影响新媒体。"""
        for signal, callback in self._player_connections:
            signal.disconnect(callback)
        self._player_connections.clear()
        generation = self._media_generation
        for name, handler in (
            ("positionChanged", self._on_pos), ("durationChanged", self._on_dur),
            ("playbackStateChanged", self._on_state),
            ("mediaStatusChanged", self._on_media_status),
            ("errorOccurred", self._on_error), ("seekableChanged", self._on_seekable),
        ):
            signal = getattr(self._player, name, None)
            if signal is None:
                continue

            def callback(*args, _handler=handler, _generation=generation) -> None:
                if _generation == self._media_generation and not self._switching_media:
                    _handler(*args)

            signal.connect(callback)
            self._player_connections.append((signal, callback))

    def _has_media_error(self) -> bool:
        from PySide6.QtMultimedia import QMediaPlayer
        error = getattr(self._player, "error", None)
        return (self._media_failed
                or self._player.mediaStatus() == QMediaPlayer.InvalidMedia
                or (callable(error) and error() != QMediaPlayer.NoError))

    def _on_error(self, error, message: str = "") -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        if error == QMediaPlayer.NoError:
            return
        self._fail_video(f"视频加载／解码：QMediaPlayer.{getattr(error, 'name', error)}: "
                         f"{message or self._player.errorString()}")

    def _fail_video(self, reason: str) -> None:
        if not self._media_path or self._switching_media or self._media_failed:
            return
        self._media_failed = True
        # 部分后端隔一轮才报告错误，普通 stop 的 UI 收尾不能抹掉故障前快照。
        self._resume_position = self._error_resume_position
        self._resume_playing = self._error_resume_playing
        self._pending_resume = None
        generation, path = self._media_generation, self._media_path

        def dispatch() -> None:
            if generation == self._media_generation and path == self._media_path:
                self.failed.emit(path, reason)

        QTimer.singleShot(0, self, dispatch)

    def _on_media_status(self, status) -> None:
        """播完后延迟派发，避开播放器状态转换中的重入；换源后丢弃旧事件。"""
        from PySide6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.InvalidMedia:
            self._fail_video(f"视频加载／解码：QMediaPlayer.InvalidMedia: {self._player.errorString()}")
            return
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            if self._media_path and not self._has_media_error():
                if not self._media_ready:
                    self._media_ready = True
                    self._try_restore_video()
                    self.loaded.emit(self._media_path)
                else:
                    self._try_restore_video()
            return
        if status != QMediaPlayer.EndOfMedia:
            return
        if self._has_media_error():
            return
        self._resume_playing = False
        self._error_resume_playing = False
        generation = self._media_generation
        source = self._player.source()

        def dispatch() -> None:
            if (generation == self._media_generation and not source.isEmpty()
                    and self._player.source() == source
                    and not self._has_media_error()
                    and self._player.mediaStatus() == QMediaPlayer.EndOfMedia):
                self.finished.emit(source.toLocalFile())

        QTimer.singleShot(0, self, dispatch)

    def load(self, path: str, *, autoplay: bool = False, resume: dict | None = None) -> None:
        """恢复仅使用位置，播放意图由面板的 autoplay 显式决定。"""
        self._media_generation += 1
        self._switching_media = True
        try:
            self._player.stop()
            self._player.setSource(QUrl())
            self._media_path = path
            self._media_failed = False
            self._media_ready = False
            self._resume_position = max(0, int((resume or {}).get("position", 0)))
            self._resume_playing = autoplay
            self._error_resume_position = self._resume_position
            self._error_resume_playing = autoplay
            self._pending_resume = self._resume_position if resume is not None else None
            self._resume_autoplay = autoplay
            self._bind_player_callbacks()
        except Exception as exc:
            self._media_path = path
            self._media_failed = False
            self._switching_media = False
            self._fail_video(f"视频准备：{type(exc).__name__}: {exc}")
            return
        finally:
            self._switching_media = False
        try:
            self._player.setSource(QUrl.fromLocalFile(path))
            if autoplay and resume is None and not self._media_failed:
                self._player.play()
            self._try_restore_video()
        except Exception as exc:
            self._fail_video(f"视频加载：{type(exc).__name__}: {exc}")

    def stop(self) -> None:
        self._media_generation += 1
        self._media_path = ""
        self._pending_resume = None
        self._resume_position = 0
        self._resume_playing = False
        self._error_resume_position = 0
        self._error_resume_playing = False
        self._media_failed = False
        self._media_ready = False
        self._switching_media = True
        try:
            self._player.stop()
            self._player.setSource(QUrl())
        finally:
            self._switching_media = False

    def capture_resume_state(self) -> dict:
        from PySide6.QtMultimedia import QMediaPlayer
        playing = self._resume_playing
        if not self._has_media_error() and self._pending_resume is None:
            playing = self._player.playbackState() == QMediaPlayer.PlayingState
        return {"position": self._resume_position, "playing": playing}

    def _on_seekable(self, _seekable: bool) -> None:
        self._try_restore_video()

    def _try_restore_video(self) -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        if self._pending_resume is None or self._media_failed:
            return
        if self._player.mediaStatus() not in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            return
        duration = self._player.duration()
        if duration <= 0 or not self._player.isSeekable():
            return
        position = min(self._pending_resume, duration)
        self._pending_resume = None
        self._player.setPosition(position)
        self._resume_position = position
        self._error_resume_position = position
        if self._resume_autoplay:
            self._player.play()
        else:
            self._resume_playing = False
            self._error_resume_playing = False

    def _toggle(self) -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_state(self, st) -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        self._btn_play.setText("暂停" if st == QMediaPlayer.PlayingState else "播放")
        if st == QMediaPlayer.PlayingState:
            self._resume_playing = True
            self._error_resume_playing = True
        elif st == QMediaPlayer.PausedState:
            self._resume_playing = False
            self._error_resume_playing = False
        elif st == QMediaPlayer.StoppedState:
            # 后端可能先 stop/reset，再发 error；下个事件轮次才确认普通停止。
            generation = self._media_generation

            def settle_stop() -> None:
                if (generation == self._media_generation and not self._has_media_error()
                        and self._pending_resume is None
                        and self._player.playbackState() == QMediaPlayer.StoppedState):
                    self._resume_playing = False
                    self._resume_position = self._player.position()

            QTimer.singleShot(0, self, settle_stop)

    def _on_pos(self, pos: int) -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        if (not self._media_failed and self._pending_resume is None
                and (pos > 0 or self._player.playbackState() != QMediaPlayer.StoppedState)):
            self._resume_position = max(0, pos)
            self._error_resume_position = self._resume_position
        self._slider.blockSignals(True)
        self._slider.setValue(pos)
        self._slider.blockSignals(False)
        self._update_time()

    def _on_dur(self, dur: int) -> None:
        self._slider.setRange(0, max(0, dur))
        self._update_time()
        self._try_restore_video()

    def _update_time(self) -> None:
        def fmt(ms: int) -> str:
            s = max(0, ms) // 1000
            return f"{s // 60:02d}:{s % 60:02d}"
        self._lbl_time.setText(
            f"{fmt(self._player.position())} / {fmt(self._player.duration())}"
        )

    def capture_current_frame(self) -> QPixmap | None:
        """抓视频当前帧。优先用 QVideoSink，失败则退化为 grab 视频控件。"""
        try:
            sink = self._video.videoSink()
            if sink is not None:
                frame = sink.videoFrame()
                if frame.isValid():
                    img = frame.toImage()
                    if not img.isNull():
                        return QPixmap.fromImage(img)
        except Exception:
            logging.getLogger(__name__).exception("获取视频当前帧失败，尝试窗口截图")
        try:
            pix = self._video.grab()
            return pix if not pix.isNull() else None
        except Exception:
            logging.getLogger(__name__).exception("获取视频预览截图失败")
            return None


# ---------- PDF ---------------------------------------------------------------
class PdfPreview(QWidget):
    """Qt 6.4+ 提供 QtPdf / QtPdfWidgets。无则 fallback 到提示。"""

    failed = Signal(str, str)
    loaded = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._available = True
        self._pdf_generation = 0
        self._pdf_path = ""
        self._pdf_failed = False
        self._pdf_ready = False
        self._resume_page = 0
        self._pending_page: int | None = None
        self._doc_status_callback: Callable | None = None
        try:
            from PySide6.QtPdf import QPdfDocument           # noqa
            from PySide6.QtPdfWidgets import QPdfView        # noqa
        except ImportError:
            self._available = False

        if self._available:
            from PySide6.QtPdf import QPdfDocument
            from PySide6.QtPdfWidgets import QPdfView
            self._doc = QPdfDocument(self)
            self._view = QPdfView(self)
            self._view.setDocument(self._doc)
            try:
                self._view.setPageMode(QPdfView.PageMode.MultiPage)
                self._view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            except (AttributeError, RuntimeError):
                logging.getLogger(__name__).exception("初始化 PDF 页面模式失败")
            lay.addWidget(self._view, 1)

            # task #40 T3：页码跳转 + 缩放控制条
            nav = self._view.pageNavigator()
            bar = QHBoxLayout()
            bar.setContentsMargins(4, 2, 4, 2)
            btn_prev = QPushButton("‹")
            btn_prev.setFixedWidth(32)
            btn_prev.setToolTip("上一页")
            btn_prev.clicked.connect(lambda: self._jump(-1))
            self._ed_page = QLineEdit("1")
            self._ed_page.setFixedWidth(44)
            self._ed_page.setAlignment(Qt.AlignCenter)
            self._ed_page.returnPressed.connect(self._goto_page)
            self._lbl_pages = QLabel("/ 0")
            btn_next = QPushButton("›")
            btn_next.setFixedWidth(32)
            btn_next.setToolTip("下一页")
            btn_next.clicked.connect(lambda: self._jump(1))
            self._cmb_zoom = QComboBox()
            for label, data in (
                ("适应宽度", "fit_width"), ("适应页面", "fit_view"),
                ("50%", 0.5), ("100%", 1.0), ("150%", 1.5), ("200%", 2.0),
            ):
                self._cmb_zoom.addItem(label, data)
            self._cmb_zoom.currentIndexChanged.connect(self._on_zoom_changed)
            for w in (btn_prev, self._ed_page, self._lbl_pages, btn_next):
                bar.addWidget(w)
            bar.addStretch(1)
            bar.addWidget(self._cmb_zoom)
            lay.addLayout(bar)

            if nav is not None:
                nav.currentPageChanged.connect(self._on_page_changed)
        else:
            tip = QLabel("当前 PySide6 未安装 QtPdf 模块，无法内嵌预览 PDF。\n点击右侧『用默认程序打开』查看。")
            tip.setAlignment(Qt.AlignCenter)
            tip.setStyleSheet(f"color:{_current_palette().fg2};")
            lay.addWidget(tip)

    # ---- 页码 / 缩放 ----
    def _jump(self, delta: int) -> None:
        nav = self._view.pageNavigator()
        if nav is None:
            return
        page = max(0, min(self._doc.pageCount() - 1, nav.currentPage() + delta))
        nav.jump(page, QPointF(0, 0), 0)

    def _goto_page(self) -> None:
        nav = self._view.pageNavigator()
        if nav is None:
            return
        try:
            page = int(self._ed_page.text()) - 1
        except ValueError:
            return
        page = max(0, min(self._doc.pageCount() - 1, page))
        nav.jump(page, QPointF(0, 0), 0)

    def _on_page_changed(self, page: int) -> None:
        self._ed_page.setText(str(page + 1))
        if self._pdf_path and not self._pdf_failed:
            self._resume_page = max(0, page)

    def _on_doc_status(self, status) -> None:
        from PySide6.QtPdf import QPdfDocument
        self._lbl_pages.setText(f"/ {self._doc.pageCount()}")
        if not self._pdf_path:
            return
        if status == QPdfDocument.Status.Error:
            error = self._doc.error()
            self._fail_pdf(self._pdf_error_reason(error))
        elif status == QPdfDocument.Status.Ready and not self._pdf_failed:
            count = self._doc.pageCount()
            if count <= 0:
                self._fail_pdf("PDF 解码：QPdfDocument.EmptyDocument: 文档没有可显示的页面")
                return
            page = max(0, min(count - 1, self._pending_page or 0))
            self._pending_page = None
            nav = self._view.pageNavigator()
            if nav is not None:
                nav.jump(page, QPointF(0, 0), 0)
            self._resume_page = page
            self._ed_page.setText(str(page + 1))
            if not self._pdf_ready:
                self._pdf_ready = True
                self.loaded.emit(self._pdf_path)

    def _fail_pdf(self, reason: str) -> None:
        if not self._pdf_path or self._pdf_failed:
            return
        self._pdf_failed = True
        generation, path = self._pdf_generation, self._pdf_path

        def dispatch() -> None:
            if generation == self._pdf_generation and path == self._pdf_path:
                self.failed.emit(path, reason)

        QTimer.singleShot(0, self, dispatch)

    @staticmethod
    def _pdf_error_reason(error) -> str:
        """QPdfDocument 没有 errorString，保留枚举类型并补充可读原因。"""
        name = getattr(error, "name", str(error))
        message = {
            "Unknown": "Qt PDF 后端报告未知错误",
            "DataNotYetAvailable": "文档数据尚未完整提供",
            "FileNotFound": "无法打开 PDF 源文件",
            "InvalidFileFormat": "文件内容不是可解码的 PDF",
            "IncorrectPassword": "文档需要密码或密码不正确",
            "UnsupportedSecurityScheme": "不支持此文档的加密方案",
        }.get(name, "Qt PDF 后端无法载入文档")
        return f"PDF 加载／解码：QPdfDocument.{name}: {message}"

    def _on_zoom_changed(self, _i: int) -> None:
        from PySide6.QtPdfWidgets import QPdfView
        data = self._cmb_zoom.currentData()
        try:
            if data == "fit_width":
                self._view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            elif data == "fit_view":
                self._view.setZoomMode(QPdfView.ZoomMode.FitInView)
            else:
                self._view.setZoomMode(QPdfView.ZoomMode.Custom)
                self._view.setZoomFactor(float(data))
        except (TypeError, ValueError, RuntimeError):
            logging.getLogger(__name__).exception("调整 PDF 缩放失败")

    # ---- 对外接口 ----
    def load(self, path: str, *, resume: dict | None = None) -> None:
        self.clear_doc()
        self._pdf_path = path
        self._pending_page = max(0, int((resume or {}).get("page", 0)))
        self._resume_page = self._pending_page
        if not self._available:
            self._fail_pdf("PDF 初始化：ImportError: 当前 PySide6 未安装 QtPdf 模块")
            return
        from PySide6.QtPdf import QPdfDocument
        generation = self._pdf_generation

        def on_status(status) -> None:
            if generation == self._pdf_generation and path == self._pdf_path:
                self._on_doc_status(status)

        self._doc_status_callback = on_status
        self._doc.statusChanged.connect(on_status)
        try:
            error = self._doc.load(path)
            if error is not None and error != QPdfDocument.Error.None_:
                self._fail_pdf(self._pdf_error_reason(error))
        except Exception as exc:
            self._fail_pdf(f"PDF 加载：{type(exc).__name__}: {exc}")

    def clear_doc(self) -> None:
        self._pdf_generation += 1
        self._pdf_path = ""
        self._pending_page = None
        self._resume_page = 0
        self._pdf_failed = False
        self._pdf_ready = False
        if self._available:
            if self._doc_status_callback is not None:
                self._doc.statusChanged.disconnect(self._doc_status_callback)
                self._doc_status_callback = None
            self._doc.close()
            self._lbl_pages.setText("/ 0")

    def capture_resume_state(self) -> dict:
        return {"page": self._resume_page}

    def capture_current_page(self) -> QPixmap | None:
        """渲染当前可见的第一页为 QPixmap（用作封面）。"""
        if not self._available:
            return None
        try:
            from PySide6.QtCore import QSize
            doc = self._doc
            if doc.pageCount() <= 0:
                return None
            # 取可见区域里第一个页面索引（QPdfView 没暴露 currentPage()，
            # 退化为 0；够覆盖"封面=第一页"的常见需求）
            page = 0
            try:
                nav = self._view.pageNavigator()
                if nav is not None:
                    page = max(0, nav.currentPage())
            except Exception:
                logging.getLogger(__name__).exception("读取 PDF 当前页面失败，使用第一页截图")
            size = doc.pagePointSize(page)
            # 用一个合理的分辨率渲染（按宽 1200px）
            target_w = 1200
            ratio = target_w / max(1.0, size.width())
            target = QSize(target_w, max(1, int(size.height() * ratio)))
            img = doc.render(page, target)
            if img.isNull():
                return None
            return QPixmap.fromImage(img)
        except Exception:
            logging.getLogger(__name__).exception("渲染 PDF 当前页面失败")
            return None


# ---------- 综合预览面板 -------------------------------------------------------
class PreviewPanel(QWidget):
    """根据文件类型自动选择内嵌组件；不支持的类型显示提示+打开按钮。"""

    previous_requested = Signal()
    next_requested = Signal()
    focus_requested = Signal()
    fullscreen_requested = Signal()
    escape_requested = Signal()
    video_finished = Signal(str)
    status_changed = Signal(str)
    relink_requested = Signal()

    def __init__(self, parent=None, *, wheel_navigation_enabled: Callable[[], bool] | None = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self._wheel_navigation_enabled = wheel_navigation_enabled or (lambda: False)
        self._wheel_delta = 0
        self._wheel_unit = ""
        self._current: str | None = None
        self._file_id: int | None = None
        self._label = ""
        self._generation = 0
        self._access_state = "empty"
        self._closed = False
        self._loaded_path: str | None = None
        self._last_error: ProbeResult | None = None
        self._resume: dict = {}
        self._requested_autoplay = False
        self._probe_purpose = "load"
        self._failure_reason = ""
        self._monitor = FileAccessMonitor(self)
        self._monitor.result_ready.connect(self._on_access_result)
        self._external_monitor = FileAccessMonitor(self)
        self._external_monitor.result_ready.connect(self._on_external_result)
        self._offline_timer = QTimer(self)
        self._offline_timer.setInterval(5000)
        self._offline_timer.timeout.connect(self._check_current_access)
        self._image = ImagePreview()
        self._video = VideoPreview()
        self._video.finished.connect(self._on_video_finished)
        self._pdf = PdfPreview()
        for media in (self._image, self._video, self._pdf):
            media.failed.connect(self._on_media_failed)
            media.loaded.connect(self._on_media_loaded)

        self._other = QLabel("此类型不支持内嵌预览\n请点击下方按钮以默认程序打开")
        self._other.setAlignment(Qt.AlignCenter)
        self._other.setStyleSheet(f"color:{_current_palette().fg2};")

        self._stack = QStackedWidget()
        self._stack.addWidget(self._image)   # 0
        self._stack.addWidget(self._video)   # 1
        self._stack.addWidget(self._pdf)     # 2
        self._stack.addWidget(self._other)   # 3

        self._lbl_path = QLabel("（未选择文件）")
        self._lbl_path.setStyleSheet(f"color:{_current_palette().fg2};")
        # 单行 + 中间省略，避免长路径换行把整个面板撑宽
        self._lbl_path.setWordWrap(False)
        self._lbl_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # 关键：允许标签收缩到任意宽度，否则 QLabel 的 sizeHint 会拉宽父布局
        self._lbl_path.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._lbl_path.setMinimumWidth(0)
        self._full_path: str = ""

        self._btn_open = QPushButton("用默认程序打开")
        self._btn_open.clicked.connect(self._open_external)
        self._btn_open.setEnabled(False)

        self._access_banner = QWidget()
        access_layout = QVBoxLayout(self._access_banner)
        access_layout.setContentsMargins(4, 4, 4, 4)
        self._lbl_access = QLabel()
        self._lbl_access.setWordWrap(True)
        self._lbl_access.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._lbl_access.setMinimumWidth(0)
        access_layout.addWidget(self._lbl_access)
        access_buttons = QHBoxLayout()
        self._btn_retry = QPushButton("重试")
        self._btn_retry.clicked.connect(lambda: self.retry_current(manual=True))
        self._btn_relink = QPushButton("重关联…")
        self._btn_relink.clicked.connect(self.relink_requested.emit)
        self._btn_details = QPushButton("查看详情")
        self._btn_details.clicked.connect(self._show_error_details)
        self._btn_copy_error = QPushButton("复制详情")
        self._btn_copy_error.clicked.connect(self._copy_error_details)
        for button in (self._btn_retry, self._btn_relink, self._btn_details, self._btn_copy_error):
            access_buttons.addWidget(button)
        access_buttons.addStretch(1)
        access_layout.addLayout(access_buttons)
        self._access_banner.hide()

        bar = QHBoxLayout()
        bar.addWidget(self._lbl_path, 1)
        bar.addWidget(self._btn_open)

        navigation = QHBoxLayout()
        self._btn_previous = QPushButton("← 上一个文件")
        self._btn_next = QPushButton("下一个文件 →")
        self._btn_previous.setToolTip("选择当前列表中的上一个文件")
        self._btn_next.setToolTip("选择当前列表中的下一个文件")
        for button in (self._btn_previous, self._btn_next):
            button.setProperty("flat", True)
            button.setEnabled(False)
        self._btn_previous.clicked.connect(lambda: self.previous_requested.emit())
        self._btn_next.clicked.connect(lambda: self.next_requested.emit())
        self._lbl_file_position = QLabel("0 / 0")
        self._lbl_file_position.setAlignment(Qt.AlignCenter)
        self._lbl_file_position.setProperty("muted", True)
        navigation.addWidget(self._btn_previous)
        navigation.addStretch(1)
        navigation.addWidget(self._lbl_file_position)
        navigation.addStretch(1)
        navigation.addWidget(self._btn_next)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        view_bar = QHBoxLayout()
        view_title = QLabel("预览")
        view_title.setProperty("h2", True)
        view_bar.addWidget(view_title)
        view_bar.addStretch(1)
        self._btn_focus = QPushButton("放大预览")
        self._btn_focus.setToolTip("收起周围面板，专注查看当前文件；Esc 返回")
        self._btn_focus.clicked.connect(self.focus_requested.emit)
        self._btn_fullscreen = QPushButton("全屏")
        self._btn_fullscreen.setToolTip("全屏查看（F）；双击视频也可切换全屏，Esc 返回")
        self._btn_fullscreen.clicked.connect(self.fullscreen_requested.emit)
        for button in (self._btn_focus, self._btn_fullscreen):
            button.setProperty("flat", True)
            view_bar.addWidget(button)
        lay.addLayout(view_bar)
        lay.addLayout(navigation)
        lay.addWidget(self._access_banner)
        lay.addWidget(self._stack, 1)
        lay.addLayout(bar)

        # 只接管预览画面；音量、进度、下拉框等控制条仍保留自己的滚轮操作。
        self._wheel_surfaces = [self._image._canvas, self._video._video, self._other]
        pdf_view = getattr(self._pdf, "_view", None)
        if pdf_view is not None:
            self._wheel_surfaces.append(pdf_view.viewport())
        for surface in self._wheel_surfaces:
            surface.setFocusPolicy(Qt.StrongFocus)
            surface.installEventFilter(self)

        # 快捷键仅在预览区域生效，避免影响搜索输入及文件列表的键盘操作。
        self._view_shortcuts: list[QShortcut] = []
        self._media_shortcuts: list[QShortcut] = []
        for key, slot in (
            (Qt.Key_F, self.fullscreen_requested.emit),
            (Qt.Key_Escape, self.escape_requested.emit),
            (Qt.Key_Left, lambda: self._navigate_by_key(-1)),
            (Qt.Key_Right, lambda: self._navigate_by_key(1)),
            (Qt.Key_Space, self._toggle_playback),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(slot)
            self._view_shortcuts.append(shortcut)
            if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Space):
                self._media_shortcuts.append(shortcut)
        QApplication.instance().focusChanged.connect(self._sync_shortcut_focus)
        QApplication.instance().aboutToQuit.connect(self.shutdown)

    def _sync_shortcut_focus(self, _previous: QWidget | None, current: QWidget | None) -> None:
        """滑条、倍速和页码输入优先使用自身按键，避免调整音量时切走文件。"""
        controls_focused = False
        widget = current
        while widget is not None and widget is not self:
            if isinstance(widget, (QSlider, QComboBox, QLineEdit)):
                controls_focused = True
                break
            widget = widget.parentWidget()
        for shortcut in self._media_shortcuts:
            shortcut.setEnabled(not controls_focused)

    def set_view_mode(self, focused: bool, fullscreen: bool) -> None:
        """只更新布局按钮，保持当前媒体及其播放状态。"""
        self._btn_focus.setText("返回管理" if focused else "放大预览")
        self._btn_fullscreen.setText("退出全屏" if fullscreen else "全屏")

    def _navigate_by_key(self, step: int) -> None:
        button = self._btn_previous if step < 0 else self._btn_next
        if button.isEnabled():
            (self.previous_requested if step < 0 else self.next_requested).emit()

    def _toggle_playback(self) -> None:
        if self._access_state != "ready":
            return
        if self._stack.currentWidget() is self._video:
            self._video._toggle()
        elif self._stack.currentWidget() is self._image and self._image._movie is not None:
            self._image._toggle_animation()

    def eventFilter(self, watched, event) -> bool:
        if watched in self._wheel_surfaces and event.type() == QEvent.MouseButtonPress:
            watched.setFocus(Qt.MouseFocusReason)
        if (watched is self._video._video and event.type() == QEvent.MouseButtonDblClick
                and event.button() == Qt.LeftButton):
            self.fullscreen_requested.emit()
            event.accept()
            return True
        if event.type() == QEvent.Wheel and watched in self._wheel_surfaces:
            if not self._wheel_navigation_enabled():
                self._wheel_delta = 0
                return super().eventFilter(watched, event)
            # 触控板按像素累计，普通滚轮按角度累计；一次事件最多切一个文件。
            pixel = event.pixelDelta().y()
            delta = pixel or event.angleDelta().y()
            unit = "pixel" if pixel else "angle"
            threshold = 40 if pixel else 120
            if event.phase() == Qt.ScrollBegin or unit != self._wheel_unit or delta * self._wheel_delta < 0:
                self._wheel_delta = 0
            self._wheel_unit = unit
            self._wheel_delta += delta
            if abs(self._wheel_delta) >= threshold:
                button = self._btn_previous if self._wheel_delta > 0 else self._btn_next
                signal = self.previous_requested if self._wheel_delta > 0 else self.next_requested
                self._wheel_delta = 0
                if button.isEnabled():
                    signal.emit()
            if event.phase() == Qt.ScrollEnd:
                self._wheel_delta = 0
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def set_file_navigation(
        self, can_previous: bool, can_next: bool, position: int, total: int,
    ) -> None:
        """由文件列表同步翻页状态；position 从 1 开始，未选择为 0。"""
        self._btn_previous.setEnabled(can_previous)
        self._btn_next.setEnabled(can_next)
        self._lbl_file_position.setText(f"{position} / {total}")

    def set_file_context(self, file_id: int | None, label: str = "") -> None:
        """列表先提供记录身份；不读取或修改数据库。"""
        self._file_id = file_id
        self._label = label

    @staticmethod
    def _same_path(left: str | None, right: str | None) -> bool:
        return bool(left and right and os.path.normcase(os.path.abspath(left))
                    == os.path.normcase(os.path.abspath(right)))

    def show_file(self, path: str | None, *, autoplay: bool = False) -> None:
        """立即更新选中项，文件系统访问全部由后台检查后再加载。"""
        if self._closed:
            return
        if self._same_path(path, self._current):
            return
        self._generation += 1
        self._monitor.cancel()
        self._external_monitor.cancel()
        self._offline_timer.stop()
        self._current = path
        self._video.stop()
        self._pdf.clear_doc()
        self._image.clear_image()
        for media in (self._image, self._video, self._pdf):
            media.setEnabled(True)
        self._loaded_path = None
        self._resume = {}
        self._last_error = None
        self._failure_reason = ""
        self._requested_autoplay = autoplay
        self._full_path = path or ""
        self._lbl_path.setToolTip(path or "")
        self._btn_open.setEnabled(False)
        self._stack.setCurrentIndex(3)
        if not path:
            self._access_state = "empty"
            self._file_id = None
            self._label = ""
            self._other.setText("（未选择文件）")
            self._lbl_path.setText("（未选择文件）")
            self._access_banner.hide()
            self.status_changed.emit("")
            return
        self._update_path_label()
        self._begin_check("load")

    def _begin_check(self, purpose: str) -> None:
        if not self._current or self._closed:
            return
        self._probe_purpose = purpose
        if purpose not in ("health", "retry_auto"):
            self._access_state = "checking"
            self._show_access_banner("正在检查文件…", actions=False)
            if self._loaded_path is None:
                self._other.setText("正在检查文件…")
            self.status_changed.emit("正在检查文件…")
        self._monitor.check(self._current, self._generation)

    def _on_access_result(self, generation: int, path: str, result: ProbeResult) -> None:
        if self._closed or generation != self._generation or not self._same_path(path, self._current):
            return
        purpose = self._probe_purpose
        if not result.accessible:
            self._set_unavailable(result)
            return
        if purpose == "failure":
            self._access_state = "decode_error"
            self._last_error = ProbeResult(path, True, "解码媒体", "MediaDecodeError", self._failure_reason)
            self._show_access_banner(f"无法解码文件：{self._display_name()}\n文件可读取，请检查格式或编码。")
            self._btn_open.setEnabled(True)
            self._offline_timer.start()
            self.status_changed.emit(f"预览解码失败：{self._display_name()}")
        elif purpose == "health":
            # 健康检查不重新载入，也不改变用户暂停/缩放/页码。
            return
        else:
            manual = purpose == "retry_manual"
            autoplay = (bool(self._resume.get("playing", False)) if self._resume else self._requested_autoplay)
            self._load_current(autoplay=autoplay if purpose == "load" or manual else False)

    def _load_current(self, *, autoplay: bool) -> None:
        path = self._current
        if not path:
            return
        self._last_error = None
        self._access_state = "checking"
        self._show_access_banner("正在加载预览…", actions=False)
        self._btn_open.setEnabled(True)
        kind = detect_kind(path)
        try:
            for media in (self._image, self._video, self._pdf):
                media.setEnabled(True)
            if kind == "image":
                self._stack.setCurrentIndex(0)
                self._image.load(path)
            elif kind == "video":
                self._stack.setCurrentIndex(1)
                if self._resume:
                    self._video.load(path, autoplay=autoplay, resume=self._resume)
                else:
                    self._video.load(path, autoplay=autoplay)
            elif kind == "pdf":
                self._stack.setCurrentIndex(2)
                if self._resume:
                    self._pdf.load(path, resume=self._resume)
                else:
                    self._pdf.load(path)
            else:
                self._stack.setCurrentIndex(3)
                self._other.setText("此类型不支持内嵌预览\n请点击下方按钮以默认程序打开")
                self._on_media_loaded(path)
        except Exception as exc:
            logging.exception("载入预览失败：%s", path)
            self._on_media_failed(path, f"{type(exc).__name__}: {exc}")

    def _on_media_loaded(self, path: str) -> None:
        if self._closed or not self._same_path(path, self._current) or self._access_state != "checking":
            return
        # 读取失败的二次确认期间，迟到的 loaded 不能抹掉错误。
        if self._probe_purpose == "failure":
            return
        self._loaded_path = self._current
        self._access_state = "ready"
        self._last_error = None
        self._resume = {}
        self._access_banner.hide()
        self._offline_timer.start()
        self.status_changed.emit(f"预览已就绪：{self._display_name()}")

    def _capture_resume(self) -> None:
        if self._resume:
            return
        media = self._stack.currentWidget()
        if media in (self._video, self._pdf):
            self._resume = dict(media.capture_resume_state())

    def _on_media_failed(self, path: str, reason: str) -> None:
        if self._closed or not self._same_path(path, self._current):
            return
        if self._access_state == "offline" or (self._probe_purpose == "failure" and self._monitor.busy):
            return
        self._capture_resume()
        self._failure_reason = reason
        for media in (self._image, self._video, self._pdf):
            media.setEnabled(False)
        self._begin_check("failure")

    def _set_unavailable(self, result: ProbeResult) -> None:
        previous_state = self._access_state
        previous_device_offline = self._last_error.device_offline if self._last_error else None
        self._capture_resume()
        self._access_state = "offline"
        self._last_error = result
        self._requested_autoplay = False
        # 保留缓存画面和恢复信息，同时停止读取和旧媒体的播放操作。
        self._video._player.pause()
        if self._image._movie is not None:
            self._image._movie.setPaused(True)
        for media in (self._image, self._video, self._pdf):
            media.setEnabled(False)
        summary = "设备离线" if result.device_offline else "文件无法访问"
        self._show_access_banner(f"{summary}：{self._display_name()}\n{self._current}")
        if self._loaded_path is None:
            self._stack.setCurrentIndex(3)
            self._other.setText(f"{summary}\n连接设备或恢复文件后将自动重试。")
        self._btn_open.setEnabled(False)
        self._offline_timer.start()
        if previous_state != "offline" or previous_device_offline != result.device_offline:
            self.status_changed.emit(f"{summary}：{self._display_name()}")

    def _show_access_banner(self, text: str, *, actions: bool = True) -> None:
        self._lbl_access.setText(text)
        self._access_banner.show()
        for button in (self._btn_retry, self._btn_relink, self._btn_details, self._btn_copy_error):
            button.setVisible(actions)
        self._btn_relink.setEnabled(self._file_id is not None)

    def _display_name(self) -> str:
        return self._label or ntpath.basename(self._current or "") or "文件"

    def _check_current_access(self) -> None:
        if not self._current or self._closed or self._monitor.busy:
            return
        if self._access_state == "offline":
            self.retry_current(manual=False)
        elif self._access_state in ("ready", "decode_error"):
            self._begin_check("health")

    def retry_current(self, manual: bool = True) -> None:
        """手动重试保留原播放意图；自动重连只恢复位置并保持暂停。"""
        if not self._current or self._closed:
            return
        self._capture_resume()
        self._offline_timer.stop()
        self._begin_check("retry_manual" if manual else "retry_auto")

    def _on_video_finished(self, path: str) -> None:
        if self._access_state == "ready" and self._same_path(path, self._current):
            self.video_finished.emit(path)

    def _error_details(self) -> str:
        error = self._last_error
        return "\n".join((
            f"文件名称：{self._display_name()}",
            f"记录 ID：{self._file_id if self._file_id is not None else '（未提供）'}",
            f"源路径：{self._current or '（无）'}",
            "目标路径：（不适用）",
            f"失败阶段：{error.stage if error else '（无）'}",
            f"异常类型：{error.error_type if error else '（无）'}",
            f"详细原因：{error.message if error else '（无）'}",
            "处理结果：文件记录及原文件保持不变。",
        ))

    def _show_error_details(self) -> None:
        dialogs.info(self, "预览失败详情", "当前文件预览失败，可查看详细原因。", detailed=self._error_details())

    def _copy_error_details(self) -> None:
        QApplication.clipboard().setText(self._error_details())
        self.status_changed.emit("已复制预览失败详情")

    def shutdown(self) -> None:
        """关闭时取消待处理检查，不等待不可用磁盘返回。"""
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._offline_timer.stop()
        self._monitor.cancel()
        self._external_monitor.cancel()
        self._video.stop()
        self._pdf.clear_doc()
        self._image.clear_image()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(event)

    # ------------------------------------------------------------ path label
    def _update_path_label(self) -> None:
        if not self._full_path:
            return
        fm = self._lbl_path.fontMetrics()
        avail = max(0, self._lbl_path.width() - 2)
        text = fm.elidedText(self._full_path, Qt.ElideMiddle, avail)
        self._lbl_path.setText(text)

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._update_path_label()

    def _open_external(self) -> None:
        if not self._current or self._closed or self._external_monitor.busy:
            return
        path = self._current

        def open_file() -> ProbeResult:
            try:
                open_with_default_app(path)
                return ProbeResult(path, True, "用默认程序打开")
            except Exception as exc:
                logging.exception("用默认程序打开失败：%s", path)
                return ProbeResult(path, False, "用默认程序打开", type(exc).__name__, str(exc))

        self.status_changed.emit("正在调用默认程序…")
        self._external_monitor.check(path, self._generation, operation=open_file)

    def _on_external_result(self, generation: int, path: str, result: ProbeResult) -> None:
        if self._closed or generation != self._generation or not self._same_path(path, self._current):
            return
        if result.accessible:
            self.status_changed.emit("已调用默认程序打开文件")
            return
        self._last_error = result
        self.status_changed.emit(f"打开文件失败：{self._display_name()}")
        dialogs.info(self, "打开文件失败", "无法用默认程序打开所选文件。", detailed=self._error_details())

    # ------------------------------------------------------------ capture
    def capture_pixmap(self) -> QPixmap | None:
        """返回当前预览画面的 QPixmap。
        - image：返回原图（或缩放后的 pixmap，取原图保真）
        - pdf：渲染当前可见的第一页
        - video：抓当前帧（VideoWidget 直接 grab，可能为黑屏；
                 退化方案：截屏播放器位置的窗口区域）
        - 不支持类型：返回 None
        """
        idx = self._stack.currentIndex()
        if idx == 0:  # image
            if self._image._pix is not None and not self._image._pix.isNull():
                return self._image._pix
            return None
        if idx == 2:  # pdf
            return self._pdf.capture_current_page()
        if idx == 1:  # video
            return self._video.capture_current_frame()
        return None
