"""task #49 自检：文件类型搜索只读登记记录，全部数据来自显式临时库。"""
from __future__ import annotations

import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T, closing_repos

from app.db import connect
from app.models import FileItem, Project
from app.repository import Repository
from app.search import TermNode, parse_search
from app.utils import ARCHIVE_EXTS, VIDEO_EXTS


def _query(repo: Repository, expr: str) -> list[str]:
    parsed = parse_search(expr)
    if not parsed.ok:
        raise AssertionError(parsed.error)
    return sorted(project.title for project in repo.list_projects_query(parsed.ast))


def test_file_type_search(tmp: Path, t: T) -> None:
    repo = Repository(connect(tmp / "file-type-search-test.db"))
    with closing_repos(repo):
        expected: dict[str, set[str]] = {"video": set(), "archive": set()}
        all_titles: set[str] = set()

        def project(title: str, *kinds: str, **kwargs) -> int:
            all_titles.add(title)
            for kind in kinds:
                expected[kind].add(title)
            return repo.save_project(Project(title=title, **kwargs))

        def file(pid: int, path: str, kind: str = "other", **kwargs) -> int:
            return repo.add_file(FileItem(project_id=pid, path=path, kind=kind, **kwargs))

        # 新类型按登记值计算，不受路径后缀、存储模式、来源或缺失标记限制。
        for kind in expected:
            for mode in ("link", "storage"):
                for origin in ("user", "generated"):
                    title = f"{kind}-{mode}-{origin}"
                    pid = project(title, kind, storage_mode=mode)
                    fid = file(pid, f"absent/{title}.unknown", kind, origin=origin,
                               is_relative=(mode == "storage"))
                    repo.set_file_missing(fid, True)

        # 历史 other 记录兼容全部已知扩展，包括大写和复合扩展。
        for kind, extensions in (("video", VIDEO_EXTS), ("archive", ARCHIVE_EXTS)):
            for extension in sorted(extensions):
                pid = project(f"legacy-{kind}-{extension}", kind)
                fid = file(pid, f"NOT-ON-DISK/片段.part{extension.upper()}",
                           origin="generated", is_relative=True)
                repo.set_file_missing(fid, True)
        file(project("compound-archive", "archive"), "missing/backup.TAR.GZ")

        # EXISTS 不因多条文件记录重复项目；逻辑条件可由不同文件分别满足。
        both = project("both", "video", "archive", tags=["课程", "收藏"],
                       description_md="needle")
        file(both, "missing/one.MP4")
        file(both, "missing/two.mp4", "video")
        file(both, "missing/package.ZIP")
        file(project("declared-video", "video"), "wrong.zip", "video")
        file(project("declared-archive", "archive"), "wrong.mp4", "archive")

        # 目录名、中间扩展、标签、描述、其他已登记类型均不应触发类型过滤。
        decoy = project("video archive .mp4 .zip", description_md="视频 压缩包 .mp4 .zip",
                        tags=["video", "archive"])
        for path in ("folder.mp4/readme.txt", "folder.zip/readme.txt", "x.mp4.bak",
                     "x.zip.tmp", "x.mp4/", "x.zip/", "x.mp4\\readme.txt",
                     "x.zip\\readme.txt", "xmp4", "xzip", "x.mp4 ", "x.zip "):
            file(decoy, path, label=".mp4 .zip", subfolder="video/archive")
        file(decoy, "x.mp4", "text")
        file(decoy, "x.zip", "pdf")
        project("empty")

        # 自定义 key 与显示名都不能覆盖保留字段 has。
        cur = repo.conn.execute(
            "INSERT INTO fields(name,type,ord,visible,key) VALUES(?,?,?,?,?)",
            ("has", "text", 99, 1, "has"),
        )
        conflict = project("custom-field-only", field_values={int(cur.lastrowid): "video"})
        assert conflict
        repo.conn.commit()
        before = list(repo.conn.iterdump())
        changes = repo.conn.total_changes

        # 查询期间禁止 Python 文件读取和 stat，并验证数据库内容与写入计数不变。
        with ExitStack() as stack:
            for target in ("builtins.open", "io.open", "os.stat", "os.lstat",
                           "pathlib.Path.stat", "pathlib.Path.open"):
                stack.enter_context(patch(target, side_effect=AssertionError("搜索不得访问磁盘")))
            for kind in expected:
                t.assert_eq(f"has:{kind} all registrations", _query(repo, f"has:{kind}"),
                            sorted(expected[kind]))
                t.assert_eq(f"case insensitive {kind}", _query(repo, f"HAS={kind.upper()}"),
                            sorted(expected[kind]))
            for alias, kind in (("视频", "video"), ("压缩包", "archive")):
                t.assert_eq(f"Chinese alias {alias}", _query(repo, f"has:{alias}"),
                            sorted(expected[kind]))
            t.assert_eq("AND matches files independently", _query(repo, "has:video AND has:archive"),
                        ["both"])
            t.assert_eq("OR deduplicates projects", _query(repo, "has:video OR has:archive"),
                        sorted(expected["video"] | expected["archive"]))
            t.assert_eq("NOT filters registrations", _query(repo, "has:video AND NOT has:archive"),
                        sorted(expected["video"] - expected["archive"]))
            t.assert_eq("nested boolean with NOT", _query(repo, "NOT (has:video OR has:archive)"),
                        sorted(all_titles - expected["video"] - expected["archive"]))
            t.assert_eq("combined tag and keyword", _query(repo, "has:video AND tag:课程 AND needle"), ["both"])
            t.assert_eq("both tags and archive", _query(repo, "tag:课程 AND tag:收藏 AND has:archive"),
                        ["both"])
            t.assert_eq("custom has field does not override builtin", _query(repo, "title=custom-field-only AND has:video"), [])
            for expr in ("has:unknown", "has:mp4", "has:vide", 'has:"video OR 1=1"',
                         "has:>video", "has:>=video", "has:<archive", "has:<=archive"):
                t.assert_eq(f"unsupported term {expr}", _query(repo, expr), [])
            for value in ("", "   ", "video' OR 1=1 --"):
                t.assert_eq(f"empty or injected term {value!r}", repo.list_projects_query(
                    TermNode(field="has", op=":", value=value)), [])
            t.assert_eq("unknown term under OR", _query(repo, "has:unknown OR has:archive"),
                        sorted(expected["archive"]))
            t.assert_eq("unknown term under NOT", _query(repo, "NOT has:unknown"), sorted(all_titles))

        t.assert_eq("query does not write records", repo.conn.total_changes, changes)
        t.assert_eq("query preserves all persisted values", list(repo.conn.iterdump()), before)
        sql, params = repo._compile_search_term(TermNode("has", ":", "video"))
        t.assert_true("EXISTS correlated to project", "f_has.project_id = p.id" in sql)
        t.assert_true("classification and extensions use bound parameters", "video" not in sql and ".mp4" not in sql)
        t.assert_true("extension parameters reuse shared constants", ".mp4" in params)


def test_external_file_search(tmp: Path, t: T) -> None:
    """外部链接只看文件的 is_relative，不看项目模式、URL 或磁盘。"""
    repo = Repository(connect(tmp / "external-file-search-test.db"))
    with closing_repos(repo):
        url_field = repo.add_field("参考链接", "url")
        fixtures = [
            # 项目模式和路径写法故意与文件登记方式交叉。
            ("storage-only", "storage", [("video", "stored.mp4", True, False)]),
            ("link-only", "link", [("pdf", "external.pdf", False, False)]),
            ("mixed", "storage", [("video", "stored.mp4", True, False),
                                  ("pdf", "external.pdf", False, False)]),
            ("empty", "link", []),
            ("url-only", "link", []),
            ("missing-external", "storage", [("other", "missing.dat", False, True)]),
            ("link-mode-stored", "link", [("pdf", "C:/absolute-looking.pdf", True, False)]),
            ("storage-mode-external", "storage", [("other", "relative-looking.txt", False, False)]),
            ("linked-video", "link", [("video", "linked.mp4", False, True),
                                     ("pdf", "another.pdf", False, False)]),
            ("stored-url-looking", "storage", [("other", "https://example.com/file", True, False)]),
        ]
        for title, mode, files in fixtures:
            pid = repo.save_project(Project(
                title=title, storage_mode=mode,
                field_values={url_field: "https://example.com/reference"} if title == "url-only" else {},
            ))
            for kind, path, relative, missing in files:
                fid = repo.add_file(FileItem(project_id=pid, path=path, kind=kind,
                                             is_relative=relative))
                repo.set_file_missing(fid, missing)

        external = {"link-only", "mixed", "missing-external", "storage-mode-external", "linked-video"}
        video = {"storage-only", "mixed", "linked-video"}
        all_titles = {fixture[0] for fixture in fixtures}
        before = list(repo.conn.iterdump())
        changes = repo.conn.total_changes
        with ExitStack() as stack:
            for target in ("builtins.open", "io.open", "os.stat", "os.lstat",
                           "pathlib.Path.stat", "pathlib.Path.open"):
                stack.enter_context(patch(target, side_effect=AssertionError("搜索不得访问磁盘")))
            for expression in ("has:external", "HAS=EXTERNAL", "has:外部链接"):
                t.assert_eq(f"外部链接登记语义 {expression}", _query(repo, expression), sorted(external))
            t.assert_eq("视频与链接可由不同文件满足", _query(repo, "has:video AND has:external"),
                        sorted(video & external))
            t.assert_eq("外部链接 OR 去重", _query(repo, "has:video OR has:external"),
                        sorted(video | external))
            t.assert_eq("外部链接 NOT", _query(repo, "NOT has:external"), sorted(all_titles - external))
            t.assert_eq("外部链接与否定视频组合", _query(repo, "has:external AND NOT has:video"),
                        sorted(external - video))
            t.assert_eq("外部链接嵌套逻辑", _query(repo, "NOT (has:external OR has:video)"),
                        sorted(all_titles - external - video))
        t.assert_eq("外部链接搜索不写记录", repo.conn.total_changes, changes)
        t.assert_eq("外部链接搜索保持全部持久化值", list(repo.conn.iterdump()), before)


if __name__ == "__main__":
    print("task49_file_type_search selftest")
    tests = T()
    with tempfile.TemporaryDirectory() as temp_dir:
        test_file_type_search(Path(temp_dir), tests)
        test_external_file_search(Path(temp_dir), tests)
    sys.exit(0 if tests.report() else 1)
