# SPEC: project_facts.py + sdk_inventory.py — tests and validation (for Codex, 2026-11-19)

Owner: Claude (design, review). Implementer: Codex. Design note: vault `AI學習/skills/專案事前解析工具（所有 profile 共用）— 設計與逐步實作.md` §4.4 (D1).

## Goal
A model never searches for versions or defined symbols. It reads one manifest made by a program:
per build — built or not, compiler / SDK / SysConfig really used (version + path), -D/-U per file,
.syscfg header, what is installed, every disagreement between the sources, the exact SysConfig command.
Second path (no project, only text, e.g. AM275x HyperRAM customer input): `sdk_inventory.py --match TEXT`.

## State (Claude wrote v1, works on escape32_boot)
- `~/utilities/firmware/sdk_inventory.py` — installed SDKs (product.json, incl. bundled; folder version), compilers, SysConfig; `--match` maps version claims to candidates (unique / several / none).
- `~/utilities/firmware/project_facts.py` — the manifest. Checked by hand on
  `/home/andre/workspace_ccstheia/drone/escape32_boot_lp-am13e230x`: Debug ran SysConfig 1.28.0, Release_TIMER 1.28.1,
  `.syscfg @versions 1.28.1+4785` -> DISAGREE on Debug; Release_TIMER -D IO_TIMER on every file. Right.

## Tasks
1. Run `project_facts.py` on EVERY project with a `.cproject` under `/home/andre/workspace_ccstheia` (find -maxdepth 3).
   For each: crash? facts right? Check by hand against `.cproject`, `<BUILD>/**/subdir_rules.mk`, the `.syscfg` header.
   Families to cover: tiarmclang (AM13, MSPM0, AM261, AM275x r5f), cl2000 (C2000, `--define=` / `--include_path=`),
   cl7x (AM275x c75ss0-0), c29 if any, multi-project (AM261 system), never-built builds.
2. Fix what is wrong IN THESE TWO FILES (smallest change). Known open points to check:
   - SDK detection for C2000 / powerSUITE (no top-level product.json; SDK from folder name and the sysconfig `-s` product.json).
   - AM275x sbl project: `.cproject` PRODUCTS `AM275-FREERTOS-SDK:12.0.0.22` vs what its build really used / what is installed.
   - Files compiled by a recipe whose rule target is not a `.c` (asm, `.cpp`)?
   - "SysConfig device None" when the recipe has no `-d` (fine) — but the `.syscfg` @cliArgs device should be shown (it is, in the header section).
3. Tests: `~/utilities/firmware/test_sdk_inventory.py`, `test_project_facts.py` (unittest, stdlib).
   Fixture projects in a temp dir (a small `.cproject`, a `subdir_rules.mk` with clang and cl2000 recipes, a `.syscfg` header, a fake `~/ti` tree
   with a product.json that has a trailing comma). Each test asserts a FACT (a version, a define, a DISAGREE line), never "output non-empty".
   One test must fail if the Debug 1.28.0 vs @versions 1.28.1 disagreement stopped being reported.
   Plus one smoke test on escape32_boot, skipped when the folder is missing.
   Also `sdk_inventory --match`: "SDK 12.01" + "AM275x" -> unique freertos_sdk_am275x_12_01_00_28 (fake tree in the test); no version -> "ask for it".
4. Report: `~/utilities/firmware/REPORT_codex_20261119.md` — one row per project: builds, built?, compiler, SDK, SysConfig, DISAGREE/MISSING lines,
   right/wrong, what you changed and why. List anything you could not decide.

## Rules
- Standard library only, must run on Python 3.10 (crossroads) and 3.13 (dreammaker). No uv, no libclang.
- Projects, `~/ti`, `~/.agents`, the vault: READ-ONLY. Temp output only in /tmp. Do not run SysConfig or compilers in this task.
- No case or chip names hard-coded in the scripts (tests may use them).
- No git commit. Stop and write the report; Claude reviews.

## Addendum A (Claude, 2026-11-19, after task start) — still only project info + SDK inventory
5. Staleness: per built build, compare the mtime of `<BUILD>/**/subdir_rules.mk` (or `<BUILD>/makefile`) with `.cproject` and each `.syscfg`.
   If the build is older, print `- STALE: <BUILD> was built <date>, .cproject changed <date>: rebuild before trusting these facts`.
   Real case: escape32_boot Debug/subdir_rules.mk 2026-11-16 11:48 < .cproject 2026-11-17 14:43. Test it with a fixture (os.utime).
6. Linked libraries: from `<BUILD>/makefile` linker recipe (`-Wl,-l...`, `-l...`, `-Wl,-i"<dir>"` / `--search_path`, `.cmd` linker script)
   list the libraries and where each resolves (project-local or SDK path); if a `.map` exists, the archives it names win (they were really linked).
   Real case: `/home/andre/workspace_ccstheia/drone/escape32_drv8300_lp-am13e230x/Debug/makefile:54-` links project-local prebuilt `.a`.
7. Version claims (sdk_inventory --match): also report device / part-number words in the text (e.g. AM275x, F28P65x) and list which of
   SDK / compiler / SysConfig / device the text does NOT state, as "ask the customer: ...". Claims stay claims.
8. Related projects: when sibling folders share a system project or a `*_system` / multi-core naming (AM261 system, AM275x cores),
   list them under "related projects" with their core / device id from each `.cproject`. Facts only, no guessing which one to flash.
