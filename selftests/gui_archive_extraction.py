"""压缩包解压入库 GUI 回归；仅显式临时库、自造 ZIP 和 offscreen Qt。

运行：.venv/Scripts/python.exe -X utf8 selftests/gui_archive_extraction.py
只替换模态对话框与线程调度，实际解压、SQLite 事务及文件表刷新均走产品代码。
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QLineEdit, QMenu

from app.archives import ArchiveCancelled, ArchivePasswordRequired, extract_archive
from app.db import connect
from app.models import FileItem, Project
from app.repository import Repository
from app.utils import OperationCancelled
from selftests._common import T, closing_repos
from selftests.gui_main_window_regressions import (
    _app, _close_window, _find_file_tree_item, _window,
)


def _zip(path: Path) -> dict[str, bytes]:
    """合成包含中文、多层目录、空目录及四种文件类型的 ZIP。"""
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as archive:
        archive.writestr("内部.dat", b"nested archive")
    contents = {
        "01-image.jpg": b"synthetic image",
        "中文/子层/02-video.mp4": b"synthetic video",
        "中文/03-nested.zip": nested.getvalue(),
        "04-data.dat": "自造测试数据".encode("utf-8"),
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in contents.items():
            archive.writestr(name, content)
        archive.writestr("空目录/", b"")
    return contents


def _run_sync(_parent, _title, _label, work, *, on_done, on_cancel, on_error) -> None:
    """保留真实 worker 与完成/失败/取消路由，仅固定调度顺序。"""
    try:
        result = work(lambda *_args: None, lambda: False)
    except OperationCancelled:
        on_cancel()
    except Exception as exc:
        on_error(str(exc))
    else:
        on_done(result)


def _invoke(win, *, accepted: bool = True):
    """跳过真实确认框，保留后续单独的密码询问流程。"""
    stack = ExitStack()
    stack.enter_context(patch("app.ui.archive_actions.confirm", return_value=accepted))
    stack.enter_context(patch("app.ui.archive_actions.run_with_progress", _run_sync))
    warning = stack.enter_context(patch("app.ui.archive_actions.warn"))
    return stack, warning


def _seed(win, repo, root: Path, *, stored: bool = False):
    pid = repo.save_project(Project(title="压缩包解压测试"))
    source = root / "中文素材.zip"
    contents = _zip(source)
    relative = win.library.import_copy(pid, source) if stored else str(source)
    fid = repo.add_file(FileItem(
        project_id=pid, path=relative, is_relative=stored, kind="other", subfolder="原目录",
    ))
    other = root / "不应解压.dat"
    other.write_bytes(b"unrelated selected file")
    other_id = repo.add_file(FileItem(project_id=pid, path=str(other), kind="other"))
    win.refresh_projects()
    win._select_project_by_id(pid)
    win.tbl_files.expandAll()
    _app().processEvents()
    win.tbl_files.setCurrentItem(_find_file_tree_item(win, fid))
    return pid, fid, other_id, win.library.resolve(relative, stored), contents


def test_real_extraction(t: T, root: Path, stored: bool) -> None:
    """链接/仓储都解压到仓储，保留源、目录、选择及重复解压结果。"""
    root.mkdir()
    win, repo = _window(root)
    try:
        pid, fid, other_id, source, contents = _seed(win, repo, root, stored=stored)
        source_bytes = source.read_bytes()
        repo.set_setting("storage_recycle_source", "1")
        before = repo.list_files(pid)
        label = "仓储源" if stored else "链接源"
        item = _find_file_tree_item(win, fid)
        t.assert_eq(f"{label}旧other记录树形显示archive", item.text(2), "archive")
        win._set_files_view_mode("flat", pid)
        t.assert_eq(f"{label}旧other记录平铺显示archive", _find_file_tree_item(win, fid).text(2), "archive")
        win._set_files_view_mode("tree", pid)
        win.tbl_files.expandAll()
        item = _find_file_tree_item(win, fid)
        other_item = _find_file_tree_item(win, other_id)
        win.tbl_files.setCurrentItem(other_item)
        item.setSelected(True)
        t.assert_eq(f"{label}菜单前保留两项选择", set(win._selected_file_ids()), {fid, other_id})

        stack, warning = _invoke(win)
        seen: list[str] = []

        def menu_factory(parent):
            menu = QMenu(parent)

            def choose(_position):
                seen.extend(action.text() for action in menu.actions())
                for action in menu.actions():
                    if "解压到当前项目" in action.text():
                        action.trigger()
                        break

            menu.exec = choose
            return menu

        with stack, patch("app.ui.mw_files.QMenu", side_effect=menu_factory), patch(
            "app.ui.archive_actions.QInputDialog.getText", side_effect=AssertionError("无密码 ZIP 不应询问密码"),
        ) as password_prompt:
            win._file_context_menu(win.tbl_files.visualItemRect(item).center())
        t.assert_true(f"{label}旧other压缩包右键提供解压", any("解压到当前项目" in text for text in seen))
        t.assert_eq(f"{label}完成无错误框", warning.call_count, 0)
        t.assert_eq(f"{label}普通ZIP不询问密码", password_prompt.call_count, 0)
        t.assert_eq(f"{label}解压后保留当前压缩包", win._current_file_row_id(), fid)
        t.assert_eq(f"{label}完成只添加压缩包内文件", len(repo.list_files(pid)), len(before) + len(contents))
        t.assert_true(f"{label}完成提示成功", "已解压 4 个文件" in win.statusBar().currentMessage())
        first = [file for file in repo.list_files(pid) if file.id not in {fid, other_id}]
        expected_kinds = {"01-image.jpg": "image", "02-video.mp4": "video", "03-nested.zip": "archive", "04-data.dat": "other"}
        first_content: dict[str, bytes] = {}
        for file in first:
            physical = win.library.resolve(file.path, file.is_relative)
            t.assert_true(f"{label}/{physical.name}使用仓储相对路径", file.is_relative and physical.is_relative_to(win.library.root))
            t.assert_eq(f"{label}/{physical.name}作为用户文件登记", file.origin, "user")
            t.assert_eq(f"{label}/{physical.name}类型正确", file.kind, expected_kinds[physical.name])
            # 物理目录独立，逻辑目录仍严格保留压缩包内层级。
            package_path = next(name for name in contents if Path(name).name == physical.name)
            parent = Path(package_path).parent
            expected_folder = "原目录/中文素材" + ("/" + parent.as_posix() if parent != Path(".") else "")
            t.assert_eq(f"{label}/{physical.name}逻辑层级保留", file.subfolder, expected_folder)
            t.assert_eq(f"{label}/{physical.name}解压内容完整", physical.read_bytes(), contents[package_path])
            first_content[file.path] = physical.read_bytes()
        folders = win._load_explicit_subfolders(pid)
        t.assert_in(f"{label}保留空目录", "原目录/中文素材/空目录", folders)
        t.assert_in(f"{label}保留多层目录", "原目录/中文素材/中文/子层", folders)

        stack, warning = _invoke(win)
        with stack:
            win.action_extract_archive()
        second = [file for file in repo.list_files(pid) if file.id not in {row.id for row in before + first}]
        t.assert_eq(f"{label}重复解压添加新批次", len(second), len(contents))
        t.assert_true(f"{label}重复解压目录自动_2", all(file.subfolder.startswith("原目录/中文素材_2") for file in second))
        t.assert_true(f"{label}重复解压不覆盖物理文件", not {file.path for file in first} & {file.path for file in second})
        t.assert_true(f"{label}首批文件内容保留", all(win.library.resolve(path, True).read_bytes() == content for path, content in first_content.items()))
        t.assert_eq(f"{label}源压缩包内容未改变", source.read_bytes(), source_bytes)
        t.assert_eq(f"{label}显示类型不修改旧记录", repo.get_file(fid).kind, "other")
    finally:
        _close_window(win)
        repo.conn.close()


def test_atomic_rollback(t: T, root: Path) -> None:
    """真实 SQLite trigger 使第二条 INSERT 失败，验证整批回滚。"""
    root.mkdir()
    repo = Repository(connect(root / "atomic-test.db"))
    with closing_repos(repo):
        pid = repo.save_project(Project(title="事务回滚测试"))
        repo.set_project_setting(pid, "folders", "原值")
        repo.conn.execute("CREATE TEMP TRIGGER fail_second BEFORE INSERT ON files WHEN NEW.path='fail.dat' BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        repo.conn.commit()
        failed = False
        try:
            repo.add_files_atomic(pid, [FileItem(project_id=pid, path=name) for name in ("first.dat", "fail.dat")], {"folders": "不应保存"})
        except sqlite3.IntegrityError:
            failed = True
        t.assert_true("真实事务第二条失败向外报告", failed)
        t.assert_eq("真实事务第二条失败回滚第一条", repo.list_files(pid), [])
        t.assert_eq("事务失败目录设置保留原值", repo.get_project_setting(pid, "folders", ""), "原值")
        t.assert_eq("事务失败不遗留开启的事务", repo.conn.in_transaction, False)


def test_failures(t: T, root: Path) -> None:
    """确认取消、后台取消/损坏和实际入库失败均不残留、不误报成功。"""
    root.mkdir()
    win, repo = _window(root)
    try:
        pid, fid, _, source, _ = _seed(win, repo, root)
        baseline = [(file.id, file.path) for file in repo.list_files(pid)]
        original_bytes = source.read_bytes()
        project_dir = win.library.project_dir(pid)
        initial_dirs = set(project_dir.iterdir())
        initial_folders = win._load_explicit_subfolders(pid)

        stack, warning = _invoke(win, accepted=False)
        with stack, patch("app.ui.archive_actions.extract_archive") as extract:
            win.action_extract_archive()
        t.assert_eq("确认取消不启动后台解压", extract.call_count, 0)
        t.assert_eq("确认取消不产生仓储文件", set(project_dir.iterdir()), initial_dirs)
        t.assert_eq("确认取消不提示错误", warning.call_count, 0)

        for title, exception in (("后台取消", ArchiveCancelled("合成取消")), ("损坏压缩包", RuntimeError("合成损坏"))):
            win.statusBar().clearMessage()
            stack, warning = _invoke(win)
            with stack, patch("app.ui.archive_actions.extract_archive", side_effect=exception) as extract:
                win.action_extract_archive()
            t.assert_eq(f"{title}首先无密码尝试", extract.call_args.kwargs["password"], None)
            t.assert_eq(f"{title}错误渠道正确", warning.call_count, int(title == "损坏压缩包"))
            t.assert_true(f"{title}不误报成功", "已解压" not in win.statusBar().currentMessage())
            t.assert_eq(f"{title}不登记文件", [(file.id, file.path) for file in repo.list_files(pid)], baseline)

        repo.conn.execute("CREATE TEMP TRIGGER fail_extract BEFORE INSERT ON files WHEN NEW.path LIKE '%02-video.mp4' BEGIN SELECT RAISE(ABORT, 'synthetic database failure'); END")
        repo.conn.commit()
        win.statusBar().clearMessage()
        stack, warning = _invoke(win)
        with stack:
            win.action_extract_archive()
        t.assert_eq("实际入库失败只汇总一次", warning.call_count, 1)
        t.assert_eq("实际入库失败回滚全部文件", [(file.id, file.path) for file in repo.list_files(pid)], baseline)
        t.assert_eq("实际入库失败清理本次解压目录", set(project_dir.iterdir()), initial_dirs)
        t.assert_eq("实际入库失败不改变逻辑目录", win._load_explicit_subfolders(pid), initial_folders)
        t.assert_true("实际入库失败不误报成功", "已解压" not in win.statusBar().currentMessage())
        t.assert_eq("所有失败路径保留源压缩包", source.read_bytes(), original_bytes)
        t.assert_eq("所有失败路径保留源记录", repo.get_file(fid).path, str(source))
    finally:
        _close_window(win)
        repo.conn.close()


def test_passwords(t: T, root: Path) -> None:
    """只在需要时询问密码，重试/取消正确，成功密码按设置原子追加描述。"""
    root.mkdir()
    win, repo = _window(root)
    try:
        pid, fid, _, source, contents = _seed(win, repo, root)
        original = "原有描述\n第二行保持不变"
        project = repo.get_project(pid)
        project.description_md = original
        repo.save_project(project)
        secret = "合成密码-成功"
        wrong = "合成密码-错误"
        supplied: list[str | None] = []

        def protected(archive, destination, *, password, **kwargs):
            supplied.append(password)
            if password != secret:
                raise ArchivePasswordRequired("合成压缩包需要密码")
            # 密码验证用模拟后端，解压与文件/描述落库仍使用真实 ZIP 和 SQLite。
            return extract_archive(archive, destination, **kwargs)

        from app.ui.settings import SettingsDialog
        from selftests.gui_library_media_settings import _close_widget

        settings = SettingsDialog(repo, root, root / "cabinet.db")
        try:
            checkbox = settings.chk_archive_remember_password
            t.assert_eq("记录解压密码设置默认关闭", checkbox.isChecked(), False)
            checkbox.click()
            t.assert_eq("记录密码开关点击立即持久化", repo.get_setting("archive_remember_password", "0"), "1")
            checkbox.click()
        finally:
            _close_widget(settings)

        before = len(repo.list_files(pid))
        stack, warning = _invoke(win)
        with stack, patch("app.ui.archive_actions.extract_archive", side_effect=protected), patch(
            "app.ui.archive_actions.QInputDialog.getText", side_effect=[(wrong, True), (secret, True)],
        ) as prompt:
            win.action_extract_archive()
        t.assert_eq("先无密码解压再错误密码再正确密码", supplied, [None, wrong, secret])
        t.assert_eq("错误密码后再次询问", prompt.call_count, 2)
        t.assert_true("密码输入框使用隐藏模式", all(call.args[3] == QLineEdit.Password for call in prompt.call_args_list))
        t.assert_eq("密码重试成功仅登记一次", len(repo.list_files(pid)), before + len(contents))
        t.assert_eq("默认不把密码写入描述", repo.get_project(pid).description_md, original)
        t.assert_eq("密码重试成功不弹错误框", warning.call_count, 0)

        repo.set_setting("archive_remember_password", "1")
        supplied.clear()
        before = len(repo.list_files(pid))
        stack, warning = _invoke(win)
        with stack, patch("app.ui.archive_actions.extract_archive", side_effect=protected), patch(
            "app.ui.archive_actions.QInputDialog.getText", side_effect=[(wrong, True), (secret, True)],
        ):
            win.action_extract_archive()
        description = repo.get_project(pid).description_md
        t.assert_true("开启后保留原有描述并追加一行", description.startswith(original + "\n") and len(description.splitlines()) == 3)
        t.assert_true("成功密码可准确恢复且包含源包名", json.dumps(secret, ensure_ascii=False) in description and source.name in description)
        t.assert_true("错误密码从不写入描述", wrong not in description)
        t.assert_eq("记录密码仍只入库一次", len(repo.list_files(pid)), before + len(contents))

        baseline = [(file.id, file.path) for file in repo.list_files(pid)]
        folders = win._load_explicit_subfolders(pid)
        output_dirs = set(win.library.project_dir(pid).iterdir())
        supplied.clear()
        win.statusBar().clearMessage()
        stack, warning = _invoke(win)
        with stack, patch("app.ui.archive_actions.extract_archive", side_effect=protected), patch(
            "app.ui.archive_actions.QInputDialog.getText", side_effect=[(wrong, True), ("", False)],
        ) as prompt:
            win.action_extract_archive()
        t.assert_eq("错误密码后取消不再尝试", supplied, [None, wrong])
        t.assert_eq("密码取消不保存描述", repo.get_project(pid).description_md, description)
        t.assert_eq("密码取消不登记文件", [(file.id, file.path) for file in repo.list_files(pid)], baseline)
        t.assert_eq("密码取消不遗留解压输出", set(win.library.project_dir(pid).iterdir()), output_dirs)
        t.assert_true("密码取消不误报成功", "已解压" not in win.statusBar().currentMessage())
        t.assert_eq("密码取消不弹错误框", warning.call_count, 0)

        # 在文件和目录已经写入后使描述 UPDATE 失败，验证同一个真实事务回滚三者。
        repo.conn.execute("CREATE TEMP TRIGGER fail_description BEFORE UPDATE OF description_md ON projects BEGIN SELECT RAISE(ABORT, 'synthetic description failure'); END")
        repo.conn.commit()
        win.statusBar().clearMessage()
        stack, warning = _invoke(win)
        with stack, patch("app.ui.archive_actions.extract_archive", side_effect=protected), patch(
            "app.ui.archive_actions.QInputDialog.getText", return_value=(secret, True),
        ):
            win.action_extract_archive()
        t.assert_eq("描述写入失败回滚文件登记", [(file.id, file.path) for file in repo.list_files(pid)], baseline)
        t.assert_eq("描述写入失败回滚目录设置", win._load_explicit_subfolders(pid), folders)
        t.assert_eq("描述写入失败保留原描述", repo.get_project(pid).description_md, description)
        t.assert_eq("描述写入失败清理解压输出", set(win.library.project_dir(pid).iterdir()), output_dirs)
        t.assert_eq("描述写入失败汇总一次", warning.call_count, 1)
        t.assert_true("描述写入失败不误报成功", "已解压" not in win.statusBar().currentMessage())
        t.assert_true("密码相关流程始终保留源", source.is_file() and repo.get_file(fid) is not None)
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    app = _app()
    t = T()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        # 任何意外回收到达 mock 即失败；绝不调用真实回收站。
        with patch("app.utils.move_to_trash", side_effect=AssertionError("禁止真实回收")) as trash, patch(
            "app.storage_cleanup.move_to_trash", side_effect=AssertionError("解压禁止回收源"),
        ) as storage_trash:
            test_real_extraction(t, root / "linked", False)
            test_real_extraction(t, root / "stored", True)
            test_atomic_rollback(t, root / "atomic")
            test_failures(t, root / "failures")
            test_passwords(t, root / "passwords")
            t.assert_eq("解压流程不调用通用回收", trash.call_count, 0)
            t.assert_eq("解压流程不调用仓储回收", storage_trash.call_count, 0)
    sys.exit(0 if t.report() else 1)
