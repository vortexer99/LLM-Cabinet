"""项目右键菜单的同字段值筛选；只读取已加载的项目元数据。"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMenu

from ..models import Field, Project
from ..search import SearchNode, TermNode, combine_and, parse_search
from ..search_filters import conjuncts

if TYPE_CHECKING:
    from ..repository import Repository
    from .main_window import MainWindow


def _matches_field(resolved: dict, field: Field) -> bool:
    """校验搜索标识实际落在目标字段，防止保留字、重名命中其他字段。"""
    if field.key in {"title", "description"}:
        column = "title" if field.key == "title" else "description_md"
        return resolved.get("kind") == "project" and resolved.get("column") == column
    if field.is_tags:
        return resolved.get("kind") == "tag"
    return (field.id is not None and resolved.get("kind") == "field"
            and resolved.get("field_id") == field.id)


def resolve_search_field(repo: Repository, field: Field) -> str | None:
    """优先稳定 key，再尝试显示名称；无法无歧义寻址时禁止生成查询。"""
    for candidate in (field.key, field.name):
        if candidate and _matches_field(repo._resolve_search_field(candidate), field):
            return candidate
    return None


def replace_field_filter(
    repo: Repository, node: SearchNode | None, field: Field, value: str,
) -> SearchNode:
    """替换同字段的顶层条件，不从 OR 或 NOT 内部抽取条件。"""
    identifier = resolve_search_field(repo, field)
    if identifier is None:
        raise ValueError("该字段与搜索保留字或其他字段重名，无法准确筛选。")
    remaining = [
        item for item in conjuncts(node)
        if not (isinstance(item, TermNode) and item.field is not None
                and _matches_field(repo._resolve_search_field(item.field), field))
    ]
    return combine_and(*remaining, TermNode(identifier, "=", value))


@dataclass(frozen=True)
class _FilterTarget:
    """菜单弹出时固定来源和值，不随当前选中项目变化。"""

    project_id: int | None
    field: Field
    value: str


def _apply_filter(window: MainWindow, target: _FilterTarget) -> None:
    if target.field.is_tags:
        window._on_tag_filter_changed("tag", target.value)
        return
    parsed = parse_search(window.search_box.text().strip())
    if not parsed.ok:
        window.statusBar().showMessage("请先修正搜索表达式，再筛选同字段值；也可点击“全库”清除。", 5000)
        return
    try:
        node = replace_field_filter(window.repo, parsed.ast, target.field, target.value)
    except ValueError as exc:
        window.statusBar().showMessage(str(exc), 5000)
        return
    window._set_filter_query(node)


def _menu_label(text: str, limit: int = 72) -> str:
    """菜单单行截短并转义助记符；完整值另放工具提示。"""
    compact = " ".join(text.split())
    if len(compact) > limit:
        compact = compact[:limit - 1] + "…"
    return compact.replace("&", "&&")


def _raw_values(project: Project, field: Field) -> list[str]:
    if field.is_tags:
        return list(dict.fromkeys(tag for tag in project.tags if tag.strip()))
    if field.key == "title":
        value = project.title
    elif field.key == "description":
        value = project.description_md
    else:
        value = project.field_values.get(field.id, "")
    return [str(value)] if value is not None and str(value).strip() else []


def _add_field(menu: QMenu, window: MainWindow, project: Project, field: Field) -> bool:
    values = _raw_values(project, field)
    if not values:
        return False
    destination = menu
    if field.is_tags:
        destination = menu.addMenu(_menu_label(f"筛选同{field.name}"))
        destination.setToolTipsVisible(True)
    identifier = resolve_search_field(window.repo, field)
    for value in values:
        label = value if field.is_tags else f"筛选同{field.name}：{value}"
        action = destination.addAction(_menu_label(label))
        tooltip = f"{field.name}：{value}"
        if identifier is None:
            action.setEnabled(False)
            tooltip += "\n该字段与搜索保留字或其他字段重名，无法准确筛选。请先重命名字段。"
        else:
            target = _FilterTarget(project.id, replace(field), value)
            action.triggered.connect(lambda _checked=False, target=target: _apply_filter(window, target))
        action.setToolTip(tooltip)
    return True


def populate_project_field_filter_menu(
    menu: QMenu, window: MainWindow, project: Project,
    field: Field | None = None, *, all_fields: bool = False,
) -> bool:
    """表格提供当前单元格字段动作，网格列出包含隐藏字段的完整元数据。"""
    menu.setToolTipsVisible(True)
    if not all_fields:
        return _add_field(menu, window, project, field) if field is not None else False
    fields = [item for item in window.repo.list_fields() if _raw_values(project, item)]
    if not fields:
        return False
    submenu = menu.addMenu("筛选同字段值")
    submenu.setToolTipsVisible(True)
    for item in fields:
        _add_field(submenu, window, project, item)
    return True
