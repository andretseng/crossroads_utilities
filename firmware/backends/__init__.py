#!/usr/bin/env python3
"""backends -- compiler-family layout back ends for struct_layout (D10).

Each back end exports ``record_layout(compiler, args, type_name, includes, workdir)`` returning the
same ``{"size", "members": [...]}`` dict the front end expects, and raises ``CompileError`` on a
compiler error (the front end exits 2 and prints it).
"""
from __future__ import annotations


class CompileError(Exception):
    """Raised by a back end when the compiler fails or its output cannot be parsed."""


from .clang import record_layout as clang_record_layout  # noqa: E402
from .ti_legacy import record_layout as ti_legacy_record_layout  # noqa: E402


__all__ = ["CompileError", "clang_record_layout", "ti_legacy_record_layout"]
