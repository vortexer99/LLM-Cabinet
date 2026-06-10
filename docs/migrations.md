# 数据迁移与版本化指南

> 适用范围：LLM Cabinet 本地数据（SQLite + `library/` 文件目录）
> 维护者：项目作者
> 最后更新：2026-06-10

本文档说明 **如何在新版本里安全演进数据结构**，让旧版本用户升级到新版本时
旧数据能正确、自动地迁移过来，且失败时可以回滚。

---

## 1. 两个独立版本号

| 名称 | 位置 | 用途 | 何时 +1 |
|---|---|---|---|
| `__version__` | `app/__init__.py` | 应用版本，UI 显示、打包文件名 | 每次发布 |
| `SCHEMA_VERSION` | `app/db.py` | 数据库 schema 版本，迁移判断依据 | 仅当 schema 或数据有破坏性变化时 |

两个版本号语义不同，**不强求同步**。仅修 UI bug 升 `__version__`，
不动 `SCHEMA_VERSION`；动了 schema 但没有 user-visible 变化的，
可只升 `SCHEMA_VERSION`。

应用版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)：
- `MAJOR.MINOR.PATCH`
- breaking change → `MAJOR +1`
- 新功能（兼容） → `MINOR +1`
- bug fix → `PATCH +1`

---

## 2. 数据存放位置

### 2.1 数据库

默认路径：`%APPDATA%/LLMCabinet/cabinet.db`

可在「设置 → 项目库」里看到当前数据库实际路径。

### 2.2 仓储文件目录

默认路径：`%APPDATA%/LLMCabinet/library/<project_id>/`

只有"📦 仓储"模式导入的文件会复制到这里；"🔗 链接"文件保留原始位置。

### 2.3 自动备份目录

迁移前的备份会落到 **数据库同目录** 下，文件名格式：

```
cabinet.v{old_version}.{yyyyMMdd-HHmmss}.bak
例：cabinet.v1.20260601-093015.bak
```

用户可以手动复制走、或在需要回滚时改回 `cabinet.db`（备份是完整的 SQLite 文件副本）。

---

## 3. SQLite `PRAGMA user_version`

SQLite 内置一个 32-bit 整数字段 `user_version`，专门给应用做 schema 版本号用。

- 全新数据库：`user_version == 0`
- 我们的约定：**全新库直接被打成当前 `SCHEMA_VERSION`**，跳过历史迁移
- 老库（曾被旧版本应用打开过）：`user_version` 为某个具体版本号 `N > 0`，
  按 `MIGRATIONS` 注册表逐版本应用迁移直到 `SCHEMA_VERSION`

代码位置：`app/db.py` 中 `_run_migrations()`。

---

## 4. 迁移工作流（每次发版必读）

### 4.1 步骤清单

1. **改 `SCHEMA` 字符串**：
   - 加表：在 `SCHEMA` 末尾追加 `CREATE TABLE IF NOT EXISTS ...`
   - 加列：**不要** 改 `SCHEMA` 里已有的 `CREATE TABLE`（因为 `IF NOT EXISTS`
     对老库无效）；必须在迁移函数里用 `ALTER TABLE ADD COLUMN`
   - 加索引：`CREATE INDEX IF NOT EXISTS ...` 可直接写进 `SCHEMA`

2. **写迁移函数**，命名 `_migrate_vN_to_vM`，要求：
   - **幂等**：用 `PRAGMA table_info` / `sqlite_master` 查询守卫，跑多遍结果一致
   - **不抛异常**：可控错误用 `try/except` 包好，把进度推进到下一步
   - **只前进**：不写"回滚" SQL；回滚靠 `.bak` 备份
   - **不动用户数据语义**：能保留就保留旧值

3. **注册到 `MIGRATIONS`**：
   ```python
   MIGRATIONS = [
       (1, 2, _migrate_v1_to_v2),
       (2, 3, _migrate_v2_to_v3),
       (3, 4, _migrate_v3_to_v4),
   ]
   ```

4. **`SCHEMA_VERSION += 1`**

5. **`__version__`**：按语义化版本规则升

6. **更新 `CHANGELOG.md`**：在 `## [Unreleased]` 区块加条目，标注
   `📦 schema vN → vM` 并简述迁移做了什么

7. **本地验证**（强制）：
   - 从 git 或备份找一份**老版本应用** 生成的 `cabinet.db`
   - 用**新版本应用**打开
   - 确认：(a) 没有崩溃；(b) 老数据完整可见；(c) 同目录下出现 `.bak` 备份

### 4.2 写迁移函数的代码模板

```python
def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """v1 → v2: 给 files 表加 sha256 列，用于去重检测."""
    cur = conn.cursor()
    cols = [r["name"] for r in cur.execute("PRAGMA table_info(files)")]
    if "sha256" not in cols:
        cur.execute("ALTER TABLE files ADD COLUMN sha256 TEXT")
    # 注意：ALTER TABLE 之后不要在同一个事务里 SELECT 该列，SQLite 不要求 commit
    # 但稳妥起见显式 commit
    conn.commit()


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """v2 → v3: 把 projects.storage_mode 的 NULL 值统一刷成 'link'."""
    conn.execute(
        "UPDATE projects SET storage_mode='link' "
        "WHERE storage_mode IS NULL OR storage_mode=''"
    )
    conn.commit()
```

### 4.3 必须避免的操作

| ❌ 不要 | ✅ 用这个替代 |
|---|---|
| 在 `SCHEMA` 里修改已有 `CREATE TABLE` | 在迁移函数里 `ALTER TABLE` |
| `DROP TABLE` 用户数据表 | 留着不用即可；可以在 docstring 标 deprecated |
| 直接 `DELETE FROM ...` 不可恢复的数据 | 改成"标记为隐藏"或加新列存新值 |
| 在迁移函数里调用 UI/网络 | 迁移必须纯本地、纯 SQL |
| 假设上一版迁移已跑过 | 每个迁移自检 `table_info / sqlite_master` |

> **关于 `DROP COLUMN`**：SQLite 3.35+ 支持 `ALTER TABLE DROP COLUMN`，本项目
> 要求 Python 3.12+ 自带的 sqlite 一定够新。`DROP COLUMN` 用户数据列**允许**，
> 但必须满足两个前置条件：
> 1. 列里的现存数据已被迁移到别处（或确认无需保留）
> 2. 迁移函数有 `PRAGMA table_info` 守卫确保幂等（已 DROP 时整段早返回）
>
> 实例参考 `_migrate_v3_to_v4`（task #20）：把 4 列值搬到 `project_field_values`
> 后再 `DROP COLUMN`，且全程加 `PRAGMA table_info` 守卫。

### 4.4 不能用 `ALTER COLUMN` 怎么办？

SQLite **不支持** `ALTER TABLE ... ALTER COLUMN`。`DROP COLUMN` 在 3.35+ 已
原生支持，本项目 Python 3.12+ 自带的 sqlite 一定够新（详见 § 4.3 的说明）。

需要改列定义（如 type / NOT NULL 约束变更）时：经典 12 步法（CREATE NEW →
INSERT SELECT → DROP OLD → RENAME），封装好后写进迁移函数即可。Python 标准
库自带的 sqlite3 模块对它没特殊支持，照常 `conn.executescript(...)` 即可。

---

## 5. `library/` 文件目录的迁移

文件目录变更不走 `user_version`，但同样需要"先备份再迁移"。

### 5.1 推荐模式

- **只加不改**：新版本想引入新结构时，**优先**采用"新文件用新结构、老文件保持原位"的兼容方案
- **必须移动时**：写一次性迁移：扫描旧路径，把文件 move 到新路径，更新 DB 里的 `path` 列
- 失败的单文件**保留原位**，记录到状态栏或日志；下次启动自动重试

### 5.2 当前 library 路径稳定性约定

```
library/
└── <project_id>/                ← 项目内 fan-out
    └── <hash>__<原文件名>       ← `Library.import_copy` 生成
```

- `<project_id>` 是项目数据库主键，不会变
- `<hash>` 用于防同名冲突，**任何版本都不能改算法**

如果要换 hash 算法 / 改文件名规则，必须把旧规则当 fallback 保留至少 2 个 major 版本。

---

## 6. 备份与回滚

### 6.1 自动备份触发条件

`connect(db_path)` 在打开主连接前会调用 `_probe_user_version()` 读出
旧版本号；如果 `user_version != SCHEMA_VERSION` 就 `_backup_db()` 复制一份。

→ 也就是说：**任何 schema 跨版本升级都自动备份**。同版本号下重复启动不会重复备份。

### 6.2 用户视角的回滚

如果某次升级后用户报告数据错乱：

1. 关闭应用
2. 找到 `cabinet.db` 同目录下最近一份 `cabinet.v{N}.{时间戳}.bak`
3. 把它 rename 为 `cabinet.db`（覆盖损坏的现库）
4. 用对应**旧版本应用**打开

我们承诺：备份文件是迁移**前**的完整快照，可直接被对应版本打开。

### 6.3 备份清理

目前**不会**自动清理 `.bak`。原因：
- 个人项目，数据量小，备份不大
- 用户可能想用 `.bak` 找回意外删除的项目

未来如果备份多到困扰，可以在「设置 → 项目库」加个"清理旧备份"按钮，
只删除超过 N 份 / 超过 N 天的。

---

## 7. 启动期的自愈与种子

`db.py` 中两个与历史兼容**无关**的常驻函数（`connect()` 每次启动都会跑）：

- `_seed_fields`：`fields` 表空时灌默认 7 个字段（仅在全新库触发）
- `_ensure_protected_fields`：保护字段（`title` / `description` / `tags`）缺失则补回，并将
  其类型重新归一为 `text` / `textarea` / `tags`。用于防御用户误删或将来某次迁移写错。

> 历史上曾存在 `_migrate_add_columns` / `_migrate_custom_fields` /
> `_backfill_system_field_keys` 三个用于 user_version=0 老库的兜底函数，
> 在 v0.1.0 (schema v1) 正式发布且确认无更早的存量库后已被移除。
> 后续任何新增的迁移统一走 `MIGRATIONS` 注册表。

---

## 8. 测试矩阵建议

每个版本发布前至少跑一遍：

| 起始状态 | 操作 | 预期结果 |
|---|---|---|
| 空目录 | 启动新版 | 创建新库，`user_version = SCHEMA_VERSION` |
| 上一版生成的 db | 启动新版 | `.bak` 文件出现；迁移函数依次执行；UI 数据完整 |
| 隔了 2 个版本的 db | 启动新版 | `.bak` 出现；连续应用 2 个迁移 |
| `user_version > SCHEMA_VERSION`（降级） | 启动新版 | 不动数据，UI 可读（向前兼容靠 `CREATE TABLE IF NOT EXISTS`） |
| 用户中途强杀 | 重启 | `llm_tasks` 表里 running 状态自动转 failed |

---

## 9. FAQ

**Q：能不能用 SQLAlchemy / Alembic 来管理迁移？**
A：可以但杀鸡用牛刀。本项目用直拼 SQL + `PRAGMA user_version` 已经够用，
而且不引入新依赖，PyInstaller 打包体积更小。

**Q：能不能让用户先看到"即将升级 db schema"的提示？**
A：可以做，但目前没做。要做的话在 `connect()` 之前显示一个 QMessageBox。
缺点是冷启动会被打断。如果将来出现一个**很重**的迁移（比如要扫描全部
library 文件做 rehash），可以考虑加上。

**Q：备份文件能压缩吗？**
A：能。`shutil.copy2` 改成 `gzip.open + shutil.copyfileobj` 即可。
但 SQLite 数据库通常不大（个人用户 10MB 内），目前不做。

---

## 10. 检查表（提交 PR 前自检）

- [ ] `SCHEMA_VERSION` 是否需要 +1？
- [ ] `MIGRATIONS` 是否已注册新迁移函数？
- [ ] 新迁移函数是否**幂等**？
- [ ] 是否用一份老 db 本地走过一遍？
- [ ] `CHANGELOG.md` 是否记了 `📦 schema vN → vM`？
- [ ] `__version__` 是否按语义化版本升级？
