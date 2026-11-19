"""按导入时的选择顺序，为选中的项目批量设置封面。"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QLabel, QProgressDialog,
    QVBoxLayout, QWidget,
)

from ..cover_files import CoverFileError
from ..import_preferences import IMPORT_VIDEO_COVER_KEY, apply_project_auto_cover
from ..library import Library
from ..repository import Repository
from .dialogs import info, warn


class BatchCoverDialog(QDialog):
    """批次选项只在本次操作中生效。"""

    def __init__(self, parent: QWidget | None, count: int, include_video: bool):
        super().__init__(parent)
        self.setWindowTitle("批量设置封面")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"已选 {count} 个项目。每个项目按目录和文件名的自然顺序选取第一张图片。"
        ))
        self.include_video = QCheckBox("没有图片时，截取第一个视频的首帧")
        self.include_video.setChecked(include_video)
        self.replace_existing = QCheckBox("替换已有封面")
        layout.addWidget(self.include_video)
        layout.addWidget(self.replace_existing)
        buttons = QDialogButtonBox()
        buttons.addButton("开始设置", QDialogButtonBox.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def run_batch_auto_cover(parent: QWidget | None, repo: Repository, library: Library,
                         project_ids: list[int]) -> bool:
    """固定本次选择，逐项目处理并汇总，返回是否需要刷新界面。"""
    ids = list(dict.fromkeys(project_ids))
    if not ids:
        return False
    dialog = BatchCoverDialog(parent, len(ids), repo.get_setting(IMPORT_VIDEO_COVER_KEY, "0") == "1")
    accepted = dialog.exec() == QDialog.Accepted
    include_video = dialog.include_video.isChecked()
    replace_existing = dialog.replace_existing.isChecked()
    dialog.deleteLater()
    if not accepted:
        return False

    progress = QProgressDialog("正在设置封面…", "处理完当前项目后停止", 0, len(ids), parent)
    progress.setWindowTitle("批量设置封面")
    progress.setWindowModality(Qt.ApplicationModal)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.setMinimumDuration(0)
    changed = kept = skipped = failed = warnings = 0
    details: list[str] = []
    stop_reason = "取消"
    try:
        for index, pid in enumerate(ids):
            progress.setValue(index)
            QApplication.processEvents()
            if progress.wasCanceled():
                break
            label = f"项目 ID {pid}"
            stage = "读取项目"
            try:
                project = repo.get_project(pid)
                if project is None:
                    raise LookupError("所选项目已不存在。")
                label = f"{project.title or '未命名项目'}（ID {pid}）"
                progress.setLabelText(f"正在处理 {index + 1} / {len(ids)}　{label}")
                if project.cover_file_id is not None and not replace_existing:
                    kept += 1
                    continue
                stage = "选择并设置封面"
                applied, notes = apply_project_auto_cover(
                    repo, project, library, include_video=include_video,
                    replace_existing=replace_existing,
                )
                if applied:
                    changed += 1
                else:
                    skipped += 1
                if notes:
                    warnings += 1
                    details.append(label + "\n" + "\n".join(notes))
            except Exception as exc:
                failed += 1
                logging.exception("批量设置封面失败：%s", label)
                details.append(f"{label}\n{stage}失败：{type(exc).__name__}: {exc}")
                if isinstance(exc, CoverFileError) and exc.rollback_failed:
                    stop_reason = "数据库状态需要检查，已停止"
                    break
    finally:
        progress.close()
        progress.deleteLater()
    remaining = len(ids) - changed - kept - skipped - failed
    summary = (
        f"已设置 {changed} 个，保留已有封面 {kept} 个，跳过 {skipped} 个，失败 {failed} 个。"
    )
    if warnings:
        summary += f"\n{warnings} 个项目有清理提示，详见详细信息。"
    if remaining:
        summary += f"\n{stop_reason}，剩余 {remaining} 个项目未处理。"
    (warn if details else info)(parent, "批量设置封面", summary, detailed="\n\n".join(details) or None)
    return bool(changed or failed)
