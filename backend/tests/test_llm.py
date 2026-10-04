"""Tests for LLM provider selection and fallback. No network calls are made."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from app.config import Settings
from app.services.llm import (
    LLMCapabilityError,
    LLMProviderError,
    LLMRateLimitError,
    LLMService,
    build_chat_model,
)


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "mongodb_uri": "mongodb://x",
        "llm_fallback_provider": "none",
        "google_api_key": "g-key",
        "groq_api_key": "groq-key",
        "openrouter_api_key": "or-key",
    }
    return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[call-arg]


# ------------------------------------------------------------------ selection
@pytest.mark.parametrize(
    ("provider", "model", "class_name"),
    [
        ("gemini", "gemini-3.6-flash", "ChatGoogleGenerativeAI"),
        ("groq", "llama-3.3-70b-versatile", "ChatGroq"),
        ("openrouter", "openrouter/free", "ChatOpenRouter"),
    ],
)
def test_build_chat_model_selects_provider(provider: str, model: str, class_name: str) -> None:
    chat = build_chat_model(provider, model, make_settings())
    assert type(chat).__name__ == class_name


def test_unknown_provider_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        build_chat_model("anthropic", "x", make_settings())


def test_from_settings_with_fallback() -> None:
    service = LLMService.from_settings(
        make_settings(llm_fallback_provider="openrouter", llm_fallback_model="openrouter/free")
    )
    assert type(service._primary).__name__ == "ChatGoogleGenerativeAI"
    assert type(service._fallback).__name__ == "ChatOpenRouter"


def test_api_key_not_in_model_repr() -> None:
    chat = build_chat_model("groq", "m", make_settings(groq_api_key="super-secret-groq"))
    assert "super-secret-groq" not in repr(chat)


# ------------------------------------------------------------------- fallback
class RateLimited(Exception):
    status_code = 429


class StubModel:
    """Minimal stand-in for a chat model: only what LLMService uses."""

    def __init__(self, reply: Any = None, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls = 0

    async def ainvoke(self, messages: Any) -> Any:
        self.calls += 1
        if self.error:
            raise self.error
        return self.reply

    def with_structured_output(self, schema: Any) -> StubModel:
        if self.reply == "no-structured":
            raise NotImplementedError
        return self


MESSAGES = [HumanMessage("hi")]


async def test_primary_success_skips_fallback() -> None:
    primary, fallback = StubModel(AIMessage("from primary")), StubModel(AIMessage("fb"))
    service = LLMService(primary, fallback)  # type: ignore[arg-type]
    assert await service.generate_text(MESSAGES) == "from primary"
    assert fallback.calls == 0


async def test_rate_limit_falls_back() -> None:
    primary = StubModel(error=RateLimited("quota exceeded"))
    fallback = StubModel(AIMessage("from fallback"))
    service = LLMService(primary, fallback)  # type: ignore[arg-type]
    assert await service.generate_text(MESSAGES) == "from fallback"


async def test_all_rate_limited_raises_typed_error() -> None:
    service = LLMService(  # type: ignore[arg-type]
        StubModel(error=RateLimited("429")), StubModel(error=RateLimited("429"))
    )
    with pytest.raises(LLMRateLimitError):
        await service.generate_text(MESSAGES)


async def test_provider_error_message_hides_details() -> None:
    service = LLMService(StubModel(error=RuntimeError("api_key=sk-secret failed")))  # type: ignore[arg-type]
    with pytest.raises(LLMProviderError) as info:
        await service.generate_text(MESSAGES)
    assert "sk-secret" not in str(info.value)


async def test_list_content_is_flattened() -> None:
    reply = AIMessage(content=[{"type": "text", "text": "Hello "}, {"type": "text", "text": "there"}])
    service = LLMService(StubModel(reply))  # type: ignore[arg-type]
    assert await service.generate_text(MESSAGES) == "Hello there"


class Answer(BaseModel):
    value: int


async def test_structured_output_returns_model() -> None:
    service = LLMService(StubModel(Answer(value=3)))  # type: ignore[arg-type]
    assert await service.generate_structured(MESSAGES, Answer) == Answer(value=3)


async def test_structured_output_dict_is_validated() -> None:
    service = LLMService(StubModel({"value": 7}))  # type: ignore[arg-type]
    assert (await service.generate_structured(MESSAGES, Answer)).value == 7


async def test_structured_unsupported_uses_fallback_then_errors_cleanly() -> None:
    service = LLMService(StubModel("no-structured"), StubModel(Answer(value=1)))  # type: ignore[arg-type]
    assert (await service.generate_structured(MESSAGES, Answer)).value == 1

    only_bad = LLMService(StubModel("no-structured"))  # type: ignore[arg-type]
    with pytest.raises(LLMCapabilityError):
        await only_bad.generate_structured(MESSAGES, Answer)


def test_openrouter_timeout_is_milliseconds() -> None:
    chat = build_chat_model("openrouter", "openrouter/free", make_settings(llm_timeout_seconds=45))
    assert chat.request_timeout == 45_000
    assert chat.max_retries == 0


@pytest.mark.parametrize(
    ("model", "effort", "expected"),
    [("gemini-3.6-flash", "low", "low"), ("gemini-2.5-flash", "low", None), ("gemini-3.6-flash", "none", None)],
)
def test_gemini_thinking_level(model: str, effort: str, expected: str | None) -> None:
    chat = build_chat_model("gemini", model, make_settings(llm_reasoning_effort=effort))
    assert chat.thinking_level == expected
