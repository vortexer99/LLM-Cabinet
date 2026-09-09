# 42 · API Key 安全存储

**工作量**：S
**优先级**：P2
**状态**：✅ 2026-08-01（v0.6 未发布）

## 来源

2026-07-31 前端评审。LLM API Key 目前在设置页以 Password 模式输入（`settings_dialog.py:889-902`），但保存时随 `llm_config` JSON 明文写入 `cabinet.db` 的 settings 表。库目录同步/备份/分享时 key 会跟着明文走；`PRIVACY.md` 对此也没有明确说明。

## 范围与边界

| 子任务 | 内容 | 工作量 |
|---|---|---|
| **T1** | keyring 存储 API Key（Windows 凭据管理器），settings 只留引用标记；明文自动迁移 | S |
| **T2** | UI 告知 + PRIVACY 文档同步 | XS |

**不做（本卡内）**：
- 加密整个 cabinet.db —— 超出范围，远期
- 多用户/权限模型 —— 无此需求

---

## T1 · keyring 存储

### 方案

- `requirements.txt` 增加 `keyring`（Windows 下走 Credential Manager，无需额外服务）
- 存储映射：keyring service = `llm-cabinet`，username = `{library_root_hash}:{provider_id}`
  - 按库隔离（不同库可用不同 key，与现状一致）
- `llm_config` JSON 中 `api_key` 字段改为哨兵值 `"keyring:1"`；
  - 读：见哨兵 → keyring 取；取不到（凭据被清/换机器）→ 视为未配置，UI 提示重新填写
  - 写：设置页输入 → 写 keyring → settings 写哨兵
- **迁移**：启动时（或首次读写 llm_config 时）发现明文 key → 自动写入 keyring 并替换为哨兵，静默完成
- keyring 不可用（极罕见，如精简系统）→ 回退明文存储 + 设置页显示"⚠ 当前系统不支持凭据管理器，API Key 将以明文保存在库文件中"

### 涉及点

- `app/llm/config.py`：`load_config` / `save_config` 增加 keyring 读写层
- `settings_dialog.py` API 页：输入框语义不变；测试连接读法不变
- `main_window.py` 的「从其它库导入 API 配置」（`_lib_import_api`）：keyring 值不能跨库复制 → 导入后目标库同样引用本机 keyring（同机 OK）；跨机器迁移需用户重新填 key（导入反馈里说明）

## T2 · 告知与文档

- 设置页 API 区加一行说明："根据当前配置实际保存结果提示凭据引用或明文回退"
- `PRIVACY.md` / `PRIVACY.zh-CN.md` 补充密钥存储方式段落
- 备份/恢复文档（`docs/`）说明：备份 zip 不含 API Key，恢复后需重新填写（同机也需重填）

---

## 校验

- [ ] 填写 key → 当前 llm_config 中不出现明文，keyring 中有对应条目
- [ ] 老库（明文 key）启动后自动迁移：功能不断、当前 llm_config 中明文替换为引用，源库历史可能留存
- [ ] 清空 Windows 凭据后：LLM 功能按"未配置"提示，设置页可重填
- [ ] 备份库 zip 内不含 key；恢复到新目录后按预期提示重填（同机也需重填）
- [ ] 从其它库导入 API 配置：base_url/model/默认值导入，key 按 keyring 语义处理且反馈文案说明
- [ ] PRIVACY 两份文档更新

## 依赖

- 无强依赖；与 #37（反馈统一）的文案风格保持一致

## 待澄清

> 卡片正文已按"默认决定"写成可执行状态；**若不同意，请在我编码前告知**。

1. **是否引入 `keyring` 依赖**
   - 默认决定：**引入**。Windows 桌面场景下 keyring 后端成熟（ Credential Manager），用户零配置。
   - 若你不想加依赖：本卡退化为"UI 明确告知明文存储 + PRIVACY 写明 + 备份时提示剔除"，工作量 XS。

## 2026-09-09 回归修复

- [x] 备份快照移除配置密钥并排除历史日志；凭据失败/恢复的设置提示即时刷新。
- [x] 已加入 `selftests/task42_security_regressions.py` / `selftests/gui_release_regressions.py` 对应自动回归。
