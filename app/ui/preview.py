"""图片 / 视频 / PDF 内嵌预览组件。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..utils import detect_kind, open_with_default_app


# ---------- 图片 ----------------------------------------------------------------
class ImagePreview(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(200, 200)
        self.setStyleSheet("background:#1e1e1e;color:#888;")
        self.setText("（无预览）")
        self._pix: QPixmap | None = None

    def load(self, path: str) -> None:
        pix = QPixmap(path)
        self._pix = pix if not pix.isNull() else None
        self._refresh()

    def clear_image(self) -> None:
        self._pix = None
        self.setPixmap(QPixmap())
        self.setText("（无预览）")

    def _refresh(self) -> None:
        if self._pix is None:
            return
        self.setText("")
        self.setPixmap(
            self._pix.scaled(
                self.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def resizeEvent(self, ev):
        self._refresh()
        super().resizeEvent(ev)


# ---------- 视频 ----------------------------------------------------------------
class VideoPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self._player = QMediaPlayer(self)
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

        ctrl = QHBoxLayout()
        ctrl.addWidget(self._btn_play)
        ctrl.addWidget(self._slider, 1)
        ctrl.addWidget(self._lbl_time)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._video, 1)
        lay.addLayout(ctrl)

        self._player.positionChanged.connect(self._on_pos)
        self._player.durationChanged.connect(self._on_dur)
        self._player.playbackStateChanged.connect(self._on_state)

    def load(self, path: str) -> None:
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(path))

    def stop(self) -> None:
        self._player.stop()
        self._player.setSource(QUrl())

    def _toggle(self) -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_state(self, st) -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        self._btn_play.setText("暂停" if st == QMediaPlayer.PlayingState else "播放")

    def _on_pos(self, pos: int) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(pos)
        self._slider.blockSignals(False)
        self._update_time()

    def _on_dur(self, dur: int) -> None:
        self._slider.setRange(0, max(0, dur))
        self._update_time()

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
            pass
        try:
            pix = self._video.grab()
            return pix if not pix.isNull() else None
        except Exception:
            return None


# ---------- PDF ---------------------------------------------------------------
class PdfPreview(QWidget):
    """Qt 6.4+ 提供 QtPdf / QtPdfWidgets。无则 fallback 到提示。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._available = True
        try:
            from PySide6.QtPdf import QPdfDocument           # noqa
            from PySide6.QtPdfWidgets import QPdfView        # noqa
        except Exception:
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
            except Exception:
                pass
            lay.addWidget(self._view)
        else:
            tip = QLabel("当前 PySide6 未安装 QtPdf 模块，无法内嵌预览 PDF。\n点击右侧『用默认程序打开』查看。")
            tip.setAlignment(Qt.AlignCenter)
            tip.setStyleSheet("color:#888;")
            lay.addWidget(tip)

    def load(self, path: str) -> None:
        if self._available:
            self._doc.load(path)

    def clear_doc(self) -> None:
        if self._available:
            self._doc.close()

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
                pass
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
            return None


# ---------- 综合预览面板 -------------------------------------------------------
class PreviewPanel(QWidget):
    """根据文件类型自动选择内嵌组件；不支持的类型显示提示+打开按钮。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = ImagePreview()
        self._video = VideoPreview()
        self._pdf = PdfPreview()

        self._other = QLabel("此类型不支持内嵌预览\n请点击下方按钮以默认程序打开")
        self._other.setAlignment(Qt.AlignCenter)
        self._other.setStyleSheet("color:#888;")

        self._stack = QStackedWidget()
        self._stack.addWidget(self._image)   # 0
        self._stack.addWidget(self._video)   # 1
        self._stack.addWidget(self._pdf)     # 2
        self._stack.addWidget(self._other)   # 3

        self._lbl_path = QLabel("（未选择文件）")
        self._lbl_path.setStyleSheet("color:#666;")
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

        bar = QHBoxLayout()
        bar.addWidget(self._lbl_path, 1)
        bar.addWidget(self._btn_open)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self._stack, 1)
        lay.addLayout(bar)

        self._current: str | None = None

    def show_file(self, path: str | None) -> None:
        # 切换前先停止视频/释放 PDF
        self._video.stop()
        self._pdf.clear_doc()
        self._image.clear_image()

        self._current = path
        if not path or not Path(path).exists():
            self._stack.setCurrentIndex(3)
            self._other.setText("（文件不存在或未选择）")
            self._full_path = ""
            self._lbl_path.setText("（未选择文件）")
            self._lbl_path.setToolTip("")
            self._btn_open.setEnabled(False)
            return

        self._full_path = path
        self._lbl_path.setToolTip(path)
        self._update_path_label()
        self._btn_open.setEnabled(True)
        kind = detect_kind(path)
        if kind == "image":
            self._stack.setCurrentIndex(0)
            self._image.load(path)
        elif kind == "video":
            self._stack.setCurrentIndex(1)
            self._video.load(path)
        elif kind == "pdf":
            self._stack.setCurrentIndex(2)
            self._pdf.load(path)
        else:
            self._stack.setCurrentIndex(3)
            self._other.setText("此类型不支持内嵌预览\n请点击下方按钮以默认程序打开")

    # ------------------------------------------------------------ path label
    def _update_path_label(self) -> None:
        if not self._full_path:
            return
        fm = self._lbl_path.fontMetrics()
        avail = max(0, self._lbl_path.width() - 2)
        text = fm.elidedText(self._full_path, Qt.ElideMiddle, avail)
        self._lbl_path.setText(text)

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        self._update_path_label()

    def _open_external(self) -> None:
        if self._current:
            open_with_default_app(self._current)

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
