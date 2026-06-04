"""LibraryContext: manages the currently active repository and library instance.

Supports runtime switching between libraries registered in ``cabinet.json``.
For T1 (read-only single-library mode), ``switch()`` is a no-op.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..cabinet import CabinetConfig, LibraryHandle
from ..db import connect as db_connect
from ..library import Library
from ..repository import Repository
from ..utils import app_data_dir

log = logging.getLogger(__name__)


class LibraryContext:
    """Holds the currently active ``Repository`` + ``Library`` and supports
    discovering / switching between registered libraries.

    Usage::

        ctx = LibraryContext.from_default()
        # Access current repo / library
        repo = ctx.repo
        lib  = ctx.library

        # Discover available libraries
        libs = ctx.list_libraries()

        # Switch (no-op in T1 read-only mode)
        ctx.switch("My Library")
    """

    def __init__(self, config: CabinetConfig, db_override: Optional[Path] = None):
        self._config = config
        self._repo: Optional[Repository] = None
        self._library: Optional[Library] = None
        self._current_handle: Optional[LibraryHandle] = None
        self._db_override = db_override  # single-library mode (--db)
        self.client_name: str = "standalone"  # MCP client identifier for audit log
        self.write_permission: str = "disabled"  # disabled / session / permanent

    # ---- factory ----------------------------------------------------------

    @classmethod
    def from_default(cls, config_path: Optional[Path] = None) -> "LibraryContext":
        """Load from the default ``cabinet.json`` in the app data directory.

        ``config_path`` overrides the default cabinet.json location.
        """
        config = CabinetConfig.load(config_path)
        return cls(config)

    @classmethod
    def from_single_db(cls, db_path: Path, library_root: Optional[Path] = None) -> "LibraryContext":
        """Single-library mode: bind to a specific ``cabinet.db`` path.

        ``library_root`` defaults to ``db_path.parent / "library"``.
        """
        cfg = CabinetConfig._default()
        return cls(cfg, db_override=Path(db_path).resolve())

    # ---- properties -------------------------------------------------------

    @property
    def repo(self) -> Repository:
        if self._repo is None:
            self.load_default()
        assert self._repo is not None
        return self._repo

    @property
    def library(self) -> Library:
        if self._library is None:
            self.load_default()
        assert self._library is not None
        return self._library

    # ---- library discovery ------------------------------------------------

    def list_libraries(self) -> list[dict]:
        """Return all registered libraries from ``cabinet.json``.

        In single-library (--db) mode, returns a one-item list.

        TODO: switch to ``self._config.libraries`` after the
        ``recent_libraries`` → ``libraries`` refactoring (task #13 前置重构).
        """
        if self._db_override:
            label = self._db_override.parent.name or str(self._db_override)
            return [
                {
                    "name": label,
                    "path": str(self._db_override.parent),
                    "label": label,
                    "last_opened": "",
                    "is_current": True,
                }
            ]

        results: list[dict] = []
        for h in self._config.recent_libraries:  # ← 重构后改为 .libraries
            handle_path = h.path.resolve()
            is_current = (
                self._current_handle is not None
                and handle_path == self._current_handle.path.resolve()
            )
            results.append(
                {
                    "name": h.display_name,
                    "path": str(handle_path),
                    "label": h.label,
                    "last_opened": h.last_opened or "",
                    "is_current": is_current,
                }
            )
        return results

    # ---- switch -----------------------------------------------------------

    def switch(self, name: str) -> dict:
        """Switch to a library by its display name.

        In single-library (``--db``) mode, switching is not allowed.

        Returns:
            ``{"ok": bool, "library_name": str, "path": str, "error": str}``
        """
        if self._db_override:
            return {
                "ok": False,
                "library_name": "",
                "path": "",
                "error": "单库模式下不支持切换库",
            }

        # Find the target library
        match: Optional[LibraryHandle] = None
        for h in self._config.recent_libraries:  # TODO: → .libraries after refactoring
            if h.display_name == name:
                match = h
                break

        if match is None:
            return {
                "ok": False,
                "library_name": name,
                "path": "",
                "error": f"未找到名为 '{name}' 的库。可用 list_libraries 查看全部库。",
            }

        # If already on this library, no-op
        if (
            self._current_handle is not None
            and match.path.resolve() == self._current_handle.path.resolve()
        ):
            return {
                "ok": True,
                "library_name": match.display_name,
                "path": str(match.path),
            }

        # Close old connection
        try:
            if self._repo is not None and self._repo.conn is not None:
                self._repo.conn.close()
        except Exception:
            pass

        # Open new library
        db_path = match.path / "cabinet.db"
        if not db_path.exists():
            return {
                "ok": False,
                "library_name": match.display_name,
                "path": str(match.path),
                "error": f"库文件不存在：{db_path}",
            }

        try:
            conn = db_connect(db_path)
            self._repo = Repository(conn)
            self._library = Library(match.path / "library")
            self._current_handle = match
            log.info("Switched to library: %s (%s)", match.display_name, db_path)
        except Exception as exc:
            return {
                "ok": False,
                "library_name": match.display_name,
                "path": str(match.path),
                "error": f"无法打开库：{exc}",
            }

        return {
            "ok": True,
            "library_name": match.display_name,
            "path": str(match.path),
        }

    def load_default(self) -> None:
        """Open the first available library from the registry (or the single-db override)."""
        if self._repo is not None:
            return  # already loaded

        if self._db_override:
            db_path = self._db_override
            library_root = db_path.parent / "library"
        else:
            # TODO: switch to ``self._config.libraries`` after refactoring
            libs = self._config.recent_libraries  # ← 重构后改为 .libraries
            if not libs:
                raise RuntimeError(
                    "没有可用库。请先启动 LLM Cabinet 创建或打开一个库。"
                )
            h = libs[0]
            db_path = h.path / "cabinet.db"
            library_root = h.path / "library"
            self._current_handle = h

        conn = db_connect(db_path)
        self._repo = Repository(conn)
        self._library = Library(library_root)
        log.info("LibraryContext loaded: db=%s library=%s", db_path, library_root)
