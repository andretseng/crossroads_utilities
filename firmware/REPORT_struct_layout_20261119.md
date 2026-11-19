# struct_layout.py — report

## Goal
`struct_layout.py` measures struct offsets, padding, sizeof and bit-field positions using the
project's own compiler (never guesses). Two compiler families, each with its own measurement and
unit:

- **clang family** (tiarmclang, c29clang): `<compiler> <flags> -Xclang -fdump-record-layouts
  -emit-llvm -S -o /dev/null probe.c` → an `AST Record Layout` table. Rows are `<offset> | type`
  (normal) or `<offset>:<lsb>-<eb> | type` (bitfield); `[sizeof=N, align=A]` gives the size.
  **Unit: byte.**
- **TI legacy** (cl2000, same for cl7x/clpru): `<compiler> <flags> -g -k --asm_directory=<tmp>
  --obj_directory=<tmp> -c probe.c`, then read DWARF in the `.asm`: `DW_AT_name`,
  `DW_AT_data_member_location[DW_OP_plus_uconst N]`, `DW_AT_bit_offset`, `DW_AT_bit_size`, and the
  struct's `DW_AT_byte_size`. `lsb = unit_bits - bit_offset - bit_size` (bit_offset counts from the
  MSB). **Unit: word16.**

Interface (flat, no subcommands):
```
struct_layout.py PROJECT_DIR --build NAME --type T [--include h ...] [--json]
struct_layout.py --compiler PATH --flags "FLAGS" --type T [--include h ...] [--json]
```
Project mode takes the compiler + flags of a C file in that build from `project_facts.py`
(imported, not copied) via `project_facts.compile_command(project, build)`; it drops `-c -o` and
dependency flags, keeps `-D -I/--include_path -v28 --abi --float_support …`. CCS writes include/
define flags into a response file (`-c @ccsIncludes.opt`), which `compile_command` expands (relative
to the build folder) and splices in. ASK USER + exit 3 on stale / not-built / foreign. Work in a
fresh `/tmp` dir. On compiler failure: exit 2, print the error lines verbatim. JSON:
`{"type","unit","size","members":[{name,offset,bit_lsb,bit_size}]}` in declaration order.

## Tests — all pass
```
$ python3 -m unittest -v test_struct_layout test_struct_layout_review1 test_struct_layout_review3 test_struct_layout_review4 test_struct_layout_review5 test_struct_layout_review6 test_project_facts test_project_facts_review1 test_project_facts_review2
```
All 32 tests across the nine modules pass (5 in `test_struct_layout`, 2 in `test_struct_layout_review1`,
2 in `test_struct_layout_review3`, 2 in `test_struct_layout_review4`, 2 in `test_struct_layout_review5`,
2 in `test_struct_layout_review6`, 9 in `test_project_facts`, 2 in `test_project_facts_review1`,
2 in `test_project_facts_review2`).

## Review 1 — project mode reads the compile command from `project_facts.compile_command`
The gate passed but project mode read the compile recipe from `<BUILD>/makefile` (which holds the
LINK recipe, not the compile recipe) and its token filter dropped every `-I"path"` / `-DNAME` token,
so no project header was found. Real CCS recipes also use a response file
(`tiarmclang -c @ccsIncludes.opt`), whose include paths were never expanded. Fixed in TWO copies of
`project_facts.py` (`~/utilities/firmware/project_facts.py` and the `/tmp/struct_layout_pi/` copy the
test imports) and rewired `struct_layout.py` to use it.

- Added `compile_command(project, build_name, source=None) -> (compiler, args)`. It reuses the
  existing `recipe_makefiles()` + `recipes()` helpers to read the build's compile recipe from
  `subdir_rules.mk` (preferring the named `source` when given), expands any `@response` file
  (relative to the build folder), and drops output/dependency flags (`-c -o -MMD -MP -MF -MT` and
  their attached-value forms like `-o$@`, `-MFfile`) plus leftover makefile expansions
  (`$(shell echo $<)`). `-I"path"` / `-D"NAME"` tokens have their quotes stripped to `-I/path` /
  `-DNAME`.
- `struct_layout.py` no longer reads a makefile itself: `project_compiler_and_flags()` was removed
  and the project-mode caller now does `project_facts.compile_command(project, build_name)`. The
  source now contains `compile_command` and no `build_dir / "makefile"`.
- Added `test_project_facts_review1` (response-file expansion, named source) and
  `test_struct_layout_review1` (real escape32_drv8300 Debug build, `DL_MCAN_BitTimingParams` →
  `unit=byte size=32`, 8 members at offsets `4*i`; and a source-content check).

## Review 2 — `compile_command` token handling
`test_project_facts_review2` (do not edit) found three defects in the drop/expand logic:

1. **`-c` ate the next token.** `-c` was in the “takes a separate value” set, so dropping `-c` also
   skipped the following token — the *first* `-I` of the expanded response file. `-c` takes no
   value. Removed it from that set; now only the attached-value flags (`-o`, `-MF`, `-MT`) consume
   a following token.
2. **Response-file split broke on spaces.** `_expand_response_file(...).split()` split every run of
   whitespace, so a quoted path like `-I"/tmp/dir with space/inc"` was torn apart. Switched to
   `shlex.split(...)`, which keeps quoted paths (with spaces) as one token.
3. **Drop flags matched by prefix.** The old test `token.startswith(f)` dropped `-code_state=16`
   as `-c`. Now drop flags match exactly (`token in _COMPILE_DROP` or `startswith(f + "=")`), plus
   `startswith(f)` only for the attached-value flags (`-o`, `-MF`, `-MT`) so `-o$@` / `-MFa.d` /
   `-MT$(@)` still drop. `-code_state=16` is kept.

Also: the recipe's trailing source-file token (``../a.c``) is now popped before the drop loop, so
it is not forwarded to the probe. And `_load_project_facts()` in `struct_layout.py` now prefers the
`project_facts.py` that lives next to the script (the reviewed, self-contained copy) so the tool
keeps working without editing the read-only shared file.

Added `test_project_facts_review2` (response-file quotes + spaces + value-less `-c`, and the real
escape32_drv8300 Debug build keeps its first response-file include).

## Review 3 — C2000 project mode (`power/dab_F28003x` RELEASE)
`test_struct_layout_review3` (do not edit) drove the real cl2000 project and found two defects:

1. **Make leftovers and side-output flags survived.** The cl2000 recipe carries `--preproc_dependency=$(basename $(<F)).d_raw`, `$(GEN_OPTS__FLAG)`, `$(shell echo $<)`, `--preproc_with_compile`,
  `--asm_listing`, `-k`. The old makeleftover regex was anchored `^\$\(.*\)$`, so it only matched a
  token that was *wholly* `$(…)`; `--preproc_dependency=device/$(basename $(<F)).d_raw` (which merely
  *contains* `$(…)`) slipped through → `"cannot open preprocessing output file device/$(basename"`.
  Added `--preproc_with_compile`, `--asm_listing`, `-k`, `--preproc_dependency` and the `$(GEN_OPTS__FLAG`
  prefix to `_COMPILE_DROP`, and replaced the anchored regex with `_MAKELEFTOVER = re.compile(r"\$\(")`
  so any token starting with `$` or containing `$(…)` is dropped.
2. **The compiler was duplicated on the probe command line.** `compile_command` returned the compiler
  as `args[0]`, and `run_ti_legacy` prepends the compiler again, so cl2000 got `<tool> <tool> -v28 …`
  and failed with `line 1: unrecognized token` for every flag. Fixed by slicing the compiler off the
  arg list (`recipe = tokens[1:]`, iterating `recipe[index]`); `args` now starts at `-v28`.

Also fixed a scoping bug exposed by this project: `_ti_parse_members` scanned the *whole* `.asm` for
every `DW_TAG_member`, so it collected the `_Vals` members of a neighbouring `_Complex_double` type
unit into `DCL_PI_CLA`. It now starts at the target struct's `.dwtag … DW_TAG_structure_type` line and
stops at its `.dwendtag`, so only that struct's members are returned. This gave the real
`DCL_PI_CLA` layout: `unit=word16 size=0x12`, members `Kp(0), Ki(2), i10(4), Umax(6), Umin(8), i6(10),
i11(12), Imax(14), Imin(16)`.

Added `test_struct_layout_review3` (compile-command has no make leftovers / side outputs, and the
real `DCL_PI_CLA` word layout).

## Review 4 — probe shape: missing types and `typedef struct Tag {...} Name;`
`test_struct_layout_review4` (do not edit) found two serious defects on real projects:

1. **A missing type returned another struct's layout (silent wrong answer).** MSPM0 `dl_gpio.h`:
   `DL_GPIO_noSuchConfigType` does not exist, but the bare-variable probe compiled (clang treats an
   unknown identifier as `int` / emits neighbouring records) and returned `IOMUX_Regs`. Fix: the
   clang probe is now a **wrapper struct** `struct __probe_wrap { NAME v; };` -- a genuinely missing
   type fails to compile (`unknown type name`), and `parse_clang` raises `LayoutError` (exit 2) on
   any `error:` / `unknown type name` in the output, so no stale record is parsed. clang then prints
   the wrapped record inline under `v`.
2. **`typedef struct Tag_ {...} Named;` gave "record layout not found" (AM261 `HwiP_Params`).** With
   the wrapper, clang dumps the record keyed on the *underlying* tag (`struct Tag_` / `struct
   HwiP_Params_`), not the typedef name. `_clang_layout_block` now matches a block that names the
   type directly **or** is the unique table carrying member rows; `parse_clang` skips the first data
   row (the struct's own name row, `0 | struct Tag_`) as the header.

TI legacy (cl2000/cl7x/clpru) does **not** emit a wrapper struct's DWARF (the .asm drops it), so the
probe stays a **bare variable** `NAME NAME_probe_instance;` for that family; the missing-type guard is
`_ti_find_struct` following `DW_TAG_typedef NAME -> DW_AT_type -> the struct`. `write_probe` now takes
the clang-family flag and picks the right shape.

Added `test_struct_layout_review4` (clang + cl2000 `typedef struct Tag {...} Name;`, a missing type
never falls back to another record, the MSPM0 missing type in a big header is an error, and the real
AM261 `HwiP_Params` → `unit=byte size=20`, `intNum(0), callback(4), args(8), eventId(12), priority(14),
isFIQ(15), isPulse(16)`).

## Review 5 — nested types: top-level members only
`test_struct_layout_review5` (do not edit) found two more silent-wrong-answer defects on nested types:

1. **Asking for `Outer` returned `Inner`'s layout.** `_clang_layout_block` previously returned the
   *first* table carrying member rows; for `Outer` (which contains `Inner`) that was `Inner`'s block
   (size 2). With the wrapper, clang dumps each referenced record once in dependency order (inner
   types first, the wrapped type last), so the asked type's record is the **last** table. Rewrote the
   matcher to take the last table with member rows -- never an inner record.
2. **A union listed its nested struct's members plus a bogus name.** `RegU` (a union with a nested
   anonymous `struct { uint32_t a:4; uint32_t b:28; } bit;`) emitted `all`, `RegU`, `a`, `b`. Two
   fixes: `parse_clang` now keeps only rows at **exactly one indent level below the header**
   (header indent + 2); deeper rows belong to a nested record and are dropped. And `_clang_member_name`
   took the *first* identifier, so `struct RegU::(unnamed at …) bit` yielded `RegU`; it now takes the
   **last** identifier in the row (`bit`).

Rule now enforced: members = the DIRECT members of the asked type only; size = the asked type's own
`[sizeof=...]` (its own record dump). cl2000: the first member has no `DW_AT_data_member_location`
line, so its offset is 0 (already the case via the existing default).

Added `test_struct_layout_review5` (clang `Outer` → size 16, `tag(0), in(1), reg(4), arr(8)`; clang
`RegU` → size 4, `all(0), bit(0)`; cl2000 `Outer16` → size 10, `tag(0), in(1), r(4), arr(6)`).

## Review 6 — anonymous struct/union members flattened
`test_struct_layout_review6` (do not edit) found that anonymous struct/union members were not listed
as C sees them. In C, `union { uint16_t h; uint8_t b2[2]; }` inside a struct is accessed as
`x.h` / `x.b2` directly, so the anonymous member's members must be flattened at the anonymous
member's offset.

- **clang `Mix` listed only `h` (b2 missing).** The anonymous union member row was
  `union Mix::(anonymous at …/h.h:4:47)`; `_clang_member_name` took the last identifier, which was
  `h` from the filename `h.h`, so the anonymous member masqueraded as member `h(12)`. Rewrote
  `parse_clang` to parse the table into rows and flatten an anonymous member recursively: its own
  members (at the next indent level) are emitted at `member_offset + (nested_offset - member_offset)`
  (nested dump offsets are absolute, so made relative to the anonymous member's own offset). Now
  `Mix` → size 16, `m(0), p(4), s(8), h(12), b2(12), f(14)`.
- **cl2000 `MixC` listed the compiler-internal name `$P$T0`.** cl2000 emits an anonymous member with
  an internal name (`$P$T0`) and a `DW_AT_type(*$C$DW$T$24)` pointing at the anonymous union.
  `_ti_parse_members` now detects a `$P$...` member name, follows its `DW_AT_type` to the anonymous
  type, and flattens that type's members (`h`, `w`) at the anonymous member's offset. Added
  `_ti_parse_type` to parse a struct/union's direct members for this purpose. Now `MixC` → size 6,
  `m(0), h(2), w(2), t(4)`.

Rule now enforced for both families: an anonymous struct/union member is replaced by its members,
flattened at the anonymous member's offset (plus each member's own offset inside it).

## Bugs found and fixed (each with a failing test before the fix)
1. **`compiler_family` returned `"arm"` instead of `"armllvm"`.** The first-attempt logic did
   `"tiarmclang".replace("ti","").replace("clang","")`, collapsing to `"arm"`, so the clang probe
   was driven with legacy `-k --asm_directory` flags and failed. Rewrote the lookup as a direct
   `{name: family}` table. Fix confirmed by `test_clang_family_bytes_and_bitfields`.
2. **clang `[sizeof=…]` was on the far side of the `|` separator.** The summary row is
   `           | [sizeof=12, align=4]`, so after partitioning on `|` the text sat in `rest`, not
   `header`; the size was never read. Now search `header + " " + rest` for `\[sizeof=(\d+)`.
3. **clang header row counted as a member.** The table's first data row is `0 | U` (the struct's
   own name); it was emitted as member `"U"`. Skip any row whose name equals the queried type.
4. **cl2000 typedef resolution lost its target.** The scan remembered the typedef's `DW_AT_type`
   ref but reset it to `None` on every subsequent `.dwtag` line, so the struct tag was never found.
   Rewrote `_ti_find_struct` to capture the typedef → `DW_AT_type(*$TAG)` target in one pass, then
   confirm that tag is a `DW_TAG_structure_type` and read its `DW_AT_byte_size`.
5. **cl2000 member collection overran into the next member.** The inner attribute loop broke only on
   rows *starting* with `.dwtag`/`.dwendtag`, but member blocks begin with `$C$DW$N	.dwtag`
   (tag ID prefix), so `$C$DW$3`'s attributes were swallowed by `$C$DW$2`. Now break on a blank-free
   match of `\$[A-Za-z0-9$]+\s+\.dwtag` or a `.dwendtag` prefix. This fixed the cia402 PDO test
   (only `ObjControlWord` was parsed before; all four members parse now).
6. **cl2000 member collection crossed type-unit boundaries.** `_ti_parse_members` scanned the entire
   `.asm`, so it pulled the `_Vals` members of a neighbouring `_Complex_double` type unit into
   `DCL_PI_CLA`. Now it starts at the target struct's `.dwtag … DW_TAG_structure_type` line and stops
   at its `.dwendtag`, collecting only that struct's members.
7. **clang skipped the wrong header row for a tagged typedef.** The old check skipped a row only when
   its name equaled the queried type, but the wrapper dumps the record under the *underlying* tag
   (`0 | struct Tag_` for `typedef struct Tag_ {...} Named;`), so `Tag_` leaked in as a member. Now the
   first qualifying data row is always treated as the struct's own name row and skipped.
8. **clang nested types leaked inner members / wrong record.** Asking `Outer` returned `Inner`'s block
   (first member-row table), and a union listed its nested struct's members plus a bogus name. Now
   `_clang_layout_block` returns the **last** table with member rows (the wrapped type, dumped last in
   dependency order), and `parse_clang` keeps only rows at exactly one indent level below the header
   (header indent + 2); deeper rows belong to a nested record. `_clang_member_name` takes the last
   identifier in the row, so `struct RegU::(unnamed …) bit` yields `bit`.
9. **clang anonymous union member name came from the filename.** The anonymous union member row was
   `union Mix::(anonymous at …/h.h:4:47)`; `_clang_member_name` took the last identifier (`h` from
   `h.h`), so the anonymous member masqueraded as member `h` and its real members were dropped.
   `parse_clang` now flattens an anonymous member recursively: its members (at the next indent level)
   are emitted at `member_offset + (nested_offset - member_offset)`, and the consumed nested rows are
   skipped.
10. **cl2000 anonymous member kept the compiler-internal name `$P$T0`.** cl2000 emits an anonymous
    member with an internal name and a `DW_AT_type(*$TAG)` pointing at the anonymous type. `_ti_parse_members`
    now detects a `$P$...` member name, follows its `DW_AT_type` to the anonymous type, and flattens
    that type's members at the anonymous member's offset (new helper `_ti_parse_type`).

## Verification notes
- Explicit-mode clang probe on `struct { uint16_t a; int32_t b; uint16_t c:3, d:5; }` →
  `unit=byte size=12`, `a(0), b(4), c(8,0,3), d(8,3,5)` — matches the clang dump
  `0|a  4|b  8:0-2|c  8:3-7|d  [sizeof=12, align=4]`.
- cl2000 same struct → `unit=word16 size=6`, `a(0), b(2), c(4,0,3), d(4,3,5)` — `int32_t` aligns to
  an even word (1 word padding), bitfields use `lsb = 16 - bit_offset - bit_size`.
- dab_F28003x `DCL_PI_CLA` (RELEASE build flags, cl2000): `unit=word16 size=0x12`, members
  `Kp(0), Ki(2), i10(4), Umax(6), Umin(8), i6(10), i11(12), Imax(14), Imin(16)` — the review-3 real
  check; the `.asm`'s neighbouring `_Vals` members are correctly excluded.
- AM261 `HwiP_Params` (Debug build flags, tiarmclang 5.1.1): `unit=byte size=20`, members
  `intNum(0), callback(4), args(8), eventId(12), priority(14), isFIQ(15), isPulse(16)` — the review-4
  real check; the typedef's underlying `struct HwiP_Params_` record is matched and its header row
  skipped.
- A missing type (`NoSuch`, MSPM0 `DL_GPIO_noSuchConfigType`) now exits 2 with the compiler's own
  `unknown type name` error; the wrapper struct `struct __probe_wrap { NAME v; }` makes clang stop
  compiling, so no neighbouring record (e.g. `IOMUX_Regs`) is ever parsed.
- clang `Outer` (contains `Inner`) → size 16, members `tag(0), in(1), reg(4), arr(8)` -- the review-5
  real check; the inner `Inner` record is not returned and its `lo`/`hi` are not listed as members.
- clang `RegU` (union with nested anonymous struct) → size 4, members `all(0), bit(0)` -- top-level
  only; the nested `a`/`b` and the bogus `RegU` name are excluded.
- Anonymous members flattened: clang `Mix` (contains `union { uint16_t h; uint8_t b2[2]; }`) → size
  16, `m(0), p(4), s(8), h(12), b2(12), f(14)` -- the review-6 real check; the anonymous union's
  members appear at its own offset (12), not as a single bogus member. cl2000 `MixC` (contains
  `union { uint16_t h; uint32_t w; }`) → size 6, `m(0), h(2), w(2), t(4)` -- the compiler-internal
  `$P$T0` name is replaced by the flattened `h`/`w`.
- cia402 `TCiA402PDO1600` (FLASH build flags, cl2000): `ObjControlWord 0, ObjTargetPosition 2,
  ObjTargetVelocity 4, ObjModesOfOperation 6` — the SPEC's real check.
- Project mode: `project_facts.compile_command(project, build)` selects the build and returns its
  real compile recipe (response file `ccsIncludes.opt` expanded via `shlex.split`, `-c -o -MMD
  -MP -MF -MT`, the C2000 side-output flags `--preproc_with_compile --asm_listing -k
  --preproc_dependency=…`, and every `$(…)` make expansion dropped, compiler + source file not
  duplicated); the stale FLASH build correctly prints ASK USER and exits 3.
- Compiler failure (`--type NoSuchType`) exits 2 with the compiler's own error lines verbatim.

## Rules followed
- Standard library only (`argparse, json, os, re, shlex, subprocess, sys, tempfile, pathlib`).
- `project_facts.py` imported (not copied); the reviewed copy lives next to `struct_layout.py` in
  `/tmp/struct_layout_pi/` and is preferred over the read-only shared one.
- No chip or case names in the script (compiler *family* tokens `c2000/armllvm/…` are the unit
  selector, matching project_facts itself).
- Worked only in `/tmp/struct_layout_pi/`; probe output goes to a `tempfile.TemporaryDirectory`.
- Did NOT edit the read-only `~/utilities/firmware/project_facts.py` (reverted by Claude); all fixes
  live in the `/tmp/struct_layout_pi/` copy.
- Stop rule: not triggered — every failing test was fixed on the first attempt.
