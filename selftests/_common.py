"""selftests 公用工具。

只放与具体 task 无关的基础设施：
- 简易断言收集器 T
- 与 SQLite 临时库相关的 fixture / 清理助手

不要在这里塞业务逻辑或某个 task 专属的样本数据。
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# 把仓库根加入 sys.path，让 selftests 脚本可以直接 `import app.xxx`
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class T:
    """简易断言收集器：失败不立即抛出，最后统一报告。

    用法::

        t = T()
        t.assert_eq("foo", actual, expected)
        ok = t.report()  # True 表示全部通过
    """

    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def assert_eq(self, label: str, actual, expected) -> None:
        if actual == expected:
            self.passed.append(label)
        else:
            self.failed.append((label, f"expected={expected!r}, actual={actual!r}"))

    def assert_true(self, label: str, cond: bool, hint: str = "") -> None:
        if cond:
            self.passed.append(label)
        else:
            self.failed.append((label, hint or "condition is False"))

    def assert_in(self, label: str, needle, haystack) -> None:
        if needle in haystack:
            self.passed.append(label)
        else:
            self.failed.append((label, f"{needle!r} not in {haystack!r}"))

    def report(self) -> bool:
        print("=" * 60)
        print(f"PASSED: {len(self.passed)}")
        print(f"FAILED: {len(self.failed)}")
        if self.failed:
            print("-" * 60)
            for label, hint in self.failed:
                print(f"  ✗ {label}")
                print(f"      → {hint}")
        return not self.failed


@contextmanager
def closing_repos(*repos) -> Iterator[None]:
    """在作用域退出时显式关闭 SQLite 连接。

    Windows 上 ``tempfile.TemporaryDirectory`` 清理时若仍有句柄持有
    ``.db`` 文件，会触发 ``PermissionError``。所有自检脚本都应把它们的
    ``Repository`` 传到这里。
    """
    try:
        yield
    finally:
        for r in repos:
            try:
                r.conn.close()
            except Exception:
                pass


__all__ = ["T", "closing_repos"]
