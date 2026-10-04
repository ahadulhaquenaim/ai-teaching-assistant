"""Application configuration.

All settings come from environment variables (or a local `.env` file during
development) via pydantic-settings. Secrets are typed as `SecretStr` so they are
never printed in logs, reprs, or error messages.

Provider credentials are validated conditionally: only the API keys of the LLM
providers actually selected (primary + fallback) are required.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["gemini", "groq", "openrouter"]
FallbackProvider = Literal["gemini", "groq", "openrouter", "none"]

# Maps each LLM provider to the settings attribute holding its API key.
_PROVIDER_KEY_FIELDS: dict[str, str] = {
    "gemini": "google_api_key",
    "groq": "groq_api_key",
    "openrouter": "openrouter_api_key",
}


class Settings(BaseSettings):
    """Typed application settings loaded from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app
    app_name: str = "AI Teaching Assistant"
    environment: Literal["development", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # "pretty" = colored readable lines (local dev); "json" = one JSON object per line.
    log_format: Literal["pretty", "json"] = "pretty"
    # Comma-separated list of allowed CORS origins (the Streamlit app URL).
    cors_origins: str = "http://localhost:8501"

    # ---------------------------------------------------------------- mongo
    mongodb_uri: SecretStr
    mongodb_db_name: str = "ai_teaching_assistant"
    mongodb_timeout_ms: int = Field(default=10_000, gt=0)

    # NOTE(auth): Google login (JWT issuance/verification, google_client_id,
    # jwt_secret, etc.) was deferred to focus on the RAG pipeline first.
    # `core.dependencies.get_current_user` returns a fixed local user instead.
    # Re-add JWT/Google settings here when that phase is implemented.

    # ------------------------------------------------------------------- LLM
    llm_provider: LLMProvider = "gemini"
    llm_model: str = "gemini-3.6-flash"
    llm_fallback_provider: FallbackProvider = "openrouter"
    llm_fallback_model: str = "openrouter/free"
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)
    # Reasoning depth for thinking models (Gemini 3+: thinking_level).
    # "low" keeps chat latency reasonable; "none" leaves the model default.
    llm_reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] = "low"

    # Gemini API key is shared by the Gemini LLM and Gemini embeddings.
    google_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None

    # ------------------------------------------------------------ embeddings
    embedding_model: str = "gemini-embedding-001"
    embedding_dimension: int = Field(default=768, gt=0)
    embedding_batch_size: int = Field(default=50, gt=0)
    # Pause between embedding batches to stay under free-tier per-minute limits.
    embedding_batch_delay_seconds: float = Field(default=1.0, ge=0)

    # -------------------------------------------------------------- pinecone
    pinecone_api_key: SecretStr | None = None
    pinecone_index_name: str = "ai-teaching-assistant"
    pinecone_upsert_batch_size: int = Field(default=100, gt=0)
    retrieval_top_k: int = Field(default=5, gt=0)
    # Recent chat messages used to rewrite follow-up questions (spec: 6-10).
    chat_history_messages: int = Field(default=8, ge=0, le=20)

    # ------------------------------------------------------------- ingestion
    max_upload_size_mb: int = Field(default=25, gt=0)
    chunk_size: int = Field(default=1000, gt=0)
    chunk_overlap: int = Field(default=150, ge=0)
    # Pinecone Starter allows 100 namespaces per index (one per document).
    max_documents_total: int = Field(default=95, gt=0)
    max_documents_per_user: int = Field(default=5, gt=0)
    # DOCX files have no real pages; text is split into ~page-sized sections.
    docx_chars_per_page: int = Field(default=3000, gt=0)
    # PDFs averaging fewer extractable chars per page are treated as scanned.
    min_avg_chars_per_page: int = Field(default=50, ge=0)
    # Max characters of document text sent to the LLM for the summary.
    summary_max_input_chars: int = Field(default=12_000, gt=0)

    # ------------------------------------------------------------------ quiz
    quiz_max_questions: int = Field(default=10, ge=1, le=20)
    # Regeneration rounds for invalid questions (spec: max 2).
    quiz_max_retries: int = Field(default=2, ge=0, le=5)
    # Chunks given to the question generator (sampled or retrieved).
    quiz_context_chunks: int = Field(default=12, ge=1, le=50)
    quiz_context_max_chars: int = Field(default=14_000, gt=0)

    # ------------------------------------------------------------ web search
    # DuckDuckGo (ddgs) needs no API key. Tavily was dropped: its free tier
    # now requires a payment card, which conflicts with the $0 constraint.
    web_search_daily_limit: int = Field(default=20, ge=0)
    web_search_max_results: int = Field(default=5, ge=1, le=10)
    web_search_timeout_seconds: float = Field(default=15.0, gt=0)
    # ddgs engine; "duckduckgo" only (ddgs "auto" would also query Google/Bing/etc.).
    web_search_backend: str = "duckduckgo"
    web_search_region: str = "us-en"
    # Web snippets are truncated before reaching the LLM.
    web_search_max_chars_per_result: int = Field(default=1200, gt=0)
    web_search_cache_enabled: bool = True

    @model_validator(mode="after")
    def _validate_cross_field(self) -> Settings:
        """Validate rules that depend on more than one field."""
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")

        # Only the selected providers need credentials.
        selected = {self.llm_provider}
        if self.llm_fallback_provider != "none":
            selected.add(self.llm_fallback_provider)
        for provider in sorted(selected):
            key: SecretStr | None = getattr(self, _PROVIDER_KEY_FIELDS[provider])
            if key is None or not key.get_secret_value().strip():
                env_name = _PROVIDER_KEY_FIELDS[provider].upper()
                raise ValueError(
                    f"{env_name} is required because LLM provider '{provider}' is selected"
                )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins parsed from the comma-separated env value."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance (also usable as a FastAPI dependency)."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
