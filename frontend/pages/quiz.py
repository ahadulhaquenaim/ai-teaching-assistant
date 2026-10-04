"""Quiz page: generate a quiz, take it, submit, and review score + explanations."""

from __future__ import annotations

from typing import Any

import streamlit as st

import api_client
from ui import document_picker, ready_documents, show_error

QUIZ_KEY = "active_quiz"
RESULT_KEY = "quiz_result"

st.title(":material/quiz: Quiz")

docs = ready_documents()
doc = document_picker(docs, key="quiz_doc")
if doc is None:
    st.stop()


def set_active(quiz: dict[str, Any] | None) -> None:
    st.session_state[QUIZ_KEY] = quiz
    st.session_state.pop(RESULT_KEY, None)


# ------------------------------------------------------------------ results
def render_result(result: dict[str, Any]) -> None:
    st.subheader("Results")
    cols = st.columns(3)
    cols[0].metric("Score", f"{result['score']:g} / {result['total']}")
    cols[1].metric("Percentage", f"{result['percentage']:g}%")
    correct = sum(1 for r in result["results"] if r["is_correct"])
    cols[2].metric("Correct", f"{correct} / {result['total']}")
    for r in result["results"]:
        icon = ":material/check_circle:" if r["is_correct"] else ":material/cancel:"
        with st.container(border=True):
            st.markdown(f"{icon} **Q{r['question_id'] + 1}. {r['question']}**")
            st.markdown(f"**Your answer:** {r['user_answer'] or '_(no answer)_'}")
            if not r["is_correct"]:
                st.markdown(f"**Correct answer:** {r['correct_answer']}")
            if r["score"] not in (0, 1):
                st.caption(f"Partial credit: {r['score']:g}")
            st.markdown(f"**Feedback:** {r['feedback']}")
            st.markdown(f"**Explanation:** {r['explanation']}")
            st.caption(f"📄 Source: Page {r['source_page']}")


# --------------------------------------------------------------- take quiz
def take_quiz(quiz: dict[str, Any]) -> None:
    title = quiz.get("topic") or "Whole document"
    st.subheader(f"{title} · {quiz['difficulty'].title()} · "
                 f"{'Multiple choice' if quiz['question_type'] == 'mcq' else 'Short answer'}")
    if st.button("Back to quiz list", icon=":material/arrow_back:"):
        set_active(None)
        st.rerun()

    if RESULT_KEY in st.session_state:
        render_result(st.session_state[RESULT_KEY])
        if st.button("Retake quiz", icon=":material/replay:"):
            st.session_state.pop(RESULT_KEY, None)
            st.rerun()
        return

    with st.form(f"quiz-{quiz['id']}"):
        answers: list[dict[str, Any]] = []
        for q in quiz["questions"]:
            label = f"**Q{q['id'] + 1}. {q['question']}**"
            if quiz["question_type"] == "mcq":
                choice = st.radio(label, q["options"], index=None, key=f"q-{quiz['id']}-{q['id']}")
                answers.append({"question_id": q["id"], "answer": choice or ""})
            else:
                text = st.text_area(label, key=f"q-{quiz['id']}-{q['id']}", height=90)
                answers.append({"question_id": q["id"], "answer": text})
            st.caption(f"Based on page {q['source_page']}")
        submitted = st.form_submit_button("Submit answers", type="primary", icon=":material/send:")
    if submitted:
        with st.spinner("Scoring your answers..."):
            try:
                st.session_state[RESULT_KEY] = api_client.submit_quiz(quiz["id"], answers)
                st.rerun()
            except api_client.APIError as exc:
                show_error(exc)


active = st.session_state.get(QUIZ_KEY)
if active and active.get("document_id") == doc["id"]:
    take_quiz(active)
    st.stop()

# ----------------------------------------------------------- new + history
new_tab, past_tab = st.tabs([":material/add: New quiz", ":material/history: Past quizzes"])

with new_tab, st.form("new-quiz"):
    topic = st.text_input("Topic (optional)", placeholder="Leave empty to cover the whole document",
                          max_chars=200)
    cols = st.columns(3)
    difficulty = cols[0].selectbox("Difficulty", ["easy", "medium", "hard"], index=1,
                                   format_func=str.title)
    count = cols[1].slider("Number of questions", 1, 10, 5)
    qtype = cols[2].radio("Question type", ["mcq", "short_answer"], horizontal=True,
                          format_func=lambda t: "Multiple choice" if t == "mcq" else "Short answer")
    generate = st.form_submit_button("Generate quiz", type="primary", icon=":material/auto_awesome:")
if generate:
    with st.spinner("Generating and validating questions... (free-tier AI can take up to a minute)"):
        try:
            quiz = api_client.create_quiz({
                "document_id": doc["id"],
                "topic": topic.strip() or None,
                "difficulty": difficulty,
                "number_of_questions": count,
                "question_type": qtype,
            })
            if quiz["question_count"] < count:
                st.toast(f"Generated {quiz['question_count']} valid questions (requested {count}).")
            set_active(quiz)
            st.rerun()
        except api_client.APIError as exc:
            show_error(exc)

with past_tab:
    try:
        quizzes = api_client.list_quizzes(doc["id"])
    except api_client.APIError as exc:
        show_error(exc)
        quizzes = []
    if not quizzes:
        st.caption("No quizzes for this document yet.")
    for q in quizzes:
        with st.container(border=True):
            cols = st.columns([5, 1], vertical_alignment="center")
            cols[0].markdown(f"**{q.get('topic') or 'Whole document'}** · {q['difficulty'].title()} · "
                             f"{q['question_count']} {'MCQ' if q['question_type'] == 'mcq' else 'short answer'} "
                             f"questions")
            cols[0].caption(q["created_at"][:16].replace("T", " "))
            if cols[1].button("Open", key=f"open-quiz-{q['id']}"):
                try:
                    full = api_client.get_quiz(q["id"])
                    set_active(full)
                    if full["attempts"]:
                        st.session_state[RESULT_KEY] = full["attempts"][0]  # latest attempt
                    st.rerun()
                except api_client.APIError as exc:
                    show_error(exc)
