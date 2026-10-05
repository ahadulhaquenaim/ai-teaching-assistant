# Setup and deployment reference

Detailed service setup, provider options and the parked deployment guides.
The README has the short version. For how the app works inside, see the
[project flow doc](project-flow.html).

## MongoDB Atlas

1. **Create a free M0 cluster:** cloud.mongodb.com → create a project → *Create
   cluster* → **M0 Free** → any region.
2. **Database user:** Database Access → add a user with a password.
3. **Network access:** Network Access → add your IP (or `0.0.0.0/0`, which is
   required later for Render, whose free instances have no fixed outbound IP).
   Protect the cluster with a strong password.
4. **Connection string:** cluster → Connect → Drivers → Python → copy the
   `mongodb+srv://...` string, insert the password, and set it as `MONGODB_URI`.
5. The database (`MONGODB_DB_NAME`, default `ai_teaching_assistant`) and all
   indexes are created automatically at startup.

## Pinecone

1. **Create a free Starter account** at pinecone.io.
2. **Create the index:** Database → Create index → tick **Custom settings**
   (do **not** pick a Pinecone-hosted embedding model; embeddings come from Gemini):
   - Name: `ai-teaching-assistant` (= `PINECONE_INDEX_NAME`)
   - Vector type: **Dense**, Metric: **cosine**
   - **Dimension: 768**. This must equal `EMBEDDING_DIMENSION`.
   - Cloud / region: AWS `us-east-1` (the free region)
3. **API key:** API keys → create → set `PINECONE_API_KEY`.

If you change `EMBEDDING_DIMENSION` (768, 1536 or 3072), create a new index
with the same dimension. Free tier: 100 namespaces per index (hence the
95-document cap) and 2 GB storage.

## Gemini (LLM + embeddings)

- **API key:** aistudio.google.com/apikey → create a key → `GOOGLE_API_KEY`.
  No billing is needed for the free tier.
- Models: `LLM_MODEL=gemini-3.6-flash`, `EMBEDDING_MODEL=gemini-embedding-001`,
  `EMBEDDING_DIMENSION=768`. `LLM_REASONING_EFFORT=low` gives faster answers.
- Free-tier quotas change over time; check aistudio.google.com/rate-limit. In
  testing, `gemini-3.6-flash` allowed about 5 requests per minute, so the
  OpenRouter fallback often takes over.
- Google may use free-tier API data to improve its products; don't upload
  confidential documents.

## OpenRouter (fallback LLM)

- **API key:** openrouter.ai/keys → create a key → `OPENROUTER_API_KEY`.
- As fallback: `LLM_FALLBACK_PROVIDER=openrouter`, `LLM_FALLBACK_MODEL=openrouter/free`.
  As primary: `LLM_PROVIDER=openrouter`, `LLM_MODEL=openrouter/free`.
- **Not every OpenRouter model is free.** Any model id other than a free one
  may cost money. Free limits: about 20 requests per minute and 50 per day
  without purchased credits. A low credit limit on the key adds safety.
- To run without a fallback: `LLM_FALLBACK_PROVIDER=none`.

## Groq (optional)

- **API key:** console.groq.com → API Keys → `GROQ_API_KEY`.
- `LLM_PROVIDER=groq` and `LLM_MODEL=<groq-model>` (e.g. `llama-3.3-70b-versatile`),
  or use it as the fallback with `LLM_FALLBACK_PROVIDER=groq`.

## Web search (DuckDuckGo)

No API key. `WEB_SEARCH_DAILY_LIMIT` sets searches per user per day
(default 20). Identical searches are cached for 24 hours and don't count.

---

## Google login *(later phase)*

Login is currently disabled; every request is the fixed user `local-dev-user`.

1. Create a Google Cloud project at console.cloud.google.com.
2. OAuth consent screen: External, app name, support email; scopes `openid`, `email`, `profile`.
3. Credentials → Create credentials → OAuth client ID → *Web application*.
4. Authorized redirect URIs:
   - `http://localhost:8501/oauth2callback` (local)
   - `https://<your-app>.streamlit.app/oauth2callback` (Streamlit Community Cloud)
5. Uncomment the `[auth]` and `[auth.google]` blocks in
   `frontend/.streamlit/secrets.toml` and keep `expose_tokens = ["id"]`.
6. Backend (to implement): `POST /auth/google` verifies the ID token with
   `google.oauth2.id_token.verify_oauth2_token`, upserts the user by Google
   `sub`, and returns the app's own JWT. `get_current_user` in
   `backend/app/core/dependencies.py` then decodes that JWT.
   `frontend/api_client.py` already sends `Authorization: Bearer <jwt>`.

## Deploying the backend on Render *(later phase)*

1. Push the repo to GitHub.
2. Render dashboard → **New → Blueprint** → select the repo. `render.yaml`
   defines a free web service with `rootDir: backend` and health check `/health`.
3. Fill in the secrets: `MONGODB_URI`, `GOOGLE_API_KEY`, `PINECONE_API_KEY`,
   `OPENROUTER_API_KEY`, and `CORS_ORIGINS` (your Streamlit URL).
4. Deploy, then open `https://<service>.onrender.com/health` → `{"status":"ok","mongo":"ok"}`.

Free-tier limits: sleeps after ~15 minutes idle (30–60 s cold start), 512 MB
RAM (about 200 MB peak measured), no persistent disk, and a restart stops any
ingestion in progress (those documents are marked `failed`).

## Deploying the frontend on Streamlit Community Cloud *(later phase)*

1. share.streamlit.io → **Create app** → main file **`frontend/app.py`** → Python **3.12**.
2. Secrets: `BACKEND_URL = "https://<your-service>.onrender.com"`
   (plus the `[auth]` blocks once login is enabled).
3. Deploy, then put the app URL into the backend's `CORS_ORIGINS`.

## Production checklist *(for when we deploy)*

- [ ] `ENVIRONMENT=production` (hides `/docs`), `LOG_LEVEL=INFO`
- [ ] `CORS_ORIGINS` set to the exact Streamlit URL
- [ ] All secrets in the Render dashboard, none in the repo
- [ ] Atlas: strong password; network access `0.0.0.0/0`
- [ ] Pinecone index dimension = `EMBEDDING_DIMENSION` (768), metric cosine
- [ ] `LLM_FALLBACK_MODEL` is a free OpenRouter model; low credit limit on the key
- [ ] Limits reviewed: `MAX_UPLOAD_SIZE_MB`, `MAX_DOCUMENTS_TOTAL` (≤ 99), `MAX_DOCUMENTS_PER_USER`, `WEB_SEARCH_DAILY_LIMIT`, `QUIZ_MAX_QUESTIONS`
- [ ] `/health` returns `ok`; one test upload reaches `ready`
- [ ] **Enable Google login before inviting other users**
- [ ] Tests green in `backend/` and `frontend/`
