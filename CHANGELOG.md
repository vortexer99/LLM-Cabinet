# Changelog

本项目沿用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

`__version__`（应用版本）和 `SCHEMA_VERSION`（数据库 schema 版本）独立递增。
schema 变化的发布需要在条目里显式标注 `📦 schema vX → vY` 并附迁移说明。

## [Unreleased]

📦 schema v3 → v4 — 废弃系统字段的 `projects` 列分流（task #20）：把
`projects.{author,date,source_url,rating}` 4 列的值搬到
`project_field_values` 后 DROP COLUMN。`fields.key` 中的 `author/date/
source_url/rating` 仍保留，仅作"种入时稳定标识"用（受保护判定、新建库
向导默认勾选、导入器宽松匹配），不再决定值的存储位置。打开旧库会自动生成
`cabinet.v3.<时间戳>.bak` 备份后再迁移。

历史上的 `📦 schema v2 → v3` 也包含在本次发布里（`fields.prompt_hint` 列
+ `files.missing` 列的合并迁移，task #11 T1 + task #14 T1）。

### Changed
- **task #20 废弃系统字段的 projects 列分流（schema v3 → v4，2026-06-03）**：把
  `projects.{author,date,source_url,rating}` 4 列的值搬到
  `project_field_values` 后 DROP COLUMN，让所有非保护字段（含老 4 个系统字段）
  值的存储完全统一。这是历史性的"二元世界观"清理：
  - **schema 层**：`SCHEMA_VERSION = 4`；`SCHEMA` 字符串里 `projects` 表去掉
    那 4 列；新增 `_migrate_v3_to_v4` 函数搬运数据 + `ALTER TABLE DROP COLUMN`，
    幂等（用 `PRAGMA table_info` 守卫，列已 DROP 时整段跳过）。
    rating "未填" 语义保护：v3 里 `rating INTEGER DEFAULT 0`，业务上 0 = 未填，
    迁移条件 `WHERE rating != 0` 而不是 `IS NOT NULL`，避免把"未填"误写成
    字符串 `"0"`。`INSERT OR IGNORE`：用户可能在 v3 时通过手工 SQL 已在
    `project_field_values` 里给老系统字段写过值，迁移保留已有值不覆盖。
  - **`Project` dataclass**：删除 `author / date / source_url / rating` 4 个
    顶层字段。所有调用方（12+ 处 `.author/.date/.source_url/.rating` 访问）
    改成 `repo.get_field_value(p, f)` 或直接 `p.field_values.get(fid)`。
  - **`Repository` 层**：删除 `SYSTEM_FIELD_COLUMNS` dict（v4 后无用）；
    `save_project / _row_to_project / list_projects keyword 搜索 /
    apply_field_plan_batch / delete_field /
    _collect_field_values_for_all_projects / get_field_value /
    set_field_value_on_project` 全部走 `field_values` dict 路径（受保护字段
    走 Project 顶层 + 独立 tags 表）。
  - **导出导入兼容**：`exporter` 顶层 `project.{author,date,source_url,rating}`
    保留作为向后兼容输出（从 `field_values` 反查 key→fid），同时这 4 个字段
    也出现在 `field_values_blob` 里——这样 v3 客户端读 v4 文件能从顶层取到
    值；v4 客户端读 v4 文件主要走 `field_values_blob`。`importer` 兜底逻辑
    扩展：顶层老 key 通过 `key→fid` 反查写进 `fv_by_id`（v3 文件兼容）；
    `field_values_blob` 段不再用 `is_system` 判断跳过，只跳过 `is_required`
    保护字段（title/description/tags）。
  - **字段助手 `system_protected` 状态彻底废除**：v3 时代用于"is_system 但
    类型与建议不符"的特殊跳过被识别为 dead code 删除（保护字段先被
    `_SYSTEM_REQUIRED_NAMES` 分支吃掉、改名又被 repo 拦截，永远走不到该
    兜底）。v4 起 `author/date/source_url/rating` 等"带 key 但非 protected"
    字段被 LLM 建议改类型时走 `type_conflict` 路径（被 task #19 Phase B 接管，
    批准 = 原地改 / 驳回 = 不动）。
  - **`is_system` 语义重新定义**：v4 后 `is_system`（key 非空）仅表示"种入时
    带稳定标识"，不再决定值的存储分流。代码里曾用 `is_system` 做存储路径
    分流的地方全部改成精确判定（`is_required` / `key in {...}`）。
    `models.py::Field.is_system` property 的 docstring 同步更新，明确推荐用
    `is_required` 判断保护字段、用 `key in {...}` 判断具体行为。
  - **小改动**：移除 `ProjectCardDelegate` 在网格视图卡片底部的评分星星
    渲染（原代码读 `p.rating > 0`；delegate 无 repo 注入无法优雅查 rating
    fid）；项目卡片表格里的评分列仍正常显示。
  - **selftest**：新增 `task20_unify_field_storage.py` 48 条断言（4 种迁移
    路径 + 全新库 / 端到端 v3→v4 / Project 顶层属性删除验证 / get/set
    field_value 走 field_values 路径）；改造 `task10_folder_import.py` 的
    setup_lib_a/b 注入老系统字段 + 断言改成按 fid 查 field_values；改造
    `task14_library_management.py`（`SCHEMA_VERSION = 4`）；改造
    `task19_field_type_change.py::test_count_field_filled_for_system_fields`
    为 v4 语义；新增 `task11_t3` 5 条 v4 type_conflict 断言（老系统字段
    LLM 改类型走 type_conflict / 受保护字段仍走 system_required）。全套 10
    个 selftest 共 639 条断言通过（v4 前是 9 个 / 586 条）。
- **库字段设计助手对话框高度 +10%**（2026-06-02）：`LibraryInitWizardDialog` 默认 resize 从 `900×640` 改为 `900×704`，给预览页字段表多出约一行多的可见空间，减少 LLM 给出较多字段时需要滚动的频率。
- **字段助手 type_conflict 改为「批准 = 原地改类型 / 驳回 = 不动」二态（task #19 Phase B，2026-06-02）**：基于 Phase A 已经把"安全地改字段类型"做安全，字段助手的 type_conflict 路径不再需要绕路"改名为 `<原名>_v2` 创建新字段"。新行为：
  - 预览表的 type_conflict 行**字段名只读 + 类型 ComboBox 禁用**（用户改类型的入口在「设置 → 字段」，字段助手里只批准/驳回 LLM 给的建议）；状态列改为 `⚠ 类型冲突 · 改类型`。
  - **批准**（或未驳回的默认接受）= apply 阶段对 `existing_field_id` 改 type + 用 LLM 配套给的新 hint 覆盖旧 hint + supersede 该 fid 的 pending 建议（三件事同事务）；**驳回** = `selected=False` + type/hint 还原到旧值，action 变 skip，字段彻底不动。
  - 驳回时 reason 补一句引导：`"已驳回；如果想另建一个新名字的字段，可以用预览表底部的「＋ 添加字段」按钮"`，跟 `llm_suggest_delete` / `llm_suggest_rename` 驳回风格一致。
  - 行操作「删除」对 type_conflict 行 = 退化成 `existing_user_field` + `selected=False`（跟 `llm_suggest_rename` 的行删除处理对齐），还原 type/hint 到旧值后再标记删除。
  - apply 入口在事务执行前**弹一次** `_BatchTypeChangeConfirmDialog`，列出所有要改类型的字段（旧→新 + N 条值保留 + M 条 pending 失效 + LLM 已提供配套新 hint 说明）；用户取消 → 回到预览页。
  - **breaking change**：`Repository.apply_field_plan_batch` 新增 `type_changes: list[tuple[int, str, str]]` 参数，返回值从 3-tuple `(new_ids, n_deleted, n_renamed)` 改成 4-tuple `(new_ids, n_deleted, n_renamed, n_type_changed)`。task11_t3 内 6 处旧调用全部同步改成 4-tuple 解包。
  - **关键设计区别于 Phase A**：Phase A 弹窗里有「☑ 同时清空旧 LLM 提示」checkbox（因为库设置那边只动 type，hint 跟新类型不匹配），Phase B **没有这个 checkbox**——LLM 的 `fields[i]` 输出本身就是 `{name, type, prompt_hint}` 三元组，给出新类型时已经配套给了适配新类型的新 hint（存在 `ann.prompt_hint`），apply 时直接覆盖即可。
  - **LLM 同时给改名 + 改类型 的边界处理**：LLM 协议上允许在同一轮里既给 `fields_to_rename: 出版社→出版商` 又在 `fields[出版商]` 里把 type 从 `text` 改成 `date`。rename 路径**不能**改类型（那是 `type_conflict` 的职责），否则会丢失项目历史数据的语义。现行处理：rename ann 保留旧 type，但在「解析告警」区给一条 warning：`"LLM 同时建议把「X」改名为「Y」并把类型从 A 改为 B；为保留项目历史数据，rename 路径仅改名不动类型——如需改类型，请在批准本次改名后到「设置 → 字段」对「Y」单独操作"`。`annotate_conflicts` 新增可选参数 `out_warnings: Optional[list[str]] = None`（默认 None 静默丢弃，向后兼容所有现有 selftest）；`_on_llm_finished` / `_smart_reapply_llm` 把 wizard 的 warning 通道接进去，让 warning 显示在预览页顶部那条 `lbl_warnings`。
  - 删掉 `AnnotatedSuggestion.effective_name` 里的 type_conflict 改名分支；删 `_on_rename_changed` 槽函数（type_conflict 行已无 LineEdit）；`rename_to` 字段保留在 dataclass 定义里但路径上不再被读写。
  - task11_t3 selftest 旧 type_conflict 断言（5 条："默认 selected=False / action=skip / 勾选后 create / effective_name 走 rename_to / rename_to 清空退化 skip"）按新语义重写；阶段 12 新增 11 条 Phase B 断言（批准/空 hint 边界/受保护字段静默跳过/不存在 fid 静默跳过）；5d-9/10/11 新增 6 条 rename + 改类型 warning 边界断言（触发/不触发/`out_warnings=None`）。全套 9 个 selftest 共 586 条断言通过。
- **字段类型变更加安全护栏（task #19 Phase A，2026-06-02）**：在「设置 → 字段」和「字段助手 → 现有字段表」里改字段类型，从一条无声 SQL 升级为护栏路径。以前用户切类型只动 `fields.type`，`project_field_values.value` 留着旧字符串、新类型的控件读不出来 → 用户更新元数据时控件空状态把原值无声覆盖；`fields.prompt_hint` 跟旧类型绑定（"格式：YYYY-MM-DD" 切到 rating 后照样喂 LLM）；`project_field_suggestions` 里按旧类型生成的 pending 建议照常出现在 LLM 建议汇总，接受 → 把垃圾值写进 `project_field_values`。新方案：
  - 加 `app.models.is_compatible_type_change(old, new)` 兼容性矩阵函数：同类型 / 任意→text/textarea / rating→number 视为兼容（静默切，不打扰用户）；其它组合视为不兼容（弹确认）。
  - `Repository.set_field_type` 加两个可选 kwargs：`supersede_pending_suggestions`（把该 fid 所有 pending 建议批量标 `superseded` + `resolved_at`）、`clear_prompt_hint`（清空旧 hint）；三件事进同一事务避免崩溃留下半截状态。默认 kwargs 关闭 → 完全向后兼容老调用点。
  - 新增 `_FieldTypeChangeConfirmDialog`：列出"N 条非空记录值会保留（但更新项目元数据时可能被空值覆盖）/ M 条 pending 建议会失效 / 旧 LLM 提示「<前 30 字>...」可能不匹配"三条按需显示的说明；hint 非空时挂一个「☑ 同时清空（推荐）」复选框；三条全为空（无值 + 无 pending + 无 hint）→ 即使技术上不兼容也跳过弹窗静默切。
  - **统计字段填充数必须经 `Repository.count_field_filled`**（首发版 hotfix）：系统字段（author / date / source_url / rating）的值存在 `projects` 表对应列里、**不在** `project_field_values`。`_count_field_impact` 首版直接 `SELECT FROM project_field_values` 会让系统字段永远算 0，误走"三条全空 → 静默切"路径而不弹确认对话框。修正为复用现有的 `repo.count_field_filled(f)`（它已按字段类型分流到正确表）。`library_init.py::_existing_field_change_type` 同步修正。selftest 加一支 `test_count_field_filled_for_system_fields` 防回归。
  - `project_field_values.value` **始终一字不动**——保留原字符串供"切回旧类型恢复显示"的兜底；这是核心保护，对齐 Notion 的实践。
  - 字段助手向导第 2 页「现有字段管理」表里的类型 ComboBox（`_existing_field_change_type`）同步走护栏，避免两个等价入口保护程度不一。
  - **已知限制**（已记入 task #19 风险节）：1) 项目编辑对话框加载脏值后显示为空，用户保存仍可能覆盖原值——彻底修复需要在控件加载时检测不兼容值 + 显示"原值 / 与当前类型不兼容"提示，另开卡。2) 改类型瞬间有 LLM 任务在跑会写入按旧类型语义的新 pending；本卡暂不堵。3) 任务面板「重新应用建议」可把 superseded 建议复活；本卡暂不堵。
  - 受保护字段（标题/描述/标签）保持现有"静默忽略"语义；`old == new` 时也直接 return（防止 noop 调用误伤 pending）。
- **TODO 小修小补一锅出（2026-06-02）**：清掉 `TODO.md` 里 5 条 🐛 + 1 条 🧺。
  - **设置 → 通用 的「LLM 助手对话轮数」搬到设置 → API 页**：与默认 provider / 默认语言 / 各平台 base_url+key+model 同页，更聚焦 LLM 配置入口。`wizard_max_rounds` 这个 setting key 不变，老库无需迁移；通用页留一行注释指向新位置。
  - **库字段设计助手第一页加预调整提示**：在「当前库的字段」标签下方追加一行 hint，明确说明"这里增删改只是给 LLM 的输入起点，**点「让 LLM 给出建议」之前不会写入当前库**，想直接编辑库字段请用「设置 → 字段」"。避免新用户误以为已经在编辑现库。
  - **未配置 API 时调用 LLM 元数据建议改为文字引导**：`MainWindow.action_llm_suggest_for_project`（右键菜单入口）和 `_launch_llm_from_dialog`（项目编辑对话框 ✨ 入口）都加了入口预检 `_llm_check_configured_or_prompt`：若默认 provider 不存在 / `api_key` 为空，弹 `QMessageBox.information` 提示"尚未配置 LLM API Key…请打开「设置 → API」页填入"。**不主动跳转**到设置页（按用户偏好：避免帮用户决定下一步）。
  - **新建项目对话框去掉 ✨ LLM 建议按钮**：`ProjectDialog` 引入 `_is_new_project = (project.id is None)` 标志位；新建模式下整块顶部操作条（✨ LLM 建议 / ✓ 全部接受 / ✗ 全部驳回）不构造，三个按钮引用置 `None`，`_update_bulk_buttons` 加空守护。表单底部加一行 hint："💡 项目创建完成后，可在项目列表右键「✨ LLM 元数据建议…」或在编辑对话框点 ✨ 让 LLM 帮你补全字段"。`MainWindow.action_new_project` 顺手删掉了原来兜底"请先保存项目再发起 LLM 建议"的 connect（信号永远不会触发）。
  - **项目编辑里的 date 字段默认空 + 支持清空**（B1）：旧实现用 `QDateEdit` 强制有值，新建时默认今天，无法表达"未填"。新增 `_DateEditor`（`QFrame`）：`QLineEdit`（占位 `yyyy-MM-dd（留空表示未填）`）+ 📅 按钮弹 `QCalendarWidget` 选日期 + ✕ 按钮一键清空。`_read_editor` 在保存阶段做轻量校验：空字符串保留为空，非法字符串也保存为空（避免库里出现 `"abc"` 脏值）。数据层零改动（系统字段 `date` 列允许空字符串，optional 字段 `date` 也是字符串类型）。
- **深色模式适配收尾（B4）**：
  - **`FirstRunBanner` 跟随主题切换配色**：旧版 hardcoded 浅蓝底（`#e8f4fd`）+ 深蓝字（`#0d47a1`），深色窗口下"白方块挖洞"。改为 `_apply_palette_styles()` 按 `palette().window().lightness()` 选两套配色（浅色保留原配色；深色用低饱和深蓝底 `#1c2c44` + 高对比浅蓝字 `#9ec5fe`）。**注意**：本来还实现了 `changeEvent` 钩 `PaletteChange / StyleChange / ThemeChange` 让 banner 跟随运行时主题切换；但在 PySide6 6.11.1 + Python 3.14 下 `setStyleSheet` 自身会再触发 `StyleChange`，重入 `changeEvent` → 无限递归 → **进程 native abort，主窗口完全不出来**（任务栏无图标、无 traceback、stderr 空）。**已移除 `changeEvent` 钩子**：banner 配色只在构造时按 palette 选定一次；用户运行时切主题，banner 内部颜色不会立刻变（但全局 QSS 重 apply 时 palette 会变，靠 palette 继承也够看），换来不会启动崩。banner 内 hint label 不再写 `<span style='color:#1565c0'>`，改由外层 stylesheet 选择器 `#FirstRunBanner QLabel` 统一接管。
  - **新建库向导内的 hint 文字**：3 处 `<span style='color:#666'>...</span>` 全部改为 `setProperty("hint", True)`，由全局 `QLabel[hint="true"]` 选择器接管浅色 / 深色配色。其中描述页的"备忘 + LLM 上下文"长 hint 拆成 `intro`（普通色）+ `intro_hint`（hint 色）两个 label。
  - **设置对话框左右栏分隔线**：`QFrame.VLine` 之前 hardcoded `color:#373a40`，浅色模式下显得"黑棒"。改为不写 stylesheet、设 `setFrameShadow(QFrame.Sunken)`，由 palette 默认 frame 色接管。
  - **`QGroupBox` 终于有显式 stylesheet**（`app/ui/theme.py` 浅 / 深两套都补）：之前两套 QSS 都漏了 `QGroupBox`，Qt fusion 给的默认 panel 在深色窗口上叠加成"白条遮住组标题 + 内部 form label"的诡异效果（设置 → API 里每个 provider GroupBox 标签那一行都被遮住）。现在显式给 `QGroupBox` 透明背景 + palette 协调的边框 + `::title` 用窗口色背景悬浮，浅 / 深都干净。
- **启动期未捕获异常 crash logger**（`app/main.py::_install_crash_logger`）：装一个全局 `sys.excepthook`，把任何未被 except 抓住的异常追加到 `%APPDATA%/LLMCabinet/crash.log`（含时间戳 + traceback），并尝试弹 QMessageBox 提示用户。专治 PyInstaller GUI 子系统 / 双击 .pyw / IDE 静默吞 stderr 等"窗口悄悄消失"场景。注：仅捕 Python 层异常；Qt 内部 `qFatal` / abort / segfault 仍然不会触发本钩子（那种情况只能靠 Windows 事件查看器或 PyInstaller debug 模式抓）。

### Added
- **新建库 onboarding（task #15 T1/T2/T3）**：把"新建库"从一次性技术操作（选目录→起名→重启）升级为带入门引导的多步流程。
  - **T1 多页向导**（`app/ui/wizards/new_library_wizard.py`）：第 1 页选目录 + 名称、第 2 页库描述、第 3 页默认字段（必有字段标题/描述/标签都列出，描述/标签的「列表显示」可在向导里直接勾选；可选默认字段作者 / 日期 / 评分 / 来源每个独立配「列表显示」复选框）、第 4 页（仅当已有其它库时）「从其它库迁移 API 配置」（**不迁移 / 仅 LLM 配置 / 全部迁移** 三档单选放在源库选择器上方；源库选择器为近期库下拉 + 末项「📂 浏览其它库目录...」，浏览要求目录含 `.llm-cabinet` 标记，选中的非已知库目录会插入到下拉里）。每页底部不再有「跳过」按钮（「下一步」对空内容已直通，语义重复）；描述页提示从底部合并到顶部，明确写出"用于备忘 + 发给 LLM 参考，也可暂时留空"。**晚建语义**（D1）：1~4 页只收集表单数据，最后一步「创建库」按钮才一次性原子化建库（mark + connect + seed + 应用必有字段可见性 + 加可选字段 + 写描述 + **写 `default_view_mode="list"`**（新建库默认列表视图，与「列表显示」复选框心智一致；既有库全局 fallback 仍 `"grid"`）+ 迁移 API），任意页取消零副作用；中途出错自动 `rmtree(root)` 回滚。
  - **「库 → 从其它库导入 API 配置...」菜单同步统一**：原本是 `getOpenFileName(*.db)` 裸选 db 文件，现改为与新建库向导第 4 页一致的"近期库下拉 + 「📂 浏览其它库目录...」末项"对话框，浏览选目录而非 db 文件，识别同样依赖 `.llm-cabinet` 标记。两处入口"心智模型 = 选库目录"统一。
  - **T2 首次进入引导横幅**（`app/ui/first_run_banner.py`）：主窗口中央顶部显示一条 dismissible 横幅，引导用户跑库字段设计助手 / 加字段 / 拖资料；显示条件 = 没跑过助手 + 库里没项目 + 用户没加过非系统字段 + D4 一次性标志未置位。**D4 一次性标志** `library_first_run_dismissed`：用户做过任何"成长性动作"（跑过助手 / 加过用户字段 / 创建过第一个项目 / 主动点"不再显示"）→ 永久不再显示，避免"删光项目时横幅复活"的诡异体验。
  - **T3 首次启动 Welcome**（`app/ui/welcome_dialog.py`）：`cabinet.json` 不存在（=首次安装）时弹三档欢迎对话框 — 「在自定义位置新建库」（→ T1 向导）/「使用默认位置」（D5：完全不弹任何额外对话框，最快上手语义）/「打开已有的库目录」（必须含 `.llm-cabinet` 标记）；用户选「退出」直接 `return 0` 不进主窗口。
  - **D2 默认列可见性**（仅影响新建库）：`_seed_fields` 把描述 / 标签字段默认 `visible=0`（理由：多行文本占主列表空间；标签筛选已由左侧标签树承担）；标题强制 `visible=1`。既有库不变（`_seed_fields` 仅在 `fields` 表为空时插入）。
  - **D3 API 迁移两档**：`MIGRATE_KEYS_LLM_ONLY = ["llm_config"]` / `MIGRATE_KEYS_ALL = ["llm_config", "llm_default_provider", "llm_default_language", "wizard_max_rounds"]`；复用 task #08 的 `import_settings_from_other_db`。
  - 配套：`Repository.count_user_added_fields()`（用于 T2 横幅显示条件）；`SettingsDialog.set_active_category("字段")`（横幅"📋 设置 → 字段"按钮直接跳到字段页）；`MainWindow._on_user_action_dismiss_banner` 在三处生命周期点 hook D4 标志（助手成功应用 / 在设置页改字段 / `refresh_projects` 项目数 ≥ 1）。`app/db.py` 暴露 `OPTIONAL_DEFAULT_FIELDS` 常量（4 个可选默认字段，结构 `(name, type, key, default_visible)`）；`DEFAULT_FIELDS` 元组形状对齐为同结构（兼容性：外部无解构 `DEFAULT_FIELDS` 的代码，只在 `app/db.py` 内部使用）。
- **更多文档格式现场提取（task #07 T0 短期补丁）**：在 LLM 元数据建议中"参考文件"被勾选时，直接从结构化文档抽出可读正文，而不是只塞文件名给 LLM。**全部纯标准库实现，零新依赖**：
  - **Office 三件套现代格式**：`.docx`（已有）/ `.xlsx`+`.xlsm`（已有）/ **新增** `.pptx`+`.pptm`（按幻灯片号排序，自动附该幻灯片的备注）
  - **OpenDocument 三件套**：**新增** `.odt` / `.odp`（共享 `content.xml` + `<text:p>` 路径）/ `.ods`（按 `<table:table-row>` 拼成 `a | b | c` 行）
  - **EPUB**：**新增** 走 OPF spine 顺序读各章节 xhtml + 用 stdlib 剥 HTML 标签，HTML 实体（`&hellip;`/`&mdash;`/`&nbsp;` 等）自动还原
  - **HTML / RTF**：**新增** `.html`/`.htm`（剥 script/style/标签 + 折叠空白）、`.rtf`（处理 `\uN`/`\'XX` 转义、剥控制字与分组）；从 `PLAIN_TEXT_EXTS` 中移除避免标签噪声进入 prompt
  - 仍未实现（保留"仅文件名"占位）：老 OLE 二进制 `.doc` / `.xls` / `.ppt`、`.mobi`
- **LLM 元数据建议对话框新增「内容提取」列**（task #07 T0）：每个文件用 ✅ 文本提取 / 🖼 图像直传 / ⚠ 仅文件名 标识其是否能被提取内容；列底部提示"不可提取的文件勾选了也只能让 LLM 看到文件名"。新公开 API `app.llm.context.extraction_capability(path)` 与 `extraction_capability_label(path)` 给后续 UI / 自检共享使用。
- **库管理增强（task #14）**：新增主菜单 **「工具」**：
  - **🔍 检查库一致性...**：扫描所有文件物理位置；失效项报告含项目/文件名/存储模式/原始路径，三档处理（仅查看 / 标记为缺失 / 从项目移除）。被标记的文件在文件表里显示 ⚠ 图标
  - **📦 备份此库...**：把整个库目录打成 zip（自动 WAL checkpoint、记忆上次备份目录）
  - **📥 从备份恢复库...**：从 zip 解到空目录，确认后自动切换到新库
- **多项目库并存与切换（task #08）**：每个"库"是一个完整的目录（含 `cabinet.db` + `library/` + `.llm-cabinet` 标记）。新增主菜单 **「库」**：
  - 切换库... (Ctrl+Shift+O) / 新建库... (Ctrl+Shift+N) / 🏠 回到欢迎页... / 当前库信息... / 从其它库导入 API 配置...
  - 「最近打开」子菜单（默认 5 个），含「管理列表...」对话框：列表底部「🔀 切换到选中库」按钮（仅对非当前库 enable）+ 双击列表项 = 切换；右键菜单顶端也加「🔀 切换到此库」，原本的「从列表移除 / 删除整个库... / 改名...」保留
  - **「删除整个库...」加外来文件保护**：删除前 `scan_library_for_deletion(root)` 把库根目录顶层条目分成三组——"库自身"（`cabinet.db` / `cabinet.db-wal` / `cabinet.db-shm` / `library/` / `.llm-cabinet` / `cabinet.v*.bak`）/ "软件全局"（`cabinet.json` 及 `.bak.<ts>.json`，**任何模式下都保留**）/ "用户外来内容"。当存在外来内容时，在确认（1/2）之后插入第二段对话框列出这些条目，强制让用户在「🟢 保留这些文件，只删除库数据（推荐）」与「🔴 一并删除（含目录）」之间显式选择，避免 `rmtree(root)` 误删用户在库目录里放的笔记 / 备份。新公开 API：`app.cabinet.scan_library_for_deletion()` / `delete_library_owned_only()` / `delete_library_all()` / `LibraryDeleteScan`（含 `app_global` 字段）
  - 切换走应用重启（`os.execv`），稳定且简单
  - **「切换库」严格只切换、不创建**：选到非库目录（空目录或普通目录）直接拒绝，明确引导用户改用「新建库」走 task #15 多页向导。原本"选空目录 → 询问是否新建"的简化路径已移除（它会绕过 onboarding，留下没库描述 / 没字段配置 / 没默认视图的半残库）
  - 跨库全局配置存于 `%APPDATA%/LLMCabinet/cabinet.json`；损坏自动备份重建
  - 当前库 label 显示在标题栏；当前活动库的"删除 / 移除"菜单项**也允许操作**（删除当前库或从列表移除当前库 = 弹二次确认 → 关主窗口走 Welcome 兜底重新选择库，详见下方"启动期 Welcome 兜底"）。**默认库不再有任何特权**：可以删、可以从列表移除、可以被截断挤出最近列表
- **字段级 LLM 提示（task #11 T1/T2/T4）**：
  - 「设置 → 字段」每个字段加一列「LLM 提示」按钮，点击弹文本编辑器，自定义该字段在 LLM 建议时的格式说明（如"标题不超过 30 字"、"描述 200~400 字分段说明"）。留空 = 使用默认。单条最大 500 字超限自动截断
  - prompt 拼装时把每个字段的 `prompt_hint` 注入到 user prompt 中"字段格式要求"区段；「查看 Prompt」对话框可见拼接结果
  - 项目导出包 schema 升级到 `llm-cabinet/project-export@2`，`fields_snapshot` 携带 `prompt_hint`；导入端在"自动创建字段"路径下还原。`@1` 老包仍兼容（缺失字段视为空）
  - `Repository` 新增 `add_fields_batch(fields_data)` 事务化批量接口，为 #11 T3 库初始化向导预备
- **库字段设计助手（task #11 T3）**（曾用名：库初始化助手）：主菜单 **「工具 → 🪄 LLM 助手...」** 入口（设置 → 通用顶部也提供"打开 LLM 助手..."辅助按钮），可扩展的 `WizardPlugin` 框架（按 `category` 分组列出所有注册助手）：
  - 第一个助手：**库字段设计助手** — 用一段话描述这个库的目的与字段偏好（"和 LLM 一起设计"），LLM 基于现状给出**新增 / 修改 / 删除**的字段方案 → 逐条批准 / 驳回 → 一次性写入 `fields` 表
  - 直调 provider（**绕过 `LLMTaskQueue`**，前台交互不等后台任务），按 `LLMProvider.supports_json_mode` 静态路由 JSON 原生 / Prompt 强约束两路，统一过 `parse_and_validate`（自动剥 ```` ```json ``` ```` 包裹、JSON-in-text 兜底）
  - 顶部恒显 **轮数 X / N** + **累计 token 用量**（`累计 in / out（本轮 +x / +y）`）
  - 「**重新开始**」清空状态回场景页（token 累计保留，已花的就是花了）；「**在当前基础上调整**」弹补充输入，把"上次返回 + 用户编辑 + 用户补充"再问一轮；达上限禁用「调整」按钮
  - 默认轮数上限 5（设置 → 通用 `wizard_max_rounds` spinbox 1~20）
  - **冲突预检**：每条建议在加载时与现有 `fields` 比对，4 种状态各自不同处理 — ✅ 新字段（默认勾选 → 创建）/ 🔒 系统保护（强制不勾选）/ 🔁 同名同类型（仅在现有 hint 为空时写入 LLM 提示，非空跳过不覆盖）/ ⚠ 类型冲突（默认 `<原名>_v2` 改名输入框，用户改完才能勾选创建）
  - **事务化应用**：勾选项一次性走 `Repository.add_fields_batch()`，任一失败 → 整体 `ROLLBACK` + 弹窗指明原因，助手停留在预览页让用户继续修改
  - `LLMProvider.supports_json_mode` 类属性默认 `True`，未来加新 provider 时按实际 API 能力填即可
  - 命名约定：对外文案统一用「LLM 助手」；内部代码沿用 `WizardPlugin` / `wizards/` 命名，二者是同一物
  - **本次迭代（6 月 1 日下午）**进一步打磨：
    - **从"追加字段"重定位为"全量规划"**：标题 / 描述 / 标签 三个系统必有字段也纳入预览表格，新增 ⭐ `system_required` 状态 — 强制选中、不可改名、不可改类型，但 LLM 提示列可编辑，应用时走 `update_hint_only`；用户能一次性综合研判所有字段
    - **库级描述（`settings.library_description`）**：把"这个库是干嘛的"沉淀为库级属性。「库 → 当前库信息...」对话框新增多行编辑框；助手场景描述页旁附"当前库描述"输入框，发送给 LLM；LLM 输出顶层新增 `library_description` 字段，预览页可二次编辑；应用时一并写入 settings
    - **入口 API 状态预检**：助手引导页加 banner 显示当前默认 provider/model，未配置或缺 Key 时显示红字提示且「下一步」disabled
    - **「需要建议的字段」tooltip**：LLM 元数据建议对话框中每个字段的勾选框 tooltip 现在展示该字段在「设置 → 字段」里配置的 LLM 提示；未设置时给出明确说明
    - 预览页第 0 列勾选框列宽固定为 44 像素（之前 ResizeToContents 算出过窄）；改名输入框最小宽度 120 像素
    - "建议标签"语义改为参考性展示（仅显示，不会自动写入库），文案与 tooltip 解释清楚
  - **本次迭代二轮（6 月 1 日傍晚）**继续打磨：
    - **从"全量预览"升级为真正的"全量规划"**：现有所有字段（含未被 LLM 提及的）都进预览表格；新增 📝 `existing_user_field` 状态，默认勾选表示保留，**取消勾选 → 状态变红字"🗑 将删除"**，应用阶段会真正删除该字段。系统必有字段（标题/描述/标签）与 LLM 命中现有用户字段的状态保留不变
    - **可删除已有用户字段**：`Repository.apply_field_plan_batch(creates, updates_hint, deletes)` 替代原 `add_fields_batch`，三类操作走**单一事务**；任一失败整体 ROLLBACK；受保护字段（标题/描述/标签）拒绝删除并整体回滚；删除前弹二次确认对话框，列出受影响项目数
    - **现有字段一并发给 LLM**：`build_messages(current_fields=...)` 注入"当前库已存在的字段"清单，让 LLM 基于现状给修订建议（保留有用、补缺、避免重名）
    - **标签建议改为分类策略**：之前是"科幻 / 翻译"具体标签实例，现在是"按研究对象 / 按文件类型 / 按管理人 / 按时间 …"等**分类维度**，每个维度配 2~5 个示例标签。预览页用 2 列表格展示（分类维度 / 示例标签）。schema 对应改为 `tag_axes: list[{name, examples}]`，兼容旧 `default_tags_suggestion: list[str]`（自动转成单一"通用标签"轴）
    - **表格双击编辑体验**：字段名列用自定义 `_TallLineEditDelegate` 让 QLineEdit 撑满 cell；LLM 提示列改为**双击弹独立多行编辑对话框**（标题为该行字段名，初始 540×320 像素），不再用窄行内编辑器
  - **本次迭代三轮（6 月 1 日晚 · 改名定型）**最终交互打磨：
    - **正式定名为「库字段设计助手」**（旧称"库初始化助手"）：助手对外标题 / 卡片描述 / 引导页大标题 / 「库 → 当前库信息」对话框文案 / 设置 → 字段错误提示 / db.py 注释，全部统一更新；内部代码（`LibraryInitWizard` / `library_init.py` / `WizardPlugin` / `wizard_max_rounds`）维持不动，避免无谓的重命名扩散
    - **场景页：合并描述输入**：原"请描述使用场景"+"当前库的描述"两个文本框合并为单个语义统一的输入框（一句话回答"这个库是干什么的 / 你希望它怎么管理"），用户输入即作为 LLM 的库描述基线；`build_messages` 在 user_scenario 与 library_description 同源时去重，避免 prompt 复读
    - **场景页：内嵌已有字段编辑面板**：助手内可直接增 / 删 / 重命名 / 改类型 / 上下移 / 切换可见性 / 切换 LLM 建议 / 编辑 LLM 提示，行为与「设置 → 字段」完全一致（删除字段共享 `_DeleteFieldChoiceDialog`），用户可在调用 LLM 前先调整起点
    - **预览页：新增「LLM 建议」列**（最左侧）：对每条建议明示 `新增 / 修改 / 不变 / 删除` 四档标签（绿/蓝/灰/红着色），且仅当 LLM 实际带来变化（"新增" / "修改"）时附带「批准」/「驳回」按钮（46+ 像素文字按钮，列宽 216 像素）；底部增加「✓ 全部批准」一键键。`AnnotatedSuggestion` 新增 `llm_touched` / `decision` / `has_llm_change` / `llm_change_label` 派生属性；`action` 在 `decision='rejected'` 路径下退化为 `skip`（系统必有字段保持回写旧 hint 等价 noop）
    - **预览页：移除字段勾选列**，改用顶部行操作按钮：`＋ 添加字段`（在末尾追加一条空白 new）/ `🗑 删除`（new、type_conflict 直接从列表移除；existing_user_field、same_type 标为"将删除"；system_required 拒绝）/ `↑ 上移` / `↓ 下移`（系统必有字段固定在最前，不允许跨越）。"保留 / 删除"语义现在统一由 LLM 建议列的批准/驳回与行操作按钮控制
    - **场景页：文本框高度限制**为 130 像素，把垂直空间留给下方的字段编辑表（之前 stretch 比例不当导致顶部输入框占用过多空间）
    - **批准 / 驳回改为「立即生效」语义**：原来的"批准/驳回只是标记，应用时才结算"语义不够清晰；现在点「批准」立刻把 LLM 给的 type / hint 固化到该 ann，点「驳回」立刻把 ann 还原到 LLM 提建议**之前**的状态（`new` 直接从列表移除；`same_type` / `system_required` 把 hint 还原回库里旧值；`type_conflict` 清掉 rename_to 并恢复 selected=False）。LLM 建议列只显示用户决策结果（已批准 / 已驳回 / 已删除 / 空），不再展示"新增 / 修改 / 不变"四档。一旦做出决策按钮即消失，避免反复操作；想要回到 LLM 原版改去「查看 LLM 原始响应」弹窗里走「再次应用」
    - **「查看 LLM 原始响应」改为弹出对话框**（720×480）：原来嵌在预览页底部的折叠区被替换为按钮，节省垂直空间；弹窗内：标题"LLM 原始响应（本轮）" + 等宽字体只读文本框 + 底部「🔄 再次应用 LLM 建议」按钮
    - **「再次应用 LLM 建议」智能合并**：保存了 LLM 那一轮的 payload + "当时的现有字段"快照（`_llm_round_payload` / `_llm_round_existing`）；点击该按钮会用这俩重新跑 `annotate_conflicts` 得到"如果按 LLM 走应该是什么样"，然后**保留**用户手加的字段（`llm_touched=False` 的 new）与用户标记删除的现有字段（`existing_user_field` + `selected=False`），仅 LLM 触达过的条目被重置；遇到**重名冲突**（用户手加 vs LLM 建议同名）会弹 Yes/No 对话框让用户挑保留哪个版本
    - 配套清理：`AnnotatedSuggestion.action` 移除已废弃的 `decision='rejected'` 退化分支（驳回现在在 UI 层立即改 ann 内容，action 只看 status + selected + 当前 hint/type）；同时 `system_required` 路径加入 hint 未变化时跳过的判定（避免无意义 noop 写入）
    - **去掉 prompt 中"可选系统字段"老遗产**：`_SYSTEM_PROMPT` 第 1 条原本写"可选系统字段：作者 / 日期 / 评分 / 来源 + 不要省略"，导致 LLM 把这 4 个通用字段硬塞到任何场景里（即便管理菜谱、代码片段也会出现"作者"）。现在改为"必有 3 个固定字段（标题 / 描述 / 标签）；其它字段按场景自由设计，不要凭空添加'作者 / 日期 / 评分 / 来源'之类的通用字段——除非用户描述的场景真的需要"；总数从 5~12 收紧到 5~10；type 白名单去掉 `tags`（仅「标签」字段固定用 tags，其它字段不允许）。维度名引导加上"不要直接照抄『领域 / 类型 / 状态』，按场景定"
- **库字段设计助手后续打磨（task #16）**：
  - **场景页提示**：库描述输入框上方提示文案补一句"如果是基于当前字段结构进一步修改，可以把修改意见追加在库描述的最后，它会一起发送给 LLM 进行处理"，引导用户在第二轮调用助手时知道怎么补充诉求
  - **库描述批准 / 驳回**：预览页"库描述"区域加一对按钮（仅在 LLM 这一轮**实际改过**描述、即 `_library_desc_input != _library_desc_suggested` 时显示）。批准 → 把当前编辑框内容固化；驳回 → 把描述还原为用户在场景页的原始输入；用户后续手改编辑框 → 决策回到 pending。每一轮 LLM 完成后 / 「再次应用」时都重置为 pending
  - **LLM 显式删除建议**：prompt 新增 `fields_to_delete: [{name, reason}]` 字段，让 LLM 主动列出"建议删除的现有字段 + 一句话理由"；`AnnotatedSuggestion.status` 新增 `"llm_suggest_delete"` 枚举值（默认 `selected=False` 待批准；批准 → 走 delete 路径；驳回 → 退化为普通 `existing_user_field` 保留）。UI 状态列红字"🗑 LLM 建议删除"，hover 看 LLM 给的删除理由；批准 / 驳回按钮工作；行操作"删除"对该状态语义为"批准 LLM 建议"（而非"标记将删除"）。`apply_field_plan_batch` 二次确认对话框按删除来源（用户主动 / 批准 LLM）分组显示。**完全兼容**：旧 LLM 输出无 `fields_to_delete` 时走老路径（未提及现有字段标 `existing_user_field`，行为不变）。`parse_and_validate` 容错：必有字段名（标题/描述/标签）即使误入 `fields_to_delete` 也忽略；缺 reason 给占位文字；非法条目产生 warning
    - **标签分类策略与层级标签打通**（task #06 ↔ #11 衔接）：
      - 库字段设计助手的标签字段 prompt 改为要求 LLM 用 **markdown 列表 + `维度/标签` 层级形式**给分类策略（如 `领域/科幻`、`类型/论文`、`状态/已读`），与左侧标签树按 `/` 折叠分组的规则保持一致；范例长度从 100~250 字放宽到 150~300 字以容纳结构化列表
      - LLM 元数据建议端（`app/llm/prompts.py` + `app/llm/context.py`）同步配套：tags 字段在 user prompt 中的类型说明示例从 `["科幻", "翻译"]` 升级为 `["领域/科幻", "类型/论文"]`；tags 字段的 `prompt_hint` 注入时标签从"格式要求"换成"分类策略（风格指南）"，并在 system prompt 中明确说明"这是风格指南而不是字面要求 — 挑 1~3 个最相关维度，每维 1~2 个标签，可直接用示例也可同风格新造"。原有"扁平标签"在用户没规划过分类策略时仍兼容
    - **彻底删除独立的"标签分类策略"展示区**：原预览页底部那张"分类维度 / 示例标签"二级表移除；改由 system prompt 引导 LLM 把分类策略**直接写进「标签」字段的 prompt_hint**，避免用户感觉"建议给了但不知道哪里去了"。`parse_and_validate` 仍然容忍 LLM 误返 `tag_axes` / `default_tags_suggestion`：静默丢弃 + 给出 warning 提醒 LLM 应该把这些写到标签 hint 里
    - **行为微调**：`_on_restart` 在切回场景页时刷新一次现有字段表（用户上一轮可能在助手里"应用"过修改），保持视觉一致；预览页双击 LLM 提示列后若用户编辑过 hint 且该行之前是 `rejected`，自动复位到 `pending` 让用户重新决策
    - **「应用」按钮文案明示默认语义**：原按钮"✅ 应用到库"改为"✅ 应用（未决策按默认处理）"，并补 tooltip 说明 — 未点批准/驳回的 LLM 新增/修改建议会被默认接受、LLM 删除建议则需显式批准才执行；预览页顶部 hint 同步加一行灰字提示，避免用户漏看 tooltip 后误以为"未决策 = 不会改库"
    - **批量删除二次确认对话框升级**：原来一律走"丢数据"，现在仿照「设置 → 字段」的 `_DeleteFieldChoiceDialog`，对**每个有填充率 > 0 的待删字段**都给一对单选 — "直接删除该字段及其数据" / "保留数据：把每个项目的该字段值追加到「描述」末尾再删除"（无填充数据的字段只列名字不出现选项），用一次集中弹窗完成全部决策，不打断用户的标记 / 反悔流程。仓储层 `Repository.apply_field_plan_batch` 新增 `append_for_fids: Optional[Iterable[int]]` 关键字参数（默认 None = 旧"直接丢"行为，向后兼容）；与 `Repository.delete_field(append_to_description=True)` 共用 description 追加格式 `\n\n**字段名**：值`
    - **LLM 显式改名建议（task #16 补充）**：原本 LLM 想把"出版社"改成"出版商"只能 `fields_to_delete + fields` 等价模拟，会**丢失该字段在所有项目里的历史值**。现在 prompt schema 新增 `fields_to_rename: [{old_name, new_name, reason}]` 接口，让 LLM 显式声明改名意图；`Repository.apply_field_plan_batch` 新增 `renames: list[(fid, new_name)]` 关键字参数，事务化 `UPDATE fields.name`，**保留 fid → 项目历史值零损失**。返回值由 `(new_ids, n_deleted)` 升级为 `(new_ids, n_deleted, n_renamed)`（**breaking** — 内部唯一调用点 `_on_apply` 已同步）。`AnnotatedSuggestion.status` 新增 `"llm_suggest_rename"` 枚举值（默认 `selected=False` 待批准；批准 → 走 rename 路径；驳回 → 退化为 `existing_user_field`；行操作"删除" → 直接转 `existing_user_field` + 标记删除）。UI 状态列蓝字"✎ LLM 建议改名 → 新名"（hover 看 reason），批准后改为"✎ 将改名 → 新名（已批准）"。冲突解决：同一现有字段名同时出现在 `fields`（保留）/ `fields_to_rename`（改名）/ `fields_to_delete`（删除）的优先级为 `fields > rename > delete`；`new_name` 与现有其它字段重名 / 撞必有字段（标题/描述/标签）→ parse 层警告丢弃，annotate 再防御一道。**与 fields 数组合并**：因为 prompt 要求 `fields` 是改名后的完整方案，LLM 通常会同时把 `new_name` 写进 `fields`；`annotate_conflicts` 会把这一行**合并**到 rename ann（沿用 fields 给的 prompt_hint），不再额外产出一行 `new`，避免出现"一行表示新增 B、一行表示 A 改为 B"的视觉重复
    - **`same_type` 行点删除按钮静默无效 bug 修复**：原来"现有 · 同类型"行点行操作"删除"时只在 ann 上设 `selected=False` 但状态列文字不变，且 `action` 属性根本不依赖 `selected` 走 delete 路径；现在 `action` 在 `same_type + selected=False` 时返回 `"delete"`、状态列文字与 `existing_user_field` 取消保留共享同一套红字"🗑 将删除"渲染规则
- **标签层级折叠（task #06）**：约定 `/` 为标签层级分隔符（如 `领域/科幻`、`领域/工具书`），左侧标签树自动按第一段做前缀分组，父节点可折叠/展开。点击父节点 = 同时筛选父标签自身 + 该前缀下所有子标签的项目。折叠状态持久化在 `settings.tag_tree_collapsed_prefixes`。零数据迁移。
- **批量文件夹导入（task #10）**：把多个文件夹拖到底部 DropZone，先选「单/多项目」模式；
  选「分别建立」时弹出批量导入对话框，可：
  - 识别文件夹根目录下的 `project.json`（task #09 导出物）并恢复元数据 / 字段值 / 标签
  - 选择文件存储模式（🔗 链接 / 📦 复制到仓储）和标题来源（project.json / 文件夹名）
  - 对**库内不存在的字段**选择处理策略：自动创建 / 追加到描述（默认）/ 忽略；
    可勾选"应用到本次所有项目"批量决策，否则逐项目询问
  - 兼容**未来版本**生成的 `project.json`（schema `@N` 大于本机已知最高版本时仍尝试恢复核心字段，
    并在状态列标注"更新版本生成"）
- 标签自动创建：导入项目时碰到库内不存在的标签会**直接创建**（沿用 Repository 现有行为）。

### Changed
- ⚠️ **「默认库」概念彻底取消 + 启动期 Welcome 兜底**：`%APPDATA%/LLMCabinet/` 不再被视作"自动登记的默认库"，仅作为软件全局配置（`cabinet.json` 及其备份）的存放点。具体行为：
  - **空配置** = 空配置：`CabinetConfig._default()` 不再自动塞一条"(默认库)"到 `recent_libraries`；`active_library = None`，`recent_libraries = []`。第一次安装、`cabinet.json` 损坏后回退、用户在主界面把所有库都"删除整个库"了——这三种情况下都进入空配置
  - **启动期 Welcome 兜底**（`app/main.py:_resolve_active_library_root`）：active 不可用 + 没有任何可降级的有效 recent → 弹 Welcome 让用户重新选；上次 active 失效但有具体路径时 Welcome 顶部显示「⚠ 上次打开的库已不可用：&lt;path&gt;」红字提示
  - **Welcome 选项重设**：移除「使用默认位置」选项（默认位置不再有意义）；新增「打开最近使用的库」列表（仅当 `cabinet.recent_libraries` 非空时显示，列出已知库 + 失效项灰显）；新增「📥 从备份 zip 恢复库...」入口（与「工具 → 📥 从备份恢复库...」共享 `app.library_check.restore_library`，但完成后直接走 `RESULT_OPEN_EXISTING` 进主窗口，无需重启切换）；保留「新建库...」「打开其它已有库目录...」「退出」。**视觉**：顶部品牌区显著放大 — 应用图标 128×128 居中、标题 36pt 加粗、副标题 13pt，整体约占对话框 40% 高度（`brand_box.setMinimumHeight(280)` + 父布局 stretch=2）；不再写"这是你第一次使用"等不准确的引导语 — 仅在 `stale_active != None` 时显示红字「⚠ 上次打开的库已不可用：&lt;path&gt;」。「📁 新建库」/「🔍 打开其它已有库目录」/「📥 从备份恢复库」改为**单行按钮**（高 48px、内边距 12×20px），文字直接说明动作；「📂 打开最近使用的库」用 `QFrame{StyledPanel}` 包成区块，**每个库占一行**展示「名字 — 路径」（`uniformItemSizes(True)`、行高 26px、最多 5 行可见），路径完整内容用 tooltip 兜底，区块整体高度按条数自适应。**修复 Welcome 出现在 `apply_theme()` 之前导致灰底**：`main()` 启动时立刻 `apply_theme(app, "light")` 作为兜底（之前要等到打开库读到 `settings.theme` 后才 apply，Welcome 弹出时 stylesheet 还没加载，整个对话框就只能显示 fusion 默认灰色背景，与后续主窗口的白底观感断裂）；打开库后会再次 apply 一次，dark 主题用户的偏好不丢。**关掉所有按钮的焦点环 + auto-default**（`setFocusPolicy(Qt.NoFocus)` + `setAutoDefault(False)`），避免任一项被 Qt 自动画上"默认按钮"蓝边。**左下角加「ℹ 关于」按钮**：弹独立的 `app/ui/about_dialog.py:AboutDialog`（与「设置 → 关于」内容一致但不依赖已打开的库 / Repository，可在 Welcome 期间调用）
  - **主菜单「库」新增「🏠 回到欢迎页...」**：用户可主动关闭当前库回到 Welcome（弹二次确认 → `_pending_switch_to = "__welcome__"` + 关主窗口 → main 重启走 Welcome）；当前库不删，仍在最近列表里可重新打开。**重启时会附加 `--welcome` 命令行参数**，让 main 强制走 Welcome 而不是从 recent 自动降级回原库（recent 头部就是用户刚离开的库，不加 `--welcome` 的话 `_resolve_active_library_root` 会把它选回来，"回到欢迎页"就没生效）。`--welcome` 是一次性的，下次重启会被自动剥掉
  - **默认库特权全砍**：`cabinet.remove(default)` 不再硬阻挡；`_trim_recents()` 不再强制保留默认库；管理列表对话框里默认库的"从列表移除 / 删除整个库"不再 disable
  - **当前库也允许删 / 移除**：在管理列表对话框里对当前库点"从列表移除"或"删除整个库..."会弹二次确认，确认后通过新的哨兵 `_pending_switch_to = "__welcome__"` + 关主窗口 → main 检测后 restart 走 Welcome 让用户重新选。**删之前先释放 sqlite 句柄**：`MainWindow._release_active_db_resources()` 会 `llm_queue.stop(join_timeout=2.0)` + `repo.conn.close()`，避免 Windows 下"另一个进程正在使用此文件"导致 `cabinet.db` / `-wal` / `-shm` 删不掉。`LLMTaskQueue.stop` 新增 `join_timeout` 参数等 worker 真正退出
  - **`cabinet.json` 在删除整个库时永远保留**：当库根目录恰好包含软件全局文件时，`delete_library_owned_only` / `delete_library_all` 都会跳过 `app_global` 这一类，目录本体也保留（避免 rmtree 连带 cabinet.json）；同时 `LibraryDeleteScan` 新增 `app_global` / `app_global_size` 字段，UI 里的"非库内容"清单也不会让 `cabinet.json` 出现造成困惑
  - **修复**（被本次重构吸收）：原"二级菜单点默认库提示不是有效库"+ "管理列表里切换默认库无声回滚"两 bug 自然消失——默认库不再被自动登记进 recent，不存在那个误导性条目；切换路径已统一走 `_lib_open_recent` 校验
- **新建库默认字段精简**：新建库时只创建 **标题 / 标签 / 描述** 三个保护字段（之前会一并创建作者/日期/评分/来源 7 个）。`projects` 表的 `author`/`date`/`source_url`/`rating` 列保留作为系统字段值的存放后端，用户可在「设置 → 字段」用相同名字重新加，或运行「LLM 助手 → 库字段设计助手」按场景规划。**已有库不受影响**（`_seed_fields` 仅在 `fields` 表为空时插入）。未来「新建库」流程会加一个对话框让用户选择是否一并创建这 4 个常见字段（task #15 / TODO）。
- 拖到 DropZone 的对象**全是目录且 ≥ 2 个**时，行为由"全部并入一个新项目"改为
  先弹模式选择对话框（默认"分别建立"）；旧的合并行为通过对话框中的「合并为同一项目」保留。
  单个目录与含散文件的拖入行为不变。

### Fixed
- `app/utils.py` 中 `human_size()` 重复定义了两次（前者支持 `int|float` 与浮点格式化，后者只 `int` 且会修改入参）。删除后者，仅保留前者。

### Deprecated
-

### Removed
-

---

## [0.2.0] - 2026-05-31

📦 schema v1 → v2 — 仅 `DROP TABLE IF EXISTS custom_fields`，不影响任何有效数据。

⚠️ **BREAKING**：应用数据目录与默认数据库文件名变更。详见下方 Changed 段。

### Added
- **项目导出（基础版，task #09）**：工具栏 `📤 导出项目` + 项目右键菜单
  `📤 导出项目…` 入口；导出对话框含路径选择器与"复制链接模式（🔗）原始文件"
  开关；产物为目录形式（`project.json` / `files.json` / `README.md` / `files/`），
  含字段定义 snapshot 与应用/schema 版本号，作为未来导入功能的标准结构。
- 数据库迁移注册表首次启用：新增 `_migrate_v1_to_v2`，删除 v0.1.0 前残留
  的空 `custom_fields` 表。打开旧 v1 库会自动生成 `cabinet.v1.<时间戳>.bak`
  备份后再迁移。
- 关于页新增"免责声明"行；`README` 顶部"注意"段、`PRIVACY` 末尾新增「7. 免责声明」。
- `PRIVACY` 新增「3.A 关于导出项目功能」小节，描述导出物的结构与敏感性提示。

### Changed
- ⚠️ **BREAKING**：应用数据目录由 `%APPDATA%/Fileman/` 改为 `%APPDATA%/LLMCabinet/`，
  默认数据库文件名由 `fileman.db` 改为 `cabinet.db`，自动备份命名相应改为
  `cabinet.vN.<时间戳>.bak`。环境变量 `FILEMAN_DND_DEBUG` 改名为 `LLMCABINET_DND_DEBUG`。
  **不再保留向旧路径的兜底**——升级后应用启动时若新路径不存在数据将视为全新库。
  **手动迁移步骤**（仅 v0.1.0 用户需要）：
  1. 关闭应用
  2. 把 `%APPDATA%/Fileman/` 整个目录改名为 `%APPDATA%/LLMCabinet/`
  3. 进入该目录，把 `fileman.db` 改名为 `cabinet.db`
  4. 自动备份文件（如 `fileman.v1.<时间戳>.bak`）若需保留，可手动改名前缀为 `cabinet.`
- **默认主题改为浅色**（Light）。已有用户存过 `theme` 设置不受影响；
  设置页下拉选项顺序调整为「浅色 / 深色」。
- LLM 元数据建议对话框：明确提示"无论是否勾选参考文件，**所有文件名**都会作为
  项目结构上下文发送给 LLM"。`PRIVACY` 相应段落（§2.2 / §5）同步强调。
- 工具栏简化：移除冗余的「▶ 打开 / 📂 在资源管理器中显示」两个按钮（文件区底部
  按钮、文件双击、文件右键菜单仍可访问相同功能）；`Ctrl+Return` 快捷键随之移除。
- 应用图标统一改用 `icon.ico`（多分辨率 16/32/48/64/128/256，32-bit RGBA），
  CI 构建与本地打包命令一并切换。新增 `run.py` 作为 PyInstaller 顶层入口，
  规避 `app/main.py` 相对导入在 frozen 模式下的 `ImportError`。

### Fixed
- 关于页应用图标在多分辨率 ico 下变模糊：改用 `QIcon.pixmap(target_size)`
  让 Qt 从 ico 容器挑/合成最合适尺寸的子图；同时按 `devicePixelRatio` 适配高 DPI。
- 文件表表头最后一列右侧的空白区在深色主题下显示为白色：新增 `QHeaderView`
  顶层 `background` 规则（浅色主题同步修复）。
- 工具栏 `📤 导出项目` / 右键菜单 `✨ LLM 元数据建议…` 点击无反应：
  `QAction.triggered` 会传 `bool(checked)` 实参，而 Python 中 `bool` 是 `int` 的
  子类（`isinstance(False, int) == True`），导致 `False` 被当作合法 `pid` 进入
  `repo.get_project(False)`。修法：在判 `int` 之前先排除 `bool`。

### Removed
- 移除针对 v0.1.0 之前未发布 schema 的兼容兜底：`custom_fields` 旧表定义、
  `_migrate_custom_fields`、`_migrate_add_columns`、`_backfill_system_field_keys`
  中"空 key 回填"逻辑、以及 `_run_migrations` 中 `user_version=0` 但非 fresh 库
  的兜底分支。保留的"保护字段（title/description/tags）自愈"逻辑迁入新函数
  `_ensure_protected_fields`。后续 schema 变更一律走 `MIGRATIONS` 注册表。
- 移除 `app/ui/theme.py` 末尾的死代码 `QSS = QSS_DARK`。

---

## [0.1.0] - 2026-05-31

初始版本。

- 项目化文件管理（卡片墙 / 列表两种视图）
- 字段系统（系统字段 + 用户自定义字段，可改顺序、可见性、类型）
- 标签筛选（左栏树）
- 文件预览（图片 / 视频 / PDF 内嵌；其它调用系统默认）
- 拖放新建项目 / 加入项目
- LLM 元数据助手（DeepSeek / OpenAI / Gemini / Grok）
- 文件级存储方式（🔗 链接 / 📦 仓储），可同项目混合
- 数据库 schema v1

📦 schema v1 — 初始 schema，无需迁移。

[Unreleased]: https://github.com/vortexer99/llm-cabinet/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.2.0
[0.1.0]: https://github.com/vortexer99/llm-cabinet/releases/tag/v0.1.0
