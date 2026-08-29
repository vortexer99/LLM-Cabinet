"""项目导入（task #10 / task #28 T3 导入增强）。

把若干"项目文件夹"批量导入为新项目。每个文件夹一个项目；若根目录下
存在 ``project.json`` ，则按 ``tasks/09`` 输出的 schema 恢复元数据/字段/标签。

task #28 T3 增强：
- ZIP 包导入支持（自动解包到临时目录）
- 封面图还原（cover_file_id 按新 file id 重映射）
- 拍平结构的目录树还原（优先用 files.json 的 subfolder）
- 文件来源标记还原（origin: user/generated）

设计要点见 ``tasks/10-folder-batch-import.md``。
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Callable, Literal, Optional

from .library import Library
from .models import FIELD_TYPES, FileItem, PendingFile, Project
from .repository import Repository
from .utils import detect_kind

# 当前导入器已知的最高 project-export schema 版本
# @3: files.json 含 subfolder + is_cover + origin（task #28 / task #30）
# @2: fields_snapshot 含 prompt_hint（task #11 T4）；@1 仍兼容
SUPPORTED_SCHEMA_VERSION = 3
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
    is_zip: bool = False                      # 是否从 ZIP 解压而来（task #28 T3）

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

    支持 ZIP 包：传入 .zip 文件时会自动解包到临时目录进行扫描。
    导入完成后临时目录会被清理。

    不实际导入；返回 ``ImportPlan`` 列表供 UI 展示。
    """
    existing_field_names = {f.name for f in repo.list_fields()}
    plans: list[ImportPlan] = []

    # task #28 T3: 支持 ZIP 包导入
    temp_dirs: list[Path] = []  # 记录临时目录，扫描完成后不清理（由调用方在导入完成后清理）

    for folder in folders:
        folder = Path(folder)

        # 检测 ZIP 文件
        if folder.suffix.lower() == ".zip":
            try:
                temp_dir, zip_path = _extract_zip_to_temp(folder)
                plan = _scan_one(temp_dir, existing_field_names)
                plan._temp_dir = temp_dir  # 临时目录，导入完成后清理
                plan._zip_path = zip_path  # 原始 ZIP 路径
                plan.is_zip = True
                plans.append(plan)
            except Exception as e:
                # ZIP 解压失败，创建一个带错误信息的 plan
                plan = ImportPlan(folder=folder)
                plan.parse_error = f"ZIP 解压失败：{e}"
                plan.has_project_json = False
                plans.append(plan)
            continue

        plan = _scan_one(folder, existing_field_names)
        plans.append(plan)

    return plans


def _extract_zip_to_temp(zip_path: Path) -> tuple[Path, Path]:
    """解压 ZIP 到临时目录，返回 (临时目录路径, ZIP文件路径)。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="llm_cabinet_import_"))
    with zipfile.ZipFile(zip_path, "r") as zf:
        _safe_extract_zip(zf, temp_dir)
    return temp_dir, zip_path


def _safe_extract_zip(zf: zipfile.ZipFile, target_dir: Path) -> None:
    """安全解压 ZIP，拒绝写出目标目录的成员。"""
    target_root = target_dir.resolve()
    for info in zf.infolist():
        dest = (target_root / info.filename).resolve()
        if dest != target_root and target_root not in dest.parents:
            raise ValueError(f"ZIP 包含非法路径：{info.filename}")
    zf.extractall(target_root)


def cleanup_extracted_zips(plans: list[ImportPlan]) -> None:
    """清理从 ZIP 解压出来的临时目录。"""
    for plan in plans:
        temp_dir = getattr(plan, "_temp_dir", None)
        if temp_dir and temp_dir.exists():
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass


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
    """将一个 ``ImportPlan`` 落地为新项目（同步组合版）。

    task #36 起内部拆成三个阶段，UI 可拆开调用以便把文件复制放进 worker
    线程（``prepare_project_from_plan`` → ``save_project`` →
    ``copy_files_for_import``（worker 可跑）→ ``write_import_file_rows``）；
    本函数保持旧签名旧行为，等价于三阶段在主线程顺序执行。

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
    project = prepare_project_from_plan(repo, plan, options, warnings, ask_field_policy)
    pid = repo.save_project(project)
    project.id = pid
    prepared = copy_files_for_import(
        library, pid, plan, options, warnings, progress=progress,
    )
    n_files = write_import_file_rows(repo, project, plan, prepared, warnings)
    return ImportResult(project_id=pid, n_files=n_files, warnings=warnings)


def prepare_project_from_plan(
    repo: Repository,
    plan: ImportPlan,
    options: ImportOptions,
    warnings: list[str],
    ask_field_policy: Callable[[Path, list[str]], FieldPolicy] | None = None,
) -> Project:
    """阶段 1（主线程）：决定项目元数据 + 未匹配字段策略。

    返回**尚未持久化**的 ``Project``（tags / field_values 已挂上）；
    调用方负责 ``repo.save_project``。
    """
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

    project.tags = tags
    project.field_values = field_value_map
    return project


def copy_files_for_import(
    library: Library,
    project_id: int,
    plan: ImportPlan,
    options: ImportOptions,
    warnings: list[str],
    *,
    progress: Callable[[int, int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[tuple[PendingFile, str, bool, "int | None"]]:
    """阶段 2（纯文件 IO，可放 worker 线程）：收集并（copy 模式）复制文件。

    返回 ``[(PendingFile, stored_path, is_relative, old_file_id), ...]``：
    - copy 模式：``stored_path`` 为库内相对路径（已物理复制）
    - link 模式：``stored_path`` 为源文件绝对路径（无 IO）
    失败项跳过并记 ``warnings``。``is_cancelled()`` 为 True 时提前返回
    已处理完的部分（不抛异常，由调用方决定如何收尾）。
    """
    pending_files, file_id_map = _collect_files_to_import(plan)
    pf_to_old_id = {id(pf): old_id for old_id, pf in file_id_map.items()}

    prepared: list[tuple[PendingFile, str, bool, "int | None"]] = []
    total = len(pending_files)
    for i, pf in enumerate(pending_files):
        if is_cancelled is not None and is_cancelled():
            break  # 已复制的部分照常返回，主线程决定收尾
        try:
            if options.storage_mode == "copy":
                rel = library.import_copy(project_id, pf.src)
                prepared.append((pf, rel, True, pf_to_old_id.get(id(pf))))
            else:
                prepared.append(
                    (pf, str(pf.src.resolve()), False, pf_to_old_id.get(id(pf)))
                )
        except Exception as e:
            warnings.append(f"导入文件失败 {pf.src.name}：{e}")
        if progress is not None:
            try:
                progress(i + 1, total, pf.src.name)
            except Exception:
                pass
    return prepared


def write_import_file_rows(
    repo: Repository,
    project: Project,
    plan: ImportPlan,
    prepared: list[tuple[PendingFile, str, bool, "int | None"]],
    warnings: list[str],
) -> int:
    """阶段 3（主线程）：把阶段 2 的结果写成 files 行 + 封面还原。"""
    assert project.id is not None
    n_added = 0
    old_to_new_id: dict[int, int] = {}
    for pf, stored_path, is_rel, old_file_id in prepared:
        try:
            fi = FileItem(
                project_id=project.id,
                path=stored_path,
                is_relative=is_rel,
                label=pf.label,
                kind=detect_kind(pf.src.suffix),
                ord=n_added,
                subfolder=pf.subfolder,
                origin=pf.origin,
            )
            new_id = repo.add_file(fi)
            if old_file_id:
                try:
                    old_to_new_id[int(old_file_id)] = new_id
                except (ValueError, TypeError):
                    pass
            n_added += 1
        except Exception as e:
            warnings.append(f"导入文件失败 {pf.src.name}：{e}")

    # task #28 T3：封面还原
    pj = plan.project_json or {}
    old_cover_id = pj.get("project", {}).get("cover_file_id")
    if old_cover_id and old_cover_id in old_to_new_id:
        project.cover_file_id = old_to_new_id[old_cover_id]
        repo.save_project(project)

    return n_added


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

    # 顶层老系统字段（v3 schema 兼容兜底）：
    # v3 导出的 project.json 顶层 ``project.{author,date,source_url,rating}``
    # 是真实数据；v4 起这 4 个字段值在 ``field_values_blob`` 里也会出现，但
    # 顶层仍保留作为向后兼容。这里通过 fields 表 key→fid 把顶层值落进
    # ``fv_by_id``；后续 field_values 段如果有同 fid 的条目会覆盖（以更详细
    # 的为准）。
    # 注意：title / description_md 仍是 projects 表列，按 proj 顶层属性赋值。
    key_to_fid = {f.key: f.id for f in repo.list_fields()
                  if f.key and f.id is not None}
    if isinstance(proj_data.get("author"), str) and proj_data["author"]:
        fid = key_to_fid.get("author")
        if fid is not None:
            fv_by_id[fid] = proj_data["author"]
    if isinstance(proj_data.get("date"), str) and proj_data["date"]:
        fid = key_to_fid.get("date")
        if fid is not None:
            fv_by_id[fid] = proj_data["date"]
    if isinstance(proj_data.get("source_url"), str) and proj_data["source_url"]:
        fid = key_to_fid.get("source_url")
        if fid is not None:
            fv_by_id[fid] = proj_data["source_url"]
    rating = proj_data.get("rating")
    if isinstance(rating, int) and rating > 0:
        fid = key_to_fid.get("rating")
        if fid is not None:
            fv_by_id[fid] = str(max(0, min(5, rating)))
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

    # 已匹配字段值：按 field_name 在库内查到字段定义，把值落到 fv_by_id。
    # task #20 schema v4 起：除受保护字段（title/description/tags）外，所有
    # 字段（含老 author/date/source_url/rating）都走 field_values 路径；
    # 不再用 is_system 判断跳过——只跳过受保护字段（title/description/tags
    # 是 Project 顶层属性 + 独立 tags 表，不通过 field_values 写）。
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
        if target.is_required:
            # title / description / tags 走 Project 顶层属性 + 独立 tags 表，
            # 不进 field_values（title/description 已在上面处理过；tags 走 raw_tags）
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
        # task #11 T4：@2 schema 起 fields_snapshot 含 prompt_hint
        snap_hint = snap.get("prompt_hint")
        snap_hint = snap_hint if isinstance(snap_hint, str) else ""
        try:
            new_id = repo.add_field(fname, ftype, prompt_hint=snap_hint)
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


def _safe_project_subfolder(raw: object) -> str:
    """校验并规范化项目包内的逻辑子目录。"""
    if raw is None:
        return ""
    s = str(raw).replace("\\", "/").strip("/")
    if not s:
        return ""
    parts = [p for p in s.split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise ValueError(f"非法子目录路径：{raw!r}")
    return "/".join(parts)


def _safe_exported_file_path(folder: Path, rel_path: object) -> Path:
    """把 files.json 的 exported_to 解析为包内文件路径。"""
    if not isinstance(rel_path, str) or not rel_path:
        raise ValueError("exported_to 不是有效路径")
    p = Path(rel_path)
    if p.is_absolute():
        raise ValueError(f"exported_to 不能是绝对路径：{rel_path}")
    parts = p.parts
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"exported_to 包含非法路径片段：{rel_path}")
    root = folder.resolve()
    resolved = (root / p).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"exported_to 超出项目包目录：{rel_path}")
    return resolved


def _collect_files_to_import(plan: ImportPlan) -> tuple[list[PendingFile], dict[int, PendingFile]]:
    """从文件夹收集待导入的文件路径，附带 subfolder 信息（task #17 / task #28 T3）。

    - 跳过导出包元数据（``project.json`` / ``files.json`` / ``README.md``）
    - 若存在 ``files.json``（task #28 @3 导出包），优先读取其中的 subfolder/label/origin
    - 若存在 ``files/`` 子目录，优先用其中的文件
    - 否则递归扫整个文件夹，跳过隐藏文件

    返回：
    - pending_files: list[PendingFile]
    - file_id_map: dict[int, PendingFile] 用于旧 file_id → PendingFile 映射（封面还原）
    """
    folder = plan.folder
    file_id_map: dict[int, PendingFile] = {}

    # task #28 T3：优先读取 files.json 获取 subfolder/label/origin
    files_json_path = folder / "files.json"
    if files_json_path.is_file():
        try:
            with open(files_json_path, encoding="utf-8") as f:
                files_data = json.load(f)
            files_list = files_data.get("files", [])
            preserve_structure = files_data.get("preserve_structure", True)
            pending_files: list[PendingFile] = []

            for fe in files_list:
                try:
                    exported_to = fe.get("exported_to")
                    if not exported_to:
                        continue
                    src = _safe_exported_file_path(folder, exported_to)
                    if not src.exists():
                        continue

                    # task #28 T3：从 files.json 读取 subfolder/label/origin
                    subfolder = _safe_project_subfolder(fe.get("subfolder", ""))
                    label = fe.get("label", "")
                    origin = fe.get("origin", "user")
                    if origin not in ("user", "generated"):
                        origin = "user"
                    old_id = fe.get("id")
                except (TypeError, ValueError, OSError):
                    continue

                pf = PendingFile(
                    src=src.resolve(),
                    subfolder=subfolder,
                    label=label,
                    origin=origin,
                )
                pending_files.append(pf)

                if old_id is not None:
                    file_id_map[int(old_id)] = pf

                # 拍平模式下：如果没有 subfolder 但 preserve_structure=False，
                # 需要从 exported_to 路径反推
                if not subfolder and not preserve_structure and fe.get("exported_to"):
                    # exported_to 可能是 "files/xxx_123.pdf" 形式
                    exported = fe.get("exported_to", "")
                    if exported.startswith("files/"):
                        # 尝试从文件名提取 id 来还原（如果需要）
                        pass

            # 返回从 files.json 收集的文件
            return sorted(pending_files, key=lambda pf: pf.src), file_id_map
        except Exception:
            pass  # 文件损坏时回退到物理扫描

    # 回退：物理目录结构扫描
    def _make(src: Path, root: Path) -> PendingFile:
        rel = src.parent.relative_to(root)
        subfolder = rel.as_posix() if str(rel) != "." else ""
        return PendingFile(src=src.resolve(), subfolder=subfolder)

    files_dir = folder / "files"
    if files_dir.is_dir():
        out = sorted(
            (_make(p, files_dir) for p in files_dir.rglob("*") if p.is_file()),
            key=lambda pf: pf.src,
        )
        # 回退模式下 file_id_map 为空
        return out, file_id_map

    skip_names = {"project.json", "files.json", "README.md"}
    out: list[PendingFile] = []
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
        out.append(_make(p, folder))
    return sorted(out, key=lambda pf: pf.src), file_id_map


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
