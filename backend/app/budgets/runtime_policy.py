"""Owner-supplied server policy; names and subscriptions never imply zero cost."""
import hashlib
import json
from pathlib import Path
import time
from typing import Literal

from pydantic import Field, model_validator

from ..execution import ExecutionError
from ..localrun.workflow import _read, _decode
from .models import Model, Limits, Role, Tariff


class RuntimeRoute(Model):
    provider: Literal['openrouter', 'omniroute']
    base_url: str = Field(min_length=1, max_length=512)
    model_id: str = Field(min_length=1, max_length=110, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:/-]*$')
    roles: tuple[Role, ...] = Field(min_length=1, max_length=4)
    account_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    tariff: Tariff | None = None
    upstream_tariff: Tariff | None = None
    reported_cost_unit: Literal['usd','credits'] | None = None
    credit_micro_usd: int | None = Field(default=None, ge=1, le=10**12)
    # These are trusted owner attestations of the actual transport/provider caps,
    # not assertions accepted from prompts, browser requests or a model name.
    fixed_route_verified: bool = False
    billing_categories_verified: bool = False
    provider_caps_verified: bool = False
    max_input_tokens: int = Field(default=65536, ge=1, le=10**6)
    max_output_tokens: int = Field(default=256, ge=1, le=10**6)
    max_payload_bytes: int = Field(default=16384, ge=1, le=512*1024)
    timeout_seconds: int = Field(default=60, ge=1, le=120)

    @property
    def reference(self):
        return self.provider + '/' + self.model_id

    @model_validator(mode='after')
    def unique_roles(self):
        from urllib.parse import urlsplit
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or self.base_url.endswith('/'):
            raise ValueError('Invalid fixed endpoint')
        if self.tariff is not None and self.upstream_tariff is not None:
            if any(getattr(self.upstream_tariff,key)>getattr(self.tariff,key) for key in ('input_micro_usd_per_million','output_micro_usd_per_million','request_micro_usd')):
                raise ValueError('Total bound is below upstream pricing caps')
        if len(set(self.roles)) != len(self.roles):
            raise ValueError('Duplicate roles')
        return self


class RuntimePolicy(Model):
    schema_version: Literal[1] = 1
    verified_at_ms: int = Field(ge=0)
    valid_until_ms: int = Field(ge=0)
    routes: tuple[RuntimeRoute, ...] = Field(min_length=1, max_length=16)
    # Ceilings allow an owner to approve a budget, not inference by themselves.
    global_ceiling_micro_usd: int = Field(default=0, ge=0, le=10**12)
    project_ceiling_micro_usd: int = Field(default=0, ge=0, le=10**12)
    task_ceiling_micro_usd: int = Field(default=0, ge=0, le=10**12)
    max_requests: int = Field(default=100, ge=1, le=1000)
    max_total_tokens: int = Field(default=10**8, ge=1, le=10**9)
    period_seconds: int = Field(default=86400, ge=1, le=86400)

    @model_validator(mode='after')
    def bindings(self):
        if len({r.reference for r in self.routes}) != len(self.routes):
            raise ValueError('Duplicate routes')
        if not self.verified_at_ms < self.valid_until_ms <= self.verified_at_ms + 86400*1000:
            raise ValueError('Policy evidence must be refreshed within one day')
        return self

    @property
    def fingerprint(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def ceiling(self, scope):
        return getattr(self, scope + '_ceiling_micro_usd')

    def limits(self):
        return Limits(max_requests=self.max_requests,
                      max_input_tokens=max(r.max_input_tokens for r in self.routes),
                      max_output_tokens=max(r.max_output_tokens for r in self.routes),
                      max_total_tokens=self.max_total_tokens,
                      max_request_seconds=max(r.timeout_seconds for r in self.routes),
                      lifetime_seconds=self.period_seconds, allow_paid=False, max_micro_usd=0)


def load_runtime_policy(configured: str, *, now_ms=None):
    if not configured:
        return None
    try:
        path = Path(configured)
        if not path.is_absolute():
            raise ValueError
        value = _decode(_read(path, 128*1024))
        # JSON-mode validation accepts JSON arrays as immutable tuples; strict
        # fields still reject strings for money/count/bool values.
        policy = RuntimePolicy.model_validate_json(json.dumps(value))
        now = time.time_ns()//1_000_000 if now_ms is None else now_ms
        if not policy.verified_at_ms <= now < policy.valid_until_ms:
            raise ValueError
        return policy
    except (ExecutionError, OSError, ValueError, TypeError):
        return None
