"""批量自动封面回归：显式临时库、真实登记文件与 Qt offscreen 对话框。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QDialog

from app.cover_files import CoverFileError
from app.db import connect
from app.import_preferences import apply_project_auto_cover
from app.library import Library
from app.models import FileItem, Project
from app.repository import Repository
from app.ui import batch_auto_cover
from selftests._common import T


FRAME_HELPER = "app.video_cover.save_first_video_frame"


def _project(repo: Repository, title: str) -> Project:
    """调用方必须提供显式临时数据库，禁止隐式加载应用配置。"""
    project = Project(title=title)
    project.id = repo.save_project(project)
    return project


def _file(
    repo: Repository, root: Path, project: Project, name: str,
    *, kind: str = "image", origin: str = "user", subfolder: str = "",
    missing: bool = False,
) -> int:
    path = root / f"source_{project.id}" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "image":
        image = QImage(8, 6, QImage.Format_RGB32)
        image.fill(0x336699)
        if not image.save(str(path), "PNG"):
            raise AssertionError(f"无法创建合成图片：{path}")
    else:
        path.write_bytes(b"synthetic-video" if kind == "video" else b"synthetic-document")
    fid = repo.add_file(FileItem(
        project_id=project.id, path=str(path), kind=kind, origin=origin,
        subfolder=subfolder,
    ))
    if missing:
        repo.set_file_missing(fid, True)
    return fid


def _frame(source: Path, target: Path, *, show_progress: bool = False) -> None:
    """只读本次合成媒体并写入真实 PNG，避免外部解码器和真实用户文件。"""
    if Path(source).read_bytes() != b"synthetic-video":
        raise AssertionError(f"抽帧未使用本次合成视频：{source}")
    if show_progress:
        raise AssertionError("批量抽帧不应为单个视频再次弹进度对话框")
    image = QImage(8, 6, QImage.Format_RGB32)
    image.fill(0x669933)
    if not image.save(str(target), "PNG"):
        raise AssertionError(f"无法创建合成封面：{target}")


def test_selection(t: T, root: Path) -> None:
    """历史项目手动执行不依赖导入主开关，图片/视频均遵循逻辑自然排序。"""
    root.mkdir()
    repo = Repository(connect(root / "selection-test.db"))
    library = Library(root / "library")
    try:
        repo.set_setting("import_auto_cover", "0")
        repo.set_setting("import_auto_cover_video", "0")
        project = _project(repo, "图片自然排序")
        _file(repo, root, project, "0-generated.png", origin="generated")
        _file(repo, root, project, "0-missing.png", missing=True)
        _file(repo, root, project, "page1.png", subfolder="chapter10")
        _file(repo, root, project, "page10.png", subfolder="chapter2")
        chosen = _file(repo, root, project, "page2.png", subfolder="chapter2")
        _file(repo, root, project, "0-video.mp4", kind="video")
        with patch(FRAME_HELPER, side_effect=_frame) as frame:
            changed, warnings = apply_project_auto_cover(
                repo, project, library, include_video=True, replace_existing=False,
            )
        t.assert_true("手动为历史项目设置封面不受导入关闭影响", changed)
        t.assert_eq("图片按逻辑目录和文件名自然排序", repo.get_project(project.id).cover_file_id, chosen)
        t.assert_eq("图片优先于视频且不抽帧", frame.call_count, 0)
        t.assert_eq("正常设置无警告", warnings, [])
        t.assert_eq("手动操作不修改导入主开关", repo.get_setting("import_auto_cover"), "0")

        original = _file(repo, root, project, "old-cover.png", origin="generated")
        project.cover_file_id = original
        repo.save_project(project)
        changed, _ = apply_project_auto_cover(
            repo, project, library, include_video=True, replace_existing=False,
        )
        t.assert_eq("默认保留已有封面", changed, False)
        t.assert_eq("保留封面 ID", repo.get_project(project.id).cover_file_id, original)
        changed, _ = apply_project_auto_cover(
            repo, project, library, include_video=True, replace_existing=True,
        )
        t.assert_true("明确替换已有封面", changed)
        t.assert_eq("替换重新选择首张用户图片", repo.get_project(project.id).cover_file_id, chosen)

        empty = _project(repo, "无可用候选")
        old = _file(repo, root, empty, "old-generated.png", origin="generated")
        _file(repo, root, empty, "missing.png", missing=True)
        _file(repo, root, empty, "readme.txt", kind="other")
        empty.cover_file_id = old
        repo.save_project(empty)
        changed, _ = apply_project_auto_cover(
            repo, empty, library, include_video=True, replace_existing=True,
        )
        t.assert_eq("无候选替换不清空封面", changed, False)
        t.assert_eq("无候选保留数据库封面", repo.get_project(empty.id).cover_file_id, old)
        t.assert_true("无候选保留原封面实体", Path(repo.get_file(old).path).is_file())

        video = _project(repo, "视频自然排序")
        _file(repo, root, video, "0-generated.mp4", kind="video", origin="generated")
        _file(repo, root, video, "0-missing.mp4", kind="video", missing=True)
        _file(repo, root, video, "clip1.mp4", kind="video", subfolder="chapter10")
        _file(repo, root, video, "clip10.mp4", kind="video", subfolder="chapter2")
        selected_video = _file(repo, root, video, "clip2.mp4", kind="video", subfolder="chapter2")
        with patch(FRAME_HELPER, side_effect=_frame) as frame:
            changed, _ = apply_project_auto_cover(
                repo, video, library, include_video=False, replace_existing=False,
            )
            t.assert_eq("不勾选视频时无图片项目跳过", changed, False)
            t.assert_eq("不勾选视频不抽帧", frame.call_count, 0)
            changed, warnings = apply_project_auto_cover(
                repo, video, library, include_video=True, replace_existing=False,
            )
        t.assert_true("本次勾选视频即可生成首帧", changed)
        t.assert_eq("视频抽帧一次", frame.call_count, 1)
        t.assert_eq("批量抽帧禁用单视频进度框", frame.call_args.kwargs.get("show_progress"), False)
        t.assert_eq("视频按逻辑目录及文件名自然排序", Path(frame.call_args.args[0]), Path(repo.get_file(selected_video).path))
        cover = repo.get_file(repo.get_project(video.id).cover_file_id)
        t.assert_eq("首帧登记为衍生图片", (cover.kind, cover.origin, cover.is_relative), ("image", "generated", True))
        output = library.resolve(cover.path, cover.is_relative)
        t.assert_true("首帧保存在显式临时仓库", output.resolve().is_relative_to(library.root.resolve()))
        t.assert_true("首帧为真实可读图片", not QImage(str(output)).isNull())
        t.assert_eq("视频正常生成无警告", warnings, [])
        t.assert_eq("本次视频选择不修改库偏好", repo.get_setting("import_auto_cover_video"), "0")
    finally:
        repo.conn.close()


def test_video_failure(t: T, root: Path) -> None:
    """真实落盘失败和 SQL 引用保存失败保留原封面，并清理本次生成物。"""
    root.mkdir()
    repo = Repository(connect(root / "failure-test.db"))
    library = Library(root / "library")
    try:
        project = _project(repo, "失败保留旧封面")
        old = _file(repo, root, project, "old.png", origin="generated")
        source = _file(repo, root, project, "video.mp4", kind="video")
        project.cover_file_id = old
        repo.save_project(project)
        before_files = {file.id for file in repo.list_files(project.id)}

        def fail_frame(_source: Path, target: Path, *, show_progress: bool) -> None:
            Path(target).write_bytes(b"partial-frame")
            raise RuntimeError("模拟视频解码失败")

        for label, capture in (("抽帧失败", fail_frame), ("封面引用保存失败", _frame)):
            if label == "封面引用保存失败":
                repo.conn.execute("""CREATE TRIGGER reject_test_cover BEFORE UPDATE OF cover_file_id
                    ON projects BEGIN SELECT RAISE(ABORT, '模拟封面引用保存失败'); END""")
                repo.conn.commit()
            with patch(FRAME_HELPER, side_effect=capture):
                try:
                    apply_project_auto_cover(
                        repo, project, library, include_video=True, replace_existing=True,
                    )
                except CoverFileError as exc:
                    details = "\n".join(exc.details)
                    expected_source = (repo.get_file(source).path if label == "抽帧失败" else str(library.root))
                    t.assert_in(f"{label}报告完整源路径", expected_source, details)
                    t.assert_in(f"{label}报告目标路径", "目标路径", details)
                    t.assert_in(f"{label}报告失败阶段", "失败阶段", details)
                    t.assert_in(f"{label}报告回滚结果", "回滚", details)
                else:
                    t.assert_true(f"{label}必须向调用方报告", False)
            t.assert_eq(f"{label}保留数据库旧封面", repo.get_project(project.id).cover_file_id, old)
            t.assert_eq(f"{label}恢复内存封面", project.cover_file_id, old)
            t.assert_eq(f"{label}不留下衍生文件记录", {file.id for file in repo.list_files(project.id)}, before_files)
            t.assert_eq(f"{label}不留下新封面实体", list(library.project_dir(project.id).glob("__cover_*.png")), [])
            t.assert_true(f"{label}原视频仍存在", Path(repo.get_file(source).path).is_file())
            t.assert_true(f"{label}原封面仍存在", Path(repo.get_file(old).path).is_file())
    finally:
        repo.conn.close()


def test_dialog_and_batch(t: T, root: Path) -> None:
    """真实选项默认值/取消，混合失败批次仍继续并且只弹一次结果。"""
    root.mkdir()
    repo = Repository(connect(root / "batch-test.db"))
    library = Library(root / "library")
    try:
        for include_video in (False, True):
            dialog = batch_auto_cover.BatchCoverDialog(None, 3, include_video)
            try:
                t.assert_eq(f"视频选项默认来自当前库 {include_video}", dialog.include_video.isChecked(), include_video)
                t.assert_eq(f"替换选项默认关闭 {include_video}", dialog.replace_existing.isChecked(), False)
            finally:
                dialog.close()
                dialog.deleteLater()

        first = _project(repo, "首个成功")
        first_image = _file(repo, root, first, "first.png")
        failed = _project(repo, "模拟失败项目")
        _file(repo, root, failed, "failed.png")
        last = _project(repo, "失败之后继续")
        last_image = _file(repo, root, last, "last.png")
        untouched = _project(repo, "未选项目")
        _file(repo, root, untouched, "untouched.png")
        targets = [first.id, failed.id, last.id]
        repo.set_setting("import_auto_cover_video", "1")

        def reject(dialog) -> int:
            t.assert_true("运行器读取当前库视频默认值", dialog.include_video.isChecked())
            t.assert_eq("运行器默认保留已有封面", dialog.replace_existing.isChecked(), False)
            return QDialog.Rejected

        with patch.object(batch_auto_cover.BatchCoverDialog, "exec", new=reject), patch.object(
            batch_auto_cover, "apply_project_auto_cover",
        ) as apply, patch.object(batch_auto_cover, "info") as info, patch.object(batch_auto_cover, "warn") as warn:
            changed = batch_auto_cover.run_batch_auto_cover(None, repo, library, targets)
        t.assert_eq("取消对话框无需刷新", changed, False)
        t.assert_eq("取消不处理任何项目", apply.call_count, 0)
        t.assert_eq("取消不弹结果汇总", info.call_count + warn.call_count, 0)
        t.assert_true("取消全部项目封面保持空值", all(repo.get_project(pid).cover_file_id is None for pid in targets))

        def accept(dialog) -> int:
            dialog.include_video.setChecked(False)
            dialog.replace_existing.setChecked(True)
            return QDialog.Accepted

        attempted: list[int] = []

        def apply(current_repo, project, current_library, *, include_video, replace_existing):
            attempted.append(project.id)
            t.assert_eq(f"项目{project.id}接收本次视频选项", include_video, False)
            t.assert_eq(f"项目{project.id}接收本次替换选项", replace_existing, True)
            if project.id == failed.id:
                raise OSError("模拟封面写入失败")
            return apply_project_auto_cover(
                current_repo, project, current_library,
                include_video=include_video, replace_existing=replace_existing,
            )

        with patch.object(batch_auto_cover.BatchCoverDialog, "exec", new=accept), patch.object(
            batch_auto_cover, "apply_project_auto_cover", side_effect=apply,
        ), patch.object(batch_auto_cover, "info") as info, patch.object(batch_auto_cover, "warn") as warn:
            changed = batch_auto_cover.run_batch_auto_cover(None, repo, library, targets)
        t.assert_true("批量部分成功返回需刷新", changed)
        t.assert_eq("失败后继续处理其余所选项目", attempted, targets)
        t.assert_eq("批量首项设置封面", repo.get_project(first.id).cover_file_id, first_image)
        t.assert_eq("批量末项设置封面", repo.get_project(last.id).cover_file_id, last_image)
        t.assert_eq("失败项目不留下封面", repo.get_project(failed.id).cover_file_id, None)
        t.assert_eq("未选项目保持不变", repo.get_project(untouched.id).cover_file_id, None)
        t.assert_eq("批量只弹一次汇总", info.call_count + warn.call_count, 1)
        summary = "\n".join(str(call) for mock in (info, warn) for call in mock.call_args_list)
        t.assert_in("失败汇总包含项目名", failed.title, summary)
        t.assert_in("失败汇总包含异常类型", "OSError", summary)
        t.assert_in("失败汇总包含完整原因", "模拟封面写入失败", summary)
        t.assert_eq("批次选项不修改库视频偏好", repo.get_setting("import_auto_cover_video"), "1")
    finally:
        repo.conn.close()


def main() -> bool:
    app = QApplication.instance() or QApplication([])
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-batch-auto-cover-") as temporary:
        root = Path(temporary)

        def fake_trash(path, *, allow_delete: bool = True) -> str:
            target = Path(path).resolve()
            if not target.is_relative_to(root.resolve()) or allow_delete:
                raise AssertionError("测试禁止访问真实回收站或删除临时目录之外的文件")
            target.unlink()
            return "trash"

        with patch("app.cover_files.move_to_trash", side_effect=fake_trash):
            test_selection(t, root / "选择")
            test_video_failure(t, root / "失败")
            test_dialog_and_batch(t, root / "批次")
            app.processEvents()
    return t.report()


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
