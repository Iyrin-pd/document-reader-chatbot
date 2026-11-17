import re

MASK_PII_IN_DOCS = True        # mask files before they are embedded and stored
MASK_PII_IN_QUESTIONS = True   # mask what the user types before it reaches the AI
MASK_PII_IN_ANSWERS = True     # mask anything that slips into an answer


def _luhn_ok(s):
    digits = [int(c) for c in re.sub(r"\D", "", s)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _ip_ok(s):
    return all(int(x) <= 255 for x in s.split("."))


# Order matters: more specific patterns run first.
_RULES = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), None),
    ("CARD", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), _luhn_ok),
    ("SSN", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"), None),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), None),
    ("AADHAAR", re.compile(r"(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\d)"), None),
    ("PHONE", re.compile(r"(?<![\w+])\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{2,5}){2,4}(?!\d)"), None),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?[6-9]\d{4}[\s\-]?\d{5}(?!\d)"), None),
    ("PHONE", re.compile(r"(?<!\d)\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)"), None),
    ("IP", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), _ip_ok),
]


def mask_pii(text):
    """Return (masked_text, counts), e.g. (..., {'EMAIL': 2, 'PHONE': 1})."""
    counts = {}
    if not text:
        return text, counts
    for label, pattern, validator in _RULES:
        def repl(m, label=label, validator=validator):
            if validator and not validator(m.group(0)):
                return m.group(0)
            counts[label] = counts.get(label, 0) + 1
            return f"[{label}]"
        text = pattern.sub(repl, text)
    return text, counts