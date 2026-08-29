# 34 · 主题系统统一：颜色主题化 + 浅色主题修复

**工作量**：M
**优先级**：P1（T1 浅色破相 P0）
**状态**：✅ 2026-08-01（v0.6 未发布；按用户决策废弃深色主题，落地为浅色单主题 + palette 色板）

## 来源

2026-07-31 前端评审。深色/浅色双主题名存实亡：大量颜色在 Python 代码里写死深色值，浅色主题下网格卡片、星级、预览、DropZone 全是深色块；两套 QSS 大段重复且已开始漂移（dark 有 `QListView#ProjectGrid::item` 规则，light 没有）。同时设置页自己写着"v0.3.x 之后不再维护深色模式"（`settings_dialog.py:207-214`），与代码现状方向矛盾。

## 现状盘点

| 问题 | 位置 |
|------|------|
| 网格卡片 delegate 全部写死深色（`#25262b/#2b3a55/#4dabf7/#adb5bd`…） | `project_card.py:261-311` |
| StarRating 颜色写死 | `widgets.py:29-33` |
| DropZone 两套样式写死蓝色 | `widgets.py:141-161` |
| ImagePreview / VideoPreview / 占位文案颜色写死（`#1e1e1e/#888/#000/#666`） | `preview.py:28, 70, 179, 235, 244` |
| 搜索错误色写死 `#b00020`（与主题 danger `#fa5252` 不一致） | `main_window.py:2026` |
| 状态栏可点标签 hover 色写死 `rgba(77,171,247,...)` | `main_window.py:1462-1475` |
| light QSS 缺 `QListView#ProjectGrid::item` 规则 | `theme.py`（dark 在 232-243，light 无对应） |
| dark/light 两套 QSS 各 ~200 行结构重复，人工同步 | `theme.py` 全文 |
| 全局 `* { font-size: 13px }` 写死，盖掉代码内 `setPointSize` | `theme.py:23, 413` |

---

## 范围与边界

| 子任务 | 内容 | 优先级 | 工作量 |
|---|---|---|---|
| **T1** | 建统一色板：Python 侧 `palette.py`（dark/light 双份色值），delegate/widget/preview 全部改读色板；修复浅色破相 | P0 | M |
| **T2** | QSS 模板化：dark/light 合并为一份模板 + 色板字典渲染，消除漂移；补齐 light 缺失规则 | P1 | S |
| **T3** | 字号抽变量（`font-size` 进色板/设置），为 #41 字号可调铺路 | P2 | XS |

**不做（本卡内）**：
- 删除深色主题 —— 方向问题见「待澄清」，本卡按"保留双主题"执行
- 自定义强调色/用户配色 —— 远期

---

## T1 · 统一色板

### 方案

新增 `app/ui/palette.py`：

```python
@dataclass(frozen=True)
class Palette:
    bg0: str; bg1: str; bg2: str; bg3: str
    border: str
    fg0: str; fg1: str; fg2: str
    accent: str; accent_hover: str
    select_bg: str; select_fg: str
    warn: str; danger: str

DARK = Palette(...)
LIGHT = Palette(...)

def current() -> Palette:  # 读 settings.theme 返回对应色板
```

改造点：
- `ProjectCardDelegate.paint` 所有 `QColor("#...")` → `palette.current().xxx`
- StarRating / DropZone / ImagePreview 等的内联 stylesheet → 用色板值格式化生成
- 搜索错误色 → `palette.current().danger`
- 主题切换时：delegate 持缓存的色板引用要失效（监听 theme_changed 或每次 paint 读 `current()`，后者更简单，开销可忽略）

### 约束

- 色值先原样搬运，保证 dark 下像素级不变；light 下的新值以"看得清、不刺眼"为准，一次调到位
- QSS 里的颜色本卡不动（T2 统一处理）

## T2 · QSS 模板化

- 把 `QSS_DARK` / `QSS_LIGHT` 合并为一个模板字符串，颜色位用 `{bg0}` 等占位
- `apply_theme(app, name)` 用对应 `Palette` 渲染模板
- 渲染后对比现有两份 QSS 逐条 diff，把已经漂移的差异（如 light 缺 `ProjectGrid::item`、缺 `#SearchBox` 规则）补齐到模板
- 单元自检（selftest）：两个主题渲染结果都不含未替换占位符、不包含 `__ARROW_` 残留

## T3 · 字号变量

- `font-family/font-size` 从 `*` 选择器挪到模板顶部变量
- 基础字号读 setting（默认 13），#41 提供设置 UI 后即时生效

---

## 校验

- [ ] 浅色主题下：网格卡片、星级、预览背景、DropZone、搜索错误色全部浅色协调，无深色色块
- [ ] 深色主题下：与改造前视觉一致（逐屏对比）
- [ ] 切换主题后已打开的窗口/对话框立即全部生效，无残留旧色
- [ ] light/dark 两套 QSS 由同一模板渲染，新增规则只改一处
- [ ] 设置页那句"不再维护深色模式"声明按「待澄清」结论处理

## 依赖

- 为 #40（预览增强）、#41（字号设置）提供色板/变量基础，**建议本卡先行**
- 与 #35（拆分）无强冲突（主要动 theme/widgets/project_card/preview）

## 待澄清

> 卡片正文已按"默认决定"写成可执行状态；**若不同意，请在我编码前告知**。

1. **深色主题的去留**
   - 默认决定：**保留双主题并完成颜色主题化**，同时撤回设置页"不再维护深色"的声明。理由：色板化之后双主题维护成本已经很低；直接删深色会浪费已有的完整 QSS。
   - 若你坚持废弃深色：本卡退化为"硬编码颜色全部换浅色色板 + 删主题切换 + 删 dark QSS"，工作量减半，告诉我即可。
