"""LLM 配置：以 settings 表为后端持久化。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


# 所有支持的平台 id
PROVIDER_IDS = ("deepseek", "openai", "gemini", "grok")

# 默认参数（base_url / 默认 model / 是否支持图片）
PROVIDER_DEFAULTS: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "supports_image": False,
    },
    "openai": {
        "label": "OpenAI (GPT)",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "supports_image": True,
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-2.0-flash",
        "supports_image": True,
    },
    "grok": {
        "label": "xAI Grok",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-2-vision-1212",
        "supports_image": True,
    },
}


@dataclass
class ProviderConfig:
    id: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""

    def label(self) -> str:
        return PROVIDER_DEFAULTS.get(self.id, {}).get("label", self.id)

    def supports_image(self) -> bool:
        return PROVIDER_DEFAULTS.get(self.id, {}).get("supports_image", False)


@dataclass
class LLMConfig:
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    default_provider: str = "deepseek"
    default_language: str = "中文"

    def active(self) -> ProviderConfig | None:
        return self.providers.get(self.default_provider)


# settings 键名
_SETTING_KEY = "llm_config"


def load_config(repo) -> LLMConfig:
    raw = repo.get_setting(_SETTING_KEY, "")
    cfg = LLMConfig()
    if raw:
        try:
            data = json.loads(raw)
            cfg.default_provider = data.get("default_provider", "deepseek")
            cfg.default_language = data.get("default_language", "中文")
            for pid, p in (data.get("providers") or {}).items():
                cfg.providers[pid] = ProviderConfig(
                    id=pid,
                    base_url=p.get("base_url", ""),
                    api_key=p.get("api_key", ""),
                    model=p.get("model", ""),
                )
        except Exception:
            pass
    # 用默认值兜底每个平台的 base_url/model
    for pid, defaults in PROVIDER_DEFAULTS.items():
        pc = cfg.providers.get(pid) or ProviderConfig(id=pid)
        pc.id = pid
        if not pc.base_url:
            pc.base_url = defaults["base_url"]
        if not pc.model:
            pc.model = defaults["model"]
        cfg.providers[pid] = pc
    return cfg


def save_config(repo, cfg: LLMConfig) -> None:
    data = {
        "default_provider": cfg.default_provider,
        "default_language": cfg.default_language,
        "providers": {
            pid: {
                "base_url": pc.base_url,
                "api_key": pc.api_key,
                "model": pc.model,
            }
            for pid, pc in cfg.providers.items()
        },
    }
    repo.set_setting(_SETTING_KEY, json.dumps(data, ensure_ascii=False))
