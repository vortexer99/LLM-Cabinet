"""LLM 元数据建议触发对话框。

显示当前启用的平台/模型，以及项目内的文件列表（可勾选作为参考）。
点击"执行"后通过回调返回 (ref_file_ids, target_field_ids, user_note)，
调用方负责入队和关闭其它对话框。

『需要建议的字段』默认按字段设置中的 `suggest_enabled` 勾上，但本次勾选
仅作为本次任务的临时设置，不会回写到字段定义。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..llm import LLMConfig
from ..models import Field, FileItem


class _NoElideDelegate(QStyledItemDelegate):
    """单元格不省略文本，列宽不够则直接截断。自绘文字避免被 style 二次 elide。"""

    def initStyleOption(self, option, index):  # noqa: N802
        super().initStyleOption(option, index)
        option.textElideMode = Qt.ElideNone

    def paint(self, painter, option, index):  # noqa: N802
        from PySide6.QtWidgets import (
            QApplication,
            QStyle,
            QStyleOptionViewItem,
        )

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        if not text:
            return

        text_rect = style.subElementRect(
            QStyle.SE_ItemViewItemText, opt, opt.widget
        )
        painter.save()
        painter.setClipRect(text_rect)
        painter.setFont(opt.font)
        if opt.state & QStyle.State_Selected:
            painter.setPen(opt.palette.highlightedText().color())
        else:
            painter.setPen(opt.palette.text().color())
        painter.drawText(text_rect, int(opt.displayAlignment), text)
        painter.restore()


class LLMSuggestDialog(QDialog):
    def __init__(
        self,
        project_title: str,
        files: list[FileItem],
        cfg: LLMConfig,
        fields: list[Field],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("LLM 元数据建议")
        self.resize(660, 640)

        self.ref_file_ids: list[int] = []
        self.target_field_ids: list[int] = []
        self.user_note: str = ""

        v = QVBoxLayout(self)
        v.setSpacing(8)

        # 顶部：项目标题 + 当前 provider
        top = QLabel()
        top.setTextFormat(Qt.RichText)
        active = cfg.active()
        if active and active.api_key:
            provider_str = f"<b>{active.label()}</b> · <code>{active.model}</code>"
        elif active:
            provider_str = f"<b>{active.label()}</b> · <span style='color:#fa5252'>未配置 API Key</span>"
        else:
            provider_str = "<span style='color:#fa5252'>未选择默认平台</span>"
        top.setText(
            f"为项目 「<b>{project_title or '(未命名)'}</b>」 生成元数据建议<br>"
            f"当前模型：{provider_str}"
        )
        v.addWidget(top)

        # ===== 需要建议的字段 =====
        gb_fields = QGroupBox("需要建议的字段（仅本次有效，不会修改字段设置）")
        gv = QVBoxLayout(gb_fields)
        gv.setContentsMargins(10, 8, 10, 8)
        gv.setSpacing(6)

        # 行内的全选/全不选按钮
        ops = QHBoxLayout()
        ops.addStretch(1)
        b_f_all = QPushButton("全选")
        b_f_all.setProperty("flat", True)
        b_f_all.clicked.connect(lambda: self._toggle_all_fields(True))
        b_f_none = QPushButton("全不选")
        b_f_none.setProperty("flat", True)
        b_f_none.clicked.connect(lambda: self._toggle_all_fields(False))
        b_f_default = QPushButton("恢复默认")
        b_f_default.setProperty("flat", True)
        b_f_default.clicked.connect(self._reset_fields_default)
        ops.addWidget(b_f_default); ops.addWidget(b_f_all); ops.addWidget(b_f_none)
        gv.addLayout(ops)

        # 字段网格（3 列摆放，节省纵向空间）
        grid_host = QFrame()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)
        self._field_checks: list[tuple[QCheckBox, Field]] = []
        cols = 3
        for i, f in enumerate(fields):
            cb = QCheckBox(f.name)
            cb.setChecked(bool(f.suggest_enabled))
            cb.setToolTip(f"类型：{f.type}" + ("  · 标题字段" if f.is_title else ""))
            grid.addWidget(cb, i // cols, i % cols)
            self._field_checks.append((cb, f))
        gv.addWidget(grid_host)
        v.addWidget(gb_fields)

        # ===== 文件列表 =====
        self.tbl = QTableWidget(len(files), 4)
        self.tbl.setHorizontalHeaderLabels(["", "文件", "内容提取", "说明"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setShowGrid(False)
        self.tbl.setTextElideMode(Qt.ElideNone)
        self._no_elide_delegate = _NoElideDelegate(self.tbl)
        self.tbl.setItemDelegate(self._no_elide_delegate)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setSelectionMode(QAbstractItemView.NoSelection)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.tbl.horizontalHeader()
        # 勾选列固定 40 像素，避免被压窄看不清复选框
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        self.tbl.setColumnWidth(0, 40)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        self.tbl.verticalHeader().setDefaultSectionSize(28)

        kind_icons = {"image": "🖼", "video": "🎬", "pdf": "📄", "doc": "📝",
                      "code": "💻", "other": "📦"}
        from ..llm.context import (
            EXTRACTION_FILENAME, extraction_capability_label,
        )
        self._checks: list[tuple[QCheckBox, FileItem]] = []
        for r, f in enumerate(files):
            cap_label, cap_code, cap_tip = extraction_capability_label(f.path)
            cb = QCheckBox()
            cb.setChecked(False)  # 默认全不选；由用户主动挑参考文件
            if cap_code == EXTRACTION_FILENAME:
                # 仅文件名能给出 → 勾选意义不大；保留可勾（用户也许想强行让 LLM
                # 看到这个文件的存在），但默认提示一下
                cb.setToolTip(
                    "此文件类型暂不支持内容提取；勾选后 LLM 收到的"
                    "依然只是文件名，不会看到内容。"
                )
            holder = self._wrap_center(cb)
            self.tbl.setCellWidget(r, 0, holder)

            from pathlib import Path
            it_name = QTableWidgetItem(f"{kind_icons.get(f.kind, '📦')}  {Path(f.path).name}")
            it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)
            self.tbl.setItem(r, 1, it_name)

            it_cap = QTableWidgetItem(cap_label)
            it_cap.setFlags(it_cap.flags() & ~Qt.ItemIsEditable)
            it_cap.setToolTip(cap_tip)
            self.tbl.setItem(r, 2, it_cap)

            it_label = QTableWidgetItem(f.label or "")
            it_label.setFlags(it_label.flags() & ~Qt.ItemIsEditable)
            self.tbl.setItem(r, 3, it_label)
            self._checks.append((cb, f))

        files_row = QHBoxLayout()
        files_row.addWidget(QLabel("参考文件："))
        files_row.addStretch(1)
        b_all = QPushButton("全选")
        b_all.setProperty("flat", True)
        b_all.clicked.connect(lambda: self._toggle_all(True))
        b_none = QPushButton("全不选")
        b_none.setProperty("flat", True)
        b_none.clicked.connect(lambda: self._toggle_all(False))
        files_row.addWidget(b_all); files_row.addWidget(b_none)
        v.addLayout(files_row)

        # 隐私提示：明确"内容 vs 文件名"的发送范围
        privacy_hint = QLabel(
            "ℹ️ 仅勾选项的<b>文件内容</b>会发送给 LLM；但<b>所有文件的文件名</b>"
            "都会作为项目结构上下文发送（无论是否勾选）。<br>"
            "「内容提取」列展示该文件能否被解析：✅ 走专用解析器抽正文 / "
            "🖼 直接传图 / ⚠ 仅文件名 — 不可提取的文件勾选了也只能让 LLM 看到文件名。"
        )
        privacy_hint.setTextFormat(Qt.RichText)
        privacy_hint.setWordWrap(True)
        privacy_hint.setProperty("muted", True)
        privacy_hint.setToolTip(
            "文件名出现在 prompt 的『项目内全部文件清单』部分，\n"
            "便于模型理解项目结构；文件内容仅在勾选时被读取并发送。"
        )
        v.addWidget(privacy_hint)

        v.addWidget(self.tbl, 1)

        # 附言
        v.addWidget(QLabel("附言（可选）："))
        self.ed_note = QPlainTextEdit()
        self.ed_note.setPlaceholderText("如：这是张三翻译的版本；请按官方资料填写来源 URL …")
        self.ed_note.setMaximumHeight(110)
        v.addWidget(self.ed_note)

        # 底部按钮
        bb = QDialogButtonBox()
        self.btn_run = bb.addButton("✨ 执行", QDialogButtonBox.AcceptRole)
        self.btn_run.setProperty("primary", True)
        bb.addButton("取消", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        # 若没填 key，禁用执行
        if not (active and active.api_key):
            self.btn_run.setEnabled(False)
            self.btn_run.setToolTip("请先在 设置 → API 中配置 API Key")

    @staticmethod
    def _wrap_center(widget):
        from PySide6.QtWidgets import QHBoxLayout, QWidget
        w = QWidget()
        l = QHBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.addStretch(1); l.addWidget(widget); l.addStretch(1)
        return w

    def _toggle_all(self, checked: bool) -> None:
        for cb, _ in self._checks:
            cb.setChecked(checked)

    def _toggle_all_fields(self, checked: bool) -> None:
        for cb, _ in self._field_checks:
            cb.setChecked(checked)

    def _reset_fields_default(self) -> None:
        for cb, f in self._field_checks:
            cb.setChecked(bool(f.suggest_enabled))

    def _on_accept(self) -> None:
        self.ref_file_ids = [
            f.id for cb, f in self._checks if cb.isChecked() and f.id is not None
        ]
        self.target_field_ids = [
            f.id for cb, f in self._field_checks if cb.isChecked() and f.id is not None
        ]
        self.user_note = self.ed_note.toPlainText().strip()
        self.accept()
