# tasks/

按"可以一起做的组合"切分的可执行任务卡。每个文件代表一个 PR 级别的工作包。

> 📂 **文件处理相关任务**（#01/#02/#04/#09/#10/#17/#28/#29/#30/#31a/#31b/#32）已按依赖与执行顺序梳理成导览：见 [`docs/file-handling.md`](../docs/file-handling.md)。

| 文件 | 标题 | 工作量 | 优先级 | 状态 |
|---|---|---|---|---|
| [01-files-table-customization.md](./01-files-table-customization.md) | 文件表列可见性 + 列宽自定义 + 存储方式列 | S+XS | P1 | ✅ 2026-05-31 |
| [02-files-table-detach-window.md](./02-files-table-detach-window.md) | 文件列表独立窗口 | S | P2 | 待做 |
| [03-search-calibre-like.md](./03-search-calibre-like.md) | 类 Calibre 的搜索（关键词 + 字段 + 布尔 + 搜索历史 + 收藏表达式）；已拆 Phase A (XS~S) + Phase B (M) + Phase C (XS) | M | P0 | 待做（Phase A 可先开工） |
| [04-project-system-files-folding.md](./04-project-system-files-folding.md) | 项目内系统/衍生文件折叠（二态视图：仅用户文件 / 显示所有，消费 #30 origin） | S | P1 | 待做（澄清完毕） |
| [05-data-paths-migration-and-export.md](./05-data-paths-migration-and-export.md) | 数据路径自定义/迁移 + 项目导入导出 | M+S | P1 | ✅ 已闭环（被 #08/#09/#10 覆盖，归档保留） |
| [06-tags-hierarchy-folding.md](./06-tags-hierarchy-folding.md) | 标签层级分类折叠 | S | P2 | ✅ 2026-06-01 |
| [07-local-embedding-summary.md](./07-local-embedding-summary.md) | 文件级摘要（手动导入 + 本地预处理流水线） | M+L | P1/P2 | 远期（T1/T2 可较早启动） |
| [08-multiple-libraries-switch.md](./08-multiple-libraries-switch.md) | 多项目库并存与切换（Calibre 风格） | M | P1 | ✅ 2026-06-01 |
| [09-project-export-basic.md](./09-project-export-basic.md) | 项目导出（基础版，T7 最小子集） | S | P1 | ✅ 2026-05-31 |
| [10-folder-batch-import.md](./10-folder-batch-import.md) | 文件夹批量导入 + project.json 识别 | S+S | P1 | ✅ 2026-06-01 |
| [11-field-prompt-and-library-wizard.md](./11-field-prompt-and-library-wizard.md) | 字段级 prompt 模板 + 库字段设计助手（含可扩展向导框架） | M | P1/P2 | ✅ 2026-06-02（T1/T2/T3 完工） |
| [12-selftest-infrastructure.md](./12-selftest-infrastructure.md) | 端到端自检体系（基础设施，与功能并行增量） | M | P1 | 🚧 进行中 |
| [13-mcp-server.md](./13-mcp-server.md) | MCP Server（AI 文件中枢接口；独立进程 stdio + 多库感知） | M+S+M | P1/P2 | ✅ 2026-06-03 |
| [14-library-management-enhancements.md](./14-library-management-enhancements.md) | 库管理增强（一致性检查 + 备份恢复 + 搬家指引） | S+S+XS | P1/P2 | ✅ 2026-06-01 |
| [15-new-library-onboarding.md](./15-new-library-onboarding.md) | 新建库的用户指引完善（多步向导 含库描述/默认字段勾选/默认列可见性/从其它库迁移 API + 首次进入横幅 + Welcome 对话框 + 模板系统） | S+S+S+M | P1/P1/P2/P3 | T1/T2/T3 ✅ 2026-06-02 · T4 远期 |
| [16-library-field-wizard-polish.md](./16-library-field-wizard-polish.md) | 库字段设计助手后续打磨（描述追加修改意见提示 + 库描述批准驳回 + LLM 显式删除建议） | XS+S+S | P2/P1 | ✅ 2026-06-01 |
| [17-subfolder-import-and-tree-view.md](./17-subfolder-import-and-tree-view.md) | 子文件夹导入修复 + 文件表改树形（数据库驱动 subfolder，与物理解耦） | M+M+S | P0/P1/P1 | ✅ 2026-06-10 |
| [19-field-type-change-safety.md](./19-field-type-change-safety.md) | 字段类型变更的安全护栏：库设置弹窗确认 + supersede pending + 字段助手 type_conflict 改为「批准=原地改 / 驳回=不动」 | S+S | P1 | ✅ 2026-06-02 |
| [20-unify-field-storage.md](./20-unify-field-storage.md) | 废弃系统字段的 projects 列分流，所有非保护字段值统一存 `project_field_values`（📦 schema v3 → v4）；顺带砍掉字段助手里残留的「🔒 系统字段」状态 | M | P2 | ✅ 2026-06-03 |
| [21-wizard-two-step-redesign.md](./21-wizard-two-step-redesign.md) | 字段助手两段式重构（Step 1 审阅 LLM 建议 / Step 2 编辑字段表），消除"一张表两种语义"的矩阵规则与隐式状态迁移 | M | P1 | ✅ 2026-06-03 |
| [22-wizard-status-column-redesign.md](./22-wizard-status-column-redesign.md) | 字段助手 Step 1/Step 2 列语义重组（"LLM 建议"列改成普通用户能读懂的"新增/删除/修改 (字段名,类型,提示)"动作描述；新增 `llm_pending_type_change` 让"改名+改类型"被吞场景可见；Step 2 删冗余状态列） | S+S | P2 | ✅ 2026-06-03 |
| [23-mcp-tools-consolidation.md](./23-mcp-tools-consolidation.md) | MCP 工具收敛（17 → 5 个聚合工具：`query_projects` / `manage_project` / `manage_files` / `manage_libraries` / `export_project`）；下线 AI 套 AI 的 `trigger_llm_suggestion` / `apply_suggestion` / `list_pending_suggestions` 与冗余的 `import_folder`；修正 `switch_library` 过时描述 | S+XS | P1 | ✅ 2026-06-04 |
| [24-mcp-audit-log-viewer.md](./24-mcp-audit-log-viewer.md) | MCP 操作记录查看面板：状态栏入口 + 双 Tab 对话框（审计日志 + MCP 修改项目）+ schema v5→v6 + 侧边栏 MCP 筛选 | S | P1 | ✅ 2026-06-04 |
| [25-project-list-batch-and-dnd.md](./25-project-list-batch-and-dnd.md) | 项目列表多选 + 批量操作适配 + 标签拖放赋值 | S+M | P1 | 待做 |
| [26-file-mode-library.md](./26-file-mode-library.md) | 文件模式库：每个文件 = 一个项目，隐藏文件表，标签做交叉分组 | S+S+S | P1 | 待做 |
| [27-provenance-source-link.md](./27-provenance-source-link.md) | Provenance 来源链：MCP 写入可附带 source（依据文件 + 备注），UI 显示「来源」入口 | S | P1 | 待做 |
| [28-export-structure-option.md](./28-export-structure-option.md) | 导出/导入闭环（扩展导出选项 + 批量导出 + 导入增强） | L | P1 | 待做（依赖 #17 #09 #10 #30；软依赖 #25） |
| [29-file-storage-location-management.md](./29-file-storage-location-management.md) | 文件存储位置管理（T1 链接转仓储 / T2 移动 / T3a 重关联 / T3b 替换链接 / T3c 文件夹粒度） | S+S+S+S | P2 | 待做（从 #28 拆出；相邻 #14；协同 #32） |
| [30-file-origin-marker.md](./30-file-origin-marker.md) | 文件来源标记 `files.origin`（user/generated，区分软件衍生物如封面快照）；📦 schema v7→v8 | S | P1 | 待做（服务于 #28；相邻 #04） |
| [31-files-table-sort-and-reorder.md](./31-files-table-sort-and-reorder.md) | （已拆分为 #31a / #31b，保留为指针） | — | — | 已拆分 |
| [31a-files-tree-interactions.md](./31a-files-tree-interactions.md) | 树形视图：拖动改位置 + 同级排序 + 新建空文件夹 + F2 重命名（label/物理名/subfolder） | M+ | P1 | 待做（依赖 #17；联动 #02；协同 #32） |
| [31b-files-table-flat-view.md](./31b-files-table-flat-view.md) | 扁平视图模式 + 大小/添加时间列 + 列任意排序（Qt 原生 setSortingEnabled） | M | P1 | 待做（依赖 #17；协同 #31a） |
| [32-cross-project-file-reference.md](./32-cross-project-file-reference.md) | 跨项目链接引用（最小方案：路径共享，多引用警告 + #14 跨项目引用报告）；零 schema 改动 | S+S | P2 | 待做（依赖 #17 + #14 + #29） |

## 约定

### 工作量
- **XS** ≤ 30 分钟
- **S** 半天内
- **M** 1~2 天
- **L** > 2 天

### 优先级
- **P0** 影响日常使用
- **P1** 体验明显改善
- **P2** 锦上添花 / 远期

### 任务卡格式
每个任务文件包含：来源、目标、约束、实现要点、依赖/风险、状态/完成时间。
