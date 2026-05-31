"""应用入口（task #08：多库切换）。

启动序列：

1. 读 ``cabinet.json``（CabinetConfig.load）；缺失/损坏则回退默认。
2. ``active_library`` 指向的目录就是当前要打开的库根。
3. 若该目录不可用（不存在 / 不是有效库目录），降级到默认库。
4. 派生 ``db_path = root/cabinet.db`` 与 ``library_root = root/library``。
5. 老用户首次启动新版本：``cabinet.json`` 不存在，自动以 ``%APPDATA%/LLMCabinet`` 为默认库登记。

切换库走重启：``QApplication.quit() + os.execv``，简单稳定。
"""
from __future__ import annotations

import logging
import os
import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .cabinet import (
    CabinetConfig, is_library_dir, mark_as_library, resolve_library_paths,
)
from .db import connect
from .library import Library
from .llm import load_config
from .llm.queue import LLMTaskQueue
from .repository import Repository
from .ui.main_window import MainWindow
from .ui.theme import apply_theme
from .utils import app_data_dir, app_icon_path

log = logging.getLogger(__name__)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("LLM Cabinet")
    app.setOrganizationName("LLM Cabinet")
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 9))

    # Windows 任务栏图标分组 —— 必须在创建窗口前调用
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "LLMCabinet.App.1"
            )
        except Exception:
            pass

    icon_path = app_icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))

    # ---- 解析当前要打开的库 ----
    cabinet = CabinetConfig.load()
    library_root = _resolve_active_library_root(cabinet)

    # 默认库目录如果还没标记，补一下（兼容老用户）
    if library_root == app_data_dir():
        try:
            mark_as_library(library_root)
        except Exception:
            pass

    db_path, library_subdir = resolve_library_paths(library_root)

    # 写回 cabinet.json：登记 active 与 last_opened
    cabinet.touch(library_root)
    cabinet.save()

    # ---- 打开 db、构建 Repository / Library ----
    conn = connect(db_path)
    repo = Repository(conn)

    # 兼容历史：默认库的 library_root setting 可能写着旧路径（v0.1.0 → v0.2.0 重命名遗留），
    # 启动时用当前真实 library/ 子目录覆盖（仅当 setting 为空或指向不存在路径时）
    saved_lib_root = repo.get_setting("library_root", "")
    use_lib_root = library_subdir
    if saved_lib_root:
        from pathlib import Path as _Path
        sp = _Path(saved_lib_root)
        if sp.exists():
            use_lib_root = sp
        else:
            repo.set_setting("library_root", str(library_subdir))
    else:
        repo.set_setting("library_root", str(library_subdir))

    library = Library(use_lib_root)

    theme = repo.get_setting("theme", "light") or "light"
    apply_theme(app, theme)

    llm_queue = LLMTaskQueue(repo, library, get_config=lambda: load_config(repo))
    llm_queue.start()

    win = MainWindow(
        repo, library,
        db_path=db_path,
        llm_queue=llm_queue,
        cabinet_config=cabinet,
        library_root=library_root,
    )
    win.show()
    rc = app.exec()
    llm_queue.stop()

    # 切换库的特殊退出码：在 win 关闭后由 MainWindow 设置标志
    pending_switch = getattr(win, "_pending_switch_to", None)
    if pending_switch:
        # 不在 GUI 线程里直接 execv（PySide6 偶有怪异行为）；先把 cabinet.json 写好
        # （MainWindow 已经写过了，这里再保险一次），再 exec 自己
        try:
            cabinet2 = CabinetConfig.load()
            cabinet2.touch(pending_switch)
            cabinet2.save()
        except Exception:
            pass
        _restart_self()
    return rc


def _resolve_active_library_root(cabinet: CabinetConfig):
    """决定本次启动要打开的库目录。

    优先级：cabinet.active_library → 第一个可用的 recent → 默认库（%APPDATA%/LLMCabinet）。
    可用性判断：目录存在 + (含 cabinet.db 或被允许首次写入)。
    """
    candidates: list = []
    if cabinet.active_library is not None:
        candidates.append(cabinet.active_library)
    candidates.extend(h.path for h in cabinet.recent_libraries)
    # 默认库兜底
    candidates.append(app_data_dir())

    for root in candidates:
        try:
            from pathlib import Path as _Path
            root = _Path(root)
            # 目录已存在 + 是合法库 → 直接用
            if is_library_dir(root):
                return root
            # 目录不存在 / 不是库 → 跳过；continue
        except Exception:
            continue

    # 全都不可用 → 默认库（即使不存在也在 connect 时会被建出来）
    return app_data_dir()


def _restart_self() -> None:
    """重启当前进程。PyInstaller onefile 下 sys.executable 是 exe 路径，os.execv 正常工作。"""
    try:
        os.execv(sys.executable, [sys.executable, *sys.argv])
    except Exception as e:
        log.error("重启失败：%s", e)
        # 退而求其次：弹窗提示用户手动重启
        QMessageBox.warning(
            None, "需要手动重启",
            f"切换库需要重启应用，自动重启失败：{e}\n请手动关闭并重新打开。",
        )


if __name__ == "__main__":
    sys.exit(main())
