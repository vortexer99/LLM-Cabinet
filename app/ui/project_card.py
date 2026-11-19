"""项目数据模型 + 网格卡片委托。

ProjectModel 是动态多列模型：每个列对应一个 fields 表中的可见字段，按 ord 顺序。
+ 额外列固定在最后：文件数 / 更新时间 / 项目体积。

网格视图（QListView IconMode）使用 ProjectCardDelegate 自定义绘制。
列表视图（QTableView）按列原生显示文本。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QMimeData,
    QPointF,
    QRect,
    QSize,
    Signal,
    Qt,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolTip

from ..models import Field, Project
from ..project_sizes import ProjectSize
from ..utils import human_size, utc_to_local_str
from .dnd import PROJECT_IDS_MIME
from .file_sorting import natural_name_key
from .palette import current as _current_palette

# 网格卡片
CARD_W = 168
CARD_H = 240
COVER_H = 168
PAD = 10
RADIUS = 8

# 附加列（不属于 fields，但常用）
EXTRA_COL_FILES = "__files__"
EXTRA_COL_UPDATED = "__updated__"
EXTRA_COL_SIZE = "__size__"

EXTRA_COL_NAMES = {
    EXTRA_COL_FILES: "文件数",
    EXTRA_COL_UPDATED: "更新时间",
    EXTRA_COL_SIZE: "项目体积",
}


class ProjectModel(QAbstractTableModel):
    RoleId = Qt.UserRole + 1
    RoleProject = Qt.UserRole + 2
    RoleCover = Qt.UserRole + 3
    RoleFavorite = Qt.UserRole + 4
    RoleSizeBytes = Qt.UserRole + 5

    def __init__(self):
        super().__init__()
        self._projects: list[Project] = []
        self._covers: dict[int, QPixmap | None] = {}
        self._cover_loader: Callable[[Project], QPixmap | None] | None = None
        self._file_counts: dict[int, int] = {}
        self._favorite_ids: set[int] = set()
        self._project_sizes: dict[int, ProjectSize] = {}
        self.sort_key: str | None = None
        self.sort_order = Qt.AscendingOrder
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
            cols.append(("extra", EXTRA_COL_SIZE))
        self.beginResetModel()
        self._cols = cols
        if self.sort_key not in {self.column_key(c) for c in range(len(cols))}:
            self.sort_key = None
        self._sort_rows()
        self.endResetModel()

    def column_kind(self, col: int) -> tuple[str, object] | None:
        if 0 <= col < len(self._cols):
            return self._cols[col]
        return None

    def column_key(self, col: int) -> str:
        """返回列的稳定 key，用于持久化可见列。
        - field: 带 key 的字段返回 f.key（如 'title'/'tags'/'author'），其余返回 'user:<id>'
        - extra: 返回 __files__ / __updated__ / __size__
        """
        ck = self.column_kind(col)
        if ck is None:
            return ""
        kind, payload = ck
        if kind == "extra":
            return str(payload)
        f: Field = payload  # type: ignore
        # 带 key 的字段（标题 / 描述 / 标签 / 作者 / 日期 / 评分 / 来源等）
        # 用 key 作为稳定标识符（用户字段 fid 跨库迁移会变，不稳定）
        if f.key:
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
    def set_favorite_ids(self, ids: set[int]) -> None:
        """更新星标不重置模型，保留当前选中项及媒体播放。"""
        changed = self._favorite_ids ^ ids
        self._favorite_ids = set(ids)
        for row, project in enumerate(self._projects):
            if project.id in changed:
                self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))

    def set_data(
        self,
        projects: list[Project],
        covers: dict[int, QPixmap],
        file_counts: dict[int, int] | None = None,
        cover_loader: Callable[[Project], QPixmap | None] | None = None,
        project_sizes: dict[int, ProjectSize] | None = None,
    ) -> None:
        self.beginResetModel()
        self._projects = list(projects)
        # 每轮数据使用独立缓存；键存在即代表已加载，空封面也只查询一次。
        self._covers = dict(covers)
        self._cover_loader = cover_loader
        self._file_counts = file_counts or {}
        self._project_sizes = dict(project_sizes or {})
        self._sort_rows()
        self.endResetModel()

    def update_project_sizes(self, sizes: dict[int, ProjectSize | None]) -> None:
        """局部替换缓存并保持当前排序；不重置模型或读取封面。"""
        for pid, size in sizes.items():
            if size is None:
                self._project_sizes.pop(pid, None)
            else:
                self._project_sizes[pid] = size
            index = self.index_of_id(pid)
            if index.isValid():
                self.dataChanged.emit(index, self.index(index.row(), self.columnCount() - 1))
        if self.sort_key == EXTRA_COL_SIZE:
            self._apply_sort()

    def _sort_value(self, project: Project, column: int) -> tuple | Decimal | datetime | int | None:
        """按原始数据和字段类型比较，星标、单位和截断文案不参与排序。"""
        kind, field = self.column_kind(column)
        if kind == "extra":
            if field == EXTRA_COL_SIZE:
                size = self._project_sizes.get(project.id)
                return size.bytes if size is not None else None
            if field == EXTRA_COL_FILES:
                return self._file_counts.get(project.id, 0)
            value = project.updated_at
            field_type = "date"
        else:
            field_type = field.type
            if field.is_required and field.key == "title":
                value = project.title
            elif field.is_required and field.key == "description":
                value = project.description_md
            elif field.is_required and field.key == "tags":
                value = "\n".join(sorted(project.tags, key=natural_name_key))
            else:
                value = project.field_values.get(field.id, "")
        value = str(value or "").strip()
        if not value:
            return None
        if field_type in ("number", "rating"):
            try:
                number = Decimal(value)
                return number if number.is_finite() else None
            except InvalidOperation:
                return None
        if field_type == "date":
            try:
                date = datetime.fromisoformat(value.replace("/", "-"))
                # 老记录无时区；与带时区 ISO 时间统一按 UTC 比较。
                return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)
            except (ValueError, OverflowError):
                return None
        return natural_name_key(value)

    def _sort_rows(self) -> None:
        """各列按类型排序，空值及无法解析的值在升降序中都放末尾。"""
        column = next((c for c in range(len(self._cols)) if self.column_key(c) == self.sort_key), None)
        if column is None:
            return
        known, unknown = [], []
        for project in self._projects:
            value = self._sort_value(project, column)
            if value is None:
                unknown.append(project)
            else:
                known.append((value, project))
        known.sort(key=lambda item: item[0], reverse=self.sort_order == Qt.DescendingOrder)
        self._projects = [project for _, project in known] + unknown

    def _apply_sort(self) -> None:
        self.layoutAboutToBeChanged.emit()
        old_indexes = self.persistentIndexList()
        identities = [(self._projects[index.row()].id, index.column()) for index in old_indexes]
        self._sort_rows()
        rows = {p.id: row for row, p in enumerate(self._projects)}
        self.changePersistentIndexList(
            old_indexes, [self.index(rows[pid], col) for pid, col in identities],
        )
        self.layoutChanged.emit()

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        """按列类型排序，持久索引随行迁移以保留多选和当前项。"""
        if self.column_kind(column) is None:
            return
        self.sort_key = self.column_key(column)
        self.sort_order = order
        self._apply_sort()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._projects)

    def update_project(self, project: Project, cover: QPixmap | None, file_count: int) -> None:
        """局部更新项目封面与计数，不重置模型或触发重新选择项目。"""
        index = self.index_of_id(project.id)
        if not index.isValid():
            return
        self._projects[index.row()] = project
        size = self._project_sizes.get(project.id)
        if size is not None and self._file_counts.get(project.id, 0) != file_count:
            self._project_sizes[project.id] = replace(size, stale=True)
        # 局部更新提供的是最终结果，包括空封面，不能再回退到旧加载器。
        self._covers[project.id] = cover
        self._file_counts[project.id] = file_count
        self.dataChanged.emit(index, self.index(index.row(), self.columnCount() - 1))
        if self.sort_key is not None:
            self._apply_sort()

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
            if p.id is None:
                return None
            if p.id not in self._covers and self._cover_loader is not None:
                self._covers[p.id] = self._cover_loader(p)
            return self._covers.get(p.id)
        if role == self.RoleFavorite:
            return p.id in self._favorite_ids
        if role == self.RoleSizeBytes:
            size = self._project_sizes.get(p.id)
            return size.bytes if size is not None else None

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

    def flags(self, index: QModelIndex):
        flags = super().flags(index)
        if index.isValid():
            flags |= Qt.ItemIsDragEnabled
        return flags

    def mimeTypes(self) -> list[str]:  # noqa: N802 (Qt 命名)
        return [PROJECT_IDS_MIME]

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:  # noqa: N802
        ids: list[int] = []
        seen: set[int] = set()
        for idx in indexes:
            if not idx.isValid():
                continue
            p = self.project_at(idx.row())
            if p is None or p.id is None or p.id in seen:
                continue
            ids.append(p.id)
            seen.add(p.id)

        mime = QMimeData()
        mime.setData(PROJECT_IDS_MIME, ",".join(str(pid) for pid in ids).encode("utf-8"))
        if ids:
            mime.setText(f"{len(ids)} 个项目")
        return mime

    def supportedDragActions(self):  # noqa: N802
        return Qt.CopyAction

    def _field_data(self, p: Project, f: Field, role: int):
        # 取原始值
        # task #20 schema v4 起：除受保护字段（title/description/tags）外，
        # 所有字段值（含老 author/date/source_url/rating）统一在 p.field_values。
        v = ""
        if f.is_required:
            if f.key == "title":
                v = p.title or "(未命名)"
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
            if f.is_title and p.id in self._favorite_ids:
                return "★ " + v
            return v
        if role == Qt.ToolTipRole and f.type == "url" and v:
            return f"双击在默认浏览器中打开链接\n{v}"
        if role == Qt.TextAlignmentRole:
            if f.type in ("rating", "number"):
                return int(Qt.AlignCenter)
        if role == Qt.ForegroundRole:
            pal = _current_palette()
            if f.type == "rating" and v:
                return QColor(pal.warn)
            if f.type == "url" and v:
                return QColor(pal.accent)
            if f.type == "tags" and v:
                return QColor(pal.accent)
        return None

    def _extra_data(self, p: Project, key: str, role: int):
        if key == EXTRA_COL_SIZE:
            size = self._project_sizes.get(p.id)
            if role == Qt.DisplayRole:
                if size is None:
                    return "未统计"
                text = human_size(size.bytes)
                if size.missing:
                    text = f"已知 {text}（缺失 {size.missing}）"
                if size.stale:
                    text += "（待更新）"
                return text
            if role == Qt.ToolTipRole:
                if size is None:
                    return "右键项目 → 更新项目体积，统计已登记的链接和仓储文件。"
                lines = [f"已知大小：{size.bytes:,} 字节", f"缺失或不可访问：{size.missing} 个",
                         f"统计时间：{utc_to_local_str(size.checked_at)}"]
                if size.stale:
                    lines.append("文件登记已变化，请右键更新项目体积。")
                lines.append("缓存不会自动跟踪磁盘上的变化；外部修改后请手动更新。")
                return "\n".join(lines)
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignRight | Qt.AlignVCenter)
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

    favorite_toggled = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._view = parent
        self._viewport = parent.viewport() if parent is not None else None
        self._pressed_favorite: int | None = None
        if parent is not None:
            parent.viewport().installEventFilter(self)

    @staticmethod
    def favorite_rect(rect: QRect) -> QRect:
        """星标点击区和绘制区域共享坐标，随卡片滚动。"""
        return QRect(rect.right() - 44, rect.top() + 12, 28, 28)

    def eventFilter(self, obj, event) -> bool:
        # 在视图处理鼠标前接管星标，避免收藏误触发选中、拖拽或双击编辑。
        if self._view is not None and obj is self._viewport:
            kind = event.type()
            if kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
                        QEvent.MouseButtonDblClick, QEvent.MouseMove, QEvent.ToolTip):
                pos = event.pos() if kind == QEvent.ToolTip else event.position().toPoint()
                index = self._view.indexAt(pos)
                hit = index.isValid() and self.favorite_rect(self._view.visualRect(index)).contains(pos)
                if kind == QEvent.ToolTip and hit:
                    text = "取消收藏" if index.data(ProjectModel.RoleFavorite) else "收藏项目"
                    QToolTip.showText(event.globalPos(), text, self._view)
                    return True
                if kind == QEvent.MouseMove and self._pressed_favorite is not None:
                    return True
                if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton and hit:
                    self._pressed_favorite = index.data(ProjectModel.RoleId)
                    return True
                if kind == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton and hit:
                    self._pressed_favorite = None
                    return True
                if kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                    pressed = self._pressed_favorite
                    self._pressed_favorite = None
                    if pressed is not None:
                        if hit and index.data(ProjectModel.RoleId) == pressed:
                            self.favorite_toggled.emit(pressed)
                        return True
                    if hit:
                        return True
        return super().eventFilter(obj, event)

    def sizeHint(self, _opt: QStyleOptionViewItem, _idx: QModelIndex) -> QSize:
        return QSize(CARD_W, CARD_H)

    def paint(self, painter: QPainter, opt: QStyleOptionViewItem, idx: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        p: Project = idx.data(ProjectModel.RoleProject)
        cover: QPixmap | None = idx.data(ProjectModel.RoleCover)

        rect = opt.rect.adjusted(6, 6, -6, -6)

        # task #34：颜色统一走色板（浅色单主题）
        pal = _current_palette()
        selected = opt.state & QStyle.State_Selected
        hovered = opt.state & QStyle.State_MouseOver
        bg = QColor(pal.select_bg) if selected else QColor(pal.bg1)
        border = QColor(pal.accent) if (selected or hovered) else QColor(pal.border)
        painter.setPen(QPen(border, 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, RADIUS, RADIUS)

        # 封面
        cover_rect = QRect(rect.left() + PAD, rect.top() + PAD,
                           rect.width() - 2 * PAD, COVER_H)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(pal.bg2))
        painter.drawRoundedRect(cover_rect, 4, 4)
        if cover and not cover.isNull():
            # task #33：cover 是 cover_cache 按封面区逻辑尺寸适配好的缩略图
            # （带 devicePixelRatio），按自然逻辑尺寸居中绘制即可，
            # paint 热点不再做 SmoothTransformation 实时缩放
            dpr = cover.devicePixelRatio() or 1.0
            cw = cover.width() / dpr
            ch = cover.height() / dpr
            x = cover_rect.x() + (cover_rect.width() - cw) / 2
            y = cover_rect.y() + (cover_rect.height() - ch) / 2
            painter.save()
            painter.setClipRect(cover_rect)
            painter.drawPixmap(QPointF(x, y), cover)
            painter.restore()
        else:
            painter.setPen(QColor(pal.fg2))
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
        painter.setPen(QColor(pal.fg0))
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
        painter.setPen(QColor(pal.fg1))
        fm = QFontMetrics(f)
        if subtitle:
            subtitle = fm.elidedText(str(subtitle), Qt.ElideRight, text_rect.width())
            painter.drawText(text_rect.left(), text_rect.top() + 22 + fm.ascent(), subtitle)

        # task #20 schema v4 起：rating 不再是 Project 顶层属性，统一在
        # p.field_values。grid 视图卡片底部的评分渲染暂移除（卡片表格里仍能
        # 看到评分）；如需恢复，需通过 delegate 注入字段表查 rating fid 后从
        # p.field_values 读。

        # 卡片星标始终可见，使用不透明背景避免封面颜色影响可读性。
        star_rect = self.favorite_rect(opt.rect)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(pal.bg1))
        painter.drawRoundedRect(star_rect, 6, 6)
        star_font = QFont(painter.font())
        star_font.setPointSize(16)
        painter.setFont(star_font)
        favorite = bool(idx.data(ProjectModel.RoleFavorite))
        painter.setPen(QColor(pal.warn if favorite else pal.fg1))
        painter.drawText(star_rect, Qt.AlignCenter, "★" if favorite else "☆")
        painter.restore()
