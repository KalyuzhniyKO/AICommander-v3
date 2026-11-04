"""Synthetic OpenAI-compatible responses, NOT captured OmniRoute inventory."""
import io
import json
from threading import Event
import traceback
import urllib.error
import urllib.request

import pytest

from backend.app.providers.base import ChatMessage
from backend.app.providers import omniroute as module
from backend.app.providers.omniroute import OmniRouteConfig, OmniRouteError, OmniRouteProvider


KEY = "synthetic-only-test-credential"


@pytest.fixture(autouse=True)
def isolated_omniroute_env(monkeypatch):
    for name in ["OMNIROUTE_ENABLED", "OMNIROUTE_BASE_URL", "OMNIROUTE_API_KEY",
                 "OMNIROUTE_TIMEOUT_SECONDS", "OMNIROUTE_MAX_GET_ATTEMPTS", "OMNIROUTE_MAX_OUTPUT_TOKENS"]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def declared_free_transport_context(monkeypatch, provider_budget_factory):
    original = OmniRouteProvider.__init__
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.budget = provider_budget_factory('omniroute','synthetic/provider-model',self.config.base_url or 'http://127.0.0.1',output_tokens=self.config.max_output_tokens,api_key=self.config.api_key)
    monkeypatch.setattr(OmniRouteProvider,'__init__',initialize)


class FakeOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.returned = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, dict) or isinstance(response, list):
            response = json.dumps(response).encode()
        response = io.BytesIO(response)
        self.returned.append(response)
        return response


def provider(*responses, **settings):
    opener = FakeOpener(*responses)
    config = OmniRouteConfig(enabled=True, base_url="https://gateway.invalid/v1/", api_key=KEY, **settings)
    return OmniRouteProvider(config, opener=opener), opener


def http_error(code, retry_after=None):
    body = io.BytesIO(("private prompt " + KEY).encode())
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return urllib.error.HTTPError("https://gateway.invalid", code, KEY, headers, body), body


def chat(p, **kwargs):
    return p.chat("synthetic/provider-model", [ChatMessage("user", "synthetic prompt")], 3, **kwargs)


def test_disabled_and_missing_credentials_never_open_transport(monkeypatch):
    opener = FakeOpener()
    with pytest.raises(OmniRouteError, match="disabled"):
        OmniRouteProvider(OmniRouteConfig(), opener=opener).health()
    p = OmniRouteProvider(OmniRouteConfig(enabled=True, base_url="http://127.0.0.1:20128"), opener=opener)
    with pytest.raises(OmniRouteError, match="not_configured"):
        p.models()
    with pytest.raises(OmniRouteError, match="not_configured"):
        chat(p)
    assert opener.requests == []
    for name in ["OMNIROUTE_ENABLED", "OMNIROUTE_API_KEY", "OMNIROUTE_BASE_URL"]:
        monkeypatch.delenv(name, raising=False)
    assert not OmniRouteConfig.from_env().enabled


@pytest.mark.parametrize("url", [
    "http://localhost:20128", "http://127.0.0.1:20128/v1", "http://[::1]:20128/v1/", "https://gateway.invalid/v1/",
])
def test_base_url_normalized_without_duplicate_v1(url):
    opener = FakeOpener({"data": []}, {"status": "ok"})
    p = OmniRouteProvider(OmniRouteConfig(enabled=True, base_url=url, api_key=KEY), opener=opener)
    assert p.models() == []
    assert p.health() == {"status": "ok"}
    assert opener.requests[0][0].full_url == p.config.base_url + "/v1/models"
    assert opener.requests[1][0].full_url == p.config.base_url + "/api/health"


@pytest.mark.parametrize("url", [
    "", "file:///etc/passwd", "http://gateway.invalid", "https://user:secret@gateway.invalid",
    "https://gateway.invalid/v1/v1", "https://gateway.invalid/api/v1", "https://gateway.invalid/?key=secret",
    "https://gateway.invalid/#token", "https://gateway.invalid:bad", "https://gateway.invalid:0",
    "https://gateway.invalid/\n", "https://gateway.invalid/?", "https://gateway.invalid/#",
])
def test_unsafe_or_ambiguous_endpoint_rejected_without_echo(url):
    with pytest.raises(OmniRouteError) as exc:
        OmniRouteConfig(enabled=True, base_url=url)
    assert str(exc.value) == "OmniRoute: invalid_config"


@pytest.mark.parametrize("settings", [
    {"timeout_seconds": float("nan")}, {"timeout_seconds": float("inf")}, {"timeout_seconds": 0},
    {"timeout_seconds": True}, {"max_get_attempts": 0}, {"max_get_attempts": 4},
    {"max_get_attempts": True}, {"api_key": "key\r\nheader"}, {"enabled": "true"},
    {"max_output_tokens": 0}, {"max_output_tokens": 8193}, {"max_output_tokens": True},
])
def test_invalid_config_rejected(settings):
    with pytest.raises(OmniRouteError, match="invalid_config"):
        OmniRouteConfig(**settings)


def test_env_config_explicit_and_secret_not_in_repr(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "true")
    monkeypatch.setenv("OMNIROUTE_BASE_URL", "http://localhost:20128/v1")
    monkeypatch.setenv("OMNIROUTE_API_KEY", KEY)
    monkeypatch.setenv("OMNIROUTE_MAX_GET_ATTEMPTS", "3")
    monkeypatch.setenv("OMNIROUTE_TIMEOUT_SECONDS", "4.5")
    config = OmniRouteConfig.from_env()
    assert config.enabled and config.timeout_seconds == 4.5 and config.max_get_attempts == 3
    assert KEY not in repr(config)
    monkeypatch.setenv("OMNIROUTE_ENABLED", "perhaps")
    with pytest.raises(OmniRouteError, match="invalid_config"):
        OmniRouteConfig.from_env()
    monkeypatch.setenv("OMNIROUTE_ENABLED", "true")
    monkeypatch.setenv("OMNIROUTE_TIMEOUT_SECONDS", KEY)
    with pytest.raises(OmniRouteError) as exc:
        OmniRouteConfig.from_env()
    assert KEY not in ''.join(traceback.format_exception(exc.value))


def test_health_discards_private_fields_and_sends_no_auth():
    p, opener = provider({"status": "ok", "private": KEY})
    assert p.health() == {"status": "ok"}
    assert opener.requests[0][0].get_header("Authorization") is None
    assert opener.returned[0].closed


def test_inventory_projects_only_model_ids_and_deduplicates():
    p, opener = provider({"data": [{"id": "synthetic/model-a", "private": KEY}, {"id": "synthetic/model-a"}, {"id": "synthetic/model-b"}]})
    assert p.models() == ["synthetic/model-a", "synthetic/model-b"]
    assert opener.requests[0][0].get_header("Authorization") == "Bearer " + KEY
    assert opener.returned[0].closed


@pytest.mark.parametrize("body", [
    b"not-json", b"[]", {"data": {}}, {"data": [None]}, {"data": [{"id": ""}]},
    {"data": [{"id": KEY}]}, {"data": [{"id": "bad\nID"}]},
])
def test_invalid_inventory_safe_and_response_closed(body):
    p, opener = provider(body)
    with pytest.raises(OmniRouteError, match="bad_response"):
        p.models()
    assert opener.returned[0].closed
    assert len(opener.requests) == 1


def test_chat_request_exact_id_no_fallback_no_raw_and_echoed_key_redacted():
    p, opener = provider({"choices": [{"message": {"content": "answer " + KEY}}]})
    result = chat(p)
    req, timeout = opener.requests[0]
    assert req.full_url == "https://gateway.invalid/v1/chat/completions"
    assert req.method == "POST" and timeout == 3
    assert json.loads(req.data) == {"model": "synthetic/provider-model", "messages": [{"role": "user", "content": "synthetic prompt"}], "stream": False, "max_tokens": 256}
    assert result.model_id == "synthetic/provider-model"
    assert result.provider == "omniroute" and result.raw is None
    assert result.content == "answer [REDACTED]"
    assert opener.returned[0].closed


@pytest.mark.parametrize("code,category", [(401, "auth_required"), (403, "forbidden"), (402, "quota_or_tokens"), (429, "rate_limited"), (503, "http_error"), (302, "redirect_blocked")])
def test_post_errors_never_retried_and_exception_body_not_exposed(code, category):
    error, body = http_error(code, "999999")
    p, opener = provider(error)
    with pytest.raises(OmniRouteError) as exc:
        chat(p)
    assert exc.value.category == category and exc.value.status_code == code
    assert exc.value.retry_after_seconds == 2
    assert len(opener.requests) == 1 and body.closed
    assert KEY not in ''.join(traceback.format_exception(exc.value))


@pytest.mark.parametrize("code", [401, 403, 402, 404, 302])
def test_get_auth_quota_and_redirect_failures_not_retried(code):
    error, body = http_error(code)
    p, opener = provider(error)
    with pytest.raises(OmniRouteError):
        p.models()
    assert body.closed and len(opener.requests) == 1


def test_get_retry_after_is_capped_and_retry_budget_exhausted(monkeypatch):
    delays = []
    monkeypatch.setattr(module.time, "sleep", delays.append)
    errors = [http_error(code, "100000") for code in [429, 503, 500]]
    p, opener = provider(*(e for e, _ in errors), max_get_attempts=3)
    with pytest.raises(OmniRouteError) as exc:
        p.models()
    assert exc.value.status_code == 500
    assert len(opener.requests) == 3 and delays == [2, 2]
    assert all(b.closed for _, b in errors)


@pytest.mark.parametrize("value,expected", [("garbage", None), ("nan", None), ("inf", None), ("-1", 0), ("0.1", .1), ("Thu, 01 Jan 1970 00:00:00 GMT", 0)])
def test_retry_after_parser(value, expected):
    assert OmniRouteProvider._retry_after(value) == expected


def test_get_transport_retry_then_success(monkeypatch):
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    p, opener = provider(urllib.error.URLError(KEY), {"data": []})
    assert p.models() == [] and len(opener.requests) == 2


def test_requested_timeout_capped_by_config():
    p, opener = provider({"choices": [{"message": {"content": "ok"}}]}, timeout_seconds=1.5)
    chat(p)
    assert opener.requests[0][1] == 1.5


def test_cancel_discards_response_that_arrived_after_cancellation():
    cancel = Event()
    p, opener = provider({"data": []})
    original = opener.open

    def cancelled_open(request, timeout):
        cancel.set()
        return original(request, timeout)

    opener.open = cancelled_open
    with pytest.raises(OmniRouteError, match="cancelled"):
        p.models(cancel=cancel)
    assert opener.returned[0].closed


@pytest.mark.parametrize("error,category", [(TimeoutError(KEY), "timeout"), (urllib.error.URLError(TimeoutError(KEY)), "timeout"), (RuntimeError(KEY), "transport_error")])
def test_post_transport_failure_safe_no_retry(error, category):
    p, opener = provider(error)
    with pytest.raises(OmniRouteError) as exc:
        chat(p)
    assert exc.value.category == category and len(opener.requests) == 1
    assert KEY not in ''.join(traceback.format_exception(exc.value))


def test_cancel_before_request_and_during_retry_wait(monkeypatch):
    cancel = Event()
    cancel.set()
    p, opener = provider()
    with pytest.raises(OmniRouteError, match="cancelled"):
        p.models(cancel=cancel)
    assert opener.requests == []
    cancel.clear()
    error, body = http_error(429, "2")
    p, opener = provider(error)
    monkeypatch.setattr(cancel, "wait", lambda _: cancel.set())
    with pytest.raises(OmniRouteError, match="cancelled"):
        p.models(cancel=cancel)
    assert len(opener.requests) == 1 and body.closed


def test_response_size_bound_and_no_retry():
    p, opener = provider(b"x" * (module.MAX_RESPONSE_BYTES + 2))
    with pytest.raises(OmniRouteError, match="response_too_large"):
        p.models()
    assert opener.returned[0].closed and len(opener.requests) == 1


@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]}, {"choices": [{"message": {"content": []}}]}, {"choices": [{"message": {"content": " "}}]}])
def test_invalid_chat_response_not_success(body):
    p, _ = provider(body)
    with pytest.raises(OmniRouteError, match="bad_response"):
        chat(p)


@pytest.mark.parametrize("body", [{"status": {}}, {"status": KEY}, {}])
def test_invalid_health_status_not_success(body):
    p, _ = provider(body)
    with pytest.raises(OmniRouteError, match="bad_response"):
        p.health()


def test_redirect_handler_refuses_forwarding_credentials_and_proxy_is_disabled(monkeypatch):
    handlers = []
    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *args: handlers.extend(args))
    OmniRouteProvider(OmniRouteConfig())
    assert handlers[0].proxies == {}
    req = urllib.request.Request("https://gateway.invalid", headers={"Authorization": KEY})
    assert handlers[1].redirect_request(req, None, 302, "redirect", {}, "https://other.invalid") is None


@pytest.mark.parametrize("timeout", [0, float("nan"), True])
def test_invalid_timeout_never_sends_request(timeout):
    p, opener = provider()
    with pytest.raises(OmniRouteError, match="invalid_request"):
        p.chat("synthetic/model", [ChatMessage("user", "test")], timeout)
    assert opener.requests == []
