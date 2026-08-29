# 35 · MainWindow 拆分与 UI 分层整理

**工作量**：L
**优先级**：P1
**状态**：✅ 2026-08-01（v0.6 未发布）

## 来源

2026-07-31 前端评审。`main_window.py` 已 205KB / 约 4900 行，`MainWindow` 一个类包揽菜单、库管理、搜索、项目 CRUD、文件 CRUD、拖放、导入导出、封面、子文件夹、MCP 轮询、LLM 任务。UI 层还直接写 SQL、做物理文件操作。继续在这种结构上叠加功能（#26/#27/#32 都在排队）会越来越痛苦。

## 现状盘点

| 问题 | 位置 |
|------|------|
| `MainWindow` 约 4900 行，职责全包 | `main_window.py:198` 起全文 |
| UI 直接执行 SQL | `main_window.py:466`（COUNT files）、`2708`（MAX mcp_audit id）；`settings_dialog.py:732`（pending 计数） |
| 业务逻辑在 UI 层：删项目时物理清理 unlink/rmdir、封面快照落盘、文件移动/转仓储循环 | `main_window.py:2908-2920, 4048-4082, 3640-3816` |
| 局部 import 泛滥且与顶部重复（顶部已有 `QAction/QKeySequence`，函数内再 import） | `main_window.py:277, 361, 386, 459, 543, 676` 等几十处 |
| `settings_dialog.py` 1600 行，7 个 page 一个文件 | `settings_dialog.py` 全文 |
| 死代码 | `settings_dialog.py:420` `_on_columns_changed` |
| 全局 eventFilter 挂 QApplication 拦截一切 | `main_window.py:4427` |

---

## 范围与边界

| 子任务 | 内容 | 工作量 |
|---|---|---|
| **T1** | 下沉：UI 层 SQL → `repository`；删项目/封面/移动文件等物理操作 → `library`/服务层 | S |
| **T2** | 拆 `main_window.py` 为多个模块（下表） | L |
| **T3** | 拆 `settings_dialog.py` 为每 page 一个文件；清理死代码与重复局部 import；eventFilter 收窄到具体控件 | S |

**不做（本卡内）**：
- 任何行为/交互变化 —— 纯重构，selftests 必须全绿，手工冒烟主流程
- 引入 MVC/MVVM 框架级改造 —— 只做"搬代码 + 收敛依赖"，不重写状态管理

---

## T1 · 分层下沉

1. `repository.py` 新增（UI 改为调用）：
   - `count_files_total()`
   - `max_mcp_audit_id()`
   - `count_pending_suggestions_for_field(fid)`
2. 下沉到 `library.py`（或新 `app/services/`）：
   - 删除项目的物理清理（仓储文件 unlink + 项目目录 rmdir + 失败收集）
   - 封面快照保存（`_save_cover_snapshot` 的 IO 部分）
   - #29 移动/转仓储/重关联的单文件执行函数（UI 只保留确认对话框与进度编排）
3. UI 层不再出现 `self.repo.conn.execute(`（selftest 加一条 grep 断言）

## T2 · main_window.py 拆分

目标结构（`app/ui/`）：

| 新模块 | 收编内容 |
|---|---|
| `library_menu.py` | `_build_menubar`、`_lib_*` 全部、删库流程、`_ask_delete_mode`、工具菜单（备份/恢复/一致性检查） |
| `files_panel.py` | 右下文件表：树/扁平填充、列偏好、排序、subfolder 操作、#29 存储位置管理、文件上下文菜单 |
| `project_actions.py` | 项目新建/编辑/删除/导出/批量、项目上下文菜单、封面相关 action |
| `drop_controller.py` | DropZone 显隐、eventFilter 拖放部分、`_expand_paths`、拖放导入编排、批量文件夹导入 |
| `search_bar.py` | 搜索框、防抖、历史/收藏菜单、语法错误显示 |
| `main_window.py` | 只保留：组装三栏、信号接线、 splitter 状态、状态栏、MCP 轮询 |

- 各模块以"面板/控制器"形式存在，构造函数注入 `repo/library` 与需要的回调；避免回环引用 MainWindow（必须回调的地方用 Qt 信号）
- 每搬一块跑一次 selftests + 手工冒烟，分多个 commit，不要一个巨型 commit

## T3 · settings_dialog 拆分 + 杂项

- `settings_dialog.py` → `settings/` 目录：`dialog.py`（框架）+ `pages/{general,library,view,fields,api,mcp,about}.py`
- 删 `_on_columns_changed`
- 局部 import 只保留"真正为了懒加载"的（wizards、QtMultimedia 等），其余归并到文件顶部
- eventFilter 从 QApplication 全局改为只挂 `search_box` / `tbl_files.viewport()` 等目标控件；DropZone 显隐改用各拖放控件的 dragEnter/Leave 事件驱动

---

## 校验

- [x] `python -m py_compile` 全部新模块通过；selftests 全绿（26/26 脚本，含 GUI 回归 58 断言）
- [ ] 手工冒烟：新建/编辑/删除项目、添加/删除文件、拖放导入、搜索筛选、切换库、删库、备份恢复、设置各页 —— 行为与重构前一致（留待发布前人工过一遍）
- [x] `grep "repo.conn.execute" app/ui/` 为零
- [x] `main_window.py` 降到 800 行以内（568 行）

## 完成记录（2026-08-01）

- **T1**：`repository` 新增 `count_files_total` / `max_mcp_audit_id` / `count_pending_suggestions_for_field` / `wal_checkpoint` / `list_mcp_audit_clients` / `list_mcp_audit_tools` / `clear_mcp_audit` / `last_mcp_tool_by_project`；`library_check.snapshot_file_rows`；UI 层 SQL 清零。
- **T2**：`tools/split_main_window.py`（AST 搬运，方法体零改动）把 153 个方法切到 `app/ui/mw_library.py` / `mw_search.py` / `mw_dnd.py` / `mw_files.py` / `mw_projects.py` 五个 mixin；`main_window.py` 保留 11 个方法（568 行）。`NoElideDelegate` 移至 `widgets.py` 解决循环引用。
- **T3**：`settings_dialog.py`（1562 行）拆为 `app/ui/settings/` 包 —— `dialog.py`（框架 + 信号）+ `page_{general,library,view,fields,api,mcp,about}.py`（每页一个 mixin）+ `field_dialogs.py`（3 个字段小对话框）；`tools/split_settings_dialog.py` 搬运。死代码 `_on_columns_changed` 已删；局部 import 归并（仅保留 llm / wizards 懒加载）；修复搬入包后深一层的 `parents[2]→parents[3]` 与相对导入。
- **eventFilter 收窄**：#41 已把全局过滤器收窄为仅拖放事件；DropZone 显隐保留 QApplication 全局过滤（子控件间移动不触发顶层 DragLeave，控件级方案会闪烁），dnd.py 的 ProjectViewDnD/FilesTableDnD 已是控件级。
- **自检修复**：GUI 回归中「文件定位」用例的 monkey patch 改打到 `mw_files` 命名空间；`_close_window` 停 `_search_timer` 并冲刷 DeferredDelete（消除 closed database 噪音）。

## 依赖

- **建议在 #33（性能）、#36（线程化）、#37（反馈统一）之后做**：那些卡会新增/大改 main_window 内的代码，先落地再搬，避免搬完又改、改完又搬
- 落地后再启动 #26/#27/#32 等新功能卡

## 风险

- 大重构必出小回归 → 拆分时严格"一次一块 + 每块可验证"；保留逐块 commit 方便 bisect
