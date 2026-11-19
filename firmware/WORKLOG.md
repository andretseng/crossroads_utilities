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

## Log
