#!/usr/bin/env python3
"""config_calls.py -- list every register write and driver-API call that a program makes while it
writes configuration (D4b static part, for Pi, 2026-11-19).

Builds on c_facts.py (same clang-family AST walk; reuse its functions by import, do not copy) and
c_eval (backends.evaluate). For every function that the given source files define, the compiler's AST
is dumped once and walked:

* a *register write* is an assignment (=, |=, &=, ^=, <<=, >>=, +=, -=, ...) whose left side is a
  member / array access reached through `->` or `.` of a struct that the AST marks ``volatile`` --
  e.g. ``TIMG4->COUNTERREGS.CCACT_01[...]`` or ``SysTick->VAL``. The LHS source text is the target,
  the array subscript (if any) is the index, the assignment opcode is the op, the RHS source text is
  the value.
* a *driver call* is a call whose callee name starts with one of --prefix (default DL_, SYSCFG_DL_,
  GPIO_, SysCtl_, EPWM_, ADC_, CAN_, MCAN_, SCI_, SPI_, I2C_, Interrupt_, CLA_). The callee name is
  the target and the argument list is the value.

Constant values are evaluated by the compiler: ``value_int`` is the evaluated whole-RHS constant only
when the entire RHS is a single constant expression (evaluated with the file's own includes); a
``?:`` RHS, a macro operand, or a non-constant RHS keeps ``value_int`` null. The ``path`` records the
enclosing if/else branches from outermost to innermost (innermost last).

TI legacy families report "not available yet" (exit 2). STALE / not built / foreign builds exit 3
(ASK USER). Standard library only. Work in the tool's own folder; all compiler output goes to /tmp.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_COMPILER_ERROR = 2
EXIT_ASK_USER = 3

ASK_USER = "ASK USER"

# Assignment opcodes (the spec's =, |=, &=, ^=, <<=, >>= plus the additive ones the AST reports).
ASSIGN_OPS = {"=", "|=", "&=", "^=", "<<=", ">>=", "+=", "-=", "*=", "/=", "%="}

# Default driver-API call prefixes.
DEFAULT_PREFIXES = (
    "DL_", "SYSCFG_DL_", "GPIO_", "SysCtl_", "EPWM_", "ADC_", "CAN_", "MCAN_",
    "SCI_", "SPI_", "I2C_", "Interrupt_", "CLA_",
)


# --------------------------------------------------------------------------- #
# project_facts / platforms / backends import (local copies only; reused from c_facts)
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
    if str(here.parent) not in sys.path:
        sys.path.insert(0, str(here.parent))
    import backends  # noqa: E402
    return backends


# --------------------------------------------------------------------------- #
# build selection (stale / not built / foreign -> exit 3) -- same as c_facts
# --------------------------------------------------------------------------- #

def resolve_build(project_facts, project: Path, build_name: str):
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
        print(f"{ASK_USER}: {build_name} was never built; build it in CCS then run config_calls again",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    for item in facts["foreign"]:
        print(f"{ASK_USER}: {build_name} was generated on {item['os']} ({item['path']}); rebuild on this machine",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    if facts["stale"]:
        print(f"{ASK_USER}: {facts['stale'][0]}; rebuild {build_name} in CCS, then run config_calls again",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    return build_dir, facts


# --------------------------------------------------------------------------- #
# AST dump (clang family only; reuse c_facts' concatenated-JSON parser)
# --------------------------------------------------------------------------- #

class EvalError(Exception):
    """Raised by the front end when the compiler fails or its output cannot be parsed."""


def _parse_concatenated_json(text: str) -> list[dict]:
    decoder = json.decoder.JSONDecoder()
    decls: list[dict] = []
    idx = 0
    n = len(text)
    while idx < n:
        while idx < n and text[idx] in " \n\r\t":
            idx += 1
        if idx >= n:
            break
        obj, end = decoder.raw_decode(text, idx)
        decls.append(obj)
        idx = end
    return decls


def run_ast_dump(compiler: str, args: list[str], source_path: str, function: str,
                 build_dir: Path) -> str:
    cmd = [compiler] + list(args) + [
        "-fsyntax-only",
        "-Xclang", "-ast-dump=json",
        "-Xclang", f"-ast-dump-filter={function}",
        source_path,
    ]
    with tempfile.TemporaryDirectory(prefix="config_calls_ast_") as tmp:
        proc = subprocess.run(cmd, cwd=str(tmp), capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise EvalError(proc.stderr or "clang ast-dump failed")
        return proc.stdout


def _top_level_function_names(ast_text: str, source_name: str) -> list[tuple[str, str]]:
    """The (name, file) of every top-level FunctionDecl in a full-TU dump.

    A function is *defined* in the given source file when its FunctionDecl's ``loc.file`` is that
    file (a header declaration with the same name lives in the header). Returns the names of the
    functions defined in ``source_name`` so each can be dumped individually with ``-ast-dump-filter``."""
    decls = _parse_concatenated_json(ast_text)
    if not decls:
        return []
    root = decls[0]
    out: list[tuple[str, str]] = []
    for child in root.get("inner", []) or []:
        if child.get("kind") != "FunctionDecl" or child.get("name") is None:
            continue
        loc = child.get("loc", {}) or {}
        file_name = loc.get("file")
        # A function *defined* in the given source file carries its ``loc.file`` explicitly set to
        # that file. Header declarations (and function-like macros expanded into this file) have no
        # ``file`` key in their loc, so they are excluded here.
        if file_name is None:
            continue
        if Path(file_name).name == source_name:
            out.append((child["name"], Path(file_name).name))
    return out


# --------------------------------------------------------------------------- #
# AST walking helpers (reused ideas from c_facts; not copied)
# --------------------------------------------------------------------------- #

class _LineWalker:
    """Track the last seen source line while walking the AST (clang omits repeated lines)."""

    def __init__(self) -> None:
        self.last: int | None = None

    def line_of(self, node: dict) -> int | None:
        loc = node.get("loc") or {}
        rng = node.get("range") or {}
        begin = rng.get("begin") or {}
        end = rng.get("end") or {}
        cand = loc.get("line")
        if cand is None:
            cand = begin.get("line")
        if cand is None:
            for where in ("begin", "end"):
                for key in ("expansionLoc", "spellingLoc"):
                    sub = (rng.get(where) or loc.get(key) or {}).get(key)
                    if isinstance(sub, dict) and sub.get("line") is not None:
                        cand = sub["line"]
                        break
                if cand is not None:
                    break
        if cand is None:
            cand = end.get("line")
        if cand is None:
            cand = self.last
        if cand is not None:
            self.last = cand
        return self.last


def _node_line(node: dict | None, walker: _LineWalker) -> int | None:
    if not isinstance(node, dict):
        return walker.last
    return walker.line_of(node)


def _node_offset(node: dict | None) -> tuple[int | None, int | None]:
    if not isinstance(node, dict):
        return None, None
    rng = node.get("range") or {}
    begin = rng.get("begin") or (node.get("loc") or {})
    end = rng.get("end") or {}
    start_off = begin.get("offset")
    if start_off is None:
        for key in ("expansionLoc", "spellingLoc"):
            sub = begin.get(key)
            if isinstance(sub, dict) and sub.get("offset") is not None:
                start_off = sub["offset"]
                break
    end_off = end.get("offset")
    end_tok = end.get("tokLen")
    if end_off is None:
        for key in ("expansionLoc", "spellingLoc"):
            sub = end.get(key)
            if isinstance(sub, dict) and sub.get("offset") is not None:
                end_off = sub["offset"]
                end_tok = sub.get("tokLen")
                break
    if end_off is not None and end_tok:
        end_off = end_off + end_tok
    elif end_off is None and start_off is not None and end_tok:
        end_off = start_off + end_tok
    return start_off, end_off


def _source_text(node: dict | None, source_text: str) -> str | None:
    start_off, end_off = _node_offset(node)
    if start_off is None or end_off is None:
        return None
    try:
        return source_text[start_off:end_off]
    except Exception:
        return None


def _int_literal_value(node: dict | None) -> int | None:
    if isinstance(node, dict) and node.get("kind") == "IntegerLiteral":
        value = node.get("value")
        if isinstance(value, dict) and "value" in value:
            return int(value["value"])
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _qual_type(node: dict | None) -> str:
    if isinstance(node, dict) and node.get("type"):
        t = node["type"]
        return t.get("qualType") or t.get("qual_type") or ""
    return ""


def _is_assignment(node: dict) -> bool:
    return (node.get("kind") in ("BinaryOperator", "CompoundAssignOperator")
            and node.get("opcode") in ASSIGN_OPS)


def _member_chain_text(lhs: dict, source_text: str) -> str | None:
    """The source text of a member/array access chain, e.g. ``TIMG4->COUNTERREGS.CCACT_01[IO_CC]``.

    The outer node is the whole lvalue (an ``ArraySubscriptExpr`` for ``a[b]`` or a ``MemberExpr`` for
    ``a.b``). Its begin..end range spans the entire ``a->b.c[d]`` expression, so the outer node's own
    source text is the full chain text."""
    if not isinstance(lhs, dict):
        return None
    return _source_text(lhs, source_text)


def _index_of(lhs: dict, source_text: str, compiler, args, includes, workdir, backends,
              source_file) -> tuple[str | None, int | None]:
    """Return (index_src, index_value) for ``lhs`` when it is an array subscript ``base[idx]``.

    The subscript's base is reached through an ``ImplicitCastExpr`` (the pointer); the array index is
    the subscript's own inner[1]."""
    cur = lhs
    seen = set()
    while isinstance(cur, dict):
        if id(cur) in seen:
            return None, None
        seen.add(id(cur))
        if cur.get("kind") == "ArraySubscriptExpr":
            inner = cur.get("inner") or []
            idx_node = inner[1] if len(inner) >= 2 else None
            idx_text = _source_text(idx_node, source_text)
            idx_value = _int_literal_value(idx_node)
            if idx_value is None and idx_text:
                idx_value = _try_eval_expr(idx_text, compiler, args, includes, workdir,
                                           backends, source_file)
            return idx_text, idx_value
        if cur.get("kind") not in ("ImplicitCastExpr", "UnaryExprOrTypeCastExpr"):
            return None, None
        inner = cur.get("inner") or []
        cur = inner[0] if inner else None
    return None, None


def _volatile_base(lhs: dict) -> bool:
    """True when the member/array access ``lhs`` ultimately reads a ``volatile`` object.

    The AST types the outer member access itself (``volatile uint32_t`` for ``TIMG4->COUNTERREGS.CTR``
    or the array element), so the outer node's own type is checked first; then the chain is walked
    down for the array-of-volatile case; and finally the base pointer's ``referencedDecl`` type is
    checked (a pointer to a ``volatile`` struct, e.g. ``SysTick``)."""
    seen = set()
    cur = lhs
    while isinstance(cur, dict):
        if id(cur) in seen:
            break
        seen.add(id(cur))
        qt = _qual_type(cur)
        if "volatile" in qt:
            return True
        if cur.get("kind") == "DeclRefExpr":
            ref = cur.get("referencedDecl")
            if isinstance(ref, dict) and "volatile" in (ref.get("type", {}) or {}).get("qualType", ""):
                return True
        inner = cur.get("inner") or []
        cur = inner[0] if inner else None
    return False


def _callee_name(call: dict) -> str | None:
    """The callee identifier of a CallExpr (a bare function name)."""
    inner = call.get("inner") or []
    if not inner:
        return None
    callee = inner[0]

    def _ref_name(node: dict) -> str | None:
        if node.get("kind") == "DeclRefExpr":
            if node.get("name"):
                return node["name"]
            ref = node.get("referencedDecl")
            if isinstance(ref, dict):
                return ref.get("name")
        return None

    # A member-function / dotted call (e.g. obj.f()) is not a plain driver call here.
    if callee.get("kind") == "MemberExpr":
        return None
    name = _ref_name(callee)
    if name is not None:
        return name
    # A function-pointer cast around a DeclRefExpr -- fall through to its inner.
    if callee.get("kind") in ("ImplicitCastExpr", "CStyleCastExpr", "UnaryExprOrTypeCastExpr"):
        sub = (callee.get("inner") or [None])[0]
        return _ref_name(sub) if isinstance(sub, dict) else None
    return None


def _arg_text(arg: dict, source_text: str) -> str | None:
    return _source_text(arg, source_text)


def _try_eval_expr(text: str | None, compiler, args, includes, workdir, backends, source_file) -> int | None:
    """Evaluate a constant C expression (a macro, ``1 << IO_CC``, ``A | B``, ...) with c_eval.

    Returns the computed int, or None when the text is empty or fails to evaluate (a non-constant
    expression is not a constant value). The source file is spliced in so the build's own macros are
    visible."""
    if not text:
        return None
    text = text.strip()
    incs = list(includes)
    if source_file:
        incs.append(source_file)
    if backends is None:
        return None
    try:
        values = backends.clang.evaluate(compiler, " ".join(args), [text], incs, workdir)
        return values[0]
    except Exception:
        return None


def _eval_rhs_value(rhs: dict, source_text: str, compiler, args, includes, workdir,
                    backends, source_file) -> int | None:
    """value_int for an assignment RHS.

    A plain integer literal gives its value directly. Otherwise the whole RHS is a constant
    expression and is evaluated with the compiler (the build's own macros come in through the
    includes); a ``?:`` rhs keeps value_int null (the spec: a ``?:`` rhs keeps value_int null)."""
    if rhs is None:
        return None
    if rhs.get("kind") == "ConditionalOperator":
        return None
    direct = _int_literal_value(rhs)
    if direct is not None:
        return direct
    text = _source_text(rhs, source_text)
    return _try_eval_expr(text, compiler, args, includes, workdir, backends, source_file)


# --------------------------------------------------------------------------- #
# fact extraction over one function's AST
# --------------------------------------------------------------------------- #

def _extract_writes(func_node: dict, walker: _LineWalker, source_text: str, prefixes,
                    compiler, args, includes, workdir, backends, source_file) -> list[dict]:
    """Every register write and driver call in ``func_node``, innermost-last path on each entry."""
    out: list[dict] = []
    path: list[dict] = []
    function_name = func_node.get("name")

    def record(entry: dict) -> None:
        e = dict(entry)
        e["path"] = [dict(p) for p in path]
        e["function"] = function_name
        out.append(e)
    def walk(node: dict) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")

        if kind == "IfStmt":
            inner = node.get("inner") or []
            then_node = inner[1] if len(inner) >= 2 else None
            else_node = inner[2] if len(inner) >= 3 else None
            arm_line = _node_line(node, walker)
            if then_node:
                path.append({"if_line": arm_line, "arm": "then"})
                walk(then_node)
                path.pop()
            if else_node:
                path.append({"if_line": arm_line, "arm": "else"})
                walk(else_node)
                path.pop()
            return

        if kind in ("BinaryOperator", "CompoundAssignOperator") and _is_assignment(node):
            inner = node.get("inner") or []
            lhs = inner[0] if len(inner) >= 1 else None
            rhs = inner[1] if len(inner) >= 2 else None
            # Register write: the LHS is a volatile member/array access (reached through -> or .).
            # A bare DeclRefExpr (e.g. a volatile local variable such as ``offsetCalCounter = 0``)
            # is a plain assignment, not a register write, even when the AST marks its type volatile.
            if isinstance(lhs, dict) and lhs.get("kind") in ("MemberExpr", "ArraySubscriptExpr") \
                    and _volatile_base(lhs):
                index_src, index_value = _index_of(lhs, source_text, compiler, args, includes,
                                                     workdir, backends, source_file)
                record({
                    "kind": "register",
                    "target": _member_chain_text(lhs, source_text),
                    "index": index_src,
                    "index_value": index_value,
                    "op": node.get("opcode"),
                    "value": _source_text(rhs, source_text),
                    "value_int": _eval_rhs_value(rhs, source_text, compiler, args, includes,
                                                 workdir, backends, source_file),
                    "line": _node_line(node, walker),
                })
                # Still descend: a register write's RHS may itself be a driver call.
                if rhs:
                    walk(rhs)
                return
            # A plain local-variable assignment is not a register write and not a driver call.
            if isinstance(lhs, dict) and lhs.get("kind") == "DeclRefExpr":
                if rhs:
                    walk(rhs)
                return
            # Otherwise descend into both sides (an RHS could be a driver call).
            if lhs:
                walk(lhs)
            if rhs:
                walk(rhs)
            return

        if kind == "CallExpr":
            name = _callee_name(node)
            if name and any(name.startswith(p) for p in prefixes):
                inner = node.get("inner") or []
                args_text = ", ".join(_source_text(a, source_text) for a in inner[1:]
                                      if isinstance(a, dict))
                record({
                    "kind": "call",
                    "target": name,
                    "index": None,
                    "index_value": None,
                    "op": None,
                    "value": args_text,
                    "value_int": None,
                    "line": _node_line(node, walker),
                })
            # Descend so calls nested in an argument list are still found.
            for child in node.get("inner", []) or []:
                walk(child)
            return

        for child in node.get("inner", []) or []:
            walk(child)

    for child in func_node.get("inner", []) or []:
        walk(child)
    return out


# --------------------------------------------------------------------------- #
# function selection + per-file collection
# --------------------------------------------------------------------------- #

def collect_file(project_facts, platforms, backends, project: Path, build_name: str,
                 source_file: str, prefixes) -> list[dict]:
    build_dir, _facts = resolve_build(project_facts, project, build_name)
    comp, flgs = project_facts.compile_command(project, build_name, source=source_file)
    if not comp:
        print(f"{ASK_USER}: could not determine a compile command for {build_name}",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    try:
        platform = platforms.for_compiler(comp)
        family = platform["family_kind"]
    except KeyError:
        family = "unknown"
    if family != "clang":
        print("not available yet for %s" % family, file=sys.stderr)
        sys.exit(EXIT_COMPILER_ERROR)

    source_path = str(project / source_file)
    source_name = Path(source_path).name
    source_path_resolved = Path(source_path).expanduser()
    source_text = source_path_resolved.read_text(encoding="utf-8", errors="replace")
    file_end = len(source_text)
    walker = _LineWalker()
    includes = []

    # A single full-TU dump gives the body of every function at once. A function *defined* in this
    # source file has its body (the statement range) inside this file's byte range; header
    # declarations and function-like macros expanded into this file have their body elsewhere. For
    # each name, keep the definition -- the one whose body spans this file (the later occurrence).
    with tempfile.TemporaryDirectory(prefix="config_calls_ast_") as tmp:
        cmd = [comp] + list(flgs) + ["-fsyntax-only", "-Xclang", "-ast-dump=json", source_path]
        proc = subprocess.run(cmd, cwd=str(tmp), capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise EvalError(proc.stderr or "clang ast-dump failed")
        decls = _parse_concatenated_json(proc.stdout)

    def _body_range(node: dict) -> tuple[int | None, int | None]:
        first = last = None

        def walk(n: dict) -> None:
            nonlocal first, last
            for child in n.get("inner", []) or []:
                bo, eo = _node_offset(child)
                if bo is not None:
                    first = bo if first is None else min(first, bo)
                if eo is not None:
                    last = eo if last is None else max(last, eo)
                walk(child)
        walk(node)
        return first, last

    root = decls[0]
    defns: dict[str, dict] = {}
    order: list[str] = []
    for d in root.get("inner", []) or []:
        if d.get("kind") != "FunctionDecl" or d.get("name") is None:
            continue
        body_first, body_last = _body_range(d)
        # A definition in this file has its body inside this file's byte range.
        if body_last is None or body_last > file_end:
            continue
        name = d["name"]
        # Keep the later occurrence (the definition over the header declaration) per name.
        defns[name] = d
        if name not in order:
            order.append(name)

    results: list[dict] = []
    for func_name in order:
        func_node = defns[func_name]
        writes = _extract_writes(func_node, walker, source_text, prefixes, comp, flgs,
                                 includes, build_dir, backends, source_file)
        for w in writes:
            w["file"] = source_file
        results.extend(writes)

    # Sort in file and line order.
    def _line_of(entry):
        return entry["line"]

    results.sort(key=_line_of)
    return results


# --------------------------------------------------------------------------- #
# JSON output
# --------------------------------------------------------------------------- #

def emit_writes(writes: list[dict]) -> None:
    out = []
    for w in writes:
        out.append({
            "file": w["file"],
            "line": w["line"],
            "function": w["function"],
            "kind": w["kind"],
            "target": w["target"],
            "index": w["index"],
            "index_value": w["index_value"],
            "op": w["op"],
            "value": w["value"],
            "value_int": w["value_int"],
            "path": w["path"],
        })
    print(json.dumps({"writes": out}, indent=1))


# --------------------------------------------------------------------------- #
# front end
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    project_facts = _load_project_facts()
    platforms = _load_platforms()
    backends = _load_backends()

    tokens = list(argv if argv is not None else sys.argv[1:])
    project_dir = None
    build_name = None
    as_json = False
    prefixes = list(DEFAULT_PREFIXES)
    files: list[str] = []

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--build":
            build_name = tokens[i + 1]; i += 2; continue
        if tok.startswith("--build="):
            build_name = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--prefix":
            prefixes.append(tokens[i + 1]); i += 2; continue
        if tok.startswith("--prefix="):
            prefixes.append(tok.split("=", 1)[1]); i += 1; continue
        if tok == "--json":
            as_json = True; i += 1; continue
        if tok.startswith("--"):
            i += 1; continue
        # Bare tokens: first is the project dir, the rest are source files.
        if project_dir is None:
            project_dir = tok
        else:
            files.append(tok)
        i += 1

    if project_dir is None or build_name is None or not files:
        print("usage: config_calls.py PROJECT --build B FILE [FILE ...] "
              "[--prefix P ...] [--json]", file=sys.stderr)
        return EXIT_ASK_USER

    try:
        project = Path(project_dir).expanduser().resolve()
        all_writes: list[dict] = []
        for source_file in files:
            all_writes.extend(collect_file(project_facts, platforms, backends, project,
                                           build_name, source_file, prefixes))
    except EvalError as error:
        sys.stderr.write(str(error).rstrip() + "\n")
        return EXIT_COMPILER_ERROR
    except SystemExit:
        raise
    except Exception as error:
        print(f"{ASK_USER}: {error}", file=sys.stderr)
        return EXIT_COMPILER_ERROR

    if not as_json:
        for w in all_writes:
            print(f"{w['file']}:{w['line']}: {w['kind']} {w['target']} {w['op'] or ''} {w['value']}")
        return EXIT_OK

    emit_writes(all_writes)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
