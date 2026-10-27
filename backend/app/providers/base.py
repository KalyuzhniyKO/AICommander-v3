"""Provider abstraction for chat-completion APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict
from ..budgets.models import Usage


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class ChatResult:
    provider: str
    model_id: str
    content: str
    response_time_ms: int
    raw: dict | None = None
    usage: Usage | None = None
    reported_usage: dict | None = None


class ProviderError(Exception):
    def __init__(self, message: str, category: str = "api_error") -> None:
        super().__init__(message)
        self.category = category


class ChatProvider:
    provider_name = "base"

    def chat(self, model_id: str, messages: List[ChatMessage], timeout_seconds: int) -> ChatResult:
        raise NotImplementedError


def messages_to_dicts(messages: List[ChatMessage]) -> List[Dict[str, str]]:
    return [{"role": msg.role, "content": msg.content} for msg in messages]
