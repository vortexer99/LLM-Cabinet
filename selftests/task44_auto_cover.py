"""task #44：导入自动封面，真实临时数据库和 copy/link 文件落地回归。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T, closing_repos
from app.db import connect
from app.import_preferences import apply_import_auto_cover
from app.importer import ImportOptions, import_folder_as_project, scan_folders
from app.library import Library
from app.models import FileItem, Project
from app.repository import Repository


def _folder(root: Path, name: str, names: list[str]) -> Path:
    folder = root / name
    for relative in names:
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test file")
    return folder


def _import(repo: Repository, library: Library, folder: Path, mode: str = "link"):
    return import_folder_as_project(
        repo, library, scan_folders([folder], repo)[0], ImportOptions(storage_mode=mode),
    )


def _cover(repo: Repository, pid: int) -> FileItem | None:
    project = repo.get_project(pid)
    return next((file for file in repo.list_files(pid)
                 if file.id == project.cover_file_id), None)


def main() -> bool:
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet_auto_cover_") as raw:
        tmp = Path(raw)
        repo = Repository(connect(tmp / "first.db"))
        other = Repository(connect(tmp / "second.db"))
        library = Library(tmp / "library")
        with closing_repos(repo, other):
            folder = _folder(tmp, "images", ["page10.png", "page2.png", "readme.txt"])
            result = _import(repo, library, folder)
            t.assert_eq("默认关闭", _cover(repo, result.project_id), None)
            old_pid = result.project_id
            repo.set_setting("import_auto_cover", "1")
            t.assert_eq("启用偏好不追溯旧项目", _cover(repo, old_pid), None)
            for mode in ("link", "copy"):
                result = _import(repo, library, folder, mode)
                cover = _cover(repo, result.project_id)
                t.assert_eq(f"{mode} 自然顺序 2 在 10 前", Path(cover.path).name, "page2.png")
                t.assert_eq(f"{mode} 路径模式", cover.is_relative, mode == "copy")
                t.assert_true(f"{mode} 文件真实可访问", library.resolve(cover.path, cover.is_relative).is_file())
                t.assert_eq(f"{mode} 无警告", result.warnings, [])
            result = _import(other, Library(tmp / "other_library"), folder)
            t.assert_eq("第二个库仍默认关闭", _cover(other, result.project_id), None)
            other.set_setting("import_auto_cover", "0")
            t.assert_eq("双库设置隔离", repo.get_setting("import_auto_cover"), "1")

            folder = _folder(tmp, "no_image", ["doc.pdf", "text.txt"])
            result = _import(repo, library, folder)
            t.assert_eq("无图片不设置", _cover(repo, result.project_id), None)

            # 扁平导出包里，物理名称与逻辑目录的顺序刻意不同。
            folder = _folder(tmp, "export", ["files/first.png", "files/second.png", "files/generated.png"])
            (folder / "project.json").write_text(json.dumps({
                "schema": "llm-cabinet/project-export@3", "project": {},
            }), encoding="utf-8")
            (folder / "files.json").write_text(json.dumps({"files": [
                {"id": 11, "exported_to": "files/first.png", "subfolder": "chapter10"},
                {"id": 12, "exported_to": "files/second.png", "subfolder": "chapter2"},
                {"id": 13, "exported_to": "files/generated.png", "origin": "generated"},
            ]}), encoding="utf-8")
            result = _import(repo, library, folder)
            t.assert_eq("按逻辑目录自然排序并排除衍生物", _cover(repo, result.project_id).subfolder, "chapter2")
            (folder / "project.json").write_text(json.dumps({
                "schema": "llm-cabinet/project-export@3", "project": {"cover_file_id": 13},
            }), encoding="utf-8")
            result = _import(repo, library, folder)
            cover = _cover(repo, result.project_id)
            t.assert_eq("保留恢复的衍生封面", cover.origin, "generated")
            t.assert_eq("保留恢复的封面文件", Path(cover.path).name, "generated.png")
            t.assert_eq("重复调用不覆盖已有封面", apply_import_auto_cover(repo, repo.get_project(result.project_id)), False)

            folder = _folder(tmp, "partial", ["page1.png", "page2.png"])
            add_file = repo.add_file

            def fail_first(file: FileItem) -> int:
                if Path(file.path).name == "page1.png":
                    raise RuntimeError("模拟登记失败")
                return add_file(file)

            with patch.object(repo, "add_file", side_effect=fail_first):
                result = _import(repo, library, folder)
            t.assert_eq("仅挑选成功入库图片", Path(_cover(repo, result.project_id).path).name, "page2.png")
            t.assert_eq("登记失败仍汇报成功文件", result.n_files, 1)
            t.assert_eq("登记失败汇总一条警告", len(result.warnings), 1)

            copy_file = library.import_copy

            def fail_copy(pid: int, src: Path) -> str:
                if src.name == "page1.png":
                    raise OSError("模拟复制失败")
                return copy_file(pid, src)

            with patch.object(library, "import_copy", side_effect=fail_copy):
                result = _import(repo, library, folder, "copy")
            t.assert_eq("仅挑选复制成功图片", Path(_cover(repo, result.project_id).path).name, "page2.png")
            t.assert_eq("复制失败仍汇报成功文件", result.n_files, 1)

            project = Project(title="缺失及衍生物")
            project.id = repo.save_project(project)
            for name, origin, missing in [("0.png", "generated", False), ("1.png", "user", True), ("2.png", "user", False)]:
                fid = repo.add_file(FileItem(project_id=project.id, path=name, kind="image", origin=origin))
                if missing:
                    repo.set_file_missing(fid, True)
            t.assert_true("过滤缺失和衍生物后成功设置", apply_import_auto_cover(repo, project))
            t.assert_eq("仅选择可用用户图片", _cover(repo, project.id).path, "2.png")

            save_project = repo.save_project

            def fail_cover_save(project: Project) -> int:
                if project.cover_file_id is not None:
                    raise RuntimeError("模拟封面保存失败")
                return save_project(project)

            with patch.object(repo, "save_project", side_effect=fail_cover_save):
                result = _import(repo, library, folder)
            t.assert_eq("封面保存失败仍保留导入文件计数", result.n_files, 2)
            t.assert_eq("封面保存失败仍保留数据库文件", len(repo.list_files(result.project_id)), 2)
            t.assert_true("封面保存失败汇总警告", any("自动设置导入封面失败" in warning for warning in result.warnings))
            t.assert_eq("封面保存失败不留下数据库封面", _cover(repo, result.project_id), None)
            failed_project = repo.get_project(result.project_id)
            with patch.object(repo, "save_project", side_effect=fail_cover_save):
                try:
                    apply_import_auto_cover(repo, failed_project)
                except RuntimeError:
                    t.assert_true("共享 helper 向调用方抛出保存失败", True)
                else:
                    t.assert_true("共享 helper 向调用方抛出保存失败", False)
            t.assert_eq("保存失败还原内存封面", failed_project.cover_file_id, None)
    return t.report()


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
