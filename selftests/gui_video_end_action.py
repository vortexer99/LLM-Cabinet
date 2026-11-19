"""视频播完动作 GUI 回归；只访问显式临时库和 ffmpeg 自行合成的视频。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QItemSelectionModel, Qt
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest

from app.db import connect
from app.models import FileItem, Project
from app.repository import Repository
from app.ui.main_window import MainWindow
from app.ui.settings import SettingsDialog
from selftests._preview_wait import wait_preview_probe
from selftests._common import T
from selftests.gui_main_window_regressions import (
    _app, _close_window, _find_file_tree_item, _window,
)


def _wait(predicate: Callable[[], bool], timeout: float = 6.0) -> bool:
    """处理真实播放器事件，超时失败而不跳过缺失的播放后端。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _app().processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return bool(predicate())


def _close_dialog(dialog: SettingsDialog) -> None:
    dialog.close()
    dialog.deleteLater()
    _app().sendPostedEvents(None, QEvent.DeferredDelete)
    _app().processEvents()


def _show(win: MainWindow, repo: Repository, pid: int) -> None:
    win._current_project_id = pid
    win._show_project(repo.get_project(pid))
    _app().processEvents()


def _select(win: MainWindow, fid: int, *, events: bool = True) -> None:
    item = _find_file_tree_item(win, fid)
    assert item is not None, f"临时文件行不存在：{fid}"
    win.tbl_files.setCurrentItem(item, 0, QItemSelectionModel.ClearAndSelect)
    if events:
        _app().processEvents()


def _add(repo: Repository, root: Path, pid: int, name: str, sample: Path | None = None,
         *, origin: str = "user", missing: bool = False) -> tuple[int, Path]:
    """文件全部在临时目录内生成，不复制、导出或访问用户数据库和文件。"""
    path = root / "media" / str(pid) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not missing:
        if sample is not None:
            shutil.copyfile(sample, path)
        elif path.suffix == ".png":
            image = QImage(16, 16, QImage.Format_RGB32)
            image.fill(0x3366AA)
            assert image.save(str(path))
        else:
            path.write_text("临时回归样本", encoding="utf-8")
    kind = "video" if path.suffix == ".mp4" else "image" if path.suffix == ".png" else "doc"
    fid = repo.add_file(FileItem(project_id=pid, path=str(path), is_relative=False,
                                 kind=kind, origin=origin, missing=missing))
    return fid, path


def _start(win: MainWindow, fid: int, t: T, label: str) -> None:
    """手动开始播放，确保连播测试不依赖 video_autoplay。"""
    win.preview.show_file(None)
    _select(win, fid)
    win._on_file_selected()
    player = win.preview._video._player
    t.assert_true(label + "真实视频加载", _wait(lambda: player.duration() > 0))
    player.play()
    t.assert_true(label + "真实视频开始播放", _wait(lambda: player.playbackState() == QMediaPlayer.PlayingState))


def test_settings(root: Path, t: T) -> None:
    """三种值即时保存，重开连接持久化，两个库互相隔离，未知值安全回退。"""
    roots = [root / "甲库", root / "乙库"]
    for directory in roots:
        directory.mkdir(parents=True)
    repos = [Repository(connect(directory / "cabinet.db")) for directory in roots]
    try:
        for index, (repo, directory) in enumerate(zip(repos, roots)):
            dialog = SettingsDialog(repo, directory, directory / "cabinet.db")
            try:
                combo = dialog.cmb_video_end_action
                t.assert_eq(f"库{index}默认播完停止", combo.currentData(), "stop")
                t.assert_eq(f"库{index}默认落库读取停止", repo.get_setting("video_end_action", "stop"), "stop")
                t.assert_eq(f"库{index}提供三个动作", [combo.itemData(i) for i in range(combo.count())],
                            ["stop", "loop", "next"])
                if index == 0:
                    for value in ("loop", "next", "stop", "next"):
                        combo.setCurrentIndex(combo.findData(value))
                        t.assert_eq("修改动作立即保存 " + value, repo.get_setting("video_end_action"), value)
            finally:
                _close_dialog(dialog)
        for index, directory in enumerate(roots):
            repo = Repository(connect(directory / "cabinet.db"))
            try:
                dialog = SettingsDialog(repo, directory, directory / "cabinet.db")
                try:
                    t.assert_eq(f"库{index}重开持久化且库隔离", dialog.cmb_video_end_action.currentData(),
                                "next" if index == 0 else "stop")
                finally:
                    _close_dialog(dialog)
            finally:
                repo.conn.close()
        repos[0].set_setting("video_end_action", "unknown-future-value")
        dialog = SettingsDialog(repos[0], roots[0], roots[0] / "cabinet.db")
        try:
            t.assert_eq("无效设置界面回退停止", dialog.cmb_video_end_action.currentData(), "stop")
        finally:
            _close_dialog(dialog)
    finally:
        for repo in repos:
            repo.conn.close()


def test_real_playback(root: Path, sample: Path, t: T) -> None:
    """真实 EndOfMedia 覆盖默认停止、循环、连播和列表末尾，关闭手动自动播放。"""
    win, repo = _window(root)
    player = win.preview._video._player
    finished: list[Path] = []
    win.preview.video_finished.connect(lambda path: finished.append(Path(path)))
    try:
        pid = repo.save_project(Project(title="真实播放"))
        first, first_path = _add(repo, root, pid, "clip1.mp4", sample)
        second, second_path = _add(repo, root, pid, "clip2.mp4", sample)
        repo.set_setting("video_autoplay", "0")
        _show(win, repo, pid)
        for value in (None, "invalid-value"):
            if value is not None:
                repo.set_setting("video_end_action", value)
            label = "默认停止" if value is None else "无效值回退停止"
            finished.clear()
            _start(win, first, t, label)
            t.assert_true(label + "真实播完信号", _wait(lambda: bool(finished)))
            t.assert_eq(label + "保留当前文件", win._current_file_row_id(), first)
            t.assert_eq(label + "播放器已停止", player.playbackState(), QMediaPlayer.StoppedState)
            t.assert_eq(label + "传递正确路径", finished, [first_path])

        repo.set_setting("video_end_action", "loop")
        finished.clear()
        _start(win, first, t, "循环")
        t.assert_true("循环连续产生两次真实播完信号", _wait(lambda: len(finished) >= 2))
        t.assert_eq("循环仍选当前视频", win._current_file_row_id(), first)
        t.assert_eq("循环保持视频源", Path(player.source().toLocalFile()), first_path)
        t.assert_eq("循环播完重新开始播放", player.playbackState(), QMediaPlayer.PlayingState)
        player.pause()
        position = player.position()
        with patch.object(player, "play", wraps=player.play) as play:
            win._on_file_selected()
            _app().processEvents()
            t.assert_eq("循环中手动暂停后重复选择不重启", play.call_count, 0)
            t.assert_eq("循环暂停位置不被重复选择重置", player.position(), position)
            t.assert_eq("循环手动暂停仍保持暂停", player.playbackState(), QMediaPlayer.PausedState)

        repo.set_setting("video_end_action", "next")
        finished.clear()
        _start(win, first, t, "连播")
        t.assert_true("连播真实播完后选下个视频", _wait(lambda: win._current_file_row_id() == second))
        wait_preview_probe(win.preview)
        t.assert_eq("连播使用下个视频源", Path(player.source().toLocalFile()), second_path)
        t.assert_true("自动播放关闭仍连播下个视频", _wait(lambda: player.playbackState() == QMediaPlayer.PlayingState))
        player.pause()
        with patch.object(player, "play", wraps=player.play) as play:
            win._on_file_selected()
            _app().processEvents()
            t.assert_eq("连播手动暂停后重复选择不重启", play.call_count, 0)
            t.assert_eq("连播手动暂停仍保持暂停", player.playbackState(), QMediaPlayer.PausedState)
        player.play()
        t.assert_true("列表最后视频真实播完", _wait(lambda: second_path in finished))
        QTest.qWait(100)
        t.assert_eq("连播末尾不循环文件列表", win._current_file_row_id(), second)
        t.assert_eq("连播末尾停止播放", player.playbackState(), QMediaPlayer.StoppedState)

        duplicate_pid = repo.save_project(Project(title="同一路径的两条登记记录"))
        duplicate_first = repo.add_file(FileItem(project_id=duplicate_pid, path=str(first_path),
                                                  is_relative=False, kind="video", label="第一个"))
        duplicate_second = repo.add_file(FileItem(project_id=duplicate_pid, path=str(first_path),
                                                   is_relative=False, kind="video", label="第二个"))
        _show(win, repo, duplicate_pid)
        displayed = win._preview_file_items()
        t.assert_eq("同源登记记录都在列表", len(displayed), 2)
        duplicate_first = displayed[0].data(0, Qt.UserRole)
        duplicate_second = displayed[1].data(0, Qt.UserRole)
        finished.clear()
        _start(win, duplicate_first, t, "同源登记记录连播")
        t.assert_true("相同视频路径的下一条记录正常选中", _wait(lambda: win._current_file_row_id() == duplicate_second))
        t.assert_eq("相同视频路径的下一条记录重新播放", player.playbackState(), QMediaPlayer.PlayingState)
        t.assert_true("相同路径第二条登记记录再次真实播完", _wait(lambda: len(finished) >= 2))
        t.assert_eq("同源登记记录列表末尾不循环", win._current_file_row_id(), duplicate_second)
        t.assert_eq("同源登记记录列表末尾停止", player.playbackState(), QMediaPlayer.StoppedState)
    finally:
        win.preview.show_file(None)
        _close_window(win)
        repo.conn.close()


def test_mixed_list(root: Path, sample: Path, t: T) -> None:
    """连播逐个遵循当前列表排序和来源过滤，图片、文档正常显示。"""
    win, repo = _window(root)
    try:
        pid = repo.save_project(Project(title="混排连播"))
        # 故意倒序添加，不能按数据库插入顺序或纯视频列表导航。
        text, _ = _add(repo, root, pid, "file10.txt")
        last_video, _ = _add(repo, root, pid, "file4.mp4", sample)
        image, _ = _add(repo, root, pid, "file3.png")
        excluded, _ = _add(repo, root, pid, "file2.mp4", sample, origin="generated")
        first, _ = _add(repo, root, pid, "file1.mp4", sample)
        repo.set_project_setting(pid, "files_view_mode", "flat")
        repo.set_project_setting(pid, "files_view_origin_filter", "user")
        repo.set_setting("video_end_action", "next")
        repo.set_setting("video_autoplay", "0")
        _show(win, repo, pid)
        t.assert_eq("混排列表排除衍生来源", _find_file_tree_item(win, excluded), None)
        _start(win, first, t, "图文混排升序")
        t.assert_true("播完按自然排序及来源过滤进入图片", _wait(lambda: win._current_file_row_id() == image))
        wait_preview_probe(win.preview)
        t.assert_eq("连播图片正常显示", win.preview._stack.currentIndex(), 0)
        t.assert_true("连播图片可正常抓取", win.preview.capture_pixmap() is not None)
        _start(win, last_video, t, "图文混排文档")
        t.assert_true("视频后下一项为文档时正常选中文档", _wait(lambda: win._current_file_row_id() == text))
        t.assert_eq("文档仍使用原不支持类型预览", win.preview._stack.currentIndex(), 3)
        win.tbl_files.sortByColumn(0, Qt.DescendingOrder)
        _app().processEvents()
        _start(win, last_video, t, "图文混排降序")
        t.assert_true("降序连播遵循当前列表进入图片", _wait(lambda: win._current_file_row_id() == image))
        wait_preview_probe(win.preview)
        t.assert_eq("降序连播图片预览正常", win.preview._stack.currentIndex(), 0)
    finally:
        win.preview.show_file(None)
        _close_window(win)
        repo.conn.close()


def test_guards(root: Path, sample: Path, t: T) -> None:
    """真实无效文件与人工停止不连播，控制迟到信号检查异步失效保护。"""
    win, repo = _window(root)
    video = win.preview._video
    player = video._player
    finished: list[str] = []
    win.preview.video_finished.connect(finished.append)
    try:
        pid = repo.save_project(Project(title="旧视频项目"))
        first, first_path = _add(repo, root, pid, "clip1.mp4", sample)
        second, second_path = _add(repo, root, pid, "clip2.mp4", sample)
        invalid, invalid_path = _add(repo, root, pid, "clip3-invalid.mp4")
        missing, missing_path = _add(repo, root, pid, "clip4-missing.mp4", missing=True)
        other = repo.save_project(Project(title="新项目"))
        other_file, _ = _add(repo, root, other, "clip.mp4", sample)
        repo.set_setting("video_end_action", "next")
        repo.set_setting("video_autoplay", "0")
        _show(win, repo, pid)
        _start(win, first, t, "人工停止")
        finished.clear()
        player.stop()
        QTest.qWait(1100)
        t.assert_eq("人工播放器停止不触发播完", finished, [])
        t.assert_eq("人工停止不跳下个文件", win._current_file_row_id(), first)
        _start(win, first, t, "切项目")
        finished.clear()
        _show(win, repo, other)
        QTest.qWait(1100)
        t.assert_eq("切项目不触发旧视频播完", finished, [])
        t.assert_true("切项目不跳回原项目文件", win._current_file_row_id() not in (first, second))

        _show(win, repo, pid)
        _select(win, invalid)
        player.play()
        t.assert_true("无效视频真实报告 InvalidMedia", _wait(lambda: player.mediaStatus() == QMediaPlayer.InvalidMedia))
        t.assert_eq("无效视频不自动跳过", win._current_file_row_id(), invalid)
        t.assert_eq("无效视频不报告播完", finished, [])
        _select(win, missing)
        QTest.qWait(100)
        t.assert_eq("缺失视频不自动跳过", win._current_file_row_id(), missing)
        t.assert_eq("缺失视频显示缺失提示页", win.preview._stack.currentIndex(), 3)
        t.assert_eq("缺失视频不报告播完", finished, [])

        # 模拟后端刚发 EndOfMedia，而零延时回调尚未执行；在同一事件轮次换源。
        _select(win, first)
        wait_preview_probe(win.preview)
        with patch.object(player, "mediaStatus", return_value=QMediaPlayer.EndOfMedia):
            video._on_media_status(QMediaPlayer.EndOfMedia)
            _select(win, second, events=False)
            _app().processEvents()
        wait_preview_probe(win.preview)
        t.assert_eq("换源丢弃已排队的旧视频结束事件", finished, [])
        t.assert_eq("旧结束事件不跳走新选视频", win._current_file_row_id(), second)
        t.assert_eq("旧结束事件不更改新视频源", Path(player.source().toLocalFile()), second_path)

        # 重新加载同一路径仍必须令旧 generation 失效，单比路径无法防住此场景。
        with patch.object(player, "mediaStatus", return_value=QMediaPlayer.EndOfMedia):
            video._on_media_status(QMediaPlayer.EndOfMedia)
            video.stop()
            video.load(str(second_path))
            _app().processEvents()
        t.assert_eq("停止再加载同源使过期回调失效", finished, [])
        t.assert_eq("同源重载旧回调不跳文件", win._current_file_row_id(), second)

        # 直接迟到的跨层信号也不能影响当前项目、文件、页面或无效媒体状态。
        for path, label in ((str(first_path), "旧文件路径"), (str(invalid_path), "无效路径"),
                            (str(missing_path), "缺失路径")):
            with patch.object(win, "_navigate_preview_file") as navigate:
                win.preview.video_finished.emit(path)
                t.assert_eq(label + "迟到信号不导航", navigate.call_count, 0)
        _show(win, repo, other)
        _select(win, other_file)
        with patch.object(win, "_navigate_preview_file") as navigate:
            win.preview.video_finished.emit(str(second_path))
            t.assert_eq("跨项目迟到信号不导航", navigate.call_count, 0)
        win.preview.show_file(None)
        with patch.object(player, "mediaStatus", return_value=QMediaPlayer.EndOfMedia), \
                patch.object(win, "_navigate_preview_file") as navigate:
            win.preview.video_finished.emit(player.source().toLocalFile())
            t.assert_eq("离开视频页面迟到信号不导航", navigate.call_count, 0)
    finally:
        win.preview.show_file(None)
        _close_window(win)
        repo.conn.close()


def main() -> bool:
    _app()
    t = T()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("此自测需要 ffmpeg 合成临时短视频；产品本身不依赖 ffmpeg")
    with tempfile.TemporaryDirectory(prefix="cabinet-video-end-") as directory:
        root = Path(directory)
        sample = root / "synthetic.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                        "color=c=blue:s=160x90:r=10:d=0.8", "-pix_fmt", "yuv420p", str(sample)],
                       check=True, capture_output=True)
        test_settings(root / "settings", t)
        test_real_playback(root / "playback", sample, t)
        test_mixed_list(root / "mixed", sample, t)
        test_guards(root / "guards", sample, t)
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
