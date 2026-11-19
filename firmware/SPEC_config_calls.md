# SPEC: config_calls.py — where the program writes configuration (D4b static part, for Pi, 2026-11-19)
Design note §4.6b. Not every project uses SysConfig, and code can change settings after SysConfig init. List every register write
and every driver-API call in the given files, with constant values evaluated by the compiler. Builds on c_facts.py (same AST walk;
reuse its functions by import, do not copy) and c_eval (backends.evaluate). v1: clang family; ti_legacy -> exit 2 "not available yet".

Register write = an assignment (=, |=, &=, ^=, <<=, >>=) whose left side goes through `->` or `.` of a struct reached from a
pointer that the AST marks volatile (TIMG4->COUNTERREGS.CCACT_01[...], SysTick->VAL, ...). Driver call = a call whose callee name
starts with one of --prefix (default: DL_, SYSCFG_DL_, GPIO_, SysCtl_, EPWM_, ADC_, CAN_, MCAN_, SCI_, SPI_, I2C_, Interrupt_, CLA_).
CLI: `config_calls.py PROJECT --build B FILE [FILE ...] [--prefix P ...] [--json]`; STALE etc. -> exit 3 ASK USER.
JSON: {"writes": [{"file", "line", "function", "kind": "register"|"call", "target": src (register lvalue or callee name),
  "index": src|null, "index_value": int|null, "op": "="|"|="|...|null, "value": src (rhs or args text), "value_int": int|null,
  "path": [{"if_line", "arm"}...]}]}  in file and line order. value_int only when the whole rhs is a constant expression
  (evaluate it with the file's own includes); a `?:` rhs keeps value_int null.
Tests: test_config_calls.py (given, do not edit). Work in /tmp/config_calls_pi/.
