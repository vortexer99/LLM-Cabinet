"""首帧提取回归：仅使用临时自造视频，不访问数据库或用户文件。"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QProgressDialog

from app.video_cover import save_first_video_frame


def assert_error(source: Path, target: Path, expected: str, **kwargs: int) -> None:
    try:
        save_first_video_frame(source, target, **kwargs)
    except RuntimeError as exc:
        assert expected in str(exc), str(exc)
    else:
        raise AssertionError(f"未报告预期错误：{expected}")


def assert_red_image(target: Path) -> None:
    image = QImage(str(target))
    assert not image.isNull(), "保存的 PNG 不可读取"
    color = image.pixelColor(image.width() // 2, image.height() // 2)
    assert color.red() > 180 and color.green() < 50 and color.blue() < 50, color.name()


def child(mode: str, source: Path, target: Path) -> None:
    application = QApplication([]) if mode == "gui" else None
    save_first_video_frame(source, target)
    assert_red_image(target)
    if application is not None:
        class CancelProgress(QProgressDialog):
            def show(self) -> None:
                super().show()
                # 点击取消按钮会发出 canceled；cancel() 本身不保证发出该信号。
                QTimer.singleShot(0, self.canceled.emit)

        with patch("app.video_cover.QProgressDialog", CancelProgress):
            assert_error(source, target.parent / "cancelled.png", "取消")
        assert not (target.parent / "cancelled.png").exists()
    print(f"{mode}: 首帧颜色与 PNG 输出验证通过")


def main() -> None:
    if len(sys.argv) == 5 and sys.argv[1] == "--child":
        child(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
        return
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("此自测需要 ffmpeg 生成临时视频；产品提取功能不依赖 ffmpeg。")
    with tempfile.TemporaryDirectory(prefix="cabinet-video-first-frame-") as temporary:
        root = Path(temporary)
        source = root / "red-then-blue.mp4"
        # 第一帧仅持续一帧，后面全部为蓝色，能检测误取后续帧的回归。
        subprocess.run(
            [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=96x64:r=10:d=0.1",
             "-f", "lavfi", "-i", "color=c=blue:s=96x64:r=10:d=1",
             "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
            check=True, timeout=30,
        )
        for mode in ("core", "gui"):
            subprocess.run(
                [sys.executable, __file__, "--child", mode, str(source), str(root / f"{mode}.png")],
                check=True, timeout=30,
            )
        assert_error(source, root / "timeout.png", "超时", timeout_ms=1)
        assert not (root / "timeout.png").exists()
        invalid = root / "invalid.mp4"
        invalid.write_bytes(b"not a video")
        assert_error(invalid, root / "invalid.png", "无法解码")
        assert_error(root / "missing.mp4", root / "missing.png", "不存在")
        assert_error(source, root, "保存失败")
        # 失败后再次提取验证独立播放器清理不影响下一次调用。
        save_first_video_frame(source, root / "after-errors.png")
        assert_red_image(root / "after-errors.png")
    print("PASS: task47 视频首帧（临时夹具；无数据库访问）")


if __name__ == "__main__":
    main()
