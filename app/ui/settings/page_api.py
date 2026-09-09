"""设置页 · API（LLM provider 配置）（task #35 T3：从 settings_dialog.py 拆分，方法体未改动）。

Mixin：设置页 · API（LLM provider 配置）
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ... import HOMEPAGE_URL, __version__
from ...db import SCHEMA_VERSION
from ...models import FIELD_TYPE_LABELS, FIELD_TYPES
from ...repository import Repository
from ...utils import app_data_dir, reveal_in_explorer
from ..dialogs import info, warn


class ApiPageMixin:
    """设置页 · API（LLM provider 配置）"""

    def _build_api_page(self) -> QWidget:
        from ...llm import load_config, save_config
        from ...llm.config import PROVIDER_DEFAULTS, PROVIDER_IDS

        self._llm_save_config = save_config
        self._llm_cfg = load_config(self.repo)

        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)

        title = QLabel("API（大模型）")
        title.setProperty("h1", True)
        outer.addWidget(title)

        tip = QLabel("配置 API Key 后，即可在项目元数据编辑或右键菜单中调用 LLM 生成字段建议。")
        tip.setProperty("hint", True)
        tip.setWordWrap(True)
        outer.addWidget(tip)

        # task #42：密钥存储方式说明（keyring 可用 / 回退明文两种形态）
        from ...llm.config import key_storage_notice
        storage_tip = QLabel(key_storage_notice(self.repo))
        self._key_storage_tip = storage_tip
        storage_tip.setWordWrap(True)
        outer.addWidget(storage_tip)

        # 全局：默认平台 / 默认语言
        gb_global = QGroupBox("全局")
        gf = QFormLayout(gb_global)
        self.cmb_default_provider = QComboBox()
        for pid in PROVIDER_IDS:
            self.cmb_default_provider.addItem(PROVIDER_DEFAULTS[pid]["label"], pid)
        idx = self.cmb_default_provider.findData(self._llm_cfg.default_provider)
        self.cmb_default_provider.setCurrentIndex(max(0, idx))
        self.cmb_default_provider.currentIndexChanged.connect(self._on_default_provider)
        gf.addRow("默认启用平台：", self.cmb_default_provider)

        self.cmb_default_lang = QComboBox()
        for lang in ("中文", "English"):
            self.cmb_default_lang.addItem(lang, lang)
        idx2 = self.cmb_default_lang.findData(self._llm_cfg.default_language)
        self.cmb_default_lang.setCurrentIndex(max(0, idx2))
        self.cmb_default_lang.currentIndexChanged.connect(self._on_default_language)
        gf.addRow("默认语言：", self.cmb_default_lang)
        outer.addWidget(gb_global)

        # 各平台分组（可滚动）
        scroll = QScrollArea()
        scroll.setObjectName("AppScrollArea")  # 对应 theme.py QScrollArea#AppScrollArea
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # 关键：让 viewport 不要 autoFillBackground，否则 fusion 默认浅色会盖住
        # theme.py 给 ScrollArea 设的窗口色背景，深色主题下视觉上整片白底。
        try:
            scroll.viewport().setAutoFillBackground(False)
        except Exception:
            pass
        host = QWidget()
        host.setObjectName("ApiScrollHost")  # 对应 theme.py QWidget#ApiScrollHost
        host_l = QVBoxLayout(host)
        host_l.setSpacing(8)
        host_l.setContentsMargins(0, 0, 0, 0)

        self._provider_widgets: dict[str, dict] = {}
        for pid in PROVIDER_IDS:
            host_l.addWidget(self._build_provider_box(pid))
        host_l.addStretch(1)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

        # LLM 助手对话轮数（task #11 T3 决策 2b；原在「通用」页，2026-06-02 搬来此处）
        gb_wiz = QGroupBox("LLM 助手")
        form_wiz = QFormLayout(gb_wiz)
        form_wiz.setLabelAlignment(Qt.AlignLeft)
        from ..wizards.library_init import (
            DEFAULT_MAX_ROUNDS, get_max_rounds, set_max_rounds,
        )
        self._wiz_set_max_rounds = set_max_rounds  # 保存引用，避免闭包重复 import
        self.spin_wiz_rounds = QSpinBox()
        self.spin_wiz_rounds.setRange(1, 20)
        self.spin_wiz_rounds.setValue(get_max_rounds(self.repo))
        self.spin_wiz_rounds.valueChanged.connect(self._on_wiz_rounds_changed)
        form_wiz.addRow("一次会话的最大对话轮数：", self.spin_wiz_rounds)
        hint_w = QLabel(
            f"默认 {DEFAULT_MAX_ROUNDS}。每次「让 LLM 给出建议」或「在当前基础上调整」算一轮，"
            "用户编辑预览不计数；达上限后只能「重新开始」或采用当前结果。"
        )
        hint_w.setProperty("hint", True)
        hint_w.setWordWrap(True)
        form_wiz.addRow("", hint_w)
        outer.addWidget(gb_wiz)

        return w


    def _build_provider_box(self, pid: str) -> QWidget:
        from ...llm.config import PROVIDER_DEFAULTS
        defaults = PROVIDER_DEFAULTS[pid]
        pcfg = self._llm_cfg.providers[pid]

        gb = QGroupBox(defaults["label"])
        gf = QFormLayout(gb)

        ed_url = QLineEdit(pcfg.base_url)
        ed_url.setPlaceholderText(defaults["base_url"])
        ed_url.editingFinished.connect(lambda pid=pid, e=ed_url: self._update_provider(pid, "base_url", e.text()))
        gf.addRow("Base URL：", ed_url)

        ed_key = QLineEdit(pcfg.api_key)
        ed_key.setEchoMode(QLineEdit.Password)
        ed_key.setPlaceholderText("sk-...")
        ed_key.editingFinished.connect(lambda pid=pid, e=ed_key: self._update_provider(pid, "api_key", e.text()))
        # 显示/隐藏切换
        b_eye = QToolButton()
        b_eye.setText("👁")
        b_eye.setCheckable(True)
        b_eye.toggled.connect(lambda on, e=ed_key: e.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        key_row = QHBoxLayout()
        key_row.addWidget(ed_key, 1); key_row.addWidget(b_eye)
        key_wrap = QWidget(); key_wrap.setLayout(key_row)
        gf.addRow("API Key：", key_wrap)

        ed_model = QLineEdit(pcfg.model)
        ed_model.setPlaceholderText(defaults["model"])
        ed_model.editingFinished.connect(lambda pid=pid, e=ed_model: self._update_provider(pid, "model", e.text()))
        gf.addRow("模型：", ed_model)

        # 测试按钮
        b_test = QPushButton("🔌 测试连接")
        lbl_status = QLabel("")
        lbl_status.setProperty("hint", True)
        b_test.clicked.connect(
            lambda _checked=False, pid=pid, lbl=lbl_status: self._test_provider(pid, lbl)
        )
        test_row = QHBoxLayout()
        test_row.addWidget(b_test); test_row.addWidget(lbl_status, 1)
        test_wrap = QWidget(); test_wrap.setLayout(test_row)
        gf.addRow("", test_wrap)

        if not defaults.get("supports_image"):
            note = QLabel("⚠ 此平台不支持图像输入；选中的图片文件将被跳过。")
            note.setProperty("hint", True)
            gf.addRow("", note)

        self._provider_widgets[pid] = {
            "url": ed_url, "key": ed_key, "model": ed_model, "status": lbl_status,
        }
        return gb


    def _on_default_provider(self, _i: int) -> None:
        self._llm_cfg.default_provider = self.cmb_default_provider.currentData() or "deepseek"
        self._llm_save_config(self.repo, self._llm_cfg)
        self._refresh_key_storage_notice()


    def _on_default_language(self, _i: int) -> None:
        self._llm_cfg.default_language = self.cmb_default_lang.currentData() or "中文"
        self._llm_save_config(self.repo, self._llm_cfg)
        self._refresh_key_storage_notice()


    def _update_provider(self, pid: str, key: str, value: str) -> None:
        pc = self._llm_cfg.providers.get(pid)
        if pc is None:
            return
        setattr(pc, key, (value or "").strip())
        self._llm_save_config(self.repo, self._llm_cfg)
        self._refresh_key_storage_notice()

    def _refresh_key_storage_notice(self) -> None:
        """保存后立即更新提示，包含本次凭据写入失败的明文回退结果。"""
        from ...llm.config import key_storage_notice
        self._key_storage_tip.setText(key_storage_notice(self.repo))


    def _test_provider(self, pid: str, lbl: QLabel) -> None:
        from ...llm.config import PROVIDER_DEFAULTS, ProviderConfig

        # 点测试时，实时从输入框读最新值（用户可能还没失焦保存）
        widgets = self._provider_widgets.get(pid) or {}
        ed_url = widgets.get("url")
        ed_key = widgets.get("key")
        ed_model = widgets.get("model")

        defaults = PROVIDER_DEFAULTS.get(pid)
        if defaults is None:
            lbl.setText(f"❌ 未知平台：{pid!r}")
            return

        url = (ed_url.text() if ed_url else "").strip() or defaults["base_url"]
        key = (ed_key.text() if ed_key else "").strip()
        model = (ed_model.text() if ed_model else "").strip() or defaults["model"]

        if not key:
            lbl.setText("⚠ 未填写 API Key")
            return

        # 同步进缓存配置 + 持久化（避免用户接着用却忘了失焦保存）
        pc = self._llm_cfg.providers.get(pid)
        if pc is None:
            pc = ProviderConfig(id=pid)
            self._llm_cfg.providers[pid] = pc
        pc.id = pid
        pc.base_url = url
        pc.api_key = key
        pc.model = model
        self._llm_save_config(self.repo, self._llm_cfg)

        lbl.setText("测试中…")
        lbl.repaint()
        # 放到子线程里跑，避免阻塞 UI（HTTP 调用可能 1~8 秒）
        self._run_ping_async(pc, lbl)


    def _run_ping_async(self, pc, lbl: QLabel) -> None:
        from ...llm import get_provider

        class _Worker(QObject):
            done = Signal(bool, str)

            def __init__(self, pcfg):
                super().__init__()
                self.pcfg = pcfg

            def run(self):
                try:
                    ok, msg = get_provider(self.pcfg).ping()
                except Exception as e:
                    ok, msg = False, f"{type(e).__name__}: {e}"
                self.done.emit(ok, msg)

        thread = QThread(self)
        worker = _Worker(pc)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def _on_done(ok: bool, msg: str) -> None:
            try:
                lbl.setText(("✅ " if ok else "❌ ") + msg)
            except RuntimeError:
                pass  # label 可能已随对话框销毁
            thread.quit()

        worker.done.connect(_on_done)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        # 防止 worker/thread 被 GC
        if not hasattr(self, "_ping_threads"):
            self._ping_threads: list = []
        self._ping_threads.append((thread, worker))
        thread.start()
