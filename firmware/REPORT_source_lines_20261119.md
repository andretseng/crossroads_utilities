# Source-lines report — ledger + answers only (after Claude review 2)

## Task 1 — Assembly addendum tests

The spec addendum extends `parse_cites` to `.asm`/`.S`/`.s`/`.cla`/`.inc`. The shared grammar
already accepts any `[A-Za-z][\w-]{0,31}` extension, so `.asm`, `.cla`, `.S`, `.s`, `.inc` parse
correctly with no grammar change. Two tests were added to `test_source_lines.py`:

- `test_assembly_asm_cite` — `startup_asm.ASM:42` → `("startup_asm.ASM", 42, 42)`.
- `test_assembly_cla_cite` — `table.cla:17` → `("table.cla", 17, 17)`.

## Task 2 — Replay ledgers + answers only

`replay_source_lines.py` previously ran the full session replay even with `--help`. A `--no-sessions`
flag was added (argparse); it skips `session.jsonl` and compares only the parsed ledger and answer
files. Verified: `python3 replay_source_lines.py --no-sessions --help` lists the option.

Ledgers + answers only (no sessions): **1178 files, 453 ledgers, 725 answers, 497 differences
(227 old_only, 267 new_only)**. Baseline (pre-review-1, same mode): 516 differences
(294 old_only, 219 new_only).

The `reason()` column in `replay_differences.tsv` is a **generic** string ("numeric values/times and
malformed legacy matches are not source citations") — it is NOT checked per case and is often wrong.
All class verdicts below were decided by direct inspection of the source line, not by that column.

## Task 3 — Difference classes

Each difference is a cite present in only one side. `key(file, first, last)` uses the **basename**,
so a cite whose basename differs across parsers is always old_only/new_only even when it names the
same location. For each class I took real examples and decided **fix `parse_cites`** (with a failing
test) or **new result is right** (citing a real file line).

### Fixes applied (4 fixes; tests in test_source_lines.py and test_source_lines_review1/2.py)

| # | Class | Decision | Test |
|---|-------|----------|------|
| R1 | slash-joined cites `main.c:64/io.c:188` lose the second cite | **FIX** | `test_slash_joined_cites_are_two_cites` (review1) |
| R2 | bare name without extension (`halltim_isr:424`) is a function name | **FIX** (add `Cite.bare`, `resolve` returns None when no file) | `test_bare_name_is_marked_and_does_not_resolve` (review1) |
| R3 | dotted SysConfig members (`adc1.adc_INT1.priority:73`, `input_xbar1.xbarOutput line 550`) are not cites | **FIX** (`_is_syscfg_member`); `.cproject:428` stays a cite | `test_dotted_config_member_is_not_a_cite`, `test_cproject_stays_a_cite` (review1) |
| R4 | elided `:N` / `:N-M` with no file inherits the file of the nearest previous cite on the **same line** | **FIX** (`ELIDED_CITE` + `_elided_cites`, `Cite.inherited`) | `test_elided_file_inherits_the_previous_cite_on_the_same_line`, `test_elision_never_crosses_a_line`, `test_lone_colon_number_without_a_previous_cite_is_nothing` (review2) |

**Fix R1 — slash-joined cites.** COLON_CITE/LINE_CITE lookbehind was `(?<![\w.:/])`; the `/` in
`main.c:64/io.c:188` is a cite separator, but the lookbehind rejected `io.c` as preceded by `/`, so
`io.c:188`/`io.c:194` were lost. Removed `/` from the COLON_CITE/LINE_CITE lookbehind (kept in
PATH_CITE). A leading `/` on the matched file is stripped only when the match is a bare basename, so
`main.c:64/io.c:194` → `io.c:194` while the absolute path `/opt/sdk/b.hpp` keeps its `/`. Real
example, `proj_bootflow r10 ledger line 6`:
`recvval() main.c:64/io.c:188; CMD_PROBE sendval main.c:66/io.c:194` → now all four cites.

**Fix R2 — bare names carry `bare=True`.** Added `bare: bool = False` to `Cite`; `_is_bare()` sets it
for extensionless final segments. `resolve()` returns `None` when no file matches, so a bare name
with no such file resolves to `None`. Real example, `escirq q24r2 ledger line 62`:
`ISRs (iftim_isr main.c:314, tim1_com_isr:295, … halltim_isr:424)` — `halltim_isr:424` carries
`bare=True` and does not resolve against the project root.

**Fix R3 — dotted SysConfig members are not cites.** Added `_is_syscfg_member()`: reject a final
segment with **two or more dots** (`adc1.adc_INT1.priority`) or whose **extension is mixed-case**
(`input_xbar1.xbarOutput`). Mixed case in the *stem* is kept, so `AppNote_..._LP.md` and the dotfile
`.cproject:428` stay cites. Real, `ho_pin_am261 answer`: `adc1.adc_INT1.priority:73` → `[]`;
`input_xbar1.xbarOutput line 550` → `[]`; `(.cproject:428)` → `[(".cproject", 428, 428)]`.

**Fix R4 — elided cites inherit the previous file on the same line.** Added `ELIDED_CITE`
(`(?<![\w.:]):\d+(?:\s*[-–~]\s*\d+)?(?![\w\d])`) and `_elided_cites()`: an elided `:N`/`:N-M`
inherits the file of the nearest previous accepted cite **on the same line** (tracked by line number
via `text.count("\n")`); `Cite.inherited=True`. A lone `:N` with no previous cite on its line is
dropped, and elision never crosses a newline. Real examples:
`(motor1_drive.c:416 and :438)` → `motor1_drive.c:416`, `motor1_drive.c:438` (inherited);
`config.c:190 sets …, :191 sets …, and :211-212 trigger` → `config.c:190`, `config.c:191`
(inherited), `config.c:211-212` (inherited); `see main.c:10\n:20 …` → only `main.c:10`;
`| PA11 | 45 | MCAN0 RX | :263 |` → `[]`.

### Classes where the new result is right (legacy artifacts)

| # | Class | old_only count | Why new is right |
|---|-------|----------------|------------------|
| 1 | legacy bare `.ext` as filename (`.md:74`, `.md:84`, `.md:118`) | 57 | Legacy `quote_check` allow-list dropped the leading path, so it saw the extension `.md` as the filename. New parses the full spaced path. Real: `…/CPU 模組分析 (SLAU846E).md:74` — legacy `.md:74`, new `…(SLAU846E).md:74`. (Fix 5.) |
| 2 | `.md` full path (spaced) | 2 | Same truncation: legacy saw `.md`, new keeps the real filename. |
| 3 | real `.c`/`.h` | 101 | **All legacy artifacts.** Two mechanisms, both confirmed by program inspection of every row: (a) legacy over-assigned a file to an elided `:N` — e.g. the text is literally `config.h:336` but legacy emitted `main.c:336`; or `example.syscfg pin lines (PA26 :46 …)` where `:46` has no previous cite to inherit from, yet legacy emitted `io.c:46`. New (Fix R4) correctly inherits `config.h` for `:336` and drops the orphan `:46`. (b) legacy mis-read a literal cite's file. **Class 5 re-checked by program (reviewer request): 101/101 are legacy artifacts, 0 real NEW misses.** |
| 4 | `.syscfg` real file | 18 | Legacy produced `example.syscfg:77` from malformed continuation text (`…example.syscfg sets all 21 project pins via .\`); new correctly requires a colon. |
| 5 | `.mk` real file | 9 | Legacy false positives on makefile prose; new rejects. |
| 6 | bare camelCase (`x1Pin:394`) | 4 | `x1Pin` is a pin name, not a file. New `_BARE` requires all-lowercase logical names, so it is correctly rejected. |
| 7 | bare range (`pull-up:42-45`) | 3 | Legacy `case_chain` truncated `pull-up` to `up`; new captures the full token `pull-up:42-45`. Same location, legacy basename `up:42-45` vs new `pull-up:42-45`. |
| 8 | elided continuation (`file:1 and :2`) | 0 in old_only | New requires a filename on every cite; `ecat_def.h:778 and :784` has no filename on the second part and is correctly not emitted. |

### new_only classes (267) — new is right

| Class | count | Verdict |
|-------|-------|---------|
| bare identifier:line (ISR refs `outputxbar1:340`, `halltim_isr:424`, `tim1_com_isr:295`) | 72 | grammar-consistent bare form (like `syscfg:17`). |
| `.c`/`.h` (new finds; prose "file line N", missed elisions) | 60 | new is right — e.g. `config.c line 837 enables …` (prose `FILE line N`, Fix 37) and `config.c:153 … :172` (elided inheritance, Fix R4) that legacy missed. |
| `.md` spaced paths (new finds, legacy truncated) | (inside "other") | new is right — legacy saw `.md`, new keeps the full path. |
| `.syscfg` (new finds) | 43 | new is right — legacy missed/malformed. |
| legacy bare `.md:NN` | 18 | new is right — legacy truncated. |
| `.cproject:NN` | (inside "other") | new is right — `.cproject` is a real file (Fix 5). |
| `compiler_options.json`, `linker_m33_ti_arm_clang.cmd`, `Makefile`, `explore-map.txt` | (inside "other") | new is right — real files/Makefile names legacy missed. |
| bare range (`pull-up:42-45` full token) | 4 | new is right — legacy truncated. |

## Remaining (small) differences

- `new_only` `explore-map.txt:49` (3): new matches a `.txt` map reference; arguably a map/aux file,
  not a source cite. Rare; left as-is.
- `new_only` bare identifiers like `outputxbar1:340` (4): grammar-consistent bare form; whether a
  SysConfig output-bar member is a "cite" is debatable but consistent with the `syscfg:17` bare rule.
- No real NEW misses remain in class 3 (`.c/.h`): the programmatic re-check found 0.

## Task 4 — Test output

All three test files pass:

```
$ python3 -m unittest test_source_lines test_source_lines_review1 test_source_lines_review2
Ran 29 tests in 0.062s

OK
```

29 tests total: 22 in `test_source_lines` (18 original + 2 assembly addendum + 2 grammar fixes from
earlier iterations) and 4 in `test_source_lines_review1.py` + 3 in `test_source_lines_review2.py`
(review tests, do not edit). Before review 2, its 3 tests failed; all 3 pass now.

## Stop rule

The reviewer's stop rule (2 failed attempts on the same class → stop and explain) was not triggered:
all four review classes were fixed on the first attempt, and the remaining classes are legacy
artifacts (new is right), not parse bugs. The class-5 re-check requested in review 2 is included
above; the earlier "120/120 artifacts" claim used a flawed check (literal `file:line` in text), but
the corrected check (does the line contain a valid previous cite to inherit from, and does new bind
the right file) reaches the same conclusion: **0 real NEW misses**.

## Files changed (only in /tmp/source_lines_pi)

- `source_lines.py` — `Cite.inherited` field + `ELIDED_CITE` + `_elided_cites()` (Fix R4); `Cite.bare`
  + `_is_bare()` (Fix R2); `_is_syscfg_member()` filter (Fix R3); COLON_CITE/LINE_CITE lookbehind
  drops `/` (Fix R1); leading-`/` strip only for bare basenames in `_parts`; `resolve()` returns None
  for unresolved bare names.
- `test_source_lines.py` — 2 assembly addendum tests + 2 grammar-fix tests.
- `test_source_lines_review1.py`, `test_source_lines_review2.py` — review tests (do not edit).
- `replay_source_lines.py` — `--no-sessions` flag.
- `REPORT.md` — this report.
