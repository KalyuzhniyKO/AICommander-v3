"""Model fallback logic per role."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from backend.app.budgets.runtime import ProviderBudget
from backend.app.providers.base import ChatMessage, ChatProvider, ProviderError
from backend.app.providers.openrouter import OpenRouterProvider
from backend.app.providers.omniroute import OmniRouteProvider
from backend.app.storage.repositories import Repository


@dataclass
class FallbackOutcome:
    success: bool
    output: str = ""
    provider: str = ""
    model_id: str = ""
    response_time_ms: int | None = None
    errors: list[dict[str, str]] = field(default_factory=list)


def split_model_ref(ref: str) -> tuple[str, str]:
    if "/" not in ref:
        return "openrouter", ref
    provider, model = ref.split("/", 1)
    return provider, model


def provider_for(provider_name: str, openrouter: OpenRouterProvider, omniroute: OmniRouteProvider | None = None) -> ChatProvider:
    if provider_name == "openrouter":
        return openrouter
    if provider_name == "omniroute" and omniroute is not None:
        return omniroute
    raise ProviderError(f"Provider {provider_name} is not configured", "not_configured")


def _friendly_provider_error(error: str, category: str = "api_error", provider_name: str = "openrouter") -> str:
    if category == "spend_blocked":
        return "Запрос модели не отправлен: требуется проверенная серверная политика и бюджет владельца. " + error
    lower = error.lower()
    if provider_name == "omniroute":
        if category in {"disabled", "not_configured"}:
            return "OmniRoute выключен или не настроен: проверьте OMNIROUTE_ENABLED, OMNIROUTE_BASE_URL и OMNIROUTE_API_KEY."
        if category in {"auth_required", "forbidden"}:
            return "OmniRoute отклонил доступ: проверьте inference-ключ и его разрешения."
        if category in {"rate_limited", "quota_or_tokens"}:
            return "OmniRoute: исчерпана квота или сработал rate limit; inference автоматически не повторяется."
    if category == "not_configured" or "api_key" in lower:
        return "OPENROUTER_API_KEY отсутствует или провайдер не настроен. Добавьте ключ в .env или переменные окружения."
    if category == "timeout" or "timed out" in lower or "timeout" in lower:
        return "Модель недоступна: запрос превысил timeout."
    if category == "quota_or_tokens" or "429" in error or "402" in error or "quota" in lower or "rate" in lower:
        return "Закончились лимиты или сработал rate limit провайдера."
    if "unavailable" in lower or "not found" in lower:
        return "Модель недоступна или model_id больше не поддерживается."
    return f"Модель вернула ошибку: {error}"


def run_with_fallback(
    *,
    role: str,
    model_refs: list[str],
    messages: list[ChatMessage],
    timeout_seconds: int,
    max_attempts_per_model: int,
    repository: Repository,
    round_id: int | None,
    openrouter: OpenRouterProvider,
    omniroute: OmniRouteProvider | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    provider_filter: Callable[[str], bool] | None = None,
) -> FallbackOutcome:
    errors: list[dict[str, str]] = []
    if not model_refs:
        return FallbackOutcome(success=False, errors=[{"provider": "unknown", "model_id": "", "error": f"Нет моделей в config/models.json для роли {role}. Проверьте конфигурацию."}])
    budget = next((candidate for candidate in (getattr(openrouter,'budget',None),getattr(omniroute,'budget',None)) if isinstance(candidate,ProviderBudget)),None)
    if budget is not None:
        model_refs = budget.free_first(model_refs)
    for ref in model_refs:
        provider_name, model_id = split_model_ref(ref)
        if provider_filter and not provider_filter(provider_name):
            continue
        # OmniRoute owns model/provider routing. Never replay an ambiguous inference
        # or fall through to a direct provider after it fails.
        attempts = 1 if provider_name == "omniroute" or isinstance(getattr(openrouter,"budget",None),ProviderBudget) else max(1, max_attempts_per_model)
        for _ in range(attempts):
            if cancelled():
                return FallbackOutcome(success=False, errors=errors)
            category = "unknown"
            provider = None
            try:
                provider = provider_for(provider_name, openrouter, omniroute)
                result = provider.chat(model_id=model_id, messages=messages, timeout_seconds=timeout_seconds)
                repository.update_model_status(provider_name, model_id, role, "available", response_time_ms=result.response_time_ms)
                return FallbackOutcome(True, result.content, provider_name, model_id, result.response_time_ms, errors)
            except ProviderError as exc:
                category = exc.category
                error = _friendly_provider_error(str(exc), exc.category, provider_name)
            except Exception as exc:
                error = ("OmniRoute: ошибка транспорта; inference автоматически не повторяется."
                         if provider_name == "omniroute" else f"Неожиданная ошибка провайдера: {exc}")
            errors.append({"provider": provider_name, "model_id": model_id, "error": error})
            repository.add_model_error(round_id, role, provider_name, model_id, error)
            repository.update_model_status(provider_name, model_id, role, "blocked_budget" if category == "spend_blocked" else "failed", last_error=error)
            if provider_name == "omniroute" or category == "spend_blocked" or isinstance(getattr(provider,"budget",None),ProviderBudget):
                return FallbackOutcome(success=False, errors=errors)
            continue
    return FallbackOutcome(success=False, errors=errors)
