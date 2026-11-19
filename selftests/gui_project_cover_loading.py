"""项目封面按需加载 GUI 回归：只用合成项目和内存图像，不连接数据库。"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from app.models import Project
from app.ui.project_card import ProjectModel
from selftests._common import T


def run(t: T) -> None:
    """验证全量换批失效和局部更新，同时保留旧的预加载调用方式。"""
    model = ProjectModel()
    model.set_columns([])
    projects = [Project(id=pid, title=f"合成项目 {pid}") for pid in range(1, 10001)]
    red = QPixmap(8, 8)
    red.fill(QColor("red"))
    blue = QPixmap(8, 8)
    blue.fill(QColor("blue"))
    calls: list[int | None] = []

    def load(project: Project) -> QPixmap | None:
        calls.append(project.id)
        return red if project.id == 1 else None

    def cover(row: int, column: int = 0) -> QPixmap | None:
        return model.data(model.index(row, column), ProjectModel.RoleCover)

    seeded = {3: blue}
    model.set_data(projects, seeded, {1: 5}, cover_loader=load)
    t.assert_eq("万条项目重置不读取封面", calls, [])
    t.assert_eq("项目数保持完整", model.rowCount(), 10000)
    t.assert_eq("读取项目ID", model.index(0, 0).data(ProjectModel.RoleId), 1)
    t.assert_eq("读取文件计数", model.index(0, 0).data(Qt.DisplayRole), "5")
    t.assert_eq("读取项目对象", model.index(0, 0).data(ProjectModel.RoleProject), projects[0])
    t.assert_eq("无效索引无封面", model.data(QModelIndex(), ProjectModel.RoleCover), None)
    t.assert_eq("其他角色不触发加载", calls, [])
    t.assert_true("首个可见项目加载封面", cover(0) is red)
    t.assert_eq("只加载请求的项目", calls, [1])
    t.assert_true("同项目跨列复用缓存", cover(0, 1) is red)
    t.assert_true("反复请求复用缓存", cover(0) is red)
    t.assert_eq("同批仅加载一次", calls, [1])
    t.assert_eq("空封面返回None", cover(1), None)
    t.assert_eq("空封面跨列仍为None", cover(1, 1), None)
    t.assert_eq("空结果也缓存", calls, [1, 2])
    t.assert_true("预加载封面直接使用", cover(2) is blue)
    t.assert_eq("预加载封面不再加载", calls, [1, 2])
    t.assert_eq("内部缓存不修改调用者字典", set(seeded), {3})

    changed: list[int] = []
    resets: list[bool] = []
    model.dataChanged.connect(lambda first, _last, _roles: changed.append(first.row()))
    model.modelReset.connect(lambda: resets.append(True))
    updated = replace(projects[0], title="局部更新标题")
    model.update_project(updated, None, 7)
    t.assert_eq("局部清除已有封面", cover(0), None)
    t.assert_eq("局部更新替换项目", model.project_at(0), updated)
    t.assert_eq("局部更新替换文件计数", model.index(0, 0).data(Qt.DisplayRole), "7")
    model.update_project(projects[1], blue, 2)
    t.assert_true("局部封面覆盖空缓存", cover(1) is blue)
    model.update_project(projects[3], None, 0)
    t.assert_eq("未加载项目可直接设置空封面", cover(3), None)
    model.update_project(projects[4], blue, 0)
    t.assert_true("未加载项目可直接设置封面", cover(4) is blue)
    t.assert_eq("局部更新后不调用加载器", calls, [1, 2])
    t.assert_eq("局部更新发出对应行通知", changed, [0, 1, 3, 4])
    t.assert_eq("局部更新不重置模型", resets, [])
    model.update_project(Project(id=20000), red, 9)
    t.assert_eq("缺失项目不发出更新通知", changed, [0, 1, 3, 4])

    # 同一项目 ID 在下一次刷新时必须重新取得结果，包括旧的 None。
    refreshed_calls: list[int | None] = []

    def reload(project: Project) -> QPixmap | None:
        refreshed_calls.append(project.id)
        return blue

    model.set_data(projects, {}, cover_loader=reload)
    t.assert_eq("换批仍然不立即加载", refreshed_calls, [])
    for row in (0, 1, 2, 3, 4):
        t.assert_true(f"换批清除项目{row + 1}旧缓存", cover(row) is blue)
    t.assert_eq("旧封面和空结果均失效", refreshed_calls, [1, 2, 3, 4, 5])
    t.assert_eq("旧加载器不再使用", calls, [1, 2])

    model.set_data([projects[0], Project(title="未保存项目")], {1: red}, {1: 1})
    t.assert_true("兼容旧的三个位置参数", cover(0) is red)
    t.assert_eq("没有ID不调用加载器", cover(1), None)
    model.set_data([projects[0]], {})
    t.assert_eq("省略加载器会清空旧加载器", cover(0), None)
    t.assert_eq("旧加载器调用数保持不变", refreshed_calls, [1, 2, 3, 4, 5])
    model.set_data([Project(title="未保存项目")], {}, cover_loader=load)
    t.assert_eq("懒加载模式没有ID也返回None", cover(0), None)
    t.assert_eq("没有ID不会触发加载", calls, [1, 2])


if __name__ == "__main__":
    application = QApplication.instance() or QApplication([])
    t = T()
    run(t)
    sys.exit(0 if t.report() else 1)
