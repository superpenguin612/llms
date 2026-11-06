#!/usr/bin/env python3
"""Compare the idle mod's compaction summaries with the engine's.

  run.py --out DIR TRANSCRIPT.jsonl...

Each transcript is compacted twice in forks of the same session, read back, probed,
answered blind from the two compactions, and graded. DIR must be outside this repo.
The eval calls a model and is not run by the test suite.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KINDS = ("decision", "user_words", "running", "next_step", "files", "other")
MODES = ("engine", "mod")
COMPACT_TIMEOUT = 30 * 60
MODEL_TIMEOUT = 30 * 60
TOOL_INPUT_CHARS = 200
TOOL_RESULT_CHARS = 500
MOD_PREFIX = "This session is being continued from a previous conversation. The summary below"
ENGINE_PREFIX = "This session is being continued from a previous conversation that ran out of context"
COMPACT_SETTINGS = '{"pluginConfigs":{"idle":{"options":{"compaction":"%s"}}}}'


class EvalError(Exception):
    """A failure that ends the current transcript but not the whole run."""


# --- small IO helpers ----------------------------------------------------------------

def load_jsonl(path):
    records = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_prompt(name):
    return (HERE / name).read_text(encoding="utf-8")


# --- repo/out-dir guard --------------------------------------------------------------

def repo_root(start=None):
    start = Path(start or HERE)
    try:
        r = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return Path(__file__).resolve().parents[5]


def ensure_outside_repo(out, root=None):
    out = Path(out).expanduser().resolve()
    root = Path(root).resolve() if root is not None else repo_root()
    if out == root or root in out.parents:
        raise EvalError(
            f"--out {out} is inside the git work tree {root}; choose a directory outside it "
            "(transcripts and results are private)"
        )
    return out


# --- transcript reading --------------------------------------------------------------

def transcript_cwd(path):
    for record in load_jsonl(path):
        cwd = record.get("cwd")
        if isinstance(cwd, str) and cwd:
            return Path(cwd)
    raise EvalError(f"{path}: no record has a cwd field")


def _text_of(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def summary_text(record):
    message = record.get("message") or {}
    return _text_of(message.get("content"))


def summary_mode(summary):
    if summary.startswith(ENGINE_PREFIX):
        return "engine"
    if summary.startswith(MOD_PREFIX):
        return "mod"
    return "unknown"


def is_mode_fallback(summary):
    return summary_mode(summary) == "engine"


def read_compaction(path):
    """(summary, kept records, boundary) from a compacted Claude transcript."""
    records = load_jsonl(path)
    boundary_index = None
    for index, record in enumerate(records):
        if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
            boundary_index = index
    if boundary_index is None:
        raise EvalError(f"{path}: no system/compact_boundary record")
    boundary = records[boundary_index]

    # The engine flags its summary (isCompactSummary) and lists what it kept; the mod's summary is
    # a plain user record after the boundary, followed by the kept records, up to the /compact
    # command's own records.
    after = records[boundary_index + 1:]
    summary_index = next((i for i, r in enumerate(after) if r.get("isCompactSummary") is True), None)
    if summary_index is None:
        summary_index = next((i for i, r in enumerate(after) if r.get("type") == "user"), None)
    if summary_index is None:
        raise EvalError(f"{path}: no summary record after the boundary")
    summary_record = after[summary_index]

    metadata = boundary.get("compactMetadata") or {}
    preserved = metadata.get("preservedMessages") or {}
    uuids = {str(u) for u in (preserved.get("uuids") or [])}
    if uuids:
        kept = [r for r in records if r.get("uuid") in uuids]
    else:
        kept = []
        for r in after[summary_index + 1:]:
            if "<local-command-caveat>" in summary_text(r) or "<command-name>/compact" in summary_text(r):
                break
            if r.get("type") in ("user", "assistant") and not r.get("isMeta"):
                kept.append(r)
    return summary_text(summary_record), kept, boundary


def _tool_input(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text[:TOOL_INPUT_CHARS]


def render_record(record):
    message = record.get("message") or {}
    role = record.get("type") or message.get("role") or "?"
    content = message.get("content")
    pieces = []
    if isinstance(content, str):
        pieces.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and isinstance(block.get("text"), str):
                pieces.append(block["text"])
            elif kind == "tool_use":
                pieces.append(f"[tool {block.get('name', '?')}: {_tool_input(block.get('input'))}]")
            elif kind == "tool_result":
                pieces.append(f"[tool result: {_text_of(block.get('content'))[:TOOL_RESULT_CHARS]}]")
    if not pieces:
        return ""
    return f"[{role}]\n" + "\n".join(pieces)


def render_records(records):
    return "\n\n".join(text for text in (render_record(r) for r in records) if text)


def render_compaction(summary, kept):
    return (f"## Summary\n{summary}\n\n## Kept messages\n{render_records(kept)}").rstrip() + "\n"


# --- model and compaction calls ------------------------------------------------------

def parse_stream(text):
    """(new session id, total cost) from `claude --output-format stream-json`."""
    result_id = init_id = None
    cost = None
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        session_id = event.get("session_id")
        if event.get("type") == "result":
            result_id = session_id or result_id
            value = event.get("total_cost_usd")
            if isinstance(value, (int, float)):
                cost = float(value)
        elif event.get("type") == "init" or (
            event.get("type") == "system" and event.get("subtype") == "init"
        ):
            init_id = session_id or init_id
    return result_id or init_id, cost


def run_compaction(transcript, mode):
    cwd = transcript_cwd(transcript)
    prompt = {"type": "user", "message": {"role": "user", "content": "Reply with just: ok"}}
    compact = {"type": "user", "message": {"role": "user", "content": "/compact"}}
    command = [
        "claude", "-p", "--resume", transcript.stem, "--fork-session", "--model", "sonnet",
        "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
        "--settings", COMPACT_SETTINGS % mode,
    ]
    try:
        r = subprocess.run(command, cwd=str(cwd), input=json.dumps(prompt) + "\n" + json.dumps(compact) + "\n",
                           capture_output=True, text=True, timeout=COMPACT_TIMEOUT)
    except FileNotFoundError as exc:
        raise EvalError("claude was not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise EvalError(f"{transcript.name}: {mode} compaction timed out after 30 min") from exc
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-5:]
        raise EvalError(f"{transcript.name}: {mode} compaction failed (exit {r.returncode}): {' / '.join(tail)}")
    new_id, cost = parse_stream(r.stdout)
    if not new_id:
        raise EvalError(f"{transcript.name}: {mode} compaction stream had no session id")
    return new_id, cost


def compacted_path(transcript, new_id):
    return Path.home() / ".claude" / "projects" / transcript.parent.name / f"{new_id}.jsonl"


def ask_model(prompt, body):
    payload = prompt.rstrip() + "\n\n" + body.rstrip() + "\n"
    try:
        r = subprocess.run(["claude", "-p", "--model", "sonnet", "--tools", ""], input=payload,
                           capture_output=True, text=True, timeout=MODEL_TIMEOUT)
    except FileNotFoundError as exc:
        raise EvalError("claude was not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise EvalError("claude -p timed out after 30 min") from exc
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-5:]
        raise EvalError(f"claude -p failed (exit {r.returncode}): {' / '.join(tail)}")
    if not r.stdout.strip():
        raise EvalError("claude -p returned no output")
    return r.stdout


# --- JSON replies --------------------------------------------------------------------

def parse_json(text, expected):
    opener, closer = ("[", "]") if expected == "array" else ("{", "}")
    start, end = text.find(opener), text.rfind(closer)
    if start < 0 or end <= start:
        raise ValueError(f"no {opener}...{closer} block")
    return json.loads(text[start:end + 1])


def call_json(prompt, body, expected, normalise=None):
    error = None
    for _ in range(2):
        reply = ask_model(prompt, body)
        try:
            value = parse_json(reply, expected)
            return normalise(value) if normalise else value
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            error = exc
    raise EvalError(f"could not parse model reply as {expected} JSON after two tries: {error}")


def normalise_probes(value):
    if isinstance(value, dict):
        value = value.get("probes")
    if not isinstance(value, list) or not value:
        raise ValueError("probes are not a non-empty array")
    probes = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("probe is not an object")
        q = item.get("q") or item.get("question")
        answer = item.get("answer") or item.get("a")
        kind = item.get("kind") or "other"
        if not isinstance(q, str) or not q.strip() or not isinstance(answer, str) or not answer.strip():
            raise ValueError("probe has no question or answer")
        probes.append({"q": q.strip(), "answer": answer.strip(),
                       "kind": kind if kind in KINDS else "other"})
    return probes


def normalise_answers(value, count):
    if isinstance(value, dict):
        value = value.get("answers", value)
    if isinstance(value, dict):
        items = [{"i": key, "answer": text} for key, text in value.items()]
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError("answers are not an array or object")
    answers = [None] * count
    for position, item in enumerate(items):
        if isinstance(item, dict):
            index = item.get("i", item.get("index", position + 1))
            text = item.get("answer", item.get("a", ""))
        else:
            index, text = position + 1, item
        try:
            number = int(index)
        except (TypeError, ValueError):
            number = position + 1
        if 1 <= number <= count and isinstance(text, str) and text.strip():
            answers[number - 1] = {"i": number, "answer": text.strip()}
    if any(answer is None for answer in answers):
        raise ValueError("one or more probes have no answer")
    return answers


def normalise_grades(value, count):
    if not isinstance(value, dict):
        raise ValueError("grades are not an object")
    scores = {}
    source = value.get("scores") if isinstance(value.get("scores"), dict) else value
    for label in ("A", "B"):
        submitted = source.get(label)
        if submitted is None:
            raise ValueError(f"grades have no {label} set")
        row = [None] * count
        if isinstance(submitted, list):
            items = list(enumerate(submitted, 1))
            for position, item in items:
                if isinstance(item, dict):
                    index = item.get("i", item.get("index", position))
                    score = item.get("score")
                else:
                    index, score = position, item
                try:
                    number, grade = int(index), int(score)
                except (TypeError, ValueError):
                    raise ValueError("grade is not an integer")
                if not 1 <= number <= count or grade not in (0, 1, 2):
                    raise ValueError("grade is out of range")
                row[number - 1] = grade
        elif isinstance(submitted, dict):
            for key, score in submitted.items():
                try:
                    number, grade = int(key), int(score)
                except (TypeError, ValueError):
                    raise ValueError("grade is not an integer")
                if not 1 <= number <= count or grade not in (0, 1, 2):
                    raise ValueError("grade is out of range")
                row[number - 1] = grade
        else:
            raise ValueError(f"grades for {label} are not an array or object")
        if any(grade is None for grade in row):
            raise ValueError(f"grades for {label} are incomplete")
        scores[label] = row
    return scores


# --- one transcript ------------------------------------------------------------------

def evaluate(transcript, out):
    transcript = transcript.expanduser().resolve()
    if not transcript.is_file():
        raise EvalError(f"{transcript}: no such transcript")
    session_id = transcript.stem
    session_dir = out / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "session_id": session_id,
        "transcript": str(transcript),
        "status": "error",
        "mode_fallback": False,
        "session_ids": {},
        "cost_usd": {},
        "summary_chars": {},
    }

    compactions = {}
    for mode in MODES:
        new_id, cost = run_compaction(transcript, mode)
        new_path = compacted_path(transcript, new_id)
        if not new_path.is_file():
            raise EvalError(f"{transcript.name}: {mode} compaction wrote no transcript at {new_path}")
        summary, kept, _ = read_compaction(new_path)
        (session_dir / f"{mode}.md").write_text(render_compaction(summary, kept), encoding="utf-8")
        entry["session_ids"][mode] = new_id
        entry["cost_usd"][mode] = cost
        entry["summary_chars"][mode] = len(summary)
        compactions[mode] = {"text": render_compaction(summary, kept), "framing": summary_mode(summary)}
        if mode == "mod" and summary_mode(summary) != "mod":
            entry["status"] = "mode_fallback"
            entry["mode_fallback"] = True
            entry["reason"] = (
                "mod used the engine's framing" if summary_mode(summary) == "engine"
                else "mod summary has no recognisable framing"
            )
            return entry

    probes = call_json(read_prompt("probes.md"), render_records(load_jsonl(transcript)),
                       "array", normalise_probes)
    write_json(session_dir / "probes.json", probes)

    answers = {}
    for mode in MODES:
        body = ("## Probes\n" + json.dumps(probes, ensure_ascii=False, indent=2) +
                "\n\n## Context\n" + compactions[mode]["text"])
        answers[mode] = call_json(
            read_prompt("answer.md"), body, "array",
            lambda value, n=len(probes): normalise_answers(value, n),
        )
        write_json(session_dir / f"answers-{mode}.json", answers[mode])

    labels = list(MODES)
    random.Random(session_id).shuffle(labels)
    label_map = {"A": labels[0], "B": labels[1]}
    grade_body = ("## Probes\n" + json.dumps(probes, ensure_ascii=False, indent=2) +
                  "\n\n## Answers\n" +
                  json.dumps({label: answers[mode] for label, mode in label_map.items()},
                             ensure_ascii=False, indent=2))
    grades = call_json(
        read_prompt("grade.md"), grade_body, "object",
        lambda value, n=len(probes): normalise_grades(value, n),
    )
    write_json(session_dir / "grades.json", {"labels": label_map, "scores": grades})

    by_mode = {}
    for mode in MODES:
        label = next(label for label, actual in label_map.items() if actual == mode)
        scores = grades[label]
        by_kind = {kind: 0 for kind in KINDS}
        for probe, score in zip(probes, scores):
            by_kind[probe["kind"]] += score
        by_mode[mode] = {"total": sum(scores), "by_kind": by_kind}

    below = []
    for index, probe in enumerate(probes):
        a_score, b_score = grades["A"][index], grades["B"][index]
        engine_score, mod_score = (
            (a_score, b_score) if label_map["A"] == "engine" else (b_score, a_score)
        )
        if mod_score < engine_score:
            below.append({
                "q": probe["q"],
                "kind": probe["kind"],
                "engine": answers["engine"][index]["answer"],
                "mod": answers["mod"][index]["answer"],
                "engine_score": engine_score,
                "mod_score": mod_score,
            })

    entry.update({
        "status": "scored",
        "labels": label_map,
        "scores": by_mode,
        "probes_below": below,
        "probe_count": len(probes),
    })
    return entry


def overall_pass(entries):
    # Every transcript must be scored: an error or a mod fallback is not a pass.
    if not entries or any(entry.get("status") != "scored" for entry in entries):
        return False
    return all(
        entry["scores"]["mod"]["total"] >= entry["scores"]["engine"]["total"]
        for entry in entries if entry.get("status") == "scored"
    )


# --- report --------------------------------------------------------------------------

def money(value):
    return "n/a" if value is None else f"${value:.4f}"


def report(entries):
    lines = ["# idle compaction eval", ""]
    lines.append(f"Overall: **{'PASS' if overall_pass(entries) else 'FAIL'}**")
    lines.append("")
    for entry in entries:
        lines.append(f"## {entry['session_id']}")
        lines.append("")
        lines.append(f"- transcript: `{entry['transcript']}`")
        lines.append(f"- status: {entry['status']}")
        if entry.get("mode_fallback"):
            lines.append(f"- mode fallback: yes ({entry.get('reason', 'mod used the engine framing')})")
        if entry.get("error"):
            lines.append(f"- error: {entry['error']}")
        if entry["summary_chars"]:
            lines.append(
                f"- summary lengths: engine {entry['summary_chars'].get('engine', 0)} / "
                f"mod {entry['summary_chars'].get('mod', 0)} characters"
            )
        if entry["cost_usd"]:
            lines.append(
                f"- costs: engine {money(entry['cost_usd'].get('engine'))} / "
                f"mod {money(entry['cost_usd'].get('mod'))}"
            )
        if entry["status"] == "scored":
            engine, mod = entry["scores"]["engine"], entry["scores"]["mod"]
            lines.append(f"- totals: engine {engine['total']} / mod {mod['total']}")
            lines.append(f"- labels: A={entry['labels']['A']}, B={entry['labels']['B']}")
            lines.append("")
            lines.append("| kind | engine | mod |")
            lines.append("| --- | ---: | ---: |")
            for kind in KINDS:
                lines.append(f"| {kind} | {engine['by_kind'][kind]} | {mod['by_kind'][kind]} |")
            lines.append("")
            lines.append("Probes where mod scored below engine:")
            lines.append("")
            if not entry["probes_below"]:
                lines.append("- none")
            for item in entry["probes_below"]:
                lines.append(f"- [{item['kind']}] {item['q']}")
                lines.append(f"  - engine ({item['engine_score']}): {item['engine']}")
                lines.append(f"  - mod ({item['mod_score']}): {item['mod']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def terminal_line(entry):
    if entry["status"] == "scored":
        engine, mod = entry["scores"]["engine"]["total"], entry["scores"]["mod"]["total"]
        verdict = "pass" if mod >= engine else "fail"
        return f"{entry['session_id']}: {verdict} (engine {engine}, mod {mod})"
    if entry["status"] == "mode_fallback":
        return f"{entry['session_id']}: not scored (mod used the engine framing)"
    return f"{entry['session_id']}: error ({entry.get('error', 'unknown')})"


# --- main ----------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare idle mod compaction with engine compaction on Claude transcripts. "
                    "Costs money and needs a directory outside this repo for private results."
    )
    parser.add_argument("--out", required=True, type=Path,
                        help="output directory (must be outside this git work tree)")
    parser.add_argument("transcripts", nargs="+", type=Path, help="Claude Code .jsonl transcripts")
    args = parser.parse_args(argv)

    try:
        out = ensure_outside_repo(args.out)
    except EvalError as exc:
        parser.error(str(exc))
    out.mkdir(parents=True, exist_ok=True)

    entries = []
    for transcript in args.transcripts:
        try:
            entry = evaluate(transcript, out)
        except (EvalError, OSError) as exc:
            entry = {
                "session_id": transcript.stem,
                "transcript": str(transcript.expanduser().resolve()),
                "status": "error",
                "mode_fallback": False,
                "session_ids": {},
                "cost_usd": {},
                "summary_chars": {},
                "error": str(exc),
            }
        entries.append(entry)
        print(terminal_line(entry), flush=True)

    results = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "transcripts": entries,
        "pass": overall_pass(entries),
    }
    write_json(out / "results.json", results)
    (out / "report.md").write_text(report(entries), encoding="utf-8")
    print(f"Overall: {'PASS' if results['pass'] else 'FAIL'}")
    return 0 if results["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
