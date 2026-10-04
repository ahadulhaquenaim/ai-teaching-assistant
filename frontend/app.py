"""AI Teaching Assistant - Streamlit entry point.

Run: `streamlit run app.py` from the `frontend/` directory.

Google login is deferred (the backend treats every request as one local user),
so this page shows a welcome screen instead of "Sign in with Google".
"""

from __future__ import annotations

import streamlit as st

import api_client
from ui import apply_style

st.set_page_config(page_title="AI Teaching Assistant", page_icon=":material/school:", layout="wide")
apply_style()

STEPS = [
    ("1", "Upload", "Add a PDF or DOCX of your course material. It's split and indexed in the background.",
     "documents", "Upload a document", ":material/upload_file:"),
    ("2", "Ask", "Ask questions and get answers that cite the pages they come from. "
     "Turn on web search for extra, clearly labeled context.",
     "chat", "Start a chat", ":material/chat:"),
    ("3", "Test yourself", "Generate multiple-choice or short-answer quizzes, then see your score "
     "with an explanation for every question.",
     "quiz", "Make a quiz", ":material/quiz:"),
]


def home() -> None:
    st.html(
        '<h1 class="hero-title">Study your course material by asking it questions</h1>'
        '<p class="hero-lede">Upload a PDF or DOCX, chat with it, and quiz yourself. '
        "Every answer points back to the page it came from.</p>"
    )
    st.space("medium")

    pages = {"documents": documents_page, "chat": chat_page, "quiz": quiz_page}
    for col, (num, title, body, page, cta, icon) in zip(st.columns(3, gap="medium"), STEPS):
        with col, st.container(key=f"step-{num}", gap="small", height="stretch"):
            st.html(f'<span class="step-num">{num}</span>'
                    f'<p class="step-title">{title}</p><p class="step-body">{body}</p>')
            st.page_link(pages[page], label=cta, icon=icon)

    st.space("medium")
    with st.spinner("Connecting to the server..."):
        try:
            status = api_client.health()
            st.caption(f":green[:material/cloud_done:] Server is up (version {status['version']})")
        except api_client.APIError as exc:
            st.warning(f"Server status: {exc.message}", icon=":material/cloud_off:")


home_page = st.Page(home, title="Home", icon=":material/home:", default=True)
documents_page = st.Page("pages/documents.py", title="Documents", icon=":material/description:")
chat_page = st.Page("pages/chat.py", title="Chat", icon=":material/chat:")
quiz_page = st.Page("pages/quiz.py", title="Quiz", icon=":material/quiz:")

with st.sidebar:
    st.caption(":material/person: Local dev user (sign-in disabled)")

st.navigation([home_page, documents_page, chat_page, quiz_page]).run()
