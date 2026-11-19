# SPEC: reg_decode.py — decode a register value into fields from the SDK headers (T3, for Pi, 2026-11-19)
Design note §4 T3. A model never splits a register value into fields itself. The field macros come from the build's own
preprocessor macro dump (so the right SDK version and device are used):
- clang family: `<compiler> <compile_command args> -E -dM probe.c` (probe includes the --include headers).
- ti_legacy: `<compiler> <args> --preproc_macros=<tmp>/m.txt probe.c` (cl2000 option `--preproc_macros,-ppm[=filename]`).
Evaluate each needed macro value with c_eval (backends.evaluate) — never parse `((uint32_t)0x...U)` text by hand.
Field naming styles (detect from the macros that exist for the register prefix):
 a) `<REG>_<FIELD>_MASK` + `<REG>_<FIELD>_OFS`  (AM13, MSPM0)
 b) `<REG>_<FIELD>_MASK` + `<REG>_<FIELD>_SHIFT` (CSL: AM26x, AM275x)
 c) `<REG>_<FIELD>_M` + `<REG>_<FIELD>_S`, and single-bit fields as a bare `<REG>_<FIELD>` bit value (C2000 hw_*.h)
Named values (style a/b): every other `<REG>_<FIELD>_<NAME>` macro whose value lies inside the field mask; the field's value is
named when it equals one of them (field value 0 and a `..._NAME` = 0 -> that name).
CLI: `reg_decode.py PROJECT --build B [--source FILE] --include H [...] --register REG --value V [--json]`; explicit mode as c_eval.
JSON: {"register": REG, "value": int, "style": "mask_ofs"|"mask_shift"|"m_s", "fields": [{"field", "mask", "shift", "value",
  "name": str|null}], "unassigned_bits": int}   (fields sorted by shift; unassigned_bits = value & ~(OR of all masks)).
Unknown register (no field macros) -> exit 2. STALE etc. -> exit 3. Tests: test_reg_decode.py (given). Work in /tmp/reg_decode_pi/.
