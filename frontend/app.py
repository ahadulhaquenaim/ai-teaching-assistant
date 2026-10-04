"""AI Teaching Assistant - Streamlit entry point.

Run: `streamlit run app.py` from the `frontend/` directory.

Google login is deferred (the backend treats every request as one local user),
so this page shows a welcome screen instead of "Sign in with Google".
"""

from __future__ import annotations

import base64

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


# A page of notes with highlighter strokes, a cited answer, and a ticked quiz.
HERO_ART = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 380 320">
  <rect x="58" y="26" width="230" height="272" rx="16" fill="#7CC4FF" transform="rotate(-7 173 162)"/>
  <rect x="50" y="22" width="230" height="272" rx="16" fill="#FFFFFF"/>
  <rect x="74" y="48" width="120" height="14" rx="7" fill="#1E2A4A"/>
  <rect x="70" y="82" width="150" height="18" rx="4" fill="#FFE45C"/>
  <rect x="74" y="88" width="170" height="7" rx="3.5" fill="#9AA6C4"/>
  <rect x="74" y="110" width="182" height="7" rx="3.5" fill="#C9D1E6"/>
  <rect x="74" y="130" width="140" height="7" rx="3.5" fill="#C9D1E6"/>
  <rect x="70" y="144" width="120" height="18" rx="4" fill="#FF8FB8"/>
  <rect x="74" y="150" width="176" height="7" rx="3.5" fill="#9AA6C4"/>
  <rect x="74" y="172" width="160" height="7" rx="3.5" fill="#C9D1E6"/>
  <rect x="74" y="192" width="100" height="7" rx="3.5" fill="#C9D1E6"/>
  <rect x="70" y="206" width="96" height="18" rx="4" fill="#7BE0A6"/>
  <rect x="74" y="212" width="150" height="7" rx="3.5" fill="#9AA6C4"/>
  <text x="246" y="276" font-size="13" font-family="Literata, serif" fill="#9AA6C4">p. 3</text>
  <rect x="176" y="180" width="196" height="96" rx="18" fill="#1E2A4A"/>
  <path d="M206 276 L196 300 L226 276 Z" fill="#1E2A4A"/>
  <rect x="196" y="202" width="150" height="7" rx="3.5" fill="#FFFFFF" opacity="0.9"/>
  <rect x="196" y="220" width="120" height="7" rx="3.5" fill="#FFFFFF" opacity="0.6"/>
  <rect x="196" y="240" width="62" height="22" rx="11" fill="#FFE45C"/>
  <text x="208" y="255" font-size="12" font-weight="700" font-family="sans-serif" fill="#1E2A4A">Page 3</text>
  <circle cx="300" cy="44" r="30" fill="#7BE0A6"/>
  <path d="M286 45 L296 55 L315 35" stroke="#1E2A4A" stroke-width="6" fill="none"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""
# st.html strips inline <svg>, so the art goes in as a data-URI image.
HERO_ART_SRC = "data:image/svg+xml;base64," + base64.b64encode(HERO_ART.encode()).decode()


def home() -> None:
    st.html(
        '<section class="hero"><div>'
        '<h1 class="hero-title">Study your course material by asking it questions</h1>'
        '<p class="hero-lede">Upload a PDF or DOCX, chat with it, and quiz yourself. '
        "Every answer points back to the page it came from.</p>"
        '<a class="hero-cta" href="documents" target="_self">Upload a document</a>'
        f'</div><img class="hero-art" src="{HERO_ART_SRC}" alt="Notes page with a cited answer"></section>'
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
