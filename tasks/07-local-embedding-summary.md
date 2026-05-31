# 07 · 文件级摘要（预处理流水线）

**工作量**：M+L（拆为 T1~T4，可分批落地）
**优先级**：T1/T2 = P1，T3/T4 = P2
**状态**：远期（但 T1/T2 可较早启动）

## 来源

`TODO.md → 🤖 LLM 工作流` 第 1 条「本地嵌入摘要再调 LLM」的扩展版。
配套 `README → 灵感来源 → 未来设想`「文件预处理流水线」预告。

## 目标

为每个文件引入**持久化的"摘要"属性**（依附于文件存在），它：

- 是**文件级的固有属性**，像 `label / kind` 一样存在；不强绑定 LLM 调用流程
- 可以由**多种方式**产生：本地嵌入抽取关键段、视觉模型生成图像描述、视频抽帧 + 自描述、**或用户手动导入**
- 各种消费方都能用：
  - LLM 元数据建议（替代/补充原始文件内容，省 token、过 4o-mini 这种弱多模态）
  - 预览面板的"快速概览"标签
  - 搜索功能（关键词命中摘要）
  - MCP / agent（task #13）通过 `cabinet://file/{id}/summary` 资源读取

## 范围与边界

本任务拆为 4 个子任务，按价值递减排列：

| 子任务 | 内容 | 优先级 | 工作量 |
|---|---|---|---|
| **T1** | 数据层：file 表加 `summary` / `summary_source` 列；Repository CRUD；UI 文件表显示并可编辑 | P1 | S |
| **T2** | 用户手动导入接口：在文件级菜单加「📝 导入摘要…」（从文件 / 从粘贴板），文件夹批量导入支持 `<filename>.summary.md` 旁注文件 | P1 | S |
| **T3** | 摘要提供方框架 + LLM 集成：定义 `SummaryProvider` 接口；prompt 拼装段落改为优先使用 `summary` 替代原始文件内容 | P2 | M |
| **T4** | 内置 Provider 实现：本地嵌入抽取、视觉模型、视频抽帧；按文件 `kind` 自动路由 | P2 | L |

T1/T2 是基础设施，没有 ML 依赖，体积不变，门槛低。
T3 定好接口，让用户也能写自己的 provider。
T4 才真正引入大体积本地模型，按需启用。

**不做（本任务内）**：

- 实时摘要订阅推送（文件改动 → 自动重新摘要）：复杂度高，留作远期
- 跨项目语义检索（用全库 embedding 索引）：是另一个 task，与本 task 共享 embedding 模块
- 摘要质量评估（让 LLM 评估某条摘要好不好）：研究方向，本期不做

## T1：数据层 + UI 入口

### Schema 迁移（vN → vN+1）

```sql
ALTER TABLE files ADD COLUMN summary TEXT NOT NULL DEFAULT '';
ALTER TABLE files ADD COLUMN summary_source TEXT NOT NULL DEFAULT '';
ALTER TABLE files ADD COLUMN summary_updated_at TEXT NOT NULL DEFAULT '';
```

- `summary`：文本内容，Markdown 友好；可以是任意长度但建议 < 2000 字
- `summary_source`：来源标识，例如 `"manual"` / `"embed:bge-small-zh-v1.5"` / `"vision:blip2"` / `"video-keyframe+llm"`；空表示无摘要
- `summary_updated_at`：ISO 时间戳，方便后续判断"是否需要重新生成"

### Repository 接口

```python
def get_file_summary(file_id: int) -> tuple[str, str, str]: ...
def set_file_summary(file_id: int, summary: str, source: str) -> None: ...
def list_files_without_summary(project_id: int) -> list[FileItem]: ...
```

### UI

- 文件表新加一列「摘要」（默认隐藏，列宽偏好继承 task #01）；非空时显示前 80 字 + "…"，hover tooltip 显示全文
- 文件右键菜单加「📝 编辑摘要 / 查看摘要」
- 文件预览面板下方加一块小区域，显示当前文件的摘要 + 来源标识

### 验收（T1）

- [ ] 迁移后老库可正常打开，所有文件 `summary == ""`
- [ ] 通过右键菜单编辑摘要 → 保存 → 重启后仍在
- [ ] 文件表「摘要」列可显示、可隐藏、列宽可记忆
- [ ] `summary_source` 显示在编辑对话框（只读）

## T2：用户手动导入接口

### 单文件导入

文件右键菜单「📝 导入摘要…」→ 弹对话框：

```
┌────────────────────────────────────────┐
│ 导入摘要 — example.pdf                  │
│                                        │
│ 来源：                                  │
│  ○ 从文件读取        [📂 浏览…]         │
│  ● 从剪贴板粘贴                          │
│  ○ 手动输入                             │
│                                        │
│ ┌────────────────────────────────────┐ │
│ │ <预览/编辑区，Markdown>             │ │
│ └────────────────────────────────────┘ │
│                                        │
│ 来源标识（可选）：[manual:imported    ] │
│                                        │
│        [ 取消 ]   [ 保存 ]              │
└────────────────────────────────────────┘
```

### 批量导入约定

在项目文件夹中存在 `<文件名>.summary.md` 旁注文件时，导入项目（task #10）会自动识别并落入对应文件的 `summary` 字段：

```
example.pdf
example.pdf.summary.md       ← 自动关联到 example.pdf 的 summary
```

`summary_source` 自动填 `manual:sidecar`。

这种约定也方便用户用任何外部工具（自己的脚本、其它软件、AI 工具）批量生成摘要后整体导入。

### 验收（T2）

- [ ] 单文件导入三种方式（文件/剪贴板/手动）都能落库
- [ ] 在 task #10 的导入流程中，`.summary.md` 旁注文件被正确识别
- [ ] 旁注文件的 `summary_source` 自动标 `manual:sidecar`
- [ ] 旁注约定写进 `README` 和 `docs/`

## T3：Provider 接口 + LLM 集成

### `SummaryProvider` 接口

```python
class SummaryProvider(Protocol):
    name: str                              # 'embed:bge-small-zh-v1.5'
    supports_kinds: list[str]              # ['doc', 'code'] / ['image'] / ['video']
    max_input_size: int                    # 字节，超过返回 None
    requires_network: bool

    def summarize(
        self,
        file_path: Path,
        kind: str,
        hint: str | None = None,           # 例如项目标题，作为压缩方向提示
    ) -> SummaryResult | None: ...

@dataclass
class SummaryResult:
    text: str
    source: str           # 写到 file.summary_source
    confidence: float     # 0~1，UI 展示用
```

### 注册与路由

```python
# app/summary/registry.py
PROVIDERS: list[SummaryProvider] = []

def register(provider: SummaryProvider) -> None: ...
def pick_provider(file: FileItem, prefer: str | None = None) -> SummaryProvider | None:
    """根据 file.kind + 用户偏好选择 provider"""
```

### LLM prompt 拼装改造

`app/llm/context.py` 在拼装文件段时：

- 若 `file.summary` 非空 → 使用摘要，**并标注来源**（防止模型把摘要当原始内容）
  ```
  ### 文件：example.pdf （来自 embed:bge-small-zh-v1.5 的摘要）
  <summary 内容>
  ```
- 若空 + 文件较小 → 沿用现有"原始文本提取"路径
- 若空 + 文件超大 → 跳过 / 给一行"内容过大已跳过"

### 设置

「设置 → API」加：

- ☑ 优先使用文件摘要（默认开）
- 当文件无摘要但内容超过 [200] KB 时：
  - ○ 跳过该文件
  - ○ 尝试生成摘要（需 T4 的 Provider 可用）
  - ● 沿用现状

### 验收（T3）

- [ ] 文件摘要非空 → "查看 Prompt" 对话框能看到摘要被注入且有来源标注
- [ ] 摘要为空 → 沿用旧路径
- [ ] 用户自定义实现 `SummaryProvider` 并 `register()` → 文件菜单出现一个"用 <provider> 生成摘要"项

## T4：内置 Provider（按 kind 路由）

### 三类 Provider

| Provider | 适用 kind | 体积 | 依赖 |
|---|---|---|---|
| `EmbedSummaryProvider` | `doc / code` | ~95 MB（bge-small-zh-v1.5） | `sentence-transformers` |
| `VisionDescribeProvider` | `image` | ~400 MB（BLIP-2 / 类似轻量级） | `torch` + 视觉模型 |
| `VideoKeyframeProvider` | `video` | 复用 ffmpeg + 上面两种 | `ffmpeg-python` + image provider |

**仅在用户在设置里启用对应 provider 时下载模型**——不强制打进 exe。

### 流程要点

**EmbedSummaryProvider**：
- 切块（512 chars + 64 overlap）
- 用项目标题 + 用户附言作为 query 做相似度检索
- 取 top-K 块拼接，再加一段 LLM 风格的"主题概括"（可选 + 走 LLM 生成）

**VisionDescribeProvider**：
- 调本地视觉模型 → 一段 caption + 关键标签
- 写入 `summary`，`summary_source = "vision:<model>"`

**VideoKeyframeProvider**：
- ffmpeg 抽 N 个关键帧（默认 5 个，等时间间隔）
- 每帧调 VisionDescribeProvider 拿描述
- 汇总写入 summary

### 包体积策略

主 exe **不内置** ML 依赖。用户在设置里启用 provider → 检测 site-packages 是否有对应包 → 没有则提示"需要安装：`pip install llm-cabinet[summary]`"或下载独立扩展。

这是延续 task #09 / #10 的"渐进增强"思路：核心轻量，能力按需。

### 验收（T4）

- [ ] 三种 provider 各自能跑通端到端：选文件 → 生成 → 写库 → UI 显示
- [ ] 未安装依赖时 UI 给清晰提示，不崩溃
- [ ] 用户能在 settings 里关闭某个 provider（即使装了依赖）
- [ ] 视频 provider 的关键帧数量、视觉 provider 的模型路径可配

## 隐私与安全

- **本地优先**：T4 的三种 provider 都本地运行；只有摘要文本会随 LLM 调用上送
- **来源透明**：UI 始终显示 `summary_source`，用户随时知道这条摘要哪来的
- **不静默改摘要**：所有写操作（除批量导入外）都有用户触发；自动生成必须开开关
- **PRIVACY 同步**：新增段落说明摘要是文件级数据，导出包（task #09 → bump schema）会带走

## 风险

- **包体积爆炸**：T4 的 vision + embed 加起来可能 1 GB+；用"按需安装的扩展包"策略规避
- **首次模型加载慢**：T4 的 provider 懒加载 + 进度提示
- **摘要质量参差**：弱模型生成的摘要可能误导 LLM；UI 上把"摘要"和"原始内容"在视觉上分清，便于用户判断
- **过度设计 Provider 接口**：T1/T2 完全可以在没有 Provider 概念的情况下工作；T3 接口要克制，不预留太多钩子（YAGNI）

## 依赖

- 不强依赖任何其它 task
- 与 `tasks/09`（已完成）：导出时把 `summary` 列写进 `files.json`；后续 schema 升级
- 与 `tasks/10`（已完成）：导入时识别 `.summary.md` 旁注（T2 范围）
- 与 `tasks/13`（MCP server）：暴露 `cabinet://file/{id}/summary` 资源
- 与 `tasks/12`（自检体系）：T1 配 `task07_file_summary.py`，至少覆盖 CRUD + 旁注导入

## 工作量拆分

| 子项 | 估算 |
|---|---|
| T1 数据迁移 + Repository + UI 列/编辑 | 0.5 天 |
| T2 单文件 + 旁注批量导入 | 0.5 天 |
| T3 Provider 接口 + LLM prompt 拼装改造 | 1 天 |
| T4 三类内置 Provider（每个 ~1 天） | 3 天（含依赖处理） |
| **合计** | ~5 天（M+L） |

## 后续扩展

- **摘要质量评级**：让用户给摘要打分，反馈用于 provider 改进
- **跨项目语义检索**：复用 embedding provider 建全库索引
- **摘要版本历史**：保留旧摘要，方便对比 / 回滚
- **AI 写摘要的二次校验**：用更强 LLM 校对/精炼初步摘要
- **`.summary.md` 标准化**：发布一个开放格式，让其它工具也能产出兼容的旁注文件
