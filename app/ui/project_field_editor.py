"""项目列表字段的单元格编辑，使用完整原始值并按记录 ID 保存。"""
from __future__ import annotations

import sqlite3
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QDate, QItemSelectionModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QStyledItemDelegate, QTableView, QWidget

from ..repository import Repository
from .project_card import ProjectModel


class ProjectTableView(QTableView):
    def mousePressEvent(self, event) -> None:
        """网格和刷新可能只选中首列，让该项目其他字段也响应 SelectedClicked。"""
        if event.button() == Qt.LeftButton and event.modifiers() == Qt.NoModifier:
            index = self.indexAt(event.position().toPoint())
            selection = self.selectionModel()
            if index.isValid() and not selection.isSelected(index) and selection.rowIntersectsSelection(index.row(), index.parent()):
                selection.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        super().mousePressEvent(event)


class ProjectFieldDelegate(QStyledItemDelegate):
    finished = Signal(str)

    def __init__(self, repo: Repository, table: QTableView):
        super().__init__(table)
        self.repo = repo
        self.table = table

    def createEditor(self, parent, option, index: QModelIndex) -> QWidget | None:
        kind, field = index.model().column_kind(index.column())
        if kind != "field" or len(self.table.selectionModel().selectedRows()) > 1:
            return None
        project = index.data(ProjectModel.RoleProject)
        original = self.repo.get_field_value(project, field)
        editor = QPlainTextEdit(parent) if field.type == "textarea" else QLineEdit(parent)
        editor.setProperty("projectId", project.id)
        editor.setProperty("originalValue", original)
        if isinstance(editor, QPlainTextEdit):
            editor.setPlainText(original)
        else:
            editor.setText(original)
            editor.selectAll()
        return editor

    def setEditorData(self, editor, index) -> None:
        """在创建时填入原值，避免模型刷新或展示格式覆盖正在编辑的内容。"""

    def setModelData(self, editor, model, index) -> None:
        _, field = model.column_kind(index.column())
        value = editor.toPlainText() if isinstance(editor, QPlainTextEdit) else editor.text()
        original = editor.property("originalValue")
        if value == original:
            return
        try:
            if value.strip():
                if field.type == "rating" and value.strip() not in ("0", "1", "2", "3", "4", "5"):
                    raise ValueError("评分请输入 0 到 5 的整数")
                if field.type == "number" and not Decimal(value.strip()).is_finite():
                    raise ValueError("请输入有效数字")
                if field.type == "date" and not QDate.fromString(value.strip(), "yyyy-MM-dd").isValid():
                    raise ValueError("日期格式应为 yyyy-MM-dd")
            self.repo.update_project_field(editor.property("projectId"), field, original, value)
        except InvalidOperation:
            self.finished.emit("字段未保存：请输入有效数字")
        except (ValueError, sqlite3.Error) as exc:
            self.finished.emit(f"字段未保存：{exc}")
        else:
            self.finished.emit(f"已保存「{field.name}」")
