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

/* Home hero: blue panel, copy on the left, notes-page illustration on the right. */
.hero { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); align-items: center; gap: 2rem;
  background: #2F4BD8; color: #FFFFFF; border-radius: 1.25rem; padding: clamp(1.75rem, 4vw, 3.25rem);
  overflow: hidden; }
[data-testid="stMainBlockContainer"] h1.hero-title { font-family: Literata, serif; font-weight: 600;
  font-size: clamp(2rem, 4vw, 3rem); line-height: 1.12; letter-spacing: -0.01em; margin: 0; padding: 0;
  color: #FFFFFF; max-width: 18ch; }
[data-testid="stMainBlockContainer"] p.hero-lede { font-size: 1.125rem; line-height: 1.6; color: #DCE3FF;
  max-width: 46ch; margin: 1.25rem 0 1.75rem; }
.hero-cta { display: inline-block; background: #FFE45C; color: #1E2A4A !important; font-weight: 700;
  text-decoration: none !important; padding: 0.8rem 1.4rem; border-radius: 0.7rem;
  box-shadow: 0 4px 0 #C9A800; transition: transform 0.1s ease, box-shadow 0.1s ease; }
.hero-cta:hover { transform: translateY(-1px); box-shadow: 0 5px 0 #C9A800; }
.hero-cta:active { transform: translateY(3px); box-shadow: 0 1px 0 #C9A800; }
.hero-art { width: 100%; max-width: 380px; justify-self: end; }
@media (max-width: 760px) {
  .hero { grid-template-columns: 1fr; }
  .hero-art { max-width: 300px; justify-self: center; }
}

/* Step cards: one highlighter colour each, equal height, link pinned to the bottom. */
.st-key-step-1 { --hl: #7CC4FF; --tint: #EAF5FF; --edge: #BFE0FF; }
.st-key-step-2 { --hl: #FFE45C; --tint: #FFF8D6; --edge: #F5E28A; }
.st-key-step-3 { --hl: #FF8FB8; --tint: #FFEEF4; --edge: #FBC6DA; }
[class*="st-key-step-"] { height: 100%; background: var(--tint); border: 1px solid var(--edge);
  border-radius: 1rem; padding: 1.5rem 1.5rem 1.1rem; }
[data-testid="stColumn"]:has([class*="st-key-step-"]) > div { height: 100%; }
[class*="st-key-step-"] > div:last-child { margin-top: auto; }
[class*="st-key-step-"] [data-testid="stPageLink-NavLink"] { padding-left: 0; }
[class*="st-key-step-"] [data-testid="stPageLink-NavLink"] * { color: #1E2A4A; font-weight: 700; }
.step-num { display: inline-block; font-family: Literata, serif; font-weight: 600; font-size: 1.5rem;
  line-height: 1; padding: 0.15em 0.4em; margin-bottom: 0.6rem; color: #1E2A4A; background: var(--hl);
  transform: rotate(-3deg); border-radius: 0.2em; }
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
