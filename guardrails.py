import json
import re
import time

from llm import generate
from pii import MASK_PII_IN_ANSWERS, mask_pii

# ================= settings (tune these) =================
MAX_QUESTION_CHARS = 1000
MAX_QUESTIONS_PER_MINUTE = 6
MAX_QUESTIONS_PER_SESSION = 100

MAX_FILE_MB = 25
MAX_FILES_PER_SESSION = 15
MAX_CHUNKS_PER_FILE = 1500
MAX_CHUNKS_PER_SESSION = 4000

ENABLE_LLM_MODERATION = False    # extra LLM call per question; True = stricter but slower
ENABLE_GROUNDING_CHECK = True    # extra LLM call per answer that checks it against the sources

ALLOWED_EXTS = (".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".md",
                ".png", ".jpg", ".jpeg", ".webp")
CANARY = "FOLIO-CANARY-7f3a91"   # hidden marker; if it ever appears in a reply, the prompt leaked


# ================= logging =================
def log_event(kind, detail=""):
    print(json.dumps({"guardrail": kind, "detail": mask_pii(str(detail))[0][:120], "ts": int(time.time())}))


# ================= injection patterns =================
_INJECTION = [re.compile(p, re.I) for p in [
    r"ignore\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|above|earlier|preceding)\s+(instructions?|prompts?|rules?|messages?)",
    r"disregard\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|above|earlier|preceding)",
    r"forget\s+(all\s+|everything\s+|your\s+)?(previous\s+|prior\s+|above\s+|earlier\s+)?(instructions?|rules?)",
    r"(reveal|show|print|repeat|display|leak|output)\s+(me\s+)?(your|the)\s+(system|hidden|initial|secret)\s+(prompt|instructions?|message|rules?)",
    r"pretend\s+(that\s+)?you\s+(have|are|face)\s+no\s+(rules|restrictions|limits|guidelines)",
    r"override\s+(your|the)\s+(rules|instructions|safety|guidelines)",
    r"developer\s+mode",
    r"do\s+anything\s+now",
    r"jailbreak",
    r"new\s+instructions?\s*:",
    r"do\s+not\s+(tell|inform|alert)\s+the\s+user",
]]
_TAGS = re.compile(r"</?\s*(context|system|assistant|user|instructions?)\b[^>]*>", re.I)

_SECRETS = [re.compile(p) for p in [
    r"gsk_[A-Za-z0-9]{20,}",
    r"pcsk_[A-Za-z0-9_]{20,}",
    r"sk-[A-Za-z0-9]{20,}",
    r"AIza[0-9A-Za-z_\-]{30,}",
]]


def _looks_like_injection(text):
    return any(p.search(text) for p in _INJECTION)


# ================= input guardrails =================
def _moderation_ok(text):
    prompt = (
        "You are a content safety filter for a document question-answering assistant. "
        "Reply UNSAFE only if the message asks the assistant to produce clearly harmful content "
        "(instructions for weapons or malware, hateful or sexual abuse, or violence). "
        "Questions about the contents of a document are SAFE. Reply with exactly one word: "
        f"SAFE or UNSAFE.\n\nMessage: {text}"
    )
    try:
        verdict = generate([{"role": "user", "content": prompt}], max_tokens=20).strip().upper()
        return not verdict.startswith("UNSAFE")
    except Exception:
        return True   # fail open so an outage doesn't block every question


def check_input(text):
    text = (text or "").strip()
    if not text:
        return False, "Please type a question."
    if len(text) > MAX_QUESTION_CHARS:
        return False, f"That message is too long. Please keep it under {MAX_QUESTION_CHARS} characters."
    if _looks_like_injection(text):
        log_event("input_injection", text)
        return False, ("I can only answer questions about your documents. I can't follow requests to "
                       "change or reveal my instructions. Please ask something about your files.")
    if ENABLE_LLM_MODERATION and not _moderation_ok(text):
        log_event("input_moderation", text)
        return False, "I can't help with that request. Please ask something about your documents."
    return True, ""


def check_rate(state):
    """state is st.session_state. Enforces per-minute and per-session limits."""
    now = time.time()
    stamps = [t for t in state.get("q_times", []) if now - t < 60]
    if len(stamps) >= MAX_QUESTIONS_PER_MINUTE:
        log_event("rate_limit_minute")
        return False, "You're sending messages too quickly. Please wait a few seconds and try again."
    if state.get("q_count", 0) >= MAX_QUESTIONS_PER_SESSION:
        log_event("rate_limit_session")
        return False, "You've reached the question limit for this session. Click New chat or refresh the page."
    stamps.append(now)
    state["q_times"] = stamps
    state["q_count"] = state.get("q_count", 0) + 1
    return True, ""


# ================= upload guardrails =================
def check_upload(file, file_count, total_chunks):
    name = file.name.lower()
    if not name.endswith(ALLOWED_EXTS):
        return False, f"**{file.name}** is not a supported file type."
    if file.size == 0:
        return False, f"**{file.name}** is empty."
    if file.size > MAX_FILE_MB * 1024 * 1024:
        return False, f"**{file.name}** is larger than {MAX_FILE_MB} MB."
    if file_count >= MAX_FILES_PER_SESSION:
        return False, f"You've reached the limit of {MAX_FILES_PER_SESSION} files. Click Clear documents to start over."
    if total_chunks >= MAX_CHUNKS_PER_SESSION:
        return False, "Your documents are already at the size limit for this session. Click Clear documents to start over."
    return True, ""


# ================= document (indirect injection) guardrail =================
def sanitize_chunk(text):
    """Strip tag look-alikes and neutralise instruction-like text inside a document chunk."""
    flagged = False
    cleaned = _TAGS.sub("", text)
    for p in _INJECTION:
        if p.search(cleaned):
            flagged = True
            cleaned = p.sub("[filtered]", cleaned)
    return cleaned, flagged


# ================= output guardrails =================
def filter_output(reply):
    """Returns (reply, blocked). Blocks canary leaks and redacts API-key-like strings."""
    if CANARY in reply:
        log_event("system_prompt_leak")
        return "I can't share that. Please ask a question about your documents.", True
    for p in _SECRETS:
        reply = p.sub("[redacted]", reply)
    if MASK_PII_IN_ANSWERS:
        reply, _ = mask_pii(reply)
    return reply, False


def grounding_check(reply, context):
    """True = supported, False = unsupported, None = unknown or disabled."""
    if not ENABLE_GROUNDING_CHECK or not reply:
        return None
    prompt = (
        "You are a strict fact checker. Below are CONTEXT and an ANSWER. Decide whether every "
        "factual claim in the ANSWER is supported by the CONTEXT. An answer saying the "
        "information is not available counts as SUPPORTED. Reply with exactly one word: "
        f"SUPPORTED or UNSUPPORTED.\n\nCONTEXT:\n{context[:6000]}\n\nANSWER:\n{reply[:2000]}"
    )
    try:
        verdict = generate([{"role": "user", "content": prompt}], max_tokens=20).strip().upper()
    except Exception:
        return None
    if verdict.startswith("UNSUPPORTED"):
        return False
    if verdict.startswith("SUPPORTED"):
        return True
    return None