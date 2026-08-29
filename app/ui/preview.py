"""图片 / 视频 / PDF 内嵌预览组件。

task #40 增强：
- 图片：滚轮缩放（以光标为中心）/ 拖拽平移 / 双击 适应窗口↔100%，底部控制条
- 视频：音量滑条 + 倍速 + 空格播放暂停
- PDF：页码跳转 + 缩放模式 + 页码指示
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._canvas = _ImageCanvas(self)
        self._canvas.view_changed.connect(self._sync_zoom_label)

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
        bar.addStretch(1)
        lay.addLayout(bar)

    # ---- 对外接口 ----
    @property
    def _pix(self) -> QPixmap | None:
        return self._canvas._pix

    def load(self, path: str) -> None:
        pix = QPixmap(path)
        self._canvas.set_pixmap(pix if not pix.isNull() else None)

    def clear_image(self) -> None:
        self._canvas.set_pixmap(None)

    # ---- 控制条动作 ----
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

        # 空格播放/暂停（预览面板有焦点时）
        from PySide6.QtGui import QKeySequence, QShortcut
        self._sc_space = QShortcut(QKeySequence(Qt.Key_Space), self)
        self._sc_space.setContext(Qt.WidgetWithChildrenShortcut)
        self._sc_space.activated.connect(self._toggle)

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
        lay.setSpacing(0)
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
                try:
                    nav.currentPageChanged.connect(self._on_page_changed)
                except Exception:
                    pass
            try:
                self._doc.statusChanged.connect(self._on_doc_status)
            except Exception:
                pass
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

    def _on_doc_status(self, _status) -> None:
        self._lbl_pages.setText(f"/ {self._doc.pageCount()}")

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
        except Exception:
            pass

    # ---- 对外接口 ----
    def load(self, path: str) -> None:
        if self._available:
            self._doc.load(path)
            self._lbl_pages.setText(f"/ {self._doc.pageCount()}")
            self._ed_page.setText("1")

    def clear_doc(self) -> None:
        if self._available:
            self._doc.close()
            self._lbl_pages.setText("/ 0")

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

    def resizeEvent(self, ev) -> None:  # noqa: N802
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
