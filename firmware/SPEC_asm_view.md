# SPEC: asm_view.py — which assembly lines this build assembles, where, and as what (D8, for Pi, 2026-11-19)
Design note §4.6d. Same principle as C: the assembler decides, not the model. v1: TI assemblers (cl2000 / clpru / cl7x / armcl,
`--asm_listing`); clang `.S` files: preprocess with -E (reuse preprocess.py) and mark level "preprocessed_only".
Method (TI): `project_facts.compile_command(project, build, source=<asm file>)` (its recipe in subdir_rules.mk), run it in the
build folder with `--asm_listing --obj_directory=<tmp> --asm_directory=<tmp>` (outputs only in /tmp; never reuse the .lst left in
the build folder — it can be stale). Parse <tmp>/<name>.lst: columns = listing line, source line number, optional address
(8 hex), optional encoding (hex, may end with `!` = relocated), source text. A source line is assembled when its listing line
has an address. Track the current section from `.sect "name"`, `.text`, `.data`, `.bss`. `.set`/`.equ` lines show the value in
the address column (e.g. `0001  WD_DISABLE .set 1`) -> symbols. Lines produced by macro expansion or .include belong to the
including line; report them as `expanded_from`.
CLI: `asm_view.py PROJECT --build B FILE [--lines A-B] [--json]`. STALE etc. -> exit 3 ASK USER.
JSON: {"level": "listing"|"preprocessed_only", "file", "command": [...], "symbols": [{"name", "value": int, "line"}],
 "lines": [{"line", "assembled": bool|null, "section": str|null, "address": int|null, "encoding": str|null, "text": str}]}
Tests: test_asm_view.py (given, do not edit). Work in /tmp/asm_view_pi/.
