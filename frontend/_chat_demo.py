import streamlit as st

from ui import apply_style, render_sources

st.set_page_config(layout="wide")
apply_style()
st.title(":material/chat: Chat with your document")
st.selectbox("Document", ["notes.pdf"])
with st.expander("Document summary", icon=":material/summarize:"):
    st.write("x")
with st.chat_message("user"):
    st.markdown("tell me about the user current company?")
with st.chat_message("assistant"):
    st.markdown("Based on the document, your current company is **Cefalo Bangladesh LTD**. You are listed as a "
                "**Software Engineer** there from March 2026 – Present. Lots more text here to wrap lines.")
    render_sources([{"type": "document", "page": 1}, {"type": "document", "page": 2}])
with st.chat_message("user"):
    st.markdown("tell me about my varsity.")
with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
    st.toggle("Search the web")
    st.caption(":material/travel_explore: 17 of 20 web searches left today", width="content")
st.chat_input("Ask a question about your document")
