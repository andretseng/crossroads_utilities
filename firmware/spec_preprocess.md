# SPEC: preprocess.py — which lines this build compiles, and their expanded text (for Pi, 2026-11-19)
Design note §4.5 (D2). Three levels, chosen automatically; the result always says which level was used.

1. `build`: a CCS build exists -> `project_facts.compile_command(project, build, source=<file>)`; run the compiler's preprocessor
   in the build folder (cwd = build folder): clang family `-E -dD` (keeps #define lines at their line so define lines count too);
   ti_legacy `--preproc_with_line` + `--preproc_macros`/`--pp_directory` into /tmp (check `cl2000 --help` for the exact flags).
   Use platforms.for_compiler to choose. STALE/not built/FOREIGN -> ASK USER, exit 3 (as project_facts).
2. `sdk_make`: a file inside an SDK library: `--sdk-make <library makefile>` with `--make-var NAME=VALUE` (e.g.
   MCU_PLUS_SDK_PATH, PROFILE): run `make -s -n -f <makefile> <vars>` in its folder (dry run, writes nothing), take the compile
   line that names the file, drop -c/-o/-MMD and run -E. Verified by Claude: freertos_sdk_am275x rm_pm_hal makefile
   (`makefile.am275x.wkup-r5f.ti-arm-clang`, MCU_PLUS_SDK_PATH=<sdk>, PROFILE=release) gives 61 compile lines in 0.1 s.
3. `partial`: no build and no makefile: `--define NAME[=V]` / `--undefine NAME` known symbols only; run `unifdef` (installed on
   crossroads, exit 1 = "output changed", 2 = error). Lines decided by known symbols get compiled true/false; lines under an #if
   with an unknown symbol get compiled null and that symbol is listed in unknown_symbols. Never guess unknown symbols.

Line mapping: follow the `# N "file"` / `#line N "file"` markers; match the requested file by its RESOLVED path (several headers
can end in "config.h"). A line is compiled when it appears in the output at its line number (define lines count via -dD).

CLI: `preprocess.py PROJECT --build B FILE [--lines A-B]` | `preprocess.py --sdk-make MK --make-var K=V... FILE [--lines A-B]` |
`preprocess.py --partial FILE [--define X] [--undefine Y] [--lines A-B]`; `--json`:
`{"level": "build"|"sdk_make"|"partial", "file": abs path, "command": [...] or null, "unknown_symbols": [...],
  "lines": [{"line": int, "compiled": true|false|null, "expanded": str|null}]}` for the requested range (default whole file).
Tests: test_preprocess.py (given, do not edit). Standard library + unifdef only. Work in /tmp/preprocess_pi/. Same stop rule.
Uses platforms.py from the D10 split (copied in when the split is accepted).
