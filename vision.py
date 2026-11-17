import base64
import io
import re

from groq import Groq
from PIL import Image

from config import GROQ_API_KEY, VISION_MODEL

_client = Groq(api_key=GROQ_API_KEY)

PROMPTS = {
    "transcribe": (
        "This is a scanned document page. Transcribe ALL text exactly as written, in reading "
        "order. Convert any tables to markdown tables. Briefly describe any figures, charts or "
        "images in square brackets. Output only the content."
    ),
    "describe": (
        "Describe only the figures, charts, diagrams, photos or screenshots in this image: what "
        "they show, their labels, numbers and trends. For charts, give the key values. Do not "
        "repeat normal body text. If there is nothing visual, reply exactly: NONE"
    ),
    "image": (
        "Describe this image in detail. Transcribe all visible text exactly. Convert any tables "
        "to markdown tables. For charts or graphs, give the key values and trends."
    ),
}


def _prepare(image_bytes, max_side=1600):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def describe_image(image_bytes, mode="image"):
    """Send an image to the vision model. Returns text, or '' on any failure."""
    try:
        b64 = _prepare(image_bytes)
        resp = _client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPTS[mode]},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
            max_completion_tokens=4000,
        )
        text = resp.choices[0].message.content or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        return "" if text.upper() == "NONE" else text
    except Exception as e:
        print("Vision error:", e)
        return ""