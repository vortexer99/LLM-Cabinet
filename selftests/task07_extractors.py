"""task #07 自检：内容提取扩展（pptx / odt / odp / ods / epub / html / rtf）

构造**最小可用**样本验证 `app.llm.context` 中各解析器：
- 现造 zip 包模拟 pptx/odt/odp/ods/epub 的内部结构（不依赖任何已有文件）
- 普通文件直接写在临时目录里（html / rtf）

同时验证 `extraction_capability` 路由表与 build_messages 中 kind="doc" 分支保持一致。
"""
from __future__ import annotations

import io
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T

from app.llm.context import (  # noqa: E402
    EXTRACTION_FILENAME, EXTRACTION_FULL, EXTRACTION_IMAGE, EXTRACTION_TEXT,
    _read_epub, _read_html, _read_odf_text, _read_ods, _read_pptx, _read_rtf,
    extraction_capability, extraction_capability_label,
)


def main() -> int:
    t = T()
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpstr:
            tmp = Path(tmpstr)
            _run_all(tmp, t)
    except Exception:
        traceback.print_exc()
        return 1
    return 0 if t.report() else 1


# =============================================================================
# 样本构造工具
# =============================================================================
def _zip_write(target: Path, files: dict[str, bytes]) -> None:
    """造一个最小 zip 文件，files 是 ``arcname -> bytes``。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)


def _make_pptx(tmp: Path) -> Path:
    """最小 pptx：3 张幻灯片 + 第 2 张带备注。"""
    slide1 = (
        '<?xml version="1.0"?>'
        '<p:sld xmlns:p="x" xmlns:a="y">'
        '<p:cSld><p:spTree>'
        '<a:t>第一张：项目背景</a:t>'
        '<a:t>主题：星际探索</a:t>'
        '</p:spTree></p:cSld></p:sld>'
    )
    slide2 = (
        '<?xml version="1.0"?>'
        '<p:sld xmlns:p="x" xmlns:a="y">'
        '<a:t>第二张：技术方案</a:t>'
        '</p:sld>'
    )
    slide3 = (
        '<?xml version="1.0"?>'
        '<p:sld xmlns:p="x" xmlns:a="y">'
        '<a:t>第三张：未来工作</a:t>'
        '</p:sld>'
    )
    note2 = (
        '<?xml version="1.0"?>'
        '<p:notesSld xmlns:p="x" xmlns:a="y">'
        '<a:t>讲解员备注：演示动画在此处</a:t>'
        '</p:notesSld>'
    )
    pptx = tmp / "demo.pptx"
    _zip_write(pptx, {
        "ppt/slides/slide1.xml": slide1.encode("utf-8"),
        "ppt/slides/slide2.xml": slide2.encode("utf-8"),
        "ppt/slides/slide3.xml": slide3.encode("utf-8"),
        "ppt/notesSlides/notesSlide2.xml": note2.encode("utf-8"),
    })
    return pptx


def _make_odt(tmp: Path) -> Path:
    """最小 odt：content.xml 含若干段 <text:p>。"""
    content = (
        '<?xml version="1.0"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        '<office:body><office:text>'
        '<text:h>OpenDocument 标题</text:h>'
        '<text:p>第一段：测试中文</text:p>'
        '<text:p>第二段：with English mixed</text:p>'
        '</office:text></office:body>'
        '</office:document-content>'
    )
    odt = tmp / "demo.odt"
    _zip_write(odt, {"content.xml": content.encode("utf-8")})
    return odt


def _make_odp(tmp: Path) -> Path:
    """odp 与 odt 在 content.xml 层一致；只改文件名让扩展名分流到同一个 reader。"""
    content = (
        '<?xml version="1.0"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        '<office:body><office:presentation>'
        '<text:p>幻灯片标题：年度回顾</text:p>'
        '<text:p>要点：进展 / 风险 / 计划</text:p>'
        '</office:presentation></office:body>'
        '</office:document-content>'
    )
    odp = tmp / "demo.odp"
    _zip_write(odp, {"content.xml": content.encode("utf-8")})
    return odp


def _make_ods(tmp: Path) -> Path:
    """最小 ods：两行三列。"""
    content = (
        '<?xml version="1.0"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
        '<office:body><office:spreadsheet><table:table table:name="Sheet1">'
        '<table:table-row>'
        '<table:table-cell><text:p>姓名</text:p></table:table-cell>'
        '<table:table-cell><text:p>评分</text:p></table:table-cell>'
        '<table:table-cell><text:p>备注</text:p></table:table-cell>'
        '</table:table-row>'
        '<table:table-row>'
        '<table:table-cell><text:p>张三</text:p></table:table-cell>'
        '<table:table-cell><text:p>5</text:p></table:table-cell>'
        '<table:table-cell><text:p>非常好</text:p></table:table-cell>'
        '</table:table-row>'
        '</table:table></office:spreadsheet></office:body>'
        '</office:document-content>'
    )
    ods = tmp / "demo.ods"
    _zip_write(ods, {"content.xml": content.encode("utf-8")})
    return ods


def _make_epub(tmp: Path) -> Path:
    """最小 epub：META-INF/container.xml + content.opf + 2 章 xhtml。"""
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles>'
        '</container>'
    )
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        '<manifest>'
        '<item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest>'
        '<spine><itemref idref="ch1"/><itemref idref="ch2"/></spine>'
        '</package>'
    )
    ch1 = (
        '<?xml version="1.0"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><title>Ch1</title>'
        '<style>body { color: red; }</style>'
        '</head><body>'
        '<h1>第一章 起源</h1>'
        '<p>很久很久以前&hellip;主角诞生了。</p>'
        '<script>alert("noisy");</script>'
        '</body></html>'
    )
    ch2 = (
        '<?xml version="1.0"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><title>Ch2</title></head><body>'
        '<h1>第二章 旅程</h1>'
        '<p>在山的那一边&mdash;海的那一边。</p>'
        '</body></html>'
    )
    epub = tmp / "demo.epub"
    _zip_write(epub, {
        "META-INF/container.xml": container.encode("utf-8"),
        "OEBPS/content.opf": opf.encode("utf-8"),
        "OEBPS/ch1.xhtml": ch1.encode("utf-8"),
        "OEBPS/ch2.xhtml": ch2.encode("utf-8"),
    })
    return epub


def _make_html(tmp: Path) -> Path:
    p = tmp / "demo.html"
    p.write_text(
        "<!DOCTYPE html><html><head>"
        "<title>测试文档</title>"
        "<style>.x{color:red}</style>"
        "<script>console.log('noisy');</script>"
        "</head><body>"
        "<h1>正文标题</h1>"
        "<p>第一段：你好&nbsp;世界</p>"
        "<p>第二段：&amp; 后续内容</p>"
        "</body></html>",
        encoding="utf-8",
    )
    return p


def _make_rtf(tmp: Path) -> Path:
    """最小 RTF：含中文（用 \\u 转义）+ 控制字 + 分组。"""
    p = tmp / "demo.rtf"
    # 你好（U+4F60 U+597D）= \u20320? \u22909?
    rtf = (
        r"{\rtf1\ansi\ansicpg936\deff0"
        r"{\fonttbl{\f0 SimSun;}}"
        r"\f0\fs24"
        r"\u20320?\u22909?world\par "
        r"line2\par"
        r"}"
    )
    p.write_bytes(rtf.encode("latin-1"))
    return p


# =============================================================================
# 测试主体
# =============================================================================
def _run_all(tmp: Path, t: T) -> None:
    # ----------------------------------------------------------------
    # 1. extraction_capability 路由表
    # ----------------------------------------------------------------
    cases = {
        # (扩展名, 期望分类)
        ".pptx": EXTRACTION_FULL,
        ".pptm": EXTRACTION_FULL,
        ".docx": EXTRACTION_FULL,
        ".xlsx": EXTRACTION_FULL,
        ".xlsm": EXTRACTION_FULL,
        ".odt":  EXTRACTION_FULL,
        ".odp":  EXTRACTION_FULL,
        ".ods":  EXTRACTION_FULL,
        ".epub": EXTRACTION_FULL,
        ".html": EXTRACTION_FULL,
        ".htm":  EXTRACTION_FULL,
        ".rtf":  EXTRACTION_FULL,
        ".csv":  EXTRACTION_FULL,
        ".pdf":  EXTRACTION_FULL,
        # 纯文本
        ".txt":  EXTRACTION_TEXT,
        ".md":   EXTRACTION_TEXT,
        ".markdown": EXTRACTION_TEXT,
        # 代码 → text
        ".py":   EXTRACTION_TEXT,
        ".json": EXTRACTION_TEXT,
        ".yaml": EXTRACTION_TEXT,
        # 图像 → image
        ".png":  EXTRACTION_IMAGE,
        ".jpg":  EXTRACTION_IMAGE,
        # 不支持的二进制 → filename
        ".doc":  EXTRACTION_FILENAME,
        ".xls":  EXTRACTION_FILENAME,
        ".ppt":  EXTRACTION_FILENAME,
        ".mobi": EXTRACTION_FILENAME,
        # 视频 → filename
        ".mp4":  EXTRACTION_FILENAME,
        ".mkv":  EXTRACTION_FILENAME,
    }
    for ext, expected in cases.items():
        got = extraction_capability(Path(f"x{ext}"))
        t.assert_eq(f"capability {ext} -> {expected}", got, expected)

    # extraction_capability_label 三元组
    label, code, tip = extraction_capability_label("a.pptx")
    t.assert_eq("label/code: pptx -> full", code, EXTRACTION_FULL)
    t.assert_in("label: full 含 ✅", "✅", label)
    t.assert_true("tip: full 非空", bool(tip))
    label, code, _ = extraction_capability_label("a.doc")
    t.assert_eq("label/code: doc -> filename", code, EXTRACTION_FILENAME)
    t.assert_in("label: filename 含 ⚠", "⚠", label)

    # ----------------------------------------------------------------
    # 2. _read_pptx
    # ----------------------------------------------------------------
    pptx = _make_pptx(tmp)
    text = _read_pptx(pptx)
    t.assert_in("pptx: 含幻灯片 1 标题", "项目背景", text)
    t.assert_in("pptx: 含幻灯片 2", "技术方案", text)
    t.assert_in("pptx: 含幻灯片 3", "未来工作", text)
    t.assert_in("pptx: 备注被附在幻灯片 2", "讲解员备注", text)
    t.assert_in("pptx: 幻灯片号已加上", "# 幻灯片 1", text)
    # 顺序：第 1 张 在 第 2 张 之前
    t.assert_true(
        "pptx: slide1 顺序在 slide2 之前",
        text.index("项目背景") < text.index("技术方案"),
    )

    # 损坏的 pptx → 不抛异常，给出错误信息
    bad = tmp / "bad.pptx"
    bad.write_bytes(b"not a zip")
    bad_text = _read_pptx(bad)
    t.assert_in("pptx 损坏: 错误信息开头", "(", bad_text[:5])

    # ----------------------------------------------------------------
    # 3. _read_odf_text (odt / odp 共享)
    # ----------------------------------------------------------------
    odt = _make_odt(tmp)
    text = _read_odf_text(odt)
    t.assert_in("odt: 标题", "OpenDocument 标题", text)
    t.assert_in("odt: 段 1", "第一段", text)
    t.assert_in("odt: 段 2 中英混排", "English mixed", text)

    odp = _make_odp(tmp)
    text = _read_odf_text(odp)
    t.assert_in("odp: 标题", "年度回顾", text)
    t.assert_in("odp: 要点", "进展", text)

    # ----------------------------------------------------------------
    # 4. _read_ods
    # ----------------------------------------------------------------
    ods = _make_ods(tmp)
    text = _read_ods(ods)
    t.assert_in("ods: 含表头列", "姓名", text)
    t.assert_in("ods: 行用 | 分隔", "张三 | 5 | 非常好", text)
    t.assert_eq(
        "ods: 行数 = 2",
        len([ln for ln in text.splitlines() if ln.strip()]), 2,
    )

    # ----------------------------------------------------------------
    # 5. _read_epub
    # ----------------------------------------------------------------
    epub = _make_epub(tmp)
    text = _read_epub(epub)
    t.assert_in("epub: 第一章标题", "第一章 起源", text)
    t.assert_in("epub: 第二章标题", "第二章 旅程", text)
    t.assert_in("epub: HTML 实体已还原 (&hellip;)", "…", text)
    t.assert_in("epub: HTML 实体已还原 (&mdash;)", "—", text)
    t.assert_true(
        "epub: script 被剥",
        "alert" not in text and "noisy" not in text,
    )
    t.assert_true(
        "epub: style 被剥",
        "color: red" not in text,
    )
    t.assert_true(
        "epub: 章节顺序正确",
        text.index("第一章") < text.index("第二章"),
    )

    # ----------------------------------------------------------------
    # 6. _read_html
    # ----------------------------------------------------------------
    html = _make_html(tmp)
    text = _read_html(html)
    t.assert_in("html: 标题", "正文标题", text)
    t.assert_in("html: 段 1（含 nbsp 还原）", "你好", text)
    t.assert_in("html: 段 2（含 amp 还原）", "& 后续内容", text)
    t.assert_true(
        "html: script 被剥",
        "console.log" not in text,
    )
    t.assert_true(
        "html: style 被剥",
        "color:red" not in text,
    )

    # ----------------------------------------------------------------
    # 7. _read_rtf
    # ----------------------------------------------------------------
    rtf = _make_rtf(tmp)
    text = _read_rtf(rtf)
    t.assert_in("rtf: 中文 \\u 转义被还原（你）", "你", text)
    t.assert_in("rtf: 中文 \\u 转义被还原（好）", "好", text)
    t.assert_in("rtf: 英文部分", "world", text)
    t.assert_in("rtf: 第二行", "line2", text)
    t.assert_true(
        "rtf: 控制字已剥",
        "rtf1" not in text and "fonttbl" not in text,
    )


if __name__ == "__main__":
    sys.exit(main())
