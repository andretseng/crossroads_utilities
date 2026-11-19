# SPEC: c_eval.py — the build's own compiler evaluates C constant expressions (T5 part 1, for Pi, 2026-11-19)
Design note §6.4: 14 of 30 value-type errors need typed C evaluation / AST. Part 1 = constant expressions with the build's macros
and the target's types (C integer division, 16-bit int on C28, casts, wrap). Part 2 (later) = AST facts.

Method (verified by Claude, do not invent another):
- probe file: the `--include` headers, then one line per expression: `const long long __eN = (EXPR);`
- clang family: `<compiler> <compile_command args> -S -emit-llvm -o - probe.c` -> `@__eN = ... constant i64 <value>`
- ti_legacy: `<compiler> <args> -k --asm_directory=<tmp> --obj_directory=<tmp> -c probe.c` -> in the .asm
  `||__eN||:` followed by `.bits <value>,64`; read as a 64-bit two's complement signed value.
- cwd = the build folder (relative include paths in the command). Choose the back end with platforms.for_compiler.
Put the compiler-specific part in backends/clang.py and backends/ti_legacy.py as `evaluate(compiler, args, exprs, includes, workdir)
-> [int]`; c_eval.py is the front end (no compiler-specific strings).

CLI: `c_eval.py PROJECT --build B [--source FILE] [--include H ...] EXPR [EXPR ...] [--json]` (args from
project_facts.compile_command(project, build, source)); explicit: `c_eval.py --compiler C --flags "..." [--include H] EXPR...`.
JSON: `{"results": [{"expr": str, "value": int}], "compiler": path, "unit": "byte"|"word16"}` (unit from platforms; it is the
unit of sizeof). Not a constant expression / compile error -> exit 2 with the compiler text. Stale/not built/foreign -> exit 3 ASK USER.
Tests: test_c_eval.py (given, do not edit) + test_backends.py + test_struct_layout*.py must still pass. Work in /tmp/c_eval_pi/.
