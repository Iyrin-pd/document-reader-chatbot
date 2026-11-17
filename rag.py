from llm import generate
from store import retrieve

MIN_SCORE = 0.20      # below this, a chunk is treated as irrelevant
HIGH_SCORE = 0.50     # at or above this, confidence is High
MEDIUM_SCORE = 0.30   # at or above this, confidence is Medium

SYSTEM = (
    "You are a helpful assistant that answers questions about the user's uploaded documents. "
    "Answer using ONLY the provided context. "
    "If the answer is not in the context, say you don't know. "
    "Mention the source file names you used."
)


def confidence_label(score):
    if score >= HIGH_SCORE:
        return "High"
    if score >= MEDIUM_SCORE:
        return "Medium"
    return "Low"


def rewrite_question(question, history):
    """Turn a follow-up like 'what about its price?' into a standalone question."""
    if not history:
        return question
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
    prompt = (
        "Rewrite the last user question as a standalone question using the "
        "conversation for context. Return ONLY the rewritten question.\n\n"
        f"Conversation:\n{convo}\n\nLast question: {question}"
    )
    return generate([{"role": "user", "content": prompt}], max_tokens=200) or question


def answer(question, namespace, history=None, k=4):
    history = history or []
    standalone = rewrite_question(question, history)
    chunks = retrieve(standalone, namespace, k=k)

    top_score = chunks[0]["score"] if chunks else 0.0
    relevant = [c for c in chunks if c["score"] >= MIN_SCORE]

    if not relevant:
        return {
            "reply": (
                "I couldn't find anything relevant to that in your uploaded documents. "
                "Try rephrasing, or upload a document that covers this topic."
            ),
            "chunks": [],
            "confidence": "Low",
            "top_score": top_score,
        }

    context = "\n\n".join(
        f"[Source: {c['source']}, page {c['page']}]\n{c['text']}" for c in relevant
    )
    user_msg = f"Context:\n{context}\n\nQuestion: {question}"
    messages = history[-6:] + [{"role": "user", "content": user_msg}]

    reply = generate(messages, system=SYSTEM, max_tokens=1000)
    if not reply:
        reply = "The model returned an empty response. Please try again."

    return {
        "reply": reply,
        "chunks": relevant,
        "confidence": confidence_label(top_score),
        "top_score": top_score,
    }


def suggest_followups(question, reply):
    """Return up to 3 short follow-up questions the user might ask next."""
    prompt = (
        "Based on this question and answer about a document, suggest 3 short follow-up "
        "questions the user might ask next. Return exactly 3 lines, one question per line, "
        "with no numbering or bullets.\n\n"
        f"Question: {question}\n\nAnswer: {reply}"
    )
    try:
        text = generate([{"role": "user", "content": prompt}], max_tokens=200)
        lines = [l.strip().lstrip("-•*0123456789.) ").strip() for l in text.splitlines()]
        return [l for l in lines if l][:3]
    except Exception:
        return []