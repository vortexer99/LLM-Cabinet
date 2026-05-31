"""应用入口。"""
from __future__ import annotations

import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from .db import connect
from .library import Library, default_library_root
from .llm import load_config
from .llm.queue import LLMTaskQueue
from .repository import Repository
from .ui.main_window import MainWindow
from .ui.theme import apply_theme
from .utils import app_data_dir, app_icon_path


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

    # 默认库的数据库文件名
    db_path = app_data_dir() / "cabinet.db"
    conn = connect(db_path)
    repo = Repository(conn)

    lib_root = repo.get_setting("library_root", "")
    library = Library(lib_root if lib_root else default_library_root())
    if not lib_root:
        repo.set_setting("library_root", str(library.root))

    theme = repo.get_setting("theme", "light") or "light"
    apply_theme(app, theme)

    # LLM 任务队列
    llm_queue = LLMTaskQueue(repo, library, get_config=lambda: load_config(repo))
    llm_queue.start()

    win = MainWindow(repo, library, db_path=db_path, llm_queue=llm_queue)
    win.show()
    rc = app.exec()
    llm_queue.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())
