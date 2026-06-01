"""LLM 助手插件注册表（task #11 T3）。

历史背景：内部代码沿用 wizard / WizardPlugin 命名（最初任务卡叫"向导"），
对外文案统一为"LLM 助手"——它们是同一物。

每个助手是一个 ``WizardPlugin`` 子类，通过将类追加到 ``WIZARDS`` 列表自注册。

新增助手步骤：
1. 在本目录新建 ``my_wizard.py``，定义 ``class MyWizard(WizardPlugin)``，类属性
   ``meta = WizardMeta(...)`` 并实现 ``run(repo, library)``；
2. 在本文件 import 后追加到 ``WIZARDS``。
"""
from __future__ import annotations

from .base import WizardMeta, WizardPlugin
from .library_init import LibraryInitWizard

# 注册表：UI 列表按此顺序展示，按 meta.category 分组
WIZARDS: list[type[WizardPlugin]] = [
    LibraryInitWizard,
]

__all__ = ["WizardMeta", "WizardPlugin", "WIZARDS"]
