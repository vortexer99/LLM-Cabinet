"""手动封面的落盘、引用替换和旧生成图片的保守回收。"""
from __future__ import annotations

import logging
import os
from pathlib import Path, PureWindowsPath
import stat
from typing import Callable
from uuid import uuid4

from .library import Library
from .models import FileItem, Project
from .repository import Repository
from .utils import move_to_trash


COVER_REPLACE_GENERATED_KEY = "cover_replace_generated"
_log = logging.getLogger(__name__)


class CoverFileError(RuntimeError):
    """保留完整阶段和回滚状态，供 UI 一次展示。"""

    def __init__(self, details: list[str], *, rollback_failed: bool = False):
        self.details = details
        self.rollback_failed = rollback_failed
        super().__init__("\n\n".join(details))


def _detail(file: FileItem, library: Library, stage: str, exc: Exception,
            state: str = "", source: str = "", target: str = "") -> str:
    """每条问题包含记录、源目标、阶段、异常和实际状态。"""
    path = library.resolve(file.path, file.is_relative)
    return (
        f"文件：{Path(file.path).name}；记录 ID：{file.id if file.id is not None else '尚未登记'}\n"
        f"源路径：{source or str(path)}\n目标路径：{target or str(path)}\n"
        f"失败阶段：{stage}\n原因：{type(exc).__name__}: {exc}"
        + (f"\n处理结果：{state}" if state else "")
    )


def _rollback(repo: Repository, file: FileItem, library: Library, details: list[str]) -> bool:
    try:
        repo.conn.rollback()
        return True
    except Exception as exc:
        _log.exception("封面数据库事务回滚失败")
        details.append(_detail(file, library, "回滚数据库事务", exc,
                               "数据库状态无法确认；保留新旧文件及记录，不再执行清理。"))
        return False


def _safe_generated_path(library: Library, file: FileItem, *, missing_ok: bool = False) -> Path:
    """只接纳本项目目录内、没有符号链接/重解析点/硬链接的相对文件。"""
    relative = Path(file.path)
    windows = PureWindowsPath(file.path)
    if not file.is_relative or relative.is_absolute() or windows.drive or windows.root:
        raise ValueError("生成图片不是仓库相对路径，已保留。")
    if ".." in relative.parts or ".." in windows.parts:
        raise ValueError("路径包含父目录跳转，已保留。")
    root = Path(os.path.abspath(library.root))
    target = root / relative
    project_dir = root / f"project_{file.project_id}"
    if not target.is_relative_to(project_dir) or target == project_dir:
        raise ValueError("生成图片不在所属项目仓库目录内，已保留。")
    # 包括仓库根及其父目录，避免仓库自身或祖先目录是 junction。
    for component in reversed((target, *target.parents)):
        try:
            entry = component.lstat()
        except FileNotFoundError:
            if missing_ok and component == target:
                continue
            raise
        if stat.S_ISLNK(entry.st_mode) or getattr(entry, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"路径含符号链接或重解析点，已保留：{component}")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("生成图片解析到仓库外，已保留。")
    if target.exists():
        entry = target.stat()
        if not stat.S_ISREG(entry.st_mode) or entry.st_nlink > 1:
            raise ValueError("生成图片不是普通文件或存在硬链接，已保留。")
    return target


def _check_unshared(repo: Repository, library: Library, file: FileItem, target: Path) -> None:
    if repo.conn.in_transaction:
        raise RuntimeError("数据库存在未提交事务，无法确认引用；已保留文件及记录。")
    if file.id is not None and repo.conn.execute(
        "SELECT id FROM projects WHERE cover_file_id=?", (file.id,),
    ).fetchone():
        raise ValueError("文件仍被项目用作封面，已保留。")
    canonical = os.path.normcase(str(target.resolve()))
    for row in repo.conn.execute("SELECT id,path,is_relative FROM files"):
        if row["id"] == file.id:
            continue
        other = library.resolve(row["path"], bool(row["is_relative"]))
        if os.path.normcase(str(other.resolve())) == canonical:
            raise ValueError(f"文件路径被其他记录共享（记录 ID：{row['id']}），已保留。")


def discard_cover_snapshot(repo: Repository, library: Library, file: FileItem) -> list[str]:
    """回收不再被引用的生成图片；任何不确定性均保留并报告。"""
    details: list[str] = []
    stage = "检查生成封面安全路径与引用"
    recycled = False
    try:
        if file.origin != "generated" or file.kind != "image":
            raise ValueError("仅允许清理软件生成的图片，已保留。")
        target = _safe_generated_path(library, file)
        _check_unshared(repo, library, file, target)
        stage = "移动生成封面到系统回收站"
        move_to_trash(target, allow_delete=False)
        recycled = True
        if file.id is not None:
            stage = "删除已回收封面的数据库记录"
            repo.delete_file(file.id)
    except Exception as exc:
        _log.warning("生成封面清理未完成：%s", file.path, exc_info=True)
        state = ("文件已移入系统回收站；数据库记录清理未完成。"
                 if recycled else f"文件及记录已保留；实际位置：{library.resolve(file.path, file.is_relative)}")
        details.append(_detail(file, library, stage, exc, state,
                               target="系统回收站（实际路径由系统管理）"))
        if recycled:
            _rollback(repo, file, library, details)
    return details


def replace_project_cover(repo: Repository, library: Library, project: Project,
                          cover_id: int, *, cover_only: bool = False) -> list[str]:
    """先确认新引用保存成功，再按偏好仅回收前一个生成封面。"""
    old_id = project.cover_file_id
    new_file = None
    try:
        new_file = repo.get_file(cover_id)
        if new_file is None or new_file.project_id != project.id:
            raise ValueError("新封面文件不存在或不属于当前项目。")
    except Exception as exc:
        _log.exception("检查新封面文件记录失败")
        candidate = new_file or FileItem(id=cover_id, project_id=int(project.id or 0),
                                         path="（新封面路径未能读取）")
        raise CoverFileError([_detail(candidate, library, "读取并校验新封面文件记录", exc,
                                     "未修改封面引用，旧封面未清理。")]) from exc
    if repo.conn.in_transaction:
        raise CoverFileError([_detail(new_file, library, "保存项目封面引用",
                                     RuntimeError("已有未提交事务，不能提交或回滚其他操作。"),
                                     "未修改封面引用，保留新旧文件及记录。")], rollback_failed=True)
    details: list[str] = []
    project.cover_file_id = cover_id
    try:
        if cover_only:
            # 自动批量操作只修改封面，不能用抽帧前的对象覆盖外部修改的元数据。
            cursor = repo.conn.execute(
                """UPDATE projects SET cover_file_id=?, updated_at=datetime('now')
                   WHERE id=? AND cover_file_id IS ?""",
                (cover_id, project.id, old_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("项目已删除或封面被其他操作修改，请刷新后重试。")
            repo.conn.commit()
        else:
            repo.save_project(project)
    except Exception as exc:
        _log.exception("保存项目封面引用失败")
        details.append(_detail(new_file, library, "保存项目封面引用", exc,
                               "未执行旧封面清理；新封面文件暂时保留。"))
        rolled_back = _rollback(repo, new_file, library, details)
        project.cover_file_id = old_id
        if rolled_back:
            try:
                actual = repo.get_project(int(project.id))
                if actual is not None:
                    project.cover_file_id = actual.cover_file_id
                    details.append(f"回滚后项目 {project.id} 实际封面记录 ID：{actual.cover_file_id}")
            except Exception as read_exc:
                _log.exception("确认项目封面回滚状态失败")
                details.append(_detail(new_file, library, "确认回滚后封面引用", read_exc,
                                       "数据库状态无法确认；保留文件及记录。"))
                rolled_back = False
        raise CoverFileError(details, rollback_failed=not rolled_back) from exc
    if old_id is None or old_id == cover_id:
        return []
    previous = None
    try:
        if repo.get_setting(COVER_REPLACE_GENERATED_KEY, "0") != "1":
            return []
        previous = repo.get_file(old_id)
        if previous is None or previous.origin != "generated":
            return []
        if previous.project_id != project.id:
            raise ValueError("旧生成封面不属于当前项目，已保留。")
        return discard_cover_snapshot(repo, library, previous)
    except Exception as exc:
        _log.warning("新封面已保存，检查旧封面清理条件失败", exc_info=True)
        details.append(_detail(previous or FileItem(id=old_id, project_id=int(project.id),
                                                   path="（旧封面路径未能读取）"), library,
                               "检查旧封面清理条件", exc, "新封面已保存；旧封面未清理。"))
        return details


def save_generated_cover(
    repo: Repository, library: Library, project: Project,
    writer: Callable[[Path], bool], *, label: str, source_description: str,
) -> FileItem:
    """调用写入器生成并登记封面，统一保证路径安全及失败补偿。"""
    if project.id is None:
        raise ValueError("项目尚未保存。")
    file = FileItem(project_id=int(project.id),
                    path=f"project_{project.id}/__cover_{uuid4().hex}.png",
                    is_relative=True, kind="image", origin="generated",
                    label=label)
    if repo.conn.in_transaction:
        raise CoverFileError([_detail(file, library, "创建新封面",
                                     RuntimeError("已有未提交事务，不能提交或回滚其他操作。"),
                                     "未创建文件或记录，旧封面保持原状。")], rollback_failed=True)
    stage = "创建封面目录"
    details: list[str] = []
    attempted_save = False
    try:
        library.project_dir(int(project.id))
        stage = "检查新封面安全路径"
        target = _safe_generated_path(library, file, missing_ok=True)
        stage = "保存新封面 PNG"
        attempted_save = True
        if not writer(target):
            raise OSError("封面图片写入器返回失败。")
        stage = "登记新封面文件记录"
        file.id = repo.add_file(file)
        return file
    except Exception as exc:
        _log.exception("新封面快照保存失败")
        details.append(_detail(file, library, stage, exc,
                               "未修改旧封面引用；将尝试回滚本次新生成物。",
                               source_description))
        rolled_back = _rollback(repo, file, library, details)
        if attempted_save and rolled_back:
            try:
                # 兼容数据库提交后才抛错的异常，避免删除文件后遗留未识别记录。
                row = repo.conn.execute("SELECT id FROM files WHERE path=? AND project_id=?",
                                        (file.path, file.project_id)).fetchone()
                file.id = int(row["id"]) if row else None
                if library.resolve(file.path, True).exists():
                    warnings = discard_cover_snapshot(repo, library, file)
                    details.extend(warnings or ["回滚结果：本次生成的文件已回收，文件记录已移除。"])
                else:
                    details.append("回滚结果：没有留下本次封面文件。")
            except Exception as cleanup_exc:
                _log.exception("新封面失败后的清理状态确认失败")
                details.append(_detail(file, library, "清理本次新生成物", cleanup_exc,
                                       f"实际保留位置：{library.resolve(file.path, True)}"))
        raise CoverFileError(details, rollback_failed=not rolled_back) from exc


def save_cover_snapshot(repo: Repository, library: Library, project: Project,
                        pix, source_kind: str) -> int:
    """保留手动预览截图接口，复用统一的生成物登记与失败补偿。"""
    labels = {"clipboard": "封面（来自剪切板）", "pdf": "封面（截取自 PDF）",
              "video": "封面（截取自视频）"}
    file = save_generated_cover(
        repo, library, project, lambda target: pix.save(str(target), "PNG"),
        label=labels.get(source_kind, f"封面（{source_kind}）"),
        source_description=f"当前预览画面（{source_kind}）",
    )
    return int(file.id)
