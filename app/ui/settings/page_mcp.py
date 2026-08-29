"""设置页 · MCP 集成（task #35 T3：从 settings_dialog.py 拆分，方法体未改动）。

Mixin：设置页 · MCP 集成
"""
from __future__ import annotations

import json
from pathlib import Path

import app as _app_module
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
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

from ... import HOMEPAGE_URL, __version__
from ...db import SCHEMA_VERSION
from ...models import FIELD_TYPE_LABELS, FIELD_TYPES
from ...repository import Repository
from ...utils import app_data_dir, reveal_in_explorer
from ..dialogs import info, warn


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



class McpPageMixin:
    """设置页 · MCP 集成"""

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
            info(self, "已复制",
                f"MCP 配置 JSON 已复制到剪贴板（名称：{name}）。\n请粘贴到 Claude Desktop / Cursor 的配置文件中。")

        btn_copy.clicked.connect(_on_copy)
        bb.addWidget(btn_copy)
        layout.addLayout(bb)

        dlg.exec()
