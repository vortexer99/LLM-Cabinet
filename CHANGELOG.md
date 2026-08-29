# Changelog

本项目沿用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

`__version__`（应用版本）和 `SCHEMA_VERSION`（数据库 schema 版本）独立递增。
schema 变化的发布需要在条目里显式标注 `📦 schema vX → vY` 并附迁移说明。

## [Unreleased]

📦 schema v7 → v8 — `files` 表新增 `origin` 列（`user`=用户原始文件 / `generated`=软件衍生物）。

### Added
- **API Key 安全存储（task #42）**：API Key 改存 Windows 凭据管理器（`keyring`，按库隔离），`llm_config` 里只留 `keyring:1` 引用标记；旧库明文 Key 首次读取时自动迁移；清空输入框同步删除凭据。备份 zip 与导出包不含 Key。「从其它库导入 API 配置」会把源库凭据重新挂到当前库作用域，挂不上的提示重新填写。凭据管理器不可用时回退明文存储并在「设置 → API」提示。`PRIVACY.md` / `PRIVACY.zh-CN.md` 同步更新。
- **窗口状态持久化与细节打磨（task #41）**：
  - 主窗口位置/尺寸/最大化状态在关闭时保存、启动时恢复（损坏数据自动回退默认）。
  - 新增快捷键：项目视图/文件表 `Delete` 删除（内联编辑时不误触发）；文件表 `F2` / `Shift+F2` 改为控件级快捷键（原来是全局事件过滤）。
  - 状态栏 MCP / LLM 计数标签改用新的 `ClickableLabel` 组件（替换 monkey patch）；搜索框按键处理迁到控件级过滤器，主窗口全局 eventFilter 只保留拖放追踪。
  - 文件表文件名列 hover 显示完整文件名（长文件名截断后也能看全）；目录节点 tooltip 显示完整路径。
  - 设置 → 通用 新增「界面字号」（11/12/13/14/16，立即生效）。
  - 移除分栏宽度恢复时的 `QTimer.singleShot` 二次 setSizes hack（自检验证移除后宽度仍稳定）。
- **预览面板增强（task #40）**：
  - 图片预览支持滚轮缩放（以光标为中心，10%~800%）、左键拖拽平移、双击在「适应窗口 ↔ 100%」间切换，底部控制条（放大/缩小/适应窗口/1:1/缩放百分比）。
  - 视频预览新增音量滑条、0.5x~2x 倍速、空格播放/暂停。
  - PDF 预览新增页码跳转（‹ › + 页码输入）、缩放控制（适应宽度/适应页面/50%~200%）与页码指示。
- **搜索框即时补全（task #38）**：输入时在搜索框下方弹出补全下拉，候选分「字段语法 / 标签值（`tag:` 语境，rating 字段给出 1~5）/ 收藏与最近搜索」三区；↑↓ 选择、Tab/Enter 补全、Esc 关闭（第一次按 Esc 只关补全不清空文本）。移除"聚焦搜索框自动弹历史菜单"的旧交互与状态 hack；历史/收藏仍由 ⌄ 按钮打开。新增 `Ctrl+F` 全局聚焦搜索框；语法错误色改用主题 danger 色。
- **多选批量操作面板 + 全局标签管理（task #39）**：
  - 多选项目时右侧不再空白，改为批量操作面板：批量加标签、LLM 元数据建议、导出、标记 MCP 已读、删除（与右键菜单能力一致的可视化入口）。
  - 标签树节点右键菜单：重命名标签（含 `前缀/...` 子标签整体迁移）、合并到其它标签、删除标签（显示影响项目数并需确认）；`repository` 新增 `rename_tag` / `merge_tag` / `remove_tag_everywhere` / `count_projects_with_tag`。
  - 项目区空白处右键新增「＋ 新建项目 / 📥 添加文件」菜单，与文件表空白菜单行为对齐。
- **基础搜索（task #03 Phase A）**：启用主窗口顶部搜索框，按标题/描述关键词过滤项目，支持与左侧标签、标签父节点、未分类、待审阅和 MCP 修改筛选叠加为 AND；MCP `query_projects(action="search")` 同步支持 `tag_prefix`。
- 新增 `selftests/task03_search_phase_a.py`，覆盖标题/描述关键词、keyword + tag/tag_prefix、未分类 keyword 与 MCP 搜索入口。
- **类 Calibre 搜索（task #03 Phase B）**：新增 `app/search.py` 递归下降解析器，支持纯关键词、字段过滤、标签、`AND` / `OR` / `NOT` 与括号；Repository 新增 `list_projects_query(ast)` 参数化 SQL 后端，MCP `query_projects(action="search")` 的 `field_filter` 同步接入；主界面搜索框新增“全库”切换，可临时忽略左侧筛选范围。
- 新增 `selftests/task03_search_phase_b.py`，覆盖字段 key / 字段显示名、rating/date 比较、多标签 AND、括号/NOT、语法错误与 MCP 精确搜索。
- **搜索历史与收藏表达式（task #03 Phase C）**：成功执行的搜索自动保存到 `settings.search_history`；搜索框焦点/下拉按钮可复用最近搜索；☆ 按钮可命名收藏当前表达式并保存到 `settings.saved_searches`。
- 新增 `app/search_history.py` 与 `selftests/task03_search_phase_c.py`，覆盖搜索历史去重/上限、坏 JSON 容错、收藏新增/覆盖/删除。
- 新增 `selftests/gui_main_window_regressions.py`，用 PySide6 offscreen 覆盖主窗口搜索菜单、全库搜索、GUI 宽关键词、MCP 已读右键目标、MCP 未读筛选刷新、目录/ZIP 项目包导出导入与分栏宽度等 GUI 回归。
- 新增 `tools/create_sample_library.py` 与 `docs/sample-library.md`，可生成完整样例库用于手工测试搜索、标签、文件树、缺失链接、导出导入、MCP audit、LLM 建议和搜索历史/收藏。
- 扩展样例库手测清单：补充逐步操作与预期结果，并新增深层目录、同名外链、缺失链接修复目标和空项目边界样例。
- 新增 `selftests/gui_sample_library_regressions.py`，把样例库手测清单中可自动化的搜索/筛选、文件视图、LLM 建议、MCP audit 和同名外链导出纳入 GUI 自检。
- 新增 `selftests/task_status_consistency.py`，检查任务卡头部状态与 `tasks/README.md` 索引表完成度类别是否一致，并纳入 selftests 索引。
- 新增 `selftests/task31a_files_tree_interactions.py`，覆盖文件树 `subfolder` 更新、递归重命名与显式空文件夹设置。
- 新增 `selftests/task29_file_storage_folder_ops.py`，覆盖文件夹粒度存储操作的递归范围与 missing-only 筛选。
- 新增 `selftests/task_utils_opening.py`，覆盖 Windows 文件定位不经命令行 shell 的工具函数回归。
- **文件来源标记（task #30）**：`files` 表新增 `origin` 列，区分用户原始文件与软件衍生物（如封面快照）。
  - 新生成的封面快照自动标记 `origin='generated'`。
  - 迁移时自动回填历史封面快照 `__cover_*.png` 为 `generated`。
  - 为后续 #28 导出/导入闭环、#04 文件折叠提供数据基础。
- **文件折叠二态视图（task #04）**：文件表上方新增 👤/🌐 toggle，切换「仅用户文件」/「显示所有」。无 generated 文件时自动隐藏，按项目持久化。
- **导出选项扩展（task #28 T1）**：
  - 导出模式：导出为独立包 / 仅导出项目元数据
  - 导出格式：目录形式 / ZIP 打包
  - 文件目录结构：保留目录结构 / 拍平到 files/
  - 内容选项：包含 README.md、LLM 任务历史
  - 封面图始终复制（即使不勾选"复制链接文件"）
  - files.json 新增 subfolder / is_cover / origin 字段（schema @3）
- **批量导出（task #28 T2）**：
  - 项目列表多选后导出，自动进入批量模式
  - 批量对话框显示项目列表（含文件数）
  - 可选择导出的项目
  - 进度条显示批量导出进度
- **导入增强（task #28 T3）**：
  - ZIP 包导入支持（自动解压到临时目录）
  - 封面图还原（cover_file_id 按新 file id 重映射）
  - 拍平结构目录树还原（优先用 files.json 的 subfolder）
  - 文件来源标记还原（origin: user/generated）
  - 菜单「工具 → 导入项目包...」入口
  - 拖放 ZIP 时可选择导入项目包或解压为文件夹
- **文件存储位置管理（task #29）**：
  - T1 链接转仓储：文件表右键「📦 转为仓储文件」，复制外部文件进库
  - T2 移动文件到新位置：文件表右键「📂 移动文件到...」，物理移动并更新路径
  - T3a 重关联到外部文件：文件表右键「🔧 重关联到外部文件...」，按文件名匹配修复 missing
  - T3b 替换链接目标：文件表右键「🔗 替换链接目标...」，单选链接文件替换目标
  - T3c 文件夹粒度入口：逻辑文件夹节点右键可批量转仓储、移动、重关联 missing 文件
- **文件视图增强（task #31a / #31b）**：
  - #31b 扁平视图模式：文件表新增「🌲/📋」按钮切换树形/扁平视图，按项目记忆
  - #31b 新增列：文件「大小」「添加时间」列（自动 stat 物理文件获取大小）
  - #31b 扁平视图排序：点击列头可排序（按项目记忆）
  - #31a 新建文件夹：文件表空白处/目录节点右键「📁 新建文件夹」
  - #31a 重命名：文件表右键「✏️ 重命名」修改文件说明（label）
  - #31a 树形视图列排序：同级内文件可按文件名/说明/类型/大小/添加时间/存储排序，文件夹始终置顶，按项目记忆排序状态
  - #31a 树形视图内部拖动：支持同级改序、拖入文件夹、拖回顶层，多选移动后写回 `files.ord` / `files.subfolder`
  - #31a 空文件夹持久化：显式新建的空 subfolder 存入 `project_settings.explicit_subfolders`，可右键删除空文件夹
  - #31a F2 进入说明列编辑，Shift+F2 可重命名仓储/链接文件的物理文件名；目录节点 F2 可重命名 subfolder
- **项目列表多选（task #25 Phase A/B/C）**：
  - 卡片视图/表格视图支持 Ctrl+点击多选、Shift+范围选择
  - 多选时预览区显示"已选 X 个项目"
  - 右键菜单区分单选/多选
  - 批量标记已读 MCP 修改
  - 批量删除（显示项目数量和文件统计）
  - 支持从项目列表拖动一个或多个项目到左侧标签/标签父节点，为项目批量追加标签

### Changed
- **主题系统统一为浅色单主题（task #34，按用户决策废弃深色）**：
  - 删除深色 QSS 与设置页的「主题」切换（含"不再维护深色"的旧声明）；`apply_theme(app)` 单参应用浅色主题。
  - 新增 `app/ui/palette.py` 统一色板：卡片 delegate、星级、DropZone、预览面板、搜索错误色、状态栏 hover 色等原先写死深色的 Python 侧颜色全部改读色板；浅色下网格卡片/预览不再是深色块。
  - 补齐浅色 QSS 缺失的 `QListView#ProjectGrid::item` 与搜索框规则，消除双主题时代的规则漂移。
- **长耗时操作线程化（task #36）**：导出（单/批量）、批量导入、链接转仓储、移动文件、库一致性检查、备份/恢复全部改为后台 `QThread` worker 执行，主线程不再出现 `QApplication.processEvents()`，大文件操作时界面不再卡顿或"未响应"。
  - 新增 `app/ui/workers.py`：`FileOpWorker` + `run_with_progress` 统一编排；进度对话框 WindowModal 天然防重入。
  - 线程边界：worker 只做纯文件 IO；sqlite 不跨线程——导出/检查走主线程快照（`ExportSnapshotRepo` / 行快照），文件移动/转换的 DB 更新在完成后由主线程按结果清单统一应用。
  - `importer.py` 拆为 `prepare_project_from_plan` / `copy_files_for_import` / `write_import_file_rows` 三阶段（`import_folder_as_project` 保持旧签名等价组合）；批量导入取消时未开始复制的项目会清掉空项目行，语义与旧版一致。
  - 备份/恢复打包不可取消（避免留残包）；一致性检查的"取消"按钮现在真正生效。
- **确认对话框与操作反馈统一（task #37）**：
  - 新增 `app/ui/dialogs.py` 中文对话框封装（`confirm` / `ask_yes_no_cancel` / `info` / `warn` / `error`），全 UI 约 140 处 `QMessageBox` 调用全部改走封装，确认框按钮不再出现英文 Yes/No；危险操作的默认焦点统一在「取消」。
  - 批量导入失败不再逐文件弹窗，改为结束后一次汇总（明细在详细文本里）。
  - 删除项目 / 移除文件时，仓储物理文件改为移入系统回收站（`Send2Trash`，回收站不可用时回退直接删除），删除确认文案同步说明；物理删除失败收集后统一汇报，不再静默吞掉。
  - `AGENTS.md` 新增「操作反馈策略」约定；关键静默 `except` 接入 `logging`。
- **项目列表与封面加载性能优化（task #33）**：
  - 封面改走缩略图缓存（新增 `app/ui/cover_cache.py`）：`QImageReader` 解码阶段即按卡片尺寸 × DPR 缩放，结果入 `QPixmapCache`（64MB，按 mtime 失效）；卡片 delegate 不再在 paint 热点做实时缩放，网格滚动更顺滑、内存占用显著下降。
  - 项目列表文件数改为一条 GROUP BY 聚合查询，替代逐项目 `list_files` 的 N+1；字段定义未变化时跳过列表列重建（不再每次刷新都 model reset）。
  - MCP 轮询（10s）发现新操作时不再整屏重建：仅更新标签树计数，只有正在看「未读 MCP 修改」筛选时才整刷。
  - 文件表「大小」列改为会话级缓存（按 mtime/size 自动失效），重复切换项目不再重复 stat；项目详情只查一次文件列表。
  - 「当前库信息」对话框的 library/ 大小统计挪到后台线程回填，大库不再卡住弹窗。
- **MainWindow 拆分与 UI 分层（task #35，纯重构零行为变化）**：
  - `main_window.py`（约 4900 行）拆为 5 个 mixin：`mw_library.py`（库菜单/工具）、`mw_projects.py`（项目列表/操作/LLM 入口）、`mw_files.py`（文件面板/封面）、`mw_dnd.py`（拖放导入）、`mw_search.py`（搜索框/历史收藏）；主文件只留组装与接线，降到 568 行。
  - `settings_dialog.py`（1562 行）拆为 `app/ui/settings/` 包：`dialog.py` 框架 + 每页一个 `page_*.py` mixin + `field_dialogs.py` 字段小对话框；删除死代码 `_on_columns_changed`，非懒加载的局部 import 全部归并到模块头部。
  - UI 层不再直接执行 SQL：文件总数、MCP 审计游标/客户端/工具清单、pending 建议计数、WAL checkpoint、一致性检查行快照等全部下沉 `repository` / `library_check`。
  - 两次拆分均由 AST 脚本（`tools/split_main_window.py` / `tools/split_settings_dialog.py`）搬运，方法体零改动；GUI 回归全部通过。
- 开发约定改由 `AGENTS.md` 作为单一来源，`CLAUDE.md` 仅保留到该文件的导入指针；同步修正任务卡与 `tasks/README.md` 中已完成/进行中任务状态。
- 普通关键词搜索从标题/描述扩展为标题、描述、标签、自定义字段值、文件名/文件说明/逻辑目录名；字段表达式仍保持精确字段搜索。
- 任务规划重组（基于 `docs/file-handling.md` 评审）：
  - 新增 `tasks/32-cross-project-file-reference.md`：跨项目链接引用最小方案（路径共享 + 多引用警告 + #14 跨项目引用报告 + Windows path `normcase` 归一化 + 文件表角标提示），零 schema 改动。
  - 拆分 `tasks/31-...`：原卡保留为指针，新增 `tasks/31a-files-tree-interactions.md`（树形视图拖动 / 同级排序 / 新建空 subfolder / F2 重命名）+ `tasks/31b-files-table-flat-view.md`（扁平视图模式 + 大小/添加时间列 + Qt 原生列排序）。排序持久化按视图分键 `files_table_sort_tree` / `files_table_sort_flat`。
  - 扩展 `tasks/29-file-storage-location-management.md`：原 T1/T2 之外新增 T3a 重关联到外部文件（修复 missing）/ T3b 替换链接目标（单选）/ T3c 文件夹粒度批量入口。
  - 收尾 `tasks/04-project-system-files-folding.md`：从"待澄清"敲定为"二态视图（仅用户文件 / 显示所有）"，默认显示所有 + 无 generated 时 toggle 自动隐藏，消费 #30 origin。
  - 同步 `docs/file-handling.md` 任务地图、推荐执行顺序、`tasks/README.md` 索引表、`TODO.md` 条目。

### Fixed
- 全新库直接创建到当前 schema 时补建 `mcp_audit` 表，避免主窗口首次启动查询 MCP 审计状态时崩溃。
- 主界面左/中/右三栏宽度在拖拽后会写入设置，重启后恢复上次宽度。
- 项目右键菜单的「已读MCP修改」会固定使用本次右键目标，避免因当前选区未更新而清错或没有清除 MCP 未读标记。
- Windows 上文件「定位」改为直接启动资源管理器，不再通过命令行 shell 中转，避免弹出命令行窗口并提升响应速度。
- 样例库占位图片改为生成合法 1x1 PNG，避免 GUI 加载封面时输出 libpng CRC 警告。
- 主界面搜索栏右侧按钮重新分组并固定尺寸，避免「全库」、收藏下拉与项目视图切换按钮挤在一起。

## [0.5.0] - 2026-06-10

📦 schema v6 → v7 — `files` 表新增 `subfolder` 列（逻辑子目录路径，驱动 UI 树形展示）。

### Added
- **子文件夹导入 + 文件树形展示（task #17）**：拖入含子目录的文件夹时，子目录文件全部导入并保留逻辑目录结构。
  - `files` 表新增 `subfolder` 字段，UI 文件表从 `QTableWidget` 改为 `QTreeWidget`，按 `subfolder` 折叠展示。
  - 物理存储与 UI 组织解耦：仓储模式继续拍平，`subfolder` 纯粹是数据库字段。
  - 删除文件后空逻辑目录自动消失；选中目录节点可连带删除整棵子树。
- **文件列表独立窗口（task #02）**：文件区标题行新增 ⇱ 按钮，点击后文件列表脱离主窗口成为独立 700×500 窗口；关闭窗口或再次点击（变 ⇲）自动收回到主窗口。独立窗口里仍能选文件、右键菜单、拖入新文件，预览面板与主窗口同步。
- **导入深度/文件数检查**：目录层级 ≥ 5 或文件数 ≥ 500 时弹确认对话框。
- **空文件夹处理**：单个空文件夹拖入创建 0 文件项目；多个空文件夹弹提示告知用户。
- **导入安全防护**：禁止导入库目录自身，防止递归导入。
- **隐藏文件过滤**：设置项「忽略 . 开头的文件和文件夹」（默认开启），导入时跳过 `.gitignore`、`.env`、`.git/` 等隐藏文件和目录。
- README 新增 "Looking Further: AI Team Workspace" 章节（英文/中文），引入 `gallery/AI-Team-Workspace-Concep.jpg` 概念图，说明 Cabinet 作为多 Agent 共享记忆中枢的远期定位。`gallery/README.md` 增加 Concept artwork 索引段。
- 新增任务卡 `tasks/27-provenance-source-link.md`：MCP 写入可附带 `source`（依据文件 + 备注），UI 显示「来源」入口；作为推向 AI Team Workspace 定位的第一步。
- 新增任务卡 `tasks/28-export-structure-option.md`：导出项目时可选保留目录结构或拍平（依赖 #17）。

### Changed
- 合并模式导入目录时始终保留目录名作为 `subfolder` 前缀（如拖入 `myfolder/a.txt` → `📁 myfolder/`）。
- README 中新增 1.0.0 前尝鲜提醒（英文/中文），不建议投入重度使用。

## [0.4.1] - 2026-06-05

### Added
- MCP 集成截图（Agent 端 + Cabinet 端），放入 `gallery/` 并在 README 中引用。
- 项目右键菜单新增「已读MCP修改」选项：清除项目的 MCP 修改标记。对所有项目常驻显示，方便以后多选批量操作。

### Changed
- `TODO.md` 清理：修正过期状态标记、简化索引表（单一链接指向 `tasks/README.md`）、
  更新版本规划。
- "MCP 修改过"标签改为「未读 MCP 修改」，语义更准确。

### Fixed
- 标签从项目移除后仍然残留于 `tags` 表（孤儿标签）；现 `_set_tags` 末尾自动清理。
- 关闭 MCP 审计对话框后调用不存在的方法名 `_update_mcp_count` 导致闪退；修正为 `_check_mcp_activity`。

## [0.4.0] - 2026-06-04

📦 schema v4 → v5 — 添加 `mcp_audit` 审计日志表。
📦 schema v5 → v6 — `projects` 加 `mcp_modified_at` 列，MCP 写操作自动打标。

### Added
- **MCP Server**：通过 Model Context Protocol 把库暴露给外部 AI agent（Claude Desktop /
  Cursor / Cline / Cherry Studio）。5 个聚合工具 + 8 个数据资源 + 4 个任务提示，支持三种写权限控制。
  独立进程 `python -m app.mcp.standalone` 与 GUI 并行运行，SQLite WAL 保证并发安全。
- **MCP 操作记录查看面板**：状态栏 `📋 MCP 操作` 入口，双 Tab 对话框（审计日志 +
  被 MCP 修改过的项目），支持筛选、翻页、清空。标签树新增"🤖 MCP 修改过"筛选项。
- **Agent 技能系统**：4 个内置 SOP（整理入库 / 审核质量 / 生成概览 / 推荐标签），
  YAML frontmatter + 目录结构，`prompts.py` 从文件加载。Release 页可单独下载
  `llm-cabinet-skills.zip`。
- **MCP 增强**：`get_fields` action / `default_storage_mode` / `field_values` 参数 /
  audit 日志补全 / `label` 强制要求 / `storage_mode` 遵循库设置。
- **CI 技能打包**：每次构建同时产出技能 zip 包。
- **UI 优化**：隐藏未使用标签、应用数据目录移至通用设置页、MCP 设置页更新。

### Changed
- **BREAKING · MCP 工具收敛**：17 个细粒度工具 → 5 个聚合工具（`query_projects` /
  `manage_project` / `manage_files` / `manage_libraries` / `export_project`）。
  已接入的 MCP 客户端需更新配置，重新导出 JSON。

### Removed
- `trigger_llm_suggestion` / `apply_suggestion` / `list_pending_suggestions`（agent
  套 AI 反模式，agent 场景无意义）
- `import_folder`（`create_project` + 多次 `add_file` 组合替代）

---
📎 0.3.0 起的完整提交历史见 [备份](CHANGELOG.backup.md)。

## [0.3.0] - 2026-06-04

📦 schema v2 → v3 — `fields.prompt_hint` + `files.missing` 列。
📦 schema v3 → v4 — 废弃系统字段的 `projects` 列（author/date/source_url/rating），
统一存入 `project_field_values`。

### Added
- **字段助手两段式重构**：Step 1 审阅 LLM 建议 ↔ Step 2 编辑字段表，消除一张表两种语义混乱。
- **字段助手 UI 优化**："LLM 建议"列文案用户友好化（新增/删除/修改 × 维度）、
  场景页描述编辑、库描述批准/驳回、LLM 显式删除/改名建议、类型变更安全护栏。
- **字段存储统一**：4 个系统字段值从 `projects` 列分流到 `project_field_values`，
  所有非保护字段值存储完全统一。
- **库字段设计助手打磨**：场景页提示 · 库描述批准/驳回 · LLM 显式删除/改名建议 ·
  改名+改类型组合处理 · 多次交互修复（10+ rounds）。
- **库管理增强**：一致性检查（扫描失效文件 + 标记缺失）· 备份/恢复（zip 打包）·
  搬家指引。
- **多库并存与切换**：完整的库注册表（最近 / 切换 / 新建 / 删除 / 从列表移除）。
  当前库也可删除/移除，事后重启走 Welcome 重新选库。
- **新建库 onboarding**：多页向导（目录+名称 / 库描述 / 默认字段 / API 迁移）+
  首次进入引导横幅 + Welcome 对话框三档选择。
- **字段级 LLM 提示**：每个字段可单独配置格式说明（如"标题不超过 30 字"），
  prompt 拼装时作为上下文注入。
- **更多文档格式提取**：pptx / odt / odp / ods / epub / html / rtf 的内容提取，
  纯标准库零依赖。
- **标签层级折叠**：`/` 为分隔符，标签树按前缀分组，折叠状态持久化。
- **批量文件夹导入**：多文件夹拖入识别 `project.json`，支持 auto-create/append 字段策略。
- **启动期 crash logger**：全局 `sys.excepthook` 写 `crash.log`，弹 QMessageBox 提示。

### Changed
- **类型变更加安全护栏**：改类型时弹确认（列出受影响的记录/pending 建议/旧提示），
  project_field_values 原值保留不动。
- 新建库默认仅创建标题/标签/描述三个保护字段（可选预置作者/日期/评分/来源）。
- 多文件夹拖入默认行为改为"分别建一个项目"（可切换为合并）。
- 字段助手 type_conflict 改为批准=原地改/驳回=不动（不再绕路创建新字段）。
- LLM 建议列去掉决策态后缀，驳回不改变维度展示。

### Fixed
- 文件表表头最后一列空白区深色模式发白。
- 工具栏导出/右键菜单 LLM 建议因 `bool` 被当 `int` 传入导致无反应。
- 项目编辑 date 字段默认今天且无法置空（改为可选留空 + 📅 弹日历）。
- 深色模式下设置页 GroupBox 白条遮标题、新建库向导 hint 文字不可见。
- `human_size()` 重复定义。

### Notes
- 深色模式从 v0.3.x 起不再维护（保留现有样式但不修新增功能的适配 bug）。

---
📎 0.3.0 详细的 round-by-round 修复记录见 [备份](CHANGELOG.backup.md)。

## [0.2.0] - 2026-05-31

📦 schema v1 → v2 — 移除残留 `custom_fields` 旧表。

⚠️ BREAKING：应用数据目录由 `%APPDATA%/Fileman/` 改为 `%APPDATA%/LLMCabinet/`，
默认数据库由 `fileman.db` 改为 `cabinet.db`。v0.1.0 用户需手动搬移目录。

### Added
- 项目导出（`project.json` + 文件副本 + README.md）
- 数据库迁移注册表首次启用
- 关于页新增免责声明

### Fixed
- 关于页图标在多分辨率 ico 下模糊
- 工具栏导出/右键菜单 LLM 建议点击无反应（`bool` 被误判为 `int`）

---

## [0.1.0] - 2026-05-31

初始版本。项目化文件管理（卡片/列表双视图）、字段系统、标签筛选、
文件预览、拖放、LLM 元数据助手（DeepSeek / OpenAI / Gemini / Grok）。

📦 schema v1 — 初始 schema。

[Unreleased]: https://github.com/vortexer99/llm-cabinet/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/vortexer99/llm-cabinet/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/vortexer99/llm-cabinet/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.4.0
[0.3.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.3.0
[0.2.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.2.0
[0.1.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.1.0
