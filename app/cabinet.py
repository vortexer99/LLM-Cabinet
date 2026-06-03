"""多项目库并存与切换（task #08）。

每个"库"是一个完整的目录：

    <library-root>/
    ├── cabinet.db               # SQLite 数据库
    ├── library/                 # copy 模式的文件仓储根
    │   └── project_<id>/...
    ├── cabinet.v*.bak           # schema 自动备份（已有机制）
    └── .llm-cabinet             # 标记文件（识别"这是 LLM Cabinet 库"）

跨库的全局配置：

    %APPDATA%/LLMCabinet/cabinet.json
        active_library: 当前活动库目录
        recent_libraries: 最近 5 个库的列表（label / last_opened）

历史的 ``%APPDATA%/LLMCabinet/cabinet.db`` 仍作为"默认库"，第一次启动新版本时
自动登记到 ``recent_libraries``。

设计要点见 ``tasks/08-multiple-libraries-switch.md``。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .utils import app_data_dir

log = logging.getLogger(__name__)

# 全局配置文件名
CABINET_JSON = "cabinet.json"
# 库目录的标记文件
LIBRARY_MARKER = ".llm-cabinet"
# 最近列表上限
MAX_RECENT = 5


# =============================================================================
# 数据类
# =============================================================================
@dataclass
class LibraryHandle:
    """一个项目库的元描述（不含 db 连接，纯数据）。"""

    path: Path
    label: str = ""
    last_opened: Optional[str] = None    # ISO 时间戳

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "label": self.label,
            "last_opened": self.last_opened or "",
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LibraryHandle":
        return cls(
            path=Path(str(d.get("path", ""))),
            label=str(d.get("label", "") or ""),
            last_opened=(d.get("last_opened") or None),
        )

    @property
    def display_name(self) -> str:
        return self.label or self.path.name or str(self.path)


# =============================================================================
# CabinetConfig
# =============================================================================
class CabinetConfig:
    """``%APPDATA%/LLMCabinet/cabinet.json`` 读写。

    只关心"哪些库 / 哪个是当前 / 各自的 label 和最近打开时间"，不持有 db 连接。
    """

    def __init__(
        self,
        active_library: Optional[Path],
        recent_libraries: list[LibraryHandle],
    ):
        self.active_library: Optional[Path] = active_library
        self.recent_libraries: list[LibraryHandle] = list(recent_libraries)

    # ---- 持久化 ----
    @classmethod
    def config_path(cls) -> Path:
        return app_data_dir() / CABINET_JSON

    @classmethod
    def load(cls) -> "CabinetConfig":
        """读 ``cabinet.json``。损坏 / 缺失时备份并重建为默认。"""
        path = cls.config_path()
        if not path.exists():
            return cls._default()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("cabinet.json 顶层不是对象")
        except Exception as e:
            log.warning("cabinet.json 解析失败：%s；备份并重建。", e)
            try:
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                # 使用 .bak.<ts>.json 后缀但完整保留原文件名（避免 with_suffix 把 .json 替换掉）
                bak_path = path.with_name(f"{path.name}.bak.{ts}.json")
                path.rename(bak_path)
            except Exception:
                pass
            return cls._default()

        active_raw = data.get("active_library")
        active = Path(active_raw) if active_raw else None
        recents_raw = data.get("recent_libraries") or []
        recents: list[LibraryHandle] = []
        seen: set[str] = set()
        if isinstance(recents_raw, list):
            for item in recents_raw:
                if not isinstance(item, dict):
                    continue
                h = LibraryHandle.from_dict(item)
                key = str(h.path)
                if key in seen:
                    continue
                seen.add(key)
                recents.append(h)
        return cls(active_library=active, recent_libraries=recents)

    @classmethod
    def _default(cls) -> "CabinetConfig":
        """全新 / 损坏后的默认状态：空配置。

        历史上这里会把 ``%APPDATA%/LLMCabinet`` 自动登记为"默认库"；新方案下
        ``%APPDATA%/LLMCabinet`` 仅作为 ``cabinet.json`` 等**软件层全局配置**的
        存放点，**不再**自动当作库。空配置时由启动期 ``main`` 弹 Welcome 让用户
        显式选择新建 / 打开已有库。
        """
        return cls(active_library=None, recent_libraries=[])

    def save(self) -> None:
        path = self.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "active_library": str(self.active_library) if self.active_library else "",
            "recent_libraries": [h.to_dict() for h in self.recent_libraries],
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---- 列表维护 ----
    def touch(self, lib_path: Path, label: Optional[str] = None) -> None:
        """登记 / 刷新一个库的 last_opened；保持上限 MAX_RECENT。"""
        lib_path = Path(lib_path).resolve()
        # 找现有
        existing = None
        for h in self.recent_libraries:
            if h.path.resolve() == lib_path:
                existing = h
                break
        if existing is not None:
            existing.last_opened = _now_iso()
            if label is not None:
                existing.label = label
            # 提到列表头部
            self.recent_libraries.remove(existing)
            self.recent_libraries.insert(0, existing)
        else:
            new = LibraryHandle(
                path=lib_path,
                label=(label or lib_path.name),
                last_opened=_now_iso(),
            )
            self.recent_libraries.insert(0, new)
        # 限长（默认库优先保留）
        self.recent_libraries = self._trim_recents()
        self.active_library = lib_path

    def remove(self, lib_path: Path) -> None:
        """从最近列表移除（不动磁盘）。"""
        lib_path = Path(lib_path).resolve()
        self.recent_libraries = [
            h for h in self.recent_libraries if h.path.resolve() != lib_path
        ]

    def rename(self, lib_path: Path, new_label: str) -> None:
        lib_path = Path(lib_path).resolve()
        for h in self.recent_libraries:
            if h.path.resolve() == lib_path:
                h.label = new_label
                return

    def find(self, lib_path: Path) -> Optional[LibraryHandle]:
        lib_path = Path(lib_path).resolve()
        for h in self.recent_libraries:
            if h.path.resolve() == lib_path:
                return h
        return None

    def _trim_recents(self) -> list[LibraryHandle]:
        """保留最近 MAX_RECENT 项（按 ``recent_libraries`` 现有顺序，
        ``touch`` 已经把最新的提到队首，所以这里直接切片即可）。"""
        return self.recent_libraries[:MAX_RECENT]


# =============================================================================
# 库目录工具
# =============================================================================
def resolve_library_paths(root: Path) -> tuple[Path, Path]:
    """从库根目录派生出 ``(db_path, library_subdir)``。"""
    root = Path(root)
    return (root / "cabinet.db", root / "library")


def is_library_dir(root: Path) -> bool:
    """判断目录已经是有效的 LLM Cabinet 库。

    判据（任一即可）：
    - 含 ``.llm-cabinet`` 标记文件
    - 含 ``cabinet.db`` 文件
    """
    root = Path(root)
    if not root.is_dir():
        return False
    if (root / LIBRARY_MARKER).is_file():
        return True
    if (root / "cabinet.db").is_file():
        return True
    return False


def is_empty_or_safe_for_library(root: Path) -> bool:
    """判断目录是否适合**新建**库：要么不存在、要么空、要么只有非业务文件。

    业务文件冲突时返回 False（避免污染用户已有数据）。
    """
    root = Path(root)
    if not root.exists():
        return True
    if not root.is_dir():
        return False
    # 任何 cabinet.* / library/ 都视作冲突（除非已是有效库）
    if is_library_dir(root):
        return False
    try:
        for p in root.iterdir():
            # 隐藏文件（.git / .DS_Store 等）算 OK
            if p.name.startswith("."):
                continue
            return False
    except OSError:
        return False
    return True


# Windows 路径里不允许的字符（盘符 ":" 例外，但用户手输 "C:\..." 是合法的）
_WIN_BAD_CHARS = '<>"|?*'


def validate_library_path(root: Path) -> Optional[str]:
    """检查给定路径是否适合作为新库的根目录。

    返回 ``None`` 表示通过；否则返回**面向用户**的错误描述（中文，不带技术黑话）。

    覆盖：
    - 必须是绝对路径
    - 不能是盘符根 / 系统保护目录（防灾难性删除）
    - 不允许 Windows 非法字符（``<>"|?*``）
    - 父目录必须存在（或路径本身已存在）

    内容相关检查（目录非空 / 已是其它库 / 已在 recent 列表）由调用方
    与 ``is_empty_or_safe_for_library`` 共同把关，本函数不重复。
    """
    root = Path(root)
    s = str(root)

    # 绝对路径
    if not root.is_absolute():
        return "请填写完整的绝对路径（例如 D:/Libraries/papers）。"

    # Windows 非法字符（仅检查盘符之后的部分，盘符 "C:" 里的冒号合法）
    tail = s
    if sys.platform == "win32" and len(s) >= 2 and s[1] == ":":
        tail = s[2:]
    for ch in _WIN_BAD_CHARS:
        if ch in tail:
            return f"路径里不能包含特殊字符：{_WIN_BAD_CHARS}"
    # 冒号在盘符之外的位置也是非法的
    if sys.platform == "win32" and ":" in tail:
        return "路径里冒号只能用在盘符（如 C:/）。"

    # 盘符根（C:\、D:\ ...）—— 直接拿盘符当库根太危险
    try:
        if root.parent == root:
            return "请不要直接选盘符根目录；建议在盘符下新建子目录作为库根。"
    except OSError:
        pass

    # 系统保护目录：精确匹配几个高危位置 + 它们就是库根的情况；
    # 子目录（如 C:/Users/<name>/Documents/...）不拦
    if sys.platform == "win32":
        protected = {
            Path(os.environ.get("SystemRoot", r"C:\Windows")),
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
            Path(r"C:\Users"),
        }
        try:
            r = root.resolve()
            for prot in protected:
                if r == prot.resolve():
                    return f"不允许把 {prot} 直接当作库根目录。"
        except OSError:
            pass

    # 父目录必须存在（已存在的 root 不要求）
    if not root.exists():
        parent = root.parent
        if not parent.exists() or not parent.is_dir():
            return (
                f"上层目录不存在：{parent}\n"
                "请先创建上层目录，或选一个已存在的位置。"
            )

    return None


def mark_as_library(root: Path) -> None:
    """在库根目录写入 ``.llm-cabinet`` 标记文件。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / LIBRARY_MARKER).touch(exist_ok=True)


# 库内"白名单"——这些顶层条目属于库自身，"删除整个库"时无条件清掉
# （注意 SQLite WAL 模式的 -wal / -shm 边车文件；schema 自动备份 cabinet.v*.bak）
def _is_library_owned_entry(name: str) -> bool:
    """判断库根目录下顶层条目名是否属于"库自身"。"""
    if name == LIBRARY_MARKER:
        return True
    if name == "library":  # copy 模式仓储根
        return True
    if name in ("cabinet.db", "cabinet.db-wal", "cabinet.db-shm"):
        return True
    # schema 自动备份：cabinet.vN.bak / cabinet.vN.<时间戳>.bak
    if name.startswith("cabinet.v") and name.endswith(".bak"):
        return True
    return False


# 软件全局文件——这些顶层条目属于"软件"而非任一具体库。当某个库的根目录恰好
# 也是 ``app_data_dir()``（历史遗留 / 用户故意把库建在 appdata）时，删除该库
# 不应连带破坏这些跨库的软件配置。无论用户选"仅删库数据"还是"一并删除"，这
# 类条目都被保留。
def _is_app_global_entry(name: str) -> bool:
    """判断顶层条目名是否属于"软件全局配置"（如 ``cabinet.json``）。"""
    if name == CABINET_JSON:
        return True
    # cabinet.json 的损坏备份：cabinet.json.bak.<ts>.json
    if name.startswith(CABINET_JSON + ".bak.") and name.endswith(".json"):
        return True
    return False


@dataclass
class LibraryDeleteScan:
    """``scan_library_for_deletion`` 的结果，用于 UI 二次确认。

    - ``owned``：库自身的顶层条目（删除整个库时无条件清掉）
    - ``foreign``：库目录下属于用户自己的额外内容（默认应保留，让 UI 显式询问）
    - ``app_global``：软件全局配置（如 ``cabinet.json``）；任何模式下都保留
    - ``owned_size`` / ``foreign_size`` / ``app_global_size`` / ``total_size``：
      分别按归属统计的大小（``total_size`` = 三者之和）
    """
    owned: list[Path]
    foreign: list[Path]
    app_global: list[Path]
    total_size: int
    owned_size: int
    foreign_size: int
    app_global_size: int


def _entry_size(p: Path) -> int:
    """递归估算条目大小；不抛错。"""
    try:
        if p.is_file():
            return p.stat().st_size
        if p.is_dir():
            return sum(
                f.stat().st_size for f in p.rglob("*") if f.is_file()
            )
    except OSError:
        return 0
    return 0


def scan_library_for_deletion(root: Path) -> LibraryDeleteScan:
    """扫描库目录，把顶层条目分成"库自身"/"软件全局"/"用户外来内容"三组。

    - 库自身（``owned``）：删除整个库时无条件清掉
    - 软件全局（``app_global``，如 ``cabinet.json``）：任何模式下都保留
    - 用户外来内容（``foreign``）：当非空时，UI 应让用户显式选择保留 / 一并删除
    """
    root = Path(root)
    owned: list[Path] = []
    foreign: list[Path] = []
    app_global: list[Path] = []
    owned_size = 0
    foreign_size = 0
    app_global_size = 0
    try:
        for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
            sz = _entry_size(p)
            if _is_library_owned_entry(p.name):
                owned.append(p)
                owned_size += sz
            elif _is_app_global_entry(p.name):
                app_global.append(p)
                app_global_size += sz
            else:
                foreign.append(p)
                foreign_size += sz
    except OSError:
        pass
    return LibraryDeleteScan(
        owned=owned,
        foreign=foreign,
        app_global=app_global,
        total_size=owned_size + foreign_size + app_global_size,
        owned_size=owned_size,
        foreign_size=foreign_size,
        app_global_size=app_global_size,
    )


def delete_library_owned_only(root: Path) -> list[tuple[Path, str]]:
    """只删除库内白名单条目，保留 ``root`` 目录本体与所有外来 / 软件全局内容。

    返回失败列表 ``[(path, error_message), ...]``。成功项不返回。
    """
    import shutil as _sh
    failures: list[tuple[Path, str]] = []
    scan = scan_library_for_deletion(root)
    for p in scan.owned:
        try:
            if p.is_dir() and not p.is_symlink():
                _sh.rmtree(p)
            else:
                p.unlink()
        except OSError as e:
            failures.append((p, str(e)))
    return failures


def delete_library_all(root: Path) -> list[tuple[Path, str]]:
    """删除整个库目录（含 owned + foreign），但**保留软件全局文件**。

    - 若目录里没有 ``cabinet.json`` 等软件全局文件 → 等同 ``shutil.rmtree(root)``
    - 若有 → 逐项删除 ``owned + foreign``，并保留目录本体（避免连带 app_global
      一起被 rmtree 掉）

    返回失败列表 ``[(path, error_message), ...]``。
    """
    import shutil as _sh
    root = Path(root)
    failures: list[tuple[Path, str]] = []
    scan = scan_library_for_deletion(root)
    if not scan.app_global:
        # 干净删除：rmtree 整个目录
        try:
            _sh.rmtree(root, ignore_errors=False)
        except OSError as e:
            failures.append((root, str(e)))
        return failures
    # 含 app_global：逐项删除 owned + foreign，目录本体保留
    for p in scan.owned + scan.foreign:
        try:
            if p.is_dir() and not p.is_symlink():
                _sh.rmtree(p)
            else:
                p.unlink()
        except OSError as e:
            failures.append((p, str(e)))
    return failures




def import_settings_from_other_db(other_db_path: Path, keys: list[str]) -> dict[str, str]:
    """以只读方式打开另一个库的 db，读出指定 settings 项。

    - 失败返回空 dict（不抛异常）
    - 仅返回存在的键
    """
    import sqlite3
    out: dict[str, str] = {}
    if not other_db_path.is_file():
        return out
    try:
        uri = f"file:{other_db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            for k in keys:
                row = conn.execute(
                    "SELECT value FROM settings WHERE key=?", (k,),
                ).fetchone()
                if row and row[0] is not None:
                    out[k] = str(row[0])
        finally:
            conn.close()
    except Exception as e:
        log.warning("import_settings_from_other_db 失败：%s", e)
    return out


# =============================================================================
# helpers
# =============================================================================
def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = [
    "CabinetConfig",
    "LibraryHandle",
    "LibraryDeleteScan",
    "MAX_RECENT",
    "LIBRARY_MARKER",
    "resolve_library_paths",
    "is_library_dir",
    "is_empty_or_safe_for_library",
    "validate_library_path",
    "mark_as_library",
    "scan_library_for_deletion",
    "delete_library_owned_only",
    "delete_library_all",
    "import_settings_from_other_db",
]
