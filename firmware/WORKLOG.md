# WORKLOG — tool preparation, autonomous run (user 2026-11-19: "do 1-7, handle automatically")
Roles: Claude = architecture, acceptance tests, verification. Pi (Ornith, pane "Pi - llm-wiki") = implementation in /tmp.
Acceptance: accept.sh (tests unchanged + pass + no skips + no case names) AND Claude spot checks on real projects not in the tests.
Item 7 = flow proposals written to the vault note only; no edits to ~/.agents.
Resume: read this file, then the vault note §8b, then the newest /tmp/*_pi/REPORT.md.

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
