"""隔离验证大小缓存、删除回退汇总和 API 保存后的即时提示。"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from selftests._common import T
from selftests.gui_main_window_regressions import _app, _window, _close_window, _find_file_tree_item
from selftests.task42_security_regressions import MemoryKeys
from app.models import Project, FileItem
from app.llm import config
from app.ui import mw_files, mw_projects
from app.ui.settings import SettingsDialog


def run(t, root):
    app = _app()
    win, repo = _window(root)
    try:
        src = root / "temporary.txt"
        src.write_bytes(b"x")
        f = FileItem(project_id=1, path=str(src), is_relative=False, kind="text")
        with patch.object(mw_files.time, "monotonic", return_value=10):
            first = win._file_size_str(f)
        with patch.object(mw_files.time, "monotonic", return_value=10.5), patch.object(Path, "stat", side_effect=AssertionError("缓存命中不能 stat")):
            t.assert_eq("缓存命中无需 stat", win._file_size_str(f), first)
        src.write_bytes(b"x" * 8192)
        with patch.object(mw_files.time, "monotonic", return_value=12):
            t.assert_true("过期后大小更新", win._file_size_str(f) != first)
        src.unlink()
        with patch.object(mw_files.time, "monotonic", return_value=14):
            t.assert_eq("过期后识别失效文件", win._file_size_str(f), "—")

        for project_mode in (False, True):
            pid = repo.save_project(Project(title="临时删除测试"))
            src.write_text("仅测试文件", encoding="utf-8")
            rel = win.library.import_copy(pid, src)
            fid = repo.add_file(FileItem(project_id=pid, path=rel, is_relative=True, kind="text"))
            win._current_project_id = pid
            win._show_project(repo.get_project(pid))
            _find_file_tree_item(win, fid).setSelected(True)
            module = mw_projects if project_mode else mw_files
            with patch("send2trash.send2trash", side_effect=OSError("测试回收站不可用")), patch.object(module, "confirm", return_value=True) as confirm, patch.object(module, "warn") as warn, patch.object(win, "_selected_project_ids", return_value=[pid]):
                (win.action_delete_project if project_mode else win.action_delete_files)()
                t.assert_in(f"{project_mode} 删除前披露", "永久删除", str(confirm.call_args))
                t.assert_eq(f"{project_mode} 单次结果汇总", warn.call_count, 1)
                t.assert_in(f"{project_mode} 汇总披露", "永久删除", str(warn.call_args))
            t.assert_true(f"{project_mode} 临时文件已删除", not win.library.resolve(rel, True).exists())

        with patch.object(config, "_kr", return_value=MemoryKeys(reject=True)):
            dialog = SettingsDialog(repo, root, root / "cabinet.db", win)
            try:
                dialog._update_provider("deepseek", "api_key", "fake-gui-key")
                t.assert_in("保存失败即时提示明文", "明文", dialog._key_storage_tip.text())
                with patch.object(config, "_kr", return_value=MemoryKeys()):
                    dialog._update_provider("deepseek", "api_key", "fake-gui-key")
                    t.assert_in("重试成功即时提示凭据", "系统凭据引用", dialog._key_storage_tip.text())
            finally:
                dialog.close()
                dialog.deleteLater()
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-gui-regression-") as td:
        run(t, Path(td))
    sys.exit(0 if t.report() else 1)
