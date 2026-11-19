#!/usr/bin/env python3
"""backends/ti_legacy.py -- TI-legacy DWARF record-layout back end (D10).

Drives a TI-legacy compiler (cl2000/cl7x/clpru) with ``-g -k`` and reads the DWARF in the produced
``.asm``. Units are 16-bit words for cl2000, bytes for cl7x/clpru. Raises ``CompileError`` on a
compiler error or unparseable output.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from . import CompileError


# TI legacy DWARF attributes, as they appear in the .asm text.
ATTR_NAME = re.compile(r'DW_AT_name\("([^"]*)"\)')
ATTR_BYTE_SIZE = re.compile(r'DW_AT_byte_size\((0x[0-9A-Fa-f]+|[0-9]+)\)')
ATTR_MEMBER_LOC = re.compile(r'DW_AT_data_member_location\[DW_OP_plus_uconst (0x[0-9A-Fa-f]+|[0-9]+)\]')
ATTR_BIT_OFFSET = re.compile(r'DW_AT_bit_offset\((0x[0-9A-Fa-f]+|[0-9]+)\)')
ATTR_BIT_SIZE = re.compile(r'DW_AT_bit_size\((0x[0-9A-Fa-f]+|[0-9]+)\)')
ATTR_TYPE_REF = re.compile(r'DW_AT_type\(\*(\$[A-Za-z0-9$]+)\)')


def _shlex_split(text: str) -> list[str]:
    import shlex
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _hexint(text: str) -> int:
    return int(text, 16) if text.lower().startswith("0x") else int(text)


def _write_probe(directory: Path, type_name: str, includes: list[str]) -> Path:
    """Write the probe .c that declares one bare variable of ``NAME``.

    TI legacy emits the type's DWARF only for a *bare* variable (``NAME NAME_probe_instance;``); a
    wrapper struct is left out of the .asm entirely. The missing-type guard is handled by
    ``_find_struct`` following ``DW_TAG_typedef NAME -> DW_AT_type``.
    """
    lines = ["#include <stdint.h>"]
    for header in includes:
        lines.append(f'#include "{header}"')
    lines.append(f"{type_name} {type_name}_probe_instance;")
    path = directory / "probe.c"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run(compiler: str, args: str, probe: Path, workdir: Path) -> tuple[str, Path]:
    """Compile with -g -k into a temp dir; return (asm_text, asm_path)."""
    asm_dir = workdir / "asm"
    obj_dir = workdir / "obj"
    asm_dir.mkdir(exist_ok=True)
    obj_dir.mkdir(exist_ok=True)
    cmd = [compiler]
    if args:
        cmd += _shlex_split(args)
    cmd += ["-g", "-k", f"--asm_directory={asm_dir}", f"--obj_directory={obj_dir}", "-c", str(probe)]
    proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True)
    asm_files = sorted(asm_dir.glob("*.asm"))
    if not asm_files:
        # Fall back to the obj dir name if the asm extension differs.
        asm_files = sorted(asm_dir.glob("*"))
    if not asm_files:
        raise CompileError("TI legacy produced no .asm (compiler stderr below):\n" + proc.stderr)
    return (asm_files[0].read_text(encoding="utf-8", errors="replace"), asm_files[0])


def _find_struct(asm_text: str, type_name: str) -> tuple[str | None, int | None]:
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


def _parse_type_members(asm_text: str, type_tag: str, unit_bits: int) -> list[dict]:
    """Return the direct members of the struct/union named ``type_tag`` (declaration order).

    Used both for a top-level struct and for an anonymous member's nested type (flattening)."""
    lines = asm_text.splitlines()
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


def _parse_members(asm_text: str, struct_tag: str, unit_bits: int) -> list[dict]:
    """Return the members of ``struct_tag`` in declaration order.

    TI legacy DWARF nests a structure's ``DW_TAG_member`` blocks between the struct's own
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
                    nested = _parse_type_members(asm_text, attrs["type_ref"], unit_bits)
                    members.extend(({"name": n["name"], "offset": offset + n["offset"],
                                     "bit_lsb": n["bit_lsb"], "bit_size": n["bit_size"]}
                                    for n in nested))
                else:
                    members.append({"name": name, "offset": offset, "bit_lsb": bit_lsb, "bit_size": bit_size})
            index = j
        else:
            index += 1
    return members


def record_layout(compiler: str, args: str, type_name: str, includes: list[str], workdir: Path) -> dict:
    """Measure ``type_name`` with a TI-legacy compiler; return ``{"size", "members"}``.

    The unit is 16-bit words for cl2000 and bytes for cl7x/clpru; the back end decides from the
    compiler file name so the front end never passes it. Raises ``CompileError`` (the front end exits
    2 and prints it) when the type is not found or the output is unparseable -- never silently
    returns another record's layout."""
    unit_bits = 16 if "cl2000" in Path(compiler).name.lower() else 8
    with tempfile.TemporaryDirectory(prefix="struct_layout_ti_") as tmp:
        work = Path(tmp)
        probe = _write_probe(work, type_name, includes)
        asm_text, _asm_path = _run(compiler, args, probe, work)
        struct_tag, byte_size = _find_struct(asm_text, type_name)
        if struct_tag is None:
            raise CompileError("DWARF structure for %r not found" % type_name)
        members = _parse_members(asm_text, struct_tag, unit_bits)
        if byte_size is None:
            raise CompileError("DWARF structure %r has no DW_AT_byte_size" % type_name)
        return {"unit": "word16" if unit_bits == 16 else "byte", "size": byte_size, "members": members}
