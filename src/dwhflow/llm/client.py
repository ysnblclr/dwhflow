"""OpenAI uyumlu LLM istemcisi (Ollama, vLLM, LM Studio, llama.cpp, ticari API'ler).

Güvenlik duruşu (PLAN.md 5.9.8):

* Varsayılan olarak yalnızca yerel adreslere bağlanır. Doküman içeriğinin kurum
  dışına çıkması ``allow_external_api`` ile **kayıtlı ayarda** açıkça izin
  gerektirir; tek bir isteğin gövdesiyle açılamaz.
* API anahtarı hiçbir yere yazılmaz; yalnızca hangi ortam değişkeninde olduğu
  bilinir.
* Doküman metni modele **veri** olarak, sınırlandırılmış blok içinde verilir;
  modelin araç çağırma yolu yoktur, çıktısı şemaya ve kanıt denetimine tabidir.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class LLMError(RuntimeError):
    """LLM'e ulaşılamadı ya da yanıt kullanılamadı."""


@dataclass
class LLMConfig:
    enabled: bool = False
    base_url: str = "http://localhost:11434/v1"
    model: str = "qwen3:8b"
    api_key_env: str = "DWHFLOW_AI_KEY"
    temperature: float = 0.0
    timeout_s: float = 180.0
    allow_external_api: bool = False
    max_chars_per_call: int = 3500

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> LLMConfig:
        known = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def host(self) -> str:
        return (urlparse(self.base_url).hostname or "").lower()

    @property
    def is_local(self) -> bool:
        return self.host in LOCAL_HOSTS

    def check_allowed(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise LLMError(f"geçersiz uç adresi: {self.base_url!r}")
        if not self.is_local and not self.allow_external_api:
            raise LLMError(
                f"'{self.host}' yerel bir adres değil ve dış adrese izin kapalı. Doküman "
                "içeriğinin kurum dışına çıkmaması için bu varsayılan bilinçli; açmak için "
                "ayarlarda 'dış adrese izin ver'i işaretleyin."
            )


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json_reply(text: str) -> dict[str, Any]:
    """Model yanıtından JSON nesnesini çıkarır (düşünme blokları ve kod çitleri atılır)."""
    cleaned = _FENCE.sub("", _THINK.sub("", text or "")).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("yanıtta JSON nesnesi yok")
    try:
        value = json.loads(cleaned[start:end + 1])
    except ValueError as exc:
        raise LLMError(f"yanıt JSON olarak ayrıştırılamadı: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMError("yanıt bir JSON nesnesi değil")
    return value


class LLMClient:
    def __init__(self, config: LLMConfig, transport: Any = None) -> None:
        self.config = config
        self._transport = transport  # testlerde httpx.MockTransport

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(self.config.api_key_env or "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _client(self):
        import httpx

        return httpx.Client(timeout=self.config.timeout_s, transport=self._transport)

    def chat_json(self, system: str, user: str,
                  examples: list[tuple[str, str]] | None = None) -> dict[str, Any]:
        """Tek bir JSON yanıtı ister. ``examples`` few-shot (kullanıcı, asistan) çiftleri."""
        return parse_json_reply(self.chat(system, user, examples))

    def chat(self, system: str, user: str, examples: list[tuple[str, str]] | None = None) -> str:
        """Modelin **ham** yanıt metnini döner (test konsolu ham yanıtı gösterir).

        JSON modu istenir; ayrıştırma çağırana kalır (bkz. ``parse_json_reply``).
        """
        self.config.check_allowed()
        messages = [{"role": "system", "content": system}]
        for example_user, example_assistant in examples or []:
            messages += [{"role": "user", "content": example_user},
                         {"role": "assistant", "content": example_assistant}]
        messages.append({"role": "user", "content": user})
        body: dict[str, Any] = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        try:
            with self._client() as client:
                response = client.post(url, json=body, headers=self._headers())
                if response.status_code == 400 and "response_format" in response.text:
                    body.pop("response_format")  # bazı sunucular desteklemiyor
                    response = client.post(url, json=body, headers=self._headers())
        except Exception as exc:  # noqa: BLE001 - ağ hataları kullanıcıya iletilir
            raise LLMError(f"LLM'e ulaşılamadı ({type(exc).__name__}): {exc}") from exc
        if response.status_code >= 400:
            raise LLMError(f"LLM HTTP {response.status_code}: {response.text[:300]}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM yanıtı beklenen biçimde değil (choices[0].message.content)") from exc
        if content is None:
            return ""
        if not isinstance(content, str):
            raise LLMError("LLM yanıtı beklenen biçimde değil (içerik metin değil)")
        return content

    def probe(self) -> dict[str, Any]:
        """``GET /models`` ile bağlantı testi. Yapılandırılan model listede var mı?"""
        self.config.check_allowed()
        url = f"{self.config.base_url.rstrip('/')}/models"
        try:
            with self._client() as client:
                response = client.get(url, headers=self._headers(), timeout=10)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        models: list[str] = []
        try:
            models = [m.get("id") for m in response.json().get("data", []) if isinstance(m, dict)]
        except ValueError:
            pass
        found = self.config.model in models if models else None
        return {
            "ok": response.status_code < 400,
            "status": response.status_code,
            "models": models[:40],
            "model_found": found,
            "hint": (f"'{self.config.model}' listede yok — Ollama'da `ollama pull {self.config.model}`"
                     if models and not found else ""),
        }
