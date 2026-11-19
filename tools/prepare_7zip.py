"""将官方完整 7-Zip x64 运行组件准备到仓库的 .tools/7zip/。

来源：https://www.7-zip.org/download.html
固定发行：https://github.com/ip7z/7zip/releases/tag/26.06
SHA256 来自上述官方发行资产的实际下载内容；升级时须同时核对版本和摘要。
只运行校验后的独立 7zr.exe，将安装包作为 7z 归档读取；从不执行安装包，
不调用安装器、不修改注册表或 PATH。运行组件包含支持 RAR 的完整 7z.dll。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request


VERSION = "26.06"
RELEASE_URL = f"https://github.com/ip7z/7zip/releases/download/{VERSION}"
DOWNLOADS = {
    "7z2606-x64.exe": "9b697f65ecc2d9b0152f5ee96d72dde522a7827690349d9307935dacf40f42ec",
    "7zr.exe": "e80db2dad715a6c5adabe292d31ed54e11f4e9b99ca349827b50573b175891bc",
}
RUNTIME_HASHES = {
    "7z.exe": "dc0493429fe28136fcb177a0e77c9875cbb3b0bff5f2f8eeba4df302e98cef76",
    "7z.dll": "500db9eb17f3c21b729ba41dd02ecfe1823da05028c7e4ba129084cc74cc364e",
    "License.txt": "519ac0a4bded9c18ea02e0afb71f663d8c47373bd9facd3ac96a79f51d77765d",
    "readme.txt": "de4a45e9dcdabfd21e5e0e31e29079998f216db15079217b6160e92b1c151e40",
}
NOTICE = f"""7-Zip {VERSION} (Windows x64), Copyright (C) 1999-2026 Igor Pavlov.
This distribution includes unmodified official 7z.exe and 7z.dll.
License: GNU LGPL 2.1 or later, with BSD notices and the unRAR restriction.
See the accompanying original License.txt for complete license information.
Project: https://www.7-zip.org/
Binary: {RELEASE_URL}/7z2606-x64.exe
Corresponding source: {RELEASE_URL}/7z2606-src.7z
GNU LGPL 2.1: https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html
"""
NOTICE_NAME = "THIRD_PARTY_NOTICES.txt"


def _sha256(path: Path) -> str:
    """分块校验文件，避免将下载内容整体载入内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_runtime(folder: Path) -> None:
    """拒绝缺失、替换或经符号链接跳转的运行文件。"""
    for name, expected in RUNTIME_HASHES.items():
        path = folder / name
        if path.resolve() != folder / name or not path.is_file():
            raise RuntimeError(f"运行组件缺失或路径异常：{path}")
        if _sha256(path) != expected:
            raise RuntimeError(f"运行组件 SHA256 不匹配：{path}")
    notice = folder / NOTICE_NAME
    if notice.resolve() != notice or not notice.is_file():
        raise RuntimeError(f"第三方版权说明缺失或路径异常：{notice}")
    if notice.read_text(encoding="utf-8") != NOTICE:
        raise RuntimeError(f"第三方版权说明不匹配：{notice}")


def _download(name: str, expected: str, folder: Path) -> Path:
    """仅下载固定的官方资产，校验完成前不得运行。"""
    path = folder / name
    request = urllib.request.Request(
        f"{RELEASE_URL}/{name}", headers={"User-Agent": "LLM-Cabinet-prepare-7zip"}
    )
    with urllib.request.urlopen(request, timeout=60) as response, path.open("xb") as output:
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > 16 * 1024 * 1024:
                raise RuntimeError(f"官方资产大小异常：{name}")
            output.write(chunk)
    if _sha256(path) != expected:
        raise RuntimeError(f"下载文件 SHA256 不匹配：{name}")
    return path


def prepare() -> Path:
    """在独立临时目录准备完整运行时，确认后原子移动；不覆盖既有目录。"""
    if os.name != "nt":
        raise RuntimeError("该脚本仅准备 Windows x64 运行组件，请在 Windows 上执行。")
    root = Path(__file__).resolve().parents[1]
    tools = root / ".tools"
    # 防止已有 junction / symlink 将写入或临时目录清理引向仓库外。
    if tools.resolve() != tools:
        raise RuntimeError(f"工具目录不能是符号链接或目录联接：{tools}")
    tools.mkdir(exist_ok=True)
    target = tools / "7zip"
    if target.resolve() != target:
        raise RuntimeError(f"运行目录不能是符号链接或目录联接：{target}")
    if target.exists():
        _verify_runtime(target)
        return target / "7z.exe"

    # TemporaryDirectory 仅清理由本脚本在已验证 .tools 下新建的随机目录。
    with tempfile.TemporaryDirectory(prefix="prepare-7zip-", dir=tools) as temporary:
        staging = Path(temporary).resolve()
        assets = {
            name: _download(name, expected, staging)
            for name, expected in DOWNLOADS.items()
        }
        extracted = staging / "runtime"
        result = subprocess.run(
            [
                str(assets["7zr.exe"]), "x", str(assets["7z2606-x64.exe"]),
                f"-o{extracted}", "-y", "-spd", *RUNTIME_HASHES,
            ],
            cwd=staging,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            details = result.stdout.decode("utf-8", errors="replace")
            raise RuntimeError(f"读取官方安装包失败：\n{details}")
        (extracted / NOTICE_NAME).write_text(NOTICE, encoding="utf-8")
        _verify_runtime(extracted)
        # Windows 下 rename 不覆盖已存在的目标；并发准备也不会替换用户文件。
        extracted.rename(target)
    return target / "7z.exe"


def main() -> int:
    try:
        executable = prepare()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"准备 7-Zip 失败：{error}", file=sys.stderr)
        return 1
    print(f"7-Zip {VERSION} x64 已就绪：{executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
