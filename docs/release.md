# 发布流程（Release Process）

LLM Cabinet 的 Windows 单 exe 通过 GitHub Actions 自动构建，
workflow 文件：[`.github/workflows/build-windows.yml`](../.github/workflows/build-windows.yml)。

本文档面向**维护者**，描述日常构建与正式发版的全流程，以及 CI 内部做了什么、
如何排错。终端用户的安装说明请见 [`README.zh-CN.md`](../README.zh-CN.md)。

---

## 1. 速查

| 我想做的事 | 怎么做 |
|---|---|
| 拿一份当前 main 的测试 exe | GitHub → Actions → `Build Windows EXE` → Run workflow，到运行页底部下载 Artifact |
| 正式发布一个版本 | 走 [§3 发版步骤](#3-发版步骤) |
| 出 prerelease（rc/beta） | 同 §3，但 tag 名带 `-`（如 `v0.2.0-rc1`），同时 `__version__` 也要写成 `"0.2.0-rc1"` |
| 改了 schema | 走 §3，并确认 `CHANGELOG.md` 标注 `📦 schema vN → vM` |

---

## 2. 触发与产物

| 触发方式 | 何时跑 | 产物去向 |
|---|---|---|
| **手动**（workflow_dispatch） | Actions 页面点 Run workflow | Artifact（90 天，仅登录用户可下载） |
| **tag `v*` 推送** | `git push origin v0.x.y` | Artifact + **GitHub Release**（带 exe + 自动 release notes） |

产物文件名规则：

- tag 触发：`LLM Cabinet-0.2.0.exe`（直接取 tag 去掉 `v`）
- 手动触发：`LLM Cabinet-0.1.0+abc1234.exe`（`__version__` + 7 位 commit SHA）

> 没有把 `push: main` 加入触发器，避免每次合并都消耗 CI。需要时改 workflow 第 11 行附近加上即可。

---

## 3. 发版步骤

### 3.1 准备

发版前一律先在本地完整跑一遍 GUI 自测，确认无 regression。

```powershell
.venv\Scripts\activate
python -m app.main
```

### 3.2 改版本号 + CHANGELOG

按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格更新 `CHANGELOG.md`：

1. 把 `[Unreleased]` 段落里的内容搬到一个新的版本段，例如 `[0.2.0] - 2026-06-15`
2. 重新建一份空的 `[Unreleased]` 占位
3. 文件末尾添加对应的对比链接

同步修改 `app/__init__.py`：

```python
__version__ = "0.2.0"
```

> **重要**：`__version__` 必须与你接下来要打的 tag（去掉 `v`）**完全一致**。
> 不一致时 CI 的 `Verify tag matches __version__` 这一步会硬 fail，
> 拒绝构建——这是有意为之，避免发出版本号错乱的 exe。

如果本次发版涉及数据库 schema 变化，确认：

- `app/db.py` 中 `SCHEMA_VERSION` 已 bump
- `MIGRATIONS` 列表已注册新的迁移函数（幂等）
- `docs/migrations.md` 已记一笔
- `CHANGELOG.md` 该版本段显式标注 `📦 schema vN → vM`

### 3.3 提交、打 tag、推送

```powershell
git add app/__init__.py CHANGELOG.md
git commit -m "release: v0.2.0"
git tag v0.2.0
git push origin main
git push origin v0.2.0
```

> 两条 push 顺序无关紧要，但 `git push origin v0.2.0`
> 这一条**才是真正触发 CI 的动作**。

### 3.4 等 CI 完成

到 GitHub Actions 页面看 `Build Windows EXE` 的进度（约 3-5 分钟）。
成功后：

- **Releases** 页出现新条目 `LLM Cabinet v0.2.0`，挂着 `LLM Cabinet-0.2.0.exe`
- 自动生成的 release notes 包含本次 tag 与上次 tag 之间的所有 commit / PR 标题

如果发现 release notes 自动总结不够好，可以手动到 Releases 页编辑补充内容；
exe 不会受影响。

### 3.5 验证发布物

下载 Release 中的 exe，在干净的 Windows 机器上（最好是没装 Python 的）跑一遍：

- 启动正常，主界面渲染无误
- 启动后 `%APPDATA%\LLMCabinet\cabinet.db` 能创建/打开
- 至少试一次"新建项目 + 添加文件 + 预览"完整流程

通过即发版完毕。

### 3.6 如果出了问题需要撤回

```powershell
# 删除远端 tag 和 release
git push --delete origin v0.2.0
# 然后在 GitHub Releases 页手动删除对应 release（或用 gh CLI）

# 删除本地 tag
git tag -d v0.2.0
```

修复问题后从 §3.2 重新走流程。**不要复用同一个 tag 名**——容易出现旧 release 缓存。
直接用下一个补丁号（如 `v0.2.1`）。

---

## 4. CI workflow 逐步说明

按 [`build-windows.yml`](../.github/workflows/build-windows.yml) 文件顺序：

| 步骤 | 作用 | 失败常见原因 |
|---|---|---|
| Checkout | 拉源码 | — |
| Set up Python 3.11 + pip cache | 装 Python、缓存依赖 | requirements.txt 文件不存在 |
| Install dependencies | `pip install -r requirements.txt && pip install pyinstaller` | PySide6 / pypdf wheel 临时下载失败（重跑即可） |
| **Smoke import check** | 不开窗口直接 import 核心模块 | 代码 SyntaxError / 循环 import / 缺包 |
| Resolve version | 解析最终文件名用的版本号 | — |
| **Verify tag matches __version__** | tag 触发专用，校验 tag 与 `__version__` 一致 | 忘了改 `app/__init__.py`，详见 §3.2 |
| Build with PyInstaller | onefile 打包 | 资源文件路径不对、`--add-data` 参数和实际目录不一致 |
| Rename artifact with version | `LLM Cabinet.exe` → `LLM Cabinet-<ver>.exe` | — |
| Upload artifact | 永远上传，方便手动触发也能拿到 exe | — |
| Create / update GitHub Release | **仅 tag 触发**，上传 exe + 自动 release notes | 需要 `permissions: contents: write`（已设置） |

PyInstaller 参数与 [`README.zh-CN.md`](../README.zh-CN.md) 中"打包为单 exe"段落保持一比一对齐：

```
pyinstaller -w -F -n "LLM Cabinet" \
  --icon icon.ico \
  --add-data "icon.ico;." \
  --add-data "icon.jpg;." \
  --add-data "PRIVACY.md;." \
  --add-data "PRIVACY.zh-CN.md;." \
  --add-data "app/ui/assets;app/ui/assets" \
  run.py
```

---

## 5. FAQ / 排错

### Q：CI 跑出来 "Tag (X) does not match app.__version__ (Y)" 怎么办？

漏改了 `app/__init__.py`。两种修法：

```powershell
# 方案一：版本号其实就是 X，改 __version__ 后重新发版（用新 tag 号）
# 编辑 app/__init__.py 把 __version__ 改成 X
git add app/__init__.py
git commit -m "release: bump version to X"
git push --delete origin vX     # 删旧 tag
git tag -d vX
git tag vX.0.1                  # 升一个补丁号，避免缓存
git push origin main
git push origin vX.0.1
```

```powershell
# 方案二：版本号应该是 Y，tag 打错了
git push --delete origin vX
git tag -d vX
git tag vY
git push origin vY
```

### Q：构建产物多大？正常吗？

PySide6 onefile 大约 65-80MB（含 Qt 全套）。低于 50MB 或高于 100MB 都值得怀疑：
前者可能漏打 Qt 插件（PDF / 视频 codec 缺），后者可能误带了开发依赖。

### Q：用户反馈"Windows 保护了你的电脑"怎么办？

未签名 exe 触发 SmartScreen 是预期行为，让用户点 **"更多信息" → "仍要运行"**。
彻底解决要购买代码签名证书（个人一年 $100+），暂不考虑。可以在 README 加一条说明。

### Q：手动触发能不能也发 Release？

故意没做。手动触发产物只进 Artifact，**避免误发**。要 Release 必须打 tag —— 一种轻量的"二次确认"。

### Q：怎么本地复刻 CI 行为？

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller

# 冒烟
python -c "import importlib; mods = ['app.utils','app.db','app.models','app.library','app.repository','app.llm','app.llm.providers','app.llm.queue']; [importlib.import_module(m) for m in mods]; print('OK')"

# 打包
pyinstaller -w -F -n "LLM Cabinet" `
  --icon icon.ico `
  --add-data "icon.ico;." `
  --add-data "icon.jpg;." `
  --add-data "PRIVACY.md;." `
  --add-data "PRIVACY.zh-CN.md;." `
  --add-data "app/ui/assets;app/ui/assets" `
  run.py

dist/"LLM Cabinet.exe"
```

### Q：要不要也出 onedir 版本？

onedir 启动更快但用户要解压一个文件夹，分发体验比 onefile 差。
当前选择 onefile（约慢 1 秒启动，换"双击一个文件就能用"）。
要并行出两份只需在 workflow 里加一个 job 跑 `pyinstaller -D ...` 并打 zip。

---

## 6. 安全边界与限制

- workflow 用 `permissions: contents: write`，仅授权创建 Release，不能改其它仓库设置
- 没有用到任何 secrets（API key、签名证书等）。`HUNYUAN_API_KEY` 等本应由终端用户填，不进 CI
- artifact 默认 90 天保留，Release 资产无过期
- 如果将来引入代码签名，证书必须存到 GitHub Secrets（绝不能落代码库），workflow 增加签名步骤

---

## 7. 待办 / 未来增强

按需考虑：

- [ ] tag 命名约束（regex 校验 `v\d+\.\d+\.\d+(-[a-z0-9]+)?`）
- [ ] PR 触发只跑 smoke + 打包（不发布），快速发现破坏性改动
- [ ] 同时出 `LLM Cabinet-<ver>.zip`（含 PRIVACY 等文本，方便阅读）
- [ ] 加 SHA256 校验文件（`*.exe.sha256`）随 Release 一起上传
- [ ] 代码签名（需购买证书）

---

最后更新：见 git log。
