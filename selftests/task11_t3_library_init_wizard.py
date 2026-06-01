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
        t.assert_eq("type_conflict 默认 selected=False", a_zlp2.selected, False)
        t.assert_eq("type_conflict 默认 action=skip", a_zlp2.action, "skip")
        a_zlp2.selected = True
        t.assert_eq(
            "type_conflict 勾选后 action=create",
            a_zlp2.action, "create",
        )
        t.assert_eq(
            "type_conflict effective_name 走 rename_to",
            a_zlp2.effective_name, "子流派_v2",
        )
        a_zlp2.rename_to = "   "
        t.assert_eq(
            "type_conflict rename_to 清空 → action 退化 skip",
            a_zlp2.action, "skip",
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
        new_ids2, n_del = repo.apply_field_plan_batch(
            creates=[("出版社", "text", "出版社全名")],
            updates_hint=[(zlp_field.id, "硬/软/赛博朋克")],
            deletes=[old_field.id],
        )
        t.assert_eq("apply_field_plan_batch 创建 1 个", len(new_ids2), 1)
        t.assert_eq("apply_field_plan_batch 删除 1 个", n_del, 1)
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
        new_ids3, n_del3 = repo.apply_field_plan_batch([], [], [])
        t.assert_eq("空入参 creates=[]", new_ids3, [])
        t.assert_eq("空入参 deletes=0", n_del3, 0)

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

        _, n_del_e = repo.apply_field_plan_batch(
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


if __name__ == "__main__":
    sys.exit(main())
