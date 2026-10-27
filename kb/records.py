"""Record files: YAML front matter, body sections, excerpts, wikilinks, generated blocks."""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from kb.util import KBError, atomic_write

_FM = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def split_front_matter(text: str):
    """Return (front_matter_dict_or_None, body). Raises KBError on invalid YAML."""
    m = _FM.match(text)
    if not m:
        return None, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise KBError(f"invalid YAML front matter: {e}".splitlines()[0]) from None
    if not isinstance(fm, dict):
        raise KBError("front matter must be a YAML mapping")
    return fm, text[m.end():]


# Front matter is written in a fixed order: what a reader needs first, tool metadata last.
FIRST_KEYS = ("type", "title", "description", "status", "superseded_by", "tier", "kind", "aliases", "tags")
LAST_KEYS = ("original", "sha256", "bytes", "format", "captured", "captured_from", "obtained_from", "original_note",
             "originals", "originals_note", "text", "text_sha256", "text_quality", "extracted_with",
             "last_checked", "mined_by", "mined_on", "updated")


def ordered(fm: dict) -> dict:
    first = [k for k in FIRST_KEYS if k in fm]
    last = [k for k in LAST_KEYS if k in fm]
    middle = [k for k in fm if k not in first and k not in last]
    return {k: fm[k] for k in first + middle + last}


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):  # never emit &anchors / *aliases
        return True


def dump_yaml(data) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=1000)


def dump_front_matter(fm: dict) -> str:
    return "---\n" + dump_yaml(ordered(fm)) + "---\n"


@dataclass
class Record:
    path: Path              # absolute
    fm: dict
    body: str
    rel: str = ""           # path relative to the vault root, posix

    @property
    def id(self) -> str:
        return self.path.stem

    @property
    def type(self) -> str:
        return str(self.fm.get("type", ""))

    @property
    def title(self) -> str:
        return str(self.fm.get("title", self.id))

    def text(self) -> str:
        return dump_front_matter(self.fm) + self.body

    def save(self) -> None:
        atomic_write(self.path, self.text())


def read_record(path, rel: str = "") -> Record | None:
    """Read a Markdown file; None if it has no front matter."""
    path = Path(path)
    fm, body = split_front_matter(path.read_text(encoding="utf-8", errors="replace"))
    if fm is None or "type" not in fm:  # e.g. skill or tool front matter: not a kb record
        return None
    return Record(path=path, fm=fm, body=body, rel=rel)


def write_record(path, fm: dict, body: str) -> Record:
    rec = Record(path=Path(path), fm=fm, body=body if body.startswith("\n") or not body else "\n" + body)
    rec.save()
    return rec


# --- sections -------------------------------------------------------------------

_HEADING = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"(?ms)^[ \t]*(```|~~~).*?(?:^[ \t]*\1[ \t]*$|\Z)")


def _mask_fences(text: str) -> str:
    """Blank out fenced code blocks (same length, newlines kept) so headings inside them are ignored."""
    return _FENCE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def find_section(body: str, heading: str, level: int = 2):
    """(start_of_heading, start_of_content, end) for the first `## heading`, or None.
    The section ends at the next heading of the same or higher level."""
    masked = _mask_fences(body)
    for m in _HEADING.finditer(masked):
        if len(m.group(1)) == level and m.group(2).strip().lower() == heading.lower():
            end = len(body)
            for n in _HEADING.finditer(masked, m.end()):
                if len(n.group(1)) <= level:
                    end = n.start()
                    break
            return m.start(), m.end(), end
    return None


def get_section(body: str, heading: str, level: int = 2) -> str | None:
    s = find_section(body, heading, level)
    return None if s is None else body[s[1]:s[2]].strip("\n")


def append_to_section(body: str, heading: str, line: str, level: int = 2, top: bool = False) -> str:
    """Add a line to a section (at its end, or right under the heading when top=True),
    creating the section at the end of the body if it is missing."""
    s = find_section(body, heading, level)
    if s is None:
        return body.rstrip("\n") + f"\n\n{'#' * level} {heading}\n{line}\n"
    _, content, end = s
    if top:
        return body[:content] + "\n" + line + body[content:]
    before = body[:end].rstrip("\n")
    return before + "\n" + line + "\n" + ("\n" + body[end:] if body[end:] else "")


def without_section(body: str, heading: str, level: int = 2) -> str:
    s = find_section(body, heading, level)
    return body if s is None else body[:s[0]] + body[s[2]:]


# --- excerpts -------------------------------------------------------------------

@dataclass
class Excerpt:
    eid: str                # "e3"
    text: str               # quote text without '>' markers
    location: str = ""
    withdrawn: str = ""     # reason, if withdrawn
    gone: str = ""          # date, if gone in a recapture
    raw: str = field(default="", repr=False)


_EXC_HEAD = re.compile(r"(?m)^###[ \t]+(e\d+)[ \t]*$")


def parse_excerpts(body: str) -> list[Excerpt]:
    section = get_section(body, "Excerpts")
    if not section:
        return []
    out = []
    heads = list(_EXC_HEAD.finditer(_mask_fences(section)))
    for i, m in enumerate(heads):
        chunk = section[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(section)]
        quote, loc, withdrawn, gone = [], "", "", ""
        for line in chunk.splitlines():
            s = line.strip()
            if s.startswith(">"):
                quote.append(s[1:].lstrip())
            elif s.lower().startswith("location:"):
                loc = s.split(":", 1)[1].strip()
            elif s.lower().startswith("withdrawn:"):
                withdrawn = s.split(":", 1)[1].strip() or "withdrawn"
            elif s.lower().startswith("gone:"):
                gone = s.split(":", 1)[1].strip() or "gone"
        out.append(Excerpt(m.group(1), "\n".join(quote).strip(), loc, withdrawn, gone, chunk))
    return out


def next_excerpt_id(body: str) -> str:
    nums = [int(e.eid[1:]) for e in parse_excerpts(body)]
    return f"e{max(nums, default=0) + 1}"


# --- wikilinks --------------------------------------------------------------------

@dataclass(frozen=True)
class Link:
    target: str
    anchor: str = ""
    alias: str = ""
    embed: bool = False


_WIKILINK = re.compile(r"(!?)\[\[([^\[\]|#\n]*)(?:#([^\[\]|\n]*))?(?:\|([^\[\]\n]*))?\]\]")
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)


def parse_links(text: str, skip_code: bool = True) -> list[Link]:
    if skip_code:
        text = _CODE.sub("", text)
    return [Link(m.group(2).strip(), (m.group(3) or "").strip(), (m.group(4) or "").strip(), m.group(1) == "!")
            for m in _WIKILINK.finditer(text) if m.group(2).strip()]


def links_in_value(value) -> list[Link]:
    """Wikilinks inside any (nested) front matter value."""
    if isinstance(value, str):
        return parse_links(value, skip_code=False)
    if isinstance(value, dict):
        return [l for v in value.values() for l in links_in_value(v)]
    if isinstance(value, (list, tuple)):
        return [l for v in value for l in links_in_value(v)]
    return []


def link_target_name(target: str) -> str:
    """Basename used for resolution: 'notes/x.md' -> 'x'; 'img.png' stays 'img.png'."""
    name = target.rsplit("/", 1)[-1]
    return name[:-3] if name.lower().endswith(".md") else name


# --- generated blocks ---------------------------------------------------------------

def replace_block(text: str, name: str, content: str) -> str:
    """Replace (or append) the region between <!-- kb:NAME start --> and <!-- kb:NAME end -->."""
    start, end = f"<!-- kb:{name} start -->", f"<!-- kb:{name} end -->"
    block = f"{start}\n{content.strip()}\n{end}"
    i, j = text.find(start), text.find(end)
    if i >= 0 and j > i:
        return text[:i] + block + text[j + len(end):]
    if (i >= 0) != (j >= 0):
        raise KBError(f"unbalanced kb:{name} markers; fix or delete them and re-run")
    return text.rstrip("\n") + ("\n\n" if text.strip() else "") + block + "\n"


def get_block(text: str, name: str) -> str | None:
    start, end = f"<!-- kb:{name} start -->", f"<!-- kb:{name} end -->"
    i, j = text.find(start), text.find(end)
    return text[i + len(start):j].strip() if i >= 0 and j > i else None
