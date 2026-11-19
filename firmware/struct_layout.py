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
    # Prefer the copy that lives next to this script (the reviewed, self-contained one); fall back
    # to ~/utilities/firmware when it is absent.  This keeps the tool working even if the shared
    # project_facts has not yet been updated with compile_command.
    here = Path(__file__).resolve().parent / "project_facts.py"
    util = Path(os.path.expanduser("~/utilities/firmware/project_facts.py"))
    candidates = [here, util]
    for cand in candidates:
        if cand.is_file():
            sys.path.insert(0, str(cand.parent))
            break
    import project_facts  # noqa: E402
    return project_facts


# --------------------------------------------------------------------------- #
# back end selection
# --------------------------------------------------------------------------- #

def _load_platforms():
    here = Path(__file__).resolve().parent / "platforms.py"
    util = Path(os.path.expanduser("~/utilities/firmware/platforms.py"))
    for cand in (here, util):
        if cand.is_file() and str(cand.parent) not in sys.path:
            sys.path.insert(0, str(cand.parent))
    import platforms  # noqa: E402
    return platforms


def _load_backends():
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import backends  # noqa: E402
    return backends


def _back_end_for(compiler: str, platforms, backends):
    """Return the back-end module for the compiler, or exit 3 for an unknown compiler."""
    try:
        platform = platforms.for_compiler(compiler)
    except KeyError as error:
        print(f"{ASK_USER}: {error}", file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    return backends.clang if platform["family_kind"] == "clang" else backends.ti_legacy


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_measure(compiler: str, args: str, type_name: str, includes: list[str],
                platforms, backends) -> dict:
    """Measure one type via the matching compiler-family back end."""
    module = _back_end_for(compiler, platforms, backends)
    try:
        return module.record_layout(compiler, args, type_name, includes, Path("."))
    except backends.CompileError as error:
        # A compiler error: the front end exits 2 and prints the error lines verbatim.
        raise LayoutError("__COMPILER_ERROR__\n" + str(error)) from error


# --------------------------------------------------------------------------- #
# project mode (via project_facts)
# --------------------------------------------------------------------------- #

def resolve_project(project_facts, project: Path, build_name: str):
    """Return build_dir, compiler, flags or exit 3 with ASK USER for stale / not built / foreign."""
    installed = project_facts.inventory([project_facts.DEFAULT_ROOT])
    headers = project_facts.syscfg_headers(project)
    entries = project_facts.cproject_builds(project)
    if not entries:
        print(f"{ASK_USER}: no .cproject with builds in {project}", file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    facts_by_name = {e["name"]: project_facts.build_facts(project, e, headers, installed,
                                                          __import__("platform").system()) for e in entries}
    if build_name not in facts_by_name:
        print(f"{ASK_USER}: unknown build {build_name!r}; builds: {[e['name'] for e in entries]}",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    facts = facts_by_name[build_name]
    build_dir = project / build_name
    if not facts["built"]:
        print(f"{ASK_USER}: {build_name} was never built; build it in CCS then run struct_layout again",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    for item in facts["foreign"]:
        print(f"{ASK_USER}: {build_name} was generated on {item['os']} ({item['path']}); rebuild on this machine",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    if facts["stale"]:
        print(f"{ASK_USER}: {facts['stale'][0]}; rebuild {build_name} in CCS, then run struct_layout again",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    return build_dir, facts


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def emit(data: dict) -> None:
    print(json.dumps(data, indent=1))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

class LayoutError(Exception):
    """Raised by the front end when a back end reports a compiler/parse error."""


def main(argv=None) -> int:
    project_facts = _load_project_facts()
    platforms = _load_platforms()
    backends = _load_backends()
    tokens = list(argv if argv is not None else sys.argv[1:])
    # Flat interface (no subcommands):
    #   struct_layout.py PROJECT_DIR --build NAME --type T [--include h ...] [--json]
    #   struct_layout.py --compiler PATH --flags ".." --type T [--include h ...] [--json]
    project_dir = None
    build_name = None
    compiler = None
    flags = ""
    type_name = None
    includes: list[str] = []
    as_json = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--build":
            build_name = tokens[i + 1]; i += 2; continue
        if tok.startswith("--build="):
            build_name = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--compiler":
            compiler = tokens[i + 1]; i += 2; continue
        if tok.startswith("--compiler="):
            compiler = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--flags":
            flags = tokens[i + 1]; i += 2; continue
        if tok.startswith("--flags="):
            flags = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--type":
            type_name = tokens[i + 1]; i += 2; continue
        if tok.startswith("--type="):
            type_name = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--include":
            includes.append(tokens[i + 1]); i += 2; continue
        if tok.startswith("--include="):
            includes.append(tok.split("=", 1)[1]); i += 1; continue
        if tok == "--json":
            as_json = True; i += 1; continue
        if tok.startswith("-"):
            # Unknown flag; skip it and its value if it looks like one.
            i += 1; continue
        if project_dir is None:
            project_dir = tok
        i += 1

    if type_name is None:
        print("usage: struct_layout.py [--compiler PATH --flags \"..\"] --type T [--include h ...] [--json]"
              " | struct_layout.py PROJECT_DIR --build NAME --type T [--include h ...] [--json]",
              file=sys.stderr)
        return EXIT_ASK_USER

    try:
        if compiler is not None:
            data = cmd_measure(compiler, flags or "", type_name, includes, platforms, backends)
        else:
            if project_dir is None:
                print("usage: struct_layout.py PROJECT_DIR --build NAME --type T [--include h ...] [--json]",
                      file=sys.stderr)
                return EXIT_ASK_USER
            if build_name is None:
                print(f"{ASK_USER}: --build NAME required", file=sys.stderr)
                return EXIT_ASK_USER
            project = Path(project_dir).expanduser().resolve()
            build_dir, _facts = resolve_project(project_facts, project, build_name)
            # Compile command comes from project_facts (the build's real compile recipe, with any
            # @response file expanded), not from a makefile read here.
            comp, flgs = project_facts.compile_command(project, build_name)
            if not comp:
                print(f"{ASK_USER}: could not determine a compile command for {build_name}",
                      file=sys.stderr)
                return EXIT_ASK_USER
            data = cmd_measure(comp, " ".join(flgs), type_name, includes, platforms, backends)
    except LayoutError as error:
        text = str(error)
        if text.startswith("__COMPILER_ERROR__"):
            # Compiler failed: print its error lines verbatim, exit 2.
            sys.stdout.write(text[len("__COMPILER_ERROR__"):].lstrip() + "\n")
            return EXIT_COMPILER_ERROR
        sys.stderr.write(str(error) + "\n")
        return EXIT_COMPILER_ERROR
    if as_json:
        emit(data)
    else:
        print(json.dumps(data, indent=1))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
