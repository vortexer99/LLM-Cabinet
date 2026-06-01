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

# task #11 T1：单字段 prompt_hint 最大长度（防上下文爆炸）
PROMPT_HINT_MAX_CHARS = 500

# 直接以文本方式安全读取的扩展（utf-8/gbk）
PLAIN_TEXT_EXTS = {".txt", ".md", ".markdown", ".csv"}
# 二进制压缩文档：直接读会出乱码，按类型走专门提取或仅给文件名
# 已实现提取的：.docx / .xlsx / .pptx / .odt / .ods / .odp / .epub / .rtf / .html
# 仍未实现的：老 OLE 二进制（.doc/.xls/.ppt）、.mobi
# .xlsm/.pptm 等带宏的现代格式与对应 .xlsx/.pptx 同结构，识别为同类
BINARY_DOC_EXTS = {
    ".doc", ".xls", ".ppt",   # 老 OLE 二进制
    ".mobi",
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


def _read_pptx(path: Path, char_limit: int = TEXT_CHARS_LIMIT) -> str:
    """提取 pptx/pptm：按幻灯片号顺序抓 ppt/slides/slideN.xml 中 <a:t> 文本。

    - 备注（``ppt/notesSlides/notesSlideN.xml``）若存在，附在该幻灯片末尾，前缀「备注：」
    - 按 ``slide<n>.xml`` 中的 N 排序，避免 ZIP 顺序乱序
    """
    import re
    import xml.etree.ElementTree as ET
    import zipfile

    def _texts_from(xml_bytes: bytes) -> list[str]:
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            return []
        out: list[str] = []
        for el in root.iter():
            if el.tag.split("}")[-1] == "t" and el.text:
                out.append(el.text)
        return out

    try:
        with zipfile.ZipFile(str(path)) as z:
            names = z.namelist()
            slide_re = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
            slides = sorted(
                (int(m.group(1)), n)
                for n in names if (m := slide_re.match(n))
            )
            note_map: dict[int, str] = {}
            note_re = re.compile(r"^ppt/notesSlides/notesSlide(\d+)\.xml$")
            for n in names:
                m = note_re.match(n)
                if m:
                    note_map[int(m.group(1))] = n

            out: list[str] = []
            for idx, name in slides:
                texts = _texts_from(z.read(name))
                if not texts:
                    continue
                block = [f"# 幻灯片 {idx}", *texts]
                note_name = note_map.get(idx)
                if note_name:
                    note_texts = _texts_from(z.read(note_name))
                    if note_texts:
                        block.append("备注：" + " ".join(note_texts))
                out.append("\n".join(block))
                if sum(len(x) for x in out) > char_limit:
                    break
            text = "\n\n".join(out)
            return text[:char_limit] if text else "(pptx 内无文本)"
    except Exception as e:
        return f"(pptx 解析失败: {e})"


# OpenDocument (.odt/.odp) 使用 content.xml + <text:p>/<text:h>/<text:span>
# .ods 走 <table:table-row>/<table:table-cell> 单独处理
def _read_odf_text(path: Path, char_limit: int = TEXT_CHARS_LIMIT) -> str:
    """提取 odt / odp：抓 content.xml 中所有 text:* 节点的文本。

    odt 是文档、odp 是演示。两者结构都基于 OpenDocument 的 ``<text:p>``/``<text:h>``/
    ``<text:span>`` 容器，逻辑可以共用。
    """
    import xml.etree.ElementTree as ET
    import zipfile
    try:
        with zipfile.ZipFile(str(path)) as z:
            if "content.xml" not in z.namelist():
                return "(odf 内未找到 content.xml)"
            root = ET.fromstring(z.read("content.xml"))
            parts: list[str] = []
            for el in root.iter():
                tag = el.tag.split("}")[-1]
                # 段落 / 标题：在前面补换行
                if tag in ("p", "h"):
                    parts.append("\n")
                if el.text:
                    parts.append(el.text)
                # tail 文本（标签后紧跟的字符）
                if el.tail and el.tail.strip():
                    parts.append(el.tail)
            text = "".join(parts)
            lines = [ln.strip() for ln in text.splitlines()]
            text = "\n".join([ln for ln in lines if ln])
            return text[:char_limit] if text else "(odf 内无文本)"
    except Exception as e:
        return f"(odf 解析失败: {e})"


def _read_ods(path: Path, char_limit: int = TEXT_CHARS_LIMIT) -> str:
    """提取 ods（OpenDocument 表格）：按行 ``<table:table-row>`` 抓单元格。

    与 xlsx 的 sharedStrings 不同，ods 把单元格文本直接内联在 ``<text:p>``
    子节点里。这里把每行单元格用 ``|`` 拼接，与 xlsx 的输出风格一致。
    """
    import xml.etree.ElementTree as ET
    import zipfile
    try:
        with zipfile.ZipFile(str(path)) as z:
            if "content.xml" not in z.namelist():
                return "(ods 内未找到 content.xml)"
            root = ET.fromstring(z.read("content.xml"))
            out: list[str] = []
            for el in root.iter():
                if el.tag.split("}")[-1] != "table-row":
                    continue
                cells: list[str] = []
                for cell in el:
                    if cell.tag.split("}")[-1] != "table-cell":
                        continue
                    parts: list[str] = []
                    for sub in cell.iter():
                        if sub.text:
                            parts.append(sub.text)
                    text = "".join(parts).strip()
                    if text:
                        cells.append(text)
                if cells:
                    out.append(" | ".join(cells))
                if sum(len(x) for x in out) > char_limit:
                    break
            text = "\n".join(out)
            return text[:char_limit] if text else "(ods 内无文本数据)"
    except Exception as e:
        return f"(ods 解析失败: {e})"


def _read_epub(path: Path, char_limit: int = TEXT_CHARS_LIMIT) -> str:
    """提取 epub：按 spine 顺序读章节的 xhtml/html，stdlib html.parser 抽文本。

    流程：``META-INF/container.xml`` → 找 OPF → 用 ``manifest`` 把 id 映射到 href，
    再按 ``spine`` 顺序（章节顺序）拉每个 xhtml；OPF 找不到时退化为按文件名顺序。
    """
    import re
    import xml.etree.ElementTree as ET
    import zipfile
    try:
        with zipfile.ZipFile(str(path)) as z:
            names = z.namelist()
            spine_files: list[str] = []

            # 1) 先找 OPF：META-INF/container.xml -> rootfile@full-path
            opf_path = None
            if "META-INF/container.xml" in names:
                try:
                    root = ET.fromstring(z.read("META-INF/container.xml"))
                    for el in root.iter():
                        if el.tag.split("}")[-1] == "rootfile":
                            opf_path = el.attrib.get("full-path")
                            break
                except ET.ParseError:
                    opf_path = None

            base = ""
            if opf_path and opf_path in names:
                base = "/".join(opf_path.split("/")[:-1])
                if base:
                    base += "/"
                try:
                    root = ET.fromstring(z.read(opf_path))
                except ET.ParseError:
                    root = None
                if root is not None:
                    href_by_id: dict[str, str] = {}
                    for el in root.iter():
                        if el.tag.split("}")[-1] == "item":
                            iid = el.attrib.get("id", "")
                            href = el.attrib.get("href", "")
                            if iid and href:
                                href_by_id[iid] = href
                    for el in root.iter():
                        if el.tag.split("}")[-1] == "itemref":
                            idref = el.attrib.get("idref", "")
                            href = href_by_id.get(idref)
                            if href:
                                full = base + href
                                if full in names:
                                    spine_files.append(full)

            # 2) Fallback：按文件名顺序找 .xhtml / .html
            if not spine_files:
                spine_files = sorted(
                    n for n in names
                    if n.lower().endswith((".xhtml", ".html", ".htm"))
                )

            tag_re = re.compile(r"<[^>]+>")
            ws_re = re.compile(r"\s+")
            out: list[str] = []
            for fname in spine_files:
                try:
                    raw = z.read(fname).decode("utf-8", errors="replace")
                except Exception:
                    continue
                # 去 script/style 整段
                raw = re.sub(
                    r"<(script|style)[^>]*>.*?</\1>",
                    " ", raw, flags=re.DOTALL | re.IGNORECASE,
                )
                stripped = tag_re.sub(" ", raw)
                # HTML 实体粗略还原（保持轻量，不引第三方）
                import html as _html
                stripped = _html.unescape(stripped)
                stripped = ws_re.sub(" ", stripped).strip()
                if stripped:
                    out.append(stripped)
                if sum(len(x) for x in out) > char_limit:
                    break
            text = "\n\n".join(out)
            return text[:char_limit] if text else "(epub 内无文本)"
    except Exception as e:
        return f"(epub 解析失败: {e})"


def _read_html(path: Path, char_limit: int = TEXT_CHARS_LIMIT) -> str:
    """从 .html / .htm 抽纯文本（去 script/style/标签 + 折叠空白）。"""
    import re
    raw = ""
    for enc in ("utf-8", "gbk"):
        try:
            raw = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
        except Exception as e:
            return f"(html 读取失败: {e})"
    if not raw:
        try:
            raw = path.read_bytes().decode("utf-8", errors="replace")
        except Exception as e:
            return f"(html 读取失败: {e})"
    raw = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        " ", raw, flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", raw)
    import html as _html
    text = _html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:char_limit] if text else "(html 内无文本)"


def _read_rtf(path: Path, char_limit: int = TEXT_CHARS_LIMIT) -> str:
    """轻量 RTF 解析：去转义/控制字 + 处理 \\u 与 \\'XX 转义。

    不追求 100% 还原，仅保证文本可读、不出现一大堆 ``\rtf1...`` 控制序列。
    """
    import re
    try:
        raw = path.read_bytes().decode("latin-1", errors="replace")
    except Exception as e:
        return f"(rtf 读取失败: {e})"
    # \uN -> 对应 Unicode 字符。RTF 规范规定 \uN 后会跟 1 个 ANSI 替代字符
    # （一般是 "?"），让旧版 reader 还能显示。可选空白；替代字符可有可无；
    # 不吃 \ 或 { } 这种 RTF 控制字符。
    def _unicode_repl(m: "re.Match[str]") -> str:
        try:
            n = int(m.group(1))
            if n < 0:
                n += 65536
            return chr(n)
        except ValueError:
            return ""
    # 优先级：\?  > 1 个非控制字符；二者择其一即可，避免连吃
    raw = re.sub(r"\\u(-?\d+)(?:\?|\s)?", _unicode_repl, raw)
    # \'XX -> 对应 ANSI 字符（gbk 兜底，准确度有限但不会爆）
    def _hex_repl(m: "re.Match[str]") -> str:
        try:
            return bytes([int(m.group(1), 16)]).decode("gbk", errors="replace")
        except Exception:
            return ""
    raw = re.sub(r"\\'([0-9a-fA-F]{2})", _hex_repl, raw)
    # 移除常见的控制字（\par 转换成换行，其它直接吃）
    raw = re.sub(r"\\par[d]?\b", "\n", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", raw)
    raw = raw.replace("\\*", "")
    # 去掉 RTF 分组
    raw = raw.replace("{", "").replace("}", "")
    # 折叠空白
    lines = [ln.strip() for ln in raw.splitlines()]
    text = "\n".join([ln for ln in lines if ln])
    return text[:char_limit] if text else "(rtf 内无文本)"


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
    # 字段级 prompt 提示（task #11 T1）：每个字段的 prompt_hint 会追加到该字段的描述行后；
    # 单条 hint 截断到 PROMPT_HINT_MAX_CHARS 字符避免上下文爆炸
    def _line(f: Field) -> str:
        s = f"- **{f.name}** (类型: {f.type})"
        if f.type == "tags":
            s += "  — 多值字段，请返回 JSON 数组，例如 [\"科幻\", \"翻译\"]"
        hint = (f.prompt_hint or "").strip()
        if hint:
            if len(hint) > PROMPT_HINT_MAX_CHARS:
                hint = hint[:PROMPT_HINT_MAX_CHARS] + "…"
            s += f"\n  · 格式要求：{hint}"
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
            if ext == ".xlsx" or ext == ".xlsm":
                text = _read_xlsx(path)
            elif ext == ".docx":
                text = _read_docx(path)
            elif ext in (".pptx", ".pptm"):
                text = _read_pptx(path)
            elif ext in (".odt", ".odp"):
                text = _read_odf_text(path)
            elif ext == ".ods":
                text = _read_ods(path)
            elif ext == ".epub":
                text = _read_epub(path)
            elif ext in (".html", ".htm"):
                text = _read_html(path)
            elif ext == ".rtf":
                text = _read_rtf(path)
            elif ext == ".csv":
                text = _read_csv(path)
            elif ext in PLAIN_TEXT_EXTS:
                text = _read_text_file(path, TEXT_CHARS_LIMIT)
            elif ext in BINARY_DOC_EXTS:
                # 老 OLE 二进制（.doc/.xls/.ppt）/ .mobi：未实现解析，避免乱码
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


# =============================================================================
# 内容提取能力查询（task #07 短期方案配套；UI 给用户预览每个文件能否被提取）
# =============================================================================
# 维护原则：与 build_messages 中的 kind=="doc" / "code" / "image" 分支一一对应。
# 当 _read_xxx 新增/调整时，同步更新这里。
EXTRACTION_FULL = "full"        # 走专门解析器，能拿到正文文本
EXTRACTION_TEXT = "text"        # 直接当文本读（utf-8/gbk）
EXTRACTION_IMAGE = "image"      # 直接附图给支持图像的 provider
EXTRACTION_FILENAME = "filename"  # 仅文件名作线索；视频 / .doc/.xls/.ppt / .mobi
EXTRACTION_NONE = "none"        # 兜底（理论不会出现）

_EXTRACTION_LABELS = {
    EXTRACTION_FULL: "✅ 文本提取",
    EXTRACTION_TEXT: "✅ 纯文本",
    EXTRACTION_IMAGE: "🖼 图像直传",
    EXTRACTION_FILENAME: "⚠ 仅文件名",
    EXTRACTION_NONE: "—",
}

# 已有专门解析器的扩展（kind=="doc"），与上方 build_messages 分支保持同步
_FULL_EXTRACT_EXTS = {
    ".pdf",  # _read_pdf
    ".docx", ".xlsx", ".xlsm", ".pptx", ".pptm",
    ".odt", ".odp", ".ods",
    ".epub",
    ".html", ".htm", ".rtf",
    ".csv",
}


def extraction_capability(path) -> str:
    """对 ``path`` 返回 ``EXTRACTION_*`` 之一。

    供 UI 在调用 LLM 之前提示用户某个文件的内容能否被提取（task #07 短期方案）。
    """
    from pathlib import Path as _P
    p = _P(path) if not isinstance(path, _P) else path
    ext = p.suffix.lower()
    kind = detect_kind(p)
    if kind == "image":
        return EXTRACTION_IMAGE
    if kind == "video":
        return EXTRACTION_FILENAME
    if ext in _FULL_EXTRACT_EXTS:
        return EXTRACTION_FULL
    if ext in BINARY_DOC_EXTS:
        return EXTRACTION_FILENAME
    if ext in PLAIN_TEXT_EXTS:
        return EXTRACTION_TEXT
    if kind == "code":
        return EXTRACTION_TEXT
    if kind == "doc":
        # 兜底：未知 doc 扩展名（既不在 PLAIN_TEXT_EXTS 也不在专门解析器里）→ 当文本读
        return EXTRACTION_TEXT
    # other：除了名字什么都给不了
    return EXTRACTION_FILENAME


def extraction_capability_label(path) -> tuple[str, str, str]:
    """返回 (短标签, 状态码, tooltip)，给 UI 直接用。

    例：``("✅ 文本提取", "full", "将解析 docx/xlsx/... 等结构化文档抽取正文")``
    """
    code = extraction_capability(path)
    label = _EXTRACTION_LABELS.get(code, code)
    tip_map = {
        EXTRACTION_FULL: "走专用解析器抽取结构化文档正文（pptx/docx/xlsx/odt/ods/odp/epub/pdf/html/rtf/csv）",
        EXTRACTION_TEXT: "按纯文本读取（utf-8 / gbk 兜底）",
        EXTRACTION_IMAGE: "图片会以原图传给支持视觉的 provider（限 4 张 × 3 MB）",
        EXTRACTION_FILENAME: "暂不支持解析此格式；只有文件名会作为提示发给 LLM",
        EXTRACTION_NONE: "未知",
    }
    return label, code, tip_map.get(code, "")


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
