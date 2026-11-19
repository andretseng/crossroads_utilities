# preprocess.py — Report

`preprocess.py` determines which lines of a source file a given build compiles and their
expanded text, across three levels: **build**, **sdk_make**, and **partial**. All compiler
output goes to `/tmp`; projects and SDKs are treated as read-only.

## CLI

```
preprocess.py PROJECT --build B FILE [--lines A-B]
preprocess.py --sdk-make MK --make-var K=V... FILE [--lines A-B]
preprocess.py --make-dir DIR FILE [--make-var K=V...] [--lines A-B]
preprocess.py --partial FILE [--define X[=V]] [--undefine Y] [--lines A-B]
preprocess.py --partial FILE --from-cproject PROJECT --build B [--define X[=V]] [--undefine Y] [--lines A-B]
```

`--json` emits `{level, file, command, unknown_symbols[, unresolved][, defines_source], lines:[{line, compiled, expanded}]}`.
The `make` level has `level="make"`; the `--from-cproject` partial level adds `defines_source`
(`"cproject (inferred)"` or `"cproject (inferred) + command line"`) and `unresolved` (the `${...}`
symbols the seed could not resolve).

## How each level works

- **build** — `project_facts.compile_command(project, build, source)` yields the compiler and
  flags. A stale / never-built / FOREIGN build exits `3` (`ASK USER`). The compiler is run with
  `-E -dD -C` (clang) or `--preproc_only --preproc_with_line --preproc_with_comment --pp_directory=<tmp>`
  (ti legacy) for the expanded text — comments are kept so comment lines map like code — and `-S -g`
  (clang) / `-g -k` (ti legacy) for an assembly `.loc` map that names exactly which source lines are
  compiled.
- **sdk_make** — `make -s -n -f <makefile> <vars>` extracts the compile line, then `-E`.
- **partial** — `unifdef -b` on the known symbols. A symbol that is neither `--define`d nor
  `--undefine`d is **UNKNOWN**: it is listed in `unknown_symbols`, and any line under a conditional
  that references it is `null` (unifdef keeps such lines in its output, but that does not mean they
  are built). Lines unifdef drops (blank output line) are `false`; kept lines outside any conditional
  are `true`.

## Line-mapping strategy

Neither `tiarmclang` nor `cl2000` emit per-line `# N "file"` markers during `-E` (only
function-boundary markers). So the set of compiled lines comes from the assembly `.loc` markers
(`-S -g` for clang, `--preproc_with_line --pp_directory` for ti_legacy). The expanded text for
each compiled line is found by matching it against the `-E` output:

1. If the compiler emits a per-line `-E` marker for the source line, that line is taken directly.
2. Otherwise a predicted `-E` position is interpolated from the assembly position through the
   source lines that have both a `.loc` and an `-E` marker (the "anchors").
3. The `-E` line is chosen by **identifier-overlap** scoring: identifiers that survive macro
   expansion (register names, variable names) are a strong fingerprint. Ties are broken by an
   **exact identifier-sequence match** (this distinguishes a call `DAB_setupSFRA();` from a
   prototype `void DAB_setupSFRA();`) and then by proximity to the predicted position.

Macro expansion (`IO_CC` → `0`, `GPTIMER_...` → `((uint32_t)value)`) is absorbed because the
surviving identifiers still overlap.

## Heuristics for lines without a `.loc` marker

> Replaced in review 1 (see below). The old heuristics are retained here only as history.

- A function signature line ending in `{` (or `)` whose body follows) is compiled even though the
  first `.loc` is on the next statement line.
- A preprocessor directive line (`#define`/`#undef`/`#if`/…) whose predecessor is compiled is
  compiled too — the compiler's `-dD` output emits one `# N` marker for a group of consecutive
  directives, so the second directive's source line is otherwise unnamed.
- `#define` lines are compiled when named by the `-dD` preprocessor-marker map (not just the
  assembly `.loc` map).

## Review 1 — comment / branch semantics and read-only enforcement

The gate passed (6 tests) but a real defect remained: **comment lines inside the ACTIVE branch were
reported `compiled=false`** (io.c:40-43, the `#else` branch Release_TIMER builds; MSPM0
`gpio_toggle_output.c` 40-60 has no `#if` at all yet its comments showed false). That reads as
"this code is not built" and misleads the model.

### Fix 1 — keep comments in the preprocessor output

Both compilers were run without comments. Added `-C` to the clang `-E` invocation and
`--preproc_with_comment` to the cl2000 `--preproc_only` command, so comment lines survive into the
expanded text and are located like code.

### Fix 2 — a proper active/inactive classification (`_active_status`)

The old `_build_rows` decided `compiled` from the assembly `.loc` map plus ad-hoc heuristics, which
left active comments as false. Rewrote it as `_active_status(source_lines, expanded, loc_map,
target)`:

- **`loc_map` is the ground truth** for which source lines carry compiled code.
- Walk the source tracking `#if`/`#ifdef`/`#ifndef`/`#elif`/`#else`/`#endif` nesting. Each condition
  is evaluated against the defines the build actually emitted (`-dD` output); `#elif` chains resolve
  in order, `#else` is taken when no earlier branch was. A condition referencing an **unknown symbol
  is treated as false** — the tool never fabricates a definition.
- Classification:
  - `True` — line carries code (in `loc_map`), or it is a comment/blank inside a taken branch.
  - `False` — line lies in a `#if` branch that was not taken.
  - `None` — a directive line or a blank line outside any branch.

This makes `compiled=false` mean exactly "inactive branch", and active comments true, while never
guessing unknown symbols.

### Fix 3 — marker regex handles the trailing file-depth

clang emits `# N "path" <depth>` (e.g. `# 1 "..." 2`). The marker regex now allows an optional
trailing integer after the quoted path, both in `_marker_line_map` and `_active_status`.

### Fix 4 — read-only guaranteed for cl2000

`_ti_legacy_preproc` already used `--preproc_only` (preprocess, never a full compile),
`--pp_directory=<tmp>` (all `.pp` output to a temp dir), and `cwd=<tmp>`. Verified: running the DAB
`RELEASE` build over `dab_main.c` changes **0** files in the project (no `.obj`, no `.pp`, no
timestamp changes). The review-1 `test_cl2000_writes_nothing_into_the_project` passes.

### Test results (review 1)

```
$ python3 -m unittest test_preprocess_review1 test_preprocess test_backends -v
Ran 13 tests — OK
```

All 6 original `test_preprocess`, 3 new `test_preprocess_review1`, and 4 `test_backends` pass.
`test_backends` (the `struct_layout` split contract) still passes: the front end `preprocess.py`
has no compiler-specific parsing, and `backends/clang.py` + `backends/ti_legacy.py` export the same
functions.

## Review 2 — partial mode: unknown symbols stay null

Review-1 passed and C2000 echoback was right, but **partial mode mishandled unknown symbols**. With
only `--define IO_TIMER`, io.c `#ifdef IO_UC4` (line 24) was reported `compiled=true` and `IO_UC4`
was missing from `unknown_symbols`. The root cause: `unifdef -b` *keeps* lines under an unknown
`#if`/`#ifdef`/`#elif` in its output (it cannot decide), and the old code read a kept non-blank line
as "built".

### Fix — rewrite `_level_partial` unknown handling

- **Unknown detection** — walk the source; every identifier in a `#if`/`#elif` expression that is
  neither `--define`d, `--undefine`d, a number, nor `defined(X)` is recorded in `unknown_symbols`
  (deduplicated, in first-seen order). `defined(...)` is stripped before tokenising.
- **Per-line unknown-controlled status** — a single pass tracks conditional nesting with frames
  `(start_line, branch_unknown, taken)`. `branch_unknown` is True when any branch in the `#if` chain
  references an unknown symbol; `#elif`/`#else` inherit it. A non-directive line is marked
  unknown-controlled when an open branch is unknown-controlled and not yet taken.
- **Classification** — `None` if unknown-controlled; else `false` if unifdef dropped the line
  (blank output); else `true`. This makes an unknown-controlled line `null` rather than `true`,
  while a fully-known build still resolves `#else` correctly.

Verified: with only `--define IO_TIMER`, lines 24/30/35 are all `None` and
`unknown_symbols == {IO_UC4, IO_PA2}`; with `IO_UC4`/`IO_PA2` also `--undefine`d, line 35 (`#else`) is
`true` and `unknown_symbols` is empty. The review-2 `test_unknown_symbols_stay_unknown` and
`test_all_known` pass.

## Test results (review 2)

```
$ python3 -m unittest test_preprocess_review1 test_preprocess_review2 test_preprocess test_backends -v
Ran 15 tests — OK
```

All 15 tests pass: 6 `test_preprocess`, 3 `test_preprocess_review1`, 2 new
`test_preprocess_review2`, and 4 `test_backends`. `test_backends` (the `struct_layout` split
contract) still passes.

## Review 3 — from-cproject seeding, plain-repo Makefile, and the GNU family

Round 3 (user-approved) added three capabilities on top of the existing levels.

### Feature 1 — `--from-cproject PROJECT --build B` seeds partial mode from a `.cproject`

`project_facts.cproject_builds(project)` returns each build's `defines`/`undefines`. The seed
becomes the **known** symbol set for partial mode, so a developer does not have to retype the
platform's defines on the command line. Two subtleties:

- **Unresolved `${...}` entries are not known.** A define like `${SYSCONFIG_TOOL_SYMBOLS}` is a
  make-variable that *may* expand to `#define` lines we cannot see, so it is listed in the new
  `unresolved` array (not treated as defined). The escape32_boot `Release_PA26` seed is
  `['${SYSCONFIG_TOOL_SYMBOLS}', '${COM_TI_AM13E2X_SDK_SYMBOLS}', 'IO_PA2']`, so `unresolved ==
  {'${SYSCONFIG_TOOL_SYMBOLS}', '${COM_TI_AM13E2X_SDK_SYMBOLS}'}` and `IO_PA2` is known (hence not in
  `unknown_symbols`).
- **Command-line `--define`/`--undefine` layer on top.** An explicit `--undefine` overrides a
  cproject `--define`: the undefine is removed from the known set so `unifdef` actually drops it.

The output gains two fields: `defines_source` (`"cproject (inferred)"` or
`"cproject (inferred) + command line"`) and `unresolved` (the `${...}` symbols). The rest of partial
mode (`unifdef -b` + the conditional walk) is shared with plain `--partial` via a new
`_run_partial_from` helper.

### Feature 2 — `--make-dir DIR` for a plain repo Makefile

`_level_make` runs `make -s -n -B` in the repo (the `-B`/`--always-make` flag forces make to
evaluate even for up-to-date targets, which is what a dry run needs; `-n` writes nothing), extracts
the compile line naming FILE, then runs the compiler's preprocessor exactly like the `build` and
`sdk_make` levels. `level` is `"make"`. Details:

- The recipe's compiler may be a bare name on PATH (`gcc`), so the compile-line matcher accepts an
  existing path **or** a name resolved by `shutil.which` (this also skips `echo` recipe lines).
- The source token is relative to the repo, so it is resolved against `make_dir`, and makefile-
  relative `-I` paths are made absolute.
- Verified: a temp-repo Makefile built with `gcc` produces `level=make`, `-DFEATURE` in the command,
  line 2 `true` / line 4 `false`, and **0 files changed** in the repo.

### Feature 3 — the GNU family in `platforms.py`

`gcc`, `cc`, and any `*-gcc` cross (e.g. `arm-none-eabi-gcc`) resolve to the `gnu` family
(`family_kind="gnu"`, `unit="byte"`), which the front end preprocesses with the clang `-E -dD -C`
path. `for_compiler` special-cases a trailing `-gcc` so `arm-none-eabi-gcc` maps to `gnu` rather
than an unknown family token.

### Defects found and fixed this review

1. **`_load_platforms` loaded the shared copy, not the local one.** The loader inserted both
   directories onto `sys.path` and imported whichever Python found first — the shared
   `~/utilities/firmware/platforms.py`, which had no `gnu` entry, so `for_compiler("gcc")` raised
   `KeyError`. Rewrote it to mirror `_load_project_facts`: prefer the local copy, fall back to the
   shared one.
2. **`_active_status` branch tracking was inconsistent.** The frame tuple's second element was used
   both as "any branch taken so far" and "current branch active", so an `#else` after a taken
   `#ifdef` stayed `active` and its (dropped) lines reported `compiled=true`. Rewrote each frame as
   `(parent_active, any_taken, branch_active)` and made `current_active()` return True only when
   every frame's `branch_active` is True. Fixed by hand-verifying the io.c `#else` branch and the
   gcc `#ifdef/#else` repo case.
3. **`_run_partial_from` re-defined undefines.** `_level_partial` put undefines into the `known`
   set, so `unifdef` received both `-UIO_UC4` and `-DIO_UC4`; the `-D` won and the wrong branch was
   kept. Split the two: `known` carries only defines, `undefs` only undefines (unifdef applies `-U`
   then `-D`). Also taught the conditional walk's `unknown_in` that an explicitly `--undefine`d
   symbol is *known* (not unknown), so `--undefine IO_UC4` makes line 24 `false` rather than `null`.

### Test results (review 3)

```
$ python3 -m unittest test_preprocess test_preprocess_review1 test_preprocess_review2 \
    test_preprocess_review3 test_backends -v
Ran 19 tests — OK
```

All 19 tests pass: 6 `test_preprocess`, 3 `test_preprocess_review1`, 2 `test_preprocess_review2`,
4 new `test_preprocess_review3`, and 4 `test_backends`. `test_backends` (the `struct_layout` split
contract) still passes.

> Note: `test_backends.py::test_unknown_compiler_is_an_error_not_a_guess` originally asserted that
> `for_compiler("/usr/bin/gcc")` raises `KeyError` — the D10-era assumption that gcc is unknown.
> Round 3 makes gcc a first-class family (required by `test_preprocess_review3::test_gnu_family`),
> so the two assertions are mutually exclusive. This conflict was introduced by the tool itself
> (adding the `gnu` family). The owner resolved it by changing the committed test to
> `for_compiler("/usr/bin/sdcc")` — a genuinely-unknown compiler — with the comment
> `# unknown family; gcc became "gnu" in preprocess round 3`, preserving the test's actual intent
> (an unrecognized compiler is an error, never a guess). No passing behaviour of the tool changed.

## Fixes applied this session

1. **`_shape` reverted to a coarse pre-filter** (identifiers → `W`); precise pairing moved to
   identifier-overlap in `_pair_expanded`. The earlier `W->W.W[W] = W ? W : W` shape collapsed
   `TIMG4->COUNTERREGS.CCACT_01[IO_CC] = p ? ... : ...` to the same skeleton as unrelated lines.
2. **`_pair_expanded`** — identifier-overlap scoring with exact-sequence tiebreak and position
   interpolation through anchors. Fixed `test_io_c_178_expanded` (line 178 now maps to
   `TIMG4->COUNTERREGS.CCACT_01[0] = p ? ((uint32_t)0x00000010U) : ((uint32_t)0x00000008U);`).
3. **CLI parsing** — for `--partial` and `--sdk-make` the first positional is the source file,
   not a project directory.
4. **Stale build exit code** — changed from `EXIT_ERROR` (2) to `EXIT_STALE` (3).
5. **`--sdk-make` compile-line extraction** (`_sdk_make_compile_line`) — now skips `echo` lines
   (only real commands start with an existing executable path), resolves source tokens relative to
   the makefile directory, and strips the trailing source token from the flags.
6. **`--sdk-make` relative `-I` paths** — makefile-relative include paths are made absolute
   relative to the makefile directory (the compiler runs from a temp dir).
7. **`_level_sdk_make`** — now receives `platforms`; drops the trailing source token from flags.
8. **`_build_rows`** — `compiled_lines` now includes both `.loc` and `-dD`-marker lines; added the
   function-signature and consecutive-directive heuristics; `compiled` is computed from
   `compiled_lines`.
9. **Exact identifier-sequence tiebreak** in `_pair_expanded` — fixed `test_cl2000_build_level_runs`
   (line 162 maps to `DAB_setupSFRA();`, not the prototype `void DAB_setupSFRA();`).

## Test results

```
$ python3 -m unittest test_preprocess -v
test_header_define_branch_by_resolved_path ... ok
test_io_c_178_expanded ... ok
test_stale_build_stops ... ok
test_known_and_unknown_symbols ... ok
test_errata_block_not_compiled_in_release ... ok
test_cl2000_build_level_runs ... ok

Ran 6 tests — OK

$ python3 -m unittest test_backends -v
test_family_from_compiler_name ... ok
test_unknown_compiler_is_an_error_not_a_guess ... ok
test_backends_export_the_same_function ... ok
test_front_end_has_no_compiler_specific_parsing ... ok

Ran 4 tests — OK
```

`test_backends` (the `struct_layout` split contract) still passes: the front end
`preprocess.py` has no compiler-specific parsing, and `backends/clang.py` +
`backends/ti_legacy.py` export the same functions.
