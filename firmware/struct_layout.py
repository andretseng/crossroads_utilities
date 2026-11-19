#!/usr/bin/env python3
"""struct_layout.py — measured struct layout (front end, D10).

A model never works out offsets, padding, sizeof or bit-field positions; the project's own
compiler, with the build's own flags, measures them.  The compiler-specific measurement and parsing
live in compiler-family back ends (``backends.clang`` and ``backends.ti_legacy``); this front end
only picks the right back end with ``platforms.for_compiler`` and formats the result.

The compiler and flags come from project_facts.py (project mode) or are given explicitly.
Standard library only; no chip or case names.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_COMPILER_ERROR = 2
EXIT_ASK_USER = 3

ASK_USER = "ASK USER"


# --------------------------------------------------------------------------- #
# project_facts import (build selection / staleness / FOREIGN / not-built)
# --------------------------------------------------------------------------- #

def _load_project_facts():
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared# Prefer the copy that lives next to this script (the reviewed, self-contained one); fall back
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared# to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared# project_facts has not yet been updated with compile_command.
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedhere = Path(__file__).resolve().parent / "project_facts.py"
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedcandidates = [here]
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedfor cand in candidates:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if cand.is_file():
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        sys.path.insert(0, str(cand.parent))
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        break
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedimport project_facts  # noqa: E402
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedreturn project_facts


# --------------------------------------------------------------------------- #
# back end selection
# --------------------------------------------------------------------------- #

def _load_platforms():
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedhere = Path(__file__).resolve().parent / "platforms.py"
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedfor cand in (here,):
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if cand.is_file() and str(cand.parent) not in sys.path:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        sys.path.insert(0, str(cand.parent))
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedimport platforms  # noqa: E402
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedreturn platforms


def _load_backends():
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedhere = Path(__file__).resolve().parent
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedif str(here) not in sys.path:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    sys.path.insert(0, str(here))
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedimport backends  # noqa: E402
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedreturn backends


def _back_end_for(compiler: str, platforms, backends):
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared"""Return the back-end module for the compiler, or exit 3 for an unknown compiler."""
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedtry:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    platform = platforms.for_compiler(compiler)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedexcept KeyError as error:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    print(f"{ASK_USER}: {error}", file=sys.stderr)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    sys.exit(EXIT_ASK_USER)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedreturn backends.clang if platform["family_kind"] == "clang" else backends.ti_legacy


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_measure(compiler: str, args: str, type_name: str, includes: list[str],
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared            platforms, backends) -> dict:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared"""Measure one type via the matching compiler-family back end."""
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedmodule = _back_end_for(compiler, platforms, backends)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedtry:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    return module.record_layout(compiler, args, type_name, includes, Path("."))
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedexcept backends.CompileError as error:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    # A compiler error: the front end exits 2 and prints the error lines verbatim.
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    raise LayoutError("__COMPILER_ERROR__\n" + str(error)) from error


# --------------------------------------------------------------------------- #
# project mode (via project_facts)
# --------------------------------------------------------------------------- #

def resolve_project(project_facts, project: Path, build_name: str):
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared"""Return build_dir, compiler, flags or exit 3 with ASK USER for stale / not built / foreign."""
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedinstalled = project_facts.inventory([project_facts.DEFAULT_ROOT])
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedheaders = project_facts.syscfg_headers(project)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedentries = project_facts.cproject_builds(project)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedif not entries:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    print(f"{ASK_USER}: no .cproject with builds in {project}", file=sys.stderr)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    sys.exit(EXIT_ASK_USER)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedfacts_by_name = {e["name"]: project_facts.build_facts(project, e, headers, installed,
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared                                                      __import__("platform").system()) for e in entries}
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedif build_name not in facts_by_name:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    print(f"{ASK_USER}: unknown build {build_name!r}; builds: {[e['name'] for e in entries]}",
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared          file=sys.stderr)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    sys.exit(EXIT_ASK_USER)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedfacts = facts_by_name[build_name]
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedbuild_dir = project / build_name
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedif not facts["built"]:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    print(f"{ASK_USER}: {build_name} was never built; build it in CCS then run struct_layout again",
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared          file=sys.stderr)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    sys.exit(EXIT_ASK_USER)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedfor item in facts["foreign"]:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    print(f"{ASK_USER}: {build_name} was generated on {item['os']} ({item['path']}); rebuild on this machine",
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared          file=sys.stderr)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    sys.exit(EXIT_ASK_USER)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedif facts["stale"]:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    print(f"{ASK_USER}: {facts['stale'][0]}; rebuild {build_name} in CCS, then run struct_layout again",
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared          file=sys.stderr)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    sys.exit(EXIT_ASK_USER)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedreturn build_dir, facts


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def emit(data: dict) -> None:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedprint(json.dumps(data, indent=1))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

class LayoutError(Exception):
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared"""Raised by the front end when a back end reports a compiler/parse error."""


def main(argv=None) -> int:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedproject_facts = _load_project_facts()
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedplatforms = _load_platforms()
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedbackends = _load_backends()
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedtokens = list(argv if argv is not None else sys.argv[1:])
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared# Flat interface (no subcommands):
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared#   struct_layout.py PROJECT_DIR --build NAME --type T [--include h ...] [--json]
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared#   struct_layout.py --compiler PATH --flags ".." --type T [--include h ...] [--json]
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedproject_dir = None
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedbuild_name = None
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedcompiler = None
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedflags = ""
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedtype_name = None
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedincludes: list[str] = []
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedas_json = False
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedi = 0
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedwhile i < len(tokens):
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    tok = tokens[i]
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if tok == "--build":
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        build_name = tokens[i + 1]; i += 2; continue
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if tok.startswith("--build="):
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        build_name = tok.split("=", 1)[1]; i += 1; continue
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if tok == "--compiler":
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        compiler = tokens[i + 1]; i += 2; continue
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if tok.startswith("--compiler="):
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        compiler = tok.split("=", 1)[1]; i += 1; continue
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if tok == "--flags":
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        flags = tokens[i + 1]; i += 2; continue
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if tok.startswith("--flags="):
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        flags = tok.split("=", 1)[1]; i += 1; continue
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if tok == "--type":
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        type_name = tokens[i + 1]; i += 2; continue
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if tok.startswith("--type="):
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        type_name = tok.split("=", 1)[1]; i += 1; continue
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if tok == "--include":
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        includes.append(tokens[i + 1]); i += 2; continue
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if tok.startswith("--include="):
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        includes.append(tok.split("=", 1)[1]); i += 1; continue
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if tok == "--json":
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        as_json = True; i += 1; continue
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if tok.startswith("-"):
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        # Unknown flag; skip it and its value if it looks like one.
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        i += 1; continue
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if project_dir is None:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        project_dir = tok
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    i += 1

 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedif type_name is None:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    print("usage: struct_layout.py [--compiler PATH --flags \"..\"] --type T [--include h ...] [--json]"
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared          " | struct_layout.py PROJECT_DIR --build NAME --type T [--include h ...] [--json]",
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared          file=sys.stderr)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    return EXIT_ASK_USER

 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedtry:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if compiler is not None:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        data = cmd_measure(compiler, flags or "", type_name, includes, platforms, backends)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    else:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        if project_dir is None:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared            print("usage: struct_layout.py PROJECT_DIR --build NAME --type T [--include h ...] [--json]",
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared                  file=sys.stderr)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared            return EXIT_ASK_USER
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        if build_name is None:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared            print(f"{ASK_USER}: --build NAME required", file=sys.stderr)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared            return EXIT_ASK_USER
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        project = Path(project_dir).expanduser().resolve()
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        build_dir, _facts = resolve_project(project_facts, project, build_name)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        # Compile command comes from project_facts (the build's real compile recipe, with any
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        # @response file expanded), not from a makefile read here.
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        comp, flgs = project_facts.compile_command(project, build_name)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        if not comp:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared            print(f"{ASK_USER}: could not determine a compile command for {build_name}",
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared                  file=sys.stderr)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared            return EXIT_ASK_USER
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        data = cmd_measure(comp, " ".join(flgs), type_name, includes, platforms, backends)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedexcept LayoutError as error:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    text = str(error)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    if text.startswith("__COMPILER_ERROR__"):
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        # Compiler failed: print its error lines verbatim, exit 2.
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        sys.stdout.write(text[len("__COMPILER_ERROR__"):].lstrip() + "\n")
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared        return EXIT_COMPILER_ERROR
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    sys.stderr.write(str(error) + "\n")
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    return EXIT_COMPILER_ERROR
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedif as_json:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    emit(data)
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedelse:
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared    print(json.dumps(data, indent=1))
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedreturn EXIT_OK


if __name__ == "__main__":
 to ~/utilities/firmware when it is absent.  This keeps the tool working even if the sharedraise SystemExit(main())
