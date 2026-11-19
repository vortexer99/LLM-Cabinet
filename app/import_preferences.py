"""库级导入封面策略；仅需视频首帧时按需加载 Qt 解码器。"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from uuid import uuid4

from .library import Library
from .models import FileItem, Project
from .repository import Repository


IMPORT_AUTO_COVER_KEY = "import_auto_cover"
IMPORT_VIDEO_COVER_KEY = "import_auto_cover_video"


def _cover_order(file: FileItem) -> tuple:
    """按逻辑相对目录与文件名自然排序，数字段按数值比较。"""
    relative_name = "/".join(
        part for part in (file.subfolder.replace("\\", "/"), Path(file.path).name)
        if part
    )
    parts = tuple(
        (1, int(part)) if part.isdecimal() else (0, part.casefold())
        for part in re.split(r"(\d+)", relative_name)
    )
    return parts, relative_name.casefold(), relative_name, file.id or 0


def _select_auto_cover(
    repo: Repository, project_id: int, *, include_video: bool,
) -> FileItem | None:
    """导入和手动共用过滤、图片优先级及逻辑路径自然排序规则。"""
    files = [
        file for file in repo.list_files(project_id)
        if file.id is not None and file.origin == "user" and not file.missing
    ]
    images = [file for file in files if file.kind == "image"]
    if images:
        return min(images, key=_cover_order)
    videos = [file for file in files if file.kind == "video"] if include_video else []
    return min(videos, key=_cover_order) if videos else None


def apply_project_auto_cover(
    repo: Repository, project: Project, library: Library, *,
    include_video: bool, replace_existing: bool,
) -> tuple[bool, list[str]]:
    """按导入规则为历史项目设置封面，不检查导入自动封面总开关。

    返回是否成功修改和清理告警；失败抛出含完整文件操作详情的 CoverFileError。
    视频使用独立播放器且不弹单视频进度框，由调用方在项目之间处理批量取消。
    """
    from .cover_files import (
        CoverFileError, _detail, discard_cover_snapshot,
        replace_project_cover, save_generated_cover,
    )

    if project.id is None:
        return False, []
    current = project
    candidate: FileItem | None = None
    generated: FileItem | None = None
    stage = "读取当前项目和自动封面候选"
    try:
        # 不允许本次提交或回滚调用方已有的其他操作。
        if repo.conn.in_transaction:
            raise CoverFileError([_detail(
                FileItem(project_id=project.id, path="（自动封面候选尚未读取）"),
                library, stage, RuntimeError("已有未提交事务，不能提交或回滚其他操作。"),
                "未修改封面，保留全部文件及记录。",
            )], rollback_failed=True)
        current = repo.get_project(project.id)
        if current is None:
            raise ValueError(f"项目不存在：{project.id}")
        project.cover_file_id = current.cover_file_id
        if current.cover_file_id is not None and not replace_existing:
            return False, []
        candidate = _select_auto_cover(repo, project.id, include_video=include_video)
        if candidate is None:
            return False, []
        if candidate.kind == "video":
            from .video_cover import save_first_video_frame

            source = library.resolve(candidate.path, candidate.is_relative)

            def write_frame(target: Path) -> bool:
                save_first_video_frame(source, target, show_progress=False)
                return True

            stage = "生成并登记视频首帧封面"
            generated = save_generated_cover(
                repo, library, current, write_frame, label="封面（视频首帧）",
                source_description=f"{source}（源视频记录 ID：{candidate.id}）",
            )
        stage = "重新检查项目封面"
        # 抽帧会分发 Qt 事件，期间外部操作可能修改封面或元数据。
        current = repo.get_project(project.id)
        if current is None:
            raise ValueError(f"项目在设置封面前已删除：{project.id}")
        project.cover_file_id = current.cover_file_id
        cover_id = generated.id if generated is not None else candidate.id
        if (current.cover_file_id is not None and not replace_existing) or current.cover_file_id == cover_id:
            warnings = discard_cover_snapshot(repo, library, generated) if generated else []
            return False, warnings
        stage = "保存自动封面引用"
        warnings = replace_project_cover(repo, library, current, int(cover_id), cover_only=True)
        project.cover_file_id = current.cover_file_id
        return True, warnings
    except Exception as exc:
        logging.exception("项目 %s 自动设置封面失败", project.id)
        details = list(exc.details) if isinstance(exc, CoverFileError) else [_detail(
            candidate or FileItem(project_id=project.id, path="（自动封面候选尚未读取）"),
            library, stage, exc, "未执行旧封面清理。",
        )]
        rollback_failed = isinstance(exc, CoverFileError) and exc.rollback_failed
        if generated is not None and not rollback_failed:
            # 先校验引用；即便保存已提交后才抛错，也不会误删实际生效的封面。
            cleanup = discard_cover_snapshot(repo, library, generated)
            details.extend(cleanup or ["回滚结果：本次生成的文件已回收，文件记录已移除。"])
        if current is not None:
            project.cover_file_id = current.cover_file_id
        raise CoverFileError(details, rollback_failed=rollback_failed) from exc


def apply_import_auto_cover(
    repo: Repository, project: Project, library: Library | None = None,
) -> bool:
    """仅为新导入且无封面的项目选图片，或按子选项生成视频首帧封面。

    候选来自已成功登记的 files 行；排除衍生物和标记缺失的文件。
    仅导入调用方调用本函数，修改偏好本身不扫描、修改历史项目。
    保存失败向调用方抛出，由其纳入导入结果汇总。
    """
    if project.id is None or project.cover_file_id is not None:
        return False
    if repo.get_setting(IMPORT_AUTO_COVER_KEY, "0") != "1":
        return False
    candidate = _select_auto_cover(
        repo, project.id,
        include_video=repo.get_setting(IMPORT_VIDEO_COVER_KEY, "0") == "1",
    )
    if candidate is None:
        return False
    generated_path: Path | None = None
    generated_id: int | None = None
    if candidate.kind == "image":
        project.cover_file_id = candidate.id
    else:
        if library is None:
            raise RuntimeError("无法生成视频封面：未提供当前仓库路径。")
        from .video_cover import save_first_video_frame

        video = candidate
        generated_path = library.project_dir(project.id) / f"__cover_video_{uuid4().hex}.png"
        try:
            save_first_video_frame(library.resolve(video.path, video.is_relative), generated_path)
            generated_id = repo.add_file(FileItem(
                project_id=project.id,
                path=generated_path.relative_to(library.root).as_posix(),
                is_relative=True, kind="image", origin="generated",
                label="封面（视频首帧）",
            ))
            project.cover_file_id = generated_id
        except Exception:
            # 这里只清理本次新生成的截图，绝不删除原视频。
            try:
                generated_path.unlink(missing_ok=True)
            except OSError:
                logging.exception("清理未完成的视频封面失败：%s", generated_path)
            raise
    try:
        repo.save_project(project)
    except Exception:
        project.cover_file_id = None
        if generated_id is not None:
            try:
                repo.delete_file(generated_id)
                generated_path.unlink(missing_ok=True)
            except Exception:
                logging.exception("清理未保存的视频封面失败：%s", generated_path)
        raise
    return True
