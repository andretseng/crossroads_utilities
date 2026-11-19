# SPEC: syscfg_run.py — SysConfig as a calculator (for Codex, 2026-11-19)

Owner: Claude (design, review). Implementer: Codex. Design note: vault `AI學習/skills/專案事前解析工具（所有 profile 共用）— 設計與逐步實作.md` §4.6 (D4).

## Goal
A model never works out pinmux, clock frequencies, bit timing or baud divisors itself. It runs SysConfig (the build's own
version) through one script and reads the facts. Changes are tried on a copy in /tmp ("what-if"); the project is never touched.

## Verified by Claude (2026-11-19, crossroads)
- `escape32_drv8300_lp-am13e230x`, SysConfig 1.28.1, `-s /home/andre/ti/am13e230x_sdk_26_01_00_03/.metadata/product.json --script <x>.syscfg -o <dir> --compiler ticlang`:
  ~1 s, output identical to `Debug/syscfg/`.
- Clocks: `ti_sdk_dl_config.h:57-66` `CPUCLK_FREQ_HZ 200000000`, `CANCLK_FREQ_HZ 100000000`, ...
- Bit timing: `.syscfg` `mcan1.desiredNomRate = 250`, `spPercent = 80` -> `ti_sdk_dl_config.c:943-957` `gMCANBitTimes`
  `nomRatePrescalar 3, nomTimeSeg1 78, nomTimeSeg2 19, nomSynchJumpWidth 19`.
- What-if: copy + appended line `mcan1.desiredNomRate = 500;` -> `nomTimeSeg1 38, nomTimeSeg2 9, nomSynchJumpWidth 9`.

## Interface (`~/utilities/firmware/syscfg_run.py`, stdlib only, Python 3.10+)
```
syscfg_run.py PROJECT_DIR --build NAME facts            # regenerate to /tmp, compare with <BUILD>/syscfg, print facts
syscfg_run.py PROJECT_DIR --build NAME whatif 'mcan1.desiredNomRate = 500' ['inst.prop = value' ...]
```
- Tool, product.json(s), --script/--context, --compiler, device args: take them from project_facts.py (import it; do not re-parse
  makefiles). Exit 3 when project_facts says the build is STALE / not built / FOREIGN (same ASK USER line). No build -> also allow
  `--sysconfig <cli> --product <json> --script <x.syscfg>` given explicitly (for a repo without CCS build), and say which mode was used.
- Work only in a fresh `/tmp/syscfg_run.XXXX` dir; copy the .syscfg there. Never write to the project, ~/ti or ~/.agents.

## Output (Markdown for the model; `--json` too)
1. Mode + exact command run + SysConfig exit code + time.
2. ERRORS / WARNINGS: every error/warning line the CLI printed, verbatim. Verify the real format by provoking one in /tmp
   (e.g. a what-if that puts two instances on the same pin, or an impossible bit rate) and add that case to the tests.
3. facts:
   - regenerate check: files that differ from `<BUILD>/syscfg/` (ignore `.o`, `.d`, `.dot`); "identical" otherwise.
   - clocks: every `#define *_FREQ_HZ` (and `*_FREQ`) with file:line.
   - computed peripheral parameters: every `static const <Type> <name> = { .field = value, ... };` initializer in the generated `.c`,
     as `name.field = value (file:line)`, plus numeric `#define`s whose name contains BAUD, IBRD, FBRD, PRESCAL, DIV, PERIOD, LOAD.
     Generic parsing, no peripheral names hard-coded.
   - pins: the generated pin comments (signal -> pad -> package pin) with file:line.
4. whatif: the same facts for the copy, and a DIFF list: only the values that changed (old -> new, file:line in the new output).
   If the what-if line is rejected by SysConfig, show its error verbatim and do not show facts as if it worked.
5. Register-field meaning (e.g. whether a DL_MCAN seg value is "actual - 1") is NOT decided here: print
   `meaning of encoded fields: check the SDK header / TRM (T3)`.

## Tests (`test_syscfg_run.py`, unittest)
- Parser tests on small fixture generated files (defines, struct initializers, pin comments) — assert exact values.
- Diff test: two fixture outputs -> only the changed fields listed.
- One integration test on escape32_drv8300 Debug (skip if the project or SysConfig is missing): base facts give
  `gMCANBitTimes.nomTimeSeg1 = 78`; what-if `mcan1.desiredNomRate = 500` gives 38 and the DIFF lists it.
- Error test: the provoked error case appears verbatim under ERRORS.

## Rules
- Standard library only; Python 3.10 and 3.13. No chip or case names in the script (tests may use them).
- Read-only on projects, ~/ti, ~/.agents, vault. Write code in /tmp first, then ask to copy into ~/utilities/firmware/.
- No git commit. Report: `~/utilities/firmware/REPORT_syscfg_run_20261119.md` — what was verified on which projects
  (at least AM13, MSPM0, C2000 F28P65x or F28003x, AM261 multi-core if SysConfig runs there), error format found, open points.
