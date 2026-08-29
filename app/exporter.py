"""项目导出（基础版，task #09）。

把一个项目（元数据 + 字段值 + 标签 + 文件）导出到本地目录，作为可读、
人类可检视的"项目包"。后续的 ZIP 模式 / 批量 / 跨库导入复用本模块。

包结构：

    <target_root>/<safe_title>/
    ├── project.json     元数据 + 字段定义 snapshot + 字段值
    ├── files.json       文件清单（含每个文件在导出包内/原位的位置）
    ├── README.md        人类可读的项目摘要 + 导出环境信息
    └── files/           实际复制进来的文件（前缀 <file.id>__）

设计要点见 ``tasks/09-project-export-basic.md``。
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import __version__
from .db import SCHEMA_VERSION
from .library import Library
from .models import Project
from .repository import Repository
from .utils import OperationCancelled  # noqa: F401  # 供进度回调透传判断

# project.json 的 schema 标识，导入端用这个匹配版本
# @3 起 files.json 含 subfolder + is_cover + origin（task #28 / task #30）
# @2 起 fields_snapshot 含 prompt_hint（task #11 T4）；@1 仍兼容（导入端把缺失字段视为空）
EXPORT_SCHEMA = "llm-cabinet/project-export@3"


# =============================================================================
# 数据类
# =============================================================================
@dataclass
class ExportOptions:
    """导出选项（task #28 扩展版）。

    Attributes:
        target_root: 用户选的"导出位置"目录；本模块会在其下创建一个以
            项目标题安全化后命名的子目录。
        mode: 导出模式。"package"=完整包(含 files/)，"metadata_only"=仅 project.json
        export_format: 导出格式。"directory"=目录形式，"zip"=ZIP 打包
        preserve_structure: 是否保留项目内目录结构。False=拍平到 files/
        copy_link_files: 是否把"🔗 链接"模式的原始文件复制进导出包。
            默认 True（最安全，使导出包自包含）。
        include_readme: 是否生成 README.md
        include_llm_history: 是否导出 LLM 任务历史（默认 False）
        history_limit: LLM 历史导出条数限制
        overwrite: 同名目录是否覆盖；False 时自动加 ``(2) / (3)`` 后缀。
    """
    target_root: Path

    # 基本选项
    mode: str = "package"  # "package" | "metadata_only"
    export_format: str = "directory"  # "directory" | "zip"
    preserve_structure: bool = True
    copy_link_files: bool = True
    include_readme: bool = True
    include_llm_history: bool = False
    history_limit: int = 10
    overwrite: bool = False


@dataclass
class ExportResult:
    """导出结果摘要，用于 UI 反馈。"""
    project_dir: Path                       # 实际写入的项目根目录
    n_files_copied: int = 0                 # 实际复制了多少个文件
    n_files_referenced: int = 0             # 仅记录路径（link 模式且未勾选复制）
    total_bytes: int = 0                    # 复制文件的总字节数
    warnings: list[str] = field(default_factory=list)


# =============================================================================
# 工具函数
# =============================================================================
# Windows 文件名禁用字符 + 控制字符
_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows 保留名（不区分大小写，含/不含扩展名都拒绝）
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, *, fallback: str = "untitled") -> str:
    """把任意字符串清理为安全的 Windows/Unix 文件名（不含路径分隔符）。

    - 替换非法字符为下划线
    - 去掉首尾空格与点（Windows 不允许结尾的 '.' 与 ' '）
    - 撞 Windows 保留名时加前缀
    - 长度截断到 120（留余量给 fs 限制）
    - 空串退化为 fallback
    """
    s = _INVALID_CHARS_RE.sub("_", (name or "").strip())
    s = s.strip(" .")
    if not s:
        return fallback
    base_for_reserved = s.split(".", 1)[0].upper()
    if base_for_reserved in _RESERVED_NAMES:
        s = "_" + s
    return s[:120]


def unique_dirname(base: Path, name: str) -> str:
    """在 ``base`` 下寻找一个还未被占用的目录名。

    依次尝试 ``name`` / ``name (2)`` / ``name (3)`` ...
    """
    if not (base / name).exists():
        return name
    i = 2
    while (base / f"{name} ({i})").exists():
        i += 1
    return f"{name} ({i})"


def unique_filename(base: Path, name: str) -> str:
    """在 ``base`` 下寻找一个还未被占用的文件名。"""
    candidate = Path(name)
    stem = candidate.stem
    suffix = candidate.suffix
    if not (base / name).exists():
        return name
    i = 2
    while (base / f"{stem} ({i}){suffix}").exists():
        i += 1
    return f"{stem} ({i}){suffix}"


def safe_subfolder_path(raw: str) -> Path:
    """把 files.subfolder 约束为安全的相对 POSIX 路径。"""
    if not raw:
        return Path()
    parts = [p for p in raw.replace("\\", "/").split("/") if p]
    safe_parts = [sanitize_filename(p, fallback="folder") for p in parts if p not in (".", "..")]
    return Path(*safe_parts) if safe_parts else Path()


def safe_subfolder_posix(raw: str) -> str:
    """返回可写入 files.json 的安全 POSIX 子目录；顶层为空串。"""
    p = safe_subfolder_path(raw)
    return "" if str(p) == "." else p.as_posix()


def _copy_file_safely(src: Path, dst: Path) -> tuple[bool, int, str | None]:
    """复制单个文件；不让单文件失败拖垮整个导出。

    返回 ``(ok, size_bytes, warning_or_None)``。
    """
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True, dst.stat().st_size, None
    except FileNotFoundError:
        return False, 0, f"文件不存在：{src}"
    except PermissionError as e:
        return False, 0, f"权限不足，跳过：{src} ({e})"
    except OSError as e:
        return False, 0, f"复制失败 {src}: {e}"


# =============================================================================
# 主入口
# =============================================================================
def export_project(
    repo: Repository,
    library: Library,
    project: Project,
    options: ExportOptions,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> ExportResult:
    """导出单个项目。

    progress 回调签名 ``(done, total, current_file_name)``，UI 可基于此驱动
    进度条。``done`` / ``total`` 反映"文件复制"步骤的进度，元数据写入瞬时完成
    不单独报告。
    """
    assert project.id is not None, "export_project requires a saved project"

    options.target_root = Path(options.target_root)
    if not options.target_root.is_dir():
        raise NotADirectoryError(f"导出位置不存在或不是目录：{options.target_root}")

    # ---- 决定项目子目录名 ----
    safe_title = sanitize_filename(project.title or f"project_{project.id}")
    if options.overwrite:
        sub = safe_title
        target_dir = options.target_root / sub
        if target_dir.exists():
            # 覆盖语义：先清空再写
            shutil.rmtree(target_dir)
    else:
        sub = unique_dirname(options.target_root, safe_title)
        target_dir = options.target_root / sub
    # metadata_only 模式不需要 files/ 子目录
    if options.mode == "package":
        target_dir.mkdir(parents=True, exist_ok=False)
    else:
        target_dir.mkdir(parents=True, exist_ok=False)

    result = ExportResult(project_dir=target_dir)

    # ---- 收集数据 ----
    files = repo.list_files(project.id)
    fields = repo.list_fields()

    # ---- 写 project.json（metadata_only 模式下跳过 files/ 但仍写元数据） ----
    project_blob = _build_project_blob(project, fields)
    _write_json(target_dir / "project.json", project_blob)

    # ---- 仅 "package" 模式才导出文件 ----
    if options.mode == "package":
        # ---- 复制 / 引用文件，构建 files.json ----
        file_entries: list[dict] = []
        total = len(files)

        # 封面文件 ID（用于标记 is_cover）
        cover_file_id = project.cover_file_id

        # 拍平模式需要在 files/ 根目录；保留结构需要子目录
        files_subdir = target_dir / "files"
        files_subdir.mkdir(parents=True, exist_ok=True)  # 确保目录存在

        for i, f in enumerate(files):
            original_abs = library.resolve(f.path, f.is_relative)
            safe_subfolder = safe_subfolder_posix(f.subfolder)
            # 是否需要复制：封面始终复制（即使不勾选 copy_link_files）
            should_copy = f.is_relative or options.copy_link_files or (f.id == cover_file_id)

            exported_rel: str | None = None
            exported_size: int | None = None
            if should_copy:
                # 拍平模式：<name>_<id>；保留结构：<subfolder>/<name>
                src_name = Path(f.path).name
                safe_src_name = sanitize_filename(src_name, fallback=f"file_{f.id}")

                if options.preserve_structure:
                    # 保留目录结构
                    dst_name = f"{f.id}__{safe_src_name}"
                    if safe_subfolder:
                        dst_path = files_subdir / Path(safe_subfolder) / dst_name
                    else:
                        dst_path = files_subdir / dst_name
                else:
                    # 拍平：加 <id> 后缀防冲突
                    stem = Path(safe_src_name).stem
                    suffix = Path(safe_src_name).suffix
                    dst_name = f"{stem}_{f.id}{suffix}"
                    dst_path = files_subdir / dst_name

                # 确保父目录存在
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                ok, size, warn = _copy_file_safely(original_abs, dst_path)
                if ok:
                    # 记录导出后的相对路径
                    if options.preserve_structure and safe_subfolder:
                        exported_rel = f"files/{safe_subfolder}/{dst_name}"
                    else:
                        exported_rel = f"files/{dst_name}"
                    exported_size = size
                    result.n_files_copied += 1
                    result.total_bytes += size
                else:
                    if warn:
                        result.warnings.append(warn)
                    result.n_files_referenced += 1
            else:
                result.n_files_referenced += 1

            file_entries.append({
                "id": f.id,
                "subfolder": safe_subfolder,
                "origin": f.origin or "user",
                "is_cover": f.id == cover_file_id,
                "original_storage": "copy" if f.is_relative else "link",
                "original_path": str(original_abs) if not f.is_relative else f.path,
                "is_relative": bool(f.is_relative),
                "label": f.label or "",
                "kind": f.kind,
                "ord": f.ord,
                "added_at": f.added_at or "",
                "exported_to": exported_rel,
                "exported_size": exported_size,
            })
            if progress is not None:
                try:
                    progress(i + 1, total, Path(f.path).name)
                except OperationCancelled:
                    raise  # task #36：取消语义要穿透到 worker
                except Exception:
                    pass

        _write_json(target_dir / "files.json", {"preserve_structure": options.preserve_structure, "files": file_entries})

    # ---- README.md（人类可读摘要） ----
    if options.include_readme and options.mode == "package":
        _write_text(target_dir / "README.md", _build_readme(project, fields, result, files))

    # ---- ZIP 打包 ----
    if options.mode == "package" and options.export_format == "zip":
        import zipfile
        zip_name = unique_filename(
            options.target_root,
            f"{safe_title}_{datetime.now().strftime('%Y%m%d')}.zip",
        )
        zip_path = options.target_root / zip_name
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fpath in target_dir.rglob("*"):
                if fpath.is_file():
                    arcname = fpath.relative_to(target_dir)
                    zf.write(fpath, arcname)
        # 删除临时目录
        shutil.rmtree(target_dir)
        result.project_dir = zip_path

    return result


# =============================================================================
# 辅助：构建 JSON / Markdown
# =============================================================================
def _build_project_blob(project: Project, fields: list) -> dict:
    """打包 project.json 内容。

    task #20 schema v4 起：
    - 所有非保护字段值（含老 author/date/source_url/rating）都在
      project.field_values，导出时统一进 ``field_values_blob``
    - 顶层 ``project`` 字典里仍**保留** ``author/date/source_url/rating`` 4 个
      老 key，从 field_values 反查得到。这是为了让旧版客户端（v3 schema 时代）
      能读 v4 导出文件的兼容兜底——旧版只会读顶层这 4 个键
    - v4 客户端读 v4 文件时主要消费 ``field_values_blob``（避免双源不一致）
    """
    # field_id → field 对象，便于查 name/type；key → field_id，便于反查老系统字段
    field_by_id = {f.id: f for f in fields if f.id is not None}
    key_to_fid = {f.key: f.id for f in fields if f.key and f.id is not None}

    field_values_blob = []
    for fid, value in project.field_values.items():
        f = field_by_id.get(fid)
        if f is None:
            continue
        field_values_blob.append({
            "field_id": fid,
            "field_name": f.name,
            "field_key": f.key,
            "field_type": f.type,
            "value": value,
        })
    fields_snapshot = [
        {
            "id": f.id,
            "name": f.name,
            "type": f.type,
            "key": f.key,
            "ord": f.ord,
            "visible": f.visible,
            "suggest_enabled": f.suggest_enabled,
            "prompt_hint": f.prompt_hint,    # task #11 T4：跨库迁移保留 LLM 提示
        }
        for f in fields
    ]

    # 顶层老系统字段：从 field_values 反查（向后兼容旧版客户端）
    def _legacy(key: str) -> str:
        fid = key_to_fid.get(key)
        if fid is None:
            return ""
        return project.field_values.get(fid, "") or ""

    rating_str = _legacy("rating")
    try:
        rating_int = int(rating_str) if rating_str else 0
    except ValueError:
        rating_int = 0

    return {
        "schema": EXPORT_SCHEMA,
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "exporter_app_version": __version__,
        "exporter_schema_version": SCHEMA_VERSION,
        "project": {
            "title": project.title,
            # 4 个老系统字段：v4 起从 field_values 反查；保留是为了向后兼容
            "author": _legacy("author"),
            "date": _legacy("date"),
            "source_url": _legacy("source_url"),
            "rating": rating_int,
            "description_md": project.description_md,
            "storage_mode": project.storage_mode,
            "cover_file_id": project.cover_file_id,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
        },
        "tags": list(project.tags),
        "fields_snapshot": fields_snapshot,
        "field_values": field_values_blob,
    }


def _build_readme(project: Project, fields: list, result: ExportResult, files: list) -> str:
    """生成人类可读的 README.md。"""
    lines: list[str] = []
    title = project.title or "(未命名)"
    lines.append(f"# {title}")
    lines.append("")
    lines.append(
        f"> 由 LLM Cabinet v{__version__} 导出于 "
        f"{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}（schema v{SCHEMA_VERSION}）。"
    )
    lines.append("")

    # 基本信息
    # task #20 schema v4 起：老系统字段（作者/日期/评分/来源）的值从 field_values
    # 反查，按 field key 找；保留这 4 行展示是为了 README 的可读性
    key_to_field = {f.key: f for f in fields if f.key and f.id is not None}

    def _legacy(key: str) -> str:
        f = key_to_field.get(key)
        if f is None or f.id is None:
            return ""
        return project.field_values.get(f.id, "") or ""

    info_rows: list[tuple[str, str]] = []
    author_v = _legacy("author")
    if author_v:
        info_rows.append(("作者", author_v))
    date_v = _legacy("date")
    if date_v:
        info_rows.append(("日期", date_v))
    rating_v = _legacy("rating")
    if rating_v:
        try:
            r = int(rating_v)
            if r > 0:
                info_rows.append(("评分", "★" * r))
        except ValueError:
            pass
    source_v = _legacy("source_url")
    if source_v:
        info_rows.append(("来源", source_v))
    if project.tags:
        info_rows.append(("标签", "、".join(project.tags)))
    if info_rows:
        lines.append("## 基本信息")
        lines.append("")
        for k, v in info_rows:
            lines.append(f"- **{k}**：{v}")
        lines.append("")

    # 自定义字段值
    # task #20 schema v4 起：跳过已在「基本信息」段展示的 4 个老系统字段
    # （这些字段值已通过 _legacy 反查在上面渲染过；不能用 is_system 判断，
    # 因为 v4 后 is_system 仅表示"种入时带 key"，与"是否已展示"无关）
    LEGACY_INFO_KEYS = {"author", "date", "source_url", "rating"}
    custom = []
    field_by_id = {f.id: f for f in fields if f.id is not None}
    for fid, value in project.field_values.items():
        f = field_by_id.get(fid)
        if f is None or f.key in LEGACY_INFO_KEYS:
            continue
        if value:
            custom.append((f.name, value))
    if custom:
        lines.append("## 自定义字段")
        lines.append("")
        for k, v in custom:
            lines.append(f"- **{k}**：{v}")
        lines.append("")

    # 描述
    if project.description_md:
        lines.append("## 描述")
        lines.append("")
        lines.append(project.description_md.strip())
        lines.append("")

    # 文件清单
    if files:
        lines.append(f"## 文件清单（共 {len(files)} 个）")
        lines.append("")
        for f in files:
            storage = "📦" if f.is_relative else "🔗"
            label = f" — {f.label}" if f.label else ""
            lines.append(f"- {storage} `{Path(f.path).name}` ({f.kind}){label}")
        lines.append("")

    # 导出统计
    lines.append("## 导出统计")
    lines.append("")
    lines.append(f"- 复制文件：{result.n_files_copied} 个")
    lines.append(f"- 仅引用（未复制）：{result.n_files_referenced} 个")
    lines.append(f"- 复制字节数：{_human_size(result.total_bytes)}")
    if result.warnings:
        lines.append("")
        lines.append("### 警告")
        lines.append("")
        for w in result.warnings:
            lines.append(f"- ⚠️ {w}")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "> 本导出包含 `project.json`（结构化元数据）、`files.json`（文件清单）、"
        "`files/`（实际文件）。这是后续 LLM Cabinet 导入功能识别的标准结构。"
    )
    return "\n".join(lines)


def _write_json(path: Path, obj: dict) -> None:
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024:
            return f"{f:.1f} {unit}" if unit != "B" else f"{int(f)} B"
        f /= 1024
    return f"{f:.1f} PB"
