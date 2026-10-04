---
name: update-flow-docs
description: Update docs/project-flow.html so it matches the current code. Use after changing any flow (ingestion, chat graph, quiz graph, web search, LLM layer, API routes, config, data stores, frontend pages) or when the user asks to refresh, sync, or update the project flow doc or project report.
---

# Update project flow doc

Keep `docs/project-flow.html` accurate. It is the single place the user reads to learn the whole project flow.

## Steps

1. **Find what changed.** Not a git repo, so compare code to doc directly. If the user named a change, start there. Otherwise read the sources listed under each section ("Source:" line) and check them against the doc.
   - Backend: `backend/app/` (`main.py`, `api/`, `services/`, `graphs/`, `prompts/`, `db/`, `config.py`)
   - Frontend: `frontend/` (`app.py`, `ui.py`, `api_client.py`, `pages/`)
   - Run scripts: `run_local.sh`, `backend/.env.example`
2. **Read `docs/project-flow.html`** fully before editing.
3. **Edit only the sections that are stale.** Use Edit, not a full rewrite. Keep the existing structure:
   - Flow diagrams use the CSS classes `.flow`, `.step` (`fe` / `api` / `svc` / `graph` / `ext` / `db`), `.arrow`, `.branch`. Reuse them; do not add libraries or CDN links. File stays self-contained.
   - New top-level section: add a matching `<a>` in the `<nav>` and keep numbering consistent.
   - Tables: API reference (section 10), config defaults (section 12), Mongo collections (section 9), tests (section 14). Update when routes, settings, collections or test files change.
4. **Verify facts against code**, never from memory: route paths and status codes, setting names and defaults, node names and edge order in the graphs, limits and thresholds. If unsure, read the file.
5. **Update the date** in the `Last updated:` line near the top.
6. **Add one line to the Changelog** (section 17), newest first: `<li><b>YYYY-MM-DD</b> – what changed in the flow.</li>`.
7. **Report** in 2-3 lines: which sections changed and why. Mention any part of the code you could not verify.

## Rules

- Document what the code does now, not plans. Parked work (auth, deployment) stays labeled as deferred.
- Do not leave removed features in the doc (for example Tavily is dropped; do not reintroduce it).
- Do not paste secrets or real keys from `backend/.env`.
- Keep wording plain and short; the reader is learning the project.
- If no section is stale, say so and change nothing (do not bump the date or changelog).
