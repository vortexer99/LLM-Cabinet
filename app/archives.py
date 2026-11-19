"""压缩包的受限流式解压：只写独占创建的临时目录，不修改源文件。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import bz2
import gzip
import logging
import lzma
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from typing import BinaryIO
import zipfile


ARCHIVE_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".zip", ".7z", ".rar",
                    ".tar", ".tgz", ".tbz", ".tbz2", ".txz", ".gz", ".bz2", ".xz")
MAX_ARCHIVE_ENTRIES = 100_000
MAX_UNPACKED_BYTES = 100 * 1024 ** 3
ARCHIVE_TIMEOUT_SECONDS = 60 * 60
_CHUNK_SIZE = 1024 * 1024
_PROGRESS_INTERVAL = 0.05
_DEVICE_NAME = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$", re.I)
_Progress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class ArchiveProgress:
    """解压进度快照；未知总量保持 None，字节计数使用 Python 整数。"""

    completed_files: int
    total_files: int | None
    completed_bytes: int
    total_bytes: int | None
    current_name: str
    current_bytes: int
    current_total: int | None
    finished: bool = False


class ArchiveError(RuntimeError):
    """压缩包不受支持、损坏或包含不安全条目。"""


class ArchiveCancelled(ArchiveError):
    """用户取消了解压；函数创建的目录已清理。"""


class ArchivePasswordRequired(ArchiveError):
    """压缩包需要密码，或提供的密码不正确。"""


def archive_folder_name(source: Path) -> str:
    """取适合作为逻辑目录的压缩包基本名，合并去掉 tar.gz 等多重后缀。"""
    name = Path(source).name
    for suffix in ARCHIVE_SUFFIXES:
        if name.lower().endswith(suffix):
            name = name[:-len(suffix)]
            break
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(". ")
    if not name or name in (".", ".."):
        return "压缩包"
    if _DEVICE_NAME.fullmatch(name.split(".", 1)[0]):
        name = "_" + name
    return name


def _safe_relative(name: str) -> Path:
    """同时采用 Windows 路径规则，避免测试平台不同导致漏检。"""
    if not name or name.startswith(("/", "\\")):
        raise ArchiveError(f"压缩包包含绝对路径或空路径：{name!r}")
    parts = name.replace("\\", "/").split("/")
    clean: list[str] = []
    for part in parts:
        if part in ("", "."):
            continue
        if (part == ".." or part.endswith((".", " "))
                or re.search(r'[<>:"|?*\x00-\x1f]', part)
                or _DEVICE_NAME.fullmatch(part.split(".", 1)[0])):
            raise ArchiveError(f"压缩包包含不安全路径：{name!r}")
        clean.append(part)
    if not clean:
        raise ArchiveError(f"压缩包包含空路径：{name!r}")
    return Path(*clean)


class _Extraction:
    """每个调用独立保存路径冲突、资源限制和取消状态。"""

    def __init__(self, root: Path, progress: _Progress | None,
                 is_cancelled: Callable[[], bool] | None,
                 detail_progress: Callable[[ArchiveProgress], None] | None = None) -> None:
        self.root = root
        self.progress = progress
        self.is_cancelled = is_cancelled
        self.deadline = time.monotonic() + ARCHIVE_TIMEOUT_SECONDS
        self.paths: dict[str, tuple[str, bool, bool]] = {}
        self.files: list[Path] = []
        self.entries = 0
        self.bytes = 0
        self.detail_progress = detail_progress
        self.total_files: int | None = None
        self.total_bytes: int | None = None
        self.completed_bytes = 0
        self.current_name = ""
        self.current_bytes = 0
        self.current_total: int | None = None
        self._last_progress = float("-inf")

    def emit(self, *, force: bool = False, finished: bool = False) -> None:
        """分块更新节流，文件切换、文件结尾和整体成功始终发送。"""
        if self.detail_progress is None:
            return
        now = time.monotonic()
        if not force and now - self._last_progress < _PROGRESS_INTERVAL:
            return
        self._last_progress = now
        self.detail_progress(ArchiveProgress(
            len(self.files), self.total_files, self.completed_bytes, self.total_bytes,
            self.current_name, self.current_bytes, self.current_total, finished))

    def set_totals(self, total_files: int, total_bytes: int) -> None:
        self.total_files = total_files
        self.total_bytes = total_bytes
        self.emit(force=True)

    def begin_file(self, relative: Path, size: int | None) -> None:
        self.current_name = str(relative)
        self.current_bytes = 0
        self.current_total = size
        self.emit(force=True)

    def advance(self, count: int) -> None:
        self.current_bytes += count
        self.completed_bytes += count
        self.emit()

    def complete_file(self, relative: Path) -> None:
        """只有数据校验及外部工具均成功，才计入已完成文件。"""
        self.check()
        self.files.append(relative)
        self.emit(force=True)
        if self.progress is not None:
            self.progress(len(self.files), self.total_files if self.total_files is not None else 0,
                          str(relative))

    def check(self) -> None:
        if self.is_cancelled is not None and self.is_cancelled():
            raise ArchiveCancelled("已取消解压")
        if time.monotonic() > self.deadline:
            raise ArchiveError("解压超时，已清理临时目录")

    def target(self, relative: Path) -> Path:
        target = self.root / relative
        if not target.resolve().is_relative_to(self.root):
            raise ArchiveError(f"解压路径超出目标目录：{relative}")
        return target

    def register(self, name: str, directory: bool, size: int) -> Path:
        self.check()
        self.entries += 1
        if self.entries > MAX_ARCHIVE_ENTRIES:
            raise ArchiveError(f"压缩包条目数超过限制（{MAX_ARCHIVE_ENTRIES}）")
        if size < 0 or self.bytes + size > MAX_UNPACKED_BYTES:
            raise ArchiveError("压缩包解压后总大小超过限制（100 GB）")
        self.bytes += size
        relative = _safe_relative(name)
        for index in range(1, len(relative.parts) + 1):
            path = Path(*relative.parts[:index])
            original = path.as_posix()
            key = original.casefold()
            final = index == len(relative.parts)
            is_dir = directory if final else True
            previous = self.paths.get(key)
            if previous is not None:
                old_name, old_dir, explicit = previous
                if old_name != original or old_dir != is_dir or (final and explicit and not is_dir):
                    raise ArchiveError(f"压缩包包含重复路径或大小写冲突：{name}")
                if final:
                    self.paths[key] = (original, is_dir, True)
            else:
                self.paths[key] = (original, is_dir, final)
        self.target(relative)
        return relative

    def write(self, relative: Path, source: BinaryIO, size: int, *, complete: bool = True) -> None:
        target = self.target(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        remaining = size
        self.begin_file(relative, size)
        # 独占创建并按已声明大小读取，既防覆盖，也限制解压炸弹实际写入量。
        with target.open("xb") as output:
            while remaining:
                self.check()
                chunk = source.read(min(_CHUNK_SIZE, remaining))
                if not chunk:
                    raise ArchiveError(f"压缩包数据不完整：{relative}")
                output.write(chunk)
                remaining -= len(chunk)
                self.advance(len(chunk))
            self.check()
            if source.read(1):
                raise ArchiveError(f"压缩包条目大小与声明不一致：{relative}")
        self.emit(force=True)
        if complete:
            self.complete_file(relative)


def _extract_zip(source: Path, state: _Extraction, password: str | None) -> None:
    with zipfile.ZipFile(source) as archive:
        entries: list[tuple[zipfile.ZipInfo, Path]] = []
        for entry in archive.infolist():
            mode = entry.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ArchiveError(f"压缩包包含链接或特殊文件：{entry.filename}")
            if entry.flag_bits & 1 and password is None:
                raise ArchivePasswordRequired("压缩包需要密码")
            # orig_filename 保留 NUL，filename 则会被 zipfile 截断。
            if "\x00" in entry.orig_filename:
                raise ArchiveError("压缩包文件名包含非法字符")
            relative = state.register(entry.filename, entry.is_dir(), entry.file_size)
            entries.append((entry, relative))
        state.set_totals(sum(not entry.is_dir() for entry, _ in entries),
                         sum(entry.file_size for entry, _ in entries if not entry.is_dir()))
        for entry, relative in entries:
            state.check()
            if entry.is_dir():
                state.target(relative).mkdir(parents=True, exist_ok=True)
            else:
                try:
                    with archive.open(entry, pwd=password.encode("utf-8") if password is not None else None) as stream:
                        state.write(relative, stream, entry.file_size)
                except NotImplementedError:
                    raise ArchiveError("当前系统不支持此 ZIP 加密或压缩方式，请运行 tools/prepare_7zip.py 准备 7-Zip") from None
                except RuntimeError as exc:
                    if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
                        raise ArchivePasswordRequired("压缩包密码不正确，请重试") from None
                    raise


def _extract_tar(stream: BinaryIO, state: _Extraction, *, streaming: bool) -> None:
    with tarfile.open(fileobj=stream, mode="r|" if streaming else "r:*") as archive:
        for entry in archive:
            state.check()
            # 根目录条目 ./ 在常规 tar 中很常见；无需落盘。
            if entry.isdir() and entry.name in (".", "./"):
                continue
            if not (entry.isdir() or entry.isfile()) or entry.issparse():
                raise ArchiveError(f"压缩包包含链接、稀疏文件或特殊文件：{entry.name}")
            relative = state.register(entry.name, entry.isdir(), entry.size)
            if entry.isdir():
                state.target(relative).mkdir(parents=True, exist_ok=True)
            else:
                member = archive.extractfile(entry)
                if member is None:
                    raise ArchiveError(f"无法读取压缩包条目：{entry.name}")
                with member:
                    state.write(relative, member, entry.size)


def _extract_compressed_file(source: Path, state: _Extraction) -> None:
    """独立 gzip/bzip2/xz 流没有可信的解压大小，逐块限制输出总量。"""
    opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}[source.suffix.lower()]
    relative = state.register(archive_folder_name(source), False, 0)
    state.begin_file(relative, None)
    with opener(source, "rb") as stream, state.target(relative).open("xb") as output:
        while True:
            state.check()
            chunk = stream.read(_CHUNK_SIZE)
            if not chunk:
                break
            state.bytes += len(chunk)
            if state.bytes > MAX_UNPACKED_BYTES:
                raise ArchiveError("压缩包解压后总大小超过限制（100 GB）")
            output.write(chunk)
            state.advance(len(chunk))
    state.emit(force=True)
    state.complete_file(relative)


def _find_bsdtar() -> str:
    candidates: list[str] = []
    if os.name == "nt":
        candidates.append(str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"))
    executable = shutil.which("bsdtar")
    if executable:
        candidates.append(executable)
    for candidate in candidates:
        try:
            result = subprocess.run([candidate, "--version"], stdin=subprocess.DEVNULL,
                                    capture_output=True, timeout=10, creationflags=_creationflags())
            version = result.stdout.lower()
            if result.returncode == 0 and b"bsdtar" in version and b"libarchive" in version:
                return candidate
        except (OSError, subprocess.SubprocessError):
            continue
    raise ArchiveError("当前系统缺少 7Z/RAR 解压引擎，请运行 tools/prepare_7zip.py 准备 7-Zip")


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _find_7zip() -> str | None:
    """优先使用随应用准备/打包的完整 7-Zip，随后尝试系统已安装版本。"""
    candidates: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(Path(bundle) / "tools" / "7zip" / "7z.exe")
    candidates.append(Path(__file__).resolve().parents[1] / ".tools" / "7zip" / "7z.exe")
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / "7-Zip" / "7z.exe")
    for name in ("7z", "7zz"):
        executable = shutil.which(name)
        if executable:
            candidates.append(Path(executable))
    return next((str(path) for path in candidates if path.is_file()), None)


def _sevenzip_run(command: list[str], state: _Extraction,
                  consume: Callable[[BinaryIO], None], password: str | None) -> None:
    """并行监视取消/超时，错误只输出过滤后的工具信息，不记录带密码的命令。"""
    stopped = threading.Event()
    interrupted: list[ArchiveError] = []
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=errors, shell=False, creationflags=_creationflags(),
                                   start_new_session=os.name != "nt")

        def watch() -> None:
            while not stopped.wait(0.1):
                try:
                    state.check()
                except ArchiveError as exc:
                    interrupted.append(exc)
                    if process.poll() is None:
                        process.kill()
                    return

        watcher = threading.Thread(target=watch, name="7zip-cancellation", daemon=True)
        watcher.start()
        failure: Exception | None = None
        try:
            assert process.stdout is not None
            try:
                consume(process.stdout)
            except Exception as exc:
                failure = exc
                if process.poll() is None:
                    process.kill()
            finally:
                process.stdout.close()
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            stopped.set()
            watcher.join(timeout=2)
        if interrupted:
            raise interrupted[0]
        state.check()
        errors.seek(0)
        detail = errors.read(8192).decode("utf-8", errors="replace").strip()
        if password:
            detail = detail.replace(password, "***")
        lowered = detail.lower()
        if process.returncode and any(token in lowered for token in (
                "wrong password", "password is incorrect", "can not open encrypted archive")):
            raise ArchivePasswordRequired("压缩包需要密码或密码不正确，请重试") from None
        if isinstance(failure, ArchiveError) and not process.returncode:
            raise failure
        if process.returncode and not isinstance(failure, ArchiveCancelled):
            # 我们主动杀死进程时保留原始路径/资源限制错误。
            if isinstance(failure, ArchiveError) and not detail:
                raise failure
            raise ArchiveError("无法解压：压缩包损坏或压缩方式不受支持"
                               + (f"\n{detail}" if detail else "")) from failure
        if failure is not None:
            raise failure


def _extract_7zip(source: Path, state: _Extraction, executable: str,
                  password: str | None) -> None:
    if source.suffix.lower() == ".zip":
        # 7-Zip 可能显示已规范化的 ZIP 文件名；先验证原始中央目录避免 NUL 截断。
        validation = _Extraction(state.root, None, state.is_cancelled)
        with zipfile.ZipFile(source) as archive:
            for entry in archive.infolist():
                if "\x00" in entry.orig_filename:
                    raise ArchiveError("压缩包文件名包含非法字符")
                file_type = stat.S_IFMT(entry.external_attr >> 16)
                if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ArchiveError(f"压缩包包含链接或特殊文件：{entry.filename}")
                validation.register(entry.filename, entry.is_dir(), entry.file_size)
    # 随机哨兵密码令无密码归档正常读取，加密归档报错，避免 -p 空值交互询问。
    password_option = "-p" + (password if password else secrets.token_hex(32))
    common = ["-sccUTF-8", password_option]
    chunks: list[bytes] = []

    def collect(stream: BinaryIO) -> None:
        length = 0
        while True:
            state.check()
            chunk = stream.read(_CHUNK_SIZE)
            if not chunk:
                break
            length += len(chunk)
            if length > 128 * 1024 ** 2:
                raise ArchiveError("压缩包目录清单超过安全限制")
            chunks.append(chunk)

    _sevenzip_run([executable, "l", "-slt", "-ba", *common, "--", str(source)],
                  state, collect, password)
    try:
        listing = b"".join(chunks).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ArchiveError("压缩包目录清单包含无法识别的文件名") from None
    records: list[dict[str, str]] = []
    record: dict[str, str] = {}
    for line in listing.splitlines():
        if not line:
            if record:
                records.append(record)
                record = {}
            continue
        key, separator, value = line.partition(" = ")
        if not separator or key in record or (not record and key != "Path"):
            raise ArchiveError("压缩包目录清单格式异常，不能安全解压")
        record[key] = value
    if record:
        records.append(record)
    entries: list[tuple[str, Path, bool, int]] = []
    for record in records:
        state.check()
        name = record.get("Path", "")
        attributes = record.get("Attributes", "")
        unix_mode = attributes.split()[-1] if attributes.split() else ""
        if (any(record.get(key) for key in ("Symbolic Link", "Hard Link", "Reparse Point"))
                or (len(unix_mode) >= 10 and unix_mode[0] not in ("-", "d", "A", "D"))
                or record.get("Anti") == "+" or record.get("Alternate Stream") == "+"):
            raise ArchiveError(f"压缩包包含链接或特殊文件：{name}")
        directory = record.get("Folder") == "+" or attributes.startswith("D")
        try:
            size = int(record.get("Size", "0" if directory else ""))
        except ValueError:
            raise ArchiveError(f"压缩包条目大小无效：{name}") from None
        if record.get("Encrypted") == "+" and password is None:
            raise ArchivePasswordRequired("压缩包需要密码")
        relative = state.register(name, directory, size)
        entries.append((name, relative, directory, size))
    state.set_totals(sum(not directory for _, _, directory, _ in entries),
                     sum(size for _, _, directory, size in entries if not directory))
    for name, relative, directory, size in entries:
        state.check()
        if directory:
            state.target(relative).mkdir(parents=True, exist_ok=True)
            continue

        def write(stream: BinaryIO) -> None:
            state.write(relative, stream, size, complete=False)

        # -spd 禁用通配符；-so 仅输出数据，7-Zip 从不获准直接写入文件系统。
        # 包含选择器使用 -i!，防止 @name 位置参数被工具当作本地清单文件读取。
        _sevenzip_run([executable, "x", "-so", "-spd", "-r-", "-i!" + name,
                      *common, "--", str(source)],
                      state, write, password)
        state.complete_file(relative)


def _extract_libarchive(source: Path, state: _Extraction, password: str | None) -> None:
    executable = _find_bsdtar()
    # 仅转换到 stdout；-P 保留原始绝对路径供上层拒绝，不执行 -x 文件系统解压。
    # PAX 保存结构化类型/大小/Unicode 文件名，避免从 -tv 文本猜测链接类型。
    command = [executable, "-c", "-P", "--format=pax", "-f", "-", "@" + str(source)]
    stopped = threading.Event()
    interrupted: list[ArchiveError] = []
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=errors, shell=False, creationflags=_creationflags(),
                                   start_new_session=os.name != "nt")

        def watch() -> None:
            # 在后台终止被管道读取阻塞的子进程，主线程随后从 EOF 返回。
            while not stopped.wait(0.1):
                try:
                    state.check()
                except ArchiveError as exc:
                    interrupted.append(exc)
                    if process.poll() is None:
                        process.kill()
                    return

        watcher = threading.Thread(target=watch, name="archive-cancellation", daemon=True)
        watcher.start()
        failure: Exception | None = None
        try:
            assert process.stdout is not None
            try:
                _extract_tar(process.stdout, state, streaming=True)
                # tarfile 在尾块处结束；继续排空，保证进程不会卡在 stdout 写入。
                while process.stdout.read(_CHUNK_SIZE):
                    state.check()
            except Exception as exc:
                failure = exc
                if process.poll() is None:
                    process.kill()
            finally:
                process.stdout.close()
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            stopped.set()
            watcher.join(timeout=2)
        if interrupted:
            raise interrupted[0]
        state.check()
        errors.seek(0)
        detail = errors.read(8192).decode("utf-8", errors="replace").strip()
        if password:
            detail = detail.replace(password, "***")
        lowered = detail.lower()
        encrypted = any(word in lowered for word in ("passphrase", "password", "encrypt"))
        if process.returncode and encrypted:
            if any(word in lowered for word in ("not supported", "unsupported", "unavailable")):
                raise ArchiveError("当前系统不支持此压缩包的加密方式，请运行 tools/prepare_7zip.py 准备 7-Zip") from None
            if password is None:
                raise ArchivePasswordRequired("压缩包需要密码") from None
            # libarchive 3.7.7 的 -c @archive --passphrase 路径存在上游错误，
            # 不能把已知不受支持的转换误报为密码错误，导致 UI 无限重试。
            raise ArchiveError("当前系统不支持使用密码解压此格式，请运行 tools/prepare_7zip.py 准备 7-Zip") from None
        if process.returncode and not isinstance(failure, ArchiveError):
            raise ArchiveError("无法解压：压缩包损坏、密码缺失/错误或系统不支持此压缩方式"
                               + (f"\n{detail}" if detail else "")) from failure
        if failure is not None:
            raise failure


def _cleanup_destination(root: Path, identity: os.stat_result) -> None:
    """只删除本次独占创建、且解析路径和身份均未被替换的目录。"""
    if not root.exists() and not root.is_symlink():
        return
    if root.is_symlink() or root.resolve() != root:
        raise ArchiveError(f"解压目录被替换，未执行清理：{root}")
    current = root.stat()
    if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
        raise ArchiveError(f"解压目录身份已改变，未执行清理：{root}")
    shutil.rmtree(root)


def extract_archive(source: Path, destination: Path, *, progress: _Progress | None = None,
                    is_cancelled: Callable[[], bool] | None = None,
                    password: str | None = None,
                    detail_progress: Callable[[ArchiveProgress], None] | None = None) -> list[Path]:
    """解压到不存在的目录并返回所有普通文件的相对路径，失败或取消时清理目录。

    ZIP/7Z/RAR 优先使用完整 7-Zip；缺少引擎时 ZIP 使用标准库，7Z/RAR 使用系统 libarchive。
    只接受普通文件和目录，保留目录层次，禁止链接、路径穿越和大小写覆盖。
    progress 回调参数为 ``(已完成文件数, 总文件数或 0, 当前相对路径)``。
    detail_progress 提供文件数与字节进度；未知总量为 None，仅成功后 finished 为 True。
    """
    source = Path(source).resolve(strict=True)
    if not source.is_file():
        raise ArchiveError("压缩包源路径不是普通文件")
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ArchiveError("解压目标目录已存在，不能覆盖")
    # 父目录必须由调用者准备；不能清理调用者已有或间接创建的父目录。
    parent = destination.parent.resolve(strict=True)
    root = parent / destination.name
    if root == source or source.is_relative_to(root):
        raise ArchiveError("解压目标不能包含源压缩包")
    state = _Extraction(root, progress, is_cancelled, detail_progress)
    state.check()
    try:
        root.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise ArchiveError("解压目标目录已存在，不能覆盖") from exc
    identity = root.stat()
    try:
        state.emit(force=True)
        name = source.name.lower()
        sevenzip = _find_7zip() if name.endswith((".zip", ".7z", ".rar")) else None
        if sevenzip:
            _extract_7zip(source, state, sevenzip, password)
        elif name.endswith(".zip"):
            _extract_zip(source, state, password)
        elif name.endswith((".7z", ".rar")):
            _extract_libarchive(source, state, password)
        elif name.endswith((".tar", ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz", ".tbz2", ".txz")):
            with source.open("rb") as stream:
                _extract_tar(stream, state, streaming=False)
        elif name.endswith((".gz", ".bz2", ".xz")):
            _extract_compressed_file(source, state)
        else:
            raise ArchiveError("不支持此压缩包格式")
        state.check()
        state.emit(force=True, finished=True)
        return state.files
    except BaseException as exc:
        try:
            _cleanup_destination(root, identity)
        except OSError as cleanup_error:
            logging.exception("清理解压临时目录失败：%s", root)
            raise ArchiveError(f"解压失败，且无法清理临时目录：{root}；{cleanup_error}") from exc
        if isinstance(exc, (ArchiveError, KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(exc, subprocess.TimeoutExpired):
            # TimeoutExpired 的默认文本包含完整命令，不能暴露其中的 -p 参数。
            raise ArchiveError("解压工具未及时退出，已终止解压") from None
        detail = str(exc)
        if password:
            detail = detail.replace(password, "***")
        raise ArchiveError(f"解压失败：压缩包损坏、密码错误或格式不受支持（{detail}）") from None
