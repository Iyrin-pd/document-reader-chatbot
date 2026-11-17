import os
from dotenv import load_dotenv

load_dotenv()

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")

INDEX_NAME = "rag-chatbot"
EMBED_MODEL = "all-MiniLM-L6-v2"
EMBED_DIM = 384

# Fill in only the one you use
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

LLM_MODEL = "openai/gpt-oss-120b"

VISION_MODEL = "qwen/qwen3.6-27b"
MAX_VISION_CALLS = 20   # max images/pages sent to the vision model per file