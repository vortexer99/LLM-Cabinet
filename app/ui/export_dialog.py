"""项目导出对话框（task #09 / task #28 T2 批量导出扩展）。

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
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..models import Project
from .dialogs import warn


class ExportDialog(QDialog):
    """收集导出参数（单项目或批量）。"""

    def __init__(
        self,
        project: Project | None = None,
        n_files: int = 0,
        last_export_dir: str = "",
        projects: list[tuple[int, str, int]] | None = None,  # [(pid, title, n_files), ...]
        parent=None,
    ):
        super().__init__(parent)
        self.setMinimumWidth(520)

        self._project = project  # 单项目模式
        self._projects = projects  # 批量模式 [(pid, title, n_files), ...]
        self._n_files = n_files
        self._selected_project_indices: set[int] = set()

        is_batch = projects is not None
        if is_batch:
            self._init_batch_ui()
        else:
            self._init_single_ui(last_export_dir)

    # ============================================================ single project UI
    def _init_single_ui(self, last_export_dir: str) -> None:
        """单项目导出 UI。"""
        self.setWindowTitle("导出项目")

        v = QVBoxLayout(self)
        v.setSpacing(10)

        # 顶部：项目摘要
        title = self._project.title or "(未命名)" if self._project else "(未命名)"
        head = QLabel(
            f"项目：「<b>{_escape(title)}</b>」  ·  共 <b>{self._n_files}</b> 个文件"
        )
        head.setTextFormat(Qt.RichText)
        v.addWidget(head)

        # 导出位置
        self._add_location_ui(v, last_export_dir)

        # 选项区
        self._add_options_ui(v)

        v.addStretch(1)

        # 按钮
        self._add_buttons(v, "📤  执行导出")

    # ============================================================ batch export UI
    def _init_batch_ui(self) -> None:
        """批量导出 UI。"""
        self.setWindowTitle(f"批量导出（{len(self._projects)} 个项目）")

        v = QVBoxLayout(self)
        v.setSpacing(10)

        # 提示
        hint = QLabel(f"将导出以下项目到所选目录，每个项目一个子文件夹：")
        v.addWidget(hint)

        # 项目列表（CheckBox）
        from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QCheckBox, QFrame

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(150)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(4)

        self._project_checkboxes: list[QCheckBox] = []
        for i, (pid, title, n_files) in enumerate(self._projects):
            cb = QCheckBox(f"{title} ({n_files} 个文件)")
            cb.setChecked(n_files > 0)  # 默认勾选有文件的
            if n_files == 0:
                cb.setEnabled(False)  # 空项目灰显
                cb.setToolTip("空项目无法导出")
            else:
                self._selected_project_indices.add(i)
                cb.stateChanged.connect(lambda state, idx=i: self._on_project_toggled(idx, state))
            self._project_checkboxes.append(cb)
            scroll_layout.addWidget(cb)

        scroll.setWidget(scroll_widget)
        v.addWidget(scroll)

        # 导出位置
        last_dir = ""
        self._add_location_ui(v, last_dir)

        # 导出模式（task #28 T1 扩展）
        self._add_export_mode_ui(v)

        v.addStretch(1)

        # 按钮
        self._add_buttons(v, f"📤  导出 {len(self._selected_project_indices)} 个项目")

    def _on_project_toggled(self, index: int, state: int) -> None:
        """项目勾选状态变化。"""
        if state == 2:  # Checked
            self._selected_project_indices.add(index)
        else:
            self._selected_project_indices.discard(index)

        # 更新按钮文本
        count = len(self._selected_project_indices)
        self.btn_run.setText(f"📤  导出 {count} 个项目")

    def _add_location_ui(self, v: QVBoxLayout, last_export_dir: str) -> None:
        """添加导出位置选择 UI。"""
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

    def _add_options_ui(self, v: QVBoxLayout) -> None:
        """添加选项 UI（单项目模式）。"""
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

        v.addWidget(gb)

    def _add_export_mode_ui(self, v: QVBoxLayout) -> None:
        """添加导出模式 UI（批量模式，task #28 T1 扩展）。"""
        # 导出模式
        mode_gb = QGroupBox("导出模式")
        mode_v = QVBoxLayout(mode_gb)
        self.radio_package = QRadioButton("导出为独立包（包含 files/）")
        self.radio_package.setChecked(True)
        self.radio_metadata = QRadioButton("仅导出项目元数据（project.json）")
        mode_v.addWidget(self.radio_package)
        mode_v.addWidget(self.radio_metadata)
        v.addWidget(mode_gb)

        # 导出格式
        format_gb = QGroupBox("导出格式")
        format_v = QVBoxLayout(format_gb)
        self.radio_directory = QRadioButton("目录形式（可读、便于手动调整）")
        self.radio_directory.setChecked(True)
        self.radio_zip = QRadioButton("ZIP 打包（便于分享）")
        format_v.addWidget(self.radio_directory)
        format_v.addWidget(self.radio_zip)
        v.addWidget(format_gb)

        # 文件结构
        structure_gb = QGroupBox("文件目录结构")
        structure_v = QVBoxLayout(structure_gb)
        self.radio_preserve = QRadioButton("保留项目内目录结构")
        self.radio_preserve.setChecked(True)
        self.radio_flat = QRadioButton("拍平到 files/（所有文件平铺）")
        structure_v.addWidget(self.radio_preserve)
        structure_v.addWidget(self.radio_flat)
        v.addWidget(structure_gb)

        # 选项
        opts_gb = QGroupBox("内容选项")
        opts_v = QVBoxLayout(opts_gb)
        self.chk_readme = QCheckBox("包含 README.md")
        self.chk_readme.setChecked(True)
        self.chk_llm_history = QCheckBox("包含 LLM 任务历史")
        self.chk_copy_link = QCheckBox("复制链接(🔗)文件")
        self.chk_copy_link.setChecked(True)
        opts_v.addWidget(self.chk_readme)
        opts_v.addWidget(self.chk_llm_history)
        opts_v.addWidget(self.chk_copy_link)
        v.addWidget(opts_gb)

    def _add_buttons(self, v: QVBoxLayout, btn_text: str) -> None:
        """添加按钮。"""
        bb = QDialogButtonBox()
        self.btn_run = bb.addButton(btn_text, QDialogButtonBox.AcceptRole)
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

    def selected_projects(self) -> list[int]:
        """返回选中的项目索引列表。"""
        return list(self._selected_project_indices)

    # task #28 T1 扩展
    def mode(self) -> str:
        """导出模式：package / metadata_only"""
        if hasattr(self, 'radio_metadata'):
            return "metadata_only" if self.radio_metadata.isChecked() else "package"
        return "package"

    def export_format(self) -> str:
        """导出格式：directory / zip"""
        if hasattr(self, 'radio_zip'):
            return "zip" if self.radio_zip.isChecked() else "directory"
        return "directory"

    def preserve_structure(self) -> bool:
        """是否保留目录结构"""
        if hasattr(self, 'radio_preserve'):
            return self.radio_preserve.isChecked()
        return True

    def include_readme(self) -> bool:
        """是否包含 README"""
        return getattr(self, 'chk_readme', QCheckBox()).isChecked()

    def include_llm_history(self) -> bool:
        """是否包含 LLM 历史"""
        return getattr(self, 'chk_llm_history', QCheckBox()).isChecked()

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
            warn(self, "请填写导出位置", "请先选择一个导出文件夹。")
            return
        p = Path(raw).expanduser()
        if not p.exists():
            warn(
                self, "路径不存在",
                f"目录不存在：{p}\n请先创建后再试，或点击「浏览」选择已有目录。",
            )
            return
        if not p.is_dir():
            warn(self, "不是目录", f"不是一个目录：{p}")
            return

        # 批量模式检查是否选中了项目
        if self._projects is not None and not self._selected_project_indices:
            warn(self, "未选中项目", "请至少选择一个要导出的项目。")
            return

        self.accept()


def _escape(s: str) -> str:
    """轻量 HTML 转义，避免标题里的 & < > 把富文本搞乱。"""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )