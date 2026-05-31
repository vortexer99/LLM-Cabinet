"""task #06 自检：标签层级折叠的数据层语义。

验证 ``Repository.list_projects(tag=..., tag_prefix=...)`` 能正确支持：
- 精确匹配（沿用旧行为）
- 前缀匹配：含同名父标签 + ``<prefix>/<rest>`` 子标签
- 空白参数：返回全部项目
- ``tag`` 优先于 ``tag_prefix``（如同时传，按精确）

不接 GUI，只验证 Repository 行为。
"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T

from app.db import connect
from app.models import Project
from app.repository import Repository


def setup_lib(tmp: Path) -> Repository:
    """造一个含层级标签的小库。

    项目布局：
      P1: tags = ['领域/科幻', '翻译']
      P2: tags = ['领域/工具书']
      P3: tags = ['领域']                    ← 父标签自身作为标签
      P4: tags = ['翻译']                    ← 与 P1 共享一个标签
      P5: tags = ['专辑/2024', '专辑/2024/夏']  ← 多层；本期只做单层折叠，会被并入"专辑"前缀
      P6: tags = []                          ← 无标签
    """
    repo = Repository(connect(tmp / "lib.db"))

    def add(title: str, tags: list[str]) -> int:
        p = Project(title=title)
        pid = repo.save_project(p)
        p.id = pid
        p.tags = tags
        repo.save_project(p)
        return pid

    add("P1", ["领域/科幻", "翻译"])
    add("P2", ["领域/工具书"])
    add("P3", ["领域"])
    add("P4", ["翻译"])
    add("P5", ["专辑/2024", "专辑/2024/夏"])
    add("P6", [])
    return repo


def main() -> int:
    t = T()
    repo = None
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpstr:
            tmp = Path(tmpstr)
            repo = setup_lib(tmp)
            _run_all(repo, t)
            try:
                repo.conn.close()
            except Exception:
                pass
    finally:
        ok = t.report()
    return 0 if ok else 1


def _run_all(repo: Repository, t: T) -> None:
    # ---- 全部项目 ----
    all_p = repo.list_projects()
    t.assert_eq("list_projects(): 总数", len(all_p), 6)

    # ---- 精确匹配（旧行为，回归测试）----
    by_tag = repo.list_projects(tag="翻译")
    titles = sorted(p.title for p in by_tag)
    t.assert_eq("list_projects(tag='翻译'): 命中 P1+P4",
                titles, ["P1", "P4"])

    by_tag = repo.list_projects(tag="领域/科幻")
    t.assert_eq("list_projects(tag='领域/科幻'): 仅 P1",
                [p.title for p in by_tag], ["P1"])

    by_tag = repo.list_projects(tag="领域")
    t.assert_eq("list_projects(tag='领域'): 仅 P3（精确，不含子标签）",
                [p.title for p in by_tag], ["P3"])

    by_tag = repo.list_projects(tag="不存在的标签")
    t.assert_eq("list_projects(tag=不存在): 空", by_tag, [])

    # ---- 前缀匹配（新功能）----
    by_pref = repo.list_projects(tag_prefix="领域")
    titles = sorted(p.title for p in by_pref)
    t.assert_eq("tag_prefix='领域': P1(子) + P2(子) + P3(自身)",
                titles, ["P1", "P2", "P3"])

    by_pref = repo.list_projects(tag_prefix="专辑")
    titles = sorted(p.title for p in by_pref)
    t.assert_eq("tag_prefix='专辑': P5（多层标签也命中）",
                titles, ["P5"])

    by_pref = repo.list_projects(tag_prefix="翻译")
    titles = sorted(p.title for p in by_pref)
    # 没有 "翻译/..." 的子标签，但 "翻译" 自身有；前缀匹配应等价于精确
    t.assert_eq("tag_prefix='翻译': 仅自身标签存在 → P1+P4",
                titles, ["P1", "P4"])

    by_pref = repo.list_projects(tag_prefix="不存在")
    t.assert_eq("tag_prefix=不存在: 空", by_pref, [])

    # ---- tag 与 tag_prefix 同传：tag 优先 ----
    res = repo.list_projects(tag="领域", tag_prefix="领域")
    t.assert_eq("tag='领域' 与 tag_prefix='领域' 同传：tag 优先（仅 P3）",
                [p.title for p in res], ["P3"])

    # ---- 边缘：前缀本身不在标签表，但有子标签存在（去掉父项目 P3 后）----
    # 删除 P3 → '领域'独立标签消失，仅剩"领域/科幻"、"领域/工具书"
    p3 = next(p for p in repo.list_projects() if p.title == "P3")
    repo.delete_project(p3.id)
    by_pref = repo.list_projects(tag_prefix="领域")
    titles = sorted(p.title for p in by_pref)
    t.assert_eq("删 P3 后 tag_prefix='领域': 仅 P1+P2",
                titles, ["P1", "P2"])

    # ---- 边缘：精确匹配也工作 ----
    by_tag = repo.list_projects(tag="领域")
    t.assert_eq("删 P3 后 tag='领域'（精确）: 空", by_tag, [])

    # ---- keyword + tag_prefix 组合（确保查询不互相破坏）----
    # P1 标题有 "P1"，加上 tag_prefix='领域' 应仍能命中
    by_combo = repo.list_projects(keyword="P", tag_prefix="领域")
    titles = sorted(p.title for p in by_combo)
    t.assert_eq("keyword + tag_prefix 组合: P1+P2",
                titles, ["P1", "P2"])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
