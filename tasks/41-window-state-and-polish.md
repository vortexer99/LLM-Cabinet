# 41 · 窗口状态持久化与细节打磨合集

**工作量**：S+M
**优先级**：P2
**状态**：✅ 2026-08-01（v0.6 未发布）

## 来源

2026-07-31 前端评审中不适合单独成卡的一批小改进，合在一起做一个 PR。每项都小，但都是日常每天都会蹭到的毛边。

## 范围与边界

| 子任务 | 内容 | 工作量 |
|---|---|---|
| **T1** | 窗口 geometry / 最大化状态持久化 | XS |
| **T2** | splitter 恢复 hack（`QTimer.singleShot(0)` 二次 setSizes）根治 | S |
| **T3** | 快捷键补全：Delete 删除、Ctrl+F 聚焦搜索 | XS |
| **T4** | `ClickableLabel` 组件替换 monkey patch；全局 eventFilter 收窄 | XS |
| **T5** | 文件名列 hover tooltip 显示完整文件名 | XS |
| **T6** | 字号可调（设置 → 通用） | S |

**不做（本卡内）**：以上各项之外的任何新功能。

---

## T1 · 窗口 geometry 持久化

- 关闭时 `saveGeometry()` + `isMaximized()` 存入 settings（`window_geometry` / `window_maximized`）
- 启动时恢复；恢复失败（配置损坏/显示器变少）回退 `resize(1400, 880)`（`main_window.py:232`）
- 弹出式文件列表窗口（`_detach_files_panel`，`main_window.py:1641`）同样记忆尺寸与位置

## T2 · splitter 恢复 hack 根治

- 现状：`_restore_main_splitter_sizes` 先 `setSizes` 再 `QTimer.singleShot(0, setSizes)`（`main_window.py:2272-2277`），说明存在布局时序问题（widget 尺寸未就绪时 setSizes 被重算覆盖）
- 排查根因（大概率是 `_show_project` 里内容变化触发的 sizeHint 链式重算），改为在合适的时机一次性设置（如 `showEvent` 后 / 布局 activate 后），删掉 singleShot 兜底
- 验收：左右三栏宽度在项目切换、多选、刷新后保持用户拖出的值

## T3 · 快捷键

| 键 | 语境 | 动作 |
|---|---|---|
| `Delete` | 项目视图有选中 | 删除项目（走现有确认） |
| `Delete` | 文件表有选中 | 移除文件（走现有确认） |
| `Ctrl+F` | 全局 | 聚焦搜索框并全选（与 #38 T3 重叠，谁先做谁带） |

- 用 `QShortcut` 挂对应 widget（`Qt.WidgetWithChildrenShortcut`），避免与文本输入冲突

## T4 · ClickableLabel + eventFilter 收窄

- 新增 `widgets.py: ClickableLabel(QLabel)`（`clicked` 信号），替换 `main_window.py:1468, 1479` 两处 `mousePressEvent = lambda` 猴子补丁
- eventFilter 从 QApplication 全局（`main_window.py:4427`）收窄到 `search_box` / `tbl_files` 视口；DropZone 显隐逻辑随迁（与 #35 T3 重叠，谁先做谁带）

## T5 · 文件名列 tooltip

- 文件表文件名列设 tooltip = 完整文件名（目前 ElideNone 硬截断后看不到后半段）
- 目录节点 tooltip = 完整 subfolder 路径
- 顺带检查项目列表视图 title 列同样处理

## T6 · 字号可调

- 设置 → 通用 增加"界面字号"（11/12/13/14/16 档，默认 13）
- 依赖 #34 T3 的字号变量：改设置 → 重新渲染 QSS → `apply_theme`
- delegate 里 `setPointSize(10/9)` 的相对字号按比例跟随（卡片标题 = 基础 - 3，副标题 = 基础 - 4）

---

## 校验

- [ ] 调整窗口大小/最大化 → 重启应用 → 布局恢复；拔掉副屏后启动不错位
- [ ] 切换项目/多选/刷新后三栏宽度稳定（不再依赖 singleShot）
- [ ] Delete 在两个视图中各自触发正确的删除确认；文本输入框里按 Delete 不误触发
- [ ] 状态栏两个计数标签点击行为不变，实现走 ClickableLabel
- [ ] 长文件名 hover 显示全名
- [ ] 字号调到 16：全 UI 不串行不裁切（重点看表格行高、卡片布局），改回 13 恢复

## 依赖

- T3 与 #38 T3 重叠（Ctrl+F），T4 与 #35 T3 重叠（eventFilter 收窄）—— 先落地的卡顺手完成，另一张卡划掉对应项
- T6 依赖 #34 T3（字号变量）；#34 未做则 T6 后置
