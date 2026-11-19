# syscfg_run.py implementation report

Date: 2026-11-19  
Runtime: Python 3.10.12 on crossroads  
Scope: `spec_syscfg_run.md`

## Outcome

Implemented `syscfg_run.py` as a standard-library-only wrapper around `project_facts.py`. Facts mode passes the original `.syscfg` path to SysConfig and redirects only `-o` to a fresh `/tmp/syscfg_run.XXXX` directory. What-if mode copies the script's project directory into that workspace, excluding CCS build directories declared by `.cproject`, and edits the copied script. Keeping the script's relative location preserves project-relative resources such as `dab_F28003x/solution.js`. Neither mode edits a project or SDK file.

The CLI supports:

- `PROJECT_DIR --build NAME facts`
- `PROJECT_DIR --build NAME whatif 'instance.property = value' [...]`
- explicit mode with `--sysconfig`, repeated `--product`, repeated `--script`/`--context`, and optional `--compiler`/`--device`
- Markdown output and `--json`
- exit 3 without running SysConfig when `project_facts` marks the selected build stale, never built, or foreign, or when a build-recorded SDK `product.json` is missing

## Extracted facts

The generic parser reports:

- every `#define *_FREQ_HZ` and `*_FREQ`, with generated file and line;
- top-level fields of every `static const <Type> <name> = {...}` initializer as `name.field = value`;
- numeric defines whose names contain `BAUD`, `IBRD`, `FBRD`, `PRESCAL`, `DIV`, `PERIOD`, or `LOAD`;
- generated `signal -> pad -> package pin` comments;
- only intersecting parsed values that changed between base and what-if output;
- generated-file differences against `<BUILD>/syscfg`, ignoring compiler outputs `.obj`, `.lst`, `.o`, `.d`, `.dot`, `.pp`, and `.rov.xs`; ignored filenames are listed on one line.

Every result prints `meaning of encoded fields: check the SDK header / TRM (T3)`; the script does not infer register encoding semantics.

## Tests

Eleven `unittest` tests pass:

1. Fixture parser: exact clock, numeric-define, struct-field, and pin values/locations.
2. Fixture diff: only `gMCANBitTimes.nomTimeSeg1` changes from 78 to 38.
3. Regenerate fixture: all seven compiler-output suffixes are ignored and reported.
4. Explicit-mode fake CLI: runs what-if on a copy and leaves the source `.syscfg` unchanged.
5. Facts mode passes the original script path and loads its relative fixture resource.
6. What-if copies the relative fixture resource and excludes the declared `Debug` build directory.
7. Rejected fake what-if: exact diagnostic appears under `WHATIF ERRORS` and no successful facts are emitted.
8. Real escape32 DRV8300 base plus 500-kbit/s what-if.
9. Real rejected MCAN rate with TI's diagnostic asserted verbatim.
10. Never-built Release configuration: exit 3, exact `ASK USER`, and no SysConfig execution.
11. Missing AM261 SDK/product metadata: exact install-or-rebuild `ASK USER` and no SysConfig execution.

Command run:

```text
python3 -m unittest -v test_syscfg_run.py
Ran 11 tests ... OK
```

## Project verification

| Family/project/build | Result | Evidence |
|---|---|---|
| AM13 — `escape32_drv8300_lp-am13e230x`, Debug | Verified | SysConfig 1.28.1 exited 0 in 1.035 s; regenerated files were identical to `Debug/syscfg` after ignoring `.o`, `.d`, `.dot`; parsed 10 clocks, 239 top-level computed fields/selected numeric defines, and 6 pin comments. `CPUCLK_FREQ_HZ = 200000000`; base `gMCANBitTimes.nomTimeSeg1 = 78`; 500-kbit/s what-if gives 38 and DIFF reports it. |
| MSPM0 — `gpio_toggle_output_LP_MSPM0G5187_nortos_ticlang`, Debug | Verified | Outside the restricted filesystem sandbox, build-selected SysConfig 1.28.1 exited 0 in 0.814 s; parsed `CPUCLK_FREQ = 32000000`, 1 clock, 0 selected parameters, and 5 pins. `Debug/syscfg` does not exist, so no regenerate comparison was available. The earlier `~/TI_pinmux/products.json` EROFS result was caused by the test sandbox, not the project. |
| C2000 F28003x — `dab_F28003x`, RELEASE | Verified | SysConfig 1.21.0 facts mode exited 0 in 1.067 s with the original `main.syscfg`; parsed 3 clocks. A harmless what-if on the copied project also exited 0 for both base and copied script, proving `solution.js` remains resolvable. The regenerate comparison is identical; `board.d`, `board.lst`, `board.obj`, `c2000ware_libraries.d`, `c2000ware_libraries.lst`, and `c2000ware_libraries.obj` are listed on the ignored-output line. |
| AM261 multi-core — `servo_drive_am261x-lp_r5fss0-1`, Debug | Correctly blocked | The build-recorded `/home/andre/ti/ind_comms_sdk_am261x_2025_00_00_08/.metadata/product.json` does not exist. The wrapper now stops before execution, prints `ASK USER: install /home/andre/ti/ind_comms_sdk_am261x_2025_00_00_08 or rebuild with an installed SDK`, and exits 3. |

## Real diagnostic format

The invalid temporary assignment `mcan1.desiredNomRate = 999999999` produced exit 1 and these lines:

```text
error: MCAN(/drivers/mcan) desiredNomRate: Must be in the range of 1 to 1000
1 error(s), 0 warning(s)
```

Both lines are preserved verbatim in output. Because the what-if was rejected, no what-if facts or DIFF are presented as successful.

## Open points

- TI SysConfig updates product-discovery state under `~/TI_pinmux`. Initial in-sandbox MSPM0/C2000/AM13 attempts failed with EROFS; the reported real-project results were rerun outside that restricted sandbox. The scripts and generated outputs still followed the required read-only-source and `/tmp` rules.
- The tested AM261 build references an absent 2025 industrial-communications SDK. The wrapper does not substitute the installed 2026 SDK because that would no longer be the build's own command.
- Multiple-script what-if mode appends assignments to the first copied script while preserving every script/context in the command. Selecting a different context needs an explicit interface decision.
- Parser output records generated C expressions as text. Interpreting whether a value is encoded as “actual minus one” remains a T3 SDK-header/TRM task.
- Python 3.13 was not available on crossroads; execution was verified on Python 3.10.12 using only 3.10-compatible standard-library APIs.
