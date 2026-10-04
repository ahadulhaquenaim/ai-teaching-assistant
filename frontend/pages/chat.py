"""Chat page: pick a document, manage sessions, ask grounded questions.

Document sources show as page numbers; web sources (when "Search the web" is
on) show as clickable links in a separate, clearly labeled block.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

import api_client
from ui import document_picker, ready_documents, render_sources, show_error

SESSION_KEY = "chat_session_id"

st.title(":material/chat: Chat with your document")

docs = ready_documents()
doc = document_picker(docs, key="chat_doc")
if doc is None:
    st.stop()

# A session belongs to one document; reset when the document changes.
if st.session_state.get("chat_doc_id") != doc["id"]:
    st.session_state["chat_doc_id"] = doc["id"]
    st.session_state.pop(SESSION_KEY, None)

if doc.get("summary"):
    with st.expander("Document summary", icon=":material/summarize:"):
        st.write(doc["summary"])

# ------------------------------------------------------------------ sidebar
with st.sidebar.container(key="side-chats"):
    st.html('<p class="side-heading">Chats</p>')
    if st.button("New chat", icon=":material/add:", use_container_width=True, key="new-chat"):
        st.session_state.pop(SESSION_KEY, None)
        st.rerun()
    try:
        sessions = api_client.list_sessions(doc["id"])
    except api_client.APIError as exc:
        show_error(exc)
        sessions = []
    current = st.session_state.get(SESSION_KEY)
    if not sessions:
        st.html('<p class="side-empty">Your chats about this document will show here.</p>')
    # Row keys carry the state so CSS can mark the open chat (ui.STYLE: .st-key-chatrow-*).
    with st.container(key="chat-list"):
        for s in sessions:
            state = "open" if s["id"] == current else "idle"
            with st.container(key=f"chatrow-{state}-{s['id']}", horizontal=True, vertical_alignment="center",
                              gap="small"):
                if st.button(s["title"], key=f"open-{s['id']}", use_container_width=True, help=s["title"]):
                    st.session_state[SESSION_KEY] = s["id"]
                    st.rerun()
                if st.button("", icon=":material/delete:", key=f"delete-{s['id']}", help="Delete chat"):
                    try:
                        api_client.delete_session(s["id"])
                        if current == s["id"]:
                            st.session_state.pop(SESSION_KEY, None)
                        st.rerun()
                    except api_client.APIError as exc:
                        show_error(exc)

# ----------------------------------------------------------------- history
session_id = st.session_state.get(SESSION_KEY)
messages: list[dict[str, Any]] = []
if session_id:
    try:
        messages = api_client.list_messages(session_id)
    except api_client.APIError as exc:
        if exc.status_code == 404:  # deleted elsewhere
            st.session_state.pop(SESSION_KEY, None)
        else:
            show_error(exc)

if not messages:
    st.caption("Ask anything about the document. Answers cite the pages they come from.")

for m in messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] == "assistant":
            render_sources(m.get("sources", []))
            if m.get("web_search_enabled"):
                st.caption(":material/travel_explore: Web search was on for this answer")

# ------------------------------------------------------------------- input
with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
    web_search = st.toggle("Search the web", key="web_search_toggle",
                           help="Adds clearly labeled web context. The document stays the primary source.")
    try:
        usage = api_client.web_search_usage()
        st.caption(f":material/travel_explore: {usage['remaining']} of {usage['limit']} web searches left today",
                   width="content")
    except api_client.APIError:
        st.caption("Web search usage unavailable", width="content")

question = st.chat_input("Ask a question about your document")
if question:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Searching the document and the web..." if web_search else "Searching the document..."):
            try:
                if not session_id:
                    session_id = api_client.create_session(doc["id"])["id"]
                    st.session_state[SESSION_KEY] = session_id
                turn = api_client.send_message(session_id, question, web_search)
            except api_client.APIError as exc:
                show_error(exc)
                st.stop()
        reply = turn["assistant_message"]
        st.markdown(reply["content"])
        render_sources(reply.get("sources", []))
    st.rerun()  # refresh history, sidebar titles, and remaining searches
