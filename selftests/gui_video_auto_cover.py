"""视频自动封面集成回归：临时双库、真实导入和设置；抽帧由小 PNG 替代。"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QImage

from app.db import connect
from app.import_preferences import apply_import_auto_cover
from app.importer import ImportOptions, cleanup_extracted_zips, import_folder_as_project, scan_folders
from app.library import Library
from app.models import FileItem, Project
from app.repository import Repository
from app.storage_cleanup import cleanup_storage_sources
from app.ui import mw_dnd
from app.ui.settings import SettingsDialog
from selftests._common import T
from selftests.gui_library_media_settings import _close_widget
from selftests.gui_main_window_regressions import _app, _close_window, _window


FRAME_HELPER = "app.video_cover.save_first_video_frame"


def _frame(source: Path, target: Path) -> None:
    """验证实际输入可读，生成真实 PNG，不调用媒体解码器。"""
    if Path(source).read_bytes() != b"synthetic-video":
        raise AssertionError(f"抽帧没有使用本次合成视频：{source}")
    image = QImage(8, 6, QImage.Format_RGB32)
    image.fill(0x336699)
    if not image.save(str(target), "PNG"):
        raise AssertionError(f"测试 PNG 保存失败：{target}")


def _source(root: Path, name: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic-video")
    return path


def _settings(repo: Repository, parent: bool = True, child: bool = True) -> None:
    repo.set_setting("import_auto_cover", "1" if parent else "0")
    repo.set_setting("import_auto_cover_video", "1" if child else "0")


def _cover(repo: Repository, pid: int) -> FileItem | None:
    project = repo.get_project(pid)
    return repo.get_file(project.cover_file_id) if project.cover_file_id else None


def _assert_generated(t: T, label: str, repo: Repository, library: Library, pid: int) -> None:
    cover = _cover(repo, pid)
    t.assert_true(f"{label}有封面", cover is not None)
    if cover is None:
        return
    t.assert_eq(f"{label}封面登记为库内衍生图片", (cover.origin, cover.kind, cover.is_relative), ("generated", "image", True))
    path = library.resolve(cover.path, cover.is_relative)
    t.assert_true(f"{label}图片真实可读", not QImage(str(path)).isNull())
    t.assert_true(f"{label}图片位于当前仓库", path.resolve().is_relative_to(library.root.resolve()))


def _import(repo: Repository, library: Library, path: Path, mode: str = "link"):
    plans = scan_folders([path], repo)
    try:
        return import_folder_as_project(repo, library, plans[0], ImportOptions(storage_mode=mode))
    finally:
        cleanup_extracted_zips(plans)


def test_selection(t: T, root: Path) -> None:
    """共享策略过滤候选并按逻辑目录自然排序，主子开关控制生效。"""
    root.mkdir()
    repo = Repository(connect(root / "test.db"))
    library = Library(root / "library")
    try:
        def project(rows: list[tuple[str, str, str, bool, str]]) -> Project:
            item = Project(title="选择回归")
            item.id = repo.save_project(item)
            for name, kind, origin, missing, subfolder in rows:
                path = _source(root, name)
                fid = repo.add_file(FileItem(project_id=item.id, path=str(path), kind=kind, origin=origin, subfolder=subfolder))
                if missing:
                    repo.set_file_missing(fid, True)
            return item

        video = [("clip2.mp4", "video", "user", False, "")]
        with patch(FRAME_HELPER, side_effect=_frame) as capture:
            item = project(video)
            repo.set_setting("import_auto_cover", "1")
            t.assert_eq("缺省子选项关闭", apply_import_auto_cover(repo, item, library), False)
            _settings(repo, parent=False)
            t.assert_eq("主关闭子开启不生效", apply_import_auto_cover(repo, item, library), False)
            _settings(repo, child=False)
            t.assert_eq("主开启子关闭不生效", apply_import_auto_cover(repo, item, library), False)
            t.assert_eq("三个关闭情形均不抽帧", capture.call_count, 0)
            _settings(repo)

            item = project(video + [("picture.png", "image", "user", False, "")])
            t.assert_true("有用户图片优先设置", apply_import_auto_cover(repo, item, library))
            t.assert_eq("图片优先不抽帧", capture.call_count, 0)
            t.assert_eq("优先用户图片", _cover(repo, item.id).origin, "user")

            item = project([
                ("derived.png", "image", "generated", False, ""),
                ("missing.png", "image", "user", True, ""),
                ("derived.mp4", "video", "generated", False, ""),
                ("missing.mp4", "video", "user", True, ""),
                ("chapter10.mp4", "video", "user", False, "chapter10"),
                ("clip10.mp4", "video", "user", False, "chapter2"),
                ("clip2.mp4", "video", "user", False, "chapter2"),
            ])
            t.assert_true("排除缺失和衍生候选后抽帧", apply_import_auto_cover(repo, item, library))
            t.assert_eq("按逻辑目录及文件名自然顺序选首视频", Path(capture.call_args.args[0]).name, "clip2.mp4")
            _assert_generated(t, "共享策略", repo, library, item.id)
            old_cover = item.cover_file_id
            count = capture.call_count
            t.assert_eq("已有封面直接保留", apply_import_auto_cover(repo, item, library), False)
            t.assert_eq("已有封面不额外抽帧", capture.call_count, count)
            t.assert_eq("已有封面ID保持", repo.get_project(item.id).cover_file_id, old_cover)
            item = project([("readme.txt", "other", "user", False, "")])
            t.assert_eq("没有视频不生成封面", apply_import_auto_cover(repo, item, library), False)
            t.assert_eq("无候选不调用抽帧", capture.call_count, count)
    finally:
        repo.conn.close()


def test_imports_and_failures(t: T, root: Path) -> None:
    """文件夹 link/copy 与 ZIP 真实落库，失败和取消保留原导入并清理截图。"""
    root.mkdir()
    repo = Repository(connect(root / "test.db"))
    library = Library(root / "library")
    folder = root / "videos"
    _source(folder, "clip10.mp4")
    _source(folder, "clip2.mp4")
    try:
        _settings(repo)
        archive = root / "videos.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            for path in folder.iterdir():
                zipped.write(path, path.name)
        with patch(FRAME_HELPER, side_effect=_frame) as capture:
            for label, path, mode in (("文件夹链接", folder, "link"), ("文件夹仓储", folder, "copy"), ("ZIP", archive, "copy")):
                result = _import(repo, library, path, mode)
                t.assert_eq(f"{label}导入文件计数不含衍生封面", result.n_files, 2)
                t.assert_eq(f"{label}成功无警告", result.warnings, [])
                _assert_generated(t, label, repo, library, result.project_id)
                video = next(row for row in repo.list_files(result.project_id) if row.kind == "video" and Path(row.path).name == "clip2.mp4")
                t.assert_eq(f"{label}抽帧使用已入库视频路径", Path(capture.call_args.args[0]), library.resolve(video.path, video.is_relative))

        for message in ("模拟解码失败", "已取消提取视频首帧"):
            def fail(_source: Path, target: Path) -> None:
                Path(target).write_bytes(b"partial PNG")
                raise RuntimeError(message)

            with patch(FRAME_HELPER, side_effect=fail):
                result = _import(repo, library, folder)
            t.assert_eq(f"{message}原导入仍成功", result.n_files, 2)
            t.assert_eq(f"{message}保留用户记录", len(repo.list_files(result.project_id)), 2)
            t.assert_eq(f"{message}没有封面", _cover(repo, result.project_id), None)
            t.assert_true(f"{message}返回汇总警告", any(message in warning for warning in result.warnings))
            t.assert_eq(f"{message}清理截图残片", list(library.project_dir(result.project_id).glob("__cover_video_*.png")), [])

        original_save = repo.save_project

        def fail_save(project: Project) -> int:
            if project.cover_file_id is not None:
                raise RuntimeError("模拟封面保存失败")
            return original_save(project)

        with patch(FRAME_HELPER, side_effect=_frame), patch.object(repo, "save_project", side_effect=fail_save):
            result = _import(repo, library, folder)
        t.assert_eq("封面保存失败补偿删除衍生记录", len(repo.list_files(result.project_id)), 2)
        t.assert_eq("封面保存失败补偿删除截图", list(library.project_dir(result.project_id).glob("__cover_video_*.png")), [])
        t.assert_eq("封面保存失败不留下封面", _cover(repo, result.project_id), None)
        t.assert_true("封面保存失败纳入导入警告", any("模拟封面保存失败" in warning for warning in result.warnings))
    finally:
        repo.conn.close()


def test_drop(t: T, root: Path) -> None:
    """真实拖入入口；仓储回收源后仍从已登记的仓库副本读取首帧。"""
    root.mkdir()
    win, repo = _window(root)
    try:
        _settings(repo)
        repo.set_setting("storage_recycle_source", "1")
        source = _source(root / "outside", "drop.mp4")
        recycled: list[Path] = []

        def trash(path: str | Path, *, allow_delete: bool = True) -> str:
            path = Path(path).resolve()
            if not path.is_relative_to(root.resolve()) or allow_delete:
                raise AssertionError("测试禁止触及临时目录之外或实际回收站")
            path.unlink()
            recycled.append(path)
            return "trash"

        def recycle(_parent, current_repo, library, candidates):
            return cleanup_storage_sources(current_repo, library, candidates)

        def frame_after_recycle(path: Path, target: Path) -> None:
            t.assert_eq("拖入先回收源文件再抽帧", recycled, [source.resolve()])
            t.assert_eq("抽帧时外部源已经不存在", source.exists(), False)
            t.assert_true("拖入抽帧使用当前仓库副本", Path(path).resolve().is_relative_to(win.library.root.resolve()))
            _frame(path, target)

        with patch.object(mw_dnd.QInputDialog, "getText", return_value=("视频拖入", True)), patch.object(
            win, "_ask_storage_for_import", return_value=("copy", ""),
        ), patch("app.ui.mw_files.recycle_sources_with_progress", side_effect=recycle), patch(
            "app.storage_cleanup.move_to_trash", side_effect=trash,
        ), patch(FRAME_HELPER, side_effect=frame_after_recycle) as capture, patch("app.ui.mw_files.warn") as file_warn, patch("app.ui.mw_dnd.warn") as cover_warn:
            win._drop_create_project(win._expand_paths([source]), source_paths=[source])
        projects = repo.list_projects(keyword="视频拖入")
        t.assert_eq("真实拖入创建单个项目", len(projects), 1)
        t.assert_eq("拖入抽帧一次", capture.call_count, 1)
        t.assert_eq("拖入成功没有警告", file_warn.call_count + cover_warn.call_count, 0)
        if projects:
            _assert_generated(t, "拖入", repo, win.library, projects[0].id)

        # 取消抽帧只影响封面，入口仍报告导入成功并给出一次警告。
        source = _source(root, "cancel.mp4")
        repo.set_setting("storage_recycle_source", "0")
        with patch.object(mw_dnd.QInputDialog, "getText", return_value=("取消视频封面", True)), patch.object(
            win, "_ask_storage_for_import", return_value=("link", ""),
        ), patch(FRAME_HELPER, side_effect=RuntimeError("已取消提取视频首帧")), patch("app.ui.mw_dnd.warn") as warning:
            win._drop_create_project(win._expand_paths([source]), source_paths=[source])
        projects = repo.list_projects(keyword="取消视频封面")
        t.assert_eq("取消抽帧仍创建拖入项目", len(projects), 1)
        t.assert_eq("取消抽帧只给一次汇总警告", warning.call_count, 1)
        if projects:
            t.assert_eq("取消抽帧保留已导入文件", len(repo.list_files(projects[0].id)), 1)
            t.assert_eq("取消抽帧拖入项目无封面", projects[0].cover_file_id, None)
    finally:
        _close_window(win)
        repo.conn.close()


def test_settings(t: T, root: Path) -> None:
    """子开关持久化，主关闭仅禁用，重开设置及另一个库保持隔离。"""
    root.mkdir()
    for index in range(2):
        library_root = root / str(index)
        library_root.mkdir()
        db_path = library_root / "test.db"
        repo = Repository(connect(db_path))
        try:
            dialog = SettingsDialog(repo, library_root, db_path)
            try:
                parent, child = dialog.chk_import_auto_cover, dialog.chk_import_video_cover
                t.assert_eq(f"库{index}子选项默认关闭", child.isChecked(), False)
                t.assert_eq(f"库{index}主关闭时子选项禁用", child.isEnabled(), False)
                if index == 0:
                    parent.click()
                    t.assert_true("主开启使子选项可用", child.isEnabled())
                    child.click()
                    t.assert_eq("子开启立即落库", repo.get_setting("import_auto_cover_video"), "1")
                    parent.click()
                    t.assert_true("主关闭保留子勾选", child.isChecked())
                    t.assert_eq("主关闭禁用子选项", child.isEnabled(), False)
            finally:
                _close_widget(dialog)
        finally:
            repo.conn.close()

    for index in range(2):
        library_root = root / str(index)
        repo = Repository(connect(library_root / "test.db"))
        try:
            dialog = SettingsDialog(repo, library_root, library_root / "test.db")
            try:
                t.assert_eq(f"库{index}重开保留各库子值", dialog.chk_import_video_cover.isChecked(), index == 0)
                t.assert_eq(f"库{index}重开仍由主开关禁用", dialog.chk_import_video_cover.isEnabled(), False)
                if index == 0:
                    dialog.chk_import_auto_cover.click()
                    t.assert_true("重开主开关后恢复子勾选", dialog.chk_import_video_cover.isChecked())
                    t.assert_true("重开主开关后恢复子可用", dialog.chk_import_video_cover.isEnabled())
            finally:
                _close_widget(dialog)
        finally:
            repo.conn.close()


def main() -> bool:
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-video-auto-cover-") as temporary:
        root = Path(temporary)
        test_selection(t, root / "选择")
        test_imports_and_failures(t, root / "导入")
        test_drop(t, root / "拖入")
        test_settings(t, root / "设置")
    return t.report()


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
