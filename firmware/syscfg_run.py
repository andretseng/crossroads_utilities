#!/usr/bin/env python3
"""Run SysConfig as a calculator using project_facts or explicit CLI inputs."""
import argparse
import filecmp
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_facts  # noqa: E402
from sdk_inventory import DEFAULT_ROOT, inventory  # noqa: E402


IGNORED_SUFFIXES = {".o", ".d", ".dot"}
PARAMETER_WORDS = ("BAUD", "IBRD", "FBRD", "PRESCAL", "DIV", "PERIOD", "LOAD")
DEFINE_RE = re.compile(r"^\s*#define\s+([A-Za-z_]\w*)\s+(.+?)\s*$")
STRUCT_RE = re.compile(r"\bstatic\s+const\s+([A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*)\s+([A-Za-z_]\w*)\s*=\s*\{")
FIELD_RE = re.compile(r"\.([A-Za-z_]\w*)\s*=\s*([^,\n}]+)")
PIN_RE = re.compile(r"Defines\s+for\s+([^:]+):.*?with\s+pinCMx\s+(\S+)\s+on\s+package\s+pin\s+(\S+)", re.I)


def _strip_comment(value):
    return re.split(r"\s*(?://|/\*)", value, maxsplit=1)[0].strip()


def _numeric(value):
    compact = value.strip()
    while compact.startswith("(") and compact.endswith(")"):
        compact = compact[1:-1].strip()
    return bool(re.fullmatch(r"[-+]?(?:0[xX][0-9A-Fa-f]+|\d+)(?:[uUlLfF]+)?", compact))


def _entry(label, value, path, line, kind, **extra):
    row = {"label": label, "value": value, "file": path.as_posix(), "line": line, "kind": kind}
    row.update(extra)
    return row


def parse_generated(output_dir):
    """Extract clocks, numeric parameters, struct fields and pin comments."""
    output_dir = Path(output_dir)
    clocks, parameters, pins = [], [], []
    for path in sorted(item for item in output_dir.rglob("*") if item.is_file() and item.suffix.lower() in {".c", ".h"}):
        relative = path.relative_to(output_dir)
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for number, line in enumerate(lines, 1):
            match = DEFINE_RE.match(line)
            if match:
                name, value = match.group(1), _strip_comment(match.group(2))
                if re.search(r"_FREQ(?:_HZ)?$", name):
                    clocks.append(_entry(name, value, relative, number, "clock"))
                if any(word in name for word in PARAMETER_WORDS) and _numeric(value):
                    parameters.append(_entry(name, value, relative, number, "define"))
            match = PIN_RE.search(line)
            if match:
                signal, pad, package_pin = (part.rstrip("*/") for part in match.groups())
                pins.append(_entry(signal, f"{pad} -> {package_pin}", relative, number, "pin",
                                   pad=pad, package_pin=package_pin))
        for match in STRUCT_RE.finditer(text):
            opening = text.find("{", match.start())
            depth, end = 0, opening
            while end < len(text):
                if text[end] == "{":
                    depth += 1
                elif text[end] == "}":
                    depth -= 1
                    if depth == 0:
                        end += 1
                        break
                end += 1
            body = text[opening + 1:end - 1]
            for field in FIELD_RE.finditer(body):
                value = _strip_comment(field.group(2)).strip()
                prefix = re.sub(r"/\*.*?\*/|//[^\n]*", "", body[:field.start()], flags=re.S)
                if prefix.count("{") != prefix.count("}") or value.startswith("{"):
                    continue
                line = text.count("\n", 0, opening + 1 + field.start()) + 1
                parameters.append(_entry(f"{match.group(2)}.{field.group(1)}", value, relative, line,
                                         "struct", type=match.group(1)))
    values = {item["label"]: item for item in clocks + parameters + pins}
    return {"clocks": clocks, "parameters": parameters, "pins": pins, "values": values}


def diff_facts(before, after):
    """Only values present in both outputs whose rendered value changed."""
    changed = []
    for label in sorted(set(before["values"]) & set(after["values"])):
        old, new = before["values"][label], after["values"][label]
        if old["value"] != new["value"]:
            changed.append({"label": label, "old": old["value"], "new": new["value"],
                            "file": new["file"], "line": new["line"]})
    return changed


def compare_generated(generated, reference):
    generated, reference = Path(generated), Path(reference) if reference else None
    if reference is None:
        return {"status": "not available (explicit mode)", "files": []}
    if not reference.is_dir():
        return {"status": f"reference directory missing: {reference}", "files": []}
    generated_files = {path.relative_to(generated): path for path in generated.rglob("*")
                       if path.is_file() and path.suffix.lower() not in IGNORED_SUFFIXES}
    reference_files = {path.relative_to(reference): path for path in reference.rglob("*")
                       if path.is_file() and path.suffix.lower() not in IGNORED_SUFFIXES}
    rows = []
    for name in sorted(set(generated_files) | set(reference_files)):
        if name not in reference_files:
            rows.append({"file": name.as_posix(), "difference": "generated only"})
        elif name not in generated_files:
            rows.append({"file": name.as_posix(), "difference": "reference only"})
        elif not filecmp.cmp(generated_files[name], reference_files[name], shallow=False):
            rows.append({"file": name.as_posix(), "difference": "content differs"})
    return {"status": "identical" if not rows else "different", "files": rows}


def diagnostics(output, exit_code=0):
    lines = output.splitlines()
    errors = [line for line in lines if re.search(r"\berror\b", line, re.I)]
    if exit_code and not errors:
        errors = lines
    return {"errors": errors,
            "warnings": [line for line in lines if re.search(r"\bwarning\b", line, re.I)]}


def run_command(tokens, workspace, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    try:
        completed = subprocess.run(tokens, cwd=workspace, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, check=False)
        code, output = completed.returncode, completed.stdout
    except OSError as error:
        code, output = 127, f"ERROR: {error}"
    elapsed = time.monotonic() - start
    return {"command": shlex.join(tokens), "exit_code": code, "elapsed_seconds": round(elapsed, 3),
            "output": output, **diagnostics(output, code)}


def _replace_output(tokens, output_dir):
    result = list(tokens)
    for index, token in enumerate(result[:-1]):
        if token in ("-o", "--output"):
            result[index + 1] = str(output_dir)
    return result


def _copy_scripts(tokens, scripts, workspace, assignments=()):
    workspace.mkdir(parents=True, exist_ok=True)
    result, replacements = list(tokens), {}
    for index, source in enumerate(scripts):
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(f"SysConfig script not found: {source}")
        target = workspace / f"script_{index}_{source_path.name}"
        shutil.copy2(source_path, target)
        if assignments and index == 0:
            with target.open("a", encoding="utf-8") as stream:
                stream.write("\n// syscfg_run.py what-if\n")
                for assignment in assignments:
                    stream.write(assignment.rstrip(";") + ";\n")
        replacements[str(source)] = str(target)
    for index, token in enumerate(result):
        if token in replacements:
            result[index] = replacements[token]
    return result


def _project_mode(project, build_name):
    project = Path(project).expanduser().resolve()
    entries = project_facts.cproject_builds(project)
    entry = next((item for item in entries if item["name"] == build_name), None)
    if entry is None:
        raise ValueError(f"unknown build {build_name!r}; builds: {[item['name'] for item in entries]}")
    installed = inventory([DEFAULT_ROOT])
    facts = project_facts.build_facts(project, entry, project_facts.syscfg_headers(project), installed)
    asks = []
    if facts["foreign"]:
        asks.append(f"rebuild {build_name} on this machine")
    if not facts["built"] or facts["stale"]:
        asks.append(f"rebuild {build_name} in CCS, then run project_facts again")
    if asks:
        return {"blocked": True, "asks": asks, "project_facts": facts, "project": project}
    if not facts["sysconfig"] or not facts["sysconfig"]["regenerate"]:
        raise ValueError(f"build {build_name!r} has no SysConfig command")
    return {"blocked": False, "mode": f"project build ({build_name})", "project": project,
            "reference": project / build_name / "syscfg", "tokens": shlex.split(facts["sysconfig"]["regenerate"]),
            "scripts": facts["sysconfig"]["scripts"], "project_facts": facts}


def _explicit_mode(args):
    if not args.sysconfig or not args.product or not args.script:
        raise ValueError("explicit mode requires --sysconfig, --product, and --script")
    tokens = [args.sysconfig]
    for product in args.product:
        tokens += ["-s", product]
    for index, script in enumerate(args.script):
        tokens += ["--script", script]
        if index < len(args.context):
            tokens += ["--context", args.context[index]]
    if args.device:
        tokens += ["-d", args.device]
    tokens += ["-o", "/tmp/syscfg_run_output"]
    if args.compiler:
        tokens += ["--compiler", args.compiler]
    return {"blocked": False, "mode": "explicit CLI", "project": None, "reference": None,
            "tokens": tokens, "scripts": args.script}


def calculate(mode, action, assignments):
    workspace = Path(tempfile.mkdtemp(prefix="syscfg_run.", dir="/tmp"))
    result = {"mode": mode["mode"], "workspace": str(workspace), "action": action,
              "meaning": "meaning of encoded fields: check the SDK header / TRM (T3)"}
    base_output = workspace / "base_output"
    base_tokens = _replace_output(mode["tokens"], base_output)
    base_tokens = _copy_scripts(base_tokens, mode["scripts"], workspace / "base_scripts")
    base_run = run_command(base_tokens, workspace, base_output)
    result["base"] = base_run
    if base_run["exit_code"] == 0 and not base_run["errors"]:
        result["base"]["facts"] = parse_generated(base_output)
        result["base"]["regenerate"] = compare_generated(base_output, mode["reference"])
    if action == "whatif" and base_run["exit_code"] == 0 and not base_run["errors"]:
        whatif_output = workspace / "whatif_output"
        whatif_tokens = _replace_output(mode["tokens"], whatif_output)
        whatif_tokens = _copy_scripts(whatif_tokens, mode["scripts"], workspace / "whatif_scripts", assignments)
        whatif_run = run_command(whatif_tokens, workspace, whatif_output)
        result["whatif"] = whatif_run
        if whatif_run["exit_code"] == 0 and not whatif_run["errors"]:
            result["whatif"]["facts"] = parse_generated(whatif_output)
            result["diff"] = diff_facts(result["base"]["facts"], result["whatif"]["facts"])
    return result


def _diagnostic_markdown(title, lines):
    if not lines:
        return [f"## {title}", "", "- none"]
    return [f"## {title}", "", "```text", *lines, "```"]


def _facts_markdown(facts, heading):
    out = [f"## {heading}", "", "### Clocks", ""]
    out += [f"- {item['label']} = {item['value']} ({item['file']}:{item['line']})" for item in facts["clocks"]] or ["- none"]
    out += ["", "### Computed peripheral parameters", ""]
    out += [f"- {item['label']} = {item['value']} ({item['file']}:{item['line']})" for item in facts["parameters"]] or ["- none"]
    out += ["", "### Pins", ""]
    out += [f"- {item['label']} -> {item['value']} ({item['file']}:{item['line']})" for item in facts["pins"]] or ["- none"]
    return out


def render(result):
    if result.get("blocked"):
        out = ["# SysConfig run", "", "- mode: project build", "- SysConfig was not run"]
        out += [f"- ASK USER: {ask}" for ask in result["asks"]]
        return "\n".join(out) + "\n"
    out = ["# SysConfig run", "", f"- mode: {result['mode']}", f"- workspace: {result['workspace']}"]
    for label in ("base", "whatif"):
        if label not in result:
            continue
        run = result[label]
        out += [f"- {label} command: `{run['command']}`", f"- {label} exit code: {run['exit_code']}",
                f"- {label} time: {run['elapsed_seconds']:.3f} s"]
        out += [""] + _diagnostic_markdown(f"{label.upper()} ERRORS", run["errors"])
        out += [""] + _diagnostic_markdown(f"{label.upper()} WARNINGS", run["warnings"])
        if "facts" in run:
            if label == "base":
                check = run["regenerate"]
                out += ["", "## Regenerate check", "", f"- {check['status']}"]
                out += [f"- {item['file']}: {item['difference']}" for item in check["files"]]
            out += [""] + _facts_markdown(run["facts"], f"{label.capitalize()} facts")
    if "diff" in result:
        out += ["", "## DIFF", ""]
        out += [f"- {item['label']}: {item['old']} -> {item['new']} ({item['file']}:{item['line']})"
                for item in result["diff"]] or ["- no parsed values changed"]
    out += ["", result["meaning"]]
    return "\n".join(out) + "\n"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?")
    parser.add_argument("action", nargs="?")
    parser.add_argument("assignments", nargs="*")
    parser.add_argument("--build")
    parser.add_argument("--sysconfig")
    parser.add_argument("--product", action="append", default=[])
    parser.add_argument("--script", action="append", default=[])
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--compiler")
    parser.add_argument("--device")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_intermixed_args(argv)
    if args.project in ("facts", "whatif"):
        shifted = [value for value in [args.action, *args.assignments] if value is not None]
        args.action, args.assignments, args.project = args.project, shifted, None
    if args.action not in ("facts", "whatif"):
        parser.error("action must be facts or whatif")
    if args.action == "whatif" and not args.assignments:
        parser.error("whatif requires at least one assignment")
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        mode = _project_mode(args.project, args.build) if args.project and args.build else _explicit_mode(args)
        if mode.get("blocked"):
            result, status = mode, 3
        else:
            result = calculate(mode, args.action, args.assignments)
            run = result.get("whatif", result["base"])
            status = run["exit_code"] or (1 if run["errors"] else 0)
    except (OSError, ValueError) as error:
        result, status = {"error": str(error)}, 2
    if args.json:
        print(json.dumps(result, indent=1, default=str))
    elif "error" in result:
        print(f"ERROR: {result['error']}", file=sys.stderr)
    else:
        sys.stdout.write(render(result))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
