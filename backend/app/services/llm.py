"""Provider-agnostic LLM service.

This is the ONLY module that knows about Gemini, Groq, or OpenRouter. Graphs,
routes, and services call `LLMService.generate_text` / `generate_structured`
and never import a provider SDK.

Provider and model come from `LLM_PROVIDER` / `LLM_MODEL`. An optional
fallback (`LLM_FALLBACK_PROVIDER` / `LLM_FALLBACK_MODEL`) is used
automatically when the primary provider fails, e.g. on a free-tier rate limit.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any, Protocol, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, SecretStr

from app.config import Settings
from app.services.retry import is_rate_limit_error

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)


# --------------------------------------------------------------------- errors
class LLMError(Exception):
    """Base class for LLM failures. Messages never include API keys."""


class LLMRateLimitError(LLMError):
    """All configured providers are rate-limited."""


class LLMProviderError(LLMError):
    """A provider failed for a reason other than rate limiting."""


class LLMCapabilityError(LLMError):
    """The configured model cannot do what was asked (e.g. structured output)."""


# ------------------------------------------------------------------ providers
def _build_gemini(model: str, api_key: SecretStr, settings: Settings) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    extra: dict[str, Any] = {}
    # thinking_level only exists on Gemini 3+; deep thinking adds many seconds per call.
    if settings.llm_reasoning_effort != "none" and model.startswith("gemini-3"):
        extra["thinking_level"] = settings.llm_reasoning_effort
    return ChatGoogleGenerativeAI(
        model=model,
        api_key=api_key,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        **extra,
    )


def _build_groq(model: str, api_key: SecretStr, settings: Settings) -> BaseChatModel:
    from langchain_groq import ChatGroq

    return ChatGroq(
        model=model,
        api_key=api_key,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def _build_openrouter(model: str, api_key: SecretStr, settings: Settings) -> BaseChatModel:
    from langchain_openrouter import ChatOpenRouter

    return ChatOpenRouter(
        model=model,
        api_key=api_key,
        temperature=settings.llm_temperature,
        # ChatOpenRouter's timeout is in MILLISECONDS (maps to SDK timeout_ms).
        timeout=int(settings.llm_timeout_seconds * 1000),
        # Each SDK retry unit widens its backoff window by ~150 s, which would
        # stall a chat request for minutes. Fallback/failure handling happens in
        # LLMService instead.
        max_retries=0,
    )


_Builder = Callable[[str, SecretStr, Settings], BaseChatModel]

# provider name -> (builder, settings attribute holding its API key)
_PROVIDERS: dict[str, tuple[_Builder, str]] = {
    "gemini": (_build_gemini, "google_api_key"),
    "groq": (_build_groq, "groq_api_key"),
    "openrouter": (_build_openrouter, "openrouter_api_key"),
}


def build_chat_model(provider: str, model: str, settings: Settings) -> BaseChatModel:
    """Instantiate the LangChain chat model for `provider`."""
    if provider not in _PROVIDERS:
        raise ValueError(f"Unsupported LLM provider: {provider!r}")
    builder, key_attr = _PROVIDERS[provider]
    api_key: SecretStr | None = getattr(settings, key_attr)
    if api_key is None or not api_key.get_secret_value().strip():
        raise ValueError(f"{key_attr.upper()} is required for LLM provider '{provider}'")
    return builder(model, api_key, settings)


# -------------------------------------------------------------------- service
class LLM(Protocol):
    """Interface used by graphs/services (lets tests inject fakes)."""

    async def generate_text(self, messages: Sequence[BaseMessage]) -> str: ...

    async def generate_structured(
        self, messages: Sequence[BaseMessage], schema: type[SchemaT]
    ) -> SchemaT: ...


class LLMService:
    """Primary model with an optional fallback model behind one interface."""

    def __init__(
        self,
        primary: BaseChatModel,
        fallback: BaseChatModel | None = None,
        *,
        primary_name: str = "primary",
        fallback_name: str | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_name = primary_name
        self._fallback_name = fallback_name

    @classmethod
    def from_settings(cls, settings: Settings) -> LLMService:
        primary = build_chat_model(settings.llm_provider, settings.llm_model, settings)
        fallback = None
        fallback_name = None
        if settings.llm_fallback_provider != "none":
            fallback = build_chat_model(
                settings.llm_fallback_provider, settings.llm_fallback_model, settings
            )
            fallback_name = f"{settings.llm_fallback_provider}:{settings.llm_fallback_model}"
        logger.info(
            "llm configured",
            extra={
                "primary": f"{settings.llm_provider}:{settings.llm_model}",
                "fallback": fallback_name,
            },
        )
        return cls(
            primary,
            fallback,
            primary_name=f"{settings.llm_provider}:{settings.llm_model}",
            fallback_name=fallback_name,
        )

    async def generate_text(self, messages: Sequence[BaseMessage]) -> str:
        """Return the model's text reply."""
        result = await self._invoke_with_fallback(lambda m: m, list(messages))
        return _message_text(result).strip()

    async def generate_structured(
        self, messages: Sequence[BaseMessage], schema: type[SchemaT]
    ) -> SchemaT:
        """Return a validated instance of `schema` (Pydantic) from the model."""

        def to_structured(model: BaseChatModel) -> Runnable[Any, Any]:
            try:
                return model.with_structured_output(schema)
            except NotImplementedError as exc:
                raise LLMCapabilityError(
                    "The configured model does not support structured output. "
                    "Choose another LLM_MODEL or LLM_PROVIDER."
                ) from exc

        result = await self._invoke_with_fallback(to_structured, list(messages))
        if isinstance(result, schema):
            return result
        if isinstance(result, dict):
            return schema.model_validate(result)
        raise LLMCapabilityError(
            "The model returned an invalid structured response. "
            "Choose a model that supports structured output."
        )

    async def _invoke_with_fallback(
        self, wrap: Callable[[BaseChatModel], Runnable[Any, Any]], messages: list[BaseMessage]
    ) -> Any:
        """Try the primary model, then the fallback; map failures to typed errors."""
        attempts = [(self._primary_name, self._primary)]
        if self._fallback is not None:
            attempts.append((self._fallback_name or "fallback", self._fallback))

        last_exc: BaseException | None = None
        for name, model in attempts:
            try:
                return await wrap(model).ainvoke(messages)
            except LLMCapabilityError as exc:
                last_exc = exc
            except Exception as exc:  # provider SDKs raise many unrelated types
                last_exc = exc
            # Log type/status only: SDK messages can echo request details.
            logger.warning(
                "llm call failed",
                extra={
                    "model": name,
                    "error_type": type(last_exc).__name__,
                    "rate_limited": is_rate_limit_error(last_exc),
                },
            )

        assert last_exc is not None
        if isinstance(last_exc, LLMCapabilityError):
            raise last_exc
        if is_rate_limit_error(last_exc):
            raise LLMRateLimitError(
                "The AI model is rate-limited right now. Please try again in a minute."
            ) from last_exc
        raise LLMProviderError("The AI model is temporarily unavailable.") from last_exc


def _message_text(message: Any) -> str:
    """Extract plain text from an AIMessage (content may be a list of parts)."""
    text = getattr(message, "text", None)
    if isinstance(text, str):
        return text
    if callable(text):  # older langchain-core exposed .text() as a method
        return str(text())
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)
