"""链接转仓储的移动策略；数据库快照/写回留在主线程，文件操作可放后台。"""
from __future__ import annotations

import errno
import logging
import os
import shutil
import stat
from dataclasses import astuple, dataclass, replace
from pathlib import Path
from typing import Callable

from .library import Library
from .models import FileItem
from .repository import Repository
from .utils import move_to_trash

STORAGE_CONVERT_MOVE_KEY = "storage_convert_move"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MoveFile:
    """不可变记录快照；完整字段用于提交前识别并发修改。"""

    file_id: int
    project_id: int
    path: str
    label: str
    record: tuple


@dataclass(frozen=True)
class MoveInputs:
    root: Path
    files: tuple[MoveFile, ...]
    references: tuple[tuple[int, str, bool], ...]


@dataclass(frozen=True)
class TransferResult:
    file: MoveFile
    source: Path
    target: Path
    relative_path: str
    moved: bool
    reason: str
    target_stat: tuple[int, int, int, int]


def prepare_move_inputs(repo: Repository, library: Library, files: list[FileItem]) -> MoveInputs:
    """主线程读取本库引用；不打开默认数据库或复制用户数据。"""
    if repo.conn.in_transaction:
        raise RuntimeError("数据库仍有未提交事务，请完成当前操作后重试。")
    unique = {int(file.id): file for file in files if file.id is not None and not file.is_relative}
    selected = tuple(MoveFile(fid, file.project_id, file.path, file.label, astuple(file))
                     for fid, file in unique.items())
    references = tuple((int(row["id"]), row["path"], bool(row["is_relative"]))
                       for row in repo.conn.execute("SELECT id,path,is_relative FROM files").fetchall())
    return MoveInputs(Path(library.root).absolute(), selected, references)


def _fingerprint(path: Path) -> tuple[int, int, int, int]:
    current = path.stat()
    return current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns


def _has_redirect(path: Path) -> bool:
    """识别源文件及其父目录中的符号链接、junction 和其它重解析点。"""
    for part in (path, *path.parents):
        current = part.lstat()
        if stat.S_ISLNK(current.st_mode) or getattr(current, "st_file_attributes", 0) & 0x400:
            return True
    return False


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _detail(file: MoveFile, source: Path | str, target: Path | str,
            stage: str, error: BaseException | str) -> str:
    problem = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else error
    return (f"记录 ID：{file.file_id}；文件：{file.label or Path(file.path).name}\n"
            f"源路径：{source}\n目标路径：{target}\n阶段：{stage}\n{problem}")


def _discard_created(path: Path) -> None:
    """仅清理由本次排他创建的副本，保留系统回收站恢复能力。"""
    if os.path.lexists(path):
        move_to_trash(path, allow_delete=False)


def _copy_exclusive(source: Path, target: Path) -> None:
    """排他创建目标文件，绝不覆盖同名文件或悬空链接。"""
    created = False
    try:
        with target.open("xb") as destination:
            created = True
            with source.open("rb") as original:
                shutil.copyfileobj(original, destination, 1024 * 1024)
        shutil.copystat(source, target)
    except Exception as exc:
        if created:
            try:
                _discard_created(target)
            except Exception as cleanup_error:
                logger.exception("清理转换失败的仓储副本失败：%s", target)
                raise OSError(f"{type(exc).__name__}: {exc}；副本清理失败，保留路径 {target}："
                              f"{type(cleanup_error).__name__}: {cleanup_error}") from exc
        raise


def move_no_replace(source: Path, target: Path) -> None:
    """同盘直接移动，跨盘排他复制后移走源文件；两种情况都不覆盖目标。

    Windows 的 rename 天然拒绝覆盖；POSIX 用 link/unlink 实现无覆盖移动。
    跨盘使用排他创建的文件，避免 shutil.move 对已出现的目标文件覆盖。
    """
    if os.path.lexists(target):
        raise FileExistsError(errno.EEXIST, "目标已存在，拒绝覆盖", str(target))
    source_state = _fingerprint(source)
    try:
        if os.name == "nt":
            os.rename(source, target)
            return
        os.link(source, target, follow_symlinks=False)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        _copy_exclusive(source, target)
    try:
        if _fingerprint(source) != source_state:
            raise OSError("源文件在移动传输期间发生变化，已保留原文件")
        source.unlink()
    except Exception as exc:
        try:
            _discard_created(target)
        except Exception as cleanup_error:
            logger.exception("移动失败后清理目标失败：%s", target)
            raise OSError(f"{type(exc).__name__}: {exc}；源文件仍在 {source}，"
                          f"副本保留在 {target}：{type(cleanup_error).__name__}: {cleanup_error}") from exc
        raise


def _reference_index(inputs: MoveInputs) -> tuple[dict[str, set[int]], dict[tuple[int, int], set[int]], str]:
    by_path: dict[str, set[int]] = {}
    by_inode: dict[tuple[int, int], set[int]] = {}
    uncertain = ""
    for fid, path, relative in inputs.references:
        source = inputs.root / path if relative else Path(path)
        try:
            by_path.setdefault(_key(source), set()).add(fid)
            current = source.stat()
            if current.st_ino:
                by_inode.setdefault((current.st_dev, current.st_ino), set()).add(fid)
        except FileNotFoundError:
            # 不存在的引用不能被移动破坏；规范化路径仍参与同源检查。
            continue
        except Exception as exc:
            logger.exception("读取本库文件引用失败：%s", source)
            uncertain = f"本库记录 {fid} 的源路径无法核验（{type(exc).__name__}: {exc}）"
    return by_path, by_inode, uncertain


def transfer_files(inputs: MoveInputs, progress_cb: Callable, is_cancelled: Callable) -> dict:
    """后台执行整批文件 IO；取消仅停止后续项，已完成结果必须交给主线程提交。"""
    results: list[TransferResult] = []
    errors: list[str] = []
    warnings: list[str] = []
    processed = 0
    total = len(inputs.files)
    by_path, by_inode, uncertain = _reference_index(inputs)
    root = inputs.root.resolve()
    for index, file in enumerate(inputs.files):
        if is_cancelled():
            break
        processed += 1
        source = Path(file.path).absolute()
        target: Path | str = inputs.root / f"project_{file.project_id}" / source.name
        stage = "核验源文件与目标目录"
        moved_without_result = False
        progress_cb(index, total, f"正在处理：{file.label or file.path}")
        try:
            if not Path(file.path).is_absolute():
                raise ValueError(f"链接源路径必须是绝对路径：{file.path}")
            if not source.is_file():
                raise FileNotFoundError(f"源文件不存在或不是普通文件：{source}")
            before = _fingerprint(source)
            reason = uncertain
            if _has_redirect(source):
                reason = "源路径包含符号链接或目录重解析点"
            elif source.resolve().is_relative_to(root):
                reason = "源文件位于当前库内部"
            elif ((by_path.get(_key(source), set()) | by_inode.get(before[:2], set())) - {file.file_id}):
                reason = "本库还有其它文件记录引用同一源文件（包含本次所选记录）"
            target_dir = inputs.root / f"project_{file.project_id}"
            target_dir.mkdir(parents=True, exist_ok=True)
            if _has_redirect(target_dir) or not target_dir.resolve().is_relative_to(root):
                raise ValueError(f"项目目录含重解析点或位于库外：{target_dir}")
            stage = "复制并保留源文件" if reason else "移动原文件"
            suffix = 0
            while True:
                target = target_dir / (source.name if suffix == 0
                                       else f"{source.stem}_{suffix}{source.suffix}")
                if os.path.lexists(target):
                    suffix += 1
                    continue
                try:
                    if reason:
                        _copy_exclusive(source, target)
                    else:
                        if _fingerprint(source) != before:
                            raise OSError("源文件在移动前发生变化")
                        move_no_replace(source, target)
                        moved_without_result = True
                    break
                except FileExistsError:
                    suffix += 1
            result = TransferResult(file, source, target,
                                    target.relative_to(inputs.root).as_posix(),
                                    not bool(reason), reason, _fingerprint(target))
            results.append(result)
            moved_without_result = False
            if reason:
                warnings.append(_detail(file, source, target, "保守复制，源文件已保留", reason))
        except Exception as exc:
            logger.exception("链接转仓储失败，记录 %s：%s", file.file_id, source)
            errors.append(_detail(file, source, target, stage, exc))
            if moved_without_result:
                # 移动后 stat 等收尾也可能失败；此时尚无可提交结果，立即在后台恢复。
                try:
                    if _has_redirect(source.parent) or _has_redirect(Path(target)):
                        raise OSError("恢复路径出现重解析点，拒绝自动恢复")
                    move_no_replace(Path(target), source)
                    errors[-1] += f"\n回滚成功；原文件已恢复：{source}"
                except Exception as restore_error:
                    logger.exception("移动后收尾失败，恢复源文件失败：%s", source)
                    errors[-1] += (f"\n回滚失败：{type(restore_error).__name__}: {restore_error}"
                                   f"\n仓储文件可恢复路径：{target}；原路径：{source}（不会覆盖现有文件）")
    progress_cb(total, total, "文件处理结束")
    return {"results": results, "errors": errors, "warnings": warnings,
            "processed": processed, "cancelled": bool(is_cancelled())}


def commit_transfers(repo: Repository, results: list[TransferResult]) -> tuple[list[TransferResult], list[TransferResult], list[str]]:
    """主线程核验记录快照后写回；失败项交给后台恢复，绝不静默忽略。"""
    committed: list[TransferResult] = []
    rollback: list[TransferResult] = []
    errors: list[str] = []
    for result in results:
        transaction_owned = False
        try:
            if repo.conn.in_transaction:
                raise RuntimeError("数据库仍有未提交事务，拒绝提交转换结果")
            # 在校验与更新之间锁住其它写入者，防止刚校验完记录又被外部 MCP 删除。
            repo.conn.execute("BEGIN IMMEDIATE")
            transaction_owned = True
            current = repo.get_file(result.file.file_id)
            if current is None:
                raise RuntimeError("文件记录已删除")
            if astuple(current) != result.file.record:
                raise RuntimeError("文件记录在转换期间已改变")
            repo.update_file(replace(current, path=result.relative_path, is_relative=True))
            committed.append(result)
        except Exception as exc:
            logger.exception("链接转仓储写回失败，记录 %s", result.file.file_id)
            rollback.append(result)
            errors.append(_detail(result.file, result.source, result.target, "数据库写回失败，等待恢复文件", exc))
            if transaction_owned:
                try:
                    repo.conn.rollback()
                except Exception as rollback_error:
                    logger.exception("撤销失败的转换数据库事务失败")
                    errors[-1] += f"\n撤销数据库事务失败：{type(rollback_error).__name__}: {rollback_error}"
    return committed, rollback, errors


def rollback_transfers(results: list[TransferResult], progress_cb: Callable, is_cancelled: Callable) -> list[str]:
    """后台恢复失败项；恢复不可取消，原位置已出现文件时保留仓储文件待恢复。"""
    reports: list[str] = []
    for index, result in enumerate(results):
        progress_cb(index, len(results), f"正在恢复：{result.file.label or result.source.name}")
        try:
            if _has_redirect(result.target):
                raise OSError("仓储路径出现重解析点，拒绝自动移动或清理")
            if _fingerprint(result.target) != result.target_stat:
                raise OSError("仓储文件在恢复前已变化，拒绝自动移动或清理")
            if result.moved:
                if _has_redirect(result.source.parent):
                    raise OSError("原目录出现重解析点，拒绝自动恢复")
                move_no_replace(result.target, result.source)
                reports.append(_detail(result.file, result.source, result.target,
                                       "回滚成功", f"已恢复原文件：{result.source}"))
            else:
                _discard_created(result.target)
                reports.append(_detail(result.file, result.source, result.target,
                                       "回滚成功", "原文件一直保留，未登记副本已移入回收站。"))
        except Exception as exc:
            logger.exception("恢复转换失败文件失败：%s -> %s", result.target, result.source)
            reports.append(_detail(result.file, result.source, result.target, "回滚失败",
                                   f"{type(exc).__name__}: {exc}\n"
                                   f"请手动恢复；仓储文件保留路径：{result.target}\n"
                                   f"原位置当前{'有文件，未覆盖' if os.path.lexists(result.source) else '无文件'}。"))
    progress_cb(len(results), len(results), "恢复处理结束")
    return reports
