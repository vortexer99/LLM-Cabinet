"""侧栏条件与搜索表达式转换；表达式是唯一查询来源。"""
from __future__ import annotations

import re

from .search import AndNode, NotNode, OrNode, SearchNode, TermNode, combine_and


TAG_FIELDS = {"tag", "tags", "tag_prefix", "__tag_prefix", "favorite", "hidden", "untagged", "__untagged", "review", "mcp"}
FILE_VALUES = {"video": "video", "视频": "video", "archive": "archive", "压缩包": "archive",
               "external": "external", "外部链接": "external"}


def quote_value(value: str) -> str:
    """按搜索解析器规则转义值，不把标签里的关键字、引号当作语法。"""
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def format_search(node: SearchNode | None) -> str:
    """完整保留布尔语义，序列化后可复制、编辑、收藏和重新解析。"""
    if node is None:
        return ""
    if isinstance(node, TermNode):
        if node.field is None:
            return quote_value(node.value)
        operator = node.op if node.op in {":", "="} else ":" + node.op
        field = node.field
        if not re.fullmatch(r"[\w-]+", field) or field.upper() in {"AND", "OR", "NOT"}:
            field = quote_value(field)
        return field + operator + quote_value(node.value)
    if isinstance(node, NotNode):
        return "NOT (" + format_search(node.item) + ")"
    operator = " AND " if isinstance(node, AndNode) else " OR "
    return "(" + operator.join(format_search(item) for item in node.items) + ")"


def conjuncts(node: SearchNode | None) -> list[SearchNode]:
    """只拆顶层合取，不能从混合 OR 或 NOT 中抽走条件。"""
    if node is None:
        return []
    if isinstance(node, AndNode):
        return [part for item in node.items for part in conjuncts(item)]
    return [node]


def is_filter_group(node: SearchNode, group: str) -> bool:
    if isinstance(node, TermNode):
        field = (node.field or "").casefold()
        return field in TAG_FIELDS if group == "tag" else field == "has"
    if isinstance(node, NotNode):
        # 只替换能映射到三态标签的直接否定；复杂 NOT 保持原布尔语义。
        return group == "tag" and _tag_term_selection(node.item) is not None
    if isinstance(node, (AndNode, OrNode)):
        return all(is_filter_group(item, group) for item in node.items)
    return False


def replace_filter(node: SearchNode | None, group: str, replacement: SearchNode | None) -> SearchNode | None:
    """替换顶层同组条件，混合逻辑保留原义；相同条件不重复加入。"""
    remaining = [item for item in conjuncts(node) if not is_filter_group(item, group)]
    if replacement is not None and replacement not in remaining:
        remaining.append(replacement)
    return combine_and(*remaining)


def sidebar_node(kind: str, value: str) -> SearchNode | None:
    if kind == "not_tag":
        return NotNode(TermNode("tag", "=", value))
    if kind == "not_tag_prefix":
        return NotNode(TermNode("tag_prefix", ":", value))
    if kind == "tag":
        return TermNode("tag", "=", value)
    if kind == "tag_prefix":
        return TermNode("tag_prefix", ":", value)
    if kind in {"favorite", "hidden", "untagged", "review", "mcp"}:
        return TermNode(kind, ":", "true")
    return None


def _tag_term_selection(node: SearchNode) -> tuple[str, str] | None:
    """识别可以完整投影到标签或标签前缀节点的单项条件。"""
    if not isinstance(node, TermNode):
        return None
    field = (node.field or "").casefold()
    if field in {"tag", "tags"} and node.op == "=":
        return "tag", node.value
    if field in {"tag_prefix", "__tag_prefix"} and node.op in {":", "="}:
        return "tag_prefix", node.value
    return None


def _is_combinable_tag_group(node: SearchNode) -> bool:
    """只抽取独立的标签条件；收藏、隐藏范围、混合 OR 和复杂 NOT 留在外层。"""
    if isinstance(node, TermNode):
        return (node.field or "").casefold() in {"tag", "tags", "tag_prefix", "__tag_prefix"}
    if isinstance(node, NotNode):
        return _tag_term_selection(node.item) is not None
    if isinstance(node, (AndNode, OrNode)):
        return bool(node.items) and all(_is_combinable_tag_group(item) for item in node.items)
    return False


def combine_tag_filter(
    node: SearchNode | None, kind: str, value: str, mode: str,
) -> SearchNode | None:
    """整体组合独立标签子表达式，其余条件始终保留为外层 AND。"""
    if kind not in {"tag", "tag_prefix"}:
        return node
    if mode.casefold() not in {"all", "any", "and", "or"}:
        raise ValueError(f"未知标签组合方式：{mode}")
    replacement = sidebar_node(kind, value)
    parts = conjuncts(node)
    tags = [item for item in parts if _is_combinable_tag_group(item)]

    def contains_positive(item: SearchNode) -> bool:
        if isinstance(item, TermNode):
            return _tag_term_selection(item) == (kind, value)
        return isinstance(item, (AndNode, OrNode)) and any(contains_positive(child) for child in item.items)

    # 对已参与组合的同一正向项重复激活保持幂等，不改变现有 AND/OR 的括号。
    if any(contains_positive(item) for item in tags):
        return node
    remaining = [item for item in parts if not _is_combinable_tag_group(item)]
    previous = combine_and(*tags)
    if previous is None:
        combined = replacement
    elif mode.casefold() in {"all", "and"}:
        combined = combine_and(previous, replacement)
    else:
        combined = OrNode([previous, replacement])
    return combine_and(*remaining, combined)


def tag_states(node: SearchNode | None) -> dict[tuple[str, str], str]:
    """投影独立标签表达式中的正负项；同一项同时正负出现时不伪造状态。"""
    states: dict[tuple[str, str], set[str]] = {}

    def collect(item: SearchNode) -> None:
        selection = _tag_term_selection(item)
        state = "include"
        if isinstance(item, NotNode):
            selection = _tag_term_selection(item.item)
            state = "exclude"
        if selection is not None:
            states.setdefault(selection, set()).add(state)
        elif isinstance(item, (AndNode, OrNode)):
            for child in item.items:
                collect(child)

    for part in conjuncts(node):
        if _is_combinable_tag_group(part):
            collect(part)
    return {selection: next(iter(values)) for selection, values in states.items() if len(values) == 1}


def tag_selection(node: SearchNode | None) -> tuple[str, str]:
    """仅单个可精确表示的条件选中树节点，其余保持表达式而不伪造选择。"""
    parts = [item for item in conjuncts(node) if is_filter_group(item, "tag")]
    if not parts:
        def contains_tag(item: SearchNode | None) -> bool:
            if isinstance(item, TermNode):
                return (item.field or "").casefold() in TAG_FIELDS
            if isinstance(item, NotNode):
                return contains_tag(item.item)
            return isinstance(item, (AndNode, OrNode)) and any(contains_tag(child) for child in item.items)
        if contains_tag(node):
            return "query", ""
        return "all", ""
    if len(parts) == 1 and isinstance(parts[0], NotNode):
        selection = _tag_term_selection(parts[0].item)
        if selection is not None:
            return "not_" + selection[0], selection[1]
    if len(parts) == 1 and isinstance(parts[0], TermNode):
        term = parts[0]
        field = (term.field or "").casefold()
        selection = _tag_term_selection(term)
        if selection is not None:
            return selection
        if field == "__untagged":
            return "untagged", ""
        if field in {"favorite", "hidden", "untagged", "review", "mcp"} and term.op in {":", "="} and term.value.casefold() in {"true", "1"}:
            return field, ""
    return "query", ""


def file_selection(node: SearchNode | None) -> tuple[set[str], str]:
    def kinds(part: SearchNode) -> set[str] | None:
        if not isinstance(part, TermNode) or (part.field or "").casefold() != "has" or part.op not in {":", "="}:
            return None
        value = FILE_VALUES.get(part.value.casefold())
        return {value} if value else None

    parts = [item for item in conjuncts(node) if is_filter_group(item, "file")]
    if len(parts) == 1 and isinstance(parts[0], OrNode):
        items, mode = parts[0].items, "any"
    else:
        items, mode = parts, "all"
    selected: set[str] = set()
    for item in items:
        values = kinds(item)
        if values is None:
            return set(), "any"
        selected.update(values)
    return selected, mode if len(selected) > 1 else "any"
