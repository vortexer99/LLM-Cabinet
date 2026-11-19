"""项目简介标签 GUI 回归：显式临时数据库与合成媒体，不访问用户库。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QItemSelectionModel, QModelIndex, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QScrollArea

from app.models import FileItem, Project
from app.search import TermNode, parse_search
from app.ui.theme import apply_theme
from selftests._common import T
from selftests._preview_wait import wait_preview_probe
from selftests.gui_cover_playback import _player
from selftests.gui_main_window_regressions import _app, _close_window, _window
from selftests.gui_preview_file_navigation import _select
from selftests.gui_search_filter_sync import nodes


def events() -> None:
    """处理布局与选择事件，不依赖媒体后端或搜索防抖等待。"""
    for _ in range(3):
        _app().processEvents()


def run(root: Path, t: T, screenshot: Path | None = None) -> None:
    """通过真实主窗口和鼠标/键盘验证标签入口的完整筛选行为。"""
    # Windows offscreen 插件可能不枚举系统字体；只补载系统字体供真实主题截图使用。
    if os.name == "nt" and not QFontDatabase.families():
        font_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for font_name in ("msyh.ttc", "segoeui.ttf"):
            if (font_dir / font_name).is_file():
                QFontDatabase.addApplicationFont(str(font_dir / font_name))
    apply_theme(_app())
    win, repo = _window(root)
    try:
        win.resize(1280, 820)
        special = ["带 空格", '引"号', r"反\斜杠", "<b>原样标签</b>", "AND"]
        a = repo.save_project(Project(title="alpha 视频", description_md="合成项目简介；下方标签可直接筛选。",
                                      tags=["A", *special]))
        b = repo.save_project(Project(title="alpha 同标签", tags=["A"]))
        c = repo.save_project(Project(title="alpha 子标签", tags=["A/子标签"]))
        d = repo.save_project(Project(title="beta 无标签"))
        files: dict[int, list[int]] = {}
        for pid, kinds in ((a, ["video", "doc"]), (b, ["pdf"]), (c, ["video"]), (d, ["doc"])):
            files[pid] = []
            for i, kind in enumerate(kinds):
                path = root / f"synthetic-{pid}-{i}.{'mp4' if kind == 'video' else 'txt'}"
                path.write_text("合成占位文件，不解码", encoding="utf-8")
                files[pid].append(repo.add_file(FileItem(
                    project_id=pid, path=str(path.resolve()), is_relative=False, kind=kind,
                )))

        # 初始项目选择与普通切换不解码占位文件。
        with patch.object(win.preview, "show_file"):
            win.refresh_projects()
            win._select_project_by_id(a)
            events()

        def ids() -> set[int]:
            return {win.proj_model.project_at(row).id for row in range(win.proj_model.rowCount())}

        def shown_tags() -> list[str]:
            return [chip.full_text for chip in win.project_tags.chips]

        def execute(text: str) -> None:
            win.search_box.setText(text)
            win._search_timer.stop()
            win._completion.hide()
            win.refresh_projects(filter_only=True)
            events()

        def select(pid: int) -> None:
            # 多选后当前索引可能未变，先清空当前索引以触发真实选择通知。
            win.proj_view.selectionModel().setCurrentIndex(QModelIndex(), QItemSelectionModel.Clear)
            win._select_project_by_id(pid)
            events()

        def click(name: str) -> None:
            chip = next(chip for chip in win.project_tags.chips if chip.full_text == name)
            if isinstance(win.project_tags, QScrollArea):
                win.project_tags.ensureWidgetVisible(chip)
                events()
            QTest.mouseClick(chip, Qt.LeftButton)
            events()

        def tag_terms() -> list[tuple[str, str]]:
            parsed = parse_search(win.search_box.text())
            t.assert_true("标签点击生成有效查询", parsed.ok, repr(parsed.error))
            return [(node.op, node.value) for node in nodes(parsed.ast)
                    if isinstance(node, TermNode) and node.field == "tag"]

        t.assert_eq("选中项目显示全部原始标签", set(shown_tags()), {"A", *special})
        t.assert_true("有标签时标签区可见", win.project_tags.isVisible())
        t.assert_true("标签区位于简介下方", win.project_tags.mapToGlobal(win.project_tags.rect().topLeft()).y()
                      >= win.lbl_meta_desc.mapToGlobal(win.lbl_meta_desc.rect().bottomLeft()).y())
        t.assert_true("标签支持无障碍名称", all(chip.accessibleName() for chip in win.project_tags.chips))
        select(d)
        t.assert_true("无标签项目隐藏标签区", win.project_tags.isHidden())
        t.assert_eq("无标签项目清空旧标签", shown_tags(), [])
        select(b)
        t.assert_eq("切换项目更新标签", shown_tags(), ["A"])
        t.assert_true("有标签项目切换后行高和标签可见", win.project_tags.height() > 0
                      and all(chip.isVisible() for chip in win.project_tags.chips))
        select(a)
        selection = win.proj_view.selectionModel()
        selection.select(win.proj_model.index_of_id(b), QItemSelectionModel.Select)
        win._on_project_selected(win.proj_model.index_of_id(a), QModelIndex())
        events()
        t.assert_true("多选清空并隐藏单项目标签", win.project_tags.isHidden() and not shown_tags())
        selection.clearSelection()
        select(a)

        # 数据保存后的标准刷新入口必须更新元数据，避免只更新项目列表。
        project = repo.get_project(a)
        project.tags.append("新保存标签")
        repo.save_project(project)
        win.refresh_projects()
        events()
        t.assert_eq("元数据刷新后标签同步", set(shown_tags()), {"A", *special, "新保存标签"})
        t.assert_true("可见标签区刷新后保持有效行高", win.project_tags.height() > 0
                      and all(chip.isVisible() for chip in win.project_tags.chips))
        if screenshot is not None:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            t.assert_true("合成项目截图保存", win.grab().save(str(screenshot)))

        execute("")
        select(a)
        click("A")
        t.assert_eq("点击精确标签排除子标签", ids(), {a, b})
        t.assert_eq("点击标签写入完整精确查询", tag_terms(), [("=", "A")])
        t.assert_eq("点击标签同步侧栏", win.tag_tree.current_filter(), ("tag", "A"))
        query = win.search_box.text()
        with patch.object(win, "refresh_projects", wraps=win.refresh_projects) as refresh:
            click("A")
            QTest.qWait(250)
            t.assert_eq("重复点击不叠加条件", win.search_box.text(), query)
            t.assert_eq("一次点击只执行一次刷新", refresh.call_count, 1)
        t.assert_true("程序修改查询不弹补全", not win._completion.isVisible())
        t.assert_true("程序修改查询不遗留防抖定时器", not win._search_timer.isActive())

        for name in special:
            click(name)
            t.assert_eq(f"特殊标签精确筛选 {name}", ids(), {a})
            t.assert_eq(f"特殊标签原样往返 {name}", tag_terms(), [("=", name)])
            t.assert_eq(f"特殊标签同步侧栏 {name}", win.tag_tree.current_filter(), ("tag", name))
            t.assert_eq(f"特殊标签无富文本解释 {name}",
                        next(chip for chip in win.project_tags.chips if chip.full_text == name).textFormat(), Qt.PlainText)

        execute("alpha")
        win.file_type_filter.set_selected_kinds({"video"})
        select(a)
        click("A")
        t.assert_eq("标签与关键词文件筛选相交", ids(), {a})
        parsed = parse_search(win.search_box.text())
        t.assert_true("点击标签保留关键词", any(isinstance(node, TermNode) and node.field is None
                      and node.value == "alpha" for node in nodes(parsed.ast)))
        t.assert_eq("点击标签保留文件类型", win.file_type_filter.selected_kinds, {"video"})
        t.assert_eq("保留筛选时侧栏同步精确标签", win.tag_tree.current_filter(), ("tag", "A"))

        # 模拟真实播放器入口，不实际解码；筛选后项目仍命中时应保持文件和播放状态。
        player, state = _player()
        with patch.object(win.preview._video, "_player", player):
            _select(win, files[a][0])
            wait_preview_probe(win.preview)
            t.assert_true("筛选前已载入所选合成视频", state["source"].isLocalFile())
            player.setPosition(12345)
            player.pause()
            events()
            before = state.copy()
            t.assert_eq("合成视频已加载且暂停", state["playback"], QMediaPlayer.PausedState)
            with patch.object(win.preview, "show_file", wraps=win.preview.show_file) as show:
                player.reset_mock()
                click("A")
                t.assert_eq("标签过滤保留文件当前行", win._current_file_row_id(), files[a][0])
                t.assert_eq("标签过滤保留预览文件ID", win._current_file_id, files[a][0])
                t.assert_eq("标签过滤不重载预览", show.call_count, 0)
                t.assert_eq("标签过滤保留视频源位置暂停状态", state, before)
                t.assert_eq("标签过滤不调用播放器", player.mock_calls, [])

        win._preview_focus.toggle_focus()
        events()
        t.assert_true("专注模式隐藏标签区", not win.project_tags.isVisible())
        # 后台元数据刷新也不能让标签在专注预览中重新出现。
        win.refresh_projects()
        events()
        t.assert_true("专注模式元数据刷新仍隐藏标签", not win.project_tags.isVisible())
        win._preview_focus.restore()
        events()
        t.assert_true("退出专注恢复有标签项目", win.project_tags.isVisible())

        execute("")
        select(a)
        widths = (win.width(), win._main_splitter.sizes())
        many = ["长标签" * 180, *(f"大量标签-{i:03}" for i in range(80))]
        project = repo.get_project(a)
        project.tags = many
        repo.save_project(project)
        win.refresh_projects()
        events()
        t.assert_eq("长标签及大量标签不撑宽窗口", win.width(), widths[0])
        t.assert_eq("长标签不改变分栏宽度", win._main_splitter.sizes(), widths[1])
        t.assert_eq("大量标签全部保留", set(shown_tags()), set(many))
        t.assert_true("标签区高度有上限", win.project_tags.height() <= 160)
        long_chip = next(chip for chip in win.project_tags.chips if chip.full_text == many[0])
        t.assert_true("长标签完整内容可查看", many[0] in long_chip.toolTip())
        t.assert_true("长标签显示会省略而保留完整名称", long_chip.text() != many[0]
                      and long_chip.accessibleName() == many[0])
        t.assert_true("大量标签有滚动容器", isinstance(win.project_tags, QScrollArea))
        if isinstance(win.project_tags, QScrollArea):
            area = win.project_tags
            t.assert_true("大量标签存在可滚动范围", area.verticalScrollBar().maximum() > 0)
            for chip in win.project_tags.chips:
                area.ensureWidgetVisible(chip)
                events()
                pos = chip.mapTo(area.viewport(), chip.rect().center())
                t.assert_true(f"标签可滚动访问 {chip.full_text[:22]}", area.viewport().rect().contains(pos))
            last = next(chip for chip in win.project_tags.chips if chip.full_text == many[-1])
            area.ensureWidgetVisible(last)
            events()
            last.setFocus()
            QTest.keyClick(last, Qt.Key_Return)
            events()
            t.assert_eq("滚动末尾标签支持键盘筛选", tag_terms(), [("=", many[-1])])
            click(many[0])
            t.assert_eq("省略的长标签仍以完整值筛选", tag_terms(), [("=", many[0])])

        win._current_project_id = None
        win._show_project(None)
        events()
        t.assert_true("清空选中项目清空隐藏标签", win.project_tags.isHidden() and not shown_tags())
    finally:
        win.preview.show_file(None)
        _close_window(win)
        repo.conn.close()


if __name__ == "__main__":
    _app()
    t = T()
    screenshot = Path(tempfile.gettempdir()) / "cabinet-project-tag-chips-preview.png"
    with tempfile.TemporaryDirectory(prefix="cabinet-project-tag-chips-") as directory:
        run(Path(directory), t, screenshot)
    print(f"合成数据截图：{screenshot}")
    raise SystemExit(0 if t.report() else 1)
