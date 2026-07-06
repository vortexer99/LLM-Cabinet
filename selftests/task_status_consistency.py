"""任务状态一致性自检（跨任务，非单个 task 的端到端验证）。

动机：同一个"完成状态"被手抄在多处（任务卡 `**状态**` 头、`tasks/README.md`
索引表、TODO.md、CHANGELOG.md），必然发散。本脚本把"任务卡头部状态"与
"README 索引表状态"归一成粗粒度类别后比对，自动抓出二者相互矛盾的漂移
（例如文件头写 `待做` 而索引写 `✅`，或反之）。

只比对"完成度类别"，不强求文案逐字一致——后者会因日期/子任务描述差异
产生大量假阳性。类别：

- DONE     ：有 ✅，且无任何未完成标记
- PARTIAL  ：🚧/进行中，或"部分 ✅ + 部分 待做/远期"混合
- TODO     ：待做/⚪/远期/排期，且无 ✅
- SPLIT    ：已拆分（指针卡）
- UNKNOWN  ：无法识别（视为失败信号）

危险漂移 = 一侧 DONE、另一侧 TODO（或类别不等）。这正是历史上 #02/#04/
#09/#13/#24/#28/#29/#31a/#31b 出过的问题。

跑法（仓库根目录）::

    python selftests/task_status_consistency.py

退出码 0=一致，1=发现漂移/索引缺口。无副作用、不碰数据库、不导入 app。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from _common import T

TASKS_DIR = _ROOT / "tasks"
README = TASKS_DIR / "README.md"

# 任务卡头部的 **状态** 行（可能带 `> ` 引用前缀，冒号可为中/英文）。
_STATUS_RE = re.compile(r"\*\*状态\*\*\s*[:：]\s*(.+?)\s*$")
# README 表格首列里的任务卡链接，文件名以数字开头（01.. / 31a.. 等）。
_LINK_RE = re.compile(r"\[([0-9][^\]]*\.md)\]")


def task_files() -> list[Path]:
    """tasks/ 下所有编号任务卡（排除 README.md）。"""
    return sorted(p for p in TASKS_DIR.glob("*.md") if p.name.lower() != "readme.md")


def file_status(md_path: Path) -> str | None:
    """提取任务卡头部的 `**状态**` 文案；没有则返回 None。"""
    for line in md_path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip(">").strip()
        m = _STATUS_RE.search(stripped)
        if m:
            return m.group(1).strip()
    return None


def readme_status_map() -> dict[str, str]:
    """解析 README 索引表，返回 {任务卡文件名: 状态单元格文案}。"""
    rows: dict[str, str] = {}
    for line in README.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        m = _LINK_RE.search(cells[0])
        if not m:  # 跳过表头 / 分隔行
            continue
        rows[m.group(1)] = cells[-1]
    return rows


def classify(status: str | None) -> str:
    """把状态文案归一成粗粒度完成度类别。"""
    s = status or ""
    if "已拆分" in s:
        return "SPLIT"
    has_done = "✅" in s
    has_wip = "🚧" in s or "进行中" in s
    has_todo = "待做" in s or "⚪" in s
    has_defer = "远期" in s or "排期" in s
    if has_wip:
        return "PARTIAL"
    if has_done and (has_todo or has_defer):
        return "PARTIAL"
    if has_done:
        return "DONE"
    if has_todo or has_defer:
        return "TODO"
    return "UNKNOWN"


def test_every_task_file_indexed() -> bool:
    """每个任务卡都必须在 README 索引表里有一行。"""
    t = T()
    indexed = readme_status_map()
    for p in task_files():
        t.assert_in(f"{p.name} 在索引表中", p.name, indexed)
    return t.report()


def test_every_index_row_has_file() -> bool:
    """README 索引表里的每一行都必须指向真实存在的任务卡。"""
    t = T()
    existing = {p.name for p in task_files()}
    for fname in readme_status_map():
        t.assert_in(f"索引行 {fname} 对应文件存在", fname, existing)
    return t.report()


def test_status_buckets_match() -> bool:
    """任务卡头部状态 与 README 索引状态 的完成度类别必须一致。

    这是核心断言：抓"一边完成一边待做"的漂移。无 `**状态**` 头的文件作为
    软提示打印，不计入失败（约定缺口，非危险漂移）。
    """
    t = T()
    indexed = readme_status_map()
    missing_header: list[str] = []

    for p in task_files():
        fstat = file_status(p)
        if fstat is None:
            missing_header.append(p.name)
            continue
        rstat = indexed.get(p.name)
        if rstat is None:
            continue  # 由 test_every_task_file_indexed 负责报告
        fbucket = classify(fstat)
        rbucket = classify(rstat)
        t.assert_eq(
            f"{p.name} 类别一致（文件={fstat!r} / 索引={rstat!r}）",
            fbucket,
            rbucket,
        )
        t.assert_true(
            f"{p.name} 状态可识别",
            fbucket != "UNKNOWN" and rbucket != "UNKNOWN",
            f"文件={fstat!r} 索引={rstat!r}",
        )

    ok = t.report()
    if missing_header:
        print("-" * 60)
        print("软提示：以下任务卡缺少 `**状态**` 头部（不符合主流约定，未计入失败）：")
        for name in missing_header:
            print(f"  ? {name}")
    return ok


if __name__ == "__main__":
    print("=" * 60)
    print("task_status_consistency selftest")
    print("=" * 60)

    results = []
    for name, fn in [
        ("test_every_task_file_indexed", test_every_task_file_indexed),
        ("test_every_index_row_has_file", test_every_index_row_has_file),
        ("test_status_buckets_match", test_status_buckets_match),
    ]:
        print(f"\n--- {name} ---")
        results.append(fn())
        sys.stdout.flush()

    print("\n" + "=" * 60)
    all_passed = all(results)
    print("ALL PASSED" if all_passed else "SOME FAILED")
    print("=" * 60)
    sys.exit(0 if all_passed else 1)
