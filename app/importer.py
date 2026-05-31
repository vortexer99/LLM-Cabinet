"""项目导入（task #10）。

把若干"项目文件夹"批量导入为新项目。每个文件夹一个项目；若根目录下
存在 ``project.json`` ，则按 ``tasks/09`` 输出的 schema 恢复元数据/字段/标签。

设计要点见 ``tasks/10-folder-batch-import.md``。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Callable, Literal, Optional

from .library import Library
from .models import FIELD_TYPES, FileItem, Project
from .repository import Repository
from .utils import detect_kind

# 当前导入器已知的最高 project-export schema 版本
SUPPORTED_SCHEMA_VERSION = 1
SCHEMA_PREFIX = "llm-cabinet/project-export@"

FieldPolicy = Literal["create", "append_to_desc", "ignore"]


# =============================================================================
# 数据类
# =============================================================================
@dataclass
class ImportPlan:
    """单个文件夹的扫描结果，作为预览/确认依据。"""

    folder: Path
    has_project_json: bool = False
    project_json: Optional[dict] = None       # 解析后；None = 无或损坏
    schema_version: Optional[int] = None      # @N 中的 N；None = 未识别
    is_future_schema: bool = False            # schema_version > SUPPORTED_SCHEMA_VERSION
    parse_error: str = ""                     # 解析失败原因（用于 warning 与 UI 状态）
    unmatched_fields: list[str] = dc_field(default_factory=list)  # 库内不存在的字段名
    unmatched_field_values: dict[str, str] = dc_field(default_factory=dict)  # 备用：append_to_desc 时用

    @property
    def status_text(self) -> str:
        """给 UI 状态列用的简短文本。"""
        if not self.has_project_json:
            return "⚪ 普通文件夹"
        if self.parse_error:
            return f"⚠ 配置解析失败：{self.parse_error}"
        warns: list[str] = []
        if self.is_future_schema:
            warns.append("更新版本生成")
        if self.unmatched_fields:
            warns.append(f"{len(self.unmatched_fields)} 字段未匹配")
        if warns:
            return "⚠ 已识别（" + " · ".join(warns) + "）"
        return "✅ 已识别 project.json"


@dataclass
class ImportOptions:
    """批量导入选项。"""

    storage_mode: Literal["link", "copy"] = "link"
    title_source: Literal["project_json", "folder_name"] = "project_json"
    field_policy: FieldPolicy = "append_to_desc"
    field_policy_apply_all: bool = True


@dataclass
class ImportResult:
    """单个项目的导入结果。"""

    project_id: int
    n_files: int = 0
    warnings: list[str] = dc_field(default_factory=list)


# =============================================================================
# 扫描阶段
# =============================================================================
def scan_folders(folders: list[Path], repo: Repository) -> list[ImportPlan]:
    """扫描每个文件夹，识别其中是否存在合法的 ``project.json``。

    不实际导入；返回 ``ImportPlan`` 列表供 UI 展示。
    """
    existing_field_names = {f.name for f in repo.list_fields()}
    plans: list[ImportPlan] = []
    for folder in folders:
        plan = _scan_one(folder, existing_field_names)
        plans.append(plan)
    return plans


def _scan_one(folder: Path, existing_field_names: set[str]) -> ImportPlan:
    folder = Path(folder)
    plan = ImportPlan(folder=folder)
    pj = folder / "project.json"
    if not pj.is_file():
        return plan

    plan.has_project_json = True
    try:
        raw = pj.read_text(encoding="utf-8")
    except OSError as e:
        plan.parse_error = f"读取失败：{e}"
        plan.has_project_json = False
        return plan

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        plan.parse_error = f"JSON 错误：{e.msg}"
        plan.has_project_json = False
        return plan

    if not isinstance(data, dict):
        plan.parse_error = "顶层不是对象"
        plan.has_project_json = False
        return plan

    schema = data.get("schema") or ""
    if not isinstance(schema, str) or not schema.startswith(SCHEMA_PREFIX):
        plan.parse_error = f"schema 字段无效：{schema!r}"
        plan.has_project_json = False
        return plan

    # 解析 schema 版本号
    ver_str = schema[len(SCHEMA_PREFIX):]
    try:
        version = int(ver_str)
    except ValueError:
        plan.parse_error = f"schema 版本号无效：{ver_str!r}"
        plan.has_project_json = False
        return plan

    plan.schema_version = version
    plan.is_future_schema = version > SUPPORTED_SCHEMA_VERSION
    plan.project_json = data

    # 识别未匹配字段（按 field_name 比对，向前兼容字段）
    field_values = data.get("field_values") or []
    for fv in field_values:
        if not isinstance(fv, dict):
            continue
        fname = fv.get("field_name") or ""
        if not fname:
            continue
        if fname not in existing_field_names:
            if fname not in plan.unmatched_fields:
                plan.unmatched_fields.append(fname)
            value = fv.get("value")
            if value is not None and value != "":
                plan.unmatched_field_values[fname] = str(value)

    return plan


# =============================================================================
# 导入阶段
# =============================================================================
def import_folder_as_project(
    repo: Repository,
    library: Library,
    plan: ImportPlan,
    options: ImportOptions,
    *,
    progress: Callable[[int, int, str], None] | None = None,
    ask_field_policy: Callable[[Path, list[str]], FieldPolicy] | None = None,
) -> ImportResult:
    """将一个 ``ImportPlan`` 落地为新项目。

    Args:
        repo: 数据仓库
        library: 文件仓库（用于 copy 模式落地）
        plan: 扫描阶段产出的计划
        options: 用户选项
        progress: 文件复制进度回调 ``(done, total, name)``
        ask_field_policy: 当 ``options.field_policy_apply_all=False`` 且本项目存在
            未匹配字段时，UI 用此回调让用户**就该项目**单独决策。回调返回
            ``FieldPolicy``。若回调为 ``None``，回退到 ``options.field_policy``。
    """
    warnings: list[str] = []

    # ---- 决定项目元数据 ----
    project, tags, field_value_map = _build_project_from_plan(
        plan, options, warnings, repo,
    )

    # ---- 决定本项目对未匹配字段的处理策略 ----
    effective_policy = options.field_policy
    if plan.unmatched_fields and not options.field_policy_apply_all and ask_field_policy is not None:
        try:
            effective_policy = ask_field_policy(plan.folder, list(plan.unmatched_fields))
        except Exception as e:
            warnings.append(f"未匹配字段策略询问失败，回退默认：{e}")

    # ---- 处理未匹配字段 ----
    if plan.unmatched_fields:
        _apply_field_policy(
            repo, project, plan, effective_policy, field_value_map, warnings,
        )

    # ---- 写项目 ----
    project.tags = tags
    project.field_values = field_value_map
    pid = repo.save_project(project)
    project.id = pid

    # ---- 处理文件 ----
    n_files = _import_files_for_project(
        repo, library, project, plan, options, warnings, progress,
    )

    return ImportResult(project_id=pid, n_files=n_files, warnings=warnings)


# -----------------------------------------------------------------------------
# 各阶段实现
# -----------------------------------------------------------------------------
def _build_project_from_plan(
    plan: ImportPlan,
    options: ImportOptions,
    warnings: list[str],
    repo: Repository,
) -> tuple[Project, list[str], dict[int, str]]:
    """从 plan + options 构造 ``Project`` 对象（尚未持久化）。

    返回 ``(project, tags, field_value_map_by_id)``。

    field_value_map 仅含**库内已存在**的字段；未匹配字段的处理交给
    ``_apply_field_policy``。
    """
    proj = Project()
    tags: list[str] = []
    fv_by_id: dict[int, str] = {}

    pj = plan.project_json or {}
    proj_data = pj.get("project") if isinstance(pj.get("project"), dict) else {}

    # 标题
    if (
        options.title_source == "project_json"
        and isinstance(proj_data.get("title"), str)
        and proj_data.get("title").strip()
    ):
        proj.title = proj_data["title"].strip()
    else:
        proj.title = plan.folder.name

    # 系统字段（向前兼容核心字段）
    if isinstance(proj_data.get("author"), str):
        proj.author = proj_data["author"]
    if isinstance(proj_data.get("date"), str):
        proj.date = proj_data["date"]
    if isinstance(proj_data.get("source_url"), str):
        proj.source_url = proj_data["source_url"]
    rating = proj_data.get("rating")
    if isinstance(rating, int):
        proj.rating = max(0, min(5, rating))
    desc = proj_data.get("description_md")
    if isinstance(desc, str):
        proj.description_md = desc

    # storage_mode 由 options 决定，不取 project.json 里的（避免新机器路径失效）
    proj.storage_mode = options.storage_mode

    # 标签：复用已有，不存在则在 _set_tags 里自动创建（Repository 已有逻辑）
    raw_tags = pj.get("tags")
    if isinstance(raw_tags, list):
        for t in raw_tags:
            if isinstance(t, str) and t.strip():
                tags.append(t.strip())

    # 已匹配字段值：按 field_name 在库内查到字段定义，把值落到 fv_by_id
    name_to_field = {f.name: f for f in repo.list_fields() if f.id is not None}
    field_values = pj.get("field_values") or []
    for fv in field_values:
        if not isinstance(fv, dict):
            continue
        fname = fv.get("field_name") or ""
        if not fname or fname in plan.unmatched_fields:
            continue   # 未匹配字段交给 _apply_field_policy 处理
        target = name_to_field.get(fname)
        if target is None or target.id is None:
            continue
        if target.is_system:
            # 系统字段：值已在上面的"系统字段"段落处理过；不重复落 field_values
            continue
        value = fv.get("value")
        if value is None or value == "":
            continue
        fv_by_id[target.id] = str(value)

    if plan.is_future_schema:
        warnings.append(
            f"此包由更新版本（@{plan.schema_version}）的 LLM Cabinet 生成；"
            "建议升级以避免遗漏新字段。"
        )

    return proj, tags, fv_by_id


def _apply_field_policy(
    repo: Repository,
    project: Project,
    plan: ImportPlan,
    policy: FieldPolicy,
    field_value_map: dict[int, str],
    warnings: list[str],
) -> None:
    """处理未匹配字段。会就地修改 ``project.description_md`` / ``field_value_map``。"""
    pj = plan.project_json or {}
    field_values = pj.get("field_values") or []
    fields_snapshot = {
        item.get("name"): item
        for item in (pj.get("fields_snapshot") or [])
        if isinstance(item, dict) and item.get("name")
    }

    if policy == "ignore":
        warnings.append(
            f"已忽略 {len(plan.unmatched_fields)} 个未匹配字段："
            + "、".join(plan.unmatched_fields)
        )
        return

    if policy == "append_to_desc":
        lines: list[str] = []
        for fv in field_values:
            if not isinstance(fv, dict):
                continue
            fname = fv.get("field_name") or ""
            if fname not in plan.unmatched_fields:
                continue
            value = fv.get("value")
            if value is None or value == "":
                continue
            lines.append(f"> - **{fname}**：{value}")
        if lines:
            appendix = "\n".join(["", "> 库内不存在的字段（已保留原值）："] + lines)
            base = (project.description_md or "").rstrip()
            project.description_md = (base + "\n\n" + appendix.lstrip("\n")).lstrip("\n")
        warnings.append(
            f"已把 {len(plan.unmatched_fields)} 个未匹配字段值追加到描述："
            + "、".join(plan.unmatched_fields)
        )
        return

    # policy == "create"
    created: list[str] = []
    failed: list[str] = []
    for fname in plan.unmatched_fields:
        snap = fields_snapshot.get(fname) or {}
        ftype = snap.get("type") or "text"
        if ftype not in FIELD_TYPES:
            warnings.append(f"字段「{fname}」类型 {ftype!r} 不合法，已 fallback 为 text")
            ftype = "text"
        try:
            new_id = repo.add_field(fname, ftype)
            created.append(fname)
            # 写入值
            for fv in field_values:
                if not isinstance(fv, dict) or fv.get("field_name") != fname:
                    continue
                value = fv.get("value")
                if value is None or value == "":
                    continue
                field_value_map[new_id] = str(value)
                break
        except Exception as e:
            failed.append(f"{fname}（{e}）")
    if created:
        warnings.append(f"已自动创建字段：{'、'.join(created)}")
    if failed:
        warnings.append(f"创建字段失败：{'、'.join(failed)}")


def _import_files_for_project(
    repo: Repository,
    library: Library,
    project: Project,
    plan: ImportPlan,
    options: ImportOptions,
    warnings: list[str],
    progress: Callable[[int, int, str], None] | None,
) -> int:
    """复制 / 链接文件夹中的文件到 project。返回成功导入数量。"""
    assert project.id is not None

    # 收集要导入的文件：优先 files.json 列出的；否则递归扫文件夹（排除 project.json/files.json/README.md/files/ 子目录）
    file_paths = _collect_files_to_import(plan)

    n_added = 0
    total = len(file_paths)
    for i, src in enumerate(file_paths):
        try:
            kind = detect_kind(src.suffix)
            if options.storage_mode == "copy":
                rel = library.import_copy(project.id, src)
                fi = FileItem(
                    project_id=project.id,
                    path=rel,
                    is_relative=True,
                    label="",
                    kind=kind,
                    ord=n_added,
                )
            else:
                fi = FileItem(
                    project_id=project.id,
                    path=str(src.resolve()),
                    is_relative=False,
                    label="",
                    kind=kind,
                    ord=n_added,
                )
            repo.add_file(fi)
            n_added += 1
        except Exception as e:
            warnings.append(f"导入文件失败 {src.name}：{e}")

        if progress is not None:
            try:
                progress(i + 1, total, src.name)
            except Exception:
                pass

    return n_added


def _collect_files_to_import(plan: ImportPlan) -> list[Path]:
    """从文件夹收集待导入的文件路径。

    - 跳过导出包元数据（``project.json`` / ``files.json`` / ``README.md``）
    - 若存在 ``files/`` 子目录（task #09 导出包结构），优先用其中的文件
    - 否则递归扫整个文件夹，跳过隐藏文件
    """
    folder = plan.folder
    files_dir = folder / "files"
    if files_dir.is_dir():
        return sorted(p for p in files_dir.rglob("*") if p.is_file())

    skip_names = {"project.json", "files.json", "README.md"}
    out: list[Path] = []
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(folder)
        # 跳过导出包元数据
        if len(rel.parts) == 1 and p.name in skip_names:
            continue
        # 跳过隐藏文件
        if any(part.startswith(".") for part in rel.parts):
            continue
        out.append(p)
    return sorted(out)


# =============================================================================
# 工具：路径分类
# =============================================================================
def split_paths_by_kind(paths: list[str]) -> tuple[list[Path], list[Path]]:
    """把混合路径分成 (目录列表, 文件列表)。其它（不存在/特殊）忽略。"""
    dirs: list[Path] = []
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            dirs.append(p)
        elif p.is_file():
            files.append(p)
    return dirs, files


__all__ = [
    "FieldPolicy",
    "ImportPlan",
    "ImportOptions",
    "ImportResult",
    "scan_folders",
    "import_folder_as_project",
    "split_paths_by_kind",
    "SUPPORTED_SCHEMA_VERSION",
    "SCHEMA_PREFIX",
]
