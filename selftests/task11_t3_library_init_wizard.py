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
    # 3a 合法 JSON（标签分类策略已合并进标签字段 hint，不再有独立 tag_axes）
    payload, warns = parse_and_validate(
        '{"fields":[{"name":"子流派","type":"text","prompt_hint":"硬科幻/软科幻"},'
        '{"name":"出版年代","type":"date","prompt_hint":""}]}'
    )
    t.assert_eq("合法 JSON：fields 数量", len(payload["fields"]), 2)
    t.assert_eq("合法 JSON：第一条 name", payload["fields"][0]["name"], "子流派")
    t.assert_eq("合法 JSON：第一条 type", payload["fields"][0]["type"], "text")
    t.assert_eq("合法 JSON：第一条 hint", payload["fields"][0]["prompt_hint"], "硬科幻/软科幻")
    t.assert_eq("合法 JSON：无 warning", len(warns), 0)
    t.assert_true(
        "合法 JSON：未给 library_description 时不在 payload 里",
        "library_description" not in payload,
    )
    t.assert_true(
        "合法 JSON：payload 不含 tag_axes 键（已废弃）",
        "tag_axes" not in payload,
    )

    # 3a-1 LLM 误返独立 tag_axes：被静默丢弃 + 给出 warning
    payload_legacy_axes, w_legacy_axes = parse_and_validate(
        '{"fields":[{"name":"标签","type":"tags","prompt_hint":"3~6 个"}],'
        '"tag_axes":[{"name":"研究对象","examples":["人物","事件"]}]}'
    )
    t.assert_true(
        "误返 tag_axes：payload 不含该键",
        "tag_axes" not in payload_legacy_axes,
    )
    t.assert_true(
        "误返 tag_axes：产生 warning 提示用户改写到标签 hint",
        any("分类策略" in s and "标签" in s for s in w_legacy_axes),
    )

    # 3a-1b LLM 误返 default_tags_suggestion：同样静默丢弃 + warning
    payload_legacy, w_legacy = parse_and_validate(
        '{"fields":[{"name":"标题","type":"text","prompt_hint":""}],'
        '"default_tags_suggestion":["科幻","翻译"]}'
    )
    t.assert_true(
        "误返 default_tags_suggestion：payload 不含相关键",
        "tag_axes" not in payload_legacy
        and "default_tags_suggestion" not in payload_legacy,
    )
    t.assert_true(
        "误返 default_tags_suggestion：产生 warning",
        any("分类策略" in s for s in w_legacy),
    )

    # 3a-2 顶层含 library_description
    payload_lib, _ = parse_and_validate(
        '{"library_description": "你管理一份科幻小说库……",'
        '"fields":[{"name":"标题","type":"text","prompt_hint":"30 字内"}]}'
    )
    t.assert_eq(
        "library_description 进入 payload",
        payload_lib["library_description"], "你管理一份科幻小说库……",
    )

    # 3a-3 type='tags' 仅允许出现在系统标签字段
    payload_tag_ok, w_ok = parse_and_validate(
        '{"fields":[{"name":"标签","type":"tags","prompt_hint":"3~6 个"}]}'
    )
    t.assert_eq(
        "tags 类型用于「标签」字段时保留",
        payload_tag_ok["fields"][0]["type"], "tags",
    )
    t.assert_eq("tags 用于系统字段不产生 warning", len(w_ok), 0)

    payload_tag_bad, w_bad = parse_and_validate(
        '{"fields":[{"name":"分类","type":"tags","prompt_hint":""}]}'
    )
    t.assert_eq(
        "tags 类型用于非系统字段时 fallback 为 text",
        payload_tag_bad["fields"][0]["type"], "text",
    )
    t.assert_true(
        "tags 误用产生 warning",
        any("tags" in w for w in w_bad),
    )

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

    # 4b：library_description 注入
    msgs_with_desc = build_messages(
        "学术论文", history=[], extra_instruction="",
        library_description="这是个论文管理库",
    )
    user_text_d = msgs_with_desc[1]["content"][0]["text"]
    t.assert_in("library_description 注入 user prompt", "这是个论文管理库", user_text_d)
    t.assert_in(
        "提示语指明 LLM 应在此基础上完善",
        "完善输出 library_description", user_text_d,
    )

    msgs2 = build_messages(
        "学术论文",
        history=[{"content": '{"fields":[{"name":"DOI","type":"url"}]}'}],
        extra_instruction="加一个引用次数字段",
    )
    user_text2 = msgs2[1]["content"][0]["text"]
    t.assert_in("history 文本进入 user prompt", "DOI", user_text2)
    t.assert_in("extra 进入 user prompt", "引用次数", user_text2)

    # ----------------------------------------------------------------
    # 阶段 5：annotate_conflicts（全量规划）
    # ----------------------------------------------------------------
    # 关键变更（task #11 T3 6/1 晚迭代）：
    #   * 所有现有字段都会进 ann 列表（按 ord 顺序排在最前），
    #     未被 LLM 命中 → status=existing_user_field（默认 selected=True 表示保留）
    #   * LLM 新名字追加在末尾
    #   * existing_user_field 取消勾选 → action=delete
    db = tmp / "task11_t3.db"
    repo = Repository(connect(db))
    with closing_repos(repo):
        # 新建库现在只有 标题/标签/描述 3 个保护字段；再造 2 个用户字段
        repo.add_field("子流派", "text", prompt_hint="")  # 用户字段，hint 空
        repo.add_field("阅读状态", "text", prompt_hint="未读/在读/已读")  # hint 非空
        repo.add_field("旧字段", "text", prompt_hint="过时的字段")  # 用户字段，LLM 不会命中
        existing = repo.list_fields()

        suggestions = [
            # LLM 给的 6 条（其中 5 条命中现有字段，1 条全新）
            {"name": "标题", "type": "text", "prompt_hint": "30 字内，体现作品类型"},
            {"name": "标签", "type": "tags", "prompt_hint": "3~6 个标签"},
            {"name": "描述", "type": "textarea", "prompt_hint": "200~400 字"},
            {"name": "子流派", "type": "text", "prompt_hint": "硬/软"},
            {"name": "阅读状态", "type": "text", "prompt_hint": "新版本说明"},
            {"name": "ISBN", "type": "text", "prompt_hint": "13 位"},
        ]
        ann = annotate_conflicts(suggestions, existing)
        # 预期顺序：现有字段按 ord（标题/标签/描述/子流派/阅读状态/旧字段）+ ISBN
        names_in_order = [a.name for a in ann]
        t.assert_eq("annotate 总条数 = 现有 6 + 新 1", len(ann), 7)
        t.assert_eq(
            "annotate 顺序：现有按 ord 在前 + 新增追加",
            names_in_order,
            ["标题", "标签", "描述", "子流派", "阅读状态", "旧字段", "ISBN"],
        )

        # 找索引
        idx_by_name = {a.name: i for i, a in enumerate(ann)}

        # ⭐ 系统必有字段（被 LLM 命中）
        for nm in ("标题", "标签", "描述"):
            a = ann[idx_by_name[nm]]
            t.assert_eq(f"{nm} status=system_required", a.status, "system_required")
            t.assert_eq(f"{nm} selected=True（强制）", a.selected, True)
            t.assert_eq(
                f"{nm} action=update_hint_only",
                a.action, "update_hint_only",
            )
            t.assert_true(
                f"{nm} 拿到 existing_field_id",
                a.existing_field_id is not None,
            )
        # 标签 type 强制对齐为 tags
        t.assert_eq(
            "标签 type 强制对齐为 tags（来自现有字段）",
            ann[idx_by_name["标签"]].type, "tags",
        )

        # 🔁 子流派 — 同名同类型 + 现有 hint 为空 → update_hint_only
        a_zlp = ann[idx_by_name["子流派"]]
        t.assert_eq("子流派 status=same_type", a_zlp.status, "same_type")
        t.assert_eq("子流派 selected=True", a_zlp.selected, True)
        t.assert_eq(
            "子流派 现有 hint 空 → action=update_hint_only",
            a_zlp.action, "update_hint_only",
        )

        # 🔁 阅读状态 — 同名同类型 + 现有 hint 非空 → action=skip
        a_yds = ann[idx_by_name["阅读状态"]]
        t.assert_eq("阅读状态 status=same_type", a_yds.status, "same_type")
        t.assert_eq(
            "阅读状态 现有 hint 非空 → action=skip",
            a_yds.action, "skip",
        )

        # 📝 旧字段 — 现有用户字段，LLM 未命中 → existing_user_field
        a_old = ann[idx_by_name["旧字段"]]
        t.assert_eq(
            "旧字段 status=existing_user_field",
            a_old.status, "existing_user_field",
        )
        t.assert_eq("旧字段 默认 selected=True（保留）", a_old.selected, True)
        t.assert_eq("旧字段 默认 action=keep", a_old.action, "keep")
        t.assert_true(
            "旧字段 拿到 existing_field_id",
            a_old.existing_field_id is not None,
        )
        # 用户取消勾选 → action 转为 delete
        a_old.selected = False
        t.assert_eq(
            "旧字段 取消勾选 → action=delete",
            a_old.action, "delete",
        )

        # ✅ ISBN — 全新
        a_isbn = ann[idx_by_name["ISBN"]]
        t.assert_eq("ISBN status=new", a_isbn.status, "new")
        t.assert_eq("ISBN action=create", a_isbn.action, "create")

        # type_conflict 路径单独验证（独立小数据集）
        existing2 = repo.list_fields()
        type_conflict_sugg = [
            {"name": "子流派", "type": "rating", "prompt_hint": ""},
        ]
        ann2 = annotate_conflicts(type_conflict_sugg, existing2)
        # 现有 + 0 新 = 仍然 6 个（"子流派" 被 LLM 命中 → 进入 type_conflict 而非 existing_user_field）
        a_zlp2 = next(a for a in ann2 if a.name == "子流派")
        t.assert_eq("type_conflict status", a_zlp2.status, "type_conflict")
        # task #19 Phase B：默认 selected=True 表示"未驳回 = 接受"
        t.assert_eq("type_conflict 默认 selected=True", a_zlp2.selected, True)
        # task #19 Phase B：未决策（默认接受）→ action=change_type
        t.assert_eq(
            "type_conflict 默认 action=change_type",
            a_zlp2.action, "change_type",
        )
        # 驳回（selected=False）→ action=skip
        a_zlp2.selected = False
        t.assert_eq(
            "type_conflict 驳回后 action=skip",
            a_zlp2.action, "skip",
        )
        # effective_name 不再有 _v2 改名逻辑，永远 = 原名
        a_zlp2.selected = True
        t.assert_eq(
            "type_conflict effective_name = 原名（不再走 _v2 改名）",
            a_zlp2.effective_name, "子流派",
        )

        # 重复同名 LLM 建议 dedup（保留第一次）
        dup_sugg = [
            {"name": "新字段", "type": "text", "prompt_hint": "第一次"},
            {"name": "新字段", "type": "rating", "prompt_hint": "第二次"},
        ]
        ann_dup = annotate_conflicts(dup_sugg, existing2)
        new_field_anns = [a for a in ann_dup if a.name == "新字段"]
        t.assert_eq("重名 LLM 建议 dedup → 仅 1 条", len(new_field_anns), 1)
        t.assert_eq(
            "重名 LLM 建议保留第一次 hint",
            new_field_anns[0].prompt_hint, "第一次",
        )

        # ----------------------------------------------------------
        # 阶段 5b：LLM 建议列（llm_change_label / has_llm_change / decision）
        # ----------------------------------------------------------
        # 复用前面的 ann（含 system_required / same_type / existing_user_field / new）
        # 旧字段 selected 在阶段 5 已被改为 False；这里先恢复，便于测试默认场景
        next(a for a in ann if a.name == "旧字段").selected = True

        # 6/1 晚最终语义：批准 / 驳回**立即生效**（直接改 ann 内容），
        # llm_change_label 只反映用户决策结果（已批准 / 已驳回 / 已删除 / ""），
        # 不再展示"新增/修改/不变"四档。decision 由 UI 写入。

        # decision='pending'（默认）：所有条目 label 应为 ""（除非已被取消保留）
        labels = {a.name: a.llm_change_label for a in ann}
        t.assert_eq("pending：标题 label 空", labels["标题"], "")
        t.assert_eq("pending：子流派 label 空", labels["子流派"], "")
        t.assert_eq("pending：阅读状态 label 空", labels["阅读状态"], "")
        t.assert_eq("pending：旧字段 label 空", labels["旧字段"], "")
        t.assert_eq("pending：ISBN label 空", labels["ISBN"], "")

        # 旧字段取消勾选 → 标签变 "已删除"
        ann_old_local = next(a for a in ann if a.name == "旧字段")
        ann_old_local.selected = False
        t.assert_eq(
            "旧字段取消勾选 → 标签为 已删除",
            ann_old_local.llm_change_label, "已删除",
        )

        # has_llm_change：仅 LLM 触达且会带来变化的条目才触发批准/驳回按钮
        # 标题/描述/标签：现有 hint 空 + LLM 给了 hint → has_llm_change=True
        t.assert_true(
            "标题（system_required, hint 改）应有按钮",
            next(a for a in ann if a.name == "标题").has_llm_change,
        )
        # 子流派：same_type，现有 hint 空 + LLM 新 hint → has_llm_change=True
        t.assert_true(
            "子流派（same_type, hint 空 → 新）应有按钮",
            next(a for a in ann if a.name == "子流派").has_llm_change,
        )
        # 阅读状态：same_type，现有 hint 非空 → 不会被覆盖 → 无按钮
        t.assert_eq(
            "阅读状态（same_type, hint 非空 → 跳过）不应有按钮",
            next(a for a in ann if a.name == "阅读状态").has_llm_change, False,
        )
        # 旧字段：未被 LLM 命中
        t.assert_eq(
            "未命中的「旧字段」不应有按钮",
            next(a for a in ann if a.name == "旧字段").has_llm_change, False,
        )
        # ISBN：new
        t.assert_true(
            "ISBN（new）应有按钮",
            next(a for a in ann if a.name == "ISBN").has_llm_change,
        )

        # decision 标记后 label 反映决策（驳回的实际 ann 内容回滚由 UI 层负责，
        # 数据层这里只验证 label 与 action 关系）
        a_isbn_d = next(a for a in ann if a.name == "ISBN")
        a_isbn_d.decision = "approved"
        t.assert_eq("ISBN 批准 → label=已批准", a_isbn_d.llm_change_label, "已批准")
        # action 不再依赖 decision；ISBN 是 new + selected=True → create
        t.assert_eq("ISBN 批准 + selected=True → action=create", a_isbn_d.action, "create")
        a_isbn_d.decision = "pending"

        # same_type(子流派) hint 不同 → action=update_hint_only；
        # 注意现在 action 看的是 _hint_changed()（驳回会让 UI 把 hint 还原成现有的）
        a_zlp_d = next(a for a in ann if a.name == "子流派")
        # 当前 ann.prompt_hint = LLM 给的"硬/软"，existing 是空 → hint_changed=True
        t.assert_eq(
            "子流派 pending（LLM hint 注入）→ action=update_hint_only",
            a_zlp_d.action, "update_hint_only",
        )
        # 模拟"驳回立即生效"的副作用：UI 会把 prompt_hint 还原为 existing_prompt_hint
        a_zlp_d.prompt_hint = a_zlp_d.existing_prompt_hint or ""
        a_zlp_d.decision = "rejected"
        t.assert_eq("子流派 驳回（hint 已被还原）→ label=已驳回", a_zlp_d.llm_change_label, "已驳回")
        t.assert_eq(
            "子流派 驳回（hint 已还原 = existing）→ action=skip",
            a_zlp_d.action, "skip",
        )

        # system_required（标题）：批准时若用户后续没改 hint → action=update_hint_only
        a_title_d = next(a for a in ann if a.name == "标题")
        a_title_d.decision = "approved"
        if a_title_d._hint_changed():
            t.assert_eq(
                "标题批准（hint 与现有不同）→ action=update_hint_only",
                a_title_d.action, "update_hint_only",
            )
        # 模拟驳回：UI 把 hint 还原成 existing → action=skip
        a_title_d.prompt_hint = a_title_d.existing_prompt_hint or ""
        a_title_d.decision = "rejected"
        t.assert_eq(
            "标题驳回（hint 已还原 = existing）→ action=skip",
            a_title_d.action, "skip",
        )

        # ----------------------------------------------------------
        # 阶段 5c：LLM 显式删除建议（task #16 T3）
        # ----------------------------------------------------------
        # 5c-1 parse_and_validate 解析 fields_to_delete
        payload_d, w_d = parse_and_validate(
            '{"fields":[{"name":"标题","type":"text","prompt_hint":"30字"}],'
            '"fields_to_delete":['
            ' {"name":"老旧字段","reason":"和当前场景无关，建议清理"},'
            ' {"name":"无理由字段"},'
            ' {"name":""},'
            ' "非对象",'
            ' {"name":"标题","reason":"不应该建议删必有字段"}'
            ']}'
        )
        deletes = payload_d.get("fields_to_delete", [])
        t.assert_eq(
            "fields_to_delete 解析后保留的条数（含无理由占位）", len(deletes), 2,
        )
        names_d = [d["name"] for d in deletes]
        t.assert_in("fields_to_delete 含「老旧字段」", "老旧字段", names_d)
        t.assert_in("fields_to_delete 含「无理由字段」", "无理由字段", names_d)
        t.assert_true(
            "fields_to_delete 不应含必有字段「标题」",
            "标题" not in names_d,
        )
        t.assert_eq(
            "fields_to_delete 老旧字段 reason 取自 LLM",
            next(d for d in deletes if d["name"] == "老旧字段")["reason"],
            "和当前场景无关，建议清理",
        )
        t.assert_in(
            "fields_to_delete 无理由占位 reason",
            "未提供", next(d for d in deletes if d["name"] == "无理由字段")["reason"],
        )
        # 至少应有 3 条 warning（必有字段拒、空 name、非对象）
        t.assert_true(
            "fields_to_delete 错误条目产生 warning",
            len(w_d) >= 3,
        )

        # 5c-2 fields_to_delete 全空 → payload 不含该键
        payload_empty, _ = parse_and_validate(
            '{"fields":[{"name":"标题","type":"text","prompt_hint":""}],'
            '"fields_to_delete":[]}'
        )
        t.assert_true(
            "fields_to_delete 空数组 → payload 无该键",
            "fields_to_delete" not in payload_empty,
        )

        # 5c-3 annotate_conflicts 接受 suggested_deletes
        # 用"旧字段"做目标——阶段 5 一开始创建过，到阶段 7d 才会被真删
        existing3 = repo.list_fields()
        target_name = "旧字段"
        target_field = next(
            (f for f in existing3 if f.name == target_name), None,
        )
        if target_field is not None:  # 防御：阶段顺序变化时跳过
            ann_d = annotate_conflicts(
                [{"name": "标题", "type": "text", "prompt_hint": "30 字内"}],
                existing3,
                suggested_deletes=[
                    {"name": target_name, "reason": "场景已变化，无需此字段"},
                ],
            )
            ann_target = next(a for a in ann_d if a.name == target_name)
            t.assert_eq(
                "旧字段 status=llm_suggest_delete",
                ann_target.status, "llm_suggest_delete",
            )
            t.assert_eq(
                "默认 selected=False（待批准）",
                ann_target.selected, False,
            )
            t.assert_eq(
                "默认 action=keep（pending/驳回保守保留）",
                ann_target.action, "keep",
            )
            t.assert_eq(
                "reason 取自 LLM",
                ann_target.reason, "场景已变化，无需此字段",
            )
            t.assert_true(
                "llm_touched=True",
                ann_target.llm_touched,
            )
            t.assert_true(
                "has_llm_change=True（应展示批准/驳回按钮）",
                ann_target.has_llm_change,
            )

            # 批准 → selected=True → action=delete
            ann_target.selected = True
            ann_target.decision = "approved"
            t.assert_eq(
                "批准后 action=delete",
                ann_target.action, "delete",
            )
            t.assert_eq(
                "批准后 llm_change_label=已批准",
                ann_target.llm_change_label, "已批准",
            )

        # 5c-4 LLM 同时把字段放进 fields 与 fields_to_delete → 以 fields 为准
        # 用"子流派"测试（阶段 5 创建的现有用户字段）
        ann_conflict = annotate_conflicts(
            [{"name": "子流派", "type": "text", "prompt_hint": "硬/软"}],
            existing3,
            suggested_deletes=[
                {"name": "子流派", "reason": "本来想删"},
            ],
        )
        ann_zlp_c = next(a for a in ann_conflict if a.name == "子流派")
        t.assert_true(
            "fields 与 fields_to_delete 冲突 → 以 fields 为准",
            ann_zlp_c.status != "llm_suggest_delete",
        )

        # 5c-5 旧 LLM 输出（无 fields_to_delete）兼容
        ann_legacy = annotate_conflicts(
            [{"name": "标题", "type": "text", "prompt_hint": ""}],
            existing3,
            # suggested_deletes 默认 None
        )
        any_llm_delete = any(
            a.status == "llm_suggest_delete" for a in ann_legacy
        )
        t.assert_eq(
            "旧 LLM 输出（无 fields_to_delete）→ 不产生 llm_suggest_delete",
            any_llm_delete, False,
        )

        # ----------------------------------------------------------
        # 5d：fields_to_rename — LLM 显式改名建议（task #16 补充）
        # ----------------------------------------------------------
        # 5d-1 parse_and_validate 解析 fields_to_rename
        payload_r, w_r = parse_and_validate(
            '{"fields":[{"name":"标题","type":"text","prompt_hint":"30 字"}],'
            '"fields_to_rename":['
            ' {"old_name":"出版社","new_name":"出版商","reason":"更准确"},'
            ' {"old_name":"作者","new_name":"作家"},'
            ' {"old_name":"","new_name":"x","reason":"空 old"},'
            ' {"old_name":"y","new_name":"","reason":"空 new"},'
            ' {"old_name":"z","new_name":"z","reason":"old=new"},'
            ' {"old_name":"标题","new_name":"题名","reason":"老李"},'
            ' {"old_name":"q","new_name":"标签","reason":"撞必有"},'
            ' "字符串行"'
            ']}'
        )
        renames_p = payload_r.get("fields_to_rename", [])
        t.assert_eq(
            "fields_to_rename 解析后保留的条数",
            len(renames_p), 2,
        )
        rmap = {r["old_name"]: r for r in renames_p}
        t.assert_in("rename 含 出版社→出版商", "出版社", rmap)
        t.assert_in("rename 含 作者→作家", "作者", rmap)
        t.assert_eq(
            "rename 出版社 new_name", rmap["出版社"]["new_name"], "出版商",
        )
        t.assert_eq(
            "rename 出版社 reason 取自 LLM",
            rmap["出版社"]["reason"], "更准确",
        )
        t.assert_in(
            "rename 作者缺 reason → 占位",
            "未提供", rmap["作者"]["reason"],
        )
        # 至少应有 5 条 warning：空 old / 空 new / old=new / 必有 old / 撞必有 new / 非对象
        t.assert_true(
            "fields_to_rename 错误条目产生 warning",
            len(w_r) >= 5,
        )

        # 5d-2 fields_to_rename 全空 → payload 不含该键
        payload_re, _ = parse_and_validate(
            '{"fields":[{"name":"标题","type":"text","prompt_hint":""}],'
            '"fields_to_rename":[]}'
        )
        t.assert_true(
            "fields_to_rename 空数组 → payload 无该键",
            "fields_to_rename" not in payload_re,
        )

        # 5d-3 annotate_conflicts 接受 suggested_renames，对未命中 fields 的现有
        # 字段产生 llm_suggest_rename 状态
        existing_for_r = repo.list_fields()
        # 找一个仍然存在的现有用户字段（"子流派"）
        zlp_r = next((f for f in existing_for_r if f.name == "子流派"), None)
        if zlp_r is not None:
            ann_r = annotate_conflicts(
                [{"name": "标题", "type": "text", "prompt_hint": ""}],
                existing_for_r,
                suggested_renames=[
                    {"old_name": "子流派", "new_name": "亚类型", "reason": "更通用"},
                ],
            )
            a_zlp_r = next(a for a in ann_r if a.name == "子流派")
            t.assert_eq(
                "rename 命中现有字段 → llm_suggest_rename status",
                a_zlp_r.status, "llm_suggest_rename",
            )
            t.assert_eq(
                "rename ann.llm_rename_new_name 已填充",
                a_zlp_r.llm_rename_new_name, "亚类型",
            )
            t.assert_eq(
                "rename ann.selected 默认 False（待批准）",
                a_zlp_r.selected, False,
            )
            t.assert_eq(
                "rename ann.action（pending）→ keep",
                a_zlp_r.action, "keep",
            )
            # 模拟用户批准
            a_zlp_r.selected = True
            t.assert_eq(
                "rename ann.action（已批准）→ rename",
                a_zlp_r.action, "rename",
            )

        # 5d-4 同名出现在 fields 与 fields_to_rename → fields 为准
        ann_r_conflict = annotate_conflicts(
            [{"name": "子流派", "type": "text", "prompt_hint": ""}],
            existing_for_r,
            suggested_renames=[
                {"old_name": "子流派", "new_name": "亚类型", "reason": "x"},
            ],
        )
        a_zlp_r_c = next(a for a in ann_r_conflict if a.name == "子流派")
        t.assert_true(
            "fields 与 fields_to_rename 冲突 → 以 fields 为准",
            a_zlp_r_c.status != "llm_suggest_rename",
        )

        # 5d-5 同时出现在 fields_to_rename 与 fields_to_delete → rename 优先
        a_zlp_existing = next(
            (a for a in repo.list_fields() if a.name == "子流派"),
            None,
        )
        if a_zlp_existing is not None:
            ann_dr_conflict = annotate_conflicts(
                [{"name": "标题", "type": "text", "prompt_hint": ""}],
                existing_for_r,
                suggested_renames=[
                    {"old_name": "子流派", "new_name": "亚类型", "reason": "x"},
                ],
                suggested_deletes=[{"name": "子流派", "reason": "y"}],
            )
            a_dr = next(a for a in ann_dr_conflict if a.name == "子流派")
            t.assert_eq(
                "rename + delete 冲突 → rename 优先",
                a_dr.status, "llm_suggest_rename",
            )

        # 5d-6 new_name 与现有其它字段重名 → 改名建议被丢弃（防御性）
        ann_r_dup = annotate_conflicts(
            [{"name": "标题", "type": "text", "prompt_hint": ""}],
            existing_for_r,
            suggested_renames=[
                # 试图把"子流派"改名为"标题"——和现有必有字段重名
                {"old_name": "子流派", "new_name": "标题", "reason": "x"},
            ],
        )
        # parse 层已经过滤"标题"，但 annotate 也再防御一道；这里其实 parse 已挡住，
        # 不会落到 annotate；但若直接构造 dict 走 annotate，应该也安全
        any_rename = any(a.status == "llm_suggest_rename" for a in ann_r_dup)
        t.assert_eq(
            "rename new_name 与现有重名 → 不产生 llm_suggest_rename",
            any_rename, False,
        )

        # 5d-7 LLM 同时把改名后的 new_name 写进 fields 数组（这是预期场景：
        # prompt 要求 fields 是改名后的完整方案）。原 bug：会同时产生
        # 一行 llm_suggest_rename + 一行 new。修复后：只产生 rename 一行，
        # 第二遍处理 new 时跳过 new_name；如果 fields[new_name] 给了 hint，
        # 合并到 rename ann 上。
        if zlp_r is not None:
            ann_r_dup_fields = annotate_conflicts(
                # 注意：现有字段仍叫"子流派"，LLM 在 fields 里给的是改名后的"亚类型"
                [
                    {"name": "标题", "type": "text", "prompt_hint": ""},
                    {"name": "亚类型", "type": "text", "prompt_hint": "更通用 hint"},
                ],
                existing_for_r,
                suggested_renames=[
                    {"old_name": "子流派", "new_name": "亚类型", "reason": "更通用"},
                ],
            )
            # 关键：不应同时出现 "子流派"(rename) 与 "亚类型"(new)
            rename_anns = [
                a for a in ann_r_dup_fields if a.status == "llm_suggest_rename"
            ]
            new_anns = [a for a in ann_r_dup_fields if a.status == "new"]
            t.assert_eq(
                "rename 同时在 fields 里 → rename ann 数量",
                len(rename_anns), 1,
            )
            t.assert_eq(
                "rename 同时在 fields 里 → 不应再产生 new ann",
                len(new_anns), 0,
            )
            # rename ann 的 hint 取自 fields[new_name] 给的
            t.assert_eq(
                "rename ann 合并 fields[new_name].prompt_hint",
                rename_anns[0].prompt_hint, "更通用 hint",
            )
            # 名字仍是 old_name（rename ann 显示原名 → 状态列附新名）
            t.assert_eq(
                "rename ann 名字仍是 old_name",
                rename_anns[0].name, "子流派",
            )
            t.assert_eq(
                "rename ann llm_rename_new_name 是 new_name",
                rename_anns[0].llm_rename_new_name, "亚类型",
            )

        # 5d-8 LLM 在 fields[new_name] 没给 prompt_hint → 用现有字段的旧 hint
        if zlp_r is not None:
            existing_zlp = next(f for f in existing_for_r if f.name == "子流派")
            ann_r_no_hint = annotate_conflicts(
                [
                    {"name": "标题", "type": "text", "prompt_hint": ""},
                    {"name": "亚类型", "type": "text", "prompt_hint": ""},
                ],
                existing_for_r,
                suggested_renames=[
                    {"old_name": "子流派", "new_name": "亚类型", "reason": "x"},
                ],
            )
            r_ann = next(
                a for a in ann_r_no_hint if a.status == "llm_suggest_rename"
            )
            t.assert_eq(
                "rename ann 无新 hint → 沿用现有字段旧 hint",
                r_ann.prompt_hint, existing_zlp.prompt_hint,
            )

        # 5d-9 LLM "既改名又改类型" → 类型变更被静默忽略，但加 warning
        # （task #19 收尾；rename 路径只能改名，不能改类型，那是 type_conflict 的职责）
        if zlp_r is not None:
            # 子流派 现状是 text；LLM 在 fields[亚类型] 里给了 type=rating
            warnings_rt: list[str] = []
            ann_rt = annotate_conflicts(
                [
                    {"name": "标题", "type": "text", "prompt_hint": ""},
                    {"name": "亚类型", "type": "rating", "prompt_hint": "1-5"},
                ],
                existing_for_r,
                suggested_renames=[
                    {"old_name": "子流派", "new_name": "亚类型", "reason": "x"},
                ],
                out_warnings=warnings_rt,
            )
            r_ann_rt = next(
                a for a in ann_rt if a.status == "llm_suggest_rename"
            )
            t.assert_eq(
                "rename + 改类型：ann.type 仍是旧类型 text（rename 不改类型）",
                r_ann_rt.type, "text",
            )
            t.assert_eq(
                "rename + 改类型：llm_rename_new_name 仍正确",
                r_ann_rt.llm_rename_new_name, "亚类型",
            )
            t.assert_eq(
                "rename + 改类型：合并新 hint",
                r_ann_rt.prompt_hint, "1-5",
            )
            t.assert_true(
                "rename + 改类型：触发了类型变更被忽略的 warning",
                any("rename 路径仅改名不动类型" in w for w in warnings_rt),
            )

        # 5d-10 LLM "纯改名（type 一致）" → 不触发 warning
        if zlp_r is not None:
            warnings_pure: list[str] = []
            annotate_conflicts(
                [
                    {"name": "标题", "type": "text", "prompt_hint": ""},
                    {"name": "亚类型", "type": "text", "prompt_hint": ""},
                ],
                existing_for_r,
                suggested_renames=[
                    {"old_name": "子流派", "new_name": "亚类型", "reason": "x"},
                ],
                out_warnings=warnings_pure,
            )
            t.assert_eq(
                "纯改名（type 一致）：不触发类型变更 warning",
                [w for w in warnings_pure if "rename 路径" in w], [],
            )

        # 5d-11 out_warnings=None（默认）：不抛错也不污染调用方
        if zlp_r is not None:
            ann_silent = annotate_conflicts(
                [
                    {"name": "标题", "type": "text", "prompt_hint": ""},
                    {"name": "亚类型", "type": "rating", "prompt_hint": ""},
                ],
                existing_for_r,
                suggested_renames=[
                    {"old_name": "子流派", "new_name": "亚类型", "reason": "x"},
                ],
                # 不传 out_warnings → 默认 None，warning 静默丢弃
            )
            r_ann_silent = next(
                a for a in ann_silent if a.status == "llm_suggest_rename"
            )
            t.assert_eq(
                "out_warnings=None：rename ann 行为不变",
                r_ann_silent.type, "text",
            )


        # ----------------------------------------------------------
        # 5e：same_type 行点删除按钮 → action='delete' 回归测试
        #     6/1 晚发现的 bug：之前 same_type 行点删除按钮静默无效（视觉无变化、
        #     应用时也不会真删）。现在 action 属性对 same_type+selected=False 走 delete。
        # ----------------------------------------------------------
        # 找一个仍然存在的现有用户字段（"子流派"）模拟 same_type 命中
        zlp_e = next((f for f in existing_for_r if f.name == "子流派"), None)
        if zlp_e is not None:
            ann_st = annotate_conflicts(
                [{"name": "子流派", "type": "text", "prompt_hint": "硬/软/赛博"}],
                existing_for_r,
            )
            a_zlp_st = next(a for a in ann_st if a.name == "子流派")
            t.assert_eq("same_type 命中 status", a_zlp_st.status, "same_type")
            # 默认 selected=True：现有 hint 非空场景 → action skip；
            # 这里 zlp_e.prompt_hint 可能非空，统一以 selected=False 走 delete 验证
            a_zlp_st.selected = False
            t.assert_eq(
                "same_type + selected=False → action='delete'（bug 回归）",
                a_zlp_st.action, "delete",
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
        # 阶段 7d：apply_field_plan_batch — 单事务创建 + 更新 + 删除
        # ----------------------------------------------------------
        # 准备：找几个待操作字段
        all_now = repo.list_fields()
        old_field = next(f for f in all_now if f.name == "旧字段")
        zlp_field = next(f for f in all_now if f.name == "子流派")
        before_ids = {f.id for f in all_now}
        before_n = len(all_now)

        # 7d-1 成功路径：建 1 个 + 改 1 个 hint + 删 1 个
        new_ids2, n_del, n_ren_d1, _n_tc = repo.apply_field_plan_batch(
            creates=[("出版社", "text", "出版社全名")],
            updates_hint=[(zlp_field.id, "硬/软/赛博朋克")],
            deletes=[old_field.id],
        )
        t.assert_eq("apply_field_plan_batch 创建 1 个", len(new_ids2), 1)
        t.assert_eq("apply_field_plan_batch 删除 1 个", n_del, 1)
        t.assert_eq("apply_field_plan_batch 7d-1 改名 0 个", n_ren_d1, 0)
        after = repo.list_fields()
        after_ids = {f.id for f in after}
        t.assert_eq("应用后字段数 = before + 1 - 1", len(after), before_n + 1 - 1)
        t.assert_true(
            "出版社 已创建", any(f.name == "出版社" for f in after),
        )
        t.assert_true(
            "旧字段 已删除", not any(f.name == "旧字段" for f in after),
        )
        t.assert_eq(
            "子流派 hint 已更新",
            repo.get_field(zlp_field.id).prompt_hint,
            "硬/软/赛博朋克",
        )

        # 7d-2 拒绝删除受保护字段（标题）→ ROLLBACK
        title_field = next(f for f in repo.list_fields() if f.name == "标题")
        before_n2 = len(repo.list_fields())
        threw = False
        try:
            repo.apply_field_plan_batch(
                creates=[("尝试创建x", "number", "")],
                updates_hint=[],
                deletes=[title_field.id],
            )
        except ValueError:
            threw = True
        t.assert_eq("拒绝删除标题 → ValueError", threw, True)
        t.assert_eq(
            "拒绝删除后字段数不变（已 ROLLBACK）",
            len(repo.list_fields()), before_n2,
        )
        t.assert_true(
            "标题仍存在",
            any(f.name == "标题" for f in repo.list_fields()),
        )
        t.assert_true(
            "尝试创建x 创建被回滚",
            not any(f.name == "尝试创建x" for f in repo.list_fields()),
        )

        # 7d-3 全空入参（应是 noop 但不抛错）
        new_ids3, n_del3, n_ren3, _n_tc3 = repo.apply_field_plan_batch([], [], [])
        t.assert_eq("空入参 creates=[]", new_ids3, [])
        t.assert_eq("空入参 deletes=0", n_del3, 0)
        t.assert_eq("空入参 renames=0", n_ren3, 0)

        # 7d-4 创建路径中遇空 name → 全部 ROLLBACK
        before_n4 = len(repo.list_fields())
        threw2 = False
        try:
            repo.apply_field_plan_batch(
                creates=[("有效字段", "text", ""), ("", "text", "")],
                updates_hint=[],
                deletes=[],
            )
        except ValueError:
            threw2 = True
        t.assert_eq("空 name 抛 ValueError", threw2, True)
        t.assert_eq(
            "失败后字段数不变（含已开始的创建被回滚）",
            len(repo.list_fields()), before_n4,
        )
        t.assert_true(
            "有效字段 创建被回滚",
            not any(f.name == "有效字段" for f in repo.list_fields()),
        )

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

        # ----------------------------------------------------------
        # 阶段 10：apply_field_plan_batch.append_for_fids — 删除前把字段值
        #   追加到 description（与 Repository.delete_field(append=True) 等价）
        # ----------------------------------------------------------
        # 10a 准备：建一个「译者」字段，再添两个项目（一个有值、一个空），删它
        new_ids_e = repo.add_fields_batch([("译者", "text", "")])
        translator_fid = new_ids_e[0]
        p_a = Project(title="带译者的项目", description_md="原始描述")
        p_a.id = repo.save_project(p_a)
        p_a.field_values = {translator_fid: "陈灼"}
        repo.save_project(p_a)
        p_b = Project(title="无译者项目", description_md="原始描述B")
        p_b.id = repo.save_project(p_b)

        before_desc_a = repo.get_project(p_a.id).description_md
        before_desc_b = repo.get_project(p_b.id).description_md

        _, n_del_e, _, _ = repo.apply_field_plan_batch(
            creates=[],
            updates_hint=[],
            deletes=[translator_fid],
            append_for_fids={translator_fid},
        )
        t.assert_eq("apply_field_plan_batch (append) 删除 1 个", n_del_e, 1)
        t.assert_true(
            "译者字段已删除",
            not any(f.name == "译者" for f in repo.list_fields()),
        )
        new_desc_a = repo.get_project(p_a.id).description_md
        t.assert_in("项目 A description 含原始描述", "原始描述", new_desc_a)
        t.assert_in(
            "项目 A description 末尾追加了字段值",
            "**译者**：陈灼", new_desc_a,
        )
        t.assert_true(
            "项目 A description 比之前长（确实追加了）",
            len(new_desc_a) > len(before_desc_a),
        )
        new_desc_b = repo.get_project(p_b.id).description_md
        t.assert_eq(
            "项目 B description 未变（无填充值）",
            new_desc_b, before_desc_b,
        )

        # 10b append_for_fids=None / 空集 → 旧"直接丢"行为，不改 description
        new_ids_f = repo.add_fields_batch([("译者2", "text", "")])
        tr2_fid = new_ids_f[0]
        p_c = Project(title="另一个项目", description_md="C原始")
        p_c.id = repo.save_project(p_c)
        p_c.field_values = {tr2_fid: "李明"}
        repo.save_project(p_c)
        before_desc_c = repo.get_project(p_c.id).description_md
        repo.apply_field_plan_batch([], [], [tr2_fid])
        new_desc_c = repo.get_project(p_c.id).description_md
        t.assert_eq(
            "默认参数（append_for_fids=None） → description 不变（旧行为）",
            new_desc_c, before_desc_c,
        )

        # ----------------------------------------------------------
        # 阶段 11：apply_field_plan_batch.renames — 保留 fid 改名
        # ----------------------------------------------------------
        # 11a 准备：建一个「编辑」字段，写一个项目带值，然后改名为「责任编辑」
        new_ids_r = repo.add_fields_batch([("编辑", "text", "")])
        editor_fid = new_ids_r[0]
        p_r = Project(title="带编辑的项目")
        p_r.id = repo.save_project(p_r)
        p_r.field_values = {editor_fid: "王编"}
        repo.save_project(p_r)

        # 11a-1 改名 + 该 fid 上原值仍然能读到（关键：保留 id）
        _, _, n_ren_a, _ = repo.apply_field_plan_batch(
            creates=[],
            updates_hint=[],
            deletes=[],
            renames=[(editor_fid, "责任编辑")],
        )
        t.assert_eq("renames 改名 1 个", n_ren_a, 1)
        f_after = repo.get_field(editor_fid)
        t.assert_eq("改名后 fid 不变", f_after.id, editor_fid)
        t.assert_eq("改名后 name=新名", f_after.name, "责任编辑")
        # 关键断言：项目里该字段的值没丢
        p_after = repo.get_project(p_r.id)
        t.assert_eq(
            "改名后 project_field_values 仍能查到原值",
            p_after.field_values.get(editor_fid), "王编",
        )

        # 11b 改名为相同名字 → noop（n_renamed=0）
        _, _, n_ren_b, _ = repo.apply_field_plan_batch(
            [], [], [], renames=[(editor_fid, "责任编辑")],
        )
        t.assert_eq("renames 同名 → noop", n_ren_b, 0)

        # 11c 改名空字符串 → ValueError + ROLLBACK
        threw_re = False
        try:
            repo.apply_field_plan_batch(
                [], [], [], renames=[(editor_fid, "")],
            )
        except ValueError:
            threw_re = True
        t.assert_eq("renames 空 new_name → ValueError", threw_re, True)
        t.assert_eq(
            "ROLLBACK 后字段名仍是「责任编辑」",
            repo.get_field(editor_fid).name, "责任编辑",
        )

        # 11d 改名撞已有字段名 → ValueError
        # 先建另一个字段「主编」，再尝试把 editor_fid 改成「主编」
        new_ids_main = repo.add_fields_batch([("主编", "text", "")])
        threw_dup = False
        try:
            repo.apply_field_plan_batch(
                [], [], [], renames=[(editor_fid, "主编")],
            )
        except ValueError:
            threw_dup = True
        t.assert_eq("renames 撞已有字段名 → ValueError", threw_dup, True)

        # 11e 改名受保护字段 → ValueError
        title_fid = next(f for f in repo.list_fields() if f.name == "标题").id
        threw_prot = False
        try:
            repo.apply_field_plan_batch(
                [], [], [], renames=[(title_fid, "题目")],
            )
        except ValueError:
            threw_prot = True
        t.assert_eq("renames 受保护字段 → ValueError", threw_prot, True)

        # 11f 综合：renames + creates + updates_hint + deletes 同事务
        # 把"责任编辑"改回"主编辑"、新建"校对"、删除"主编"
        main_fid = new_ids_main[0]
        new_ids_f, n_del_f, n_ren_f, _n_tc_f = repo.apply_field_plan_batch(
            creates=[("校对", "text", "")],
            updates_hint=[],
            deletes=[main_fid],
            renames=[(editor_fid, "主编辑")],
        )
        t.assert_eq("混合事务 创建 1", len(new_ids_f), 1)
        t.assert_eq("混合事务 删除 1", n_del_f, 1)
        t.assert_eq("混合事务 改名 1", n_ren_f, 1)
        t.assert_eq(
            "改名后字段名为「主编辑」",
            repo.get_field(editor_fid).name, "主编辑",
        )

        # ----------------------------------------------------------
        # 阶段 12：apply_field_plan_batch 的 type_changes 参数（task #19 Phase B）
        # ----------------------------------------------------------
        # 12a 批准 type_conflict 路径：原地改 type + 用 LLM 新 hint 覆盖 +
        #     supersede 该 fid 的 pending suggestions
        cur = repo.conn.cursor()
        cur.execute(
            "INSERT INTO fields(name, type, ord, visible, key, prompt_hint) "
            "VALUES('页数', 'text', 99, 1, NULL, '原 hint：随便填')"
        )
        page_fid = cur.lastrowid
        repo.conn.commit()
        # 插一条 pending suggestion
        p_pages = Project(title="带页数的项目")
        repo.save_project(p_pages)
        cur.execute(
            "INSERT INTO project_field_suggestions"
            "(project_id, field_id, suggested_value, source_task_id, status) "
            "VALUES(?, ?, '约 300 页', NULL, 'pending')",
            (p_pages.id, page_fid),
        )
        repo.conn.commit()

        new_ids12, n_del12, n_ren12, n_tc12 = repo.apply_field_plan_batch(
            creates=[],
            updates_hint=[],
            deletes=[],
            type_changes=[(page_fid, "number", "整数；不要带单位")],
        )
        t.assert_eq("type_changes 改类型数=1", n_tc12, 1)
        t.assert_eq("type_changes 其它操作=0", (len(new_ids12), n_del12, n_ren12), (0, 0, 0))
        f_after = repo.get_field(page_fid)
        t.assert_eq("type_changes 改后 type", f_after.type, "number")
        t.assert_eq(
            "type_changes 用 LLM 新 hint 覆盖旧 hint",
            f_after.prompt_hint, "整数；不要带单位",
        )
        # pending supersede 校验
        n_pending_after = repo.conn.execute(
            "SELECT COUNT(*) FROM project_field_suggestions "
            "WHERE field_id=? AND status='pending'",
            (page_fid,),
        ).fetchone()[0]
        t.assert_eq("type_changes 后 pending=0", int(n_pending_after), 0)
        n_super_after = repo.conn.execute(
            "SELECT COUNT(*) FROM project_field_suggestions "
            "WHERE field_id=? AND status='superseded' AND resolved_at IS NOT NULL",
            (page_fid,),
        ).fetchone()[0]
        t.assert_eq("type_changes 后 superseded=1", int(n_super_after), 1)

        # 12b LLM 给空 hint 的边界 → hint 写空覆盖
        cur.execute(
            "INSERT INTO fields(name, type, ord, visible, key, prompt_hint) "
            "VALUES('字数', 'text', 100, 1, NULL, '保留的旧 hint')"
        )
        word_fid = cur.lastrowid
        repo.conn.commit()
        repo.apply_field_plan_batch(
            [], [], [],
            type_changes=[(word_fid, "number", "")],
        )
        t.assert_eq(
            "空 hint：覆盖后 hint 为空",
            repo.get_field(word_fid).prompt_hint, "",
        )
        t.assert_eq(
            "空 hint：type 仍正确改了",
            repo.get_field(word_fid).type, "number",
        )

        # 12c 受保护字段（title）静默跳过
        title_fid_t = None
        for f in repo.list_fields():
            if f.key == "title":
                title_fid_t = f.id
                break
        t.assert_true("找到 title fid", title_fid_t is not None)
        before_type = repo.get_field(title_fid_t).type
        _, _, _, n_tc_p = repo.apply_field_plan_batch(
            [], [], [],
            type_changes=[(title_fid_t, "number", "新 hint")],
        )
        t.assert_eq("受保护字段 type_changes 跳过", n_tc_p, 0)
        t.assert_eq(
            "受保护字段 type 不变",
            repo.get_field(title_fid_t).type, before_type,
        )

        # 12d 不存在的 fid 静默跳过
        _, _, _, n_tc_n = repo.apply_field_plan_batch(
            [], [], [],
            type_changes=[(99999, "number", "")],
        )
        t.assert_eq("不存在 fid 静默跳过", n_tc_n, 0)


if __name__ == "__main__":
    sys.exit(main())
