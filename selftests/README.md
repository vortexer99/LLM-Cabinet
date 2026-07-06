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
| [task03_search_phase_a.py](./task03_search_phase_a.py) | 基础搜索 Phase A：标题/描述 keyword、keyword + tag/tag_prefix AND、未分类 keyword、MCP `search_projects` 透传 tag_prefix | [tasks/03](../tasks/03-search-calibre-like.md) Phase A |
| [task06_tags_hierarchy.py](./task06_tags_hierarchy.py) | `Repository.list_projects(tag, tag_prefix)` 的层级匹配语义 | [tasks/06](../tasks/06-tags-hierarchy-folding.md) |
| [task07_extractors.py](./task07_extractors.py) | 现场内容提取（pptx / odt / odp / ods / epub / html / rtf）+ `extraction_capability` 路由表 | [tasks/07](../tasks/07-local-embedding-summary.md) T0 |
| [task08_multi_libraries.py](./task08_multi_libraries.py) | `app.cabinet` 模块：CabinetConfig 持久化、touch/remove/rename、默认库永驻、损坏 json 重建、库目录探测、import_settings_from_other_db | [tasks/08](../tasks/08-multiple-libraries-switch.md) |
| [task10_folder_import.py](./task10_folder_import.py) | `app.exporter` ↔ `app.importer` 闭环；schema 兼容；三档字段策略；文件还原 | [tasks/10](../tasks/10-folder-batch-import.md) |
| [task11_field_prompt.py](./task11_field_prompt.py) | 字段级 prompt_hint 数据层与 prompt 拼装；`add_fields_batch` 事务；导出 @2 / 导入兼容 | [tasks/11](../tasks/11-field-prompt-and-library-wizard.md) T1/T2/T4 |
| [task11_t3_library_init_wizard.py](./task11_t3_library_init_wizard.py) | LLM 助手框架（WIZARDS 注册 / `is_available`）+ `parse_and_validate` 边界（含废弃 tag_axes 兼容兜底 + LLM 显式删除建议 `fields_to_delete` + LLM 显式改名建议 `fields_to_rename`）+ `annotate_conflicts` 全量规划状态（含 `llm_suggest_delete` / `llm_suggest_rename` + rename/delete/fields 冲突优先级）+ `llm_change_label` / `decision` 退化路径 + `wizard_max_rounds` 持久化 + `apply_field_plan_batch` 事务化（成功/失败/空入参三路 + renames 保 fid 改名 + same_type 行删除回归）+ `append_for_fids` 删除前追加到 description | [tasks/11](../tasks/11-field-prompt-and-library-wizard.md) T3 + [tasks/16](../tasks/16-library-field-wizard-polish.md) |
| [task14_library_management.py](./task14_library_management.py) | 库一致性检查（仓储 + 链接两类失效）+ 三档处理（noop/mark/delete）+ 备份/恢复 zip 往返 + 错误兜底 | [tasks/14](../tasks/14-library-management-enhancements.md) |
| [task15_new_library_onboarding.py](./task15_new_library_onboarding.py) | 新建库向导数据契约：`OPTIONAL_DEFAULT_FIELDS` / `MIGRATE_KEYS_LLM_ONLY` / `MIGRATE_KEYS_ALL` 常量；D2 默认列可见性（描述/标签 visible=0）；`count_user_added_fields` helper；7 步建库底层路径（mark + 加可选字段 + 写描述 + 两档 API 迁移）；T2 横幅显示条件 + D4 一次性标志；T3 Welcome 结果常量分发 | [tasks/15](../tasks/15-new-library-onboarding.md) T1/T2/T3 |
| [task19_field_type_change.py](./task19_field_type_change.py) | 字段类型变更安全护栏 Phase A：`is_compatible_type_change` 兼容矩阵；`set_field_type` 三件事进同事务（改 type + supersede pending + clear hint）；默认 kwargs 向后兼容；受保护字段静默忽略；old==new noop；失败 ROLLBACK | [tasks/19](../tasks/19-field-type-change-safety.md) Phase A |
| [task20_unify_field_storage.py](./task20_unify_field_storage.py) | schema v3 → v4 迁移：4 列 DROP COLUMN 后系统字段值统一存 `project_field_values`；`Project` dataclass 顶层属性 author/date/source_url/rating 移除；`get/set_field_value` 走 field_values 路径；幂等 + 已有 pfv 行不覆盖 + rating "未填"语义保护 | [tasks/20](../tasks/20-unify-field-storage.md) |
| [task21_wizard_two_step.py](./task21_wizard_two_step.py) | 字段助手两段式重构纯函数底座：`merge_decisions_into_drafts` / `diff_drafts_to_plan` / `check_undelete_name_conflict` / `summary_dialog_button_label` / `clone_draft` / `drafts_are_dirty` / `step1_visible_indices` 全部分支 | [tasks/21](../tasks/21-wizard-two-step-redesign.md) |
| [task22_wizard_status_redesign.py](./task22_wizard_status_redesign.py) | 字段助手"LLM 建议"列文案重组：`step1_changed_dimensions` 把 ann × 实际改动维度映射成 `['name', 'type', 'hint']` 子集（rename 路径合并改类型后与 type_conflict 同构判 type）；`step1_action_label` 输出 (label, tooltip) 含决策态后缀；`annotate_conflicts` 把 rename + 改类型合并到 `ann.type`；`step1_visible_indices` 收紧到 `has_llm_change` | [tasks/22](../tasks/22-wizard-status-column-redesign.md) |
| [task25_multi_select.py](./task25_multi_select.py) | 项目列表多选的数据层回归：批量删除、批量标记 MCP 已读、选中 id 逻辑、Phase C 批量追加标签后端 | [tasks/25](../tasks/25-project-list-batch-and-dnd.md) |
| [task29_file_storage_folder_ops.py](./task29_file_storage_folder_ops.py) | 文件存储位置管理 T3c：逻辑文件夹粒度批量入口的数据层范围（递归子层级 + missing_only） | [tasks/29](../tasks/29-file-storage-location-management.md) T3c |
| [task31a_files_tree_interactions.py](./task31a_files_tree_interactions.py) | 文件树交互数据层底座：`update_file` 写回 subfolder、`set_file_subfolder` 移动逻辑目录、`rename_subfolder` 递归重命名子层级、`explicit_subfolders` 空文件夹设置往返 | [tasks/31a](../tasks/31a-files-tree-interactions.md) |
| [task_status_consistency.py](./task_status_consistency.py) | 任务卡 `**状态**` 头与 `tasks/README.md` 索引表的完成度类别一致性（抓"一边完成一边待做"漂移）+ 索引完整性（双向） | 跨任务（无单一 task） |

## 不接入 CI 的原因

- 真 SQLite + 真临时目录 + Windows 下编码差异，跨平台跑很烦
- 这些脚本是开发者写完 task 时的"自我交代"，不是回归基线
- 等单元测试体系建起来后，端到端可以用 pytest fixture 重写并接 CI；
  那时这层会被自然替代
