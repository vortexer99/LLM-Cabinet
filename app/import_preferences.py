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
    files = [
        file for file in repo.list_files(project.id)
        if file.id is not None
        and file.origin == "user" and not file.missing
    ]
    candidates = [file for file in files if file.kind == "image"]
    generated_path: Path | None = None
    generated_id: int | None = None
    if candidates:
        project.cover_file_id = min(candidates, key=_cover_order).id
    else:
        if repo.get_setting(IMPORT_VIDEO_COVER_KEY, "0") != "1":
            return False
        videos = [file for file in files if file.kind == "video"]
        if not videos:
            return False
        if library is None:
            raise RuntimeError("无法生成视频封面：未提供当前仓库路径。")
        from .video_cover import save_first_video_frame

        video = min(videos, key=_cover_order)
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
