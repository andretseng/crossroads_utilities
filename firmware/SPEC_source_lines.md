# SPEC: source_lines.py — one shared module for source line numbers (for Codex, 2026-11-19)

Owner: Claude (design, review). Implementer: Codex. Design note: vault `AI學習/skills/專案事前解析工具（所有 profile 共用）— 設計與逐步實作.md` §4.7 (D5).
Queue: start after syscfg_run.py is reported.

## Problem
Line-number tools exist but (1) are mostly after-the-fact checks, (2) live only in the exploring pool, (3) `file:line` is parsed in
12+ places, each slightly different, so the same mistakes are fixed again and again (overnight log Fix 5 `.cproject:428` called
malformed; Fix 22 a range end read as a value; Fix 37 "Makefile line 141" read as a C line).
Parsers today (read each before writing): `~/.agents/pool/exploring/explore-guard/`{cite_check, quote_check, ledger_add (parse_cite),
render_answer, finalize_answer, logic_check, case_chain, translation_check, seen_lines, ledger_cite_seen, numbered}.py,
`~/.agents/bin/exploring-pipeline/`{make_note, make_lean_multi, appnote/appnote_stage}.py, `~/.agents/pool/crafting/write-note/scripts/notecheck.py`.

## Phase 1 (this task): the module + equivalence replay. Do NOT edit ~/.agents (callers switch in phase 2, after Claude's review).

`~/utilities/firmware/source_lines.py` (stdlib, Python 3.10+), importable and a CLI:
1. show: `source_lines.py show FILE [START [END]]` -> `N: text` with real numbers (superset of numbered.py: same output format,
   same 200-line default, same tail line). Optional `--facts VIEW_DIR`: after each line, the code_view facts for that line if a view exists.
2. parse: `parse_cites(text) -> [Cite(file, first, last, raw, span)]` — ONE grammar for every cite form found in the callers:
   `file.c:12`, `file.c:12-20`, `file.c:12,14`, dot files and long extensions (`.cproject:428`, `x.syscfg:46`), paths, backticked or
   not, inside parentheses or tables; NOT a value (`0x1000:2`), NOT a time (`10:30`), NOT a range end taken as a value; "Makefile line 141"
   / "x.syscfg line N" bind to that file, never to the C file before it. Document each rule with the Fix number or caller it comes from.
3. resolve: `resolve(cite, roots) -> Path | None` — basename lookup under roots with the callers' skip dirs (`.git`, `__pycache__`,
   `SDKDocs`, `node_modules`); several matches -> ambiguous (return all, never pick one silently).
4. verify: `verify(cite, roots, quote=None)` -> exists? line in range? quote on that line (whitespace-normalized, same rule as quote_check)?
5. fill (the level-1 part): `fill(file, first, last | find="text")` -> the exact lines with numbers, so a writer (ledger_add, note
   makers) can store file:line + text taken from the file, and the model never types a line number. `find` returns every match with
   its line; zero or several matches are reported as such.

## Equivalence replay (the acceptance test)
- Inputs: all saved runs under `~/exploring_lab/cases/*/runs/*/` (explore-ledger.md, 453 files; session.jsonl) and
  `~/exploring_lab/cases/*/answers/**` (answer / reply / report .md). Read-only.
- For each text, run every old parser that applies (import the old modules read-only from ~/.agents; do not modify them) and
  `parse_cites`; write a diff: cites only old found, only new found, different ranges. Each difference must be explained in the
  report as "new is right because ..." (with the Fix number / real file line) or fixed in the module. Target: no unexplained difference.
- Same for verify vs cite_check/quote_check results on the answers.

## Tests (`test_source_lines.py`, unittest)
- One test per grammar rule, including the three Fix cases (5, 22, 37) — each must fail if that rule is removed.
- show output byte-identical to numbered.py on a fixture file.
- fill(find=...) with 0 / 1 / 2 matches.

## Rules
- Standard library only; no case or chip names in the module. Read-only on ~/.agents, ~/exploring_lab, projects, vault.
- Code in /tmp first, then ask to copy into ~/utilities/firmware/. No commit.
- Report: `~/utilities/firmware/REPORT_source_lines_20261119.md` — grammar table (rule, source caller/Fix, test), replay numbers,
  every difference with its explanation, and a phase-2 plan: which caller switches to which function, in what order.

## Addendum (2026-11-19): assembly files
The grammar and show/verify must also cover assembly sources: `.asm`, `.S`, `.s`, `.cla`, `.inc` (TI asm comments start with `;`,
labels in column 1). Add one test each for a `.asm` and a `.cla` cite.
