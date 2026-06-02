"""task #08 自检：多库切换的非 GUI 数据层。

验证 ``app.cabinet`` 模块：
- ``CabinetConfig.load`` / ``save`` 往返
- ``touch``：新增、提升到列表头部、上限 5 条
- 默认库永远不被截断 / 不能被 ``remove``
- ``rename`` / ``find``
- 损坏的 cabinet.json 备份并重建为默认
- ``is_library_dir`` / ``is_empty_or_safe_for_library``
- ``mark_as_library`` 写出标记文件
- ``import_settings_from_other_db`` 只读拉取
- ``resolve_library_paths`` 派生路径

不接 GUI；不调 main_window。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T

from app.cabinet import (
    CABINET_JSON, CabinetConfig, LIBRARY_MARKER, MAX_RECENT,
    delete_library_owned_only, import_settings_from_other_db,
    is_empty_or_safe_for_library, is_library_dir, mark_as_library,
    resolve_library_paths, scan_library_for_deletion,
)
from app.db import connect
from app.repository import Repository


def main() -> int:
    t = T()
    repos: list[Repository] = []
    # CabinetConfig 内部用 app_data_dir() 来定位 cabinet.json。
    # 我们把 APPDATA 临时指向一个 tmp 目录，这样测试不污染用户真实目录。
    saved_appdata = os.environ.get("APPDATA")
    saved_xdg = os.environ.get("XDG_DATA_HOME")
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpstr:
            tmp = Path(tmpstr)
            fake_appdata = tmp / "AppData_Roaming"
            fake_appdata.mkdir()
            os.environ["APPDATA"] = str(fake_appdata)
            # 非 Windows 也设一下 XDG_DATA_HOME 兜底
            os.environ["XDG_DATA_HOME"] = str(fake_appdata)

            _run_all(tmp, t, repos)
            for r in repos:
                try:
                    r.conn.close()
                except Exception:
                    pass
    finally:
        # 还原环境变量
        if saved_appdata is not None:
            os.environ["APPDATA"] = saved_appdata
        elif "APPDATA" in os.environ:
            del os.environ["APPDATA"]
        if saved_xdg is not None:
            os.environ["XDG_DATA_HOME"] = saved_xdg
        elif "XDG_DATA_HOME" in os.environ:
            del os.environ["XDG_DATA_HOME"]
        ok = t.report()
    return 0 if ok else 1


def _run_all(tmp: Path, t: T, repos: list[Repository]) -> None:
    from app.utils import app_data_dir
    default_root = app_data_dir().resolve()
    cabinet_json_path = default_root / CABINET_JSON

    # ----------------------------------------------------------------
    # 阶段 1：load 缺失文件 → 默认值（默认库登记）
    # ----------------------------------------------------------------
    cfg = CabinetConfig.load()
    t.assert_eq(
        "缺失 cabinet.json：active 默认为默认库",
        Path(cfg.active_library).resolve() if cfg.active_library else None,
        default_root,
    )
    t.assert_eq(
        "缺失：recent 含 1 条（默认库）",
        len(cfg.recent_libraries), 1,
    )
    t.assert_eq(
        "默认库 path 正确",
        cfg.recent_libraries[0].path.resolve(), default_root,
    )

    # save 后能 load 回来
    cfg.save()
    t.assert_true("save 后 cabinet.json 存在", cabinet_json_path.is_file())
    cfg2 = CabinetConfig.load()
    t.assert_eq(
        "save+load 往返：recent 数量",
        len(cfg2.recent_libraries), 1,
    )

    # ----------------------------------------------------------------
    # 阶段 2：touch 新增 / 提升 / 上限
    # ----------------------------------------------------------------
    libs = [tmp / f"lib{i}" for i in range(1, 7)]
    for p in libs:
        p.mkdir()
        mark_as_library(p)

    cfg.touch(libs[0], label="L1")
    t.assert_eq("touch[1] 后 active 切换", cfg.active_library, libs[0])
    t.assert_eq("touch[1] 后 recent 数量", len(cfg.recent_libraries), 2)
    t.assert_eq("touch[1] 头部即新", cfg.recent_libraries[0].path, libs[0])

    cfg.touch(libs[1], label="L2")
    cfg.touch(libs[2], label="L3")
    cfg.touch(libs[3], label="L4")
    cfg.touch(libs[4], label="L5")  # 加上默认库共 6 条 → 应被截断
    t.assert_eq(
        "touch 多次后 ≤ MAX_RECENT (默认库强制保留)",
        len(cfg.recent_libraries), MAX_RECENT,
    )
    # 默认库始终在列表里（即便被截断也会被补回）
    t.assert_true(
        "默认库始终在最近列表",
        any(h.path.resolve() == default_root for h in cfg.recent_libraries),
    )

    # 再 touch lib0 → 提到头部
    cfg.touch(libs[0])
    t.assert_eq("再 touch[0] 提到头部", cfg.recent_libraries[0].path, libs[0])

    # ----------------------------------------------------------------
    # 阶段 3：remove / rename / find / 默认库不能 remove
    # ----------------------------------------------------------------
    # 重新 touch lib0、lib1 进列表（之前的 touch 顺序可能让它们被 trim 掉），
    # 确保后面 remove / rename 测试有目标
    cfg.touch(libs[0], label="L1-back")
    cfg.touch(libs[1], label="L2-back")
    before = len(cfg.recent_libraries)
    cfg.remove(libs[0])
    t.assert_eq("remove 后数量 -1", len(cfg.recent_libraries), before - 1)
    t.assert_true(
        "remove 后 lib0 不在列表",
        not any(h.path.resolve() == libs[0].resolve() for h in cfg.recent_libraries),
    )

    # 默认库 remove 应被忽略
    before = len(cfg.recent_libraries)
    cfg.remove(default_root)
    t.assert_eq(
        "默认库 remove 被忽略",
        len(cfg.recent_libraries), before,
    )

    cfg.rename(libs[1], "新名字")
    h = cfg.find(libs[1])
    t.assert_eq("rename 生效", h.label if h else None, "新名字")

    # find 不存在时返回 None
    t.assert_eq("find 不存在 → None", cfg.find(tmp / "nope"), None)

    # save + load 维持
    cfg.save()
    cfg3 = CabinetConfig.load()
    h2 = cfg3.find(libs[1])
    t.assert_eq("save/load 后 rename 仍在", h2.label if h2 else None, "新名字")

    # ----------------------------------------------------------------
    # 阶段 4：损坏的 cabinet.json → 备份并重建
    # ----------------------------------------------------------------
    cabinet_json_path.write_text("{not json", encoding="utf-8")
    cfg_recovered = CabinetConfig.load()
    t.assert_eq(
        "损坏后回退默认：recent 数量",
        len(cfg_recovered.recent_libraries), 1,
    )
    # 应有 .bak.* 备份文件
    bak_files = list(default_root.glob(f"{CABINET_JSON}.bak.*"))
    t.assert_true("损坏的 json 已备份", len(bak_files) >= 1)
    # 现在 cabinet.json 是新建的，损坏文件已搬走 → 但 cfg_recovered 是内存对象，要 save 才会写新 json
    cfg_recovered.save()
    t.assert_true(
        "重建后 cabinet.json 是合法 JSON",
        isinstance(json.loads(cabinet_json_path.read_text(encoding="utf-8")), dict),
    )

    # ----------------------------------------------------------------
    # 阶段 5：is_library_dir / is_empty_or_safe_for_library / mark_as_library
    # ----------------------------------------------------------------
    fresh = tmp / "fresh"
    fresh.mkdir()
    t.assert_eq("空目录不是 library_dir", is_library_dir(fresh), False)
    t.assert_eq("空目录可作为新库目录", is_empty_or_safe_for_library(fresh), True)

    mark_as_library(fresh)
    t.assert_eq("mark 后 .llm-cabinet 标记存在",
                (fresh / LIBRARY_MARKER).is_file(), True)
    t.assert_eq("mark 后是 library_dir", is_library_dir(fresh), True)
    t.assert_eq(
        "已是库的目录不再是 'safe for new library'",
        is_empty_or_safe_for_library(fresh), False,
    )

    # 含 cabinet.db（无 marker）也算 library_dir（兼容老库）
    only_db = tmp / "old_lib"
    only_db.mkdir()
    (only_db / "cabinet.db").write_text("", encoding="utf-8")
    t.assert_eq("仅含 cabinet.db 也算 library_dir", is_library_dir(only_db), True)

    # 含其它文件的目录不能新建库
    dirty = tmp / "dirty"
    dirty.mkdir()
    (dirty / "user.txt").write_text("hi", encoding="utf-8")
    t.assert_eq("含业务文件 → 不是 safe for new library",
                is_empty_or_safe_for_library(dirty), False)

    # 隐藏文件不影响判定
    hidden = tmp / "hidden_only"
    hidden.mkdir()
    (hidden / ".gitignore").write_text("*", encoding="utf-8")
    t.assert_eq("仅含隐藏文件 → 仍 safe", is_empty_or_safe_for_library(hidden), True)

    # ----------------------------------------------------------------
    # 阶段 6：resolve_library_paths
    # ----------------------------------------------------------------
    db_p, lib_sub = resolve_library_paths(fresh)
    t.assert_eq("派生 db_path", db_p, fresh / "cabinet.db")
    t.assert_eq("派生 library/", lib_sub, fresh / "library")

    # ----------------------------------------------------------------
    # 阶段 7：import_settings_from_other_db
    # ----------------------------------------------------------------
    src_db = tmp / "src" / "cabinet.db"
    src_db.parent.mkdir()
    src_repo = Repository(connect(src_db))
    repos.append(src_repo)
    src_repo.set_setting("llm_config", '{"openai": {"api_key": "k"}}')
    src_repo.set_setting("llm_default_provider", "openai")
    src_repo.set_setting("unrelated_key", "should not be read")

    out = import_settings_from_other_db(
        src_db, ["llm_config", "llm_default_provider", "unrelated_key", "missing"]
    )
    t.assert_in("import_settings: 含 llm_config", "llm_config", out)
    t.assert_in("import_settings: 含 llm_default_provider", "llm_default_provider", out)
    t.assert_eq("import_settings: 不抓 missing 键", "missing" in out, False)
    t.assert_eq(
        "import_settings: 值正确",
        out.get("llm_default_provider"), "openai",
    )

    # 不存在的 db
    out2 = import_settings_from_other_db(tmp / "nonexistent.db", ["llm_config"])
    t.assert_eq("不存在的 db → 空 dict", out2, {})

    # ----------------------------------------------------------------
    # 阶段 8：scan_library_for_deletion / delete_library_owned_only
    # （删除整个库时识别"用户外来内容"，避免误删笔记 / 备份等）
    # ----------------------------------------------------------------
    delroot = tmp / "delete_test_lib"
    delroot.mkdir()
    # 库自身条目（白名单）
    (delroot / LIBRARY_MARKER).write_text("")
    (delroot / "cabinet.db").write_bytes(b"x" * 1024)
    (delroot / "cabinet.db-wal").write_bytes(b"w" * 32)
    (delroot / "cabinet.db-shm").write_bytes(b"s" * 16)
    (delroot / "cabinet.v3.bak").write_bytes(b"b" * 64)
    (delroot / "cabinet.v2.20260101120000.bak").write_bytes(b"b" * 48)
    libsub = delroot / "library"
    libsub.mkdir()
    (libsub / "project_1.txt").write_bytes(b"y" * 256)
    # 用户外来内容
    (delroot / "notes.md").write_bytes(b"u" * 100)
    (delroot / "backup_2024.zip").write_bytes(b"z" * 200)
    foreign_dir = delroot / "myfolder"
    foreign_dir.mkdir()
    (foreign_dir / "inner.txt").write_bytes(b"f" * 50)

    scan = scan_library_for_deletion(delroot)
    owned_names = {p.name for p in scan.owned}
    foreign_names = {p.name for p in scan.foreign}
    t.assert_eq("scan owned 含库标记", LIBRARY_MARKER in owned_names, True)
    t.assert_eq("scan owned 含 cabinet.db", "cabinet.db" in owned_names, True)
    t.assert_eq("scan owned 含 cabinet.db-wal", "cabinet.db-wal" in owned_names, True)
    t.assert_eq("scan owned 含 cabinet.db-shm", "cabinet.db-shm" in owned_names, True)
    t.assert_eq("scan owned 含 cabinet.vN.bak", "cabinet.v3.bak" in owned_names, True)
    t.assert_eq(
        "scan owned 含 cabinet.vN.<时间戳>.bak",
        "cabinet.v2.20260101120000.bak" in owned_names, True,
    )
    t.assert_eq("scan owned 含 library/", "library" in owned_names, True)
    t.assert_eq("scan foreign 含 notes.md", "notes.md" in foreign_names, True)
    t.assert_eq("scan foreign 含外来 zip", "backup_2024.zip" in foreign_names, True)
    t.assert_eq("scan foreign 含外来子目录", "myfolder" in foreign_names, True)
    t.assert_eq(
        "scan owned + foreign 数量 = 顶层条目数",
        len(scan.owned) + len(scan.foreign), 10,
    )
    t.assert_eq("scan foreign_size > 0", scan.foreign_size > 0, True)
    t.assert_eq("scan owned_size > 0", scan.owned_size > 0, True)

    # 执行 owned-only 删除
    failures = delete_library_owned_only(delroot)
    t.assert_eq("delete_owned_only 全部成功", failures, [])
    t.assert_eq("delroot 目录本身保留", delroot.is_dir(), True)
    t.assert_eq("库标记被删", (delroot / LIBRARY_MARKER).exists(), False)
    t.assert_eq("cabinet.db 被删", (delroot / "cabinet.db").exists(), False)
    t.assert_eq("library/ 被删", libsub.exists(), False)
    t.assert_eq("cabinet.v3.bak 被删", (delroot / "cabinet.v3.bak").exists(), False)
    t.assert_eq("notes.md 保留", (delroot / "notes.md").is_file(), True)
    t.assert_eq("backup_2024.zip 保留", (delroot / "backup_2024.zip").is_file(), True)
    t.assert_eq("myfolder 保留", foreign_dir.is_dir(), True)
    t.assert_eq("myfolder/inner.txt 保留", (foreign_dir / "inner.txt").is_file(), True)
    # 删完后，目录已不再是有效库
    t.assert_eq("删完后 is_library_dir = False", is_library_dir(delroot), False)

    # 干净库（无外来内容）扫描结果：foreign 为空
    cleanroot = tmp / "delete_test_clean"
    cleanroot.mkdir()
    (cleanroot / LIBRARY_MARKER).write_text("")
    (cleanroot / "cabinet.db").write_bytes(b"x" * 32)
    scan2 = scan_library_for_deletion(cleanroot)
    t.assert_eq("干净库 scan: foreign 空", scan2.foreign, [])
    t.assert_eq("干净库 scan: owned 非空", len(scan2.owned) >= 2, True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
