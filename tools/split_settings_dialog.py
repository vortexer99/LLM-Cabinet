"""一次性重构脚本（task #35 T3）：把 app/ui/settings_dialog.py 拆成 settings/ 包。

原理与 tools/split_main_window.py 相同：用 AST 定位 SettingsDialog 类里每个方法
的源码区间，按"方法名 → 页 mixin"映射搬运到 settings/page_*.py；字段相关小对话框
搬到 settings/field_dialogs.py；settings/dialog.py 保留类骨架（__init__ /
set_active_category / 信号）。搬运不改任何方法体文本，保证零手误。

死代码 _on_columns_changed（已废弃的空方法）在本脚本中直接丢弃。

用法：python tools/split_settings_dialog.py（幂等性不保证，跑一次后请删除或保留归档）
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path("app/ui/settings_dialog.py")
OUT_DIR = Path("app/ui/settings")

# ---------------------------------------------------------------- 方法分配
GENERAL = {
    "_build_general_page", "_on_wiz_rounds_changed", "_on_font_size_changed",
    "_open_wizards",
}
LIBRARY = {
    "_build_library_page", "_on_storage_changed", "_on_ignore_dotfiles_changed",
}
VIEW = {
    "_build_view_page", "_on_view_changed",
}
FIELDS = {
    "_build_fields_page", "_reload_fields_table", "_current_field_id",
    "_field_add", "_field_rename", "_field_toggle_visible",
    "_field_toggle_suggest", "_field_edit_prompt_hint", "_field_change_type",
    "_count_field_impact", "_field_move", "_field_delete",
}
API = {
    "_build_api_page", "_build_provider_box", "_on_default_provider",
    "_on_default_language", "_update_provider", "_test_provider",
    "_run_ping_async",
}
MCP = {
    "_build_mcp_page", "_mcp_show_export_dialog",
}
ABOUT = {
    "_build_about_page", "_open_privacy_doc",
}
DEAD = {"_on_columns_changed"}  # 死代码，直接丢弃

# ---------------------------------------------------------------- 头部模板
# 与原 settings_dialog.py 顶部一致的全量导入，避免漏导入（同 mw_* 的统一头部策略）
HEADER_COMMON = '''"""{title}（task #35 T3：从 settings_dialog.py 拆分，方法体未改动）。

{mixin_doc}
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
from ...utils import app_data_dir, reveal_in_explorer
from ..dialogs import info, warn


'''

HEADER_PAGE = HEADER_COMMON

CLASS_TMPL = '''class {cls}:
    """{doc}"""

'''


def main() -> None:
    src = SRC.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)

    dlg = next(
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "SettingsDialog"
    )
    field_dlg_names = {
        "_DeleteFieldChoiceDialog", "_AddFieldDialog", "_FieldTypeChangeConfirmDialog",
    }
    field_dlgs = [
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name in field_dlg_names
    ]
    mcp_html = next(
        n for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_MCP_CAPABILITIES_HTML" for t in n.targets)
    )

    def seg(node) -> str:
        start = node.lineno - 1
        if getattr(node, "decorator_list", None) and node.decorator_list:
            start = node.decorator_list[0].lineno - 1
        return "".join(lines[start:node.end_lineno])

    def fix_rel(text: str) -> str:
        """搬入 settings/ 包后深了一层，方法体内的相对导入统一加一级。"""
        return re.sub(
            r"from (\.+)(?=\w)",
            lambda m: "from " + m.group(1) + ".",
            text,
        )

    methods: dict[str, str] = {}
    signals: list[str] = []
    for n in dlg.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods[n.name] = seg(n)
        elif isinstance(n, ast.Assign):
            # 类级 Signal 定义
            for t in n.targets:
                if isinstance(t, ast.Name):
                    signals.append(seg(n))

    methods = {k: fix_rel(v) for k, v in methods.items()}

    assigned = GENERAL | LIBRARY | VIEW | FIELDS | API | MCP | ABOUT
    all_names = set(methods)
    unknown = (assigned | DEAD) - all_names
    assert not unknown, f"映射了不存在的方法: {unknown}"
    keep = all_names - assigned - DEAD
    print(f"保留在 dialog.py: {sorted(keep)}")
    assert keep == {"__init__", "set_active_category"}, f"意外保留: {keep}"

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    groups = [
        ("page_general.py", "GeneralPageMixin", "设置页 · 通用", GENERAL),
        ("page_library.py", "LibraryPageMixin", "设置页 · 项目库", LIBRARY),
        ("page_view.py", "ViewPageMixin", "设置页 · 视图", VIEW),
        ("page_fields.py", "FieldsPageMixin", "设置页 · 字段（库级字段管理）", FIELDS),
        ("page_api.py", "ApiPageMixin", "设置页 · API（LLM provider 配置）", API),
        ("page_mcp.py", "McpPageMixin", "设置页 · MCP 集成", MCP),
        ("page_about.py", "AboutPageMixin", "设置页 · 关于", ABOUT),
    ]
    for fname, cls, doc, names in groups:
        body = [HEADER_PAGE.format(title=doc, mixin_doc=f"Mixin：{doc}")]
        if fname == "page_mcp.py":
            body.append(seg(mcp_html) + "\n\n\n")
        body.append(CLASS_TMPL.format(cls=cls, doc=doc))
        ordered = [n for n in methods if n in names]
        for name in ordered:
            body.append(methods[name].rstrip() + "\n\n\n")
        text = "".join(body).rstrip() + "\n"
        (OUT_DIR / fname).write_text(text, encoding="utf-8", newline="\n")
        print(f"写出 settings/{fname}: {len(ordered)} 个方法")

    # ---- field_dialogs.py ----
    body = [HEADER_PAGE.format(
        title="字段相关小对话框",
        mixin_doc=(
            "删除字段的数据处理选择、新建字段、字段类型变更确认。"
            "供设置页与建库向导共用。"
        ),
    )]
    for n in field_dlgs:
        body.append(fix_rel(seg(n)).rstrip() + "\n\n\n")
    (OUT_DIR / "field_dialogs.py").write_text(
        "".join(body).rstrip() + "\n", encoding="utf-8", newline="\n",
    )
    print(f"写出 settings/field_dialogs.py: {len(field_dlgs)} 个类")

    # ---- dialog.py（框架）----
    mixin_imports = (
        "\nfrom .page_about import AboutPageMixin\n"
        "from .page_api import ApiPageMixin\n"
        "from .page_fields import FieldsPageMixin\n"
        "from .page_general import GeneralPageMixin\n"
        "from .page_library import LibraryPageMixin\n"
        "from .page_mcp import McpPageMixin\n"
        "from .page_view import ViewPageMixin\n"
    )
    parts = [HEADER_PAGE.format(
        title="设置对话框框架",
        mixin_doc=(
            "左类别 + 右内容（QListWidget + QStackedWidget）。"
            "各页实现见同包 page_*.py mixin；字段小对话框见 field_dialogs.py。"
        ),
    )]
    parts.append(mixin_imports)
    parts.append(
        '\n\nclass SettingsDialog(\n'
        "    GeneralPageMixin,\n"
        "    LibraryPageMixin,\n"
        "    ViewPageMixin,\n"
        "    FieldsPageMixin,\n"
        "    ApiPageMixin,\n"
        "    McpPageMixin,\n"
        "    AboutPageMixin,\n"
        "    QDialog,\n"
        "):\n"
    )
    parts.append('    """设置面板。变更通过信号通知，调用方决定是否立即应用。"""\n\n')
    for s in signals:
        parts.append("    " + s.strip() + "\n")
    parts.append("\n")
    for name in ("__init__", "set_active_category"):
        parts.append(methods[name].rstrip() + "\n\n\n")
    (OUT_DIR / "dialog.py").write_text(
        "".join(parts).rstrip() + "\n", encoding="utf-8", newline="\n",
    )
    print("写出 settings/dialog.py")

    # ---- __init__.py ----
    (OUT_DIR / "__init__.py").write_text(
        '"""设置对话框包（task #35 T3：从 settings_dialog.py 拆分）。\n\n'
        "页实现：page_general / page_library / page_view / page_fields /\n"
        "page_api / page_mcp / page_about；字段小对话框：field_dialogs。\n"
        '"""\n'
        "from .dialog import SettingsDialog\n"
        "from .field_dialogs import (\n"
        "    _AddFieldDialog,\n"
        "    _DeleteFieldChoiceDialog,\n"
        "    _FieldTypeChangeConfirmDialog,\n"
        ")\n\n"
        '__all__ = [\n'
        '    "SettingsDialog",\n'
        '    "_AddFieldDialog",\n'
        '    "_DeleteFieldChoiceDialog",\n'
        '    "_FieldTypeChangeConfirmDialog",\n'
        "]\n",
        encoding="utf-8", newline="\n",
    )
    print("写出 settings/__init__.py")


if __name__ == "__main__":
    main()
