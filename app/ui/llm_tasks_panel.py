"""LLM 任务面板：运行中 + 历史。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..repository import Repository
from .dialogs import confirm, info
from ..utils import utc_to_local_str


class LLMTasksDialog(QDialog):
    # 重新应用建议到项目后发出，主窗口可据此刷新待审阅 / 列表
    suggestions_reapplied = Signal(int, int)   # (project_id, count)

    def __init__(self, repo: Repository, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.setWindowTitle("LLM 任务")
        self.resize(720, 540)

        v = QVBoxLayout(self)
        v.setSpacing(8)

        title = QLabel("LLM 任务队列")
        title.setProperty("h1", True)
        v.addWidget(title)

        # 表
        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(["项目", "类型", "状态", "Tokens", "时间"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setShowGrid(False)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.tbl.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.tbl.verticalHeader().setDefaultSectionSize(28)
        v.addWidget(self.tbl, 1)

        # 错误详情区
        self.lbl_detail = QLabel("")
        self.lbl_detail.setWordWrap(True)
        self.lbl_detail.setProperty("hint", True)
        self.lbl_detail.setMinimumHeight(40)
        v.addWidget(self.lbl_detail)

        bb = QDialogButtonBox()
        b_refresh = QPushButton("⟳ 刷新")
        b_refresh.clicked.connect(self.reload)
        bb.addButton(b_refresh, QDialogButtonBox.ActionRole)
        self._b_view_prompt = QPushButton("🔍 查看 Prompt / 原始响应")
        self._b_view_prompt.setEnabled(False)
        self._b_view_prompt.clicked.connect(self._show_prompt_dialog)
        bb.addButton(self._b_view_prompt, QDialogButtonBox.ActionRole)
        self._b_reapply = QPushButton("♻ 再次应用此任务的建议")
        self._b_reapply.setEnabled(False)
        self._b_reapply.clicked.connect(self._reapply_suggestions)
        bb.addButton(self._b_reapply, QDialogButtonBox.ActionRole)
        bb.addButton(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self.tbl.itemSelectionChanged.connect(self._on_sel)
        self.reload()

    def reload(self) -> None:
        tasks = self.repo.list_llm_tasks(limit=200)
        # 排序：运行中/排队 优先，按时间倒序
        order = {"running": 0, "queued": 1, "failed": 2, "done": 3, "cancelled": 4}
        tasks.sort(key=lambda t: (order.get(t.status, 9), -(t.id or 0)))
        kind_label = {"meta_suggest": "LLM 元数据建议"}
        status_label = {
            "queued": "排队中", "running": "运行中",
            "done": "✓ 完成", "failed": "✗ 失败",
            "cancelled": "已取消", "superseded": "被覆盖",
        }
        self.tbl.setRowCount(len(tasks))
        for r, t in enumerate(tasks):
            it_proj = QTableWidgetItem(t.project_title or "(已删除)")
            it_proj.setData(Qt.UserRole, t.id)
            self.tbl.setItem(r, 0, it_proj)
            self.tbl.setItem(r, 1, QTableWidgetItem(kind_label.get(t.type, t.type)))
            it_st = QTableWidgetItem(status_label.get(t.status, t.status))
            self.tbl.setItem(r, 2, it_st)
            tk = ""
            if t.status == "done":
                tk = f"{t.tokens_in}↑ / {t.tokens_out}↓"
            self.tbl.setItem(r, 3, QTableWidgetItem(tk))
            ts = t.finished_at or t.started_at or t.created_at or ""
            self.tbl.setItem(r, 4, QTableWidgetItem(utc_to_local_str(ts)))

    def _on_sel(self) -> None:
        r = self.tbl.currentRow()
        if r < 0:
            self.lbl_detail.setText("")
            self._b_view_prompt.setEnabled(False)
            self._b_reapply.setEnabled(False)
            return
        it = self.tbl.item(r, 0)
        if not it:
            return
        tid = it.data(Qt.UserRole)
        # 重新查
        tasks = self.repo.list_llm_tasks(limit=500)
        t = next((x for x in tasks if x.id == tid), None)
        if not t:
            self.lbl_detail.setText("")
            self._b_view_prompt.setEnabled(False)
            self._b_reapply.setEnabled(False)
            return
        # 「查看 Prompt」仅对完成/失败的任务可用（有数据可看）
        self._b_view_prompt.setEnabled(t.status in ("done", "failed"))
        self._current_task = t
        # 「再次应用」仅对 done 且有 suggestions 且项目仍存在时可用
        can_reapply = False
        if t.status == "done" and t.project_id is not None:
            try:
                import json as _json
                blob = _json.loads(t.result_json or "null")
                sug = (blob or {}).get("suggestions") if isinstance(blob, dict) else None
                if isinstance(sug, dict) and sug and self.repo.get_project(t.project_id):
                    can_reapply = True
            except Exception:
                pass
        self._b_reapply.setEnabled(can_reapply)
        if t.status == "failed":
            self.lbl_detail.setText(
                f"<span style='color:#fa5252'>错误：</span>{t.error}"
            )
        elif t.status == "done":
            # 兼容新旧两种 result_json 结构
            blob: dict | None = None
            try:
                import json as _json
                blob = _json.loads(t.result_json or "null")
            except Exception:
                blob = None
            if isinstance(blob, dict) and "suggestions" in blob:
                sug = blob.get("suggestions") or {}
                raw = blob.get("raw_text") or ""
                applicable = blob.get("applicable_count", 0)
                if sug:
                    pretty = "<br>".join(
                        f"<b>{k}：</b>{(v or '')[:120]}"
                        for k, v in sug.items()
                    )
                    self.lbl_detail.setText(
                        f"<b>建议字段（{len(sug)} 项，可应用 {applicable} 项）：</b><br>{pretty}"
                    )
                else:
                    # 解析为空：展示原始响应辅助排查
                    raw_short = (raw or "(空响应)")[:400]
                    self.lbl_detail.setText(
                        "<span style='color:#fab005'>未解析出任何字段建议。</span>"
                        f"<br><b>原始响应：</b><br>{raw_short}"
                    )
            else:
                # 旧格式：直接是字段字典
                preview = (t.result_json or "")[:400]
                self.lbl_detail.setText(f"<b>结果：</b>{preview}")
        else:
            self.lbl_detail.setText(f"任务 #{t.id}  ·  {t.provider}/{t.model}")
        self.lbl_detail.setTextFormat(Qt.RichText)

    def _show_prompt_dialog(self) -> None:
        t = getattr(self, "_current_task", None)
        if t is None:
            return
        import json as _json
        try:
            blob = _json.loads(t.result_json or "null")
        except Exception:
            blob = None

        sections: list[str] = []
        sections.append(f"任务 #{t.id}  ·  {t.provider}/{t.model}  ·  状态: {t.status}")
        sections.append(f"创建: {utc_to_local_str(t.created_at)}    "
                        f"完成: {utc_to_local_str(t.finished_at)}")
        if t.error:
            sections.append(f"\n[错误]\n{t.error}")
        if isinstance(blob, dict):
            target_names = blob.get("target_field_names") or []
            ref_files = blob.get("ref_files") or []
            user_prompt = blob.get("user_prompt") or ""
            raw_text = blob.get("raw_text") or ""
            sug = blob.get("suggestions") or {}

            sections.append(f"\n[请求字段] ({len(target_names)})\n" + ", ".join(target_names))
            sections.append(f"\n[参考文件] ({len(ref_files)})\n" +
                            ("\n".join(ref_files) if ref_files else "(无)"))
            sections.append("\n[User Prompt]\n" + (user_prompt or "(空)"))
            sections.append("\n[原始响应]\n" + (raw_text or "(空)"))
            sections.append("\n[解析后建议]\n" +
                            _json.dumps(sug, ensure_ascii=False, indent=2))

        text = "\n".join(sections)

        from PySide6.QtWidgets import QPlainTextEdit
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Prompt / 响应  ·  任务 #{t.id}")
        dlg.resize(820, 600)
        lay = QVBoxLayout(dlg)
        ed = QPlainTextEdit()
        ed.setReadOnly(True)
        ed.setPlainText(text)
        lay.addWidget(ed, 1)
        bb2 = QDialogButtonBox(QDialogButtonBox.Close)
        bb2.rejected.connect(dlg.accept)
        lay.addWidget(bb2)
        dlg.exec()

    # ---------------------------------------------------------------- reapply
    def _reapply_suggestions(self) -> None:
        """把所选任务存的 suggestions 重新写入 project_field_suggestions（pending）。
        同字段的旧 pending 会被 add_suggestions 自动 superseded。"""
        t = getattr(self, "_current_task", None)
        if t is None or t.status != "done" or t.project_id is None:
            return

        # 解析 suggestions
        import json as _json
        try:
            blob = _json.loads(t.result_json or "null")
        except Exception:
            blob = None
        sug: dict = (blob or {}).get("suggestions") if isinstance(blob, dict) else None
        if not isinstance(sug, dict) or not sug:
            info(self, "提示", "此任务没有可重新应用的建议。")
            return

        project = self.repo.get_project(int(t.project_id))
        if project is None:
            info(self, "提示", "原项目已被删除。")
            return

        # 字段名 → field_id（按当前最新的字段表，旧任务里没的字段会被丢弃）
        fields = self.repo.list_fields()
        name_to_field = {f.name: f for f in fields if f.id is not None}

        items: list[tuple[int, str]] = []
        dropped: list[str] = []
        skipped_same: list[str] = []
        for name, value in sug.items():
            value = (str(value) if value is not None else "").strip()
            if not value:
                continue
            f = name_to_field.get(name)
            if f is None or f.id is None:
                dropped.append(name)
                continue
            # 与当前项目值相同则跳过（避免产生无意义的建议）
            cur = self._current_value(project, f)
            if cur.strip() == value:
                skipped_same.append(name)
                continue
            items.append((f.id, value))

        if not items:
            msg = "没有需要重新应用的建议。"
            if dropped:
                msg += f"\n\n（{len(dropped)} 个字段在当前已不存在：{', '.join(dropped)}）"
            if skipped_same:
                msg += f"\n\n（{len(skipped_same)} 个字段当前值已与建议一致）"
            info(self, "提示", msg)
            return

        if not confirm(
            self, "确认",
            f"将为项目「{project.title}」重新生成 {len(items)} 条待审阅建议。\n"
            f"同字段的旧建议会被自动标记为 superseded。是否继续？",
            yes="继续", default_yes=True,
        ):
            return

        n = self.repo.add_suggestions(int(project.id), int(t.id), items)
        self.suggestions_reapplied.emit(int(project.id), n)
        tip = f"已生成 {n} 条新待审阅建议。"
        if dropped:
            tip += f"\n（{len(dropped)} 个字段已不存在被丢弃）"
        if skipped_same:
            tip += f"\n（{len(skipped_same)} 个字段值已一致被跳过）"
        info(self, "完成", tip)

    @staticmethod
    def _current_value(p, f) -> str:
        """与 queue._current_value 同步：取项目当前字段值（用于与建议对比）。

        task #20 schema v4 起：除受保护字段（title/description/tags）外，所有
        字段值（含老 author/date/source_url/rating）统一在 p.field_values。
        """
        if f.is_required:
            if f.key == "title":
                return p.title
            if f.key == "description":
                return p.description_md or ""
            if f.key == "tags":
                return ", ".join(p.tags)
        return p.field_values.get(f.id or -1, "")
