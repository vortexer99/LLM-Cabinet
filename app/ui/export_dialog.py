"""项目导出对话框（task #09）。

不在对话框内部执行实际导出，仅收集参数；执行交给 ``MainWindow.action_export_project``。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..models import Project


class ExportDialog(QDialog):
    """收集"导出到哪里"和"是否复制链接文件"两个参数。"""

    def __init__(
        self,
        project: Project,
        n_files: int,
        last_export_dir: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("导出项目")
        self.setMinimumWidth(520)

        self._project = project
        self._n_files = n_files

        v = QVBoxLayout(self)
        v.setSpacing(10)

        # 顶部：项目摘要
        title = project.title or "(未命名)"
        head = QLabel(
            f"项目：「<b>{_escape(title)}</b>」  ·  共 <b>{n_files}</b> 个文件"
        )
        head.setTextFormat(Qt.RichText)
        v.addWidget(head)

        # 导出位置
        loc_lbl = QLabel("导出位置：")
        v.addWidget(loc_lbl)

        loc_row = QHBoxLayout()
        self.ed_dir = QLineEdit(last_export_dir or str(Path.home()))
        self.ed_dir.setPlaceholderText("选择一个文件夹作为导出根目录…")
        btn_browse = QPushButton("📂  浏览…")
        btn_browse.clicked.connect(self._on_browse)
        loc_row.addWidget(self.ed_dir, 1)
        loc_row.addWidget(btn_browse)
        v.addLayout(loc_row)

        hint = QLabel(
            "将在所选位置下创建一个以项目标题命名的子文件夹，包含 "
            "<code>project.json</code> / <code>files.json</code> / "
            "<code>README.md</code> / <code>files/</code>。"
        )
        hint.setTextFormat(Qt.RichText)
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        v.addWidget(hint)

        # 选项区
        gb = QGroupBox("选项")
        gv = QVBoxLayout(gb)
        gv.setContentsMargins(10, 8, 10, 8)
        gv.setSpacing(4)

        self.chk_copy_link = QCheckBox(
            "复制链接模式（🔗）的原始文件到导出目录"
        )
        self.chk_copy_link.setChecked(True)
        self.chk_copy_link.setToolTip(
            "勾选（推荐）：导出包自包含，可拷贝/迁移到其它机器后完整恢复。\n"
            "不勾选：链接文件仅在 files.json 中记录其原绝对路径，体积小但依赖原机器。\n\n"
            "仓储模式（📦）文件总是会被复制（它们本就是库内副本）。"
        )
        gv.addWidget(self.chk_copy_link)

        sub_hint = QLabel(
            "未勾选时，链接模式文件不会被复制；只在 files.json 中记录其"
            "原绝对路径，便于在原机器上恢复。"
        )
        sub_hint.setWordWrap(True)
        sub_hint.setProperty("muted", True)
        gv.addWidget(sub_hint)

        v.addWidget(gb)
        v.addStretch(1)

        # 按钮
        bb = QDialogButtonBox()
        self.btn_run = bb.addButton("📤  执行导出", QDialogButtonBox.AcceptRole)
        self.btn_run.setProperty("primary", True)
        bb.addButton("取消", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    # ============================================================ getters
    def target_root(self) -> Path:
        return Path(self.ed_dir.text().strip()).expanduser()

    def copy_link_files(self) -> bool:
        return self.chk_copy_link.isChecked()

    # ============================================================ slots
    def _on_browse(self) -> None:
        start = self.ed_dir.text().strip() or str(Path.home())
        d = QFileDialog.getExistingDirectory(
            self, "选择导出位置", start,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if d:
            self.ed_dir.setText(d)

    def _on_accept(self) -> None:
        raw = self.ed_dir.text().strip()
        if not raw:
            QMessageBox.warning(self, "请填写导出位置", "请先选择一个导出文件夹。")
            return
        p = Path(raw).expanduser()
        if not p.exists():
            QMessageBox.warning(
                self, "路径不存在",
                f"目录不存在：{p}\n请先创建后再试，或点击「浏览」选择已有目录。",
            )
            return
        if not p.is_dir():
            QMessageBox.warning(self, "不是目录", f"不是一个目录：{p}")
            return
        self.accept()


def _escape(s: str) -> str:
    """轻量 HTML 转义，避免标题里的 & < > 把富文本搞乱。"""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )
