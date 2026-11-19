"""解压双进度回归：只使用临时自造压缩包，不连接任何数据库。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import gzip
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import archives
from app.archives import ArchiveCancelled, ArchiveError, ArchiveProgress, extract_archive


def check_known(events: list[ArchiveProgress], expected_files: int, expected_bytes: int) -> None:
    assert events[0].total_files is None and events[0].total_bytes is None
    known = [event for event in events if event.total_files is not None]
    assert known and all(event.total_files == expected_files for event in known)
    assert all(event.total_bytes == expected_bytes for event in known)
    assert events[-1].finished
    assert events[-1].completed_files == expected_files
    assert events[-1].completed_bytes == expected_bytes
    assert not any(event.finished for event in events[:-1])
    assert all(a.completed_bytes <= b.completed_bytes for a, b in zip(events, events[1:]))
    assert all(a.completed_files <= b.completed_files for a, b in zip(events, events[1:]))


def zip_tests(root: Path) -> None:
    source = root / "progress.zip"
    payload = b"chunk payload" * 100
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("folder/", b"")
        archive.writestr("folder/large.bin", payload)
        archive.writestr("empty.bin", b"")
        archive.writestr("last.bin", b"last")
    events: list[ArchiveProgress] = []
    legacy: list[tuple[int, int, str]] = []
    with patch.object(archives, "_find_7zip", return_value=None), \
            patch.object(archives, "_CHUNK_SIZE", 32), \
            patch.object(archives, "_PROGRESS_INTERVAL", 0):
        extract_archive(source, root / "zip-output", detail_progress=events.append,
                        progress=lambda *args: legacy.append(args))
    check_known(events, 3, len(payload) + 4)
    assert [done for done, _, _ in legacy] == [1, 2, 3]
    assert all(total == 3 for _, total, _ in legacy)
    partial = [event for event in events if 0 < event.current_bytes < len(payload)
               and event.current_name.endswith("large.bin")]
    assert partial and all(event.completed_files == 0 for event in partial)
    assert any(event.current_name == "empty.bin" and event.current_total == 0
               and event.current_bytes == 0 for event in events)

    # 极短任务也必须发送开始、文件切换、末尾；分块消息仍受时间节流约束。
    throttled: list[ArchiveProgress] = []
    with patch.object(archives, "_find_7zip", return_value=None), \
            patch.object(archives, "_CHUNK_SIZE", 1), \
            patch.object(archives, "_PROGRESS_INTERVAL", 3600):
        extract_archive(source, root / "throttled-output", detail_progress=throttled.append)
    check_known(throttled, 3, len(payload) + 4)
    assert len(throttled) <= 12
    for name, size in (("folder/large.bin", len(payload)), ("empty.bin", 0), ("last.bin", 4)):
        matching = [event for event in throttled if Path(event.current_name).as_posix() == name]
        assert any(event.current_bytes == 0 for event in matching)
        assert any(event.current_bytes == size for event in matching)

    # 取消发生在文件内部，不能发布整体成功，也不能把该文件计为完成。
    cancelled = False
    interrupted: list[ArchiveProgress] = []

    def cancel(event: ArchiveProgress) -> None:
        nonlocal cancelled
        interrupted.append(event)
        if 0 < event.current_bytes < len(payload):
            cancelled = True

    with patch.object(archives, "_find_7zip", return_value=None), \
            patch.object(archives, "_CHUNK_SIZE", 32), \
            patch.object(archives, "_PROGRESS_INTERVAL", 0):
        try:
            extract_archive(source, root / "cancel-output", detail_progress=cancel,
                            is_cancelled=lambda: cancelled)
        except ArchiveCancelled:
            pass
        else:
            raise AssertionError("文件内部取消没有生效")
    assert interrupted and not any(event.finished for event in interrupted)
    assert interrupted[-1].completed_files == 0
    assert interrupted[-1].completed_bytes < len(payload)
    assert not (root / "cancel-output").exists()

    # 数据损坏必须失败，不能用已读取字节数来冒充校验成功。
    damaged = root / "damaged.zip"
    with zipfile.ZipFile(damaged, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("bad.bin", b"unique corrupt payload")
    damaged.write_bytes(damaged.read_bytes().replace(b"unique corrupt payload", b"Unique corrupt payload"))
    errors: list[ArchiveProgress] = []
    with patch.object(archives, "_find_7zip", return_value=None):
        try:
            extract_archive(damaged, root / "damaged-output", detail_progress=errors.append)
        except ArchiveError:
            pass
        else:
            raise AssertionError("CRC 损坏未被拒绝")
    assert not any(event.finished or event.completed_files for event in errors)

    for index, names in enumerate(([], ["folder/"], ["empty.bin"])):
        empty = root / f"empty-{index}.zip"
        with zipfile.ZipFile(empty, "w") as archive:
            for name in names:
                archive.writestr(name, b"")
        events = []
        with patch.object(archives, "_find_7zip", return_value=None):
            extract_archive(empty, root / f"empty-output-{index}", detail_progress=events.append)
        check_known(events, int("empty.bin" in names), 0)


def stream_tests(root: Path) -> None:
    payload = b"stream content" * 100
    source = root / "unknown.gz"
    source.write_bytes(gzip.compress(payload))
    tar_source = root / "unknown.tar"
    with tarfile.open(tar_source, "w") as archive:
        for name, content in (("first.bin", payload), ("last.bin", b"end")):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    for index, path in enumerate((source, tar_source)):
        events: list[ArchiveProgress] = []
        legacy: list[tuple[int, int, str]] = []
        with patch.object(archives, "_CHUNK_SIZE", 32), \
                patch.object(archives, "_PROGRESS_INTERVAL", 0):
            extract_archive(path, root / f"stream-output-{index}", detail_progress=events.append,
                            progress=lambda *args: legacy.append(args))
        assert events[-1].finished
        assert all(event.total_bytes is None and event.total_files is None for event in events)
        assert all(total == 0 for _, total, _ in legacy)
        assert any(0 < event.current_bytes < len(payload) for event in events)
        assert events[-1].completed_bytes == len(payload) + (3 if index else 0)

    # 真正的流式 TAR 入口与 libarchive stdout 共用同一条无总量路径。
    target = root / "tar-pipe-output"
    target.mkdir()
    events = []
    state = archives._Extraction(target, None, None, events.append)
    with tar_source.open("rb") as stream:
        archives._extract_tar(stream, state, streaming=True)
    assert len(state.files) == 2 and state.completed_bytes == len(payload) + 3
    assert all(event.total_files is None and event.total_bytes is None for event in events)
    assert not any(event.finished for event in events)


def sevenzip_tests(root: Path) -> None:
    engine = archives._find_7zip()
    if engine is None:
        print("SKIP: 未安装 7-Zip，跳过真实引擎分块进度")
        return
    fixture = root / "sevenzip-fixture"
    fixture.mkdir()
    payload = b"sevenzip progress payload" * 100
    (fixture / "large.bin").write_bytes(payload)
    (fixture / "empty.bin").write_bytes(b"")
    (fixture / "last.bin").write_bytes(b"end")
    (fixture / "folder").mkdir()
    for suffix in ("7z", "zip"):
        source = root / f"real-progress.{suffix}"
        subprocess.run([engine, "a", "-bd", "-bso0", "-bsp0", f"-t{suffix}", str(source), "."],
                       cwd=fixture, check=True, stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       creationflags=archives._creationflags(), timeout=30)
        events: list[ArchiveProgress] = []
        with patch.object(archives, "_CHUNK_SIZE", 32), \
                patch.object(archives, "_PROGRESS_INTERVAL", 0):
            extract_archive(source, root / f"real-output-{suffix}", detail_progress=events.append)
        check_known(events, 3, len(payload) + 3)
        assert any(0 < event.current_bytes < len(payload)
                   and event.current_name == "large.bin" for event in events)


def integer_and_validation_tests(root: Path) -> None:
    # 无需构造数 GB 文件，直接验证与 UI 信号传递相同的快照和累计计数类型。
    large = 5 * 1024 ** 3 + 123
    events: list[ArchiveProgress] = []
    state = archives._Extraction(root, None, None, events.append)
    state.set_totals(1, large)
    state.begin_file(Path("large.bin"), large)
    state.advance(large)
    state.emit(force=True)
    assert events[-1].current_bytes == large == events[-1].completed_bytes
    assert events[-1].current_total == large == events[-1].total_bytes
    try:
        events[-1].finished = True
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("进度快照必须不可变")

    # 外部进程失败之前，即使数据已全部读出，也不能计为完成文件。
    target = root / "unvalidated-output"
    target.mkdir()
    state = archives._Extraction(target, None, None, events.append)
    state.set_totals(1, 4)
    state.write(Path("file.bin"), io.BytesIO(b"data"), 4, complete=False)
    assert state.files == [] and events[-1].completed_files == 0
    assert events[-1].completed_bytes == 4 and not events[-1].finished
    state.complete_file(Path("file.bin"))
    assert events[-1].completed_files == 1 and not events[-1].finished


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="llm-cabinet-archive-progress-") as directory:
        root = Path(directory)
        zip_tests(root)
        stream_tests(root)
        sevenzip_tests(root)
        integer_and_validation_tests(root)
    print("PASS: task48 archive progress (temporary fixtures only; no database access)")


if __name__ == "__main__":
    main()
