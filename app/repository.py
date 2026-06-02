"""数据访问层（CRUD）。所有 UI 通过 Repository 与数据库交互。"""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional

from .models import Field, FieldSuggestion, FileItem, LLMTask, Project


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ------------------------------------------------------------------ projects
    def _attach_project_extras(self, projects: list[Project]) -> list[Project]:
        """批量补齐 Project.tags 和 Project.field_values。

        list_projects 这类返回多个 Project 的接口默认只查 projects 表，
        不带 tags / 自定义字段值，导致项目列表里 tags 列、用户字段列显示为空。
        这里用两条聚合查询批量补齐，避免 N+1。
        """
        if not projects:
            return projects
        ids = [p.id for p in projects if p.id is not None]
        if not ids:
            return projects
        placeholders = ",".join(["?"] * len(ids))

        # tags: project_id -> [tag_name, ...]
        tag_map: dict[int, list[str]] = {pid: [] for pid in ids}
        rows = self.conn.execute(
            f"""SELECT pt.project_id AS pid, t.name AS name
                FROM project_tags pt
                JOIN tags t ON t.id = pt.tag_id
                WHERE pt.project_id IN ({placeholders})
                ORDER BY t.name""",
            ids,
        ).fetchall()
        for r in rows:
            tag_map.setdefault(int(r["pid"]), []).append(r["name"])

        # field_values: project_id -> {field_id: value}
        fv_map: dict[int, dict[int, str]] = {pid: {} for pid in ids}
        rows = self.conn.execute(
            f"""SELECT project_id AS pid, field_id, value
                FROM project_field_values
                WHERE project_id IN ({placeholders})""",
            ids,
        ).fetchall()
        for r in rows:
            fv_map.setdefault(int(r["pid"]), {})[int(r["field_id"])] = r["value"] or ""

        for p in projects:
            if p.id is None:
                continue
            p.tags = tag_map.get(p.id, [])
            p.field_values = fv_map.get(p.id, {})
        return projects

    def list_projects(
        self,
        keyword: str = "",
        tag: str = "",
        tag_prefix: str = "",
    ) -> list[Project]:
        """列出项目。

        过滤参数（互斥使用，``tag`` 优先于 ``tag_prefix``）：
        - ``tag``：精确匹配标签名（沿用旧行为）
        - ``tag_prefix``：匹配所有形如 ``<prefix>`` 或 ``<prefix>/...`` 的标签（task #06 标签层级折叠）
        """
        sql = """
        SELECT DISTINCT p.* FROM projects p
        LEFT JOIN project_tags pt ON pt.project_id = p.id
        LEFT JOIN tags t ON t.id = pt.tag_id
        WHERE 1=1
        """
        params: list = []
        if keyword:
            sql += " AND (p.title LIKE ? OR p.author LIKE ? OR p.description_md LIKE ?)"
            kw = f"%{keyword}%"
            params += [kw, kw, kw]
        if tag:
            sql += " AND t.name = ?"
            params.append(tag)
        elif tag_prefix:
            # 同名父标签（"领域"）与子标签（"领域/科幻"）都命中
            sql += " AND (t.name = ? OR t.name LIKE ?)"
            params.append(tag_prefix)
            params.append(f"{tag_prefix}/%")
        sql += " ORDER BY p.updated_at DESC, p.id DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return self._attach_project_extras(
            [self._row_to_project(r) for r in rows]
        )


    def get_project(self, pid: int) -> Optional[Project]:
        row = self.conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if not row:
            return None
        p = self._row_to_project(row)
        p.tags = self.list_tags_of(pid)
        p.field_values = self.get_field_values(pid)
        return p

    def save_project(self, p: Project) -> int:
        cur = self.conn.cursor()
        if p.id is None:
            cur.execute(
                """INSERT INTO projects
                   (title, author, date, source_url, rating, description_md,
                    storage_mode, cover_file_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (p.title, p.author, p.date, p.source_url, p.rating,
                 p.description_md, p.storage_mode, p.cover_file_id),
            )
            p.id = cur.lastrowid
        else:
            cur.execute(
                """UPDATE projects SET
                     title=?, author=?, date=?, source_url=?, rating=?,
                     description_md=?, storage_mode=?, cover_file_id=?,
                     updated_at=datetime('now')
                   WHERE id=?""",
                (p.title, p.author, p.date, p.source_url, p.rating,
                 p.description_md, p.storage_mode, p.cover_file_id, p.id),
            )
        self._set_tags(p.id, p.tags)
        self._set_field_values(p.id, p.field_values)
        self.conn.commit()
        return p.id  # type: ignore[return-value]

    def delete_project(self, pid: int) -> None:
        self.conn.execute("DELETE FROM projects WHERE id=?", (pid,))
        self.conn.commit()

    def touch_project(self, pid: int) -> None:
        self.conn.execute(
            "UPDATE projects SET updated_at=datetime('now') WHERE id=?", (pid,)
        )
        self.conn.commit()

    # ------------------------------------------------------------------ tags
    def all_tags(self) -> list[str]:
        rows = self.conn.execute("SELECT name FROM tags ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    def tag_counts(self) -> list[tuple[str, int]]:
        """返回 [(tag_name, project_count), ...]，按名称排序。"""
        rows = self.conn.execute(
            """SELECT t.name, COUNT(pt.project_id) AS c
               FROM tags t
               LEFT JOIN project_tags pt ON pt.tag_id = t.id
               GROUP BY t.id ORDER BY t.name"""
        ).fetchall()
        return [(r["name"], r["c"]) for r in rows]

    def count_projects_total(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"]

    def count_user_added_fields(self) -> int:
        """统计用户额外添加的字段数（task #15 T2 横幅显示条件用）。

        定义："用户额外添加" = `fields` 表里 `key` 为空（无系统字段绑定）的字段
        数量。系统种子字段（标题 / 描述 / 标签 + 可选默认 4 个）都有 key，所以
        刚建好的库该计数应为 0；用户在「设置 → 字段」加过一个就会 ≥ 1。
        """
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM fields WHERE key IS NULL OR key = ''"
        ).fetchone()["c"]

    def count_projects_untagged(self) -> int:
        return self.conn.execute(
            """SELECT COUNT(*) AS c FROM projects p
               WHERE NOT EXISTS (
                 SELECT 1 FROM project_tags pt WHERE pt.project_id = p.id
               )"""
        ).fetchone()["c"]

    def list_projects_untagged(self, keyword: str = "") -> list[Project]:
        sql = """
        SELECT p.* FROM projects p
        WHERE NOT EXISTS (
          SELECT 1 FROM project_tags pt WHERE pt.project_id = p.id
        )
        """
        params: list = []
        if keyword:
            sql += " AND (p.title LIKE ? OR p.author LIKE ? OR p.description_md LIKE ?)"
            kw = f"%{keyword}%"
            params += [kw, kw, kw]
        sql += " ORDER BY p.updated_at DESC, p.id DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return self._attach_project_extras(
            [self._row_to_project(r) for r in rows]
        )

    def list_tags_of(self, pid: int) -> list[str]:
        rows = self.conn.execute(
            """SELECT t.name FROM tags t
               JOIN project_tags pt ON pt.tag_id = t.id
               WHERE pt.project_id = ? ORDER BY t.name""",
            (pid,),
        ).fetchall()
        return [r["name"] for r in rows]

    def _set_tags(self, pid: int, tags: Iterable[str]) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM project_tags WHERE project_id=?", (pid,))
        for name in {t.strip() for t in tags if t.strip()}:
            cur.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
            tag_id = cur.execute(
                "SELECT id FROM tags WHERE name=?", (name,)
            ).fetchone()["id"]
            cur.execute(
                "INSERT OR IGNORE INTO project_tags(project_id, tag_id) VALUES(?,?)",
                (pid, tag_id),
            )

    # ------------------------------------------------------------------ fields (schema)
    # 系统字段 key → projects 表列名
    SYSTEM_FIELD_COLUMNS = {
        "title": "title",
        "author": "author",
        "date": "date",
        "source_url": "source_url",
        "rating": "rating",
        "description": "description_md",
    }

    def list_fields(self) -> list[Field]:
        rows = self.conn.execute(
            "SELECT id, name, type, ord, visible, key, suggest_enabled, prompt_hint "
            "FROM fields ORDER BY ord, id"
        ).fetchall()
        return [
            Field(
                id=r["id"], name=r["name"], type=r["type"] or "text",
                ord=r["ord"], visible=bool(r["visible"]), key=r["key"],
                suggest_enabled=bool(r["suggest_enabled"]),
                prompt_hint=r["prompt_hint"] or "",
            )
            for r in rows
        ]

    def get_field(self, fid: int) -> Optional[Field]:
        row = self.conn.execute(
            "SELECT id, name, type, ord, visible, key, suggest_enabled, prompt_hint "
            "FROM fields WHERE id=?",
            (fid,),
        ).fetchone()
        if not row:
            return None
        return Field(
            id=row["id"], name=row["name"], type=row["type"] or "text",
            ord=row["ord"], visible=bool(row["visible"]), key=row["key"],
            suggest_enabled=bool(row["suggest_enabled"]),
            prompt_hint=row["prompt_hint"] or "",
        )

    def set_field_suggest_enabled(self, fid: int, enabled: bool) -> None:
        self.conn.execute(
            "UPDATE fields SET suggest_enabled=? WHERE id=?",
            (1 if enabled else 0, fid),
        )
        self.conn.commit()

    def add_field(self, name: str, ftype: str = "text", *, prompt_hint: str = "") -> int:
        name = name.strip()
        if not name:
            raise ValueError("字段名不能为空")
        cur = self.conn.cursor()
        max_ord = cur.execute(
            "SELECT COALESCE(MAX(ord), -1) AS m FROM fields"
        ).fetchone()["m"]
        cur.execute(
            "INSERT INTO fields(name, type, ord, visible, key, prompt_hint) "
            "VALUES(?, ?, ?, 1, NULL, ?)",
            (name, ftype, (max_ord or -1) + 1, prompt_hint or ""),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def add_fields_batch(
        self, fields_data: list[tuple[str, str, str]],
    ) -> list[int]:
        """批量创建字段（task #11 T3 决策 5）。

        Args:
            fields_data: list of ``(name, type, prompt_hint)``

        Returns:
            按入参顺序返回新建字段的 id 列表。任一失败 → 整体 ROLLBACK 并抛出原异常。
        """
        if not fields_data:
            return []
        cur = self.conn.cursor()
        try:
            cur.execute("BEGIN")
            row = cur.execute(
                "SELECT COALESCE(MAX(ord), -1) AS m FROM fields"
            ).fetchone()
            max_ord = row["m"] if row else -1
            new_ids: list[int] = []
            for i, (name, ftype, hint) in enumerate(fields_data):
                name = (name or "").strip()
                if not name:
                    raise ValueError("字段名不能为空")
                cur.execute(
                    "INSERT INTO fields(name, type, ord, visible, key, prompt_hint) "
                    "VALUES(?, ?, ?, 1, NULL, ?)",
                    (name, ftype, max_ord + 1 + i, hint or ""),
                )
                new_ids.append(cur.lastrowid)
            self.conn.commit()
            return new_ids
        except Exception:
            self.conn.rollback()
            raise

    def apply_field_plan_batch(
        self,
        creates: list[tuple[str, str, str]],
        updates_hint: list[tuple[int, str]],
        deletes: list[int],
        *,
        append_for_fids: Optional[Iterable[int]] = None,
        renames: Optional[list[tuple[int, str]]] = None,
    ) -> tuple[list[int], int, int]:
        """库字段设计助手「应用」一次性事务（task #11 T3 全量规划）。

        四类操作放进同一个 BEGIN/COMMIT：
          - renames：UPDATE fields SET name=? WHERE id=?（保留 fid，
            项目历史值通过 ``field_values`` 关联保持不动）
          - creates：新建用户字段（同 ``add_fields_batch``）
          - updates_hint：仅更新 prompt_hint
          - deletes：删除字段（系统字段会清空对应 projects 列；用户字段
            走 ``project_field_values`` 的 CASCADE）

        执行顺序：renames 先于 creates。理由：renames 把 "出版社" 改为 "出版商"
        后，creates 才能安全地添加另一个名为 "出版社" 的新字段（如果用户想这么做）。
        实际上 LLM 改名建议下游不会有这种边界场景，但顺序保持稳健。

        删除保护：
          - 受保护字段（``is_required``，即 标题/描述/标签）拒绝删除/改名，
            整体抛 ValueError
          - 不存在的 fid 静默跳过

        改名校验：
          - new_name 为空 → ValueError
          - new_name 与改名后的现存字段名冲突 → ValueError
          - 改名同一 fid 多次 → 以最后一次为准（不抛错）

        Args:
            creates: ``[(name, type, prompt_hint), ...]``
            updates_hint: ``[(field_id, prompt_hint), ...]``
            deletes: ``[field_id, ...]``
            append_for_fids: ``deletes`` 中需要"先把每个项目的字段值追加到
                description 末尾"的 fid 集合（与 ``Repository.delete_field``
                的 ``append_to_description=True`` 语义一致）；其它待删字段
                走"直接丢"路径。默认 None = 全部直接丢。
            renames: ``[(field_id, new_name), ...]``，UPDATE fields.name 用；
                默认 None = 不改名。受保护字段 fid（标题/描述/标签）→ ValueError。

        Returns:
            ``(new_ids, n_deleted, n_renamed)``。任一失败 → ROLLBACK 抛原异常。
        """
        append_set: set[int] = set(append_for_fids or ())
        renames_list: list[tuple[int, str]] = list(renames or [])
        cur = self.conn.cursor()
        try:
            cur.execute("BEGIN")

            # 0) renames（先于 creates，避免"新建字段名"与"待改名旧名"撞上）
            n_renamed = 0
            if renames_list:
                from .models import PROTECTED_FIELD_KEYS
                # fid → 最终新名（去重，最后一次为准）
                fid_to_new: dict[int, str] = {}
                for fid, new_name in renames_list:
                    new_name = (new_name or "").strip()
                    if not new_name:
                        raise ValueError("改名时新字段名不能为空")
                    fid_to_new[fid] = new_name
                # 收集"改名后不再占用"的旧名 与 "改名后将占用"的新名
                # 用于检查冲突
                fids_being_renamed = set(fid_to_new.keys())
                # 当前所有字段：除了正在被改名的，其名字仍占位
                surviving_names: set[str] = set()
                for r in cur.execute("SELECT id, name, key FROM fields"):
                    if r["id"] in fids_being_renamed:
                        continue
                    surviving_names.add(r["name"])
                # 新名集合（用于内部去重）
                new_names: list[str] = []
                for fid, new_name in fid_to_new.items():
                    f_row = cur.execute(
                        "SELECT id, name, key FROM fields WHERE id=?", (fid,),
                    ).fetchone()
                    if f_row is None:
                        # 不存在的 fid 静默跳过
                        continue
                    if f_row["key"] in PROTECTED_FIELD_KEYS:
                        raise ValueError(
                            f"『{f_row['name']}』是受保护字段，不可改名"
                        )
                    if new_name == f_row["name"]:
                        # 与原名相同 → noop，不计入 n_renamed
                        continue
                    if new_name in surviving_names or new_name in new_names:
                        raise ValueError(
                            f"改名冲突：新名「{new_name}」与已有字段重复"
                        )
                    new_names.append(new_name)
                    cur.execute(
                        "UPDATE fields SET name=? WHERE id=?",
                        (new_name, fid),
                    )
                    n_renamed += 1

            # 1) creates
            new_ids: list[int] = []
            if creates:
                row = cur.execute(
                    "SELECT COALESCE(MAX(ord), -1) AS m FROM fields"
                ).fetchone()
                max_ord = row["m"] if row else -1
                for i, (name, ftype, hint) in enumerate(creates):
                    name = (name or "").strip()
                    if not name:
                        raise ValueError("字段名不能为空")
                    cur.execute(
                        "INSERT INTO fields(name, type, ord, visible, key, "
                        "prompt_hint) VALUES(?, ?, ?, 1, NULL, ?)",
                        (name, ftype, max_ord + 1 + i, hint or ""),
                    )
                    new_ids.append(cur.lastrowid)

            # 2) updates_hint
            for fid, hint in updates_hint:
                cur.execute(
                    "UPDATE fields SET prompt_hint=? WHERE id=?",
                    (hint or "", fid),
                )

            # 3) deletes
            n_deleted = 0
            for fid in deletes:
                f = self.get_field(fid)
                if f is None:
                    continue
                # 受保护字段拒绝删除
                from .models import PROTECTED_FIELD_KEYS
                if f.key in PROTECTED_FIELD_KEYS:
                    raise ValueError(
                        f"『{f.name}』是受保护字段，不可删除"
                    )
                # 选择了"追加到 description"：先把每条非空值拼到对应 project
                # 的 description_md 末尾；与 delete_field(append_to_description=True)
                # 的格式保持一致（"\n\n**字段名**：值"）
                if fid in append_set:
                    rows = self._collect_field_values_for_all_projects(f)
                    for project_id, value in rows:
                        if not value:
                            continue
                        desc_row = cur.execute(
                            "SELECT description_md FROM projects WHERE id=?",
                            (project_id,),
                        ).fetchone()
                        desc = (
                            (desc_row["description_md"] or "")
                            if desc_row else ""
                        )
                        appendix = f"\n\n**{f.name}**：{value}"
                        new_desc = (desc.rstrip() + appendix).lstrip("\n")
                        cur.execute(
                            "UPDATE projects SET description_md=?, "
                            "updated_at=datetime('now') WHERE id=?",
                            (new_desc, project_id),
                        )
                # 系统非必有字段（作者/日期/评分/来源）：清空 projects 对应列
                if f.key in self.SYSTEM_FIELD_COLUMNS:
                    col = self.SYSTEM_FIELD_COLUMNS[f.key]
                    default = 0 if f.key == "rating" else ""
                    cur.execute(f"UPDATE projects SET {col}=?", (default,))
                # 用户字段：CASCADE 会清 project_field_values
                cur.execute("DELETE FROM fields WHERE id=?", (fid,))
                n_deleted += 1

            self.conn.commit()
            return new_ids, n_deleted, n_renamed
        except Exception:
            self.conn.rollback()
            raise

    def set_field_prompt_hint(self, fid: int, prompt_hint: str) -> None:
        self.conn.execute(
            "UPDATE fields SET prompt_hint=? WHERE id=?",
            (prompt_hint or "", fid),
        )
        self.conn.commit()

    def rename_field(self, fid: int, new_name: str) -> None:
        self.conn.execute("UPDATE fields SET name=? WHERE id=?", (new_name.strip(), fid))
        self.conn.commit()

    def set_field_type(
        self,
        fid: int,
        ftype: str,
        *,
        supersede_pending_suggestions: bool = False,
        clear_prompt_hint: bool = False,
    ) -> None:
        """修改字段类型。task #19 Phase A 起支持两个可选副作用开关。

        - ``supersede_pending_suggestions=True``：把该 fid 所有 ``status='pending'``
          的 ``project_field_suggestions`` 标记为 ``superseded`` —— 因为旧建议
          的 ``suggested_value`` 是按旧类型语义生成的字符串，新类型下接受它会
          污染数据
        - ``clear_prompt_hint=True``：同时把 ``fields.prompt_hint`` 清空 ——
          原 hint（如"格式：YYYY-MM-DD"）跟新类型大概率不匹配

        三件事在同一事务，避免中途崩溃留下不一致状态。受保护字段（标题/描述/
        标签）静默忽略（与历史行为一致）。
        """
        f = self.get_field(fid)
        if f is None or f.is_required:
            return  # 保护字段类型固定，静默忽略
        if f.type == ftype:
            return  # 无变化，跳过；避免无谓事务与 supersede 误伤
        cur = self.conn.cursor()
        try:
            cur.execute("BEGIN")
            cur.execute("UPDATE fields SET type=? WHERE id=?", (ftype, fid))
            if clear_prompt_hint:
                cur.execute(
                    "UPDATE fields SET prompt_hint='' WHERE id=?", (fid,)
                )
            if supersede_pending_suggestions:
                cur.execute(
                    "UPDATE project_field_suggestions "
                    "SET status='superseded', resolved_at=datetime('now') "
                    "WHERE field_id=? AND status='pending'",
                    (fid,),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def set_field_visible(self, fid: int, visible: bool) -> None:
        self.conn.execute(
            "UPDATE fields SET visible=? WHERE id=?", (1 if visible else 0, fid)
        )
        self.conn.commit()

    def reorder_fields(self, ordered_ids: list[int]) -> None:
        cur = self.conn.cursor()
        for i, fid in enumerate(ordered_ids):
            cur.execute("UPDATE fields SET ord=? WHERE id=?", (i, fid))
        self.conn.commit()

    def delete_field(
        self,
        fid: int,
        *,
        append_to_description: bool = False,
    ) -> None:
        """删除字段定义。

        title 字段不允许删除。
        - append_to_description=True：把每个项目的该字段值追加到 description_md 末尾后删除
        - append_to_description=False：直接删除（CASCADE 清理用户字段值；系统字段对应列清空）
        """
        f = self.get_field(fid)
        if f is None:
            return
        if f.is_required:
            raise ValueError(f"『{f.name}』字段不可删除")

        # 收集所有项目当前的该字段值，方便统一处理
        rows = self._collect_field_values_for_all_projects(f)

        cur = self.conn.cursor()
        if append_to_description:
            for project_id, value in rows:
                if not value:
                    continue
                desc_row = cur.execute(
                    "SELECT description_md FROM projects WHERE id=?", (project_id,)
                ).fetchone()
                desc = (desc_row["description_md"] or "") if desc_row else ""
                appendix = f"\n\n**{f.name}**：{value}"
                new_desc = (desc.rstrip() + appendix).lstrip("\n")
                cur.execute(
                    "UPDATE projects SET description_md=?, "
                    "updated_at=datetime('now') WHERE id=?",
                    (new_desc, project_id),
                )

        # 若是系统字段，需要清空对应列
        if f.is_system and f.key in self.SYSTEM_FIELD_COLUMNS:
            col = self.SYSTEM_FIELD_COLUMNS[f.key]
            default = 0 if f.key == "rating" else ""
            cur.execute(f"UPDATE projects SET {col}=?", (default,))

        # 删除字段定义（CASCADE 会同时删除 project_field_values 中的相关行）
        cur.execute("DELETE FROM fields WHERE id=?", (fid,))
        self.conn.commit()

    def _collect_field_values_for_all_projects(
        self, f: Field
    ) -> list[tuple[int, str]]:
        """返回 [(project_id, value), ...]，空值跳过。"""
        out: list[tuple[int, str]] = []
        if f.is_system and f.key in self.SYSTEM_FIELD_COLUMNS:
            col = self.SYSTEM_FIELD_COLUMNS[f.key]
            rows = self.conn.execute(
                f"SELECT id, {col} AS v FROM projects WHERE {col} IS NOT NULL AND {col} != ''"
            ).fetchall()
            for r in rows:
                v = r["v"]
                if isinstance(v, int):
                    v = str(v) if v else ""
                if v:
                    out.append((r["id"], str(v)))
        else:
            rows = self.conn.execute(
                "SELECT project_id, value FROM project_field_values "
                "WHERE field_id=? AND value IS NOT NULL AND value != ''",
                (f.id,),
            ).fetchall()
            for r in rows:
                out.append((r["project_id"], r["value"]))
        return out

    def count_field_filled(self, f: Field) -> int:
        return len(self._collect_field_values_for_all_projects(f))

    # ------------------------------------------------------------------ field values (统一接口)
    def get_field_value(self, project: Project, f: Field) -> str:
        """从 Project 对象按字段抽取值（系统字段读列，用户字段读 field_values dict）。
        tags 字段返回逗号分隔的字符串。"""
        if f.is_system:
            if f.key == "title":
                return project.title
            if f.key == "author":
                return project.author
            if f.key == "date":
                return project.date
            if f.key == "source_url":
                return project.source_url
            if f.key == "rating":
                return str(project.rating) if project.rating else ""
            if f.key == "description":
                return project.description_md
            if f.key == "tags":
                return ", ".join(project.tags)
            return ""
        if f.id is None:
            return ""
        return project.field_values.get(f.id, "")

    def set_field_value_on_project(self, project: Project, f: Field, value: str) -> None:
        """把字段值写回 Project 对象（在 save_project 之前调用）。
        tags 字段：用逗号/中文逗号分隔。"""
        if f.is_system:
            if f.key == "title":
                project.title = value
            elif f.key == "author":
                project.author = value
            elif f.key == "date":
                project.date = value
            elif f.key == "source_url":
                project.source_url = value
            elif f.key == "rating":
                try:
                    project.rating = int(value) if value else 0
                except ValueError:
                    project.rating = 0
            elif f.key == "description":
                project.description_md = value
            elif f.key == "tags":
                # 兼容半/全角逗号、分号
                import re
                parts = [t.strip() for t in re.split(r"[,，;；]", value or "")]
                project.tags = [t for t in parts if t]
        else:
            if f.id is not None:
                v = (value or "").strip()
                if v:
                    project.field_values[f.id] = v
                else:
                    project.field_values.pop(f.id, None)

    # 旧版接口（用户字段值的低层读写，保留）
    def get_field_values(self, pid: int) -> dict[int, str]:
        rows = self.conn.execute(
            "SELECT field_id, value FROM project_field_values WHERE project_id=?",
            (pid,),
        ).fetchall()
        return {r["field_id"]: (r["value"] or "") for r in rows}

    def _set_field_values(self, pid: int, values: dict[int, str]) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM project_field_values WHERE project_id=?", (pid,))
        for fid, v in values.items():
            v = (v or "").strip()
            if not v:
                continue
            cur.execute(
                "INSERT INTO project_field_values(project_id, field_id, value) "
                "VALUES(?, ?, ?)",
                (pid, fid, v),
            )

    # ------------------------------------------------------------------ files
    def list_files(self, pid: int) -> list[FileItem]:
        rows = self.conn.execute(
            "SELECT * FROM files WHERE project_id=? ORDER BY ord, id",
            (pid,),
        ).fetchall()
        return [self._row_to_file(r) for r in rows]

    def get_file(self, fid: int) -> Optional[FileItem]:
        row = self.conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
        return self._row_to_file(row) if row else None

    def add_file(self, f: FileItem) -> int:
        cur = self.conn.execute(
            """INSERT INTO files(project_id, path, is_relative, label, kind, ord)
               VALUES(?,?,?,?,?,?)""",
            (f.project_id, f.path, int(f.is_relative), f.label, f.kind, f.ord),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update_file(self, f: FileItem) -> None:
        self.conn.execute(
            """UPDATE files SET path=?, is_relative=?, label=?, kind=?, ord=?
               WHERE id=?""",
            (f.path, int(f.is_relative), f.label, f.kind, f.ord, f.id),
        )
        self.conn.commit()

    def delete_file(self, fid: int) -> None:
        self.conn.execute("DELETE FROM files WHERE id=?", (fid,))
        # 若该文件是某项目封面，置空封面
        self.conn.execute(
            "UPDATE projects SET cover_file_id=NULL WHERE cover_file_id=?", (fid,)
        )
        self.conn.commit()

    def reorder_files(self, ordered_ids: list[int]) -> None:
        cur = self.conn.cursor()
        for i, fid in enumerate(ordered_ids):
            cur.execute("UPDATE files SET ord=? WHERE id=?", (i, fid))
        self.conn.commit()

    def set_file_missing(self, fid: int, missing: bool) -> None:
        """标记/取消文件缺失标记（task #14 T1）。"""
        self.conn.execute(
            "UPDATE files SET missing=? WHERE id=?",
            (1 if missing else 0, fid),
        )
        self.conn.commit()

    def clear_all_missing_flags(self) -> int:
        """清空所有 missing 标记（一致性检查重新跑时用），返回受影响行数。"""
        cur = self.conn.execute(
            "UPDATE files SET missing=0 WHERE missing=1"
        )
        self.conn.commit()
        return cur.rowcount or 0


    # ------------------------------------------------------------------ settings
    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ project_settings
    def get_project_setting(
        self, project_id: int, key: str, default: str = "",
    ) -> str:
        row = self.conn.execute(
            "SELECT value FROM project_settings WHERE project_id=? AND key=?",
            (project_id, key),
        ).fetchone()
        return row["value"] if row else default

    def set_project_setting(self, project_id: int, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO project_settings(project_id, key, value) VALUES(?,?,?) "
            "ON CONFLICT(project_id, key) DO UPDATE SET value=excluded.value",
            (project_id, key, value),
        )
        self.conn.commit()

    # ============================================================ LLM tasks
    def create_llm_task(
        self, project_id: Optional[int], project_title: str, ttype: str,
        payload_json: str, provider: str, model: str,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO llm_tasks(project_id, project_title, type, status, "
            "payload_json, provider, model) VALUES(?,?,?,'queued',?,?,?)",
            (project_id, project_title, ttype, payload_json, provider, model),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update_llm_task_status(
        self, tid: int, status: str,
        *,
        result_json: Optional[str] = None,
        error: Optional[str] = None,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
    ) -> None:
        sets = ["status=?"]
        params: list = [status]
        if status == "running":
            sets.append("started_at=datetime('now')")
        if status in ("done", "failed", "cancelled"):
            sets.append("finished_at=datetime('now')")
        if result_json is not None:
            sets.append("result_json=?"); params.append(result_json)
        if error is not None:
            sets.append("error=?"); params.append(error)
        if tokens_in is not None:
            sets.append("tokens_in=?"); params.append(tokens_in)
        if tokens_out is not None:
            sets.append("tokens_out=?"); params.append(tokens_out)
        params.append(tid)
        self.conn.execute(
            f"UPDATE llm_tasks SET {', '.join(sets)} WHERE id=?", params,
        )
        self.conn.commit()

    def list_llm_tasks(self, limit: int = 100) -> list[LLMTask]:
        rows = self.conn.execute(
            "SELECT * FROM llm_tasks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_llm_task(r) for r in rows]

    def count_llm_tasks_active(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM llm_tasks WHERE status IN ('queued','running')"
        ).fetchone()
        return row["c"]

    def trim_llm_tasks(self, keep: int = 100) -> None:
        """只保留最近 N 条已完成/失败任务（活动任务永远保留）。"""
        self.conn.execute(
            "DELETE FROM llm_tasks WHERE id IN ("
            " SELECT id FROM llm_tasks "
            " WHERE status NOT IN ('queued','running') "
            " ORDER BY id DESC LIMIT -1 OFFSET ?"
            ")", (keep,)
        )
        self.conn.commit()

    @staticmethod
    def _row_to_llm_task(r: sqlite3.Row) -> LLMTask:
        return LLMTask(
            id=r["id"], project_id=r["project_id"],
            project_title=r["project_title"] or "",
            type=r["type"] or "meta_suggest",
            status=r["status"] or "queued",
            payload_json=r["payload_json"] or "",
            result_json=r["result_json"] or "",
            error=r["error"] or "",
            provider=r["provider"] or "",
            model=r["model"] or "",
            tokens_in=r["tokens_in"] or 0,
            tokens_out=r["tokens_out"] or 0,
            created_at=r["created_at"] or "",
            started_at=r["started_at"] or "",
            finished_at=r["finished_at"] or "",
        )

    # ============================================================ Field suggestions
    def add_suggestions(
        self, project_id: int, source_task_id: int,
        items: list[tuple[int, str]],
    ) -> int:
        """批量插入 (field_id, value) 建议；老的 pending 标记 superseded。
        返回插入条数。"""
        cur = self.conn.cursor()
        # 先把同一项目里仍 pending 的同字段建议设为 superseded
        for fid, _ in items:
            cur.execute(
                "UPDATE project_field_suggestions SET status='superseded', "
                "resolved_at=datetime('now') WHERE project_id=? AND field_id=? "
                "AND status='pending'",
                (project_id, fid),
            )
        n = 0
        for fid, value in items:
            value = (value or "").strip()
            if not value:
                continue
            cur.execute(
                "INSERT INTO project_field_suggestions"
                "(project_id, field_id, suggested_value, source_task_id, status) "
                "VALUES(?,?,?,?,'pending')",
                (project_id, fid, value, source_task_id),
            )
            n += 1
        self.conn.commit()
        return n

    def list_pending_suggestions(self, project_id: int) -> list[FieldSuggestion]:
        rows = self.conn.execute(
            "SELECT * FROM project_field_suggestions "
            "WHERE project_id=? AND status='pending' ORDER BY field_id",
            (project_id,),
        ).fetchall()
        return [self._row_to_suggestion(r) for r in rows]

    def resolve_suggestion(self, sid: int, status: str) -> None:
        """status: applied / rejected"""
        if status not in ("applied", "rejected"):
            return
        self.conn.execute(
            "UPDATE project_field_suggestions SET status=?, "
            "resolved_at=datetime('now') WHERE id=?",
            (status, sid),
        )
        self.conn.commit()

    def resolve_all_suggestions(self, project_id: int, status: str) -> None:
        if status not in ("applied", "rejected"):
            return
        self.conn.execute(
            "UPDATE project_field_suggestions SET status=?, "
            "resolved_at=datetime('now') WHERE project_id=? AND status='pending'",
            (status, project_id),
        )
        self.conn.commit()

    def count_projects_with_pending_suggestions(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT project_id) AS c FROM project_field_suggestions "
            "WHERE status='pending'"
        ).fetchone()
        return row["c"]

    def list_projects_pending_review(self) -> list[Project]:
        rows = self.conn.execute(
            "SELECT p.* FROM projects p "
            "WHERE EXISTS ("
            " SELECT 1 FROM project_field_suggestions s "
            " WHERE s.project_id=p.id AND s.status='pending'"
            ") ORDER BY p.updated_at DESC, p.id DESC"
        ).fetchall()
        return self._attach_project_extras(
            [self._row_to_project(r) for r in rows]
        )

    @staticmethod
    def _row_to_suggestion(r: sqlite3.Row) -> FieldSuggestion:
        return FieldSuggestion(
            id=r["id"], project_id=r["project_id"], field_id=r["field_id"],
            suggested_value=r["suggested_value"] or "",
            source_task_id=r["source_task_id"],
            status=r["status"] or "pending",
            created_at=r["created_at"] or "",
            resolved_at=r["resolved_at"] or "",
        )

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _row_to_project(row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            title=row["title"] or "",
            author=row["author"] or "",
            date=row["date"] or "",
            source_url=row["source_url"] or "",
            rating=row["rating"] or 0,
            description_md=row["description_md"] or "",
            storage_mode=row["storage_mode"] or "link",
            cover_file_id=row["cover_file_id"],
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    @staticmethod
    def _row_to_file(row: sqlite3.Row) -> FileItem:
        # missing 列在 v3 → v4 才加上；老 db 用 try/except 兼容（理论上迁移已跑）
        try:
            missing_v = bool(row["missing"])
        except (IndexError, KeyError):
            missing_v = False
        return FileItem(
            id=row["id"],
            project_id=row["project_id"],
            path=row["path"],
            is_relative=bool(row["is_relative"]),
            label=row["label"] or "",
            kind=row["kind"] or "other",
            ord=row["ord"] or 0,
            added_at=row["added_at"] or "",
            missing=missing_v,
        )
