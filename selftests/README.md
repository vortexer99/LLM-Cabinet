# selftests/

开发期端到端自检脚本。和"单元测试"是不同物种：

| 维度 | `selftests/`（这里） | 单元测试（暂未引入） |
|---|---|---|
| 跑法 | 开发者**手动**按需跑 | CI / pytest 自动跑 |
| 范围 | **端到端**：临时库 + 真实文件 + 跨模块 | 单函数 / 单类 |
| 依赖 | 真 SQLite、真文件系统、真 `app.*` | 大量 mock |
| 不能依赖 | `QApplication` 与任何 GUI 控件 | 同 |
| 何时新增 | 一个 task 完工时 | 复杂逻辑写完时 |

## 运行方式

```powershell
# 仓库根目录下，PowerShell（建议先设 PYTHONIOENCODING 避免中文输出乱码）
$env:PYTHONIOENCODING = 'utf-8'
python selftests/task10_folder_import.py
```

退出码：

- `0`：全部断言通过
- `1`：有断言失败（详情打印到 stdout）
- `2`：脚本崩溃（traceback 打印到 stdout）

也可以一口气跑全部：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
Get-ChildItem selftests/task*.py | ForEach-Object { python $_.FullName }
```

## 文件命名

- `task<NN>_<short>.py`：对应 `tasks/<NN>-...md` 的端到端验证
- `_common.py` / 其它 `_*.py`：公用基础设施，下划线开头表明"非可执行入口"

## 公用工具（`_common.py`）

```python
from selftests._common import T, closing_repos

t = T()
t.assert_eq("foo", actual, expected)
t.assert_true("bar", cond)
t.assert_in("baz", needle, haystack)
ok = t.report()        # 打印 PASSED/FAILED 计数 + 失败详情
sys.exit(0 if ok else 1)

# closing_repos: with 块退出时关闭 Repository.conn，
# 避免 Windows 上 tempfile.TemporaryDirectory 清理报权限错
with closing_repos(repo_a, repo_b):
    ...
```

## 写新自检的 checklist

- 文件名 = `taskNN_<short>.py`
- 用 `tempfile.TemporaryDirectory(ignore_cleanup_errors=True)` 隔离副作用
- 所有 `Repository` 在退出前 `conn.close()`（最稳的方式：把它们收集到 list，
  在 `with tempfile.TemporaryDirectory` 退出之前手动关；或包一层 helper）
- **不要**`from PySide6...`：这一层只验证非 UI 逻辑
- 断言失败用 `T.assert_*`，不要直接 `raise`：让一次跑能看到所有失败点
- 末尾 `sys.exit(0 if t.report() else 1)`

## 已收录

| 脚本 | 验证范围 | 关联 task |
|---|---|---|
| [task06_tags_hierarchy.py](./task06_tags_hierarchy.py) | `Repository.list_projects(tag, tag_prefix)` 的层级匹配语义 | [tasks/06](../tasks/06-tags-hierarchy-folding.md) |
| [task10_folder_import.py](./task10_folder_import.py) | `app.exporter` ↔ `app.importer` 闭环；schema 兼容；三档字段策略；文件还原 | [tasks/10](../tasks/10-folder-batch-import.md) |
| [task11_field_prompt.py](./task11_field_prompt.py) | 字段级 prompt_hint 数据层与 prompt 拼装；`add_fields_batch` 事务；导出 @2 / 导入兼容 | [tasks/11](../tasks/11-field-prompt-and-library-wizard.md) T1/T2/T4 |

## 不接入 CI 的原因

- 真 SQLite + 真临时目录 + Windows 下编码差异，跨平台跑很烦
- 这些脚本是开发者写完 task 时的"自我交代"，不是回归基线
- 等单元测试体系建起来后，端到端可以用 pytest fixture 重写并接 CI；
  那时这层会被自然替代
