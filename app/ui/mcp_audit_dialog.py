"""MCP 操作记录查看面板（task #24）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..repository import Repository
from ..utils import utc_to_local_str

_TOOL_LABELS = {
    "create_project": "创建项目",
    "update_project": "修改项目",
    "add_tag": "添加标签",
    "remove_tag": "移除标签",
    "add_file": "添加文件",
    "remove_file": "删除文件",
    "export_project": "导出项目",
    # 只读工具（基本不在 audit 中）
    "get_project": "查看项目",
    "list_files": "列出文件",
    "search_projects": "搜索项目",
    "count_projects": "统计项目",
    "list_libraries": "列出库",
    "switch_library": "切换库",
    "get_field_definition": "查看字段定义",
    # task #23 已下线，历史记录兼容
    "import_folder": "导入文件夹 *",
    "apply_suggestion": "应用建议 *",
    "trigger_llm_suggestion": "触发 LLM 建议 *",
    "list_pending_suggestions": "列出建议 *",
}

_STATUS_ICONS = {
    "success": "🟢 成功",
    "denied": "🟡 拒绝",
    "error": "🔴 失败",
}

PAGE_SIZE = 50


class MCPAuditDialog(QDialog):
    """MCP 操作记录 + MCP 修改项目双 tab 对话框。"""

    def __init__(self, repo: Repository, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.setWindowTitle("MCP 操作记录")
        self.resize(780, 560)

        v = QVBoxLayout(self)

        title = QLabel("MCP 操作记录")
        title.setProperty("h1", True)
        v.addWidget(title)

        # ---- tabs ----
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_audit_tab(), "操作记录")
        self.tabs.addTab(self._build_modified_tab(), "MCP 修改过的项目")
        v.addWidget(self.tabs, 1)

        # ---- bottom buttons ----
        bb = QDialogButtonBox()
        bb.addButton(QDialogButtonBox.Close).clicked.connect(self.reject)
        v.addWidget(bb)

        self._reload_audit()

    # ================================================================== audit tab

    def _build_audit_tab(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 4, 0, 0)

        # --- filters ---
        f_row = QHBoxLayout()
        f_row.addWidget(QLabel("客户端:"))
        self.cmb_client = QComboBox()
        self.cmb_client.currentIndexChanged.connect(self._reload_audit)
        f_row.addWidget(self.cmb_client)

        f_row.addWidget(QLabel("操作:"))
        self.cmb_tool = QComboBox()
        self.cmb_tool.currentIndexChanged.connect(self._reload_audit)
        f_row.addWidget(self.cmb_tool)

        f_row.addWidget(QLabel("状态:"))
        self.cmb_status = QComboBox()
        self.cmb_status.addItems(["全部", "成功", "拒绝", "失败"])
        self.cmb_status.currentIndexChanged.connect(self._reload_audit)
        f_row.addWidget(self.cmb_status)

        b_refresh = QPushButton("⟳ 刷新")
        b_refresh.clicked.connect(self._reload_audit)
        f_row.addWidget(b_refresh)

        b_clear = QPushButton("清空记录")
        b_clear.clicked.connect(self._clear_audit)
        f_row.addWidget(b_clear)

        f_row.addStretch()
        l.addLayout(f_row)

        # --- table ---
        self.tbl = QTableWidget(0, 7)
        self.tbl.setHorizontalHeaderLabels([
            "时间", "客户端", "操作", "结果", "参数摘要", "错误信息", "ID",
        ])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setShowGrid(False)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.tbl.horizontalHeader()
        # Fixed-width columns
        for col, width in [(0, 160), (1, 100), (2, 100), (3, 70), (6, 60)]:
            h.setSectionResizeMode(col, QHeaderView.Fixed)
            self.tbl.setColumnWidth(col, width)
        # Resizable columns — user can adjust
        h.setSectionResizeMode(4, QHeaderView.Interactive)
        self.tbl.setColumnWidth(4, 250)
        h.setSectionResizeMode(5, QHeaderView.Interactive)
        self.tbl.setColumnWidth(5, 150)
        self.tbl.setColumnHidden(1, True)  # 客户端 — 固定为 standalone，无意义
        self.tbl.setColumnHidden(6, True)  # ID
        l.addWidget(self.tbl, 1)

        # --- stats + pagination ---
        p_row = QHBoxLayout()
        self.lbl_stat = QLabel()
        p_row.addWidget(self.lbl_stat)
        p_row.addStretch()
        self.btn_prev = QPushButton("← 上一页")
        self.btn_prev.clicked.connect(self._page_prev)
        p_row.addWidget(self.btn_prev)
        self.lbl_page = QLabel("第 1/1 页")
        p_row.addWidget(self.lbl_page)
        self.btn_next = QPushButton("下一页 →")
        self.btn_next.clicked.connect(self._page_next)
        p_row.addWidget(self.btn_next)
        l.addLayout(p_row)

        self._page = 0
        self._total = 0
        return w

    def _reload_audit(self) -> None:
        """Reload audit table with current filters."""
        client = self.cmb_client.currentData() or ""
        tool = self.cmb_tool.currentData() or ""
        status_text = self.cmb_status.currentText()
        status = {"成功": "success", "拒绝": "denied", "失败": "error"}.get(status_text, "")

        self._total = self.repo.count_mcp_audit(
            client_name=client, tool_name=tool, result_status=status,
        )
        rows = self.repo.list_mcp_audit(
            offset=self._page * PAGE_SIZE, limit=PAGE_SIZE,
            client_name=client, tool_name=tool, result_status=status,
        )

        self.tbl.setRowCount(0)
        self.tbl.setRowCount(len(rows))
        for i, r in enumerate(rows):
            ts = utc_to_local_str(r.get("ts", "") or "", fmt="%Y-%m-%d %H:%M:%S")
            self.tbl.setItem(i, 0, self._item(ts))
            self.tbl.setItem(i, 1, self._item(r.get("client_name") or "-"))
            self.tbl.setItem(i, 2, self._item(_TOOL_LABELS.get(r.get("tool_name", ""), r.get("tool_name", ""))))
            status_val = r.get("result_status", "success")
            self.tbl.setItem(i, 3, self._item(_STATUS_ICONS.get(status_val, status_val)))
            args = r.get("arguments_json", "") or ""
            self.tbl.setItem(i, 4, self._item(self._format_args(args), tooltip=args))
            err = r.get("error_message", "") or ""
            self.tbl.setItem(i, 5, self._item(self._truncate(err, 40), tooltip=err))
            self.tbl.setItem(i, 6, self._item(str(r.get("id", ""))))

        total_pages = max(1, (self._total + PAGE_SIZE - 1) // PAGE_SIZE)
        self.lbl_stat.setText(f"共 {self._total} 条记录")
        self.lbl_page.setText(f"第 {self._page + 1}/{total_pages} 页")
        self.btn_prev.setEnabled(self._page > 0)
        self.btn_next.setEnabled(self._page + 1 < total_pages)

        # Populate client/tool combos (once)
        if self.cmb_client.count() <= 1:
            self._populate_combos()

    def _populate_combos(self) -> None:
        """Fill client and tool dropdowns with DISTINCT values."""
        # Block signals to avoid recursive _reload_audit
        self.cmb_client.blockSignals(True)
        self.cmb_tool.blockSignals(True)

        self.cmb_client.clear()
        self.cmb_client.addItem("全部", "")
        for name in self.repo.list_mcp_audit_clients():
            self.cmb_client.addItem(name, name)

        self.cmb_tool.clear()
        self.cmb_tool.addItem("全部", "")
        for tool in self.repo.list_mcp_audit_tools():
            label = _TOOL_LABELS.get(tool, tool)
            self.cmb_tool.addItem(label, tool)

        self.cmb_client.blockSignals(False)
        self.cmb_tool.blockSignals(False)

    def _clear_audit(self) -> None:
        if confirm(
            self, "清空记录",
            "确定要清空所有 MCP 操作记录吗？此操作不可恢复。",
            yes="清空", danger=True,
        ):
            self.repo.clear_mcp_audit()
            self._page = 0
            self._reload_audit()

    def _page_prev(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._reload_audit()

    def _page_next(self) -> None:
        total = max(1, (self._total + PAGE_SIZE - 1) // PAGE_SIZE)
        if self._page + 1 < total:
            self._page += 1
            self._reload_audit()

    # =============================================================== modified tab

    def _build_modified_tab(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 4, 0, 0)

        self.tbl_mod = QTableWidget(0, 4)
        self.tbl_mod.setHorizontalHeaderLabels([
            "项目 ID", "标题", "最近 MCP 操作", "最近操作类型",
        ])
        self.tbl_mod.verticalHeader().setVisible(False)
        self.tbl_mod.setShowGrid(False)
        self.tbl_mod.setAlternatingRowColors(True)
        self.tbl_mod.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_mod.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.tbl_mod.horizontalHeader()
        self.tbl_mod.setColumnWidth(0, 80)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_mod.setColumnWidth(2, 160)
        self.tbl_mod.setColumnWidth(3, 140)
        l.addWidget(self.tbl_mod, 1)

        self._reload_modified()
        return w

    def _reload_modified(self) -> None:
        """Reload MCP-modified projects table."""
        projects = self.repo.list_mcp_modified_projects()

        # Resolve last tool per project
        last_tools: dict[int, str] = {}
        if projects:
            pids = [p["id"] for p in projects]
            last_tools = self.repo.last_mcp_tool_by_project(pids)

        self.tbl_mod.setRowCount(0)
        self.tbl_mod.setRowCount(len(projects))
        for i, p in enumerate(projects):
            pid = p["id"]
            self.tbl_mod.setItem(i, 0, self._item(str(pid)))
            self.tbl_mod.setItem(i, 1, self._item(p.get("title", "")))
            self.tbl_mod.setItem(i, 2, self._item(p.get("mcp_modified_at", "") or ""))
            tool_name = last_tools.get(pid, "")
            self.tbl_mod.setItem(i, 3, self._item(_TOOL_LABELS.get(tool_name, tool_name)))

    # =================================================================== helpers

    @staticmethod
    def _item(text: str, tooltip: str = "") -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        if tooltip:
            item.setToolTip(tooltip)
        return item

    @staticmethod
    def _format_args(args_json: str) -> str:
        """友好显示参数摘要。"""
        if not args_json:
            return "-"
        import json as _json
        try:
            obj = _json.loads(args_json)
            if not isinstance(obj, dict):
                return MCPAuditDialog._truncate(args_json, 60)
            # 只有 project_id 的历史记录 → 简写
            if list(obj.keys()) == ["project_id"]:
                return f"项目 #{obj['project_id']}（参数未记录）"
            # update_project: 显示改动摘要
            if "title" in obj and "field_values" in obj:
                parts = [f"项目 #{obj.get('project_id', '?')}"]
                if obj.get("title", "(不变)") != "(不变)":
                    parts.append("改标题")
                if obj.get("description", "(不变)") != "(不变)":
                    parts.append("改描述")
                if obj.get("tags", "(不变)") != "(不变)":
                    parts.append("改标签")
                if obj.get("field_values", "(不变)") != "(不变)":
                    parts.append("填字段值")
                return "，".join(parts)
        except Exception:
            pass
        return MCPAuditDialog._truncate(args_json, 60)

    @staticmethod
    def _truncate(s: str, max_len: int) -> str:
        s = s.strip()
        if len(s) <= max_len:
            return s
        return s[:max_len] + "…"

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._reload_audit()
        self._reload_modified()
