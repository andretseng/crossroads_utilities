# REPORT: link_facts.py (T6) — implementer = Pi (Ornith)

Date: 2026-11-19. Work folder: `/tmp/link_facts_pi/`. Standard library only, Python 3.10.
`link_facts.py` written; `test_link_facts.py` NOT edited.

## Test output (after Review 1 fixes)

```
$ cd /tmp/link_facts_pi && python3 -m unittest -v test_link_facts
test_overlaps_and_over_full (test_link_facts.Fixture) ... ok
test_summary_regions_and_entry (test_link_facts.Fixture) ... ok
test_symbol_section_by_containing_range (test_link_facts.Fixture) ... ok
test_main_symbol (test_link_facts.RealAM13) ... ok
test_makefile_names_the_map_and_old_one_is_ignored (test_link_facts.RealAM13) ... ok
test_no_debug_overlaps_in_real_image (test_link_facts.RealAM13) ... ok
test_stale_build_stops (test_link_facts.RealAM13) ... ok
test_map_name_comes_from_makefile_not_from_project_name (test_link_facts.RealAM261) ... ok
test_word_unit_and_symbols (test_link_facts.RealC2000) ... ok

Ran 9 tests in 1.8s

OK
```

All 9 pass (3 new tests added by Claude in Review 1: `test_no_debug_overlaps_in_real_image`,
`test_map_name_comes_from_makefile_not_from_project_name`, and the overlap-count/assertNotIn
extensions to `test_overlaps_and_over_full`).

## What changed for Review 1

Three defects, each matched by a test:

1. **Map/XML come from the makefile linker options, not newest-mtime.**
   Added `makefile_link_names(build_dir)`, which reads `<BUILD>/makefile`, joins line
   continuations, and regex-matches the map (`-m"x"` / `-Wl,-m"x"`) and XML
   (`--xml_link_info"x"` / `-Wl,--xml_link_info"x"`) stems. The double quote is kept out of the
   regex literal (built with `chr(34)`) so it cannot close the raw string. In project mode the
   makefile-named files are used; newest-mtime is only the fallback when the makefile names none
   (and the `source`/report says so). Verified: AM13 → `escape32_boot_lp-am13e230x.map`,
   AM261 Debug → `ipc_notify_echo.Debug.map` (not the project name).

2. **`ignored` never lists the files in use.** `ignored` is now built from the actual in-use
   `xml_path`/`map_path` names: every other `.map` / `*_linkInfo.xml` in the build folder is
   ignored, the in-use pair is never. Verified: AM13 `ignored` contains only the old
   `ESCape32_boot_LP_AM13E230.map`; AM261 `ignored` is `[]`.

3. **Overlaps only between components placed in memory.** `LinkXML` now computes `placed_ids`
   — object-component IDs whose logical group (`logical_group_list`) is referenced by a
   `placement_map` `allocated_space/logical_group_ref`. `cmd_overlaps` restricts itself to those
   components, so `.debug_*` (whose logical groups are in no `allocated_space`) never appears.
   Verified on the real AM13 image: no `.debug*` section in overlaps.

Also: `summary` now reports both `xml` and `map` paths (the in-use pair) regardless of which is
the parsed `source`, since the tests assert both. The text-map fallback (`section`, `regions`,
`entry`) still works when no XML is present.

## Still checked by hand

- `--build` arg order with `--json` trailing; explicit `--xml`/`--map` mode; text-map entry
  parsing (fixed earlier: `_section` no longer mutates `self.text`).
- AM261 Debug: `map` = `ipc_notify_echo.Debug.map`, `xml` =
  `servo_drive_am261x-lp_r5fss0-0_linkInfo.xml`, `ignored = []`.
- Real AM13 `overlaps`: no `.debug*` names, `over_full = []`.
- No chip/case names in the script (generic `c2000`/`armllvm` family tokens only); read-only
  projects untouched; nothing committed.```

All 7 pass. The 3 `Real*` tests ran (both projects present), so the smoke tests were not skipped.

## What I checked by hand

- **Interface / arg parsing.** The contract is `link_facts.py PROJECT_DIR --build NAME <command> [names...]`
  and `link_facts.py --xml FILE [--map FILE] --unit byte|word16 <command>`. Because `--build NAME` is
  followed by a positional command, I parse arguments manually (not `argparse` positionals): flags
  `--build/--xml/--map/--unit/--json` consume their values, everything else is positional. `--build`
  takes the next token as its value so `summary`/`symbol`/`overlaps`/`section` land as the command.
- **Source selection.** Project mode prefers `_linkInfo.xml`, falls back to the text `.map` when no XML;
  the `source` field (`"xml"`/`"map"`) and the path reported match the source actually read. Text-map
  fallback verified against the real AM13 map: `MEMORY CONFIGURATION` → regions, `SECTION ALLOCATION
  MAP` → sections, `ENTRY POINT SYMBOL` → entry.
- **Old-map selection by makefile name.** `Release_TIMER/` holds both the current
  `escape32_boot_lp-am13e230x.{map,linkInfo.xml}` (2026-11-18) and the old
  `ESCape32_boot_LP_AM13E230.{map,linkInfo.xml}` (2026-11-16). I select the **newest** by mtime
  (alphabetical would wrongly pick the old one), and list every other `.map`/`*_linkInfo.xml` in the
  build folder under `ignored`. Verified: `data["xml"]` ends with `.../Release_TIMER/escape32_boot_lp-am13e230x_linkInfo.xml`,
  and the old `.map` appears in `ignored`.
- **Unit / compiler family.** Determined from the build makefile via `project_facts.compiler_family`
  on the `ti-cgt-<family>` token. AM13 → `tiarmclang`/armllvm → `byte`; DAB → `ti-cgt-c2000` → `c2000`
  → `word16`. C2000 addresses are 16-bit words; the script never mixes units. `main` = `0x835a7`,
  `_c_int00` = `0x840ad` (both already carry the thumb bit in the C2000 XML, returned as-is).
- **"not linked" rule.** A symbol absent from the symbol table → `found: false`,
  note `"not in symbol table: may be inlined or removed"`. A found symbol's section is the object
  component whose run range `[run, run+size)` contains the symbol address; when several contain it, the
  exact match (`address == run_address`) wins (needed so `g` → `.text.g`, not `.text.f`).
  `input_file` = the component's `input_file_ref` name (archive member for `.a`, object file otherwise).
- **Overlaps / over-full.** Overlaps = pairs of object components whose run ranges intersect within the
  same page (`0x120–0x140` for `.text.f`/`.text.g`); `over_full` = regions whose `used > length`
  (`RAM` in the fixture).
- **Staleness / build selection (via `project_facts.py`).** Imported `~/utilities/firmware/project_facts.py`
  (added to `sys.path`, read only — not copied). Build list + staleness + FOREIGN come from it.
  `Debug` (older than `.cproject`) → exit 3 + `ASK USER`. Unknown build → exit 3 + `ASK USER`.
- **JSON contract.** `--json` emits integers as numbers (not strings), matching the addendum exactly
  for `summary` / `symbol` / `overlaps`. Non-`--json` output is Markdown tables (entry, regions with
  used%, symbols, sections, overlaps, over-full).
- **Read-only respected.** Only read the two real projects and `~/utilities`; wrote only under `/tmp`.
  No chip/case names in the script (generic `c2000`/`armllvm` family tokens only).

## Files

- `/tmp/link_facts_pi/link_facts.py` — implementation.
- `/tmp/link_facts_pi/report.md` — this file.
- (Spec asked for `~/utilities/firmware/report_link_facts_20261119.md` too, but `~/utilities` is
  read-only for me; the report lives here instead.)

## Open points / notes

- **Staleness scope is cheap and limited.** Per the spec's "[check what is cheap and say what you used]",
  staleness reuses `project_facts.staleness`, which compares the build's generated rule files against
  `.cproject` and `*.syscfg`. It does **not** re-derive the "map/XML older than the newest object or
  source the build compiled" check independently — that would require parsing each `<BUILD>/**/subdir_rules.mk`
  compile recipe and comparing mtimes of every `.o`/`.c`. I rely on `project_facts` for the build-not-built
  and stale signals; the extra "link artifacts newer than inputs" check is noted but not separately implemented.
- **Text-map parser is best-effort.** The XML is always preferred, so the `.map` fallback (regions,
  sections, entry) is only exercised when XML is missing. Section input-piece parsing handles the normal
  `file.o (.sec)` and archive `lib.a : member (.sec)` lines and skips `--HOLE--`.
- **`section` command** is implemented (load/run/size/inputs) but not covered by the given tests.
- No commit made, as required.
