"""文件表的压缩包解压编排：后台解包，主线程原子登记文件。"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from PySide6.QtWidgets import QInputDialog, QLineEdit

from ..archives import ArchiveCancelled, ArchivePasswordRequired, archive_folder_name, extract_archive
from ..models import FileItem
from ..utils import OperationCancelled, detect_kind
from .dialogs import confirm, warn
from .file_sorting import natural_name_key
from .archive_progress import run_archive_with_progress as run_with_progress

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .main_window import MainWindow


def extract_current_archive(window: MainWindow) -> None:
    """解压右键当前压缩包到当前项目，不受多选中的其他文件影响。"""
    fid = window._current_file_row_id()
    file = window.repo.get_file(fid) if fid is not None else None
    if file is None or detect_kind(file.path) != "archive":
        return
    source = window.library.resolve(file.path, file.is_relative)
    if not source.is_file():
        warn(window, "无法解压", "压缩包不存在，请先修复文件路径。")
        return
    project_id = file.project_id
    library = window.library
    repo = window.repo
    existing = repo.list_files(project_id)
    folders = window._load_explicit_subfolders(project_id)
    occupied = set(folders)
    for folder in folders:
        parts = folder.split("/")
        occupied.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    for entry in existing:
        parts = entry.subfolder.split("/") if entry.subfolder else []
        occupied.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        occupied.add("/".join(filter(None, (entry.subfolder, Path(entry.path).name))))
    parent = file.subfolder.strip("/")
    base = archive_folder_name(source)
    logical = "/".join(filter(None, (parent, base)))
    number = 2
    occupied_keys = {name.casefold() for name in occupied}
    while logical.casefold() in occupied_keys:
        logical = "/".join(filter(None, (parent, f"{base}_{number}")))
        number += 1

    if not confirm(
        window, "解压到当前项目",
        f"将「{source.name}」解压并以仓储方式加入：\n{logical}\n\n"
        "保留压缩包和包内目录结构；不会执行仓储导入后的原文件回收。",
        yes="解压并加入项目",
    ):
        return
    secret: str | None = None
    project_dir = library.project_dir(project_id).resolve()
    destination = project_dir / f"__extract_{uuid4().hex}"

    def discard_output() -> None:
        # 只清理本次生成的解压目录；绝不移除用户原压缩包或其他仓储文件。
        resolved = destination.resolve()
        if resolved.parent != project_dir or not resolved.name.startswith("__extract_"):
            raise RuntimeError("解压清理路径超出本次目标目录。")
        if destination.exists():
            shutil.rmtree(destination)

    def work(progress_cb, is_cancelled):
        extracted = False
        try:
            paths = extract_archive(
                source, destination, password=secret,
                progress=progress_cb, is_cancelled=is_cancelled,
                detail_progress=getattr(progress_cb, "detail", None),
            )
            extracted = True
            directories = [p.relative_to(destination).as_posix()
                           for p in destination.rglob("*") if p.is_dir()]
            if is_cancelled():
                discard_output()
                raise OperationCancelled()
            return {"paths": paths, "directories": directories}
        except ArchivePasswordRequired:
            return {"password_required": True}
        except ArchiveCancelled as exc:
            raise OperationCancelled() from exc
        except Exception:
            if extracted:
                discard_output()
            raise

    def done(payload) -> None:
        nonlocal secret
        if payload.get("password_required"):
            prompt = "压缩包需要密码。" if secret is None else "密码不正确或无法解密，请重新输入。"
            entered, accepted = QInputDialog.getText(
                window, "压缩包密码", f"{source.name}\n{prompt}", QLineEdit.Password,
            )
            if not accepted:
                secret = None
                window.statusBar().showMessage("已取消解压，原压缩包保留。", 5000)
                return
            secret = entered
            start()
            return
        paths, directories = payload["paths"], payload["directories"]
        # 元数据保存失败时整批回滚，不把半套解压结果显示为成功。
        try:
            records = [FileItem(
                project_id=project_id,
                path=(destination / path).relative_to(library.root.resolve()).as_posix(),
                is_relative=True, kind=detect_kind(path), origin="user",
                ord=len(existing) + index,
                subfolder=logical + ("/" + path.parent.as_posix() if path.parent != Path(".") else ""),
            ) for index, path in enumerate(sorted(paths, key=lambda p: natural_name_key(p.as_posix())))]
            current_folders = window._load_explicit_subfolders(project_id)
            current_folders.add(logical)
            current_folders.update(f"{logical}/{path}" for path in directories)
            description_line = None
            if secret is not None and repo.get_setting("archive_remember_password", "0") == "1":
                # JSON 引号与转义保持一行，并使含换行/引号的密码仍能准确辨认。
                description_line = (
                    f"压缩包 {json.dumps(source.name, ensure_ascii=False)} "
                    f"解压密码：{json.dumps(secret, ensure_ascii=False)}"
                )
            repo.add_files_atomic(project_id, records, {
                window.EXPLICIT_SUBFOLDERS_SETTING_KEY: json.dumps(sorted(current_folders), ensure_ascii=False),
            }, description_append=description_line)
        except Exception as exc:
            # 描述追加值可能含密码，不记录数据库异常详情，避免敏感值进入日志。
            logger.error("解压文件登记失败：%s", type(exc).__name__)
            secret = None
            cleanup_note = ""
            try:
                discard_output()
            except Exception as cleanup_exc:
                logger.exception("清理解压输出失败")
                cleanup_note = f"\n未能清理本次解压目录：{destination}\n{cleanup_exc}"
            warn(window, "解压入库失败", f"文件记录未保存，原压缩包保留。\n错误类型：{type(exc).__name__}{cleanup_note}")
            return
        secret = None
        project = repo.get_project(project_id)
        if project is not None and window._current_project_id == project_id:
            window._refresh_cover_without_preview_reset(project)
        window.statusBar().showMessage(f"已解压 {len(records)} 个文件到「{logical}」，原压缩包保留。", 7000)

    def failed(message: str) -> None:
        nonlocal secret
        if secret:
            message = message.replace(secret, "[密码已隐藏]")
        secret = None
        warn(window, "解压失败", f"原压缩包保留。\n{message}")

    def cancelled() -> None:
        nonlocal secret
        secret = None
        window.statusBar().showMessage("解压已取消，原压缩包保留。", 5000)

    def start() -> None:
        run_with_progress(
            window, "解压压缩包", f"正在解压「{source.name}」…", work,
            on_done=done, on_cancel=cancelled, on_error=failed,
        )

    start()
