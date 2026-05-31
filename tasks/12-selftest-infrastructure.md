# 12 · 端到端自检体系

**工作量**：M（增量推进，按 task 配套补）
**优先级**：P1（基础设施类，与功能并行）
**状态**：🚧 进行中（task #10 已配套）

## 来源

`tasks/10` 完成时配套写了第一份端到端自检脚本，期间发现 importer 一个真实 bug（已匹配字段值未恢复）。
此 task 把"端到端自检"作为长期持续投入的基础设施，规范化目录、模板、覆盖范围与新 task 的配套策略。

## 目标

让"每个写完的 task 都有可重复运行、机器可判定的自检脚本"成为开发常态：

- 写完功能 → 至少跑一遍自检证明它真的工作
- 后续别处改动牵连本功能 → 重跑自检即可发现回归
- 新 task 写自检脚本时**有模板、有公用工具**，不重复造轮子

## 已落地

参考 [`selftests/`](../selftests/) 目录与其 [README](../selftests/README.md)：

```
selftests/
├── README.md                   定位、运行方式、命名规范、checklist
├── _common.py                  T 断言收集器 + closing_repos
└── task10_folder_import.py     #10 配套：45 个断言
```

`T` 类设计要点：

- `assert_eq / assert_true / assert_in`：失败不抛异常，全部跑完再统一报告
- `report()` 打印 PASSED/FAILED 计数 + 失败详情，返回 bool 决定 exit code

`closing_repos`：在 `with tempfile.TemporaryDirectory` 退出前显式关闭 SQLite 连接，规避 Windows 句柄占用导致的清理 PermissionError。

## 范围与边界

**做**：

- 与具体 task 配套的端到端脚本：`taskNN_<short>.py`，按模板写
- 公用工具沉淀到 `_common.py`：断言收集器、临时库 fixture、清理助手等
- 优先覆盖**数据层**与**跨模块流程**（Repository / exporter / importer / library / db migration）
- 必要时加"快速冒烟"脚本：模块 import 通透性、版本号一致性等小检查

**不做（本 task 内）**：

- 单元测试体系（pytest + 覆盖率统计）：那是另一个抽象层，等需要 CI 时再起
- GUI 自动化（Qt 控件级测试）：QTest 复杂、收益低；保持"非 UI 层"边界
- 接入 CI：`selftests/` 定位是开发者本地手动跑（参见 `selftests/README.md` 末尾"为什么不接入 CI"）

## 当前覆盖矩阵

| 子系统 | 已覆盖？ | 已有/计划脚本 | 说明 |
|---|---|---|---|
| `app.exporter` ↔ `app.importer` 闭环 | ✅ | `task10_folder_import.py` | 含 schema 兼容、三档字段策略、文件还原 |
| `app.db` 迁移 | ❌ | `task00_db_migration.py`（待补） | v1→v2 迁移、user_version 推进、自动备份 |
| `app.repository` 字段/标签/项目 CRUD | ❌ | `task00_repository_crud.py`（待补） | 字段删除时三种处理路径、tags 自动创建、保护字段不可删 |
| `app.library` 落地与 resolve | ⚠️ 间接 | 已被 `task10` 覆盖部分；可补 `task00_library.py` | import_copy 同名冲突自增；resolve 还原绝对路径 |
| `app.llm.config` 加载/保存 | ❌ | `task00_llm_config.py`（待补） | 配置往返、敏感字段不丢、默认值兜底 |
| `app.llm.context` prompt 拼装 | ❌ | `task00_llm_context.py`（待补） | 文件文本提取（pdf/docx/code）、长度截断、target_fields 行为 |
| 版本号一致性（CI 已验过） | ✅ | `_smoke_version.py`（可选轻量） | `app.__version__` 与 tag 一致；CHANGELOG 链接行存在 |

`task00_*` 命名表示"非 task 配套，但属于对应模块的基础自检"。这类脚本可以不一一对应任务卡，按需补。

## 实现要点

### A. 命名规则

- `taskNN_<short>.py`：对应 `tasks/NN-...md` 的端到端验证（一对一）
- `task00_<module>.py`：模块级基础自检（与某个 task 关系不直接，但属于"应该常态有的护栏"）
- `_*.py`：公用基础设施，**非可执行入口**
- `_smoke_*.py`：可选的"瞬间能跑完"的极简冒烟（毫秒级）

### B. 必备 checklist（写在 `selftests/README.md`）

- 用 `tempfile.TemporaryDirectory(ignore_cleanup_errors=True)` 隔离副作用
- 所有 `Repository` 在退出前 `conn.close()`（参考 `task10_folder_import.py` 的写法或 `closing_repos`）
- **不要** `from PySide6...`：保持非 UI 边界
- 用 `T.assert_*`，不直接 `raise`
- 末尾 `sys.exit(0 if t.report() else 1)`

### C. 一键全跑（脚本或 PowerShell 函数）

后续可以加一个 `selftests/run_all.ps1`：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$failed = 0
Get-ChildItem $PSScriptRoot/task*.py | ForEach-Object {
    Write-Host "▶ $($_.Name)"
    & python $_.FullName
    if ($LASTEXITCODE -ne 0) { $failed++ }
}
Write-Host "----"
Write-Host "$failed script(s) failed."
exit $failed
```

> 本期可以**不做**这个聚合脚本，等 `selftests/` 文件 ≥ 3 个再加，避免过早抽象。

### D. 与代码改动的关系

- 新加 task → 必须配 `taskNN_*.py`，与 task 同 PR 提交
- 修 bug 但没对应 task → 在最相关的 task 脚本里加一个**回归断言**（命名带 `regression_` 前缀），并在 commit message 里写明
- 重构跨模块代码 → 跑全部 `selftests/`，failures 一律修到全绿才合并

## 风险

- **过度投入**：自检本身的维护成本可能超过它发现的 bug。**只覆盖关键数据/迁移路径**，UI 表层不投入
- **临时目录清理失败**：Windows + SQLite 的老问题，已用 `closing_repos` 与 `ignore_cleanup_errors` 兜底
- **断言写得太具体**：会让无害重构都"红"。坚持"对外行为断言"，不要断言内部实现细节（如某个 SQL 的具体 ORDER BY）
- **跨平台编码**：PowerShell 控制台默认 GBK，输出中文 emoji 会炸。`README` 里固定要求 `PYTHONIOENCODING=utf-8`

## 依赖

- 不强依赖任何其它 task
- 与所有功能 task 都有**协作关系**（每个 task 完工时增量产生一个自检脚本）
- 与 CI（`build-windows.yml`）独立：CI 只做打包冒烟，不跑 `selftests/`

## 后续扩展（按优先级）

| 优先级 | 项 | 说明 |
|---|---|---|
| P1 | `task00_db_migration.py` | v1→v2 迁移路径与备份；schema 演进必须有此护栏 |
| P1 | `task00_repository_crud.py` | 字段三种删除路径、保护字段、字段值类型转换 |
| P2 | `task00_llm_config.py` | 配置往返；API key 持久化但不入 git |
| P2 | `task00_library.py` | import_copy 同名冲突；resolve 行为 |
| P3 | `selftests/run_all.ps1` | 聚合一键跑 |
| P3 | `task00_llm_context.py` | prompt 拼装 + 文件文本提取（涉及 pdf/docx 解析，pip 依赖较重） |
| 远期 | 接入 pytest + GitHub Actions | 等单元测试体系起来后，把端到端用 pytest fixture 重写并接 CI |

## 验收（持续）

每加一个新功能 task：

- [ ] `selftests/taskNN_*.py` 存在
- [ ] 能直接 `python selftests/taskNN_*.py` 跑通
- [ ] 至少包含：核心成功路径 1 个 + 边界/错误路径 ≥ 1 个
- [ ] 打印 `PASSED: N / FAILED: 0`，exit code 0
- [ ] 跑时间 ≤ 10 秒（端到端可接受范围；超时考虑拆脚本或精简样本）
