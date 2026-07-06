"""搜索历史与收藏表达式的 settings JSON 辅助函数。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any


HISTORY_SETTING_KEY = "search_history"
SAVED_SEARCHES_SETTING_KEY = "saved_searches"
MAX_HISTORY = 20


def load_history(raw: str) -> list[str]:
    """从 settings 字符串读取搜索历史，容错坏 JSON。"""
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        text = str(item).strip() if item is not None else ""
        if text and text not in out:
            out.append(text)
    return out[:MAX_HISTORY]


def dump_history(items: list[str]) -> str:
    clean: list[str] = []
    for item in items:
        text = (item or "").strip()
        if text and text not in clean:
            clean.append(text)
    return json.dumps(clean[:MAX_HISTORY], ensure_ascii=False)


def add_history(raw: str, query: str) -> str:
    """把成功执行的搜索置顶保存。"""
    query = (query or "").strip()
    if not query:
        return dump_history(load_history(raw))
    items = [q for q in load_history(raw) if q != query]
    return dump_history([query, *items])


def remove_history(raw: str, query: str) -> str:
    query = (query or "").strip()
    return dump_history([q for q in load_history(raw) if q != query])


def load_saved_searches(raw: str) -> list[dict[str, str]]:
    """读取收藏表达式列表，输出统一字段。"""
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        query = str(item.get("query") or "").strip()
        if not name or not query or name in seen:
            continue
        seen.add(name)
        out.append(
            {
                "name": name,
                "query": query,
                "created_at": str(item.get("created_at") or ""),
                "updated_at": str(item.get("updated_at") or ""),
            }
        )
    return out


def dump_saved_searches(items: list[dict[str, Any]]) -> str:
    clean: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        name = str(item.get("name") or "").strip()
        query = str(item.get("query") or "").strip()
        if not name or not query or name in seen:
            continue
        seen.add(name)
        clean.append(
            {
                "name": name,
                "query": query,
                "created_at": str(item.get("created_at") or ""),
                "updated_at": str(item.get("updated_at") or ""),
            }
        )
    return json.dumps(clean, ensure_ascii=False)


def upsert_saved_search(raw: str, name: str, query: str) -> tuple[str, bool]:
    """新增或覆盖收藏表达式，返回 ``(新 JSON, 是否覆盖)``。"""
    name = (name or "").strip()
    query = (query or "").strip()
    if not name:
        raise ValueError("收藏名称不能为空")
    if not query:
        raise ValueError("搜索表达式不能为空")

    now = datetime.now().isoformat(timespec="seconds")
    items = load_saved_searches(raw)
    for item in items:
        if item["name"] == name:
            item["query"] = query
            item["updated_at"] = now
            if not item.get("created_at"):
                item["created_at"] = now
            return dump_saved_searches(items), True
    items.insert(0, {"name": name, "query": query, "created_at": now, "updated_at": now})
    return dump_saved_searches(items), False


def remove_saved_search(raw: str, name: str) -> str:
    name = (name or "").strip()
    return dump_saved_searches(
        [item for item in load_saved_searches(raw) if item["name"] != name]
    )

