"""Calibre 风格项目搜索表达式解析器（task #03 Phase B）。

输入示例：
- ``三体`` -> 普通关键词（由 Repository 搜项目元数据和文件清单）
- ``author:刘慈欣 AND rating:>=4``
- ``tag:科幻 AND (tag:翻译 OR NOT date:<2024-01-01)``
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SearchOp = Literal[":", "=", ">", ">=", "<", "<="]


@dataclass(frozen=True)
class TermNode:
    field: str | None
    op: SearchOp
    value: str


@dataclass(frozen=True)
class AndNode:
    items: list["SearchNode"]


@dataclass(frozen=True)
class OrNode:
    items: list["SearchNode"]


@dataclass(frozen=True)
class NotNode:
    item: "SearchNode"


SearchNode = TermNode | AndNode | OrNode | NotNode


@dataclass(frozen=True)
class SearchSyntaxErrorInfo:
    message: str
    position: int


@dataclass(frozen=True)
class SearchParseResult:
    ok: bool
    ast: SearchNode | None = None
    error: SearchSyntaxErrorInfo | None = None


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str
    pos: int


class _ParseError(Exception):
    def __init__(self, message: str, position: int):
        super().__init__(message)
        self.message = message
        self.position = position


def parse_search(query: str) -> SearchParseResult:
    """解析搜索表达式，语法错误以结构化结果返回。"""
    try:
        parser = _Parser(_tokenize(query or ""))
        ast = parser.parse()
    except _ParseError as e:
        return SearchParseResult(
            ok=False,
            error=SearchSyntaxErrorInfo(e.message, e.position),
        )
    return SearchParseResult(ok=True, ast=ast)


def combine_and(*nodes: SearchNode | None) -> SearchNode | None:
    """把多个 AST 以 AND 合并，自动压平空节点。"""
    items: list[SearchNode] = []
    for node in nodes:
        if node is None:
            continue
        if isinstance(node, AndNode):
            items.extend(node.items)
        else:
            items.append(node)
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    return AndNode(items)


def field_term(field: str, value: str, op: SearchOp = ":") -> SearchNode | None:
    value = (value or "").strip()
    if not value:
        return None
    return TermNode(field=field, op=op, value=value)


def _tokenize(query: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    n = len(query)
    while i < n:
        ch = query[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "()":
            tokens.append(_Token(ch, ch, i))
            i += 1
            continue
        if ch in ":=<>":
            if ch in "<>" and i + 1 < n and query[i + 1] == "=":
                tokens.append(_Token("OP", ch + "=", i))
                i += 2
            else:
                tokens.append(_Token("OP", ch, i))
                i += 1
            continue
        if ch in ("'", '"'):
            tok, i = _read_quoted(query, i)
            tokens.append(tok)
            continue
        start = i
        while i < n and (not query[i].isspace()) and query[i] not in "()':=<>\"":
            i += 1
        if start == i:
            raise _ParseError(f"无法识别字符：{query[i]}", i)
        text = query[start:i]
        upper = text.upper()
        if upper in ("AND", "OR", "NOT"):
            tokens.append(_Token(upper, text, start))
        else:
            tokens.append(_Token("WORD", text, start))
    tokens.append(_Token("EOF", "", n))
    return tokens


def _read_quoted(query: str, start: int) -> tuple[_Token, int]:
    quote = query[start]
    chars: list[str] = []
    i = start + 1
    while i < len(query):
        ch = query[i]
        if ch == "\\" and i + 1 < len(query):
            chars.append(query[i + 1])
            i += 2
            continue
        if ch == quote:
            return _Token("WORD", "".join(chars), start), i + 1
        chars.append(ch)
        i += 1
    raise _ParseError("引号没有闭合", start)


class _Parser:
    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.i = 0

    def parse(self) -> SearchNode | None:
        if self._peek().kind == "EOF":
            return None
        node = self._parse_or()
        if self._peek().kind != "EOF":
            t = self._peek()
            raise _ParseError(f"意外的标记：{t.text}", t.pos)
        return node

    def _parse_or(self) -> SearchNode:
        items = [self._parse_and()]
        while self._match("OR"):
            items.append(self._parse_and())
        return items[0] if len(items) == 1 else OrNode(items)

    def _parse_and(self) -> SearchNode:
        items = [self._parse_not()]
        while self._match("AND"):
            items.append(self._parse_not())
        return items[0] if len(items) == 1 else AndNode(items)

    def _parse_not(self) -> SearchNode:
        if self._match("NOT"):
            return NotNode(self._parse_not())
        return self._parse_primary()

    def _parse_primary(self) -> SearchNode:
        if self._match("("):
            node = self._parse_or()
            if not self._match(")"):
                t = self._peek()
                raise _ParseError("缺少右括号", t.pos)
            return node
        return self._parse_term()

    def _parse_term(self) -> SearchNode:
        tok = self._peek()
        if tok.kind != "WORD":
            raise _ParseError("这里需要关键词或字段表达式", tok.pos)
        self.i += 1
        if self._peek().kind == "OP" and self._peek().text in (":", "="):
            base_op = self._peek().text
            self.i += 1
            if self._peek().kind == "(":
                raise _ParseError("字段值不能直接使用括号，请写成 tag:科幻", self._peek().pos)
            op = base_op
            if base_op == ":" and self._peek().kind == "OP" and self._peek().text in (">", ">=", "<", "<="):
                op = self._peek().text
                self.i += 1
            value = self._read_value()
            return TermNode(field=tok.text, op=op, value=value)
        return TermNode(field=None, op=":", value=tok.text)

    def _read_value(self) -> str:
        tok = self._peek()
        if tok.kind != "WORD":
            raise _ParseError("字段值不能为空", tok.pos)
        self.i += 1
        return tok.text

    def _peek(self) -> _Token:
        return self.tokens[self.i]

    def _match(self, kind: str) -> bool:
        if self._peek().kind == kind:
            self.i += 1
            return True
        return False
