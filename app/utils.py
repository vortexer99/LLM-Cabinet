"""通用工具函数。"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# 文件类型判定
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".ico"}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"}
PDF_EXTS = {".pdf"}
DOC_EXTS = {
    ".txt", ".md", ".markdown", ".rtf",
    ".doc", ".docx", ".odt",
    ".xls", ".xlsx", ".ods", ".csv",
    ".ppt", ".pptx", ".odp",
    ".html", ".htm", ".epub", ".mobi",
}
# 代码 / 配置 / 数据文件 —— 按文本方式发给 LLM
# 值为 Markdown fenced 代码块的语言提示，便于模型识别
CODE_EXTS: dict[str, str] = {
    # ---------------- 通用脚本/语言 ----------------
    ".py": "python", ".pyi": "python", ".pyw": "python",
    ".pyx": "cython", ".pxd": "cython", ".pxi": "cython",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx",
    ".coffee": "coffeescript",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".groovy": "groovy", ".gvy": "groovy", ".gy": "groovy",
    ".scala": "scala", ".sc": "scala",
    ".clj": "clojure", ".cljs": "clojure", ".cljc": "clojure", ".edn": "edn",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cxx": "cpp", ".cc": "cpp", ".c++": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp", ".h++": "cpp", ".ipp": "cpp",
    ".cs": "csharp", ".csx": "csharp",
    ".fs": "fsharp", ".fsi": "fsharp", ".fsx": "fsharp", ".fsproj": "xml",
    ".vb": "vbnet", ".vbs": "vbscript", ".bas": "basic",
    ".go": "go", ".rs": "rust", ".swift": "swift",
    ".rb": "ruby", ".rake": "ruby", ".gemspec": "ruby", ".erb": "erb",
    ".php": "php", ".phtml": "php", ".phps": "php",
    ".pl": "perl", ".pm": "perl", ".t": "perl", ".pod": "perl",
    ".lua": "lua",
    ".r": "r", ".rd": "r",
    ".dart": "dart",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".ksh": "bash", ".csh": "bash", ".tcsh": "bash",
    ".fish": "fish",
    ".ps1": "powershell", ".psm1": "powershell", ".psd1": "powershell",
    ".bat": "batch", ".cmd": "batch",
    ".sql": "sql", ".psql": "sql", ".mysql": "sql", ".ddl": "sql",
    ".m": "objectivec", ".mm": "objectivec",
    ".asm": "asm", ".s": "asm", ".nasm": "asm", ".yasm": "asm",
    ".f": "fortran", ".f77": "fortran", ".f90": "fortran",
    ".f95": "fortran", ".f03": "fortran", ".f08": "fortran",
    ".for": "fortran",
    ".jl": "julia",
    # ---------------- 函数式 / 学术 / 历史语言 ----------------
    ".hs": "haskell", ".lhs": "haskell", ".cabal": "cabal",
    ".elm": "elm",
    ".purs": "purescript",
    ".idr": "idris",
    ".agda": "agda",
    ".ml": "ocaml", ".mli": "ocaml",
    ".sml": "sml",
    ".erl": "erlang", ".hrl": "erlang",
    ".ex": "elixir", ".exs": "elixir", ".eex": "elixir", ".heex": "elixir",
    ".lisp": "lisp", ".lsp": "lisp", ".cl": "lisp", ".asd": "lisp",
    ".scm": "scheme", ".ss": "scheme",
    ".rkt": "racket",
    ".tcl": "tcl",
    ".st": "smalltalk",
    ".adb": "ada", ".ads": "ada",
    ".cob": "cobol", ".cbl": "cobol", ".cpy": "cobol",
    ".pas": "pascal", ".pp": "pascal", ".dpr": "delphi", ".lpr": "delphi",
    ".pro": "prolog",
    ".forth": "forth", ".fth": "forth", ".4th": "forth",
    ".d": "d",
    ".nim": "nim", ".nims": "nim",
    ".cr": "crystal",
    ".zig": "zig",
    ".v": "v",
    ".odin": "odin",
    ".jai": "jai",
    ".abap": "abap",
    ".apex": "apex",
    ".raku": "raku", ".rakumod": "raku", ".p6": "raku",
    # ---------------- 前端 / 模板 / 样式 ----------------
    ".css": "css", ".scss": "scss", ".sass": "sass", ".less": "less",
    ".styl": "stylus",
    ".vue": "vue", ".svelte": "svelte", ".astro": "astro",
    ".ejs": "ejs", ".pug": "pug", ".jade": "pug",
    ".hbs": "handlebars", ".handlebars": "handlebars", ".mustache": "mustache",
    ".liquid": "liquid", ".twig": "twig", ".njk": "nunjucks",
    ".tmpl": "go-template", ".gotmpl": "go-template", ".gohtml": "go-template",
    ".qml": "qml",
    # ---------------- 配置 / 数据 ----------------
    ".json": "json", ".jsonc": "json", ".json5": "json5",
    ".ndjson": "json", ".jsonl": "json",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini", ".cfg": "ini", ".conf": "ini", ".config": "ini",
    ".env": "dotenv",
    ".properties": "properties",
    ".xml": "xml", ".plist": "xml", ".svg": "xml",
    ".xsd": "xml", ".dtd": "xml", ".wsdl": "xml", ".rng": "xml",
    ".xsl": "xml", ".xslt": "xml",
    ".csproj": "xml", ".vcxproj": "xml", ".vbproj": "xml",
    ".sln": "ini",
    # ---------------- 构建 / 包管理 / DSL ----------------
    ".cmake": "cmake",
    ".dockerfile": "dockerfile", ".containerfile": "dockerfile",
    ".gradle": "gradle",
    ".bazel": "bazel", ".bzl": "bazel", ".buck": "bazel", ".star": "starlark",
    ".ninja": "ninja", ".mk": "makefile",
    ".tf": "hcl", ".tfvars": "hcl", ".hcl": "hcl",
    ".nix": "nix",
    ".bicep": "bicep",
    ".proto": "protobuf",
    ".graphql": "graphql", ".gql": "graphql",
    ".thrift": "thrift",
    ".capnp": "capnp",
    ".fbs": "flatbuffers",
    ".avsc": "json", ".avdl": "avro",
    ".aidl": "aidl",
    ".sol": "solidity", ".vy": "vyper",
    # ---------------- LaTeX / 技术写作 / 文档源 ----------------
    ".tex": "latex", ".sty": "latex", ".cls": "latex", ".bib": "bibtex",
    ".adoc": "asciidoc", ".asciidoc": "asciidoc",
    ".rst": "rst",
    ".org": "org",
    ".typ": "typst",
    # ---------------- 图表 / 可视化 DSL ----------------
    ".dot": "dot", ".gv": "dot",
    ".puml": "plantuml", ".plantuml": "plantuml",
    ".mmd": "mermaid", ".mermaid": "mermaid",
    # ---------------- 着色器 / 游戏 ----------------
    ".glsl": "glsl", ".vert": "glsl", ".frag": "glsl",
    ".geom": "glsl", ".tesc": "glsl", ".tese": "glsl", ".comp": "glsl",
    ".hlsl": "hlsl", ".fx": "hlsl",
    ".metal": "metal",
    ".wgsl": "wgsl",
    ".shader": "shaderlab",
    # ---------------- Notebook / 文学化编程 ----------------
    ".ipynb": "json", ".rmd": "rmarkdown", ".qmd": "quarto",
    # ---------------- 文本日志 ----------------
    ".log": "log",
    # ---------------- 制表数据 ----------------
    ".tsv": "tsv",
    # Makefile / Dockerfile 等无后缀的会在 detect_kind 单独识别
}


def detect_kind(path: str | Path) -> str:
    """根据扩展名返回文件大类：image / video / pdf / doc / code / other。"""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in DOC_EXTS:
        return "doc"
    if ext in CODE_EXTS:
        return "code"
    # 文件名特例（无扩展名的常见构建脚本/配置）
    name = p.name.lower()
    if name in _CODE_FILENAMES:
        return "code"
    return "other"


# 无扩展名（或多点扩展名）但属于代码/构建脚本/配置的常见文件名
_CODE_FILENAMES: set[str] = {
    # 构建脚本
    "makefile", "gnumakefile", "bsdmakefile",
    "dockerfile", "containerfile",
    "rakefile", "gemfile", "guardfile", "capfile", "thorfile", "vagrantfile",
    "berksfile", "puppetfile", "fastfile", "appfile", "matchfile",
    "podfile", "cartfile", "package.swift",
    "cmakelists.txt", "meson.build", "build.gradle", "settings.gradle",
    "build.xml", "build.sbt", "build.boot", "deps.edn", "project.clj",
    "build.zig", "build.rs", "build.gn",
    "snapfile", "jenkinsfile", "fastfile", "brewfile",
    "procfile",
    # JS/TS 工程
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "bun.lockb", "tsconfig.json", "jsconfig.json",
    # Python
    "pipfile", "pipfile.lock", "poetry.lock", "pyproject.toml",
    "requirements.txt", "setup.py", "setup.cfg", "manifest.in",
    "tox.ini", "noxfile.py", "ruff.toml",
    # Rust / Go
    "cargo.toml", "cargo.lock", "go.mod", "go.sum", "go.work",
    # Ruby / PHP
    "gemfile.lock", "composer.json", "composer.lock",
    # Git / 版本控制
    ".gitignore", ".gitattributes", ".gitmodules", ".gitconfig",
    ".mailmap", ".gitkeep",
    # 编辑器 / 工具配置
    ".editorconfig", ".prettierrc", ".prettierignore",
    ".eslintrc", ".eslintignore", ".stylelintrc",
    ".babelrc", ".browserslistrc",
    ".npmrc", ".yarnrc", ".nvmrc", ".node-version",
    ".python-version", ".ruby-version", ".tool-versions",
    ".dockerignore", ".helmignore",
    ".env", ".env.local", ".env.production", ".env.development",
    ".flake8", ".pylintrc", ".isort.cfg",
    # CI / 部署
    ".travis.yml", "appveyor.yml", "circle.yml",
    "codecov.yml", ".codecov.yml",
    # Web 服务器
    ".htaccess", ".htpasswd", "nginx.conf",
}


def code_language(path: str | Path) -> str:
    """返回 Markdown fenced 代码块用的语言提示；无法识别返回空串。"""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in CODE_EXTS:
        return CODE_EXTS[ext]
    name = p.name.lower()
    # 常见无扩展名/特殊命名文件
    if name in ("dockerfile", "containerfile"):
        return "dockerfile"
    if name in ("makefile", "gnumakefile", "bsdmakefile", "rakefile"):
        return "makefile"
    if name in ("cmakelists.txt", "meson.build"):
        return "cmake" if "cmake" in name else "meson"
    if name in ("jenkinsfile", "fastfile", "appfile", "matchfile",
                "podfile", "cartfile", "berksfile", "puppetfile",
                "vagrantfile", "guardfile", "capfile", "thorfile",
                "brewfile", "snapfile", "gemfile", "gemfile.lock"):
        return "ruby"  # 这些 DSL 大多是 Ruby
    if name in ("pipfile", "poetry.lock", "pyproject.toml"):
        return "toml"
    if name in ("requirements.txt", ".python-version", ".ruby-version",
                ".node-version", ".nvmrc", ".tool-versions"):
        return "text"
    if name in ("package.json", "tsconfig.json", "jsconfig.json",
                "composer.json"):
        return "json"
    if name in ("yarn.lock", "pnpm-lock.yaml"):
        return "yaml"
    if name in ("cargo.toml",):
        return "toml"
    if name in ("cargo.lock", "go.mod", "go.sum", "go.work"):
        return "text"
    if name in ("build.gradle", "settings.gradle"):
        return "gradle"
    if name == "build.sbt":
        return "scala"
    if name == "project.clj" or name == "deps.edn":
        return "clojure"
    if name == "nginx.conf":
        return "nginx"
    if name in (".gitignore", ".gitattributes", ".gitconfig", ".gitmodules",
                ".editorconfig", ".dockerignore", ".helmignore",
                ".npmrc", ".yarnrc", ".babelrc", ".browserslistrc",
                ".eslintrc", ".eslintignore", ".prettierrc", ".prettierignore",
                ".stylelintrc", ".flake8", ".pylintrc", ".isort.cfg",
                ".htaccess", ".htpasswd", ".mailmap"):
        return "ini"
    return ""


def app_icon_path() -> Path | None:
    """返回应用图标文件的绝对路径，找不到返回 None。

    搜索顺序：
    1) 源码模式：仓库根目录下 icon.jpg / icon.ico / icon.png
    2) PyInstaller 单文件模式：sys._MEIPASS 临时目录
    3) PyInstaller 单目录模式：可执行文件同目录
    """
    names = ("icon.ico", "icon.png", "icon.jpg")

    bases: list[Path] = []
    # 1) 仓库根（app/.. 的父目录）
    bases.append(Path(__file__).resolve().parents[1])
    # 2) PyInstaller 单文件解包目录
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass))
    # 3) 可执行文件同目录
    if getattr(sys, "frozen", False):
        bases.append(Path(sys.executable).parent)
    # 4) 当前工作目录兜底
    bases.append(Path.cwd())

    for base in bases:
        for n in names:
            p = base / n
            if p.is_file():
                return p
    return None


def app_data_dir() -> Path:
    """返回应用数据目录（Windows 下为 %APPDATA%/Fileman）。

    路径名保留 'Fileman' 以兼容历史用户数据；应用展示名为 'LLM Cabinet'。
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / "Fileman"
    d.mkdir(parents=True, exist_ok=True)
    return d


def open_with_default_app(path: str | Path) -> None:
    """用系统默认程序打开文件。"""
    p = str(Path(path).resolve())
    if sys.platform == "win32":
        os.startfile(p)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        os.system(f'open "{p}"')
    else:
        os.system(f'xdg-open "{p}"')


def reveal_in_explorer(path: str | Path) -> None:
    """在资源管理器中定位文件。"""
    p = Path(path).resolve()
    if sys.platform == "win32":
        os.system(f'explorer /select,"{p}"')
    elif sys.platform == "darwin":
        os.system(f'open -R "{p}"')
    else:
        os.system(f'xdg-open "{p.parent}"')


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"


def utc_to_local_str(s: str, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """SQLite datetime('now') 给的是 'YYYY-MM-DD HH:MM:SS'（UTC，无时区）。
    转成本地时区的字符串。空串/解析失败时原样返回。
    """
    if not s:
        return ""
    raw = s.strip()
    # 兼容带 'T' 或带毫秒的形式
    raw = raw.replace("T", " ")
    candidates = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M")
    dt = None
    for f in candidates:
        try:
            dt = datetime.strptime(raw, f)
            break
        except ValueError:
            continue
    if dt is None:
        return s
    # SQLite datetime('now') 是 UTC
    dt = dt.replace(tzinfo=timezone.utc).astimezone()
    return dt.strftime(fmt)
