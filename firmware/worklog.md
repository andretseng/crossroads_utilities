# WORKLOG — tool preparation, autonomous run (user 2026-11-19: "do 1-7, handle automatically")
Roles: Claude = architecture, acceptance tests, verification. Pi (Ornith, pane "Pi - llm-wiki") = implementation in /tmp.
Acceptance: accept.sh (tests unchanged + pass + no skips + no case names) AND Claude spot checks on real projects not in the tests.
Item 7 = flow proposals written to the vault note only; no edits to ~/.agents.
Resume: read this file, then the vault note §8b, then the newest /tmp/*_pi/report.md.

## Plan
1. struct_layout + compile_command merge; then split into front + backends/clang.py + backends/ti_legacy.py (D10)
2. link_facts part 2 (rules, whatif-cmd) — tests ready (test_link_cmd.py)
3. preprocessing wrapper (3 levels) + code_view.py merged
4. config_calls.py, then asm_view.py
5. T3 field decode, T7 SDK register index, T5 AST
6. offline acceptance: r1-r26 error types vs tools
7. flow proposals (vault only)
8. note tools in ~/utilities/notes/ (D11: note_tables, note_check, tw_wording, note_recheck) — queued by user 2026-11-19, after 1-6

## Log
- 13:53 item 1a: struct_layout + compile_command merged (6 rounds). Unseen checks: DL_TimerG_ClockConfig size 3 (short enums, AM13+MSPM0), TOBJ1018 C28 padding word, size 10.
- 13:55 item 1b split sent to Pi; item 3 preprocess spec+tests ready (/tmp/preprocess_pi), real values verified (io.c:178, config.h:51/54, psc.c:153/156, dab_main.c:162).
- 13:59 item 1 done: split accepted (6 real cases identical before/after).
- item 6 done (analysis, vault note §6.4): 30 value-type errors; preprocess 8, source_lines 5, syscfg_run 2, tw 1; 14 need T5 -> T5 moved before items 4/asm. T5 part 1 c_eval spec+tests ready (values verified: IO_CC^1=1, 833, C28 sizeof(int)=1, (int)40000=-25536, ISR*2=30000).
- Order now: link_cmd (Pi, running) -> preprocess -> c_eval -> c_facts (AST) -> config_calls -> asm_view -> T3/T7 -> note tools.
- 14:02 c_facts spec+tests ready (io.c sendbuf/recvbuf: else-if parent, ?: values 0x10/0x08, assignment paths, !n++).
- 14:02 item 7 done: flow proposals P1-P10 in vault note §8c (not implemented). Vault note committed (not pushed).
- 14:03 config_calls spec+tests ready (io.c 159/162/180 values, 151 path, config.c:21 call).
- 14:04 reg_decode (T3) spec+tests ready (CCCTL_01 139264 -> COC CAPTURE, ZCOND CC_TRIG_FALL; C2000 TBCTL 0x40B m_s).
- 14:05 asm_view spec+tests ready (codestartbranch.asm 81/83/100, WD_DISABLE).
- 14:05 item 8 note tools spec+tests ready (~/utilities/notes).
- 14:15 assignment plan: Pi lane = link_cmd (running) -> preprocess -> asm_view -> notes -> reg_decode -> config_calls.
  Codex lane (quota back ~14:55) = c_eval + c_facts (T5, AST-heavy, needs backends.evaluate first). T7 SDK register index folded
  into reg_decode later (`--list PREFIX`), not a separate tool.
- 14:16 user: Codex takes over ONLY after Claude reaches 95% weekly. Codex lane cancelled; Pi lane = link_cmd -> preprocess -> c_eval -> c_facts -> asm_view -> notes -> reg_decode -> config_calls. Handover watch armed (weekly>=95 -> send handoff to Codex).
- 14:56 link_cmd round 3: review1 (C28 .cmd, real preprocessor) and review2 (generated cmd) passed; review3 sent (wrote .pp into projects - deleted 3 stray .pp; clang-driver -Wl). 
- 15:15 item 2 done: link_cmd accepted after 3 rounds (C28 lowercase/split/EABI, generated cmd, .pp written into projects, -Wl relink, AM261 cmd lost). Note: cl2000 fallback still reads <name>.pp next to the .cmd if pp_directory output is missing (read only, stale risk).
- 16:20 preprocess round 1: gate OK, review1 sent (comment lines in active branch reported false = misleading; an earlier Pi version rewrote dab_F28003x/RELEASE/dab_main.obj at 15:40 by compiling in the build folder - content should equal the build's, but the file was rewritten; user informed).
- 16:42 preprocess review2 passed; round 3 sent (.cproject-seeded partial, plain make, gnu family) - user-approved.
- 16:58 item 3 done: preprocess accepted after 3 rounds (+ my own test conflict fixed: test_backends sdcc). Spot checks: echoback, Debug_TIMER .cproject seed (config.h 51 true / 54 false).
- 18:00 found: tools fell back to ~/utilities imports, so tests in /tmp used the repo's backends and hid Pi deleting record_layout. Fixed repo tools (own folder only) and accept.sh (merge check + fake HOME + import check).
- 18:07 c_eval accepted after 2 reviews: accept.sh 22 tests + merge check OK; Codex independently matched unseen AM261 TI ARM Clang IR (-7/2=-3, sizeof(int)=4) and C28 cl2000 assembly ((int)40000=-25536, sizeof(int)=1 word16); neither project changed.
- 18:04 HANDOVER: Claude weekly reached 95%; handoff sent automatically; Codex (pane term_d2b64e10..., 'Take over firmware task') took over supervision. Claude stopped its Pi watcher. Open at handover: c_eval review 2 (Pi restoring record_layout + own-folder imports).
- 18:16 c_facts sent to Pi in /tmp/c_facts_pi from commit ad24a72; gate includes c_facts plus c_eval/backends/struct_layout/preprocess regressions. Codex will add measured review tests for every real-project defect before acceptance.
- 18:40 c_facts review1 rejected after supplied gate passed: unseen AM261 compiler AST exposed reversed statement ranges and a missing ternary target. Added measured test_c_facts_review1.py (also nonexistent-function + project snapshot checks); corrected review expectations to raw AST node ranges and removed an initial global-assignment assertion because assignments are local-only by spec.
- 19:42 c_facts accepted after review1: accept.sh 33 tests + merge check OK; Codex matched unseen AM261 focLoop ranges/ternary target to direct tiarmclang AST, verified nonexistent-function exit 2 and separate C28 ti_legacy error, and confirmed both projects unchanged.
- 19:45 asm_view sent to Pi in /tmp/asm_view_pi from commit f8dfd2c; gate includes asm_view plus c_facts/c_eval/backends/struct_layout/preprocess regressions, with compiler-listing and project-snapshot review required before acceptance.
- 20:03 asm_view review1 rejected after supplied gate passed: unseen F28P65x fresh listing exposed listing columns leaking into `text` and an incomplete `command`; real AM261 clang `.S` incorrectly took TI listing path; nonexistent-source error omitted the requested name. Added measured test_asm_view_review1.py with project snapshots.
- 20:15 asm_view accepted after review1: accept.sh 44 tests + merge check OK; Codex independently matched unseen F28P65x cl2000 listing values and AM261 tiarmclang `.S` preprocessing, verified nonexistent-source error and unchanged projects.
