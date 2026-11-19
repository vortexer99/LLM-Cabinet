"""链接转仓储的直接移动流程：主线程快照/提交，后台传输与恢复。"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from ..models import FileItem
from ..storage_move import (
    commit_transfers, prepare_move_inputs, rollback_transfers, transfer_files,
)
from .dialogs import confirm, info, warn
from .workers import run_with_progress

logger = logging.getLogger(__name__)


def convert_files_by_move(parent, files: list[FileItem], *, scope_label: str = "所选文件") -> None:
    """按库设置移动链接源；保守复制项始终保留源，不进入复制后回收流程。"""
    link_files = [file for file in files if not file.is_relative]
    if not files:
        info(parent, "提示", f"{scope_label}中没有可处理的文件。")
        return
    if not link_files:
        info(parent, "提示", f"{scope_label}已都是仓储模式，无需转换。")
        return
    if not confirm(
        parent, "确认转换",
        f"{scope_label}：将把 {len(link_files)} 个🔗链接文件移动进库：\n\n"
        + "\n".join(f"  • {file.label or file.path}" for file in link_files[:5])
        + (f"\n  … 等共 {len(link_files)} 个" if len(link_files) > 5 else "")
        + f"\n\n已仓储文件 {len(files) - len(link_files)} 个将跳过。\n\n"
        "移动成功后原位置将不再保留文件，原目录保留。\n"
        "被本库其它记录引用、位于库内或含符号链接/目录重解析点的源文件，"
        "会改为复制并保留原文件。\n"
        "本次转换优先使用直接移动，不执行复制后回收原文件设置。",
        yes="转换", danger=True,
    ):
        return
    try:
        inputs = prepare_move_inputs(parent.repo, parent.library, link_files)
    except Exception as exc:
        logger.exception("准备直接移动转换失败")
        details = f"阶段：读取转换快照\n{type(exc).__name__}: {exc}\n文件列表：\n" + "\n".join(
            f"记录 ID：{file.id}；文件：{file.label or Path(file.path).name}；源路径：{file.path}"
            for file in link_files)
        warn(parent, "转换失败", "无法准备转换，原文件均未移动。", detailed=details)
        return

    # 只释放本次源文件的当前预览，避免本应用的视频占用阻止 Windows 移动。
    preview = getattr(parent, "preview", None)
    current_path = getattr(preview, "_current", None)
    preview_released = False
    if current_path and any(
        os.path.normcase(os.path.abspath(file.path)) == os.path.normcase(os.path.abspath(current_path))
        for file in inputs.files
    ):
        preview.show_file(None)
        preview_released = True

    def _refresh() -> None:
        project_id = getattr(parent, "_current_project_id", None)
        if project_id is not None and hasattr(parent, "_show_project"):
            # 沿用项目刷新已有的选择恢复逻辑，保持当前项、多选、折叠与其它文件预览。
            parent._show_project(parent.repo.get_project(project_id), preserve_preview=True)
        else:
            parent._refresh_files_table()
        if preview_released and hasattr(parent, "_on_file_selected"):
            parent._on_file_selected()

    def _failed(message: str) -> None:
        logger.error("直接移动转换后台任务失败：%s", message)
        _refresh()
        details = f"阶段：后台文件处理\n{message}\n文件列表：\n" + "\n".join(
            f"记录 ID：{file.file_id}；文件：{file.label or Path(file.path).name}；源路径：{file.path}；"
            f"预期目标目录：{inputs.root / f'project_{file.project_id}'}"
            for file in inputs.files)
        warn(parent, "转换失败", "后台任务未完成，请查看详细文件列表与错误。", detailed=details)

    def _on_done(payload: dict) -> None:
        committed, rollback, db_errors = commit_transfers(parent.repo, payload["results"])
        errors = list(payload["errors"]) + db_errors
        warnings = list(payload["warnings"])

        def _finish(recovery: list[str]) -> None:
            moved = sum(result.moved for result in committed)
            copied = len(committed) - moved
            lines = [f"✅ 已移动到库内：{moved} 个", f"📋 已复制并保留原文件：{copied} 个"]
            if payload["cancelled"]:
                lines.append(f"⏸ 已取消：{len(inputs.files) - payload['processed']} 个未处理；已完成项已保存。")
            if errors:
                lines.append(f"⏭ 失败：{len(errors)} 项（完整文件列表见详细信息）")
            if warnings:
                lines.append(f"保守复制提示：{len(warnings)} 项（原因见详细信息）")
            if recovery:
                lines.append(f"已对 {len(recovery)} 项执行恢复；恢复结果及可恢复路径见详细信息。")
            details = "\n\n".join(errors + warnings + recovery)
            _refresh()
            status = getattr(parent, "statusBar", None)
            if callable(status):
                status().showMessage(f"转换完成：移动 {moved} 个，复制保留源 {copied} 个，失败 {len(errors)} 项", 8000)
            (warn if errors else info)(parent, "转换完成", "\n".join(lines), detailed=details or None)

        if rollback:
            # 跨盘恢复同样可能耗时，禁止在主线程 shutil.move；恢复阶段不允许取消。
            run_with_progress(
                parent, "恢复未入库文件", "正在恢复转换失败的原文件…",
                lambda progress, cancelled: rollback_transfers(rollback, progress, cancelled),
                on_done=_finish,
                on_error=lambda message: _finish([
                    f"阶段：后台回滚失败\n{message}\n" + "\n".join(
                        f"记录 ID：{result.file.file_id}；文件：{result.file.label or result.source.name}；"
                        f"原路径：{result.source}；仓储文件可恢复路径：{result.target}"
                        for result in rollback)
                ]),
                cancellable=False,
            )
        else:
            _finish([])

    run_with_progress(
        parent, "链接转仓储", f"{scope_label}：正在转换…",
        lambda progress, cancelled: transfer_files(inputs, progress, cancelled),
        on_done=_on_done, on_error=_failed,
    )
