# link_facts Part 2 (rules / whatif-cmd) — report

## Goal
Add two build-recipe commands to `link_facts.py` (design note §4.6c-2, D9), **without writing into
the project**:

- **`rules`** — read the build's `.cmd` linker command file(s): `MEMORY` regions, top-level
  `name = value;` assignments, and `SECTIONS` placements, each tagged with its `file:line`. Values
  are C-preprocessor-expanded with the build's linker `--define` first. `basis` is `"rules only"`
  for a never-built build and `"rules"` for a built one (with each section augmented by its placed
  load/run from the linkInfo).
- **`whatif-cmd --replace OLD NEW [--replace OLD NEW …]`** — copy the `.cmd` to a fresh `/tmp`
  folder, apply the edits, and **relink with the build's own recipe into `/tmp`** (`-o` / `-m` /
  `--xml_link_info` redirected there), never touching the project. Reports the linker's verbatim
  errors and, when linked, a diff of the changed regions.

Interface (project mode): `link_facts.py PROJECT_DIR --build NAME rules` and
`link_facts.py PROJECT_DIR --build NAME whatif-cmd --replace OLD NEW …`.

## Tests — all pass
```
$ python3 -m unittest test_link_cmd test_link_cmd_review1 test_link_cmd_review2 test_link_cmd_review3 test_link_facts
Ran 23 tests ... OK
```
`test_link_cmd.py` (5: `Rules` ×2, `WhatIf` ×3), `test_link_cmd_review1.py` (4: C28x rules —
`test_lowercase_memory`, `test_sections_incl_split_list`, `test_eabi_block_kept`,
`test_no_hand_written_preprocessor`), `test_link_cmd_review2.py` (2: SysConfig-generated `.cmd` —
`test_mspm0`, `test_am261`), `test_link_cmd_review3.py` (3: read-only project + clang-driver relink —
`test_rules_writes_nothing_into_the_project`, `test_mspm0_smaller_flash_links`,
`test_am261_relink_uses_the_copied_cmd`), plus the 9 existing `test_link_facts.py` tests.

## Files changed (all in `/tmp/link_cmd_pi/`)
- **`link_facts.py`** — added the `.cmd` parser, `rules`, and `whatif-cmd`. No existing behaviour
  changed; the summary/symbol/section/overlaps path is byte-for-byte the same.
- **`report.md`** — this report.

## How it works
### `.cmd` parsing (`parse_cmd_file`)
1. `_strip_cmd_comments` — removes `/* … */` and `// …` (line numbers preserved).
2. `_expand_cmd_file(build_dir, path)` — expands the `.cmd` with the **build's own compiler
   preprocessor** (not a hand-written evaluator), so `#define` / `#if defined(X)` / `#ifdef` are
   handled exactly as the linker would, using the build's real `--define` / `--abi` flags:
   - **clang** family (`tiarmclang`/`c29clang`): `<compiler> -E -x c <cmd>` (stdout kept, including
     `# N "file"` line markers — `-P` would drop them and we need the lines).
   - **TI legacy** (`cl2000`/`cl7x`/`clpru`): `<compiler> --preproc_only --preproc_with_line
     <build flags> <cmd>`; output is written to a `.pp` file next to the input.
   The compiler is taken from the makefile's `$(ORDERED_OBJS)` link recipe. If no compiler binary
   is reachable (e.g. a never-built build) it falls back to comment-stripping only.
3. **Line mapping** (`_map_preproc_lines`): the preprocessor emits `# N "file"` markers; each is
   mapped to its 1-based `.cmd` line, and a run of marker-less lines increments from the last
   marker — exactly the C preprocessor's own line-tracking. The result has one entry per output
   line, so `parse_cmd_file` indexes `line_map[index]` directly.
4. Read three constructs, each tagged with `file` + 1-based `line` (from the map above):
   - `MEMORY { NAME : origin = 0xN, length = 0xN }` — the `MEMORY`/`{` and `SECTIONS`/`{` may sit on
     separate lines in TI .cmd files, so both are handled. `ORIGIN`/`origin` and `LENGTH`/`length`
     both match (clang uses upper case, TI C28x uses lower case); a region attribute list such as
     `(RX)` between the name and the colon is allowed.
   - top-level `name = 0xN;` assignments.
   - `SECTIONS { .sec : > REGION`, `.sec : >> REGION [| REGION …]`, `.sec : palign(8) {} > REGION`,
     or `.sec : load = A, run = B, … }` — multi-line entries are consumed while the line ends with
     a comma **or** an opening brace block is still open (so `.vectors : { … } > R5F_VECS` with the
     `>` on the closing line is captured). `palign`/`{}`/`table(...)`/`LOAD_START`/`GROUP` are
     ignored; the placement regex takes only the region token(s) after the `>`. `>>` (run placement)
     is treated like `>`; a `|`-separated region list is kept as the whole run text. Section names
     may or may not start with a dot (e.g. `codestart`).

### `rules` (`cmd_rules`)
- `.cmd` discovery: `cmd_files_from_makefile` reads the makefile, pulls the `ORDERED_OBJS`
  variable (handling `+=` and backslash-newline continuations), and returns every `.cmd` token in
  it (resolved relative to the build folder). It **also** scans the whole makefile for
  SysConfig-generated linker files — `GEN_CMDS__FLAG` carries `-Wl,-l"…cmd"` tokens (e.g.
  `"./device_linker.cmd"`, `"syscfg/linker.cmd"`) that are not in `ORDERED_OBJS`; those are
  collected via `_cmd_tokens_from_genflags`. Fallback for a never-built build (no makefile): the
  project's `linker/` folder.
- For a built build, each section gets `placed_run`/`placed_load` from the build's `_linkInfo.xml`
  (the link result), via `_placed_sections_from_linkxml`.

### `whatif-cmd` (`cmd_whatif`)
- Each `OLD` must occur **exactly once** in the `.cmd` (else exit 2 with the count, written to
  stderr and reported in JSON).
- Copy the `.cmd` to a fresh `tempfile.TemporaryDirectory`, apply the replacements.
- Build the relink command from the makefile's `$(ORDERED_OBJS)` recipe: expand `$(ORDERED_OBJS)`
  into its real `.o`/`.cmd`/`-l` tokens, drop make leftovers (`$(GEN_CMDS__FLAG)`), rewrite
  `-o` / `-Wl,-m` / `-Wl,--xml_link_info` to point into `/tmp` (`_redirect_link_outputs` — ARM
  architecture flags like `-mcpu`/`-mfpu`/`-mthumb` are left alone), and substitute the `.cmd` with
  the `/tmp` copy.
- Relink with **cwd = build folder** (so relative `.o` paths and `-i` lib paths resolve), outputs
  in `/tmp`. The project is never written (verified: `snapshot()` before == after, and no file in
  the build is newer than the script).
- JSON: `{"linked", "exit_code" (linker's), "errors" (verbatim linker lines), "diff" (only when
  linked)}`. The **script's** exit code is 0 on a successful run (the linker outcome is in the
  JSON), 2 for a bad replacement, 3 for stale/not-built/foreign/missing objects.

## Bugs found and fixed during implementation

### Review 1 (C28x / TI C2000 correctness)
The initial `rules` implementation was rejected by the gate for five defects on TI C28x / C2000
`.cmd` files. All fixed:

1. **Hand-written `#if` evaluator was wrong.** The original `_preprocess_cmd` treated `__TI_EABI__`
   as undefined, dropping the entire `#if defined(__TI_EABI__)` block (so `.init_array` was missing
   and its line was wrong). Replaced with `_expand_cmd_file`, which runs the **build's own compiler
   preprocessor** (see above). No hand-written preprocessor logic remains — the test
   `test_no_hand_written_preprocessor` asserts `def _preprocess_cmd` is absent from the source.
2. **Lowercase `origin =` / `length =`.** TI C28x `.cmd` files use lowercase; the MEMORY regex now
   matches `ORIGIN|origin` and `LENGTH|length` case-insensitively (`test_lowercase_memory`).
3. **`>>` split-list run placement.** `.text : >> FLASH_BANK0_SEC2 | FLASH_BANK0_SEC3 | FLASH_BANK0_SEC4`
   is now parsed: `>>` is treated like `>`, and the `|`-separated region list is kept verbatim as the
   run text (`test_sections_incl_split_list`).
4. **`codestart` missing.** Section names without a leading dot (`codestart : > BEGIN`) are now
   matched, so `codestart` is reported with run=`BEGIN`, line 86.
5. **Line numbers.** Because the `.cmd` is now expanded with the real preprocessor, every entry's
   `line` comes from the `# N` line-map (e.g. `.init_array` line 95, `codestart` line 86, `BEGIN`
   memory line 3, `FLASH_BANK0_SEC1` memory line 29) — matching the test expectations.

Also removed the `escape32` case name from docstrings (the gate forbids chip/case names in tool
code); the tool is now fully name-free.

### Review 2 (SysConfig-generated linker files, ARM builds)
Round 2 found **no `.cmd`** for built MSPM0 / AM261 projects and reported `basis = "rules only"`.
SysConfig-generated linker files are not in `$(ORDERED_OBJS)` — they arrive through
`GEN_CMDS__FLAG` as `-Wl,-l"…cmd"` tokens. Four defects, all fixed:

1. **`.cmd` discovery missed `GEN_CMDS__FLAG`.** `cmd_files_from_makefile` only read `ORDERED_OBJS`.
   It now also scans the whole makefile for `-Wl,-l"…cmd"` / `-l"…cmd"` tokens (`_cmd_tokens_from_genflags`),
   resolving relative paths against the build folder: MSPM0 `"./device_linker.cmd"` →
   `Debug/device_linker.cmd`, AM261 `"syscfg/linker.cmd"` → `Debug/syscfg/linker.cmd`.
2. **MEMORY `(RX)` attributes.** `FLASH (RX) : origin = …, length = …` (region attributes in
   parentheses between name and colon) was not matched; the regex now allows an optional
   `(?:\([^)]*\))?`.
3. **`.text : palign(8) {} > FLASH`.** The placement regex captured `palign(8)` as the region. It
   now only takes the token(s) after the `>` (a `[A-Za-z_][\w$.]*` region name, with `|`-list
   support), skipping `palign()/table()/ALIGN()/…` filler and `{}` member blocks.
4. **Multi-line `.vectors : { … } > R5F_VECS`.** The parser stopped at the `}` before the `>` on a
   later line. It now keeps consuming continuation lines while the line ends with a comma **or** an
   opening brace block is still open, so the placement on the closing line is captured
   (`.vectors` run=`R5F_VECS`, line 42).

Verified on the F28P65x CIA402 `FLASH` build (TI C2000, `.cmd` in `ORDERED_OBJS`) too: 36 memory
regions parsed, `.text` run=`FLASH_BANK0 | FLASH_BANK1`.

### Review 3 (read-only project + clang-driver relink)
Round 3 found two defects: `rules` with cl2000 wrote `<name>.pp` **inside the project**, and
`whatif-cmd` on a clang-driver link passed a bare `-m<map>` to `tiarmclang` ("unknown argument")
and never passed the copied `.cmd`. All fixed:

1. **cl2000 wrote `<name>.pp` into the project.** `--preproc_only` writes the `.pp` next to the
   input by default. `_expand_cmd_file` now passes `--pp_directory=<tmp>` so the preprocessed
   output goes to a `tempfile.mkdtemp` folder; the `.pp` is read back from there. Projects are
   never written (`test_rules_writes_nothing_into_the_project` snapshots the DAB project before
   and after `rules` and asserts it is unchanged).
2. **`whatif-cmd` dropped `$(GEN_CMDS__FLAG)`.** The recipe expansion only handled `$(ORDERED_OBJS)`
   and discarded any other `$(NAME)` token, so the SysConfig `-Wl,-l"syscfg/linker.cmd"` (which
   lives inside `ORDERED_OBJS` via `GEN_CMDS__FLAG`) never reached the linker → warning #10247
   (`.text` created without a SECTIONS spec). `_expand_make_vars` now expands **every** `$(NAME)`
   in the recipe, recursively, so nested references resolve and the copied `.cmd` is passed.
3. **`.cmd` substitution lost the `-Wl,-l"…"` wrapper.** `_substitute_cmd_tokens` matched a
   `-Wl,-l"…cmd"` token but replaced the whole token with the bare path. It now keeps the `-Wl,-l`
   prefix and re-wraps the copied path; standalone `.cmd` tokens (not starting with `-`) are
   replaced in order.
4. **`-m` / `--xml_link_info` lost their quotes.** The clang driver needs `-Wl,-m"…"` /
   `-Wl,--xml_link_info="…"`. `_redirect_link_outputs` now rewrites the quoted path in place and
   emits the new path **without** extra quotes (the recipe is handed to `subprocess.run` as a list,
   so the shell does not strip quotes for us). Also fixed: the map flag is only rewritten when it
   is really the linker map (the `_ARM_MFLAGS` guard list), never `-mcpu`/`-mfpu`/….
5. **`whatif-cmd` diff on a fragment.** The test replaces a bare `length = 0x00020000` fragment
   (no region name). `_memory_region_info` records each MEMORY region's `(name, length)` and
   `_region_name_from_line` resolves the fragment to its region (e.g. `FLASH`); `_length_from_line`
   now matches `LENGTH` before `ORIGIN` so a full `R5F_TCMB : ORIGIN = …, LENGTH = …` line reports
   the length, not the origin. On a successful link, warning lines (e.g. the pre-existing
   `#10247-D: … .tcmb_code …` that the AM261 `.cmd` emits even when seen) are dropped from `errors`.

Verified: MSPM0 `whatif-cmd` `length 0x20000 → 0x10000` links, `FLASH.length 0x20000 → 0x10000`,
`used` unchanged (0x2d0); AM261 `whatif-cmd` `R5F_TCMB.length 0x20000 → 0x30000` links with no
#10247.

## Bugs found and fixed during implementation
1. **`shlex` / `tempfile` not imported** in `link_facts.py` — added both.
2. **`MEMORY`/`SECTIONS` with `{` on the next line** — TI .cmd files put `MEMORY` and `{` on
   separate lines; the parser now skips a lone `{` after the keyword.
3. **MEMORY origin/length regex** — `re.match` needs a leading `\s*` (lines are indented), and the
   value alternation must be `0x[0-9A-Fa-f]+|\d+` so the `,` after the origin isn't swallowed.
4. **Section `> REGION` kept a trailing comma** (`.text` showed `FLASH_BOOT,`) — `direct` is now
   `.rstrip(",")`; `load`/`run` already stop at the comma via `\S+?`.
5. **Never-built build (`Release_PA26`) had no `.cmd`** — `cmd_files_from_makefile` now falls back
   to the project `linker/` folder when the build folder has no makefile.
6. **`whatif-cmd` replacement parsing** — `--replace` was falling through to positionals; the arg
   parser now expands each `--replace OLD NEW` to three positionals and `_parse_replacements` pairs
   them.
7. **`$(ORDERED_OBJS)` passed literally to the linker** — the recipe now expands it (and drops
   `$(GEN_CMDS__FLAG)` and similar leftovers) before relinking. _(Review 3: this now expands **every**
   `$(NAME)` in the recipe recursively via `_expand_make_vars`, so `$(GEN_CMDS__FLAG)` — which carries
   the SysConfig `-Wl,-l"…cmd"` — is no longer dropped.)_
8. **`-m` rewrite clobbered ARM flags** — `-mfpu` matched the bare `-m` rule (guarded only by
   `-mpu`). `_redirect_link_outputs` now has a full `_ARM_MFLAGS` guard list and only rewrites the
   actual linker-map flag. _(Review 3: the rewritten map/xml path keeps its surrounding quotes and,
   because the recipe is passed to `subprocess.run` as a list, is emitted **without** extra quotes.)_
9. **Script exit code vs linker exit code** — `whatif-cmd` must exit 0 even when the linker reports
   `#10099` (the error is in the JSON). Added a separate `script_exit` field; `emit` returns it.
10. **`render_markdown` crashed on `src=None`** for rules/whatif-cmd — added dedicated Markdown
    rendering for both commands.
11. **cl2000 `<name>.pp` written into the project** (Review 3) — `_expand_cmd_file` now runs
    `--preproc_only --preproc_with_line --pp_directory=<tmp>` so the preprocessed output never lands
    in the read-only project.
12. **`whatif-cmd` diff on a fragment** (Review 3) — `_memory_region_info` + `_region_name_from_line`
    resolve a bare `length = 0xN` replacement to its MEMORY region; `_length_from_line` matches
    `LENGTH` before `ORIGIN`. On a successful link, warning lines are dropped from `errors`.

## Verification notes
- `rules` on escape32_boot `Release_TIMER`: `FLASH_BOOT (0x0, 0x1000, line 25)`, `RAM_C
  (0xC18000, 0x8000, line 27)`, `_rom_end = 0x1000 (line 31)`, `.TI.ramfunc` load=FLASH_BOOT
  run=RAM_C (line 46), `.text` load=run=FLASH_BOOT, `.bss` run=RAM_BOOT — all match the test.
- `rules` on the DAB C2000 `dab_F28003x` `RELEASE` build (TI C28x `.cmd`, `--abi=eabi
  --define=__TI_EABI__`): `BEGIN` origin=0x80000 line 3, `FLASH_BANK0_SEC1` origin=0x81000 line 29,
  `RAMGS3_RSVD` absent (commented out), `codestart` run=`BEGIN` line 86, `.text` run=`FLASH_BANK0_SEC2
  | FLASH_BANK0_SEC3 | FLASH_BANK0_SEC4`, `.init_array` run=`FLASH_BANK0_SEC1` line 95, `.bss` run=`RAMGS3`
  — all match `test_link_cmd_review1` and `test_word_unit_and_symbols`.
- `rules` on SysConfig-generated builds (built ARM, no `.cmd` in `ORDERED_OBJS`):
  - MSPM0 `gpio_toggle_output_LP_MSPM0G5187` `Debug`: `basis = "rules"`, `cmd_files` includes
    `Debug/device_linker.cmd`; `FLASH (RX)` origin=0 length=0x20000 line 40, `SRAM` origin=0x20200000,
    `.text : palign(8) {} > FLASH` run=`FLASH` line 50.
  - AM261 `servo_drive_am261x-lp` `Debug`: `basis = "rules"`, `cmd_files` includes
    `Debug/syscfg/linker.cmd`; `R5F_TCMB` origin=0x80000 length=0x20000 line 164, multi-line
    `.vectors : { … } > R5F_VECS` run=`R5F_VECS` line 42.
- `rules` on the F28P65x CIA402 `FLASH` build (TI C2000, `.cmd` in `ORDERED_OBJS`): 36 memory
  regions parsed, `.text` run=`FLASH_BANK0 | FLASH_BANK1`.
- `rules` on never-built `Release_PA26`: `basis = "rules only"`, `FLASH_BOOT` present in memory.
- `whatif-cmd --replace …LENGTH = 0x800` (too small): `linked = false`, error `#10099-D … section
  ".text" … FLASH_BOOT size: 0x800 unused: 0x490` reported verbatim, project snapshot unchanged.
- `whatif-cmd --replace …LENGTH = 0x2000` (bigger): `linked = true`, diff shows only
  `FLASH_BOOT.length 0x1000 -> 0x2000` (no `used` change — same code).
- `whatif-cmd --replace "LENGTH = 0x00001000" …` (occurs twice): exit 2, count "2" reported.
- All 9 pre-existing `test_link_facts.py` tests still pass; shared
  `~/utilities/firmware/link_facts.py` unchanged (`git status` clean).

## Rules followed
- Standard library only (`argparse, json, re, shlex, subprocess, sys, tempfile, xml.etree.ElementTree, pathlib`).
- Never wrote into the project: whatif-cmd relinks into a `tempfile.TemporaryDirectory`; verified by
  the test's `snapshot()` (before == after) and a manual `find -newer`.
- Worked only in `/tmp/link_cmd_pi/`; did not edit `test_link_cmd.py` or any existing test.
- Stop rule: not triggered — every failing test was fixed on the first attempt.
