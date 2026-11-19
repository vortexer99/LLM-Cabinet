"""仓储复制落库后，按库级设置安全回收已导入的源文件。"""
from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .library import Library
from .repository import Repository
from .utils import move_to_trash

STORAGE_RECYCLE_SOURCE_KEY = "storage_recycle_source"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceSnapshot:
    """复制前源文件的身份及状态；不读取文件内容。"""

    path: Path
    size: int
    mtime_ns: int
    dev: int
    ino: int


@dataclass(frozen=True)
class _StoredFileSnapshot:
    """主线程读取的文件记录；后台不保留可变模型或 SQLite 对象。"""

    path: str
    is_relative: bool


class _LinkRowsSnapshot:
    def __init__(self, paths: tuple[str, ...]):
        self.paths = paths

    def fetchall(self) -> list[dict[str, str]]:
        return [{"path": path} for path in self.paths]


class _ConnectionSnapshot:
    def __init__(self, in_transaction: bool, paths: tuple[str, ...]):
        self.in_transaction = in_transaction
        self.paths = paths

    def execute(self, sql: str) -> _LinkRowsSnapshot:
        """只允许现有回收检查所需的只读查询，绝不持有真实连接。"""
        if sql.strip() != "SELECT path FROM files WHERE is_relative=0":
            raise ValueError("回收快照不支持此查询")
        return _LinkRowsSnapshot(self.paths)


class StorageCleanupRepoSnapshot:
    """供回收 worker 使用的纯 Python 只读数据，须在主线程预先创建。"""

    def __init__(
        self, enabled: str, in_transaction: bool,
        files: dict[int, _StoredFileSnapshot | None], linked_paths: tuple[str, ...],
    ):
        self._enabled = enabled
        self._files = dict(files)
        self.conn = _ConnectionSnapshot(in_transaction, linked_paths)

    def get_setting(self, key: str, default: str = "") -> str:
        return self._enabled if key == STORAGE_RECYCLE_SOURCE_KEY else default

    def get_file(self, file_id: int) -> _StoredFileSnapshot | None:
        return self._files.get(file_id)


def snapshot_cleanup_repo(
    repo: Repository, candidates: list[tuple[SourceSnapshot, str, int]],
) -> StorageCleanupRepoSnapshot:
    """主线程预读回收所需数据库状态；返回值不持有 repo 或 SQLite 对象。

    使用快照回收时，调用方须在 worker 完成前阻止当前库的其他写入。
    文件 stat、路径解析及回收等磁盘操作仍全部由回收阶段执行。
    """
    enabled = repo.get_setting(STORAGE_RECYCLE_SOURCE_KEY, "0")
    in_transaction = repo.conn.in_transaction
    files: dict[int, _StoredFileSnapshot | None] = {}
    paths: tuple[str, ...] = ()
    if enabled == "1" and candidates:
        for file_id in {entry[2] for entry in candidates}:
            row = repo.get_file(file_id)
            files[file_id] = (
                None if row is None else _StoredFileSnapshot(row.path, row.is_relative)
            )
        paths = tuple(row["path"] for row in repo.conn.execute(
            "SELECT path FROM files WHERE is_relative=0",
        ).fetchall())
    return StorageCleanupRepoSnapshot(enabled, in_transaction, files, paths)


def capture_storage_source(source: Path) -> SourceSnapshot:
    """记录源文件的绝对路径与 stat，用于提交后检查源文件是否变化。"""
    path = Path(source).resolve()
    current = path.stat()
    if not stat.S_ISREG(current.st_mode):
        raise OSError(f"源路径不是普通文件：{path}")
    return SourceSnapshot(
        path, current.st_size, current.st_mtime_ns, current.st_dev, current.st_ino,
    )


def _path_key(path: Path) -> str:
    """统一大小写与路径别名，避免重复回收或遗漏链接引用。"""
    return os.path.normcase(str(path.resolve()))


def cleanup_storage_sources(
    repo: Repository | StorageCleanupRepoSnapshot,
    library: Library,
    candidates: list[tuple[SourceSnapshot, str, int]],
    *,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[str]:
    """只回收复制与数据库提交均已成功的文件；跳过或失败以警告返回。

    不移除任何目录；回收站不可用时保留源文件，绝不回退硬删除。
    调用方只传普通仓储复制的候选项，ZIP 解压临时文件不应加入候选。
    """
    if not candidates:
        return []
    warnings: list[str] = []
    try:
        if repo.get_setting(STORAGE_RECYCLE_SOURCE_KEY, "0") != "1":
            return []
        root = library.root.resolve()
        # 主线程批量收尾期间不更新数据库，引用路径只需读取、规范化一次。
        linked_paths = repo.conn.execute(
            "SELECT path FROM files WHERE is_relative=0",
        ).fetchall()
        linked_keys = {_path_key(Path(row["path"])) for row in linked_paths}
        # 同一源文件可能被重复导入：先分组，所有落库目标均检查后只回收一次。
        grouped: dict[str, list[tuple[SourceSnapshot, str, int]]] = {}
        for candidate in candidates:
            grouped.setdefault(_path_key(candidate[0].path), []).append(candidate)
    except Exception as exc:
        logger.exception("检查仓储源文件回收设置失败")
        return [f"检查源文件回收条件失败，已保留源文件：{exc}"]

    total = len(grouped)
    for index, (source_key, entries) in enumerate(grouped.items()):
        if is_cancelled is not None and is_cancelled():
            warnings.append(f"已取消原文件回收，剩余 {total - index} 个源文件已保留。")
            break
        snapshot = entries[0][0]
        source = snapshot.path
        if progress is not None:
            progress(index, total, f"正在回收原文件：{source.name}")
        try:
            reason = ""
            if repo.conn.in_transaction:
                reason = "数据库仍有未提交事务"
            elif source.resolve().is_relative_to(root):
                reason = "源文件位于当前仓库内"
            else:
                for expected, relative_path, file_id in entries:
                    row = repo.get_file(file_id)
                    if row is None or not row.is_relative or row.path != relative_path:
                        reason = "仓储文件记录未成功保存或已改变"
                        break
                    target = library.resolve(relative_path, True).resolve()
                    if not target.is_relative_to(root) or not target.is_file():
                        reason = "仓储副本不存在或不在当前仓库内"
                        break
                    if target.stat().st_size != expected.size:
                        reason = "仓储副本大小与复制前的源文件不一致"
                        break
                    if expected != snapshot:
                        reason = "同一源文件在复制期间发生变化"
                        break
                if not reason:
                    if source_key in linked_keys:
                        reason = "本库仍有链接记录引用此源文件"
                if not reason and capture_storage_source(source) != snapshot:
                    reason = "源文件在复制期间发生变化"
                # 再检查事务状态，绝不通过回收操作隐式提交调用方事务。
                if not reason and repo.conn.in_transaction:
                    reason = "数据库仍有未提交事务"
            if reason:
                message = f"已保留源文件 {source}：{reason}"
                logger.warning(message)
                warnings.append(message)
                continue
            move_to_trash(source, allow_delete=False)
        except Exception as exc:
            logger.exception("回收仓储源文件失败，已保留源文件：%s", source)
            warnings.append(f"回收源文件失败，已保留 {source}：{exc}")
    if progress is not None:
        progress(total, total, "原文件处理结束")
    return warnings
