#!/usr/bin/env python3
"""reg_decode.py -- decode a register value into its fields from a build's macro dump (T3).

A model never splits a register value into fields itself. The field macros come from the build's own
preprocessor macro dump (so the right SDK version and device are used):

* clang family:  `<compiler> <compile_command args> -E -dM probe.c`
                 (probe includes the --include headers).
* ti_legacy:     `<compiler> <args> --preproc_macros=<tmp>/m.txt probe.c`

Each needed macro value is evaluated with the build's own compiler via ``backends.evaluate`` (never
parsed out of ``((uint32_t)0x...U)`` text). Field naming styles are detected from the macros that
exist for the register prefix:

  a) ``<REG>_<FIELD>_MASK`` + ``<REG>_<FIELD>_OFS``      (AM13, MSPM0)   -> style "mask_ofs"
  b) ``<REG>_<FIELD>_MASK`` + ``<REG>_<FIELD>_SHIFT``    (CSL: AM26x, AM275x) -> style "mask_shift"
  c) ``<REG>_<FIELD>_M`` + ``<REG>_<FIELD>_S`` and single-bit fields as a bare ``<REG>_<FIELD>``
     bit value (C2000 hw_*.h)                              -> style "m_s"

Named values (styles a/b): every other ``<REG>_<FIELD>_<NAME>`` macro whose value lies inside the
field mask; the field's value is named when it equals one of them (field value 0 and a
``..._NAME = 0`` both name the field).

CLI: ``reg_decode.py PROJECT --build B [--source FILE] --include H [...] --register REG --value V
[--json]``; explicit mode as c_eval (``--compiler C --flags "..." [--include H ...]``).

JSON: ``{"register": REG, "value": int, "style": "mask_ofs"|"mask_shift"|"m_s",
"fields": [{"field", "mask", "shift", "value", "name": str|null}], "unassigned_bits": int}``
(fields sorted by shift; unassigned_bits = value & ~(OR of all masks)).

Unknown register (no field macros) -> exit 2. STALE etc. -> exit 3.

Standard library only. No chip or case names. Work in the tool's own folder; all compiler output
goes to /tmp.
"""
from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_UNKNOWN_REGISTER = 2
EXIT_STALE = 3

ASK_USER = "ASK USER"


# --------------------------------------------------------------------------- #
# project_facts / platforms / backends import (same layout as c_eval.py)
# --------------------------------------------------------------------------- #

def _load_project_facts():
    here = Path(__file__).resolve().parent / "project_facts.py"
    if not here.is_file():
        raise RuntimeError("project_facts.py not found")
    sys.path.insert(0, str(here.parent))
    import project_facts  # noqa: E402
    return project_facts


def _load_platforms():
    here = Path(__file__).resolve().parent / "platforms.py"
    if not here.is_file():
        raise RuntimeError("platforms.py not found")
    if str(here.parent) not in sys.path:
        sys.path.insert(0, str(here.parent))
    import platforms  # noqa: E402
    return platforms


def _load_backends():
    here = Path(__file__).resolve().parent / "backends"
    if not (here / "__init__.py").is_file():
        raise RuntimeError("backends package not found")
    if str(here.parent) not in sys.path:
        sys.path.insert(0, str(here.parent))
    import backends  # noqa: E402
    return backends


# --------------------------------------------------------------------------- #
# build / staleness resolution (mirrors c_eval.py)
# --------------------------------------------------------------------------- #

def _resolve_project(project_facts, platforms, project, build_name, host_os):
    """Return (build_dir, compiler, flag_string, family) or exit 3 for stale / not built / FOREIGN."""
    installed = project_facts.inventory([project_facts.DEFAULT_ROOT])
    headers = project_facts.syscfg_headers(project)
    entries = project_facts.cproject_builds(project)
    if not entries:
        print(f"{ASK_USER}: no .cproject with builds in {project}", file=sys.stderr)
        sys.exit(EXIT_STALE)
    facts_by_name = {e["name"]: project_facts.build_facts(project, e, headers, installed, host_os)
                     for e in entries}
    if build_name not in facts_by_name:
        print(f"{ASK_USER}: unknown build {build_name!r}; builds: {[e['name'] for e in entries]}",
              file=sys.stderr)
        sys.exit(EXIT_STALE)
    facts = facts_by_name[build_name]
    build_dir = project / build_name
    if not facts["built"]:
        print(f"{ASK_USER}: {build_name} was never built; build it in CCS then run reg_decode again",
              file=sys.stderr)
        sys.exit(EXIT_STALE)
    for item in facts["foreign"]:
        print(f"{ASK_USER}: {build_name} was generated on {item['os']} ({item['path']}); rebuild on this machine",
              file=sys.stderr)
        sys.exit(EXIT_STALE)
    if facts["stale"]:
        print(f"{ASK_USER}: {facts['stale'][0]}; rebuild {build_name} in CCS, then run reg_decode again",
              file=sys.stderr)
        sys.exit(EXIT_STALE)
    compiler, flag_args = project_facts.compile_command(project, build_name, source=None)
    family = platforms.for_compiler(compiler)["family_kind"]
    return build_dir, compiler, " ".join(flag_args), family


# --------------------------------------------------------------------------- #
# macro dump
# --------------------------------------------------------------------------- #

def _shlex_split(text):
    try:
        import shlex
        return shlex.split(text)
    except Exception:
        return text.split()


def _parse_macro_defs(text):
    """Parse ``#define NAME value`` lines (from ``-dM`` or ``--preproc_macros``) into ``{NAME: value_str}``."""
    defs = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("#define"):
            continue
        m = re.match(r"^#define\s+(\w+)(?:\s*\([^)]*\))?\s+(.*)$", line, re.S)
        if not m:
            continue
        body = re.sub(r"//.*$", "", m.group(2)).strip()
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S).strip()
        # ``-dM`` / ``--preproc_macros`` wrap every value in an outer ``((type)...)`` pair, so the
        # parens are balanced before the value is handed to the compiler for evaluation (the value
        # is never parsed out of text by hand -- it is always evaluated with the build's compiler).
        body = _balance_parens(body)
        defs[m.group(1)] = body
    return defs


def _balance_parens(body):
    """Balance a macro value body by wrapping it so the paren count closes (``((type)X)``)."""
    depth = body.count("(") - body.count(")")
    if depth > 0:
        return "(" * depth + body
    if depth < 0:
        return body + ")" * (-depth)
    return body


def _find_include(header, flag_string, workdir):
    """Resolve a --include header to an existing file.

    The header is given by name (e.g. ``common.h`` / ``hw_epwm.h``). It is matched directly first
    (a path on disk or relative to the build dir), then searched recursively inside every include
    directory the build's own flags carry (``-I`` for clang, ``--include_path=`` for ti_legacy)."""
    p = Path(header)
    if p.is_file():
        return str(p)
    cand = Path(workdir) / header
    if cand.is_file():
        return str(cand)
    inc_dirs = []
    for tok in _shlex_split(flag_string):
        if tok.startswith("-I"):
            inc_dirs.append(tok[2:])
        elif tok.startswith("--include_path="):
            inc_dirs.append(tok.split("=", 1)[1])
    for d in inc_dirs:
        base = Path(d)
        if not base.is_dir():
            continue
        for match in sorted(base.rglob(header.split("/")[-1])):
            if match.is_file():
                return str(match)
    return None


def _dump_macros(compiler, flag_string, includes, family, workdir):
    """Return the build's macro definitions as ``{NAME: value_str}``.

    clang family runs ``-E -dM``; ti_legacy runs ``--preproc_macros=<tmp>/m.txt``. The probe includes
    the first --include header; each --include header's own directory is added to the include search
    path so its transitive includes resolve."""
    with tempfile.TemporaryDirectory(prefix="reg_decode_mp_") as tmp:
        work = Path(tmp)
        probe = work / "probe.c"
        # Resolve each --include header to a real file and add its directory to the search path.
        resolved = [_find_include(h, flag_string, workdir) for h in includes]
        if resolved:
            probe.write_text('#include "%s"\nint main(void){return 0;}\n' % Path(resolved[0]).name,
                             encoding="utf-8")
        else:
            probe.write_text("int main(void){return 0;}\n", encoding="utf-8")
        flags = _shlex_split(flag_string)
        inc_flags = []
        for r in resolved:
            if r:
                d = str(Path(r).parent)
                inc_flags.append(("--include_path=" if family == "ti_legacy" else "-I") + d)
        text = ""
        if family == "ti_legacy":
            macro_file = str(work / "m.txt")
            cmd = [compiler] + flags + inc_flags + ["--preproc_macros=" + macro_file, str(probe)]
            proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True, timeout=300)
            mp = Path(macro_file)
            if mp.is_file():
                text = mp.read_text(encoding="utf-8", errors="replace")
        else:
            cmd = [compiler] + flags + inc_flags + ["-E", "-dM", str(probe)]
            proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True, timeout=300)
            text = proc.stdout or ""
        return _parse_macro_defs(text)


# --------------------------------------------------------------------------- #
# field detection / decode
# --------------------------------------------------------------------------- #

KEYWORDS = ("MASK", "OFS", "SHIFT", "M", "S")


def _suffix_word(rest):
    """For a macro name suffix ``rest`` (after ``<REG>_``) return ``(field, keyword)`` or ``None``.

    ``COC_MASK`` -> field ``COC``, keyword ``MASK``; ``CTRMODE_M`` -> field ``CTRMODE``, keyword ``M``.
    A bare ``rest`` (e.g. ``CTRMODE`` with no keyword) returns ``None`` (handled as a single-bit bit)."""
    m = re.match(r"^_?([A-Za-z][A-Za-z0-9_]*)$", rest)
    if not m:
        return None
    whole = m.group(1)
    # A keyword alone (``MASK``, ``M``) has no field part -> not a field macro here.
    if whole in KEYWORDS:
        return None
    # ``<FIELD>_KEYWORD`` or ``<FIELD>KEYWORD`` (e.g. ``CTRMODE_M``).
    for kw in KEYWORDS:
        if whole.endswith("_" + kw):
            return (whole[: -(len(kw) + 1)], kw)
        if whole.endswith(kw) and len(whole) > len(kw):
            return (whole[: -len(kw)], kw)
    return None


def _eval_one(body, compiler, flag_string, includes, workdir):
    """Evaluate one macro value body to an int with the build's own compiler."""
    t = body.strip().rstrip("uULfF")
    if re.fullmatch(r"(0[xX][0-9a-fA-F]+|[0-9]+)", t):
        try:
            return int(t, 16) if t.lower().startswith("0x") else int(t, 10)
        except ValueError:
            pass
    try:
        return backends_evaluate(compiler, flag_string, [t], includes, workdir)[0]
    except Exception:
        return _int_value(t)


def _int_value(text):
    t = (text or "").strip()
    if not t:
        return None
    try:
        return int(t, 16) if t.lower().startswith("0x") else int(t, 10)
    except ValueError:
        return None


def decode_register(reg, value, defs, compiler, flag_string, includes, workdir):
    """Decode ``value`` for register ``reg`` using macro dump ``defs``.

    Return the result dict, or ``None`` when the register has no field macros (unknown register)."""
    # Collect fields: FIELD -> {keyword: body}.
    fields = {}
    bare_bits = {}  # FIELD -> body (a bare <REG>_<FIELD> bit macro, style m_s only)
    for name in defs:
        prefix = reg + "_"
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix):]
        sw = _suffix_word(rest)
        if sw:
            field, keyword = sw
            fields.setdefault(field, {})[keyword] = defs[name]
        else:
            # A bare macro <REG>_<FIELD> whose value is a single bit (style m_s).
            field = rest.split("_", 1)[0]
            bare_bits.setdefault(field, defs[name])

    # Detect the style.
    style = None
    if fields and all(f in ("M", "S") or set(f.keys()) <= {"M", "S"} for f in fields.values()):
        # every field macro uses only M/S keywords
        if fields and any("M" in f or "S" in f for f in fields.values()):
            style = "m_s"
    if style is None and fields:
        has_mask = any("MASK" in f for f in fields.values())
        if has_mask:
            if any("OFS" in f for f in fields.values()):
                style = "mask_ofs"
            elif any("SHIFT" in f for f in fields.values()):
                style = "mask_shift"

    if style is None:
        return None

    # Build field entries.
    entries = []  # (field, mask, shift)
    if style == "m_s":
        for field, kw in fields.items():
            m_body = kw.get("M")
            s_body = kw.get("S")
            if m_body is None:
                continue
            mask = _eval_one(m_body, compiler, flag_string, includes, workdir)
            shift = _eval_one(s_body, compiler, flag_string, includes, workdir) if s_body is not None else 0
            if mask is None or shift is None:
                continue
            entries.append((field, mask, shift))
        # single-bit bare macros: a 1-bit field at the given bit position.
        for field, body in bare_bits.items():
            # A bare <REG>_<FIELD> bit macro carries the bit *value* itself (e.g. PRDLD = 0x8 is bit 3).
            bval = _eval_one(body, compiler, flag_string, includes, workdir)
            if bval is None or bval <= 0:
                continue
            if (bval & (bval - 1)) != 0:  # not a single bit
                continue
            entries.append((field, bval, bval.bit_length() - 1))
    else:
        for field, kw in fields.items():
            if "MASK" not in kw:
                continue
            mask = _eval_one(kw["MASK"], compiler, flag_string, includes, workdir)
            if mask is None:
                continue
            if style == "mask_ofs" and "OFS" in kw:
                shift = _eval_one(kw["OFS"], compiler, flag_string, includes, workdir)
            elif style == "mask_shift" and "SHIFT" in kw:
                shift = _eval_one(kw["SHIFT"], compiler, flag_string, includes, workdir)
            else:
                shift = 0
            if shift is None:
                shift = 0
            entries.append((field, mask, shift))

    # Field name -> shift, so named values (raw masked words) can be shifted to compare.
    shift_pos = {f[0]: f[2] for f in entries}

    # Named values: <REG>_<FIELD>_<NAME> whose evaluated value lies inside the field mask.
    named = {}  # FIELD -> list of (value, name)
    for name in defs:
        prefix = reg + "_"
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix):]
        parts = rest.split("_", 1)
        if len(parts) < 2:
            continue
        field_word, name_word = parts
        if field_word not in shift_pos:
            continue
        if name_word in ("MASK", "OFS", "SHIFT", "M", "S"):
            continue
        nval = _eval_one(defs[name], compiler, flag_string, includes, workdir)
        # Named values are raw masked words; shift them to compare against the field's shifted value.
        if nval is not None:
            nval = nval >> shift_pos[field_word]
        named.setdefault(field_word, []).append((nval, name_word))

    result_fields = []
    for field, mask, shift in entries:
        # The field's value is the shifted field bits (0, 1, 2, ...), not the raw masked word.
        fval = (value & mask) >> shift
        name_val = None
        for nval, nname in named.get(field, []):
            if nval is not None and fval == nval:
                name_val = nname
                break
        result_fields.append({"field": field, "mask": mask, "shift": shift,
                              "value": fval, "name": name_val})

    result_fields.sort(key=lambda f: f["shift"] if f["shift"] is not None else 0)
    or_masks = 0
    for f in result_fields:
        or_masks |= f["mask"]
    return {
        "register": reg,
        "value": value,
        "style": style,
        "fields": result_fields,
        "unassigned_bits": value & ~or_masks,
    }


# --------------------------------------------------------------------------- #
# backends.evaluate reference (bound after import)
# --------------------------------------------------------------------------- #

backends_evaluate = None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def emit(data):
    print(json.dumps(data, indent=1))


def main(argv=None):
    global backends_evaluate
    project_facts = _load_project_facts()
    platforms = _load_platforms()
    backends = _load_backends()
    backends_evaluate = backends.clang.evaluate

    tokens = list(argv if argv is not None else sys.argv[1:])
    project = None
    build_name = None
    source = None
    compiler = None
    flags = ""
    includes = []
    reg = None
    value = None
    as_json = False

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--build":
            build_name = tokens[i + 1]; i += 2; continue
        if tok.startswith("--build="):
            build_name = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--source":
            source = tokens[i + 1]; i += 2; continue
        if tok.startswith("--source="):
            source = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--include":
            includes.append(tokens[i + 1]); i += 2; continue
        if tok.startswith("--include="):
            includes.append(tok.split("=", 1)[1]); i += 1; continue
        if tok == "--register":
            reg = tokens[i + 1]; i += 2; continue
        if tok.startswith("--register="):
            reg = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--value":
            value = tokens[i + 1]; i += 2; continue
        if tok.startswith("--value="):
            value = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--compiler":
            compiler = tokens[i + 1]; i += 2; continue
        if tok.startswith("--compiler="):
            compiler = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--flags":
            flags = tokens[i + 1]; i += 2; continue
        if tok.startswith("--flags="):
            flags = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--json":
            as_json = True; i += 1; continue
        if not tok.startswith("-"):
            project = tok; i += 1; continue
        i += 1

    if value is None:
        print("reg_decode: --value is required", file=sys.stderr)
        return EXIT_UNKNOWN_REGISTER
    try:
        value_int = int(value, 0)
    except ValueError:
        print("reg_decode: bad value %r" % value, file=sys.stderr)
        return EXIT_UNKNOWN_REGISTER

    host_os = platform.system()

    if compiler is None:
        if not project or not build_name:
            print("reg_decode: PROJECT --build B required", file=sys.stderr)
            return EXIT_STALE
        build_dir, compiler, flag_string, family = _resolve_project(project_facts, platforms, Path(project), build_name, host_os)
        workdir = str(build_dir)
    else:
        workdir = "."
        family = platforms.for_compiler(compiler)["family_kind"]

    # Pick the evaluate backend for this compiler family (clang vs ti_legacy).
    backends_evaluate = backends.clang.evaluate if family == "clang" else backends.ti_legacy.evaluate

    defs = _dump_macros(compiler, flag_string, includes, family, workdir)
    result = decode_register(reg, value_int, defs, compiler, flag_string, includes, workdir)
    if result is None:
        print("reg_decode: no field macros for register %r" % reg, file=sys.stderr)
        return EXIT_UNKNOWN_REGISTER

    emit(result)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
