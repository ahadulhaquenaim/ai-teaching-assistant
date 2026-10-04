"""Tests for settings validation, including conditional LLM provider keys."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.config import Settings

BASE: dict[str, Any] = {
    "mongodb_uri": "mongodb://localhost:27017",
}


def make(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> Settings:
    # Remove provider keys set by conftest so each test controls them.
    for var in ("GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
                "LLM_PROVIDER", "LLM_FALLBACK_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    return Settings(_env_file=None, **{**BASE, **overrides})  # type: ignore[call-arg]


def test_groq_only_does_not_require_openrouter_key(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, llm_provider="groq", llm_fallback_provider="none",
             groq_api_key="g", openrouter_api_key="")
    assert s.llm_provider == "groq"


def test_openrouter_only_does_not_require_groq_key(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, llm_provider="openrouter", llm_model="openrouter/free",
             llm_fallback_provider="none", openrouter_api_key="o")
    assert s.llm_provider == "openrouter"


def test_missing_primary_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="GROQ_API_KEY is required"):
        make(monkeypatch, llm_provider="groq", llm_fallback_provider="none")


def test_blank_primary_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="GOOGLE_API_KEY is required"):
        make(monkeypatch, llm_provider="gemini", llm_fallback_provider="none",
             google_api_key="   ")


def test_missing_fallback_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="OPENROUTER_API_KEY is required"):
        make(monkeypatch, llm_provider="gemini", google_api_key="k",
             llm_fallback_provider="openrouter")


def test_gemini_with_openrouter_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, google_api_key="k", openrouter_api_key="o")
    assert (s.llm_provider, s.llm_fallback_provider) == ("gemini", "openrouter")


def test_invalid_provider_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        make(monkeypatch, llm_provider="anthropic", llm_fallback_provider="none")


def test_chunk_overlap_must_be_smaller(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="CHUNK_OVERLAP"):
        make(monkeypatch, google_api_key="k", llm_fallback_provider="none",
             chunk_size=100, chunk_overlap=100)


def test_secrets_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, google_api_key="super-secret-value", llm_fallback_provider="none")
    assert "super-secret-value" not in repr(s)
    assert "super-secret-value" not in str(s.model_dump())


def test_derived_values(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, google_api_key="k", llm_fallback_provider="none",
             cors_origins="http://a.com, http://b.com", max_upload_size_mb=25)
    assert s.cors_origin_list == ["http://a.com", "http://b.com"]
    assert s.max_upload_size_bytes == 25 * 1024 * 1024
