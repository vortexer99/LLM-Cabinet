# 16 · 库字段设计助手后续打磨

**工作量**：XS+S+S
**优先级**：T1 = P2，T2 = P1，T3 = P1
**状态**：T1 ✅ 2026-06-01 · T2 ✅ 2026-06-01 · T3 ✅ 2026-06-01

> 续接 task #11 T3「库字段设计助手」（2026-06-01 已完成首版）。本卡专门收集
> 助手投入使用后用户反馈的若干打磨项；不重写也不引入大改动，目标是**让现有
> 交互更准确、不留歧义**。

## 来源

用户使用「库字段设计助手」过程中提出的细节问题（2026-06-01 晚反馈）：

1. 「填写库描述」时缺乏**追加修改意见**的引导，用户不知道可以把"在已有字段
   基础上进一步修改"的诉求直接追加到库描述末尾发给 LLM
2. LLM 返回建议后，**库描述这一项没有批准 / 驳回入口** — 字段建议有，但库描述
   被默默覆盖到 `_library_desc_suggested` 里，用户即便不想要也无法一键还原
3. 现状下 LLM 实际上**无法主动建议删除字段**：助手把"LLM 输出 fields 列表中
   未提及现有字段"统一标为 `existing_user_field`（"现有字段，LLM 本次未提及，
   默认保留"），语义模糊（LLM 是想删？是忘了？是觉得保留就行？），且这种
   "隐式删除候选"也没有批准 / 驳回按钮可以走

## 目标

把上述三个细节交互打磨到位：

- 让用户在写库描述时清楚"可以在末尾追加修改意见"
- 让库描述像字段一样有**批准 / 驳回**入口
- 让 LLM 真正能**显式**给出"建议删除某字段"的建议，并以专门状态走批准 / 驳回流程

## 范围与边界

| 子任务 | 内容 | 优先级 | 工作量 |
|---|---|---|---|
| **T1** | 场景页库描述输入框增加引导提示文案 | P2 | XS |
| **T2** | 预览页库描述加批准 / 驳回按钮（与字段建议同语义：批准固化、驳回还原到 LLM 之前的版本） | P1 | S |
| **T3** | LLM 显式删除建议：扩展 prompt 让 LLM 在 JSON 里输出 `fields_to_delete: [...]`；新增 `AnnotatedSuggestion.status="llm_suggest_delete"`；UI 给红字状态 + 批准 / 驳回 | P1 | S |

T1 / T2 / T3 互不依赖，可分别推进。

**不做（本任务内）**：

- 库描述的版本历史 / diff 视图（远期，单独 task）
- 让 LLM 主动建议"重命名"现有字段（语义复杂，需要 mapping；不是高频需求，单独评估）

## T1：库描述输入框追加修改意见引导

### 现状

`app/ui/wizards/library_init.py` 场景页（`_build_scenario_page`）顶部 label 写：

```
这个库是干什么的 / 你希望它怎么管理？包括内容类型、字段偏好、特殊约定等，
越具体越好。（这段会作为「库描述」一并发给 LLM，LLM 会基于它完善并给出字段建议。）
```

但用户在第二轮调用助手（已经有了一套字段、想"把字段 X 改成 Y"或"删掉字段 Z"）时
不知道该怎么操作 — 实际上**追加在库描述末尾即可**，因为整段会发给 LLM 当
"使用场景描述 + 库描述"上下文。

### 改动

label 文案补一句提示：

```
这个库是干什么的 / 你希望它怎么管理？包括内容类型、字段偏好、特殊约定等，
越具体越好。
（这段会作为「库描述」一并发给 LLM，LLM 会基于它完善并给出字段建议。
如果是基于当前字段结构进一步修改，可以把修改意见追加在库描述的末尾，
它会一起发送给 LLM 进行处理。）
```

或者拆成两段更清楚：主提示 + `hint` 灰字小提示（参考 `prompts.py` 中其它
hint 标签的样式）。

### 自检

无需新增；纯文案修改不触发任何代码路径变化。

## T2：库描述的批准 / 驳回

### 现状

`_render_preview` 里把 LLM 给的 `library_description` 同步到
`ed_preview_library_desc`（多行文本框），用户可以编辑、也可以原样接受、还可以
彻底清空。但**无法**一键"驳回，把 LLM 改之前的版本（用户输入的那段）拿回来"。

### 改动

预览页"库描述"区域的 label 行右侧加一对小按钮（与字段表里的批准 / 驳回风格一致）：

```
库描述（LLM 完善后；可继续编辑后再「应用」）         [批准] [驳回]
┌──────────────────────────────────────────────┐
│ ……LLM 完善后的描述……                            │
└──────────────────────────────────────────────┘
```

按钮逻辑：

* **批准**：把 `_library_desc_suggested` 固定为当前编辑框内容；按钮变成"已批准"
  状态（与字段批准列一致）
* **驳回**：把 `_library_desc_suggested` 与编辑框还原为 `_library_desc_input`
  （即用户在场景页最初的输入）；按钮变成"已驳回"
* 仅当 LLM 这一轮**实际改过**库描述（`_library_desc_suggested != _library_desc_input`）
  时才显示这对按钮；否则隐藏（避免无意义的 noop 决策）
* 用户后续手改编辑框 → 决策回到 pending（与字段双击 hint 后的处理一致）
* `_collect_user_edited_payload` / `_on_apply` 里写库的逻辑不变（始终以
  `_library_desc_suggested` 落库）

### 实现要点

* 新增实例属性 `self._library_desc_decision: str = "pending"`，与字段的 `decision` 同语义
* 新增 `_make_library_desc_decision_buttons()` 辅助函数
* `_render_preview` 末尾根据 `_library_desc_input` vs `_library_desc_suggested`
  决定按钮组的可见性
* "再次应用 LLM 建议"路径（`_on_reapply_llm`）也要把 desc 决策回到 pending

### 自检

`selftests/task16_*.py`（或追加到 `task11_t3_library_init_wizard.py`）：

* `_library_desc_input` == `_library_desc_suggested` → 无按钮显示
* 批准 → 状态固化；后续编辑回到 pending
* 驳回 → `_library_desc_suggested` 还原到 `_library_desc_input`
* 「再次应用」后决策回到 pending

## T3：LLM 显式删除建议

### 现状（问题剖析）

LLM 当前**不会**主动告诉用户"建议删除字段 X"。`annotate_conflicts` 第一遍
扫现有字段时，对"LLM 输出 fields 中**没有**这个名字"的现有字段一律标
`existing_user_field` + reason="现有字段，LLM 本次未提及。默认保留；
取消勾选则在「应用」时**删除**该字段。"

这种"隐式删除候选"有两个问题：

1. **语义模糊**：用户看到"LLM 本次未提及"——但不知道这是"LLM 想删"还是
   "LLM 觉得保留就行只是没在 fields 里重述" — 实际上 LLM 大部分情况是后者
2. **无批准/驳回入口**：只能用左侧勾选取消保留来删除，节奏上跟字段建议的
   批准/驳回不统一

### 改动

#### 3.1 Prompt 升级

让 LLM 在输出里**显式**列出"建议删除"的字段名。`_SYSTEM_PROMPT` 加一条：

```json
{
  "library_description": "...",
  "fields": [...],          ← LLM 认为应该有的字段（保留 + 新增 + 修改）
  "fields_to_delete": [     ← 新增：LLM 主动建议删除的现有字段（可选，默认空）
    {"name": "字段名", "reason": "为什么建议删除（一句话）"}
  ]
}
```

约束：
- **仅当**该字段在"当前库已存在的字段"列表里且与场景明显无关时才放进
  `fields_to_delete`；其它现有字段默认保留（不出现在 `fields_to_delete` 里
  也不出现在 `fields` 里都视为"保留"）
- LLM 必须给 `reason`，**不要**只给名字

#### 3.2 数据层

* `parse_and_validate`：解析 `fields_to_delete`，过滤掉 name 为空 / reason
  缺失的项；放进 `payload["fields_to_delete"]`
* `annotate_conflicts(suggestions, existing_fields, suggested_deletes=None)`：
  新增第三个参数；遍历现有字段时，如果 `name in suggested_deletes_by_name`
  且 LLM 没在 fields 里提到 → 设 `status="llm_suggest_delete"`
  + `reason=<LLM 给的删除理由>` + `selected=True` + `llm_touched=True`
* `AnnotatedSuggestion.status` 新增枚举值 `"llm_suggest_delete"`：
  * `action`：批准 → `delete`；驳回 → `keep`；pending → `keep`（保守默认）
  * `llm_change_label`："已批准" / "已驳回" / "待删除"（pending 时）/ "已删除"（批准后或现 existing_user_field 等价路径）
  * `has_llm_change`: True（始终触发批准 / 驳回按钮）

#### 3.3 UI

* 状态列文字：`🗑 LLM 建议删除`，红字
* `reason` 列表 tooltip：显示 LLM 给的删除理由原文
* 批准 / 驳回按钮：
  * **批准** → 等价当前"取消保留"路径（`selected=False` + 状态变红字"将删除"）
  * **驳回** → 等价"保留"，状态变成普通的"📝 现有字段"，selected=True
  * 一旦决策按钮就消失（与字段建议保持一致）
* 应用阶段（`_on_apply`）：批准的 `llm_suggest_delete` 加入 `to_delete`
  名单，走 `apply_field_plan_batch.deletes`；二次确认对话框里把这些与
  "用户主动取消保留的字段"合并显示

#### 3.4 兼容旧 LLM 输出

LLM 老 prompt（不知道 `fields_to_delete` 字段）→ 不输出该字段 → `payload`
里没这个键 → 走老路径（所有未提及的现有字段标 `existing_user_field`）。
新旧无缝兼容。

### 自检

`selftests/task16_*.py`（或追加到 `task11_t3_library_init_wizard.py`）：

* `parse_and_validate` 解析合法 `fields_to_delete`：含 reason 的保留，缺
  reason 或 name 空的过滤；产生 warning
* `annotate_conflicts` 在 `suggested_deletes` 命中时给出 `llm_suggest_delete`
  状态；`reason` 取自 LLM 而非默认文案
* `action` 路径：pending → `keep`；approved → `delete`；rejected → `keep`
* 与现有"用户主动取消保留" `existing_user_field` 路径不冲突（两个独立 status）
* 批量应用：批准的 LLM 删除建议进入 `apply_field_plan_batch.deletes`

## 验收标准

- T1：场景页 label 文案补充到位，明确告知用户"修改意见可以追加到库描述末尾"
- T2：库描述区域出现批准 / 驳回按钮（仅在 LLM 实际改过描述时显示），逻辑与
  字段批准 / 驳回一致；驳回能还原到用户原始输入；自检通过
- T3：LLM 真正能主动给删除建议；UI 用红字"🗑 LLM 建议删除"显示，附 LLM 的
  删除理由 tooltip；批准 / 驳回按钮工作正常；与既有"用户主动删除"路径并存不
  冲突；旧 LLM 输出（无 `fields_to_delete`）兼容；自检通过

## 完成时间

- T1：✅ 2026-06-01
- T2：✅ 2026-06-01
- T3：✅ 2026-06-01
