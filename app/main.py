"""应用入口（task #08：多库切换；task #15：欢迎兜底）。

启动序列：

1. 读 ``cabinet.json``（``CabinetConfig.load``）；缺失/损坏则回退为**空配置**
   （不再自动登记"默认库"，``%APPDATA%/LLMCabinet`` 仅作为软件全局配置存放点）。
2. 解析 ``active_library``：
   - 仍有效 → 直接进主窗口
   - 失效 / 不存在 / 全部 recent 也失效 / 空配置 → 弹 **Welcome** 让用户重新选
3. Welcome 返回 ``None`` → 用户退出，应用直接 ``return 0``。
4. 派生 ``db_path = root/cabinet.db`` 与 ``library_root = root/library``。

切换库走重启：``QApplication.quit() + os.execv``，简单稳定。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .cabinet import (
    CabinetConfig, is_library_dir, resolve_library_paths,
)
from .db import connect
from .library import Library
from .llm import load_config
from .llm.queue import LLMTaskQueue
from .repository import Repository
from .ui.main_window import MainWindow
from .ui.theme import apply_theme
from .utils import app_icon_path

log = logging.getLogger(__name__)


def _install_crash_logger() -> None:
    """安装全局异常捕获 —— 让"窗口悄悄消失"问题可调查。

    背景：
      * PyInstaller GUI 子系统打包后没有 stderr console，未捕获异常会**静默退出**，
        用户只看到"窗口什么都没出现"，没线索可查。
      * 直接 ``python run.py`` 时若用户用 .pyw / 双击 .py / IDE 默默吞掉 stderr，
        现象一样。

    所以：
      * 任何**未在 except 中捕获**的异常 → 写到 ``%APPDATA%/LLMCabinet/crash.log``，
        每次启动追加一段含时间戳 + traceback 的记录；
      * 同时尝试弹一个 QMessageBox（如果 QApplication 还在），让用户至少
        能看到"启动失败：XXX，详见 crash.log"。

    这是**纯诊断兜底**：不替任何上层 try/except 接锅，也不改主流程。
    """
    import traceback
    from datetime import datetime
    from .utils import app_data_dir

    def _hook(exc_type, exc, tb) -> None:
        # KeyboardInterrupt 不写日志（用户主动 Ctrl+C）
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        msg = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            log_path = app_data_dir() / "crash.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"\n===== {datetime.now().isoformat()} =====\n")
                f.write(msg)
        except Exception:
            pass
        # 仍照常打到 stderr（python run.py 终端能看到）
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:
            pass
        # 尝试弹 QMessageBox —— 仅当 QApplication 已存在（启动早期未必有）
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            qapp = QApplication.instance()
            if qapp is not None:
                QMessageBox.critical(
                    None, "LLM Cabinet 启动失败",
                    f"出现未捕获异常：\n\n{exc_type.__name__}: {exc}\n\n"
                    f"详见 crash.log：\n{app_data_dir() / 'crash.log'}",
                )
        except Exception:
            pass

    sys.excepthook = _hook


_install_crash_logger()


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

    # 启动时先应用主题——Welcome 对话框可能在还没打开任何库时就弹出，
    # 没有 stylesheet 会被 Qt 的 fusion 默认灰色背景渲染，与主窗口观感断裂。
    # task #34 起仅浅色单主题。
    apply_theme(app)

    # ---- 读 cabinet.json ----
    cabinet = CabinetConfig.load()

    # 命令行 --welcome：用户在主界面点了「回到欢迎页」并触发 restart；本次启动
    # 强制走 Welcome 兜底，不要从 recent 自动降级（否则 recent 头部就是用户刚
    # 离开的那个库，会被 _resolve_active_library_root 选中，"回到欢迎页"就失效了）。
    force_welcome = "--welcome" in sys.argv[1:]

    # ---- 解析当前要打开的库（含 Welcome 兜底） ----
    if force_welcome:
        library_root, stale_active = (None, None)
    else:
        library_root, stale_active = _resolve_active_library_root(cabinet)
    if library_root is None:
        # active 不可用 + 没有可降级的有效 recent → 弹 Welcome 让用户重新选
        chosen = _run_welcome(cabinet, stale_active=stale_active)
        if chosen is None:
            return 0  # 用户退出
        library_root = chosen

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
        sp = Path(saved_lib_root)
        if sp.exists():
            use_lib_root = sp
        else:
            repo.set_setting("library_root", str(library_subdir))
    else:
        repo.set_setting("library_root", str(library_subdir))

    library = Library(use_lib_root)

    # task #41 T6：应用用户字号设置
    try:
        _font_size = int(repo.get_setting("ui_font_size", "13") or "13")
    except (TypeError, ValueError):
        _font_size = 13
    apply_theme(app, font_size=_font_size)

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
    # 关停 LLM worker 并等它真正退（最多 2 秒）。这对"切换库重启"路径尤为重要：
    # 否则 main 进程退出前 sqlite 连接可能仍被 worker 持有，下次启动同一目录会
    # 看到 -wal 文件还在。
    llm_queue.stop(join_timeout=2.0)

    # 切换库的特殊退出码：在 win 关闭后由 MainWindow 设置标志
    pending_switch = getattr(win, "_pending_switch_to", None)
    if pending_switch is not None:
        # 不在 GUI 线程里直接 execv（PySide6 偶有怪异行为）；先把 cabinet.json 写好
        # （MainWindow 已经写过了，这里再保险一次），再 exec 自己。
        # 特殊值 ``"__welcome__"``：用户主动「回到欢迎页」或在主界面里把当前库删了；
        # 重启时附加 ``--welcome`` 命令行参数，确保 main 强制走 Welcome 兜底而不
        # 是从 recent 自动降级回原库（删当前库情景下 recent 已无效；但"回到欢迎页"
        # 不删任何东西，recent 头部就是刚离开的库，必须用命令行参数显式覆盖）。
        extra_args: list[str] = []
        try:
            cabinet2 = CabinetConfig.load()
            if pending_switch == "__welcome__":
                cabinet2.active_library = None
                extra_args.append("--welcome")
            else:
                cabinet2.touch(pending_switch)
            cabinet2.save()
        except Exception:
            pass
        _restart_self(extra_args=extra_args)
    return rc


def _run_welcome(
    cabinet: CabinetConfig,
    *,
    stale_active: Optional[Path] = None,
) -> Optional[Path]:
    """弹 Welcome 让用户选择新建 / 打开已有库。

    返回 ``Path | None``：
    * 用户选定的库目录 → 主流程把它 touch + save 后正常进主窗口
    * ``None`` → 用户选了"退出"或关闭对话框，主流程返回 0 不进主窗口

    选项分发：
    * "新建库"           → 直接打开 NewLibraryWizard（task #15 T1）；
      向导成功 → 返回新库根；向导取消 → 重新弹 Welcome
    * "打开最近使用的库"  → 返回选中的 recent 库目录
    * "打开其它已有目录"  → 返回用户选的目录
    """
    from .ui.welcome_dialog import (
        RESULT_NEW_CUSTOM, RESULT_OPEN_EXISTING, WelcomeDialog,
    )
    from .ui.wizards.new_library_wizard import NewLibraryWizard

    while True:
        dlg = WelcomeDialog(cabinet, stale_active=stale_active)
        rc = dlg.exec()
        if rc == RESULT_OPEN_EXISTING:
            return dlg.opened_path
        if rc == RESULT_NEW_CUSTOM:
            wiz = NewLibraryWizard(cabinet)
            if wiz.exec() == NewLibraryWizard.Accepted and wiz.created_root is not None:
                return wiz.created_root
            # 向导取消 → 回 Welcome 重选
            continue
        # rc == QDialog.Rejected（退出）
        return None


def _resolve_active_library_root(
    cabinet: CabinetConfig,
) -> tuple[Optional[Path], Optional[Path]]:
    """决定本次启动要打开的库目录。

    返回 ``(library_root, stale_active)``：
    - ``library_root`` = 解析出的有效库根；为 ``None`` 表示需要走 Welcome 兜底
    - ``stale_active`` = 上次的 ``active_library`` 但本次不可用（用于 Welcome
      角标提示"上次打开 X 已失效"）；为 ``None`` 表示没有这种情况

    解析顺序：
    1. ``cabinet.active_library`` 可用 → 直接返回它
    2. ``recent_libraries`` 里第一个可用的 → 返回它（同时把原 active 记为 stale）
    3. 都不可用 → 返回 ``(None, stale_active)`` 让上层弹 Welcome
    """
    stale_active: Optional[Path] = None

    # 1. 试 active
    if cabinet.active_library is not None:
        try:
            ap = Path(cabinet.active_library)
            if is_library_dir(ap):
                return (ap, None)
            stale_active = ap
        except Exception:
            stale_active = None

    # 2. 试 recent（按 last_opened 倒序，已是 cabinet.recent_libraries 的现有顺序）
    for h in cabinet.recent_libraries:
        try:
            rp = Path(h.path)
            if is_library_dir(rp):
                return (rp, stale_active)
        except Exception:
            continue

    # 3. 全都不可用 → 走 Welcome
    return (None, stale_active)


def _restart_self(*, extra_args: Optional[list[str]] = None) -> None:
    """重启当前进程。PyInstaller onefile 下 sys.executable 是 exe 路径，os.execv 正常工作。

    ``extra_args`` 用于追加本次重启需要的额外命令行参数（比如 ``--welcome``
    强制走 Welcome 兜底）；会在 ``sys.argv`` 之后追加 + 去重。

    会过滤掉 ``--welcome`` —— 该参数仅对**本次**启动有效（一次性"强制 Welcome"
    意图），重启时若 ``extra_args`` 里没显式带，必须把它去掉，否则用户从 Welcome
    选了库进主界面后再做切换/重启又会再次被踹回 Welcome。
    """
    try:
        argv = [a for a in sys.argv if a != "--welcome"]
        for a in (extra_args or []):
            if a not in argv:
                argv.append(a)
        os.execv(sys.executable, [sys.executable, *argv])
    except Exception as e:
        log.error("重启失败：%s", e)
        # 退而求其次：弹窗提示用户手动重启
        QMessageBox.warning(
            None, "需要手动重启",
            f"切换库需要重启应用，自动重启失败：{e}\n请手动关闭并重新打开。",
        )


if __name__ == "__main__":
    sys.exit(main())
