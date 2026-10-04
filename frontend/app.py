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
    ("1", "Upload", "Add a PDF or DOCX. It's indexed in the background.",
     "documents", "Upload a document", ":material/upload_file:"),
    ("2", "Ask", "Get answers that cite the pages they come from.",
     "chat", "Start a chat", ":material/chat:"),
    ("3", "Test yourself", "Generate a quiz and see an explanation for every answer.",
     "quiz", "Make a quiz", ":material/quiz:"),
]

# Left: product name, promise, actions. Right: a preview of a real chat turn and quiz question.
HERO = """
<section class="hero">
  <div class="hero-copy">
    <p class="brand"><span class="brand-mark" aria-hidden="true"></span>AI Teaching Assistant</p>
    <h1 class="hero-title">Ask your course notes anything. Every answer shows its page.</h1>
    <p class="hero-lede">Upload the PDFs and DOCX files from your course. The assistant answers your
      questions from them, cites the pages it used, and writes quizzes so you can check what stuck.</p>
    <div class="hero-actions">
      <a class="btn-primary" href="documents" target="_self">Upload a document</a>
      <a class="btn-quiet" href="chat" target="_self">Open chat</a>
    </div>
  </div>
  <div class="preview" role="img" aria-label="Example: a question about the notes, an answer citing pages 3 and 7, and a quiz question">
    <div class="preview-bar"><span class="file">software-design-notes.pdf</span><span class="ready">Ready</span></div>
    <div class="msg msg-user">Why is constructor injection easier to test?</div>
    <div class="msg msg-ai">
      <p>The class receives its dependencies instead of creating them, so a test can pass in a fake
        repository. The notes walk through this with a mock database.</p>
      <p class="cites"><span class="cite">Page 3</span><span class="cite">Page 7</span></p>
    </div>
    <div class="quiz">
      <p class="quiz-q">Question 1 of 5: Which change makes <b>OrderService</b> easiest to test?</p>
      <p class="opt">Create the database inside the constructor</p>
      <p class="opt opt-right">Pass the repository into the constructor</p>
    </div>
  </div>
</section>
"""


def home() -> None:
    st.html(HERO)

    pages = {"documents": documents_page, "chat": chat_page, "quiz": quiz_page}
    with st.container(key="steps"):
        for col, (num, title, body, page, cta, icon) in zip(st.columns(3, gap="large"), STEPS):
            with col:
                st.html(f'<p class="step-title"><span class="step-num">{num}</span>{title}</p>'
                        f'<p class="step-body">{body}</p>')
                st.page_link(pages[page], label=cta, icon=icon)

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

# Rendered before the page so it always shows; CSS (ui.STYLE) pins it to the sidebar bottom,
# below anything a page adds (e.g. the chat list).
with st.sidebar.container(key="side-footer"):
    st.html('<div class="side-user"><span class="side-avatar" aria-hidden="true">LD</span>'
            '<div><p class="side-name">Local dev user</p><p class="side-note">Sign-in is off</p></div></div>')

st.navigation([home_page, documents_page, chat_page, quiz_page]).run()
