"""库一致性检查与备份/恢复（task #14）。

- ``run_consistency_check``：扫描所有 ``is_relative=True`` 的文件，
  对每个 ``library.resolve()`` 检查物理是否存在；同时对 ``is_relative=False``
  的链接文件做"原路径已失效"检测。
- ``apply_consistency_action``：按用户选择 ``mark`` / ``delete`` / ``noop``
  处理报告里的失效项。
- ``backup_library`` / ``restore_library``：把整个库目录打 zip / 从 zip 解到目录。

入口在 MainWindow 的「工具」菜单。
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional

from .library import Library
from .repository import Repository

log = logging.getLogger(__name__)


# =============================================================================
# 一致性检查
# =============================================================================
@dataclass
class MissingFileEntry:
    file_id: int
    project_id: int
    project_title: str
    file_name: str
    file_path: str           # 原始 path（rel 或 abs）
    is_relative: bool
    resolved: str            # library.resolve 后的绝对路径（用于显示）

    @property
    def storage_label(self) -> str:
        return "📦 仓储" if self.is_relative else "🔗 链接"


@dataclass
class ConsistencyReport:
    total_files: int = 0
    n_storage: int = 0           # is_relative=True 总数
    n_link: int = 0              # is_relative=False 总数
    storage_missing: list[MissingFileEntry] = field(default_factory=list)
    link_missing: list[MissingFileEntry] = field(default_factory=list)

    @property
    def total_missing(self) -> int:
        return len(self.storage_missing) + len(self.link_missing)


ConsistencyAction = Literal["mark", "delete", "noop"]


def run_consistency_check(
    repo: Repository,
    library: Library,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> ConsistencyReport:
    """扫描全库文件物理状态。

    progress 回调签名 ``(done, total, current_name)``。
    """
    rep = ConsistencyReport()

    # 一次拿出全部文件 + 项目标题（避免 N+1）
    rows = repo.conn.execute(
        """SELECT f.id AS fid, f.project_id AS pid, f.path AS path,
                  f.is_relative AS is_rel, p.title AS title
           FROM files f LEFT JOIN projects p ON p.id = f.project_id
           ORDER BY f.id"""
    ).fetchall()
    rep.total_files = len(rows)

    for i, r in enumerate(rows):
        is_rel = bool(r["is_rel"])
        if is_rel:
            rep.n_storage += 1
        else:
            rep.n_link += 1
        try:
            resolved = library.resolve(r["path"], is_rel)
        except Exception:
            resolved = Path(r["path"])
        name = Path(r["path"]).name
        if not resolved.is_file():
            entry = MissingFileEntry(
                file_id=int(r["fid"]),
                project_id=int(r["pid"] or 0),
                project_title=r["title"] or "(未命名)",
                file_name=name,
                file_path=r["path"],
                is_relative=is_rel,
                resolved=str(resolved),
            )
            if is_rel:
                rep.storage_missing.append(entry)
            else:
                rep.link_missing.append(entry)
        if progress is not None:
            try:
                progress(i + 1, rep.total_files, name)
            except Exception:
                pass

    return rep


def apply_consistency_action(
    repo: Repository,
    report: ConsistencyReport,
    action: ConsistencyAction,
) -> tuple[int, int]:
    """按 action 处理报告里的失效项。

    返回 ``(n_marked, n_deleted)``。
    """
    if action == "noop":
        return (0, 0)

    affected = report.storage_missing + report.link_missing
    if action == "mark":
        for entry in affected:
            repo.set_file_missing(entry.file_id, True)
        return (len(affected), 0)

    if action == "delete":
        for entry in affected:
            repo.delete_file(entry.file_id)
        return (0, len(affected))

    return (0, 0)


# =============================================================================
# 备份 / 恢复
# =============================================================================
def backup_library(
    library_root: Path,
    target_zip: Path,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> Path:
    """把整个库目录打成 zip。

    返回最终 zip 路径（带 .zip 后缀）。target_zip 不带 .zip 时自动加上。
    用户负责事先关闭 LLM worker 与 db connection 以避免锁问题（建议主调用方
    用『让用户先关闭应用 → 在外部跑此函数』；GUI 内嵌调用方需先 PRAGMA wal_checkpoint）。
    """
    library_root = Path(library_root)
    if not library_root.is_dir():
        raise NotADirectoryError(f"库目录不存在：{library_root}")

    target_zip = Path(target_zip)
    if target_zip.suffix.lower() != ".zip":
        target_zip = target_zip.with_suffix(target_zip.suffix + ".zip")

    target_zip.parent.mkdir(parents=True, exist_ok=True)
    base = target_zip.with_suffix("")  # shutil.make_archive 自己加扩展名

    # 临时重定向：让 shutil.make_archive 把整个目录打包，根目录名 = library_root.name
    parent = library_root.parent
    base_name_in_zip = library_root.name
    out = shutil.make_archive(
        base_name=str(base),
        format="zip",
        root_dir=str(parent),
        base_dir=base_name_in_zip,
    )
    if progress is not None:
        try:
            progress(1, 1, target_zip.name)
        except Exception:
            pass
    return Path(out)


def restore_library(
    src_zip: Path,
    target_root: Path,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> Path:
    """从 zip 解出一个库目录。

    src_zip 是 ``backup_library`` 的产物。target_root 必须不存在或为空目录。
    返回解出后实际的库根（zip 内可能有顶层包裹目录，自动检测）。
    """
    import zipfile
    src_zip = Path(src_zip)
    if not src_zip.is_file():
        raise FileNotFoundError(f"备份文件不存在：{src_zip}")
    target_root = Path(target_root)
    if target_root.exists():
        if not target_root.is_dir() or any(target_root.iterdir()):
            raise FileExistsError(
                f"目标目录非空，拒绝覆盖：{target_root}"
            )
    else:
        target_root.mkdir(parents=True, exist_ok=True)

    # 解压到一个临时同级目录，再把里面"实际的库"挪到 target_root
    import tempfile
    with tempfile.TemporaryDirectory(prefix="cabinet_restore_") as tmp:
        with zipfile.ZipFile(src_zip, "r") as z:
            z.extractall(tmp)
            if progress is not None:
                try:
                    progress(1, 1, src_zip.name)
                except Exception:
                    pass
        # 找解压后的"库根"：要么直接就是 cabinet.db 所在目录，
        # 要么有一层包裹目录（backup_library 写出的就是这种）
        tmp_path = Path(tmp)
        candidates = [tmp_path]
        for child in tmp_path.iterdir():
            if child.is_dir():
                candidates.append(child)
        chosen: Optional[Path] = None
        for c in candidates:
            if (c / "cabinet.db").is_file():
                chosen = c
                break
        if chosen is None:
            raise ValueError(
                f"备份 zip 内未找到 cabinet.db，可能不是有效的 LLM Cabinet 备份"
            )
        # 把 chosen 内容搬到 target_root
        for item in chosen.iterdir():
            shutil.move(str(item), str(target_root / item.name))

    return target_root


__all__ = [
    "ConsistencyReport",
    "MissingFileEntry",
    "ConsistencyAction",
    "run_consistency_check",
    "apply_consistency_action",
    "backup_library",
    "restore_library",
]
