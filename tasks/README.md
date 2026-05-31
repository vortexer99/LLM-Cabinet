# tasks/

按"可以一起做的组合"切分的可执行任务卡。每个文件代表一个 PR 级别的工作包。

| 文件 | 标题 | 工作量 | 优先级 | 状态 |
|---|---|---|---|---|
| [01-files-table-customization.md](./01-files-table-customization.md) | 文件表列可见性 + 列宽自定义 + 存储方式列 | S+XS | P1 | ✅ 2026-05-31 |
| [02-files-table-detach-window.md](./02-files-table-detach-window.md) | 文件列表独立窗口 | S | P2 | 待做 |
| [03-search-calibre-like.md](./03-search-calibre-like.md) | 类 Calibre 的搜索（关键词 + 字段 + 布尔） | M | P0 | 待做 |
| [04-project-system-files-folding.md](./04-project-system-files-folding.md) | 项目内系统/配置文件折叠 | S | P1 | 待澄清 |
| [05-data-paths-migration-and-export.md](./05-data-paths-migration-and-export.md) | 数据路径自定义/迁移 + 项目导入导出 | M+S | P1 | T6 弃用 · T7 部分被 #09/#10 覆盖 |
| [06-tags-hierarchy-folding.md](./06-tags-hierarchy-folding.md) | 标签层级分类折叠 | S | P2 | 待做 |
| [07-local-embedding-summary.md](./07-local-embedding-summary.md) | 本地嵌入摘要再调 LLM | L | P2 | 远期 |
| [08-multiple-libraries-switch.md](./08-multiple-libraries-switch.md) | 多项目库并存与切换（Calibre 风格） | M | P1 | 待做 |
| [09-project-export-basic.md](./09-project-export-basic.md) | 项目导出（基础版，T7 最小子集） | S | P1 | ✅ 2026-05-31 |
| [10-folder-batch-import.md](./10-folder-batch-import.md) | 文件夹批量导入 + project.json 识别 | S+S | P1 | ✅ 2026-06-01 |
| [11-field-prompt-and-library-wizard.md](./11-field-prompt-and-library-wizard.md) | 字段级 prompt 模板 + 库初始化向导（含可扩展向导框架） | M | P1/P2 | 待做 |

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
