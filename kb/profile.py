"""kb.yaml profiles: traits, types and key specs (spec §2.4-2.5)."""

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from kb.util import KBError, parse_cadence, parse_time

SCHEMA = 4
TEMPLATES = Path(__file__).parent / "templates"

TRAITS = {
    "origin": ("external", "internal"),
    "original": ("none", "immutable"),
    "text": ("none", "extracted", "ocr"),
    "quotes": ("none", "verbatim"),
    "role": ("synthesis", "input", "output"),
    "tracked": ("none", "refresh", "watch"),
    "claims": ("free", "cited"),
}
STATUSES = ("active", "draft", "stale", "superseded", "implemented")
SCALAR_SPECS = ("string", "date", "list", "link", "links", "bool", "int")


@dataclass
class TypeDef:
    name: str
    folder: str
    origin: str
    original: str = "none"
    text: str = "none"
    quotes: str = "none"
    role: str = "synthesis"
    tracked: str = "none"
    claims: str = "free"
    refresh: str = "never"
    watch_every: str = "1d"
    keys: dict = field(default_factory=dict)
    required: list = field(default_factory=list)

    def folder_for(self, fm: dict | None = None, **values) -> str:
        """Fill {kind}/{context}/... placeholders from values or front matter."""
        vals = {**(fm or {}), **values}
        try:
            return self.folder.format(**{k: str(v) for k, v in vals.items()})
        except KeyError as e:
            raise KBError(f"type {self.name!r} folder {self.folder!r} needs {e.args[0]!r}") from None

    @property
    def folder_prefix(self) -> str:
        """Folder up to the first placeholder, e.g. 'evidence/' for 'evidence/{context}'."""
        return self.folder.split("{", 1)[0].rstrip("/")


@dataclass
class Profile:
    data: dict
    types: dict  # name -> TypeDef

    @property
    def name(self) -> str:
        return str(self.data.get("name", ""))

    @property
    def access(self) -> str:
        return str(self.data.get("access", "shared"))

    @property
    def tiers(self) -> list:
        return list(self.data.get("tiers") or [])

    @property
    def tags(self) -> list | None:
        """Controlled tag names, or None when the profile doesn't control tags."""
        t = self.data.get("tags")
        return list(t) if isinstance(t, (list, dict)) else None

    @property
    def tag_meanings(self) -> dict:
        t = self.data.get("tags")
        return {str(k): str(v or "") for k, v in t.items()} if isinstance(t, dict) else {str(k): "" for k in t or []}

    @property
    def event_dates(self) -> list:
        return [e.get("date") for e in self.data.get("events") or [] if isinstance(e, dict)]

    def get(self, key, default=None):
        return self.data.get(key, default)

    def type(self, name: str) -> TypeDef:
        if name not in self.types:
            raise KBError(f"unknown type {name!r}; kb.yaml defines: {', '.join(sorted(self.types))}")
        return self.types[name]


def _typedef(name: str, spec: dict) -> TypeDef:
    if not isinstance(spec, dict):
        raise KBError(f"kb.yaml types.{name} must be a mapping")
    if "origin" not in spec:
        raise KBError(f"kb.yaml types.{name}: 'origin' is required (external or internal)")
    for trait, allowed in TRAITS.items():
        if trait in spec and spec[trait] not in allowed:
            raise KBError(f"kb.yaml types.{name}.{trait}: {spec[trait]!r} is not one of {', '.join(allowed)}")
    keys = spec.get("keys") or {}
    for k, ks in keys.items():
        if isinstance(ks, list) and any(isinstance(x, bool) for x in ks):
            raise KBError(f"kb.yaml types.{name}.keys.{k}: YAML read yes/no/true/false as booleans; "
                          f"quote them (\"yes\") or use other words")
        if not (isinstance(ks, list) or ks in SCALAR_SPECS):
            raise KBError(f"kb.yaml types.{name}.keys.{k}: use one of {', '.join(SCALAR_SPECS)} or an enum list")
    for c in ("refresh", "watch_every"):
        if c in spec:
            parse_cadence(spec[c])
    known = set(TRAITS) | {"folder", "keys", "required", "refresh", "watch_every"}
    unknown = set(spec) - known
    if unknown:
        raise KBError(f"kb.yaml types.{name}: unknown setting(s) {', '.join(sorted(unknown))}")
    return TypeDef(name=name, folder=str(spec.get("folder", name)),
                   **{t: spec[t] for t in TRAITS if t in spec},
                   refresh=str(spec.get("refresh", "never")), watch_every=str(spec.get("watch_every", "1d")),
                   keys=dict(keys), required=list(spec.get("required") or []))


def parse_profile(data: dict) -> Profile:
    if not isinstance(data, dict):
        raise KBError("kb.yaml must be a YAML mapping")
    schema = data.get("schema")
    if schema != SCHEMA:
        raise KBError(f"kb.yaml schema is {schema!r}, this kb supports {SCHEMA}; "
                      f"run a matching kb version or migrate the vault")
    if data.get("access", "shared") not in ("shared", "local"):
        raise KBError("kb.yaml access must be 'shared' or 'local'")
    types = data.get("types") or {}
    if not types:
        raise KBError("kb.yaml defines no types; copy them from a starter profile (kb init --profile generic)")
    return Profile(data=data, types={n: _typedef(n, s) for n, s in types.items()})


def load_profile(root) -> Profile:
    path = Path(root) / "kb.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise KBError(f"no kb.yaml in {root}; run `kb init`") from None
    except yaml.YAMLError as e:
        raise KBError(f"kb.yaml is not valid YAML: {str(e).splitlines()[0]}") from None
    return parse_profile(data)


def starter_names() -> list:
    return sorted(p.stem for p in (TEMPLATES / "profiles").glob("*.yaml"))


def starter_text(name: str) -> str:
    path = TEMPLATES / "profiles" / f"{name}.yaml"
    if not path.exists():
        raise KBError(f"no starter profile {name!r}; choose one of {', '.join(starter_names())}")
    return path.read_text(encoding="utf-8")


# --- key specs ----------------------------------------------------------------

def check_value(spec, value) -> str | None:
    """Return an error message if value doesn't match the key spec, else None."""
    if isinstance(spec, list):
        vals = value if isinstance(value, list) else [value]
        bad = [v for v in vals if v not in spec]
        return f"{bad[0]!r} is not one of {', '.join(map(str, spec))}" if bad else None
    if spec == "string":
        return None if isinstance(value, (str, int, float)) else "expected a string"
    if spec == "int":
        return None if isinstance(value, int) and not isinstance(value, bool) else "expected an integer"
    if spec == "bool":
        return None if isinstance(value, bool) else "expected true or false"
    if spec == "date":
        if isinstance(value, (dt.date, dt.datetime)) or parse_time(value) is not None:
            return None
        return "expected an ISO date (YYYY-MM-DD)"
    if spec in ("list", "links"):
        if not isinstance(value, list):
            return "expected a list"
        if spec == "links" and any(not (isinstance(v, str) and v.startswith("[[")) for v in value):
            return "expected a list of quoted wikilinks like \"[[id]]\""
        return None
    if spec == "link":
        return None if isinstance(value, str) and value.startswith("[[") else "expected a quoted wikilink like \"[[id]]\""
    return None
