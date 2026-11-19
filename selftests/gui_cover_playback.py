"""手动截封面保持视频状态的 GUI 回归；仅使用显式临时库和合成文件。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QItemSelectionModel, Qt, QUrl
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtMultimedia import QMediaPlayer

from selftests._preview_wait import wait_preview_probe

from app.models import FileItem, Project
from app.ui.main_window import MainWindow
from selftests._common import T, closing_repos
from selftests.gui_main_window_regressions import (
    _app, _close_window, _find_file_tree_item, _window,
)


def _directories(win: MainWindow) -> dict[str, bool]:
    """记录逻辑目录展开状态，不保留重建后失效的 Qt 节点。"""
    result: dict[str, bool] = {}

    def collect(parent) -> None:
        for i in range(parent.childCount()):
            item = parent.child(i)
            if item.data(0, Qt.UserRole) == -1:
                result[item.data(0, Qt.UserRole + 1)] = item.isExpanded()
            collect(item)

    collect(win.tbl_files.invisibleRootItem())
    return result


def _player() -> tuple[Mock, dict]:
    """替代媒体后端，记录真实预览路径的调用且不解码合成视频。"""
    state = {"source": QUrl(), "position": 0, "playback": QMediaPlayer.StoppedState}
    player = Mock(name="synthetic_player")

    def set_source(source: QUrl) -> None:
        state.update(source=source, position=0)

    def set_playback(value) -> None:
        state["playback"] = value

    player.setSource.side_effect = set_source
    player.setPosition.side_effect = lambda value: state.update(position=value)
    player.play.side_effect = lambda: set_playback(QMediaPlayer.PlayingState)
    player.pause.side_effect = lambda: set_playback(QMediaPlayer.PausedState)
    player.stop.side_effect = lambda: set_playback(QMediaPlayer.StoppedState)
    player.source.side_effect = lambda: state["source"]
    player.position.side_effect = lambda: state["position"]
    player.playbackState.side_effect = lambda: state["playback"]
    return player, state


def test_capture(tmp: Path, t: T, mode: str, origin: str, paused: bool) -> None:
    """真实主窗口截图落库，覆盖多选、过滤、两种视图和连续截图。"""
    win, repo = _window(tmp)
    with closing_repos(repo):
        try:
            pid = repo.save_project(Project(title="手动视频封面"))

            def add(name: str, folder: str, kind: str = "video") -> int:
                path = tmp / "synthetic_source" / folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic-video-no-decoding")
                return repo.add_file(FileItem(
                    project_id=pid, path=str(path.resolve()), is_relative=False,
                    kind=kind, subfolder=folder, origin="user",
                ))

            first = add("clip1.mp4", "media")
            second = add("clip2.mp4", "media")
            add("hidden.txt", "folded/nested", "doc")
            # 真实滚动范围，避免只验证滚动条始终为零的退化情况。
            for i in range(45):
                add(f"tail{i:02}.txt", "zzz", "doc")
            repo.set_setting("video_autoplay", "1")
            repo.set_project_setting(pid, "files_view_mode", mode)
            repo.set_project_setting(pid, "files_view_origin_filter", origin)
            win.refresh_projects()
            win._select_project_by_id(pid)
            _app().processEvents()

            player, state = _player()
            with (
                patch.object(win.preview._video, "_player", player),
                patch.object(win.preview, "show_file", wraps=win.preview.show_file) as show,
                patch.object(win.preview._video, "load", wraps=win.preview._video.load) as load,
                patch.object(win.preview._video, "stop", wraps=win.preview._video.stop) as stop,
                patch.object(win.preview, "capture_pixmap") as capture,
            ):
                item = _find_file_tree_item(win, first)
                other = _find_file_tree_item(win, second)
                t.assert_true("测试文件行存在", item is not None and other is not None)
                if item is None or other is None:
                    return
                win.tbl_files.setCurrentItem(item, 0, QItemSelectionModel.ClearAndSelect)
                other.setSelected(True)
                if mode == "tree":
                    for i in range(win.tbl_files.topLevelItemCount()):
                        node = win.tbl_files.topLevelItem(i)
                        if node.data(0, Qt.UserRole + 1) == "folded":
                            node.setExpanded(False)
                wait_preview_probe(win.preview)
                player.setPosition(12345)
                if paused:
                    player.pause()
                _app().processEvents()
                scroll = win.tbl_files.verticalScrollBar()
                scroll.setValue(scroll.maximum() // 2)
                before_scroll = scroll.value()
                before_dirs = _directories(win)
                before_state = state.copy()
                t.assert_true("测试具有非零滚动范围", before_scroll > 0)
                initial_count = len(repo.list_files(pid))
                previous_path: Path | None = None
                previous_bytes: bytes | None = None

                for shot, color in enumerate(("#2378b9", "#dc9131"), 1):
                    pix = QPixmap(12, 9)
                    pix.fill(QColor(color))
                    capture.return_value = pix
                    show.reset_mock()
                    load.reset_mock()
                    stop.reset_mock()
                    player.reset_mock()
                    win.action_set_cover()
                    _app().processEvents()

                    label = f"{mode}/{origin}/{'暂停' if paused else '播放'}/截图{shot}"
                    project = repo.get_project(pid)
                    cover = repo.get_file(project.cover_file_id) if project.cover_file_id else None
                    t.assert_true(f"{label}登记封面", cover is not None)
                    if cover is None:
                        continue
                    t.assert_eq(f"{label}衍生仓储图片", (cover.origin, cover.kind, cover.is_relative), ("generated", "image", True))
                    path = win.library.resolve(cover.path, cover.is_relative)
                    saved = QPixmap(str(path))
                    t.assert_true(f"{label}真实 PNG 存在且颜色正确", not saved.isNull() and saved.toImage().pixelColor(0, 0) == QColor(color))
                    t.assert_true(f"{label}位于临时仓库", path.resolve().is_relative_to(win.library.root.resolve()))
                    index = win.proj_model.index_of_id(pid)
                    model_project = win.proj_model.project_at(index.row())
                    model_cover = win.proj_model.data(index, win.proj_model.RoleCover)
                    t.assert_eq(f"{label}项目模型封面ID更新", model_project.cover_file_id, cover.id)
                    t.assert_true(f"{label}项目模型图片更新", model_cover is not None and model_cover.toImage().pixelColor(0, 0) == QColor(color))
                    t.assert_eq(f"{label}项目模型文件计数更新", win.proj_model._file_counts[pid], initial_count + shot)
                    t.assert_eq(f"{label}保持当前视频行", win._current_file_row_id(), first)
                    t.assert_eq(f"{label}保持预览文件ID", win._current_file_id, first)
                    t.assert_eq(f"{label}保持多选", set(win._selected_file_ids()), {first, second})
                    t.assert_eq(f"{label}保持播放源进度和暂停状态", state, before_state)
                    t.assert_eq(f"{label}不重选预览", show.call_count, 0)
                    t.assert_eq(f"{label}不加载或停止视频", (load.call_count, stop.call_count), (0, 0))
                    t.assert_eq(f"{label}不调用播放器", player.mock_calls, [])
                    t.assert_eq(f"{label}保持目录展开折叠", _directories(win), before_dirs)
                    t.assert_eq(f"{label}保持滚动位置", scroll.value(), before_scroll)
                    t.assert_eq(f"{label}封面遵循来源过滤", _find_file_tree_item(win, cover.id) is not None, origin == "all")
                    items = win._preview_file_items()
                    current_index = next(i for i, row in enumerate(items) if row.data(0, Qt.UserRole) == first)
                    t.assert_eq(f"{label}导航边界同步", (win.preview._btn_previous.isEnabled(), win.preview._btn_next.isEnabled()), (current_index > 0, current_index + 1 < len(items)))
                    if previous_path is not None:
                        t.assert_true(f"{label}新截图不复用旧路径", path != previous_path)
                        t.assert_eq(f"{label}旧截图内容保留", previous_path.read_bytes(), previous_bytes)
                    previous_path, previous_bytes = path, path.read_bytes()

                show.reset_mock()
                load.reset_mock()
                stop.reset_mock()
                player.reset_mock()
                win.preview._btn_next.click()
                wait_preview_probe(win.preview)
                _app().processEvents()
                t.assert_eq("截图后下一文件从原视频继续", win._current_file_row_id(), second)
                t.assert_eq("下一文件收束多选", win._selected_file_ids(), [second])
                t.assert_eq("下一文件真实预览只切换一次", (show.call_count, load.call_count, stop.call_count), (1, 1, 1))
                t.assert_eq("下一文件媒体源正确", Path(state["source"].toLocalFile()).name, "clip2.mp4")
                t.assert_eq("下一文件按设置自动播放", state["playback"], QMediaPlayer.PlayingState)
        finally:
            _close_window(win)


def main() -> bool:
    _app()
    t = T()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        for mode in ("tree", "flat"):
            for origin in ("all", "user"):
                for paused in (False, True):
                    name = f"{mode}_{origin}_{'paused' if paused else 'playing'}"
                    print(f"-> {name}")
                    test_capture(Path(directory) / name, t, mode, origin, paused)
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
