import io
import re

from groq import Groq
from PIL import Image

from config import GROQ_API_KEY
from guardrails import log_event
from vision import _prepare

ENABLE_IMAGE_MODERATION = True          # False = skip the safety-model call (validation still runs)
IMAGE_MODERATION_FAIL_CLOSED = False    # True = block images if the safety scan is unavailable
GUARD_MODEL = "meta-llama/llama-guard-4-12b"

MAX_PIXELS = 40_000_000
MIN_SIDE = 32
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF"}

# Llama Guard category codes that block an image. Categories such as privacy, specialized
# advice, defamation, IP and elections are ignored, since ordinary documents often trigger them.
BLOCKED_CATEGORIES = {"S1", "S2", "S3", "S4", "S9", "S10", "S11", "S12"}
CATEGORY_NAMES = {
    "S1": "violent content", "S2": "criminal content", "S3": "sex-related crime",
    "S4": "child safety", "S9": "weapons", "S10": "hateful content",
    "S11": "self-harm", "S12": "sexual content",
}

_client = Groq(api_key=GROQ_API_KEY)


def validate_image(data):
    """Check that the bytes are a real, reasonably sized image."""
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        fmt = (img.format or "").upper()
    except Exception:
        return False, "not a readable image"
    if fmt not in ALLOWED_FORMATS:
        return False, "unsupported image format"
    if w < MIN_SIDE or h < MIN_SIDE:
        return False, "image too small"
    if w * h > MAX_PIXELS:
        return False, "image dimensions too large"
    return True, ""


def moderate_image(data):
    """Returns ('ok' | 'unsafe', reason)."""
    try:
        b64 = _prepare(data, max_side=1024)
        resp = _client.chat.completions.create(
            model=GUARD_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Image to review."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
            max_completion_tokens=40,
        )
        out = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print("Image moderation error:", e)
        log_event("image_scan_error", e)
        if IMAGE_MODERATION_FAIL_CLOSED:
            return "unsafe", "the safety scan was unavailable"
        return "ok", ""

    if out.lower().startswith("unsafe"):
        cats = set(re.findall(r"S\d+", out.upper()))
        hit = cats & BLOCKED_CATEGORIES
        if not cats or hit:
            names = ", ".join(CATEGORY_NAMES.get(c, c) for c in sorted(hit)) or "unsafe content"
            log_event("image_blocked", names)
            return "unsafe", names
    return "ok", ""


def scan_image(data):
    """Returns (status, reason) where status is 'ok', 'invalid' or 'unsafe'."""
    ok, reason = validate_image(data)
    if not ok:
        return "invalid", reason
    if not ENABLE_IMAGE_MODERATION:
        return "ok", ""
    return moderate_image(data)