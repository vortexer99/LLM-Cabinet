"""SQLite 连接、schema 与一次性迁移。

字段（Field）抽象：
- title 是必有的核心字段，存在 projects.title 列里，且在 fields 表里 key='title'。
- description 是受保护字段，存在 projects.description_md 列里，key='description'。
- tags 走独立的 tags + project_tags 多对多表，key='tags'。
- 其它所有字段（含 author/date/source_url/rating 这些"老系统字段"，以及全部
  用户自定义字段）值统一存到 project_field_values，按 field_id 关联。
- ``fields.key`` 非空仅表示"种入时的稳定标识"（用于受保护判定、新建库向导默认
  勾选、导入器宽松匹配），不再决定值的存储位置（task #20 schema v4 起）。

版本管理：见 ``SCHEMA_VERSION`` 与 ``MIGRATIONS``。完整说明见 ``docs/migrations.md``。
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable

# =============================================================================
# Schema 版本
# =============================================================================
# 每次需要数据库迁移时 +1，并在下方 MIGRATIONS 注册表里追加一项 (from_v, to_v, fn)。
# 全新数据库会直接被打上当前 SCHEMA_VERSION，无需跑历史迁移。
SCHEMA_VERSION = 8

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    description_md  TEXT,
    storage_mode    TEXT NOT NULL DEFAULT 'link',
    cover_file_id   INTEGER,
    mcp_modified_at TEXT,                     -- task #24：MCP 修改标记
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS project_tags (
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id)     ON DELETE CASCADE,
    PRIMARY KEY (project_id, tag_id)
);

CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    is_relative INTEGER NOT NULL DEFAULT 0,
    label       TEXT,
    kind        TEXT NOT NULL,
    ord         INTEGER NOT NULL DEFAULT 0,
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    missing     INTEGER NOT NULL DEFAULT 0,  -- 一致性检查标记（task #14 T1）
    subfolder   TEXT NOT NULL DEFAULT '',    -- 逻辑子目录路径（task #17）：POSIX 格式，"" = 顶层
    origin      TEXT NOT NULL DEFAULT 'user' -- 文件来源（task #30）：user=用户原始文件 / generated=软件衍生物
);

CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id);

-- 字段定义：
--   key 非空 → 种入时带稳定标识（用于受保护判定、新建库向导默认勾选、导入器
--             宽松匹配）；不再决定值的存储位置（task #20 schema v4 起）
--             受保护字段：title / description / tags，由 PROTECTED_FIELD_KEYS 维护
--   key 空   → 用户自定义字段
-- 所有非保护字段值（含 author/date/source_url/rating 等老系统字段）统一存
-- project_field_values，按 field_id 关联
CREATE TABLE IF NOT EXISTS fields (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL DEFAULT 'text',
    ord             INTEGER NOT NULL DEFAULT 0,
    visible         INTEGER NOT NULL DEFAULT 1,
    key             TEXT,
    suggest_enabled INTEGER NOT NULL DEFAULT 1,  -- 是否让 LLM 对该字段提建议
    prompt_hint     TEXT NOT NULL DEFAULT ''     -- 该字段在 LLM 建议时附加的格式说明（task #11 T1）
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fields_name ON fields(name);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fields_key  ON fields(key) WHERE key IS NOT NULL;

CREATE TABLE IF NOT EXISTS project_field_values (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    field_id   INTEGER NOT NULL REFERENCES fields(id)   ON DELETE CASCADE,
    value      TEXT,
    PRIMARY KEY (project_id, field_id)
);

-- LLM 任务队列
CREATE TABLE IF NOT EXISTS llm_tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    project_title TEXT,                           -- 冗余：项目可能被删，仍可显示
    type         TEXT NOT NULL,                   -- 'meta_suggest'
    status       TEXT NOT NULL DEFAULT 'queued',  -- queued/running/done/failed/cancelled
    payload_json TEXT,                            -- 入参（参考文件、附言、provider 等）
    result_json  TEXT,                            -- 返回的结构化结果
    error        TEXT,
    provider     TEXT,                            -- 实际使用的 provider id
    model        TEXT,
    tokens_in    INTEGER DEFAULT 0,
    tokens_out   INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    started_at   TEXT,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_tasks_status ON llm_tasks(status);
CREATE INDEX IF NOT EXISTS idx_llm_tasks_created ON llm_tasks(created_at DESC);

-- 字段建议
CREATE TABLE IF NOT EXISTS project_field_suggestions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    field_id        INTEGER NOT NULL REFERENCES fields(id)   ON DELETE CASCADE,
    suggested_value TEXT,
    source_task_id  INTEGER REFERENCES llm_tasks(id) ON DELETE SET NULL,
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending/applied/rejected/superseded
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_pfs_project ON project_field_suggestions(project_id, status);
CREATE INDEX IF NOT EXISTS idx_pfs_field   ON project_field_suggestions(field_id);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

-- 项目级别的设置项（与全局 settings 区分，按项目独立存储）
-- 当前键名：files_table_columns
CREATE TABLE IF NOT EXISTS project_settings (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      TEXT,
    PRIMARY KEY (project_id, key)
);
"""


# 默认字段，按显示顺序
# (name, type, key)
#
# task #15 T1（D2 默认列可见性）：新库默认只 seed 标题 / 标签 / 描述 三个保护字段；
# 其中：
#   - 标题：visible=1（强制，且 UI 层 is_title 也强制可见）
#   - 描述：visible=0（多行文本在主列表里太占空间，仅在项目编辑对话框显示）
#   - 标签：visible=0（标签筛选已经有左侧标签树，列表里再列一遍冗余）
# 既有库不动（fields 表非空时 _seed_fields 直接 return，不会改 visible）。
#
# 「作者 / 日期 / 评分 / 来源」是**可选默认字段**：新建库向导（task #15 T1）让用户
# 在第 3 页勾选是否一并创建。它们的值跟其它用户字段一样存在 project_field_values
# 里（task #20 schema v4 起；v3 及更早版本曾存在 projects 表的同名列里）。
# DEFAULT_FIELDS 仅含强制 seed 的 3 个保护字段；OPTIONAL_DEFAULT_FIELDS 是可选的 4 个，
# 数据结构 = (name, type, key, default_visible)。
DEFAULT_FIELDS = [
    # (name, type, key, default_visible)
    ("标题",   "text",     "title",       1),
    ("标签",   "tags",     "tags",        0),
    ("描述",   "textarea", "description", 0),
]

OPTIONAL_DEFAULT_FIELDS = [
    # (name, type, key, default_visible)
    ("作者",   "text",     "author",     1),
    ("日期",   "date",     "date",       1),
    ("评分",   "rating",   "rating",     1),
    ("来源",   "url",      "source_url", 1),
]


def _seed_fields(conn: sqlite3.Connection) -> None:
    """若 fields 表为空，插入默认受保护字段（标题 / 标签 / 描述）。

    可见性默认（D2）：标题 visible=1、描述/标签 visible=0；可选默认字段不在
    本函数 seed 范围内（由 task #15 新建库向导按用户勾选决定）。

    task #20 schema v4 起：所有非保护字段值统一存 project_field_values，本函数
    只负责往 fields 表插记录，不做任何值层面的迁移。
    """
    cur = conn.cursor()
    n = cur.execute("SELECT COUNT(*) AS c FROM fields").fetchone()["c"]
    if n > 0:
        return
    for i, (name, ftype, key, vis) in enumerate(DEFAULT_FIELDS):
        cur.execute(
            "INSERT INTO fields(name, type, ord, visible, key) VALUES(?,?,?,?,?)",
            (name, ftype, i, vis, key),
        )
    conn.commit()


def _ensure_protected_fields(conn: sqlite3.Connection) -> None:
    """保护字段（title / description / tags）自愈：缺失则补回、类型强制归一。

    这是与历史无关的运行时防御：用户如果误删了保护字段（或将来某次迁移写错），
    应用启动时能恢复到可用状态。幂等。
    """
    cur = conn.cursor()

    # 兜底：必须存在 key='title' 的字段
    has_title = cur.execute(
        "SELECT 1 FROM fields WHERE key='title' LIMIT 1"
    ).fetchone()
    if not has_title:
        # 把所有现有字段 ord+1，再插入 ord=0 的标题
        cur.execute("UPDATE fields SET ord = ord + 1")
        cur.execute(
            "INSERT INTO fields(name, type, ord, visible, key) VALUES(?, ?, 0, 1, 'title')",
            ("标题", "text"),
        )

    # 兜底：description / tags 也必须存在
    for name, ftype, key, _vis in DEFAULT_FIELDS:
        if key not in ("description", "tags"):
            continue
        exists = cur.execute(
            "SELECT 1 FROM fields WHERE key=? LIMIT 1", (key,)
        ).fetchone()
        if exists:
            continue
        max_ord = cur.execute(
            "SELECT COALESCE(MAX(ord), -1) AS m FROM fields"
        ).fetchone()["m"]
        # 自愈插入用 visible=1（保守）：用户运行时被自愈触发说明字段是被误删了；
        # 此时按"看得见"恢复更稳，免得用户以为没补回来。
        # 与首次 seed 的 D2（描述/标签 visible=0）有意区别。
        cur.execute(
            "INSERT INTO fields(name, type, ord, visible, key) VALUES(?, ?, ?, 1, ?)",
            (name, ftype, (max_ord or -1) + 1, key),
        )

    # 标题字段：必显、类型固定 text（LLM 建议是否参与由用户在设置页自由勾选）
    cur.execute("UPDATE fields SET visible=1, type='text' WHERE key='title'")
    # 描述字段：类型固定 textarea
    cur.execute("UPDATE fields SET type='textarea' WHERE key='description'")
    # 标签字段：类型固定 tags
    cur.execute("UPDATE fields SET type='tags' WHERE key='tags'")
    conn.commit()



def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # ---- 迁移前：版本探测 + 自动备份 ----
    pre_version = _probe_user_version(db_path)
    if pre_version is not None and pre_version != SCHEMA_VERSION:
        _backup_db(db_path, pre_version)

    # check_same_thread=False：允许 LLM worker 线程通过同一连接访问；
    # 调用方必须自行加锁保证串行化（见 LLMTaskQueue._db_lock）
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=3000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA)
    # 版本化迁移：按注册表顺序应用未跑过的迁移
    _run_migrations(conn)
    _seed_fields(conn)
    _ensure_protected_fields(conn)
    # 启动时把卡在 running 的任务标记为 failed（应用上次异常退出留下的）
    conn.execute(
        "UPDATE llm_tasks SET status='failed', error='中断（程序退出）', "
        "finished_at=datetime('now') WHERE status IN ('queued','running')"
    )
    conn.commit()
    return conn


# =============================================================================
# 版本化迁移
# =============================================================================
# 增加迁移的步骤：
# 1. 在 db.py 顶部把 SCHEMA_VERSION += 1
# 2. 写一个 _migrate_vN_to_vM(conn) 函数（幂等：用 PRAGMA table_info / IF NOT EXISTS 守卫）
# 3. 在下方 MIGRATIONS 列表追加 (N, M, _migrate_vN_to_vM)
# 4. 在 docs/migrations.md 与 CHANGELOG.md 里记一笔
# 5. 用一份老版本生成的 db 文件本地验证一次

def _probe_user_version(db_path: Path) -> int | None:
    """在不污染主连接的情况下读 user_version。
    数据库不存在 → 返回 None；存在但版本号为 0 表示老版本或全新创建。"""
    if not db_path.exists():
        return None
    try:
        probe = sqlite3.connect(str(db_path))
        try:
            v = probe.execute("PRAGMA user_version").fetchone()[0]
        finally:
            probe.close()
        return int(v)
    except sqlite3.DatabaseError:
        # 文件损坏或者不是 sqlite，让主流程去抛错
        return None


def _backup_db(db_path: Path, old_version: int) -> Path | None:
    """迁移前自动备份。失败不阻塞主流程。"""
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = db_path.with_name(
            f"{db_path.stem}.v{old_version}.{stamp}.bak"
        )
        shutil.copy2(db_path, backup)
        return backup
    except OSError:
        return None


def _get_user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_user_version(conn: sqlite3.Connection, v: int) -> None:
    # PRAGMA 不支持参数绑定，需要直接拼接整数
    conn.execute(f"PRAGMA user_version = {int(v)}")


def _is_fresh_database(conn: sqlite3.Connection) -> bool:
    """判断是不是首次创建的全新库：projects 表为空 + 无 fields。"""
    n_proj = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    n_field = conn.execute("SELECT COUNT(*) FROM fields").fetchone()[0]
    return int(n_proj) == 0 and int(n_field) == 0


# 迁移注册表：(from_version, to_version, migrate_fn)
def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """v1 → v2：删除 v0.1.0 之前残留的 ``custom_fields`` 旧表。

    v1 库里这张表可能仍存在但已空（在 v0.1.0 发布前用过、之后不再读写）。
    新代码已经移除所有引用，这里把表 DROP 掉收尾。幂等。
    """
    conn.execute("DROP TABLE IF EXISTS custom_fields")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """v2 → v3：两项新增列，一次性合并迁移。

    - ``fields.prompt_hint``（task #11 T1）：用户在「设置 → 字段」里对每个字段填
      LLM 提示，组装 user prompt 时按字段注入。
    - ``files.missing``（task #14 T1）：用户跑「检查库一致性」后选择"标记为缺失"
      → 给文件加 missing=1，UI 展示 ⚠ 图标提示文件不在物理位置。

    幂等：先用 PRAGMA table_info 探测列是否已存在。
    """
    field_cols = {r[1] for r in conn.execute("PRAGMA table_info(fields)").fetchall()}
    if "prompt_hint" not in field_cols:
        conn.execute(
            "ALTER TABLE fields ADD COLUMN prompt_hint TEXT NOT NULL DEFAULT ''"
        )

    file_cols = {r[1] for r in conn.execute("PRAGMA table_info(files)").fetchall()}
    if "missing" not in file_cols:
        conn.execute(
            "ALTER TABLE files ADD COLUMN missing INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """v3 → v4：废弃系统字段的 projects 列分流，统一用 project_field_values（task #20）。

    把 ``projects.{author,date,source_url,rating}`` 4 列的值搬进
    ``project_field_values``，然后 DROP COLUMN 这 4 列。

    迁移后 ``fields.key`` 中的 ``author/date/source_url/rating`` 仍保留，仅作
    "种入时稳定标识"（用于新建库向导默认勾选、导入器宽松匹配），不再决定值的
    存储位置。

    特殊处理：
    - ``rating`` 是 INTEGER DEFAULT 0；v3 里 "未填" 语义是 0 不是 NULL，迁移
      时过滤掉 `rating = 0` 避免把"未填"误写成有效值"0"
    - 其它 3 列是 TEXT 默认 NULL；过滤掉 NULL 和空串
    - 迁移使用 ``INSERT OR IGNORE``：如果 project_field_values 里已经有该
      (project_id, field_id) 记录（用户后期可能手工动过），保留已有值不覆盖

    幂等：用 PRAGMA table_info 探测列是否还存在；列已被 DROP 则跳过整段迁移。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    cols_to_migrate = [c for c in ("author", "date", "source_url", "rating") if c in cols]
    if not cols_to_migrate:
        return  # 已经迁过

    cur = conn.cursor()
    # 1) 找出每个待迁列对应的 fields.id（按 key 查；这些字段在 fields 表里可能
    #    存在也可能不存在 —— 取决于用户建库时勾选了哪些可选字段）
    key_to_fid: dict[str, int] = {}
    for key in cols_to_migrate:
        row = cur.execute("SELECT id FROM fields WHERE key=?", (key,)).fetchone()
        if row is not None:
            key_to_fid[key] = int(row[0])

    # 2) 把 projects.<col> 有意义的值搬进 project_field_values。
    #    rating 是 INTEGER，CAST 成 TEXT；其它本来就是 TEXT。
    for key, fid in key_to_fid.items():
        col = key  # 列名等同 key
        if key == "rating":
            cur.execute(
                f"INSERT OR IGNORE INTO project_field_values(project_id, field_id, value) "
                f"SELECT id, ?, CAST({col} AS TEXT) FROM projects "
                f"WHERE {col} IS NOT NULL AND {col} != 0",
                (fid,),
            )
        else:
            cur.execute(
                f"INSERT OR IGNORE INTO project_field_values(project_id, field_id, value) "
                f"SELECT id, ?, {col} FROM projects "
                f"WHERE {col} IS NOT NULL AND {col} != ''",
                (fid,),
            )

    # 3) DROP COLUMN：SQLite >= 3.35 支持 ALTER TABLE DROP COLUMN。
    #    本项目要求 Python 3.12+，SQLite 一定够新。
    for col in cols_to_migrate:
        cur.execute(f"ALTER TABLE projects DROP COLUMN {col}")


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """task #13 T3：添加 mcp_audit 表（MCP 审计日志）。"""

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mcp_audit (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT NOT NULL DEFAULT (datetime('now')),
            client_name     TEXT,
            tool_name       TEXT NOT NULL,
            arguments_json  TEXT,
            result_status   TEXT NOT NULL DEFAULT 'success',
            error_message   TEXT
        )
    """)


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """task #24：projects 加 mcp_modified_at 列（MCP 修改标记）。"""

    conn.execute("ALTER TABLE projects ADD COLUMN mcp_modified_at TEXT")


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """task #17：files 加 subfolder 列（逻辑子目录路径，驱动 UI 树形展示）。"""

    file_cols = {r[1] for r in conn.execute("PRAGMA table_info(files)").fetchall()}
    if "subfolder" not in file_cols:
        conn.execute(
            "ALTER TABLE files ADD COLUMN subfolder TEXT NOT NULL DEFAULT ''"
        )


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """task #30：files 加 origin 列（用户数据 / 软件衍生物），并回填历史封面快照。

    - 新增 origin 列，默认 'user'
    - 回填：历史上 _save_cover_snapshot 生成的封面快照文件名形如
      project_N/__cover_<ts>.png，按文件名前缀精准识别（不会误伤用户文件）
    """
    file_cols = {r[1] for r in conn.execute("PRAGMA table_info(files)").fetchall()}
    if "origin" not in file_cols:
        conn.execute(
            "ALTER TABLE files ADD COLUMN origin TEXT NOT NULL DEFAULT 'user'"
        )
    # 回填：历史上 _save_cover_snapshot 生成的封面快照
    conn.execute(
        r"UPDATE files SET origin='generated' "
        r"WHERE origin='user' AND (path LIKE '%/__cover\_%' ESCAPE '\' "
        r"OR path LIKE '__cover\_%' ESCAPE '\')"
    )


MIGRATIONS: list[tuple[int, int, Callable[[sqlite3.Connection], None]]] = [
    (1, 2, _migrate_v1_to_v2),
    (2, 3, _migrate_v2_to_v3),
    (3, 4, _migrate_v3_to_v4),
    (4, 5, _migrate_v4_to_v5),
    (5, 6, _migrate_v5_to_v6),
    (6, 7, _migrate_v6_to_v7),
    (7, 8, _migrate_v7_to_v8),
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    """按注册表顺序运行未跑过的迁移。

    - 全新库 (PRAGMA user_version == 0 且无 projects/fields 行) → 直接打 SCHEMA_VERSION。
    - 旧库 (user_version < SCHEMA_VERSION) → 顺序应用 MIGRATIONS。
    - user_version > SCHEMA_VERSION（用户降级了客户端）→ 不动数据，但记录提示。
    """
    cur_v = _get_user_version(conn)

    if cur_v == 0 and _is_fresh_database(conn):
        _set_user_version(conn, SCHEMA_VERSION)
        return

    if cur_v > SCHEMA_VERSION:
        # 用户用了更新的版本生成 db 后又用了旧客户端打开 → 跳过迁移，靠 schema
        # 容错（CREATE TABLE IF NOT EXISTS 不会改已存在的表）
        return

    for from_v, to_v, fn in MIGRATIONS:
        if cur_v < to_v:
            fn(conn)
            _set_user_version(conn, to_v)
            cur_v = to_v
    conn.commit()
