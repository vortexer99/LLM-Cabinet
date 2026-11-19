"""仓储原文件回收的后台进度入口。"""
from __future__ import annotations

import logging

from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QWidget

from ..library import Library
from ..repository import Repository
from ..storage_cleanup import (
    STORAGE_RECYCLE_SOURCE_KEY, SourceSnapshot,
    cleanup_storage_sources, snapshot_cleanup_repo,
)
from .workers import run_with_progress


def recycle_sources_with_progress(
    parent: QWidget,
    repo: Repository,
    library: Library,
    candidates: list[tuple[SourceSnapshot, str, int]],
) -> list[str]:
    """主线程快照数据库，后台执行文件检查/回收，等待期间显示模态进度。"""
    if not candidates or repo.get_setting(STORAGE_RECYCLE_SOURCE_KEY, "0") != "1":
        return []
    try:
        snapshot = snapshot_cleanup_repo(repo, candidates)
    except Exception as exc:
        logging.exception("准备原文件回收失败，已保留原文件")
        return [f"准备原文件回收失败，已保留原文件：{exc}"]
    loop = QEventLoop(parent)
    warnings: list[str] = []
    completed = False

    def finish(messages: list[str]) -> None:
        nonlocal completed
        warnings.extend(messages)
        completed = True
        loop.quit()

    def work(progress_cb, is_cancelled):
        return cleanup_storage_sources(
            snapshot, library, candidates, progress=progress_cb, is_cancelled=is_cancelled,
        )

    # 进度框阻止主窗口重入，事件循环保持绘制和取消响应；不把 SQLite 连接传给 worker。
    run_with_progress(
        parent, "回收仓储原文件", "正在检查并回收原文件…", work,
        on_done=finish,
        on_cancel=lambda: finish(["原文件回收已取消，未处理的原文件保留，已入库文件不受影响。"]),
        on_error=lambda message: finish([f"原文件回收未完成，未处理的原文件保留：{message}"]),
    )
    if not completed:
        loop.exec()
    loop.deleteLater()
    return warnings
