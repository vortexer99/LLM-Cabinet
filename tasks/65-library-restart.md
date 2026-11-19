# Task #65：修复切换库自动重启失败

状态：✅ 完成（2026-11-18）

## 问题

通过 `start.ps1`（`python -m app.main`）启动后，切换库退出，控制台报错，目标库需要手动重启才能打开。

## 根因与修复

- 模块启动的 `sys.argv[0]` 是 `app/main.py`，旧重启逻辑将其作为普通脚本执行，导致 `ImportError: attempted relative import with no known parent package`。
- 源码版使用同一 Python 和绝对路径 `run.py` 重启，不依赖工作目录。
- 打包版仅传一次可执行文件路径；保留普通参数，过滤过期 `--welcome`，按本次需要重新追加。
- 重启异常记录堆栈，并通过统一中文提示框提供启动参数和异常详情。

## 验证

- 仅隔离临时目录／配置与测试数据，禁止读取用户数据库。
- 验证源码模块／顶层脚本入口、打包参数、带空格路径、欢迎页参数、失败详情和子进程导入。
- `task65_library_restart.py`：8 项测试通过，隔离子进程从无关工作目录执行顶层入口，验证包导入正确。
- `task08_multi_libraries.py`：85 项回归断言通过；修改文件语法检查与 `git diff --check` 通过。
