"""Heuristic flag for prompt-injection-like content in captured text (spec §1.9).
It flags for a human/agent to look at; it never blocks or alters content."""

import re

_PATTERNS = [
    r"\b(?:ignore|disregard|forget)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|all)\b[^.\n]{0,20}\b(?:instructions?|prompts?|rules|context)\b",
    r"\byou are now\b[^.\n]{0,60}",
    r"\b(?:system|developer) (?:prompt|message|instructions?)\b",
    r"<\|(?:im_start|im_end|system|endoftext)\|>",
    r"\bdo not (?:tell|inform|alert|mention (?:this )?to) the user\b",
    r"\b(?:as an ai|ai assistant|language model)[^.\n]{0,40}\b(?:must|should|need to)\b",
    r"\b(?:run|execute) the following (?:command|code|script)\b",
    r"\bnew instructions?:",
]
_RX = [re.compile(p, re.I) for p in _PATTERNS]
_HIDDEN = re.compile(r"[\U000E0000-\U000E007F]|[​-‏⁠-⁤‪-‮]{3,}")


def scan(text: str, limit: int = 5) -> list[str]:
    """Up to `limit` short snippets that look like instructions aimed at an agent, or hidden characters."""
    found = []
    for rx in _RX:
        for m in rx.finditer(text or ""):
            a, b = max(0, m.start() - 30), min(len(text), m.end() + 30)
            found.append(" ".join(text[a:b].split()))
            if len(found) >= limit:
                return found
    if _HIDDEN.search(text or ""):
        found.append("hidden Unicode tag/zero-width/bidi characters present")
    return found[:limit]
