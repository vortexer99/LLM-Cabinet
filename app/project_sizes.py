"""项目登记文件的体积快照与统计；本模块不连接数据库、不读取文件内容。"""
from __future__ import annotations

import hashlib
import json
import stat
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import FileItem
from .utils import OperationCancelled

SIZE_SETTING_KEY = "project_size_v1"


def files_fingerprint(files: Iterable[FileItem]) -> str:
    """登记成员或实际存储位置变化时失效；不为检测变化访问磁盘。"""
    return records_fingerprint((f.id or 0, f.path, bool(f.is_relative)) for f in files)


def records_fingerprint(records: Iterable[tuple[int, str, bool]]) -> str:
    """轻量查询只提供指纹所需列，无需构造完整 FileItem。"""
    rows = sorted(records)
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProjectSize:
    """一次完整统计的已知大小；缺失或不可访问项不按零字节声称完整。"""

    bytes: int
    missing: int
    checked_at: str
    fingerprint: str
    stale: bool = False

    def to_json(self) -> str:
        data = asdict(self)
        data.pop("stale")
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> ProjectSize | None:
        """损坏或旧格式缓存按未统计处理，避免影响项目列表。"""
        try:
            data = json.loads(raw)
            if (type(data["bytes"]) is not int or data["bytes"] < 0
                    or type(data["missing"]) is not int or data["missing"] < 0
                    or not isinstance(data["checked_at"], str)
                    or not isinstance(data["fingerprint"], str)):
                return None
            return cls(data["bytes"], data["missing"], data["checked_at"], data["fingerprint"])
        except (ValueError, KeyError, TypeError):
            return None


@dataclass(frozen=True)
class ProjectSizeSnapshot:
    """主线程准备的纯路径快照，可安全交给文件操作 worker。"""

    project_id: int
    fingerprint: str
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class ProjectSizeResult:
    project_id: int
    size: ProjectSize
    errors: tuple[str, ...]


def measure_project_sizes(
    snapshots: list[ProjectSizeSnapshot],
    progress: Callable[[int, int, str], None],
    is_cancelled: Callable[[], bool],
) -> list[ProjectSizeResult]:
    """只 stat 已登记文件；不递归目录，不读取内容，取消不返回半批结果。"""
    total = sum(len(snapshot.paths) for snapshot in snapshots)
    done = 0
    last_progress = 0.0
    results: list[ProjectSizeResult] = []
    for snapshot in snapshots:
        size = 0
        missing = 0
        errors: list[str] = []
        for path in snapshot.paths:
            if is_cancelled():
                raise OperationCancelled()
            try:
                metadata = path.stat()
                if not stat.S_ISREG(metadata.st_mode):
                    raise OSError("登记路径不是普通文件")
                size += metadata.st_size
            except (OSError, ValueError) as exc:
                missing += 1
                # 汇总明细设上限，避免大量失效链接占用过多内存。
                if len(errors) < 30:
                    errors.append(f"{path}：{exc}")
            done += 1
            now = time.monotonic()
            if done == 1 or done == total or now - last_progress >= 0.05:
                progress(done, total, path.name)
                last_progress = now
        if is_cancelled():
            raise OperationCancelled()
        results.append(ProjectSizeResult(
            snapshot.project_id,
            ProjectSize(size, missing, datetime.now(timezone.utc).isoformat(), snapshot.fingerprint),
            tuple(errors),
        ))
    return results
