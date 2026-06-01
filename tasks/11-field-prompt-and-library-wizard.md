# 11 · 字段级 prompt 模板 + 库初始化向导

**工作量**：M（拆为 T1~T4，可分批发版）
**优先级**：T1/T2 = P1，T3/T4 = P2
**状态**：T1 ✅ 2026-06-01 · T2 ✅ 2026-06-01（复用现有"查看 Prompt"对话框） · T3 ✅ 2026-06-01 · T4 ✅ 2026-06-01

> 命名变更：任务卡撰写时使用"向导/库初始化向导"称呼；中期对外文案曾用「库初始化助手」；最终（2026-06-01 晚）对外文案统一为「LLM 助手」/「**库字段设计助手**」（菜单 → 工具 → 🪄 LLM 助手）。内部代码（`WizardPlugin` / `wizards/` / `library_init.py` / `LibraryInitWizard` / `wizard_max_rounds`）仍沿用 wizard / library_init 命名以避免无谓的重命名变更。下文继续保留"向导"的写法作为历史记录。

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

### T3 设计决策（2026-06-01 敲定）

| # | 决策点 | 决定 |
|---|---|---|
| 1 | 入口位置 | **主菜单新增「工具」顶级菜单** → `🪄 向导...` 弹 `WizardListDialog`。`#14` 的"检查库一致性 / 备份此库"等也挂入此菜单。设置 → 通用顶部加一个辅助入口按钮"打开向导..."，不重复实现 |
| 2 | 「再问一轮」对话历史承接 | 在第 4 步预览页提供两个明确按钮：「**重新开始**」（清空状态回到第 2 步）/「**在当前基础上调整 →** 补充：____」（带补充输入框，prompt 拼"上次返回 + 用户编辑 + 用户补充"） |
| 2b | 对话轮数上限 | 默认 **5 轮**；设置 → 通用加 `wizard_max_rounds` spinbox（范围 1~20）。一次完整的「输入 → LLM 返回」算一轮，用户编辑预览不计数。达上限时「在当前基础上调整」按钮 disabled，提示「已达 N/N 轮，请『重新开始』或采用当前结果」。预览页一直显示当前 X/N |
| 3 | LLM 调用走队列还是直调 | **直调 provider，绕过 `LLMTaskQueue`**。向导是前台交互场景，不能等其它后台任务。复用 `repo` 中默认 provider 与配置（不为向导单独配 provider） |
| 4 | 结构化输出 | **按 provider 能力静态路由（不做 try/fallback）**：`LLMProvider` 加属性 `supports_json_mode: bool`，默认 True。向导调用时若 True → 走 `json_mode=True`（甲）；False → 走"prompt 强约束 + 解析"（丙），sys prompt 里塞完整 schema 示例 + "不要输出 markdown 代码块、不要解释、严格输出 JSON"。两路最终都走同一个 `parse_and_validate(text)`，丙路径预处理时多一步剥离 ` ```json ... ``` ` 包裹 |
| 5 | 与现有字段的冲突处理 | 应用前**预检**：把每条 LLM 建议字段名与现有 `fields` 表比对，给四种冲突状态（详见下方"冲突预检"小节）。应用阶段用**事务**（新增 `Repository.add_fields_batch()`），任一字段创建失败 → 整体回滚 → 弹错误指明哪条失败 |

### 设计原则

未来还会有「库优化整理向导」「LLM 字段 batch 重生成向导」等，**入口与框架要可扩展**：

- 入口：主菜单 **「工具」→ `🪄 向导...`**（决策 1）
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

入口（主菜单「工具 → 🪄 向导...」）：

```python
def open_wizards_dialog(self):
    dlg = WizardListDialog(repo=self.repo, library=self.library, parent=self)
    dlg.exec()
```

`WizardListDialog` 按 `category` 分组列出所有注册向导，禁用不满足前置条件的项（如 `require_empty_lib=True` 但库非空）。

### 库初始化向导内容（第一个 WizardPlugin）

多步对话：

1. **引导页**：解释这是干什么的；强调"会调用 LLM；可以随时取消；不会在你点'应用'之前修改库"；展示当前轮数上限
2. **场景描述输入**：用户用自然语言描述使用场景（"我管理学术论文" / "我管理游戏素材" / "我管理菜谱" ...）
3. **LLM 反馈**（异步调用，**直调 provider 不走队列** —— 决策 3）：返回结构化建议
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
   按 provider 能力静态路由 JSON mode / prompt 约束（决策 4），统一走 `parse_and_validate(text)`。
4. **预览 + 编辑页**：表格展示 LLM 给的字段建议，每行可编辑/删除/上下移；**每行带"冲突状态"列**（见下方「冲突预检」小节）。底部两个按钮：
   - 「**重新开始**」→ 清空状态回到第 2 步
   - 「**在当前基础上调整**」→ 弹补充说明输入框；prompt 拼"上次返回 + 用户编辑 + 用户补充"，回到第 3 步
   - 顶部显示 `轮数 X / N`；达上限时上面两个按钮中后者 disabled
   - 折叠区"查看原始响应"展示 LLM 返回的原文（甲乙丙模式都展示，方便排查）
5. **应用页**：点击「应用」→ **以单一事务批量写入 fields 表 + settings**（详见「事务化应用」）；点「取消」全部丢弃

### 冲突预检（决策 5）

第 4 步预览页加载时，把 LLM 建议过一遍 `fields` 表比对，给每条建议一个状态：

| 状态 | 触发条件 | UI 表现 | 应用阶段行为 |
|---|---|---|---|
| ✅ 新字段 | name 在库中不存在 | 默认勾选，正常颜色 | INSERT 一条新字段 |
| 🔒 系统字段名 | name in `PROTECTED_FIELD_KEYS` 对应中文（"标题"/"描述"/"标签"），或与 system field 的 `name` 撞 | 强制不勾选 + disabled，灰色 + tooltip 解释 | 跳过 |
| 🔁 同名同类型 | name 已存在 + type 一致 | 默认勾选，文案"已存在，仅更新 LLM 提示" | 不创建字段；如该字段当前 `prompt_hint` 为空，**写入** LLM 给的 prompt_hint；非空则跳过（不覆盖用户已有配置） |
| ⚠️ 同名不同类型 | name 已存在 + type 不一致 | 默认**不**勾选；行右侧显示「重命名为：[原名_v2]」输入框；用户改完且新名不冲突 → 状态变 ✅，可勾选 | 按用户改后的名字 INSERT |

冲突检测放进 `parse_and_validate` 之后、UI 渲染之前的纯函数：

```python
def annotate_conflicts(
    suggestions: list[dict], existing_fields: list[Field],
) -> list[AnnotatedSuggestion]: ...
```

### 事务化应用（决策 5）

`Repository` 新增批量接口：

```python
def add_fields_batch(
    self,
    fields_data: list[tuple[str, str, str]],   # (name, type, prompt_hint)
) -> list[int]:
    """批量创建字段，单一事务。任一失败抛异常并 ROLLBACK。
    返回新建字段的 id 列表，顺序与入参一致。"""
    cur = self.conn.cursor()
    try:
        cur.execute("BEGIN")
        max_ord = cur.execute(
            "SELECT COALESCE(MAX(ord), -1) AS m FROM fields"
        ).fetchone()["m"]
        new_ids: list[int] = []
        for i, (name, ftype, hint) in enumerate(fields_data):
            cur.execute(
                "INSERT INTO fields(name, type, ord, visible, key, prompt_hint) "
                "VALUES(?, ?, ?, 1, NULL, ?)",
                (name.strip(), ftype, max_ord + 1 + i, hint or ""),
            )
            new_ids.append(cur.lastrowid)
        self.conn.commit()
        return new_ids
    except Exception:
        self.conn.rollback()
        raise
```

向导 T1 已经给 `fields` 加了 `prompt_hint` 列，本接口直接利用。

T3 应用流程：

```python
# 1. 收集勾选的字段（含同名同类型的"仅更新 hint"项分流）
to_create = [s for s in checked if s.action == "create"]
to_update_hint = [s for s in checked if s.action == "update_hint_only"]

# 2. 批量创建
try:
    new_ids = repo.add_fields_batch([(s.name, s.type, s.hint) for s in to_create])
except Exception as e:
    QMessageBox.critical(self, "应用失败",
        f"创建字段时出错，已回滚：\n{e}")
    return  # 不关闭向导，让用户检查冲突状态

# 3. 单独更新已存在字段的 hint
for s in to_update_hint:
    repo.set_field_prompt_hint(s.existing_field_id, s.hint)

# 4. 默认标签
if user_chose_to_apply_tags:
    for tag in tags_to_create:
        ...
```

### LLM 调用实现要点

```python
# app/ui/wizards/library_init.py
def call_wizard_llm(repo, prompt: str, history: list[dict]) -> dict:
    config = load_config(repo)
    provider = create_provider(config.default_provider, config)
    messages = build_wizard_messages(prompt, history, provider.supports_json_mode)
    if provider.supports_json_mode:
        resp = provider.chat(messages, json_mode=True, timeout=120.0)
    else:
        resp = provider.chat(messages, timeout=120.0)
    return parse_and_validate(resp.text)
```

`parse_and_validate` 的职责：

- 去除 ` ```json ... ``` ` markdown 包裹（仅丙路径会出现，但兼容处理）
- `json.loads`，失败 → 抛 `WizardLLMOutputError("模型输出不规范，请重试")`
- 校验顶层 `fields` 是 list；每个 item 含 `name` (str) / `type` (str)；`type` 不在 `FIELD_TYPES` 白名单时 fallback 为 `text` 并加 warning
- 返回结构化 dict + warning 列表

### `LLMProvider` 改造（决策 4）

```python
class LLMProvider(ABC):
    supports_json_mode: bool = True   # 默认 True，子类可覆盖
    ...
```

四家现状：

| Provider | supports_json_mode |
|---|---|
| DeepSeek | True |
| OpenAI | True |
| Gemini | True（用 `responseMimeType`） |
| Grok | True（OpenAI 兼容） |

未来添新 provider 时按实际 API 能力填即可。

### 数据落地

- 新字段：直接 INSERT 到 `fields`，`prompt_hint` 取 LLM 建议
- 默认标签：可选，由用户决定是否一起创建
- 向导本身不需要新表；如要记录"是否运行过初始化向导"，加个 setting `library_init_wizard_done = "1"`
- `wizard_max_rounds`（默认 "5"）作为 setting 持久化

### 验收（T3）

- [ ] 主菜单出现「工具 → 🪄 向导...」入口；设置 → 通用顶部也有"打开向导..."辅助按钮
- [ ] 向导列表对话框正确按 category 分组
- [ ] 库初始化向导：库非空时显示「⚠ 当前库已有 N 个字段，运行向导仍会追加而非替换」
- [ ] 输入场景 → 调用 LLM → 收到建议；网络/key 错误时友好提示
- [ ] 预览页可编辑/删除/排序字段建议
- [ ] 「重新开始」清空状态 + 计数归零
- [ ] 「在当前基础上调整」+ 补充说明 → 历史正确承接，模型有响应
- [ ] 当前轮数 / 上限 一直显示在预览页
- [ ] 达到 `wizard_max_rounds` 上限 → 「在当前基础上调整」按钮 disabled，提示文案出现
- [ ] 设置 → 通用 修改 `wizard_max_rounds` 后立即生效
- [ ] 「应用」后 `fields` 表正确写入，关闭向导回到主界面字段列表已更新
- [ ] 「取消」不留下任何变更
- [ ] LLM 调用**不**进 `mcp_audit` / `llm_tasks` 表（直调路径），但保留独立轻量日志（`wizard_session.json` 临时）便于调试
- [ ] 模型返回非合法 JSON / 字段类型不在白名单 → 友好错误提示 + 折叠区显示原始响应
- [ ] 后续新增 `library_tidy.py` 只需在 `WIZARDS` 注册即可出现在列表，不动主流程代码
- [ ] **冲突预检**：
  - [ ] LLM 建议含与现有字段同名同类型 → 行显示 🔁 + "已存在，仅更新 LLM 提示"
  - [ ] LLM 建议含 `标题/描述/标签` 等系统字段名 → 行显示 🔒 + 强制不勾选 + 灰色
  - [ ] LLM 建议含同名不同类型 → 行显示 ⚠️ + 重命名输入框；用户改名后变 ✅ 可勾选
  - [ ] LLM 建议全部新字段 → 全部 ✅ 默认勾选
- [ ] **事务化应用**：
  - [ ] 故意构造一条会让 `add_fields_batch` 失败的输入（如手动改成空字符串）→ 应用失败 → 弹窗显示原因 → 库内字段表**完全无变化**（已回滚）→ 向导停留在预览页可继续修改
  - [ ] 同名同类型分支：现有字段 `prompt_hint` 为空 → 写入 LLM 提示；现有 `prompt_hint` 非空 → 不覆盖

## T4：导出/导入携带 prompt_hint

- 修改 `tasks/09` 的 `project.json` schema → bump 到 `llm-cabinet/project-export@2`，`fields_snapshot` 每项追加 `prompt_hint`
- `tasks/10` 导入器同步识别新字段；老 schema (`@1`) 兼容（`prompt_hint` 视为空）

## 风险

- **LLM 给的 schema 不可靠**：必须有可视化预览 + 用户编辑步骤，不允许"一键应用 LLM 输出"
- **字段类型枚举**：LLM 可能产出未知 type，加白名单过滤（与 `app/models.py` 的字段类型对齐）
- **向导框架过度设计**：T3 的 `WizardPlugin` 框架本身工作量很小（一个基类 + 注册表），但要克制——别现在就为还没出现的向导加配置/钩子机制，YAGNI
- **prompt_hint 太长导致 token 超限**：拼装时单字段 hint 截断到 N 字符（默认 500），整体 prompt 用现有截断逻辑兜底
- **不支持 json_mode 的 provider 输出会带 markdown 包裹**：`parse_and_validate` 必须先剥 ` ```json ... ``` `；如果模型仍输出非 JSON 文本，给清晰错误而非崩溃
- **轮数上限设得过小**：`wizard_max_rounds=1` 时用户可能体验差（一次没满意就只能重来）；spinbox 最小值锁 1，但默认 5 已经够宽松
- **同名不同类型的"重命名"误用**：用户可能改成与第三方字段同名 → 实时校验输入框，冲突时再次标 ⚠️ 并禁用勾选
- **事务里串行 INSERT 性能**：单次最多 ~20 个字段，无性能压力；用一次 BEGIN/COMMIT 即可

## 依赖

- T1 不依赖任何其它 task
- T3 软依赖 T1（向导生成的字段需要写入 `prompt_hint`）
- T4 依赖 T1 + `tasks/09`（已完成） + `tasks/10`（待做）

## 工作量拆分

| 子项 | 估算 |
|---|---|
| T1 数据迁移 + 字段编辑 UI + prompt 拼装 | 0.5 天 |
| T2 Prompt 可视化 | 0.2 天 |
| T3 向导框架 + 库初始化向导（含 supports_json_mode、轮数上限 UI、parse_and_validate、冲突预检 + 事务化应用） | 2.2 天 |
| T4 导入导出携带 prompt_hint | 0.2 天 |
| **合计** | ~3.1 天（M） |

## 后续扩展

- **库维护向导**：扫描"哪些字段填充率低 / 哪些项目缺关键字段"，给出补全建议
- **字段批量重生成向导**：选中 N 个项目 + M 个字段 → 用统一 prompt 批量调用 LLM
- **向导市场**：用户可分享向导脚本（远期、需要安全沙箱）
