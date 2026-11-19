#!/usr/bin/env python3
"""c_eval.py -- the build's own compiler evaluates C constant expressions (T5 part 1).

A model can hand-evaluate simple arithmetic, but it cannot see what a *particular* build computes
once the build's ``#define`` macros and the target's integer types come in: C integer division
truncates, a C28x ``int`` is 16 bits and wraps, casts and overflow follow the target ABI. This tool
asks the real toolchain.

Method (verified by Claude): write a probe file that ``#include``s the requested headers and then
one ``const long long __eN = (EXPR);`` line per expression, and compile it with the build's own
compiler and flags. clang family runs ``-S -emit-llvm -o -`` and the LLVM IR names each value as
``@__eN = hidden constant i64 <value>``; TI legacy runs ``-k`` into a temp dir and the assembly lists
each value at ``||__eN||:`` followed by ``.bits <value>,64``. The value is read as a signed 64-bit
two's-complement integer. The back end is chosen with ``platforms.for_compiler``; this front end
carries no compiler-specific strings.

CLI: ``c_eval.py PROJECT --build B [--source FILE] [--include H ...] EXPR [EXPR ...] [--json]`` (the
compiler and flags come from ``project_facts.compile_command``); or explicitly
``c_eval.py --compiler C --flags "..." [--include H] EXPR...``. JSON is
``{"results": [{"expr": str, "value": int}], "compiler": path, "unit": "byte"|"word16"}`` (unit is
the target's ``sizeof`` unit from ``platforms``). A non-constant expression or compile error exits 2
with the compiler text; a stale / not built / FOREIGN build exits 3 with ASK USER.

Standard library only. No chip or case names. Work in the tool's own folder; all compiler output
goes to /tmp.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_COMPILER_ERROR = 2
EXIT_ASK_USER = 3

ASK_USER = "ASK USER"


# --------------------------------------------------------------------------- #
# project_facts / platforms / backends import
# --------------------------------------------------------------------------- #

def _load_project_facts():
    here = Path(__file__).resolve().parent / "project_facts.py"
    if not here.is_file():
        raise RuntimeError("project_facts.py not found")
    sys.path.insert(0, str(here.parent))
    import project_facts  # noqa: E402
    return project_facts


def _load_platforms():
    here = Path(__file__).resolve().parent / "platforms.py"
    if not here.is_file():
        raise RuntimeError("platforms.py not found")
    if str(here.parent) not in sys.path:
        sys.path.insert(0, str(here.parent))
    import platforms  # noqa: E402
    return platforms


def _load_backends():
    here = Path(__file__).resolve().parent / "backends"
    if not (here / "__init__.py").is_file():
        raise RuntimeError("backends package not found")
    # Insert at the FRONT so ``import backends`` finds this one even when another package named
    # ``backends`` is already on sys.path.
    if str(here.parent) not in sys.path:
        sys.path.insert(0, str(here.parent))
    import backends  # noqa: E402
    return backends


# --------------------------------------------------------------------------- #
# back end / staleness selection
# --------------------------------------------------------------------------- #

def _back_end_for(compiler: str, platforms, backends):
    """Return the back-end module for the compiler, or exit 3 for an unknown compiler."""
    try:
        platform = platforms.for_compiler(compiler)
    except KeyError as error:
        print(f"{ASK_USER}: {error}", file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    return backends.clang if platform["family_kind"] == "clang" else backends.ti_legacy, platform


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
        print(f"{ASK_USER}: {build_name} was never built; build it in CCS then run c_eval again",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    for item in facts["foreign"]:
        print(f"{ASK_USER}: {build_name} was generated on {item['os']} ({item['path']}); rebuild on this machine",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    if facts["stale"]:
        print(f"{ASK_USER}: {facts['stale'][0]}; rebuild {build_name} in CCS, then run c_eval again",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    return build_dir, facts


# --------------------------------------------------------------------------- #
# command
# --------------------------------------------------------------------------- #

def cmd_eval(compiler: str, args: str, exprs: list[str], includes: list[str],
             platforms, backends, workdir: Path) -> dict:
    """Evaluate each expression in ``exprs`` with the matching compiler-family back end.

    Every expression is compiled in its own probe so one bad expression cannot drop the others; the
    results are returned in the given order. If any expression fails to evaluate, ``EvalError`` is
    raised naming every expression that could not be evaluated (the front end exits 2) -- a missing
    answer never looks like success.

    ``workdir`` is where the compiler is invoked from: the build folder for project mode (so a
    relative ``--cmd_file=...`` response and make-relative include paths resolve), or ``.`` for the
    explicit ``--compiler``/``--flags`` form (whose flags carry absolute paths)."""
    module, platform = _back_end_for(compiler, platforms, backends)
    results: list[dict] = []
    failed: list[str] = []
    for expr in exprs:
        try:
            value = module.evaluate_one(compiler, args, expr, includes, workdir)
            results.append({"expr": expr, "value": value})
        except backends.CompileError:
            # This expression is not a constant (or otherwise fails to compile); remember it and keep
            # going so the other expressions still get answered.
            failed.append(expr)
    if failed:
        raise EvalError("could not evaluate constant expression(s): " + ", ".join(failed))
    return {
        "results": results,
        "compiler": compiler,
        "unit": platform["unit"],
    }


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def emit(data: dict) -> None:
    print(json.dumps(data, indent=1))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

class EvalError(Exception):
    """Raised by the front end when a back end reports a compiler/parse error."""


def main(argv=None) -> int:
    project_facts = _load_project_facts()
    platforms = _load_platforms()
    backends = _load_backends()

    tokens = list(argv if argv is not None else sys.argv[1:])
    # Flat interface (no subcommands):
    #   c_eval.py PROJECT --build B [--source FILE] [--include H ...] EXPR [EXPR ...] [--json]
    #   c_eval.py --compiler C --flags "..." [--include H ...] EXPR [EXPR ...] [--json]
    project_dir = None
    build_name = None
    source = None
    compiler = None
    flags = ""
    includes: list[str] = []
    as_json = False

    exprs: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--build":
            build_name = tokens[i + 1]; i += 2; continue
        if tok.startswith("--build="):
            build_name = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--source":
            source = tokens[i + 1]; i += 2; continue
        if tok.startswith("--source="):
            source = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--compiler":
            compiler = tokens[i + 1]; i += 2; continue
        if tok.startswith("--compiler="):
            compiler = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--flags":
            flags = tokens[i + 1]; i += 2; continue
        if tok.startswith("--flags="):
            flags = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--include":
            includes.append(tokens[i + 1]); i += 2; continue
        if tok.startswith("--include="):
            includes.append(tok.split("=", 1)[1]); i += 1; continue
        if tok == "--json":
            as_json = True; i += 1; continue
        if tok == "--":
            # End-of-options marker: it changes nothing here, because a single-dash token (``-1``,
            # ``-7 / 2``) is already treated as an expression and only ``--`` double-dash tokens are
            # flags. Kept so callers can pass ``--`` to force expression mode without breaking the
            # trailing ``--json`` that the test harness appends.
            i += 1; continue
        if tok.startswith("--"):
            # An unknown ``--`` double-dash token is a flag; skip it so it is never mistaken for an
            # expression. (Known ``--`` flags are handled above.)
            i += 1; continue
        # A bare token: the first one (before any --compiler) is the project directory in project
        # mode; every later bare token is a constant expression to evaluate.
        if project_dir is None and compiler is None:
            project_dir = tok
        else:
            exprs.append(tok)
        i += 1

    if not exprs:
        print("usage: c_eval.py PROJECT --build B [--source FILE] [--include H ...] EXPR [EXPR ...] [--json]"
              " | c_eval.py --compiler C --flags \"...\" [--include H ...] EXPR [EXPR ...] [--json]",
              file=sys.stderr)
        return EXIT_COMPILER_ERROR

    try:
        if compiler is not None:
            data = cmd_eval(compiler, flags or "", exprs, includes, platforms, backends, Path("."))
        else:
            if project_dir is None:
                print("usage: c_eval.py PROJECT --build B [--source FILE] [--include H ...] EXPR [EXPR ...] [--json]",
                      file=sys.stderr)
                return EXIT_ASK_USER
            if build_name is None:
                print(f"{ASK_USER}: --build NAME required", file=sys.stderr)
                return EXIT_ASK_USER
            project = Path(project_dir).expanduser().resolve()
            build_dir, _facts = resolve_project(project_facts, project, build_name)
            # Compile command comes from project_facts (the build's real compile recipe, with any
            # @response file expanded), not from a makefile read here.
            comp, flgs = project_facts.compile_command(project, build_name, source=source)
            if not comp:
                print(f"{ASK_USER}: could not determine a compile command for {build_name}",
                      file=sys.stderr)
                return EXIT_ASK_USER
            data = cmd_eval(comp, " ".join(flgs), exprs, includes, platforms, backends, build_dir)
    except EvalError as error:
        # Compiler failed (non-constant expression, undeclared identifier, ...): print its text and
        # exit 2, exactly as the back end reported it.
        sys.stderr.write(str(error).rstrip() + "\n")
        return EXIT_COMPILER_ERROR
    if as_json:
        emit(data)
    else:
        print(json.dumps(data, indent=1))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
