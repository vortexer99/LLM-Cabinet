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
    author: str = ""
    date: str = ""
    source_url: str = ""
    rating: int = 0
    description_md: str = ""
    storage_mode: str = "link"
    cover_file_id: Optional[int] = None
    created_at: str = ""
    updated_at: str = ""

    tags: list[str] = field(default_factory=list)
    # 用户自定义字段值：field_id -> value
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
