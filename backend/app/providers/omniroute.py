"""Opt-in text transport, checked against OmniRoute 3.8.51 with scoped live probes.

No credential discovery, model routing or fallback lives in this module.
Application callers must explicitly configure the transport and exact route.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
import ipaddress
from hashlib import sha256
import json
import math
import os
import time
from threading import Event
import urllib.error
import urllib.parse
import urllib.request

from ..budgets.runtime import ProviderBudget, usage_from_response, reported_usage, retry_after_delay
from .base import ChatMessage, ChatProvider, ChatResult, ProviderError, messages_to_dicts

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_RETRY_DELAY_SECONDS = 2.0


class OmniRouteError(ProviderError):
    def __init__(self, category: str, status_code: int | None = None,
                 retry_after_seconds: float | None = None):
        # Never include response bodies, headers, URLs, prompts or exception text.
        super().__init__(f"OmniRoute: {category}", category)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def _valid_model_id(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 256
            and value == value.strip() and not any(ord(c) < 32 or ord(c) == 127 for c in value))


@dataclass(frozen=True)
class OmniRouteConfig:
    enabled: bool = False
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 10.0
    max_get_attempts: int = 2
    max_output_tokens: int = 256

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise OmniRouteError("invalid_config")
        if (isinstance(self.timeout_seconds, bool)
                or not isinstance(self.timeout_seconds, (float, int))
                or not math.isfinite(self.timeout_seconds)
                or not 0 < self.timeout_seconds <= 60
                or type(self.max_get_attempts) is not int
                or not 1 <= self.max_get_attempts <= 3):
            raise OmniRouteError("invalid_config")
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 8192:
            raise OmniRouteError("invalid_config")
        if not isinstance(self.api_key, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in self.api_key):
            raise OmniRouteError("invalid_config")
        if not isinstance(self.base_url, str):
            raise OmniRouteError("invalid_config")
        if not self.base_url and not self.enabled:
            return
        try:
            url = urllib.parse.urlsplit(self.base_url)
            host = url.hostname
            port = url.port
            try:
                loopback = ipaddress.ip_address(host or "").is_loopback
            except ValueError:
                loopback = host == "localhost"
            if (url.scheme not in {"http", "https"} or not host
                    or url.username is not None or url.password is not None
                    or url.query or url.fragment or "?" in self.base_url or "#" in self.base_url
                    or url.path.rstrip("/") not in {"", "/v1"}
                    or any(c.isspace() or ord(c) < 32 for c in self.base_url)
                    or (port is not None and not 0 < port <= 65535)
                    or (url.scheme == "http" and not loopback)):
                raise ValueError
        except (ValueError, TypeError):
            raise OmniRouteError("invalid_config") from None
        # Accept a service root or /v1 without ever constructing /v1/v1.
        object.__setattr__(self, "base_url", urllib.parse.urlunsplit((url.scheme, url.netloc, "", "", "")))

    @classmethod
    def from_env(cls) -> OmniRouteConfig:
        """Read only explicit environment variables; never .env or CLI stores."""
        enabled = os.environ.get("OMNIROUTE_ENABLED", "false").lower()
        if enabled not in {"true", "false"}:
            raise OmniRouteError("invalid_config")
        try:
            return cls(
                enabled=enabled == "true",
                base_url=os.environ.get("OMNIROUTE_BASE_URL", ""),
                api_key=os.environ.get("OMNIROUTE_API_KEY", ""),
                timeout_seconds=float(os.environ.get("OMNIROUTE_TIMEOUT_SECONDS", "10")),
                max_get_attempts=int(os.environ.get("OMNIROUTE_MAX_GET_ATTEMPTS", "2")),
                max_output_tokens=int(os.environ.get("OMNIROUTE_MAX_OUTPUT_TOKENS", "256")),
            )
        except (ValueError, OverflowError):
            raise OmniRouteError("invalid_config") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OmniRouteProvider(ChatProvider):
    provider_name = "omniroute"

    def __init__(self, config: OmniRouteConfig | None = None, *, opener=None, budget=None):
        self.budget = budget
        self.config = config if config is not None else OmniRouteConfig.from_env()
        # Do not inherit ambient proxy credentials or forward auth on redirects.
        self._opener = opener if opener is not None else urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect())

    @staticmethod
    def _check_cancel(cancel: Event | None):
        if cancel is not None and cancel.is_set():
            raise OmniRouteError("cancelled")

    @staticmethod
    def _retry_after(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            seconds = float(value)
        except (ValueError, TypeError):
            try:
                seconds = parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                return None
        if not math.isfinite(seconds):
            return None
        return min(MAX_RETRY_DELAY_SECONDS, max(0.0, seconds))

    def _request(self, path: str, *, payload: dict | None = None,
                 authenticated: bool = False, timeout: float | None = None,
                 cancel: Event | None = None) -> dict:
        if not self.config.enabled:
            raise OmniRouteError("disabled")
        if authenticated and not self.config.api_key:
            raise OmniRouteError("not_configured")
        timeout = self.config.timeout_seconds if timeout is None else timeout
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise OmniRouteError("invalid_request")
        timeout = min(timeout, self.config.timeout_seconds)
        method = "POST" if payload is not None else "GET"
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        attempts = self.config.max_get_attempts if method == "GET" else 1
        for attempt in range(attempts):
            self._check_cancel(cancel)
            retry_after = None
            try:
                request = urllib.request.Request(self.config.base_url + path, data=data, headers=headers, method=method)
                with self._opener.open(request, timeout=timeout) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                self._check_cancel(cancel)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise OmniRouteError("response_too_large")
                try:
                    result = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise OmniRouteError("bad_response") from None
                if not isinstance(result, dict):
                    raise OmniRouteError("bad_response")
                return result
            except urllib.error.HTTPError as exc:
                code = exc.code
                retry_after = self._retry_after(exc.headers.get("Retry-After") if exc.headers else None)
                exc.close()
                category = {401: "auth_required", 403: "forbidden", 402: "quota_or_tokens", 429: "rate_limited"}.get(
                    code, "redirect_blocked" if 300 <= code < 400 else "http_error")
                failure = OmniRouteError(category, code, retry_after)
                failure.budget_retry_after_seconds = retry_after_delay(exc.headers.get("Retry-After") if exc.headers else None)
                retryable = code in {429, 500, 502, 503, 504}
            except OmniRouteError:
                raise
            except (TimeoutError, urllib.error.URLError) as exc:
                is_timeout = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
                failure = OmniRouteError("timeout" if is_timeout else "transport_error")
                retryable = True
            except Exception:
                # Do not chain arbitrary transport exceptions that may echo auth.
                raise OmniRouteError("transport_error") from None
            self._check_cancel(cancel)
            if not retryable or attempt + 1 == attempts:
                raise failure from None
            delay = retry_after if retry_after is not None else 0.25 * (2 ** attempt)
            if cancel is not None:
                cancel.wait(delay)
            else:
                time.sleep(delay)
        raise AssertionError("unreachable")

    def health(self, *, cancel: Event | None = None) -> dict[str, str]:
        result = self._request("/api/health", cancel=cancel)
        if not isinstance(result.get("status"), str) or result["status"] not in {"ok", "healthy", "degraded", "unhealthy"}:
            raise OmniRouteError("bad_response")
        return {"status": result["status"]}

    def models(self, *, cancel: Event | None = None) -> list[str]:
        result = self._request("/v1/models", authenticated=True, cancel=cancel)
        rows = result.get("data")
        if not isinstance(rows, list) or len(rows) > 10000:
            raise OmniRouteError("bad_response")
        ids = []
        for row in rows:
            model = row.get("id") if isinstance(row, dict) else None
            if not _valid_model_id(model) or self.config.api_key in model:
                raise OmniRouteError("bad_response")
            ids.append(model)
        return list(dict.fromkeys(ids))

    def chat(self, model_id: str, messages: list[ChatMessage], timeout_seconds: int,
             *, cancel: Event | None = None) -> ChatResult:
        if isinstance(timeout_seconds,bool) or not isinstance(timeout_seconds,(int,float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise OmniRouteError('invalid_request')
        if (not _valid_model_id(model_id) or not messages
                or (self.config.api_key and self.config.api_key in model_id)
                or any(not isinstance(m, ChatMessage) or m.role not in {"system", "user", "assistant"}
                       or not isinstance(m.content, str) for m in messages)):
            raise OmniRouteError("invalid_request")
        if not self.config.enabled:
            raise OmniRouteError('disabled')
        if not self.config.api_key:
            raise OmniRouteError('not_configured')
        if not isinstance(self.budget, ProviderBudget):
            from .base import ProviderError
            raise ProviderError('Запрос модели не отправлен: pricing_policy_unavailable','spend_blocked')
        return self.budget.chat(provider=self.provider_name,model=model_id,base_url=self.config.base_url,
            messages=messages_to_dicts(messages),timeout_seconds=timeout_seconds,
            output_limit=self.config.max_output_tokens,credential_sha256=sha256(self.config.api_key.encode()).hexdigest(),
            send=lambda payload,timeout:self._send_chat(payload,timeout,cancel))

    def _send_chat(self, payload, timeout_seconds, cancel):
        start = time.monotonic()
        model_id = payload['model']
        parsed = self._request('/v1/chat/completions',authenticated=True,payload=payload,
                               timeout=timeout_seconds,cancel=cancel)
        try:
            content = parsed["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise OmniRouteError("bad_response") from None
        if not isinstance(content, str) or not content.strip():
            raise OmniRouteError("bad_response")
        # Never expose an echoed configured credential, even in a successful body.
        content = content.replace(self.config.api_key, "[REDACTED]")
        return ChatResult(provider=self.provider_name, model_id=model_id, content=content,
                          response_time_ms=int((time.monotonic() - start) * 1000), raw=None, usage=usage_from_response(parsed), reported_usage=reported_usage(parsed))
