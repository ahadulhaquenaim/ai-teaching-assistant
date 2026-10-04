"""Shared UI helpers: status badges, source rendering, document pickers."""

from __future__ import annotations

from typing import Any

import streamlit as st

import api_client

# Small layer on top of .streamlit/config.toml: things the theme can't express.
STYLE = """
<style>
/* Comfortable reading width for every page. */
[data-testid="stMainBlockContainer"] { max-width: 1120px; padding-top: 4.5rem; }

/* Home hero */
.hero-title { font-family: Literata, serif; font-weight: 600; font-size: clamp(2rem, 4vw, 2.9rem);
  line-height: 1.15; letter-spacing: -0.01em; margin: 0 0 0.75rem; color: #1E2A4A; max-width: 24ch; }
.hero-lede { font-size: 1.125rem; line-height: 1.6; color: #46526F; max-width: 60ch; margin: 0; }
/* Streamlit's own h1 rules zero the margin; this beats them. */
[data-testid="stMainBlockContainer"] p.hero-lede { margin-top: 1.25rem; }

/* Step cards: equal height, link pinned to the bottom. */
[class*="st-key-step-"] { height: 100%; background: #FFFFFF; border: 1px solid #D9DEEA;
  border-radius: 0.75rem; padding: 1.5rem 1.5rem 1.1rem; }
[data-testid="stColumn"]:has([class*="st-key-step-"]) > div { height: 100%; }
[class*="st-key-step-"] > div:last-child { margin-top: auto; }
[class*="st-key-step-"] [data-testid="stPageLink-NavLink"] { padding-left: 0; }
[class*="st-key-step-"] [data-testid="stPageLink-NavLink"] * { color: #2F4BD8; font-weight: 700; }
.step-num { display: inline-block; font-family: Literata, serif; font-weight: 600; font-size: 1.5rem;
  line-height: 1; padding: 0.15em 0.35em; margin-bottom: 0.6rem; color: #1E2A4A;
  background: linear-gradient(100deg, transparent 2%, #FFE45C 6%, #FFE45C 92%, transparent 97%); }
.step-title { font-family: Literata, serif; font-weight: 600; font-size: 1.3rem; margin: 0 0 0.4rem; }
.step-body { color: #46526F; line-height: 1.55; margin: 0; }

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
    """Document pages and web links, clearly separated."""
    pages = sorted({s["page"] for s in sources if s.get("type") == "document"})
    web = [s for s in sources if s.get("type") == "web"]
    if pages:
        st.caption("📄 **From your document:** " + " · ".join(f"Page {p}" for p in pages))
    if web:
        links = "\n".join(f"- 🌐 [{w['title']}]({w['url']})" for w in web)
        st.caption("**Web sources (external, not course material):**")
        st.markdown(links)


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
