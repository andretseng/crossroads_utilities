#!/usr/bin/env python3
"""platforms.py — compiler-family table for struct_layout (D10).

One table keyed by compiler *family* (derived from the compiler file name, never from the chip)
maps to the measurement properties the front end needs:

* ``family_kind`` -- which back end drives the compiler (``"clang"`` or ``"ti_legacy"``);
* ``unit`` -- the size unit the compiler reports (``"byte"`` or ``"word16"``);
* ``endian`` -- the target endianness (informational; all supported targets are little-endian).

``for_compiler(path)`` decides the family from the compiler file name so the front end never has
to know the compiler-specific details itself.
"""
from __future__ import annotations

from pathlib import Path

# family token -> measurement properties.
_PLATFORMS = {
    # clang family: -Xclang -fdump-record-layouts, byte units.
    "armllvm": {"family_kind": "clang", "unit": "byte", "endian": "little"},
    "tiarmclang": {"family_kind": "clang", "unit": "byte", "endian": "little"},
    "c29": {"family_kind": "clang", "unit": "byte", "endian": "little"},
    # TI legacy: -g -k DWARF in the .asm, word16 (or byte) units.
    "c2000": {"family_kind": "ti_legacy", "unit": "word16", "endian": "little"},
    "cl2000": {"family_kind": "ti_legacy", "unit": "word16", "endian": "little"},
    "c7000": {"family_kind": "ti_legacy", "unit": "byte", "endian": "little"},
    "cl7x": {"family_kind": "ti_legacy", "unit": "byte", "endian": "little"},
    "pru": {"family_kind": "ti_legacy", "unit": "byte", "endian": "little"},
    "clpru": {"family_kind": "ti_legacy", "unit": "byte", "endian": "little"},
    "arm": {"family_kind": "ti_legacy", "unit": "byte", "endian": "little"},
    "armcl": {"family_kind": "ti_legacy", "unit": "byte", "endian": "little"},
    # GNU host/cross compilers (gcc, cc, arm-none-eabi-gcc, any *-gcc): preprocess like clang.
    "gnu": {"family_kind": "gnu", "unit": "byte", "endian": "little"},
}

# compiler file name -> family token, so a raw compiler path resolves straight to a platform.
_FAMILY_BY_NAME = {
    "tiarmclang": "armllvm",
    "c29clang": "c29",
    "cl2000": "c2000",
    "cl7x": "c7000",
    "cl6x": "c6000",
    "clpru": "pru",
    "armcl": "arm",
    "gcc": "gnu",
    "cc": "gnu",
}


def for_compiler(path: str) -> dict:
    """Return the platform table for the compiler at ``path`` (decided from its file name).

    Raises ``KeyError`` for a compiler whose family is not in the table -- the front end turns this
    into an ASK USER / exit 3, never a guess."""
    name = Path(path).name.lower().removesuffix(".exe")
    # A cross toolchain name ends in ``-gcc`` (e.g. ``arm-none-eabi-gcc``); treat any such name as
    # GNU. Otherwise match the exact known name.
    if name.endswith("-gcc"):
        family = "gnu"
    else:
        family = _FAMILY_BY_NAME.get(name, name)
    try:
        return _PLATFORMS[family]
    except KeyError:
        raise KeyError("no platform table for compiler %r (family %r)" % (path, family)) from None
