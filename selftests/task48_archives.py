"""归档解压回归：仅使用临时自造数据；--real-fixtures 另下载官方 RAR 测试夹具。"""

from __future__ import annotations

import argparse
import bz2
import gzip
import hashlib
import io
import lzma
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
from unittest.mock import patch
import urllib.request
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import archives
from app.archives import ArchiveCancelled, ArchiveError, ArchivePasswordRequired, extract_archive


def expect_error(source: Path, target: Path, kind: type[Exception] = ArchiveError,
                 **kwargs: object) -> str:
    before = hashlib.sha256(source.read_bytes()).digest()
    try:
        extract_archive(source, target, **kwargs)
    except kind as exc:
        assert not target.exists(), f"失败后遗留了解压目录：{target}"
        assert hashlib.sha256(source.read_bytes()).digest() == before, "源压缩包被修改"
        return str(exc)
    raise AssertionError(f"未抛出预期错误：{source.name}")


def make_zip(path: Path, names: list[str]) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                archive.writestr(name, b"test data")


def basic_tests(root: Path) -> None:
    assert archives.archive_folder_name(Path("hello.tar.gz")) == "hello"
    assert archives.archive_folder_name(Path("HELLO.TAR.BZ2")) == "HELLO"
    assert archives.archive_folder_name(Path(".zip")) == "压缩包"
    assert archives.archive_folder_name(Path("CON.zip")) == "_CON"
    source = root / "nested.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("目录/中文.txt", "内容".encode())
        archive.writestr("empty/", b"")
        archive.writestr("plain.txt", b"plain")
    target = root / "nested-output"
    paths = extract_archive(source, target)
    assert set(paths) == {Path("目录/中文.txt"), Path("plain.txt")}
    assert (target / "目录/中文.txt").read_text(encoding="utf-8") == "内容"
    assert (target / "empty").is_dir()
    occupied = root / "occupied"
    occupied.mkdir()
    sentinel = occupied / "keep.txt"
    sentinel.write_bytes(b"keep")
    try:
        extract_archive(source, occupied)
    except ArchiveError:
        assert sentinel.read_bytes() == b"keep"
    else:
        raise AssertionError("覆盖了已有目标目录")

    # 独立压缩流不要求内部为 tar；名称只去掉对应压缩后缀。
    for suffix, compress in (("gz", gzip.compress), ("bz2", bz2.compress), ("xz", lzma.compress)):
        source = root / f"single.txt.{suffix}"
        source.write_bytes(compress(b"single stream"))
        target = root / f"single-{suffix}"
        assert extract_archive(source, target) == [Path("single.txt")]
        assert (target / "single.txt").read_bytes() == b"single stream"


def safety_tests(root: Path) -> None:
    # 每次使用独立压缩包和目标；即使已有条目先写出，失败也必须全部清理。
    unsafe = ["../escape.txt", "/absolute.txt", "C:/drive.txt", "dir/../escape.txt",
              r"..\escape.txt", r"\server\share.txt", "file.txt:stream", "CON.txt",
              "dir/NUL", "name.", "name ", "dir/COM1.log"]
    for index, name in enumerate(unsafe):
        source = root / f"unsafe-{index}.zip"
        make_zip(source, ["first.txt", name])
        expect_error(source, root / f"unsafe-output-{index}")
    source = root / "nul-name.zip"
    make_zip(source, ["bad!hidden"])
    source.write_bytes(source.read_bytes().replace(b"bad!hidden", b"bad\x00hidden"))
    expect_error(source, root / "nul-name-output")
    for index, names in enumerate((["same.txt", "same.txt"], ["Case.txt", "case.txt"],
                                   ["dir/file.txt", "DIR/other.txt"], ["dir", "dir/file.txt"])):
        source = root / f"duplicate-{index}.zip"
        make_zip(source, names)
        expect_error(source, root / f"duplicate-output-{index}")
    source = root / "symlink.zip"
    with zipfile.ZipFile(source, "w") as archive:
        entry = zipfile.ZipInfo("link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, "../outside")
    expect_error(source, root / "symlink-output")
    for suffix in ("zip", "7z", "rar", "tar"):
        source = root / f"damaged.{suffix}"
        source.write_bytes(b"not an archive\x00")
        expect_error(source, root / f"damaged-output-{suffix}")
    source = root / "cancel.zip"
    make_zip(source, ["one.txt", "two.txt"])
    cancelled = False

    def progress(_done: int, _total: int, _name: str) -> None:
        nonlocal cancelled
        cancelled = True

    expect_error(source, root / "cancel-output", ArchiveCancelled,
                 progress=progress, is_cancelled=lambda: cancelled)
    with patch.object(archives, "MAX_ARCHIVE_ENTRIES", 1):
        expect_error(source, root / "entry-limit")
    with patch.object(archives, "MAX_UNPACKED_BYTES", 1):
        expect_error(source, root / "size-limit")
    assert not (root.parent / "escape.txt").exists()


def tar_tests(root: Path) -> None:
    for suffix, mode in (("tar", "w"), ("tar.gz", "w:gz"), ("tbz2", "w:bz2"), ("txz", "w:xz")):
        source = root / f"nested.{suffix}"
        with tarfile.open(source, mode) as archive:
            directory = tarfile.TarInfo("./")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
            directory = tarfile.TarInfo("folder")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
            archive.addfile(directory)
            entry = tarfile.TarInfo("folder/data.txt")
            entry.size = 7
            archive.addfile(entry, io.BytesIO(b"payload"))
        target = root / f"tar-output-{suffix}"
        assert extract_archive(source, target) == [Path("folder/data.txt")]
        assert (target / "folder/data.txt").read_bytes() == b"payload"
    for index, entry_type in enumerate((tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE,
                                        tarfile.CHRTYPE, tarfile.BLKTYPE)):
        source = root / f"link-{index}.tar"
        with tarfile.open(source, "w") as archive:
            entry = tarfile.TarInfo("link")
            entry.type = entry_type
            entry.linkname = "../outside"
            archive.addfile(entry)
        expect_error(source, root / f"link-output-{index}")
    for index, name in enumerate(("../", "/absolute", "C:/drive")):
        source = root / f"tar-unsafe-{index}.tar"
        with tarfile.open(source, "w") as archive:
            entry = tarfile.TarInfo(name)
            entry.type = tarfile.DIRTYPE
            archive.addfile(entry)
        expect_error(source, root / f"tar-unsafe-output-{index}")


def sevenzip_tests(root: Path) -> None:
    engine = archives._find_7zip()
    if engine is None:
        print("SKIP: full 7-Zip runtime not available")
        return
    fixture = root / "fixture"
    fixture.mkdir()
    (fixture / "data.txt").write_bytes(b"private fixture data")
    (fixture / "目录").mkdir()
    (fixture / "目录/中文.txt").write_text("测试内容", encoding="utf-8")

    def create(name: str, options: list[str]) -> Path:
        source = root / name
        subprocess.run([engine, "a", "-bd", "-bso0", "-bsp0", *options, str(source), "."],
                       cwd=fixture, check=True, stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       creationflags=archives._creationflags(), timeout=30)
        return source

    for name, options in (("normal.7z", ["-t7z"]),
                           ("encrypted.7z", ["-t7z", "-psecret", "-mhe=on"]),
                           ("aes.zip", ["-tzip", "-mem=AES256", "-psecret"]),
                           ("zipcrypt.zip", ["-tzip", "-mem=ZipCrypto", "-psecret"])):
        source = create(name, options)
        encrypted = "-psecret" in options
        if encrypted:
            expect_error(source, root / (name + "-missing"), ArchivePasswordRequired)
            detail = expect_error(source, root / (name + "-wrong"), ArchivePasswordRequired, password="wrong")
            assert "wrong" not in detail, "错误提示暴露了密码"
        target = root / (name + "-success")
        files = extract_archive(source, target, password="secret" if encrypted else None)
        assert set(files) == {Path("data.txt"), Path("目录/中文.txt")}
        assert (target / "data.txt").read_bytes() == b"private fixture data"
        assert (target / "目录/中文.txt").read_text(encoding="utf-8") == "测试内容"
    # 明确验证无引擎路径，避免完整引擎掩盖标准库密码回归。
    source = root / "zipcrypt.zip"
    with patch.object(archives, "_find_7zip", return_value=None):
        expect_error(source, root / "stdlib-missing", ArchivePasswordRequired)
        expect_error(source, root / "stdlib-wrong", ArchivePasswordRequired, password="bad")
        target = root / "stdlib-success"
        extract_archive(source, target, password="secret")
        assert (target / "data.txt").read_bytes() == b"private fixture data"
        # bsdtar 退路也读取真实 7z，且与完整引擎保持普通文件结果一致。
        target = root / "bsdtar-success"
        files = extract_archive(root / "normal.7z", target)
        assert set(files) == {Path("data.txt"), Path("目录/中文.txt")}
        assert (target / "data.txt").read_bytes() == b"private fixture data"
    # 工具按内容识别格式；即使后缀伪装，结构化清单中的链接也不能落盘。
    for entry_type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
        source = root / f"engine-link-{entry_type!r}.7z"
        with tarfile.open(source, "w") as archive:
            entry = tarfile.TarInfo("link")
            entry.type = entry_type
            entry.linkname = "../outside"
            archive.addfile(entry)
        expect_error(source, root / f"engine-link-output-{entry_type!r}")
    # 时间限制为零时必须在进程启动前退出，也不能创建目标目录。
    with patch.object(archives, "ARCHIVE_TIMEOUT_SECONDS", -1):
        expect_error(root / "normal.7z", root / "engine-timeout")
    print("PASS: real 7z, header encryption, AES ZIP, ZipCrypto and password retry")


def rar_fixture_tests(root: Path) -> None:
    """夹具仅下载到 TemporaryDirectory；来自 rarfile 官方项目，无用户数据。"""
    base = "https://raw.githubusercontent.com/markokr/rarfile/master/test/files/"
    for name, encrypted in (("rar5-crc.rar", False), ("rar5-psw.rar", True), ("rar5-hpsw.rar", True)):
        source = root / name
        with urllib.request.urlopen(base + name, timeout=30) as response:
            data = response.read(64 * 1024)
        assert data.startswith(b"Rar!"), "下载的官方夹具不是 RAR"
        source.write_bytes(data)
        if encrypted:
            expect_error(source, root / (name + "-missing"), ArchivePasswordRequired)
            expect_error(source, root / (name + "-wrong"), ArchivePasswordRequired, password="wrong")
        target = root / (name + "-output")
        paths = extract_archive(source, target, password="password" if encrypted else None)
        assert set(paths) == {Path("stest1.txt"), Path("stest2.txt")}
        assert (target / "stest1.txt").stat().st_size == 2048
        assert (target / "stest2.txt").stat().st_size == 2048
        assert (target / "stest1.txt").read_bytes() == (target / "stest2.txt").read_bytes()
    print("PASS: official real RAR fixtures, encrypted data and encrypted headers")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-fixtures", action="store_true", help="下载官方 RAR 夹具到临时目录进行真实回归")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="llm-cabinet-archives-test-") as directory:
        root = Path(directory)
        basic_tests(root)
        safety_tests(root)
        tar_tests(root)
        sevenzip_tests(root)
        if args.real_fixtures:
            rar_fixture_tests(root)
    print("PASS: task48 archive extraction tests (temporary fixtures only; no database access)")


if __name__ == "__main__":
    main()
