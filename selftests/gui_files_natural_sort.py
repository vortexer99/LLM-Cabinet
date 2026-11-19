"""文件名自然排序回归：树形/平铺、数字段、目录和原有排序偏好。

直接运行 ``.venv/Scripts/python.exe -B selftests/gui_files_natural_sort.py``。
使用 offscreen Qt 和最小文件面板，避免启动主窗口及其后台任务。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem

from selftests._common import T, closing_repos
from app.db import connect
from app.models import FileItem, Project
from app.repository import Repository
from app.ui.file_sorting import FileTreeItem, RAW_NAME_ROLE, natural_name_key
from app.ui.files_table_columns import COLUMNS
from app.ui.mw_files import FilesPanelMixin


class _Panel(FilesPanelMixin):
    """复用实际建树和排序入口，只替换依赖物理文件的大小读取。"""

    def __init__(self, repo: Repository, project_id: int) -> None:
        self.repo = repo
        self._current_project_id = project_id
        self.tbl_files = QTreeWidget()
        self.tbl_files.setHeaderLabels([column.label for column in COLUMNS])

    def _file_size_str(self, file: FileItem) -> str:
        return f"{file.ord} KB"

    def _resolve(self, file: FileItem) -> Path:
        """为拖放角色提供合成路径，排序测试无需读取文件。"""
        return Path(file.path)

    def _update_preview_file_navigation(self, *_args) -> None:
        """此最小面板不构建预览，翻页联动由独立主窗口回归覆盖。"""
        return


def _children(parent: QTreeWidget | QTreeWidgetItem) -> list[QTreeWidgetItem]:
    if isinstance(parent, QTreeWidget):
        return [parent.topLevelItem(i) for i in range(parent.topLevelItemCount())]
    return [parent.child(i) for i in range(parent.childCount())]


def _file_names(parent: QTreeWidget | QTreeWidgetItem) -> list[str]:
    return [item.data(0, RAW_NAME_ROLE) for item in _children(parent)
            if item.data(0, Qt.UserRole) != -1]


def _ids(tree: QTreeWidget) -> list[int]:
    return [item.data(0, Qt.UserRole) for item in _children(tree)]


def test_name_keys(t: T) -> None:
    cases = [
        ("连续编号", ["20.png", "10.png", "2.png", "11.png", "1.png"],
         ["1.png", "2.png", "10.png", "11.png", "20.png"]),
        ("图片多段数字", ["img10_2.png", "img2_11.png", "img2_2.png", "img2_1.png"],
         ["img2_1.png", "img2_2.png", "img2_11.png", "img10_2.png"]),
        ("中英文混排", ["图10.png", "a10.png", "图2.png", "A2.png", "图1.png"],
         ["A2.png", "a10.png", "图1.png", "图2.png", "图10.png"]),
        ("零前缀及大小写兜底", ["a2.png", "a02.png", "A2.png", "a002.png"],
         ["a002.png", "a02.png", "A2.png", "a2.png"]),
        ("空名称和数字开头", ["2a", "", "10a", "1a"], ["", "1a", "2a", "10a"]),
        ("括号内编号", ["xxxxx(1).jpg", "xxxxx(10).jpg", "xxxxx(2).jpg"],
         ["xxxxx(1).jpg", "xxxxx(2).jpg", "xxxxx(10).jpg"]),
    ]
    for label, names, expected in cases:
        t.assert_eq(label, sorted(names, key=natural_name_key), expected)
        t.assert_eq(f"{label}输入反转仍确定", sorted(reversed(names), key=natural_name_key), expected)


def _save_files(repo: Repository, pid: int) -> list[FileItem]:
    """同级与深层各有乱序文件，编号故意不与 ord 一致。"""
    files: list[FileItem] = []
    kinds = ["image", "video", "pdf", "doc", "code"]
    for subfolder in ("", "目录2", "目录2/层2"):
        for index, number in enumerate((20, 2, 11, 1, 10)):
            file = FileItem(
                project_id=pid, path=f"/unread-files/{subfolder}/{number}.png",
                subfolder=subfolder, kind=kinds[index], missing=index % 2 == 0,
                label=f"说明{number}", ord=index // 2,
                added_at=f"2026-11-{index + 1:02d}T10:00:00",
                is_relative=index % 2 == 0,
            )
            file.id = repo.add_file(file)
            files.append(file)
    return files


def test_tree(repo: Repository, pid: int, files: list[FileItem], t: T) -> None:
    panel = _Panel(repo, pid)
    panel._save_explicit_subfolders(pid, {
        "目录10", "目录1", "目录2/层10", "目录2/层1",
    })
    panel._set_tree_sort_state("name", False)
    panel._populate_files_tree(files)
    expected = ["1.png", "2.png", "10.png", "11.png", "20.png"]
    root_children = _children(panel.tbl_files)
    t.assert_eq("顶层目录自然排序且在文件前",
                [item.data(0, RAW_NAME_ROLE) for item in root_children[:3]],
                ["目录1", "目录2", "目录10"])
    folder2 = root_children[1]
    t.assert_eq("嵌套目录自然排序且在文件前",
                [item.data(0, RAW_NAME_ROLE) for item in _children(folder2)[:3]],
                ["层1", "层2", "层10"])
    nested = _children(folder2)[1]
    for label, parent in (("顶层", panel.tbl_files), ("子目录", folder2), ("深层", nested)):
        t.assert_eq(f"树形{label}忽略图标和缺失标记", _file_names(parent), expected)
    t.assert_true("缺失提示仍显示", any(item.text(0).startswith("⚠ ")
                                       for item in root_children[3:]))

    # 用实际表头入口切到降序，检查所有层级和目录优先规则。
    panel._on_files_tree_header_clicked(0)
    t.assert_eq("表头切换保存降序", panel._tree_sort_state(), ("name", True))
    for label, parent in (("顶层", panel.tbl_files), ("子目录", folder2), ("深层", nested)):
        t.assert_eq(f"树形{label}降序", _file_names(parent), list(reversed(expected)))
    t.assert_eq("降序仍保持目录先且目录升序",
                [item.data(0, RAW_NAME_ROLE) for item in _children(panel.tbl_files)[:3]],
                ["目录1", "目录2", "目录10"])

    panel._set_tree_sort_state("custom", False)
    panel._sort_files_tree()
    for label, parent, subfolder in (("顶层", panel.tbl_files, ""),
                                     ("子目录", folder2, "目录2"),
                                     ("深层", nested, "目录2/层2")):
        ordered = sorted((f for f in files if f.subfolder == subfolder),
                         key=lambda f: (f.ord, f.id))
        t.assert_eq(f"自定义{label}保持ord及id顺序", _file_names(parent),
                    [Path(f.path).name for f in ordered])
    panel.tbl_files.close()


def test_flat(repo: Repository, pid: int, files: list[FileItem], t: T) -> None:
    panel = _Panel(repo, pid)
    root_files = [file for file in files if not file.subfolder]
    panel._populate_files_flat(root_files)
    expected = ["1.png", "2.png", "10.png", "11.png", "20.png"]
    t.assert_eq("平铺加载名称升序偏好", _file_names(panel.tbl_files), expected)
    t.assert_true("平铺使用名称排序条目", all(isinstance(item, FileTreeItem)
                                             for item in _children(panel.tbl_files)))
    panel.tbl_files.sortByColumn(0, Qt.DescendingOrder)
    t.assert_eq("平铺名称降序忽略图标和缺失标记", _file_names(panel.tbl_files),
                list(reversed(expected)))

    # 同样的显示值交给原生 Qt 对照，验证所有非名称列沿用原来的排序行为。
    baseline = QTreeWidget()
    baseline.setColumnCount(len(COLUMNS))
    for item in _children(panel.tbl_files):
        reference = QTreeWidgetItem([item.text(col) for col in range(len(COLUMNS))])
        reference.setData(0, Qt.UserRole, item.data(0, Qt.UserRole))
        baseline.addTopLevelItem(reference)
    baseline.setSortingEnabled(True)
    for col in range(1, len(COLUMNS)):
        for order in (Qt.AscendingOrder, Qt.DescendingOrder):
            panel.tbl_files.sortByColumn(col, order)
            baseline.sortByColumn(col, order)
            t.assert_eq(f"平铺{COLUMNS[col].key}列{order.name}沿用Qt行为",
                        _ids(panel.tbl_files), _ids(baseline))
    baseline.close()
    panel.tbl_files.close()


def test_mixed_names_in_views(repo: Repository, pid: int, t: T) -> None:
    """数字兜底规则必须通过两种实际视图的原名角色生效。"""
    names = ["图10.png", "img10_2.png", "a2.png", "img2_11.png",
             "图2.png", "A2.png", "img2_2.png", "a002.png"]
    expected = ["a002.png", "A2.png", "a2.png", "img2_2.png",
                "img2_11.png", "img10_2.png", "图2.png", "图10.png"]
    files = [FileItem(id=i + 100, project_id=pid, path=name,
                      kind="image" if i % 2 else "other", missing=i % 3 == 0)
             for i, name in enumerate(names)]
    for mode in ("tree", "flat"):
        panel = _Panel(repo, pid)
        panel._set_tree_sort_state("name", False)
        if mode == "tree":
            panel._populate_files_tree(files)
        else:
            panel._populate_files_flat(files)
        t.assert_eq(f"{mode}中英文多数字及零前缀升序", _file_names(panel.tbl_files), expected)
        if mode == "tree":
            panel._on_files_tree_header_clicked(0)
        else:
            panel.tbl_files.sortByColumn(0, Qt.DescendingOrder)
        t.assert_eq(f"{mode}中英文多数字及零前缀降序", _file_names(panel.tbl_files),
                    list(reversed(expected)))
        panel.tbl_files.close()


def test_default_parenthesized_names(repo: Repository, t: T) -> None:
    """旧项目无排序偏好也按名称排序，括号编号不受导入 ord 影响。"""
    pid = repo.save_project(Project(title="无排序偏好的旧项目"))
    for order, name in enumerate(("xxxxx(1).jpg", "xxxxx(10).jpg", "xxxxx(2).jpg")):
        repo.add_file(FileItem(project_id=pid, path=name, ord=order, kind="image"))
    files = repo.list_files(pid)
    expected = ["xxxxx(1).jpg", "xxxxx(2).jpg", "xxxxx(10).jpg"]
    for mode in ("tree", "flat"):
        panel = _Panel(repo, pid)
        t.assert_eq(f"{mode}无偏好默认名称升序", panel._tree_sort_state(), ("name", False))
        (panel._populate_files_tree if mode == "tree" else panel._populate_files_flat)(files)
        t.assert_eq(f"{mode}默认括号编号自然顺序", _file_names(panel.tbl_files), expected)
        panel.tbl_files.close()
    panel = _Panel(repo, pid)
    panel._set_tree_sort_state("custom", False)
    panel._populate_files_tree(files)
    t.assert_eq("明确的自定义顺序仍保留", _file_names(panel.tbl_files),
                ["xxxxx(1).jpg", "xxxxx(10).jpg", "xxxxx(2).jpg"])
    panel.tbl_files.close()


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(["files-natural-sort-selftest"])
    app.setQuitOnLastWindowClosed(False)
    t = T()
    test_name_keys(t)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        repo = Repository(connect(Path(directory) / "cabinet.db"))
        with closing_repos(repo):
            pid = repo.save_project(Project(title="文件自然排序"))
            files = _save_files(repo, pid)
            test_tree(repo, pid, files, t)
            test_flat(repo, pid, files, t)
            test_mixed_names_in_views(repo, pid, t)
            test_default_parenthesized_names(repo, t)
    sys.exit(0 if t.report() else 1)
