"""封面替换 GUI 回归；只使用显式临时库、合成图片及模拟回收目录。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QItemSelectionModel, QMimeData
from PySide6.QtGui import QColor, QPixmap

from app.db import connect
from selftests._preview_wait import wait_preview_probe

from app.models import FileItem, Project
from app.repository import Repository
from app.ui.main_window import MainWindow
from app.ui.settings import SettingsDialog
from selftests._common import T, closing_repos
from selftests.gui_cover_playback import _player
from selftests.gui_library_media_settings import _close_widget
from selftests.gui_main_window_regressions import (
    _app, _close_window, _find_file_tree_item, _window,
)


KEY = "cover_replace_generated"


class FakeTrash:
    """模拟回收，拒绝处理本测试目录以外的文件及硬删除回退。"""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.destination = self.root / "fake-trash"
        self.destination.mkdir(parents=True)
        self.paths: list[Path] = []
        self.failure = False

    def __call__(self, path: str | Path, *, allow_delete: bool = True) -> str:
        source = Path(path).resolve()
        if not source.is_relative_to(self.root):
            raise AssertionError(f"禁止操作临时目录之外的文件：{source}")
        if allow_delete:
            raise AssertionError("封面回收禁止硬删除回退")
        if self.failure:
            raise PermissionError("合成测试：旧封面被占用")
        source.rename(self.destination / f"{len(self.paths)}-{source.name}")
        self.paths.append(source)
        return "trash"


def _pix(color: str) -> QPixmap:
    """构造可验证颜色的真实封面图片。"""
    pix = QPixmap(14, 10)
    pix.fill(QColor(color))
    return pix


def _cover(win: MainWindow, repo: Repository, pid: int) -> tuple[FileItem, Path]:
    """从当前测试库取得封面及其实际路径。"""
    project = repo.get_project(pid)
    assert project is not None and project.cover_file_id is not None
    cover = repo.get_file(project.cover_file_id)
    assert cover is not None
    return cover, win.library.resolve(cover.path, cover.is_relative)


def test_settings(tmp: Path, t: T) -> None:
    """设置默认保留、即时保存、重开持久化及两个独立库的隔离。"""
    for index in range(2):
        root = tmp / f"library-{index}"
        root.mkdir(parents=True)
        db_path = root / "cabinet.db"
        repo = Repository(connect(db_path))
        with closing_repos(repo):
            dialog = SettingsDialog(repo, root, db_path)
            try:
                combo = dialog.cmb_cover_replacement
                t.assert_eq(f"库{index}默认保留旧封面", combo.currentData(), "0")
                t.assert_eq(f"库{index}缺省值为保留", repo.get_setting(KEY, "0"), "0")
                if index == 0:
                    combo.setCurrentIndex(combo.findData("1"))
                    t.assert_eq("替换选项即时落库", repo.get_setting(KEY, "0"), "1")
                    combo.setCurrentIndex(combo.findData("0"))
                    t.assert_eq("保留选项即时落库", repo.get_setting(KEY, "1"), "0")
                    combo.setCurrentIndex(combo.findData("1"))
            finally:
                _close_widget(dialog)

    for index in range(2):
        root = tmp / f"library-{index}"
        db_path = root / "cabinet.db"
        repo = Repository(connect(db_path))
        with closing_repos(repo):
            dialog = SettingsDialog(repo, root, db_path)
            try:
                expected = "1" if index == 0 else "0"
                t.assert_eq(f"库{index}重开设置持久化且隔离", dialog.cmb_cover_replacement.currentData(), expected)
            finally:
                _close_widget(dialog)


def test_capture(tmp: Path, t: T, replace: bool, paused: bool) -> None:
    """两次真实截图验证文件回收、记录数量及视频播放状态。"""
    win, repo = _window(tmp)
    trash = FakeTrash(tmp)
    with closing_repos(repo):
        try:
            pid = repo.save_project(Project(title="封面截图替换"))
            source = tmp / "synthetic.mp4"
            source.write_bytes(b"synthetic-video-no-decoding")
            fid = repo.add_file(FileItem(
                project_id=pid, path=str(source), is_relative=False,
                kind="video", origin="user",
            ))
            # 保留路径使用真正的缺省设置，防止默认策略意外变化。
            if replace:
                repo.set_setting(KEY, "1")
            repo.set_setting("video_autoplay", "1")
            repo.set_project_setting(pid, "files_view_origin_filter", "all")
            win.refresh_projects()
            win._select_project_by_id(pid)
            player, state = _player()
            with (
                patch("app.cover_files.move_to_trash", side_effect=trash),
                patch.object(win.preview._video, "_player", player),
                patch.object(win.preview, "capture_pixmap") as capture,
                patch.object(win.preview, "show_file", wraps=win.preview.show_file) as show,
            ):
                item = _find_file_tree_item(win, fid)
                assert item is not None
                win.tbl_files.setCurrentItem(item, 0, QItemSelectionModel.ClearAndSelect)
                wait_preview_probe(win.preview)
                player.setPosition(23456)
                if paused:
                    player.pause()
                _app().processEvents()
                before = state.copy()
                previous: tuple[FileItem, Path] | None = None
                for shot, color in enumerate(("#267fc1", "#e2a129"), 1):
                    capture.return_value = _pix(color)
                    show.reset_mock()
                    player.reset_mock()
                    win.action_set_cover()
                    _app().processEvents()
                    cover, path = _cover(win, repo, pid)
                    label = f"{'替换' if replace else '保留'}/{'暂停' if paused else '播放'}/截图{shot}"
                    t.assert_eq(f"{label}登记衍生图片", (cover.origin, cover.kind), ("generated", "image"))
                    t.assert_true(f"{label}图片正确", QPixmap(str(path)).toImage().pixelColor(0, 0) == QColor(color))
                    t.assert_eq(f"{label}保持视频行", win._current_file_row_id(), fid)
                    t.assert_eq(f"{label}保持预览ID", win._current_file_id, fid)
                    t.assert_eq(f"{label}保持源进度与播放状态", state, before)
                    t.assert_eq(f"{label}不触发预览重载", show.call_count, 0)
                    t.assert_eq(f"{label}不调用播放器", player.mock_calls, [])
                    expected_count = 2 if replace else shot + 1
                    t.assert_eq(f"{label}文件记录数量", len(repo.list_files(pid)), expected_count)
                    t.assert_eq(f"{label}项目模型数量", win.proj_model._file_counts[pid], expected_count)
                    if previous is not None:
                        old, old_path = previous
                        t.assert_true(f"{label}新图片独立路径", path != old_path)
                        t.assert_eq(f"{label}旧文件是否保留", old_path.exists(), not replace)
                        t.assert_eq(f"{label}旧记录是否保留", repo.get_file(old.id) is not None, not replace)
                    previous = cover, path
                t.assert_eq("回收次数与设置一致", len(trash.paths), 1 if replace else 0)
                t.assert_true("原始视频始终保留", source.exists() and repo.get_file(fid) is not None)
        finally:
            _close_window(win)


def test_clipboard_and_cleanup_failure(tmp: Path, t: T) -> None:
    """剪切板入口替换衍生封面、保留原图，回收失败仍成功设置新封面。"""
    win, repo = _window(tmp)
    trash = FakeTrash(tmp)
    with closing_repos(repo):
        try:
            pid = repo.save_project(Project(title="剪切板封面"))
            source = tmp / "original.png"
            _pix("#883399").save(str(source), "PNG")
            original_bytes = source.read_bytes()
            original = repo.add_file(FileItem(
                project_id=pid, path=str(source), is_relative=False,
                kind="image", origin="user",
            ))
            project = repo.get_project(pid)
            project.cover_file_id = original
            repo.save_project(project)
            repo.set_setting(KEY, "1")
            win.refresh_projects()
            win._select_project_by_id(pid)
            # 替代系统剪切板边界，避免覆盖用户实际的剪切板内容。
            mime = QMimeData()
            clipboard = Mock()
            clipboard.mimeData.return_value = mime
            clipboard.image.side_effect = lambda: mime.imageData()
            with (
                patch("app.cover_files.move_to_trash", side_effect=trash),
                patch("app.ui.mw_files.warn") as warning,
                patch("PySide6.QtWidgets.QApplication.clipboard", return_value=clipboard),
            ):
                for color in ("#6633aa", "#33bb88"):
                    mime.setImageData(_pix(color).toImage())
                    win.action_set_cover_from_clipboard()
                    _app().processEvents()
                    cover, path = _cover(win, repo, pid)
                    t.assert_eq("剪切板登记衍生封面", cover.origin, "generated")
                    t.assert_true("剪切板新封面颜色正确", QPixmap(str(path)).toImage().pixelColor(0, 0) == QColor(color))
                    t.assert_eq("原始图片字节保留", source.read_bytes(), original_bytes)
                    t.assert_true("原始图片记录保留", repo.get_file(original) is not None)
                t.assert_eq("剪切板二次设置只回收衍生封面", len(trash.paths), 1)
                t.assert_eq("正常更换不警告", warning.call_count, 0)
                old, old_path = _cover(win, repo, pid)
                trash.failure = True
                mime.setImageData(_pix("#cc6633").toImage())
                win.action_set_cover_from_clipboard()
                _app().processEvents()
                latest, latest_path = _cover(win, repo, pid)
                t.assert_true("回收失败仍设新封面", latest.id != old.id and latest_path.exists())
                t.assert_true("回收失败原文件保留", old_path.exists())
                t.assert_true("回收失败旧记录保留", repo.get_file(old.id) is not None)
                t.assert_eq("回收失败仅一次汇总警告", warning.call_count, 1)
                if warning.call_args is not None:
                    details = warning.call_args.kwargs.get("detailed", "")
                    for label, token in (
                        ("失败详情包含源路径", str(old_path)),
                        ("失败详情包含记录ID", str(old.id)),
                        ("失败详情包含异常类型", "PermissionError"),
                        ("失败详情包含异常原因", "旧封面被占用"),
                    ):
                        t.assert_in(label, token, details)
                t.assert_in("回收失败仍显示新封面成功", "封面", win.statusBar().currentMessage())
        finally:
            _close_window(win)


def main() -> bool:
    _app()
    t = T()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        test_settings(root / "settings", t)
        for replace in (False, True):
            for paused in (False, True):
                case = root / f"capture-{replace}-{paused}"
                print(f"-> {case.name}")
                test_capture(case, t, replace, paused)
        test_clipboard_and_cleanup_failure(root / "clipboard", t)
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
