# 02 · 文件列表独立窗口

**工作量**：S  
**优先级**：P2  
**状态**：✅ 2026-06-10

## 来源
`TODO.md → 🎨 UI / 交互` 第 3 条

## 目标
点击文件区右上角"⇱"按钮，文件列表脱离主窗口成为独立窗口；关闭后回到三栏布局。

## 实现要点
- 文件区组件存为实例属性 `self._files_panel`（QWidget），可在主窗口右下 splitter 与独立 QDialog 之间迁移
- 主窗口加 detach/attach 方法：
  - `_detach_files_panel`：从 `self._right_v_split` 取出 `_files_panel`，放入新 QDialog；splitter 加占位 QLabel
  - `_attach_files_panel`：把 `_files_panel` 放回 splitter，移除占位，关闭并销毁 QDialog
- 独立窗口里仍能接收：拖放（FilesTableDnD 直接挂在 tbl_files 上，跟随面板搬家）、右键菜单、选中→预览联动（通过 `_on_file_selected` 槽走 `self.preview`，已是间接调用）
- 关闭独立窗口时自动 attach：`dlg.finished.connect(self._attach_files_panel)`，并加 try/except 兜底重入
- 主窗口关闭 = 一并关闭独立窗口：QDialog 父对象指向主窗口，Qt 自动级联

## 验收
- [x] 点击 ⇱ 按钮，文件列表弹出为 700×500 独立窗口
- [x] 独立窗口里能选文件、右键菜单、拖入新文件
- [x] 选中文件时主窗口预览面板同步刷新
- [x] 关闭独立窗口 / 再次点击按钮（变 ⇲），文件列表回到主窗口
- [x] 主窗口关闭时，独立窗口一并关闭

## 注记
- 没有写 selftest：纯 UI 状态机（QWidget 父子切换 + QDialog 生命周期），
  `selftests/` 约定不依赖 QApplication，可抽出的纯逻辑几乎为零。已在 main_window
  注释中说明状态转换。

## 依赖
- T1 完成后做更顺：保证文件表组件足够自包含 — 已验证 #17 改造后 `tbl_files` 与
  `FilesTableDnD` 都挂在面板内部，搬家无副作用

## 风险
- 拖放事件过滤器（FilesTableDnD）的父子关系切换时要重新挂载 — 实测无需，
  `FilesTableDnD` 通过 `installEventFilter(table)` 挂在 tbl_files 上，
  整个 `_files_panel` 搬家时事件过滤器随之迁移
- 预览面板与文件选中状态的同步要走 signal 而非引用 — 当前 `_on_file_selected`
  通过 `self.preview` 间接调用，没有 dangling 风险

