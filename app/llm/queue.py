"""LLM 任务队列：单 worker 线程，按 created_at 顺序处理。

Qt signals:
    task_changed(task_id: int)        # 任意任务状态变化
    counts_changed(active: int)       # 活动任务数变化（驱动状态栏数字）
    suggestions_added(project_id: int, count: int)
    task_failed(task_id: int, error: str)
"""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from ..library import Library
from ..models import Field, FileItem, Project
from ..repository import Repository
from .config import LLMConfig, ProviderConfig
from .context import build_messages, parse_response
from .providers import get_provider


class LLMTaskQueue(QObject):
    task_changed = Signal(int)
    counts_changed = Signal(int)
    suggestions_added = Signal(int, int)   # (project_id, count)
    task_failed = Signal(int, str)

    def __init__(self, repo: Repository, library: Library, get_config):
        """get_config: () -> LLMConfig（每次取最新配置）"""
        super().__init__()
        self.repo = repo
        self.library = library
        self._get_config = get_config

        self._q: "queue.Queue[int]" = queue.Queue()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        # 保护 sqlite 连接的锁（sqlite3 默认不支持多线程同一连接）
        self._db_lock = threading.RLock()

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._run, name="LLMTaskWorker", daemon=True,
        )
        self._worker.start()

    def stop(self, *, join_timeout: float = 0.0) -> None:
        """通知 worker 退出。

        ``join_timeout > 0`` 时同步等待 worker 退出最多 N 秒；超时不抛错。
        Windows 下"删除当前库"前需要把 worker 线程上挂着的 sqlite 连接彻底
        放掉，调用方应传 ``join_timeout=2``（worker 主循环 timeout=0.5，正常
        会很快退）。
        """
        self._stop.set()
        if join_timeout > 0 and self._worker is not None and self._worker.is_alive():
            try:
                self._worker.join(timeout=join_timeout)
            except RuntimeError:
                pass

    # ----------------------------------------------------- public api
    def enqueue_meta_suggest(
        self,
        project: Project,
        ref_file_ids: list[int],
        user_note: str,
        target_field_ids: list[int] | None = None,
    ) -> int:
        """target_field_ids：本次任务真正向 LLM 征求建议的字段 id 列表。
        None 表示按字段定义里的 suggest_enabled 取（旧行为）。
        """
        cfg = self._get_config()
        active = cfg.active()
        if active is None or not active.api_key:
            raise RuntimeError("尚未配置 API Key（请在 设置 → API 中填写）")

        payload: dict = {
            "ref_file_ids": ref_file_ids,
            "user_note": user_note,
            "language": cfg.default_language,
        }
        if target_field_ids is not None:
            payload["target_field_ids"] = list(target_field_ids)
        with self._db_lock:
            tid = self.repo.create_llm_task(
                project_id=project.id,
                project_title=project.title or "(未命名)",
                ttype="meta_suggest",
                payload_json=json.dumps(payload, ensure_ascii=False),
                provider=active.id,
                model=active.model,
            )
        self._q.put(tid)
        self._emit_counts()
        self.task_changed.emit(tid)
        return tid

    def active_count(self) -> int:
        with self._db_lock:
            return self.repo.count_llm_tasks_active()

    # ----------------------------------------------------- worker
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                tid = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process(tid)
            except Exception as e:
                # 兜底：永远不让 worker 死掉
                try:
                    with self._db_lock:
                        self.repo.update_llm_task_status(tid, "failed", error=str(e))
                except Exception:
                    pass
                self.task_failed.emit(tid, str(e))
            finally:
                self.task_changed.emit(tid)
                self._emit_counts()

    def _process(self, tid: int) -> None:
        with self._db_lock:
            tasks = [t for t in self.repo.list_llm_tasks(limit=1000) if t.id == tid]
        if not tasks:
            return
        task = tasks[0]

        # 取项目（可能在排队期间被改）
        with self._db_lock:
            project = self.repo.get_project(task.project_id) if task.project_id else None
            fields = self.repo.list_fields()
            files: list[FileItem] = self.repo.list_files(task.project_id) if task.project_id else []
        if project is None:
            with self._db_lock:
                self.repo.update_llm_task_status(tid, "failed", error="项目不存在")
            return

        try:
            payload = json.loads(task.payload_json or "{}")
        except Exception:
            payload = {}
        ref_ids = set(payload.get("ref_file_ids") or [])
        user_note = payload.get("user_note") or ""
        language = payload.get("language") or "中文"
        # 任务级临时覆盖：本次想让 LLM 给建议的字段 id 列表（None=按字段定义）
        target_ids_override = payload.get("target_field_ids")

        # 标记 running
        with self._db_lock:
            self.repo.update_llm_task_status(tid, "running")
        self.task_changed.emit(tid)

        # 拼接 messages
        cfg = self._get_config()
        pcfg: ProviderConfig | None = cfg.providers.get(task.provider)
        if pcfg is None or not pcfg.api_key:
            with self._db_lock:
                self.repo.update_llm_task_status(tid, "failed", error="平台配置丢失或未填写 API Key")
            self.task_failed.emit(tid, "平台配置丢失或未填写 API Key")
            return
        provider = get_provider(pcfg)

        # 上下文：所有字段都参与，提供已知信息（与"列表是否显示"无关）
        context_fields = list(fields)
        # 目标：优先使用任务自带的 target_field_ids；否则按字段定义的 suggest_enabled
        if isinstance(target_ids_override, list):
            wanted = {int(x) for x in target_ids_override}
            target_fields = [f for f in context_fields if (f.id in wanted)]
        else:
            target_fields = [f for f in context_fields if f.suggest_enabled]
        # 解析 ref 文件路径
        chosen: list[tuple[FileItem, Path]] = []
        for fi in files:
            if fi.id in ref_ids:
                p = self.library.resolve(fi.path, fi.is_relative)
                chosen.append((fi, p))

        messages = build_messages(
            project, context_fields, target_fields, chosen, user_note,
            language=language,
            allow_images=provider.supports_image(),
            all_files=files,
        )

        # 抽取一份 user prompt 文本用于排查（图片不存）
        user_prompt_text = ""
        try:
            for m in messages:
                if m.get("role") == "user":
                    c = m.get("content")
                    if isinstance(c, list):
                        user_prompt_text = "\n".join(
                            x.get("text", "") for x in c if x.get("type") == "text"
                        )
                    elif isinstance(c, str):
                        user_prompt_text = c
                    break
        except Exception:
            pass

        # 调用 LLM
        try:
            resp = provider.chat(messages, json_mode=True, timeout=120.0)
        except Exception as e:
            with self._db_lock:
                self.repo.update_llm_task_status(tid, "failed", error=str(e))
            self.task_failed.emit(tid, str(e))
            return

        # 解析 → 字段名 -> 值
        suggestions = parse_response(resp.text)
        # 映射字段名 → field_id
        name_to_field = {f.name: f for f in target_fields}
        items: list[tuple[int, str]] = []
        for name, value in suggestions.items():
            f = name_to_field.get(name)
            if f is None or f.id is None:
                continue
            value = (value or "").strip()
            if not value:
                continue
            # 如果与当前值相同，跳过
            cur = self._current_value(project, f)
            if cur.strip() == value:
                continue
            items.append((f.id, value))

        # result_json 同时保留：解析后的建议 + 原始响应文本 + user prompt（便于排查）
        result_blob = {
            "suggestions": suggestions,
            "raw_text": resp.text or "",
            "applicable_count": len(items),
            "target_field_names": [f.name for f in target_fields],
            "ref_files": [str(p) for _, p in chosen],
            "user_prompt": user_prompt_text,
        }

        # 写入建议 + 任务状态
        with self._db_lock:
            n = self.repo.add_suggestions(project.id, tid, items) if project.id else 0
            self.repo.update_llm_task_status(
                tid, "done",
                result_json=json.dumps(result_blob, ensure_ascii=False),
                tokens_in=resp.tokens_in, tokens_out=resp.tokens_out,
            )
            self.repo.trim_llm_tasks(keep=100)

        if project.id is not None:
            self.suggestions_added.emit(project.id, n)

    @staticmethod
    def _current_value(p: Project, f: Field) -> str:
        if f.is_system:
            if f.key == "title": return p.title
            if f.key == "author": return p.author
            if f.key == "date": return p.date
            if f.key == "rating": return str(p.rating) if p.rating else ""
            if f.key == "source_url": return p.source_url
            if f.key == "description": return p.description_md or ""
            return ""
        return p.field_values.get(f.id or -1, "")

    def _emit_counts(self) -> None:
        try:
            self.counts_changed.emit(self.active_count())
        except Exception:
            pass
