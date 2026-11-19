#!/usr/bin/env python3
"""preprocess.py -- which lines this build compiles, and their expanded text (D2).

A model can reason about preprocessor conditionals, but it cannot see what a *particular*
build actually compiles. This tool asks the real toolchain. Three levels, chosen
automatically; the result always says which level was used.

1. ``build`` -- a CCS build exists. ``project_facts.compile_command(project, build, source=<file>)``
   gives the compiler and flags; the compiler's preprocessor expands the file. clang family runs
   ``-E -dD`` (define lines stay at their line so they count); TI legacy runs
   ``--preproc_with_line`` plus ``--pp_directory`` into a temp dir. ``platforms.for_compiler``
   picks the family. STALE / not built / FOREIGN -> ASK USER, exit 3.

2. ``sdk_make`` -- a file inside an SDK library. ``--sdk-make <library makefile>`` with
   ``--make-var NAME=VALUE``; run ``make -s -n -f <makefile> <vars>`` in the library folder (dry
   run, writes nothing), take the compile line that names the file, drop ``-c``/``-o``/``-MMD`` and
   run ``-E``.

3. ``partial`` -- no build and no makefile. ``--define NAME[=V]`` / ``--undefine NAME`` on known
   symbols only; run ``unifdef`` (``-b`` keeps blank lines so output line numbers equal source line
   numbers). Lines decided by known symbols compile true/false; lines under an ``#if`` with an
   unknown symbol compile null and that symbol is listed in ``unknown_symbols``. Never guess unknown
   symbols.

Line mapping follows the ``# N "file"`` / ``#line N "file"`` markers where the compiler emits them.
The requested file is matched by its RESOLVED path (several headers can end in ``config.h``). A line
is compiled when it appears in the output at its source line number (define lines count via ``-dD``).
Some TI compilers emit markers only at function boundaries, so for the ``build`` and ``sdk_make``
levels the compiler's assembly (``-S -g`` for clang, ``-g -k`` for TI legacy) carries the true
per-source-line information and is used to decide which lines are compiled; the ``-E`` output
supplies the expanded text.

Standard library + unifdef only. No chip or case names. Work in the tool's own folder.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_STALE = 3
ASK_USER = "ASK USER"


# --------------------------------------------------------------------------- #
# project_facts / platforms import (build selection, staleness, FOREIGN)
# --------------------------------------------------------------------------- #

def _load_project_facts():
    here = Path(__file__).resolve().parent / "project_facts.py"
    for cand in (here,):
        if cand.is_file():
            sys.path.insert(0, str(cand.parent))
            import project_facts  # noqa: E402
            return project_facts
    raise RuntimeError("project_facts.py not found")


def _load_platforms():
    here = Path(__file__).resolve().parent / "platforms.py"
    # Prefer the local copy (the front end's own platform table); fall back to the shared one.
    for cand in (here,):
        if cand.is_file():
            sys.path.insert(0, str(cand.parent))
            import platforms  # noqa: E402
            return platforms
    raise RuntimeError("platforms.py not found")


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


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def _emit(data: dict) -> None:
    print(json.dumps(data, indent=1))


def _usage_and_exit() -> int:
    print(
        "usage:\n"
        "  preprocess.py PROJECT --build B FILE [--lines A-B]\n"
        "  preprocess.py --sdk-make MK --make-var K=V... FILE [--lines A-B]\n"
        "  preprocess.py --partial FILE [--define X[=V]] [--undefine Y] [--lines A-B]",
        file=sys.stderr,
    )
    return EXIT_ERROR


# --------------------------------------------------------------------------- #
# line-range parsing
# --------------------------------------------------------------------------- #

def _parse_lines(spec: str | None) -> tuple[int | None, int | None]:
    """Parse ``"A-B"`` or ``"A"`` into (lo, hi); both None means whole file."""
    if not spec:
        return None, None
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return (int(lo) if lo else None), (int(hi) if hi else None)
    single = int(spec)
    return single, single


def _in_range(line: int, lo: int | None, hi: int | None) -> bool:
    if lo is not None and line < lo:
        return False
    if hi is not None and line > hi:
        return False
    return True


# --------------------------------------------------------------------------- #
# level 1: build
# --------------------------------------------------------------------------- #

def _build_facts(project_facts, project: Path, build_name: str):
    """Return (build_dir, compiler, flags) or exit 3 for stale / not built / FOREIGN."""
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
        print(f"{ASK_USER}: {build_name} was never built; build it in CCS then run preprocess again",
              file=sys.stderr)
        sys.exit(EXIT_ERROR)
    for item in facts["foreign"]:
        print(f"{ASK_USER}: {build_name} was generated on {item['os']} ({item['path']}); rebuild on this machine",
              file=sys.stderr)
        sys.exit(EXIT_ERROR)
    if facts["stale"]:
        print(f"{ASK_USER}: {facts['stale'][0]}; rebuild {build_name} in CCS, then run preprocess again",
              file=sys.stderr)
        sys.exit(EXIT_STALE)
    return build_dir, facts


def _compile_command(project_facts, project: Path, build_name: str, source: str):
    """Return (compiler, flags) from project_facts, or (None, None)."""
    try:
        comp, flgs = project_facts.compile_command(project, build_name, source=source)
    except TypeError:
        comp, flgs = project_facts.compile_command(project, build_name)
    return comp, flgs


def _clang_preproc(compiler: str, flags: list[str], source: Path, workdir: Path) -> str:
    """Run clang family ``-E -dD -C``; return the expanded text.

    ``-C`` keeps comments in the output so comment lines in an active branch are named by a ``# N``
    marker exactly like code, and can be reported as compiled. ``-dD`` keeps ``#define`` lines at
    their source line so they count."""
    cmd = [compiler] + list(flags) + ["-E", "-dD", "-C", str(source)]
    proc = _run(cmd, cwd=str(workdir))
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError(proc.stderr)
    return proc.stdout


def _ti_legacy_preproc(compiler: str, flags: list[str], source: Path, workdir: Path) -> tuple[str, Path]:
    """Run cl2000-style ``--preproc_with_line --pp_directory=<tmp>``.

    Returns (expanded_text, pp_path). The .pp file name is the source name with ``.c``/``.cpp``
    replaced by ``.pp`` (cl2000 writes ``<name>.pp`` into ``--pp_directory``). ``--preproc_macros``
    conflicts with ``--preproc_with_line`` on cl2000, so it is not used."""
    pp_dir = workdir / "pp"
    pp_dir.mkdir(exist_ok=True)
    stem = source.name
    for ext in (".c", ".cpp", ".cc", ".cxx"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)] + ".pp"
            break
    else:
        stem += ".pp"
    pp_path = pp_dir / stem
    cmd = [compiler] + list(flags) + [
        "--preproc_only", "--preproc_with_line", "--preproc_with_comment",
        f"--pp_directory={pp_dir}", "-c", str(source),
    ]
    proc = _run(cmd, cwd=str(workdir))
    if not pp_path.is_file():
        candidates = sorted(pp_dir.glob("*.pp"))
        if not candidates:
            raise RuntimeError(proc.stderr or "ti legacy produced no .pp")
        pp_path = candidates[0]
    return pp_path.read_text(encoding="utf-8", errors="replace"), pp_path


def _assembly_loc_map(compiler: str, family: str, flags: list[str], source: Path,
                      workdir: Path) -> dict[int, int]:
    """Return {source_line: output_line} from the compiler's assembly debug markers.

    clang family emits ``.loc <fileid> <src> <col>`` with a ``.file`` table mapping file ids to
    paths; ti legacy emits ``.dwpsn file "path",line <n>,column <m>``. Either way we collect the
    true per-source-line information that the preprocessor ``# N`` markers do not provide."""
    outdir = workdir / "asm"
    outdir.mkdir(exist_ok=True)
    ext = ".s" if family == "clang" else ".asm"
    asm_path = outdir / (source.name + ext)
    if family == "clang":
        cmd = [compiler] + list(flags) + ["-S", "-g", f"--output={asm_path}", str(source)]
    else:
        cmd = [compiler] + list(flags) + ["-g", "-k", f"--asm_directory={outdir}", "-c", str(source)]
    proc = _run(cmd, cwd=str(workdir))
    if family == "clang" and not asm_path.is_file():
        return {}
    if family != "clang" and not asm_path.is_file():
        candidates = sorted(outdir.glob("*.asm"))
        if not candidates:
            return {}
        asm_path = candidates[0]
    text = asm_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    target = _resolve(str(source))
    result: dict[int, int] = {}
    if family == "clang":
        id_to_path: dict[int, str] = {}
        for line in lines:
            # Two forms: ".file N "path"" (single arg) or ".file N "dir" "file"" (two args).
            m = re.search(r'\.file\s+(\d+)\s+"((?:[^"\\]|\\.)*)"\s+"((?:[^"\\]|\\.)*)"', line)
            if m:
                id_to_path[int(m.group(1))] = _resolve(f"{m.group(2)}/{m.group(3)}")
                continue
            m = re.search(r'\.file\s+(\d+)\s+"((?:[^"\\]|\\.)*)"', line)
            if m:
                id_to_path[int(m.group(1))] = _resolve(m.group(2))
        for idx, line in enumerate(lines, start=1):
            m = re.search(r"\.loc\s+(\d+)\s+(\d+)\s+(\d+)", line)
            if not m:
                continue
            fid = int(m.group(1))
            src_line = int(m.group(2))
            path = id_to_path.get(fid)
            if path is not None and path == target:
                result[src_line] = idx
    else:
        for idx, line in enumerate(lines, start=1):
            m = re.search(r'\.dwpsn\s+file\s+"((?:[^"\\]|\\.)*)",\s*line\s+(\d+)', line)
            if m and _resolve(m.group(1)) == target:
                src_line = int(m.group(2))
                result.setdefault(src_line, idx)
    return result


def _marker_line_map(expanded: list[str], target: str) -> dict[int, int]:
    """Map source line -> output line using ``# N "path"`` / ``#line N "path"`` markers.

    A source line is active for every output line until the next marker that names the target file
    again. Returns {source_line: first_output_line_in_scope}.

    The preprocessor is run with comments kept (clang ``-C``, cl2000 ``--preproc_with_comment``), so
    comment lines in an active branch appear in the output with their source marker, exactly like
    code. A source line is therefore *active* when it is named by a marker; it is *absent* when it is
    a directive (``#if``/``#else``/...) or when it lies in an inactive ``#if`` branch."""
    mapping: dict[int, int] = {}
    current: int | None = None
    for idx, line in enumerate(expanded, start=1):
        m = re.match(r'^#\s+(\d+)\s+"((?:[^"\\]|\\.)*)"\s*\d*\s*$', line)
        if not m:
            m = re.match(r'^#line\s+(\d+)\s+"((?:[^"\\]|\\.)*)"\s*\d*\s*$', line)
        if m:
            ln = int(m.group(1))
            path = _resolve(m.group(2))
            if path == target:
                current = ln
            elif current is not None:
                current = None
            continue
        if current is not None and current not in mapping:
            mapping[current] = idx
    return mapping


def _active_status(source_lines: list[str], expanded: list[str], loc_map: dict[int, int],
                   target: str) -> dict[int, bool | None]:
    """Return {source_line: True | False | None} for the requested file.

    ``loc_map`` (the assembly ``.loc`` map) is the ground truth for which source lines carry compiled
    code. Comments and blanks are not in ``loc_map`` but are *compiled* when they lie inside an active
    region. A line is therefore classified as:

    * ``True``  -- it carries code (in ``loc_map``), or it is a comment/blank inside an active region.
    * ``False`` -- it lies in an ``#if`` branch that was not taken (no line of that branch carries
      code).
    * ``None``  -- a directive line (``#if``/``#ifdef``/``#elif``/``#else``/``#endif``) or a blank
      line outside any branch; the tool never guesses these.

    Active vs inactive branches are decided by walking the source and tracking ``#if`` nesting,
    evaluating each condition against the symbols the build actually defines (from the ``-dD``
    output). A branch is taken when its condition holds; ``#elif`` chains resolve in order; an
    ``#else`` is taken when no earlier branch was. A condition referencing an unknown symbol is
    treated as false -- the tool never fabricates a definition.
    """
    compiled = set(loc_map.keys())
    directive_re = re.compile(r"^#\s*(if|ifdef|ifndef|elif|else|endif)\b")

    # Collect the defines the build actually emitted (so #if/#elif can be evaluated).
    defines: set[str] = set()
    define_pat = re.compile(r"^#define\s+(\w+)")
    for line in expanded:
        m = define_pat.match(line.strip())
        if m:
            defines.add(m.group(1))

    def cond_true(expr: str) -> bool:
        """Evaluate a simple #if expression against known defines. Unknown identifiers -> false."""
        expr = expr.strip()
        if not expr:
            return True
        expr = re.sub(r"defined\s*\(\s*(\w+)\s*\)", r"\1", expr)
        or_parts = re.split(r"\|\|", expr)
        for part in or_parts:
            if _and_part_true(part, defines):
                return True
        return False

    def _and_part_true(part: str, defines: set[str]) -> bool:
        part = part.strip()
        if not part:
            return False
        and_parts = re.split(r"&&", part)
        for ap in and_parts:
            ap = ap.strip()
            if not ap:
                continue
            m = re.fullmatch(r"(\w+)", ap)
            if m:
                if m.group(1) not in defines:
                    return False
                continue
            m = re.match(r"(\w+)\s*(==|!=|<|>|<=|>=)\s*(\S+)", ap)
            if m:
                name, op, val = m.group(1), m.group(2), m.group(3).strip()
                if name not in defines:
                    return False
                nv = _define_int(defines.get(name))
                iv = _define_int(val)
                if nv is None or iv is None:
                    return False
                ops = {"==": nv == iv, "!=": nv != iv, "<": nv < iv, ">": nv > iv,
                       "<=": nv <= iv, ">=": nv >= iv}
                if not ops.get(op, False):
                    return False
                continue
            return False
        return True

    def _define_int(text):
        if text is None:
            return None
        text = text.strip()
        try:
            return int(text, 0)
        except ValueError:
            return None

    stack: list[tuple[bool, bool, bool]] = []

    def current_active() -> bool:
        # Active only when every enclosing branch is currently taken (its branch_active is True).
        return all(f[2] for f in stack)

    status: dict[int, bool | None] = {}
    for idx, raw in enumerate(source_lines, start=1):
        s = raw.strip()
        m_if = re.match(r"^#\s*(if|ifdef|ifndef)\b(.*)$", s)
        m_elif = re.match(r"^#\s*elif\b(.*)$", s)
        m_else = re.match(r"^#\s*else\b", s)
        m_endif = re.match(r"^#\s*endif\b", s)
        if m_if:
            cond = m_if.group(2).strip()
            parent_active = current_active()
            if not parent_active:
                branch_active = False
            elif re.match(r"^ifdef\b", cond):
                name = cond[len("ifdef"):].strip()
                branch_active = name in defines
            elif re.match(r"^ifndef\b", cond):
                name = cond[len("ifndef"):].strip()
                branch_active = name not in defines
            else:
                branch_active = cond_true(cond)
            # frame: (parent_active, any_taken, branch_active)
            stack.append((parent_active, branch_active, branch_active))
            status[idx] = None
            continue
        if m_elif:
            if stack:
                parent_active, any_taken, _ = stack.pop()
                cond = m_elif.group(1).strip()
                branch_active = parent_active and (not any_taken) and cond_true(cond)
                stack.append((parent_active, any_taken or branch_active, branch_active))
            status[idx] = None
            continue
        if m_else:
            if stack:
                parent_active, any_taken, _ = stack.pop()
                branch_active = parent_active and (not any_taken)
                stack.append((parent_active, any_taken or branch_active, branch_active))
            status[idx] = None
            continue
        if m_endif:
            if stack:
                stack.pop()
            status[idx] = None
            continue
        if directive_re.match(s):
            status[idx] = None
        elif s == "":
            status[idx] = None if not stack else current_active()
        elif idx in compiled:
            status[idx] = True
        else:
            status[idx] = current_active()
    return status

def _identifiers(text: str) -> list[str]:
    """Return the identifier tokens in ``text`` in order (identities that survive macro expansion)."""
    return re.findall(r"\b[A-Za-z_]\w*\b", text)


def _pair_expanded(source_lines: list[str], expanded: list[str],
                   loc_map: dict[int, int],
                   expanded_loc: dict[int, int]) -> dict[int, str]:
    """Return {source_line: expanded_text} for the compiled source lines.

    Strategy: for each compiled source line we know its assembly output line (``loc_map``) and, where
    the compiler emits them, its preprocessor output line (``expanded_loc``). The ``-E`` output is a
    second linear scan of the same source, in the same order, but without per-line markers for some
    TI/clang builds. We pair by identifier overlap: the identifiers that survive macro expansion
    (register names like ``TIMG4``/``COUNTERREGS``/``CCACT_01``, variable names like ``p``) are a
    strong fingerprint. For each compiled source line we take the ``-E`` line whose identifier set
    overlaps the most with the source line's, breaking ties by ``-E`` position closest to the line's
    predicted position (from the assembly ``.loc`` via the ``-E`` markers as anchors)."""
    # Predicted ``-E`` position per compiled source line, from assembly position interpolated through
    # the anchors (source lines with both a .loc and an -E marker).
    anchors = [(loc_map[sl], expanded_loc[sl]) for sl in loc_map if sl in expanded_loc]
    anchors.sort()

    def predicted_pp(asm_pos: int) -> int:
        if not anchors:
            return asm_pos
        if asm_pos <= anchors[0][0]:
            return anchors[0][1]
        if asm_pos >= anchors[-1][0]:
            return anchors[-1][1]
        for i in range(1, len(anchors)):
            a0, p0 = anchors[i - 1]
            a1, p1 = anchors[i]
            if a0 <= asm_pos <= a1:
                if a1 == a0:
                    return p1
                frac = (asm_pos - a0) / (a1 - a0)
                return int(round(p0 + frac * (p1 - p0)))
        return anchors[-1][1]

    # Pre-compute identifier sequences and sets for every non-blank ``-E`` line.
    pp_idsets = []
    for line in expanded:
        s = line.strip()
        idseq = _identifiers(s) if s else []
        pp_idsets.append((s, idseq, set(idseq)) if s else (s, [], set()))

    result: dict[int, str] = {}
    for src_line, asm_pos in loc_map.items():
        src_idents = _identifiers(source_lines[src_line - 1])
        src_idset = set(src_idents)
        # Exact -E marker for this line (clang per-line case).
        if src_line in expanded_loc:
            out_ln = expanded_loc[src_line]
            if 1 <= out_ln <= len(expanded):
                result[src_line] = expanded[out_ln - 1].strip()
                continue
        if not src_idset:
            continue
        target_pp = predicted_pp(asm_pos)
        best = None
        best_score = -1
        best_dist = float("inf")
        for out_ln, (text, idseq, idset) in enumerate(pp_idsets, start=1):
            if not idset:
                continue
            overlap = len(src_idset & idset)
            if overlap == 0:
                continue
            # Prefer an exact identifier-sequence match (same identifiers in the same order), which
            # distinguishes a call ``DAB_setupSFRA();`` from a prototype ``void DAB_setupSFRA();``.
            exact = 1 if idseq == src_idents else 0
            dist = abs(out_ln - target_pp)
            # Prefer exact sequence, then higher overlap, then closer position.
            score = exact * 1_000_000_000 + overlap * 1_000_000 - dist
            if score > best_score or (score == best_score and dist < best_dist):
                best_score = score
                best_dist = dist
                best = text.strip()
        if best is not None:
            result[src_line] = best
    return result


def _pair_expanded_with_source(source_lines: list[str], expanded: list[str],
                               loc_map: dict[int, int],
                               expanded_loc: dict[int, int]) -> dict[int, str]:
    """Alias kept for compatibility; delegates to :func:`_pair_expanded`."""
    return _pair_expanded(source_lines, expanded, loc_map, expanded_loc)


def _match_expanded_to_source(source_lines: list[str], expanded: list[str], src_index: int,
                              loc_map: dict[int, int],
                              expanded_loc: dict[int, int] | None,
                              pairing: dict[int, str]) -> str | None:
    """Return the expanded text for source line ``src_index``.

    1. Exact preprocessor-marker position, if the compiler emitted one for this line.
    2. The greedy pairing built by :func:`_pair_expanded_with_source`.
    """
    if expanded_loc and src_index in expanded_loc:
        out_ln = expanded_loc[src_index]
        if 1 <= out_ln <= len(expanded):
            return expanded[out_ln - 1].strip()
    return pairing.get(src_index)


def _build_rows(source_path: Path, expanded: list[str], loc_map: dict[int, int],
                expanded_loc: dict[int, int], lo: int | None, hi: int | None) -> list[dict]:
    """Assemble the ``lines`` array from the expanded text and the active-region analysis.

    ``expanded`` was produced with comments kept, so :func:`_active_status` decides ``compiled`` from
    the preprocessor markers alone: active lines (code or comment) are ``True``, directive and blank
    lines are ``None``, and lines in an inactive ``#if`` branch are ``False``. The assembly ``.loc``
    map and the ``-dD`` marker map are still used by :func:`_match_expanded_to_source` to supply the
    expanded text for each active line."""
    source_lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(source_lines)
    status = _active_status(source_lines, expanded, loc_map, _resolve(str(source_path)))
    pairing = _pair_expanded_with_source(source_lines, expanded, loc_map, expanded_loc)
    rows = []
    for src_line in range(1, total + 1):
        if not _in_range(src_line, lo, hi):
            continue
        compiled = status.get(src_line)
        expanded_line = _match_expanded_to_source(source_lines, expanded, src_line, loc_map,
                                                  expanded_loc, pairing)
        rows.append({
            "line": src_line,
            "compiled": compiled,
            "expanded": expanded_line,
        })
    return rows


def _level_build(project_facts, platforms, project: Path, build_name: str, source: str,
                 lo: int | None, hi: int | None) -> dict:
    build_dir, _facts = _build_facts(project_facts, project, build_name)
    compiler, flags = _compile_command(project_facts, project, build_name, source)
    if not compiler:
        print(f"{ASK_USER}: could not determine a compile command for {build_name}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    source_path = (project / source) if not Path(source).is_absolute() else Path(source)
    source_path = source_path.resolve()
    family = platforms.for_compiler(compiler)["family_kind"]

    with tempfile.TemporaryDirectory(prefix="preprocess_build_") as tmp:
        workdir = Path(tmp)
        if family == "clang":
            expanded_text = _clang_preproc(compiler, flags, source_path, workdir)
        else:
            expanded_text, _pp_path = _ti_legacy_preproc(compiler, flags, source_path, workdir)

    with tempfile.TemporaryDirectory() as tmp2:
        loc_map = _assembly_loc_map(compiler, family, flags, source_path, Path(tmp2))

    expanded = expanded_text.splitlines()
    expanded_loc = _marker_line_map(expanded, _resolve(str(source_path)))
    return {
        "level": "build",
        "file": _resolve(str(source_path)),
        "command": [compiler] + flags,
        "unknown_symbols": [],
        "lines": _build_rows(source_path, expanded, loc_map, expanded_loc, lo, hi),
    }


# --------------------------------------------------------------------------- #
# level 2b: plain repo Makefile (make-dir)
# --------------------------------------------------------------------------- #

def _repo_make_compile_line(make_dir: Path, make_file: str | None, target_file: str,
                            makevars: list[str]) -> list[str] | None:
    """Run ``make -s -n -B`` in ``make_dir`` and return the compile line naming ``target_file``.

    ``-B`` (equivalent to ``--always-make``) forces make to re-evaluate even if targets are up to
    date, which is what we want for a dry run of a plain repo Makefile. Writes nothing (``-n``)."""
    cmd = ["make", "-s", "-n", "-B"]
    if make_file:
        cmd += ["-f", str(make_file)]
    cmd += makevars
    cmd.append("all")  # build the default target(s)
    proc = _run(cmd, cwd=str(make_dir))
    target = _resolve(target_file)
    makefile_dir = str(make_dir)
    # The target as written in the recipe is relative to the make dir; resolve it the same way so the
    # per-token comparison below matches.
    target_in_make = _resolve(os.path.join(makefile_dir, target_file))
    for line in proc.stdout.splitlines():
        stripped = line.lstrip()
        first = stripped.split(" ", 1)[0]
        # A real compile command starts with a compiler: either an existing path (``/usr/bin/gcc`` or
        # ``./gcc``) or a bare name on PATH (``gcc``). This also skips ``echo ...`` recipe lines.
        is_exe = os.path.exists(first) or os.path.exists(first + (".exe" if os.name == "nt" else ""))
        if not is_exe and not first.startswith(("/", "./")):
            first_abs = shutil.which(first)
            if first_abs is None:
                continue
        if target_file.split("/")[-1] not in line:
            continue
        tokens = _shlex_split(line)
        out: list[str] = []
        skip_next = False
        for tok in tokens:
            if skip_next:
                skip_next = False
                continue
            if tok in ("-c", "-MMD", "-MF", "-MP", "-MQ"):
                continue
            if tok == "-o":
                skip_next = True
                continue
            if tok.startswith("-o"):
                continue
            out.append(tok)
            cand = tok if os.path.isabs(tok) else os.path.join(makefile_dir, tok)
            if cand == target_in_make or _resolve(cand) == target:
                return out
    return None


def _level_make(platforms, make_dir: Path, make_file: str | None, source: str, makevars: list[str],
                lo: int | None, hi: int | None) -> dict:
    """Plain-repo level: ``make -s -n -B`` in a repo, take the compile line naming FILE, then run the
    compiler's preprocessor. ``level`` is ``"make"``."""
    make_dir = make_dir.resolve()
    compile_line = _repo_make_compile_line(make_dir, make_file, source, makevars)
    if not compile_line:
        print(f"{ASK_USER}: no compile line naming {source} in makefile in {make_dir}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    compiler = compile_line[0]
    flags = compile_line[1:]
    family = platforms.for_compiler(compiler)["family_kind"]
    # The recipe's source token is relative to the make dir; resolve it there (fall back to cwd for
    # an absolute or already-resolved path).
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = (make_dir / source_path).resolve()
    source_path = source_path.resolve()
    source = Path(source_path)
    # Drop the trailing source token and make makefile-relative -I paths absolute.
    makefile_dir = str(make_dir)
    while flags:
        cand = flags[-1]
        if os.path.isabs(cand) and _resolve(cand) == source_path:
            flags.pop()
        elif not os.path.isabs(cand) and _resolve(os.path.join(makefile_dir, cand)) == source_path:
            flags.pop()
        else:
            break
    for idx, flag in enumerate(flags):
        if flag.startswith("-I") and len(flag) > 2 and not os.path.isabs(flag[2:]) and not os.path.exists(flag[2:]):
            flags[idx] = "-I" + os.path.join(makefile_dir, flag[2:])

    with tempfile.TemporaryDirectory(prefix="preprocess_make_") as tmp:
        workdir = Path(tmp)
        if family == "clang" or family == "gnu":
            expanded_text = _clang_preproc(compiler, flags, source, workdir)
        else:
            expanded_text, _ = _ti_legacy_preproc(compiler, flags, source, workdir)

    with tempfile.TemporaryDirectory() as tmp2:
        loc_map = _assembly_loc_map(compiler, family, flags, source, Path(tmp2))

    expanded = expanded_text.splitlines()
    expanded_loc = _marker_line_map(expanded, _resolve(str(source_path)))
    return {
        "level": "make",
        "file": _resolve(str(source_path)),
        "command": compile_line,
        "unknown_symbols": [],
        "lines": _build_rows(source, expanded, loc_map, expanded_loc, lo, hi),
    }


# --------------------------------------------------------------------------- #
# level 2: sdk_make
# --------------------------------------------------------------------------- #

def _sdk_make_compile_line(makefile: Path, target_file: str, makevars: list[str]) -> list[str] | None:
    """Run ``make -s -n -f <makefile> <vars>`` in the makefile folder and return the compile line
    that names ``target_file`` (as tokens), or None."""
    cmd = ["make", "-s", "-n", "-f", str(makefile)] + makevars
    proc = _run(cmd, cwd=str(makefile.parent))
    target = _resolve(target_file)
    makefile_dir = str(makefile.parent)
    for line in proc.stdout.splitlines():
        # Skip echo / recipe-continuation lines: only real compile commands start with an
        # executable path (the compiler). This avoids matching ``echo Compiling: ...psc.c`` lines.
        stripped = line.lstrip()
        first = stripped.split(" ", 1)[0]
        if not (first.startswith("/") or first.startswith("./")) or not os.path.exists(first):
            continue
        if target_file.split("/")[-1] not in line:
            continue
        tokens = _shlex_split(line)
        out: list[str] = []
        skip_next = False
        for tok in tokens:
            if skip_next:
                skip_next = False
                continue
            if tok in ("-c", "-MMD", "-MF", "-MP", "-MQ"):
                continue
            if tok == "-o":
                skip_next = True
                continue
            if tok.startswith("-o"):
                continue
            out.append(tok)
            # Resolve the source token relative to the makefile directory (the recipe uses paths
            # relative to the makefile), and compare against the resolved target.
            cand = tok if os.path.isabs(tok) else os.path.join(makefile_dir, tok)
            if cand == target_file or _resolve(cand) == target:
                return out
    return None


def _level_sdk_make(platforms, makefile: Path, target_file: str, makevars: list[str],
                    lo: int | None, hi: int | None) -> dict:
    compile_line = _sdk_make_compile_line(makefile, target_file, makevars)
    if not compile_line:
        print(f"{ASK_USER}: no compile line naming {target_file} in makefile {makefile}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    compiler = compile_line[0]
    flags = compile_line[1:]
    family = platforms.for_compiler(compiler)["family_kind"]
    source_path = _resolve(target_file)
    source = Path(source_path)
    # The compile line carries the source as its final token (possibly a makefile-relative path);
    # drop any trailing token that resolves to the target so the compiler is invoked with the
    # absolute source path instead.
    while flags:
        cand = flags[-1]
        if os.path.isabs(cand) and _resolve(cand) == source_path:
            flags.pop()
        elif not os.path.isabs(cand) and _resolve(os.path.join(str(makefile.parent), cand)) == source_path:
            flags.pop()
        else:
            break
    # The recipe uses makefile-relative -I paths; the compiler is run from a temp dir, so make them
    # absolute relative to the makefile directory.
    makefile_dir = str(makefile.parent)
    for idx, flag in enumerate(flags):
        if flag.startswith("-I") and len(flag) > 2 and not os.path.isabs(flag[2:]) and not os.path.exists(flag[2:]):
            flags[idx] = "-I" + os.path.join(makefile_dir, flag[2:])
    with tempfile.TemporaryDirectory(prefix="preprocess_sdk_") as tmp:
        workdir = Path(tmp)
        if family == "clang":
            expanded_text = _clang_preproc(compiler, flags, source, workdir)
        else:
            expanded_text, _pp_path = _ti_legacy_preproc(compiler, flags, source, workdir)

    with tempfile.TemporaryDirectory() as tmp2:
        loc_map = _assembly_loc_map(compiler, family, flags, source, Path(tmp2))

    expanded = expanded_text.splitlines()
    expanded_loc = _marker_line_map(expanded, source_path)
    return {
        "level": "sdk_make",
        "file": source_path,
        "command": compile_line,
        "unknown_symbols": [],
        "lines": _build_rows(source, expanded, loc_map, expanded_loc, lo, hi),
    }


# --------------------------------------------------------------------------- #
# level 3a: partial seeded from a .cproject (from-cproject)
# --------------------------------------------------------------------------- #

def _unresolved(defs: list[str]) -> list[str]:
    """Return the defines that reference an unresolved ``${...}`` variable, in first-seen order."""
    out: list[str] = []
    for d in defs:
        if "${" in d and d not in out:
            out.append(d)
    return out


def _level_from_cproject(project_facts, platforms, cproject: str, build_name: str,
                         source: str, defines: dict[str, str | None], undefs: set[str],
                         lo: int | None, hi: int | None) -> dict:
    """Partial mode whose known symbols come from a build's ``.cproject`` defines/undefines.

    Entries containing an unresolved ``${...}`` are NOT known -- they are listed in ``unresolved``
    (such a variable may define symbols we cannot see). Explicit ``--define``/``--undefine`` are
    added on top. The rest of partial mode (unifdef + conditional walk) then runs with this seed."""
    project = Path(cproject).expanduser().resolve()
    entries = project_facts.cproject_builds(project)
    if build_name is None:
        print(f"{ASK_USER}: --from-cproject needs --build NAME; builds: {[e['name'] for e in entries]}",
              file=sys.stderr)
        sys.exit(EXIT_ERROR)
    entry = next((e for e in entries if e["name"] == build_name), None)
    if entry is None:
        print(f"{ASK_USER}: unknown build {build_name!r} in {project / '.cproject'}; builds: "
              f"{[e['name'] for e in entries]}", file=sys.stderr)
        sys.exit(EXIT_ERROR)

    # Known symbols: the concrete (unresolved-${}) defines plus the undefines. Unresolved ${...} go
    # to ``unresolved`` and are deliberately NOT treated as known.
    known: set[str] = set()
    for d in entry.get("defines", []):
        if "${" not in d:
            known.add(d)
    for u in entry.get("undefines", []):
        known.add(u)
    unresolved = _unresolved(entry.get("defines", []))

    # Apply command-line --define/--undefine on top. Undefines are removed from ``known`` so unifdef
    # actually drops them (a command-line --undefine overrides a cproject --define).
    for k in defines:
        known.add(k)
    for u in undefs:
        known.add(u)
        known.discard(u)
    known -= undefs

    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = Path.cwd() / source_path
    source_path = source_path.resolve()

    data = _run_partial_from(source_path, known, undefs, lo, hi)
    data["level"] = "partial"
    data["command"] = None
    data["unresolved"] = unresolved
    data["defines_source"] = "cproject (inferred) + command line" if (defines or undefs) else "cproject (inferred)"
    return data


def _run_partial_from(source_path: Path, known: set[str], undefs: set[str],
                      lo: int | None, hi: int | None) -> dict:
    """Run unifdef and the conditional walk with a precomputed ``known`` symbol set.

    Shared by plain ``--partial`` (``known`` = command-line defines plus undefines) and
    ``--from-cproject`` (``known`` = the cproject seed plus command line). ``undefs`` are the symbols
    explicitly undefined (so unifdef drops them) and ``known`` are the symbols treated as defined.
    Returns the full result dict minus the ``defines_source`` / ``unresolved`` fields the caller
    sets."""
    source_lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(source_lines)

    # Build the unifdef argument list from ``known``: undefines first (-U), then defines (-D).
    args = ["unifdef", "-b"]
    for name in sorted(undefs):
        args.append(f"-U{name}")
    for name in sorted(known):
        args.append(f"-D{name}")
    args.append(str(source_path))

    proc = _run(args, timeout=120)
    if proc.returncode not in (0, 1):
        print(f"{ASK_USER}: unifdef error: {proc.stderr.strip() or proc.stdout.strip()}", file=sys.stderr)
        sys.exit(EXIT_ERROR)

    out_lines = proc.stdout.splitlines()
    # unifdef -b keeps blank lines for removed content, so output line N corresponds to source line N.
    kept: list[bool] = [False] * (total + 1)  # 1-indexed; True when output line N is non-blank
    for idx, line in enumerate(out_lines, start=1):
        if idx > total:
            break
        kept[idx] = bool(line.strip())

    def cond_tokens(expr: str) -> list[str]:
        """Identifier tokens in a #if/#elif expression, ignoring ``defined``."""
        expr = re.sub(r"defined\s*\(\s*\w+\s*\)", " ", expr)  # drop defined(X)
        return re.findall(r"\b[A-Za-z_]\w*\b", expr)

    def unknown_in(expr: str) -> list[str]:
        """Unknown symbols referenced in ``expr`` (not known, not an explicit undef, not a number,
        not ``defined``)."""
        found: list[str] = []
        for tok in cond_tokens(expr):
            if tok in known or tok in undefs or tok == "defined":
                continue
            if re.fullmatch(r"[-+]?0x[0-9a-fA-F]+|[-+]?\d+", tok):
                continue
            if tok not in found:
                found.append(tok)
        return found

    # Walk the source once, tracking conditional nesting, and record per-line whether the line lies
    # inside an unknown-controlled, not-yet-taken branch. A frame is (start_line, branch_unknown,
    # taken); branch_unknown is True if any branch in the #if chain references an unknown symbol.
    unknown: list[str] = []
    unknown_set: set[str] = set()

    def note_unknown(tok: str) -> None:
        if tok not in unknown_set:
            unknown_set.add(tok)
            unknown.append(tok)

    stack2: list[tuple[int, bool, bool]] = []
    unknown_controlled: list[bool] = [False] * (total + 2)
    for idx, raw in enumerate(source_lines, start=1):
        s = raw.strip()
        m_if = re.match(r"^#\s*(if|ifdef|ifndef)\b(.*)$", s)
        m_elif = re.match(r"^#\s*elif\b(.*)$", s)
        m_else = re.match(r"^#\s*else\b", s)
        m_endif = re.match(r"^#\s*endif\b", s)
        if m_if:
            expr = m_if.group(2).strip()
            unk = unknown_in(expr)
            for u in unk:
                note_unknown(u)
            parent_unknown = any(f[1] for f in stack2)
            branch_unknown = parent_unknown or bool(unk)
            stack2.append((idx, branch_unknown, False))
            continue
        if m_elif:
            if stack2:
                start, branch_unknown, taken = stack2.pop()
                expr = m_elif.group(1).strip()
                unk = unknown_in(expr)
                for u in unk:
                    note_unknown(u)
                new_branch_unknown = any(f[1] for f in stack2) or bool(unk)
                stack2.append((start, new_branch_unknown, taken or not new_branch_unknown))
            continue
        if m_else:
            if stack2:
                start, branch_unknown, taken = stack2.pop()
                # #else is unknown-controlled iff any branch in the #if chain was unknown-controlled.
                stack2.append((start, branch_unknown, taken or not branch_unknown))
            continue
        if m_endif:
            if stack2:
                stack2.pop()
            continue
        # Non-directive line: unknown-controlled iff some open branch is unknown-controlled and not
        # yet taken (unifdef kept it, but we cannot say it is built).
        unknown_controlled[idx] = any(f[1] and not f[2] for f in stack2)

    rows = []
    for src_line in range(1, total + 1):
        if not _in_range(src_line, lo, hi):
            continue
        kept_flag = kept[src_line]
        uc = unknown_controlled[src_line]
        if uc:
            # Under an unknown-controlled conditional: not built, regardless of what unifdef kept.
            compiled = None
        elif not kept_flag:
            # unifdef removed it (surrounding conditional not taken).
            compiled = False
        else:
            compiled = True
        rows.append({
            "line": src_line,
            "compiled": compiled,
            "expanded": source_lines[src_line - 1] if 0 <= src_line - 1 < total else None,
        })
    return {
        "file": _resolve(str(source_path)),
        "unknown_symbols": unknown,
        "lines": rows,
    }


# --------------------------------------------------------------------------- #
# level 3: partial (unifdef, command-line symbols only)
# --------------------------------------------------------------------------- #

def _level_partial(source: str, defines: dict[str, str | None], undefs: set[str],
                   lo: int | None, hi: int | None) -> dict:
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = Path.cwd() / source_path
    source_path = source_path.resolve()

    # ``known`` = command-line defines only; undefines are passed separately so unifdef drops them
    # (they must not also be passed as -D, which would re-define them).
    known = set(defines.keys())
    data = _run_partial_from(source_path, known, undefs, lo, hi)
    data["level"] = "partial"
    data["command"] = None
    data["unresolved"] = []
    data["defines_source"] = "command line"
    return data




# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    project_facts = _load_project_facts()
    platforms = _load_platforms()

    tokens = list(argv if argv is not None else sys.argv[1:])
    project_dir = None
    build_name = None
    sdk_make = None
    make_dir = None
    make_file = None
    from_cproject = None
    partial = False
    makevars: list[str] = []
    source = None
    lines_spec = None
    defines: dict[str, str | None] = {}
    undefs: set[str] = set()
    as_json = False

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--build":
            build_name = tokens[i + 1]; i += 2; continue
        if tok.startswith("--build="):
            build_name = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--sdk-make":
            sdk_make = tokens[i + 1]; i += 2; continue
        if tok.startswith("--sdk-make="):
            sdk_make = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--make-dir":
            make_dir = tokens[i + 1]; i += 2; continue
        if tok.startswith("--make-dir="):
            make_dir = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--make-file":
            make_file = tokens[i + 1]; i += 2; continue
        if tok.startswith("--make-file="):
            make_file = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--from-cproject":
            from_cproject = tokens[i + 1]; i += 2; continue
        if tok.startswith("--from-cproject="):
            from_cproject = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--make-var":
            makevars.append(tokens[i + 1]); i += 2; continue
        if tok == "--partial":
            partial = True; i += 1; continue
        if tok == "--define":
            spec = tokens[i + 1]; i += 2
            if "=" in spec:
                k, v = spec.split("=", 1)
                defines[k] = v
            else:
                defines[spec] = None
            continue
        if tok.startswith("--define="):
            spec = tok.split("=", 1)[1]
            if "=" in spec:
                k, v = spec.split("=", 1)
                defines[k] = v
            else:
                defines[spec] = None
            i += 1; continue
        if tok == "--undefine":
            undefs.add(tokens[i + 1]); i += 2; continue
        if tok.startswith("--undefine="):
            undefs.add(tok.split("=", 1)[1]); i += 1; continue
        if tok == "--lines":
            lines_spec = tokens[i + 1]; i += 2; continue
        if tok.startswith("--lines="):
            lines_spec = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--json":
            as_json = True; i += 1; continue
        if tok.startswith("-"):
            i += 1; continue
        # Positional: for --partial/--sdk-make/--make-dir the first is the source file, otherwise the
        # first is the project directory and the second is the source file.
        if partial or sdk_make is not None or make_dir is not None or from_cproject is not None:
            if source is None:
                source = tok
        elif project_dir is None:
            project_dir = tok
        elif source is None:
            source = tok
        i += 1

    if source is None:
        return _usage_and_exit()

    lo, hi = _parse_lines(lines_spec)

    try:
        if partial and from_cproject is not None:
            # Partial mode seeded from a .cproject build's defines/undefines.
            data = _level_from_cproject(project_facts, platforms, from_cproject, build_name,
                                        source, defines, undefs, lo, hi)
        elif partial:
            data = _level_partial(source, defines, undefs, lo, hi)
        elif make_dir is not None:
            data = _level_make(platforms, Path(make_dir).expanduser(), make_file, source, makevars, lo, hi)
        elif sdk_make is not None:
            data = _level_sdk_make(platforms, Path(sdk_make).expanduser(), source, makevars, lo, hi)
        elif build_name is not None and project_dir is not None:
            project = Path(project_dir).expanduser().resolve()
            data = _level_build(project_facts, platforms, project, build_name, source, lo, hi)
        else:
            return _usage_and_exit()
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
    raise SystemExit(main())
