"""PyInstaller / 直接 ``python run.py`` 用的顶层入口。

``app/main.py`` 内部用了相对导入（``from .db import ...``），它只能通过
``python -m app.main`` 这种"把 app 当包加载"的方式运行；PyInstaller 直接
打 ``app/main.py`` 会把它当顶层脚本，相对导入会抛
``ImportError: attempted relative import with no known parent package``。

本文件作为顶层入口，让 PyInstaller 始终通过 ``app`` 这个包来调用 ``main()``。
日常开发仍然推荐 ``python -m app.main``。
"""
from __future__ import annotations

import sys

from app.main import main


if __name__ == "__main__":
    sys.exit(main())
