"""项目网址 GUI 回归：编辑器打开按钮、列表双击路由和浏览器失败反馈。

仅使用显式路径的临时数据库和合成项目；拦截浏览器调用，不实际联网。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, Qt, QUrl
from PySide6.QtWidgets import QLineEdit, QWidget

from app.db import connect
from app.models import Field, Project
from app.repository import Repository
from app.ui import web_links
from app.ui.project_dialog import ProjectDialog, _UrlEditor
from selftests._common import T, closing_repos
from selftests.gui_main_window_regressions import (
    _app, _close_window, _field, _window,
)


def _close_widget(widget: QWidget) -> None:
    """清理控件的延迟删除信号，随后才能关闭临时数据库。"""
    widget.close()
    widget.deleteLater()
    _app().sendPostedEvents(None, QEvent.DeferredDelete)
    _app().processEvents()


def _url_fields(repo: Repository) -> tuple[Field, Field]:
    """合成带稳定 key 的来源字段及普通自定义 URL 字段。"""
    source_id = _field(repo, "来源", "url", "source_url")
    custom_id = repo.add_field("参考链接", "url")
    fields = {field.id: field for field in repo.list_fields()}
    return fields[source_id], fields[custom_id]


def test_browser_open(t: T) -> None:
    """只接受显式 HTTP(S)，并通过统一提示反馈无效值和系统启动失败。"""
    valid = (
        "https://example.com/source?q=1#part",
        "http://localhost:8080/path",
        "  https://example.com/中文?q=测试  ",
    )
    for value in valid:
        url = web_links.web_url(value)
        t.assert_true(f"HTTP(S)链接可解析 {value}", isinstance(url, QUrl))
        if url is not None:
            t.assert_true(f"链接有host {value}", bool(url.host()))
    invalid = (
        "", "example.com", "https://", "https://bad host/path",
        "file:///C:/Windows/notepad.exe", "javascript:alert(1)",
    )
    parent = QWidget()
    try:
        with patch.object(web_links.QDesktopServices, "openUrl", return_value=True) as browser, patch.object(web_links, "warn") as warn:
            t.assert_true("浏览器成功打开返回真", web_links.open_web_url(parent, valid[2]))
            t.assert_eq("浏览器接收去首尾空格的QUrl", browser.call_args,
                        call(QUrl(valid[2].strip())))
            t.assert_eq("成功无需警告", warn.call_count, 0)
            browser.reset_mock()
            for value in invalid:
                t.assert_eq(f"拒绝非网页值 {value!r}", web_links.web_url(value), None)
                t.assert_eq(f"非法值返回假 {value!r}", web_links.open_web_url(parent, value), False)
            t.assert_eq("非法链接不启动浏览器", browser.call_count, 0)
            t.assert_eq("每个非法值均有提示", warn.call_count, len(invalid))
        with patch.object(web_links.QDesktopServices, "openUrl", return_value=False) as browser, patch.object(web_links, "warn") as warn:
            t.assert_eq("系统拒绝打开返回假", web_links.open_web_url(parent, valid[0]), False)
            t.assert_eq("系统拒绝打开只尝试一次", browser.call_count, 1)
            t.assert_eq("系统拒绝打开提示一次", warn.call_count, 1)
            t.assert_eq("警告归属调用控件", warn.call_args.args[0], parent)
    finally:
        _close_widget(parent)


def test_dialog(t: T, root: Path) -> None:
    """真实项目对话框支持所有 URL 字段，保留编辑、建议应用和保存语义。"""
    root.mkdir()
    repo = Repository(connect(root / "cabinet.db"))
    with closing_repos(repo):
        source, custom = _url_fields(repo)
        source_url = "https://example.com/original"
        suggested_url = "https://example.com/suggested"
        pid = repo.save_project(Project(title="链接编辑回归", field_values={
            source.id: source_url,
            custom.id: "",
        }))
        # source_task_id 可空；合成建议不启动任何 LLM 任务。
        repo.add_suggestions(pid, None, [(custom.id, suggested_url)])
        dialog = ProjectDialog(repo.get_project(pid), repo)
        try:
            for field in (source, custom):
                editor = dialog._editors[field.id][1]
                t.assert_true(f"{field.name}使用URL编辑器", isinstance(editor, _UrlEditor))
                t.assert_true(f"{field.name}仍兼容单行编辑", isinstance(editor, QLineEdit))
            source_editor = dialog._editors[source.id][1]
            custom_editor = dialog._editors[custom.id][1]
            t.assert_eq("现有来源链接可打开", source_editor._open_action.isEnabled(), True)
            t.assert_eq("空自定义网址禁用打开", custom_editor._open_action.isEnabled(), False)
            t.assert_in("打开action已加入编辑器", source_editor._open_action, source_editor.actions())

            # 打开时读取当前文本，避免将构造时的旧值捕获在信号闭包中。
            current = "https://example.com/edited"
            source_editor.setText(current)
            with patch.object(web_links.QDesktopServices, "openUrl", return_value=True) as browser, patch.object(web_links, "warn") as warn:
                source_editor._open_action.trigger()
                t.assert_eq("编辑器按钮打开当前输入", browser.call_args, call(QUrl(current)))
                t.assert_eq("打开网址不改编辑内容", source_editor.text(), current)
                t.assert_eq("打开网址不自动保存数据库", repo.get_project(pid).field_values[source.id], source_url)
                t.assert_eq("编辑器成功打开无警告", warn.call_count, 0)

            for value in ("", "  ", "example.com", "javascript:alert(1)"):
                source_editor.setText(value)
                t.assert_eq(f"输入非法值动态禁用 {value!r}", source_editor._open_action.isEnabled(), False)
            source_editor.setText("  https://example.com/final  ")
            t.assert_eq("输入有效链接恢复打开", source_editor._open_action.isEnabled(), True)
            t.assert_eq("读取URL仍去首尾空格", dialog._read_editor(source, source_editor), "https://example.com/final")

            dialog._apply_one(custom.id)
            t.assert_eq("应用建议保留新网址", custom_editor.text(), suggested_url)
            t.assert_eq("建议更新后自动启用打开", custom_editor._open_action.isEnabled(), True)
            t.assert_eq("建议真实标记已应用", repo.list_pending_suggestions(pid), [])
            with patch.object(web_links.QDesktopServices, "openUrl", return_value=False), patch.object(web_links, "warn") as warn:
                custom_editor._open_action.trigger()
                t.assert_eq("编辑器启动失败提示一次", warn.call_count, 1)
            dialog._accept()
            repo.save_project(dialog.project())
            persisted = repo.get_project(pid)
            t.assert_eq("保存来源URL兼容旧流程", persisted.field_values[source.id], "https://example.com/final")
            t.assert_eq("保存自定义URL兼容建议流程", persisted.field_values[custom.id], suggested_url)
        finally:
            _close_widget(dialog)


def test_table_signal(t: T, root: Path) -> None:
    """从真实 MainWindow 的 doubleClicked 信号验证列类型路由。"""
    root.mkdir()
    win, repo = _window(root)
    try:
        source, custom = _url_fields(repo)
        source_url = "https://example.com/source?key=1#full"
        custom_url = "https://example.com/custom"
        pid = repo.save_project(Project(title="列表网址回归", field_values={
            source.id: source_url, custom.id: custom_url,
        }))
        win.refresh_projects()
        win._set_view_mode("list")
        _app().processEvents()
        model = win.proj_model
        row = model.index_of_id(pid).row()
        columns = {model.column_key(col): col for col in range(model.columnCount())}

        def emit(key: str) -> None:
            win.proj_table.doubleClicked.emit(model.index(row, columns[key]))

        with patch.object(win, "action_edit_project") as edit, patch.object(web_links.QDesktopServices, "openUrl", return_value=True) as browser, patch.object(web_links, "warn") as warn:
            for key, expected in (("source_url", source_url), (f"user:{custom.id}", custom_url)):
                index = model.index(row, columns[key])
                t.assert_eq(f"{key}展示值不变", model.data(index, Qt.DisplayRole), expected)
                tooltip = model.data(index, Qt.ToolTipRole) or ""
                t.assert_in(f"{key}提示包含完整网址", expected, tooltip)
                t.assert_in(f"{key}提示双击操作", "双击", tooltip)
                emit(key)
                t.assert_eq(f"{key}双击路由默认浏览器", browser.call_args, call(QUrl(expected)))
            t.assert_eq("URL列双击不进入编辑框", edit.call_count, 0)
            t.assert_eq("两种URL字段只启动两次浏览器", browser.call_count, 2)
            for key in ("title", "__files__", "__updated__"):
                emit(key)
            t.assert_eq("普通列双击仍进入编辑", edit.call_count, 3)
            t.assert_eq("普通列不触发浏览器", browser.call_count, 2)
            t.assert_eq("列表成功打开无需警告", warn.call_count, 0)

        # 真实刷新后覆盖空URL和非法URL两条分支。
        project = repo.get_project(pid)
        project.field_values[source.id] = ""
        project.field_values[custom.id] = "javascript:alert(1)"
        repo.save_project(project)
        win.refresh_projects()
        row = model.index_of_id(pid).row()
        with patch.object(win, "action_edit_project") as edit, patch.object(web_links.QDesktopServices, "openUrl", return_value=True) as browser, patch.object(web_links, "warn") as warn:
            emit("source_url")
            t.assert_eq("空URL双击仍编辑", edit.call_count, 1)
            emit(f"user:{custom.id}")
            t.assert_eq("非空非法URL不退回编辑", edit.call_count, 1)
            t.assert_eq("非法URL双击提示一次", warn.call_count, 1)
            t.assert_eq("空及非法URL不启动浏览器", browser.call_count, 0)

        project.field_values[source.id] = source_url
        repo.save_project(project)
        win.refresh_projects()
        row = model.index_of_id(pid).row()
        with patch.object(win, "action_edit_project") as edit, patch.object(web_links.QDesktopServices, "openUrl", return_value=False), patch.object(web_links, "warn") as warn:
            emit("source_url")
            t.assert_eq("列表浏览器启动失败提示一次", warn.call_count, 1)
            t.assert_eq("启动失败不会意外打开编辑框", edit.call_count, 0)
    finally:
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    t = T()
    test_browser_open(t)
    with tempfile.TemporaryDirectory(prefix="cabinet-gui-project-links-", ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        test_dialog(t, root / "编辑器")
        test_table_signal(t, root / "列表")
    sys.exit(0 if t.report() else 1)
