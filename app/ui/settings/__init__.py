"""设置对话框包（task #35 T3：从 settings_dialog.py 拆分）。

页实现：page_general / page_library / page_view / page_fields /
page_api / page_mcp / page_about；字段小对话框：field_dialogs。
"""
from .dialog import SettingsDialog
from .field_dialogs import (
    _AddFieldDialog,
    _DeleteFieldChoiceDialog,
    _FieldTypeChangeConfirmDialog,
)

__all__ = [
    "SettingsDialog",
    "_AddFieldDialog",
    "_DeleteFieldChoiceDialog",
    "_FieldTypeChangeConfirmDialog",
]
