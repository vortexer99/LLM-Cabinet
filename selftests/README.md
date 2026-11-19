# selftests/

开发期端到端自检脚本。和"单元测试"是不同物种：

| 维度 | `selftests/`（这里） | 单元测试（暂未引入） |
|---|---|---|
| 跑法 | 开发者**手动**按需跑 | CI / pytest 自动跑 |
| 范围 | **端到端**：临时库 + 真实文件 + 跨模块 | 单函数 / 单类 |
| 依赖 | 真 SQLite、真文件系统、真 `app.*` | 大量 mock |
| 不能依赖 | 普通 `task*.py` 不依赖 `QApplication`；GUI 回归另放 `gui_*.py` | 同 |
| 何时新增 | 一个 task 完工时 | 复杂逻辑写完时 |

## 运行方式

- `gui_project_field_filter.py`：项目表格／卡片右键同字段值筛选，作者精确匹配、原始值、右键目标捕获、多选、特殊字符、组合条件、空值和解析冲突；仅显式临时库与合成数据。
- `gui_tag_modifier_filters.py`：Shift／Ctrl 标签与前缀 AND／OR 组合、结果集和外层条件、负向与特殊标签、重复点击、组合图标及搜索恢复、普通三态回归；仅显式临时库及合成数据。
- `gui_tag_tristate.py`：左侧标签及前缀包含／排除／不筛选三态、鼠标和键盘激活、搜索反向同步、刷新与组合查询、无效表达式及简介标签正向定位；仅显式临时库和合成数据。
- `task65_library_restart.py`：切换库的源码／打包重启参数、绝对入口与空格路径、欢迎页一次性参数、失败详情及隔离子进程包导入验证；不读取用户数据库。
- `gui_project_tag_chips.py`：简介下标签显示、实际点击与键盘筛选、特殊名称精确匹配、搜索与侧栏同步、组合条件、项目切换及专注模式、标签布局和预览状态保持；仅显式临时库与合成数据。
- `gui_cover_file_visibility.py`：封面等生成文件默认隐藏，保留显式来源偏好；原始图片封面可见，树／平铺导航及自动选首文件跳过隐藏项，切换来源保持预览；仅临时库。
- `gui_cover_replacement.py`：旧封面保留／替换设置即时保存、重开与双库隔离，连续截图保持视频播放，剪切板更换、原始图片保护及回收失败详情；仅临时库、合成图片和模拟回收目录。
- `task63_cover_files.py`：封面替换保存顺序、旧生成文件回收与共享／路径保护、写入和回滚故障；仅独立临时库及合成文件。
- `gui_drag_file_order.py`：内部拖动跨目录保留自然升降序、同级插入基于显示顺序且无关目录不乱、无效拖动不写记录／刷新、自定义跨目录插入及恢复名称排序；仅临时库合成路径。
- `gui_saved_search_sidebar.py`：搜索收藏左侧入口、真实点击替换查询、完整匹配高亮、保存／覆盖／删除即时同步、单次刷新、预览保持及重开／库隔离；仅临时库合成记录。
- `gui_extension_search.py`：扩展名精确匹配、大小写／点号／复合扩展、OR／AND／NOT、目录及附加后缀边界、禁止文件读取，以及“有视频类型”命名收藏保存／恢复与补全；显式临时库和合成登记路径。
- `task56_search_filter_sync.py` / `gui_search_filter_sync.py`：侧栏与完整查询等价、特殊标签转义、布尔逻辑、公开状态条件、双向控件同步、复制／历史／收藏复用及清空；只使用显式临时库。
- `gui_project_column_sorting.py`：项目列表全列类型排序、自然数字／负数小数／日期时区／空值非法值、真实表头升降序、多选与预览保持，以及刷新／字段重排和隐藏删除；仅显式临时库及合成记录。
- `gui_preview_wheel.py`：临时库和合成图片，真实 Qt 滚轮事件覆盖图片／视频／PDF／提示画面导航、边界、角度／像素累计、关闭恢复缩放、控制条隔离及空列表。库级保存和重开隔离由 `gui_library_media_settings.py` 覆盖。
- `gui_animated_images.py`：合成双色 WebP／GIF 的真实换帧、暂停／继续、重复选择、缩放与平移保持、当前帧截图、静态／损坏图片及切换释放；无数据库、无需 Pillow，Qt offscreen。
- `gui_filter_performance.py`：临时库生成 1200 项目、18000 文件、200 标签和合成封面，记录标签切换耗时；断言侧栏复用、仅可见卡片加载封面、列表模式零封面读取、命中范围文件统计、选择／预览保持及本连接／外部连接写入失效（Qt offscreen）。
- `gui_project_cover_loading.py`：万条合成项目的封面按需读取、成功及空值缓存、换批失效和局部更新；仅内存图像，无数据库（Qt offscreen）。

- `task50_project_favorites.py`：收藏按库持久化、批量去重、失败原子回滚、删除级联、不改更新时间及搜索组合；仅显式临时库。
- `gui_project_favorites_random.py`：真实星标／按钮／Ctrl+D／右键批量收藏，收藏筛选及计数、搜索补全、重启保留；随机候选严格限定当前筛选，待执行搜索、空／错误结果、双视图同步及预览保持（Qt offscreen，临时库）。

- `gui_archive_progress.py`：解压总进度/当前文件双进度、未知大小、大于 2GB 比例、取消按钮/Esc/关闭等待清理；真实后台 ZIP 解压三路回调、GUI 主线程信号及临时库真实入口（Qt offscreen）。
- `task48_archive_progress.py`：ZIP/TAR/压缩流与可用的真实 7-Zip 引擎分块进度、不可变快照、总量/字节单调性、取消清理、大整数及校验前不计完成；仅临时自造压缩包，不访问数据库。
- `task48_archives.py --real-fixtures`：ZIP/TAR/压缩流、真实 7Z/ZIP 加密及公开 RAR 加密夹具；仅临时目录，`--real-fixtures` 会联网获取上游测试包，完整格式验证需先运行 `tools/prepare_7zip.py`。
- `task44_auto_cover.py`：导入自动封面、已有封面保留、存储模式与库隔离。
- `gui_library_media_settings.py`：库级开关持久化及视频自动播放（Qt offscreen）。
- `gui_video_auto_cover.py`：视频封面父子开关联动及双库持久化、候选自然排序和图片优先、文件夹/ZIP/拖入真实导入、抽帧失败/取消及补偿清理、仓储源回收后的副本路径；仅临时库与合成文件，模拟抽帧和回收站（Qt offscreen）。
- `task47_video_first_frame.py`：独立视频首帧提取的真实 Qt 解码、PNG 输出及错误处理；仅合成测试视频和临时目录。
- `gui_storage_recycle.py`：仓储原文件回收开关持久化、库隔离、GUI 添加及项目转换的成功/失败/取消汇总、批量项目共享源统一回收、真实后台回收线程及取消；仅临时库、合成源文件及模拟回收目录，绝不调用真实回收站。

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

GUI 回归自检单独跑，要求使用已安装 PySide6 的 Python，并默认走 Qt offscreen：

- `gui_file_type_filters.py`：真实主窗口 + QTest 操作视频/压缩/外部链接树形条目，右键任一/全部模式及清除、空格/Esc、折叠高度和全库清空；覆盖与标签/关键词叠加、标题摘要、`has:` 补全和 AND/OR/NOT 搜索、旧 `other` 后缀兼容及缺失记录。仅显式临时库和不存在的自造媒体路径。运行：`.venv\Scripts\python.exe -X utf8 selftests\gui_file_type_filters.py`。

- `gui_layout_persistence.py`：固定 1920×1080 隔离虚拟屏幕，真实鼠标拖动两处分栏，实际显示后关闭重开同一临时库，验证左右宽度、右侧上下比例、普通/最大化窗口、连续重启、关闭补保存、不同库隔离、损坏设置回退、文件面板弹出收回和折叠比例；仅使用显式临时测试库。运行：`.venv\Scripts\python.exe -X utf8 selftests\gui_layout_persistence.py`。

- `gui_preview_file_navigation.py`：预览区文件翻页与真实列表选择、排序、过滤及自动播放联动，仅使用临时测试库。

- `gui_cover_playback.py`：手动连续截取视频封面后保留播放/暂停、进度、当前行与多选、目录折叠和滚动；覆盖树形/平铺、全部/用户来源过滤、项目封面与计数刷新、旧截图保留及后续翻页，仅使用显式临时库、合成文件和模拟媒体后端（Qt offscreen）。

- `gui_files_natural_sort.py`：文件名自然排序，覆盖树形/平铺、升降序、目录层级和自定义顺序。

- `gui_files_header_sorting.py`：真实主窗口中用 QTest 鼠标点击文件表头，覆盖首次树形自然升降序、重选项目、树形/平铺切换、类型列、排序偏好持久化及旧平铺枚举设置兼容；仅使用显式临时库和自造文件。

- `gui_project_links.py`：项目来源及自定义 URL 字段的打开按钮、列表双击路由、网址校验与启动失败提示；仅用临时库并拦截浏览器调用。

```powershell
$env:PYTHONIOENCODING = 'utf-8'
C:\Users\hfdxm\AppData\Local\Python\bin\python.exe -B selftests\gui_main_window_regressions.py
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
- 普通 `task*.py` **不要**`from PySide6...`：这一层只验证非 UI 逻辑
- GUI 行为回归放在 `gui_*.py`，脚本必须能 offscreen 启动、自己关闭窗口和数据库连接
- 断言失败用 `T.assert_*`，不要直接 `raise`：让一次跑能看到所有失败点
- 末尾 `sys.exit(0 if t.report() else 1)`

## 已收录

| 脚本 | 验证范围 | 关联 task |
|---|---|---|
| [task49_file_type_search.py](./task49_file_type_search.py) | `has:video` / `has:archive` 搜索、布尔组合、旧 `other` 后缀兼容、缺失记录和未知值；仅显式临时库与自造数据 | [tasks/49](../tasks/49-project-file-type-filters.md) |
| [gui_file_type_filters.py](./gui_file_type_filters.py) | 标签样式条目多选/右键组合与清除/键盘/折叠、标签和关键词叠加、全库清空、标题摘要、补全及 has 布尔搜索；Qt offscreen + 显式临时库 | [tasks/49](../tasks/49-project-file-type-filters.md) |
| [gui_archive_extraction.py](./gui_archive_extraction.py) | 真实 ZIP 解压入临时库：链接/仓储源保留、中文多层与空目录、重复解压、类型显示和旧记录右键入口、多选当前项、取消/损坏/原子入库失败清理；按需密码询问、错误重试/取消、密码记录设置与描述同事务回滚；Qt offscreen，拦截真实回收站 | [tasks/48](../tasks/48-archive-extraction.md) |
| [gui_archive_progress.py](./gui_archive_progress.py) | 解压双进度：百分比/未知大小/8GB 比例、换文件归零与空包完成、按钮/Esc/关闭请求取消并等待；真实后台解压成功/分块取消/失败和 GUI 主线程信号、真实 MainWindow 临时库入口；Qt offscreen | [tasks/48](../tasks/48-archive-extraction.md) |
| [task48_archive_progress.py](./task48_archive_progress.py) | 解压详情快照：ZIP/TAR/压缩流与真实 7-Zip 分块、已知/未知总量、字节与文件数单调、完成/取消/失败、大整数及校验前不计完成；临时自造包，无数据库访问 | [tasks/48](../tasks/48-archive-extraction.md) |
| [gui_cover_playback.py](./gui_cover_playback.py) | 真实 MainWindow 手动连续截封面：保持视频源、进度、播放/暂停和文件选择，刷新封面与文件计数；树形/平铺及来源过滤、折叠/滚动、旧图保留与下一文件导航；临时库 + 合成文件 + 模拟媒体后端 | 手动截封面播放状态修复 |
| [gui_files_header_sorting.py](./gui_files_header_sorting.py) | 真实 MainWindow + QTest 鼠标点击表头：首次树形自然升降序、重选项目、树形/平铺往返、类型列、排序偏好持久化及旧平铺枚举设置兼容；临时库 + 自造文件 + Qt offscreen | 文件表头点击修复 |
| [gui_storage_recycle.py](./gui_storage_recycle.py) | 库级原文件回收开关持久化/隔离；GUI 添加与整项目转换的落库后回收、同源去重、共享链接保留、复制/写回/回收失败汇总与取消；批量两个 plan 共享源统一回收；真实后台线程执行回收、主线程 DB 快照及取消；仅临时库、合成文件及模拟回收目录 | [tasks/46](../tasks/46-storage-recycle-source.md) |
| [task46_storage_recycle.py](./task46_storage_recycle.py) | 仓储源文件回收底层护栏：默认关闭、链接/ZIP/库内源保留、部分失败及事务保护、共享引用、同源去重、源文件变化与禁止硬删除回退；模拟回收站 | [tasks/46](../tasks/46-storage-recycle-source.md) |
| [gui_project_storage_conversion.py](./gui_project_storage_conversion.py) | 项目右键转仓储的列表/网格目标固定；整项目复制覆盖子目录、过滤隐藏的衍生文件、已有仓储跳过、丢失源文件汇总、取消不改与文件右键多选保留；仅临时库和合成文件 + Qt offscreen | [tasks/29](../tasks/29-file-storage-location-management.md) 项目级入口 |
| [gui_batch_project_storage.py](./gui_batch_project_storage.py) | 多选项目转仓储：列表/网格菜单固定打开时的 ID、右键未选项回到单项目；一次确认/任务/汇总、项目 ID 去重、过滤无关、取消和缺失源部分成功；跨项目共享源整批落库后仅模拟回收一次、未选项目链接保留；仅显式临时库和合成文件 + Qt offscreen，绝不调用真实回收站 | [tasks/29](../tasks/29-file-storage-location-management.md) 多选项目入口 |
| [gui_project_links.py](./gui_project_links.py) | URL 编辑器动态启用、读取/保存及 LLM 建议兼容；真实主窗口列表 doubleClicked 路由（来源/自定义 URL/普通列/空值）、完整网址提示及浏览器失败反馈；临时库 + Qt offscreen，不实际联网 | 项目来源链接修复 |
| [task43_skill_cli.py](./task43_skill_cli.py) | 真实 CLI 子进程与临时双库：UTF-8 JSON/退出码、显式选库、误选数据库/未来 schema 只读保护、读写/正文授权、字段 ID/保护字段校验与无半写、仓储设置/参数优先级及回退、标签/文件/导出、MCP 审计与后台任务保护、异地 skill 入口 | [tasks/43](../tasks/43-skill-cli.md) |
| [task03_search_phase_a.py](./task03_search_phase_a.py) | 基础搜索 Phase A：宽关键词、keyword + tag/tag_prefix AND、未分类 keyword、MCP `search_projects` 透传 tag_prefix | [tasks/03](../tasks/03-search-calibre-like.md) Phase A |
| [task03_search_phase_b.py](./task03_search_phase_b.py) | 类 Calibre 搜索 Phase B：宽关键词命中字段值/标签/文件清单、字段 key / 显示名、rating/date 比较、多标签 AND、括号/NOT、语法错误与 MCP 精确搜索 | [tasks/03](../tasks/03-search-calibre-like.md) Phase B |
| [task03_search_phase_c.py](./task03_search_phase_c.py) | 搜索历史与收藏表达式 Phase C：settings JSON 容错、历史去重/上限、收藏新增/覆盖/删除 | [tasks/03](../tasks/03-search-calibre-like.md) Phase C |
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
| [gui_main_window_regressions.py](./gui_main_window_regressions.py) | 主窗口 GUI 回归：搜索菜单 FocusIn 防重复、全库清空搜索、GUI 宽关键词命中字段/文件、网格/列表右键 MCP 已读目标、MCP 未读筛选刷新、目录/ZIP 项目包导出导入 round-trip、主 splitter 宽度保存/读取与右栏宽度稳定；需 PySide6 + offscreen Qt | 跨任务 GUI 回归 |
| [gui_sample_library_regressions.py](./gui_sample_library_regressions.py) | 样例库 GUI 回归：生成临时 sample library 后覆盖基线项目/字段、搜索/筛选、文件树/扁平视图/来源过滤、项目编辑 LLM 建议、MCP audit 筛选与同名外链导出唯一性；需 PySide6 + offscreen Qt | 样例库手测自动化 |
| [task25_multi_select.py](./task25_multi_select.py) | 项目列表多选的数据层回归：批量删除、批量标记 MCP 已读、选中 id 逻辑、Phase C 批量追加标签后端 | [tasks/25](../tasks/25-project-list-batch-and-dnd.md) |
| [task29_file_storage_folder_ops.py](./task29_file_storage_folder_ops.py) | 文件存储位置管理 T3c：逻辑文件夹粒度批量入口的数据层范围（递归子层级 + missing_only） | [tasks/29](../tasks/29-file-storage-location-management.md) T3c |
| [task31a_files_tree_interactions.py](./task31a_files_tree_interactions.py) | 文件树交互数据层底座：`update_file` 写回 subfolder、`set_file_subfolder` 移动逻辑目录、`rename_subfolder` 递归重命名子层级、`explicit_subfolders` 空文件夹设置往返 | [tasks/31a](../tasks/31a-files-tree-interactions.md) |
| [task_utils_opening.py](./task_utils_opening.py) | 工具函数回归：Windows `reveal_in_explorer()` 直接启动 Explorer，不经 `os.system`/shell，避免 GUI 定位文件时弹命令行窗口 | 跨任务工具函数 |
| [task54_project_sizes.py](./task54_project_sizes.py) | 项目体积统计口径、缺失/权限错误、取消、缓存容错/持久化/登记失效、分批查询与原子保存；临时库和自造文件，不读取文件内容 | [tasks/54](../tasks/54-project-sizes.md) |
| [gui_project_sizes.py](./gui_project_sizes.py) | 真实体积表头点击、数值排序、未知置后、持久索引及多选/当前项/预览保留；真实后台线程、主线程 SQLite、GUI 响应、取消无写入和右键多选入口；临时库 + Qt offscreen | [tasks/54](../tasks/54-project-sizes.md) |
| [task_status_consistency.py](./task_status_consistency.py) | 任务卡 `**状态**` 头与 `tasks/README.md` 索引表的完成度类别一致性（抓"一边完成一边待做"漂移）+ 索引完整性（双向） | 跨任务（无单一 task） |

## 预览与搜索交互回归

- `task70_project_hidden.py`：隐藏状态原子保存／回滚、库隔离和持久化、默认与显式搜索范围、计数、元数据不变、删除级联；显式临时数据库。
- `gui_project_hidden.py`：列表／网格批量右键隐藏与取消、菜单目标固定、侧栏计数和搜索同步、保存筛选、随机范围、错误反馈与重启；显式临时库与合成记录。
- `gui_preview_offline.py`：当前文件后台检查／超时、离线保留缓存、视频位置与播放意图、PDF 页码、自动重连暂停、权限与解码错误、过期结果和关闭、记录不变及详情／复制／重关联；显式临时库与自造媒体，需 ffmpeg。
- `gui_preview_media_errors.py`：图片／动图／视频／PDF 错误、恢复快照及过期 Qt 回调隔离；仅合成临时文件，不连接数据库。
- `gui_storage_direct_move.py`：直接移动设置、同盘／模拟跨盘、共享与重解析点保护、取消、写回失败回源、恢复冲突、完整错误清单及真实线程边界；显式临时库与合成文件。
- `gui_file_error_details.py`：超过十项失败仍提供完整文件清单，部分成功、移动写回失败、重关联与重命名的源／目标／阶段／异常说明；显式临时库。

- `gui_video_end_action.py`：视频结束停止／循环／下一文件，设置持久化与库隔离、来源过滤、过期事件防护；仅合成媒体和临时库，需要 ffmpeg。

新增预览与搜索交互回归（均为显式临时测试库）：

- `gui_preview_focus.py`：放大／全屏／Esc、键盘和双击、恢复分栏、关闭持久化及合成视频播放连续性；需要 ffmpeg 生成临时视频。
- `gui_auto_select_first_file.py`：首文件设置、排序／过滤、空项目、多选、同项目刷新、自动播放和库隔离。
- `gui_search_popup_dismiss.py`：补全弹层外部点击／焦点／Esc 关闭、候选点击与键盘选择、侧栏填入后的清空按钮。

## 不接入 CI 的原因

- 真 SQLite + 真临时目录 + Windows 下编码差异，跨平台跑很烦
- 这些脚本是开发者写完 task 时的"自我交代"，不是回归基线
- 等单元测试体系建起来后，端到端可以用 pytest fixture 重写并接 CI；
  那时这层会被自然替代

### 发布前回归（2026-09-09）

- `task42_security_regressions.py`：字面标签前缀/重叠迁移、模拟凭据失败与恢复、备份全字节密钥检查、恢复完整性和损坏配置处理。
- `gui_release_regressions.py`：大小缓存避免 stat、过期更新、文件/项目永久删除回退汇总、API 保存后即时反馈。全部使用临时库与模拟密钥。
