"""设置对话框：左类别 + 右内容（QListWidget + QStackedWidget）。

页：
- 通用：主题
- 项目库：默认存储模式、库根目录（只读，可在资源管理器打开）
- 视图：默认视图（grid/list）、默认列
- 关于：版本、数据库与库路径
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import HOMEPAGE_URL, __version__
from ..db import SCHEMA_VERSION
from ..repository import Repository
from ..utils import app_data_dir, reveal_in_explorer

# ---------------------------------------------------------------------------
# MCP capabilities display (shown in Settings → MCP page)
# ---------------------------------------------------------------------------
_MCP_CAPABILITIES_HTML = """\
<h3 style="margin-top:0">🔧 工具（共 5 个）</h3>

<p><b>浏览与搜索</b></p>
<ul style="margin-top:2px; margin-bottom:8px">
<li><b>query_projects</b> — 搜索 / 查看 / 统计项目（search / get / count）</li>
<li><b>manage_libraries</b> — 列出 / 切换库、查看字段定义（list / switch / get_field / get_fields）</li>
</ul>

<p><b>编辑与管理</b></p>
<ul style="margin-top:2px; margin-bottom:8px">
<li><b>manage_project</b> — 创建项目、修改信息、增减标签（create / update / add_tag / remove_tag）</li>
<li><b>manage_files</b> — 列出 / 添加 / 移除项目文件（list / add / remove）</li>
<li><b>export_project</b> — 导出项目到本地目录</li>
</ul>

<h3>📦 数据资源（8 个）</h3>
<ul style="margin-top:2px; margin-bottom:8px">
<li><b>cabinet://library/info</b> — 库元信息（路径、项目数等）</li>
<li><b>cabinet://library/stats</b> — 统计概览（标签分布、填充率等）</li>
<li><b>cabinet://tags</b> — 所有标签及每标签的项目计数</li>
<li><b>cabinet://fields</b> — 所有自定义字段的定义</li>
<li><b>cabinet://projects</b> — 全部项目的摘要列表</li>
<li><b>cabinet://project/{id}</b> — 单个项目的完整元数据</li>
<li><b>cabinet://project/{id}/files</b> — 某个项目下的文件清单</li>
<li><b>cabinet://file/{id}</b> — 单个文件内容（默认禁用）</li>
</ul>

<h3>📋 任务提示（4 个）</h3>
<ul style="margin-top:2px; margin-bottom:0">
<li><b>整理新入库文件</b> — 引导 agent 按流程发现、匹配、导入新文件</li>
<li><b>审核元数据质量</b> — 检查描述、标签、字段填充率，生成质量报告</li>
<li><b>生成库概览</b> — 统计项目、标签分布、近期活动，生成综合报告</li>
<li><b>推荐标签</b> — 分析项目内容，推荐合适的标签</li>
</ul>
"""


class SettingsDialog(QDialog):
    """设置面板。变更通过信号通知，调用方决定是否立即应用。"""

    theme_changed = Signal(str)                  # "dark" | "light"
    default_storage_changed = Signal(str)        # "link" | "copy"
    default_view_changed = Signal(str)           # "grid" | "list"
    fields_changed = Signal()                    # 字段定义变化（增删改顺序可见性类型）

    def __init__(self, repo: Repository, library_root: Path, db_path: Path, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.library_root = library_root
        self.db_path = db_path

        self.setWindowTitle("设置")
        self.resize(720, 520)

        # ---- 类别栏 ----
        self.cat_list = QListWidget()
        self.cat_list.setObjectName("SettingsCategories")
        self.cat_list.setFixedWidth(160)
        self.cat_list.setSpacing(2)
        self._categories: list[str] = ["通用", "项目库", "视图", "字段", "API", "MCP", "关于"]
        for name in self._categories:
            QListWidgetItem(name, self.cat_list)
        self.cat_list.setCurrentRow(0)

        # ---- 内容栈 ----
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_general_page())
        self.stack.addWidget(self._build_library_page())
        self.stack.addWidget(self._build_view_page())
        self.stack.addWidget(self._build_fields_page())
        self.stack.addWidget(self._build_api_page())
        self.stack.addWidget(self._build_mcp_page())
        self.stack.addWidget(self._build_about_page())

        self.cat_list.currentRowChanged.connect(self.stack.setCurrentIndex)

        # ---- 底部按钮 ----
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.accept)

        # ---- 拼装 ----
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.cat_list)
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        # 不再写死颜色：之前用 #373a40 仅适配深色，浅色模式下会显得"黑棒"。
        # 让 Qt 用当前 palette 的默认 frame 颜色，浅 / 深都自然。
        sep.setFrameShadow(QFrame.Sunken)
        body.addWidget(sep)
        body.addWidget(self.stack, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        wrap = QWidget()
        wrap.setLayout(body)
        root.addWidget(wrap, 1)
        bb_wrap = QHBoxLayout()
        bb_wrap.setContentsMargins(12, 8, 12, 12)
        bb_wrap.addStretch(1)
        bb_wrap.addWidget(bb)
        root.addLayout(bb_wrap)

    def set_active_category(self, name: str) -> None:
        """切到指定类目页（task #15 T2 横幅"📋 设置 → 字段"按钮用）。

        ``name`` 不在已知类目里时静默忽略（保持当前页）。
        """
        try:
            idx = self._categories.index(name)
        except ValueError:
            return
        self.cat_list.setCurrentRow(idx)

    # =================================================================
    # 通用
    # =================================================================
    def _build_general_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        title = QLabel("通用")
        title.setProperty("h1", True)
        lay.addWidget(title)

        # LLM 助手入口（task #11 T3 决策 1：辅助入口；内部代码沿用 wizard 命名）
        wiz_row = QHBoxLayout()
        wiz_lbl = QLabel(
            "🪄 通过 LLM 助手让 AI 帮你规划字段结构、整理库等。"
        )
        wiz_lbl.setWordWrap(True)
        wiz_row.addWidget(wiz_lbl, 1)
        btn_wiz = QPushButton("打开 LLM 助手...")
        btn_wiz.clicked.connect(self._open_wizards)
        wiz_row.addWidget(btn_wiz)
        lay.addLayout(wiz_row)

        gb = QGroupBox("外观")
        form = QFormLayout(gb)
        form.setLabelAlignment(Qt.AlignLeft)

        self.cmb_theme = QComboBox()
        self.cmb_theme.addItem("浅色 (Light)", "light")
        self.cmb_theme.addItem("深色 (Dark)", "dark")
        cur = self.repo.get_setting("theme", "light") or "light"
        idx = self.cmb_theme.findData(cur)
        self.cmb_theme.setCurrentIndex(max(0, idx))
        self.cmb_theme.currentIndexChanged.connect(self._on_theme_changed)
        form.addRow("主题：", self.cmb_theme)

        # 主题维护规划提示：v0.3.x 之后不再维护深色模式（精力 + 经费有限）。
        note = QLabel(
            "说明：受精力与经费所限，v0.3.x 之后的版本将不再继续维护深色模式，"
            "届时仅保留浅色主题。"
        )
        note.setWordWrap(True)
        note.setProperty("hint", True)
        form.addRow("", note)

        lay.addWidget(gb)

        # 应用数据目录（软件层级属性，与库无关）
        gb_data = QGroupBox("数据位置")
        row = QHBoxLayout(gb_data)
        lbl = QLabel(f"应用数据目录：{app_data_dir()}")
        lbl.setWordWrap(True)
        row.addWidget(lbl, 1)
        b_open = QPushButton("📂")
        b_open.setFixedWidth(40)
        b_open.clicked.connect(lambda: reveal_in_explorer(app_data_dir()))
        row.addWidget(b_open)
        lay.addWidget(gb_data)

        lay.addStretch(1)
        return w

    def _on_wiz_rounds_changed(self, v: int) -> None:
        self._wiz_set_max_rounds(self.repo, v)

    def _open_wizards(self) -> None:
        """从设置 → 通用 打开 LLM 助手列表对话框。"""
        from .wizard_list_dialog import WizardListDialog
        # library 不在 SettingsDialog 上下文里，助手可能不需要它，传 None 即可
        # （当前唯一一个助手 LibraryInitWizard 不使用 library，仅用 repo）。
        dlg = WizardListDialog(self.repo, library=None, parent=self)
        dlg.exec()
        if dlg.any_applied():
            self.fields_changed.emit()

    def _on_theme_changed(self, _i: int) -> None:
        v = self.cmb_theme.currentData()
        self.repo.set_setting("theme", v)
        self.theme_changed.emit(v)

    # =================================================================
    # 项目库
    # =================================================================
    def _build_library_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        title = QLabel("项目库")
        title.setProperty("h1", True)
        lay.addWidget(title)

        gb1 = QGroupBox("默认导入行为")
        form = QFormLayout(gb1)
        self.cmb_storage = QComboBox()
        self.cmb_storage.addItem("🔗 链接（仅记录路径，不动用户文件）", "link")
        self.cmb_storage.addItem("📦 仓储（复制到统一仓库目录）", "copy")
        cur = self.repo.get_setting("default_storage_mode", "link") or "link"
        idx = self.cmb_storage.findData(cur)
        self.cmb_storage.setCurrentIndex(max(0, idx))
        self.cmb_storage.currentIndexChanged.connect(self._on_storage_changed)
        form.addRow("默认存储方式：", self.cmb_storage)
        hint = QLabel(
            "拖放新建项目、以及向项目里添加文件时使用此默认值。\n"
            "每次添加文件仍可在弹出的对话框中临时改选；"
            "存储方式是文件级属性，同一项目内可混合存在。"
        )
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        form.addRow("", hint)
        lay.addWidget(gb1)

        gb2 = QGroupBox("库目录")
        v = QVBoxLayout(gb2)
        path_row = QHBoxLayout()
        self.ed_lib = QLineEdit(str(self.library_root))
        self.ed_lib.setReadOnly(True)
        btn_open = QPushButton("📂  打开")
        btn_open.clicked.connect(lambda: reveal_in_explorer(self.library_root))
        path_row.addWidget(self.ed_lib, 1)
        path_row.addWidget(btn_open)
        v.addLayout(path_row)
        tip = QLabel("仓库目录用于存放『复制』模式下导入的文件。")
        tip.setProperty("hint", True)
        tip.setWordWrap(True)
        v.addWidget(tip)
        lay.addWidget(gb2)

        # 数据位置（只读 + 打开按钮）
        gb3 = QGroupBox("数据位置")
        gv = QVBoxLayout(gb3)
        for label, path in (
            ("数据库", self.db_path),
        ):
            row = QHBoxLayout()
            lbl = QLabel(f"{label}：")
            lbl.setFixedWidth(110)
            lbl.setProperty("muted", True)
            ed = QLineEdit(str(path))
            ed.setReadOnly(True)
            b_open = QPushButton("📂")
            b_open.setToolTip("在资源管理器中打开")
            b_open.setProperty("flat", True)
            # 数据库定位到所在目录；目录直接打开
            target = path if Path(path).is_dir() else Path(path).parent
            b_open.clicked.connect(lambda _=False, t=target: reveal_in_explorer(t))
            row.addWidget(lbl)
            row.addWidget(ed, 1)
            row.addWidget(b_open)
            gv.addLayout(row)

        # Schema 版本 + 备份状态
        ver_row = QHBoxLayout()
        ver_cap = QLabel("Schema 版本：")
        ver_cap.setFixedWidth(110)
        ver_cap.setProperty("muted", True)
        ver_val = QLabel(f"v{SCHEMA_VERSION}")
        ver_val.setToolTip(
            "数据库 schema 版本号（独立于应用版本号）。\n"
            "升级新版应用打开旧 db 时，会自动备份并应用迁移脚本。\n"
            "备份文件落在数据库同目录，文件名形如 cabinet.vN.时间戳.bak"
        )
        # 顺手统计同目录下的 .bak 数量
        try:
            bak_dir = Path(self.db_path).parent
            n_bak = sum(
                1 for p in bak_dir.glob(f"{Path(self.db_path).stem}.v*.bak")
            )
        except OSError:
            n_bak = 0
        bak_info = QLabel(
            f"·  自动备份：{n_bak} 份" if n_bak else "·  自动备份：暂无"
        )
        bak_info.setProperty("muted", True)
        ver_row.addWidget(ver_cap)
        ver_row.addWidget(ver_val)
        ver_row.addSpacing(8)
        ver_row.addWidget(bak_info)
        ver_row.addStretch(1)
        gv.addLayout(ver_row)

        lay.addWidget(gb3)

        lay.addStretch(1)
        return w

    def _on_storage_changed(self, _i: int) -> None:
        v = self.cmb_storage.currentData()
        self.repo.set_setting("default_storage_mode", v)
        self.default_storage_changed.emit(v)

    # =================================================================
    # 视图
    # =================================================================
    def _build_view_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        title = QLabel("视图")
        title.setProperty("h1", True)
        lay.addWidget(title)

        gb1 = QGroupBox("默认视图")
        form = QFormLayout(gb1)
        self.cmb_view = QComboBox()
        self.cmb_view.addItem("网格（封面墙）", "grid")
        self.cmb_view.addItem("列表（表格）", "list")
        cur = self.repo.get_setting("default_view_mode", "grid") or "grid"
        idx = self.cmb_view.findData(cur)
        self.cmb_view.setCurrentIndex(max(0, idx))
        self.cmb_view.currentIndexChanged.connect(self._on_view_changed)
        form.addRow("启动时视图：", self.cmb_view)
        lay.addWidget(gb1)

        hint = QLabel(
            "列表视图显示的字段及其顺序，请到『字段』页管理：勾选字段的『显示』即可显示在列表中。"
        )
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        lay.addWidget(hint)

        lay.addStretch(1)
        return w

    def _on_view_changed(self, _i: int) -> None:
        v = self.cmb_view.currentData()
        self.repo.set_setting("default_view_mode", v)
        self.default_view_changed.emit(v)

    def _on_columns_changed(self, _state: int) -> None:
        # 已废弃：列可见性合并到字段页
        pass

    # =================================================================
    # 字段（库级）
    # =================================================================
    def _build_fields_page(self) -> QWidget:
        from ..models import FIELD_TYPES, FIELD_TYPE_LABELS
        self._FIELD_TYPES = FIELD_TYPES
        self._FIELD_TYPE_LABELS = FIELD_TYPE_LABELS

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)

        title = QLabel("字段")
        title.setProperty("h1", True)
        lay.addWidget(title)

        tip = QLabel(
            "管理所有字段及其类型与顺序。表中顺序即为『项目编辑』对话框和列表视图中显示的顺序。"
            "『标题』『描述』『标签』为必有字段，不可删除或修改类型。"
        )
        tip.setProperty("hint", True)
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # 字段表（5 列：字段名 / 类型 / 显示 / 元数据建议 / LLM 提示）
        self.tbl_fields = QTableWidget(0, 5)
        self.tbl_fields.setHorizontalHeaderLabels(
            ["字段名", "类型", "显示", "元数据建议", "LLM 提示"]
        )
        self.tbl_fields.verticalHeader().setVisible(False)
        self.tbl_fields.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_fields.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_fields.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_fields.setShowGrid(False)
        self.tbl_fields.setAlternatingRowColors(True)
        h = self.tbl_fields.horizontalHeader()
        # 字段名占主，类型给固定较宽列以容纳类型名（如"评分（1~5 星）"）
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.Fixed)
        h.setSectionResizeMode(3, QHeaderView.Fixed)
        h.setSectionResizeMode(4, QHeaderView.Fixed)
        self.tbl_fields.setColumnWidth(1, 180)
        self.tbl_fields.setColumnWidth(2, 60)
        self.tbl_fields.setColumnWidth(3, 90)
        self.tbl_fields.setColumnWidth(4, 110)
        # 行高要足够容纳带 padding 的 QComboBox，否则文字会被上下裁掉只剩一条
        self.tbl_fields.verticalHeader().setDefaultSectionSize(40)
        lay.addWidget(self.tbl_fields, 1)

        # 操作按钮
        ops = QHBoxLayout()
        b_add = QPushButton("＋ 添加")
        b_add.clicked.connect(self._field_add)
        b_rename = QPushButton("✎ 重命名")
        b_rename.clicked.connect(self._field_rename)
        b_del = QPushButton("🗑 删除")
        b_del.setProperty("danger", True)
        b_del.clicked.connect(self._field_delete)
        b_up = QPushButton("↑ 上移")
        b_up.clicked.connect(lambda: self._field_move(-1))
        b_down = QPushButton("↓ 下移")
        b_down.clicked.connect(lambda: self._field_move(1))
        for b in (b_add, b_rename, b_del):
            ops.addWidget(b)
        ops.addStretch(1)
        ops.addWidget(b_up)
        ops.addWidget(b_down)
        lay.addLayout(ops)

        self._reload_fields_table()
        return w

    def _reload_fields_table(self) -> None:
        fields = self.repo.list_fields()
        self.tbl_fields.blockSignals(True)
        self.tbl_fields.setRowCount(len(fields))
        for r, f in enumerate(fields):
            # 字段名 — 受保护字段标"必有"（不可删除 / 类型固定）。
            # 不区分 is_title vs is_required：用户已经能从字段名看出它是
            # "标题"，括号里再写一遍"标题"是冗余的；只需要告知"必有"。
            tag_suffix = "  (必有)" if f.is_required else ""
            display_name = f.name + tag_suffix
            it_name = QTableWidgetItem(display_name)
            it_name.setData(Qt.UserRole, f.id)
            self.tbl_fields.setItem(r, 0, it_name)

            # 类型 ComboBox
            cmb = QComboBox()
            # 防止 cell widget 在某些 DPI / 缩放下被压成"竖排字"宽度
            cmb.setMinimumWidth(140)
            # 行高足够，但 cell widget 默认会被拉满 cell；显式给个最小高度，
            # 避免文字被裁切只剩一条横线
            cmb.setMinimumHeight(28)
            cmb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            for t in self._FIELD_TYPES:
                cmb.addItem(self._FIELD_TYPE_LABELS.get(t, t), t)
            # 受保护字段可能有 FIELD_TYPES 之外的固定类型（如 tags），单独追加并选中
            ftype = f.type or "text"
            if cmb.findData(ftype) < 0:
                cmb.addItem(self._FIELD_TYPE_LABELS.get(ftype, ftype), ftype)
            idx = cmb.findData(ftype)
            cmb.setCurrentIndex(max(0, idx))
            if f.is_required:
                cmb.setEnabled(False)
                cmb.setToolTip(f"『{f.name}』字段类型固定，不可修改")
            cmb.currentIndexChanged.connect(
                lambda _i, fid=f.id, box=cmb: self._field_change_type(fid, box.currentData())
            )
            self.tbl_fields.setCellWidget(r, 1, cmb)

            # 显示复选框
            cb = QCheckBox()
            cb.setChecked(f.visible)
            if f.is_title:
                cb.setEnabled(False)
                cb.setToolTip("标题字段必显")
            cb.stateChanged.connect(
                lambda _s, fid=f.id, box=cb: self._field_toggle_visible(fid, box.isChecked())
            )
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addStretch(1); hl.addWidget(cb); hl.addStretch(1)
            self.tbl_fields.setCellWidget(r, 2, holder)

            # LLM 建议复选框（标题字段也允许用户自由开关）
            cb_sug = QCheckBox()
            cb_sug.setChecked(f.suggest_enabled)
            cb_sug.stateChanged.connect(
                lambda _s, fid=f.id, box=cb_sug: self._field_toggle_suggest(fid, box.isChecked())
            )
            holder2 = QWidget()
            hl2 = QHBoxLayout(holder2)
            hl2.setContentsMargins(0, 0, 0, 0)
            hl2.addStretch(1); hl2.addWidget(cb_sug); hl2.addStretch(1)
            self.tbl_fields.setCellWidget(r, 3, holder2)

            # LLM 提示按钮（task #11 T1）：点击弹文本编辑器编辑该字段的 prompt_hint
            btn_hint = QPushButton(
                "📝 已设置" if (f.prompt_hint or "").strip() else "✎ 编辑…"
            )
            btn_hint.setFlat(True)
            if (f.prompt_hint or "").strip():
                btn_hint.setToolTip(
                    "当前提示：\n" + (f.prompt_hint[:300] + ("…" if len(f.prompt_hint) > 300 else ""))
                )
            else:
                btn_hint.setToolTip("点击编辑该字段的 LLM 提示（留空使用默认）")
            btn_hint.clicked.connect(
                lambda _checked=False, fid=f.id, name=f.name, hint=f.prompt_hint or "":
                    self._field_edit_prompt_hint(fid, name, hint)
            )
            self.tbl_fields.setCellWidget(r, 4, btn_hint)
        self.tbl_fields.blockSignals(False)

    def _current_field_id(self) -> int | None:
        r = self.tbl_fields.currentRow()
        if r < 0:
            return None
        it = self.tbl_fields.item(r, 0)
        return it.data(Qt.UserRole) if it else None

    def _field_add(self) -> None:
        from ..models import FIELD_TYPE_LABELS, FIELD_TYPES
        dlg = _AddFieldDialog(parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.repo.add_field(dlg.name, dlg.type)
        except Exception as e:
            QMessageBox.warning(self, "失败", str(e))
            return
        self._reload_fields_table()
        self.fields_changed.emit()

    def _field_rename(self) -> None:
        fid = self._current_field_id()
        if fid is None:
            return
        f = self.repo.get_field(fid)
        if not f:
            return
        new_name, ok = QInputDialog.getText(self, "重命名", "新字段名：", text=f.name)
        if not ok or not new_name.strip() or new_name.strip() == f.name:
            return
        try:
            self.repo.rename_field(fid, new_name.strip())
        except Exception as e:
            QMessageBox.warning(self, "失败", str(e))
            return
        self._reload_fields_table()
        self.fields_changed.emit()

    def _field_toggle_visible(self, fid: int, visible: bool) -> None:
        self.repo.set_field_visible(fid, visible)
        self.fields_changed.emit()

    def _field_toggle_suggest(self, fid: int, enabled: bool) -> None:
        self.repo.set_field_suggest_enabled(fid, enabled)
        # 不发 fields_changed（视图列不受影响）

    def _field_edit_prompt_hint(self, fid: int, name: str, current_hint: str) -> None:
        """编辑字段的 LLM 提示（task #11 T1）。"""
        from PySide6.QtWidgets import (
            QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout,
        )
        dlg = QDialog(self)
        dlg.setWindowTitle(f"LLM 提示 — {name}")
        dlg.resize(560, 360)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)

        head = QLabel(
            f"为字段「<b>{name}</b>」自定义 LLM 建议时的格式说明。<br>"
            "留空 = 使用默认（仅按类型说明）；填写 = 在 user prompt 中追加为该字段的「格式要求」。"
        )
        head.setWordWrap(True)
        v.addWidget(head)

        ed = QPlainTextEdit()
        ed.setPlaceholderText(
            "示例（标题）：不超过 30 字；不要带书名号；体现作品类型与核心主题。\n"
            "示例（描述）：200~400 字；先一句概括，再分段说明背景/亮点/适用人群；不使用 emoji。"
        )
        ed.setPlainText(current_hint or "")
        v.addWidget(ed, 1)

        char_lbl = QLabel()
        char_lbl.setProperty("hint", True)

        def _update_count() -> None:
            n = len(ed.toPlainText())
            char_lbl.setText(
                f"  当前 {n} 字（建议 ≤ 500；超过会被自动截断）"
            )
        ed.textChanged.connect(_update_count)
        _update_count()
        v.addWidget(char_lbl)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("保存")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        if dlg.exec() != QDialog.Accepted:
            return
        new_hint = ed.toPlainText().strip()
        self.repo.set_field_prompt_hint(fid, new_hint)
        self._reload_fields_table()

    def _field_change_type(self, fid: int, ftype: str) -> None:
        """改字段类型（task #19 Phase A）：兼容切静默执行；不兼容走确认弹窗。"""
        from ..models import is_compatible_type_change

        f = self.repo.get_field(fid)
        if f is None or f.type == ftype:
            return
        if is_compatible_type_change(f.type, ftype):
            self.repo.set_field_type(fid, ftype)
            self.fields_changed.emit()
            return

        n_values, m_pending = self._count_field_impact(fid)
        # 三条都没东西可保护 → 静默切（即使技术上不兼容）
        if n_values == 0 and m_pending == 0 and not (f.prompt_hint or "").strip():
            self.repo.set_field_type(fid, ftype)
            self.fields_changed.emit()
            return

        confirmed, clear_hint = _FieldTypeChangeConfirmDialog.ask(
            self, f, ftype, n_values, m_pending,
        )
        if not confirmed:
            # 用户取消：ComboBox 的 currentIndex 已经被切到新类型；整表重画
            # 把视觉拽回去
            self._reload_fields_table()
            return

        try:
            self.repo.set_field_type(
                fid, ftype,
                supersede_pending_suggestions=(m_pending > 0),
                clear_prompt_hint=clear_hint,
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "失败", str(e))
            self._reload_fields_table()
            return
        self.fields_changed.emit()

    def _count_field_impact(self, fid: int) -> tuple[int, int]:
        """统计改类型会影响多少 (非空字段值数, pending 建议数)。

        - 非空值数走 ``repo.count_field_filled``：它会按字段类型分流——系统字段
          读 ``projects`` 表对应列、用户字段读 ``project_field_values``。绝对
          不要只查 ``project_field_values``，那样对系统字段（如 author / date /
          source_url / rating）永远返回 0，会导致"明明有数据却静默切类型不弹窗"。
        - pending 建议数走 ``project_field_suggestions``（这张表系统字段 / 用户
          字段都用同一份 schema）。
        """
        f = self.repo.get_field(fid)
        if f is None:
            return 0, 0
        n_values = self.repo.count_field_filled(f)
        m_pending = self.repo.conn.execute(
            "SELECT COUNT(*) FROM project_field_suggestions "
            "WHERE field_id=? AND status='pending'",
            (fid,),
        ).fetchone()[0]
        return int(n_values), int(m_pending)


    def _field_move(self, delta: int) -> None:
        r = self.tbl_fields.currentRow()
        if r < 0:
            return
        target = r + delta
        if target < 0 or target >= self.tbl_fields.rowCount():
            return
        ids: list[int] = []
        for i in range(self.tbl_fields.rowCount()):
            it = self.tbl_fields.item(i, 0)
            if it:
                ids.append(it.data(Qt.UserRole))
        ids[r], ids[target] = ids[target], ids[r]
        self.repo.reorder_fields(ids)
        self._reload_fields_table()
        self.tbl_fields.setCurrentCell(target, 0)
        self.fields_changed.emit()

    def _field_delete(self) -> None:
        fid = self._current_field_id()
        if fid is None:
            return
        f = self.repo.get_field(fid)
        if not f:
            return
        if f.is_required:
            QMessageBox.information(self, "提示", f"『{f.name}』字段不可删除。")
            return

        # 统计影响项目数（系统字段读 projects 列，用户字段读 project_field_values）
        cnt = self.repo.count_field_filled(f)

        dlg = _DeleteFieldChoiceDialog(f.name, cnt, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.repo.delete_field(fid, append_to_description=dlg.append_to_desc)
        except Exception as e:
            QMessageBox.warning(self, "失败", str(e))
            return
        self._reload_fields_table()
        self.fields_changed.emit()

    # =================================================================
    # API（LLM）
    # =================================================================
    def _build_api_page(self) -> QWidget:
        from ..llm import load_config, save_config
        from ..llm.config import PROVIDER_DEFAULTS, PROVIDER_IDS

        self._llm_save_config = save_config
        self._llm_cfg = load_config(self.repo)

        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)

        title = QLabel("API（大模型）")
        title.setProperty("h1", True)
        outer.addWidget(title)

        tip = QLabel("配置 API Key 后，即可在项目元数据编辑或右键菜单中调用 LLM 生成字段建议。")
        tip.setProperty("hint", True)
        tip.setWordWrap(True)
        outer.addWidget(tip)

        # 全局：默认平台 / 默认语言
        gb_global = QGroupBox("全局")
        gf = QFormLayout(gb_global)
        self.cmb_default_provider = QComboBox()
        for pid in PROVIDER_IDS:
            self.cmb_default_provider.addItem(PROVIDER_DEFAULTS[pid]["label"], pid)
        idx = self.cmb_default_provider.findData(self._llm_cfg.default_provider)
        self.cmb_default_provider.setCurrentIndex(max(0, idx))
        self.cmb_default_provider.currentIndexChanged.connect(self._on_default_provider)
        gf.addRow("默认启用平台：", self.cmb_default_provider)

        self.cmb_default_lang = QComboBox()
        for lang in ("中文", "English"):
            self.cmb_default_lang.addItem(lang, lang)
        idx2 = self.cmb_default_lang.findData(self._llm_cfg.default_language)
        self.cmb_default_lang.setCurrentIndex(max(0, idx2))
        self.cmb_default_lang.currentIndexChanged.connect(self._on_default_language)
        gf.addRow("默认语言：", self.cmb_default_lang)
        outer.addWidget(gb_global)

        # 各平台分组（可滚动）
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setObjectName("AppScrollArea")  # 对应 theme.py QScrollArea#AppScrollArea
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # 关键：让 viewport 不要 autoFillBackground，否则 fusion 默认浅色会盖住
        # theme.py 给 ScrollArea 设的窗口色背景，深色主题下视觉上整片白底。
        try:
            scroll.viewport().setAutoFillBackground(False)
        except Exception:
            pass
        host = QWidget()
        host.setObjectName("ApiScrollHost")  # 对应 theme.py QWidget#ApiScrollHost
        host_l = QVBoxLayout(host)
        host_l.setSpacing(8)
        host_l.setContentsMargins(0, 0, 0, 0)

        self._provider_widgets: dict[str, dict] = {}
        for pid in PROVIDER_IDS:
            host_l.addWidget(self._build_provider_box(pid))
        host_l.addStretch(1)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

        # LLM 助手对话轮数（task #11 T3 决策 2b；原在「通用」页，2026-06-02 搬来此处）
        gb_wiz = QGroupBox("LLM 助手")
        form_wiz = QFormLayout(gb_wiz)
        form_wiz.setLabelAlignment(Qt.AlignLeft)
        from .wizards.library_init import (
            DEFAULT_MAX_ROUNDS, get_max_rounds, set_max_rounds,
        )
        self._wiz_set_max_rounds = set_max_rounds  # 保存引用，避免闭包重复 import
        self.spin_wiz_rounds = QSpinBox()
        self.spin_wiz_rounds.setRange(1, 20)
        self.spin_wiz_rounds.setValue(get_max_rounds(self.repo))
        self.spin_wiz_rounds.valueChanged.connect(self._on_wiz_rounds_changed)
        form_wiz.addRow("一次会话的最大对话轮数：", self.spin_wiz_rounds)
        hint_w = QLabel(
            f"默认 {DEFAULT_MAX_ROUNDS}。每次「让 LLM 给出建议」或「在当前基础上调整」算一轮，"
            "用户编辑预览不计数；达上限后只能「重新开始」或采用当前结果。"
        )
        hint_w.setProperty("hint", True)
        hint_w.setWordWrap(True)
        form_wiz.addRow("", hint_w)
        outer.addWidget(gb_wiz)

        return w

    def _build_provider_box(self, pid: str) -> QWidget:
        from ..llm.config import PROVIDER_DEFAULTS
        defaults = PROVIDER_DEFAULTS[pid]
        pcfg = self._llm_cfg.providers[pid]

        gb = QGroupBox(defaults["label"])
        gf = QFormLayout(gb)

        ed_url = QLineEdit(pcfg.base_url)
        ed_url.setPlaceholderText(defaults["base_url"])
        ed_url.editingFinished.connect(lambda pid=pid, e=ed_url: self._update_provider(pid, "base_url", e.text()))
        gf.addRow("Base URL：", ed_url)

        ed_key = QLineEdit(pcfg.api_key)
        ed_key.setEchoMode(QLineEdit.Password)
        ed_key.setPlaceholderText("sk-...")
        ed_key.editingFinished.connect(lambda pid=pid, e=ed_key: self._update_provider(pid, "api_key", e.text()))
        # 显示/隐藏切换
        from PySide6.QtWidgets import QToolButton
        b_eye = QToolButton()
        b_eye.setText("👁")
        b_eye.setCheckable(True)
        b_eye.toggled.connect(lambda on, e=ed_key: e.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        key_row = QHBoxLayout()
        key_row.addWidget(ed_key, 1); key_row.addWidget(b_eye)
        key_wrap = QWidget(); key_wrap.setLayout(key_row)
        gf.addRow("API Key：", key_wrap)

        ed_model = QLineEdit(pcfg.model)
        ed_model.setPlaceholderText(defaults["model"])
        ed_model.editingFinished.connect(lambda pid=pid, e=ed_model: self._update_provider(pid, "model", e.text()))
        gf.addRow("模型：", ed_model)

        # 测试按钮
        b_test = QPushButton("🔌 测试连接")
        lbl_status = QLabel("")
        lbl_status.setProperty("hint", True)
        b_test.clicked.connect(
            lambda _checked=False, pid=pid, lbl=lbl_status: self._test_provider(pid, lbl)
        )
        test_row = QHBoxLayout()
        test_row.addWidget(b_test); test_row.addWidget(lbl_status, 1)
        test_wrap = QWidget(); test_wrap.setLayout(test_row)
        gf.addRow("", test_wrap)

        if not defaults.get("supports_image"):
            note = QLabel("⚠ 此平台不支持图像输入；选中的图片文件将被跳过。")
            note.setProperty("hint", True)
            gf.addRow("", note)

        self._provider_widgets[pid] = {
            "url": ed_url, "key": ed_key, "model": ed_model, "status": lbl_status,
        }
        return gb

    def _on_default_provider(self, _i: int) -> None:
        self._llm_cfg.default_provider = self.cmb_default_provider.currentData() or "deepseek"
        self._llm_save_config(self.repo, self._llm_cfg)

    def _on_default_language(self, _i: int) -> None:
        self._llm_cfg.default_language = self.cmb_default_lang.currentData() or "中文"
        self._llm_save_config(self.repo, self._llm_cfg)

    def _update_provider(self, pid: str, key: str, value: str) -> None:
        pc = self._llm_cfg.providers.get(pid)
        if pc is None:
            return
        setattr(pc, key, (value or "").strip())
        self._llm_save_config(self.repo, self._llm_cfg)

    def _test_provider(self, pid: str, lbl: QLabel) -> None:
        from ..llm.config import PROVIDER_DEFAULTS, ProviderConfig

        # 点测试时，实时从输入框读最新值（用户可能还没失焦保存）
        widgets = self._provider_widgets.get(pid) or {}
        ed_url = widgets.get("url")
        ed_key = widgets.get("key")
        ed_model = widgets.get("model")

        defaults = PROVIDER_DEFAULTS.get(pid)
        if defaults is None:
            lbl.setText(f"❌ 未知平台：{pid!r}")
            return

        url = (ed_url.text() if ed_url else "").strip() or defaults["base_url"]
        key = (ed_key.text() if ed_key else "").strip()
        model = (ed_model.text() if ed_model else "").strip() or defaults["model"]

        if not key:
            lbl.setText("⚠ 未填写 API Key")
            return

        # 同步进缓存配置 + 持久化（避免用户接着用却忘了失焦保存）
        pc = self._llm_cfg.providers.get(pid)
        if pc is None:
            pc = ProviderConfig(id=pid)
            self._llm_cfg.providers[pid] = pc
        pc.id = pid
        pc.base_url = url
        pc.api_key = key
        pc.model = model
        self._llm_save_config(self.repo, self._llm_cfg)

        lbl.setText("测试中…")
        lbl.repaint()
        # 放到子线程里跑，避免阻塞 UI（HTTP 调用可能 1~8 秒）
        self._run_ping_async(pc, lbl)

    def _run_ping_async(self, pc, lbl: QLabel) -> None:
        from PySide6.QtCore import QObject, QThread, Signal

        from ..llm import get_provider

        class _Worker(QObject):
            done = Signal(bool, str)

            def __init__(self, pcfg):
                super().__init__()
                self.pcfg = pcfg

            def run(self):
                try:
                    ok, msg = get_provider(self.pcfg).ping()
                except Exception as e:
                    ok, msg = False, f"{type(e).__name__}: {e}"
                self.done.emit(ok, msg)

        thread = QThread(self)
        worker = _Worker(pc)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def _on_done(ok: bool, msg: str) -> None:
            try:
                lbl.setText(("✅ " if ok else "❌ ") + msg)
            except RuntimeError:
                pass  # label 可能已随对话框销毁
            thread.quit()

        worker.done.connect(_on_done)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        # 防止 worker/thread 被 GC
        if not hasattr(self, "_ping_threads"):
            self._ping_threads: list = []
        self._ping_threads.append((thread, worker))
        thread.start()

    # =================================================================
    # MCP 集成
    # =================================================================
    def _build_mcp_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        title = QLabel("MCP 集成")
        title.setProperty("h1", True)
        lay.addWidget(title)

        desc = QLabel(
            "通过 MCP 协议把项目库暴露给外部 AI agent（Claude Desktop / Cursor / Cline 等），"
            "让 agent 可以搜索、浏览和管理你的项目。独立进程通过 stdio 通信，不开放网络端口。"
        )
        desc.setWordWrap(True)
        lay.addWidget(desc)

        # 使用提示
        tip = QLabel(
            "💡 启动方法：<code>python -m app.mcp.standalone</code>（或通过下方导出 JSON 后由客户端自动启动）\n"
            "💡 建议安装 Agent 技能：将 <code>app/mcp/skills/llm-cabinet/</code> 目录下的四个技能添加到你的 AI 客户端（或从 Release 页面下载 <code>llm-cabinet-skills.zip</code>），"
            "可获得文件整理、元数据审核、库概览和标签推荐等自动化能力。详见 <a href='https://github.com/vortexer99/LLM-Cabinet'>项目文档</a>。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray; font-size: 11px;")
        tip.setTextFormat(Qt.RichText)
        tip.setOpenExternalLinks(True)
        lay.addWidget(tip)

        gb = QGroupBox("导出 MCP 配置")
        gv = QVBoxLayout(gb)
        gv.setSpacing(8)

        hint = QLabel(
            "生成一段 JSON 配置，粘贴到对应客户端的配置文件中即可连接。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        gv.addWidget(hint)

        places = QLabel(
            "粘贴位置：<br>"
            "&nbsp;&nbsp;Claude Desktop → <code>claude_desktop_config.json</code> 的 <code>mcpServers</code> 节点<br>"
            "&nbsp;&nbsp;Cursor → 项目根目录 <code>.cursor/mcp.json</code><br>"
            "&nbsp;&nbsp;Cherry Studio → 右上角设置 → MCP服务器 → 添加 → 从JSON导入，粘贴后启用，"
            "再到智能体设置中开启工具并设置预授权"
        )
        places.setWordWrap(True)
        places.setStyleSheet("color: gray; font-size: 11px;")
        places.setTextFormat(Qt.RichText)
        gv.addWidget(places)

        self._btn_export = QPushButton("导出 JSON...")
        self._btn_export.clicked.connect(self._mcp_show_export_dialog)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self._btn_export)
        btn_row.addStretch()
        gv.addLayout(btn_row)

        lay.addWidget(gb)

        # ---- 可调用能力 ----
        gb_caps = QGroupBox("可调用能力（Tools / Resources / Prompts）")
        cv = QVBoxLayout(gb_caps)
        cv.setSpacing(0)

        caps_browser = QTextBrowser()
        caps_browser.setOpenExternalLinks(False)
        caps_browser.setFrameShape(QFrame.NoFrame)
        caps_browser.setMinimumHeight(260)
        caps_browser.setHtml(_MCP_CAPABILITIES_HTML)
        cv.addWidget(caps_browser)

        lay.addWidget(gb_caps, 1)

        return w

    def _mcp_show_export_dialog(self) -> None:
        """Show the MCP config export dialog."""
        dlg = QDialog(self)
        dlg.setWindowTitle("导出 MCP 配置")
        dlg.setMinimumWidth(420)
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)

        # Mode
        gb_mode = QGroupBox("库模式")
        mv = QVBoxLayout(gb_mode)
        self._radio_multi = QRadioButton("多库模式（推荐）")
        self._radio_single = QRadioButton("仅当前库")
        self._radio_multi.setChecked(True)
        mv.addWidget(self._radio_multi)
        mv.addWidget(self._radio_single)

        mode_hint = QLabel(
            "多库：agent 可发现全部库并自然语言切换。只需配置一次。\n"
            "单库：agent 仅操作当前打开的库，适合敏感资料。"
        )
        mode_hint.setWordWrap(True)
        mode_hint.setStyleSheet("color: gray; font-size: 11px; margin-left: 18px;")
        mv.addWidget(mode_hint)
        layout.addWidget(gb_mode)

        # Read-only mode toggle
        gb_write = QGroupBox("只读模式")
        wv = QVBoxLayout(gb_write)
        self._cb_write = QCheckBox("只读模式（agent 只能浏览和搜索，不能修改数据）")
        self._cb_write.setChecked(False)
        self._cb_write.setToolTip(
            "默认关闭：agent 可以正常浏览和编辑库内容。\n"
            "勾选后 agent 只能查看，适合公开场合或给别人演示时使用。"
        )
        wv.addWidget(self._cb_write)

        write_hint = QLabel(
            "默认情况下 agent 拥有添加/修改能力（对应 --write-permission session，仅当次连接有效）。\n"
            "Claude Desktop 内的写工具默认也需要手动批准，形成双重保护。"
            "如需完全只读，请勾选上方复选框。"
        )
        write_hint.setWordWrap(True)
        write_hint.setStyleSheet("color: gray; font-size: 11px; margin-left: 18px;")
        wv.addWidget(write_hint)
        layout.addWidget(gb_write)

        # Buttons
        bb = QHBoxLayout()
        bb.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(dlg.reject)
        bb.addWidget(btn_cancel)

        btn_copy = QPushButton("复制 JSON")
        btn_copy.setDefault(True)

        def _on_copy():
            import json
            from PySide6.QtGui import QGuiApplication

            multi = self._radio_multi.isChecked()
            read_only = self._cb_write.isChecked()

            args: list[str] = ["-m", "app.mcp.standalone"]
            if not multi:
                args.extend(["--db", str(self.db_path)])
            if not read_only:
                args.append("--write-permission")
                args.append("session")

            # Build server name with suffixes
            name = "llm-cabinet"
            if not multi:
                # Use library directory name as suffix
                lib_name = self.library_root.name if self.library_root else "single"
                name += f"-{lib_name}"
            if read_only:
                name += "-ro"

            import app as _app_module
            project_root = str(Path(_app_module.__file__).resolve().parent.parent)
            entry = {
                "command": "python",
                "args": args,
                "env": {"PYTHONPATH": project_root},
            }
            full = {"mcpServers": {name: entry}}
            text = json.dumps(full, ensure_ascii=False, indent=2)
            QGuiApplication.clipboard().setText(text)
            dlg.accept()
            QMessageBox.information(self, "已复制",
                f"MCP 配置 JSON 已复制到剪贴板（名称：{name}）。\n请粘贴到 Claude Desktop / Cursor 的配置文件中。")

        btn_copy.clicked.connect(_on_copy)
        bb.addWidget(btn_copy)
        layout.addLayout(bb)

        dlg.exec()

    # =================================================================
    # 关于
    # =================================================================
    def _build_about_page(self) -> QWidget:
        from PySide6.QtGui import QIcon, QPixmap
        from ..utils import app_icon_path

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)

        title = QLabel("关于")
        title.setProperty("h1", True)
        lay.addWidget(title)

        # 顶部品牌区：图标 + 应用名 + 版本 + 副标题
        brand = QHBoxLayout()
        brand.setSpacing(14)

        # 高 DPI 友好的目标尺寸（逻辑像素 96，物理像素按 devicePixelRatio 放大）
        target_logical = 96
        dpr = self.devicePixelRatioF() or 1.0
        target_phys = int(round(target_logical * dpr))

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(target_logical, target_logical)
        icon_lbl.setAlignment(Qt.AlignCenter)
        ip = app_icon_path()
        if ip is not None:
            pix: QPixmap | None = None
            suffix = ip.suffix.lower()
            if suffix == ".ico":
                # ico 是多尺寸容器；QPixmap 直接加载只取第一帧（通常 16×16 → 拉伸糊）
                # 用 QIcon 加载，再让它按目标尺寸挑/合成最合适的子图
                icon = QIcon(str(ip))
                pix = icon.pixmap(target_phys, target_phys)
            else:
                pix = QPixmap(str(ip))
                if not pix.isNull():
                    pix = pix.scaled(
                        target_phys, target_phys,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
            if pix is not None and not pix.isNull():
                pix.setDevicePixelRatio(dpr)
                icon_lbl.setPixmap(pix)
        brand.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        name_lbl = QLabel("<b style='font-size:18pt;'>LLM Cabinet</b>")
        name_lbl.setTextFormat(Qt.RichText)
        text_col.addWidget(name_lbl)
        ver_lbl = QLabel(
            f"应用版本 v{__version__}　·　数据库 schema v{SCHEMA_VERSION}"
        )
        ver_lbl.setProperty("muted", True)
        ver_lbl.setToolTip(
            "应用版本（__version__）和数据库 schema 版本独立递增。\n"
            "升级新版应用打开旧 db 时，会自动备份并应用迁移脚本。"
        )
        text_col.addWidget(ver_lbl)
        text_col.addSpacing(4)
        sub_lbl = QLabel("带 AI 元数据助手的轻量级项目化文件管理器")
        sub_lbl.setWordWrap(True)
        text_col.addWidget(sub_lbl)
        text_col.addStretch(1)
        brand.addLayout(text_col, 1)

        brand_wrap = QWidget()
        brand_wrap.setLayout(brand)
        lay.addWidget(brand_wrap)

        # 数据隐私
        lay.addSpacing(18)
        privacy_row = QHBoxLayout()
        privacy_lbl = QLabel("数据隐私：")
        privacy_lbl.setFixedWidth(110)
        privacy_lbl.setProperty("muted", True)
        privacy_btn = QPushButton("📄 查看《数据隐私声明》")
        privacy_btn.setProperty("flat", True)
        privacy_btn.clicked.connect(self._open_privacy_doc)
        privacy_row.addWidget(privacy_lbl)
        privacy_row.addWidget(privacy_btn)
        privacy_row.addStretch(1)
        lay.addLayout(privacy_row)

        # License
        lic_row = QHBoxLayout()
        lic_lbl = QLabel("许可证：")
        lic_lbl.setFixedWidth(110)
        lic_lbl.setProperty("muted", True)
        lic_text = QLabel("MIT License")
        lic_row.addWidget(lic_lbl)
        lic_row.addWidget(lic_text)
        lic_row.addStretch(1)
        lay.addLayout(lic_row)

        # 免责声明
        disc_row = QHBoxLayout()
        disc_lbl = QLabel("免责声明：")
        disc_lbl.setFixedWidth(110)
        disc_lbl.setProperty("muted", True)
        disc_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        disc_text = QLabel(
            "本人将持续维护本软件，但不对任何使用过程中（包括但不限于异常使用、"
            "误操作、系统故障、第三方 LLM 服务异常）导致的文件丢失、数据损坏或"
            "其他损失承担责任。请通过定期备份保护重要数据。本软件按"
            "「原样」提供，详见 MIT License。"
        )
        disc_text.setWordWrap(True)
        disc_text.setProperty("muted", True)
        disc_row.addWidget(disc_lbl)
        disc_row.addWidget(disc_text, 1)
        lay.addLayout(disc_row)

        # 项目主页（GitHub）
        gh_row = QHBoxLayout()
        gh_lbl = QLabel("项目主页：")
        gh_lbl.setFixedWidth(110)
        gh_lbl.setProperty("muted", True)
        gh_link = QLabel(
            f"<a href='{HOMEPAGE_URL}'>{HOMEPAGE_URL}</a>"
        )
        gh_link.setTextFormat(Qt.RichText)
        gh_link.setOpenExternalLinks(True)
        gh_link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        gh_link.setToolTip("在浏览器中打开 GitHub 仓库")
        gh_row.addWidget(gh_lbl)
        gh_row.addWidget(gh_link)
        gh_row.addStretch(1)
        lay.addLayout(gh_row)

        lay.addStretch(1)
        return w

    def _open_privacy_doc(self) -> None:
        """打开 PRIVACY 文件。UI 是中文，优先打开中文版；找不到再退回英文版。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        # 源码运行模式：仓库根目录下；打包后：可执行文件目录 / _MEIPASS
        import sys as _sys
        roots: list[Path] = [Path(__file__).resolve().parents[2], Path.cwd()]
        meipass = getattr(_sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass))
        if getattr(_sys, "frozen", False):
            roots.append(Path(_sys.executable).parent)
        names = ("PRIVACY.zh-CN.md", "PRIVACY.md")
        for root in roots:
            for n in names:
                p = root / n
                if p.is_file():
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
                    return
        # 兜底：弹一个对话框直接展示在线版/本地缺失提示
        QMessageBox.information(
            self, "数据隐私声明",
            "未找到本地 PRIVACY 文件。请到项目仓库根目录查看。",
        )


class _DeleteFieldChoiceDialog(QDialog):
    """删除字段时让用户选择如何处理已有项目的值。"""

    def __init__(self, field_name: str, project_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("删除字段")
        self.setMinimumWidth(440)
        self.append_to_desc = False

        v = QVBoxLayout(self)
        v.setSpacing(10)

        lbl = QLabel(
            f"即将删除字段 「<b>{field_name}</b>」。<br>"
            f"当前有 <b>{project_count}</b> 个项目填写了该字段的值。"
            f"<br><br>请选择如何处理已有数据："
        )
        lbl.setTextFormat(Qt.RichText)
        lbl.setWordWrap(True)
        v.addWidget(lbl)

        self.rb_drop = QRadioButton("直接删除该字段及其所有相关数据")
        self.rb_append = QRadioButton(
            "保留数据：把每个项目的该字段值追加到 描述（description）末尾，再删除字段"
        )
        self.rb_drop.setChecked(True)

        grp = QButtonGroup(self)
        grp.addButton(self.rb_drop)
        grp.addButton(self.rb_append)

        v.addWidget(self.rb_drop)
        v.addWidget(self.rb_append)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _on_ok(self) -> None:
        self.append_to_desc = self.rb_append.isChecked()
        self.accept()


class _AddFieldDialog(QDialog):
    """添加新字段：字段名 + 类型。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        from ..models import FIELD_TYPES, FIELD_TYPE_LABELS

        self.setWindowTitle("新建字段")
        self.setMinimumWidth(360)

        self.name = ""
        self.type = "text"

        form = QFormLayout()
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("例如：译者、ISBN")
        form.addRow("字段名：", self.ed_name)

        self.cmb_type = QComboBox()
        for t in FIELD_TYPES:
            self.cmb_type.addItem(FIELD_TYPE_LABELS.get(t, t), t)
        form.addRow("类型：", self.cmb_type)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _on_ok(self) -> None:
        name = self.ed_name.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "字段名不能为空")
            return
        self.name = name
        self.type = self.cmb_type.currentData() or "text"
        self.accept()


class _FieldTypeChangeConfirmDialog(QDialog):
    """字段类型变更的确认对话框（task #19 Phase A）。

    类型切换不会动 ``project_field_values.value``（保留原字符串，切回旧类型即可
    恢复显示），但新控件读不动旧值，更新元数据时可能被空状态覆盖；同时该字段
    挂着的 LLM pending 建议也都按旧类型生成，应失效；prompt_hint 大概率跟新
    类型不匹配，提供"是否同时清空"的选项（默认不勾，避免误删用户写的 hint，
    文案上仍标注"（推荐）"提示用户多数场景下该清空）。
    """

    def __init__(
        self,
        field,
        new_type: str,
        n_values: int,
        m_pending: int,
        parent=None,
    ):
        super().__init__(parent)
        from ..models import FIELD_TYPE_LABELS

        self.setWindowTitle("确认更改字段类型")
        self.setMinimumWidth(480)
        self.clear_hint = False

        v = QVBoxLayout(self)
        v.setSpacing(10)

        old_label = FIELD_TYPE_LABELS.get(field.type, field.type)
        new_label = FIELD_TYPE_LABELS.get(new_type, new_type)
        head = QLabel(
            f"将「<b>{field.name}</b>」从 <b>{old_label}</b> 改为 "
            f"<b>{new_label}</b>？"
        )
        head.setTextFormat(Qt.RichText)
        head.setWordWrap(True)
        v.addWidget(head)

        # 三条说明（按需显示）
        bullets: list[str] = []
        if n_values > 0:
            bullets.append(
                f"已有 <b>{n_values}</b> 条非空记录的字段值会保留在数据库里，"
                "但新类型的控件可能读不出来（切回旧类型即可恢复显示；"
                "<b>更新项目元数据时</b>若提交空值会被覆盖）"
            )
        if m_pending > 0:
            bullets.append(
                f"待批准的 LLM 建议 <b>{m_pending}</b> 条会失效"
            )
        has_hint = bool((field.prompt_hint or "").strip())
        if has_hint:
            hint_preview = (field.prompt_hint or "").strip().replace("\n", " ")
            if len(hint_preview) > 30:
                hint_preview = hint_preview[:30] + "…"
            bullets.append(
                f"该字段的 LLM 提示「<i>{hint_preview}</i>」可能跟新类型不匹配"
            )

        if bullets:
            body = QLabel(
                "<ul style='margin-left:-20px'>"
                + "".join(f"<li>{b}</li>" for b in bullets)
                + "</ul>"
            )
            body.setTextFormat(Qt.RichText)
            body.setWordWrap(True)
            v.addWidget(body)

        # 清空 hint 的 checkbox（仅当 hint 非空时显示）
        # 默认不勾选以避免误删用户精心写的 hint；文字保留"（推荐）"
        # 提示用户大多数情况下应该清空（旧 hint 通常和新类型不匹配）。
        if has_hint:
            self.cb_clear_hint = QCheckBox("同时清空该字段的 LLM 提示（推荐）")
            self.cb_clear_hint.setChecked(False)
            v.addWidget(self.cb_clear_hint)
        else:
            self.cb_clear_hint = None

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("确认更改")
        bb.button(QDialogButtonBox.Cancel).setText("取消")
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _on_ok(self) -> None:
        self.clear_hint = bool(
            self.cb_clear_hint is not None and self.cb_clear_hint.isChecked()
        )
        self.accept()

    @classmethod
    def ask(
        cls, parent, field, new_type: str, n_values: int, m_pending: int,
    ) -> tuple[bool, bool]:
        """返回 ``(confirmed, clear_hint)``。"""
        dlg = cls(field, new_type, n_values, m_pending, parent=parent)
        ok = dlg.exec() == QDialog.Accepted
        return ok, (dlg.clear_hint if ok else False)


