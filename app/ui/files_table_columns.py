"""项目右下角文件表的列定义、可见性 / 列宽偏好的持久化辅助。

列是**固定**的（不像项目列表那样跟 fields schema 联动），
但用户可以：
- 拖拽列宽（除文件名列外，文件名 Stretch）
- 在表头右键菜单切换列的可见性（文件名不可隐藏）

偏好按项目独立存储到 project_settings 表，键 = files_table_columns。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

# project_settings 中的键
SETTING_KEY = "files_table_columns"


@dataclass
class FilesColumn:
    key: str             # 稳定唯一标识；持久化用这个
    label: str           # 表头显示文本
    default_width: int   # 默认列宽（像素）
    mandatory: bool = False  # 必显（不可隐藏）

    @property
    def hideable(self) -> bool:
        return not self.mandatory


# 列定义（顺序即为表中的左→右物理顺序）
# task #31b: 新增 size / added_at 列
COLUMNS: list[FilesColumn] = [
    FilesColumn(key="name",     label="文件名",   default_width=320, mandatory=True),
    FilesColumn(key="label",    label="说明",     default_width=240),
    FilesColumn(key="kind",     label="类型",     default_width=80),
    FilesColumn(key="size",     label="大小",     default_width=80),
    FilesColumn(key="added_at", label="添加时间", default_width=140),
    FilesColumn(key="storage",  label="存储",     default_width=80),
]

# {key: index} 反查
INDEX_BY_KEY: dict[str, int] = {c.key: i for i, c in enumerate(COLUMNS)}


def column_by_key(key: str) -> FilesColumn | None:
    i = INDEX_BY_KEY.get(key)
    return COLUMNS[i] if i is not None else None


# ---- 用户偏好结构 -----------------------------------------------------------
# 在 project_settings 里以 JSON 字符串存储：
# {"prefs": {"<key>": {"visible": bool, "width": int}}}


def load_prefs(raw: str) -> dict[str, dict]:
    """解析 project_settings 里的原始字符串为 {key: {visible, width}}。
    缺失/损坏时返回空 dict。"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    prefs = data.get("prefs") if "prefs" in data else data
    if not isinstance(prefs, dict):
        return {}
    out: dict[str, dict] = {}
    for k, v in prefs.items():
        if not isinstance(v, dict):
            continue
        out[str(k)] = {
            "visible": bool(v.get("visible", True)),
            "width": int(v.get("width", 0)) or 0,
        }
    return out


def dump_prefs(prefs: dict[str, dict]) -> str:
    """把 {key: {visible, width}} 序列化为可存的 JSON 字符串。"""
    return json.dumps({"prefs": prefs}, ensure_ascii=False)


def resolve_pref(prefs: dict[str, dict], col: FilesColumn) -> tuple[bool, int]:
    """根据已存偏好 + 列定义，返回 (是否可见, 实际列宽)。"""
    p = prefs.get(col.key, {})
    visible = bool(p.get("visible", True))
    if col.mandatory:
        visible = True   # 强制可见
    width = int(p.get("width", 0)) or col.default_width
    return visible, width
