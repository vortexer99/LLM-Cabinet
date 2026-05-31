# 11 · 字段级 prompt 模板 + 库初始化向导

**工作量**：M（拆为 T1~T4，可分批发版）
**优先级**：T1/T2 = P1，T3/T4 = P2
**状态**：待做

## 来源

`TODO.md → 🤖 LLM 工作流`：

- 「自定义 LLM prompt 模板，每个字段单独给出建议使用的格式」
- 「初始化库时，通过和 LLM 交流，规划项目的字段结构，标题、描述等字段的格式（形成专属的 prompt 模板）」

## 目标

把"该字段的产出长什么样"这件事**沉淀到字段定义里**，并在 LLM 调用时按目标字段拼装到 prompt。
进一步，让用户能通过**对话式向导**让 LLM 帮忙规划库的字段结构与各字段的格式说明。

## 范围与边界

本任务拆为 4 个子任务，可分批落地：

| 子任务 | 内容 | 优先级 | 工作量 |
|---|---|---|---|
| **T1** | 字段表新增 `prompt_hint` 列；设置 → 字段编辑增加输入框；prompt 拼装时按目标字段注入 | P1 | S |
| **T2** | 「查看 Prompt」对话框可视化拼接结果（已有 prompt 子串 + 字段 hint 注入区分高亮） | P1 | XS |
| **T3** | 库向导框架（菜单入口 + 多向导可扩展）+ 第一个向导：字段结构规划 | P2 | M |
| **T4** | 项目导入/导出（#09/#10）携带 `prompt_hint`，跨库迁移保留 LLM 配置 | P2 | XS |

T1/T2 可独立发版，不依赖 T3/T4。

## T1：字段级 prompt 模板

### 数据迁移

`fields` 表新增列：

```sql
ALTER TABLE fields ADD COLUMN prompt_hint TEXT NOT NULL DEFAULT '';
```

写一条新的 schema 迁移（参照 `app/db/migrations/`），bump `user_version`。

### 字段编辑 UI

在「设置 → 字段」对话框里，每个字段一行已有的：名称 / 类型 / 排序，
新增一栏可点击展开的 **「LLM 提示」**（`QPlainTextEdit`，多行，可空）。

- 占位文：`留空 = 使用全局默认；填写 = 该字段在 LLM 建议时附加这段格式说明`
- 示例（标题字段）：`不超过 30 字；不要带书名号；体现作品类型与核心主题。`
- 示例（描述字段）：`200~400 字；先一句概括，再分段说明背景/亮点/适用人群；不使用 emoji。`

### prompt 拼装改造

`app/llm/context.py` 里组装 user prompt 时，对每个 **target_field** 检查其 `prompt_hint`：

- 非空 → 在 `context_fields_desc` 区段把该字段的格式描述替换 / 追加为用户自定义内容
- 空 → 沿用全局默认描述

策略默认 = **追加**（不覆盖默认描述，避免用户写得太短反而丢上下文）；
设置 → 通用 加一个开关「字段 prompt 模式：追加 / 覆盖」（持久化到 settings）。

### 验收（T1）

- [ ] 新建库 / 升级老库后，`fields.prompt_hint` 列存在，默认空
- [ ] 设置 → 字段：编辑某字段的 LLM 提示，保存后再打开值还在
- [ ] 触发 LLM 建议，包含该 target_field → 在「查看 Prompt」里能看到提示已注入
- [ ] 切换"追加 / 覆盖"模式，prompt 文本对应变化
- [ ] 字段被删除 → 关联 prompt_hint 一起删（沿用现有 ON DELETE CASCADE 即可）

## T2：Prompt 拼接可视化

`app/ui/llm_tasks_panel.py` 现有「🔍 查看 Prompt / 原始响应」对话框扩展：

- `[User Prompt]` 段把"字段 hint 注入区"用一个可折叠/带颜色标签包起来（例如行首 `[hint:字段名]`），方便用户判断 hint 是否被正确拼上去
- 新增一个 tab「字段 hint 一览」：表格展示本次任务每个 target_field 是否注入了 hint、注入的全文是什么

低成本，主要为 T1 的 debug 服务。

## T3：库初始化向导（含扩展性框架）

### 设计原则

未来还会有「库优化整理向导」「LLM 字段 batch 重生成向导」等，**入口与框架要可扩展**：

- 入口统一在 **「设置 → 通用」** 顶部 / 或 **「项目」菜单 → 向导」** 子菜单（二选一，建议菜单方式更显眼）
- 每个向导一个 `WizardPlugin` 子类，模块自注册到 `app/ui/wizards/__init__.py`
- 向导列表对话框：图标 + 标题 + 一句话描述 + 「启动」按钮，按 `category` 分组（`库初始化` / `库维护` / `LLM 优化` ...）

### 框架代码骨架

```
app/ui/wizards/
├── __init__.py            ← 注册表：WIZARDS: list[type[WizardPlugin]]
├── base.py                ← class WizardPlugin(QDialog) + meta
├── library_init.py        ← 本 task 的"库初始化向导"实现
└── (future) library_tidy.py / field_batch_refine.py / ...
```

```python
# base.py
@dataclass
class WizardMeta:
    id: str                  # "library_init"
    title: str               # "库初始化向导"
    description: str         # 一句话描述
    category: str            # "库初始化" / "库维护" / ...
    icon: str = "🪄"
    require_empty_lib: bool = False   # 仅在库为空时可用

class WizardPlugin(QDialog):
    meta: WizardMeta = ...
    def run(self, repo, library) -> bool: ...   # 返回是否实际应用了变更
```

入口（主菜单或设置面板）：

```python
def open_wizards_dialog(self):
    dlg = WizardListDialog(repo=self.repo, library=self.library, parent=self)
    dlg.exec()
```

`WizardListDialog` 按 `category` 分组列出所有注册向导，禁用不满足前置条件的项（如 `require_empty_lib=True` 但库非空）。

### 库初始化向导内容（第一个 WizardPlugin）

多步对话：

1. **引导页**：解释这是干什么的；强调"会调用 LLM；可以随时取消；不会在你点'应用'之前修改库"
2. **场景描述输入**：用户用自然语言描述使用场景（"我管理学术论文" / "我管理游戏素材" / "我管理菜谱" ...）
3. **LLM 反馈**（异步调用）：返回结构化建议
   ```json
   {
     "fields": [
       {"name": "标题", "type": "text", "prompt_hint": "..."},
       {"name": "作者", "type": "text", "prompt_hint": "..."},
       {"name": "评分", "type": "rating", "prompt_hint": ""},
       ...
     ],
     "default_tags_suggestion": ["科幻", "翻译", ...]
   }
   ```
4. **预览 + 编辑页**：表格展示 LLM 给的字段建议，每行可编辑/删除/上下移；底部「再问一轮」回到第 2 步带上历史
5. **应用页**：点击「应用」→ 实际写入 fields 表、settings；点「取消」全部丢弃

### 数据落地

- 新字段：直接 INSERT 到 `fields`，`prompt_hint` 取 LLM 建议
- 默认标签：可选，由用户决定是否一起创建
- 向导本身不需要新表；如要记录"是否运行过初始化向导"，加个 setting `library_init_wizard_done = "1"`

### 验收（T3）

- [ ] 「设置 → 通用」顶部 / 或菜单出现「🪄 向导...」入口
- [ ] 向导列表对话框正确按 category 分组
- [ ] 库初始化向导：库非空时显示「⚠ 当前库已有 N 个字段，运行向导仍会追加而非替换」
- [ ] 输入场景 → 调用 LLM → 收到建议；网络/key 错误时友好提示
- [ ] 预览页可编辑/删除/排序字段建议
- [ ] 「应用」后 `fields` 表正确写入，关闭向导回到主界面字段列表已更新
- [ ] 「取消」不留下任何变更
- [ ] 后续新增 `library_tidy.py` 只需在 `WIZARDS` 注册即可出现在列表，不动主流程代码

## T4：导出/导入携带 prompt_hint

- 修改 `tasks/09` 的 `project.json` schema → bump 到 `llm-cabinet/project-export@2`，`fields_snapshot` 每项追加 `prompt_hint`
- `tasks/10` 导入器同步识别新字段；老 schema (`@1`) 兼容（`prompt_hint` 视为空）

## 风险

- **LLM 给的 schema 不可靠**：必须有可视化预览 + 用户编辑步骤，不允许"一键应用 LLM 输出"
- **字段类型枚举**：LLM 可能产出未知 type，加白名单过滤（与 `app/models.py` 的字段类型对齐）
- **向导框架过度设计**：T3 的 `WizardPlugin` 框架本身工作量很小（一个基类 + 注册表），但要克制——别现在就为还没出现的向导加配置/钩子机制，YAGNI
- **prompt_hint 太长导致 token 超限**：拼装时单字段 hint 截断到 N 字符（默认 500），整体 prompt 用现有截断逻辑兜底

## 依赖

- T1 不依赖任何其它 task
- T3 软依赖 T1（向导生成的字段需要写入 `prompt_hint`）
- T4 依赖 T1 + `tasks/09`（已完成） + `tasks/10`（待做）

## 工作量拆分

| 子项 | 估算 |
|---|---|
| T1 数据迁移 + 字段编辑 UI + prompt 拼装 | 0.5 天 |
| T2 Prompt 可视化 | 0.2 天 |
| T3 向导框架 + 库初始化向导 | 1.5 天 |
| T4 导入导出携带 prompt_hint | 0.2 天 |
| **合计** | ~2.4 天（M） |

## 后续扩展

- **库维护向导**：扫描"哪些字段填充率低 / 哪些项目缺关键字段"，给出补全建议
- **字段批量重生成向导**：选中 N 个项目 + M 个字段 → 用统一 prompt 批量调用 LLM
- **向导市场**：用户可分享向导脚本（远期、需要安全沙箱）
