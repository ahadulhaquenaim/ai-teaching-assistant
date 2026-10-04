# AI Teaching Assistant

Upload a PDF or DOCX, chat with it, and generate quizzes from it. Answers are
grounded in the uploaded document with page citations. An optional
**Search the web** toggle adds clearly labeled web context. Everything runs on
free tiers: **total cost $0**.

| Layer | Technology (free tier) |
|---|---|
| Backend | FastAPI (Python 3.12) on **Render** free web service |
| Frontend | Streamlit (multipage) on **Streamlit Community Cloud** |
| Orchestration | LangChain + LangGraph |
| LLM | **Gemini** (primary) with automatic **OpenRouter** fallback; **Groq** supported |
| Embeddings | Gemini `gemini-embedding-001`, 768 dimensions |
| Vector DB | **Pinecone** Starter: one index, one namespace per document |
| Database | **MongoDB Atlas** M0 (PyMongo async API) |
| Web search | **DuckDuckGo** via `ddgs` (no API key) |

> **Status: local use only.** Google login and cloud deployment (Render +
> Streamlit Community Cloud) are planned for a later phase. The deployment files
> (`render.yaml`, `.python-version`) and the deployment sections below are ready
> for then. Today the app runs on your machine for one user.

## Quick start (local)

Needs Python 3.12 and free accounts for MongoDB Atlas, Pinecone and Google AI
Studio (OpenRouter is optional). Details for each are further down.

```bash
# 1. One-time setup
cd backend  && python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && cd ..
cd frontend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && cd ..
cp backend/.env.example backend/.env       # then fill in the keys

# 2. Run everything (backend :8000 + frontend :8501); Ctrl+C stops both
./run_local.sh
```

Open **http://localhost:8501**. The API docs are at http://localhost:8000/docs.
No `secrets.toml` is needed locally: the frontend talks to `http://localhost:8000`
by default.

Minimum `backend/.env` for local use (everything else has defaults):

```env
MONGODB_URI=mongodb+srv://...              # Atlas M0 connection string
GOOGLE_API_KEY=...                         # Gemini: LLM + embeddings
PINECONE_API_KEY=...                       # index: 768 dimensions, cosine
# Fallback model when Gemini is rate-limited (free, key from openrouter.ai/keys):
OPENROUTER_API_KEY=...
# ...or skip the fallback entirely:
# LLM_FALLBACK_PROVIDER=none
```

## Changes from the original spec

These were decided during the build; each one keeps the cost at $0.

| Topic | Decision | Why |
|---|---|---|
| Google login | **Deferred.** Every request is treated as one fixed user (`local-dev-user`). | Focus on the RAG pipeline first. Ownership checks are still in every endpoint and repository, so enabling auth later only replaces `get_current_user` in `backend/app/core/dependencies.py`. |
| Web search | **DuckDuckGo only**; Tavily removed. | Tavily's free tier now requires a payment card. |
| LLM | **Gemini primary, OpenRouter fallback**, Groq optional. | Gemini has a free API tier; OpenRouter `openrouter/free` absorbs Gemini rate limits. |
| Document quota | At most **95 documents in total** and **5 per user** (configurable). | Pinecone Starter allows 100 namespaces per index, and each document is one namespace. |
| DOCX pages | DOCX text is split into ~3000-character sections that act as "pages". | DOCX files have no fixed pages; citations refer to these sections. |

---

## Architecture

```text
Streamlit (frontend/)  --HTTP-->  FastAPI (backend/app)
                                    ├── api/          routes (thin)
                                    ├── services/     use-cases + external clients
                                    │     ├── llm.py           ONLY place that knows Gemini / Groq / OpenRouter
                                    │     ├── embeddings.py    Gemini embeddings (batched, retried)
                                    │     ├── vectorstore.py   Pinecone async client (namespace = document_id)
                                    │     ├── web_search.py    DuckDuckGo + daily limit + 24h cache
                                    │     └── ingestion.py     extract -> clean -> chunk -> embed -> upsert -> summary
                                    ├── graphs/       LangGraph Q&A graph and quiz graph
                                    ├── prompts/      every prompt lives here
                                    └── db/           MongoDB connection, indexes, repositories
```

**Upload:** `POST /documents` validates the file and returns `202` with an id
immediately. Ingestion runs as a FastAPI background task; the frontend polls
`GET /documents/{id}` until the status is `ready` or `failed`. Uploaded files
are held in memory only and never stored.

**Q&A graph:** `rewrite_query → retrieve_document → grade_documents →`
(if nothing is relevant: rephrase and retrieve once more) `→` with web search
on: `build_search_query → web_search → grade_web_results →` `generate_answer`.
When the document doesn't contain the answer, the reply is exactly
*"This is not covered in the document."*

**Quiz graph:** `select_content` (by topic, or chunks sampled across the whole
document) `→ generate_questions` (structured output) `→ validate`
(format, 4 options, exactly one correct answer, no duplicates, source page
exists, answer supported by the source) `→` regenerate only the invalid
questions, at most 2 times.

---

## Local setup

Requires **Python 3.12** (3.11+ works; deployment pins 3.12).

### Backend

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt        # runtime + test dependencies
cp .env.example .env                       # then fill in the values (see below)
uvicorn app.main:app --reload              # http://localhost:8000, docs at /docs
```

> **Watch out for shell variables.** Real environment variables override
> `.env`. If your shell profile exports `GOOGLE_API_KEY` (e.g. in `~/.zshrc`),
> the app uses that value instead of the one in `.env`. Remove it from the
> profile, or run `env -u GOOGLE_API_KEY uvicorn app.main:app --reload`.

### Frontend

```bash
cd frontend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # set BACKEND_URL
streamlit run app.py                       # http://localhost:8501
```

### Environment variables

All configuration comes from environment variables (`backend/.env` locally,
the Render dashboard in production). See `backend/.env.example` for the full,
commented list. Required:

| Variable | Where to get it |
|---|---|
| `MONGODB_URI` | MongoDB Atlas, see below |
| `GOOGLE_API_KEY` | Google AI Studio (Gemini LLM + embeddings) |
| `PINECONE_API_KEY` | Pinecone console |
| `OPENROUTER_API_KEY` | OpenRouter, needed while `LLM_FALLBACK_PROVIDER=openrouter` |

Only the keys of the **selected** LLM providers are required. With
`LLM_PROVIDER=gemini` and `LLM_FALLBACK_PROVIDER=none`, neither
`GROQ_API_KEY` nor `OPENROUTER_API_KEY` is needed. The app refuses to start,
with a clear message, if a selected provider's key is missing.

### Tests

```bash
cd backend && pytest -q      # 173 tests
cd frontend && pytest -q     # 15 tests (Streamlit AppTest + API client)
```

Every external service (LLM, Gemini, Groq, OpenRouter, Pinecone, DuckDuckGo,
MongoDB) is mocked. A test guard blocks all real network connections, so the
tests never spend API credits.

---

## Google OAuth *(later phase)*

Login is currently disabled; this is how to enable it later. Not needed for local use.

1. **Google Cloud project:** create one at console.cloud.google.com.
2. **OAuth consent screen:** External, app name, support email; scopes `openid`, `email`, `profile`.
3. **OAuth client:** Credentials → Create credentials → OAuth client ID → *Web application*.
4. **Authorized redirect URIs:**
   - `http://localhost:8501/oauth2callback` (local)
   - `https://<your-app>.streamlit.app/oauth2callback` (Streamlit Community Cloud)
5. **Streamlit OIDC config:** uncomment the `[auth]` and `[auth.google]` blocks in
   `frontend/.streamlit/secrets.toml` and keep `expose_tokens = ["id"]`, so the
   app can read Google's ID token from `st.user.tokens["id"]`.
6. **Backend token verification (to implement):** `POST /auth/google` verifies
   the ID token with `google.oauth2.id_token.verify_oauth2_token` (audience = your
   client ID), upserts the user by Google `sub`, and returns the app's own JWT.
   `get_current_user` then decodes that JWT instead of returning the fixed user.
   `frontend/api_client.py` already sends `Authorization: Bearer <jwt>` when one
   is in the session, and clears the session on HTTP 401.

---

## MongoDB Atlas

1. **Create a free M0 cluster:** cloud.mongodb.com → create a project → *Create
   cluster* → **M0 Free** → any region.
2. **Database user:** Database Access → add a user with a password.
3. **Network access:** Network Access → add IP → **`0.0.0.0/0`** (allow from
   anywhere). This is required for Render, whose free instances have no fixed
   outbound IP. Protect the cluster with a strong password.
4. **Connection string:** cluster → Connect → Drivers → Python → copy the
   `mongodb+srv://...` string, insert the password, and set it as `MONGODB_URI`.
5. **Database configuration:** the database (`MONGODB_DB_NAME`, default
   `ai_teaching_assistant`) and all indexes, including the 24-hour TTL index on
   `web_search_cache`, are created automatically at startup.

Collections: `users`, `documents`, `chat_sessions`, `chat_messages`,
`quizzes`, `quiz_attempts`, `web_search_usage`, `web_search_cache`.

---

## Pinecone

1. **Create a free Starter account** at pinecone.io.
2. **Create the index:** Database → Create index → tick **Custom settings**
   (do **not** pick a Pinecone-hosted embedding model; embeddings come from Gemini):
   - Name: `ai-teaching-assistant` (= `PINECONE_INDEX_NAME`)
   - Vector type: **Dense**, Metric: **cosine**
   - **Dimension: 768**. This must equal `EMBEDDING_DIMENSION`.
   - Cloud / region: AWS `us-east-1` (the free region)
3. **API key:** API keys → create → set `PINECONE_API_KEY`.

> **The index dimension must match the Gemini embedding output dimension.**
> If you change `EMBEDDING_DIMENSION` (768, 1536 or 3072), create a new index
> with the same dimension. The app also checks every embedding's length.

**Namespace strategy:** one namespace per document (`namespace = document_id`).
Vector ids are `document_id#chunk_index`; metadata holds `document_id`,
`page_number`, `chunk_index`, `text`. Queries only ever target the namespace
of a document the user owns, and results from any other document are dropped
as an extra check.

**Deleting a document** drops its namespace first, then deletes its chat
sessions, messages, quizzes and quiz attempts from MongoDB. If Pinecone fails,
the delete returns 503 and nothing is removed, so no vectors are orphaned.

**Free-tier limits:** 100 namespaces per index (hence the 95-document cap) and
2 GB storage.

---

## Gemini (LLM + embeddings)

- **API key:** aistudio.google.com/apikey → create a key → `GOOGLE_API_KEY`.
  No billing is needed for the free tier.
- **Embedding model:** `EMBEDDING_MODEL=gemini-embedding-001`.
- **Output dimension:** `EMBEDDING_DIMENSION=768` (must match Pinecone).
  Documents are embedded with task type `RETRIEVAL_DOCUMENT` and questions with
  `RETRIEVAL_QUERY`, in batches (`EMBEDDING_BATCH_SIZE`) with a pause between
  batches and exponential backoff on 429 errors.
- **Chat model:** `LLM_MODEL=gemini-3.6-flash`. `LLM_REASONING_EFFORT=low` sets
  Gemini 3's thinking level for faster answers.
- **Free-tier limits:** quotas are per model and change over time; check
  aistudio.google.com/rate-limit. In testing, `gemini-3.6-flash` allowed
  **5 requests per minute**. A chat turn uses 2–5 LLM calls, so rate limits are
  common, and the OpenRouter fallback takes over automatically.
- Google may use free-tier API data to improve its products; don't upload
  confidential documents.

## OpenRouter (fallback LLM)

- **API key:** openrouter.ai/keys → create a key → `OPENROUTER_API_KEY`.
- **Configure it as the fallback:** `LLM_FALLBACK_PROVIDER=openrouter`,
  `LLM_FALLBACK_MODEL=openrouter/free`.
- **Or as the primary provider:** `LLM_PROVIDER=openrouter`, `LLM_MODEL=openrouter/free`.
- `openrouter/free` routes to a currently free model, for $0 experimentation
  when available.
- **Not every OpenRouter model is free.** Any other model id may cost money.
  OpenRouter does not make paid models free. Free-model availability and
  limits change over time.
- **Free limits:** about 20 requests per minute and **50 requests per day**
  without purchased credits. Setting a low credit limit on the key adds safety.
- If the selected free model can't produce structured output (used for
  grading and quizzes), the app raises a clear error and tries the other
  configured provider; pick a different model if this keeps happening.

## Groq (optional)

- **API key:** console.groq.com → API Keys → `GROQ_API_KEY`.
- **Model configuration:** `LLM_PROVIDER=groq` and `LLM_MODEL=<groq-model>`, for
  example `llama-3.3-70b-versatile`. Groq can also be the fallback
  (`LLM_FALLBACK_PROVIDER=groq`, `LLM_FALLBACK_MODEL=...`).
- **Free-tier limits:** per-model request and token limits, listed in the Groq console.

Switching providers is configuration only; no graph, route, prompt or
repository code changes.

## Web search (DuckDuckGo)

- No API key. `WEB_SEARCH_BACKEND=duckduckgo` keeps searches on DuckDuckGo
  (the `ddgs` "auto" mode would also query Google, Bing and others).
- **Daily limit:** `WEB_SEARCH_DAILY_LIMIT` searches per user per UTC day
  (default 20), enforced atomically in MongoDB. Failed searches are refunded.
  `GET /usage/web-search` returns the remaining searches.
- **Cache:** identical queries are cached for 24 hours; cache hits don't count
  against the limit.
- **Safety:** web text is sanitized (angle brackets and control characters
  removed, truncated) and placed in an `<untrusted_web_content>` section that
  the LLM is told to treat only as reference material. Answers keep
  **From your document** and **Additional context from the web** separate.
- If the web search fails, times out or the limit is reached, the answer uses
  the document only and says why.
- DuckDuckGo has no official quota but throttles bursts; the app retries once.

---

## Deploying the backend on Render *(later phase, not needed for local use)*

1. Push the repo to GitHub.
2. Render dashboard → **New → Blueprint** → select the repo. `render.yaml`
   defines a **free** web service with `rootDir: backend`, health check
   `/health`, and Python pinned by `.python-version` (3.12).
3. When prompted, fill in the secrets: `MONGODB_URI`, `GOOGLE_API_KEY`,
   `PINECONE_API_KEY`, `OPENROUTER_API_KEY`, and `CORS_ORIGINS` (your Streamlit
   URL, e.g. `https://your-app.streamlit.app`). Leave `GROQ_API_KEY` empty
   unless you use Groq.
4. Deploy, then open `https://<service>.onrender.com/health` → `{"status":"ok","mongo":"ok"}`.

**Free-tier limitations**

- **Sleeps after ~15 minutes idle.** The first request afterwards takes about
  30–60 s (cold start); the frontend shows *"Waking up the server..."* and retries.
- **512 MB RAM.** Measured: about 170 MB at startup and about 200 MB peak while
  ingesting a 60-page PDF. Runs with one worker.
- **No persistent disk.** Files are processed in memory and discarded.
- **Restarts end background tasks.** A restart or sleep stops any ingestion in
  progress. On startup, documents still marked `processing` are marked `failed`
  with a "please re-upload" message.
- **Free instance hours** are limited per month; one always-on-when-used
  service fits.

## Deploying the frontend on Streamlit Community Cloud *(later phase)*

1. share.streamlit.io → **Create app** → select the repo → main file
   **`frontend/app.py`** → Advanced settings → Python **3.12**.
2. **Secrets** (App settings → Secrets): paste
   ```toml
   BACKEND_URL = "https://<your-service>.onrender.com"
   ```
   (Add the `[auth]` blocks from `secrets.toml.example` once login is enabled.)
3. Deploy, then put the app URL into the backend's `CORS_ORIGINS` on Render.

---

## API

Swagger UI at `/docs` when `ENVIRONMENT` is not `production`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness + MongoDB check (503 when degraded) |
| POST | `/documents` | Upload PDF/DOCX → 202 + id (background ingestion) |
| GET | `/documents`, `/documents/{id}` | List / status polling |
| DELETE | `/documents/{id}` | Delete document, vectors, chats, quizzes |
| POST | `/chat/sessions` | New chat for a ready document |
| GET | `/chat/sessions?document_id=` | List sessions |
| GET/POST | `/chat/sessions/{id}/messages` | History / ask (`{"content","web_search"}`) |
| DELETE | `/chat/sessions/{id}` | Delete session + messages |
| POST | `/quizzes` | Generate a quiz |
| GET | `/quizzes?document_id=`, `/quizzes/{id}` | List / get (answers hidden until submitted) |
| POST | `/quizzes/{id}/submit` | Score (MCQ exact, short answers by LLM) |
| GET | `/usage/web-search` | Remaining web searches today |

Errors always look like `{"error": {"code": "...", "message": "..."}}`.

---

## Production checklist *(for when you deploy)*

- [ ] `ENVIRONMENT=production` (hides `/docs`), `LOG_LEVEL=INFO`
- [ ] `CORS_ORIGINS` set to the exact Streamlit URL
- [ ] All secrets set in the Render dashboard, none in the repo (`.env` and `secrets.toml` are git-ignored)
- [ ] Atlas: strong database password; network access `0.0.0.0/0` (needed for Render)
- [ ] Pinecone index dimension = `EMBEDDING_DIMENSION` (768), metric cosine
- [ ] `LLM_FALLBACK_MODEL` is a **free** OpenRouter model (`openrouter/free`); low credit limit on the key
- [ ] Limits reviewed: `MAX_UPLOAD_SIZE_MB`, `MAX_DOCUMENTS_TOTAL` (≤ 99), `MAX_DOCUMENTS_PER_USER`, `WEB_SEARCH_DAILY_LIMIT`, `QUIZ_MAX_QUESTIONS`
- [ ] `/health` returns `ok` after deploy; one test upload reaches `ready`
- [ ] **Before inviting other users: enable Google login.** With auth deferred, everyone who can reach the app shares the single `local-dev-user` account.
- [ ] Tests green: `pytest -q` in `backend/` and `frontend/`
