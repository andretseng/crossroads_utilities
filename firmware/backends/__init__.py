#!/usr/bin/env python3
"""backends -- compiler-family back ends for c_eval and struct_layout (D10 split).

Each back end exports ``evaluate(compiler, args, exprs, includes, workdir)`` for c_eval -- returning a
list of ints (one per input expression, in order) and raising ``CompileError`` on a compiler error or
unparseable output (the front end exits 2 and prints it) -- as well as ``record_layout`` for
struct_layout.

c_eval: the front end writes a probe file with one ``const long long __eN = (EXPR);`` line per
expression, runs the build's own compiler with the build's own flags, and reads the constant value the
compiler computed. clang family reads the LLVM constant; TI legacy reads the ``.bits <value>,64`` in
the assembly. struct_layout: clang reads the ``AST Record Layout`` dump (byte units); TI legacy reads
the DWARF in the ``.asm`` (word16 units for cl2000, byte units for cl7x/clpru). Units are the
compiler's own; the c_eval value is always a signed 64-bit two's-complement int.

Standard library only. No chip or case names.
"""
from __future__ import annotations


class CompileError(Exception):
    """Raised by a back end when the compiler fails or its output cannot be parsed."""


from .clang import evaluate as clang_evaluate  # noqa: E402
from .clang import record_layout as clang_record_layout  # noqa: E402
from .ti_legacy import evaluate as ti_legacy_evaluate  # noqa: E402
from .ti_legacy import record_layout as ti_legacy_record_layout  # noqa: E402


__all__ = [
    "CompileError",
    "clang_evaluate",
    "clang_record_layout",
    "ti_legacy_evaluate",
    "ti_legacy_record_layout",
]
