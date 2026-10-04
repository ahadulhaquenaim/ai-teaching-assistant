"""Shared UI helpers: status badges, source rendering, document pickers."""

from __future__ import annotations

from typing import Any

import streamlit as st

import api_client

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
