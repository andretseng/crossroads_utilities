#!/usr/bin/env python3
"""struct_layout.py — measured struct layout.

A model never works out offsets, padding, sizeof or bit-field positions; the project's own
compiler, with the build's own flags, measures them.  Two compiler families are supported,
each with its own measurement and unit:

* clang family (tiarmclang, c29clang): ``<compiler> <flags> -Xclang -fdump-record-layouts
  -emit-llvm -S -o /dev/null probe.c`` prints an ``AST Record Layout`` table whose rows are
  ``<offset> | <type>`` for normal members and ``<offset>:<lsb>-<eb> | <type>`` for bitfields,
  plus ``[sizeof=N, align=A]``.  Units are bytes.
* TI legacy (cl2000, and the same way cl7x/clpru): the same sources compiled with
  ``-g -k --asm_directory=<tmp> --obj_directory=<tmp> -c probe.c`` then the DWARF lines in the
  ``.asm`` are read: ``DW_AT_name`` / ``DW_AT_data_member_location[DW_OP_plus_uconst N]`` /
  ``DW_AT_bit_offset`` / ``DW_AT_bit_size`` and the struct's ``DW_AT_byte_size``.  bit_offset is
  counted from the MSB of the storage unit, so lsb = unit_bits - bit_offset - bit_size.  Units
  are 16-bit words.

The compiler and flags come from project_facts.py (project mode) or are given explicitly.
Standard library only; no chip or case names.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_COMPILER_ERROR = 2
EXIT_ASK_USER = 3

ASK_USER = "ASK USER"

# clang family compilers emit the record-layout dump with this marker line.
# TI legacy DWARF attributes, as they appear in the .asm text.
ATTR_NAME = re.compile(r'DW_AT_name\("([^"]*)"\)')
ATTR_BYTE_SIZE = re.compile(r'DW_AT_byte_size\((0x[0-9A-Fa-f]+|[0-9]+)\)')
ATTR_MEMBER_LOC = re.compile(r'DW_AT_data_member_location\[DW_OP_plus_uconst (0x[0-9A-Fa-f]+|[0-9]+)\]')
ATTR_BIT_OFFSET = re.compile(r'DW_AT_bit_offset\((0x[0-9A-Fa-f]+|[0-9]+)\)')
ATTR_BIT_SIZE = re.compile(r'DW_AT_bit_size\((0x[0-9A-Fa-f]+|[0-9]+)\)')
ATTR_TYPE_REF = re.compile(r'DW_AT_type\(\*(\$[A-Za-z0-9$]+)\)')


# --------------------------------------------------------------------------- #
# project_facts import (build selection / staleness / FOREIGN / not-built)
# --------------------------------------------------------------------------- #

def _load_project_facts():
    # Prefer the copy that lives next to this script (the reviewed, self-contained one); fall back
    # to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared
    # project_facts has not yet been updated with compile_command.
    here = Path(__file__).resolve().parent / "project_facts.py"
    util = Path(os.path.expanduser("~/utilities/firmware/project_facts.py"))
    candidates = [here, util]
    for cand in candidates:
        if cand.is_file():
            sys.path.insert(0, str(cand.parent))
            break
    import project_facts  # noqa: E402
    return project_facts


# --------------------------------------------------------------------------- #
# compiler families
# --------------------------------------------------------------------------- #

def compiler_family(compiler_path: str) -> str:
    """Return the family token (armllvm / c29 / c2000 / c7000 / c6000 / pru) for a compiler."""
    name = Path(compiler_path).name.lower().removesuffix(".exe")
    return {"tiarmclang": "armllvm", "c29clang": "c29", "cl2000": "c2000",
            "cl7x": "c7000", "cl6x": "c6000", "clpru": "pru"}.get(name, name)


def unit_for_family(family: str) -> str:
    """cl2000/cl7x/clpru measure 16-bit words; the clang family measures bytes."""
    return "word16" if family in ("c2000", "c7000", "c6000", "pru") else "byte"


def is_clang_family(family: str) -> bool:
    return family in ("armllvm", "c29")


# --------------------------------------------------------------------------- #
# probe file
# --------------------------------------------------------------------------- #

def write_probe(directory: Path, type_name: str, includes: list[str], clang: bool) -> Path:
    """Write the probe .c that declares one variable of ``NAME``.

    **clang** uses a wrapper struct -- ``struct __probe_wrap { NAME v; };`` -- so a *missing* type
    fails to compile (exit 2) instead of silently resolving to another record, and so the wrapped
    record is printed inline under ``v`` even for ``typedef struct Tag {...} NAME;``.

    **TI legacy (cl2000/cl7x/clpru)** emits the type's DWARF only for a *bare* variable
    (``NAME NAME_probe_instance;``); a wrapper struct is left out of the .asm entirely. The missing-
    type guard for TI is handled by ``_ti_find_struct`` following ``DW_TAG_typedef NAME -> DW_AT_type``.
    """
    lines = ["#include <stdint.h>"]
    for header in includes:
        lines.append(f'#include "{header}"')
    if clang:
        lines.append(f"struct __probe_wrap {{ {type_name} v; }};")
    else:
        lines.append(f"{type_name} {type_name}_probe_instance;")
    path = directory / "probe.c"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# clang-family parser
# --------------------------------------------------------------------------- #

def run_clang(compiler: str, flags: str, probe: Path, workdir: Path) -> str:
    """Run the clang record-layout dump; return stdout (stderr merged for error reporting)."""
    cmd = [compiler]
    if flags:
        cmd += shlex_split(flags)
    cmd += ["-Xclang", "-fdump-record-layouts", "-emit-llvm", "-S", "-o", "/dev/null", str(probe)]
    proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True)
    return proc.stdout + proc.stderr


def parse_clang(output: str, type_name: str) -> dict:
    """Parse an ``AST Record Layout`` table for one struct into the JSON layout dict."""
    # A missing type fails to compile; the wrapper makes clang stop with an ``error:`` line, so a
    # bare/unknown name must never fall back to some other dumped record.
    if re.search(r"\berror:\s", output) or "unknown type name" in output:
        raise LayoutError("clang record layout for %r not found (compile error)" % type_name)
    block = _clang_layout_block(output, type_name)
    if block is None:
        raise LayoutError("clang record layout for %r not found" % type_name)
    # Parse the table into rows: (indent, offset, bit_lsb, bit_size, type_text). The header row and
    # the ``[sizeof=...]`` summary are not rows.
    rows: list[tuple[int, int, int | None, int | None, str]] = []
    for line in block.splitlines():
        header, _, rest = line.partition("|")
        indent = len(rest) - len(rest.lstrip())
        header = header.strip()
        rest = rest.strip()
        if not header and not rest:
            continue
        if re.search(r"\[sizeof=(\d+)", header + " " + rest):
            continue
        field = re.match(r"(\d+)(?::(\d+)-(\d+))?", header)
        if not field:
            continue
        rows.append((indent, int(field.group(1)),
                     int(field.group(2)) if field.group(2) is not None else None,
                     int(field.group(3)) - int(field.group(2)) + 1 if field.group(2) is not None else None,
                     rest))
    if not rows:
        raise LayoutError("clang layout for %r has no rows" % type_name)
    header_indent = rows[0][0]
    size = _clang_block_size(block)
    if size is None:
        raise LayoutError("clang layout for %r has no [sizeof=...]" % type_name)
    members = _clang_collect_members(rows, header_indent, header_indent + 2, 0)
    return {"unit": "byte", "size": size, "members": members}


def _clang_block_size(block: str) -> int | None:
    match = re.search(r"\[sizeof=(\d+)", block)
    return int(match.group(1)) if match else None


def _clang_collect_members(rows: list[tuple], start: int, target_indent: int, base_offset: int, member_abs_offset: int | None = None) -> list[dict]:
    """Collect the direct members of a record starting at ``rows[start]``.

    ``target_indent`` is the indent of a direct member (one level below the record header). A member
    whose type text names an *anonymous* struct/union (``union Mix::(anonymous at …)``) is flattened:
    its own members -- found at the next indent level -- are emitted at
    ``base_offset + (nested_offset - member_abs_offset)`` because C accesses them as ``x.<member>``
    directly. ``member_abs_offset`` is this member's own dump offset, used to make nested offsets
    relative (``None`` for a top-level record, so offsets are taken as-is).
    """
    members: list[dict] = []
    index = start
    while index < len(rows):
        indent, offset, bit_lsb, bit_size, type_text = rows[index]
        index += 1
        if indent < target_indent:
            # Returned to a shallower level (the record's own header or a sibling): stop.
            break
        if indent > target_indent:
            # Deeper than a direct member without an intervening anonymous header: skip (should not
            # happen in a well-formed dump).
            continue
        name = _clang_member_name(type_text)
        member_offset = base_offset + (offset - member_abs_offset if member_abs_offset is not None else offset)
        # An anonymous struct/union member: flatten its nested members at its own offset.
        if "anonymous" in type_text:
            # Nested rows sit at indent + 1; their dump offsets are absolute, so make them relative
            # to this member's own offset before adding ``base_offset``.
            nested = _clang_collect_members(rows, index, indent + 2, member_offset, offset)
            members.extend(nested)
            # Skip the nested rows we just consumed (they sit deeper than target_indent until the next
            # direct member).
            while index < len(rows) and rows[index][0] > target_indent:
                index += 1
            continue
        members.append({"name": name, "offset": member_offset,
                        "bit_lsb": bit_lsb, "bit_size": bit_size})
    return members


def _clang_layout_block(output: str, type_name: str) -> str | None:
    """Return the ``AST Record Layout`` block for the wrapped type ``type_name``.

    The probe is ``struct __probe_wrap { NAME v; };``, so clang dumps each referenced record once,
    in dependency order (inner types first, the wrapped type last). The record for the asked type is
    therefore the *last* table -- the outermost record, expanded inline under ``v``. For a plain
    typedef that table is keyed on the name (``U``); for ``typedef struct Tag {...} NAME;`` it is
    keyed on the underlying tag (``struct Tag``). Matching by "last table with member rows" avoids
    grabbing an inner record (e.g. ``Inner`` when asked for ``Outer``)."""
    blocks = output.split("*** Dumping AST Record Layout")
    chosen = None
    for block in blocks[1:]:
        # Table runs from this marker to the next ``*** `` section header.
        next_section = block.find("\n*** ", 1)
        table = block if next_section < 0 else block[:next_section]
        # A real record has at least one ``<offset> | <member>`` data row (not the summary/header).
        if re.search(r"^\s*\d+(?::\d+-\d+)?\s+\|\s+\S", table, re.M):
            chosen = table
    return chosen


def _clang_member_name(rest: str) -> str:
    """Extract the member name from a clang table row (``uint16_t c:3`` -> ``c``; ``struct X::(...) bit`` -> ``bit``)."""
    # The type text may carry an elaborated-type prefix and an ``(unnamed at file:line)`` annotation
    # before the member name (``struct RegU::(unnamed at …) bit``); the member name is the *last*
    # identifier anywhere in the row, before any trailing bitfield colon.
    ids = re.findall(r"\b([A-Za-z_]\w*)", rest)
    if not ids:
        return rest.split()[-1] if rest.split() else rest
    return ids[-1].rstrip(",:")


# --------------------------------------------------------------------------- #
# TI legacy (cl2000/cl7x/clpru) DWARF parser
# --------------------------------------------------------------------------- #

def run_ti_legacy(compiler: str, flags: str, probe: Path, workdir: Path) -> tuple[str, Path]:
    """Compile with -g -k into a temp dir; return (asm_text, asm_path)."""
    asm_dir = workdir / "asm"
    obj_dir = workdir / "obj"
    asm_dir.mkdir(exist_ok=True)
    obj_dir.mkdir(exist_ok=True)
    cmd = [compiler]
    if flags:
        cmd += shlex_split(flags)
    cmd += ["-g", "-k", f"--asm_directory={asm_dir}", f"--obj_directory={obj_dir}", "-c", str(probe)]
    proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True)
    asm_files = sorted(asm_dir.glob("*.asm"))
    if not asm_files:
        # Fall back to the obj dir name if the asm extension differs.
        asm_files = sorted(asm_dir.glob("*"))
    if not asm_files:
        raise LayoutError("cl2000 produced no .asm (compiler stderr below):\n" + proc.stderr)
    return (asm_files[0].read_text(encoding="utf-8", errors="replace"), asm_files[0])


def parse_ti_legacy(asm_text: str, type_name: str, unit_bits: int = 16) -> dict:
    """Parse the DWARF of one .asm into the JSON layout dict (units are 16-bit words)."""
    struct_tag, byte_size = _ti_find_struct(asm_text, type_name)
    if struct_tag is None:
        raise LayoutError("DWARF structure for %r not found" % type_name)
    members = _ti_parse_members(asm_text, struct_tag, unit_bits)
    if byte_size is None:
        raise LayoutError("DWARF structure %r has no DW_AT_byte_size" % type_name)
    return {"unit": "word16", "size": byte_size, "members": members}


def _ti_find_struct(asm_text: str, type_name: str) -> tuple[str | None, int | None]:
    """Return (struct_tag, byte_size) for the typedef/struct named ``type_name``.

    The name may be a ``DW_TAG_typedef`` (``typedef struct {...} U;``) whose ``DW_AT_type``
    points at the ``DW_TAG_structure_type``, or a ``DW_TAG_structure_type`` declared directly.
    """
    lines = asm_text.splitlines()
    # 1. Find the tag whose name is type_name and record its kind + DW_AT_type target.
    name_tag = None
    name_kind = None
    name_type_ref = None
    current_tag = None
    for index, line in enumerate(lines):
        tag_match = re.match(r"(\$[A-Za-z0-9$]+)\s+\.dwtag\s+(DW_TAG_\w+)", line)
        if tag_match:
            current_tag, current_type = tag_match.group(1), tag_match.group(2)
        name_match = ATTR_NAME.search(line)
        if name_match and current_tag and name_match.group(1) == type_name:
            name_tag = current_tag
            name_kind = current_type
            name_type_ref = None
        type_ref = ATTR_TYPE_REF.search(line)
        if type_ref and current_tag == name_tag and name_kind == "DW_TAG_typedef":
            name_type_ref = type_ref.group(1)
    if name_tag is None:
        return None, None
    # 2. Resolve to the struct tag: typedef -> DW_AT_type target; else the tag itself.
    struct_tag = name_type_ref if name_type_ref else name_tag
    # If the resolved tag is not a structure_type, scan for the structure_type with matching name.
    if not _tag_is_structure_type(lines, struct_tag):
        struct_tag = None
    byte_size = None
    if struct_tag is not None:
        for index, line in enumerate(lines):
            if re.match(rf"{re.escape(struct_tag)}\s+\.dwtag\s+DW_TAG_structure_type", line):
                for later in lines[index + 1:]:
                    bm = ATTR_BYTE_SIZE.search(later)
                    if bm:
                        byte_size = _hexint(bm.group(1))
                        break
                    if later.strip().startswith(".dwendtag"):
                        break
                break
    return struct_tag, byte_size


def _tag_is_structure_type(lines: list[str], tag: str | None) -> bool:
    if not tag:
        return False
    for line in lines:
        match = re.match(rf"{re.escape(tag)}\s+\.dwtag\s+DW_TAG_structure_type", line)
        if match:
            return True
    return False


def _ti_parse_members(asm_text: str, struct_tag: str, unit_bits: int) -> list[dict]:
    """Return the members of ``struct_tag`` in declaration order.

    cl2000's DWARF nests a structure's ``DW_TAG_member`` blocks between the struct's own
    ``.dwtag ... DW_TAG_structure_type`` line and its ``.dwendtag``; members of *other* types
    (other type units, nested unions) sit outside that block and must not be collected.

    An anonymous struct/union member is emitted by the compiler with an internal name such as
    ``$P$T0`` and a ``DW_AT_type`` pointing at the anonymous type; C accesses that type's members
    directly (``x.<member>``), so they are flattened at the anonymous member's offset.
    """
    lines = asm_text.splitlines()
    # Locate the target struct's opening .dwtag line, then scan until its .dwendtag.
    start = None
    for index, line in enumerate(lines):
        if re.match(rf"{re.escape(struct_tag)}\s+\.dwtag\s+DW_TAG_structure_type", line):
            start = index
            break
    if start is None:
        return []
    members: list[dict] = []
    index = start + 1
    while index < len(lines):
        row = lines[index]
        stripped = row.lstrip()
        if stripped.startswith(".dwendtag"):
            break
        tag_match = re.match(r"(\$[A-Za-z0-9$]+)\s+\.dwtag\s+DW_TAG_member", row)
        if tag_match:
            # Collect this member's attributes until the next .dwtag / .dwendtag line.
            attrs: dict[str, str] = {}
            j = index + 1
            while j < len(lines):
                nrow = lines[j]
                nstripped = nrow.lstrip()
                if nstripped.startswith(".dwendtag") or re.match(r"\$[A-Za-z0-9$]+\s+\.dwtag", nstripped):
                    break
                for key, pattern in (("name", ATTR_NAME), ("loc", ATTR_MEMBER_LOC),
                                     ("bit_offset", ATTR_BIT_OFFSET), ("bit_size", ATTR_BIT_SIZE),
                                     ("type_ref", ATTR_TYPE_REF)):
                    m = pattern.search(nrow)
                    if m and key not in attrs:
                        attrs[key] = m.group(1)
                j += 1
            name = attrs.get("name")
            if name:
                offset = _hexint(attrs["loc"]) if "loc" in attrs else 0
                if "bit_offset" in attrs and "bit_size" in attrs:
                    bit_offset = _hexint(attrs["bit_offset"])
                    bit_size = _hexint(attrs["bit_size"])
                    bit_lsb = unit_bits - bit_offset - bit_size
                else:
                    bit_lsb = None
                    bit_size = None
                # An anonymous member (compiler-internal ``$P$T0`` name): follow DW_AT_type to the
                # anonymous type and flatten its members at this member's offset.
                if name.startswith("$P$") and "type_ref" in attrs:
                    nested = _ti_parse_type(asm_text, attrs["type_ref"], unit_bits)
                    members.extend(({"name": n["name"], "offset": offset + n["offset"],
                                     "bit_lsb": n["bit_lsb"], "bit_size": n["bit_size"]}
                                    for n in nested))
                else:
                    members.append({"name": name, "offset": offset, "bit_lsb": bit_lsb, "bit_size": bit_size})
            index = j
        else:
            index += 1
    return members


def _ti_parse_type(asm_text: str, type_tag: str, unit_bits: int) -> list[dict]:
    """Return the direct members of the struct/union named ``type_tag`` (for anonymous flattening)."""
    lines = asm_text.splitlines()
    # Find the type's opening .dwtag line (DW_TAG_structure_type or DW_TAG_union_type).
    start = None
    for index, line in enumerate(lines):
        if re.match(rf"{re.escape(type_tag)}\s+\.dwtag\s+(DW_TAG_(?:structure|union)_type)", line):
            start = index
            break
    if start is None:
        return []
    members: list[dict] = []
    index = start + 1
    while index < len(lines):
        row = lines[index]
        stripped = row.lstrip()
        if stripped.startswith(".dwendtag"):
            break
        tag_match = re.match(r"(\$[A-Za-z0-9$]+)\s+\.dwtag\s+DW_TAG_member", row)
        if tag_match:
            attrs: dict[str, str] = {}
            j = index + 1
            while j < len(lines):
                nrow = lines[j]
                nstripped = nrow.lstrip()
                if nstripped.startswith(".dwendtag") or re.match(r"\$[A-Za-z0-9$]+\s+\.dwtag", nstripped):
                    break
                for key, pattern in (("name", ATTR_NAME), ("loc", ATTR_MEMBER_LOC),
                                     ("bit_offset", ATTR_BIT_OFFSET), ("bit_size", ATTR_BIT_SIZE)):
                    m = pattern.search(nrow)
                    if m and key not in attrs:
                        attrs[key] = m.group(1)
                j += 1
            name = attrs.get("name")
            if name:
                offset = _hexint(attrs["loc"]) if "loc" in attrs else 0
                if "bit_offset" in attrs and "bit_size" in attrs:
                    bit_offset = _hexint(attrs["bit_offset"])
                    bit_size = _hexint(attrs["bit_size"])
                    bit_lsb = unit_bits - bit_offset - bit_size
                else:
                    bit_lsb = None
                    bit_size = None
                members.append({"name": name, "offset": offset, "bit_lsb": bit_lsb, "bit_size": bit_size})
            index = j
        else:
            index += 1
    return members


def _hexint(text: str) -> int:
    return int(text, 16) if text.lower().startswith("0x") else int(text)


class LayoutError(Exception):
    """Raised when a compiler's output cannot be turned into a layout."""


def shlex_split(text: str) -> list[str]:
    import shlex
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_measure(compiler: str, flags: str, type_name: str, includes: list[str]) -> dict:
    """Measure one type and return the JSON layout dict (raises LayoutError on parse failure)."""
    family = compiler_family(compiler)
    unit = unit_for_family(family)
    with tempfile.TemporaryDirectory(prefix="struct_layout_") as tmp:
        workdir = Path(tmp)
        probe = write_probe(workdir, type_name, includes, is_clang_family(family))
        if is_clang_family(family):
            output = run_clang(compiler, flags, probe, workdir)
            if "AST Record Layout" not in output:
                # The compiler may have failed; surface its error verbatim.
                if output.strip() and "Record Layout" not in output:
                    raise LayoutError("__COMPILER_ERROR__\n" + output)
            return parse_clang(output, type_name)
        asm_text, _asm_path = run_ti_legacy(compiler, flags, probe, workdir)
        return parse_ti_legacy(asm_text, type_name)


# --------------------------------------------------------------------------- #
# project mode (via project_facts)
# --------------------------------------------------------------------------- #

def resolve_project(project_facts, project: Path, build_name: str):
    """Return build_dir, compiler, flags or exit 3 with ASK USER for stale / not built / foreign."""
    installed = project_facts.inventory([project_facts.DEFAULT_ROOT])
    headers = project_facts.syscfg_headers(project)
    entries = project_facts.cproject_builds(project)
    if not entries:
        print(f"{ASK_USER}: no .cproject with builds in {project}", file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    facts_by_name = {e["name"]: project_facts.build_facts(project, e, headers, installed,
                                                          __import__("platform").system()) for e in entries}
    if build_name not in facts_by_name:
        print(f"{ASK_USER}: unknown build {build_name!r}; builds: {[e['name'] for e in entries]}",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    facts = facts_by_name[build_name]
    build_dir = project / build_name
    if not facts["built"]:
        print(f"{ASK_USER}: {build_name} was never built; build it in CCS then run struct_layout again",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    for item in facts["foreign"]:
        print(f"{ASK_USER}: {build_name} was generated on {item['os']} ({item['path']}); rebuild on this machine",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    if facts["stale"]:
        print(f"{ASK_USER}: {facts['stale'][0]}; rebuild {build_name} in CCS, then run struct_layout again",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    return build_dir, facts


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def emit(data: dict) -> None:
    print(json.dumps(data, indent=1))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    project_facts = _load_project_facts()
    tokens = list(argv if argv is not None else sys.argv[1:])
    # Flat interface (no subcommands):
    #   struct_layout.py PROJECT_DIR --build NAME --type T [--include h ...] [--json]
    #   struct_layout.py --compiler PATH --flags ".." --type T [--include h ...] [--json]
    project_dir = None
    build_name = None
    compiler = None
    flags = ""
    type_name = None
    includes: list[str] = []
    as_json = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--build":
            build_name = tokens[i + 1]; i += 2; continue
        if tok.startswith("--build="):
            build_name = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--compiler":
            compiler = tokens[i + 1]; i += 2; continue
        if tok.startswith("--compiler="):
            compiler = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--flags":
            flags = tokens[i + 1]; i += 2; continue
        if tok.startswith("--flags="):
            flags = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--type":
            type_name = tokens[i + 1]; i += 2; continue
        if tok.startswith("--type="):
            type_name = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--include":
            includes.append(tokens[i + 1]); i += 2; continue
        if tok.startswith("--include="):
            includes.append(tok.split("=", 1)[1]); i += 1; continue
        if tok == "--json":
            as_json = True; i += 1; continue
        if tok.startswith("-"):
            # Unknown flag; skip it and its value if it looks like one.
            i += 1; continue
        if project_dir is None:
            project_dir = tok
        i += 1

    if type_name is None:
        print("usage: struct_layout.py [--compiler PATH --flags \"..\"] --type T [--include h ...] [--json]"
              " | struct_layout.py PROJECT_DIR --build NAME --type T [--include h ...] [--json]",
              file=sys.stderr)
        return EXIT_ASK_USER

    try:
        if compiler is not None:
            data = cmd_measure(compiler, flags or "", type_name, includes)
        else:
            if project_dir is None:
                print("usage: struct_layout.py PROJECT_DIR --build NAME --type T [--include h ...] [--json]",
                      file=sys.stderr)
                return EXIT_ASK_USER
            if build_name is None:
                print(f"{ASK_USER}: --build NAME required", file=sys.stderr)
                return EXIT_ASK_USER
            project = Path(project_dir).expanduser().resolve()
            build_dir, _facts = resolve_project(project_facts, project, build_name)
            # Compile command comes from project_facts (the build's real compile recipe, with any
            # @response file expanded), not from a makefile read here.
            comp, flgs = project_facts.compile_command(project, build_name)
            if not comp:
                print(f"{ASK_USER}: could not determine a compile command for {build_name}",
                      file=sys.stderr)
                return EXIT_ASK_USER
            data = cmd_measure(comp, " ".join(flgs), type_name, includes)
    except LayoutError as error:
        text = str(error)
        if text.startswith("__COMPILER_ERROR__"):
            # Compiler failed: print its error lines verbatim, exit 2.
            sys.stdout.write(text[len("__COMPILER_ERROR__"):].lstrip() + "\n")
            return EXIT_COMPILER_ERROR
        sys.stderr.write(str(error) + "\n")
        return EXIT_COMPILER_ERROR
    if as_json:
        emit(data)
    else:
        print(json.dumps(data, indent=1))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
