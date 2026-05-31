"""项目数据模型 + 网格卡片委托。

ProjectModel 是动态多列模型：每个列对应一个 fields 表中的可见字段，按 ord 顺序。
+ 额外两列固定在最后：标签 / 文件数（这两个不是 fields，但常用）。

网格视图（QListView IconMode）使用 ProjectCardDelegate 自定义绘制。
列表视图（QTableView）按列原生显示文本。
"""
from __future__ import annotations

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QRect,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..models import Field, Project
from ..utils import utc_to_local_str

# 网格卡片
CARD_W = 168
CARD_H = 240
COVER_H = 168
PAD = 10
RADIUS = 8

# 附加列（不属于 fields，但常用）
EXTRA_COL_FILES = "__files__"
EXTRA_COL_UPDATED = "__updated__"

EXTRA_COL_NAMES = {
    EXTRA_COL_FILES: "文件数",
    EXTRA_COL_UPDATED: "更新时间",
}


class ProjectModel(QAbstractTableModel):
    RoleId = Qt.UserRole + 1
    RoleProject = Qt.UserRole + 2
    RoleCover = Qt.UserRole + 3

    def __init__(self):
        super().__init__()
        self._projects: list[Project] = []
        self._covers: dict[int, QPixmap] = {}
        self._file_counts: dict[int, int] = {}
        # 列定义：list of (kind, payload)
        # kind == "field":  payload = Field
        # kind == "extra":  payload = EXTRA_COL_* 常量
        self._cols: list[tuple[str, object]] = []

    # ---- 列定义 ----
    def set_columns(self, fields: list[Field], include_extras: bool = True) -> None:
        cols: list[tuple[str, object]] = []
        for f in fields:
            cols.append(("field", f))
        if include_extras:
            cols.append(("extra", EXTRA_COL_FILES))
            cols.append(("extra", EXTRA_COL_UPDATED))
        self.beginResetModel()
        self._cols = cols
        self.endResetModel()

    def column_kind(self, col: int) -> tuple[str, object] | None:
        if 0 <= col < len(self._cols):
            return self._cols[col]
        return None

    def column_key(self, col: int) -> str:
        """返回列的稳定 key，用于持久化可见列。
        - field: 系统字段返回 f.key（如 'title'/'tags'），用户字段返回 'user:<id>'
        - extra: 返回 __files__ / __updated__
        """
        ck = self.column_kind(col)
        if ck is None:
            return ""
        kind, payload = ck
        if kind == "extra":
            return str(payload)
        f: Field = payload  # type: ignore
        if f.is_system and f.key:
            return f.key
        return f"user:{f.id}"

    def column_label(self, col: int) -> str:
        ck = self.column_kind(col)
        if ck is None:
            return ""
        kind, payload = ck
        if kind == "extra":
            return EXTRA_COL_NAMES.get(str(payload), "")
        f: Field = payload  # type: ignore
        return f.name

    # ---- 数据 ----
    def set_data(
        self,
        projects: list[Project],
        covers: dict[int, QPixmap],
        file_counts: dict[int, int] | None = None,
    ) -> None:
        self.beginResetModel()
        self._projects = projects
        self._covers = covers
        self._file_counts = file_counts or {}
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._projects)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else max(1, len(self._cols))

    def headerData(self, section: int, orient: Qt.Orientation, role: int = Qt.DisplayRole):
        if orient == Qt.Horizontal and role == Qt.DisplayRole:
            return self.column_label(section) or ""
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        p = self._projects[index.row()]

        if role == self.RoleId:
            return p.id
        if role == self.RoleProject:
            return p
        if role == self.RoleCover:
            return self._covers.get(p.id) if p.id is not None else None

        col = index.column()
        ck = self.column_kind(col)
        if ck is None:
            return None
        kind, payload = ck

        if kind == "field":
            f: Field = payload  # type: ignore
            return self._field_data(p, f, role)
        if kind == "extra":
            return self._extra_data(p, str(payload), role)
        return None

    def _field_data(self, p: Project, f: Field, role: int):
        # 取原始值
        v = ""
        if f.is_system:
            if f.key == "title":
                v = p.title or "(未命名)"
            elif f.key == "author":
                v = p.author
            elif f.key == "date":
                v = p.date
            elif f.key == "source_url":
                v = p.source_url
            elif f.key == "rating":
                v = ("★" * p.rating + "☆" * (5 - p.rating)) if p.rating > 0 else ""
            elif f.key == "description":
                v = (p.description_md or "").strip().replace("\n", " ")[:120]
            elif f.key == "tags":
                v = "  ".join(f"#{t}" for t in p.tags)
        else:
            if f.id is not None:
                raw = p.field_values.get(f.id, "")
                if f.type == "rating":
                    try:
                        r = int(raw) if raw else 0
                    except ValueError:
                        r = 0
                    v = ("★" * r + "☆" * (5 - r)) if r > 0 else ""
                else:
                    v = raw

        if role == Qt.DisplayRole:
            return v
        if role == Qt.TextAlignmentRole:
            if f.type in ("rating", "number"):
                return int(Qt.AlignCenter)
        if role == Qt.ForegroundRole:
            if f.type == "rating" and v:
                return QColor("#f5a623")
            if f.type == "url" and v:
                return QColor("#74c0fc")
            if f.type == "tags" and v:
                return QColor("#74c0fc")
        return None

    def _extra_data(self, p: Project, key: str, role: int):
        if role == Qt.DisplayRole:
            if key == EXTRA_COL_FILES:
                return str(self._file_counts.get(p.id, 0)) if p.id is not None else ""
            if key == EXTRA_COL_UPDATED:
                return utc_to_local_str(p.updated_at)
        if role == Qt.TextAlignmentRole:
            if key == EXTRA_COL_FILES:
                return int(Qt.AlignCenter)
        return None

    # ---- helpers ----
    def project_at(self, row: int) -> Project | None:
        return self._projects[row] if 0 <= row < len(self._projects) else None

    def index_of_id(self, pid: int) -> QModelIndex:
        for i, p in enumerate(self._projects):
            if p.id == pid:
                return self.index(i, 0)
        return QModelIndex()


# ===================================================================== delegate
class ProjectCardDelegate(QStyledItemDelegate):
    """网格卡片：封面 + 标题 + 第一个可见非标题字段 + 评分（若有）。"""

    def sizeHint(self, _opt: QStyleOptionViewItem, _idx: QModelIndex) -> QSize:
        return QSize(CARD_W, CARD_H)

    def paint(self, painter: QPainter, opt: QStyleOptionViewItem, idx: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        p: Project = idx.data(ProjectModel.RoleProject)
        cover: QPixmap | None = idx.data(ProjectModel.RoleCover)

        rect = opt.rect.adjusted(6, 6, -6, -6)

        selected = opt.state & QStyle.State_Selected
        hovered = opt.state & QStyle.State_MouseOver
        bg = QColor("#2b3a55") if selected else QColor("#25262b")
        border = QColor("#4dabf7") if (selected or hovered) else QColor("#2c2e33")
        painter.setPen(QPen(border, 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, RADIUS, RADIUS)

        # 封面
        cover_rect = QRect(rect.left() + PAD, rect.top() + PAD,
                           rect.width() - 2 * PAD, COVER_H)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#1a1b1e"))
        painter.drawRoundedRect(cover_rect, 4, 4)
        if cover and not cover.isNull():
            scaled = cover.scaled(cover_rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = cover_rect.x() + (cover_rect.width() - scaled.width()) // 2
            y = cover_rect.y() + (cover_rect.height() - scaled.height()) // 2
            painter.save()
            painter.setClipRect(cover_rect)
            painter.drawPixmap(x, y, scaled)
            painter.restore()
        else:
            painter.setPen(QColor("#495057"))
            f = QFont(painter.font())
            f.setPointSize(28)
            painter.setFont(f)
            painter.drawText(cover_rect, Qt.AlignCenter, "📁")

        # 文字
        text_top = cover_rect.bottom() + 8
        text_rect = QRect(rect.left() + PAD, text_top,
                          rect.width() - 2 * PAD, rect.bottom() - text_top - 4)

        f = QFont(painter.font())
        f.setPointSize(10)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor("#e9ecef"))
        fm = QFontMetrics(f)
        title = fm.elidedText(p.title or "(未命名)", Qt.ElideRight, text_rect.width())
        painter.drawText(text_rect.left(), text_rect.top() + fm.ascent(), title)

        # 副标题：取第一个可见非标题字段（model 第 1 列）的显示值
        subtitle = ""
        model = idx.model()
        if model is not None and model.columnCount() > 1:
            sib = model.index(idx.row(), 1)
            subtitle = sib.data(Qt.DisplayRole) or ""

        f.setPointSize(9)
        f.setBold(False)
        painter.setFont(f)
        painter.setPen(QColor("#adb5bd"))
        fm = QFontMetrics(f)
        if subtitle:
            subtitle = fm.elidedText(str(subtitle), Qt.ElideRight, text_rect.width())
            painter.drawText(text_rect.left(), text_rect.top() + 22 + fm.ascent(), subtitle)

        if p.rating > 0:
            painter.setPen(QColor("#f5a623"))
            painter.drawText(
                text_rect.left(),
                text_rect.bottom() - 4,
                "★" * p.rating + "☆" * (5 - p.rating),
            )

        painter.restore()
