# SPEC: link_facts.py — what the linker really did (T6) (for Codex, 2026-11-19)

Owner: Claude (design, review). Implementer: Codex. Design note: vault `AI學習/skills/專案事前解析工具（所有 profile 共用）— 設計與逐步實作.md` §4.6c (D7).
Queue: after source_lines phase 1.

## Goal
A model never guesses addresses, memory use, whether a function was linked, or which file a symbol came from. It asks this script.

## Verified by Claude (2026-11-19)
- The build makefile names the map and the XML: `-m"<name>.map"` and `--xml_link_info="<name>_linkInfo.xml"`
  (escape32_boot Release_TIMER, dab_F28003x RELEASE). Use those names; never glob `*.map`.
- Trap: `escape32_boot_lp-am13e230x/Release_TIMER/` also holds an OLD `ESCape32_boot_LP_AM13E230.map` + `_linkInfo.xml` (2026-11-16,
  old artifact name) next to the current `escape32_boot_lp-am13e230x.map` (2026-11-18). Report other maps as "old, not used".
- `_linkInfo.xml` root `link_info`: `input_file_list`, `object_component_list` (run_address, load_address, size, alignment,
  input_file_ref), `logical_group_list`, `placement_map`, `symbol_table`, `entry_point`. Text map: `MEMORY CONFIGURATION` table
  (e.g. `FLASH_BOOT 00000000 00001000 used 00000c00`).

## Interface (`~/utilities/firmware/link_facts.py`, stdlib only, Python 3.10+)
```
link_facts.py PROJECT_DIR --build NAME summary          # memory regions, sections, libraries, entry, old maps ignored
link_facts.py PROJECT_DIR --build NAME symbol NAME...   # address, size, section, load/run, source .o/.a (member), linked yes/no
link_facts.py PROJECT_DIR --build NAME section NAME...  # load/run address, size, input pieces
link_facts.py PROJECT_DIR --build NAME overlaps         # overlapping sections / regions, and region use over 100 %
```
- Build selection, staleness, FOREIGN, not built: from `project_facts.py` (import it); same `ASK USER` + exit 3.
  Also exit 3 when the map/XML is older than the newest object or source the build compiled [check what is cheap and say what you used].
- Prefer `_linkInfo.xml` (ElementTree); fall back to the text map only when there is no XML, and label the source.
- Address unit: on C28x addresses are 16-bit words; take the unit from the linker/compiler family facts, print it, never mix.
- "not linked" must be decided from the symbol table / object components (a static function may be inlined: say "not in symbol
  table — may be inlined or removed", same rule as linked_check.py; read it first).
- Output Markdown (with `file:line` into the map where a text map line is used) and `--json`.

## Tests (`test_link_facts.py`, unittest)
- Fixture XML and fixture text map: exact addresses/sizes/regions; overlap detection; old-map selection by makefile name.
- Real smoke tests (skip when missing): escape32_boot Release_TIMER `FLASH_BOOT` used 0xc00 of 0x1000; the old ESCape32 map is
  reported as not used. dab_F28003x RELEASE (C28x, word addresses) one symbol with its address and unit.

## Rules
- Standard library only; no chip/case names in the script. Read-only everywhere except /tmp. Code in /tmp first, then ask to copy.
- No commit. Report `~/utilities/firmware/REPORT_link_facts_20261119.md`: projects checked (AM13, C2000, MSPM0, AM261, AM275x sbl
  if built), what was right/wrong, open points. Disassembly of `.out` is a later step — not in this task.

## Addendum (2026-11-19): implementer = Pi (Ornith); the acceptance tests are written by Claude
`test_link_facts.py` is given and must pass unchanged. Do not edit it. JSON contract (`--json`, integers as numbers, not strings):
- Project mode: `link_facts.py PROJECT_DIR --build NAME <command>`; explicit mode for fixtures: `link_facts.py --xml FILE [--map FILE] --unit byte|word16 <command>`.
- `summary` -> `{"source": "xml"|"map", "xml": path|null, "map": path|null, "ignored": [paths of other .map/_linkInfo.xml in the build folder],
  "unit": "byte"|"word16", "entry": {"name": str, "address": int}, "regions": [{"name", "page", "origin", "length", "used", "unused"}]}`
- `symbol NAME...` -> `{"symbols": [{"name", "found": bool, "address": int|null, "section": str|null, "section_address": int|null,
  "section_size": int|null, "input_file": str|null, "note": str}]}` — section = the object component whose run range [run, run+size)
  contains the address; input_file = that component's input file name. Not found -> found false and note
  "not in symbol table: may be inlined or removed".
- `overlaps` -> `{"overlaps": [{"a": name, "b": name, "start": int, "end": int}], "over_full": [region names]}` — overlaps between
  object components' run ranges inside the same page, and regions whose used > length.
- unit: word16 when the build's compiler family is cl2000 (C28x), else byte. Exit 3 + ASK USER as in project_facts.

## Review 1 (Claude, 2026-11-19) — defects found on real projects; tests added to test_link_facts.py
1. The map/XML must come from the makefile's linker options (`-m"..."` / `-Wl,-m"..."`, `--xml_link_info="..."` /
   `-Wl,--xml_link_info="..."`), NOT from the newest mtime. Newest-mtime is only the fallback when the makefile names none (say so).
   AM261 servo r5fss0-0 Debug: makefile names `ipc_notify_echo.Debug.map`.
2. `ignored` lists only the OTHER map/XML files; the ones in use must never appear there (escape32 listed its current files as old).
3. Overlaps only between components that are placed in memory: a component counts when its logical group (logical_group_list) is
   referenced by a placement_map `allocated_space/logical_group_ref`. Debug sections (.debug_*) are not placed and must never appear.
   For a text map without XML, use the SECTION ALLOCATION MAP and skip sections the map marks as not allocated (COPY/DSECT/debug).

## Part 2 (2026-11-19): linker command file — `rules` and `whatif-cmd` (implementer: Pi; tests by Claude: test_link_cmd.py)
Design note §4.6c-2 (D9). Do NOT write a full .cmd parser: let the linker do the work.
Verified by Claude: in escape32_boot Release_TIMER, a copy of `../linker/boot_ticlang.cmd` with FLASH_BOOT LENGTH 0x800, relinked
from the build folder with the build's own link recipe and the existing .o files, outputs in /tmp: 0.16 s, linker says
`error #10099-D: program will not fit into available memory ... section ".text" size 0x886 ... FLASH_BOOT size: 0x800 unused: 0x490`.
The link recipe is the `-o "<name>.out" $(ORDERED_OBJS)` rule in `<BUILD>/makefile`; ORDERED_OBJS lists the .o, the .cmd and `-Wl,-l` libs.

- `rules` (JSON `{"cmd_files": [path], "memory": [{"name", "origin", "length", "file", "line"}], "symbols": [{"name", "value", "file", "line"}],
  "sections": [{"name", "load": str|null, "run": str|null, "file", "line"}]}`): the .cmd file(s) named in ORDERED_OBJS. Expand `#if`/
  `#define` with the build's linker --define first (C preprocessor semantics; the linker's own rule), then read MEMORY, top-level
  `name = value;` assignments and SECTIONS entries (`> REGION` means load and run = REGION; `load = A, run = B`; `> 0xADDR` keeps the
  address text). Values as int where they are numbers. Works also for a build that was never built (label `"basis": "rules only"`);
  for a built build `"basis": "rules"` and each section adds `"placed_run"`/`"placed_load"` from the linkInfo (result).
- `whatif-cmd --replace OLD NEW [--replace OLD NEW ...]`: OLD must occur exactly once in the .cmd (else exit 2 with the count). Copy the
  .cmd to a fresh /tmp folder, apply, relink with the build recipe (cwd = build folder, .o as they are, -o/-m/--xml_link_info into /tmp),
  never touch the project. JSON `{"linked": bool, "exit_code": int, "errors": [verbatim linker error lines],
  "diff": {"regions": [{"name", "field", "old", "new"}], "symbols": [{"name", "old", "new"}]}}` (diff only when linked).
  Exit 3 + ASK USER when the build is stale/not built/foreign or any listed .o is missing (relink needs real objects).
