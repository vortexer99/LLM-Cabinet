"""封面缩略图缓存（task #33）。

要点：
- ``QImageReader.setScaledSize`` 在解码阶段即缩放，避免全尺寸原图进内存
- 结果进 ``QPixmapCache``（上限 64MB）；key 含路径 + mtime + 尺寸，
  文件内容变动后自动失效
- 返回的 pixmap 带 ``devicePixelRatio``，调用方按逻辑尺寸直接绘制即可，
  paint 热点不再做 ``SmoothTransformation`` 实时缩放

只服务"卡片/列表小图"；预览面板仍读原图，不经过本缓存。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImageReader, QPixmap, QPixmapCache

_CACHE_LIMIT_KB = 64 * 1024  # 64MB
_limit_initialized = False


def _ensure_limit() -> None:
    global _limit_initialized
    if not _limit_initialized:
        QPixmapCache.setCacheLimit(_CACHE_LIMIT_KB)
        _limit_initialized = True


def get_cover(path: Path, target: QSize, dpr: float = 1.0) -> QPixmap | None:
    """返回适配 ``target``（逻辑尺寸）的封面缩略图；读不到/非图片返回 None。

    Args:
        path: 图片文件绝对路径
        target: 目标逻辑尺寸（如卡片封面区 148x168）
        dpr: 设备像素比，缩略图按 ``target * dpr`` 解码并记录在 pixmap 上
    """
    _ensure_limit()
    try:
        st = Path(path).stat()
    except OSError:
        return None
    dpr = max(1.0, float(dpr))
    key = (
        f"cover:{path}:{st.st_mtime_ns}:{st.st_size}:"
        f"{target.width()}x{target.height()}@{dpr:.2f}"
    )
    cached = QPixmapCache.find(key)
    if cached is not None:
        return cached

    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    src = reader.size()
    if not src.isValid() or src.isEmpty():
        return None
    px_target = QSize(int(target.width() * dpr), int(target.height() * dpr))
    reader.setScaledSize(src.scaled(px_target, Qt.KeepAspectRatio))
    img = reader.read()
    if img.isNull():
        return None
    pix = QPixmap.fromImage(img)
    pix.setDevicePixelRatio(dpr)
    QPixmapCache.insert(key, pix)
    return pix
