# SPEC: c_facts.py — control-flow and value facts from the compiler's AST (T5 part 2, for Pi, 2026-11-19)
Design note §6.4. These are the facts the model got wrong again and again in the softuart runs (Fix 11, 12, 15, 18, 23, 24, 26, 31, 35):
which `if` an `else` belongs to, which branch an assignment is in, the two values of a `?:`, post-increment in a condition.
Facts only — no interpretation such as "LSB first" or "8 data bits" in v1.

Method: clang family only in v1. `<compiler> <project_facts.compile_command args for FILE> -fsyntax-only -Xclang -ast-dump=json
-Xclang -ast-dump-filter=<FUNCTION> <file>` in the build folder (verified by Claude on escape32_boot Release_TIMER io.c: rc 0, 275 kB).
Note: clang JSON omits "line" when it equals the previous node's line; keep the last seen line while walking (in "loc" and
"range.begin/end", also inside "expansionLoc"/"spellingLoc" for macro locations). Source text of a node = the file text between
its begin and end offsets (`offset` + `tokLen`). Constant values of macro operands: evaluate with c_eval (backends.evaluate).
ti_legacy families: exit 2 "AST facts not available for <family> yet" (honest; planned later).

CLI: `c_facts.py PROJECT --build B FILE --function NAME [--json]` (STALE etc. -> exit 3 ASK USER).
JSON:
{"function": {"name", "first_line", "last_line"},
 "ifs": [{"line", "condition": src text, "then": [first,last], "else": [first,last] | null, "else_if_line": int | null,
          "parent_if_line": int | null}],     # parent_if_line: for an `else if`, the `if` whose else it is
 "conditionals": [{"line", "target": src text | null, "condition": src text, "then": src text, "else": src text,
                   "then_value": int | null, "else_value": int | null}],
 "assignments": [{"line", "var": name, "op": "=", "|=", ">>=", ..., "value": src text, "value_int": int | null,
                  "path": [{"if_line": int, "arm": "then"|"else"}, ...]}],   # innermost last; only plain local variables
 "increments": [{"line", "var", "op": "++"|"--", "postfix": bool, "in_condition_of": if line | null}]}
Tests: test_c_facts.py (given, do not edit). Work in /tmp/c_facts_pi/. Use project_facts, platforms, backends (c_eval's evaluate).
