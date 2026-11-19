#!/usr/bin/env python3
"""project_facts.py PROJECT_DIR [--build NAME] [--root DIR ...] [--json]

The facts of a CCS project, per build, read from the project's own files (no model):
which builds exist and whether each was built; the compiler, SDK and SysConfig each
build really uses (with versions and paths); the -D/-U symbols per source file; the
.syscfg header (@cliArgs, @versions); whether each tool is installed; every place
where the sources disagree; and the exact SysConfig command of the build.

Trust order: the build's compile commands (<BUILD>/**/subdir_rules.mk) > .cproject >
comments and README. A build that was never built has only .cproject facts, marked
"from .cproject (not built)".
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdk_inventory import DEFAULT_ROOT, inventory, starts_with, version_tuple  # noqa: E402

COMPILER = re.compile(r"^(tiarmclang|c29clang|cl2000|cl7x|cl6x|clpru|armcl|.*gcc|.*clang)(\.exe)?$", re.I)
FAMILY = {"TICLANG": "armllvm", "C2000": "c2000", "C7000": "c7000", "C6000": "c6000", "C29": "c29", "PRU": "pru"}


# ---------- .cproject ----------

def cproject_builds(project):
    path = project / ".cproject"
    if not path.is_file():
        return []
    builds = []
    for config in ET.parse(path).getroot().iter("configuration"):
        name = config.get("name")
        if not name or config.find(".//toolChain") is None:
            continue
        tags, defines, undefines, codegen = {}, [], [], None
        for option in config.iter("option"):
            kind = option.get("superClass") or ""
            values = [item.get("value") for item in option.iter("listOptionValue")]
            if kind.endswith("OPT_TAGS"):
                tags.update(value.split("=", 1) for value in values if value and "=" in value)
            elif kind.endswith("OPT_CODEGEN_VERSION"):
                codegen = option.get("value")
            elif re.search(r"compilerID\.DEFINE$", kind):
                defines += values
            elif re.search(r"compilerID\.UNDEFINE$", kind):
                undefines += values
        chain = config.find(".//toolChain").get("superClass") or ""
        family = next((value for key, value in FAMILY.items() if f"_{key}_" in chain or f".{key}_" in chain), None)
        products = dict(item.split(":", 1) for item in tags.get("PRODUCTS", "").split(";") if ":" in item)
        builds.append({"name": name, "products": products, "codegen": codegen, "family": family,
                       "device": tags.get("DEVICE_CONFIGURATION_ID"), "defines": defines, "undefines": undefines})
    return builds


# ---------- <BUILD>/**/subdir_rules.mk ----------

def recipes(build_dir):
    """[(source, tokens, makefile, line)] for every compile recipe, and the SysConfig recipe (or None)."""
    compiles, sysconfig = [], None
    for makefile in sorted(build_dir.rglob("subdir_rules.mk")):
        rows = makefile.read_text(encoding="utf-8", errors="replace").splitlines()
        source = None
        for number, row in enumerate(rows, 1):
            rule = re.match(r"^([^#\s][^:]*)\s*:\s*([^\s|]+)", row)
            if rule:
                source = rule.group(2); continue
            if not row.startswith(("\t", " ")):
                continue
            try:
                tokens = shlex.split(row.strip().lstrip("@"))
            except ValueError:
                continue
            if not tokens:
                continue
            tool = os.path.basename(tokens[0])
            if "sysconfig_cli" in tool and sysconfig is None:
                sysconfig = (tokens, makefile, number)
            elif COMPILER.match(tool) and source:
                compiles.append((source, tokens, makefile, number))
    return compiles, sysconfig


def symbols(tokens):
    defines, undefines, includes = [], [], []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        for prefixes, bucket in ((("-D", "--define="), defines), (("-U", "--undefine="), undefines),
                                 (("-I", "--include_path="), includes)):
            hit = next((prefix for prefix in prefixes if token.startswith(prefix)), None)
            if hit:
                value = token[len(hit):]
                if not value and index + 1 < len(tokens):
                    index += 1; value = tokens[index]
                bucket.append(value)
                break
        index += 1
    return defines, undefines, includes


def option(tokens, *names):
    for index, token in enumerate(tokens):
        if token in names and index + 1 < len(tokens):
            return tokens[index + 1]
        for name in names:
            if token.startswith(name + "="):
                return token.split("=", 1)[1]
    return None


def sdk_home(path):
    """The nearest folder at or above path holding .metadata/product.json, else the install folder under ~/ti."""
    path = Path(path)
    for folder in [path] + list(path.parents):
        if (folder / ".metadata" / "product.json").is_file():
            return folder
    return None


# ---------- .syscfg header ----------

def syscfg_headers(project):
    out = []
    for path in sorted(project.glob("*.syscfg")):
        text = path.read_text(encoding="utf-8", errors="replace")[:4000]
        header = {"file": path.name}
        for key in ("cliArgs", "v2CliArgs"):
            match = re.search(r"@%s\s+([^\r\n]+)" % key, text)
            if match:
                try:
                    tokens = shlex.split(match.group(1))
                except ValueError:
                    tokens = []
                header[key] = {tokens[i][2:]: tokens[i + 1] for i in range(len(tokens) - 1) if tokens[i].startswith("--")}
        match = re.search(r"@versions\s+(\{[^\r\n]*\})", text)
        if match:
            try:
                header["versions"] = json.loads(match.group(1))
            except ValueError:
                header["versions"] = match.group(1)
        out.append(header)
    return out


# ---------- one build ----------

def build_facts(project, entry, headers, installed):
    build_dir = project / entry["name"]
    facts = {"name": entry["name"], "cproject": entry, "built": (build_dir / "makefile").is_file(),
             "maps": sorted(path.name for path in build_dir.glob("*.map")) if build_dir.is_dir() else [],
             "compilers": [], "sdks": [], "files": [], "sysconfig": None, "disagree": [], "missing": []}
    compiles, sysconfig = recipes(build_dir) if build_dir.is_dir() else ([], None)
    sdk_paths, compilers = {}, {}
    for source, tokens, makefile, number in compiles:
        defines, undefines, includes = symbols(tokens)
        facts["files"].append({"source": source, "defines": defines, "undefines": undefines,
                               "recipe": f"{makefile.relative_to(project).as_posix()}:{number}"})
        compilers.setdefault(tokens[0], None)
        for include in includes:
            home = sdk_home(include) if include.startswith("/") else None
            if home:
                sdk_paths.setdefault(str(home), None)
    facts["compilers"] = [{"path": path, "version": ".".join(map(str, version_tuple(path))),
                           "exists": Path(path).is_file()} for path in compilers]
    facts["sdks"] = [{"path": path, "folder_version": ".".join(map(str, version_tuple(Path(path).name)))} for path in sdk_paths]
    if sysconfig:
        tokens, makefile, number = sysconfig
        product = option(tokens, "-s", "--product")
        facts["sysconfig"] = {"tool": tokens[0], "version": ".".join(map(str, version_tuple(Path(tokens[0]).parent.name))),
                              "product_json": product, "script": option(tokens, "--script"),
                              "device": option(tokens, "-d", "--device"), "compiler": option(tokens, "--compiler"),
                              "exists": Path(tokens[0]).is_file(), "recipe": f"{makefile.relative_to(project).as_posix()}:{number}",
                              "regenerate": shlex.join(tokens[:tokens.index("-o") + 1] + [f"/tmp/syscfg_{entry['name']}"] + tokens[tokens.index("-o") + 2:])
                              if "-o" in tokens else None}
        if product and Path(product).parent.parent not in [Path(item["path"]) for item in facts["sdks"]]:
            facts["sdks"].append({"path": str(Path(product).parent.parent),
                                  "folder_version": ".".join(map(str, version_tuple(Path(product).parent.parent.name)))})
    check(facts, entry, headers, installed)
    return facts


def check(facts, entry, headers, installed):
    disagree, missing = facts["disagree"], facts["missing"]
    products = entry["products"]
    stated_sysconfig = products.get("sysconfig")
    used = facts["sysconfig"]
    if used and stated_sysconfig and not starts_with(version_tuple(used["version"]), version_tuple(stated_sysconfig)):
        disagree.append(f"SysConfig: .cproject PRODUCTS says {stated_sysconfig}, the build ran {used['version']} ({used['recipe']})")
    for header in headers:
        tool = (header.get("versions") or {}).get("tool") if isinstance(header.get("versions"), dict) else None
        if used and tool and not starts_with(version_tuple(tool), version_tuple(used["version"])):
            disagree.append(f"SysConfig: {header['file']} @versions tool {tool} (last saved with), the build ran {used['version']}")
        stated = (header.get("cliArgs") or {}).get("product", "")
        if "@" in stated and facts["sdks"]:
            want = version_tuple(stated.rsplit("@", 1)[1])
            if not any(starts_with(version_tuple(sdk["folder_version"]), want) for sdk in facts["sdks"]):
                disagree.append(f"SDK: {header['file']} @cliArgs product {stated}, the build uses "
                                + ", ".join(sdk["path"] for sdk in facts["sdks"]))
    sdk_versions = {name: version for name, version in products.items() if name != "sysconfig"}
    for name, version in sdk_versions.items():
        if facts["sdks"] and not any(starts_with(version_tuple(sdk["folder_version"]), version_tuple(version))
                                     or starts_with(version_tuple(version), version_tuple(sdk["folder_version"]))
                                     for sdk in facts["sdks"]):
            disagree.append(f"SDK: .cproject PRODUCTS says {name} {version}, the build uses "
                            + ", ".join(sdk["path"] for sdk in facts["sdks"]))
    codegen = version_tuple(entry["codegen"])
    for compiler in facts["compilers"]:
        if codegen and not starts_with(version_tuple(compiler["version"]), codegen):
            disagree.append(f"Compiler: .cproject codegen {entry['codegen']}, the build ran {compiler['path']}")
    for item in facts["compilers"] + ([used] if used else []):
        if not item["exists"]:
            missing.append(f"not on this machine: {item.get('path') or item.get('tool')}")
    if not facts["built"]:
        tools = {"sysconfig": [item["version"] for item in installed["sysconfig"]],
                 "compiler": [(item["name"], item["version"]) for item in installed["compilers"]]}
        if stated_sysconfig and not any(starts_with(version_tuple(v), version_tuple(stated_sysconfig)) for v in tools["sysconfig"]):
            missing.append(f"SysConfig {stated_sysconfig} (from .cproject) is not installed")
        if codegen and not any(starts_with(version_tuple(v), codegen) and (not entry["family"] or n == entry["family"])
                               for n, v in tools["compiler"]):
            missing.append(f"compiler {entry['family'] or ''} {entry['codegen']} (from .cproject) is not installed")
        for name, version in sdk_versions.items():
            if not any(starts_with(version_tuple(sdk.get("folder_version")), version_tuple(version))
                       or starts_with(version_tuple(sdk.get("version")), version_tuple(version)) for sdk in installed["sdks"]):
                missing.append(f"SDK {name} {version} (from .cproject) is not installed")


# ---------- output ----------

def common_and_extra(files, key):
    sets = [set(item[key]) for item in files]
    common = set.intersection(*sets) if sets else set()
    extra = [(item["source"], sorted(set(item[key]) - common)) for item in files if set(item[key]) - common]
    return sorted(common), extra


def render(project, headers, builds):
    out = [f"# Project facts: {project.name}", "", f"Project: {project}", ""]
    out.append("Read these instead of searching. Values come from the build's own files; comments and README are claims.")
    out += ["", "## .syscfg header (shared by all builds; the tool version is the one it was last saved with)"]
    for header in headers:
        out.append(f"- {header['file']}: @cliArgs {header.get('cliArgs', 'none')}; @versions {header.get('versions', 'none')}")
    if not headers:
        out.append("- no .syscfg in the project folder")
    for facts in builds:
        entry = facts["cproject"]
        source = "compile commands" if facts["built"] else "from .cproject (not built: build it in CCS first for real facts)"
        out += ["", f"## Build {facts['name']} — {source}"]
        out.append(f"- built: {'yes' if facts['built'] else 'no'}; map: {', '.join(facts['maps']) or 'none'}")
        out.append(f"- .cproject: PRODUCTS {entry['products'] or 'none'}; codegen {entry['codegen']} ({entry['family']}); device {entry['device']}")
        for compiler in facts["compilers"]:
            out.append(f"- compiler: {compiler['path']} (version {compiler['version']})")
        for sdk in facts["sdks"]:
            out.append(f"- SDK: {sdk['path']} (folder version {sdk['folder_version']})")
        if facts["sysconfig"]:
            info = facts["sysconfig"]
            out.append(f"- SysConfig: {info['tool']} (version {info['version']}); product.json {info['product_json']}; "
                       f"device {info['device']}; script {info['script']} ({info['recipe']})")
            if info["regenerate"]:
                out.append(f"- regenerate SysConfig output with the build's own tool: `{info['regenerate']}`")
        if facts["built"]:
            common, extra = common_and_extra(facts["files"], "defines")
            out.append(f"- -D on every file ({len(facts['files'])} files): {', '.join(common) or 'none'}")
            out += [f"- -D only on {source}: {', '.join(values)}" for source, values in extra]
            common_u, extra_u = common_and_extra(facts["files"], "undefines")
            if common_u or extra_u:
                out.append(f"- -U on every file: {', '.join(common_u) or 'none'}")
                out += [f"- -U only on {source}: {', '.join(values)}" for source, values in extra_u]
        else:
            out.append(f"- -D (from .cproject, unresolved ${{...}} kept as is): {', '.join(entry['defines']) or 'none'}")
            if entry["undefines"]:
                out.append(f"- -U (from .cproject): {', '.join(entry['undefines'])}")
        out += [f"- DISAGREE: {item}" for item in facts["disagree"]]
        out += [f"- MISSING: {item}" for item in facts["missing"]]
    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project", type=Path)
    parser.add_argument("--build", action="append")
    parser.add_argument("--root", type=Path, action="append")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    project = args.project.expanduser().resolve()
    entries = cproject_builds(project)
    if not entries:
        parser.error(f"no .cproject with builds in {project}")
    if args.build:
        unknown = set(args.build) - {entry["name"] for entry in entries}
        if unknown:
            parser.error(f"unknown build(s) {sorted(unknown)}; builds: {[entry['name'] for entry in entries]}")
        entries = [entry for entry in entries if entry["name"] in args.build]
    installed = inventory([root.expanduser().resolve() for root in (args.root or [DEFAULT_ROOT])])
    headers = syscfg_headers(project)
    builds = [build_facts(project, entry, headers, installed) for entry in entries]
    if args.json:
        print(json.dumps({"project": str(project), "syscfg": headers, "builds": builds}, indent=1, default=str))
    else:
        sys.stdout.write(render(project, headers, builds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
