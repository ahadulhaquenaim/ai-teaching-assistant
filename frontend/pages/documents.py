"""Documents page: upload PDF/DOCX, list with status badges, summaries, delete.

While any document is processing, the list re-renders every few seconds
(st.fragment with run_every) so status changes appear without a page reload.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import api_client
import streamlit as st
from ui import show_error, status_badge

POLL_SECONDS = 3
MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

st.title(":material/description: Documents")

# ------------------------------------------------------------------- upload
with st.form("upload", clear_on_submit=True, border=True):
    uploaded = st.file_uploader("Upload a PDF or DOCX", type=["pdf", "docx"])
    submitted = st.form_submit_button("Upload", icon=":material/upload:", type="primary")
if submitted:
    if uploaded is None:
        st.warning("Choose a file first.")
    else:
        ext = uploaded.name.rsplit(".", 1)[-1].lower()
        with st.spinner(f"Uploading {uploaded.name}..."):
            try:
                api_client.upload_document(
                    uploaded.name, uploaded.getvalue(), MIME.get(ext, "application/octet-stream")
                )
                st.toast(f"{uploaded.name} uploaded. Processing started.", icon=":material/check:")
            except api_client.APIError as exc:
                show_error(exc)


# --------------------------------------------------------------------- list
def fmt_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


@st.dialog("Delete document?")
def confirm_delete(doc: dict[str, Any]) -> None:
    st.write(f"**{doc['filename']}** and all its chats and quizzes will be permanently deleted.")
    if st.button("Delete", type="primary", icon=":material/delete:"):
        try:
            api_client.delete_document(doc["id"])
            st.rerun()
        except api_client.APIError as exc:
            show_error(exc)


def render_document(doc: dict[str, Any]) -> None:
    with st.container(border=True):
        top = st.columns([6, 2, 1], vertical_alignment="center")
        with top[0]:
            st.markdown(f"**{doc['filename']}**")
            meta = [doc["file_type"].upper(), fmt_date(doc["created_at"])]
            if doc.get("page_count"):
                meta.append(f"{doc['page_count']} pages")
            if doc.get("chunk_count"):
                meta.append(f"{doc['chunk_count']} chunks")
            st.caption(" · ".join(meta))
        with top[1]:
            status_badge(doc["status"])
        with top[2]:
            disabled = doc["status"] == "processing"
            if st.button(
                "",
                icon=":material/delete:",
                key=f"del-{doc['id']}",
                disabled=disabled,
                help="Wait for processing to finish" if disabled else "Delete",
            ):
                confirm_delete(doc)
        if doc["status"] == "ready" and doc.get("summary"):
            st.write(doc["summary"])
        elif doc["status"] == "processing":
            st.caption("Extracting, chunking and embedding... this page refreshes automatically.")
        elif doc["status"] == "failed":
            st.error(doc.get("error_message") or "Processing failed.", icon=":material/error:")


def document_list() -> None:
    try:
        docs = api_client.list_documents()
    except api_client.APIError as exc:
        show_error(exc)
        return
    if not docs:
        st.info("No documents yet. Upload one above.", icon=":material/info:")
        return

    processing = any(d["status"] == "processing" for d in docs)
    was_processing = st.session_state.get("docs_were_processing", False)
    st.session_state["docs_were_processing"] = processing
    if was_processing and not processing:
        # Processing just finished: rerun the whole page to stop polling.
        st.rerun(scope="app")

    st.subheader(f"Your documents ({len(docs)})")
    for doc in docs:
        render_document(doc)


# Poll only while something is processing (decided on each full run).
try:
    any_processing = any(d["status"] == "processing" for d in api_client.list_documents())
except api_client.APIError:
    any_processing = False
st.fragment(document_list, run_every=POLL_SECONDS if any_processing else None)()
