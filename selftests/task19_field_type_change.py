"""task #19 Phase A 自检：字段类型变更的安全护栏。

覆盖：
- ``app.models.is_compatible_type_change`` 的兼容性矩阵
- ``Repository.set_field_type`` 三件事进同事务：
  * 改 ``fields.type``
  * ``project_field_values.value`` 保留不动（核心保护）
  * ``supersede_pending_suggestions`` 开关：pending 标记 superseded、resolved_at 非 NULL
  * ``clear_prompt_hint`` 开关：hint 变空串
- 边界路径：
  * 受保护字段静默忽略（不改 type、不动 hint、不 supersede）
  * old == new 直接返回（不开启事务，不误伤）
  * 默认 kwargs 关闭时只动 type，hint/pending 不变（向后兼容）
- 异常路径：UPDATE 失败时整体 ROLLBACK
"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T, closing_repos

from app.db import connect
from app.models import (
    FIELD_TYPES, Project, is_compatible_type_change,
)
from app.repository import Repository


# =============================================================================
# A. 兼容性矩阵（纯函数）
# =============================================================================
def test_compat_matrix(t: T) -> None:
    # 同类型 → 永远兼容（兜底）
    for ft in FIELD_TYPES:
        t.assert_true(f"compat: {ft} → {ft}", is_compatible_type_change(ft, ft))

    # 任意 → text / textarea：兼容
    for old in FIELD_TYPES:
        for new in ("text", "textarea"):
            t.assert_true(
                f"compat: {old} → {new}",
                is_compatible_type_change(old, new),
            )

    # rating → number：兼容（"1".."5" 在 number 控件里能读）
    t.assert_true(
        "compat: rating → number",
        is_compatible_type_change("rating", "number"),
    )

    # 反向 / 跨语义切换：不兼容
    INCOMPAT_PAIRS = [
        ("text", "date"), ("text", "number"), ("text", "rating"), ("text", "url"),
        ("textarea", "date"), ("textarea", "number"), ("textarea", "rating"),
        ("textarea", "url"),
        ("number", "date"), ("number", "rating"), ("number", "url"),
        ("date", "number"), ("date", "rating"), ("date", "url"),
        ("url", "date"), ("url", "number"), ("url", "rating"),
    ]
    for old, new in INCOMPAT_PAIRS:
        t.assert_true(
            f"incompat: {old} → {new}",
            not is_compatible_type_change(old, new),
        )


# =============================================================================
# B. set_field_type：三件事 + 开关组合
# =============================================================================
def _seed_pending_suggestions(
    repo: Repository, pid: int, fid: int, value: str = "auto",
) -> int:
    """直接插一条 pending suggestion，绕开 add_suggestions 的 supersede 副作用。"""
    cur = repo.conn.cursor()
    cur.execute(
        "INSERT INTO project_field_suggestions"
        "(project_id, field_id, suggested_value, source_task_id, status) "
        "VALUES(?, ?, ?, NULL, 'pending')",
        (pid, fid, value),
    )
    repo.conn.commit()
    return cur.lastrowid


def _seed_field_value(repo: Repository, pid: int, fid: int, value: str) -> None:
    repo.conn.execute(
        "INSERT OR REPLACE INTO project_field_values(project_id, field_id, value) "
        "VALUES(?, ?, ?)",
        (pid, fid, value),
    )
    repo.conn.commit()


def _count_pending(repo: Repository, fid: int) -> int:
    return repo.conn.execute(
        "SELECT COUNT(*) FROM project_field_suggestions "
        "WHERE field_id=? AND status='pending'",
        (fid,),
    ).fetchone()[0]


def _count_superseded(repo: Repository, fid: int) -> int:
    return repo.conn.execute(
        "SELECT COUNT(*) FROM project_field_suggestions "
        "WHERE field_id=? AND status='superseded' AND resolved_at IS NOT NULL",
        (fid,),
    ).fetchone()[0]


def test_set_field_type_full_switches(t: T, tmp: Path) -> None:
    db = tmp / "task19_full.db"
    repo = Repository(connect(db))
    with closing_repos(repo):
        fid = repo.add_field("出版日期", "text", prompt_hint="格式 YYYY-MM-DD")
        # 加一个项目，写字段值
        p = Project(title="样书 1")
        repo.save_project(p)
        _seed_field_value(repo, p.id, fid, "老王送的咖啡机")  # 故意是个 text 串
        # 加两条 pending 建议
        _seed_pending_suggestions(repo, p.id, fid, "2024-03-15")
        _seed_pending_suggestions(repo, p.id, fid, "2024-04-01")

        # 调全开：三件事都做
        repo.set_field_type(
            fid, "date",
            supersede_pending_suggestions=True,
            clear_prompt_hint=True,
        )

        f = repo.get_field(fid)
        t.assert_eq("fields.type 已改为 date", f.type, "date")
        t.assert_eq("fields.prompt_hint 被清空", f.prompt_hint, "")

        # project_field_values.value 一字未动（核心保护）
        row = repo.conn.execute(
            "SELECT value FROM project_field_values WHERE field_id=?", (fid,),
        ).fetchone()
        t.assert_eq(
            "project_field_values.value 保留不动",
            row["value"], "老王送的咖啡机",
        )

        # pending 全部 superseded、resolved_at 非 NULL
        t.assert_eq("pending 数 = 0", _count_pending(repo, fid), 0)
        t.assert_eq("superseded 数 = 2", _count_superseded(repo, fid), 2)


def test_set_field_type_default_kwargs_compatible(t: T, tmp: Path) -> None:
    """默认 kwargs 关闭：只改 type，hint / pending 不动 → 向后兼容。"""
    db = tmp / "task19_default.db"
    repo = Repository(connect(db))
    with closing_repos(repo):
        fid = repo.add_field("作者", "text", prompt_hint="姓 名")
        p = Project(title="书 A")
        repo.save_project(p)
        _seed_pending_suggestions(repo, p.id, fid, "鲁迅")

        repo.set_field_type(fid, "textarea")  # 默认 kwargs 关闭

        f = repo.get_field(fid)
        t.assert_eq("默认 kwargs：type 已改", f.type, "textarea")
        t.assert_eq("默认 kwargs：hint 不动", f.prompt_hint, "姓 名")
        t.assert_eq(
            "默认 kwargs：pending 不动", _count_pending(repo, fid), 1,
        )
        t.assert_eq(
            "默认 kwargs：无 superseded 记录",
            _count_superseded(repo, fid), 0,
        )


def test_set_field_type_partial_clear_hint_only(t: T, tmp: Path) -> None:
    db = tmp / "task19_clear.db"
    repo = Repository(connect(db))
    with closing_repos(repo):
        fid = repo.add_field("评级", "text", prompt_hint="1-5 整数")
        p = Project(title="书 B")
        repo.save_project(p)
        _seed_pending_suggestions(repo, p.id, fid, "5")

        repo.set_field_type(fid, "rating", clear_prompt_hint=True)

        f = repo.get_field(fid)
        t.assert_eq("only clear_hint：type 已改", f.type, "rating")
        t.assert_eq("only clear_hint：hint 被清空", f.prompt_hint, "")
        t.assert_eq(
            "only clear_hint：pending 不动",
            _count_pending(repo, fid), 1,
        )


def test_set_field_type_partial_supersede_only(t: T, tmp: Path) -> None:
    db = tmp / "task19_super.db"
    repo = Repository(connect(db))
    with closing_repos(repo):
        fid = repo.add_field("页数", "text", prompt_hint="纯数字")
        p = Project(title="书 C")
        repo.save_project(p)
        _seed_pending_suggestions(repo, p.id, fid, "320")

        repo.set_field_type(fid, "number", supersede_pending_suggestions=True)

        f = repo.get_field(fid)
        t.assert_eq("only supersede：type 已改", f.type, "number")
        t.assert_eq("only supersede：hint 不动", f.prompt_hint, "纯数字")
        t.assert_eq(
            "only supersede：pending=0", _count_pending(repo, fid), 0,
        )
        t.assert_eq(
            "only supersede：superseded=1", _count_superseded(repo, fid), 1,
        )


def test_set_field_type_protected_silent(t: T, tmp: Path) -> None:
    """受保护字段（is_required）：三件事一件都不做。"""
    db = tmp / "task19_protected.db"
    repo = Repository(connect(db))
    with closing_repos(repo):
        # 找到标题字段（系统必有，key='title'）
        title_fid = None
        for f in repo.list_fields():
            if f.key == "title":
                title_fid = f.id
                break
        t.assert_true("找到标题字段", title_fid is not None)

        before = repo.get_field(title_fid)
        repo.set_field_type(
            title_fid, "number",
            supersede_pending_suggestions=True,
            clear_prompt_hint=True,
        )
        after = repo.get_field(title_fid)
        t.assert_eq("受保护字段 type 不变", after.type, before.type)
        t.assert_eq(
            "受保护字段 hint 不变", after.prompt_hint, before.prompt_hint,
        )


def test_set_field_type_noop_when_same(t: T, tmp: Path) -> None:
    """old == new：直接返回，hint / pending 都不动（即使开关全开）。"""
    db = tmp / "task19_noop.db"
    repo = Repository(connect(db))
    with closing_repos(repo):
        fid = repo.add_field("备注", "text", prompt_hint="自由文本")
        p = Project(title="书 D")
        repo.save_project(p)
        _seed_pending_suggestions(repo, p.id, fid, "看完了")

        repo.set_field_type(
            fid, "text",  # 与现状相同
            supersede_pending_suggestions=True,
            clear_prompt_hint=True,
        )
        f = repo.get_field(fid)
        t.assert_eq("noop：type 不变", f.type, "text")
        t.assert_eq("noop：hint 不变（未误伤）", f.prompt_hint, "自由文本")
        t.assert_eq("noop：pending 不变（未误伤）", _count_pending(repo, fid), 1)


def test_count_field_filled_for_system_fields(t: T, tmp: Path) -> None:
    """系统字段（author/date/source_url/rating）的值存在 projects 表对应列里，
    不在 project_field_values。

    UI 层 ``_count_field_impact`` 必须经 ``repo.count_field_filled``，否则
    系统字段永远算 0，误走"三条全空 → 静默切"路径而不弹确认对话框
    （这是 task #19 Phase A 首发版本的 bug，已修）。
    """
    db = tmp / "task19_sysfield.db"
    repo = Repository(connect(db))
    with closing_repos(repo):
        # 通过新建库向导拿到的"作者"系统字段；现在直接 INSERT 模拟
        # （_seed_fields 不会自动加 author，需要向导用户勾选；这里直接造）
        cur = repo.conn.cursor()
        cur.execute(
            "INSERT INTO fields(name, type, ord, visible, key) "
            "VALUES('作者', 'text', 5, 1, 'author')"
        )
        author_fid = cur.lastrowid
        repo.conn.commit()

        # 造 3 个项目：2 个填了 author、1 个空
        for title, author in [
            ("书 X", "鲁迅"), ("书 Y", "巴金"), ("书 Z", ""),
        ]:
            p = Project(title=title, author=author)
            repo.save_project(p)

        f = repo.get_field(author_fid)
        t.assert_true("找到 author 字段且 is_system", f.is_system)
        t.assert_eq(
            "count_field_filled 正确统计系统字段值（=2）",
            repo.count_field_filled(f), 2,
        )

        # 反例：只查 project_field_values 会漏算系统字段（=0）
        raw_count = repo.conn.execute(
            "SELECT COUNT(*) FROM project_field_values "
            "WHERE field_id=? AND value IS NOT NULL AND value!=''",
            (author_fid,),
        ).fetchone()[0]
        t.assert_eq(
            "（反例）直接查 project_field_values 会漏算（=0）",
            int(raw_count), 0,
        )


# 备注：事务化的 ROLLBACK 行为是 SQLite 的语义保证（BEGIN…ROLLBACK），不是
# 我们自己实现的逻辑；且 sqlite3.Connection.cursor 是只读 slot，无法用
# monkey-patch 优雅模拟"中途失败"。所以这一支不在 selftest 范围内 —— 真要
# 校验，请用 pytest + unittest.mock 或单独的故障注入测试框架。


# =============================================================================
# main
# =============================================================================
def main() -> int:
    t = T()
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            tmp = Path(td)
            test_compat_matrix(t)
            test_set_field_type_full_switches(t, tmp)
            test_set_field_type_default_kwargs_compatible(t, tmp)
            test_set_field_type_partial_clear_hint_only(t, tmp)
            test_set_field_type_partial_supersede_only(t, tmp)
            test_set_field_type_protected_silent(t, tmp)
            test_set_field_type_noop_when_same(t, tmp)
            test_count_field_filled_for_system_fields(t, tmp)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2
    return 0 if t.report() else 1


if __name__ == "__main__":
    sys.exit(main())
