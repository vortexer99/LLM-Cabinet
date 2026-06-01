"""task #11 T3 自检：库初始化向导（不依赖真实 LLM）。

覆盖：
  - WIZARDS 注册表正常加载，LibraryInitWizard 元数据合规
  - WizardPlugin.is_available（require_empty_lib / 库非空 时）
  - parse_and_validate 多种边界（合法 / markdown 包裹 / 含前后噪声 / 类型 fallback /
    顶层非对象 / 缺 fields / 空字段名 / 全空）
  - annotate_conflicts 4 种状态（new / system_protected / same_type / type_conflict）
    + same_type 已有 hint vs 空 hint 的 update 行为分流
  - get/set_max_rounds 边界（默认值 / 越界值 fallback / 持久化）
  - LLMProvider.supports_json_mode 默认 True 且四家 provider 全部 True
  - Repository.add_fields_batch 事务化：成功路径返回 id 列表 + 失败路径整体 ROLLBACK
"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selftests._common import T, closing_repos

from app.db import connect
from app.llm.providers import (
    BaseProvider, DeepSeekProvider, GeminiProvider, GrokProvider, OpenAIProvider,
)
from app.repository import Repository
from app.ui.wizards import WIZARDS
from app.ui.wizards.base import WizardMeta, WizardPlugin
from app.ui.wizards.library_init import (
    DEFAULT_MAX_ROUNDS,
    LibraryInitWizard,
    SETTING_MAX_ROUNDS,
    WizardLLMOutputError,
    annotate_conflicts,
    build_messages,
    get_max_rounds,
    parse_and_validate,
    set_max_rounds,
)


def main() -> int:
    t = T()
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpstr:
            tmp = Path(tmpstr)
            _run_all(tmp, t)
    except Exception:
        traceback.print_exc()
        return 1
    ok = t.report()
    return 0 if ok else 1


def _run_all(tmp: Path, t: T) -> None:
    # ----------------------------------------------------------------
    # 阶段 1：注册表 / meta
    # ----------------------------------------------------------------
    t.assert_eq("WIZARDS 包含 1 个向导（library_init）", len(WIZARDS), 1)
    t.assert_true(
        "LibraryInitWizard 在 WIZARDS 中",
        LibraryInitWizard in WIZARDS,
    )
    t.assert_eq(
        "library_init meta.id", LibraryInitWizard.meta.id, "library_init",
    )
    t.assert_eq(
        "library_init meta.category",
        LibraryInitWizard.meta.category,
        "库初始化",
    )
    t.assert_true(
        "WizardMeta 是 frozen dataclass",
        getattr(WizardMeta, "__dataclass_params__").frozen,
    )

    # ----------------------------------------------------------------
    # 阶段 2：Provider supports_json_mode
    # ----------------------------------------------------------------
    t.assert_eq("BaseProvider 默认 supports_json_mode", BaseProvider.supports_json_mode, True)
    for cls in (OpenAIProvider, DeepSeekProvider, GeminiProvider, GrokProvider):
        t.assert_eq(
            f"{cls.__name__}.supports_json_mode = True",
            cls.supports_json_mode, True,
        )

    # ----------------------------------------------------------------
    # 阶段 3：parse_and_validate
    # ----------------------------------------------------------------
    # 3a 合法 JSON
    payload, warns = parse_and_validate(
        '{"fields":[{"name":"子流派","type":"text","prompt_hint":"硬科幻/软科幻"},'
        '{"name":"出版年代","type":"date","prompt_hint":""}],'
        '"default_tags_suggestion":["科幻","翻译"]}'
    )
    t.assert_eq("合法 JSON：fields 数量", len(payload["fields"]), 2)
    t.assert_eq("合法 JSON：第一条 name", payload["fields"][0]["name"], "子流派")
    t.assert_eq("合法 JSON：第一条 type", payload["fields"][0]["type"], "text")
    t.assert_eq("合法 JSON：第一条 hint", payload["fields"][0]["prompt_hint"], "硬科幻/软科幻")
    t.assert_eq("合法 JSON：tags 解析", payload["default_tags_suggestion"], ["科幻", "翻译"])
    t.assert_eq("合法 JSON：无 warning", len(warns), 0)

    # 3b 包 markdown 代码块
    payload2, _ = parse_and_validate(
        "```json\n"
        '{"fields":[{"name":"作者","type":"text"}]}\n'
        "```"
    )
    t.assert_eq("剥 markdown：fields 数量", len(payload2["fields"]), 1)

    # 3c 前后含解释文字（兼容："这是结果：{...}"）
    payload3, _ = parse_and_validate(
        '说明：以下是字段方案。\n{"fields":[{"name":"出版社","type":"text"}]}\n谢谢！'
    )
    t.assert_eq("含前后噪声：fields 数量", len(payload3["fields"]), 1)
    t.assert_eq(
        "含前后噪声：name", payload3["fields"][0]["name"], "出版社",
    )

    # 3d 类型未知 fallback 为 text + warning
    payload4, w4 = parse_and_validate(
        '{"fields":[{"name":"封面","type":"image","prompt_hint":""}]}'
    )
    t.assert_eq("未知 type fallback", payload4["fields"][0]["type"], "text")
    t.assert_true(
        "未知 type 产生 warning",
        any("不合法" in s for s in w4),
    )

    # 3e 错误路径
    def _expect_err(label: str, text: str) -> None:
        try:
            parse_and_validate(text)
        except WizardLLMOutputError:
            t.passed.append(label)
            return
        t.failed.append((label, "未抛 WizardLLMOutputError"))

    _expect_err("空字符串 → 报错", "")
    _expect_err("空白 → 报错", "   \n   ")
    _expect_err("非 JSON → 报错", "你好世界")
    _expect_err("顶层数组 → 报错", '[{"name":"a","type":"text"}]')
    _expect_err("缺 fields 数组 → 报错", '{"foo": 1}')
    _expect_err(
        "fields 全空 → 报错",
        '{"fields":[{"name":"","type":"text"},{"type":"text"}]}',
    )

    # 3f 含部分无效项时仅报 warning（其余仍解析）
    payload5, w5 = parse_and_validate(
        '{"fields":[{"name":"作者","type":"text"},{"name":""},'
        '"非对象",{"type":"text"}]}'
    )
    t.assert_eq("混入坏项：保留有效条目", len(payload5["fields"]), 1)
    t.assert_true("混入坏项：产生多条 warning", len(w5) >= 2)

    # ----------------------------------------------------------------
    # 阶段 4：build_messages 拼装
    # ----------------------------------------------------------------
    msgs1 = build_messages("学术论文", history=[], extra_instruction="")
    t.assert_eq("messages 至少 system+user 两条", len(msgs1), 2)
    t.assert_eq("第 1 条 role=system", msgs1[0]["role"], "system")
    t.assert_eq("第 2 条 role=user", msgs1[1]["role"], "user")
    user_text1 = msgs1[1]["content"][0]["text"]
    t.assert_in("user prompt 含场景描述", "学术论文", user_text1)

    msgs2 = build_messages(
        "学术论文",
        history=[{"content": '{"fields":[{"name":"DOI","type":"url"}]}'}],
        extra_instruction="加一个引用次数字段",
    )
    user_text2 = msgs2[1]["content"][0]["text"]
    t.assert_in("history 文本进入 user prompt", "DOI", user_text2)
    t.assert_in("extra 进入 user prompt", "引用次数", user_text2)

    # ----------------------------------------------------------------
    # 阶段 5：annotate_conflicts
    # ----------------------------------------------------------------
    db = tmp / "task11_t3.db"
    repo = Repository(connect(db))
    with closing_repos(repo):
        # repo 启动后内置 7 个 system fields。再造一个用户字段方便测 same_type / type_conflict
        repo.add_field("子流派", "text", prompt_hint="")  # 用户字段，hint 空
        repo.add_field("阅读状态", "text", prompt_hint="未读/在读/已读")  # hint 非空
        existing = repo.list_fields()

        suggestions = [
            # 全新字段 → ✅ new
            {"name": "ISBN", "type": "text", "prompt_hint": "13 位"},
            # 与系统字段重名 → 🔒 system_protected
            {"name": "标题", "type": "text", "prompt_hint": "覆盖测试"},
            # 同名同类型 + 现有 hint 为空 → 🔁 same_type → update_hint_only
            {"name": "子流派", "type": "text", "prompt_hint": "硬/软"},
            # 同名同类型 + 现有 hint 非空 → 🔁 same_type → action=skip（不覆盖）
            {"name": "阅读状态", "type": "text", "prompt_hint": "新版本说明"},
            # 同名不同类型 → ⚠ type_conflict（默认 rename_to = 原名_v2）
            {"name": "子流派", "type": "rating", "prompt_hint": ""},
        ]
        ann = annotate_conflicts(suggestions, existing)
        t.assert_eq("annotate 总条数", len(ann), 5)

        t.assert_eq("0/ ISBN status=new", ann[0].status, "new")
        t.assert_eq("0/ ISBN selected", ann[0].selected, True)
        t.assert_eq("0/ ISBN action=create", ann[0].action, "create")
        t.assert_eq("0/ ISBN effective_name", ann[0].effective_name, "ISBN")

        t.assert_eq("1/ 标题 status=system_protected", ann[1].status, "system_protected")
        t.assert_eq("1/ 标题 selected=False", ann[1].selected, False)
        t.assert_eq("1/ 标题 action=skip", ann[1].action, "skip")

        t.assert_eq("2/ 子流派(text) status=same_type", ann[2].status, "same_type")
        t.assert_eq("2/ 子流派(text) selected=True", ann[2].selected, True)
        t.assert_eq("2/ 子流派(text) action=update_hint_only", ann[2].action, "update_hint_only")
        t.assert_true(
            "2/ 子流派 拿到现有 field id",
            ann[2].existing_field_id is not None,
        )

        t.assert_eq("3/ 阅读状态 status=same_type", ann[3].status, "same_type")
        t.assert_eq(
            "3/ 阅读状态 现有 hint 非空 → action=skip",
            ann[3].action, "skip",
        )

        t.assert_eq("4/ 子流派(rating) status=type_conflict", ann[4].status, "type_conflict")
        t.assert_eq("4/ 子流派(rating) selected=False（默认）", ann[4].selected, False)
        # 默认 rename 后并未勾选 → action=skip
        t.assert_eq("4/ 默认未勾选 → action=skip", ann[4].action, "skip")
        # 模拟用户勾选并保留默认 rename_to
        ann[4].selected = True
        t.assert_eq(
            "4/ 勾选后 action=create", ann[4].action, "create",
        )
        t.assert_eq(
            "4/ effective_name 走 rename_to",
            ann[4].effective_name, "子流派_v2",
        )
        # 用户清空 rename_to → 退化为 skip（避免误创建无名字段）
        ann[4].rename_to = "   "
        t.assert_eq(
            "4/ rename_to 清空后 action 退化 skip",
            ann[4].action, "skip",
        )

        # ----------------------------------------------------------
        # 阶段 6：get/set_max_rounds 持久化
        # ----------------------------------------------------------
        t.assert_eq(
            "get_max_rounds 默认值",
            get_max_rounds(repo), DEFAULT_MAX_ROUNDS,
        )
        set_max_rounds(repo, 8)
        t.assert_eq("set→get 8", get_max_rounds(repo), 8)
        t.assert_eq(
            "settings 表确实写入",
            repo.get_setting(SETTING_MAX_ROUNDS), "8",
        )
        # 越界值 -> set 内部 clamp
        set_max_rounds(repo, 999)
        t.assert_eq("set 999 被钳位到 20", get_max_rounds(repo), 20)
        set_max_rounds(repo, 0)
        t.assert_eq("set 0 被钳位到 1", get_max_rounds(repo), 1)
        # 手写非法值 -> get fallback
        repo.set_setting(SETTING_MAX_ROUNDS, "abc")
        t.assert_eq(
            "非法字符串 fallback 默认",
            get_max_rounds(repo), DEFAULT_MAX_ROUNDS,
        )
        repo.set_setting(SETTING_MAX_ROUNDS, "0")
        t.assert_eq(
            "越界数字 fallback 默认",
            get_max_rounds(repo), DEFAULT_MAX_ROUNDS,
        )

        # ----------------------------------------------------------
        # 阶段 7：add_fields_batch 事务化
        # ----------------------------------------------------------
        # 7a 成功路径：批量返回 id 列表
        before_n = len(repo.list_fields())
        new_ids = repo.add_fields_batch([
            ("ISBN", "text", "13 位"),
            ("引用次数", "number", "整数"),
        ])
        t.assert_eq("batch 返回 id 数量", len(new_ids), 2)
        t.assert_true(
            "batch 返回 id 全部为 int",
            all(isinstance(i, int) and i > 0 for i in new_ids),
        )
        after_n = len(repo.list_fields())
        t.assert_eq("batch 后字段总数 +2", after_n, before_n + 2)
        names_after = {f.name for f in repo.list_fields()}
        t.assert_in("batch ISBN 已创建", "ISBN", names_after)
        t.assert_in("batch 引用次数 已创建", "引用次数", names_after)

        # 7b 失败路径：第二条 name 为空 → ValueError；第一条插入应被回滚
        before_n2 = len(repo.list_fields())
        before_names = {f.name for f in repo.list_fields()}
        threw = False
        try:
            repo.add_fields_batch([
                ("会议", "text", "主办方/年份"),
                ("", "text", ""),  # 触发 ValueError
            ])
        except ValueError:
            threw = True
        t.assert_eq("空 name 抛 ValueError", threw, True)
        t.assert_eq(
            "失败后字段总数不变（已 rollback）",
            len(repo.list_fields()), before_n2,
        )
        after_names = {f.name for f in repo.list_fields()}
        t.assert_eq(
            "失败后字段名集合不变",
            after_names, before_names,
        )

        # 7c 空入参 → 返回 [] 不 BEGIN
        empty_ids = repo.add_fields_batch([])
        t.assert_eq("空入参返回 []", empty_ids, [])

        # ----------------------------------------------------------
        # 阶段 8：set_field_prompt_hint（同名同类型路径用）
        # ----------------------------------------------------------
        f = next(f for f in repo.list_fields() if f.name == "ISBN")
        repo.set_field_prompt_hint(f.id, "ISBN-13，13 位数字（含 978/979 前缀）")
        f2 = repo.get_field(f.id)
        t.assert_eq(
            "set_field_prompt_hint 后值已更新",
            f2.prompt_hint,
            "ISBN-13，13 位数字（含 978/979 前缀）",
        )

        # ----------------------------------------------------------
        # 阶段 9：is_available
        # ----------------------------------------------------------
        # 当前没有 projects（只加了字段）→ require_empty_lib 默认 False，所以可用
        ok, _ = LibraryInitWizard.is_available(repo)
        t.assert_eq("LibraryInit 默认可用", ok, True)

        # 临时定义一个 require_empty_lib=True 的子类做反向测试
        class _StrictWizard(WizardPlugin):
            meta = WizardMeta(
                id="_strict", title="严格向导", description="",
                require_empty_lib=True,
            )
        ok2, reason2 = _StrictWizard.is_available(repo)
        # 库内一个 project 都没有 → 仍然可用
        t.assert_eq("严格向导：空库 → 可用", ok2, True)
        # 加一个项目
        from app.models import Project
        p = Project(title="测试")
        repo.save_project(p)
        ok3, reason3 = _StrictWizard.is_available(repo)
        t.assert_eq("严格向导：库非空 → 不可用", ok3, False)
        t.assert_in("严格向导：reason 含项目数", "1", reason3)


if __name__ == "__main__":
    sys.exit(main())
