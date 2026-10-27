"""Deterministic checks for common AI-writing tells in our own prose (not in quotes or extracted text).
Patterns follow the humanizer skill's catalogue; only the ones a regex can catch reliably are here."""

import re

WORDS = ("actually", "additionally", "align with", "bolstered", "crucial", "deep dive", "delve", "delves", "delving",
         "enduring", "enhance", "enhances", "garner", "highlights", "highlighted", "highlighting", "interplay",
         "intricate", "intricacies", "landscape", "meticulous", "meticulously", "pivotal", "showcase", "showcases",
         "showcasing", "tapestry", "testament", "underscore", "underscores", "underscored", "vibrant", "valuable",
         "seamless", "seamlessly", "leverage", "leverages", "comprehensive", "noteworthy", "notably")

PATTERNS = [
    ("ai-word", re.compile(r"\b(" + "|".join(re.escape(w) for w in WORDS) + r")\b", re.I),
     "overused AI word; say the plain thing"),
    ("inflation", re.compile(r"\b(stands? as a testament|plays? an? (?:key|crucial|pivotal|vital) role|underscores? "
                             r"(?:its|the) importance|evolving landscape|setting the stage|a step in the right "
                             r"direction|reflects a broader|lasting legacy)\b", re.I),
     "inflated significance; keep the fact, drop the importance claim"),
    ("ing-rider", re.compile(r",\s+(highlighting|underscoring|emphasizing|showcasing|reflecting|symbolizing|fostering|"
                             r"ensuring|contributing to)\b", re.I),
     "trailing -ing phrase adds weight, not a fact; cut it or state what the source says"),
    ("not-x-but-y", re.compile(r"\b(?:not (?:just|only|merely) [^.;:\n]{1,60}, but|isn't (?:just )?[^.;:\n]{1,40}[,;] "
                               r"it's)\b", re.I),
     "not-X-but-Y contrast; state the point directly"),
    ("about-the-document", re.compile(r"(?:^|\n)\s*(?:[-*]\s+)?(?:This|The) (?:source|document|article|page|post|paper|"
                                      r"whitepaper|repo|repository|advisory|guide|card|note) (?:provides|describes|"
                                      r"discusses|outlines|explores|offers|presents|covers|examines|delves)\b", re.I),
     "writes about the document; start with its subject (who/what, when, what it says)"),
    ("chatbot", re.compile(r"\b(I hope this helps|let me know if|great question|certainly!|as an AI)\b", re.I),
     "chat residue; delete it"),
]

_SKIP_SECTIONS = {"excerpts", "timeline", "changes", "evidence (generated)", "contents (generated)"}
_FENCE = re.compile(r"(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$")
_BLOCK = re.compile(r"(?s)<!-- kb:(\w+) start -->.*?<!-- kb:\1 end -->")
_LINK = re.compile(r"!?\[\[[^\]]*\]\]|\[[^\]]*\]\([^)]*\)|`[^`\n]*`")


def prose(body: str) -> str:
    """Our own writing in a record body: drops code, generated blocks, quotes, links/code spans and
    sections that hold verbatim or tool-written text. Line count is preserved for reporting."""
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))
    t = _BLOCK.sub(blank, _FENCE.sub(blank, body))
    out, skip = [], False
    for line in t.split("\n"):
        h = re.match(r"(#{2,3})\s+(.*)", line)
        if h:
            name = h.group(2).strip().lower()
            if len(h.group(1)) == 2:
                skip = name in _SKIP_SECTIONS
            out.append("")
            continue
        out.append("" if skip or line.lstrip().startswith(">") else _LINK.sub(" ", line))
    return "\n".join(out)


def check(body: str, max_dashes: int = 3) -> list[tuple[int, str, str, str]]:
    """(line_number_in_body, rule, matched_text, hint) for each tell found."""
    text, hits = prose(body), []
    for rule, rx, hint in PATTERNS:
        for m in rx.finditer(text):
            hits.append((text.count("\n", 0, m.start()) + 1, rule, m.group(0).strip(), hint))
    for n, para in _paragraphs(text):
        dashes = para.count("—") + para.count(" -- ")
        if dashes > max_dashes:
            hits.append((n, "dashes", f"{dashes} dashes in one paragraph",
                         "dashes as the universal connector; use periods, commas or parentheses"))
    return sorted(hits)


def _paragraphs(text):
    start = 1
    for chunk in re.split(r"\n\s*\n", text):
        yield start, chunk
        start += chunk.count("\n") + 2
