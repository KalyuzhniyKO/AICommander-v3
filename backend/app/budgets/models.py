"""Trusted server metadata only: never prompts, credentials, response bodies or prices guessed from names."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$')]
ModelID = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$')]
Count = Annotated[int, Field(ge=0, le=10**9)]
Money = Annotated[int, Field(ge=0, le=9*10**15)]
Role = Literal['analyst', 'architect', 'developer', 'reviewer']


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, hide_input_in_errors=True)


class Tariff(Model):
    # Worst declared rates; optional fixed request fee, all rounded UP.
    request_micro_usd: int = Field(default=0, ge=0, le=10**12)
    input_micro_usd_per_million: int = Field(ge=0, le=10**12)
    output_micro_usd_per_million: int = Field(ge=0, le=10**12)

    def quote(self, input_tokens: int, output_tokens: int) -> int:
        if any(type(value) is not int or value<0 for value in (input_tokens,output_tokens)):
            raise ValueError('Invalid token counts')
        input_cost=input_tokens*self.input_micro_usd_per_million
        output_cost=output_tokens*self.output_micro_usd_per_million
        return self.request_micro_usd + (input_cost+999_999)//1_000_000 + (output_cost+999_999)//1_000_000


class Route(Model):
    role: Role
    model_id: ModelID
    tariff: Tariff | None = None


class Limits(Model):
    max_requests: int = Field(default=8, ge=1, le=1000)
    max_input_tokens: int = Field(default=4096, ge=1, le=10**6)
    max_output_tokens: int = Field(default=1024, ge=1, le=10**6)
    max_total_tokens: int = Field(default=16384, ge=1, le=10**9)
    max_request_seconds: int = Field(default=90, ge=1, le=3600)
    lifetime_seconds: int = Field(default=600, ge=1, le=86400)
    max_micro_usd: int = Field(default=0, ge=0, le=10**12)
    allow_paid: bool = False
    base_backoff_seconds: int = Field(default=60, ge=1, le=3600)
    max_backoff_seconds: int = Field(default=3600, ge=1, le=86400)

    @model_validator(mode='after')
    def backoff_bounds(self):
        if self.base_backoff_seconds > self.max_backoff_seconds:
            raise ValueError('Invalid backoff bounds')
        return self


class Policy(Model):
    routes: tuple[Route, ...] = Field(min_length=1, max_length=64)
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode='after')
    def unique_routes(self):
        if len({(r.role,r.model_id) for r in self.routes}) != len(self.routes):
            raise ValueError('Duplicate role/model permission')
        return self


class Request(Model):
    operation_id: Identifier
    role: Role
    model_id: ModelID
    input_tokens: int = Field(ge=0, le=10**6)
    output_tokens: int = Field(ge=1, le=10**6)
    timeout_seconds: int = Field(ge=1, le=3600)


class Usage(Model):
    input_tokens: Count
    output_tokens: Count
    # None means billing is UNKNOWN, including on a declared-zero tariff.
    cost_micro_usd: Money | None = None


class Outcome(Model):
    kind: Literal['completed','quota','rejected','unknown']
    http_status: int | None = Field(default=None, ge=100, le=599)
    provider_code: Literal['rate_limit','quota_exhausted'] | None = None
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86400)
    usage: Usage | None = None

    @model_validator(mode='after')
    def consistent(self):
        if self.kind=='completed' and (self.http_status is None or not 200 <= self.http_status < 300 or self.provider_code):
            raise ValueError('Completion requires a successful response')
        if self.kind=='quota' and self.http_status!=429 and self.provider_code is None:
            raise ValueError('Quota requires a structured signal')
        if self.kind=='rejected' and self.http_status not in {400,401,403,404,405,413,422}:
            raise ValueError('Rejection requires an explicit rejection status')
        if self.kind!='quota' and (self.retry_after_seconds is not None or self.provider_code is not None):
            raise ValueError('Quota metadata requires quota outcome')
        if self.kind=='unknown' and self.usage is not None:
            raise ValueError('Unknown outcomes cannot refund reservations')
        return self


class Decision(Model):
    allowed: bool
    code: str
    operation_id: Identifier
    estimated_micro_usd: Money | None = None
    cooldown_until_ms: int | None = None
    state: str | None = None
    # Reservation replay is evidence, not another permission to send.
    created: bool = False
