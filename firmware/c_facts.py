#!/usr/bin/env python3
"""c_facts.py -- control-flow and value facts from the compiler's AST (T5 part 2).

A model can read a function by eye, but it keeps getting the *facts* wrong: which `if` an `else`
belongs to, which branch an assignment is in, the two values of a `?:`, a post-increment sitting in a
condition. This tool asks the compiler's own AST instead of guessing.

Method (clang family only in v1): run

    <compiler> <project_facts.compile_command args for FILE> -fsyntax-only
             -Xclang -ast-dump=json -Xclang -ast-dump-filter=<FUNCTION> <file>

in the build folder. clang emits one JSON AST per function (concatenated); each node carries a
``loc`` and a ``range.begin/end`` with ``line``/``offset``/``tokLen``. clang omits ``line`` when it
equals the previous node's line, so the walker keeps the last seen line while walking (in "loc" and
"range.begin/end", also inside "expansionLoc"/"spellingLoc" for macro locations). Source text of a
node = the file text between its begin and end offsets (``offset`` + ``tokLen``). Constant values of
macro operands are evaluated with c_eval's ``backends.evaluate``.

TI legacy families report "AST facts not available" (exit 2) -- honest, planned later.

CLI: ``c_facts.py PROJECT --build B FILE --function NAME [--json]`` (STALE etc. -> exit 3 ASK USER).
Standard library only. No chip or case names. Work in the tool's own folder; all compiler output
goes to /tmp.
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


# --------------------------------------------------------------------------- #
# project_facts / platforms / backends import (local copies only)
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
# build selection (stale / not built / foreign -> exit 3)
# --------------------------------------------------------------------------- #

def resolve_build(project_facts, project: Path, build_name: str):
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
        print(f"{ASK_USER}: {build_name} was never built; build it in CCS then run c_facts again",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    for item in facts["foreign"]:
        print(f"{ASK_USER}: {build_name} was generated on {item['os']} ({item['path']}); rebuild on this machine",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    if facts["stale"]:
        print(f"{ASK_USER}: {facts['stale'][0]}; rebuild {build_name} in CCS, then run c_facts again",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    return build_dir, facts


# --------------------------------------------------------------------------- #
# AST dump
# --------------------------------------------------------------------------- #

class EvalError(Exception):
    """Raised by the front end when the compiler fails or its output cannot be parsed."""


def _parse_concatenated_json(text: str) -> list[dict]:
    """Parse clang's concatenated JSON AST (one object per function) into a list."""
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
    """Run clang's -fsyntax-only -ast-dump=json for one function; return stdout text.

    ``args`` are the build's compile-command flags (defines, includes, target flags). The compiler
    runs from the build folder so make-relative include paths and any ``@response``-expanded flags
    resolve. All output stays in /tmp; nothing is written into the project."""
    cmd = [compiler] + list(args) + [
        "-fsyntax-only",
        "-Xclang", "-ast-dump=json",
        "-Xclang", f"-ast-dump-filter={function}",
        source_path,
    ]
    with tempfile.TemporaryDirectory(prefix="c_facts_ast_") as tmp:
        proc = subprocess.run(cmd, cwd=str(build_dir), capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise EvalError(proc.stderr or "clang ast-dump failed")
        return proc.stdout


# --------------------------------------------------------------------------- #
# AST walking helpers
# --------------------------------------------------------------------------- #

class _LineWalker:
    """Track the last seen source line while walking the AST.

    clang omits ``line`` whenever it equals the previous node's line, so a node's own ``loc`` /
    ``range.begin`` may be ``None``. The walker returns the last non-None line seen on the path,
    which is the line the node actually sits on. When a node carries an ``offset`` but no ``line``,
    the optional ``offset_to_line`` table resolves it exactly (needed because clang sometimes omits
    the line even across a line boundary, e.g. a ``VarDecl`` whose ``range.begin`` has only an
    offset)."""

    def __init__(self, offset_to_line=None) -> None:
        self.last: int | None = None
        self.offset_to_line = offset_to_line

    def line_of(self, node: dict) -> int | None:
        loc = node.get("loc") or {}
        rng = node.get("range") or {}
        begin = rng.get("begin") or {}
        end = rng.get("end") or {}
        # 1. Explicit lines from loc and range.begin.
        cand = loc.get("line")
        if cand is None:
            cand = begin.get("line")
        # 2. Macro locations: expansionLoc / spellingLoc carry their own line (begin first, since the
        #    begin offset is the node's true start).
        if cand is None:
            for where in ("begin", "end"):
                for key in ("expansionLoc", "spellingLoc"):
                    sub = (rng.get(where) or loc.get(key) or {}).get(key)
                    if isinstance(sub, dict) and sub.get("line") is not None:
                        cand = sub["line"]
                        break
                if cand is not None:
                    break
        # 3. Resolve the begin offset to a line when clang omitted the begin line. This is checked
        #    before range.end.line because the begin offset is the node's true start; the end line
        #    would report the node's end instead (clang can omit the begin line even across a line
        #    boundary, e.g. a CompoundStmt whose ``{`` sits on the previous line).
        if cand is None and self.offset_to_line is not None:
            off = begin.get("offset")
            if off is not None:
                cand = self.offset_to_line(off)
        # 4. Fall back to range.end.line, then the last seen line.
        if cand is None:
            cand = end.get("line")
        if cand is None:
            cand = self.last
        if cand is not None:
            self.last = cand
        return self.last


def _node_line(node: dict | None, walker: _LineWalker) -> int | None:
    """The source line of ``node`` (its own loc/range, else the last seen line)."""
    if not isinstance(node, dict):
        return walker.last
    return walker.line_of(node)


def _make_offset_to_line(source_text: str):
    """Return a function mapping a character offset to its 1-based source line.

    Built once from the source file; used to resolve node offsets whose ``line`` clang omitted."""
    # Prefix end-offsets of each line for binary search.
    import bisect
    boundaries = [0]
    total = 0
    for line in source_text.splitlines(keepends=True):
        total += len(line)
        boundaries.append(total)
    def offset_to_line(off: int) -> int:
        if off < 0:
            return None
        if off > total:
            off = total
        # bisect_right finds the first boundary > off; that index is the 1-based line number.
        return bisect.bisect_right(boundaries, off)
    return offset_to_line


def _node_offset(node: dict | None) -> tuple[int | None, int | None]:
    """(begin_offset, end_offset) of a node from its range; end_offset is after the last token.

    clang's ``range.end`` may omit a direct ``offset`` when the end sits in a macro expansion; then
    the ``expansionLoc`` / ``spellingLoc`` inside ``range.end`` carry the primary-file offset.
    """
    if not isinstance(node, dict):
        return None, None
    rng = node.get("range") or {}
    begin = rng.get("begin") or (node.get("loc") or {})
    end = rng.get("end") or {}
    # begin.offset may be absent when the node starts inside a macro; use expansionLoc/spellingLoc.
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
        # Fall back to the expansion/spelling offset (primary-file offset for macro ends).
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
    """The file text between a node's begin and end offsets."""
    start_off, end_off = _node_offset(node)
    if start_off is None or end_off is None:
        return None
    try:
        return source_text[start_off:end_off]
    except Exception:
        return None


def _stmt_range(node: dict | None, walker: _LineWalker) -> tuple[int | None, int | None]:
    """(first, last) raw AST statement lines for ``node`` -- the node's own begin..end range,
    extended by its children. This matches what the compiler's AST reports: a ``CompoundStmt``
    block spans from its opening brace line to its closing brace line, braces included (not
    content-only)."""
    first = None
    last = None
    if isinstance(node, dict):
        # The node's own begin and end lines (via last-seen-line tracking, with macro-location and
        # offset fallbacks) are the raw statement range.
        first = _node_line(node, walker)
        rng = node.get("range") or {}
        end_node = rng.get("end") or {}
        end_line = end_node.get("line")
        if end_line is None:
            end_line = (end_node.get("expansionLoc") or {}).get("line")
        if end_line is None:
            end_off = end_node.get("offset")
            if end_off is not None and walker.offset_to_line is not None:
                end_line = walker.offset_to_line(end_off)
        last = end_line if end_line is not None else first
        # Extend by children so a statement whose begin/end lines are omitted still spans its body.
        for child in node.get("inner", []) or []:
            cfirst, clast = _stmt_range(child, walker)
            if cfirst is not None:
                first = cfirst if first is None else min(first, cfirst)
            if clast is not None:
                last = clast if last is None else max(last, clast)
    else:
        first = walker.last
        last = walker.last
    return first, last


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


def _is_local_var_ref(node: dict | None) -> bool:
    """True when ``node`` is a DeclRefExpr to a VarDecl/ParmVarDecl (a plain local variable)."""
    if not isinstance(node, dict) or node.get("kind") != "DeclRefExpr":
        return False
    ref = node.get("referencedDecl")
    return isinstance(ref, dict) and ref.get("kind") in ("VarDecl", "ParmVarDecl")


# --------------------------------------------------------------------------- #
# fact extraction
# --------------------------------------------------------------------------- #

def _extract_ifs(func_node: dict, walker: _LineWalker, source_text: str) -> list[dict]:
    """Collect every IfStmt into the ``ifs`` fact.

    Each entry: {line, condition, then:[first,last], else:[first,last]|null,
    else_if_line:int|null, parent_if_line:int|null}. ``condition`` is the source text of the
    condition expression. For an ``else if``, the parent's ``else_if_line`` points at it and its own
    ``parent_if_line`` points at the ``if`` whose ``else`` it is."""
    ifs: list[dict] = []

    def walk(node: dict, parent_if: dict | None) -> None:
        if not isinstance(node, dict):
            return
        if node.get("kind") == "IfStmt":
            inner = node.get("inner") or []
            cond_node = inner[0] if len(inner) >= 1 else None
            then_node = inner[1] if len(inner) >= 2 else None
            else_node = inner[2] if len(inner) >= 3 else None
            line = _node_line(node, walker)
            entry = {
                "line": line,
                "condition": _source_text(cond_node, source_text),
                "then": list(_stmt_range(then_node, walker)) if then_node else None,
                "else": list(_stmt_range(else_node, walker)) if else_node else None,
                "else_if_line": None,
                "parent_if_line": None,
            }
            ifs.append(entry)
            if cond_node:
                walk(cond_node, entry)
            if then_node:
                walk(then_node, entry)
            if else_node:
                walk(else_node, entry)
        else:
            for child in node.get("inner", []) or []:
                walk(child, parent_if)

    for child in func_node.get("inner", []) or []:
        walk(child, None)

    # Link else-if entries: an else branch that is itself an IfStmt is an ``else if``.
    for entry in ifs:
        if entry["else"] is not None:
            pass  # linked below by structural walk
    # Structural second pass to set the links precisely.
    _link_else_ifs(func_node, ifs)
    return ifs


def _link_else_ifs(node: dict, ifs: list[dict]) -> None:
    """Set else_if_line / parent_if_line by matching IfStmt else-branches to else-if IfStmts."""
    if not isinstance(node, dict):
        return
    if node.get("kind") == "IfStmt":
        inner = node.get("inner") or []
        else_node = inner[2] if len(inner) >= 3 else None
        if else_node and else_node.get("kind") == "IfStmt":
            walker = _LineWalker()
            parent_line = _node_line(node, walker)
            else_if_line = _node_line(else_node, walker)
            parent_entry = next((e for e in ifs if e["line"] == parent_line), None)
            else_entry = next((e for e in ifs if e["line"] == else_if_line), None)
            if parent_entry is not None:
                parent_entry["else_if_line"] = else_if_line
            if else_entry is not None:
                else_entry["parent_if_line"] = parent_line
        for child in node.get("inner", []) or []:
            _link_else_ifs(child, ifs)
    else:
        for child in node.get("inner", []) or []:
            _link_else_ifs(child, ifs)


def _extract_conditionals(func_node: dict, walker: _LineWalker, source_text: str,
                          compiler: str, args: list[str], includes: list[str],
                          workdir: Path, backends, source_file: str) -> list[dict]:
    """Collect every ConditionalOperator (``?:``) into the ``conditionals`` fact."""
    out: list[dict] = []

    def walk(node: dict, parent: dict | None) -> None:
        if not isinstance(node, dict):
            return
        if node.get("kind") == "ConditionalOperator":
            inner = node.get("inner") or []
            cond = inner[0] if len(inner) >= 1 else None
            then_e = inner[1] if len(inner) >= 2 else None
            else_e = inner[2] if len(inner) >= 3 else None
            then_text = _source_text(then_e, source_text)
            else_text = _source_text(else_e, source_text)
            # A plain integer literal gives its value directly; a macro operand is evaluated with
            # c_eval's backends.evaluate so the build's own macro value comes in.
            then_value = _int_literal_value(then_e) or _eval_text(
                then_text, compiler, args, includes, workdir, backends, source_file)
            else_value = _int_literal_value(else_e) or _eval_text(
                else_text, compiler, args, includes, workdir, backends, source_file)
            # The ``target`` is the LHS of the enclosing assignment (``LHS = (...)``), when the
            # ternary is the RHS of a ``=`` / compound-assign to a local variable.
            target = None
            if parent and parent.get("kind") in ("BinaryOperator", "CompoundAssignOperator"):
                popcode = parent.get("opcode")
                if popcode in {"=", "|=", "&=", "^=", "<<=", ">>=", "+=", "-=", "*=", "/=", "%="}:
                    lhs = parent.get("inner", [None])[0]
                    if isinstance(lhs, dict):
                        target = _source_text(lhs, source_text)
            out.append({
                "line": _node_line(node, walker),
                "target": target,
                "condition": _source_text(cond, source_text),
                "then": then_text,
                "else": else_text,
                "then_value": then_value,
                "else_value": else_value,
            })
        for child in node.get("inner", []) or []:
            walk(child, node)

    for child in func_node.get("inner", []) or []:
        walk(child, None)
    return out


def _extract_assignments(func_node: dict, walker: _LineWalker, source_text: str,
                         compiler: str, args: list[str], includes: list[str],
                         workdir: Path, backends, source_file: str) -> list[dict]:
    """Collect assignments to plain local variables into the ``assignments`` fact.

    Each entry: {line, var, op, value (src text), value_int (int|null),
    path: [{if_line, arm}, ...] with innermost last}. Only plain local-variable assignments are
    reported; the path records the enclosing if/else branches from outermost to innermost."""
    out: list[dict] = []
    path: list[dict] = []
    ASSIGN_OPS = {"=", "|=", "&=", "^=", "<<=", ">>=", "+=", "-=", "*=", "/=", "%="}

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
        if kind in ("BinaryOperator", "CompoundAssignOperator"):
            opcode = node.get("opcode")
            if opcode in ASSIGN_OPS:
                inner = node.get("inner") or []
                lhs = inner[0] if len(inner) >= 1 else None
                rhs = inner[1] if len(inner) >= 2 else None
                if _is_local_var_ref(lhs):
                    ref = lhs.get("referencedDecl", {})
                    out.append({
                        "line": _node_line(node, walker),
                        "var": ref.get("name") if isinstance(ref, dict) else None,
                        "op": opcode,
                        "value": _source_text(rhs, source_text),
                        "value_int": _int_literal_value(rhs),
                        "path": [dict(p) for p in path],
                    })
                    if rhs:
                        walk(rhs)
                    return
        if kind == "VarDecl":
            # A local variable with an initializer (``int p = -1;``) is its first assignment.
            init = node.get("init")
            if init and node.get("inner"):
                init_node = node["inner"][0]
                ref = node.get("referencedDecl")
                var_name = ref.get("name") if isinstance(ref, dict) else node.get("name")
                value_int = _int_literal_value(init_node)
                if value_int is None:
                    # Evaluate the initializer expression (e.g. ``-1``, ``IO_CC ^ 1``) with c_eval.
                    init_text = _source_text(init_node, source_text)
                    if init_text:
                        value_int = _eval_text(init_text, compiler, args, [], workdir,
                                               backends, source_file)
                out.append({
                    "line": _node_line(node, walker),
                    "var": var_name,
                    "op": "=",
                    "value": _source_text(init_node, source_text),
                    "value_int": value_int,
                    "path": [dict(p) for p in path],
                })
                return
        for child in node.get("inner", []) or []:
            walk(child)

    for child in func_node.get("inner", []) or []:
        walk(child)
    return out


def _extract_increments(func_node: dict, walker: _LineWalker, source_text: str) -> list[dict]:
    """Collect ++/-- operators into the ``increments`` fact.

    Each entry: {line, var, op ("++"|"--"), postfix: bool, in_condition_of: if line|null}. A
    post-increment in a condition reports the line of the IfStmt whose condition contains it.
    Postfix vs prefix is decided from the source text order (``n++`` vs ``--n``)."""
    out: list[dict] = []
    cond_if: list[int | None] = []

    def walk(node: dict) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        if kind == "IfStmt":
            inner = node.get("inner") or []
            cond_node = inner[0] if len(inner) >= 1 else None
            if cond_node:
                cond_if.append(_node_line(node, walker))
                walk(cond_node)
                cond_if.pop()
            then_node = inner[1] if len(inner) >= 2 else None
            else_node = inner[2] if len(inner) >= 3 else None
            if then_node:
                walk(then_node)
            if else_node:
                walk(else_node)
            return
        if kind == "UnaryOperator" and node.get("opcode") in ("++", "--"):
            operand = (node.get("inner") or [None])[0]
            var = None
            if isinstance(operand, dict):
                ref = operand.get("referencedDecl")
                if isinstance(ref, dict):
                    var = ref.get("name")
            postfix = bool(node.get("isPostfix"))
            out.append({
                "line": _node_line(node, walker),
                "var": var,
                "op": node["opcode"],
                "postfix": postfix,
                "in_condition_of": cond_if[-1] if cond_if else None,
            })
            return
        for child in node.get("inner", []) or []:
            walk(child)

    for child in func_node.get("inner", []) or []:
        walk(child)
    return out


# --------------------------------------------------------------------------- #
# value evaluation (c_eval back end) for constant operands
# --------------------------------------------------------------------------- #

def _eval_macro_operands(func_node: dict, compiler: str, args: list[str],
                         includes: list[str], workdir: Path, backends) -> None:
    """Evaluate constant IntegerLiteral operands whose source text is a macro (best effort).

    The primary facts report source text; numeric values are taken directly from the AST for plain
    integer literals. This helper evaluates any integer literal whose source text is not a plain
    number (e.g. a macro like ``IO_CC``) via c_eval's ``backends.evaluate`` and stores it under the
    node's ``_value`` key. Results are not part of the required JSON but keep the door open."""
    if backends is None:
        return

    def collect(node: dict, acc: list[dict]) -> None:
        if isinstance(node, dict):
            if node.get("kind") == "IntegerLiteral":
                text = _int_literal_text(node)
                if text is not None and not text.lstrip("-").isdigit():
                    acc.append((node, text))
            for child in node.get("inner", []) or []:
                collect(child, acc)

    acc: list[dict] = []
    collect(func_node, acc)
    if not acc:
        return
    exprs = [text for _, text in acc]
    try:
        values = backends.clang.evaluate(compiler, " ".join(args), exprs, includes, workdir)
        for (node, _text), value in zip(acc, values):
            node["_eval_value"] = value
    except Exception:
        pass


def _int_literal_text(node: dict) -> str | None:
    value = node.get("value")
    if isinstance(value, dict) and "value" in value:
        return str(value["value"])
    if isinstance(value, str):
        return value
    return None


def _eval_text(text: str | None, compiler: str, args: list[str], includes: list[str],
               workdir: Path, backends, source_file: str | None = None) -> int | None:
    """Evaluate a source-text operand (a macro like ``GPTIMER_CCACT_01_LACT_CCP_LOW``) with c_eval.

    Returns the computed int, or None when the text is empty, not a single token, or fails to
    evaluate (a non-constant expression is not a constant value). The source file is spliced into the
    probe so the build's own macro definitions are visible."""
    if not text:
        return None
    text = text.strip()
    # Only evaluate a single-token operand (an identifier or number); skip anything with spaces
    # (a full expression) -- those are not constant macro operands.
    if " " in text or "(" in text or ")" in text:
        return None
    incs = list(includes)
    if source_file:
        incs.append(source_file)
    try:
        values = backends.clang.evaluate(compiler, " ".join(args), [text], incs, workdir)
        return values[0]
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# JSON output
# --------------------------------------------------------------------------- #

def emit(data: dict) -> None:
    print(json.dumps(data, indent=1))


# --------------------------------------------------------------------------- #
# front end
# --------------------------------------------------------------------------- #

def build_facts(project_facts, platforms, backends, project: Path, build_name: str,
                source_file: str, function: str) -> dict:
    """Run the compiler AST dump for one function and extract all facts."""
    build_dir, _facts = resolve_build(project_facts, project, build_name)
    comp, flgs = project_facts.compile_command(project, build_name, source=source_file)
    if not comp:
        print(f"{ASK_USER}: could not determine a compile command for {build_name}",
              file=sys.stderr)
        sys.exit(EXIT_ASK_USER)
    # Detect the compiler family; AST facts are clang-only in v1.
    try:
        platform = platforms.for_compiler(comp)
        family = platform["family_kind"]
    except KeyError:
        family = "unknown"
    if family != "clang":
        print(f"AST facts not available for {family} yet", file=sys.stderr)
        sys.exit(EXIT_COMPILER_ERROR)
    source_path = str(project / source_file)
    ast_text = run_ast_dump(comp, flgs, source_path, function, build_dir)
    decls = _parse_concatenated_json(ast_text)

    # Choose the function *defined* in the source file (not a header declaration with the same name).
    func_node = None
    for d in decls:
        if d.get("kind") != "FunctionDecl" or d.get("name") != function:
            continue
        loc = d.get("loc", {})
        if loc.get("file") and Path(loc["file"]).name == Path(source_path).name:
            func_node = d
            break
    if func_node is None:
        for d in decls:
            if d.get("kind") == "FunctionDecl" and d.get("name") == function:
                func_node = d
                break
    if func_node is None:
        print(f"{ASK_USER}: function {function!r} not found in {source_file}", file=sys.stderr)
        sys.exit(EXIT_COMPILER_ERROR)

    source_path_resolved = Path(source_path).expanduser()
    source_text = source_path_resolved.read_text(encoding="utf-8", errors="replace")
    walker = _LineWalker(_make_offset_to_line(source_text))

    end_rng = func_node.get("range", {}).get("end") or {}
    func_first = _node_line(func_node, walker)
    func_last = end_rng.get("line") or func_first

    # Evaluate any macro-valued integer operands (best effort; results are not in the required JSON).
    _eval_macro_operands(func_node, comp, flgs, [], build_dir, backends)

    return {
        "function": {"name": function, "first_line": func_first, "last_line": func_last},
        "ifs": _extract_ifs(func_node, walker, source_text),
        "conditionals": _extract_conditionals(func_node, walker, source_text, comp, flgs, [],
                                              build_dir, backends, source_file),
        "assignments": _extract_assignments(func_node, walker, source_text, comp, flgs, [],
                                             build_dir, backends, source_file),
        "increments": _extract_increments(func_node, walker, source_text),
    }


def main(argv=None) -> int:
    project_facts = _load_project_facts()
    platforms = _load_platforms()
    backends = _load_backends()

    tokens = list(argv if argv is not None else sys.argv[1:])
    project_dir = None
    build_name = None
    source_file = None
    function = None
    as_json = False

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--build":
            build_name = tokens[i + 1]; i += 2; continue
        if tok.startswith("--build="):
            build_name = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--source":
            source_file = tokens[i + 1]; i += 2; continue
        if tok.startswith("--source="):
            source_file = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--function":
            function = tokens[i + 1]; i += 2; continue
        if tok.startswith("--function="):
            function = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--json":
            as_json = True; i += 1; continue
        if tok.startswith("--"):
            i += 1; continue
        # Bare tokens: first is the project dir, next is the source file.
        if project_dir is None:
            project_dir = tok
        elif source_file is None:
            source_file = tok
        i += 1

    if project_dir is None or build_name is None or source_file is None or function is None:
        print("usage: c_facts.py PROJECT --build B FILE --function NAME [--json]", file=sys.stderr)
        return EXIT_ASK_USER

    try:
        project = Path(project_dir).expanduser().resolve()
        data = build_facts(project_facts, platforms, backends, project, build_name,
                           source_file, function)
    except EvalError as error:
        sys.stderr.write(str(error).rstrip() + "\n")
        return EXIT_COMPILER_ERROR
    except SystemExit:
        raise
    except Exception as error:
        print(f"{ASK_USER}: {error}", file=sys.stderr)
        return EXIT_COMPILER_ERROR
    if as_json:
        emit(data)
    else:
        print(json.dumps(data, indent=1))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
