import html
import uuid

import streamlit as st

from rag import answer, suggest_followups
from store import ingest_file, wait_until_indexed, delete_namespace

APP_NAME = "Chatbot"

st.set_page_config(page_title=APP_NAME, layout="centered", initial_sidebar_state="collapsed")

# ---------- styling ----------
st.markdown(
    """
    <style>
    #MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"],
    [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] { display: none; }
    header[data-testid="stHeader"] { background: transparent; }

    .block-container { max-width: 760px; padding-top: 1.5rem; padding-bottom: 8rem; }

    /* top bar */
    .brand { font-size: 1.35rem; font-weight: 700; letter-spacing: -0.02em; padding-top: 0.35rem; }
    .topline { border-bottom: 1px solid #ECECEC; margin: 0.3rem 0 1rem 0; }

    /* document chips */
    .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 1rem; }
    .chip { display: inline-flex; align-items: center; gap: 8px; padding: 6px 12px;
            border: 1px solid #E3E3E3; border-radius: 999px; background: #F7F7F8;
            font-size: 0.82rem; color: #333; }
    .chip small { color: #888; }

    /* chat messages: no avatars, user bubble on the right */
    [data-testid="stChatMessageAvatarUser"],
    [data-testid="stChatMessageAvatarAssistant"] { display: none; }
    [data-testid="stChatMessage"] { background: transparent; padding: 0.5rem 0; }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) { flex-direction: row-reverse; }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
        background: #F1F1F2; border-radius: 18px; padding: 0.6rem 1.1rem;
        max-width: 80%; flex: 0 1 auto; }

    .meta { font-size: 0.78rem; color: #888; margin-top: 0.4rem; }
    .meta b { font-weight: 600; color: #555; }

    /* welcome */
    .welcome { text-align: center; margin: 12vh 0 2rem 0; }
    .welcome h1 { font-size: 2.4rem; font-weight: 700; letter-spacing: -0.03em; margin-bottom: 0.4rem; }
    .welcome p { color: #777; font-size: 1.05rem; }

    /* buttons */
    .stButton > button { border-radius: 12px; border: 1px solid #E3E3E3; background: #FFFFFF; }
    .stButton > button:hover { background: #F7F7F8; border-color: #CFCFCF; }

    /* chat input */
    [data-testid="stChatInput"] { border-radius: 18px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------- session state ----------
st.session_state.setdefault("namespace", f"session-{uuid.uuid4().hex[:10]}")
st.session_state.setdefault("messages", [])
st.session_state.setdefault("files", {})


def new_chat():
    st.session_state.messages = []


def clear_docs():
    delete_namespace(st.session_state.namespace)
    st.session_state.namespace = f"session-{uuid.uuid4().hex[:10]}"
    st.session_state.messages = []
    st.session_state.files = {}


def ask(q):
    st.session_state.pending = q


messages = st.session_state.messages

# ---------- chat input (with attach button) ----------
submission = st.chat_input(
    f"Message {APP_NAME}  (use + to attach files)",
    accept_file="multiple",
        file_type=["pdf", "docx", "pptx", "xlsx", "csv", "txt", "md", "png", "jpg", "jpeg", "webp"],
)

question = None
new_files = []
if submission:
    question = (submission.text or "").strip() or None
    new_files = list(submission.files or [])
if question is None and not new_files:
    question = st.session_state.pop("pending", None)

# ---------- process attached files ----------
for f in new_files:
    key = f"{f.name}-{f.size}"
    if key in st.session_state.files:
        continue
    with st.spinner(f"Reading {f.name}..."):
        n = ingest_file(f, f.name, st.session_state.namespace)
    st.session_state.files[key] = {"name": f.name, "chunks": n}
    if n == 0:
        messages.append({
            "role": "assistant", "kind": "note",
            "content": f"I couldn't find any content from **{f.name}**. The file may be empty, corrupted or unsupported format.",
        })
    else:
        messages.append({
            "role": "user", "kind": "file",
            "content": f"Attached **{f.name}**",
        })

total_chunks = sum(v["chunks"] for v in st.session_state.files.values())
if new_files and total_chunks:
    with st.spinner("Indexing..."):
        wait_until_indexed(st.session_state.namespace, total_chunks)

has_docs = total_chunks > 0

# ---------- top bar ----------
c1, c2, c3 = st.columns([5, 1.4, 1.9])
with c1:
    st.markdown(f'<div class="brand">{APP_NAME}</div>', unsafe_allow_html=True)
with c2:
    st.button("New chat", on_click=new_chat, use_container_width=True)
with c3:
    if has_docs:
        st.button("Clear documents", on_click=clear_docs, use_container_width=True)
st.markdown('<div class="topline"></div>', unsafe_allow_html=True)

# ---------- document chips ----------
if has_docs:
    chips = "".join(
        f'<span class="chip">{html.escape(v["name"])} <small>{v["chunks"]} chunks</small></span>'
        for v in st.session_state.files.values() if v["chunks"] > 0
    )
    st.markdown(f'<div class="chips">{chips}</div>', unsafe_allow_html=True)

# ---------- welcome screen ----------
if not messages and not question:
    if has_docs:
        st.markdown(
            f'<div class="welcome"><h1>{APP_NAME}</h1><p>Ask anything about your documents.</p></div>',
            unsafe_allow_html=True,
        )
        for j, q in enumerate([
            "Summarize the main points of my documents",
            "What are the key terms or definitions?",
            "List any important dates, numbers or deadlines",
        ]):
            st.button(q, key=f"ex_{j}", on_click=ask, args=(q,), use_container_width=True)
    else:
        st.markdown(
            f'<div class="welcome"><h1>{APP_NAME}</h1>'
            f"<p>Click the + button in the message box below to attach a file, "
            f"then ask questions about it.</p></div>",
            unsafe_allow_html=True,
        )


# ---------- render chat ----------
def render_extras(m):
    if m.get("confidence"):
        st.markdown(
            f'<div class="meta">Confidence: <b>{m["confidence"]}</b> '
            f'· top match {m["top_score"]:.2f}</div>',
            unsafe_allow_html=True,
        )
    if m.get("chunks"):
        with st.expander(f"View sources ({len(m['chunks'])})"):
            for c in m["chunks"]:
                st.markdown(f"**{c['source']}** · page {c['page']} · score {c['score']:.2f}")
                st.caption(c["text"])


for i, m in enumerate(messages):
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] == "assistant":
            render_extras(m)
    is_last = i == len(messages) - 1
    if is_last and m["role"] == "assistant" and m.get("followups") and not question:
        for j, q in enumerate(m["followups"]):
            st.button(q, key=f"fu_{i}_{j}", on_click=ask, args=(q,), use_container_width=True)

# ---------- handle new question ----------
if question:
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in messages if m.get("kind") not in ("file", "note")
    ]
    messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    if not has_docs:
        messages.append({
            "role": "assistant", "kind": "note",
            "content": "Please attach a PDF or text file first using the + button in the message box.",
        })
        st.rerun()

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            result = answer(question, st.session_state.namespace, history)
            followups = suggest_followups(question, result["reply"]) if result["chunks"] else []

    messages.append({
        "role": "assistant",
        "content": result["reply"],
        "chunks": result["chunks"],
        "confidence": result["confidence"],
        "top_score": result["top_score"],
        "followups": followups,
    })
    st.rerun()