# 30 · 文件来源标记（user / generated）

**工作量**：S
**优先级**：P1
**状态**：待做

## 来源

task #28 评审时浮现的设计缺口：**软件衍生数据与用户原始数据在数据层完全平权，无法区分**。

典型案例是「封面快照」。封面有两种来源（见 `main_window.action_set_cover` / `_save_cover_snapshot`）：

| 封面来源 | 是否产生新数据 | 现状如何落地 |
|----------|----------------|--------------|
| 用户自己的图片文件 | 否 | `cover_file_id` 仅指向已存在的 file 行（纯指针） |
| 截 PDF 首页 / 视频帧 / 剪贴板图 | **是** | 生成 `__cover_<ts>.png` 落到 `library/project_N/`，再**当成一条普通 `files` 行**写入 |

软件生成的封面快照，和用户拖进来的原始文件，共用同一张 `files` 表、同一个 `library/` 目录、文件表里并排显示。唯一区分信号只有两个**很弱**的约定：

1. 文件名前缀 `__cover_`
2. `label` 文本「封面（截取自 PDF）」等

`_save_cover_snapshot` 自己也写了句心照不宣的注释「截图都是衍生物，统一放仓库」，但 schema 层面零区分。

**这导致的问题**：
- 导出/导入（#28）把衍生封面当普通文件搬来搬去，重导入后它"是衍生物"这件事彻底丢失。
- 一致性检查（#14）、占用统计把衍生物和用户数据混算。
- 用户文件列表里混入一堆 `__cover_*.png`，分不清哪些是自己的。

## 目标

给 `files` 表加一个轻量来源标记，**形式化"用户数据 vs 软件衍生数据"的区分**，让导出/导入、一致性检查、未来的缩略图缓存都能统一消费。

本卡只做**底座**（schema + 模型 + 生成端打标 + 历史回填）；具体消费方（#28 导出带 origin、#14 分开统计、UI 折叠展示）各自在自己卡里接。

---

## 范围与边界

**做**：
- `files` 表加 `origin` 列（schema v7 → v8）
- `FileItem` 模型 + Repository 读写支持 `origin`
- `_save_cover_snapshot` 生成封面时写 `origin='generated'`
- 历史数据回填：已存在的 `__cover_*.png` 行标 `origin='generated'`

**不做（交给消费方各自的卡）**：
- #28：files.json 带 `origin`、导入时尊重它（在 #28 内做）
- #14：一致性检查/占用统计按 origin 分开（#14 已完成，后续小修，不在本卡强求）
- UI 把 generated 文件折叠/打标/筛选 —— 与 task #04（项目内系统/配置文件折叠）天然同源，建议并入 #04，见「待澄清」

---

## 设计

### `origin` 取值

```
'user'       —— 用户导入的原始文件（默认值）
'generated'  —— 软件生成的衍生物（封面快照、未来的缩略图/摘要等）
```

- 取值设计为**开放字符串**（不是布尔），未来可细分 `'thumbnail'` / `'summary'` 而不必再改 schema。
- 列定义：`origin TEXT NOT NULL DEFAULT 'user'`——默认 `'user'` 保证既有行、未显式赋值的新行都安全归类为用户数据。

### Schema 迁移（v7 → v8）

按 `db.py` 既有流程（`docs/migrations.md` 有说明）：

1. `SCHEMA_VERSION` 7 → 8
2. `SCHEMA` 常量里 `files` 表加 `origin TEXT NOT NULL DEFAULT 'user'`（给全新库）
3. 新增 `_migrate_v7_to_v8(conn)`（幂等，给旧库）：

```python
def _migrate_v7_to_v8(conn):
    """task #30：files 加 origin 列（用户数据 / 软件衍生物），并回填历史封面快照。"""
    file_cols = {r[1] for r in conn.execute("PRAGMA table_info(files)").fetchall()}
    if "origin" not in file_cols:
        conn.execute(
            "ALTER TABLE files ADD COLUMN origin TEXT NOT NULL DEFAULT 'user'"
        )
    # 回填：历史上 _save_cover_snapshot 生成的封面快照文件名形如
    # project_N/__cover_<ts>.png，按文件名前缀精准识别（不会误伤用户文件）
    conn.execute(
        r"UPDATE files SET origin='generated' "
        r"WHERE origin='user' AND (path LIKE '%/__cover\_%' ESCAPE '\' "
        r"OR path LIKE '__cover\_%' ESCAPE '\')"
    )
```

4. `MIGRATIONS` 注册表追加 `(7, 8, _migrate_v7_to_v8)`
5. 同步 `docs/migrations.md` + `CHANGELOG.md [Unreleased]`
6. 用一份 v7 老库本地验证一次

> ⚠️ 回填只认 `__cover_` 文件名前缀（精准），不用 label「封面（…）」判定——避免用户把自己的图片改名/打标后被误标为衍生物。

### 模型 + Repository

- `models.FileItem` 加 `origin: str = "user"`
- `repository.add_file`：INSERT 带上 `origin`
- `repository.update_file`：当前 `update_file` 只更新 `path/is_relative/label/kind/ord`，**不动 origin**（origin 一旦确定不应被普通编辑改写，符合预期）
- `_row_to_file`：读出 `origin`（缺列兜底 `'user'`）

### 生成端打标

`_save_cover_snapshot` 里构造的 `FileItem` 加 `origin="generated"`：

```python
fi = FileItem(
    project_id=int(p.id),
    path=rel,
    is_relative=True,
    label=label,
    kind="image",
    origin="generated",   # ← 本卡新增
)
```

---

## 校验

- [ ] 全新库：`files` 表含 `origin` 列，默认 `'user'`
- [ ] v7 老库升级：迁移后含 `origin` 列；历史 `__cover_*.png` 行被标 `'generated'`，其余仍 `'user'`
- [ ] 迁移幂等：重复打开同一库不报错、不重复回填
- [ ] 截 PDF/视频/剪贴板封面 → 新生成的 file 行 `origin='generated'`
- [ ] 用户拖入的图片设为封面 → 该文件 `origin` 仍是 `'user'`（只动 cover_file_id）
- [ ] 普通编辑文件（改 label）不会把 origin 改掉
- [ ] 迁移前自动备份（`*.v7.*.bak`）正常生成
- [ ] `selftests/task00_db_migration.py` 覆盖 v7 → v8

---

## 依赖

- **强依赖**：task #17（subfolder，最近一次 files 表迁移 v6→v7）✅ —— 本卡是其后的 v7→v8
- **服务于**：task #28（导出/导入闭环）—— #28 的 files.json 带 `origin`、导入时尊重它
- **相邻**：task #04（系统/配置文件折叠）—— UI 层把 generated 文件折叠/区分展示建议并入 #04
- **联动**：task #12 自检（迁移脚本）、`docs/migrations.md`、`CHANGELOG.md`

---

## 工作量拆分

| 子项 | 估算 |
|---|---|
| schema v7→v8 迁移（加列 + 回填 + 注册） | 0.2 天 |
| 模型 / Repository / 生成端打标 | 0.2 天 |
| 文档同步（migrations.md / CHANGELOG）+ selftest | 0.2 天 |
| **合计** | ~0.6 天（S） |

---

## 后续扩展

- **更细的 origin 取值**：`'thumbnail'` / `'summary'`（task #07 本地摘要落地时）
- **MCP 暴露**：`query_projects` / `manage_files` 返回里带 origin，让 agent 也能区分
- **占用统计**：库设置里分别显示"用户文件 / 软件生成"的体积占用

---

## 待澄清

> 卡片正文已按"默认决定"写成可执行状态；**若不同意，请在我编码前告知**。

1. **generated 文件在文件表怎么展示**
   - 默认决定：本卡**不动 UI**，generated 文件照常显示（保证封面在文件列表里可见、可重设）。把"折叠/隐藏/打标软件生成文件"的交互**并入 task #04**（它本就是做"系统/配置文件折叠"的）。
   - 若你希望本卡顺手给 generated 文件加个小图标/tooltip 区分，告诉我，加 ~0.1 天。

2. **origin 粒度**
   - 默认决定：先只分 `'user'` / `'generated'` 两值，但列用开放字符串，未来可加细分（`thumbnail`/`summary`）而不改 schema。
   - 若你现在就想一步到位区分封面/缩略图/摘要，告诉我，回填和取值约定要相应扩展。

3. **历史回填的判定依据**
   - 默认决定：只按文件名前缀 `__cover_` 精准回填（漏标也只是把个别衍生封面留作 'user'，不会误伤用户文件）。
   - 若你能接受"用 label 模糊匹配多回填一些"，告诉我，但有误标用户文件的风险。
