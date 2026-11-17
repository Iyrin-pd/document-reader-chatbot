import hashlib
import hmac
import json
import os
import re
import secrets
import time

from config import EMBED_DIM
from store import _index

NS = "__users__"
_DUMMY = [0.1] * EMBED_DIM      # Pinecone needs a vector; the data lives in the metadata

# ---------- limits (tune these) ----------
DAILY_QUESTIONS = 50
DAILY_UPLOADS = 10
DAILY_VISION_CALLS = 60
MAX_USERS = 50                  # Pinecone's free plan allows 100 namespaces per index
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 300
PBKDF2_ROUNDS = 200_000
SIGNUP_CODE = os.getenv("SIGNUP_CODE", "").strip()   # optional invite code

_USERNAME = re.compile(r"^[a-z0-9_]{3,20}$")


def _today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _hash(password, salt_hex):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), PBKDF2_ROUNDS
    ).hex()


def _get(username):
    res = _index.fetch(ids=[username], namespace=NS)
    vec = res.vectors.get(username)
    if vec is None:
        return None
    return dict(vec.metadata or {})


def _save(username, rec):
    _index.upsert(
        vectors=[{"id": username, "values": _DUMMY, "metadata": rec}],
        namespace=NS,
    )


def _user_count():
    try:
        info = (_index.describe_index_stats().namespaces or {}).get(NS)
        if info is None:
            return 0
        return info["vector_count"] if isinstance(info, dict) else getattr(info, "vector_count", 0)
    except Exception:
        return 0


def namespace_for(username):
    return f"user-{username}"


# ---------- accounts ----------
def signup(username, password, code=""):
    """Returns (record, error_message)."""
    username = (username or "").strip().lower()
    password = password or ""
    if not _USERNAME.match(username):
        return None, "Username must be 3-20 characters: lowercase letters, numbers or underscore."
    if not 8 <= len(password) <= 128:
        return None, "Password must be 8-128 characters."
    if SIGNUP_CODE and not hmac.compare_digest((code or "").strip(), SIGNUP_CODE):
        return None, "Invalid invite code."
    if _user_count() >= MAX_USERS:
        return None, "Sign-ups are closed: the user limit has been reached."
    if _get(username) is not None:
        return None, "That username is already taken."

    salt = secrets.token_hex(16)
    rec = {
        "pw_hash": _hash(password, salt),
        "salt": salt,
        "created": int(time.time()),
        "day": _today(),
        "q_count": 0,
        "up_count": 0,
        "vis_count": 0,
        "fails": 0,
        "locked_until": 0,
        "files_json": "{}",
    }
    _save(username, rec)
    return rec, ""


def login(username, password):
    """Returns (record, error_message)."""
    username = (username or "").strip().lower()
    password = password or ""
    rec = _get(username) if _USERNAME.match(username) else None

    if rec is None:
        _hash(password, secrets.token_hex(16))   # keep timing similar for unknown users
        return None, "Invalid username or password."

    now = time.time()
    locked_until = float(rec.get("locked_until", 0))
    if locked_until > now:
        mins = int((locked_until - now) // 60) + 1
        return None, f"Too many failed attempts. Try again in about {mins} minute(s)."

    if not hmac.compare_digest(_hash(password, rec["salt"]), rec["pw_hash"]):
        rec["fails"] = int(rec.get("fails", 0)) + 1
        if rec["fails"] >= MAX_FAILED_LOGINS:
            rec["locked_until"] = now + LOCKOUT_SECONDS
            rec["fails"] = 0
        _save(username, rec)
        return None, "Invalid username or password."

    if int(rec.get("fails", 0)) or float(rec.get("locked_until", 0)):
        rec["fails"] = 0
        rec["locked_until"] = 0
        _save(username, rec)
    return rec, ""


# ---------- quotas and stored files ----------
def _roll(rec):
    """Reset the daily counters when the (UTC) date changes."""
    today = _today()
    if rec.get("day") != today:
        rec["day"] = today
        rec["q_count"] = 0
        rec["up_count"] = 0
        rec["vis_count"] = 0


def remaining(rec):
    _roll(rec)
    return {
        "questions": max(0, DAILY_QUESTIONS - int(rec.get("q_count", 0))),
        "uploads": max(0, DAILY_UPLOADS - int(rec.get("up_count", 0))),
        "vision": max(0, DAILY_VISION_CALLS - int(rec.get("vis_count", 0))),
    }


def load_files(rec):
    try:
        return json.loads(rec.get("files_json") or "{}")
    except Exception:
        return {}


def update(username, rec, files=None, questions=0, uploads=0, vision=0):
    """Add to the usage counters and/or save the file list, in a single write."""
    _roll(rec)
    rec["q_count"] = int(rec.get("q_count", 0)) + questions
    rec["up_count"] = int(rec.get("up_count", 0)) + uploads
    rec["vis_count"] = int(rec.get("vis_count", 0)) + vision
    if files is not None:
        rec["files_json"] = json.dumps(files)
    _save(username, rec)