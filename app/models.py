"""轻量数据类。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# 字段类型常量
FIELD_TYPES = ("text", "textarea", "date", "url", "rating", "number")
FIELD_TYPE_LABELS = {
    "text": "单行文本",
    "textarea": "多行文本",
    "date": "日期",
    "url": "URL",
    "rating": "评分（1~5 星）",
    "number": "数字",
    "tags": "标签（多值）",  # 仅作为虚拟字段类型，不在普通"添加"对话框里出现
}

# 不可删除、不可修改类型的字段 key
PROTECTED_FIELD_KEYS = ("title", "description", "tags")


# 类型变更兼容性矩阵（task #19 Phase A）
#
# "兼容" = 新类型的控件能正常读出旧值的字符串表示，不会出现"读不动 → 显示空 →
# 用户保存时把原值无声覆盖"的数据丢失。
# 不兼容 ≠ 不允许，只是 UI 层会弹确认对话框告知后果。
#
# 设计原则：
# - 所有字段值底层都是 TEXT，切类型本身不动 project_field_values.value
# - text / textarea 控件能显示任何字符串 → 任意类型切到它俩都兼容
# - 反向（任意 → date/url/rating/number）控件大概率读不出来 → 不兼容
# - rating → number：rating 存的是 "1".."5" 这种整数字符串，number 控件能读
# - number → rating：number 可能 >5 或负数或浮点，rating 控件显示 0 星 → 不兼容
# - 同类型互换（理论上不该发生，但兜底）= 兼容
def is_compatible_type_change(old: str, new: str) -> bool:
    """判断字段类型变更是否"完全兼容"（旧值能被新类型控件正常读出）。

    返回 True 表示 UI 可以静默切换，无需弹确认；False 表示需要弹确认告知用户。
    """
    if old == new:
        return True
    # 目标类型是 text / textarea：任何字符串都能显示
    if new in ("text", "textarea"):
        return True
    # rating → number：rating 的存储格式 "1".."5" 在 number 控件里能正确解析
    if old == "rating" and new == "number":
        return True
    return False


@dataclass
class Field:
    id: Optional[int] = None
    name: str = ""
    type: str = "text"
    ord: int = 0
    visible: bool = True
    key: Optional[str] = None
    suggest_enabled: bool = True
    prompt_hint: str = ""           # task #11 T1：LLM 建议该字段时附加的格式说明

    @property
    def is_system(self) -> bool:
        """字段是否带 ``key``（"种入时的稳定标识"）。

        task #20 schema v4 起：``key`` 非空仅表示"该字段在新建库向导有种子记录"
        / "导入器宽松匹配可识别"，**不再决定值的存储位置**。所有非保护字段值
        都存 ``project_field_values``。

        实际语义判断推荐：
        - 判定"受保护"（不可删/不可改类型/类型固定）：用 ``is_required``
        - 判定"在导入导出 README 已展示"等具体行为：直接判 ``key in {...}`` 集合
        - 仅判定"是否种入时带 key"（极少用）：仍可用 ``is_system``
        """
        return self.key is not None

    @property
    def is_title(self) -> bool:
        return self.key == "title"

    @property
    def is_required(self) -> bool:
        """受保护字段：不可删除、不可改类型。"""
        return self.key in PROTECTED_FIELD_KEYS

    @property
    def is_tags(self) -> bool:
        return self.key == "tags"


@dataclass
class Project:
    id: Optional[int] = None
    title: str = ""
    description_md: str = ""
    storage_mode: str = "link"
    cover_file_id: Optional[int] = None
    created_at: str = ""
    updated_at: str = ""

    tags: list[str] = field(default_factory=list)
    # 用户自定义字段值 + 老系统字段值（task #20 schema v4 起统一存这里）：
    # field_id -> value
    field_values: dict[int, str] = field(default_factory=dict)


@dataclass
class FileItem:
    id: Optional[int] = None
    project_id: int = 0
    path: str = ""
    is_relative: bool = False
    label: str = ""
    kind: str = "other"
    ord: int = 0
    added_at: str = ""
    missing: bool = False           # task #14 T1：库一致性检查标记


# ============================================================ LLM
@dataclass
class LLMTask:
    id: Optional[int] = None
    project_id: Optional[int] = None
    project_title: str = ""
    type: str = "meta_suggest"
    status: str = "queued"   # queued/running/done/failed/cancelled
    payload_json: str = ""   # 入参 JSON
    result_json: str = ""    # 返回结果 JSON
    error: str = ""
    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""


@dataclass
class FieldSuggestion:
    id: Optional[int] = None
    project_id: int = 0
    field_id: int = 0
    suggested_value: str = ""
    source_task_id: Optional[int] = None
    status: str = "pending"  # pending/applied/rejected/superseded
    created_at: str = ""
    resolved_at: str = ""
