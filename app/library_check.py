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
from .utils import OperationCancelled

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


def snapshot_file_rows(repo: Repository) -> list:
    """一致性检查所需的文件清单快照（task #36：主线程查好再交给 worker）。

    worker 线程不得触碰 ``repo.conn``（sqlite 连接不跨线程）。
    """
    return repo.conn.execute(
        """SELECT f.id AS fid, f.project_id AS pid, f.path AS path,
                  f.is_relative AS is_rel, p.title AS title
           FROM files f LEFT JOIN projects p ON p.id = f.project_id
           ORDER BY f.id"""
    ).fetchall()


def run_consistency_check(
    repo: Repository,
    library: Library,
    *,
    progress: Callable[[int, int, str], None] | None = None,
    rows: list | None = None,
) -> ConsistencyReport:
    """扫描全库文件物理状态。

    progress 回调签名 ``(done, total, current_name)``。

    ``rows``：可选的文件清单快照（task #36）。UI 线程化时由主线程先查好
    传入，worker 线程里不再触碰 ``repo.conn``；为 None 时维持旧行为现查。
    """
    rep = ConsistencyReport()

    # 一次拿出全部文件 + 项目标题（避免 N+1）
    if rows is None:
        rows = snapshot_file_rows(repo)
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
            except OperationCancelled:
                raise  # task #36：取消语义要穿透到 worker
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
def _write_backup_database(source: Path, destination: Path) -> None:
    """创建无凭据、无历史空闲页的独立快照，不修改源库或访问凭据管理器。"""
    import json
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
            dst.execute("PRAGMA secure_delete=ON")
            row = dst.execute("SELECT value FROM settings WHERE key='llm_config'").fetchone()
            if row:
                try:
                    data = json.loads(row[0])
                    for provider in data.get("providers", {}).values():
                        provider["api_key"] = ""
                    cleaned = json.dumps(data, ensure_ascii=False)
                except (ValueError, TypeError, AttributeError):
                    # 配置损坏时不能把不可解析的原文（可能含密钥）带进备份。
                    log.warning("备份中移除了无法解析的 LLM 配置")
                    cleaned = "{}"
                dst.execute("UPDATE settings SET value=? WHERE key='llm_config'", (cleaned,))
            dst.commit()
            dst.execute("VACUUM")


def backup_library(
    library_root: Path,
    target_zip: Path,
    *,
    include_foreign: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
) -> Path:
    """把库目录打成 zip。

    返回最终 zip 路径（带 .zip 后缀）。target_zip 不带 .zip 时自动加上。
    数据库使用在线快照并移除 API Key；不打包 WAL、journal 和旧迁移备份。
    文件复制期间应避免修改仓储文件，以保证文件与数据库快照相符。

    内容控制：
    - **始终跳过** ``cabinet.json`` 等"软件全局配置"（``_is_app_global_entry``）：
      这些文件只在库根 == ``%APPDATA%/LLMCabinet/`` 的历史场景下才会出现，
      不属于任一库的内容，备份带上反而会污染恢复目标
    - ``include_foreign=False``（默认 True）时还会跳过"用户外来内容"
      （非 ``_is_library_owned_entry``、非 ``_is_app_global_entry`` 的顶层条目），
      产出"瘦身"备份（只含 cabinet.db / library/ / .llm-cabinet 等）
    - zip 内根目录名 = ``library_root.name``（与 ``shutil.make_archive`` 对齐，
      保证 ``restore_library`` 能正确识别"包裹一层"的 zip 结构）

    progress 回调签名 ``(current, total, name) -> None``：``current/total`` 都是
    粗略文件计数，``name`` 是当前文件名。
    """
    import tempfile
    # 在函数内部 import 同包内的 helper，避免文件顶部循环依赖
    from .cabinet import _is_app_global_entry, _is_library_owned_entry

    library_root = Path(library_root)
    if not library_root.is_dir():
        raise NotADirectoryError(f"库目录不存在：{library_root}")

    target_zip = Path(target_zip)
    if target_zip.suffix.lower() != ".zip":
        target_zip = target_zip.with_suffix(target_zip.suffix + ".zip")
    target_zip.parent.mkdir(parents=True, exist_ok=True)

    base_name_in_zip = library_root.name

    # 第一遍：决定要不要把每个顶层条目纳入备份
    selected_top: list[Path] = []
    try:
        for p in library_root.iterdir():
            if (p.resolve() == target_zip.resolve()
                    or p.name in ("cabinet.db-wal", "cabinet.db-shm", "cabinet.db-journal")
                    or (p.name.startswith("cabinet.v") and p.name.endswith(".bak"))):
                continue
            if _is_app_global_entry(p.name):
                continue  # 软件全局：永不备份（防孤儿）
            if _is_library_owned_entry(p.name):
                selected_top.append(p)
                continue
            # 其它都是 foreign（用户外来内容）
            if include_foreign:
                selected_top.append(p)
    except OSError as e:
        raise RuntimeError(f"扫描库目录失败：{e}") from e

    # 第二遍：递归收集所有要打包的文件，记录 zip 内 arcname；
    # 同时记录"空目录"以保留与 shutil.make_archive 一致的目录结构（restore
    # 端有断言依赖空 library/ 等子目录的存在）
    files_to_pack: list[tuple[Path, str]] = []
    empty_dirs: list[str] = []
    for top in selected_top:
        if top.is_file():
            arc = f"{base_name_in_zip}/{top.name}"
            files_to_pack.append((top, arc))
        elif top.is_dir():
            had_any = False
            for sub in top.rglob("*"):
                if sub.is_file():
                    if sub.resolve() == target_zip.resolve():
                        continue
                    rel = sub.relative_to(library_root).as_posix()
                    arc = f"{base_name_in_zip}/{rel}"
                    files_to_pack.append((sub, arc))
                    had_any = True
            if not had_any:
                # 空目录：写一条目录条目占位
                rel = top.relative_to(library_root).as_posix()
                empty_dirs.append(f"{base_name_in_zip}/{rel}/")

    with tempfile.TemporaryDirectory(prefix="cabinet_backup_") as tmp:
        snapshot = Path(tmp) / "cabinet.db"
        _write_backup_database(library_root / "cabinet.db", snapshot)
        # 快照失败时尚未打开输出 zip，保留用户原有备份。
        return _pack_backup_files(target_zip, snapshot, files_to_pack, empty_dirs, progress)


def _pack_backup_files(
    target_zip: Path, snapshot: Path, files_to_pack: list[tuple[Path, str]],
    empty_dirs: list[str], progress: Callable[[int, int, str], None] | None,
) -> Path:
    """打包已筛选文件；数据库只使用已清理快照，读失败如实报错。"""
    import zipfile
    total = len(files_to_pack)
    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arc in empty_dirs:
            # ZipInfo 名字以 "/" 结尾被解释为目录条目
            zf.writestr(zipfile.ZipInfo(arc), "")
        for i, (src, arc) in enumerate(files_to_pack, 1):
            zf.write(snapshot if src.name == "cabinet.db" and arc.count("/") == 1 else src,
                     arcname=arc)
            if progress is not None:
                try:
                    progress(i, total, src.name)
                except Exception:
                    pass

    return target_zip


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
        # 把 chosen 内容搬到 target_root；
        # 跳过 cabinet.json 等"软件全局配置"——老备份（库建在 appdata 时）
        # 会把全局配置一起打包，搬到新位置就成了孤儿，后续删除/扫描时会
        # 困扰用户（被识别为 app_global 不删）。源头清理，restore 直接丢弃。
        from .cabinet import _is_app_global_entry
        for item in chosen.iterdir():
            if _is_app_global_entry(item.name):
                continue
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
