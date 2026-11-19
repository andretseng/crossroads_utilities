#!/usr/bin/env python3
"""backends/clang.py -- clang-family record-layout back end (D10).

Drives a clang-family compiler (tiarmclang, c29clang) with ``-Xclang -fdump-record-layouts`` and
parses the ``AST Record Layout`` table. Units are bytes. Raises ``CompileError`` on a compiler
error or unparseable output.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from . import CompileError


# clang family compilers emit the record-layout dump with this marker line.
_DUMP_MARKER = "*** Dumping AST Record Layout"


def _shlex_split(text: str) -> list[str]:
    import shlex
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _write_probe(directory: Path, type_name: str, includes: list[str]) -> Path:
    """Write the probe .c that declares one variable of ``NAME``.

    A wrapper struct -- ``struct __probe_wrap { NAME v; };`` -- makes a *missing* type fail to
    compile (so the front end exits 2) instead of silently resolving to another record, and makes
    the wrapped record print inline under ``v`` even for ``typedef struct Tag {...} NAME;``.
    """
    lines = ["#include <stdint.h>"]
    for header in includes:
        lines.append(f'#include "{header}"')
    lines.append(f"struct __probe_wrap {{ {type_name} v; }};")
    path = directory / "probe.c"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run(compiler: str, args: str, probe: Path, workdir: Path) -> str:
    """Run the clang record-layout dump; return stdout (stderr merged for error reporting)."""
    cmd = [compiler]
    if args:
        cmd += _shlex_split(args)
    cmd += ["-Xclang", "-fdump-record-layouts", "-emit-llvm", "-S", "-o", "/dev/null", str(probe)]
    proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True)
    return proc.stdout + proc.stderr


def _block_size(block: str) -> int | None:
    match = re.search(r"\[sizeof=(\d+)", block)
    return int(match.group(1)) if match else None


def _collect_members(rows: list[tuple], start: int, target_indent: int, base_offset: int,
                     member_abs_offset: int | None = None) -> list[dict]:
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
        name = _member_name(type_text)
        member_offset = base_offset + (offset - member_abs_offset if member_abs_offset is not None else offset)
        # An anonymous struct/union member: flatten its nested members at its own offset.
        if "anonymous" in type_text:
            # Nested rows sit at indent + 1; their dump offsets are absolute, so make them relative
            # to this member's own offset before adding ``base_offset``.
            nested = _collect_members(rows, index, indent + 2, member_offset, offset)
            members.extend(nested)
            # Skip the nested rows we just consumed (they sit deeper than target_indent until the next
            # direct member).
            while index < len(rows) and rows[index][0] > target_indent:
                index += 1
            continue
        members.append({"name": name, "offset": member_offset,
                        "bit_lsb": bit_lsb, "bit_size": bit_size})
    return members


def _member_name(rest: str) -> str:
    """Extract the member name from a clang table row (``uint16_t c:3`` -> ``c``; ``struct X::(...) bit`` -> ``bit``)."""
    # The type text may carry an elaborated-type prefix and an ``(unnamed at file:line)`` annotation
    # before the member name (``struct RegU::(unnamed at …) bit``); the member name is the *last*
    # identifier anywhere in the row, before any trailing bitfield colon.
    ids = re.findall(r"\b([A-Za-z_]\w*)", rest)
    if not ids:
        return rest.split()[-1] if rest.split() else rest
    return ids[-1].rstrip(",:")


def _layout_block(output: str, type_name: str) -> str | None:
    """Return the ``AST Record Layout`` block for the wrapped type ``type_name``.

    The probe is ``struct __probe_wrap { NAME v; };``, so clang dumps each referenced record once,
    in dependency order (inner types first, the wrapped type last). The record for the asked type is
    therefore the *last* table -- the outermost record, expanded inline under ``v``. For a plain
    typedef that table is keyed on the name (``U``); for ``typedef struct Tag {...} NAME;`` it is
    keyed on the underlying tag (``struct Tag``). Matching by "last table with member rows" avoids
    grabbing an inner record (e.g. ``Inner`` when asked for ``Outer``)."""
    blocks = output.split(_DUMP_MARKER)
    chosen = None
    for block in blocks[1:]:
        # Table runs from this marker to the next ``*** `` section header.
        next_section = block.find("\n*** ", 1)
        table = block if next_section < 0 else block[:next_section]
        # A real record has at least one ``<offset> | <member>`` data row (not the summary/header).
        if re.search(r"^\s*\d+(?::\d+-\d+)?\s+\|\s+\S", table, re.M):
            chosen = table
    return chosen


def record_layout(compiler: str, args: str, type_name: str, includes: list[str], workdir: Path) -> dict:
    """Measure ``type_name`` with a clang-family compiler; return ``{"size", "members"}``.

    Raises ``CompileError`` (the front end exits 2 and prints it) on a compiler error or unparseable
    output -- never silently returns another record's layout."""
    with tempfile.TemporaryDirectory(prefix="struct_layout_clang_") as tmp:
        work = Path(tmp)
        probe = _write_probe(work, type_name, includes)
        output = _run(compiler, args, probe, work)
        # A missing type fails to compile; the wrapper makes clang stop with an ``error:`` line, so a
        # bare/unknown name must never fall back to some other dumped record.
        if re.search(r"\berror:\s", output) or "unknown type name" in output:
            raise CompileError("clang record layout for %r not found (compile error)" % type_name)
        block = _layout_block(output, type_name)
        if block is None:
            raise CompileError("clang record layout for %r not found" % type_name)
        # Parse the table into rows: (indent, offset, bit_lsb, bit_size, type_text). The header row
        # and the ``[sizeof=...]`` summary are not rows.
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
            raise CompileError("clang layout for %r has no rows" % type_name)
        header_indent = rows[0][0]
        size = _block_size(block)
        if size is None:
            raise CompileError("clang layout for %r has no [sizeof=...]" % type_name)
        members = _collect_members(rows, header_indent, header_indent + 2, 0)
        return {"unit": "byte", "size": size, "members": members}
