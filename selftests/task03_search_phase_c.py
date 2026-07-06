"""task #03 Phase C 自检：搜索历史与收藏表达式 settings JSON。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T

from app.search_history import (
    add_history,
    load_history,
    load_saved_searches,
    remove_history,
    remove_saved_search,
    upsert_saved_search,
)


def test_history(t: T) -> None:
    raw = "not json"
    t.assert_eq("bad history JSON becomes empty list", load_history(raw), [])

    raw = "[]"
    for i in range(25):
        raw = add_history(raw, f"q{i}")
    items = load_history(raw)
    t.assert_eq("history keeps newest first", items[:3], ["q24", "q23", "q22"])
    t.assert_eq("history caps at 20", len(items), 20)

    raw = add_history(raw, "q20")
    items = load_history(raw)
    t.assert_eq("duplicate query moves to top", items[0], "q20")
    t.assert_eq("duplicate query remains unique", items.count("q20"), 1)

    raw = remove_history(raw, "q20")
    t.assert_true("history item removed", "q20" not in load_history(raw))


def test_saved_searches(t: T) -> None:
    t.assert_eq("bad saved JSON becomes empty list", load_saved_searches("{"), [])

    raw, overwritten = upsert_saved_search("[]", "高分科幻", "tag:科幻 AND rating:>=4")
    t.assert_true("new saved search is not overwrite", not overwritten)
    items = load_saved_searches(raw)
    t.assert_eq("saved search name stored", items[0]["name"], "高分科幻")
    t.assert_eq("saved search query stored", items[0]["query"], "tag:科幻 AND rating:>=4")

    raw, overwritten = upsert_saved_search(raw, "高分科幻", "tag:科幻 AND rating:>=5")
    t.assert_true("same name overwrites", overwritten)
    items = load_saved_searches(raw)
    t.assert_eq("overwrite keeps one item", len(items), 1)
    t.assert_eq("overwrite updates query", items[0]["query"], "tag:科幻 AND rating:>=5")

    raw, _ = upsert_saved_search(raw, "近期", "date:>=2024-01-01")
    names = [item["name"] for item in load_saved_searches(raw)]
    t.assert_eq("new saved search is inserted first", names, ["近期", "高分科幻"])

    raw = remove_saved_search(raw, "高分科幻")
    names = [item["name"] for item in load_saved_searches(raw)]
    t.assert_eq("saved search removed by name", names, ["近期"])

    parsed = json.loads(raw)
    t.assert_true("saved JSON remains a list", isinstance(parsed, list))


if __name__ == "__main__":
    print("=" * 60)
    print("task03_search_phase_c selftest")
    print("=" * 60)
    t = T()
    test_history(t)
    test_saved_searches(t)
    ok = t.report()
    sys.exit(0 if ok else 1)

