"""在原有布局中切换专注预览和全屏，保留播放器及用户布局。"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QRect, Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QToolBar, QWidget
from shiboken6 import isValid


@dataclass
class _LayoutSnapshot:
    """保存显式可见状态，避免父窗口隐藏影响可见性判断。"""

    widgets: list[tuple[QWidget, bool]]
    main_sizes: list[int]
    right_sizes: list[int]
    focus_widget: QWidget | None


class PreviewFocusController(QObject):
    """只隐藏已有控件，不移动预览组件或重新加载媒体。"""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self._window = window
        self.focused = False
        self.fullscreen = False
        self._changing = False
        self._snapshot: _LayoutSnapshot | None = None
        self._fullscreen_from_focus = False
        self._window_state = Qt.WindowNoState
        self._window_geometry = QRect()

    def toggle_focus(self) -> None:
        """全屏中切换专注按钮时，一次退出到原有普通布局。"""
        if self._changing:
            return
        if self.fullscreen or self.focused:
            self.restore()
        else:
            self._enter_focus()

    def toggle_fullscreen(self) -> None:
        """退出全屏后回到进入全屏之前的模式。"""
        if self._changing:
            return
        if self.fullscreen:
            self._leave_fullscreen()
            return
        self._fullscreen_from_focus = self.focused
        self._window_state = self._window.windowState()
        # restoreGeometry 会把窗口强制挪回当前屏幕，并可能因窗口边框
        # 校正改变尺寸；同一个窗口的临时全屏应精确恢复原有客户区。
        self._window_geometry = QRect(
            self._window.normalGeometry() if self._window.isMaximized()
            else self._window.geometry()
        )
        if not self.focused:
            self._enter_focus()
        self._changing = True
        try:
            # 原生窗口变化可能触发布局保存，先标记模式。
            self.fullscreen = True
            self._update_preview()
            self._window.showFullScreen()
            self._focus_preview()
        finally:
            self._changing = False

    def escape(self) -> None:
        """Esc 每次退出一层：全屏、专注、普通。"""
        if self._changing:
            return
        if self.fullscreen:
            self._leave_fullscreen()
        elif self.focused:
            self.restore()

    def restore(self) -> None:
        """同步恢复普通布局，关闭窗口前调用以保存真实布局。"""
        if self._changing:
            return
        if self.fullscreen:
            self._leave_fullscreen(keep_focus=True)
        if not self.focused:
            return
        self._changing = True
        try:
            snapshot = self._snapshot
            if snapshot is not None:
                for widget, visible in snapshot.widgets:
                    if isValid(widget):
                        widget.removeEventFilter(self)
                        if widget is self._window.project_tags:
                            widget.set_suspended(False)
                        else:
                            widget.setVisible(visible)
                self._window.layout().activate()
                # 兄弟面板重新显示后，先同步恢复分隔条手柄宽度。
                # 否则关闭事件中立刻保存会暂时把手柄的像素算进面板。
                self._window._main_splitter.refresh()
                self._window._right_v_split.refresh()
                # Qt 会在尺寸变化后按原比例分配；同尺寸下恢复原像素值。
                self._window._main_splitter.setSizes(snapshot.main_sizes)
                self._window._right_v_split.setSizes(snapshot.right_sizes)
            self._snapshot = None
            # 恢复全过程仍为 focused，避免中途把隐藏尺寸写入设置。
            self.focused = False
            self._update_preview()
            if snapshot is not None:
                previous = snapshot.focus_widget
                if previous is not None and isValid(previous) and previous.isVisible():
                    previous.setFocus(Qt.OtherFocusReason)
        finally:
            self._changing = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        # 后台选择/批量操作可能重新显示面板；专注期间维持隐藏状态。
        if self.focused and not self._changing and event.type() == QEvent.Show:
            if isinstance(watched, QWidget):
                watched.hide()
        return super().eventFilter(watched, event)

    def _enter_focus(self) -> None:
        self._changing = True
        try:
            window = self._window
            widgets = [
                window._main_splitter.widget(0),
                window._main_splitter.widget(1),
                window._right_v_split.widget(1),
                window.lbl_meta_title,
                window.lbl_meta_desc,
                window.project_tags,
                window._batch_panel,
                *window.findChildren(QToolBar),
                window.statusBar(),
            ]
            # 弹出的文件列表仍在独立窗口；这里只隐藏 splitter 中的占位。
            self._snapshot = _LayoutSnapshot(
                widgets=[(widget, not widget.isHidden()) for widget in widgets if widget is not None],
                main_sizes=window._main_splitter.sizes(),
                right_sizes=window._right_v_split.sizes(),
                focus_widget=QApplication.focusWidget(),
            )
            self.focused = True
            window.project_tags.set_suspended(True)
            for widget, _visible in self._snapshot.widgets:
                widget.installEventFilter(self)
                widget.hide()
            self._update_preview()
            self._focus_preview()
        finally:
            self._changing = False

    def _leave_fullscreen(self, *, keep_focus: bool = False) -> None:
        self._changing = True
        try:
            # focused 保持为 True，直至普通布局完全恢复。
            self.fullscreen = False
            self._window.showNormal()
            self._window.setGeometry(self._window_geometry)
            if self._window_state & Qt.WindowMaximized:
                self._window.showMaximized()
            self._update_preview()
            self._focus_preview()
        finally:
            self._changing = False
        if not keep_focus and not self._fullscreen_from_focus:
            self.restore()

    def _update_preview(self) -> None:
        self._window.preview.set_view_mode(self.focused, self.fullscreen)

    def _focus_preview(self) -> None:
        self._window.preview.setFocus(Qt.OtherFocusReason)
