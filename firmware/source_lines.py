#!/usr/bin/env python3
"""Display, parse, resolve, verify, and fill source-line citations.

The citation grammar consolidates the exploring/crafting callers named by
spec_source_lines.md.  In particular:

* a leading-dot filename and extensions longer than six characters are files
  (Fix 5: ``.cproject:428``; ledger_add and make_note);
* a hyphen, en dash, or tilde joins a range and a comma starts another range
  for the same file (Fix 22; quote_check and render_answer);
* ``FILE line N`` binds N to FILE, rather than to an earlier C filename
  (Fix 37);
* paths, backticks, parentheses, and Markdown table cells do not alter a cite
  (cite_check, quote_check, notecheck);
* numeric values and times are not filenames, so ``0x1000:2`` and ``10:30``
  are excluded (Fix 22 replay false positives).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
from typing import Iterable


SKIP_DIRS = {".git", "__pycache__", "SDKDocs", "node_modules"}
_FILE_INDEX: dict[Path, tuple[Path, ...]] = {}

# A normal filename has a dot/extension.  The two extensionless forms are
# Makefile (Fix 37) and quote_check's lower-case logical names (e.g. syscfg:4).
_COMPONENT = r"[A-Za-z0-9_+@%.-]+"
_DOTTED = r"(?:\.[A-Za-z][\w-]{0,31}|[A-Za-z0-9_+@%-][A-Za-z0-9_+@%.-]*\.[A-Za-z][\w-]{0,31})"
_BARE = r"(?:Makefile|[a-z][a-z0-9_-]{1,30})"
_PREFIX = rf"(?:(?:~|[A-Za-z]:)?/(?:{_COMPONENT}/)*|(?:\.\.?/)?(?:{_COMPONENT}/)*)"
_FILE = rf"(?:{_PREFIX})?(?:{_DOTTED}|{_BARE})"
_LINE_FILE = rf"(?:{_PREFIX})?(?:{_DOTTED}|Makefile)"
# A path whose final segment contains a space, parenthesis, or non-ASCII (Fix: real knowledge-base
# files live at paths like `.../CPU 模組分析 (SLAU846E).md:74`).  Each `/`-segment may not start with
# a space, so prose like `A / B / file.c:5` (space-slash-space) never reads as a path; COLON_CITE
# still handles the common no-space case.
_PATHSEG = r"[^/\s][^/:\r\n]*"
_PATH = rf"(?:[A-Za-z]:)?/{_PATHSEG}(?:/{_PATHSEG})+"


def _path_ok(path: str) -> bool:
    """Reject a `_PATH` match that is really prose: a space followed by a lowercase ASCII letter
    (e.g. `/2 to SemiMCLK (config.h`) is not a path.  A space before `(` or a non-ASCII character
    (e.g. `CPU 模組分析 (SLAU846E).md`) is a genuine spaced filename and is kept."""
    for i, ch in enumerate(path):
        if ch == " " and i + 1 < len(path):
            nxt = path[i + 1]
            if nxt.isascii() and nxt.islower():
                return False
    return True
_SPEC = r"\d{1,7}(?:\s*[-–~]\s*\d{1,7})?(?:\s*[,;]\s*:?[ ]*\d{1,7}(?:\s*[-–~]\s*\d{1,7})?)*"

# A trailing `:` after the line is a Markdown table column separator, not part of the cite, so the
# lookahead rejects only a following word char or digit (Fix: `...md:74: | ...` in answer tables).
# The lookbehind keeps `.` `:` and word chars (so `a.b:5` and `x:5` are not re-matched mid-token)
# but drops `/`: in `main.c:64/io.c:188` the `/` joins two cites, so `io.c` must be matchable.
COLON_CITE = re.compile(
    rf"(?<![\w.:])(?P<file>{_FILE}):(?P<spec>{_SPEC})(?![\w\d])"
)
PATH_CITE = re.compile(
    rf"(?<![\w.:/])(?P<file>{_PATH}):(?P<spec>{_SPEC})(?![\w\d])"
)
LINE_CITE = re.compile(
    rf"(?<![\w.:])(?P<file>{_LINE_FILE})\s+lines?\s+(?P<spec>{_SPEC})(?![\w\d])",
    re.IGNORECASE,
)
RANGE_PART = re.compile(r"\s*:?(\d{1,7})(?:\s*[-–~]\s*(\d{1,7}))?\s*")
# An elided cite: ``:N`` or ``:N-M`` with no file.  The ``:`` is part of the match so the lookbehind
# sees the character before the colon (rejecting ``file:5`` and ``10:30``).  The file is not known
# here; ``parse_cites`` inherits it from the nearest previous cite on the same line (see below).
ELIDED_CITE = re.compile(
    rf"(?<![\w.:]):(?P<spec>\d{{1,7}}(?:\s*[-–~]\s*\d{{1,7}})?)(?![\w\d])"
)


def _final_segment(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _is_syscfg_member(filename: str) -> bool:
    """A dotted SysConfig member chain (e.g. `adc1.adc_INT1.priority`,
    `input_xbar1.xbarOutput`) is not a source file.  A real filename uses `.` only for its
    extension, so reject a final segment with two or more dots, or whose extension is mixed-case
    (member names are camelCase like `xbarOutput`; real extensions are all-lowercase or all-uppercase
    acronyms like `md`/`ASM`).  `.cproject` and `AppNote_..._LP.md` (mixed case only in the stem) are
    kept."""
    seg = _final_segment(filename)
    if seg.count(".") >= 2:
        return True
    if "." in seg:
        ext = seg.rsplit(".", 1)[-1]
        letters = [ch for ch in ext if ch.isalpha()]
        if letters and any(ch.isupper() for ch in letters) and any(ch.islower() for ch in letters):
            return True
    return False


def _is_bare(filename: str) -> bool:
    """A bare, extensionless logical name (quote_check's `syscfg:4`, ISR names like `halltim_isr`)."""
    seg = _final_segment(filename)
    return "." not in seg


@dataclass(frozen=True)
class Cite:
    file: str
    first: int
    last: int
    raw: str
    span: tuple[int, int]
    bare: bool = False
    inherited: bool = False


@dataclass(frozen=True)
class SourceLine:
    number: int
    text: str


@dataclass(frozen=True)
class FillResult:
    file: Path
    lines: tuple[SourceLine, ...]
    matches: int
    status: str


@dataclass(frozen=True)
class VerifyResult:
    cite: Cite
    resolved: Path | tuple[Path, ...] | None
    exists: bool
    line_in_range: bool
    quote_matches: bool | None
    message: str


def _parts(match: re.Match[str]) -> list[Cite]:
    """Expand comma/semicolon line lists without treating an end as a value."""
    raw = match.group(0)
    out = []
    for item in re.split(r"[,;]", match.group("spec")):
        part = RANGE_PART.fullmatch(item)
        if not part:
            continue
        first = int(part.group(1))
        last = max(first, int(part.group(2) or first))
        file = match.group("file").strip()
        # A leading `/` is a cite separator (``main.c:64/io.c:188`` -> `io.c`), but an absolute path
        # (``/opt/sdk/b.hpp``) keeps it.  Strip only when the match is a bare basename.
        if "/" not in file[1:]:
            file = file.lstrip("/")
        out.append(Cite(file, first, last, raw, match.span(), bare=_is_bare(file)))
    return out


def parse_cites(text: str) -> list[Cite]:
    """Return citations in source order using the shared grammar.

    A comma/semicolon list yields one Cite per listed range because it can be
    discontinuous (``f.c:12,20-22`` is not the continuous range 12-22).

    An elided ``:N``/``:N-M`` (no file) inherits the file of the nearest previous
    cite on the SAME line; elision never crosses a newline, and a lone ``:N`` with
    no previous cite on its line is nothing.
    """
    elided_matches = list(ELIDED_CITE.finditer(text))
    elided_ids = {id(m) for m in elided_matches}
    matches = (list(COLON_CITE.finditer(text)) + list(PATH_CITE.finditer(text))
               + list(LINE_CITE.finditer(text)) + elided_matches)
    matches = [m for m in matches
               if id(m) in elided_ids or (_path_ok(m.group("file")) and m.groupdict().get("file") is not None)]
    matches.sort(key=lambda item: (item.start(), item.end()))
    accepted: list[re.Match[str]] = []
    for match in matches:
        if accepted and match.start() < accepted[-1].end():
            continue
        accepted.append(match)
    cites: list[Cite] = []
    for match in accepted:
        if id(match) in elided_ids:
            cites.extend(_elided_cites(match, text, cites))
        else:
            cites.extend(cite for cite in _parts(match) if not _is_syscfg_member(cite.file))
    return cites


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos)


def _elided_cites(match: re.Match[str], text: str, prior: list[Cite]) -> list[Cite]:
    """Expand an elided ``:N``/``:N-M`` match into Cites that inherit the file of the nearest
    previous cite on the same line.  A lone ``:N`` with no such predecessor is dropped."""
    spec = match.group("spec")
    line_no = _line_of(text, match.start())
    # Nearest previous accepted cite that ends on this same line (elision never crosses a newline).
    owner = None
    for c in reversed(prior):
        if _line_of(text, c.span[0]) < line_no:
            break
        owner = c
    out = []
    for item in re.split(r"[,;]", spec):
        part = RANGE_PART.fullmatch(item)
        if not part:
            continue
        if owner is None:
            break
        first = int(part.group(1))
        last = max(first, int(part.group(2) or first))
        out.append(Cite(owner.file, first, last, match.group(0), match.span(),
                        bare=owner.bare, inherited=True))
    return out


def _walk_files(root: Path) -> Iterable[Path]:
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS)
        for name in sorted(files):
            yield Path(directory) / name


def _index(root: Path) -> tuple[Path, ...]:
    if root not in _FILE_INDEX:
        _FILE_INDEX[root] = tuple(_walk_files(root)) if root.is_dir() else ()
    return _FILE_INDEX[root]


def resolve(cite: Cite | str, roots: Iterable[str | Path]) -> Path | list[Path] | None:
    """Resolve a citation; return every hit when ambiguous, never pick one."""
    name = cite.file if isinstance(cite, Cite) else str(cite)
    expanded = Path(name).expanduser()
    if expanded.is_absolute():
        return expanded.resolve() if expanded.is_file() else None
    root_paths = [Path(root).expanduser().resolve() for root in roots]
    hits: list[Path] = []
    normalized = name.replace("\\", "/").lstrip("./")
    for root in root_paths:
        direct = root / normalized
        if direct.is_file():
            hits.append(direct.resolve())
        for candidate in _index(root):
            rel = candidate.relative_to(root).as_posix()
            if candidate.name == Path(normalized).name and ("/" not in normalized or rel.endswith(normalized)):
                hits.append(candidate.resolve())
    unique = list(dict.fromkeys(hits))
    if not unique:
        # A bare, extensionless name (ISR/function name such as `halltim_isr`) is not a file; when
        # nothing resolves, it is reported as unresolvable (None), never guessed.
        return None
    return unique[0] if len(unique) == 1 else unique


def quote_norm(text: str) -> str:
    """Use quote_check's normalization (#define, '=', whitespace, quotes)."""
    text = re.sub(r"#\s*define\b", "", text.replace("`", ""), flags=re.IGNORECASE)
    return re.sub(r"\s+", "", text.replace("=", "")).rstrip(";").replace("'", '"')


def _quote_on_lines(quote: str, lines: list[str]) -> bool:
    span = "".join(lines)
    if quote_norm(quote) in quote_norm(span):
        return True
    tokens = re.findall(r"[A-Za-z_]\w*|\d+", quote)
    return bool(tokens) and all(token in span for token in tokens)


def verify(cite: Cite, roots: Iterable[str | Path], quote: str | None = None) -> VerifyResult:
    """Check resolution, cited bounds, and an optional normalized quote."""
    found = resolve(cite, roots)
    if found is None:
        return VerifyResult(cite, None, False, False, None if quote is None else False,
                            f"unresolved: {cite.file}")
    candidates = found if isinstance(found, list) else [found]
    valid, quoted = [], []
    for path in candidates:
        rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
        in_range = 1 <= cite.first <= cite.last <= len(rows)
        if in_range:
            valid.append(path)
            if quote is not None and _quote_on_lines(quote, rows[cite.first - 1:cite.last]):
                quoted.append(path)
    resolved: Path | tuple[Path, ...] = candidates[0] if len(candidates) == 1 else tuple(candidates)
    if len(candidates) > 1:
        prefix = f"ambiguous: {len(candidates)} files match"
    else:
        prefix = str(candidates[0])
    ok_quote = None if quote is None else bool(quoted)
    message = prefix
    if not valid:
        message += f"; line {cite.first}-{cite.last} is outside the file"
    elif quote is not None and not quoted:
        message += "; quote does not match the cited line(s)"
    return VerifyResult(cite, resolved, True, bool(valid), ok_quote, message)


def fill(file: str | Path, first: int | None = None, last: int | None = None,
         *, find: str | None = None) -> FillResult:
    """Return exact numbered source lines, or every line containing ``find``."""
    path = Path(file).expanduser().resolve()
    rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if find is not None:
        found = tuple(SourceLine(number, row) for number, row in enumerate(rows, 1) if find in row)
        count = len(found)
        status = "one match" if count == 1 else ("no matches" if count == 0 else f"{count} matches")
        return FillResult(path, found, count, status)
    if first is None:
        raise ValueError("first is required when find is not used")
    end = first if last is None else last
    if first < 1 or end < first:
        raise ValueError("line range must satisfy 1 <= first <= last")
    selected = tuple(SourceLine(number, rows[number - 1])
                     for number in range(first, min(end, len(rows)) + 1))
    return FillResult(path, selected, len(selected), "exact lines")


def _view_facts(view_dir: Path, file: Path) -> dict[int, list[str]]:
    candidates = [view_dir / (file.name + ".view.md")]
    candidates += sorted(view_dir.rglob(file.name + ".view.md")) if view_dir.is_dir() else []
    view = next((path for path in dict.fromkeys(candidates) if path.is_file()), None)
    if view is None:
        return {}
    facts: dict[int, list[str]] = {}
    current = None
    for row in view.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^.*?:(\d+):\s", row)
        if match:
            current = int(match.group(1)); facts.setdefault(current, [])
        elif current is not None and row.startswith("  "):
            facts[current].append(row)
    return facts


def show_text(file: str | Path, start: int = 1, end: int | None = None,
              facts: str | Path | None = None) -> str:
    """Render numbered.py-compatible output, optionally followed by view facts."""
    path = Path(file)
    rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
    actual_end = min(len(rows), start + 199) if end is None else end
    fact_rows = _view_facts(Path(facts), path) if facts else {}
    output = []
    for number in range(max(1, start), min(len(rows), actual_end) + 1):
        output.append(f"{number}: {rows[number - 1]}")
        output.extend(fact_rows.get(number, ()))
    if actual_end < len(rows):
        output.append(f"... ({len(rows)} lines in {file}; shown {max(1, start)}-{actual_end})")
    return "\n".join(output) + ("\n" if output else "")


def _cite_json(cite: Cite) -> dict:
    return {"file": cite.file, "first": cite.first, "last": cite.last,
            "raw": cite.raw, "span": list(cite.span)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show")
    show.add_argument("file"); show.add_argument("start", nargs="?", type=int, default=1)
    show.add_argument("end", nargs="?", type=int); show.add_argument("--facts")
    parse = sub.add_parser("parse"); parse.add_argument("text", nargs="?")
    parse.add_argument("--file", dest="text_file")
    resolve_cmd = sub.add_parser("resolve"); resolve_cmd.add_argument("cite")
    resolve_cmd.add_argument("--root", action="append", required=True)
    verify_cmd = sub.add_parser("verify"); verify_cmd.add_argument("cite")
    verify_cmd.add_argument("--root", action="append", required=True); verify_cmd.add_argument("--quote")
    fill_cmd = sub.add_parser("fill"); fill_cmd.add_argument("file")
    fill_cmd.add_argument("first", nargs="?", type=int); fill_cmd.add_argument("last", nargs="?", type=int)
    fill_cmd.add_argument("--find")
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            sys.stdout.write(show_text(args.file, args.start, args.end, args.facts)); return 0
        if args.command == "parse":
            text = (Path(args.text_file).read_text(encoding="utf-8", errors="replace")
                    if args.text_file else (args.text if args.text is not None else sys.stdin.read()))
            print(json.dumps([_cite_json(cite) for cite in parse_cites(text)], indent=1)); return 0
        parsed = parse_cites(args.cite) if args.command in ("resolve", "verify") else []
        if args.command in ("resolve", "verify") and len(parsed) != 1:
            parser.error("cite argument must contain exactly one continuous citation")
        if args.command == "resolve":
            result = resolve(parsed[0], args.root)
            paths = [] if result is None else ([result] if isinstance(result, Path) else result)
            print(json.dumps([str(path) for path in paths])); return 0 if len(paths) == 1 else 1
        if args.command == "verify":
            result = verify(parsed[0], args.root, args.quote)
            print(json.dumps({**result.__dict__, "cite": _cite_json(result.cite),
                              "resolved": str(result.resolved)}, default=str, indent=1))
            return 0 if result.exists and result.line_in_range and result.quote_matches is not False else 1
        result = fill(args.file, args.first, args.last, find=args.find)
        for line in result.lines:
            print(f"{line.number}: {line.text}")
        if args.find is not None:
            print(result.status)
        return 0 if result.matches else 1
    except (OSError, ValueError) as error:
        print(f"source_lines.py: {error}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
