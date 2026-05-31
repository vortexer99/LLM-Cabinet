"""批量导入文件夹对话框（task #10）。

展示扫描结果，让用户配置：
- 文件存储模式（链接 / 复制）
- 标题来源（project.json / 文件夹名）
- 未匹配字段策略（自动创建 / 追加到描述 / 忽略）+ 是否应用到全部
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QRadioButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ..importer import FieldPolicy, ImportOptions, ImportPlan


class ImportDialog(QDialog):
    """批量导入文件夹对话框。"""

    def __init__(self, plans: list[ImportPlan], parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量导入文件夹")
        self.resize(720, 560)

        self._plans = plans

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)

        # ---- 顶部说明 ----
        n_recognized = sum(
            1 for p in plans if p.has_project_json and not p.parse_error
        )
        head = QLabel(
            f"共 {len(plans)} 个文件夹将被导入为新项目；"
            f"其中 {n_recognized} 个识别到 project.json。"
        )
        head.setWordWrap(True)
        lay.addWidget(head)

        # ---- 文件夹清单（树形：父行=文件夹，子行=未匹配字段预览） ----
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["文件夹", "状态"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setAlternatingRowColors(True)
        h = self.tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._populate_tree()
        lay.addWidget(self.tree, 1)

        # ---- 选项区 ----
        lay.addWidget(self._build_options_group())

        # ---- 按钮 ----
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("📥  开始导入")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    # ------------------------------------------------------------------ helpers
    def _populate_tree(self) -> None:
        for plan in self._plans:
            parent = QTreeWidgetItem([plan.folder.name, plan.status_text])
            parent.setToolTip(0, str(plan.folder))
            self.tree.addTopLevelItem(parent)

            if plan.has_project_json and plan.project_json:
                self._add_preview_children(parent, plan)

        self.tree.expandToDepth(0)

    def _add_preview_children(self, parent: QTreeWidgetItem, plan: ImportPlan) -> None:
        """把 project.json 的关键预览信息作为子节点展示。"""
        pj = plan.project_json or {}
        proj_data = pj.get("project") if isinstance(pj.get("project"), dict) else {}

        title = proj_data.get("title") or "(无标题)"
        QTreeWidgetItem(parent, ["标题", str(title)])

        tags = pj.get("tags") or []
        if isinstance(tags, list) and tags:
            QTreeWidgetItem(parent, ["标签", "、".join(str(t) for t in tags)])

        # 字段值
        fvs = pj.get("field_values") or []
        if isinstance(fvs, list) and fvs:
            fv_node = QTreeWidgetItem(parent, ["字段值", f"{len(fvs)} 项"])
            for fv in fvs:
                if not isinstance(fv, dict):
                    continue
                fname = fv.get("field_name") or "(无名)"
                value = fv.get("value")
                value_str = "" if value is None else str(value)
                child = QTreeWidgetItem(
                    fv_node, [str(fname), value_str[:200]]
                )
                if fname in plan.unmatched_fields:
                    child.setText(0, f"⚠ {fname}（库内不存在）")

        # 未匹配字段单独提示
        if plan.unmatched_fields:
            warn = QTreeWidgetItem(parent, [
                "⚠ 未匹配字段",
                "、".join(plan.unmatched_fields),
            ])
            warn.setToolTip(1, "这些字段在当前库的字段表中不存在；按下方策略处理。")

    def _build_options_group(self) -> QWidget:
        gb = QGroupBox("选项")
        v = QVBoxLayout(gb)
        v.setSpacing(8)

        # 文件存储模式
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("文件存储模式："))
        self._rb_link = QRadioButton("🔗 链接到原位置")
        self._rb_copy = QRadioButton("📦 复制到仓储")
        self._rb_link.setChecked(True)
        bg1 = QButtonGroup(self)
        bg1.addButton(self._rb_link)
        bg1.addButton(self._rb_copy)
        row1.addWidget(self._rb_link)
        row1.addWidget(self._rb_copy)
        row1.addStretch(1)
        v.addLayout(row1)

        link_hint = QLabel(
            "  链接到原位置仅在原机器有效；跨机器请选「复制到仓储」。"
        )
        link_hint.setStyleSheet("color: gray; font-size: 11px;")
        v.addWidget(link_hint)

        # 标题来源
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("项目标题：    "))
        self._rb_title_pj = QRadioButton("沿用 project.json")
        self._rb_title_folder = QRadioButton("文件夹名")
        self._rb_title_pj.setChecked(True)
        bg2 = QButtonGroup(self)
        bg2.addButton(self._rb_title_pj)
        bg2.addButton(self._rb_title_folder)
        row2.addWidget(self._rb_title_pj)
        row2.addWidget(self._rb_title_folder)
        row2.addStretch(1)
        v.addLayout(row2)
        title_hint = QLabel(
            "  「沿用 project.json」时若无配置，自动 fallback 到文件夹名。"
        )
        title_hint.setStyleSheet("color: gray; font-size: 11px;")
        v.addWidget(title_hint)

        # 未匹配字段策略
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("未匹配字段："))
        self._rb_field_create = QRadioButton("自动创建")
        self._rb_field_append = QRadioButton("追加到描述")
        self._rb_field_ignore = QRadioButton("忽略")
        self._rb_field_append.setChecked(True)  # 默认值（保守）
        bg3 = QButtonGroup(self)
        bg3.addButton(self._rb_field_create)
        bg3.addButton(self._rb_field_append)
        bg3.addButton(self._rb_field_ignore)
        row3.addWidget(self._rb_field_create)
        row3.addWidget(self._rb_field_append)
        row3.addWidget(self._rb_field_ignore)
        row3.addStretch(1)
        v.addLayout(row3)
        self._cb_apply_all = QCheckBox("应用到本次所有项目")
        self._cb_apply_all.setChecked(True)
        v.addWidget(self._cb_apply_all)
        field_hint = QLabel(
            "  ⚠「自动创建」会把未知字段写入当前库的字段表，影响整个库。\n"
            "  取消勾选「应用到本次所有项目」时，遇到未匹配字段会逐项目询问。"
        )
        field_hint.setStyleSheet("color: gray; font-size: 11px;")
        field_hint.setWordWrap(True)
        v.addWidget(field_hint)

        return gb

    # ------------------------------------------------------------------ getters
    def options(self) -> ImportOptions:
        if self._rb_field_create.isChecked():
            policy: FieldPolicy = "create"
        elif self._rb_field_ignore.isChecked():
            policy = "ignore"
        else:
            policy = "append_to_desc"
        return ImportOptions(
            storage_mode="copy" if self._rb_copy.isChecked() else "link",
            title_source=(
                "project_json" if self._rb_title_pj.isChecked() else "folder_name"
            ),
            field_policy=policy,
            field_policy_apply_all=self._cb_apply_all.isChecked(),
        )


# =============================================================================
# 单项目级"未匹配字段策略"询问对话框
# =============================================================================
class FieldPolicyAskDialog(QDialog):
    """当用户取消勾选"应用到全部"时，对每个含未匹配字段的项目弹这个询问。"""

    def __init__(self, folder: Path, unmatched_fields: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("未匹配字段处理")
        self.setMinimumWidth(420)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)

        head = QLabel(
            f"项目「<b>{folder.name}</b>」中有 {len(unmatched_fields)} 个字段在当前库中不存在："
        )
        head.setWordWrap(True)
        head.setTextFormat(Qt.RichText)
        lay.addWidget(head)

        names = QLabel("、".join(unmatched_fields))
        names.setWordWrap(True)
        names.setStyleSheet("color: gray;")
        lay.addWidget(names)

        self._rb_create = QRadioButton("自动创建（写入当前库的字段表）")
        self._rb_append = QRadioButton("追加到描述（不动 schema）")
        self._rb_ignore = QRadioButton("忽略（丢弃这些字段值）")
        self._rb_append.setChecked(True)
        bg = QButtonGroup(self)
        bg.addButton(self._rb_create)
        bg.addButton(self._rb_append)
        bg.addButton(self._rb_ignore)
        lay.addWidget(self._rb_create)
        lay.addWidget(self._rb_append)
        lay.addWidget(self._rb_ignore)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def policy(self) -> FieldPolicy:
        if self._rb_create.isChecked():
            return "create"
        if self._rb_ignore.isChecked():
            return "ignore"
        return "append_to_desc"
