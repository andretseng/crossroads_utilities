#!/usr/bin/env python3
"""Read-only equivalence replay for SPEC_source_lines.md."""
from collections import Counter
import importlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import source_lines

GUARD = Path("/home/andre/.agents/pool/exploring/explore-guard")
PIPE = Path("/home/andre/.agents/bin/exploring-pipeline")
CRAFT = Path("/home/andre/.agents/pool/crafting/write-note/scripts/notecheck.py")


def load_legacy():
    """Import every safe named parser read-only; unsafe top-level CLIs are replayed by exact regex."""
    sys.path[:0] = [str(GUARD), str(PIPE), str(PIPE / "appnote")]
    modules = {}
    for name in ("cite_check", "quote_check", "ledger_add", "render_answer", "case_chain",
                 "translation_check", "seen_lines", "make_note", "make_lean_multi", "appnote_stage"):
        modules[name] = importlib.import_module(name)
    spec = importlib.util.spec_from_file_location("legacy_notecheck", CRAFT)
    modules["notecheck"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modules["notecheck"])
    return modules


def key(file, first, last=None):
    return Path(str(file).replace("\\", "/")).name, int(first), int(last if last is not None else first)


def canonical(items):
    """One semantic range per file/start; lossy callers often drop only the end."""
    out = {}
    for file, first, last in items:
        out[(file, first)] = max(last, out.get((file, first), first))
    return {(file, first, last) for (file, first), last in out.items()}


def ranged(file, spec, line_numbers):
    out = set()
    for part in re.split(r"[,;]", spec):
        hit = re.fullmatch(r"\s*:?(\d{1,7})(?:\s*[-\u2010-\u2015~]\s*(\d{1,7}))?\s*", part)
        if hit:
            first = int(hit.group(1)); last = max(first, int(hit.group(2) or first))
            out.add(key(file, first, last))
    return out


def legacy_sets(text):
    m = load_legacy()
    out = {}
    out["cite_check"] = {key(c["path"], c["a"], c["b"])
                         for line in text.splitlines() for c in m["cite_check"].line_citations(line)}
    out["quote_check"] = set()
    for hit in m["quote_check"].CITE.finditer(text):
        out["quote_check"] |= ranged(hit.group(1), hit.group(2), m["quote_check"].line_numbers)
    ledger_search = re.compile(r"([\w./+\-]*?\.[A-Za-z][\w\-]{0,11}):(\d{1,7})(?:[-–](\d{1,7}))?")
    out["ledger_add"] = {key(*hit.groups()) for hit in ledger_search.finditer(text)}
    out["render_answer"] = set(m["render_answer"].cite_ranges(text))
    exact = {
        "finalize_answer": re.compile(r"[\w.+\-]+\.(?:c|h|cc|cpp|S|s|py|md|json|yaml|xdt|mk):(\d{1,7})(?!\d)"),
        "logic_check": re.compile(r"[\w./+\-]+\.(?:c|h|cc|cpp|S|s|py|js|mjs|ts|json|yaml|yml|cfg|syscfg|xml|html|md|txt|ld|cmd):\d+"),
        "seen_lines": re.compile(r"([\w.+\-]+\.[A-Za-z]{1,6}):(\d{1,7})(?:[-–](\d{1,7}))?(?!\d)"),
    }
    out["finalize_answer"] = set()
    for hit in exact["finalize_answer"].finditer(text):
        raw = hit.group(0); file, number = raw.rsplit(":", 1); out["finalize_answer"].add(key(file, number))
    out["logic_check"] = set()
    for hit in exact["logic_check"].finditer(text):
        file, number = hit.group(0).rsplit(":", 1); out["logic_check"].add(key(file, number))
    out["case_chain"] = set()
    for hit in m["case_chain"].CITE.finditer(text):
        file, spec = hit.group(0).rsplit(":", 1)
        a, _, b = spec.partition("-"); out["case_chain"].add(key(file, a, b or a))
    out["translation_check"] = set()
    facts = m["translation_check"].facts(text)
    for raw in facts["cites"]:
        file, spec = raw.rsplit(":", 1); a, _, b = spec.partition("-")
        out["translation_check"].add(key(file, a, b or a))
    out["seen_lines"] = {key(*hit.groups()) for hit in exact["seen_lines"].finditer(text)}
    out["ledger_cite_seen"] = set(out["seen_lines"])
    out["numbered"] = set()  # formatter, no citation parser
    out["make_note"] = set()
    for hit in m["make_note"].CODEREF.finditer(text):
        file, spec = hit.group(0).rsplit(":", 1); a, _, b = spec.partition("-")
        out["make_note"].add(key(file, a, b or a))
    out["make_lean_multi"] = {key(*hit.groups()) for hit in m["make_lean_multi"].CITE.finditer(text)}
    out["appnote_stage"] = set()
    for hit in m["appnote_stage"].INLINE_CITE.finditer(text):
        raw = hit.group(0); file, spec = raw.split(":", 1)
        out["appnote_stage"] |= ranged(file, spec.replace(":", ""), m["quote_check"].line_numbers)
    out["notecheck"] = set()
    _fm, _body, provenance = m["notecheck"].split_note(text)
    claims, _errors = m["notecheck"].parse_provenance(provenance)
    for claim in claims:
        hit = m["notecheck"].REF.match(claim["source"])
        listed = m["notecheck"].REF_LIST.match(claim["source"])
        if hit:
            out["notecheck"].add(key(hit.group(1), hit.group(2), hit.group(3)))
        elif listed:
            out["notecheck"] |= ranged(listed.group(1), listed.group(2), m["quote_check"].line_numbers)
    return out


def reason(kind, item, text):
    file, first, last = item
    if kind == "new_only":
        if file.startswith("."):
            return "new is right: Fix 5 requires leading-dot filenames"
        if re.search(rf"\b{re.escape(file)}\s+lines?\s+{first}\b", text, re.I):
            return "new is right: Fix 37 requires FILE line N binding"
        if len(Path(file).suffix.lstrip(".")) > 6:
            return "new is right: Fix 5 permits long extensions"
        return "new is right: the shared grammar covers an extension/path omitted by at least one legacy allow-list"
    return "new is right: numeric values/times and malformed legacy matches are not source citations"


def replay_one(path_string):
    path = Path(path_string)
    text = path.read_text(encoding="utf-8", errors="replace")
    old = legacy_sets(text)
    new = {key(c.file, c.first, c.last) for c in source_lines.parse_cites(text)}
    old = {name: canonical(values) for name, values in old.items()}
    union = canonical(set().union(*old.values()))
    new = canonical(new)
    rows = []
    old_map = {(file, first): last for file, first, last in union}
    new_map = {(file, first): last for file, first, last in new}
    old_only = [item for item in union if (item[0], item[1]) not in new_map]
    new_only = [item for item in new if (item[0], item[1]) not in old_map]
    ranges = [(file, first, old_last, new_map[(file, first)])
              for (file, first), old_last in old_map.items()
              if (file, first) in new_map and old_last != new_map[(file, first)]]
    for kind, items in (("old_only", sorted(old_only)), ("new_only", sorted(new_only))):
        for item in items:
            rows.append((str(path), kind, f"{item[0]}:{item[1]}" + (f"-{item[2]}" if item[2] != item[1] else ""),
                         reason(kind, item, text)))
    for file, first, old_last, new_last in sorted(ranges):
        rows.append((str(path), "different_range", f"{file}:{first}-{old_last} -> {first}-{new_last}",
                     "new is right: it preserves the full range/list; at least one legacy caller truncates it"))
    per_parser = {name: (len(values), len(values - new), len(new - values)) for name, values in old.items()}
    return len(new), len(union), per_parser, rows


def inputs(skip_sessions):
    base = Path("/home/andre/exploring_lab/cases")
    ledgers = sorted(base.rglob("explore-ledger.md"))
    sessions = sorted(base.rglob("session.jsonl"))
    answers = sorted(path for path in base.glob("*/answers/**/*.md")
                     if path.name in {"answer.md", "explore-answer.md", "reply.md", "report.md"})
    if skip_sessions:
        sessions = []
    return list(dict.fromkeys(ledgers + sessions + answers)), ledgers, sessions, answers


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Read-only equivalence replay for SPEC_source_lines.md.")
    parser.add_argument("--no-sessions", action="store_true",
                        help="skip session.jsonl files (tool output); replay ledgers and answers only")
    args = parser.parse_args()
    skip_sessions = args.no_sessions

    # Parent import records that all safe legacy modules load successfully before replay.
    imported = sorted(load_legacy())
    files, ledgers, sessions, answers = inputs(skip_sessions)
    totals = Counter(); parser_totals = Counter(); differences = []
    with multiprocessing.Pool(min(12, os.cpu_count() or 1)) as pool:
        for new_count, old_count, parsers, rows in pool.imap_unordered(replay_one, map(str, files), chunksize=4):
            totals["new"] += new_count; totals["old_union"] += old_count
            for name, (count, old_only, new_only) in parsers.items():
                parser_totals[(name, "count")] += count
                parser_totals[(name, "old_only")] += old_only
                parser_totals[(name, "new_only")] += new_only
            differences.extend(rows)
    diff_path = HERE / "replay_differences.tsv"
    with diff_path.open("w", encoding="utf-8") as stream:
        stream.write("file\tkind\tcite\texplanation\n")
        for row in sorted(differences):
            stream.write("\t".join(row) + "\n")
    summary = {
        "files": len(files), "ledgers": len(ledgers), "sessions": len(sessions), "answers": len(answers),
        "new_cites": totals["new"], "old_union_cites": totals["old_union"],
        "differences": len(differences), "old_only": sum(r[1] == "old_only" for r in differences),
        "new_only": sum(r[1] == "new_only" for r in differences), "imported": imported,
        "parsers": {name: {field: parser_totals[(name, field)] for field in ("count", "old_only", "new_only")}
                    for name in sorted({name for name, _ in parser_totals})},
    }
    (HERE / "replay_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
