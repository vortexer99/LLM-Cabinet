"""新建库引导向导（task #15 T1）。

功能：把"新建库"从一次性技术操作（选目录 → 起名 → 重启）升级为多页 onboarding
流程：

* 第 1 页：选择目录 + 名称
* 第 2 页：库描述（可选）
* 第 3 页：可选默认字段勾选 + 列表显示控制
* 第 4 页（仅当已有其它库时）：从其它库迁移 API 配置（仅 LLM / 全部 两档）

设计要点（与 ``tasks/15-new-library-onboarding.md`` 的 D1~D5 一致）：

* **D1 db 晚建**：第 1~4 页只收集表单数据到 ``self.state``；用户在最后一页点
  「创建库」才一次性原子化建库（mark + connect + seed + 加可选 + 写描述 + 迁移
  API）。任何一步失败 → ``shutil.rmtree(root)`` 整体回滚。任意页取消 / 关 X →
  零副作用（什么都没碰过）。
* **D2 默认列可见性**：仰仗 ``app/db.py:_seed_fields`` 已经按 D2 配置（描述 /
  标签 visible=0）。本向导只对**可选默认字段**控制可见性。
* **D3 API 迁移两档**：仅迁移 LLM 配置（默认） / 全部迁移。
* **D4 / D5**：与 T2 / T3 协同；本文件不直接负责。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...cabinet import (
    CabinetConfig,
    LibraryHandle,
    import_settings_from_other_db,
    is_empty_or_safe_for_library,
    mark_as_library,
    resolve_library_paths,
    validate_library_path,
)
from ...db import OPTIONAL_DEFAULT_FIELDS, connect as db_connect


# 页索引常量
PAGE_PATH = 0
PAGE_DESCRIPTION = 1
PAGE_FIELDS = 2
PAGE_API_MIGRATE = 3  # 仅当 want_api_page=True 时显示


# 迁移内容两档（D3）
MIGRATE_KEYS_LLM_ONLY = ["llm_config"]
MIGRATE_KEYS_ALL = [
    "llm_config",
    "llm_default_provider",
    "llm_default_language",
    "wizard_max_rounds",
]


@dataclass
class _OptionalFieldChoice:
    """第 3 页里每行可选字段的选择状态。"""
    name: str
    type: str
    key: str
    selected: bool = False
    visible: bool = True  # 列表显示


@dataclass
class _NewLibraryState:
    """整个向导收集的所有用户选择。直到点「创建库」才被读出。"""
    root: Optional[Path] = None
    label: str = ""
    description: str = ""
    optional_fields: list[_OptionalFieldChoice] = field(default_factory=list)
    # 必有字段的列表可见性（标题恒为 True；描述/标签默认 False，但用户可在向导里勾选）
    required_visible: dict[str, bool] = field(
        default_factory=lambda: {"title": True, "description": False, "tags": False}
    )
    # API 迁移
    migrate_source: Optional[Path] = None  # None = 不迁移
    migrate_full: bool = False  # False = 仅 llm_config / True = 全部


class NewLibraryWizard(QDialog):
    """新建库的多页向导（QDialog + QStackedWidget）。

    用法：
        wiz = NewLibraryWizard(cabinet_config, parent=...)
        if wiz.exec() == QDialog.Accepted:
            # 库已经被创建好；wiz.created_root 是新库根路径
            ...
    """

    def __init__(
        self,
        cabinet_config: CabinetConfig,
        *,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建库")
        self.setMinimumSize(640, 520)
        self.cabinet_config = cabinet_config
        self.state = _NewLibraryState()
        # 初始化可选默认字段选择（默认全不选；列表显示默认全开）
        self.state.optional_fields = [
            _OptionalFieldChoice(name=name, type=ftype, key=key, selected=False, visible=bool(vis))
            for (name, ftype, key, vis) in OPTIONAL_DEFAULT_FIELDS
        ]
        # 是否需要 API 迁移页：已存在至少一个**其它**库才显示
        self._existing_handles: list[LibraryHandle] = list(
            cabinet_config.recent_libraries
        )
        self.want_api_page: bool = len(self._existing_handles) > 0
        # 创建成功后写回（外部可读）
        self.created_root: Optional[Path] = None

        self._build_ui()
        self._refresh_buttons()

    # ----- UI 构建 ----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)

        # 顶部：标题 + 当前页指示
        top = QHBoxLayout()
        ttl = QLabel("新建库")
        f = QFont(); f.setPointSize(14); f.setBold(True)
        ttl.setFont(f)
        top.addWidget(ttl)
        top.addStretch(1)
        self.lbl_step = QLabel("")
        self.lbl_step.setProperty("muted", True)
        top.addWidget(self.lbl_step)
        root.addLayout(top)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_page_path())
        self.stack.addWidget(self._build_page_description())
        self.stack.addWidget(self._build_page_fields())
        if self.want_api_page:
            self.stack.addWidget(self._build_page_api_migrate())
        root.addWidget(self.stack, 1)

        # 底部按钮
        btns = QHBoxLayout()
        self.btn_back = QPushButton("← 上一步")
        self.btn_back.clicked.connect(self._on_back)
        btns.addWidget(self.btn_back)
        btns.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        btns.addWidget(self.btn_cancel)
        # 注：原本每页底部还有「跳过」按钮；后来发现「下一步」在描述页 / 字段页就已
        # 经允许空内容直接通过，再加「跳过」反而让按钮区拥挤、语义重复。所以移除。
        self.btn_next = QPushButton("下一步 →")
        self.btn_next.setDefault(True)
        self.btn_next.clicked.connect(self._on_next)
        btns.addWidget(self.btn_next)
        self.btn_finish = QPushButton("✅ 创建库")
        self.btn_finish.clicked.connect(self._on_finish)
        btns.addWidget(self.btn_finish)
        root.addLayout(btns)

        self.stack.currentChanged.connect(self._refresh_buttons)
        self.stack.setCurrentIndex(PAGE_PATH)

    def _build_page_path(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)
        v.addWidget(QLabel("<h3>📁 选择库目录</h3>"))

        form = QFormLayout()
        path_row = QHBoxLayout()
        self.ed_path = QLineEdit()
        self.ed_path.setPlaceholderText("例如：D:/Libraries/papers")
        path_row.addWidget(self.ed_path, 1)
        b_browse = QPushButton("浏览...")
        b_browse.clicked.connect(self._on_browse_dir)
        path_row.addWidget(b_browse)
        form.addRow("路径：", path_row)

        self.ed_label = QLineEdit()
        self.ed_label.setPlaceholderText("仅显示用；可随时改")
        form.addRow("名称：", self.ed_label)
        v.addLayout(form)

        hint = QLabel("💡 建议选择空目录；非空目录会被拒绝。")
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addStretch(1)
        return w

    def _build_page_description(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)
        v.addWidget(QLabel("<h3>📝 库描述（可选）</h3>"))
        intro = QLabel("这个库打算管理什么内容？有什么特别约定？")
        intro.setWordWrap(True)
        intro_hint = QLabel(
            "这段描述用于你自己备忘、以及发给 LLM 作为「库字段设计助手」的核心上下文；"
            "现在也可以暂时留空，之后随时回到「设置 → 库」补写。"
        )
        intro_hint.setProperty("hint", True)
        intro_hint.setWordWrap(True)
        v.addWidget(intro)
        v.addWidget(intro_hint)
        self.ed_description = QPlainTextEdit()
        self.ed_description.setPlaceholderText(
            "例如：管理我读过的科幻小说与论文；标题用书名/篇名；"
            "标签按『领域/科幻』『状态/已读』层级使用..."
        )
        v.addWidget(self.ed_description, 1)
        return w

    def _build_page_fields(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)
        v.addWidget(QLabel("<h3>📋 默认字段</h3>"))

        # 必有字段（不可移除，但描述/标签的「列表显示」可在此勾选）
        v.addWidget(QLabel("<b>必有字段（不可移除）：</b>"))
        self._req_vis_widgets: dict[str, QCheckBox] = {}
        for key, name, ftype, default_vis, hint in [
            ("title",       "标题", "text",     True,  "（标题列必显，不可隐藏）"),
            ("description", "描述", "textarea", False, "（多行文本，建议默认隐藏）"),
            ("tags",        "标签", "tags",     False, "（左侧标签树已可筛选，建议默认隐藏）"),
        ]:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"✅ {name}  ({ftype})"))
            row.addStretch(1)
            cb_vis = QCheckBox("列表显示")
            cb_vis.setChecked(self.state.required_visible.get(key, default_vis))
            if key == "title":
                cb_vis.setEnabled(False)
                cb_vis.setToolTip("标题列必显，不可隐藏")
            else:
                cb_vis.setToolTip(f"建库时{hint[1:-1]}；之后可在「设置 → 字段」改")
            row.addWidget(cb_vis)
            v.addLayout(row)
            self._req_vis_widgets[key] = cb_vis

        # 可选字段
        v.addWidget(QLabel("<b>可选常用字段（按需勾选）：</b>"))
        self._opt_widgets: list[tuple[QCheckBox, QCheckBox]] = []
        for choice in self.state.optional_fields:
            row = QHBoxLayout()
            cb_use = QCheckBox(f"{choice.name}  ({choice.type})")
            cb_use.setChecked(choice.selected)
            row.addWidget(cb_use)
            row.addStretch(1)
            cb_vis = QCheckBox("列表显示")
            cb_vis.setChecked(choice.visible)
            row.addWidget(cb_vis)
            v.addLayout(row)
            self._opt_widgets.append((cb_use, cb_vis))

        hint = QLabel(
            "💡 不确定要哪些？直接「下一步」即可，建完库后可在「LLM 助手 → 库字段设计助手」里"
            "让 AI 根据你的库描述给出方案。\n"
            "💡 「列表显示」决定字段是否出现在主界面项目列表中；建库后随时可在"
            "「设置 → 字段」里改。"
        )
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addStretch(1)
        return w

    def _build_page_api_migrate(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)
        v.addWidget(QLabel("<h3>🔑 从其它库迁移 API 配置</h3>"))
        v.addWidget(QLabel(
            "检测到你已经有其它库；可以把现有的 LLM 平台 / API Key / "
            "默认 provider / 默认语言等配置一键带到新库，省去重复填表。"
        ))

        # 迁移内容三档（不迁移 / 仅 LLM / 全部）
        v.addWidget(QLabel("<b>迁移内容：</b>"))
        self.rb_none = QRadioButton(
            "不迁移，等会自己在「设置 → API」配"
        )
        self.rb_llm_only = QRadioButton(
            "仅迁移 LLM 配置（provider 列表 + 各 api_key）"
        )
        self.rb_llm_only.setChecked(True)
        self.rb_full = QRadioButton(
            "全部迁移（含默认 provider / 默认语言 / 助手轮数）"
        )
        grp = QButtonGroup(self)
        grp.addButton(self.rb_none)
        grp.addButton(self.rb_llm_only)
        grp.addButton(self.rb_full)
        v.addWidget(self.rb_none)
        v.addWidget(self.rb_llm_only)
        v.addWidget(self.rb_full)

        # 源库选择（近期库下拉 + 「浏览其它库...」末项）
        self.lbl_source = QLabel("<b>从哪个库迁移？</b>")
        v.addWidget(self.lbl_source)
        self.cmb_source = QComboBox()
        # 按 last_opened 倒序展示（cabinet_config.recent_libraries 已经按这个顺序）
        for h in self._existing_handles:
            label = f"{h.display_name}  —  {h.path}"
            self.cmb_source.addItem(label, userData=str(h.path))
        self.cmb_source.addItem("📂 浏览其它库目录...", userData="__browse__")
        # 默认选项 = 当前活动库（如果有）
        active = self.cabinet_config.active_library
        if active is not None:
            for i in range(self.cmb_source.count()):
                if self.cmb_source.itemData(i) == str(active):
                    self.cmb_source.setCurrentIndex(i)
                    break
        self.cmb_source.activated.connect(self._on_source_combo_activated)
        v.addWidget(self.cmb_source)

        # 联动启停
        self.rb_none.toggled.connect(self._refresh_api_page_enabled)
        self.rb_llm_only.toggled.connect(self._refresh_api_page_enabled)
        self.rb_full.toggled.connect(self._refresh_api_page_enabled)
        self._refresh_api_page_enabled()

        v.addStretch(1)
        return w

    def _refresh_api_page_enabled(self) -> None:
        """根据"不迁移"是否选中，启停下方源库选择控件。"""
        do_migrate = not self.rb_none.isChecked()
        self.lbl_source.setEnabled(do_migrate)
        self.cmb_source.setEnabled(do_migrate)

    def _on_source_combo_activated(self, index: int) -> None:
        """选择"浏览其它库..."时弹目录选择器，让用户挑一个含 .llm-cabinet 标记的库目录。"""
        if self.cmb_source.itemData(index) != "__browse__":
            return
        from ...cabinet import is_library_dir
        d = QFileDialog.getExistingDirectory(
            self, "选择要迁移配置的库目录（必须含 .llm-cabinet 标记）",
        )
        # 不论用户选了还是取消，先把选中项重置回原来那个，避免"浏览..."项停留为当前选中
        # （稍后若选择有效库目录，会切到对应的项 / 新插入项）
        prev_idx = max(0, index - 1) if self.cmb_source.count() > 1 else 0
        if not d:
            self.cmb_source.setCurrentIndex(prev_idx)
            return
        path = Path(d)
        if not is_library_dir(path):
            QMessageBox.warning(
                self, "不是有效的库目录",
                f"目录 {path} 缺少 .llm-cabinet 标记，无法识别为 LLM Cabinet 库。",
            )
            self.cmb_source.setCurrentIndex(prev_idx)
            return
        # 命中已存在的项就直接选；否则在 "浏览..." 项之前插入新项
        target_str = str(path)
        for i in range(self.cmb_source.count()):
            if self.cmb_source.itemData(i) == target_str:
                self.cmb_source.setCurrentIndex(i)
                return
        browse_idx = self.cmb_source.count() - 1  # "浏览..." 是末项
        self.cmb_source.insertItem(
            browse_idx, f"{path.name}  —  {path}", userData=target_str,
        )
        self.cmb_source.setCurrentIndex(browse_idx)

    # ----- 按钮 / 翻页 ------------------------------------------------------
    def _refresh_buttons(self, *_args) -> None:
        idx = self.stack.currentIndex()
        last_idx = self.stack.count() - 1
        # 步骤指示
        self.lbl_step.setText(f"第 {idx + 1} / {self.stack.count()} 步")
        self.btn_back.setVisible(idx > 0)
        # 最后一页：显示"创建库"，隐藏"下一步"
        is_last = (idx == last_idx)
        self.btn_next.setVisible(not is_last)
        self.btn_finish.setVisible(is_last)

    def _on_back(self) -> None:
        idx = self.stack.currentIndex()
        if idx > 0:
            self.stack.setCurrentIndex(idx - 1)

    def _on_next(self) -> None:
        idx = self.stack.currentIndex()
        if idx == PAGE_PATH:
            if not self._validate_and_collect_path():
                return
        elif idx == PAGE_DESCRIPTION:
            self._collect_description()
        elif idx == PAGE_FIELDS:
            self._collect_fields()
        # 翻到下一页
        if idx + 1 < self.stack.count():
            self.stack.setCurrentIndex(idx + 1)

    def _on_finish(self) -> None:
        # 最后一页：先收集本页数据，再尝试建库
        idx = self.stack.currentIndex()
        if idx == PAGE_FIELDS:
            self._collect_fields()
        elif idx == PAGE_API_MIGRATE:
            self._collect_api_migrate()
        ok = self._create_library()
        if ok:
            self.accept()
        # 失败时不关闭对话框，让用户决定重试或取消

    # ----- 收集每页数据 ----------------------------------------------------
    def _validate_and_collect_path(self) -> bool:
        path_str = self.ed_path.text().strip()
        if not path_str:
            QMessageBox.warning(self, "请输入路径", "请先选一个库目录。")
            return False
        root = Path(path_str)
        # 路径合法性（绝对路径 / 非法字符 / 系统保护目录 / 父目录存在 等）
        err = validate_library_path(root)
        if err is not None:
            QMessageBox.warning(self, "路径不合适", err)
            return False
        if not is_empty_or_safe_for_library(root):
            QMessageBox.warning(
                self, "目录不可用",
                f"目录 {root} 已含其它文件，不适合作为新库目录。\n请选一个空目录。",
            )
            return False
        # 不允许与已有库目录重复
        for h in self._existing_handles:
            if h.path.resolve() == root.resolve():
                QMessageBox.warning(
                    self, "目录冲突",
                    f"目录 {root} 已经是一个已知库（{h.display_name}）。\n"
                    "如果想打开它，请用「库 → 切换库」。",
                )
                return False
        self.state.root = root
        self.state.label = self.ed_label.text().strip() or root.name
        return True

    def _collect_description(self) -> None:
        self.state.description = self.ed_description.toPlainText().strip()

    def _collect_fields(self) -> None:
        for choice, (cb_use, cb_vis) in zip(
            self.state.optional_fields, self._opt_widgets,
        ):
            choice.selected = cb_use.isChecked()
            choice.visible = cb_vis.isChecked()
        # 必有字段的可见性（标题强制 True；描述/标签按用户勾选）
        for key, cb in self._req_vis_widgets.items():
            self.state.required_visible[key] = (
                True if key == "title" else cb.isChecked()
            )

    def _collect_api_migrate(self) -> None:
        if self.rb_none.isChecked():
            self.state.migrate_source = None
            self.state.migrate_full = False
            return
        data = self.cmb_source.currentData()
        # data 可能是 "__browse__"（用户没确定就一直停在浏览项）/ 路径字符串 / None
        if isinstance(data, str) and data and data != "__browse__":
            self.state.migrate_source = Path(data)
        else:
            # 选了「迁移」但下拉没有有效目标 → 等同不迁移（兜底，避免抛异常）
            self.state.migrate_source = None
        self.state.migrate_full = self.rb_full.isChecked()

    # ----- 创建库（D1 7 步原子化） ----------------------------------------
    def _create_library(self) -> bool:
        """晚建：所有写盘操作集中在此，任一步失败 → rmtree 整体回滚。"""
        s = self.state
        if s.root is None:
            QMessageBox.warning(self, "状态异常", "未收集到目标目录。")
            return False

        root = s.root
        # 标记 rmtree 边界：root 在 mark_as_library 之前是否存在 / 是否非空？
        # is_empty_or_safe_for_library 已经保证目录是空/不存在 / 仅包含安全标记。
        # 失败时 rmtree(root) 是安全的（不会误删用户已有内容）。
        root_pre_existed = root.exists()
        try:
            # Step 1: mark + lib_subdir + connect db
            mark_as_library(root)
            db_path, lib_subdir = resolve_library_paths(root)
            lib_subdir.mkdir(parents=True, exist_ok=True)
            conn = db_connect(db_path)  # 自动 _seed_fields + _ensure_protected_fields
            try:
                cur = conn.cursor()

                # Step 2: 应用「必有字段」的可见性偏好（标题恒可见；描述/标签
                # _seed_fields 默认 visible=0，但用户在第 3 页可勾选展示）
                for key, vis in s.required_visible.items():
                    if key == "title":
                        continue  # 标题恒可见，UI 也禁用了；跳过避免无谓 update
                    cur.execute(
                        "UPDATE fields SET visible=? WHERE key=?",
                        (1 if vis else 0, key),
                    )

                # Step 3-4: 加可选字段（_seed_fields 已经种了 标题/描述/标签）
                # 直接 INSERT，跟 _seed_fields 同款 schema；ord 接续
                row = cur.execute(
                    "SELECT COALESCE(MAX(ord), -1) AS m FROM fields"
                ).fetchone()
                next_ord = (row["m"] if row else -1) + 1
                for choice in s.optional_fields:
                    if not choice.selected:
                        continue
                    cur.execute(
                        "INSERT INTO fields(name, type, ord, visible, key) "
                        "VALUES(?, ?, ?, ?, ?)",
                        (
                            choice.name, choice.type, next_ord,
                            1 if choice.visible else 0, choice.key,
                        ),
                    )
                    next_ord += 1

                # Step 5: 写库描述
                if s.description:
                    cur.execute(
                        "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
                        ("library_description", s.description),
                    )

                # Step 5b: 新建库默认视图 = list（表格）
                # 全局 fallback 仍是 "grid"（既有库不动；见 main_window 启动加载）；
                # 这里只对**通过本向导新建的库**显式写入 list，让"列表显示"复选框
                # 控制的列直接成为新库首屏视觉。
                cur.execute(
                    "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
                    ("default_view_mode", "list"),
                )

                # Step 6: API 迁移（D3 三档；"不迁移"时 migrate_source = None）
                if s.migrate_source is not None:
                    src_db, _ = resolve_library_paths(s.migrate_source)
                    keys = MIGRATE_KEYS_ALL if s.migrate_full else MIGRATE_KEYS_LLM_ONLY
                    imported = import_settings_from_other_db(src_db, keys)
                    for k, v in imported.items():
                        cur.execute(
                            "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
                            (k, v),
                        )

                conn.commit()
            finally:
                conn.close()

            # Step 7: 注册到 cabinet_config（不在 try 里 rmtree 范围内—— 走到这里
            # 已经是写盘成功，只剩内存配置注册）
            self.cabinet_config.touch(root, label=s.label)
            self.cabinet_config.save()
            self.created_root = root
            return True
        except Exception as e:  # noqa: BLE001
            # 回滚：删掉刚才 mark 的目录
            try:
                if root.exists():
                    if root_pre_existed:
                        # 目录用户原本就存在（且通过了 is_empty_or_safe_for_library 检查
                        # 即只包含安全标记 / 无业务文件），rmtree 后再 mkdir 还回去
                        shutil.rmtree(root)
                        root.mkdir(parents=True, exist_ok=True)
                    else:
                        shutil.rmtree(root)
            except OSError:
                pass
            QMessageBox.critical(
                self, "创建失败",
                f"创建库时出错（已尝试回滚目录）：\n{e}",
            )
            return False

    # ----- 工具 -------------------------------------------------------------
    def _on_browse_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "选择新库的位置（建议选空目录）",
        )
        if d:
            self.ed_path.setText(d)
            # 没填名字时用目录名做默认 label
            if not self.ed_label.text().strip():
                self.ed_label.setText(Path(d).name)
