"""AI Teaching Assistant - Streamlit entry point.

Run: `streamlit run app.py` from the `frontend/` directory.

Google login is deferred (the backend treats every request as one local user),
so this page shows a welcome screen instead of "Sign in with Google".
"""

from __future__ import annotations

import streamlit as st

import api_client

st.set_page_config(page_title="AI Teaching Assistant", page_icon=":material/school:", layout="wide")


def home() -> None:
    st.title(":material/school: AI Teaching Assistant")
    st.write(
        "Upload a PDF or DOCX, chat with it, and generate quizzes. "
        "Answers are grounded in your document with page citations."
    )
    col1, col2, col3 = st.columns(3)
    with col1, st.container(border=True):
        st.markdown("**1. Upload**")
        st.caption("Add course material on the Documents page. Processing runs in the background.")
        st.page_link(documents_page, label="Go to Documents", icon=":material/description:")
    with col2, st.container(border=True):
        st.markdown("**2. Chat**")
        st.caption("Ask questions. Optionally search the web for extra, clearly labeled context.")
        st.page_link(chat_page, label="Go to Chat", icon=":material/chat:")
    with col3, st.container(border=True):
        st.markdown("**3. Quiz**")
        st.caption("Generate MCQ or short-answer quizzes and get scored with explanations.")
        st.page_link(quiz_page, label="Go to Quiz", icon=":material/quiz:")

    st.divider()
    with st.spinner("Connecting to the server..."):
        try:
            status = api_client.health()
            st.success(f"Server is up (version {status['version']}).", icon=":material/cloud_done:")
        except api_client.APIError as exc:
            st.warning(f"Server status: {exc.message}", icon=":material/cloud_off:")


home_page = st.Page(home, title="Home", icon=":material/home:", default=True)
documents_page = st.Page("pages/documents.py", title="Documents", icon=":material/description:")
chat_page = st.Page("pages/chat.py", title="Chat", icon=":material/chat:")
quiz_page = st.Page("pages/quiz.py", title="Quiz", icon=":material/quiz:")

with st.sidebar:
    st.caption(":material/person: Local dev user · sign-in disabled")

st.navigation([home_page, documents_page, chat_page, quiz_page]).run()
