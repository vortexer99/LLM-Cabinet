"""链接直接移动转仓储回归：仅显式临时库、合成文件及 offscreen Qt。"""
from __future__ import annotations

import os
import errno
import shutil
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import connect
from app.models import FileItem, Project
from app.repository import Repository
from app.ui.settings import SettingsDialog
from selftests._common import T
from selftests.gui_library_media_settings import _close_widget
from selftests.gui_main_window_regressions import _app, _close_window, _window


KEY = "storage_convert_move"


def _source(root: Path, name: str, content: str | None = None) -> Path:
    """所有源文件均在当前测试临时目录内自行构造。"""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content or f"合成文件：{name}", encoding="utf-8")
    return path


@contextmanager
def _fixture(root: Path):
    root.mkdir(parents=True)
    win, repo = _window(root)
    repo.set_setting(KEY, "1")
    # 同时打开回收，验证直接移动优先且不会经过真实回收站。
    repo.set_setting("storage_recycle_source", "1")
    try:
        yield win, repo
    finally:
        _close_window(win)
        repo.conn.close()


def _run_sync(_parent, _title, _label, worker, *, on_done, on_error, **_kwargs) -> None:
    """只替换线程调度，保留真实磁盘操作及主线程写回。"""
    on_done(worker(lambda *_args: None, lambda: False))


@contextmanager
def _dialogs(*, runner=_run_sync):
    """捕获真实入口的单次汇总，不弹出模态窗口。"""
    with ExitStack() as stack:
        calls = []
        for module in ("app.ui.mw_files", "app.ui.storage_move"):
            stack.enter_context(patch(f"{module}.confirm", return_value=True))
            calls.extend(stack.enter_context(patch(f"{module}.{name}")) for name in ("info", "warn"))
            if runner is not None:
                stack.enter_context(patch(f"{module}.run_with_progress", side_effect=runner))
        yield calls


def _summary(dialogs, *, detailed: bool = False) -> str:
    """详细模式仅检查 detailed，防止摘要截断掩盖丢失失败项。"""
    values = []
    for dialog in dialogs:
        for call in dialog.call_args_list:
            values.extend([call.kwargs.get("detailed", "")] if detailed else [*call.args[1:], *call.kwargs.values()])
    return "\n".join(str(value) for value in values)


def _assert_single_summary(t: T, dialogs, label: str) -> None:
    t.assert_eq(label, sum(dialog.call_count for dialog in dialogs), 1)


def test_settings(t: T, root: Path) -> None:
    """两个真实临时库验证缺省关闭、即时保存及库隔离。"""
    for index in range(2):
        library_root = root / str(index)
        library_root.mkdir(parents=True)
        db_path = library_root / "test.db"
        repo = Repository(connect(db_path))
        try:
            dialog = SettingsDialog(repo, library_root, db_path)
            try:
                t.assert_eq(f"库{index}默认关闭移动", dialog.chk_storage_convert_move.isChecked(), False)
                t.assert_eq(f"库{index}缺省设置关闭", repo.get_setting(KEY, "0"), "0")
                if index == 0:
                    dialog.chk_storage_convert_move.click()
                    t.assert_eq("设置开启立即保存", repo.get_setting(KEY), "1")
                    dialog.chk_storage_convert_move.click()
                    t.assert_eq("设置关闭立即保存", repo.get_setting(KEY), "0")
                    dialog.chk_storage_convert_move.click()
            finally:
                _close_widget(dialog)
        finally:
            repo.conn.close()
    for index in range(2):
        library_root = root / str(index)
        db_path = library_root / "test.db"
        repo = Repository(connect(db_path))
        try:
            dialog = SettingsDialog(repo, library_root, db_path)
            try:
                t.assert_eq(f"重开库{index}移动设置隔离持久化", dialog.chk_storage_convert_move.isChecked(), index == 0)
            finally:
                _close_widget(dialog)
        finally:
            repo.conn.close()


def test_move_and_collision(t: T, root: Path) -> None:
    """实际同盘移动不复制，同名目标不覆盖，文件 ID 与元数据不变。"""
    with _fixture(root) as (win, repo):
        pid = repo.save_project(Project(title="直接移动", description_md="项目说明", tags=["合成标签"]))
        paths = [_source(root, f"external/{folder}/same.txt", f"内容{folder}") for folder in ("a", "b")]
        ids = [repo.add_file(FileItem(project_id=pid, path=str(path), label=f"标签{index}",
            kind="text", ord=index + 7, subfolder=f"逻辑/目录{index}", origin="generated"))
            for index, path in enumerate(paths)]
        project = repo.get_project(pid)
        project.cover_file_id = ids[0]
        repo.save_project(project)
        before = {fid: asdict(repo.get_file(fid)) for fid in ids}
        occupied = win.library.project_dir(pid) / "same.txt"
        occupied.write_text("已有内容", encoding="utf-8")
        with _dialogs() as dialogs, patch("shutil.copy2", side_effect=AssertionError("同盘移动不应复制")), patch(
            "shutil.copyfileobj", side_effect=AssertionError("同盘移动不应流式复制"),
        ):
            win.action_convert_project_to_storage(pid)
        _assert_single_summary(t, dialogs, "成功移动一次汇总")
        t.assert_eq("已有同名文件不覆盖", occupied.read_text(encoding="utf-8"), "已有内容")
        targets = []
        for index, (fid, source) in enumerate(zip(ids, paths)):
            row = repo.get_file(fid)
            target = win.library.resolve(row.path, row.is_relative)
            targets.append(target)
            t.assert_true(f"记录{fid}转仓储", row.is_relative)
            t.assert_true(f"记录{fid}移走原文件", not source.exists())
            t.assert_true(f"记录{fid}保留原目录", source.parent.is_dir())
            t.assert_eq(f"记录{fid}移动内容完整", target.read_text(encoding="utf-8"), f"内容{'ab'[index]}")
            current = asdict(row)
            original = before[fid]
            for key in ("path", "is_relative"):
                current.pop(key)
                original.pop(key)
            t.assert_eq(f"记录{fid}所有元数据不变", current, original)
        t.assert_eq("同名移动分别落地", len(set(targets)), 2)
        t.assert_eq("封面记录ID不变", repo.get_project(pid).cover_file_id, ids[0])


def test_shared_protection(t: T, root: Path) -> None:
    """批内重复、其他项目引用和仓库内文件均保守复制留源。"""
    with _fixture(root) as (win, repo):
        pid = repo.save_project(Project(title="受保护源"))
        other = repo.save_project(Project(title="其他项目"))
        duplicate = _source(root, "external/duplicate.txt")
        shared = _source(root, "external/shared.txt")
        inside = _source(win.library.root, "existing/inside.txt")
        paths = [duplicate, duplicate, shared, inside]
        ids = [repo.add_file(FileItem(project_id=pid, path=str(path), subfolder=str(index)))
            for index, path in enumerate(paths)]
        other_id = repo.add_file(FileItem(project_id=other, path=str(shared)))
        other_before = asdict(repo.get_file(other_id))
        with _dialogs() as dialogs:
            win.action_convert_project_to_storage(pid)
        _assert_single_summary(t, dialogs, "保护源一次汇总")
        for fid, source in zip(ids, paths):
            row = repo.get_file(fid)
            t.assert_true(f"保护记录{fid}成功转仓储", row.is_relative)
            t.assert_true(f"保护记录{fid}保留源", source.is_file())
            t.assert_eq(f"保护记录{fid}复制内容一致", win.library.resolve(row.path, row.is_relative).read_bytes(), source.read_bytes())
        t.assert_eq("其他项目引用不改", asdict(repo.get_file(other_id)), other_before)
        t.assert_in("保护原因进入汇总", "保留", _summary(dialogs))


def test_cancellation(t: T, root: Path) -> None:
    """用户取消确认不处理；后台取消提交已完成项并保留未处理项。"""
    with _fixture(root) as (win, repo):
        pid = repo.save_project(Project(title="取消移动"))
        paths = [_source(root, f"external/{index}.txt") for index in range(3)]
        ids = [repo.add_file(FileItem(project_id=pid, path=str(source))) for source in paths]
        with _dialogs(), patch("app.ui.storage_move.confirm", return_value=False), patch(
            "app.ui.mw_files.confirm", return_value=False,
        ):
            win.action_convert_project_to_storage(pid)
        t.assert_true("取消确认保留全部原文件", all(path.exists() for path in paths))
        t.assert_true("取消确认全部记录保持链接", all(not repo.get_file(fid).is_relative for fid in ids))

        def run_cancelled(_parent, _title, _label, worker, *, on_done, on_error) -> None:
            # 首项真正移走后即请求取消，不依赖 worker 查询取消的次数。
            on_done(worker(lambda *_args: None, lambda: not paths[0].exists()))

        with _dialogs(runner=run_cancelled) as dialogs:
            win.action_convert_project_to_storage(pid)
        _assert_single_summary(t, dialogs, "取消后台一次汇总")
        t.assert_true("取消后首项已提交仓储", repo.get_file(ids[0]).is_relative)
        t.assert_true("取消后首项原文件移走", not paths[0].exists())
        t.assert_true("取消后未处理原文件保留", all(path.exists() for path in paths[1:]))
        t.assert_true("取消后未处理记录保持链接", all(not repo.get_file(fid).is_relative for fid in ids[1:]))
        t.assert_in("取消汇总有未处理数", "2 个未处理", _summary(dialogs))


def test_cross_volume_and_link(t: T, root: Path) -> None:
    """模拟 EXDEV 验证跨盘移动，真实 hardlink 引用必须保护共享源。"""
    with _fixture(root) as (win, repo):
        pid = repo.save_project(Project(title="跨盘和链接保护"))
        other = repo.save_project(Project(title="hardlink 引用"))
        source = _source(root, "external/cross-volume.txt")
        content = source.read_bytes()
        fid = repo.add_file(FileItem(project_id=pid, path=str(source)))
        original_rename = os.rename

        def rename(old, new, *args, **kwargs):
            if Path(old) == source:
                raise OSError(errno.EXDEV, "模拟跨盘移动")
            return original_rename(old, new, *args, **kwargs)

        # 运行环境为 Windows；仅模拟原子移动报告跨盘，保留真实读写及源移除。
        with _dialogs(), patch("app.storage_move.os.rename", side_effect=rename):
            win.action_convert_project_to_storage(pid)
        row = repo.get_file(fid)
        t.assert_true("跨盘记录成功转仓储", row.is_relative)
        t.assert_true("跨盘移动删除源且保留目录", not source.exists() and source.parent.is_dir())
        t.assert_eq("跨盘移动内容完整", win.library.resolve(row.path, row.is_relative).read_bytes(), content)

        linked = _source(root, "external/hardlink-original.txt")
        alias = root / "external/hardlink-alias.txt"
        os.link(linked, alias)
        linked_id = repo.add_file(FileItem(project_id=pid, path=str(linked)))
        repo.add_file(FileItem(project_id=other, path=str(alias)))
        with _dialogs():
            win.action_convert_project_to_storage(pid)
        t.assert_true("hardlink 引用文件转仓储", repo.get_file(linked_id).is_relative)
        t.assert_true("hardlink 本库引用保留两个路径", linked.exists() and alias.exists())

        symbolic_source = _source(root, "external/symbolic-source.txt")
        symbolic = root / "external/symbolic.txt"
        try:
            symbolic.symlink_to(symbolic_source)
        except OSError:
            # 无开发者模式的 Windows 不允许创建符号链接，仅模拟重解析识别；
            # 复制与记录转换仍使用实际合成文件。
            symbolic.write_bytes(symbolic_source.read_bytes())
        symbolic_id = repo.add_file(FileItem(project_id=pid, path=str(symbolic)))
        import app.storage_move as storage_move
        original_redirect = storage_move._has_redirect
        with _dialogs(), patch("app.storage_move._has_redirect", side_effect=lambda path: Path(path) == symbolic or original_redirect(path)):
            win.action_convert_project_to_storage(pid)
        t.assert_true("符号链接保守复制转仓储", repo.get_file(symbolic_id).is_relative)
        t.assert_true("符号链接源及目标原件保留", symbolic.exists() and symbolic_source.exists())


def test_database_rollback(t: T, root: Path) -> None:
    """DB 提交失败移回原处，原路径冲突时不覆盖并保留仓储目标。"""
    with _fixture(root) as (win, repo):
        pid = repo.save_project(Project(title="写回失败"))
        paths = [_source(root, f"external/{name}.txt") for name in ("ok", "rollback", "conflict")]
        contents = [path.read_bytes() for path in paths]
        ids = [repo.add_file(FileItem(project_id=pid, path=str(path))) for path in paths]
        before = {fid: asdict(repo.get_file(fid)) for fid in ids}
        original_update = repo.update_file
        attempted_targets = {}

        def update(row):
            if row.id in ids[1:]:
                attempted_targets[row.id] = win.library.resolve(row.path, row.is_relative)
                if row.id == ids[2]:
                    paths[2].write_text("后来出现的文件，不可覆盖", encoding="utf-8")
                raise RuntimeError(f"模拟数据库写回失败-{row.id}")
            return original_update(row)

        with _dialogs() as dialogs, patch.object(repo, "update_file", side_effect=update):
            win.action_convert_project_to_storage(pid)
        _assert_single_summary(t, dialogs, "落库及回滚失败一次汇总")
        t.assert_true("部分失败保留成功提交项", repo.get_file(ids[0]).is_relative and not paths[0].exists())
        t.assert_eq("落库失败文件成功回源", paths[1].read_bytes(), contents[1])
        t.assert_true("成功回源清除目标", not attempted_targets[ids[1]].exists())
        t.assert_eq("回滚冲突不覆盖现有源", paths[2].read_text(encoding="utf-8"), "后来出现的文件，不可覆盖")
        t.assert_eq("回滚冲突完整内容仍在目标", attempted_targets[ids[2]].read_bytes(), contents[2])
        for fid in ids[1:]:
            t.assert_eq(f"失败记录{fid}保持原值", asdict(repo.get_file(fid)), before[fid])
        detailed = _summary(dialogs, detailed=True)
        for index in (1, 2):
            for value in (str(ids[index]), str(paths[index]), str(attempted_targets[ids[index]]), f"模拟数据库写回失败-{ids[index]}"):
                t.assert_in(f"失败{ids[index]}详细信息含{value}", value, detailed)
        for value in ("RuntimeError", "回滚", "保留"):
            t.assert_in(f"回滚完整详细信息含{value}", value, detailed)


def test_missing_full_details(t: T, root: Path) -> None:
    """超过摘要长度的缺失源仍必须逐个保留完整详细路径和记录。"""
    with _fixture(root) as (win, repo):
        pid = repo.save_project(Project(title="缺失批量"))
        paths = [root / "external" / f"missing-{index}.txt" for index in range(13)]
        ids = [repo.add_file(FileItem(project_id=pid, path=str(path))) for path in paths]
        with _dialogs() as dialogs:
            win.action_convert_project_to_storage(pid)
        _assert_single_summary(t, dialogs, "批量缺失一次汇总")
        detailed = _summary(dialogs, detailed=True)
        for fid, path in zip(ids, paths):
            t.assert_true(f"缺失{fid}记录保持链接", not repo.get_file(fid).is_relative)
            t.assert_in(f"缺失{fid}完整路径进入详细列表", str(path), detailed)
            t.assert_in(f"缺失{fid}记录ID进入详细列表", str(fid), detailed)
        t.assert_in("缺失异常类型进入详细列表", "FileNotFoundError", detailed)


def test_real_worker_threads(t: T, root: Path) -> None:
    """保留真实 Qt worker，验证移动/回源在线程中且 SQLite 留在主线程。"""
    import app.storage_move as storage_move
    with _fixture(root) as (win, repo):
        pid = repo.save_project(Project(title="实际后台移动回滚"))
        sources = [_source(root, f"external/{name}.txt") for name in ("thread-ok", "thread-fail")]
        ids = [repo.add_file(FileItem(project_id=pid, path=str(path))) for path in sources]
        main_thread = threading.get_ident()
        move_threads = []
        database_threads = []
        original_move = storage_move.move_no_replace
        original_get = repo.get_file
        original_update = repo.update_file

        def move(source, target):
            move_threads.append(threading.get_ident())
            return original_move(source, target)

        def get(fid):
            database_threads.append(threading.get_ident())
            return original_get(fid)

        def update(row):
            database_threads.append(threading.get_ident())
            if row.id == ids[1]:
                raise RuntimeError("真实线程模拟落库失败")
            return original_update(row)

        with _dialogs(runner=None) as dialogs, patch("app.storage_move.move_no_replace", side_effect=move), patch.object(
            repo, "get_file", side_effect=get,
        ), patch.object(repo, "update_file", side_effect=update):
            win.action_convert_project_to_storage(pid)
            deadline = time.monotonic() + 15
            while not any(dialog.call_count for dialog in dialogs) and time.monotonic() < deadline:
                _app().processEvents()
                time.sleep(0.01)
            _app().processEvents()
        t.assert_eq("真实后台执行两次移动和一次回源", len(move_threads), 3)
        t.assert_true("真实移动和回源全部离开主线程", bool(move_threads) and all(value != main_thread for value in move_threads))
        t.assert_true("真实SQLite查询和更新全部在主线程", bool(database_threads) and all(value == main_thread for value in database_threads))
        t.assert_true("真实后台成功项已提交且移走源", repo.get_file(ids[0]).is_relative and not sources[0].exists())
        t.assert_true("真实后台失败项已回源且保留链接", not repo.get_file(ids[1]).is_relative and sources[1].is_file())
        _assert_single_summary(t, dialogs, "真实后台落库与回源一次汇总")


def test_transfer_safety_edges(t: T, root: Path) -> None:
    """验证移动收尾异常、跨盘传输中源变更及事务归属的恢复边界。"""
    import app.storage_move as storage_move
    with _fixture(root) as (win, repo):
        pid = repo.save_project(Project(title="传输安全边界"))
        source = _source(root, "external/stat-fail.txt")
        content = source.read_bytes()
        fid = repo.add_file(FileItem(project_id=pid, path=str(source)))
        target = win.library.project_dir(pid) / source.name
        inputs = storage_move.prepare_move_inputs(repo, win.library, [repo.get_file(fid)])
        original_fingerprint = storage_move._fingerprint
        failed = False

        def fingerprint(path):
            nonlocal failed
            if Path(path) == target and not failed:
                failed = True
                raise PermissionError("模拟移动后stat失败")
            return original_fingerprint(path)

        with patch("app.storage_move._fingerprint", side_effect=fingerprint):
            payload = storage_move.transfer_files(inputs, lambda *_args: None, lambda: False)
        t.assert_eq("移动后stat失败无可提交记录", payload["results"], [])
        t.assert_eq("移动后stat失败自动回源内容完整", source.read_bytes(), content)
        t.assert_true("移动后stat失败目标已恢复移走", not target.exists())
        details = "\n".join(payload["errors"])
        for value in ("PermissionError", "模拟移动后stat失败", "回滚成功", str(source), str(target), str(fid)):
            t.assert_in(f"stat失败详情含{value}", value, details)

        changing = _source(root, "external/changing.txt")
        destination = win.library.project_dir(pid) / changing.name
        original_rename = os.rename
        original_copy = shutil.copyfileobj
        fake_trash = root / "fake-trash"
        fake_trash.mkdir()
        discarded = []

        def rename(old, new, *args, **kwargs):
            if Path(old) == changing:
                raise OSError(errno.EXDEV, "模拟跨盘")
            return original_rename(old, new, *args, **kwargs)

        def copy(original, copied, length):
            original_copy(original, copied, length)
            changing.write_text("传输期间出现的新内容，必须保留", encoding="utf-8")

        def trash(path, *, allow_delete):
            t.assert_eq("清理本次失败副本不硬删除", allow_delete, False)
            path = Path(path)
            if not path.is_relative_to(root):
                raise AssertionError("模拟回收只允许本次临时目录")
            path.rename(fake_trash / path.name)
            discarded.append(path)
            return "trash"

        failure = None
        with patch("app.storage_move.os.rename", side_effect=rename), patch(
            "app.storage_move.shutil.copyfileobj", side_effect=copy,
        ), patch("app.storage_move.move_to_trash", side_effect=trash):
            try:
                storage_move.move_no_replace(changing, destination)
            except OSError as exc:
                failure = exc
        t.assert_true("跨盘源变更拒绝删除并报告错误", failure is not None)
        t.assert_eq("跨盘源变更保留最新内容", changing.read_text(encoding="utf-8"), "传输期间出现的新内容，必须保留")
        t.assert_eq("跨盘源变更清理本次旧副本", discarded, [destination])
        t.assert_true("跨盘源变更仓储目录不留旧副本", not destination.exists())

        transaction_source = _source(root, "external/transaction.txt")
        transaction_id = repo.add_file(FileItem(project_id=pid, path=str(transaction_source)))
        inputs = storage_move.prepare_move_inputs(repo, win.library, [repo.get_file(transaction_id)])
        payload = storage_move.transfer_files(inputs, lambda *_args: None, lambda: False)
        # 快照取得后出现另一个未提交事务，转换不得擅自提交或回滚它。
        repo.conn.execute("UPDATE settings SET value='pending-other-transaction' WHERE key=?", (KEY,))
        committed, recovery, errors = storage_move.commit_transfers(repo, payload["results"])
        t.assert_eq("已有事务拒绝提交转换", committed, [])
        t.assert_eq("已有事务转换项进入回源", len(recovery), 1)
        t.assert_true("已有事务仍由原调用者持有", repo.conn.in_transaction)
        t.assert_eq("已有事务未被擅自rollback", repo.get_setting(KEY), "pending-other-transaction")
        t.assert_in("已有事务拒绝原因进入报告", "未提交事务", "\n".join(errors))
        repo.conn.rollback()
        reports = storage_move.rollback_transfers(recovery, lambda *_args: None, lambda: True)
        t.assert_true("已有事务转换文件仍可恢复且忽略取消", transaction_source.is_file())
        t.assert_in("已有事务回源成功有报告", "回滚成功", "\n".join(reports))


def main() -> bool:
    _app()
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-gui-direct-move-") as temporary:
        root = Path(temporary)
        # 所有路径均为上述独立临时目录；任何回收调用都是测试失败。
        with patch("app.storage_cleanup.move_to_trash", side_effect=AssertionError("直接移动不得进入回收站")):
            test_settings(t, root / "settings")
            test_move_and_collision(t, root / "move")
            test_shared_protection(t, root / "shared")
            test_cancellation(t, root / "cancel")
            test_cross_volume_and_link(t, root / "cross-link")
            test_database_rollback(t, root / "rollback")
            test_missing_full_details(t, root / "missing")
            test_real_worker_threads(t, root / "threads")
            test_transfer_safety_edges(t, root / "edges")
    return t.report()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
