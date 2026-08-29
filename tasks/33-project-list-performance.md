# 33 · 项目列表与封面加载性能优化

**工作量**：M
**优先级**：P0
**状态**：✅ 2026-08-01（v0.6 未发布）

## 来源

2026-07-31 前端评审（`app/ui/` 全量通读）。项目一多，主界面就会卡：搜索框每敲一个字（200ms 防抖）就触发一次全量重建，封面按原图全尺寸解码，每次刷新都是 N+1 查询。

## 现状盘点

| 瓶颈 | 位置 | 问题 |
|------|------|------|
| 封面全尺寸解码 | `main_window.py:1862-1872` `_cover_pix` | `QPixmap(path)` 直接读原图，几 MB 一张全进内存，无任何缓存 |
| paint 热点实时缩放 | `project_card.py:274` | 每次 `paint` 都 `cover.scaled(..., SmoothTransformation)`，滚动帧率随卡片数线性下降 |
| N+1 文件数查询 | `main_window.py:2073-2078` | `refresh_projects` 对每个项目单独 `list_files` 只为数个数 |
| 每次全量重建 | `main_window.py:1998-2003` | `_rebuild_columns()`（model reset）+ 整棵标签树重建 + 全部封面重载，搜索每次击键都跑一遍 |
| `_show_project` 重复查询 | `main_window.py:2201, 2231` | `list_files` 调两次（一次显示、一次数总数） |
| 文件大小逐行 stat | `main_window.py:2384-2405` | 每个文件一次磁盘 `stat`，缓存 dict 随每次 `_show_project` 重建而失效 |
| MCP 轮询全量刷 | `main_window.py:2706-2714` | 每 10s 发现新 audit 就 `refresh_projects()` 全量重建 |
| 库信息统计卡 UI | `main_window.py:476` | `rglob("*")` 递归求 library/ 大小，主线程同步执行 |

---

## 范围与边界

| 子任务 | 内容 | 工作量 |
|---|---|---|
| **T1** | 封面缩略图缓存：按目标尺寸一次缩放、缓存复用，杜绝全尺寸解码 + paint 热点缩放 | M |
| **T2** | 查询减重：文件数 GROUP BY 单查；`_show_project` 复用一次 `list_files`；MCP 轮询只精准刷新受影响项 | S |
| **T3** | 文件大小列会话级缓存（按 `(path, mtime)` 失效）；库信息大小统计挪到后台线程 | S |

**不做（本卡内）**：
- 虚拟化/分页加载项目列表 —— 百级项目量，缩略图 + 查询减重足够；真到万级再说
- 磁盘持久缩略图缓存 —— 见「待澄清」
- 标签树增量更新 —— 标签树重建本身开销不大，大头在封面与查询

---

## T1 · 封面缩略图缓存

### 方案

1. 新增 `app/ui/cover_cache.py`：
   - `get_cover(project_id, file_id, path, target_size) -> QPixmap | None`
   - 内存 LRU（直接用 `QPixmapCache`，上限调到 ~64MB）；
     key = `cover:{file_id}:{mtime_ns}:{w}x{h}` —— 文件变动自动失效
   - 加载时用 `QImageReader` + `setScaledSize`（解码阶段就缩放，比解码原图再 `scaled` 省一个数量级内存）
   - 目标尺寸 = 卡片封面区尺寸 × 设备 DPR（`devicePixelRatioF`），一次解码到位
2. `ProjectModel.set_data` 持有的 `covers` 改为存缩略图（而非全尺寸 pixmap）
3. `ProjectCardDelegate.paint` 删掉实时 `scaled(SmoothTransformation)`，直接按缓存尺寸绘制（必要时只做低成本最近邻微调）
4. 同步受益点：`_cover_pix` 目前还被详情/预览相关路径复用——全部改走缓存

### 约束

- 图片读失败 / 非图片 → 返回 None，走现有 📁 占位逻辑，行为不变
- 缓存只服务"卡片与列表小图"；预览面板（`preview.py`）仍读原图，不经过缩略图缓存

---

## T2 · 查询减重

1. `repository.py` 新增 `count_files_by_project() -> dict[int, int]`：
   `SELECT project_id, COUNT(*) FROM files GROUP BY project_id` 一次查完，
   替换 `refresh_projects` 里的逐项目 `list_files`
2. `_show_project` 只查一次 `list_files`，总数直接 `len()` 原始列表（过滤前先存）
3. `_check_mcp_activity` 发现新 audit 时：
   - 更新状态栏计数
   - 标签树 MCP 计数精准更新（重调 `populate` 或只更新该节点文本）
   - 项目列表仅当当前筛选是 `mcp` 或受影响项目在当前列表中才刷新
4. `_rebuild_columns` 仅在字段 schema 变化时执行（对比 `list_fields` 指纹缓存），不再每次 `refresh_projects` 都 reset model

## T3 · 文件大小与库信息

1. 文件大小列：会话级缓存 `{(path, mtime): size_str}` 挂在 MainWindow 生命周期上，
   `_get_size` 先查缓存；`mtime` 变了才重新 `stat`
2. `_lib_info` 的 library/ 大小：弹窗先显示「统计中…」，`QThreadPool`/`QThread` 后台算完回填；
   对话框关闭时线程结果直接丢弃（参考 `settings_dialog.py:984` `_run_ping_async` 的既有模式）

---

## 校验

- [ ] 200 个项目（其中 50 个带几 MB 封面图）下：连续滚动网格视图不卡，内存占用明显低于改造前
- [ ] 搜索框连续输入：界面响应流畅，不再整屏闪烁重建
- [ ] 修改封面文件内容（同名覆盖）后刷新 → 卡片显示新图（缓存按 mtime 失效）
- [ ] MCP 有新操作时：只有相关计数/行刷新，整表不闪
- [ ] 文件表大小列：同一项目反复切换选中，第二次起无重复 stat
- [ ] 库信息对话框打开即出，大小数字稍后回填，不卡 UI

## 依赖

- 与 #35（MainWindow 拆分）改动同一文件：**建议本卡先于 #35 落地**，让重构搬的是优化后的代码
- `settings_dialog.py` 的 `_run_ping_async` 线程模式可复用参考

## 待澄清

> 卡片正文已按"默认决定"写成可执行状态；**若不同意，请在我编码前告知**。

1. **缩略图缓存是否落磁盘**
   - 默认决定：**只做内存 LRU（`QPixmapCache`）**。进程内够用，免去缓存目录管理、失效清理、打包路径等一堆事。代价是每次启动重新解码一遍首屏封面（几十张缩略图，秒级内可接受）。
   - 若你库里封面图特别多（几百张）且启动就要秒开，告诉我，改成磁盘缓存（`library/.cache/thumbs/`）。
