from groq import Groq

from config import GROQ_API_KEY, LLM_MODEL

_client = Groq(api_key=GROQ_API_KEY)


def generate(messages, system="", max_tokens=1000):
    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    resp = _client.chat.completions.create(
        model=LLM_MODEL,
        messages=msgs,
        max_completion_tokens=max_tokens + 2000,
    )
    return (resp.choices[0].message.content or "").strip()