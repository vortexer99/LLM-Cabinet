"""数据访问层（CRUD）。所有 UI 通过 Repository 与数据库交互。"""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional

from .models import Field, FieldSuggestion, FileItem, LLMTask, PendingFile, Project
from .search import AndNode, NotNode, OrNode, SearchNode, TermNode


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
            where, ps = self._compile_keyword_search(keyword)
            sql += f" AND ({where})"
            params += ps
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

    def list_projects_query(self, ast: SearchNode | None) -> list[Project]:
        """按 Phase B 搜索 AST 列出项目。

        解析器只负责把字符串变成 AST；这里负责字段解析与参数化 SQL。
        所有标签条件都用 EXISTS，保证 ``tag:A AND tag:B`` 能匹配同时拥有
        两个标签的项目。
        """
        sql = "SELECT p.* FROM projects p"
        params: list = []
        if ast is not None:
            where, params = self._compile_search_node(ast)
            sql += f" WHERE {where}"
        sql += " ORDER BY p.updated_at DESC, p.id DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return self._attach_project_extras(
            [self._row_to_project(r) for r in rows]
        )

    def _compile_search_node(self, node: SearchNode) -> tuple[str, list]:
        """递归编译搜索 AST，返回 ``(sql片段, 参数列表)``。"""
        if isinstance(node, AndNode):
            parts: list[str] = []
            params: list = []
            for item in node.items:
                sql, ps = self._compile_search_node(item)
                parts.append(f"({sql})")
                params.extend(ps)
            return " AND ".join(parts) if parts else "1=1", params
        if isinstance(node, OrNode):
            parts = []
            params = []
            for item in node.items:
                sql, ps = self._compile_search_node(item)
                parts.append(f"({sql})")
                params.extend(ps)
            return " OR ".join(parts) if parts else "1=0", params
        if isinstance(node, NotNode):
            sql, params = self._compile_search_node(node.item)
            return f"NOT ({sql})", params
        if isinstance(node, TermNode):
            return self._compile_search_term(node)
        return "1=0", []

    def _compile_keyword_search(self, value: str) -> tuple[str, list]:
        """编译普通关键词搜索。

        普通关键词按用户直觉搜索项目的可见元数据与文件清单；文件正文全文搜索
        另需索引/抽取能力，不在这里做。
        """
        kw = f"%{value}%"
        return (
            "("
            "p.title LIKE ?"
            " OR p.description_md LIKE ?"
            " OR EXISTS ("
            "  SELECT 1 FROM project_tags pt_kw"
            "  JOIN tags t_kw ON t_kw.id = pt_kw.tag_id"
            "  WHERE pt_kw.project_id = p.id AND t_kw.name LIKE ?"
            " )"
            " OR EXISTS ("
            "  SELECT 1 FROM project_field_values pfv_kw"
            "  WHERE pfv_kw.project_id = p.id AND pfv_kw.value LIKE ?"
            " )"
            " OR EXISTS ("
            "  SELECT 1 FROM files f_kw"
            "  WHERE f_kw.project_id = p.id"
            "  AND (f_kw.path LIKE ? OR f_kw.label LIKE ? OR f_kw.subfolder LIKE ?)"
            " )"
            ")"
        ), [kw, kw, kw, kw, kw, kw, kw]

    def _compile_search_term(self, term: TermNode) -> tuple[str, list]:
        value = (term.value or "").strip()
        if not value:
            return "1=0", []

        if term.field is None:
            return self._compile_keyword_search(value)

        field = self._resolve_search_field(term.field)
        kind = field["kind"]
        if kind == "unknown":
            # 不抛给 UI；写错字段时返回空结果，避免误以为全库都命中。
            return "1=0", []
        if kind == "internal_untagged":
            return (
                "NOT EXISTS ("
                " SELECT 1 FROM project_tags pt WHERE pt.project_id = p.id"
                ")"
            ), []
        if kind == "tag":
            return self._compile_tag_search(term.op, value, prefix=False)
        if kind == "tag_prefix":
            return self._compile_tag_search(":", value, prefix=True)
        if kind == "project":
            return self._compile_project_column_search(field["column"], term.op, value)
        if kind == "field":
            return self._compile_field_value_search(
                int(field["field_id"]),
                str(field.get("type") or "text"),
                term.op,
                value,
            )
        return "1=0", []

    def _resolve_search_field(self, raw: str) -> dict:
        """按 task #03 规则解析字段名。"""
        name = (raw or "").strip()
        folded = name.casefold()

        protected = {
            "title": {"kind": "project", "column": "title", "type": "text"},
            "description": {"kind": "project", "column": "description_md", "type": "textarea"},
            "tag": {"kind": "tag"},
            "tags": {"kind": "tag"},
            # 下面是 UI/后端组合左侧筛选用的内部字段，不暴露给用户文档。
            "__tag_prefix": {"kind": "tag_prefix"},
            "__untagged": {"kind": "internal_untagged"},
        }
        if folded in protected:
            return protected[folded]

        fields = self.list_fields()
        for f in fields:
            if (f.key or "").casefold() == folded:
                if f.key == "title":
                    return {"kind": "project", "column": "title", "type": f.type}
                if f.key == "description":
                    return {"kind": "project", "column": "description_md", "type": f.type}
                if f.key == "tags":
                    return {"kind": "tag"}
                return {"kind": "field", "field_id": f.id, "type": f.type}
        for f in fields:
            if f.name == name:
                if f.key == "title":
                    return {"kind": "project", "column": "title", "type": f.type}
                if f.key == "description":
                    return {"kind": "project", "column": "description_md", "type": f.type}
                if f.key == "tags":
                    return {"kind": "tag"}
                return {"kind": "field", "field_id": f.id, "type": f.type}
        return {"kind": "unknown"}

    def _compile_project_column_search(
        self, column: str, op: str, value: str,
    ) -> tuple[str, list]:
        if column not in {"title", "description_md"}:
            return "1=0", []
        if op == ":":
            return f"p.{column} LIKE ?", [f"%{value}%"]
        if op == "=":
            return f"p.{column} = ?", [value]
        return "1=0", []

    def _compile_tag_search(
        self, op: str, value: str, *, prefix: bool,
    ) -> tuple[str, list]:
        if prefix:
            return (
                "EXISTS ("
                " SELECT 1 FROM project_tags pt"
                " JOIN tags t ON t.id = pt.tag_id"
                " WHERE pt.project_id = p.id"
                " AND (t.name = ? OR t.name LIKE ?)"
                ")"
            ), [value, f"{value}/%"]
        if op == ":":
            cmp_sql = "t.name LIKE ?"
            params = [f"%{value}%"]
        elif op == "=":
            cmp_sql = "t.name = ?"
            params = [value]
        else:
            return "1=0", []
        return (
            "EXISTS ("
            " SELECT 1 FROM project_tags pt"
            " JOIN tags t ON t.id = pt.tag_id"
            " WHERE pt.project_id = p.id"
            f" AND {cmp_sql}"
            ")"
        ), params

    def _compile_field_value_search(
        self, field_id: int, field_type: str, op: str, value: str,
    ) -> tuple[str, list]:
        if field_id <= 0:
            return "1=0", []
        if field_type in {"number", "rating"}:
            sql, params = self._field_numeric_condition(op, value)
        elif field_type == "date":
            sql, params = self._field_date_condition(op, value)
        else:
            sql, params = self._field_text_condition(op, value)
        if not sql:
            return "1=0", []
        return (
            "EXISTS ("
            " SELECT 1 FROM project_field_values pfv"
            " WHERE pfv.project_id = p.id"
            " AND pfv.field_id = ?"
            f" AND {sql}"
            ")"
        ), [field_id, *params]

    @staticmethod
    def _field_text_condition(op: str, value: str) -> tuple[str, list]:
        if op == ":":
            return "pfv.value LIKE ?", [f"%{value}%"]
        if op == "=":
            return "pfv.value = ?", [value]
        return "", []

    @staticmethod
    def _field_numeric_condition(op: str, value: str) -> tuple[str, list]:
        try:
            number = float(value)
        except ValueError:
            return "", []
        sql_op = "=" if op == ":" else op
        if sql_op not in {"=", ">", ">=", "<", "<="}:
            return "", []
        return f"CAST(pfv.value AS REAL) {sql_op} ?", [number]

    @staticmethod
    def _field_date_condition(op: str, value: str) -> tuple[str, list]:
        sql_op = "=" if op == ":" else op
        if sql_op not in {"=", ">", ">=", "<", "<="}:
            return "", []
        return f"pfv.value {sql_op} ?", [value]


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
                   (title, description_md, storage_mode, cover_file_id)
                   VALUES (?,?,?,?)""",
                (p.title, p.description_md, p.storage_mode, p.cover_file_id),
            )
            p.id = cur.lastrowid
        else:
            cur.execute(
                """UPDATE projects SET
                     title=?, description_md=?, storage_mode=?, cover_file_id=?,
                     updated_at=datetime('now')
                   WHERE id=?""",
                (p.title, p.description_md, p.storage_mode, p.cover_file_id,
                 p.id),
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
            where, ps = self._compile_keyword_search(keyword)
            sql += f" AND ({where})"
            params += ps
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
        # 清理不再被任何项目引用的孤儿标签
        cur.execute(
            "DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM project_tags)"
        )

    def batch_add_tag(self, project_ids: Iterable[int], tag: str) -> int:
        """为多个项目追加同一个标签，返回实际新增关联数量。"""
        tag = (tag or "").strip()
        ids: list[int] = []
        seen: set[int] = set()
        for raw in project_ids:
            try:
                pid = int(raw)
            except (TypeError, ValueError):
                continue
            if pid > 0 and pid not in seen:
                ids.append(pid)
                seen.add(pid)
        if not tag or not ids:
            return 0

        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"SELECT id FROM projects WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        existing_ids = [int(r["id"]) for r in rows]
        if not existing_ids:
            return 0

        cur = self.conn.cursor()
        try:
            cur.execute("BEGIN")
            cur.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag,))
            tag_id = cur.execute(
                "SELECT id FROM tags WHERE name=?", (tag,),
            ).fetchone()["id"]

            changed: list[int] = []
            for pid in existing_ids:
                cur.execute(
                    "INSERT OR IGNORE INTO project_tags(project_id, tag_id) VALUES(?,?)",
                    (pid, tag_id),
                )
                if cur.rowcount:
                    changed.append(pid)

            if changed:
                changed_placeholders = ",".join("?" for _ in changed)
                cur.execute(
                    f"UPDATE projects SET updated_at=datetime('now') "
                    f"WHERE id IN ({changed_placeholders})",
                    changed,
                )
            self.conn.commit()
            return len(changed)
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------------------ fields (schema)
    # task #20 schema v4 起：移除 SYSTEM_FIELD_COLUMNS dict。
    # 历史上 author/date/source_url/rating/description 的 key 用来 dispatch 到
    # projects 表的同名列。v4 起：
    # - title / description_md 仍存 projects 列，但它们 is_required=True，
    #   永远不会走 delete_field / count 路径
    # - tags 走独立 tags + project_tags 多对多表
    # - 其它一切字段值（含老 author/date/source_url/rating）统一存
    #   project_field_values
    # 因此 dispatch dict 已不再需要。
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
        type_changes: Optional[list[tuple[int, str, str]]] = None,
    ) -> tuple[list[int], int, int, int]:
        """库字段设计助手「应用」一次性事务（task #11 T3 全量规划）。

        五类操作放进同一个 BEGIN/COMMIT：
          - type_changes：UPDATE fields SET type=?, prompt_hint=? + supersede
            该 fid 的 pending suggestions（task #19 Phase B；
            type_conflict 路径批准时走这里）
          - renames：UPDATE fields SET name=? WHERE id=?（保留 fid，
            项目历史值通过 ``field_values`` 关联保持不动）
          - creates：新建用户字段（同 ``add_fields_batch``）
          - updates_hint：仅更新 prompt_hint
          - deletes：删除字段。task #20 schema v4 起所有非保护字段值都在
            project_field_values，CASCADE 自动清；受保护字段（title/description
            /tags）拒绝删除。

        执行顺序：type_changes → renames → creates → updates_hint → deletes。
        理由：
          - type_changes 先做，把 hint / type 一起写完，避免下游 updates_hint
            重复 UPDATE 同一 fid（调用方应避免把 type_change 的 fid 同时放进
            updates_hint，否则后者会覆盖前者；事务最终结果以 updates_hint 为准）
          - renames 在 creates 之前，避免"新建字段名"与"待改名旧名"撞上

        删除保护：
          - 受保护字段（``is_required``，即 标题/描述/标签）拒绝删除/改名/改类型，
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
            type_changes: ``[(field_id, new_type, new_prompt_hint), ...]``，
                字段助手 type_conflict 批准时使用。受保护字段静默跳过
                （不抛错，与 ``set_field_type`` 一致）。

        Returns:
            ``(new_ids, n_deleted, n_renamed, n_type_changed)``。任一失败 →
            ROLLBACK 抛原异常。
        """
        append_set: set[int] = set(append_for_fids or ())
        renames_list: list[tuple[int, str]] = list(renames or [])
        type_changes_list: list[tuple[int, str, str]] = list(type_changes or [])
        cur = self.conn.cursor()
        try:
            cur.execute("BEGIN")

            # -1) type_changes（先于其它一切；用 LLM 给的新 type + new hint 覆盖，
            #     并把该 fid 所有 pending suggestion 标 superseded）
            n_type_changed = 0
            if type_changes_list:
                from .models import PROTECTED_FIELD_KEYS
                for fid, new_type, new_hint in type_changes_list:
                    f_row = cur.execute(
                        "SELECT key FROM fields WHERE id=?", (fid,),
                    ).fetchone()
                    if f_row is None:
                        continue  # 不存在的 fid 静默跳过
                    if f_row["key"] in PROTECTED_FIELD_KEYS:
                        # 受保护字段类型固定，静默跳过（与 set_field_type 一致）
                        continue
                    cur.execute(
                        "UPDATE fields SET type=?, prompt_hint=? WHERE id=?",
                        (new_type, new_hint or "", fid),
                    )
                    cur.execute(
                        "UPDATE project_field_suggestions "
                        "SET status='superseded', resolved_at=datetime('now') "
                        "WHERE field_id=? AND status='pending'",
                        (fid,),
                    )
                    n_type_changed += 1

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
                # 系统非必有字段（作者/日期/评分/来源）：
                # task #20 schema v4 起，这些字段值统一存在
                # project_field_values，CASCADE 会自动清；不再需要单独清空
                # projects 表的列（4 列已经在迁移里 DROP 了）
                # 受保护字段（title/description/tags）已在上面拒绝，不会到这里
                cur.execute("DELETE FROM fields WHERE id=?", (fid,))
                n_deleted += 1

            self.conn.commit()
            return new_ids, n_deleted, n_renamed, n_type_changed
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

        受保护字段（title/description/tags）不允许删除。
        - append_to_description=True：把每个项目的该字段值追加到 description_md 末尾后删除
        - append_to_description=False：直接删除（CASCADE 清理 project_field_values）

        task #20 schema v4 起，所有非保护字段值都存 project_field_values，
        CASCADE 自动清；不再需要清空 projects 表的列。
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

        # 删除字段定义（CASCADE 自动删除 project_field_values 中的相关行）
        cur.execute("DELETE FROM fields WHERE id=?", (fid,))
        self.conn.commit()

    def _collect_field_values_for_all_projects(
        self, f: Field
    ) -> list[tuple[int, str]]:
        """返回 [(project_id, value), ...]，空值跳过。

        task #20 schema v4 起的存储分布：
        - title / description_md：仍存 projects 列（受保护字段）
        - tags：独立 tags + project_tags 多对多表（受保护字段）
        - 其它一切字段（含老 author/date/source_url/rating）：project_field_values
        """
        out: list[tuple[int, str]] = []
        if f.is_required:
            # 受保护字段不会被 delete_field 删，但 count_field_filled 可能被调用
            if f.key == "title":
                rows = self.conn.execute(
                    "SELECT id, title AS v FROM projects "
                    "WHERE title IS NOT NULL AND title != ''"
                ).fetchall()
                for r in rows:
                    out.append((r["id"], r["v"]))
                return out
            if f.key == "description":
                rows = self.conn.execute(
                    "SELECT id, description_md AS v FROM projects "
                    "WHERE description_md IS NOT NULL AND description_md != ''"
                ).fetchall()
                for r in rows:
                    out.append((r["id"], r["v"]))
                return out
            if f.key == "tags":
                rows = self.conn.execute(
                    """SELECT pt.project_id AS pid, GROUP_CONCAT(t.name, ', ') AS v
                       FROM project_tags pt
                       JOIN tags t ON t.id = pt.tag_id
                       GROUP BY pt.project_id"""
                ).fetchall()
                for r in rows:
                    if r["v"]:
                        out.append((r["pid"], r["v"]))
                return out
        # 普通字段（含老 author/date/source_url/rating） + 用户自定义字段
        if f.id is None:
            return out
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
        """从 Project 对象按字段抽取值。

        task #20 schema v4 起的存储分布：
        - title / description_md：projects 顶层属性
        - tags：project.tags 列表（返回逗号分隔字符串）
        - 其它一切字段（含老 author/date/source_url/rating）：project.field_values dict
        """
        if f.is_required:
            if f.key == "title":
                return project.title
            if f.key == "description":
                return project.description_md
            if f.key == "tags":
                return ", ".join(project.tags)
        if f.id is None:
            return ""
        return project.field_values.get(f.id, "")

    def set_field_value_on_project(self, project: Project, f: Field, value: str) -> None:
        """把字段值写回 Project 对象（在 save_project 之前调用）。
        tags 字段：用逗号/中文逗号分隔。

        task #20 schema v4 起：除受保护字段（title/description_md/tags）外，
        所有字段值都写入 project.field_values dict（含老 author/date/source_url/rating）。
        """
        if f.is_required:
            if f.key == "title":
                project.title = value
                return
            if f.key == "description":
                project.description_md = value
                return
            if f.key == "tags":
                # 兼容半/全角逗号、分号
                import re
                parts = [t.strip() for t in re.split(r"[,，;；]", value or "")]
                project.tags = [t for t in parts if t]
                return
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

    def list_files_under_subfolder(
        self, project_id: int, subfolder: str, *, missing_only: bool = False,
    ) -> list[FileItem]:
        """列出逻辑文件夹及其子层级下的文件（task #29 T3c）。"""
        subfolder = (subfolder or "").strip("/")
        if not subfolder:
            return []
        prefix = subfolder + "/"
        files = [
            f for f in self.list_files(project_id)
            if (f.subfolder or "") == subfolder
            or (f.subfolder or "").startswith(prefix)
        ]
        if missing_only:
            files = [f for f in files if f.missing]
        return files

    def get_file(self, fid: int) -> Optional[FileItem]:
        row = self.conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
        return self._row_to_file(row) if row else None

    def add_file(self, f: FileItem) -> int:
        cur = self.conn.execute(
            """INSERT INTO files(project_id, path, is_relative, label, kind, ord, subfolder, origin)
               VALUES(?,?,?,?,?,?,?,?)""",
            (f.project_id, f.path, int(f.is_relative), f.label, f.kind, f.ord, f.subfolder, f.origin),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update_file(self, f: FileItem) -> None:
        self.conn.execute(
            """UPDATE files SET path=?, is_relative=?, label=?, kind=?, ord=?, subfolder=?
               WHERE id=?""",
            (f.path, int(f.is_relative), f.label, f.kind, f.ord, f.subfolder, f.id),
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

    def set_file_subfolder(self, fid: int, subfolder: str) -> None:
        """更新单个文件的逻辑子目录（task #31a）。"""
        self.conn.execute(
            "UPDATE files SET subfolder=? WHERE id=?",
            (subfolder, fid),
        )
        self.conn.commit()

    def rename_subfolder(self, project_id: int, old: str, new: str) -> int:
        """重命名项目内的逻辑子目录，并同步其所有子层级。

        返回受影响的文件行数。``old='docs'`` 改为 ``notes`` 时，
        ``docs/a`` 也会同步变为 ``notes/a``。
        """
        old = (old or "").strip("/")
        new = (new or "").strip("/")
        if not old or old == new:
            return 0

        rows = self.conn.execute(
            "SELECT id, subfolder FROM files WHERE project_id=?",
            (project_id,),
        ).fetchall()
        changed: list[tuple[str, int]] = []
        prefix = old + "/"
        for r in rows:
            sf = r["subfolder"] or ""
            if sf == old:
                changed.append((new, r["id"]))
            elif sf.startswith(prefix):
                changed.append((new + sf[len(old):], r["id"]))

        cur = self.conn.cursor()
        for sf, fid in changed:
            cur.execute("UPDATE files SET subfolder=? WHERE id=?", (sf, fid))
        self.conn.commit()
        return len(changed)

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

    # ------------------------------------------------------------------ mcp audit / modified tasks (#24)
    def count_mcp_audit(
        self,
        client_name: str = "",
        tool_name: str = "",
        result_status: str = "",
    ) -> int:
        """统计 MCP 审计记录数（支持筛选）。"""
        sql = "SELECT COUNT(*) AS c FROM mcp_audit WHERE 1=1"
        params: list = []
        if client_name:
            sql += " AND client_name = ?"
            params.append(client_name)
        if tool_name:
            sql += " AND tool_name = ?"
            params.append(tool_name)
        if result_status:
            sql += " AND result_status = ?"
            params.append(result_status)
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else 0

    def list_mcp_audit(
        self,
        offset: int = 0,
        limit: int = 50,
        client_name: str = "",
        tool_name: str = "",
        result_status: str = "",
    ) -> list[dict]:
        """分页获取 MCP 审计记录。"""
        sql = (
            "SELECT id, ts, client_name, tool_name, arguments_json, "
            "result_status, error_message "
            "FROM mcp_audit WHERE 1=1"
        )
        params: list = []
        if client_name:
            sql += " AND client_name = ?"
            params.append(client_name)
        if tool_name:
            sql += " AND tool_name = ?"
            params.append(tool_name)
        if result_status:
            sql += " AND result_status = ?"
            params.append(result_status)
        sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = self.conn.execute(sql, params).fetchall()
        cols = ["id", "ts", "client_name", "tool_name", "arguments_json", "result_status", "error_message"]
        return [{c: r[i] for i, c in enumerate(cols)} for r in rows]

    def mark_project_mcp_modified(self, project_id: int) -> None:
        """更新项目的 mcp_modified_at 为当前时间。"""
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "UPDATE projects SET mcp_modified_at = ? WHERE id = ?",
            (now, project_id),
        )
        self.conn.commit()

    def clear_project_mcp_modified(self, project_id: int) -> None:
        """清除项目的 MCP 修改标记（用户已了解该修改）。"""
        self.conn.execute(
            "UPDATE projects SET mcp_modified_at = NULL WHERE id = ?",
            (project_id,),
        )
        self.conn.commit()

    def list_mcp_modified_projects(self) -> list[dict]:
        """列出被 MCP 修改过的项目（按最近修改时间倒序）。"""
        rows = self.conn.execute(
            "SELECT id, title, mcp_modified_at "
            "FROM projects WHERE mcp_modified_at IS NOT NULL "
            "ORDER BY mcp_modified_at DESC"
        ).fetchall()
        return [
            {"id": r[0], "title": r[1], "mcp_modified_at": r[2]}
            for r in rows
        ]

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _row_to_project(row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            title=row["title"] or "",
            description_md=row["description_md"] or "",
            storage_mode=row["storage_mode"] or "link",
            cover_file_id=row["cover_file_id"],
            mcp_modified_at=row["mcp_modified_at"],  # task #24
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
        # subfolder 列在 v6 → v7 才加上
        try:
            subfolder_v = row["subfolder"] or ""
        except (IndexError, KeyError):
            subfolder_v = ""
        # origin 列在 v7 → v8 才加上
        try:
            origin_v = row["origin"] or "user"
        except (IndexError, KeyError):
            origin_v = "user"
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
            subfolder=subfolder_v,
            origin=origin_v,
        )
