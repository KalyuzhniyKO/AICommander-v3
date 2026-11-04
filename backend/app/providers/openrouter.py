"""OpenRouter chat-completion provider."""

from __future__ import annotations

import json
from hashlib import sha256
import time
import urllib.error
import urllib.request

from ..budgets.runtime import ProviderBudget, usage_from_response, reported_usage, retry_after_delay
from .omniroute import _NoRedirect
from .base import ChatMessage, ChatProvider, ChatResult, ProviderError, messages_to_dicts


class OpenRouterProvider(ChatProvider):
    provider_name = "openrouter"

    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1", *, budget=None, opener=None) -> None:
        self.budget = budget
        self._opener = opener if opener is not None else urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def chat(self, model_id: str, messages: list[ChatMessage], timeout_seconds: int) -> ChatResult:
        if not self.api_key:
            raise ProviderError("OPENROUTER_API_KEY is not configured", "not_configured")
        if not isinstance(self.budget, ProviderBudget):
            raise ProviderError("Запрос модели не отправлен: pricing_policy_unavailable", "spend_blocked")
        return self.budget.chat(provider=self.provider_name,model=model_id,base_url=self.base_url,
            messages=messages_to_dicts(messages),timeout_seconds=timeout_seconds,send=self._send,
            credential_sha256=sha256(self.api_key.encode()).hexdigest())

    def _send(self, payload, timeout_seconds):
        model_id = payload['model']
        start = time.monotonic()
        payload = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/KalyuzhniyKO/AICommander-v3",
                "X-Title": "AICommander-v3",
            },
        )
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                raw = response.read(1024*1024 + 1)
                if len(raw) > 1024*1024:
                    raise ProviderError("OpenRouter response exceeded limit", "bad_response")
                body = raw.decode("utf-8")
        except urllib.error.HTTPError as exc:
            category = "quota_or_tokens" if exc.code in {402, 403, 429} else "api_error"
            # Error bodies can echo credentials/prompts. Do not persist them in SQLite.
            exc.close()
            failure = ProviderError(f"OpenRouter HTTP {exc.code}", category)
            failure.http_status = exc.code
            failure.retry_after_seconds = retry_after_delay(exc.headers.get("Retry-After") if exc.headers else None)
            raise failure from None
        except TimeoutError as exc:
            raise ProviderError("OpenRouter request timed out", "timeout") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ProviderError("OpenRouter request timed out", "timeout") from exc
            raise ProviderError("OpenRouter request failed", "api_error") from exc
        except Exception as exc:
            raise ProviderError("OpenRouter request failed", "api_error") from exc
        elapsed = int((time.monotonic() - start) * 1000)
        try:
            parsed = json.loads(body)
            content = parsed["choices"][0]["message"]["content"]
        except Exception as exc:
            raise ProviderError(f"OpenRouter returned a bad response: {exc}", "bad_response") from exc
        if not content or not str(content).strip():
            raise ProviderError("OpenRouter returned an empty response", "bad_response")
        return ChatResult(provider=self.provider_name, model_id=model_id, content=str(content), response_time_ms=elapsed, raw=parsed, usage=usage_from_response(parsed), reported_usage=reported_usage(parsed))
