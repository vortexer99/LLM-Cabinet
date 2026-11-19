"""设置页 · 项目库（task #35 T3：从 settings_dialog.py 拆分，方法体未改动）。

Mixin：设置页 · 项目库
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ... import HOMEPAGE_URL, __version__
from ...db import SCHEMA_VERSION
from ...models import FIELD_TYPE_LABELS, FIELD_TYPES
from ...repository import Repository
from ...storage_cleanup import STORAGE_RECYCLE_SOURCE_KEY
from ...utils import app_data_dir, reveal_in_explorer
from ..dialogs import info, warn


class LibraryPageMixin:
    """设置页 · 项目库"""

    def _build_library_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        title = QLabel("项目库")
        title.setProperty("h1", True)
        lay.addWidget(title)

        gb1 = QGroupBox("默认导入行为")
        form = QFormLayout(gb1)
        self.cmb_storage = QComboBox()
        self.cmb_storage.addItem("🔗 链接（仅记录路径，不动用户文件）", "link")
        self.cmb_storage.addItem("📦 仓储（复制到统一仓库目录）", "copy")
        cur = self.repo.get_setting("default_storage_mode", "link") or "link"
        idx = self.cmb_storage.findData(cur)
        self.cmb_storage.setCurrentIndex(max(0, idx))
        self.cmb_storage.currentIndexChanged.connect(self._on_storage_changed)
        form.addRow("默认存储方式：", self.cmb_storage)
        hint = QLabel(
            "拖放新建项目、以及向项目里添加文件时使用此默认值。\n"
            "每次添加文件仍可在弹出的对话框中临时改选；"
            "存储方式是文件级属性，同一项目内可混合存在。"
        )
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        form.addRow("", hint)

        self.chk_storage_recycle_source = QCheckBox("仓储存入成功后，将原文件移入回收站")
        self.chk_storage_recycle_source.setChecked(
            self.repo.get_setting(STORAGE_RECYCLE_SOURCE_KEY, "0") == "1"
        )
        self.chk_storage_recycle_source.toggled.connect(
            lambda checked: self.repo.set_setting(STORAGE_RECYCLE_SOURCE_KEY, "1" if checked else "0")
        )
        form.addRow("原文件处理：", self.chk_storage_recycle_source)
        recycle_hint = QLabel(
            "默认关闭；适用于本库后续仓储导入和链接转仓储，复制及入库成功后才回收原文件。\n"
            "保留原文件夹（含空文件夹）、未导入的文件和解包导入的 ZIP 原包。\n"
            "仍被本库其他链接引用的文件、仓库内部文件不会回收；回收站不可用时保留原文件并提示。"
        )
        recycle_hint.setProperty("hint", True)
        recycle_hint.setWordWrap(True)
        form.addRow("", recycle_hint)

        self.chk_storage_convert_move = QCheckBox("链接转仓储时，直接移动原文件")
        self.chk_storage_convert_move.setChecked(
            self.repo.get_setting("storage_convert_move", "0") == "1"
        )
        self.chk_storage_convert_move.toggled.connect(
            lambda checked: self.repo.set_setting("storage_convert_move", "1" if checked else "0")
        )
        form.addRow("转仓储方式：", self.chk_storage_convert_move)
        move_hint = QLabel(
            "默认关闭，仅影响文件、文件夹及单个／多个项目的链接转仓储。开启后优先直接移动，"
            "不经过回收站；同盘通常无需复制，跨盘仍需传输数据，原目录保留。\n"
            "为保护其他引用，共享源、仓库内部文件等情况会改用复制并保留源文件，在结果中说明。"
            "此项优先于原文件回收设置；新导入仍按原有设置处理。"
        )
        move_hint.setProperty("hint", True)
        move_hint.setWordWrap(True)
        form.addRow("", move_hint)

        self.chk_ignore_dotfiles = QCheckBox("忽略 . 开头的文件和文件夹")
        cur_idf = self.repo.get_setting("import_ignore_dotfiles", "1")
        self.chk_ignore_dotfiles.setChecked(cur_idf == "1")
        self.chk_ignore_dotfiles.toggled.connect(self._on_ignore_dotfiles_changed)
        form.addRow("隐藏文件：", self.chk_ignore_dotfiles)
        idf_hint = QLabel(
            "开启（默认）：跳过 .gitignore、.env、.git/ 等以 . 开头的文件和目录。\n"
            "关闭：导入所有文件，包括隐藏文件。"
        )
        idf_hint.setProperty("hint", True)
        idf_hint.setWordWrap(True)
        form.addRow("", idf_hint)

        self.chk_import_auto_cover = QCheckBox("导入项目时，自动用第一张图片作为封面")
        self.chk_import_auto_cover.setChecked(self.repo.get_setting("import_auto_cover", "0") == "1")
        self.chk_import_auto_cover.toggled.connect(
            lambda checked: self.repo.set_setting("import_auto_cover", "1" if checked else "0")
        )
        form.addRow("自动封面：", self.chk_import_auto_cover)
        self.chk_import_video_cover = QCheckBox("没有图片、仅有视频时，自动截取第一个视频的首帧作为封面")
        self.chk_import_video_cover.setChecked(
            self.repo.get_setting("import_auto_cover_video", "0") == "1"
        )
        self.chk_import_video_cover.setEnabled(self.chk_import_auto_cover.isChecked())
        self.chk_import_auto_cover.toggled.connect(self.chk_import_video_cover.setEnabled)
        self.chk_import_video_cover.toggled.connect(
            lambda checked: self.repo.set_setting("import_auto_cover_video", "1" if checked else "0")
        )
        video_cover_row = QHBoxLayout()
        video_cover_row.addSpacing(20)
        video_cover_row.addWidget(self.chk_import_video_cover)
        form.addRow("", video_cover_row)
        cover_hint = QLabel(
            "仅对本库后续导入的新项目生效，按逻辑目录和文件名的自然顺序选择图片。\n"
            "主开关开启后，才可选择视频首帧子选项；关闭主开关后子选项不生效。\n"
            "图片优先；没有图片时，按相同顺序选择第一个视频，截图保存为库内衍生图片。\n"
            "导入包原有封面优先；不会替换已有封面，也不会补改历史项目。"
        )
        cover_hint.setProperty("hint", True)
        cover_hint.setWordWrap(True)
        form.addRow("", cover_hint)
        self.cmb_cover_replacement = QComboBox()
        self.cmb_cover_replacement.addItem("保留旧封面文件", "0")
        self.cmb_cover_replacement.addItem("替换旧封面文件（移到回收站）", "1")
        self.cmb_cover_replacement.setCurrentIndex(
            1 if self.repo.get_setting("cover_replace_generated", "0") == "1" else 0
        )
        self.cmb_cover_replacement.currentIndexChanged.connect(
            lambda _index: self.repo.set_setting(
                "cover_replace_generated", self.cmb_cover_replacement.currentData(),
            )
        )
        form.addRow("更换封面时：", self.cmb_cover_replacement)
        replacement_hint = QLabel(
            "默认保留旧封面文件；选择替换后，新封面保存成功才回收上一张生成封面。"
            "原始图片和共享文件保留，不清理更早的历史封面。\n"
            "文件清单默认仅显示用户文件，可用清单上方的来源按钮查看生成的封面；"
            "已保存的来源筛选偏好继续生效。"
        )
        replacement_hint.setProperty("hint", True)
        replacement_hint.setWordWrap(True)
        form.addRow("", replacement_hint)

        lay.addWidget(gb1)

        gb_preview = QGroupBox("预览行为（当前库）")
        preview_layout = QVBoxLayout(gb_preview)
        self.chk_auto_select_first_file = QCheckBox("选中项目时自动选择第一个文件")
        self.chk_auto_select_first_file.setChecked(
            self.repo.get_setting("auto_select_first_file", "0") == "1"
        )
        self.chk_auto_select_first_file.toggled.connect(
            lambda checked: self.repo.set_setting("auto_select_first_file", "1" if checked else "0")
        )
        preview_layout.addWidget(self.chk_auto_select_first_file)
        first_file_hint = QLabel(
            "默认关闭；开启后，初次选中或切换项目时，按文件列表当前排序和来源筛选选择第一个文件。"
            "跳过目录节点；同项目刷新保留当前选择。视频是否播放沿用下方的自动播放设置。"
        )
        first_file_hint.setProperty("hint", True)
        first_file_hint.setWordWrap(True)
        preview_layout.addWidget(first_file_hint)
        self.chk_video_autoplay = QCheckBox("选中视频时自动开始播放")
        self.chk_video_autoplay.setChecked(self.repo.get_setting("video_autoplay", "0") == "1")
        self.chk_video_autoplay.toggled.connect(
            lambda checked: self.repo.set_setting("video_autoplay", "1" if checked else "0")
        )
        preview_layout.addWidget(self.chk_video_autoplay)
        autoplay_hint = QLabel("默认关闭；开启后，下次选中视频即开始播放。切换文件或项目会停止原视频。")
        autoplay_hint.setProperty("hint", True)
        autoplay_hint.setWordWrap(True)
        preview_layout.addWidget(autoplay_hint)
        end_row = QHBoxLayout()
        end_row.addWidget(QLabel("视频播放结束后："))
        self.cmb_video_end_action = QComboBox()
        for label, value in (("停止", "stop"), ("循环播放当前视频", "loop"), ("播放下一个文件", "next")):
            self.cmb_video_end_action.addItem(label, value)
        end_index = self.cmb_video_end_action.findData(self.repo.get_setting("video_end_action", "stop"))
        self.cmb_video_end_action.setCurrentIndex(max(0, end_index))
        self.cmb_video_end_action.currentIndexChanged.connect(
            lambda _index: self.repo.set_setting("video_end_action", self.cmb_video_end_action.currentData())
        )
        end_row.addWidget(self.cmb_video_end_action, 1)
        preview_layout.addLayout(end_row)
        end_hint = QLabel(
            "默认停止，修改立即生效。下一个文件遵循当前项目的排序和来源筛选，末尾停止、不循环；"
            "下一个是视频时自动播放，是图片等文件时显示预览。此项独立于上方的选中自动播放开关。"
        )
        end_hint.setProperty("hint", True)
        end_hint.setWordWrap(True)
        preview_layout.addWidget(end_hint)
        self.chk_preview_wheel_navigation = QCheckBox("预览区滚轮用于切换上一个／下一个文件")
        self.chk_preview_wheel_navigation.setChecked(
            self.repo.get_setting("preview_wheel_navigation", "0") == "1"
        )
        self.chk_preview_wheel_navigation.toggled.connect(
            lambda checked: self.repo.set_setting("preview_wheel_navigation", "1" if checked else "0")
        )
        preview_layout.addWidget(self.chk_preview_wheel_navigation)
        wheel_hint = QLabel(
            "默认关闭，修改立即生效。开启后在预览画面向上滚动选择上一个文件，"
            "向下选择下一个文件，首尾不循环；关闭时保留图片缩放、PDF 滚动等原有行为。"
            "音量和进度等控制条不受影响。"
        )
        wheel_hint.setProperty("hint", True)
        wheel_hint.setWordWrap(True)
        preview_layout.addWidget(wheel_hint)
        lay.addWidget(gb_preview)

        archive_group = QGroupBox("压缩包（当前库）")
        archive_layout = QVBoxLayout(archive_group)
        self.chk_archive_remember_password = QCheckBox("在项目描述中记录成功使用的解压密码")
        self.chk_archive_remember_password.setChecked(
            self.repo.get_setting("archive_remember_password", "0") == "1"
        )
        self.chk_archive_remember_password.toggled.connect(
            lambda checked: self.repo.set_setting("archive_remember_password", "1" if checked else "0")
        )
        archive_layout.addWidget(self.chk_archive_remember_password)
        archive_hint = QLabel(
            "默认关闭。开启后，仅在解压成功并入库后，向项目描述末尾追加压缩包名称和成功密码。\n"
            "密码以明文保存；分享、导出或向 LLM 发送项目描述时会包含它。关闭不会删除已有记录。"
        )
        archive_hint.setProperty("hint", True)
        archive_hint.setWordWrap(True)
        archive_layout.addWidget(archive_hint)
        lay.addWidget(archive_group)

        gb2 = QGroupBox("库目录")
        v = QVBoxLayout(gb2)
        path_row = QHBoxLayout()
        self.ed_lib = QLineEdit(str(self.library_root))
        self.ed_lib.setReadOnly(True)
        btn_open = QPushButton("📂  打开")
        btn_open.clicked.connect(lambda: reveal_in_explorer(self.library_root))
        path_row.addWidget(self.ed_lib, 1)
        path_row.addWidget(btn_open)
        v.addLayout(path_row)
        tip = QLabel("仓库目录用于存放『复制』模式下导入的文件。")
        tip.setProperty("hint", True)
        tip.setWordWrap(True)
        v.addWidget(tip)
        lay.addWidget(gb2)

        # 数据位置（只读 + 打开按钮）
        gb3 = QGroupBox("数据位置")
        gv = QVBoxLayout(gb3)
        for label, path in (
            ("数据库", self.db_path),
        ):
            row = QHBoxLayout()
            lbl = QLabel(f"{label}：")
            lbl.setFixedWidth(110)
            lbl.setProperty("muted", True)
            ed = QLineEdit(str(path))
            ed.setReadOnly(True)
            b_open = QPushButton("📂")
            b_open.setToolTip("在资源管理器中打开")
            b_open.setProperty("flat", True)
            # 数据库定位到所在目录；目录直接打开
            target = path if Path(path).is_dir() else Path(path).parent
            b_open.clicked.connect(lambda _=False, t=target: reveal_in_explorer(t))
            row.addWidget(lbl)
            row.addWidget(ed, 1)
            row.addWidget(b_open)
            gv.addLayout(row)

        # Schema 版本 + 备份状态
        ver_row = QHBoxLayout()
        ver_cap = QLabel("Schema 版本：")
        ver_cap.setFixedWidth(110)
        ver_cap.setProperty("muted", True)
        ver_val = QLabel(f"v{SCHEMA_VERSION}")
        ver_val.setToolTip(
            "数据库 schema 版本号（独立于应用版本号）。\n"
            "升级新版应用打开旧 db 时，会自动备份并应用迁移脚本。\n"
            "备份文件落在数据库同目录，文件名形如 cabinet.vN.时间戳.bak"
        )
        # 顺手统计同目录下的 .bak 数量
        try:
            bak_dir = Path(self.db_path).parent
            n_bak = sum(
                1 for p in bak_dir.glob(f"{Path(self.db_path).stem}.v*.bak")
            )
        except OSError:
            n_bak = 0
        bak_info = QLabel(
            f"·  自动备份：{n_bak} 份" if n_bak else "·  自动备份：暂无"
        )
        bak_info.setProperty("muted", True)
        ver_row.addWidget(ver_cap)
        ver_row.addWidget(ver_val)
        ver_row.addSpacing(8)
        ver_row.addWidget(bak_info)
        ver_row.addStretch(1)
        gv.addLayout(ver_row)

        lay.addWidget(gb3)

        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(w)
        return scroll


    def _on_storage_changed(self, _i: int) -> None:
        v = self.cmb_storage.currentData()
        self.repo.set_setting("default_storage_mode", v)
        self.default_storage_changed.emit(v)


    def _on_ignore_dotfiles_changed(self, checked: bool) -> None:
        self.repo.set_setting("import_ignore_dotfiles", "1" if checked else "0")
