# struct_layout D10 split — report

## Goal
Split `struct_layout` into a front end + compiler-family back ends (design note §4.6e, D10) with
**no behaviour change**: every existing `struct_layout` / `project_facts` test keeps passing, and the
CLI + JSON output are identical. Worked only in `/tmp/backends_pi/`.

## Result
All 10 test files pass — `test_backends.py` (the given acceptance test, 4 tests) plus the 9 existing
`test_struct_layout*` / `test_project_facts*` files (32 tests). **36 tests, all green.**

```
$ python3 -m unittest -v test_backends test_struct_layout test_struct_layout_review1 \
    test_struct_layout_review3 test_struct_layout_review4 test_struct_layout_review5 \
    test_struct_layout_review6 test_project_facts test_project_facts_review1 \
    test_project_facts_review2
Ran 36 tests ... OK
```

## Files created / changed (all in `/tmp/backends_pi/`)
- **`platforms.py`** (new) — one table keyed by compiler *family* →
  `{"family_kind": "clang"|"ti_legacy", "unit": "byte"|"word16", "endian": "little"}`.
  `for_compiler(path)` decides the family from the compiler **file name** (never the chip):
  `tiarmclang`→armllvm, `c29clang`→c29, `cl2000`→c2000, `cl7x`→c7000, `cl6x`→c6000, `clpru`→pru,
  `armcl`→arm; the family token then maps to a platform. Families: armllvm/tiarmclang (clang, byte),
  c29 (clang, byte), c2000/cl2000 incl. CLA (ti_legacy, word16), c7000/cl7x (ti_legacy, byte),
  pru/clpru (ti_legacy, byte), arm/armcl (ti_legacy, byte). Raises `KeyError` for an unknown
  compiler (e.g. `/usr/bin/gcc`) — the front end turns this into ASK USER / exit 3, never a guess.
- **`backends/__init__.py`** (new) — defines `CompileError(Exception)` and re-exports
  `clang.record_layout` / `ti_legacy.record_layout`. `CompileError` is defined **before** the
  submodule imports so `backends.CompileError is clang.CompileError` (a duplicate class definition
  here was the one real bug of the split — see below).
- **`backends/clang.py`** (new) — the clang-family back end: writes the wrapper-struct probe, runs
  `-Xclang -fdump-record-layouts`, and parses the `AST Record Layout` table. `record_layout`
  raises `CompileError` on a compile error / missing type / unparseable output.
- **`backends/ti_legacy.py`** (new) — the TI-legacy back end: writes the bare-variable probe, runs
  `-g -k`, and reads the DWARF in the `.asm`. `record_layout` decides its own unit (16 for cl2000,
  8 for cl7x/clpru from the compiler name) and raises `CompileError` on a missing type / unparseable
  output.
- **`struct_layout.py`** (rewritten as front end) — keeps its CLI and JSON exactly. It no longer
  contains any compiler-specific parsing: no `-fdump-record-layouts`, no `DW_AT_…`, no
  `data_member_location`, no probe-writing, no DWARF/record-table parsing. It only: loads
  `platforms` + `backends`, picks the back end with `platforms.for_compiler`, calls
  `record_layout(...)`, and formats the result. Project mode, staleness/FOREIGN/not-built handling,
  and the `project_facts.compile_command` call are unchanged.

## Behaviour preserved (verified, not just asserted)
- clang fixture `U` → `unit=byte size=12`, `a(0), b(4), c(8,0,3), d(8,3,5)`.
- cl2000 fixture `U` → `unit=word16 size=6`, `a(0), b(2), c(4,0,3), d(4,3,5)`.
- Missing type (`NoSuchType`) → exit 2, error printed verbatim (the wrapper makes clang stop; the
  TI back end raises `CompileError` when the struct is not found).
- Real projects unchanged: cia402 `TCiA402PDO1600` (cl2000), AM261 `HwiP_Params` (clang),
  dab_F28003x `DCL_PI_CLA` (cl2000), MSPM0 missing-type → exit 2.
- Project mode: stale FLASH build still prints ASK USER and exits 3.

## Bug found and fixed during the split
1. **Duplicate `CompileError` class → `except backends.CompileError` never matched.** The first
   `backends/__init__.py` defined `CompileError`, then `from .clang import record_layout`, then
   *another* `class CompileError`. `clang.py`'s `from . import CompileError` bound to the **first**
   class (during partial init of `backends`), while `backends.CompileError` resolved to the **second**
   — two distinct classes, so `cmd_measure`'s `except backends.CompileError` let the error escape as
   an uncaught traceback (exit 1, not 2). Moved `CompileError` above the submodule imports so there
   is one class. Confirmed by `backends.CompileError is clang.CompileError == True` and the
   `test_compiler_error_is_reported_not_guessed` / review-4 missing-type tests now exiting 2.

## Design notes
- The front end derives the back end purely from `platforms.for_compiler(compiler)["family_kind"]`;
  the unit (byte vs word16) and the per-back-end `unit_bits` stay inside the back ends, so the front
  end has no compiler-specific knowledge.
- Each back end owns its probe shape (wrapper struct for clang, bare variable for TI legacy) and its
  error path, so a missing type is a `CompileError` → exit 2 in both families, never a silent wrong
  answer.
- `platforms.py` and the `backends/` package are loaded from the script's own directory first
  (falling back to `~/utilities/firmware`), matching the `project_facts` loading already in place.

## Rules followed
- Standard library only.
- Worked only in `/tmp/backends_pi/`; probe output goes to a `tempfile.TemporaryDirectory` inside
  each back end.
- Did NOT edit the read-only `~/utilities/firmware/` files: `struct_layout.py`, `project_facts.py`,
  and the test files are unchanged (`git status` there shows only the unrelated `pi_watch.sh`).
  `platforms.py` / `backends/` were created only in `/tmp/backends_pi/`.
- Did NOT edit `test_backends.py` or any existing test.
- Stop rule: not triggered — every failing test (the one `CompileError` duplicate) was fixed on the
  first attempt.
