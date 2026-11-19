"""专注/全屏预览：显式临时库、合成图片/视频及真实 Qt 交互。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest

from app.models import FileItem, Project
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _window
from selftests.gui_preview_file_navigation import _select


def events() -> None:
    for _ in range(4):
        _app().processEvents()


def wait_until(predicate, seconds: float = 5) -> bool:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        events()
        if predicate():
            return True
        QTest.qWait(20)
    return False


def sizes(win) -> tuple[list[int], list[int]]:
    return win._main_splitter.sizes(), win._right_v_split.sizes()


def run(root: Path, t: T) -> None:
    win, repo = _window(root)
    try:
        pid = repo.save_project(Project(title="专注预览测试"))
        ids = []
        for n in (1, 2):
            path = root / f"image{n}.png"
            image = QImage(640, 360, QImage.Format_RGB32)
            image.fill(Qt.darkCyan if n == 1 else Qt.darkBlue)
            image.save(str(path))
            ids.append(repo.add_file(FileItem(project_id=pid, path=str(path), kind="image")))
        win.refresh_projects()
        _select(win, ids[0])
        win.resize(1280, 820)
        events()
        win._main_splitter.setSizes([210, 600, 470])
        win._right_v_split.setSizes([430, 300])
        events()
        before = sizes(win)
        old_width = win.preview.width()
        preview = win.preview
        controller = win._preview_focus
        media_parent = preview._video.parent()
        with patch.object(preview, "show_file", wraps=preview.show_file) as load:
            preview._btn_focus.click()
            events()
            t.assert_true("放大占满主区域", controller.focused and preview.width() > old_width * 2)
            t.assert_true("隐藏项目和文件列表", not win.proj_view.isVisible() and not win.tbl_files.isVisible())
            t.assert_eq("放大沿用播放器父对象", preview._video.parent(), media_parent)
            t.assert_eq("放大不重载文件", load.call_count, 0)
            t.assert_eq("返回按钮明确", preview._btn_focus.text(), "返回管理")
            preview.setFocus()
            QTest.keyClick(preview, Qt.Key_Right)
            events()
            t.assert_eq("放大后右键切换下一文件", win._current_file_row_id(), ids[1])
            QTest.keyClick(preview, Qt.Key_Right)
            t.assert_eq("文件末尾不循环", win._current_file_row_id(), ids[1])
            QTest.keyClick(preview, Qt.Key_Left)
            t.assert_eq("左键切换上一文件", win._current_file_row_id(), ids[0])
            load.reset_mock()
            QTest.keyClick(preview, Qt.Key_F)
            events()
            t.assert_true("F 进入全屏", controller.fullscreen and win.isFullScreen())
            QTest.keyClick(preview, Qt.Key_Escape)
            events()
            t.assert_true("全屏 Esc 回专注", controller.focused and not controller.fullscreen)
            QTest.keyClick(preview, Qt.Key_Escape)
            events()
            t.assert_true("专注 Esc 回管理", not controller.focused and win.tbl_files.isVisible())
            t.assert_eq("专注全屏往返恢复分栏", sizes(win), before)
            t.assert_eq("往返不重新加载文件", load.call_count, 0)
        preview._btn_fullscreen.click()
        events()
        controller.escape()
        events()
        t.assert_true("普通直接全屏退出回普通", not controller.focused and not win.isFullScreen())
        t.assert_eq("普通全屏往返保持分栏", sizes(win), before)
        # 输入框的 F/方向键不可触发预览操作。
        win.search_box.setFocus()
        QTest.keyClick(win.search_box, Qt.Key_F)
        t.assert_true("搜索输入 F 不进入全屏", not controller.fullscreen)
        win._search_timer.stop()
        win.search_box.clear()
        win._search_timer.stop()
        win._completion.hide()
        # 滑条保留键盘调整能力，不能误切换文件。
        preview._stack.setCurrentWidget(preview._video)
        preview._video._vol.setFocus()
        volume = preview._video._vol.value()
        QTest.keyClick(preview._video._vol, Qt.Key_Right)
        t.assert_eq("音量方向键仍调整音量", preview._video._vol.value(), volume + 1)
        t.assert_eq("音量方向键不切换文件", win._current_file_row_id(), ids[0])
        # 双击视频复用同一全屏控制，不走 QVideoWidget 独立全屏。
        QTest.mouseDClick(preview._video._video, Qt.LeftButton)
        events()
        t.assert_true("双击视频进入全屏", controller.fullscreen)
        QTest.mouseDClick(preview._video._video, Qt.LeftButton)
        events()
        t.assert_true("再次双击视频退出", not controller.fullscreen)
        win.showMaximized()
        events()
        controller.toggle_fullscreen()
        events()
        controller.escape()
        events()
        t.assert_true("从最大化全屏退出仍最大化", win.isMaximized())
        win.showNormal()
        events()
        win._detach_files_panel()
        events()
        controller.toggle_focus()
        controller.restore()
        t.assert_true("专注不收回独立文件窗", win._files_detach_window is not None and win._files_detach_window.isVisible())
        win._attach_files_panel()
        events()

        # ffmpeg 只生成测试素材；产品本身不依赖该命令。
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("此自测需要 ffmpeg 生成临时视频")
        video = root / "sample.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                        "color=c=blue:s=160x90:r=10:d=20", "-pix_fmt", "yuv420p", str(video)],
                       check=True, capture_output=True)
        preview.show_file(str(video))
        player = preview._video._player
        t.assert_true("真实合成视频加载", wait_until(lambda: player.duration() > 0))
        player.setPosition(2400)
        player.play()
        t.assert_true("视频实际推进", wait_until(lambda: player.position() > 2600))
        player.pause()
        position = player.position()
        source = player.source()
        with patch.object(player, "setSource", wraps=player.setSource) as set_source, \
                patch.object(player, "play", wraps=player.play) as play, \
                patch.object(player, "stop", wraps=player.stop) as stop:
            controller.toggle_focus()
            events()
            controller.toggle_fullscreen()
            events()
            controller.escape()
            controller.escape()
            events()
            t.assert_eq("暂停视频往返不改位置", player.position(), position)
            t.assert_eq("暂停视频往返不改状态", player.playbackState(), QMediaPlayer.PausedState)
            t.assert_eq("视频源不变", player.source(), source)
            t.assert_eq("布局切换不 setSource/play/stop", (set_source.call_count, play.call_count, stop.call_count), (0, 0, 0))
        preview.setFocus()
        QTest.keyClick(preview, Qt.Key_Space)
        t.assert_true("空格继续播放", wait_until(lambda: player.playbackState() == QMediaPlayer.PlayingState))
        controller.toggle_focus()
        events()
        t.assert_eq("播放中放大仍播放", player.playbackState(), QMediaPlayer.PlayingState)
        preview.setFocus()
        QTest.keyClick(preview, Qt.Key_Space)
        t.assert_eq("放大后空格暂停", player.playbackState(), QMediaPlayer.PausedState)
        controller.restore()
        events()
        before_close = sizes(win)
        controller.toggle_fullscreen()
        events()
        win.close()
        events()
        t.assert_true("关闭前退出临时预览布局", not controller.focused and not controller.fullscreen)
        t.assert_eq("关闭存正常左右分栏", json.loads(repo.get_setting(win.MAIN_SPLITTER_SETTING_KEY)), before_close[0])
        t.assert_eq("关闭存正常上下分栏", json.loads(repo.get_setting(win.RIGHT_SPLITTER_SETTING_KEY)), before_close[1])
        preview.show_file(None)
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-preview-focus-") as tmp:
        run(Path(tmp), t)
    raise SystemExit(0 if t.report() else 1)
