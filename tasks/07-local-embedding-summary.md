# 07 · 本地嵌入摘要再调 LLM

**工作量**：L  
**优先级**：P2  
**状态**：远期

## 来源
`TODO.md → 🤖 LLM 工作流` 第 1 条

## 目标
参考文件过大时（如 100 MB PDF 全书、数万行 csv），先用**本地嵌入模型**生成摘要或检索最相关的若干段，再把摘要发给云端 LLM，**提高建议精度 + 省 token + 保护隐私**。

## 实现要点

### 引入本地嵌入模型
候选：
- **sentence-transformers**（`paraphrase-multilingual-MiniLM-L12-v2`）：~120 MB，中文也行
- **nomic-embed-text**（轻量、英文为主）
- **bge-small-zh-v1.5**（中文专精，~95 MB）

推荐 **bge-small-zh-v1.5**：中文场景最强、体积小。

### 流程
1. 在 `app/llm/embed.py` 实现：
   - 模型加载（懒加载，首次启动后台下载）
   - `embed(texts: list[str]) -> ndarray`
   - 简单的 in-memory FAISS 索引
2. 在 `context.py` 加大文件处理路径：
   - 文件 > 阈值（默认 200 KB 文本 / 50 页 PDF）时启用
   - 切块（512 chars + 64 overlap）→ embedding → 索引
   - 用项目当前的"标题 + 用户附言"作为 query，取 top-K 块拼接成"摘要片段"
3. prompt 段落改成 `### 文件：xxx（嵌入检索的相关片段）` + 拼接结果

### 设置
- 设置 → API 加"启用本地嵌入预处理"开关，默认关
- 模型路径与下载源可配置（HuggingFace 或本地）

### UI 反馈
- 首次启动时如果开了开关但没下模型，弹窗"正在下载 ~95 MB 嵌入模型，是否继续？"
- 任务详情里加一行"已嵌入 N 块，发送 M 块给 LLM"

## 隐私收益
- 本地完成嵌入与检索，**只把最相关的几段**发出去
- 在 `PRIVACY.md` 加一节说明

## 依赖
- 新增 Python 依赖：`sentence-transformers` 或 `torch` + `faiss-cpu`（共计可能 1 GB+）
- 这会显著增加 pyinstaller 包体积，可能要考虑独立"扩展包"

## 风险
- **包体积**：sentence-transformers + torch 加起来约 800 MB+，对桌面应用是大负担
- **首次启动慢**：模型加载需要 2~5 秒
- **CPU 推理速度**：100 MB PDF 嵌入要 1~3 分钟
- **GPU 加速**：可选，但跨用户硬件配置差异大

## 备选方案
- 不引入嵌入模型，改用纯文本启发式（关键句提取、TextRank）
- 工作量 M，效果略差但零依赖
