import re
import time

from fastembed import TextEmbedding
from pinecone import Pinecone, ServerlessSpec

from config import PINECONE_API_KEY, INDEX_NAME, EMBED_DIM
from readers import read_document

CHUNK_SIZE = 800
OVERLAP = 150
BATCH = 100

_pc = Pinecone(api_key=PINECONE_API_KEY)
_model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")

if INDEX_NAME not in _pc.list_indexes().names():
    _pc.create_index(
        name=INDEX_NAME,
        dimension=EMBED_DIM,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )
    while not _pc.describe_index(INDEX_NAME).status["ready"]:
        time.sleep(1)

_index = _pc.Index(INDEX_NAME)


def _embed(texts):
    return [v.tolist() for v in _model.embed(texts)]


def chunk_text(text, size=CHUNK_SIZE, overlap=OVERLAP):
    """Line-based chunking so table rows stay whole."""
    lines = []
    for line in text.split("\n"):
        while len(line) > size:
            lines.append(line[:size])
            line = line[size - overlap:]
        lines.append(line)

    chunks, cur, cur_len = [], [], 0
    for line in lines:
        if cur and cur_len + len(line) + 1 > size:
            chunks.append("\n".join(cur).strip())
            keep, kl = [], 0
            for l in reversed(cur):
                if kl + len(l) > overlap:
                    break
                keep.insert(0, l)
                kl += len(l) + 1
            cur, cur_len = keep, kl
        cur.append(line)
        cur_len += len(line) + 1
    if cur:
        chunks.append("\n".join(cur).strip())
    return [c for c in chunks if c]


def safe_id(s):
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)


def ingest_file(file_obj, filename, namespace):
    """Read, chunk, embed and upsert one file. Returns the number of chunks."""
    records = []
    counter = 0
    for page_num, text in read_document(file_obj, filename):
        for chunk in chunk_text(text):
            records.append({
                "id": f"{safe_id(filename)}-{counter}",
                "text": chunk,
                "source": filename,
                "page": page_num,
            })
            counter += 1

    if not records:
        return 0

    for i in range(0, len(records), BATCH):
        batch = records[i:i + BATCH]
        embeddings = _embed([r["text"] for r in batch])
        vectors = [
            {
                "id": r["id"],
                "values": emb,
                "metadata": {"text": r["text"], "source": r["source"], "page": r["page"]},
            }
            for r, emb in zip(batch, embeddings)
        ]
        _index.upsert(vectors=vectors, namespace=namespace)

    return len(records)


def wait_until_indexed(namespace, expected, timeout=15):
    """Pinecone serverless is eventually consistent; wait until vectors are searchable."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            stats = _index.describe_index_stats()
            info = (stats.namespaces or {}).get(namespace)
            if info is not None:
                count = info["vector_count"] if isinstance(info, dict) else getattr(info, "vector_count", 0)
                if count >= expected:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def retrieve(question, namespace, k=4):
    vec = _embed([question])[0]
    res = _index.query(vector=vec, top_k=k, include_metadata=True, namespace=namespace)
    return [
        {
            "text": m.metadata["text"],
            "source": m.metadata["source"],
            "page": int(m.metadata["page"]),
            "score": float(m.score),
        }
        for m in res.matches
    ]


def delete_namespace(namespace):
    try:
        _index.delete(delete_all=True, namespace=namespace)
    except Exception:
        pass