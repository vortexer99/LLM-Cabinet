# 37 · 确认对话框与操作反馈统一

**工作量**：M
**优先级**：P1
**状态**：✅ 2026-08-01（v0.6 未发布）

## 来源

2026-07-31 前端评审。反馈体验不统一：全中文 UI 里确认框却是英文 Yes/No；批量导入每个失败文件弹一个模态框；删除物理删除且失败静默；有的操作用状态栏、有的弹窗，没有约定。

## 现状盘点

| 问题 | 位置 |
|------|------|
| `QMessageBox.question/warning` 默认英文 Yes/No 按钮遍布全 UI | `main_window.py` 几十处，如 `2894, 3871` |
| 批量导入逐文件弹模态错误框（100 个失败 = 连弹 100 次） | `main_window.py:3354-3355` |
| 删项目时仓储文件 `unlink/rmdir` 失败静默 `pass` | `main_window.py:2912-2920` |
| 删除不可撤销：仓储文件直接物理删除 | `main_window.py:3592-3596` 等 |
| `except Exception: pass` 吞错，出问题无感知 | 封面/预览/备份设置等多处 |
| 反馈渠道无约定：statusBar / 模态框混用 | 全 UI |

---

## 范围与边界

| 子任务 | 内容 | 工作量 |
|---|---|---|
| **T1** | 中文确认/警告对话框封装，全量替换英文按钮 | S |
| **T2** | 批量操作错误汇总（不逐文件弹窗）+ 删除失败如实汇报 | S |
| **T3** | 删除进系统回收站（send2trash） | S |
| **T4** | 反馈策略约定写进 AGENTS.md + 关键静默 except 接 logging | XS |

**不做（本卡内）**：
- Undo/重做系统 —— 回收站已覆盖"误删恢复"的主要场景；真 undo 是 L 级，远期

---

## T1 · 中文确认框封装

新增 `app/ui/dialogs.py`：

```python
def confirm(parent, title, text, *, yes="确定", no="取消", danger=False) -> bool
def ask_yes_no_cancel(parent, title, text, *, yes, no, cancel="取消") -> str
def warn(parent, title, text) -> None
def info(parent, title, text) -> None
```

- 基于 `QMessageBox` + `addButton(中文)`（`main_window.py:4616` 已有范例写法）
- `danger=True` 时确认按钮套 `Property("danger", True)` 样式、默认焦点在"取消"
- 全量替换 `QMessageBox.question/warning/information` 调用点（保留 `setDetailedText` 等特殊用法，封装留口子）

## T2 · 批量错误汇总

- `_import_files` 等批量路径：收集 `(item, error)` 列表，结束后一次汇总框：
  - 主文案：`完成 X / Y；失败 Z 项`
  - 失败明细放 `setDetailedText`
- 删项目/删文件的物理删除失败：同样收集后汇总，不再静默 `pass`
- 约定：批量操作**永不**在循环里弹模态框

## T3 · 回收站删除

- `requirements.txt` 增加 `Send2Trash`
- 所有"删除用户文件"路径（删项目仓储文件、删文件、#29 相关）改 `send2trash`
- send2trash 失败（如文件在 U 盘/网络盘）→ 回退硬删除，但在确认框里预先说明"将直接删除，不进回收站"
- 删除确认框文案统一加一行"文件将移入系统回收站，可从回收站恢复"

## T4 · 反馈策略约定 + logging

在 AGENTS.md 补一节（供后续所有开发遵循）：

| 场景 | 渠道 |
|---|---|
| 即时小反馈（已复制、已重命名） | statusBar `showMessage` |
| 需要用户知晓后果（删除结果、导入汇总） | 对话框 |
| 批量操作结果 | 一次汇总框（明细 detailedText） |
| 后台任务完成/失败 | statusBar + 任务面板 |

代码内 `except Exception: pass` 的关键路径（封面加载、预览、设置写入）改 `logging.exception` 或 `logging.warning`，建立 `app` logger（输出到 app_data_dir 下的 log 文件，级别 WARNING）。

---

## 校验

- [ ] 全 UI 不再出现英文 Yes/No/Cancel 按钮（重点过：删除、覆盖、切换库、导入确认）
- [ ] 批量导入 100 个含失败文件：只弹一次汇总框，明细可查
- [ ] 删除项目（含仓储文件）：文件进回收站，可从系统回收站还原
- [ ] 回收站不可用场景（模拟失败）：回退硬删除且文案有预告
- [ ] 删除过程中制造一个失败（占用文件）：结果框如实列出，不静默
- [ ] AGENTS.md 新增反馈策略一节

## 依赖

- T2 的错误汇总与 #36（线程化）共用"收集-汇总"结果结构，建议同期或先后衔接
- T3 见「待澄清」的依赖引入问题

## 待澄清

> 卡片正文已按"默认决定"写成可执行状态；**若不同意，请在我编码前告知**。

1. **是否引入 `Send2Trash` 依赖**
   - 默认决定：**引入**（纯 Python 小包，Windows 下走 SHFileOperation，稳）。回收站语义对"仓储模式误删"是最低成本的安全网。
   - 若你不想加依赖：T3 退化为"删除前确认框文案强化 + 失败如实汇报"，维持硬删除。
