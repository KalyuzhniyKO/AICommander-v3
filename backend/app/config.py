"""Minimal configuration and validation for the v3 foundation."""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from backend.app.providers.omniroute import OmniRouteConfig
from backend.app.taskflow.hermes_config import HermesConfig

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT_DIR / "config"
DEFAULT_MODELS_PATH = CONFIG_DIR / "models.json"
EXAMPLE_MODELS_PATH = CONFIG_DIR / "models.example.json"
REQUIRED_MODEL_ROLES = {"manager", "architect", "designer", "coder", "reviewer"}

class ModelConfigError(ValueError):
    def __init__(self, status: str, error: str):
        super().__init__(error)
        self.status, self.error = status, error
    def to_dict(self):
        return {"status": self.status, "error": self.error}

@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str = ""
    default_timeout_seconds: int = 60
    max_model_retries: int = 1
    database_url: str = "sqlite:///./aicommander.db"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    omniroute: OmniRouteConfig = field(default_factory=OmniRouteConfig)
    hermes: HermesConfig = field(default_factory=HermesConfig)
    local_review_root: str = ""
    spend_policy_file: str = ""
    @property
    def database_path(self) -> Path:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("Only sqlite:/// paths supported in foundation")
        p = Path(self.database_url[len(prefix):])
        return p if p.is_absolute() else ROOT_DIR / p

def _dotenv():
    p = ROOT_DIR / ".env"
    if p.exists():
        for raw in p.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if s and not s.startswith("#") and "=" in s:
                key, value = s.split("=",1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))

def get_settings() -> Settings:
    _dotenv()
    return Settings(
        local_review_root=os.environ.get("LOCAL_REVIEW_ROOT", ""),
        spend_policy_file=os.environ.get("SPEND_POLICY_FILE", ""),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY",""),
        default_timeout_seconds=max(1,int(os.environ.get("DEFAULT_TIMEOUT_SECONDS","60"))),
        max_model_retries=max(1,int(os.environ.get("MAX_MODEL_RETRIES","1"))),
        database_url=os.environ.get("DATABASE_URL","sqlite:///./aicommander.db"),
        openrouter_base_url=os.environ.get("OPENROUTER_BASE_URL","https://openrouter.ai/api/v1"),
        omniroute=OmniRouteConfig.from_env(),
        hermes=HermesConfig.from_env(),
    )

def models_config_missing_payload():
    return {"ok":False, "status":"config_missing", "error":"Файл config/models.json не найден. Создайте его из config/models.example.json.", "results":[]}

def load_model_config(path: Path | None = None, *, allow_example: bool=True, validate: bool=True):
    candidate = path or DEFAULT_MODELS_PATH
    if not candidate.exists():
        if path is None and allow_example and EXAMPLE_MODELS_PATH.exists():
            candidate=EXAMPLE_MODELS_PATH
        else:
            return {}
    raw = candidate.read_text(encoding="utf-8").strip()
    if not raw:
        raise ModelConfigError("config_empty", "Файл моделей пустой")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ModelConfigError("config_invalid",str(e)) from e
    if not isinstance(data,dict) or not data:
        raise ModelConfigError("config_empty", "Ожидается непустой объект роль -> модели")
    normalized={}
    for role, references in data.items():
        if role not in REQUIRED_MODEL_ROLES:
            raise ModelConfigError("role_unknown",f"Неизвестная роль: {role}")
        if not isinstance(references,list) or not references:
            raise ModelConfigError("role_models_empty",f"Список моделей роли {role} пустой")
        verified=[]
        for ref in references:
            if not isinstance(ref,str) or "/" not in ref:
                raise ModelConfigError("provider_prefix_missing", f"Нужен префикс provider/model в роли {role}")
            provider,mid=ref.split("/",1)
            if provider not in {"openrouter", "omniroute"}:
                raise ModelConfigError("provider_unknown", f"Неподдерживаемый provider: {provider}")
            if not mid.strip():
                raise ModelConfigError("model_id_empty",f"Пустой ID модели роли {role}")
            verified.append(ref)
        if any(ref.startswith("omniroute/") for ref in verified) and len(verified) != 1:
            raise ModelConfigError("omniroute_single_route", "Для OmniRoute укажите один маршрут роли; fallback выполняет сам OmniRoute")
        normalized[role]=verified
    return normalized
