"""滚轮切换文件：显式临时库、合成图片和真实 Qt 滚轮事件。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtWidgets import QApplication

from app.models import FileItem, Project
from selftests._common import T
from selftests._preview_wait import wait_preview_probe
from selftests.gui_main_window_regressions import _app, _close_window, _window
from selftests.gui_preview_file_navigation import _select


def wheel(widget, angle: int = 0, pixel: int = 0) -> None:
    pos = QPointF(widget.rect().center())
    event = QWheelEvent(pos, QPointF(widget.mapToGlobal(pos.toPoint())),
                        QPoint(0, pixel), QPoint(0, angle), Qt.NoButton,
                        Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def run(root: Path, t: T) -> None:
    win, repo = _window(root)
    try:
        pid = repo.save_project(Project(title="滚轮测试"))
        ids = []
        for n in (1, 2, 10):
            path = root / f"image{n}.png"
            image = QImage(40, 40, QImage.Format_RGB32)
            image.fill(Qt.red)
            image.save(str(path))
            ids.append(repo.add_file(FileItem(project_id=pid, path=str(path), kind="image")))
        win.refresh_projects()
        _select(win, ids[0])
        wait_preview_probe(win.preview)
        canvas = win.preview._image._canvas
        zoom = canvas.current_scale()
        wheel(canvas, -120)
        t.assert_eq("默认滚轮不切文件", win._current_file_row_id(), ids[0])
        t.assert_true("默认滚轮缩放图片", canvas.current_scale() != zoom)
        repo.set_setting("preview_wheel_navigation", "1")
        # 对所有受支持的画面直接发送事件，验证使用相同导航链路。
        for surface, page in zip(win.preview._wheel_surfaces, (0, 1, 3, 2)):
            win.tbl_files.clearSelection()
            _select(win, ids[0])
            win.preview._stack.setCurrentIndex(page)
            _app().processEvents()
            win._update_preview_file_navigation()
            wheel(surface, -120)
            t.assert_eq(f"{type(surface).__name__} 下滚选择下一文件", win._current_file_row_id(), ids[1])
            win.preview._stack.setCurrentIndex(page)
            _app().processEvents()
            wheel(surface, 120)
            t.assert_eq(f"{type(surface).__name__} 上滚选择上一文件", win._current_file_row_id(), ids[0])
        wheel(canvas, 120)
        t.assert_eq("开头不循环", win._current_file_row_id(), ids[0])
        zoom = canvas.current_scale()
        wheel(canvas, 120)
        t.assert_eq("边界不会回退缩放", canvas.current_scale(), zoom)
        wheel(canvas, -60)
        t.assert_eq("高精度滚轮累计前半格", win._current_file_row_id(), ids[0])
        wheel(canvas, -60)
        t.assert_eq("高精度滚轮累计整格", win._current_file_row_id(), ids[1])
        wheel(canvas, pixel=-20)
        t.assert_eq("触控板累计前半步", win._current_file_row_id(), ids[1])
        wheel(canvas, pixel=-20)
        t.assert_eq("触控板累计切换", win._current_file_row_id(), ids[2])
        wheel(canvas, -120)
        t.assert_eq("末尾不循环", win._current_file_row_id(), ids[2])
        wheel(canvas, 480)
        t.assert_eq("单次大滚动只切一个文件", win._current_file_row_id(), ids[1])
        wheel(win.preview._video._vol, -120)
        t.assert_eq("音量控制不切文件", win._current_file_row_id(), ids[1])
        repo.set_setting("preview_wheel_navigation", "0")
        wheel(canvas, -120)
        t.assert_eq("关闭立即停止滚轮导航", win._current_file_row_id(), ids[1])
        repo.set_setting("preview_wheel_navigation", "1")
        win._current_project_id = None
        win._show_project(None)
        wheel(win.preview._other, -120)
        t.assert_eq("空列表滚轮安全", win._current_file_row_id(), None)
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-wheel-") as tmp:
        run(Path(tmp), t)
    raise SystemExit(0 if t.report() else 1)
