"""把 Project + 参考文件 转成 LLM messages。"""
from __future__ import annotations

import csv as _csv
import json
from pathlib import Path
from typing import Iterable

from ..models import Field, FileItem, Project
from ..utils import code_language, detect_kind
from .prompts import SYSTEM_TEMPLATE, USER_PROMPT_TEMPLATE


# 文本类文件最多读取的字符数（避免 token 爆炸）
TEXT_CHARS_LIMIT = 8_000
PDF_CHARS_LIMIT = 12_000
PDF_PAGES_LIMIT = 6
IMAGE_BYTES_LIMIT = 3 * 1024 * 1024  # 3MB 每张
MAX_IMAGES = 4

# 直接以文本方式安全读取的扩展（utf-8/gbk）
PLAIN_TEXT_EXTS = {".txt", ".md", ".markdown", ".rtf", ".html", ".htm", ".csv"}
# 二进制压缩文档：直接读会出乱码，按类型走专门提取或仅给文件名
BINARY_DOC_EXTS = {
    ".docx", ".odt",
    ".xlsx", ".ods",
    ".pptx", ".odp",
    ".epub", ".mobi",
    ".doc", ".xls", ".ppt",  # 老 OLE 二进制
}


def _read_text_file(path: Path, limit: int) -> str:
    try:
        for enc in ("utf-8", "gbk"):
            try:
                return path.read_text(encoding=enc)[:limit]
            except UnicodeDecodeError:
                continue
        return path.read_bytes().decode("utf-8", errors="replace")[:limit]
    except Exception as e:
        return f"(读取失败: {e})"


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return "(未安装 pypdf，无法解析 PDF)"
    try:
        reader = PdfReader(str(path))
        out = []
        for i, page in enumerate(reader.pages):
            if i >= PDF_PAGES_LIMIT:
                break
            try:
                out.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n".join(out)
        return text[:PDF_CHARS_LIMIT]
    except Exception as e:
        return f"(PDF 解析失败: {e})"


def _read_xlsx(path: Path, char_limit: int = TEXT_CHARS_LIMIT) -> str:
    """轻量提取 xlsx 文本：通过 zipfile + xml 直接抓 sharedStrings 与 sheet 中可见值，
    不依赖 openpyxl。返回的字符串大致按"行：a, b, c"格式呈现。
    """
    import re
    import xml.etree.ElementTree as ET
    import zipfile
    try:
        with zipfile.ZipFile(str(path)) as z:
            names = z.namelist()
            # sharedStrings.xml
            ss: list[str] = []
            if "xl/sharedStrings.xml" in names:
                try:
                    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
                    # 命名空间剥离
                    for si in root.iter():
                        if si.tag.endswith("}si") or si.tag == "si":
                            text = "".join(t.text or "" for t in si.iter() if t.tag.endswith("}t") or t.tag == "t")
                            ss.append(text)
                except Exception:
                    ss = []
            sheets = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
            out: list[str] = []
            for sheet in sheets:
                try:
                    root = ET.fromstring(z.read(sheet))
                except Exception:
                    continue
                # 行
                for row in root.iter():
                    if not (row.tag.endswith("}row") or row.tag == "row"):
                        continue
                    cells: list[str] = []
                    for c in row:
                        if not (c.tag.endswith("}c") or c.tag == "c"):
                            continue
                        t = c.attrib.get("t", "")
                        v_text = ""
                        for child in c:
                            tag = child.tag.split("}")[-1]
                            if tag == "v":
                                v_text = (child.text or "").strip()
                            elif tag == "is":  # inlineStr
                                v_text = "".join((tt.text or "") for tt in child.iter() if tt.tag.endswith("}t"))
                        if t == "s":
                            try:
                                v_text = ss[int(v_text)] if v_text else ""
                            except Exception:
                                pass
                        if v_text:
                            cells.append(v_text.strip())
                    if cells:
                        out.append(" | ".join(cells))
                    if sum(len(x) for x in out) > char_limit:
                        break
                if sum(len(x) for x in out) > char_limit:
                    break
            text = "\n".join(out)
            return text[:char_limit] if text else "(xlsx 内无文本数据)"
    except Exception as e:
        return f"(xlsx 解析失败: {e})"


def _read_docx(path: Path, char_limit: int = TEXT_CHARS_LIMIT) -> str:
    """轻量提取 docx：抓 word/document.xml 里所有 <w:t> 文本。"""
    import xml.etree.ElementTree as ET
    import zipfile
    try:
        with zipfile.ZipFile(str(path)) as z:
            if "word/document.xml" not in z.namelist():
                return "(docx 中未找到 document.xml)"
            root = ET.fromstring(z.read("word/document.xml"))
            parts: list[str] = []
            for el in root.iter():
                tag = el.tag.split("}")[-1]
                if tag == "t" and el.text:
                    parts.append(el.text)
                elif tag in ("p", "br"):
                    parts.append("\n")
            text = "".join(parts)
            # 折叠多余空行
            lines = [ln.strip() for ln in text.splitlines()]
            text = "\n".join([ln for ln in lines if ln])
            return text[:char_limit] if text else "(docx 内无文本)"
    except Exception as e:
        return f"(docx 解析失败: {e})"


def _read_csv(path: Path, char_limit: int = TEXT_CHARS_LIMIT) -> str:
    out: list[str] = []
    try:
        for enc in ("utf-8", "gbk"):
            try:
                with path.open("r", encoding=enc, newline="") as f:
                    reader = _csv.reader(f)
                    for row in reader:
                        out.append(" | ".join(row))
                        if sum(len(x) for x in out) > char_limit:
                            break
                break
            except UnicodeDecodeError:
                continue
        text = "\n".join(out)
        return text[:char_limit] if text else "(csv 为空)"
    except Exception as e:
        return f"(csv 解析失败: {e})"


def build_messages(
    project: Project,
    context_fields: list[Field],
    target_fields: list[Field],
    files: Iterable[tuple[FileItem, Path]],
    user_note: str,
    language: str = "中文",
    *,
    allow_images: bool = True,
    all_files: list[FileItem] | None = None,
) -> list[dict]:
    """
    context_fields: 所有可见字段 —— 它们的当前值都注入到 prompt 作为上下文。
    target_fields:  context_fields 的子集，仅为这些字段征求 LLM 建议；
                    LLM 不应为不在此列表中的字段返回值。
    files: (FileItem, 解析后绝对路径) —— 用户勾选的"参考文件"，会真的把内容/图片塞进去。
    all_files: 项目内的全部文件（FileItem）。仅取文件名 + 说明 + 类型作为线索清单，
               不读内容、不传图片；让 LLM 从文件名也能感知项目全貌。
    """
    target_ids = {f.id for f in target_fields if f.id is not None}
    target_keys = {f.key for f in target_fields if f.key}

    def _is_target(f: Field) -> bool:
        if f.id is not None and f.id in target_ids:
            return True
        if f.key and f.key in target_keys:
            return True
        return False

    # ---- 当前元数据（所有上下文字段）
    cur: dict[str, str] = {}
    for f in context_fields:
        if f.is_system:
            if f.key == "title": cur[f.name] = project.title
            elif f.key == "author": cur[f.name] = project.author
            elif f.key == "date": cur[f.name] = project.date
            elif f.key == "rating":
                cur[f.name] = str(project.rating) if project.rating else ""
            elif f.key == "source_url": cur[f.name] = project.source_url
            elif f.key == "description":
                cur[f.name] = (project.description_md or "")[:300]
            elif f.key == "tags":
                cur[f.name] = ", ".join(project.tags)
        else:
            if f.id is not None:
                cur[f.name] = project.field_values.get(f.id, "")
    current_meta = json.dumps(
        {k: v for k, v in cur.items() if v},
        ensure_ascii=False, indent=2,
    ) or "(无)"

    # ---- 字段定义说明：分两组（tags 类型给一句额外说明）
    def _line(f: Field) -> str:
        s = f"- **{f.name}** (类型: {f.type})"
        if f.type == "tags":
            s += "  — 多值字段，请返回 JSON 数组，例如 [\"科幻\", \"翻译\"]"
        return s
    target_lines = [_line(f) for f in target_fields]
    context_only_lines = [_line(f) for f in context_fields if not _is_target(f)]
    target_fields_desc = "\n".join(target_lines) or "(空 —— 无需返回任何字段)"
    context_fields_desc = "\n".join(context_only_lines) or "(无)"

    # ---- 文件部分
    text_blocks: list[str] = []
    images: list[tuple[bytes, str]] = []  # (bytes, mime)

    for fi, path in files:
        if not path or not path.exists():
            continue
        kind = detect_kind(path)
        label = f.label if (f := fi).label else ""
        head = f"### 文件：{path.name}" + (f"（说明：{label}）" if label else "")

        if kind == "image" and allow_images and len(images) < MAX_IMAGES:
            try:
                size = path.stat().st_size
                if size <= IMAGE_BYTES_LIMIT:
                    data = path.read_bytes()
                    mime = _mime_for_image(path.suffix.lower())
                    images.append((data, mime))
                    text_blocks.append(head + f"\n（已作为图片附加，文件大小 {size} 字节）")
                else:
                    text_blocks.append(head + f"\n（图片过大 {size} 字节，跳过）")
            except Exception as e:
                text_blocks.append(head + f"\n（图片读取失败: {e}）")
        elif kind == "pdf":
            text = _read_pdf(path)
            text_blocks.append(head + "\n```\n" + text + "\n```")
        elif kind == "doc":
            ext = path.suffix.lower()
            if ext in (".xlsx",):
                text = _read_xlsx(path)
            elif ext in (".docx",):
                text = _read_docx(path)
            elif ext in (".csv",):
                text = _read_csv(path)
            elif ext in PLAIN_TEXT_EXTS:
                text = _read_text_file(path, TEXT_CHARS_LIMIT)
            elif ext in BINARY_DOC_EXTS:
                # 老 OLE 二进制 / odt / pptx / epub 等：未实现解析，避免乱码
                try:
                    size = path.stat().st_size
                except Exception:
                    size = 0
                text = (f"(此类型暂不支持文本提取，文件大小约 {size} 字节；"
                        f"请仅根据文件名 {path.name!r} 推断内容)")
            else:
                text = _read_text_file(path, TEXT_CHARS_LIMIT)
            text_blocks.append(head + "\n```\n" + text + "\n```")
        elif kind == "code":
            lang = code_language(path)
            text = _read_text_file(path, TEXT_CHARS_LIMIT)
            fence_open = f"```{lang}" if lang else "```"
            text_blocks.append(head + "\n" + fence_open + "\n" + text + "\n```")
        else:
            # 视频/其它：仅给文件名
            text_blocks.append(head + "\n（不支持的类型，仅作为提示）")

    files_section = "\n\n".join(text_blocks) or "(用户未选择参考文件)"

    # ---- 项目内全部文件清单（不读内容，仅给文件名+类型+说明）
    kind_emoji = {"image": "🖼", "video": "🎬", "pdf": "📄", "doc": "📝",
                  "code": "💻", "other": "📦"}
    chosen_ids = {fi.id for fi, _ in files if fi.id is not None}
    all_lines: list[str] = []
    for fi in (all_files or []):
        emoji = kind_emoji.get(fi.kind or "other", "📦")
        name = Path(fi.path).name
        marker = " ⭐" if fi.id in chosen_ids else ""
        label = f"  — {fi.label}" if fi.label else ""
        all_lines.append(f"- {emoji} {name}{marker}{label}")
    all_files_section = "\n".join(all_lines) if all_lines else "(项目暂无文件)"
    if chosen_ids:
        all_files_section += '\n\n（⭐ 标记的文件已在下方"参考文件"小节给出实际内容）'

    # ---- 拼装
    system_msg = SYSTEM_TEMPLATE.format(language=language)
    user_text = USER_PROMPT_TEMPLATE.format(
        current_meta=current_meta,
        all_files_section=all_files_section,
        context_fields_desc=context_fields_desc,
        target_fields_desc=target_fields_desc,
        user_note=(user_note or "(无)").strip(),
        files_section=files_section,
    )

    user_content: list[dict] = [{"type": "text", "text": user_text}]
    for data, mime in images:
        user_content.append({"type": "image", "data": data, "mime": mime})

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_content},
    ]


def _mime_for_image(ext: str) -> str:
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(ext, "image/png")


def parse_response(text: str) -> dict[str, str]:
    """从模型返回中提取 JSON 对象。容错：去掉可能的 ```json fence。
    数组值（如 tags）会被转成 ", " 连接的字符串，方便统一作为单值字段建议处理。
    """
    s = text.strip()
    if s.startswith("```"):
        # 去掉首尾 fence
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    try:
        data = json.loads(s)
        if not isinstance(data, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in data.items():
            if v is None:
                continue
            if isinstance(v, list):
                items = [str(x).strip() for x in v if str(x).strip()]
                if items:
                    out[str(k)] = ", ".join(items)
            else:
                sv = str(v).strip()
                if sv:
                    out[str(k)] = sv
        return out
    except Exception:
        return {}
