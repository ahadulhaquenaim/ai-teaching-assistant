# AI Teaching Assistant

Upload PDF/DOCX → chat with it (page citations) → generate quizzes. FastAPI backend (`backend/`), Streamlit frontend (`frontend/`), LangGraph, Gemini + OpenRouter fallback, Pinecone, MongoDB Atlas. Free tiers only ($0).

## Keep the flow doc current

`docs/project-flow.html` is the user's main way to learn the project flow. After any change to a flow (ingestion, chat/quiz graphs, web search, LLM layer, API routes, config, data stores, frontend pages), run the `update-flow-docs` skill (`/update-flow-docs`) before finishing the task. It edits the stale sections, bumps the date, and adds a Changelog line.

## Commands

- Run everything: `./run_local.sh` (backend :8000, frontend :8501)
- Backend tests: `cd backend && env -u GOOGLE_API_KEY .venv/bin/pytest`
- Frontend tests: `cd frontend && .venv/bin/pytest`
- Python 3.12; each of `backend/` and `frontend/` has its own `.venv`

## Architecture rules

- Routes in `api/` stay thin; logic lives in `services/`; LangGraph graphs in `graphs/`; every prompt lives in `prompts/`.
- `services/llm.py` is the ONLY module that knows Gemini/Groq/OpenRouter. Graphs and services use the `LLM`, `Embedder`, `VectorStore`, `WebSearcher` protocols, never a provider SDK.
- Tests inject fakes via `create_app(services=...)`. No test may call a real external API.
- External calls that can rate-limit go through `with_retry` (`services/retry.py`).
- Pinecone: one namespace per document (`namespace = document_id`). The vector store does not know users.

## Security rules

- Every repo/endpoint keeps the ownership check. `get_current_user` returns fixed `local-dev-user` for now.
- `document_id` comes from an ownership-checked DB lookup, never from the client, before any Pinecone query.
- Web search content is untrusted: sanitized, placed only inside `<untrusted_web_content>`.
- Never log or print API keys (settings use `SecretStr`). Do not read or paste values from `backend/.env`.

## Scope decisions (do not reverse unless the user asks)

- Google login deferred. Local use only; no deployment work (Render/Streamlit Cloud files are parked).
- Tavily dropped; web search = DuckDuckGo (`ddgs`) only.
- LLM: Gemini `gemini-3.6-flash` primary, OpenRouter `openrouter/free` fallback. User chose to keep this despite the ~5 RPM Gemini limit; do not re-propose a model split.
- Quotas: 95 documents total, 5 per user (Pinecone Starter 100-namespace cap).
- Embeddings: Gemini, 768 dims, cosine.

## Gotchas

- A `GOOGLE_API_KEY` exported in the shell overrides `backend/.env`. Run servers/tests with `env -u GOOGLE_API_KEY ...` (`run_local.sh` already unsets it).
- Free-tier LLM is slow and rate-limited: one chat turn is 2-4 LLM calls; quiz generation can take 20-60 s.
