"""Secret and PII detection, and redaction. Used by triage, capture and session ingest."""

import re
from dataclasses import dataclass


@dataclass
class Hit:
    kind: str       # e.g. "nt-hash", "github-token"
    category: str   # "secrets" or "pii"
    start: int
    end: int
    value: str


# (kind, category, pattern, group) — group is the regex group to redact (0 = whole match)
PATTERNS = [
    ("private-key", "secrets", r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----", 0),
    ("pwdump-hash", "secrets", r"(?m)^[^\s:]+:\d+:([0-9a-fA-F]{32}:[0-9a-fA-F]{32})", 1),
    ("kerberos-hash", "secrets", r"\$krb5(?:tgs|asrep|pa)\$[^\s]+", 0),
    ("crypt-hash", "secrets", r"\$(?:6|5|y|2[aby])\$[./0-9A-Za-z$]{20,}", 0),
    ("netntlm-hash", "secrets", r"(?m)^[^\s:]+::[^\s:]*:[0-9a-fA-F]{16}:[0-9a-fA-F]{32}:[0-9a-fA-F]+", 0),
    ("aws-access-key", "secrets", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", 0),
    ("github-token", "secrets", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b", 0),
    ("slack-token", "secrets", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", 0),
    ("anthropic-key", "secrets", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b", 0),
    ("openai-key", "secrets", r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b", 0),
    ("jwt", "secrets", r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", 0),
    ("assignment", "secrets",
     r"(?i)\b(?:password|passwd|pwd|passphrase|secret|api[_-]?key|access[_-]?token|auth[_-]?token|token)\b"
     r"[\"']?\s*[:=]\s*[\"']?([^\s\"',;]{4,})", 1),
    ("ssn", "pii", r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b", 0),
    ("card-number", "pii", r"\b(?:\d[ -]?){13,19}\b", 0),
]
_COMPILED = [(k, c, re.compile(p), g) for k, c, p, g in PATTERNS]


def _luhn(digits: str) -> bool:
    d = [int(x) for x in digits]
    total = sum(d[-1::-2]) + sum(sum(divmod(2 * x, 10)) for x in d[-2::-2])
    return total % 10 == 0


def scan(text: str, extra_patterns=()) -> list[Hit]:
    """All non-overlapping hits, earliest first. extra_patterns: regex strings (profile secrets.extra_patterns)."""
    pats = _COMPILED + [(f"custom-{i}", "secrets", re.compile(p), 0) for i, p in enumerate(extra_patterns)]
    hits = []
    for kind, cat, rx, g in pats:
        for m in rx.finditer(text or ""):
            start, end = m.span(g)
            val = m.group(g)
            if kind == "card-number":
                digits = re.sub(r"\D", "", val)
                if not (13 <= len(digits) <= 19 and _luhn(digits)):
                    continue
            hits.append(Hit(kind, cat, start, end, val))
    hits.sort(key=lambda h: (h.start, -(h.end - h.start)))
    out, last = [], -1
    for h in hits:
        if h.start >= last:
            out.append(h)
            last = h.end
    return out


def categories(hits) -> list[str]:
    return sorted({h.category for h in hits})


def redact(text: str, extra_patterns=()) -> tuple[str, list[Hit]]:
    """Replace each hit with [REDACTED:kind]. Returns (redacted_text, hits)."""
    hits = scan(text, extra_patterns)
    parts, pos = [], 0
    for h in hits:
        parts.append(text[pos:h.start])
        parts.append(f"[REDACTED:{h.kind}]")
        pos = h.end
    parts.append(text[pos:])
    return "".join(parts), hits
