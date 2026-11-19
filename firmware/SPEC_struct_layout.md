# SPEC: struct_layout.py — measured struct layout (for Pi, 2026-11-19)

Owner: Claude (architecture, acceptance tests, verification). Implementer: Pi (Ornith). Design note: vault
`AI學習/skills/專案事前解析工具（所有 profile 共用）— 設計與逐步實作.md` §4.8 (D6).

## Goal
A model never works out offsets, padding, sizeof or bit-field positions. The project's own compiler, with the build's own flags,
measures them.

## Verified by Claude (2026-11-19) — use these methods, do not invent others
- clang family (tiarmclang, c29clang): `<compiler> <build flags> -Xclang -fdump-record-layouts -emit-llvm -S -o /dev/null probe.c`
  prints e.g. `0 | uint8_t a`, `4 | uint32_t b`, `8:0-2 | uint16_t c`, `8:3-7 | uint16_t d`, `[sizeof=12, align=4]`.
  (`-c` gives no dump; `-fsyntax-only` dumps only builtin records.)
- TI legacy (cl2000, and the same way cl7x/clpru): `<compiler> <build flags> -g -k --asm_directory=<tmp> --obj_directory=<tmp> -c probe.c`
  then read the DWARF lines in the `.asm`: `DW_AT_name("<member>")`, `DW_AT_data_member_location[DW_OP_plus_uconst 0x2]`,
  `DW_AT_bit_offset(0x0d)`, `DW_AT_bit_size(0x03)`, and the struct's `DW_AT_byte_size(0x06)`.
  bit_offset is counted from the MSB of the storage unit: lsb = unit_bits - bit_offset - bit_size
  (cl2000 uint16_t unit: c bit_offset 13 size 3 -> bits 0-2; d bit_offset 8 size 5 -> bits 3-7).
- Units: cl2000 (C28x) addresses and sizeof are 16-bit words; clang targets bytes. Print the unit; never mix.
- Probe file: `#include` the header(s) that declare the type (plus any header they need, e.g. `ecat_def.h`), declare one variable of
  the type. Real check: cia402 `TCiA402PDO1600` with the FLASH build flags -> ObjControlWord 0, ObjTargetPosition 2,
  ObjTargetVelocity 4, ObjModesOfOperation 6 (words).

## Interface (`~/utilities/firmware/struct_layout.py`, stdlib only, Python 3.10+)
```
struct_layout.py PROJECT_DIR --build NAME --type T --include header.h [--include h2.h] [--json]   # flags from project_facts
struct_layout.py --compiler PATH --flags "FLAGS" --type T --include header.h [...] [--json]         # explicit mode
```
- Project mode takes the compiler and flags of a C file in that build from `project_facts.py` (import it; keep -D, -I/--include_path,
  -v28/-mcpu/--abi/--float_support...; drop -c, -o, dependency flags). Same ASK USER + exit 3 when project_facts says stale /
  not built / foreign. `--include` names are resolved with the build's include paths.
- Work in a fresh /tmp folder. Never write to the project.
- JSON: `{"type": T, "unit": "byte"|"word16", "size": int, "members": [{"name": str, "offset": int, "bit_lsb": int|null,
  "bit_size": int|null}]}` in declaration order; nested structs/arrays: top level members only in this version.
- If the compiler fails: exit 2 and print the compiler's error lines verbatim. No guessing.

## Acceptance tests
`test_struct_layout.py` (given, do not edit). Must pass: `python3 -m unittest -v test_struct_layout`.

## Rules
Standard library only; no chip/case names in the script. Work only in /tmp/struct_layout_pi/. Report in /tmp/struct_layout_pi/REPORT.md.
Stop after 2 failed fix attempts on the same test and write why.
