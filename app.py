import html

import streamlit as st

import users
from guardrails import check_input, check_rate, check_upload, log_event
from pii import MASK_PII_IN_DOCS, MASK_PII_IN_QUESTIONS, mask_pii
from rag import answer, suggest_followups
from store import delete_namespace, ingest_file, wait_until_indexed

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

    .brand { font-size: 1.35rem; font-weight: 700; letter-spacing: -0.02em; padding-top: 0.35rem; }
    .topline { border-bottom: 1px solid #ECECEC; margin: 0.3rem 0 0.6rem 0; }
    .disclaimer { font-size: 0.78rem; color: #999; margin-bottom: 1rem; }

    .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 1rem; }
    .chip { display: inline-flex; align-items: center; gap: 8px; padding: 6px 12px;
            border: 1px solid #E3E3E3; border-radius: 999px; background: #F7F7F8;
            font-size: 0.82rem; color: #333; }
    .chip small { color: #888; }

    [data-testid="stChatMessageAvatarUser"],
    [data-testid="stChatMessageAvatarAssistant"] { display: none; }
    [data-testid="stChatMessage"] { background: transparent; padding: 0.5rem 0; }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) { flex-direction: row-reverse; }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
        background: #F1F1F2; border-radius: 18px; padding: 0.6rem 1.1rem;
        max-width: 80%; flex: 0 1 auto; }

    .meta { font-size: 0.78rem; color: #888; margin-top: 0.4rem; }
    .meta b { font-weight: 600; color: #555; }

    .welcome { text-align: center; margin: 12vh 0 2rem 0; }
    .welcome h1 { font-size: 2.4rem; font-weight: 700; letter-spacing: -0.03em; margin-bottom: 0.4rem; }
    .welcome p { color: #777; font-size: 1.05rem; }

    .stButton > button { border-radius: 12px; border: 1px solid #E3E3E3; background: #FFFFFF; }
    .stButton > button:hover { background: #F7F7F8; border-color: #CFCFCF; }

    [data-testid="stChatInput"] { border-radius: 18px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------- session state ----------
st.session_state.setdefault("user", None)
st.session_state.setdefault("rec", None)
st.session_state.setdefault("namespace", None)
st.session_state.setdefault("messages", [])
st.session_state.setdefault("files", {})


def note(text):
    return {"role": "assistant", "kind": "note", "content": text}


def start_session(username, rec):
    st.session_state.user = username
    st.session_state.rec = rec
    st.session_state.namespace = users.namespace_for(username)
    st.session_state.files = users.load_files(rec)
    st.session_state.messages = []


def logout():
    st.session_state.user = None
    st.session_state.rec = None
    st.session_state.namespace = None
    st.session_state.files = {}
    st.session_state.messages = []


def new_chat():
    st.session_state.messages = []


def clear_docs():
    delete_namespace(st.session_state.namespace)
    st.session_state.files = {}
    st.session_state.messages = []
    try:
        users.update(st.session_state.user, st.session_state.rec, files={})
    except Exception as e:
        print("Update error:", repr(e))


def ask(q):
    st.session_state.pending = q


# ---------- login / sign up ----------
if not st.session_state.user:
    _, mid, _ = st.columns([1, 2.4, 1])
    with mid:
        st.markdown(
            f'<div class="welcome" style="margin-top:6vh"><h1>{APP_NAME}</h1>'
            f"<p>Sign in to chat with your documents.</p></div>",
            unsafe_allow_html=True,
        )
        tab_in, tab_up = st.tabs(["Sign in", "Create account"])

        with tab_in:
            with st.form("signin_form"):
                u = st.text_input("Username")
                p = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Sign in", use_container_width=True)
            if submitted:
                try:
                    rec, err = users.login(u, p)
                except Exception as e:
                    print("Login error:", repr(e))
                    rec, err = None, "Sign-in is temporarily unavailable. Please try again."
                if err:
                    st.error(err)
                else:
                    start_session(u.strip().lower(), rec)
                    st.rerun()

        with tab_up:
            with st.form("signup_form"):
                nu = st.text_input("Choose a username", help="3-20 characters: a-z, 0-9 or underscore")
                np1 = st.text_input("Password (at least 8 characters)", type="password")
                np2 = st.text_input("Confirm password", type="password")
                code = st.text_input("Invite code") if users.SIGNUP_CODE else ""
                created = st.form_submit_button("Create account", use_container_width=True)
            if created:
                if np1 != np2:
                    st.error("The passwords don't match.")
                else:
                    try:
                        rec, err = users.signup(nu, np1, code)
                    except Exception as e:
                        print("Signup error:", repr(e))
                        rec, err = None, "Sign-up is temporarily unavailable. Please try again."
                    if err:
                        st.error(err)
                    else:
                        start_session(nu.strip().lower(), rec)
                        st.rerun()

        st.caption("Your documents are private to your account.")
    st.stop()

# ---------- signed-in state ----------
username = st.session_state.user
rec = st.session_state.rec
namespace = st.session_state.namespace
files = st.session_state.files
messages = st.session_state.messages

# ---------- chat input (with attach button) ----------
submission = st.chat_input(
    f"Message {APP_NAME}  (use + to attach documents or images)",
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


def upload_notes(name, report):
    lines = []
    if report.get("pii"):
        found = ", ".join(f"{n} {label.lower()}" for label, n in report["pii"].items())
        lines.append(f"Personal data in **{name}** was masked before indexing ({found}).")
    if report.get("blocked"):
        reasons = "; ".join(sorted(set(report["blocked"])))
        lines.append(
            f"{len(report['blocked'])} image(s) or page(s) in **{name}** were blocked by the "
            f"content scan and not read ({reasons})."
        )
    if report.get("skipped"):
        lines.append(
            f"{report['skipped']} image(s) or page(s) in **{name}** were not read because the "
            f"per-file or daily image limit was reached."
        )
    return "\n\n".join(lines)


# ---------- process attached files (upload guardrails and quotas) ----------
for f in new_files:
    key = f"{f.name}-{f.size}"
    if key in files:
        continue

    left = users.remaining(rec)
    if left["uploads"] <= 0:
        messages.append(note(
            f"You've reached today's upload limit ({users.DAILY_UPLOADS} files). It resets at midnight UTC."
        ))
        break

    current_chunks = sum(v["chunks"] for v in files.values())
    ok, msg = check_upload(f, len(files), current_chunks)
    if not ok:
        log_event("upload_blocked", f.name)
        messages.append(note(msg))
        continue

    try:
        with st.spinner(f"Reading {f.name}..."):
            report = ingest_file(f, f.name, namespace, vision_limit=left["vision"])
    except Exception as e:
        print("Ingest error:", repr(e))
        messages.append(note(
            f"I couldn't process **{f.name}**. The file may be corrupted or protected. Please try another file."
        ))
        continue

    n = report["chunks"]
    if n > 0:
        files[key] = {"name": f.name, "chunks": n}
    try:
        users.update(username, rec, files=files, uploads=1, vision=report.get("vision_used", 0))
    except Exception as e:
        print("Update error:", repr(e))

    extra = upload_notes(f.name, report)
    if n == 0:
        text = f"I couldn't read any content from **{f.name}**. The file may be empty, corrupted or an unsupported format."
        messages.append(note(text + ("\n\n" + extra if extra else "")))
    else:
        messages.append({"role": "user", "kind": "file", "content": f"Attached **{f.name}**"})
        if extra:
            messages.append(note(extra))

total_chunks = sum(v["chunks"] for v in files.values())
if new_files and total_chunks:
    with st.spinner("Indexing..."):
        wait_until_indexed(namespace, total_chunks)

has_docs = total_chunks > 0

# ---------- top bar ----------
c1, c2, c3, c4 = st.columns([4.2, 1.3, 1.9, 1.5])
with c1:
    st.markdown(f'<div class="brand">{APP_NAME}</div>', unsafe_allow_html=True)
with c2:
    st.button("New chat", on_click=new_chat, use_container_width=True)
with c3:
    if has_docs:
        st.button("Clear documents", on_click=clear_docs, use_container_width=True)
with c4:
    with st.popover("Account", use_container_width=True):
        left = users.remaining(rec)
        st.markdown(f"**{username}**")
        st.caption(f"Questions left today: {left['questions']} of {users.DAILY_QUESTIONS}")
        st.caption(f"File uploads left today: {left['uploads']} of {users.DAILY_UPLOADS}")
        st.caption(f"Image reads left today: {left['vision']} of {users.DAILY_VISION_CALLS}")
        st.caption("Limits reset at midnight UTC.")
        st.button("Sign out", on_click=logout, use_container_width=True)
st.markdown('<div class="topline"></div>', unsafe_allow_html=True)

disclaimer = f"{APP_NAME} answers only from your documents and can make mistakes. Check important details against the sources."
if MASK_PII_IN_DOCS:
    disclaimer += " Emails, phone numbers and ID numbers in your files are masked before indexing."
st.markdown(f'<div class="disclaimer">{html.escape(disclaimer)}</div>', unsafe_allow_html=True)

# ---------- document chips ----------
if has_docs:
    chips = "".join(
        f'<span class="chip">{html.escape(v["name"])} <small>{v["chunks"]} chunks</small></span>'
        for v in files.values() if v["chunks"] > 0
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
            f"<p>Click the + button in the message box below to attach a document, spreadsheet or image, "
            f"then ask questions about it.</p></div>",
            unsafe_allow_html=True,
        )


# ---------- render chat ----------
def render_extras(m):
    for w in m.get("warnings", []):
        st.markdown(f'<div class="meta"><b>Note:</b> {html.escape(w)}</div>', unsafe_allow_html=True)
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

# ---------- handle new question (input guardrails, quota, PII masking) ----------
if question:
    ok, msg = check_input(question)
    if ok:
        ok, msg = check_rate(st.session_state)
    if ok and users.remaining(rec)["questions"] <= 0:
        ok = False
        msg = f"You've used all {users.DAILY_QUESTIONS} questions for today. Your quota resets at midnight UTC."
    if not ok:
        shown = question if len(question) <= 300 else question[:300] + "..."
        messages.append({"role": "user", "kind": "blocked", "content": mask_pii(shown)[0]})
        messages.append(note(msg))
        st.rerun()

    if not has_docs:
        messages.append({"role": "user", "kind": "blocked", "content": mask_pii(question)[0]})
        messages.append(note("Please attach a document or image first using the + button in the message box."))
        st.rerun()

    if MASK_PII_IN_QUESTIONS:
        question, _ = mask_pii(question)

    try:
        users.update(username, rec, questions=1)
    except Exception as e:
        print("Update error:", repr(e))

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in messages if m.get("kind") not in ("file", "note", "blocked")
    ]
    messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    result, error_text = None, None
    try:
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                result = answer(question, namespace, history)
                followups = suggest_followups(question, result["reply"]) if result["chunks"] else []
    except Exception as e:
        print("Answer error:", repr(e))
        log_event("answer_error", repr(e))
        error_text = (
            "The AI service is busy right now. Please wait a moment and try again."
            if "429" in str(e)
            else "Something went wrong while answering. Please try again."
        )

    if error_text:
        messages.append(note(error_text))
        st.rerun()

    messages.append({
        "role": "assistant",
        "content": result["reply"],
        "chunks": result["chunks"],
        "confidence": result["confidence"],
        "top_score": result["top_score"],
        "warnings": result["warnings"],
        "followups": followups,
    })
    st.rerun()