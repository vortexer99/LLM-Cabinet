"""库级媒体设置 GUI 回归：即时保存、库隔离、文件选择和视频播放调用。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QUrl
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget

from app.db import connect
from app.models import FileItem, Project
from app.repository import Repository
from app.ui import mw_dnd
from app.ui.main_window import MainWindow
from app.ui.preview import PreviewPanel
from app.ui.settings import SettingsDialog
from selftests._common import T
from selftests._preview_wait import wait_preview_probe
from selftests.gui_main_window_regressions import (
    _app, _close_window, _find_file_tree_item, _window,
)


def _close_widget(widget: QWidget) -> None:
    """及时销毁控件，避免临时库关闭后仍收到异步信号。"""
    widget.close()
    widget.deleteLater()
    _app().sendPostedEvents(None, QEvent.DeferredDelete)
    _app().processEvents()


def test_settings(t: T, root: Path) -> None:
    """复选框即时落库；重开设置和另一项目库都读取各自的值。"""
    root.mkdir()
    roots = [root / "甲库", root / "乙库"]
    for library_root in roots:
        library_root.mkdir()
    repos = [Repository(connect(p / "cabinet.db")) for p in roots]
    controls = (
        ("chk_import_auto_cover", "import_auto_cover", "导入自动封面"),
        ("chk_auto_select_first_file", "auto_select_first_file", "自动选择首文件"),
        ("chk_video_autoplay", "video_autoplay", "视频自动播放"),
        ("chk_preview_wheel_navigation", "preview_wheel_navigation", "滚轮切换文件"),
    )
    try:
        for index, (repo, library_root) in enumerate(zip(repos, roots)):
            dialog = SettingsDialog(repo, library_root, library_root / "cabinet.db")
            try:
                dialog.set_active_category("项目库")
                for attr, key, label in controls:
                    checkbox = getattr(dialog, attr)
                    t.assert_eq(f"库{index} {label}默认关闭", checkbox.isChecked(), False)
                    t.assert_eq(f"库{index} {label}缺省设置为关", repo.get_setting(key, "0"), "0")
                    if index == 0:
                        checkbox.click()
                        t.assert_eq(f"{label}点击立即保存开启", repo.get_setting(key, "0"), "1")
                        checkbox.click()
                        t.assert_eq(f"{label}点击立即保存关闭", repo.get_setting(key, "1"), "0")
                        checkbox.click()
            finally:
                _close_widget(dialog)

        # 新连接读取真实持久化值，避免只验证同一 Repository 的状态。
        for index, library_root in enumerate(roots):
            reopened_repo = Repository(connect(library_root / "cabinet.db"))
            try:
                dialog = SettingsDialog(reopened_repo, library_root, library_root / "cabinet.db")
                try:
                    for attr, key, label in controls:
                        expected = index == 0
                        t.assert_eq(f"库{index} 重开设置保留{label}", getattr(dialog, attr).isChecked(), expected)
                        t.assert_eq(f"库{index} 持久化{label}相互隔离", reopened_repo.get_setting(key, "0"), "1" if expected else "0")
                finally:
                    _close_widget(dialog)
            finally:
                reopened_repo.conn.close()
    finally:
        for repo in repos:
            repo.conn.close()


def _add_video(repo: Repository, root: Path) -> tuple[int, int, Path]:
    """空视频只用来验证路由；所有播放器加载均由测试拦截。"""
    path = root / "预览.mp4"
    path.write_bytes(b"mock-video")
    project_id = repo.save_project(Project(title="媒体设置回归"))
    file_id = repo.add_file(FileItem(
        project_id=project_id, path=str(path), is_relative=False, kind="video",
    ))
    return project_id, file_id, path


def _select_video(win: MainWindow, repo: Repository, project_id: int, file_id: int) -> None:
    """从真实文件树选择触发 itemSelectionChanged 信号。"""
    win._current_project_id = project_id
    win._show_project(repo.get_project(project_id))
    item = _find_file_tree_item(win, file_id)
    if item is None:
        raise AssertionError("临时视频未出现在主窗口文件树中")
    win.tbl_files.setCurrentItem(item)
    item.setSelected(True)
    _app().processEvents()


def test_main_window_selection(t: T, root: Path) -> None:
    """主窗口每次选择都读取当前库，设置变更和换库不会沿用旧值。"""
    root.mkdir()
    first = root / "甲库"
    second = root / "乙库"
    first.mkdir()
    second.mkdir()
    win, repo = _window(first)
    other_repo = Repository(connect(second / "cabinet.db"))
    try:
        project_id, file_id, path = _add_video(repo, first)
        other_pid, other_fid, other_path = _add_video(other_repo, second)
        with patch.object(win.preview, "show_file") as show:
            _select_video(win, repo, project_id, file_id)
            t.assert_eq("主窗口选择视频默认不自动播放", show.call_args, call(str(path), autoplay=False))
            repo.set_setting("video_autoplay", "1")
            _select_video(win, repo, project_id, file_id)
            t.assert_eq("设置开启后再次选择立即自动播放", show.call_args, call(str(path), autoplay=True))
            repo.set_setting("video_autoplay", "0")
            _select_video(win, repo, project_id, file_id)
            t.assert_eq("设置关闭后再次选择立即停止自动播放", show.call_args, call(str(path), autoplay=False))

            repo.set_setting("video_autoplay", "1")
            _select_video(win, repo, project_id, file_id)
            win.repo = other_repo
            _select_video(win, other_repo, other_pid, other_fid)
            t.assert_eq("更换当前库不沿用甲库自动播放值", show.call_args, call(str(other_path), autoplay=False))
            other_repo.set_setting("video_autoplay", "1")
            _select_video(win, other_repo, other_pid, other_fid)
            t.assert_eq("更换库后设置仍即时生效", show.call_args, call(str(other_path), autoplay=True))
            other_repo.set_setting("video_autoplay", "0")
            win.repo = repo
            _select_video(win, repo, project_id, file_id)
            t.assert_eq("切回甲库恢复甲库设置", show.call_args, call(str(path), autoplay=True))
    finally:
        win.repo = repo
        _close_window(win)
        repo.conn.close()
        other_repo.conn.close()

    # 实际切库会重建主窗口，这里验证新窗口同样只读取目标库设置。
    win, repo = _window(second)
    try:
        with patch.object(win.preview, "show_file") as show:
            _select_video(win, repo, other_pid, other_fid)
            t.assert_eq("切库重建主窗口读取目标库关闭值", show.call_args, call(str(other_path), autoplay=False))
    finally:
        _close_window(win)
        repo.conn.close()


def test_preview(t: T, root: Path) -> None:
    """拦截播放器方法验证调用顺序，不依赖真实视频解码后端。"""
    root.mkdir()
    video_path = str(root / "样本.mp4")
    Path(video_path).write_bytes(b"mock-video")
    image_path = str(root / "图片.png")
    image = QImage(8, 8, QImage.Format_RGB32)
    image.fill(0x33AA55)
    image.save(image_path)
    pdf_path = str(root / "文档.pdf")
    Path(pdf_path).write_bytes(b"mock-pdf")
    panel = PreviewPanel()
    player = panel._video._player
    try:
        with patch.object(player, "stop") as stop, patch.object(player, "setSource") as source, patch.object(player, "play") as play:
            calls = Mock()
            calls.attach_mock(stop, "stop")
            calls.attach_mock(source, "setSource")
            calls.attach_mock(play, "play")
            panel._video.load(video_path)
            t.assert_eq("视频默认停止、清理旧源再加载", calls.mock_calls, [call.stop(), call.setSource(QUrl()), call.setSource(QUrl.fromLocalFile(video_path))])
            calls.reset_mock()
            panel._video.load(video_path, autoplay=True)
            t.assert_eq("自动播放严格先停止清理再加载播放", calls.mock_calls, [call.stop(), call.setSource(QUrl()), call.setSource(QUrl.fromLocalFile(video_path)), call.play()])
            calls.reset_mock()
            panel.show_file(video_path)
            wait_preview_probe(panel)
            t.assert_eq("预览面板旧接口默认不播放", play.call_count, 0)
            t.assert_eq("旧接口仍切到视频页", panel._stack.currentIndex(), 1)
            calls.reset_mock()
            panel.show_file(video_path, autoplay=True)
            wait_preview_probe(panel)
            t.assert_eq("同一视频重复通知不停止、不重载、不恢复播放", calls.mock_calls, [])
            panel.show_file(str(root / "." / "样本.mp4"), autoplay=False)
            wait_preview_probe(panel)
            t.assert_eq("同一视频不同自动播放参数也跳过", calls.mock_calls, [])
            panel.show_file(None)
            wait_preview_probe(panel)
            calls.reset_mock()
            panel.show_file(video_path, autoplay=True)
            wait_preview_probe(panel)
            t.assert_eq("清空后重新选择视频正常自动播放", play.call_count, 1)
            calls.reset_mock()
            panel.show_file(video_path, autoplay=True)
            wait_preview_probe(panel)
            t.assert_eq("正在播放的视频重复通知也不重载", calls.mock_calls, [])
            another_video = str(root / "另一个.mp4")
            Path(another_video).write_bytes(b"mock-video")
            panel.show_file(another_video, autoplay=True)
            wait_preview_probe(panel)
            t.assert_eq("切换另一视频正常自动播放", play.call_count, 1)
            t.assert_eq("切换另一视频加载正确路径", source.call_args, call(QUrl.fromLocalFile(another_video)))
            calls.reset_mock()
            panel.show_file(video_path, autoplay=True)
            wait_preview_probe(panel)
            t.assert_eq("切换视频后再选回来重新自动播放", play.call_count, 1)
            calls.reset_mock()
            with patch.object(panel._image, "load", wraps=panel._image.load) as load:
                panel.show_file(image_path, autoplay=True)
                wait_preview_probe(panel)
                t.assert_eq("图片预览仍使用原加载接口", load.call_args, call(image_path))
            t.assert_true("切换图片停止旧视频", stop.call_count > 0)
            t.assert_eq("切换图片释放视频源", source.call_args, call(QUrl()))
            t.assert_eq("切换图片不调用视频播放", play.call_count, 0)
            t.assert_eq("图片预览页保持兼容", panel._stack.currentIndex(), 0)
            t.assert_true("图片仍可抓取封面", panel.capture_pixmap() is not None)
            with patch.object(panel._pdf, "load") as load:
                panel.show_file(pdf_path, autoplay=True)
                wait_preview_probe(panel)
                t.assert_eq("PDF预览仍使用原加载接口", load.call_args, call(pdf_path))
                t.assert_eq("PDF预览页保持兼容", panel._stack.currentIndex(), 2)
            panel.show_file(None)
            wait_preview_probe(panel)
            t.assert_eq("清空预览旧接口保持兼容", panel._stack.currentIndex(), 3)
            t.assert_eq("清空预览禁用打开按钮", panel._btn_open.isEnabled(), False)
            panel.show_file(str(root / "不存在.mp4"), autoplay=True)
            wait_preview_probe(panel)
            t.assert_eq("失效视频不触发播放", play.call_count, 0)
    finally:
        _close_widget(panel)


def test_drop_create_project(t: T, root: Path) -> None:
    """真实拖放新建项目导入图片，验证库级封面偏好确实影响导入结果。"""
    root.mkdir()
    paths = [root / "图片10.png", root / "图片2.png"]
    image = QImage(8, 8, QImage.Format_RGB32)
    image.fill(0x3366AA)
    for path in paths:
        image.save(str(path))
    win, repo = _window(root)
    old_pid: int | None = None
    try:
        for enabled in (False, True):
            if enabled:
                repo.set_setting("import_auto_cover", "1")
            title = f"拖放自动封面{'开启' if enabled else '默认关闭'}"
            with patch.object(mw_dnd.QInputDialog, "getText", return_value=(title, True)), patch.object(win, "_ask_storage_for_import", return_value=("link", "")):
                win._drop_create_project(win._expand_paths(paths), source_paths=paths)
            projects = repo.list_projects(keyword=title)
            t.assert_eq(f"{title}真实创建项目", len(projects), 1)
            if not projects:
                continue
            project = projects[0]
            files = repo.list_files(project.id)
            t.assert_eq(f"{title}真实导入全部图片", len(files), 2)
            if enabled:
                cover = repo.get_file(project.cover_file_id) if project.cover_file_id else None
                t.assert_eq("拖放开启后封面按数字自然排序选首图", Path(cover.path).name if cover else None, "图片2.png")
            else:
                t.assert_eq("拖放默认关闭不自动设置封面", project.cover_file_id, None)
                old_pid = project.id
        if old_pid is not None:
            t.assert_eq("开启自动封面不修改之前项目", repo.get_project(old_pid).cover_file_id, None)
    finally:
        _close_window(win)
        repo.conn.close()


def run(t: T, root: Path) -> None:
    _app()
    test_settings(t, root / "设置")
    test_main_window_selection(t, root / "主窗口")
    test_preview(t, root / "预览")
    test_drop_create_project(t, root / "拖放导入")


if __name__ == "__main__":
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-gui-media-settings-") as temporary:
        run(t, Path(temporary))
    sys.exit(0 if t.report() else 1)
