"""Secret redaction applied to every log chunk / payload string at ingest (D12, Security §7)."""
import math
import re

_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|pwd|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[a-z0-9\-_\.=]+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)postgres(?:ql)?://[^:\s]+:[^@\s]+@"),
    re.compile(r"(?i)mysql://[^:\s]+:[^@\s]+@"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"xox[bap]-[A-Za-z0-9\-]+"),
]
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in counts.values())


def redact(text: str) -> str:
    if not text:
        return text
    for p in _PATTERNS:
        text = p.sub("[REDACTED]", text)
    # high-entropy blobs (likely tokens)
    return _TOKEN_RE.sub(lambda m: "[REDACTED]" if _entropy(m.group(0)) > 4.2 else m.group(0), text)
