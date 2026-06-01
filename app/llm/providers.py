"""LLM Provider 统一接口。

所有 provider 共用一个标准化的 messages 输入：
    [
      {"role": "system", "content": "..."},
      {"role": "user", "content": [
          {"type": "text", "text": "..."},
          {"type": "image", "data": <bytes>, "mime": "image/png"},
      ]},
    ]

Gemini 和 Grok 内部转换；OpenAI / DeepSeek 走 OpenAI 兼容协议。
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx

from .config import ProviderConfig


@dataclass
class LLMResponse:
    text: str = ""
    raw: dict | None = None
    tokens_in: int = 0
    tokens_out: int = 0


class BaseProvider:
    id: str = ""
    # task #11 T3 决策 4：是否原生支持结构化 JSON 输出。
    # True → 调用 chat 时传 json_mode=True 走原生 API；
    # False → 上层走"prompt 强约束 + 解析"路径（LLM 助手会在 sys prompt 里塞 schema 示例）。
    # 子类按实际能力覆盖；未来加新 provider 时按 API 文档填。
    supports_json_mode: bool = True

    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg

    def supports_image(self) -> bool:
        return self.cfg.supports_image()

    # 子类必须实现
    def chat(
        self, messages: list[dict], *,
        json_mode: bool = False,
        timeout: float = 60.0,
    ) -> LLMResponse:
        raise NotImplementedError

    def ping(self, timeout: float = 8.0) -> tuple[bool, str]:
        """轻量连通性测试，子类应覆盖。默认实现做一次极简 chat。"""
        try:
            resp = self.chat(
                [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
                timeout=timeout,
            )
            text = (resp.text or "").strip()
            return (True, f"OK：{text[:40]}") if text else (False, "返回为空")
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"


# =============================================================================
# OpenAI 兼容（OpenAI / DeepSeek / Grok 都用 /chat/completions + Bearer）
# =============================================================================
class _OpenAICompatible(BaseProvider):
    def chat(
        self, messages: list[dict], *,
        json_mode: bool = False,
        timeout: float = 60.0,
    ) -> LLMResponse:
        if not self.cfg.api_key:
            raise RuntimeError(f"未配置 {self.cfg.label()} API Key")

        api_messages = [self._to_oai_message(m) for m in messages]
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": api_messages,
            "temperature": 0.4,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=timeout) as cli:
            r = cli.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()

        choices = data.get("choices") or []
        text = ""
        if choices:
            msg = choices[0].get("message") or {}
            text = msg.get("content") or ""
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            raw=data,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
        )

    def _to_oai_message(self, m: dict) -> dict:
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, str):
            return {"role": role, "content": content}
        # 列表形式 → 多模态
        parts: list[dict] = []
        for c in content or []:
            t = c.get("type")
            if t == "text":
                parts.append({"type": "text", "text": c.get("text", "")})
            elif t == "image":
                if not self.supports_image():
                    continue
                data = c.get("data")
                mime = c.get("mime", "image/png")
                if isinstance(data, (bytes, bytearray)):
                    b64 = base64.b64encode(data).decode("ascii")
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    })
        # 若全被过滤掉，至少留一个空 text
        if not parts:
            parts.append({"type": "text", "text": ""})
        return {"role": role, "content": parts}

    def ping(self, timeout: float = 8.0) -> tuple[bool, str]:
        """轻量探测：GET /models 只验证鉴权与可达性，不消耗推理。"""
        if not self.cfg.api_key:
            return False, "未填写 API Key"
        url = self.cfg.base_url.rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"}
        try:
            with httpx.Client(timeout=timeout) as cli:
                r = cli.get(url, headers=headers)
            if r.status_code == 200:
                # 尝试统计模型数，但即便 JSON 解析失败也算成功
                n = 0
                try:
                    data = r.json()
                    items = data.get("data") if isinstance(data, dict) else None
                    if isinstance(items, list):
                        n = len(items)
                except Exception:
                    pass
                return True, f"OK（可用模型 {n} 个）" if n else "OK"
            if r.status_code in (401, 403):
                return False, f"鉴权失败（HTTP {r.status_code}）：API Key 可能不正确"
            if r.status_code == 404:
                return False, "HTTP 404：Base URL 可能不正确"
            return False, f"HTTP {r.status_code}：{(r.text or '')[:120]}"
        except httpx.TimeoutException:
            return False, f"连接超时（{timeout:.0f}s）"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"


class OpenAIProvider(_OpenAICompatible):
    id = "openai"


class DeepSeekProvider(_OpenAICompatible):
    id = "deepseek"

    def supports_image(self) -> bool:
        return False  # 强制


class GrokProvider(_OpenAICompatible):
    id = "grok"


# =============================================================================
# Gemini （格式不同：contents + parts；header 用 ?key=）
# =============================================================================
class GeminiProvider(BaseProvider):
    id = "gemini"

    def chat(
        self, messages: list[dict], *,
        json_mode: bool = False,
        timeout: float = 60.0,
    ) -> LLMResponse:
        if not self.cfg.api_key:
            raise RuntimeError("未配置 Gemini API Key")

        # Gemini 把 system 和 user 混在一起，但提供 systemInstruction
        sys_text = ""
        contents: list[dict] = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                if isinstance(m.get("content"), str):
                    sys_text += m["content"] + "\n"
                else:
                    for c in m.get("content") or []:
                        if c.get("type") == "text":
                            sys_text += c.get("text", "") + "\n"
                continue
            parts = self._to_gemini_parts(m.get("content"))
            contents.append({
                "role": "user" if role != "model" else "model",
                "parts": parts,
            })

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": 0.4},
        }
        if sys_text.strip():
            payload["systemInstruction"] = {"parts": [{"text": sys_text.strip()}]}
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        url = (
            self.cfg.base_url.rstrip("/")
            + f"/models/{self.cfg.model}:generateContent"
            + f"?key={self.cfg.api_key}"
        )
        with httpx.Client(timeout=timeout) as cli:
            r = cli.post(
                url, headers={"Content-Type": "application/json"}, json=payload,
            )
            r.raise_for_status()
            data = r.json()

        text = ""
        for cand in data.get("candidates") or []:
            content = cand.get("content") or {}
            for part in content.get("parts") or []:
                if "text" in part:
                    text += part["text"]
            break
        usage = data.get("usageMetadata") or {}
        return LLMResponse(
            text=text,
            raw=data,
            tokens_in=int(usage.get("promptTokenCount", 0)),
            tokens_out=int(usage.get("candidatesTokenCount", 0)),
        )

    def _to_gemini_parts(self, content) -> list[dict]:
        if isinstance(content, str):
            return [{"text": content}]
        parts: list[dict] = []
        for c in content or []:
            t = c.get("type")
            if t == "text":
                parts.append({"text": c.get("text", "")})
            elif t == "image":
                data = c.get("data")
                mime = c.get("mime", "image/png")
                if isinstance(data, (bytes, bytearray)):
                    b64 = base64.b64encode(data).decode("ascii")
                    parts.append({
                        "inlineData": {"mimeType": mime, "data": b64},
                    })
        if not parts:
            parts.append({"text": ""})
        return parts

    def ping(self, timeout: float = 8.0) -> tuple[bool, str]:
        """Gemini 轻量探测：GET /models?key=...，不消耗推理。"""
        if not self.cfg.api_key:
            return False, "未填写 API Key"
        url = self.cfg.base_url.rstrip("/") + f"/models?key={self.cfg.api_key}"
        try:
            with httpx.Client(timeout=timeout) as cli:
                r = cli.get(url)
            if r.status_code == 200:
                n = 0
                try:
                    data = r.json()
                    items = data.get("models") if isinstance(data, dict) else None
                    if isinstance(items, list):
                        n = len(items)
                except Exception:
                    pass
                return True, f"OK（可用模型 {n} 个）" if n else "OK"
            if r.status_code in (401, 403):
                return False, f"鉴权失败（HTTP {r.status_code}）：API Key 可能不正确"
            if r.status_code == 404:
                return False, "HTTP 404：Base URL 可能不正确"
            return False, f"HTTP {r.status_code}：{(r.text or '')[:120]}"
        except httpx.TimeoutException:
            return False, f"连接超时（{timeout:.0f}s）"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"


# =============================================================================
PROVIDERS: dict[str, type[BaseProvider]] = {
    "deepseek": DeepSeekProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "grok": GrokProvider,
}


def get_provider(cfg: ProviderConfig) -> BaseProvider:
    cls = PROVIDERS.get(cfg.id)
    if cls is None:
        raise ValueError(f"unknown provider: {cfg.id}")
    return cls(cfg)
