"""LLM 助手插件基类与元数据（task #11 T3）。

所有助手都是 ``QDialog`` 子类，由 ``WizardListDialog`` 统一启动。
通过类属性 ``meta`` 暴露元信息（标题、分组、是否需要库为空等），
基类只规定接口，不强制 UI 形态。

历史背景：内部代码沿用 wizard / WizardPlugin 命名（最初任务卡叫"向导"），
对外文案统一为"LLM 助手"——它们是同一物。
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QDialog


@dataclass(frozen=True)
class WizardMeta:
    id: str
    title: str
    description: str
    category: str = "库初始化"
    icon: str = "🪄"
    # 仅在库为空（无 projects）时可用；UI 用此控制启动按钮 enabled
    require_empty_lib: bool = False


class WizardPlugin(QDialog):
    """LLM 助手基类。子类至少要定义类属性 ``meta`` 并实现 ``run``。

    生命周期：``WizardListDialog`` 创建实例 → 调 ``run(repo, library)`` →
    返回是否实际应用了变更（True 表示主界面应该刷新）。
    """

    meta: WizardMeta = WizardMeta(
        id="_base",
        title="（未命名助手）",
        description="",
    )

    def run(self, repo, library) -> bool:  # pragma: no cover - 抽象
        raise NotImplementedError

    @classmethod
    def is_available(cls, repo) -> tuple[bool, str]:
        """前置条件检查：返回 (is_available, reason_if_not)。

        子类可覆盖以加更严格的检查。基类只看 ``meta.require_empty_lib``。
        """
        if cls.meta.require_empty_lib:
            n = repo.count_projects_total()
            if n > 0:
                return False, f"当前库已有 {n} 个项目，仅支持空库使用"
        return True, ""
