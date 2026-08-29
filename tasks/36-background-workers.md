# 36 · 长耗时操作线程化（消除主线程 processEvents）

**工作量**：M
**优先级**：P1
**状态**：✅ 2026-08-01（v0.6 未发布）

## 来源

2026-07-31 前端评审。导出、批量导出、批量导入、#29 的移动/转仓储/重关联都是主线程 for 循环 + `QApplication.processEvents()` 或直接靠 `QProgressDialog` 自己 pump 事件。文件多/大时 UI 卡顿甚至"未响应"，且 `processEvents` 有重入风险（进度框弹出期间用户还能点别的按钮）。

## 现状盘点

| 位置 | 操作 | 现状 |
|------|------|------|
| `main_window.py:2968-3002` | 单项目导出 | 主线程循环 + `processEvents()` |
| `main_window.py:3037-3082` | 批量导出 | 主线程循环 + `processEvents()` |
| `main_window.py:4711-4753` | 批量文件夹导入 | 主线程循环（含复制） |
| `main_window.py:3673-3710` | 链接转仓储 | 主线程循环 |
| `main_window.py:3757-3801` | 移动文件 | 主线程循环 |
| `main_window.py:948-965` | 一致性检查 | 主线程（扫描可能上千文件） |
| `main_window.py:1109-1123` | 备份库 | 主线程打包 zip |

---

## 方案

### A. 统一 worker 基础设施

新增 `app/ui/workers.py`：

```python
class FileOpWorker(QThread):
    progress = Signal(int, int, str)   # done, total, name
    item_error = Signal(str)           # 单项失败（收集不中断）
    finished_ok = Signal(object)       # 汇总结果
    cancelled = Signal()
```

- 任务描述为"纯函数式"的数据结构（要复制/移动哪些文件、源、目标），worker 只执行文件 IO
- 取消语义：主线程置 flag，worker 在每项之间检查，已完成的项不回滚（在结果里如实汇报）
- 进度对话框只负责显示与取消按钮，不再承担事件 pump

### B. sqlite 线程边界（关键约束）

`repo.conn` 是主线程单连接，worker 线程**不得**直接使用。默认分工：

- **worker 线程**：纯文件 IO（copy/move/zip/stat），产出"结果清单"
- **主线程**：worker 完成后统一执行 DB 写入（update_file / add_file / reorder 等），一条事务提交

一致性检查这类"读 DB + stat"的操作：启动时主线程一次性快照出要检查的清单，worker 只 stat。

### C. 逐点改造

| 操作 | 改造 |
|------|------|
| 导出（单/批量） | 复制阶段走 worker；导出完成后主线程写 `last_export_dir` |
| 批量导入 | 扫描 + 复制走 worker；`import_folder_as_project` 的 DB 部分回主线程（可能需要把 importer 拆成"IO 段/DB 段"，与 #35 T1 协同） |
| #29 移动/转仓储/重关联 | IO 走 worker，完成后主线程批量 `update_file` |
| 一致性检查 | 快照清单 → worker stat → 主线程出报告 |
| 备份 | 打包走 worker |

### D. 重入防护

- 每个操作进行中：对应菜单/按钮 disable（或操作级互斥锁），防止用户重复触发同一长操作
- 删除所有 `QApplication.processEvents()` 调用点

---

## 校验

- [ ] 导出含 500+ 文件的项目：主窗口可正常拖动/点击，进度条实时，无"未响应"
- [ ] 进度中点取消：worker 在当前项后停下，结果如实汇报"已完成 X / 共 Y，已取消"
- [ ] 批量导入中途取消：已导入的项目完整存在（DB 事务不出半成品）
- [ ] 移动文件出错（占用/权限）：错误汇总显示，其余项继续
- [ ] `grep processEvents app/` 为零
- [ ] 各操作完成后 DB 状态与改造前完全一致（selftest 覆盖导出/移动路径）

## 依赖

- `settings_dialog.py:984` `_run_ping_async` 是现成的线程范式参考
- 与 #35 T1（业务下沉）强协同：**建议先做本卡或两张卡一起规划**，让下沉到服务层的函数天然就是"worker 可调用"的纯 IO/纯 DB 两段式
