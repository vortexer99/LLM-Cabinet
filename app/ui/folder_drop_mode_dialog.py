"""拖入多个文件夹时的"模式选择"对话框（task #10）。

询问用户：是把这些文件夹**合并为同一个新项目**，还是**每个文件夹各建一个项目**。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QRadioButton, QVBoxLayout,
)


class FolderDropModeDialog(QDialog):
    """轻量二选一对话框。``mode()`` 返回 ``"merge"`` 或 ``"separate"``。"""

    def __init__(self, n_folders: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量导入文件夹")
        self.setMinimumWidth(360)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 12)
        lay.setSpacing(10)

        head = QLabel(f"检测到拖入 {n_folders} 个文件夹，请选择导入方式：")
        head.setWordWrap(True)
        lay.addWidget(head)

        self._rb_separate = QRadioButton("每个文件夹分别建立一个项目")
        self._rb_merge = QRadioButton("合并为同一个新项目")
        self._rb_separate.setChecked(True)  # 默认 separate（更安全可后悔）
        lay.addWidget(self._rb_separate)
        lay.addWidget(self._rb_merge)

        hint = QLabel(
            "提示：「分别建立」可识别每个文件夹下的 project.json 配置文件；"
            "「合并为同一项目」则不识别配置，直接把所有文件加入同一个新项目。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        lay.addWidget(hint)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("下一步 →")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def mode(self) -> str:
        return "separate" if self._rb_separate.isChecked() else "merge"
