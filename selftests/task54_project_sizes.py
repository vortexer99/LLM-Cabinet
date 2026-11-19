"""项目体积统计与缓存：只用显式临时库和自行生成的临时文件。"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import connect
from app.models import FileItem, Project
from app.project_sizes import (
    SIZE_SETTING_KEY, ProjectSize, ProjectSizeSnapshot, files_fingerprint, measure_project_sizes,
)
from app.repository import Repository
from app.utils import OperationCancelled
from selftests._common import T


def run(root: Path, t: T) -> None:
    repo = Repository(connect(root / "sizes.db"))
    try:
        first = repo.save_project(Project(title="合成混合项目"))
        second = repo.save_project(Project(title="合成空项目"))
        link = root / "link.bin"
        link.write_bytes(b"1234567")
        storage = root / "storage.bin"
        storage.write_bytes(b"12345678901")
        entries = [FileItem(project_id=first, path=str(link)),
                   FileItem(project_id=first, path="storage.bin", is_relative=True),
                   FileItem(project_id=first, path=str(link)),
                   FileItem(project_id=first, path=str(root / "missing.bin")),
                   FileItem(project_id=first, path=str(root))]
        for entry in entries:
            entry.id = repo.add_file(entry)
        files = repo.project_size_files([first, second, first])
        t.assert_eq("路径快照去重项目ID", set(files), {first, second})
        t.assert_eq("快照包含全部已登记条目", len(files[first]), 5)
        t.assert_eq("未统计没有缓存", repo.project_sizes([first, second]), {})
        fingerprint = files_fingerprint(files[first])
        t.assert_eq("登记顺序不影响指纹", files_fingerprint(reversed(files[first])), fingerprint)
        snapshots = [ProjectSizeSnapshot(first, fingerprint, tuple(
            root / entry.path if entry.is_relative else Path(entry.path) for entry in files[first]
        )), ProjectSizeSnapshot(second, files_fingerprint([]), ())]
        progress: list[tuple[int, int]] = []
        # 统计应只读元信息，不读取任何文件内容。
        with patch.object(Path, "open", side_effect=AssertionError("禁止读取文件内容")):
            results = measure_project_sizes(snapshots,
                                            lambda done, total, _name: progress.append((done, total)),
                                            lambda: False)
        t.assert_eq("链接仓储并计且重复登记逐条累计", results[0].size.bytes, 25)
        t.assert_eq("失效路径和目录计为不可用", results[0].size.missing, 2)
        t.assert_eq("失效条目含错误明细", len(results[0].errors), 2)
        t.assert_eq("空项目统计为真实零", results[1].size.bytes, 0)
        t.assert_eq("空项目没有缺失", results[1].size.missing, 0)
        t.assert_eq("进度强制首尾", (progress[0], progress[-1]), ((1, 5), (5, 5)))
        t.assert_eq("缓存JSON可往返", ProjectSize.from_json(results[0].size.to_json()), results[0].size)
        for raw in ("broken", "null", "[]", '{}', '{"bytes":-1,"missing":0}',
                    '{"bytes":true,"missing":0,"checked_at":"x","fingerprint":"x"}'):
            t.assert_eq(f"损坏缓存安全忽略 {raw}", ProjectSize.from_json(raw), None)
        before = [tuple(row) for row in repo.conn.execute("SELECT * FROM projects ORDER BY id")]
        repo.save_project_sizes({result.project_id: result.size for result in results})
        t.assert_eq("缓存不改项目记录和更新时间",
                    [tuple(row) for row in repo.conn.execute("SELECT * FROM projects ORDER BY id")], before)
        with patch.object(Path, "stat", side_effect=AssertionError("缓存查询不得访问磁盘")), \
                patch.object(repo, "project_size_files", side_effect=AssertionError("指纹不读完整文件对象")):
            cached = repo.project_sizes([first, second])
        t.assert_true("刚统计的登记指纹有效", not cached[first].stale)
        t.assert_true("空项目缓存有效", not cached[second].stale)
        repo.conn.close()
        repo = Repository(connect(root / "sizes.db"))
        t.assert_eq("重开库保留缓存", repo.project_sizes([first])[first].bytes, 25)
        link.write_bytes(b"external replacement")
        t.assert_true("磁盘变化需主动更新且不隐式stat", not repo.project_sizes([first])[first].stale)
        added = repo.add_file(FileItem(project_id=first, path=str(root / "new.bin")))
        t.assert_true("新增登记使缓存失效", repo.project_sizes([first])[first].stale)
        repo.conn.execute("DELETE FROM files WHERE id=?", (added,))
        repo.conn.commit()
        t.assert_true("恢复相同登记成员缓存有效", not repo.project_sizes([first])[first].stale)
        repo.conn.execute("UPDATE files SET path=? WHERE id=?", (str(root / "moved.bin"), entries[0].id))
        repo.conn.commit()
        t.assert_true("路径变化使缓存失效", repo.project_sizes([first])[first].stale)
        repo.conn.execute("UPDATE files SET path=?,is_relative=1 WHERE id=?", (str(link), entries[0].id))
        repo.conn.commit()
        t.assert_true("存储模式变化使缓存失效", repo.project_sizes([first])[first].stale)
        repo.conn.execute("DELETE FROM files WHERE id=?", (entries[0].id,))
        repo.conn.commit()
        t.assert_true("移除登记使缓存失效", repo.project_sizes([first])[first].stale)

        cancelled = False
        raw_before = repo.get_project_setting(first, SIZE_SETTING_KEY)
        try:
            measure_project_sizes(snapshots, lambda *_args: None, lambda: True)
        except OperationCancelled:
            cancelled = True
        t.assert_true("取消抛出取消信号而非返回半批", cancelled)
        t.assert_eq("取消不改原缓存", repo.get_project_setting(first, SIZE_SETTING_KEY), raw_before)
        with patch.object(Path, "stat", side_effect=PermissionError("合成权限拒绝")):
            denied = measure_project_sizes([ProjectSizeSnapshot(first, "x", (link,))],
                                          lambda *_args: None, lambda: False)[0]
        t.assert_eq("权限拒绝计入缺失", denied.size.missing, 1)
        t.assert_eq("无法读取部分只含已知零字节", denied.size.bytes, 0)

        # 大批量缓存读取分批绑定，且只查询命中并已有缓存的登记成员。
        with repo.conn:
            repo.conn.executemany("INSERT INTO projects(id,title) VALUES(?,?)",
                                  [(pid, f"批量合成{pid}") for pid in range(100, 1101)])
        size = ProjectSize(0, 0, "2026-11-16T00:00:00+00:00", files_fingerprint([]))
        repo.save_project_sizes({pid: size for pid in range(100, 1101)})
        queries: list[str] = []
        repo.conn.set_trace_callback(queries.append)
        with patch.object(Path, "stat", side_effect=AssertionError("不得stat")):
            batch = repo.project_sizes(list(range(100, 1101)))
        repo.conn.set_trace_callback(None)
        t.assert_eq("1001项目跨批查询完整", len(batch), 1001)
        t.assert_eq("缓存和指纹各两条批查询", len(queries), 4)
        t.assert_true("指纹只取必要列", all("SELECT *" not in query for query in queries))
        queries.clear()
        repo.conn.set_trace_callback(queries.append)
        repo.project_sizes([second])
        repo.conn.set_trace_callback(None)
        t.assert_true("窄筛选不查询其他缓存项目", all("100," not in query for query in queries))
        repo.save_project_sizes({99999: size})
        t.assert_eq("统计期间已删除项目不复活", repo.project_sizes([99999]), {})

        # 某个项目写入失败时整批事务回滚。
        with repo.conn:
            repo.conn.execute(f"CREATE TRIGGER fail_size BEFORE UPDATE ON project_settings "
                              f"WHEN OLD.project_id={second} AND OLD.key='{SIZE_SETTING_KEY}' "
                              "BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
        failed = False
        try:
            repo.save_project_sizes({first: replace(size, bytes=99), second: replace(size, bytes=99)})
        except Exception:
            failed = True
        t.assert_true("合成写入故障触发", failed)
        t.assert_eq("整批保存失败回滚", repo.get_project_setting(first, SIZE_SETTING_KEY), raw_before)
    finally:
        repo.conn.close()


if __name__ == "__main__":
    t = T()
    with tempfile.TemporaryDirectory(prefix="cabinet-size-test-") as directory:
        run(Path(directory), t)
    sys.exit(0 if t.report() else 1)
