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
import shutil
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
        """全新 / 损坏后的默认状态：把 ``%APPDATA%/LLMCabinet`` 当作默认库登记。"""
        default_root = app_data_dir()
        h = LibraryHandle(
            path=default_root,
            label="(默认库)",
            last_opened=_now_iso(),
        )
        return cls(active_library=default_root, recent_libraries=[h])

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
        """从最近列表移除（不动磁盘）。默认库永不移除。"""
        lib_path = Path(lib_path).resolve()
        if lib_path == app_data_dir().resolve():
            return  # 默认库不动
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
        """保留最近 MAX_RECENT 项，但保证默认库始终在列表里。"""
        default_path = app_data_dir().resolve()
        default_in_list = any(
            h.path.resolve() == default_path for h in self.recent_libraries
        )
        result = self.recent_libraries[:MAX_RECENT]
        if default_in_list and not any(
            h.path.resolve() == default_path for h in result
        ):
            # 默认库被截断了：把列表收紧到 MAX_RECENT - 1，再补默认库到末尾
            result = self.recent_libraries[:MAX_RECENT - 1]
            for h in self.recent_libraries:
                if h.path.resolve() == default_path:
                    result.append(h)
                    break
        return result


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


def mark_as_library(root: Path) -> None:
    """在库根目录写入 ``.llm-cabinet`` 标记文件。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / LIBRARY_MARKER).touch(exist_ok=True)


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
    "MAX_RECENT",
    "LIBRARY_MARKER",
    "resolve_library_paths",
    "is_library_dir",
    "is_empty_or_safe_for_library",
    "mark_as_library",
    "import_settings_from_other_db",
]
