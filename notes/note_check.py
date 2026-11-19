#!/usr/bin/env python3
"""note_check.py — evidence checks for a note (D11, item 8).

Findings (each {"kind", "line", "detail"}), exit 1 when any error:
- frontmatter: `title`, `date` (YYYY-MM-DD), `tags` (list) present -> kind "frontmatter";
- every `[[target]]` / `[[target|alias]]` / `[[target#heading]]` resolves to `<target>.md`
  somewhere under VAULT -> "wikilink";
- every cite found by source_lines.parse_cites resolves under the roots and its line exists ->
  "cite"; when the cite is followed by an inline-code quote, the quote must be on that line ->
  "quote";
- mainland words outside code (tw_wording) -> "wording" (warning, not error).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import source_lines  # noqa: E402  shared cite parser — never invent another
import tw_wording  # noqa: E402


WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
FRONTMATTER = re.compile(r"^---\r?\n(.*?)\r?\n---", re.S)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _frontmatter_findings(text: str) -> list[dict]:
    """Findings for missing/invalid YAML frontmatter. Returns [] when frontmatter is absent or ok."""
    m = FRONTMATTER.match(text)
    if not m:
        return []
    body = m.group(1)
    findings: list[dict] = []
    title = re.search(r"^title:\s*(.+)$", body, re.M)
    date = re.search(r"^date:\s*(.+)$", body, re.M)
    tags = re.search(r"^tags:\s*(.+)$", body, re.M)
    if not title:
        findings.append({"kind": "frontmatter", "line": 1,
                         "detail": "missing title"})
    if not date or not DATE_RE.match((date.group(1) or "").strip()):
        findings.append({"kind": "frontmatter", "line": 1,
                         "detail": "date not YYYY-MM-DD"})
    if not tags:
        findings.append({"kind": "frontmatter", "line": 1,
                         "detail": "missing tags"})
    return findings


def _wikilink_findings(text: str, vault: Path) -> list[dict]:
    """Every [[target]] that does not resolve to <target>.md under VAULT."""
    findings: list[dict] = []
    # Index every .md under VAULT by its base name (e.g. "Other note.md").
    md_files = {p.name: p for p in vault.rglob("*.md")}
    for line_no, line in enumerate(text.split("\n"), 1):
        for match in WIKILINK.finditer(line):
            target = match.group(1).split("|")[0].split("#")[0].strip()
            if not target:
                continue
            if f"{target}.md" not in md_files:
                findings.append({"kind": "wikilink", "line": line_no,
                                 "detail": f"unresolved link [[{match.group(1)}])"})
    return findings


def _inline_quotes(text: str) -> list[tuple[int, str]]:
    """Return (line_no, quote_text) for every inline-code span that is NOT itself a cite."""
    quotes: list[tuple[int, str]] = []
    fence = False
    for line_no, raw in enumerate(text.split("\n"), 1):
        if raw.strip().startswith("```"):
            fence = not fence
        if fence:
            continue
        for m in re.finditer(r"`([^`]+)`", raw):
            span = m.group(1)
            # A cite like `io.c:2` is not a quote; skip it.
            if source_lines.parse_cites(span):
                continue
            quotes.append((line_no, span))
    return quotes


def _cite_findings(text: str, roots: list[str]) -> list[dict]:
    """Cite + quote findings. A cite followed by an inline-code quote must have that quote match
    the cited source line."""
    findings: list[dict] = []
    quotes = _inline_quotes(text)  # (line_no, quote_text) in order
    for line_no, line in enumerate(text.split("\n"), 1):
        cites = source_lines.parse_cites(line)
        if not cites:
            continue
        # Inline quotes on this same line, in order.
        line_quotes = [q for (ln, q) in quotes if ln == line_no]
        for ci, cite in enumerate(cites):
            quote = line_quotes[ci] if ci < len(line_quotes) else None
            result = source_lines.verify(cite, roots, quote=quote)
            if not result.exists:
                findings.append({"kind": "cite", "line": line_no,
                                 "detail": result.message})
            elif not result.line_in_range:
                findings.append({"kind": "cite", "line": line_no,
                                 "detail": f"{cite.file}:{cite.first}-{cite.last} outside file"})
            elif quote is not None and result.quote_matches is False:
                findings.append({"kind": "quote", "line": line_no,
                                 "detail": f"quote for {cite.file}:{cite.first} does not match"})
    return findings


def check_note(note_path: Path, vault: Path, roots: list[str]) -> list[dict]:
    text = note_path.read_text(encoding="utf-8", errors="replace")
    findings: list[dict] = []
    findings.extend(_frontmatter_findings(text))
    findings.extend(_wikilink_findings(text, vault))
    findings.extend(_cite_findings(text, roots))
    # Wording is a warning only; still reported but never causes a non-zero exit.
    for line_no, line in enumerate(text.split("\n"), 1):
        if line.strip().startswith("```"):
            continue
        for word, replacement in tw_wording.TW_WORDS:
            if word in line:
                findings.append({"kind": "wording", "line": line_no,
                                 "detail": f"{word} -> {replacement}"})
    return findings


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or "--json" not in argv:
        print("usage: note_check.py NOTE --vault VAULT [--root DIR ...] [--json]", file=sys.stderr)
        return 2
    note_path = None
    vault = None
    roots: list[str] = []
    as_json = "--json" in argv
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--vault":
            vault = argv[i + 1]; i += 2; continue
        if tok == "--root":
            roots.append(argv[i + 1]); i += 2; continue
        if not tok.startswith("-"):
            note_path = tok; i += 1; continue
        i += 1
    if not note_path or not vault:
        print("usage: note_check.py NOTE --vault VAULT [--root DIR ...] [--json]", file=sys.stderr)
        return 2
    note = Path(note_path)
    vault_path = Path(vault)
    findings = check_note(note, vault_path, roots)
    if as_json:
        print(json.dumps(findings, indent=1, ensure_ascii=False))
    else:
        for f in findings:
            print(f"{f['kind']}:{f['line']} {f['detail']}")
    has_error = any(f["kind"] != "wording" for f in findings)
    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
