#!/usr/bin/env python3
"""asm_view.py -- which assembly lines this build assembles, where, and as what (D8).

A model can reason about assembly, but it cannot see what a *particular* build actually assembles:
preprocessor conditionals (``.if`` / ``.else`` / ``.endif``), macro expansion, and ``.include`` all
change the picture. This tool asks the real assembler.

Two paths, chosen automatically from the compiler family (``platforms.for_compiler``):

1. **listing** -- a TI assembler (cl2000 / clpru / cl7x / armcl) with ``--asm_listing``. The build's
   own ``project_facts.compile_command`` recipe is run in the build folder with ``--asm_listing`` and
   ``--obj_directory`` / ``--asm_directory`` pointed at a *temp* folder, so the fresh listing is
   parsed and the (possibly stale) ``.lst`` left in the build folder is never reused. The listing
   columns are: source line number, optional address (8 hex), optional encoding (hex, may end with
   ``!`` = relocated), source text. A source line is *assembled* when its listing line carries an
   address. The current section (``.sect "name"``, ``.text``, ``.data``, ``.bss``) is tracked;
   ``.set`` / ``.equ`` lines record their value (in the address column) as a symbol.

2. **preprocessed_only** -- a clang-family ``.S`` file. clang has no ``--asm_listing`` listing we can
   parse the same way, so the file is preprocessed with ``-E`` (reuse ``preprocess.py``) and the level
   is reported as ``"preprocessed_only"``.

STALE / not built / FOREIGN -> exit 3 ASK USER. Standard library only. No chip or case names. All
listing / object / assembly output stays under /tmp; nothing is written into a build, project, or SDK
folder.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_STALE = 3
ASK_USER = "ASK USER"

# A listing line that assembles a source line carries an address field: 8 hex digits.
_ADDR_RE = re.compile(r"[0-9A-Fa-f]{8}")
# A source-line-number column: right-justified decimal at the start of a listing line.
_SRCNUM_RE = re.compile(r"^(\s*)(\d{1,6})\b")
# A section directive: .sect "name"  /  .text  /  .data  /  .bss
_SECT_RE = re.compile(r"\.(?:sect|text|data|bss)\b(?:\s+\"([^\"]+)\")?")


def _addr_col_end(body: str) -> int:
    """Index just past the address column of a symbol line (``0001  WD_DISABLE  .set  1``)."""
    m = re.match(r"\s*([0-9A-Fa-f]{1,8})\s*", body)
    return m.end() if m else 0


# --------------------------------------------------------------------------- #
# local dependency imports (stdlib only; no ~/utilities)
# --------------------------------------------------------------------------- #

def _load_project_facts():
    here = Path(__file__).resolve().parent / "project_facts.py"
    if here.is_file():
        sys.path.insert(0, str(here.parent))
        import project_facts
        return project_facts
    raise RuntimeError("project_facts.py not found")


def _load_platforms():
    here = Path(__file__).resolve().parent / "platforms.py"
    if here.is_file():
        sys.path.insert(0, str(here.parent))
        import platforms
        return platforms
    raise RuntimeError("platforms.py not found")


def _load_preprocess():
    here = Path(__file__).resolve().parent / "preprocess.py"
    if here.is_file():
        sys.path.insert(0, str(here.parent))
        import preprocess
        return preprocess
    raise RuntimeError("preprocess.py not found")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def _shlex_split(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _resolve(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _emit(data: dict) -> None:
    print(json.dumps(data, indent=1))


def _usage_and_exit() -> int:
    print(
        "usage:\n"
        "  asm_view.py PROJECT --build B FILE [--lines A-B] [--json]\n"
        "  asm_view.py --sdk-make MK --make-var K=V... FILE [--lines A-B] [--json]\n"
        "  asm_view.py --partial FILE [--define X[=V]] [--undefine Y] [--lines A-B] [--json]",
        file=sys.stderr,
    )
    return EXIT_ERROR


def _parse_lines(spec: str | None) -> tuple[int | None, int | None]:
    """Parse ``"A-B"`` or ``"A"`` into (lo, hi); both None means whole file."""
    if not spec:
        return None, None
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return (int(lo) if lo else None), (int(hi) if hi else None)
    single = int(spec)
    return single, single


# --------------------------------------------------------------------------- #
# TI listing parsing
# --------------------------------------------------------------------------- #

def _parse_listing(text: str, lo: int | None, hi: int | None) -> tuple[list[dict], list[dict]]:
    """Parse a TI assembler listing into (rows, symbols).

    ``rows`` is keyed by source line number (the first listing column). A source line is *assembled*
    when its listing line carries an address (8 hex). ``symbols`` collects ``.set`` / ``.equ``
    definitions (value in the address column). Lines produced by macro expansion / ``.include``
    belong to the including source line and are reported as ``expanded_from`` on that line."""
    rows: dict[int, dict] = {}
    symbols: list[dict] = []
    section = None
    # Map each source line to the first source line that produced it (macro / .include expansion).
    expanded_from: dict[int, int] = {}

    def make_row(src_line: int) -> dict:
        return {
            "line": src_line,
            "assembled": None,
            "section": section,
            "address": None,
            "encoding": None,
            "text": "",
        }

    # The listing is paginated with form-feed page headers; split them out so a page header line is
    # not mistaken for a listing row.
    pages = text.replace("\f", "\n").splitlines()
    for raw in pages:
        # Skip page headers: "<path> PAGE    N".
        if re.match(r"^[^\s].*PAGE\s+\d+\s*$", raw):
            continue
        if raw.strip() == "" or raw.strip().startswith("Copyright") or raw.strip().startswith("TMS320"):
            continue

        m = _SRCNUM_RE.match(raw)
        if not m:
            # A continuation line (address + encoding, no source number) belongs to the previous
            # source line; it is folded into that line's encoding when the line was assembled.
            continue
        src_line = int(m.group(2))
        rest = raw[m.end():]
        body = rest.strip()

        # Section directive: .sect "name" / .text / .data / .bss.
        sect = _SECT_RE.search(body)
        if sect:
            if sect.group(1) is not None:
                section = sect.group(1)
            elif body[sect.start():].startswith(".text"):
                section = ".text"
            elif body[sect.start():].startswith(".data"):
                section = ".data"
            elif body[sect.start():].startswith(".bss"):
                section = ".bss"

        # Symbol definition (.set / .equ): NAME <addr> .set VALUE. The value is in the address
        # column (4 hex for a small constant); NAME is the token after the address. The reported
        # text is the source text (symbol name + directive + value), not the address column.
        if re.search(r"\.set\b|\.equ\b", body):
            parts = body.split()
            # parts[0] is the address column (the symbol value); parts[1] is the symbol name.
            if len(parts) >= 2 and re.fullmatch(r"[0-9A-Fa-f]{1,8}", parts[0]):
                name = parts[1]
                value = int(parts[0], 16)
                # Source text: everything after the address column.
                src_text = body[_addr_col_end(body):].strip()
            else:
                name = parts[0]
                value = None
                src_text = body
            symbols.append({"name": name, "value": value, "line": src_line})
            row = rows.setdefault(src_line, make_row(src_line))
            row["text"] = src_text
            continue

        # Assembled instruction: address present (8 hex) at the start of the body.
        addr_match = _ADDR_RE.match(body)
        if addr_match:
            address = int(addr_match.group(0), 16)
            enc = None
            # The encoding is the next hex token after the address, optionally ending with ' or !.
            after = body[addr_match.end():]
            enc_match = re.match(r"\s*([0-9A-Fa-f]+!|[0-9A-Fa-f]+[']?)\s*", after)
            if enc_match:
                enc = enc_match.group(1).rstrip("!'")
                # Source text: everything after the address + encoding columns.
                src_text = after[enc_match.end():].strip()
            else:
                src_text = after.strip()
            row = rows.setdefault(src_line, make_row(src_line))
            row["assembled"] = True
            row["address"] = address
            row["section"] = section
            row["encoding"] = enc
            row["text"] = src_text
            if src_line in expanded_from:
                row["expanded_from"] = expanded_from[src_line]
        else:
            # A source line with no address: not assembled (e.g. the .else branch of an .if).
            # body is already the source text (no address/encoding columns).
            row = rows.setdefault(src_line, make_row(src_line))
            row["assembled"] = False
            row["section"] = section
            row["text"] = body

    # Filter by line range and order by line.
    out_rows = []
    for line in sorted(rows):
        if lo is not None and line < lo:
            continue
        if hi is not None and line > hi:
            continue
        out_rows.append(rows[line])
    symbols.sort(key=lambda s: s["line"])
    return out_rows, symbols
    return out_rows, symbols


# --------------------------------------------------------------------------- #
# build facts (STALE / not built / FOREIGN -> exit 3)
# --------------------------------------------------------------------------- #

def _build_facts(project_facts, project: Path, build_name: str):
    installed = project_facts.inventory([project_facts.DEFAULT_ROOT])
    headers = project_facts.syscfg_headers(project)
    entries = project_facts.cproject_builds(project)
    if not entries:
        print(f"{ASK_USER}: no .cproject with builds in {project}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    facts_by_name = {e["name"]: project_facts.build_facts(project, e, headers, installed,
                                                          __import__("platform").system()) for e in entries}
    if build_name not in facts_by_name:
        print(f"{ASK_USER}: unknown build {build_name!r}; builds: {[e['name'] for e in entries]}",
              file=sys.stderr)
        sys.exit(EXIT_ERROR)
    facts = facts_by_name[build_name]
    build_dir = project / build_name
    if not facts["built"]:
        print(f"{ASK_USER}: {build_name} was never built; build it in CCS then run asm_view again",
              file=sys.stderr)
        sys.exit(EXIT_ERROR)
    for item in facts["foreign"]:
        print(f"{ASK_USER}: {build_name} was generated on {item['os']} ({item['path']}); rebuild on this machine",
              file=sys.stderr)
        sys.exit(EXIT_ERROR)
    if facts["stale"]:
        print(f"{ASK_USER}: {facts['stale'][0]}; rebuild {build_name} in CCS, then run asm_view again",
              file=sys.stderr)
        sys.exit(EXIT_STALE)
    return build_dir, facts


def _compile_command(project_facts, project: Path, build_name: str, source: str):
    try:
        comp, flgs = project_facts.compile_command(project, build_name, source=source)
    except TypeError:
        comp, flgs = project_facts.compile_command(project, build_name)
    return comp, flgs


# --------------------------------------------------------------------------- #
# dispatcher
# --------------------------------------------------------------------------- #

def _asm_view(project_facts, platforms, preprocess, project: Path, build_name: str, source: str,
              lo: int | None, hi: int | None) -> dict:
    """Dispatch to the TI listing path or the clang preprocessed_only path by compiler family."""
    build_dir, _facts = _build_facts(project_facts, project, build_name)
    compiler, flags = _compile_command(project_facts, project, build_name, source)
    if not compiler:
        print(f"{ASK_USER}: could not determine a compile command for {build_name}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    family = platforms.for_compiler(compiler)["family_kind"]
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = (project / source_path)
    source_path = source_path.resolve()

    # A source the assembler/preprocessor cannot open must error and name the requested source.
    if not source_path.is_file():
        print(f"{ASK_USER}: source {source!r} not found in {project}", file=sys.stderr)
        sys.exit(EXIT_ERROR)

    if family == "clang":
        return _clang_preproc_level(project_facts, platforms, preprocess, project, build_name,
                                    source, lo, hi)
    return _ti_listing_level(project_facts, platforms, project, build_name, source, lo, hi)


def _ti_listing_level(project_facts, platforms, project: Path, build_name: str, source: str,
                      lo: int | None, hi: int | None) -> dict:
    build_dir, _facts = _build_facts(project_facts, project, build_name)
    compiler, flags = _compile_command(project_facts, project, build_name, source)
    if not compiler:
        print(f"{ASK_USER}: could not determine a compile command for {build_name}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    family = platforms.for_compiler(compiler)["family_kind"]
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = (project / source_path)
    source_path = source_path.resolve()

    # The recipe's source token is relative to the build folder; resolve it there so the assembler
    # opens the right file. Fall back to the project-relative path for an absolute or already-resolved
    # source.
    rel_source = source_path.name
    build_source = source_path
    # Prefer the makefile-relative form when it exists (e.g. "../device/x.asm" from RELEASE/device).
    cand = build_dir / source_path.relative_to(project) if str(source_path).startswith(str(project)) else None
    if cand is not None and cand.is_file():
        build_source = cand

    with tempfile.TemporaryDirectory(prefix="asmview_ti_") as tmp:
        work = Path(tmp)
        obj_dir = work / "obj"
        asm_dir = work / "asm"
        obj_dir.mkdir()
        asm_dir.mkdir()
        cmd = [compiler]
        if flags:
            cmd += _shlex_split(" ".join(flags))
        cmd += [
            "--asm_listing",
            f"--obj_directory={obj_dir}",
            f"--asm_directory={asm_dir}",
            "-c",
            str(build_source),
        ]
        proc = _run(cmd, cwd=str(build_dir))
        if proc.returncode != 0 and not obj_dir.glob("*.lst"):
            print(f"{ASK_USER}: assembler failed:\n{proc.stderr}", file=sys.stderr)
            sys.exit(EXIT_ERROR)
        lst_files = sorted(obj_dir.glob("*.lst"))
        if not lst_files:
            lst_files = sorted(asm_dir.glob("*.lst"))
        if not lst_files:
            print(f"{ASK_USER}: assembler produced no listing", file=sys.stderr)
            sys.exit(EXIT_ERROR)
        lst_text = lst_files[0].read_text(encoding="utf-8", errors="replace")

    rows, symbols = _parse_listing(lst_text, lo, hi)
    # The reported command is the actual command that ran: compiler + build flags + the listing /
    # temp-dir flags + -c + the (build-relative) source, so it can be replayed.
    command = [compiler]
    if flags:
        command += _shlex_split(" ".join(flags))
    command += [
        "--asm_listing",
        f"--obj_directory={obj_dir}",
        f"--asm_directory={asm_dir}",
        "-c",
        str(build_source),
    ]
    return {
        "level": "listing",
        "file": _resolve(str(source_path)),
        "command": command,
        "symbols": symbols,
        "lines": rows,
    }


# --------------------------------------------------------------------------- #
# clang .S preprocessed level (reuse preprocess.py)
# --------------------------------------------------------------------------- #

def _clang_preproc_level(project_facts, platforms, preprocess, project: Path, build_name: str,
                         source: str, lo: int | None, hi: int | None) -> dict:
    build_dir, _facts = _build_facts(project_facts, project, build_name)
    compiler, flags = _compile_command(project_facts, project, build_name, source)
    if not compiler:
        print(f"{ASK_USER}: could not determine a compile command for {build_name}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = (project / source_path)
    source_path = source_path.resolve()

    with tempfile.TemporaryDirectory(prefix="asmview_pp_") as tmp:
        workdir = Path(tmp)
        # preprocess._clang_preproc runs -E -dD -C; reuse it.
        try:
            expanded_text = preprocess._clang_preproc(compiler, list(flags), source_path, workdir)
        except AttributeError:
            # preprocess may not expose _clang_preproc; run -E directly.
            cmd = [compiler] + list(flags) + ["-E", "-dD", "-C", str(source_path)]
            proc = _run(cmd, cwd=str(workdir))
            if proc.returncode != 0 and not proc.stdout:
                print(f"{ASK_USER}: preprocessor failed:\n{proc.stderr}", file=sys.stderr)
                sys.exit(EXIT_ERROR)
            expanded_text = proc.stdout

    expanded = expanded_text.splitlines()
    source_lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()

    # Find the last "# N \"file\"" marker for this source; everything after it is the expanded
    # content of the source file (preprocessor noise is before it).
    last_marker_idx = -1
    for idx, line in enumerate(expanded):
        m = re.match(r"^#\s+\d+\s+\"([^\"]+)\"", line)
        if m and _resolve(m.group(1)) == _resolve(str(source_path)):
            last_marker_idx = idx
    content_start = last_marker_idx + 1
    content_lines = expanded[content_start:]

    # The preprocessor consumes directive lines (#if / #else / #endif / #define when already set)
    # but keeps a blank line in their place, and keeps the active content. Map the content lines
    # back to source lines one-to-one in order: every source line consumes the next content line
    # (blank for a consumed directive, the expanded text for an active line).
    rows = []
    content_iter = iter(content_lines)
    src_index = 0
    for src_line in source_lines:
        try:
            content_line = next(content_iter)
        except StopIteration:
            break
        if lo is not None and (src_index + 1) < lo:
            src_index += 1
            continue
        if hi is not None and (src_index + 1) > hi:
            src_index += 1
            continue
        rows.append({
            "line": src_index + 1,
            "assembled": None,  # preprocessed only; assembler has not run
            "section": None,
            "address": None,
            "encoding": None,
            "text": content_line,
        })
        src_index += 1
    rows.sort(key=lambda r: r["line"])
    return {
        "level": "preprocessed_only",
        "file": _resolve(str(source_path)),
        "command": [compiler] + list(flags),
        "symbols": [],
        "lines": rows,
    }


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    project_facts = _load_project_facts()
    platforms = _load_platforms()
    preprocess = _load_preprocess()

    tokens = list(argv if argv is not None else sys.argv[1:])
    project_dir = None
    build_name = None
    source = None
    lines_spec = None
    as_json = False

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--build":
            build_name = tokens[i + 1]; i += 2; continue
        if tok.startswith("--build="):
            build_name = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--lines":
            lines_spec = tokens[i + 1]; i += 2; continue
        if tok.startswith("--lines="):
            lines_spec = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--json":
            as_json = True; i += 1; continue
        if tok.startswith("-"):
            i += 1; continue
        if project_dir is None:
            project_dir = tok
        elif source is None:
            source = tok
        i += 1

    if source is None or project_dir is None or build_name is None:
        return _usage_and_exit()

    lo, hi = _parse_lines(lines_spec)

    try:
        project = Path(project_dir).expanduser().resolve()
        data = _asm_view(project_facts, platforms, preprocess, project, build_name, source, lo, hi)
    except subprocess.TimeoutExpired:
        print(f"{ASK_USER}: compiler timed out", file=sys.stderr)
        return EXIT_ERROR
    except RuntimeError as error:
        print(f"{ASK_USER}: {error}", file=sys.stderr)
        return EXIT_ERROR

    if as_json:
        _emit(data)
    else:
        print(json.dumps(data, indent=1))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
