"""Shared UI helpers: status badges, source rendering, document pickers."""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

import api_client

# Small layer on top of .streamlit/config.toml: things the theme can't express.
STYLE = """
<style>
/* Comfortable reading width for every page. */
[data-testid="stMainBlockContainer"] { max-width: 1180px; padding-top: 3rem; }
/* Home is a landing page, not a reading page: give the hero and steps more room. */
[data-testid="stMainBlockContainer"]:has(.hero) { max-width: 1440px; padding-left: 3rem; padding-right: 3rem; }

/* Home hero: promise and actions on the left, a preview of a cited answer on the right.
   Sized to fit above the fold on a laptop screen. */
.hero { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: center;
  gap: clamp(1.5rem, 4vw, 3.5rem); padding: 0.5rem 0 1.75rem; }
.brand { display: flex; align-items: center; gap: 0.6rem; margin: 0 0 1.4rem; font-family: Literata, serif;
  font-weight: 600; font-size: 1.05rem; color: #1E2A4A; }
/* A page with a folded corner: ink square, highlighter fold. */
.brand-mark { width: 1.6rem; height: 1.6rem; border-radius: 0.35rem; flex: none;
  background: linear-gradient(225deg, #FFE45C 0 28%, #1E2A4A 28% 100%); }
[data-testid="stMainBlockContainer"] h1.hero-title { font-family: Literata, serif; font-weight: 600;
  font-size: clamp(2rem, 3.4vw, 3.2rem); line-height: 1.1; letter-spacing: -0.015em; margin: 0; padding: 0;
  color: #1E2A4A; max-width: 20ch; }
[data-testid="stMainBlockContainer"] p.hero-lede { font-size: 1.1rem; line-height: 1.6; color: #46526F;
  max-width: 54ch; margin: 1.1rem 0 1.6rem; }
.hero-actions { display: flex; flex-wrap: wrap; gap: 0.75rem; }
.hero-actions a { display: inline-block; font-weight: 700; text-decoration: none !important;
  padding: 0.75rem 1.3rem; border-radius: 0.65rem; transition: background 0.15s ease, border-color 0.15s ease; }
.btn-primary { background: #2F4BD8; color: #FFFFFF !important; border: 1.5px solid #2F4BD8; }
.btn-primary:hover { background: #2238B0; border-color: #2238B0; }
.btn-quiet { color: #1E2A4A !important; border: 1.5px solid #C5CDE3; background: #FFFFFF; }
.btn-quiet:hover { border-color: #1E2A4A; }

/* Preview: an ink "window" holding one chat turn and one quiz question. */
.preview { background: #1E2A4A; color: #E6EAF5; border-radius: 1.1rem; padding: 1.1rem 1.1rem 1.25rem;
  font-size: 0.93rem; line-height: 1.5; box-shadow: 0 24px 48px -28px rgba(30, 42, 74, 0.7); }
.preview p { margin: 0; }
.preview-bar { display: flex; justify-content: space-between; align-items: center; padding: 0 0.25rem 0.9rem;
  margin-bottom: 0.9rem; border-bottom: 1px solid #34446F; font-size: 0.82rem; color: #AEB8D6; }
.preview-bar .ready { color: #7BE0A6; font-weight: 700; }
.preview-bar .ready::before { content: ""; display: inline-block; width: 0.45rem; height: 0.45rem;
  border-radius: 50%; background: #7BE0A6; margin-right: 0.4rem; vertical-align: 0.08rem; }
.msg { border-radius: 0.8rem; padding: 0.7rem 0.9rem; margin-bottom: 0.6rem; }
.msg-user { background: #2F4BD8; color: #FFFFFF; margin-left: auto; width: fit-content; max-width: 85%; }
.msg-ai { background: #2B3A62; max-width: 92%; }
.cites { display: flex; gap: 0.4rem; margin-top: 0.55rem !important; }
/* The one animated moment: highlighter swipes across each citation once. */
.cite { font-weight: 700; font-size: 0.8rem; color: #1E2A4A; padding: 0.12rem 0.55rem; border-radius: 0.3rem;
  background: linear-gradient(#FFE45C, #FFE45C) no-repeat left / 100% 100%, #C9D1E6;
  animation: swipe 0.5s ease-out 0.5s both; }
.cite + .cite { animation-delay: 0.8s; }
@keyframes swipe { from { background-size: 0% 100%, auto; } }
.quiz { background: #F6F7FB; color: #1E2A4A; border-radius: 0.8rem; padding: 0.8rem 0.9rem; margin-top: 0.9rem; }
.quiz-q { font-weight: 700; margin-bottom: 0.5rem !important; }
.opt { border: 1px solid #D9DEEA; background: #FFFFFF; border-radius: 0.5rem; padding: 0.35rem 0.7rem;
  margin-top: 0.35rem !important; color: #46526F; }
.opt-right { border-color: #7BE0A6; background: #E8FAF0; color: #1E2A4A; font-weight: 700; }
.opt-right::after { content: "Correct"; float: right; font-size: 0.78rem; color: #1E7A4C; }
@media (prefers-reduced-motion: reduce) { .cite { animation: none; } }
@media (max-width: 860px) {
  .hero { grid-template-columns: 1fr; }
}

/* How it works: one slim strip, steps separated by rules rather than boxed. */
.st-key-steps { border-top: 1px solid #D9DEEA; padding-top: 1.25rem; }
.st-key-steps [data-testid="stColumn"] + [data-testid="stColumn"] { border-left: 1px solid #D9DEEA;
  padding-left: 1.5rem; }
@media (max-width: 640px) {
  .st-key-steps [data-testid="stColumn"] + [data-testid="stColumn"] { border-left: 0; padding-left: 0; }
}
.st-key-steps [data-testid="stPageLink-NavLink"] { padding-left: 0; }
.st-key-steps [data-testid="stPageLink-NavLink"] * { color: #2F4BD8; font-weight: 700; }
.step-title { font-family: Literata, serif; font-weight: 600; font-size: 1.15rem; margin: 0 0 0.25rem;
  display: flex; align-items: center; gap: 0.55rem; }
.step-num { display: inline-grid; place-items: center; width: 1.6rem; height: 1.6rem; border-radius: 50%;
  font-size: 0.9rem; background: #FFE45C; color: #1E2A4A; }
.step-body { color: #46526F; line-height: 1.5; margin: 0; }

/* Chat: input bar shares the content column. */
[data-testid="stBottomBlockContainer"] { max-width: 1120px; padding-bottom: 1.5rem; }
[data-testid="stBottom"] > div { background: #F6F7FB; }
[data-testid="stChatInput"] > div { background: #FFFFFF; border: 1.5px solid #C5CDE3; border-radius: 0.9rem;
  box-shadow: 0 6px 20px -12px rgba(30, 42, 74, 0.35); }
[data-testid="stChatInput"] > div:focus-within { border-color: #2F4BD8; }
[data-testid="stChatInputTextArea"] { color: #1E2A4A; }
[data-testid="stChatInputSubmitButton"]:not(:disabled) { background: #2F4BD8; color: #FFFFFF; }

/* Chat: user on the right in an ink-tinted bubble, assistant as a white card. */
[data-testid="stChatMessage"] { border-radius: 0.9rem; padding: 1rem 1.25rem; margin-bottom: 0.75rem; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  flex-direction: row-reverse; margin-left: auto; width: fit-content; max-width: 80%;
  background: #E3E9FF; border: 1px solid #CBD5FB; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
  margin-right: 0.75rem; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
  background: #FFFFFF; border: 1px solid #D9DEEA; }
[data-testid="stChatMessageAvatarUser"] { background: #1E2A4A; color: #FFFFFF; }
[data-testid="stChatMessageAvatarAssistant"] { background: #FFE45C; color: #1E2A4A; }

/* Sources under an answer: page chips in highlighter yellow (same as the home preview),
   web links as outlined chips so they never pass for course material. */
.sources { display: flex; flex-direction: column; gap: 0.5rem; margin-top: 0.4rem; padding-top: 0.75rem;
  border-top: 1px solid #E6E9F2; }
.src-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; }
.src-label { font-size: 0.82rem; font-weight: 600; color: #46526F; margin-right: 0.2rem; }
.src-chip { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.82rem; font-weight: 700;
  padding: 0.2rem 0.65rem; border-radius: 999px; line-height: 1.4; }
.src-page { background: #FFE45C; color: #1E2A4A; }
.src-page::before { content: ""; width: 0.55rem; height: 0.7rem; border-radius: 0.1rem; flex: none;
  background: linear-gradient(225deg, #FFE45C 0 30%, #1E2A4A 30% 100%); }
.src-web { max-width: 22rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block;
  background: #FFFFFF; color: #2F4BD8 !important; border: 1.5px solid #C5CDE3; text-decoration: none !important; }
.src-web:hover { border-color: #2F4BD8; background: #F0F3FF; }

/* Keyboard focus stays visible on links and buttons. */
a:focus-visible, button:focus-visible { outline: 2px solid #2F4BD8; outline-offset: 2px; }
</style>
"""


def apply_style() -> None:
    st.html(STYLE)


STATUS_BADGE = {
    "ready": ("Ready", "green", ":material/check_circle:"),
    "processing": ("Processing", "orange", ":material/progress_activity:"),
    "failed": ("Failed", "red", ":material/error:"),
}


def status_badge(status: str) -> None:
    label, color, icon = STATUS_BADGE.get(status, (status.title(), "gray", None))
    st.badge(label, color=color, icon=icon)  # type: ignore[arg-type]


def show_error(exc: api_client.APIError) -> None:
    st.error(exc.message, icon=":material/error:")


def render_sources(sources: list[dict[str, Any]]) -> None:
    """Document pages and web links as chips, in separate rows."""
    pages = sorted({s["page"] for s in sources if s.get("type") == "document"})
    web = [s for s in sources if s.get("type") == "web"]
    rows = []
    if pages:
        chips = "".join(f'<span class="src-chip src-page">Page {int(p)}</span>' for p in pages)
        rows.append(f'<div class="src-row"><span class="src-label">From your document</span>{chips}</div>')
    if web:
        # Web titles and URLs are untrusted: escape them and only link http(s).
        chips = "".join(
            f'<a class="src-chip src-web" href="{html.escape(w["url"])}" target="_blank" rel="noopener noreferrer"'
            f' title="{html.escape(w["url"])}">{html.escape(w.get("title") or w["url"])}</a>'
            for w in web
            if str(w.get("url", "")).startswith(("http://", "https://"))
        )
        rows.append(f'<div class="src-row"><span class="src-label">From the web, not course material</span>{chips}</div>')
    if rows:
        st.html(f'<div class="sources">{"".join(rows)}</div>')


def ready_documents() -> list[dict[str, Any]]:
    """Ready documents, or an empty list after showing an error."""
    try:
        return [d for d in api_client.list_documents() if d["status"] == "ready"]
    except api_client.APIError as exc:
        show_error(exc)
        return []


def document_picker(docs: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """Selectbox of documents; remembers the choice across pages."""
    if not docs:
        st.info("No ready documents yet. Upload one on the **Documents** page.", icon=":material/info:")
        return None
    ids = [d["id"] for d in docs]
    remembered = st.session_state.get("selected_document_id")
    index = ids.index(remembered) if remembered in ids else 0
    doc_id = st.selectbox(
        "Document",
        ids,
        index=index,
        format_func=lambda i: next(d["filename"] for d in docs if d["id"] == i),
        key=key,
    )
    st.session_state["selected_document_id"] = doc_id
    return next(d for d in docs if d["id"] == doc_id)
