"""仓库目录与文件存储策略。"""
from __future__ import annotations

import shutil
from pathlib import Path

from .utils import app_data_dir


def default_library_root() -> Path:
    p = app_data_dir() / "library"
    p.mkdir(parents=True, exist_ok=True)
    return p


class Library:
    """管理仓库根目录；提供 'copy' 模式下文件落地与解析。"""

    def __init__(self, root: Path | None = None):
        self.root: Path = Path(root) if root else default_library_root()
        self.root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: int) -> Path:
        d = self.root / f"project_{project_id}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def import_copy(self, project_id: int, src: Path) -> str:
        """把 src 复制进 project 目录，返回相对仓库根的路径（POSIX 风格）。"""
        src = Path(src)
        target_dir = self.project_dir(project_id)
        target = target_dir / src.name
        # 同名冲突：自动加序号
        i = 1
        while target.exists():
            target = target_dir / f"{src.stem}_{i}{src.suffix}"
            i += 1
        shutil.copy2(src, target)
        return target.relative_to(self.root).as_posix()

    def resolve(self, path: str, is_relative: bool) -> Path:
        """把存储的 path 还原为绝对路径。"""
        return (self.root / path) if is_relative else Path(path)

    def remove_relative(self, rel_path: str) -> None:
        """删除仓库内文件；忽略不存在错误。"""
        p = self.root / rel_path
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass
