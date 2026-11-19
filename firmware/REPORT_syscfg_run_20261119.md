# syscfg_run.py implementation report

Date: 2026-11-19  
Runtime: Python 3.10.12 on crossroads  
Scope: `SPEC_syscfg_run.md`

## Outcome

Implemented `syscfg_run.py` as a standard-library-only wrapper around `project_facts.py`. It copies every input `.syscfg` into a fresh `/tmp/syscfg_run.XXXX` directory, runs the exact build-selected SysConfig command with only the script and output paths redirected to that workspace, and never edits a project or SDK file.

The CLI supports:

- `PROJECT_DIR --build NAME facts`
- `PROJECT_DIR --build NAME whatif 'instance.property = value' [...]`
- explicit mode with `--sysconfig`, repeated `--product`, repeated `--script`/`--context`, and optional `--compiler`/`--device`
- Markdown output and `--json`
- exit 3 without running SysConfig when `project_facts` marks the selected build stale, never built, or foreign

## Extracted facts

The generic parser reports:

- every `#define *_FREQ_HZ` and `*_FREQ`, with generated file and line;
- top-level fields of every `static const <Type> <name> = {...}` initializer as `name.field = value`;
- numeric defines whose names contain `BAUD`, `IBRD`, `FBRD`, `PRESCAL`, `DIV`, `PERIOD`, or `LOAD`;
- generated `signal -> pad -> package pin` comments;
- only intersecting parsed values that changed between base and what-if output;
- generated-file differences against `<BUILD>/syscfg`, ignoring `.o`, `.d`, and `.dot`.

Every result prints `meaning of encoded fields: check the SDK header / TRM (T3)`; the script does not infer register encoding semantics.

## Tests

Seven `unittest` tests pass:

1. Fixture parser: exact clock, numeric-define, struct-field, and pin values/locations.
2. Fixture diff: only `gMCANBitTimes.nomTimeSeg1` changes from 78 to 38.
3. Explicit-mode fake CLI: runs on a copy and leaves the source `.syscfg` unchanged.
4. Rejected fake what-if: exact diagnostic appears under `WHATIF ERRORS` and no successful facts are emitted.
5. Real escape32 DRV8300 base plus 500-kbit/s what-if.
6. Real rejected MCAN rate with TI's diagnostic asserted verbatim.
7. Never-built Release configuration: exit 3, exact `ASK USER`, and no SysConfig execution.

Command run:

```text
python3 -m unittest -v test_syscfg_run.py
Ran 7 tests ... OK
```

## Project verification

| Family/project/build | Result | Evidence |
|---|---|---|
| AM13 — `escape32_drv8300_lp-am13e230x`, Debug | Verified | SysConfig 1.28.1 exited 0 in about 1 second; regenerated files were identical to `Debug/syscfg` after ignoring `.o`, `.d`, `.dot`; parsed 10 clocks, 239 top-level computed fields/selected numeric defines, and 6 pin comments. `CPUCLK_FREQ_HZ = 200000000`; base `gMCANBitTimes.nomTimeSeg1 = 78`; 500-kbit/s what-if gives 38 and DIFF reports it. |
| MSPM0 — `gpio_toggle_output_LP_MSPM0G5187_nortos_ticlang`, Debug | Not verified | The build-selected SysConfig 1.28.1 exited 1 while attempting to update read-only `/home/andre/TI_pinmux/products.json`: `Error: EROFS: read-only file system, open '/home/andre/TI_pinmux/products.json'`. No generated facts were reported. |
| C2000 F28003x — `dab_F28003x`, RELEASE | Not verified | SysConfig 1.21.0 exited 1 with the same read-only `~/TI_pinmux/products.json` error. No generated facts were reported. |
| AM261 multi-core — `servo_drive_am261x-lp_r5fss0-1`, Debug | Not verified | SysConfig 1.25.0 exited 1 because the build-recorded `/home/andre/ti/ind_comms_sdk_am261x_2025_00_00_08/.metadata/product.json` does not exist. The command retained both scripts and their `r5fss0-0`/`r5fss0-1` contexts. |

## Real diagnostic format

The invalid temporary assignment `mcan1.desiredNomRate = 999999999` produced exit 1 and these lines:

```text
error: MCAN(/drivers/mcan) desiredNomRate: Must be in the range of 1 to 1000
1 error(s), 0 warning(s)
```

Both lines are preserved verbatim in output. Because the what-if was rejected, no what-if facts or DIFF are presented as successful.

## Open points

- SysConfig versions used by the tested MSPM0 and C2000 projects attempt to write product-discovery state under `~/TI_pinmux`, outside the required temporary workspace. The read-only environment prevented that write. I did not change `HOME`, grant write access, or substitute another SysConfig version.
- The tested AM261 build references an absent 2025 industrial-communications SDK. I did not replace it with the installed 2026 SDK because that would no longer be the build's own command.
- Multiple-script what-if mode appends assignments to the first copied script while preserving every script/context in the command. Selecting a different context needs an explicit interface decision.
- Parser output records generated C expressions as text. Interpreting whether a value is encoded as “actual minus one” remains a T3 SDK-header/TRM task.
- Python 3.13 was not available on crossroads; execution was verified on Python 3.10.12 using only 3.10-compatible standard-library APIs.
