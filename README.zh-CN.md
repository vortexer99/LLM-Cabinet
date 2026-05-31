<div align="center">

<img src="icon.jpg" alt="LLM Cabinet" width="128" />

# LLM Cabinet

带 AI 元数据助手的轻量级项目化文件管理器（Windows 桌面）。  
按"项目"组织文件，支持自定义字段、标签、Markdown 描述、封面与多模型 LLM 建议。

[English](README.md) · 简体中文

</div>

## 注意

**本项目为个人项目，不保证稳定性。本人仅为项目提供idea，具体实现几乎全部由AI生成，不保证代码质量。**

**免责声明**：本人将持续维护本软件，但**不对任何使用过程中**（包括但不限于异常使用、误操作、系统故障、第三方 LLM 服务异常）**导致的文件丢失、数据损坏或其他损失承担责任**。请通过**定期备份**保护重要数据。本软件按"原样"提供，详见 [MIT License](LICENSE)。

## 灵感来源

本项目灵感源于传统文件管理器 [Calibre](https://calibre-ebook.com/)——按"书库"的方式做标签化文件管理非常高效，但**维护元数据的人力成本极高**：一个个手动填标题、作者、标签、描述，往往让人望而却步。给文件分配合适的标签需要精力，新增一个字段时回过头维护已有条目同样需要精力。即使完全不用元数据、只靠传统的文件夹层级管理，**管理成本也并未消失**——文件名应该怎么起、放在哪一级目录下合适，每一次决策都在消耗注意力。

随着"LLM-Wiki"概念兴起（参考 Andrej Karpathy 的 [LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 以及 [nashsu/llm_wiki](https://github.com/nashsu/llm_wiki) 等相关实践）：借鉴这类项目"**让 LLM 自动读取原始资料、产出并维护结构化条目**"的工作流形式——把它搬到文件管理场景里，就是**让不会疲倦的 LLM 作为文件管家**：先帮你设计一套适合当前库的元数据方案（哪些字段、什么格式），再按这套方案对每个项目做读取与维护工作。

相比 LLM-Wiki，**很多时候你并不需要让 LLM 消化吸收所有文件**——那会消耗大量 token，而你的真实诉求可能只是"把它们放得整齐一点、找的时候能找到"。LLM Cabinet 正是这个思路在"个人文件库"场景下的落地：保留 Calibre 式的项目/标签/字段抽象，把最枯燥的"读文件 → 填元数据"那一步可选地交给 LLM。

**未来设想**：本软件可能进一步实现**外部 agent 调用**接口——你可以把项目文件全部交由 agent 管理，由它把新文件放到合适的位置并自动分配元数据；当 agent 需要资料时，再回到库里调阅检索。LLM Cabinet 期望成为这种"AI 文件中枢"的承载层。

## 截图

<table>
  <tr>
    <td align="center" width="62%">
      <img src="docs/screenshots/main-window.png" alt="主界面" />
      <br/><sub>主界面：标签筛选 · 项目列表 · 预览与文件表</sub>
    </td>
    <td align="center" width="38%">
      <img src="docs/screenshots/project-edit-llm-suggest.png" alt="项目元数据编辑对话框含 LLM 建议" />
      <br/><sub>项目编辑对话框，逐字段展示 LLM 建议（✓ 应用 / ✗ 驳回）</sub>
    </td>
  </tr>
</table>

## 特性

- **项目化组织**：一个项目对应一组相关文件，每个文件可独立填写说明（如"中文版"、"第一页"）
- **字段系统**：标题 / 作者 / 日期 / 评分 / 来源 / 标签 / 描述 等内置字段；可自由增删、排序、隐藏，并自定义类型（文本/多行/日期/URL/评分/数字）
- **标签**：作为多值字段一等公民，左栏可按标签筛选；空标签折叠至单独分组
- **两种存储模式**（每项目可选）
  - `link`：仅记录原始路径，不动用户文件
  - `copy`：导入时复制到统一仓库目录 `library/<project_id>/`
- **预览**：图片、视频、PDF 内嵌预览；其它类型调用系统默认程序打开
- **拖放**：拖文件/文件夹到空白区新建项目，拖到项目卡片加入既有项目；拖文件夹时默认以文件夹名为标题。**拖入多个文件夹**时可选择「合并为同一项目」或「每个文件夹分别建一个项目」；后者会识别每个文件夹中的 `project.json` 并恢复元数据。
- **LLM 元数据助手**（核心特色）
  - 内置 DeepSeek / OpenAI / Google Gemini / xAI Grok 四家适配
  - 一键基于现有元数据 + 参考文件（PDF/docx/xlsx/代码/图片…）生成字段建议
  - 任务后台串行排队，进度可查；建议落入"待审阅"流，逐项 ✓ 应用 / ✗ 驳回 / 全部接受
  - 字段级开关：可针对单字段关闭 LLM 建议，仍把其值作为上下文喂给模型
- **项目导出 / 批量导入**：工具栏 / 右键菜单一键导出项目到本地目录，含 `project.json` /
  `files.json` / `README.md` / `files/`；可选是否把链接模式（🔗）原始文件也复制进去。
  反向操作：把多个项目目录拖到底部 DropZone，可选择"分别建一个项目"，自动识别 `project.json`
  并恢复元数据/字段/标签——构成完整的导出/导入闭环。
- **数据库**：SQLite，便携、零配置

## 运行

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app.main
```

> 需要 Python 3.10+。首次启动会在 `%APPDATA%/LLMCabinet/` 下创建 `cabinet.db` 与 `library/` 目录，可在 **设置 → 项目库** 中查看。

## 数据隐私

LLM Cabinet 是本地应用，所有项目数据仅存于本机。**仅当你主动触发"LLM 元数据建议"时**，才会向你自己配置的 LLM 平台发起请求；细节、可控范围与已知限制请参阅 [PRIVACY.zh-CN.md](PRIVACY.zh-CN.md)（[English](PRIVACY.md)）。

## 配置 LLM

启动后打开 **设置 → API**：
1. 填写所需平台的 `API Key`（其它字段会自动填默认值）
2. 点击 **🔌 测试连接**（仅做 `GET /models` 轻量探测，不消耗推理算力）
3. 设置 **默认启用平台** 与 **默认语言**

之后即可：
- 项目编辑对话框点 **✨ LLM 建议** 按钮
- 或在项目列表右键 → **LLM 元数据建议…**

## 打包为单 exe

```powershell
pip install pyinstaller
pyinstaller -w -F -n "LLM Cabinet" `
  --icon icon.ico `
  --add-data "icon.ico;." `
  --add-data "icon.jpg;." `
  --add-data "PRIVACY.md;." `
  --add-data "PRIVACY.zh-CN.md;." `
  --add-data "app/ui/assets;app/ui/assets" `
  run.py
```

> 仓库根目录已带 `icon.ico`（多分辨率：16/32/48/64/128/256，32-bit RGBA）。如自行替换图标，建议沿用该多尺寸规格以兼顾各种 Windows 视图。

生成的 `dist/LLM Cabinet.exe` 可直接分发。

## 目录结构

```
app/
├── main.py            入口
├── db.py              SQLite 连接与建表 / 迁移
├── models.py          数据类
├── repository.py      数据访问层
├── library.py         仓库目录与文件落地策略
├── exporter.py        项目导出（目录格式 + project.json）
├── importer.py        批量文件夹导入（识别 project.json）
├── utils.py
├── llm/
│   ├── config.py      LLM 配置（providers + 默认值）
│   ├── providers.py   DeepSeek / OpenAI / Gemini / Grok 适配
│   ├── prompts.py     提示词模板
│   ├── context.py     prompt 构造 + 文件文本提取（pdf/xlsx/docx/code/…）
│   └── queue.py       后台任务队列
└── ui/
    ├── main_window.py        三栏主界面（标签树 / 项目列表 / 预览+文件）
    ├── project_dialog.py     项目元数据编辑 + 建议审阅
    ├── llm_suggest_dialog.py LLM 触发对话框（选参考文件、选字段）
    ├── llm_tasks_panel.py    任务队列面板
    ├── export_dialog.py      项目导出对话框
    ├── import_dialog.py      批量文件夹导入对话框
    ├── folder_drop_mode_dialog.py  多文件夹拖入时的"单/多项目"选择
    ├── settings_dialog.py    设置（通用/项目库/视图/字段/API/关于）
    ├── tag_tree.py
    ├── preview.py            图/视频/PDF 内嵌预览
    ├── project_card.py       网格视图模型 + 卡片绘制
    └── widgets.py
```

> 开发期端到端自检脚本见 [`selftests/`](./selftests/README.md)（手动跑，不进 CI）。

## 常见问题

### 首次运行时任务栏图标显示异常

新版本 exe 第一次启动时，Windows 任务栏图标可能短暂显示为默认 / 通用图标，**关闭程序后再打开一次即可恢复正常**，后续运行不会再出现。这是 Windows 图标缓存机制 + PyInstaller 单文件打包的已知现象，不影响功能。

## License

[MIT](LICENSE) © 2026 vortexer99
