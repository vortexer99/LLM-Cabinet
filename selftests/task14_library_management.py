"""task #14 自检：库一致性检查 + 备份/恢复。

T1 验证：
  - schema v2 → v3 ：files 表新增 missing 列
  - run_consistency_check 正确识别失效文件（仓储 + 链接两类）
  - apply_consistency_action 三档行为（noop / mark / delete）
  - clear_all_missing_flags 重置标记

T2 验证：
  - backup_library 把整个库目录打成 zip
  - restore_library 解到空目录后能识别为完整库
  - 老 zip / 非空目标目录的错误兜底
"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T

from app.db import connect, SCHEMA_VERSION
from app.library import Library
from app.library_check import (
    apply_consistency_action, backup_library, restore_library,
    run_consistency_check,
)
from app.models import FileItem, Project
from app.repository import Repository


def main() -> int:
    t = T()
    repos: list[Repository] = []
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpstr:
            tmp = Path(tmpstr)
            _run_all(tmp, t, repos)
            for r in repos:
                try:
                    r.conn.close()
                except Exception:
                    pass
    finally:
        ok = t.report()
    return 0 if ok else 1


def _run_all(tmp: Path, t: T, repos: list[Repository]) -> None:
    # ----------------------------------------------------------------
    # 阶段 1：schema v3 + missing 列
    # ----------------------------------------------------------------
    t.assert_eq("SCHEMA_VERSION = 3", SCHEMA_VERSION, 3)

    db_a = tmp / "a.db"
    repo_a = Repository(connect(db_a))
    repos.append(repo_a)
    cols = {r[1] for r in repo_a.conn.execute("PRAGMA table_info(files)").fetchall()}
    t.assert_in("files 表含 missing 列", "missing", cols)

    # ----------------------------------------------------------------
    # 阶段 2：造一个含失效文件的库
    # ----------------------------------------------------------------
    lib_root = tmp / "lib_a"
    lib_root.mkdir()
    library = Library(lib_root)

    # P1：两个仓储文件，其中一个会被外部"删除"
    p1 = Project(title="P1", storage_mode="copy")
    p1.id = repo_a.save_project(p1)
    src1 = tmp / "src1.txt"
    src1.write_text("v1", encoding="utf-8")
    src2 = tmp / "src2.txt"
    src2.write_text("v2", encoding="utf-8")
    rel1 = library.import_copy(p1.id, src1)
    rel2 = library.import_copy(p1.id, src2)
    fid_keep = repo_a.add_file(FileItem(
        project_id=p1.id, path=rel1, is_relative=True, kind="doc", ord=0,
    ))
    fid_lost_storage = repo_a.add_file(FileItem(
        project_id=p1.id, path=rel2, is_relative=True, kind="doc", ord=1,
    ))

    # P2：两个链接文件，其中一个会被外部"移走"
    p2 = Project(title="P2", storage_mode="link")
    p2.id = repo_a.save_project(p2)
    link_keep = tmp / "link_keep.txt"
    link_keep.write_text("ok", encoding="utf-8")
    link_lost = tmp / "link_lost.txt"
    link_lost.write_text("temp", encoding="utf-8")
    repo_a.add_file(FileItem(
        project_id=p2.id, path=str(link_keep), is_relative=False, kind="doc", ord=0,
    ))
    fid_lost_link = repo_a.add_file(FileItem(
        project_id=p2.id, path=str(link_lost), is_relative=False, kind="doc", ord=1,
    ))

    # 模拟外部删除：仓储文件被人手工删除
    (lib_root / rel2).unlink()
    # 链接文件原路径失效
    link_lost.unlink()

    # ----------------------------------------------------------------
    # 阶段 3：run_consistency_check
    # ----------------------------------------------------------------
    rep = run_consistency_check(repo_a, library)
    t.assert_eq("总文件数", rep.total_files, 4)
    t.assert_eq("仓储文件数", rep.n_storage, 2)
    t.assert_eq("链接文件数", rep.n_link, 2)
    t.assert_eq("仓储失效数", len(rep.storage_missing), 1)
    t.assert_eq("链接失效数", len(rep.link_missing), 1)
    t.assert_eq("storage_missing[0] file_id 匹配",
                rep.storage_missing[0].file_id, fid_lost_storage)
    t.assert_eq("link_missing[0] file_id 匹配",
                rep.link_missing[0].file_id, fid_lost_link)
    t.assert_eq("总失效数", rep.total_missing, 2)

    # ----------------------------------------------------------------
    # 阶段 4：apply_consistency_action（noop）
    # ----------------------------------------------------------------
    n_marked, n_del = apply_consistency_action(repo_a, rep, "noop")
    t.assert_eq("noop: 0 marked", n_marked, 0)
    t.assert_eq("noop: 0 deleted", n_del, 0)
    f = repo_a.get_file(fid_lost_storage)
    t.assert_eq("noop 后 missing 仍为 False", f.missing, False)

    # ----------------------------------------------------------------
    # 阶段 5：apply_consistency_action（mark）
    # ----------------------------------------------------------------
    n_marked, n_del = apply_consistency_action(repo_a, rep, "mark")
    t.assert_eq("mark: 标记数 = 2", n_marked, 2)
    t.assert_eq("mark: 删除数 = 0", n_del, 0)
    t.assert_true("mark 后失效仓储 missing=True",
                  repo_a.get_file(fid_lost_storage).missing)
    t.assert_true("mark 后失效链接 missing=True",
                  repo_a.get_file(fid_lost_link).missing)
    t.assert_eq("mark 后正常文件 missing 仍为 False",
                repo_a.get_file(fid_keep).missing, False)

    # clear_all_missing_flags
    n_clear = repo_a.clear_all_missing_flags()
    t.assert_eq("clear_all_missing_flags 清掉 2 行", n_clear, 2)
    t.assert_eq("clear 后失效仓储 missing=False",
                repo_a.get_file(fid_lost_storage).missing, False)

    # ----------------------------------------------------------------
    # 阶段 6：apply_consistency_action（delete）
    # ----------------------------------------------------------------
    rep2 = run_consistency_check(repo_a, library)
    n_marked, n_del = apply_consistency_action(repo_a, rep2, "delete")
    t.assert_eq("delete: 0 marked", n_marked, 0)
    t.assert_eq("delete: 删除数 = 2", n_del, 2)
    t.assert_eq("delete 后总文件数减 2",
                len(repo_a.list_files(p1.id)) + len(repo_a.list_files(p2.id)), 2)
    t.assert_eq("delete 后失效仓储 get_file 返回 None",
                repo_a.get_file(fid_lost_storage), None)

    # ----------------------------------------------------------------
    # 阶段 7：backup_library
    # ----------------------------------------------------------------
    # 准备一个真正的"库目录"（含 cabinet.db + library/ + .llm-cabinet 标记）
    real_lib_root = tmp / "real_lib"
    real_lib_root.mkdir()
    (real_lib_root / "library").mkdir()
    (real_lib_root / ".llm-cabinet").touch()
    real_db = real_lib_root / "cabinet.db"
    real_repo = Repository(connect(real_db))
    repos.append(real_repo)
    real_repo.add_field("ISBN", "text")
    p = Project(title="背包样本")
    p.id = real_repo.save_project(p)
    # 关闭以保证 zip 时无锁
    real_repo.conn.close()
    repos.remove(real_repo)

    # 跑备份
    backup_zip = backup_library(real_lib_root, tmp / "backup.zip")
    t.assert_true("备份 zip 存在", backup_zip.is_file())
    t.assert_true("备份 zip 大小 > 0", backup_zip.stat().st_size > 0)

    # ----------------------------------------------------------------
    # 阶段 8：restore_library
    # ----------------------------------------------------------------
    restore_target = tmp / "restored"
    out = restore_library(backup_zip, restore_target)
    t.assert_eq("restore 返回路径正确", out.resolve(), restore_target.resolve())
    t.assert_true("恢复后含 cabinet.db",
                  (restore_target / "cabinet.db").is_file())
    t.assert_true("恢复后含 .llm-cabinet 标记",
                  (restore_target / ".llm-cabinet").is_file())
    t.assert_true("恢复后含 library/ 子目录",
                  (restore_target / "library").is_dir())

    # 打开恢复后的 db 验证数据
    restored_repo = Repository(connect(restore_target / "cabinet.db"))
    repos.append(restored_repo)
    field_names = {f.name for f in restored_repo.list_fields()}
    t.assert_in("恢复后字段定义还在", "ISBN", field_names)
    titles = {p.title for p in restored_repo.list_projects()}
    t.assert_in("恢复后项目还在", "背包样本", titles)

    # ----------------------------------------------------------------
    # 阶段 9：错误兜底
    # ----------------------------------------------------------------
    # 解到非空目录 → 拒绝
    busy = tmp / "busy"
    busy.mkdir()
    (busy / "user.txt").write_text("already here", encoding="utf-8")
    threw = False
    try:
        restore_library(backup_zip, busy)
    except FileExistsError:
        threw = True
    t.assert_true("解到非空目录 → 抛 FileExistsError", threw)

    # 不存在的备份文件
    threw = False
    try:
        restore_library(tmp / "nonexistent.zip", tmp / "x")
    except FileNotFoundError:
        threw = True
    t.assert_true("不存在的备份 → 抛 FileNotFoundError", threw)

    # 不是合法库的 zip（手工造一个只含 user.txt 的 zip）
    import zipfile
    fake_zip = tmp / "fake.zip"
    with zipfile.ZipFile(fake_zip, "w") as z:
        z.writestr("readme.txt", "not a library")
    threw = False
    try:
        restore_library(fake_zip, tmp / "y")
    except ValueError:
        threw = True
    t.assert_true("非合法库 zip → 抛 ValueError", threw)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
