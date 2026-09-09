"""LLM 配置：以 settings 表为后端持久化。

task #42：API Key 不再明文落库。保存时优先写入系统凭据管理器
（``keyring``，Windows 下为 Credential Manager），settings 的 JSON 里只留
哨兵值 ``keyring:1``；keyring 不可用时回退明文（并在 UI 提示）。
读取时自动把老库里的明文 key 迁移进 keyring。

环境变量 ``LLM_CABINET_DISABLE_KEYRING=1`` 可强制禁用（selftests 用，
避免污染真实凭据管理器）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field


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

# ---------------------------------------------------------------------------
# task #42：keyring 存取层
# ---------------------------------------------------------------------------
_KEYRING_SERVICE = "llm-cabinet"
KEYRING_SENTINEL = "keyring:1"
_keyring_write_failed = False
log = logging.getLogger(__name__)


def _kr():
    """返回 keyring 模块；不可用（未安装 / 被环境变量禁用）返回 None。"""
    if os.environ.get("LLM_CABINET_DISABLE_KEYRING"):
        return None
    try:
        import keyring
        return keyring
    except Exception:
        return None


def keyring_available() -> bool:
    """系统凭据管理器是否可用（设置页据此提示存储方式）。"""
    kr = _kr()
    if kr is None:
        return False
    try:
        backend = kr.get_keyring()
        return getattr(backend, "priority", 1) > 0 and not _keyring_write_failed
    except Exception:
        return False


def _scope_for_root(root: str) -> str:
    root = (root or "").strip()
    if not root:
        return "default"
    return hashlib.sha1(root.encode("utf-8")).hexdigest()[:12]


def _keyring_scope(repo) -> str:
    """按库根目录派生 keyring 作用域（不同库的 key 互不串）。"""
    root = ""
    try:
        root = repo.get_setting("library_root", "") or ""
    except Exception:
        pass
    return _scope_for_root(root)


def _store_key(repo, pid: str, api_key: str) -> bool:
    """把 key 写入 keyring（当前库作用域）。成功返回 True。"""
    global _keyring_write_failed
    kr = _kr()
    if kr is None or not api_key:
        return False
    try:
        kr.set_password(
            _KEYRING_SERVICE, f"{_keyring_scope(repo)}:{pid}", api_key,
        )
        _keyring_write_failed = False
        return True
    except Exception:
        _keyring_write_failed = True
        log.warning("系统凭据写入失败，配置回退为明文存储")
        return False


def key_storage_notice(repo) -> str:
    """根据当前库实际保存结果提示，不把后端存在等同于密钥写入成功。"""
    try:
        data = json.loads(repo.get_setting(_SETTING_KEY, "") or "{}")
        values = [p.get("api_key", "") for p in data.get("providers", {}).values()]
    except (ValueError, TypeError, AttributeError):
        return "⚠ 密钥存储状态无法确认，请重新保存 API 配置。"
    if any(value and value != KEYRING_SENTINEL for value in values):
        return "⚠ 部分 API Key 以明文保存在库文件中，请勿直接分享库目录。应用备份会移除配置密钥。"
    if any(value == KEYRING_SENTINEL for value in values):
        return "🔒 API Key 已保存为系统凭据引用。应用备份移除密钥，恢复后需重新填写。"
    return "尚未保存 API Key。保存时优先使用系统凭据管理器；失败时会提示明文存储。"


def _read_key(repo, pid: str) -> str:
    kr = _kr()
    if kr is None:
        return ""
    try:
        return kr.get_password(
            _KEYRING_SERVICE, f"{_keyring_scope(repo)}:{pid}",
        ) or ""
    except Exception:
        return ""


def _delete_key(repo, pid: str) -> None:
    kr = _kr()
    if kr is None:
        return
    try:
        kr.delete_password(_KEYRING_SERVICE, f"{_keyring_scope(repo)}:{pid}")
    except Exception:
        pass


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

    # task #42：哨兵 → keyring 取真值；明文 → 自动迁移进 keyring
    migrated = False
    for pid, pc in cfg.providers.items():
        if pc.api_key == KEYRING_SENTINEL:
            pc.api_key = _read_key(repo, pid)
        elif pc.api_key and _store_key(repo, pid, pc.api_key):
            migrated = True
    if migrated:
        # 重写为哨兵形式（settings 不再含明文）
        save_config(repo, cfg)

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
    providers: dict[str, dict] = {}
    for pid, pc in cfg.providers.items():
        stored = False
        if pc.api_key:
            # keyring 可用 → settings 里只留哨兵；不可用 → 回退明文
            stored = _store_key(repo, pid, pc.api_key)
        else:
            _delete_key(repo, pid)
        providers[pid] = {
            "base_url": pc.base_url,
            "api_key": KEYRING_SENTINEL if stored else pc.api_key,
            "model": pc.model,
        }
    data = {
        "default_provider": cfg.default_provider,
        "default_language": cfg.default_language,
        "providers": providers,
    }
    repo.set_setting(_SETTING_KEY, json.dumps(data, ensure_ascii=False))


def rekey_imported_llm_config(repo, src_root) -> tuple[int, int]:
    """task #42：把刚导入的 llm_config 里的哨兵按**源库**作用域解出，
    重新写入当前库作用域。

    在「从其它库导入 API 配置」后调用（同机场景）。源 keyring 里查不到的
    （如备份来自另一台机器）对应平台 key 置空，让用户重新填写。
    返回 ``(迁移成功数, 需重填数)``。
    """
    raw = repo.get_setting(_SETTING_KEY, "")
    if not raw:
        return (0, 0)
    try:
        data = json.loads(raw)
    except Exception:
        return (0, 0)
    providers = data.get("providers") or {}
    src_scope = _scope_for_root(str(src_root))
    ok = fail = 0
    changed = False
    kr = _kr()
    for pid, p in providers.items():
        if p.get("api_key") != KEYRING_SENTINEL:
            continue
        key = ""
        if kr is not None:
            try:
                key = kr.get_password(_KEYRING_SERVICE, f"{src_scope}:{pid}") or ""
            except Exception:
                key = ""
        if key and _store_key(repo, pid, key):
            ok += 1
        else:
            p["api_key"] = ""
            fail += 1
        changed = True
    if changed:
        repo.set_setting(_SETTING_KEY, json.dumps(data, ensure_ascii=False))
    return (ok, fail)
