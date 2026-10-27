"""Manual model availability checks for configured role fallback chains."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.budgets.runtime import RuntimeSpend, ProviderBudget
from backend.app.config import DEFAULT_MODELS_PATH, Settings, ModelConfigError, load_model_config, models_config_missing_payload
from backend.app.orchestration.fallback import split_model_ref, provider_for, _friendly_provider_error
from backend.app.providers.base import ChatMessage, ProviderError
from backend.app.providers.openrouter import OpenRouterProvider
from backend.app.providers.omniroute import OmniRouteProvider
from backend.app.storage.repositories import Repository

CHECK_PROMPT = "Ответь одним словом: OK"
OPENROUTER_KEY_MISSING_MESSAGE = "OPENROUTER_API_KEY отсутствует. Добавьте ключ в .env или переменные окружения."


def _friendly_error(error: str, category: str = "api_error") -> str:
    text = error.strip() or "Модель вернула неизвестную ошибку."
    lower = text.lower()
    if category == "not_configured" or "api_key" in lower:
        return OPENROUTER_KEY_MISSING_MESSAGE
    if category == "timeout" or "timed out" in lower or "timeout" in lower:
        return "Модель недоступна: запрос превысил timeout. Попробуйте позже или выберите другую модель."
    if category == "quota_or_tokens" or "429" in text or "402" in text or "quota" in lower or "rate" in lower:
        return "Закончились лимиты или сработал rate limit провайдера. Проверьте баланс/лимиты OpenRouter."
    if "unavailable" in lower or "not found" in lower:
        return "Модель недоступна или указанный model_id больше не поддерживается."
    return f"Модель вернула ошибку: {text}"


def check_models(settings: Settings, repository: Repository, config_path: Path | None = None) -> dict[str, Any]:
    """Explicit inference checks for configured routes; never run at startup."""
    model_path = config_path or DEFAULT_MODELS_PATH
    if not model_path.exists():
        return models_config_missing_payload()

    try:
        model_config = load_model_config(model_path, allow_example=False)
    except ModelConfigError as exc:
        payload = exc.to_dict()
        return {"ok": False, **payload, "results": []}
    configured_openrouter = []
    configured_models = []
    for role, refs in model_config.items():
        for ref in refs:
            provider, model_id = split_model_ref(ref)
            configured_models.append((role, provider, model_id))
            if provider == "openrouter":
                configured_openrouter.append((role, provider, model_id))

    if configured_openrouter and len(configured_models) == len(configured_openrouter) and not settings.openrouter_api_key:
        results = []
        for role, provider, model_id in configured_openrouter:
            repository.update_model_status(provider, model_id, role, "failed", last_error=OPENROUTER_KEY_MISSING_MESSAGE)
            results.append({
                "role": role,
                "provider": provider,
                "model_id": model_id,
                "status": "failed",
                "last_error": OPENROUTER_KEY_MISSING_MESSAGE,
                "response_time_ms": None,
            })
        return {
            "ok": False,
            "status": "openrouter_key_missing",
            "error": OPENROUTER_KEY_MISSING_MESSAGE,
            "results": results,
        }

    openrouter = OpenRouterProvider(settings.openrouter_api_key, settings.openrouter_base_url)
    omniroute = OmniRouteProvider(settings.omniroute)
    messages = [ChatMessage("user", CHECK_PROMPT)]
    results: list[dict[str, Any]] = []
    for role, provider_name, model_id in configured_models:
        try:
            provider = provider_for(provider_name, openrouter, omniroute)
            provider.budget = ProviderBudget(RuntimeSpend(repository.conn,settings),task_id=None,origin='model_check',role=role)
            result = provider.chat(model_id=model_id, messages=messages, timeout_seconds=settings.default_timeout_seconds)
            repository.update_model_status(provider_name, model_id, role, "available", response_time_ms=result.response_time_ms)
            results.append({
                "role": role,
                "provider": provider_name,
                "model_id": model_id,
                "status": "available",
                "last_error": "",
                "response_time_ms": result.response_time_ms,
            })
        except ProviderError as exc:
            friendly = (_friendly_provider_error(str(exc), exc.category, provider_name)
                        if provider_name == "omniroute" else _friendly_error(str(exc), exc.category))
            status = "blocked_budget" if exc.category == "spend_blocked" else "failed"
            repository.update_model_status(provider_name, model_id, role, status, last_error=friendly)
            results.append({
                "role": role,
                "provider": provider_name,
                "model_id": model_id,
                "status": status,
                "last_error": friendly,
                "response_time_ms": None,
            })
        except Exception as exc:
            friendly = ("OmniRoute: ошибка транспорта; inference автоматически не повторяется."
                        if provider_name == "omniroute" else _friendly_error(str(exc)))
            repository.update_model_status(provider_name, model_id, role, "failed", last_error=friendly)
            results.append({
                "role": role,
                "provider": provider_name,
                "model_id": model_id,
                "status": "failed",
                "last_error": friendly,
                "response_time_ms": None,
            })

    return {"ok": all(item["status"] == "available" for item in results), "status": "completed", "results": results}
