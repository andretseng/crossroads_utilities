# SPEC: split struct_layout into a front end + compiler-family back ends (D10) — for Pi, 2026-11-19

Design note §4.6e (D10). Behaviour must NOT change: every existing struct_layout / project_facts test keeps passing.
- `~/utilities/firmware/platforms.py`: one table keyed by compiler family -> {"family_kind": "clang"|"ti_legacy",
  "unit": "byte"|"word16", "endian": "little"|"big"}. Families: armllvm/tiarmclang (clang, byte), c29 (clang, byte),
  c2000/cl2000 incl. CLA (ti_legacy, word16), c7000/cl7x (ti_legacy, byte), pru/clpru (ti_legacy, byte), arm/armcl (ti_legacy, byte).
  `platforms.for_compiler(path) -> dict` decides from the compiler file name (not from the chip).
- `~/utilities/firmware/backends/__init__.py`, `backends/clang.py`, `backends/ti_legacy.py`: each exports
  `record_layout(compiler, args, type_name, includes, workdir) -> {"size", "members": [...]}` (same member dicts as today) and
  raises `backends.CompileError(text)` on a compiler error (front end exits 2 and prints it).
- `struct_layout.py` keeps its CLI and JSON exactly; it picks the back end with platforms.for_compiler and contains no
  compiler-specific parsing (no "-fdump-record-layouts", no "DW_AT_" strings).
Tests: test_backends.py (given, do not edit) + all 9 existing struct_layout/project_facts test files.
Work in /tmp/backends_pi/ (copies are there). Report /tmp/backends_pi/REPORT.md. Same stop rule.
