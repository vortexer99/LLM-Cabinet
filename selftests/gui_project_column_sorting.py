"""项目全部列排序回归：真实表头鼠标操作，显式临时库及合成记录。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QItemSelectionModel, QPoint, QPersistentModelIndex, Qt
from PySide6.QtTest import QTest

from app.models import Field, FileItem, Project
from app.project_sizes import ProjectSize, files_fingerprint
from app.ui.project_card import EXTRA_COL_FILES, EXTRA_COL_SIZE, EXTRA_COL_UPDATED, ProjectModel
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _close_window, _window


def check_numeric_precision(t: T) -> None:
    """精度和非法值验证直接使用模型，不需要数据库或文件系统。"""
    model = ProjectModel()
    field = Field(id=91, name="合成精确数值", type="number")
    model.set_columns([field], include_extras=False)
    values = ["9007199254740992", "9007199254740993", "9007199254740992.1", "-0.25",
              "NaN", "Infinity", "-Infinity", "", "not-a-number"]
    model.set_data([Project(id=i, field_values={91: value}) for i, value in enumerate(values)], {})
    for order, expected in ((Qt.AscendingOrder, [3, 0, 2, 1]),
                            (Qt.DescendingOrder, [1, 2, 0, 3])):
        model.sort(0, order)
        actual = [model.project_at(row).id for row in range(model.rowCount())]
        t.assert_eq(f"Decimal精确比较且支持负小数 {order}", actual[:4], expected)
        t.assert_eq(f"非有限数非法数空值置后 {order}", set(actual[4:]), set(range(4, 9)))
    model.set_columns([], include_extras=False)
    t.assert_eq("模型删除排序列清空稳定key", model.sort_key, None)


def run(root: Path, t: T) -> None:
    """所有数据写入显式临时库；文件路径为合成登记，不读取用户文件。"""
    app = _app()
    win, repo = _window(root)
    try:
        # 把保护字段也显示出来，覆盖标题、长描述和标签的原始值排序。
        repo.conn.execute("UPDATE fields SET visible=1")
        repo.conn.commit()
        text_id = repo.add_field("排序测试文本", "text")
        number_id = repo.add_field("排序测试数字", "number")
        rating_id = repo.add_field("排序测试评分", "rating")
        date_id = repo.add_field("排序测试日期", "date")
        url_id = repo.add_field("排序测试URL", "url")
        titles = ["项目2", "项目10", "项目1", "", "项目3"]
        texts = ["值2", "值10", "值1", "", "值3"]
        numbers = ["-2.5", "10.25", "0.5", "", "invalid"]
        ratings = ["2", "10", "-1.5", "", "NaN"]
        dates = ["2026-01-01T01:00:00+08:00", "2026-01-01T00:00:00",
                 "2025-12-31T23:00:00Z", "", "2026-99-01"]
        updated = ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00+08:00",
                   "2025-12-31T23:00:00", "", "broken-date"]
        counts = [2, 10, 1, 0, 3]
        ids: list[int] = []
        for i in range(5):
            suffix = ("2", "10", "1", "", "3")[i]
            pid = repo.save_project(Project(
                title=titles[i], description_md=("共同前缀" * 40 + suffix) if suffix else "",
                tags=[f"tag{suffix}", "选中集"] if suffix else [],
                field_values={text_id: texts[i], number_id: numbers[i], rating_id: ratings[i],
                              date_id: dates[i], url_id: f"https://example.test/{suffix}" if suffix else ""},
            ))
            ids.append(pid)
            for j in range(counts[i]):
                repo.add_file(FileItem(project_id=pid, path=str(root / f"synthetic-{i}-{j}.txt")))
            repo.conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (updated[i], pid))
        repo.conn.commit()
        a, b, c, empty, invalid = ids
        repo.set_projects_favorite([b], True)
        snapshot = repo.project_size_files(ids)
        sizes = {pid: ProjectSize(size, 0, "2026-11-16T00:00:00+00:00", files_fingerprint(snapshot[pid]))
                 for pid, size in zip(ids[:4], (900, 2048, 5, 0))}
        repo.save_project_sizes(sizes)
        win.refresh_projects()
        win._set_view_mode("list")
        app.processEvents()
        model = win.proj_model
        selection = win.proj_view.selectionModel()
        selection.setCurrentIndex(model.index_of_id(a), QItemSelectionModel.ClearAndSelect)
        selection.select(model.index_of_id(c), QItemSelectionModel.Select)
        selected_ids = {a, c}
        persistent = QPersistentModelIndex(model.index_of_id(c))
        file_node = win.tbl_files.topLevelItem(0)

        def column(key: str) -> int:
            return next(col for col in range(model.columnCount()) if model.column_key(col) == key)

        def ordered() -> list[int]:
            return [model.project_at(row).id for row in range(model.rowCount())]

        def click(key: str) -> None:
            col = column(key)
            win.proj_table.scrollTo(model.index(0, col))
            app.processEvents()
            header = win.proj_table.horizontalHeader()
            point = QPoint(header.sectionViewportPosition(col) + header.sectionSize(col) // 2,
                           header.height() // 2)
            QTest.mouseClick(header.viewport(), Qt.LeftButton, pos=point)
            app.processEvents()

        # 每次切换新列首击必须升序，第二击降序；空/非法值两个方向都在末尾。
        cases = [
            ("title", [c, a, invalid, b], {empty}),
            (f"user:{text_id}", [c, a, invalid, b], {empty}),
            ("description", [c, a, invalid, b], {empty}),
            ("tags", [c, a, invalid, b], {empty}),
            (f"user:{url_id}", [c, a, invalid, b], {empty}),
            (f"user:{number_id}", [a, c, b], {empty, invalid}),
            (f"user:{rating_id}", [c, a, b], {empty, invalid}),
            (f"user:{date_id}", [a, c, b], {empty, invalid}),
            (EXTRA_COL_UPDATED, [b, c, a], {empty, invalid}),
            (EXTRA_COL_FILES, [empty, c, a, invalid, b], set()),
            (EXTRA_COL_SIZE, [empty, c, a, b], {invalid}),
        ]
        with patch.object(win, "_show_project", wraps=win._show_project) as preview:
            for key, ascending, missing in cases:
                for order, expected in ((Qt.AscendingOrder, ascending),
                                        (Qt.DescendingOrder, list(reversed(ascending)))):
                    click(key)
                    actual = ordered()
                    t.assert_eq(f"{key} 真实表头排序方向 {order}", model.sort_order, order)
                    t.assert_eq(f"{key} 稳定排序key {order}", model.sort_key, key)
                    t.assert_eq(f"{key} 原始值正确顺序 {order}", actual[:len(expected)], expected)
                    t.assert_eq(f"{key} 空非法值末尾 {order}", set(actual[len(expected):]), missing)
                    t.assert_eq(f"{key} 保留多选 {order}", set(win._selected_project_ids()), selected_ids)
                    t.assert_eq(f"{key} 保留当前项目 {order}", selection.currentIndex().data(model.RoleId), a)
                    t.assert_eq(f"{key} 保留持久索引 {order}", persistent.data(model.RoleId), c)
            t.assert_eq("全部列排序不重载预览", preview.call_count, 0)
            t.assert_true("全部列排序保留右侧文件节点", win.tbl_files.topLevelItem(0) is file_node)

        # 收藏符号只影响显示，不能改变原始标题排序。
        click("title")
        t.assert_eq("切回已排序过的新列仍首次升序", model.sort_order, Qt.AscendingOrder)
        t.assert_eq("收藏星标不改变自然标题次序", ordered(), [c, a, invalid, b, empty])

        click(f"user:{number_id}")
        click(f"user:{number_id}")
        with patch.object(win, "_show_project", wraps=win._show_project) as preview:
            win._on_tag_filter_changed("tag", "选中集")
            t.assert_eq("筛选刷新保留排序key", model.sort_key, f"user:{number_id}")
            t.assert_eq("筛选刷新保留降序", model.sort_order, Qt.DescendingOrder)
            t.assert_eq("筛选后仍数值降序且非法值末尾", ordered(), [b, c, a, invalid])
            t.assert_eq("当前项目仍命中筛选保留预览", preview.call_count, 0)

        old_column = column(f"user:{number_id}")
        all_fields = repo.list_fields()
        ordered_field_ids = [number_id] + [field.id for field in all_fields if field.id != number_id]
        repo.reorder_fields(ordered_field_ids)
        win.refresh_projects()
        new_column = column(f"user:{number_id}")
        t.assert_true("字段重排改变数字列位置", new_column != old_column)
        t.assert_eq("字段重排按稳定key保持排序", model.sort_key, f"user:{number_id}")
        t.assert_eq("字段重排保留降序", model.sort_order, Qt.DescendingOrder)
        t.assert_eq("字段重排后值顺序保持", ordered(), [b, c, a, invalid])
        t.assert_eq("表头排序指示迁移到新列", win.proj_table.horizontalHeader().sortIndicatorSection(), new_column)
        click(f"user:{number_id}")
        t.assert_eq("重排后点击同列切升序", model.sort_order, Qt.AscendingOrder)
        t.assert_eq("重排后点击同列结果正确", ordered(), [a, c, b, invalid])

        repo.conn.execute("UPDATE fields SET visible=0 WHERE id=?", (number_id,))
        repo.conn.commit()
        win.refresh_projects()
        t.assert_eq("隐藏当前排序列取消排序", model.sort_key, None)
        t.assert_true("隐藏排序列关闭指示", not win.proj_table.horizontalHeader().isSortIndicatorShown())
        click(f"user:{text_id}")
        t.assert_eq("隐藏后新列首次升序", model.sort_order, Qt.AscendingOrder)
        repo.delete_field(text_id)
        win.refresh_projects()
        t.assert_eq("删除当前排序列取消排序", model.sort_key, None)
        t.assert_true("删除排序列关闭指示", not win.proj_table.horizontalHeader().isSortIndicatorShown())
        before = ordered()
        model.sort(-1, Qt.DescendingOrder)
        model.sort(model.columnCount(), Qt.DescendingOrder)
        t.assert_eq("无效列不启用排序", model.sort_key, None)
        t.assert_eq("无效列不更改项目次序", ordered(), before)
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    t = T()
    check_numeric_precision(t)
    with tempfile.TemporaryDirectory(prefix="cabinet-column-sort-") as directory:
        run(Path(directory), t)
    sys.exit(0 if t.report() else 1)
