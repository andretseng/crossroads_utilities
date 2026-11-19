# Handoff: supervise Pi on ~/utilities firmware tools, verify every result

## Project
`/home/andre/utilities` (crossroads, git repo crossroads_utilities). Design note in the vault: `/home/andre/llm-wiki/AI學習/skills/專案事前解析工具（所有 profile 共用）— 設計與逐步實作.md`.

## Written by / intended for
Written by Claude Code (Opus) for Codex (pane "Codex" in Orca, handle at writing time `term_f7b67bda-67e2-4537-8916-479cd10fa193`; list panes again with `orca-ide terminal list --json`). 2026-11-19 06:08 UTC (14:08 local). Trigger: Claude weekly usage near its limit (user rule: at 95 % hand over to Codex).

## Purpose
User's instruction (2026-11-19): "when you reach 95% weekly limit, write handoff ask codex to take over, supervise pi task and verify output." Codex takes Claude's role: architecture + acceptance tests + verification. Pi (Ornith, pane "Pi - llm-wiki", handle at writing time `term_c7cf7d1c-92bd-4616-b0c9-6f00e5f361c7`) stays the implementer. The user authorised the whole queue (items 1-8) to run automatically.

## Context (decisions, compressed)
- Direction (user U1-U4): deterministic facts come from compilers / map / SysConfig tools, not model inference. Tools go in `~/utilities/firmware/` (code/build/SDK) and `~/utilities/notes/` (note tools). Front end per question + back ends per compiler family (`platforms.py`, `backends/clang.py`, `backends/ti_legacy.py`; D10).
- Done and merged: project_facts (+compile_command with response files), sdk_inventory, syscfg_run, link_facts part 1, source_lines phase 1, struct_layout (6 review rounds), the D10 split. Item 6 (offline acceptance, vault note §6.4) and item 7 (flow proposals P1-P10, vault note §8c, NOT implemented, do not edit `~/.agents`) are done.
- User: "pi and ornith is not reliable, so make sure its result pass your test requirement." Every Pi tool needed 2-6 review rounds; Pi's own reports and "explanations" are not evidence (one replay explanation column was a generic string; Pi once edited `~/utilities` directly against the rule).
- Pi works ONLY in `/tmp/<tool>_pi/`. Only the supervisor copies reviewed code into `~/utilities`, then commits and runs `~/utilities/push.sh` (home LAN: GitHub+gitea+datejust; away: GitHub only).
- Acceptance = (1) `~/utilities/firmware/accept.sh WORK_DIR TOOL.py TEST...` (tests byte-identical to the committed copy, all pass, none skipped, no case names in the tool) AND (2) the supervisor's own spot check on real projects NOT used in the tests, comparing with the compiler's own output. When a spot check finds a defect: write a new `test_<tool>_reviewN.py` with values you measured yourself, commit it, copy it to Pi's folder, confirm it FAILS on Pi's code, then send Pi the defect. Typical missed cases: project mode success, second compiler family, nonexistent name must error (never fall back to another result), nested/anonymous types, stale builds (exit 3 ASK USER).
- `/tmp/pi_current` (3 lines: work dir, tool file, test files) drives `~/utilities/firmware/pi_watch.sh` (run it as a background watcher); it prints the gate result when Pi's report.md changes and alarms on unexpected writes in `~/utilities` (create `/tmp/claude_copying` while you copy, remove after).

## Pointers
- Queue and log: `~/utilities/firmware/worklog.md` (read first; append one line per step).
- Specs + acceptance tests (committed, all expected values measured with the real compilers): `~/utilities/firmware/SPEC_{preprocess,c_eval,c_facts,config_calls,reg_decode,asm_view,link_facts}.md` + `test_*.py`; `~/utilities/notes/spec_notes.md` + `test_notes.py`.
- Pi lane order: link_facts part 2 (`/tmp/link_cmd_pi`, RUNNING at handoff, 5 of test_link_cmd failing) -> preprocess -> asm_view -> notes -> reg_decode -> config_calls. Codex lane (T5, AST-heavy): c_eval then c_facts (needs `backends.evaluate`). reg_decode and config_calls depend on c_eval/c_facts.
- For each next Pi task: `mkdir /tmp/<tool>_pi`, copy the SPEC, tests and current `project_facts.py sdk_inventory.py platforms.py backends/` into it, set `/tmp/pi_current`, send Pi one prompt (pattern: "read SPEC, make tests pass, do not edit tests, work only in /tmp/<tool>_pi, write report.md, stop after 2 failed attempts on the same test").
- Commits: latest `acb2e56` (worklog). Vault note commits are local only (not pushed).

## Suggested skills
Codex: `orca-ops`/`orca-cli` (panes, send to Pi), `karpathy-guidelines`, `execute-with-adviser` if a decision is unclear. Not sure which other skills are installed on the Codex side; check its own list.

## Open questions / next step
Next action: read `~/utilities/firmware/worklog.md`, check Pi's pane and `/tmp/link_cmd_pi` (`cd /tmp/link_cmd_pi && python3 -m unittest test_link_cmd test_link_facts`); when Pi reports, run accept.sh, then spot-check `rules` / `whatif-cmd` on a project not in the tests (e.g. dab_F28003x RELEASE `.cmd`, C28 PAGE syntax) before merging. In parallel start c_eval yourself per spec_c_eval.md.
Do not: edit `~/.agents`, implement flow proposals P1-P10, push the vault, or accept Pi's work on its report alone.
