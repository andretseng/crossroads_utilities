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
from datetime import datetime
import json
from pathlib import Path
from pathlib import PureWindowsPath
import platform
import re
import shlex
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdk_inventory import DEFAULT_ROOT, TOOL_FOLDER, inventory, starts_with, version_tuple  # noqa: E402

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
                       "device": tags.get("DEVICE_CONFIGURATION_ID"), "core": tags.get("DEVICE_CORE_ID"),
                       "defines": defines, "undefines": undefines})
    return builds


# ---------- subdir_rules.mk files selected by <BUILD>/makefile ----------

def recipe_makefiles(build_dir):
    """Rule files named by makefile; rglob only when makefile names none."""
    makefile = build_dir / "makefile"
    declared, selected = False, []
    if makefile.is_file():
        for row in makefile.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^\s*-include\s+(.+)$", row)
            if not match:
                continue
            try:
                names = shlex.split(match.group(1), comments=True)
            except ValueError:
                names = match.group(1).split()
            for name in names:
                if name.replace("\\", "/").endswith("subdir_rules.mk"):
                    declared = True
                    path = Path(name)
                    path = path if path.is_absolute() else build_dir / path
                    if path.is_file():
                        selected.append(path.resolve())
    if declared:
        return sorted(dict.fromkeys(selected)), "makefile includes"
    return sorted(build_dir.rglob("subdir_rules.mk")), "rglob fallback"


def recipes(makefiles):
    """[(source, tokens, makefile, line)] for every compile recipe, and the SysConfig recipe (or None)."""
    compiles, sysconfig = [], None
    for makefile in makefiles:
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
            tool = re.split(r"[\\/]", tokens[0])[-1]
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


def options(tokens, *names):
    values = []
    for index, token in enumerate(tokens):
        if token in names and index + 1 < len(tokens):
            values.append(tokens[index + 1])
        else:
            for name in names:
                if token.startswith(name + "="):
                    values.append(token.split("=", 1)[1])
    return values


def sdk_home(path):
    """The nearest folder at or above path holding SysConfig product metadata."""
    path = Path(path)
    for folder in [path] + list(path.parents):
        if any((folder / ".metadata" / name).is_file() for name in ("product.json", "sdk.json")):
            return folder
    return None


def sdk_item(path, installed, product_json=None):
    """Installed SDK/component which most specifically contains path, or a metadata fallback."""
    raw_path = str(path)
    windows_path = bool(re.match(r"^[A-Za-z]:[\\/]", raw_path))
    path = Path(path)
    candidates = []
    for item in installed["sdks"]:
        if product_json and item.get("product_json") == str(product_json):
            return dict(item, exists=Path(item["path"]).is_dir())
        try:
            path.relative_to(Path(item["path"]))
        except ValueError:
            continue
        candidates.append(item)
    if candidates:
        item = max(candidates, key=lambda item: len(Path(item["path"]).parts))
        return dict(item, exists=Path(item["path"]).is_dir())
    home = sdk_home(path)
    if not home and product_json:
        if windows_path:
            product_path = PureWindowsPath(str(product_json))
            home = product_path.parent.parent.as_posix() if product_path.parent.name == ".metadata" else None
        else:
            product_path = Path(product_json)
            home = product_path.parent.parent if product_path.parent.name == ".metadata" else None
    if not home and windows_path:
        parts = PureWindowsPath(raw_path).parts
        ti_index = next((index for index, part in enumerate(parts) if part.lower() == "ti"), None)
        if ti_index is not None and ti_index + 1 < len(parts) and not TOOL_FOLDER.match(parts[ti_index + 1]):
            home = PureWindowsPath(*parts[:ti_index + 2]).as_posix()
    if not home and path.is_absolute():
        try:
            first = path.relative_to(DEFAULT_ROOT).parts[0]
            if not TOOL_FOLDER.match(first):
                home = DEFAULT_ROOT / first
        except (ValueError, IndexError):
            pass
    if not home:
        return None
    home_path = Path(home)
    return {"path": str(home), "name": None, "version": None,
            "folder_version": ".".join(map(str, version_tuple(home))),
            "product_json": str(product_json) if product_json else None, "exists": home_path.is_dir()}


def sdk_version_tuples(sdk):
    return [version_tuple(sdk.get(key)) for key in ("version", "folder_version") if sdk.get(key)]


def compiler_family(path):
    match = re.search(r"ti[-_]cgt[-_]([a-z0-9_]+?)[-_](?=\d+(?:\.\d+)+)", str(path), re.I)
    if match:
        return re.sub(r"[-_]", "", match.group(1)).lower()
    tool = re.split(r"[\\/]", str(path))[-1].lower().removesuffix(".exe")
    return {"tiarmclang": "armllvm", "cl2000": "c2000", "cl7x": "c7000",
            "c29clang": "c29", "cl6x": "c6000", "clpru": "pru"}.get(tool, tool)


def classify_path(path, host_os):
    """Return (native/foreign/unknown, source OS) without touching the path."""
    value = str(path)
    host = "macOS" if host_os == "Darwin" else host_os
    windows = bool(re.match(r"^[A-Za-z]:[\\/]", value)) or value.lower().endswith((".exe", ".bat"))
    posix = value.startswith("/")
    if host in ("Linux", "macOS"):
        if windows:
            return "foreign", "Windows"
        return ("native", host) if posix else ("unknown", None)
    if host == "Windows":
        if posix:
            source = "macOS" if value.startswith(("/Users/", "/Applications/")) else "Linux"
            return "foreign", source
        return ("native", "Windows") if windows else ("unknown", None)
    return "unknown", None


def _equivalent(kind, name, versions, installed):
    wanted = next((version for version in versions if version), ())
    candidates = []
    if kind == "compiler":
        candidates = [item for item in installed["compilers"]
                      if item["name"] == name and version_tuple(item["version"]) == wanted]
    elif kind == "SysConfig":
        candidates = [item for item in installed["sysconfig"] if version_tuple(item["version"]) == wanted]
    else:
        candidates = [item for item in installed["sdks"] if wanted in sdk_version_tuples(item)]
    return sorted({item["path"] for item in candidates})[0] if candidates else None


def classify_recipe_paths(facts, installed, host_os):
    foreign, equivalents = [], []
    entities = []
    for item in facts["compilers"]:
        item["name"] = compiler_family(item["path"])
        entities.append(("compiler", item["name"], [version_tuple(item["version"])], item, item["path"]))
    if facts["sysconfig"]:
        item = facts["sysconfig"]
        entities.append(("SysConfig", "sysconfig", [version_tuple(item["version"])], item, item["tool"]))
    for item in facts["sdks"]:
        entities.append(("SDK", item.get("name") or "SDK", sdk_version_tuples(item), item, item["path"]))
    for kind, name, versions, item, path in entities:
        path_class, source_os = classify_path(path, host_os)
        item["path_class"], item["path_os"] = path_class, source_os
        if path_class != "foreign":
            continue
        foreign.append({"os": source_os, "path": path})
        version = next((value for value in versions if value), ())
        equivalents.append({"kind": kind, "name": name, "version": ".".join(map(str, version)),
                            "path": _equivalent(kind, name, versions, installed)})
    facts["foreign"] = list({(item["os"], item["path"]): item for item in foreign}.values())
    facts["local_equivalents"] = list({(item["kind"], item["name"], item["version"]): item
                                       for item in equivalents}.values())


def timestamp(path):
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


def staleness(project, build_dir, build_name, rule_files):
    generated = list(rule_files)
    if not generated and (build_dir / "makefile").is_file():
        generated = [build_dir / "makefile"]
    if not generated:
        return []
    built_at = max(path.stat().st_mtime for path in generated)
    inputs = [project / ".cproject"] + sorted(project.glob("*.syscfg"))
    return [f"{build_name} was built {timestamp(max(generated, key=lambda path: path.stat().st_mtime))}, "
            f"{path.name} changed {timestamp(path)}"
            for path in inputs if path.is_file() and path.stat().st_mtime > built_at]


def _resolve_file(name, search_dirs, project, build_dir):
    candidate = Path(name)
    choices = [candidate] if candidate.is_absolute() else [build_dir / candidate, project / candidate]
    choices += [folder / candidate.name for folder in search_dirs]
    for path in choices:
        if path.is_file():
            return path.resolve()
    if not candidate.is_absolute() and "$" not in name:
        try:
            return next(path.resolve() for path in project.rglob(candidate.name) if path.is_file())
        except StopIteration:
            pass
    return None


def _file_origin(path, project, sdks):
    if path is None:
        return "unresolved"
    try:
        path.relative_to(project)
        return "project"
    except ValueError:
        pass
    for sdk in sdks:
        try:
            path.relative_to(Path(sdk["path"]))
            return "SDK"
        except ValueError:
            continue
    return "other"


def linked_inputs(project, build_dir, maps, sdks):
    makefile = build_dir / "makefile"
    if not makefile.is_file():
        return {"source": None, "libraries": [], "linker_scripts": [], "search_paths": []}
    text = makefile.read_text(encoding="utf-8", errors="replace")
    logical = re.sub(r"\\\r?\n", " ", text)
    paths = re.findall(r"(?:-Wl,)?-i(?:=)?\"([^\"]+)\"", logical)
    paths += re.findall(r"--search_path(?:=|\s+)\"?([^\"\s]+)", logical)
    search_dirs = [Path(value) if Path(value).is_absolute() else build_dir / value
                   for value in dict.fromkeys(paths) if "$" not in value]
    requested = re.findall(r"(?<![A-Za-z0-9_./])(?:-Wl,)?-l([^\s\\]+)", logical)
    requested += re.findall(r"(?<![-\w./])((?!-)[^\s\"']+\.(?:a|lib))(?=\s|$)", logical, re.I)
    map_archives = []
    for name in maps:
        map_path = build_dir / name
        if map_path.is_file():
            map_text = map_path.read_text(encoding="utf-8", errors="replace")
            map_archives += re.findall(r"(?<![A-Za-z0-9_.+-])([A-Za-z0-9_+.-]+\.(?:a|lib))(?=\s*:|\()",
                                       map_text, re.I)
    source = "map" if maps else "linker recipe"
    names = sorted(dict.fromkeys(map_archives if maps else requested))
    libraries = []
    for name in names:
        path = _resolve_file(name, search_dirs, project, build_dir)
        libraries.append({"name": name, "path": str(path) if path else None,
                          "origin": _file_origin(path, project, sdks), "source": source})
    scripts = sorted(dict.fromkeys(re.findall(r"[\"']?([^\s\"']+\.cmd)[\"']?", logical, re.I)))
    linker_scripts = []
    for name in scripts:
        path = _resolve_file(name, search_dirs, project, build_dir)
        linker_scripts.append({"name": name, "path": str(path) if path else None,
                               "origin": _file_origin(path, project, sdks)})
    return {"source": source, "libraries": libraries, "linker_scripts": linker_scripts,
            "search_paths": [str(path) for path in search_dirs]}


CORE_TOKEN = re.compile(r"(?:wkup-)?(?:r\d+fss|c\d+ss|m\d+fss)\d+-\d+", re.I)
COMPILER_SUFFIX = re.compile(r"_(?:ti[-_][a-z0-9-]+|ticlang)$", re.I)


def related_projects(project):
    siblings = sorted(path for path in project.parent.iterdir() if path.is_dir() and (path / ".cproject").is_file())
    names = [path.name for path in siblings]
    prefixes = [name.rsplit("_", 1)[0] for name in names if name.lower().endswith(("_system", "_multi"))]
    prefix = next((item for item in prefixes if project.name == item or project.name.startswith(item + "_")), None)
    if prefix:
        related = [path for path in siblings if path.name == prefix or path.name.startswith(prefix + "_")]
    else:
        key = COMPILER_SUFFIX.sub("", CORE_TOKEN.sub("<core>", project.name))
        related = [path for path in siblings
                   if COMPILER_SUFFIX.sub("", CORE_TOKEN.sub("<core>", path.name)) == key]
    if len(related) < 2:
        return []
    rows = []
    for path in related:
        entries = cproject_builds(path)
        rows.append({"project": str(path), "devices": sorted({item["device"] for item in entries if item["device"]}),
                     "cores": sorted({item["core"] for item in entries if item["core"]})})
    return rows


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

def build_facts(project, entry, headers, installed, host_os=None):
    build_dir = project / entry["name"]
    facts = {"name": entry["name"], "cproject": entry, "built": (build_dir / "makefile").is_file(),
             "maps": sorted(path.name for path in build_dir.glob("*.map")) if build_dir.is_dir() else [],
             "compilers": [], "sdks": [], "files": [], "sysconfig": None, "libraries": None,
             "rule_selection": {"mode": "not built", "files": []},
             "foreign": [], "local_equivalents": [], "stale": [], "disagree": [], "missing": []}
    rule_files, selection_mode = recipe_makefiles(build_dir) if facts["built"] else ([], "not built")
    facts["rule_selection"] = {
        "mode": selection_mode,
        "files": [path.relative_to(project).as_posix() for path in rule_files],
    }
    compiles, sysconfig = recipes(rule_files)
    sdk_paths, compilers = {}, {}
    for source, tokens, makefile, number in compiles:
        defines, undefines, includes = symbols(tokens)
        facts["files"].append({"source": source, "defines": defines, "undefines": undefines,
                               "recipe": f"{makefile.relative_to(project).as_posix()}:{number}"})
        compilers.setdefault(tokens[0], None)
        for include in includes:
            item = sdk_item(include, installed) if include.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", include) else None
            if item:
                sdk_paths.setdefault(item["path"], item)
    facts["compilers"] = [{"path": path, "version": ".".join(map(str, version_tuple(path))),
                           "exists": Path(path).is_file()} for path in compilers]
    facts["sdks"] = list(sdk_paths.values())
    if sysconfig:
        tokens, makefile, number = sysconfig
        products = options(tokens, "-s", "--product")
        scripts = options(tokens, "--script")
        product = products[0] if products else None
        facts["sysconfig"] = {"tool": tokens[0], "version": ".".join(map(str, version_tuple(tokens[0]))),
                              "product_json": product, "product_jsons": products,
                              "script": scripts[0] if scripts else None, "scripts": scripts,
                              "device": option(tokens, "-d", "--device"), "compiler": option(tokens, "--compiler"),
                              "exists": Path(tokens[0]).is_file(), "recipe": f"{makefile.relative_to(project).as_posix()}:{number}",
                              "regenerate": shlex.join(tokens[:tokens.index("-o") + 1] + [f"/tmp/syscfg_{entry['name']}"] + tokens[tokens.index("-o") + 2:])
                              if "-o" in tokens else None}
        for product_path in products:
            item = sdk_item(product_path, installed, product_path)
            if item and item["path"] not in {sdk["path"] for sdk in facts["sdks"]}:
                facts["sdks"].append(item)
    if facts["built"]:
        facts["stale"] = staleness(project, build_dir, entry["name"], rule_files)
        facts["libraries"] = linked_inputs(project, build_dir, facts["maps"], facts["sdks"])
    classify_recipe_paths(facts, installed, host_os or platform.system())
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
            if not any(starts_with(version, want) for sdk in facts["sdks"] for version in sdk_version_tuples(sdk)):
                disagree.append(f"SDK: {header['file']} @cliArgs product {stated}, the build uses "
                                + ", ".join(sdk["path"] for sdk in facts["sdks"]))
    stated_sdks = {name: version for name, version in products.items() if name != "sysconfig"}
    for name, version in stated_sdks.items():
        if facts["sdks"] and not any(starts_with(actual, version_tuple(version))
                                     for sdk in facts["sdks"] for actual in sdk_version_tuples(sdk)):
            disagree.append(f"SDK: .cproject PRODUCTS says {name} {version}, the build uses "
                            + ", ".join(sdk["path"] for sdk in facts["sdks"]))
    codegen = version_tuple(entry["codegen"])
    for compiler in facts["compilers"]:
        if codegen and not starts_with(version_tuple(compiler["version"]), codegen):
            disagree.append(f"Compiler: .cproject codegen {entry['codegen']}, the build ran {compiler['path']}")
    for item in facts["compilers"] + ([used] if used else []):
        if not item["exists"] and item.get("path_class") != "foreign":
            missing.append(f"not on this machine: {item.get('path') or item.get('tool')}")
    for sdk in facts["sdks"]:
        if not sdk.get("exists", Path(sdk["path"]).is_dir()) and sdk.get("path_class") != "foreign":
            missing.append(f"not on this machine: {sdk['path']}")
    if not facts["built"]:
        tools = {"sysconfig": [item["version"] for item in installed["sysconfig"]],
                 "compiler": [(item["name"], item["version"]) for item in installed["compilers"]]}
        if stated_sysconfig and not any(starts_with(version_tuple(v), version_tuple(stated_sysconfig)) for v in tools["sysconfig"]):
            missing.append(f"SysConfig {stated_sysconfig} (from .cproject) is not installed")
        if codegen and not any(starts_with(version_tuple(v), codegen) and (not entry["family"] or n == entry["family"])
                               for n, v in tools["compiler"]):
            missing.append(f"compiler {entry['family'] or ''} {entry['codegen']} (from .cproject) is not installed")
        for name, version in stated_sdks.items():
            if not any(starts_with(actual, version_tuple(version))
                       for sdk in installed["sdks"] for actual in sdk_version_tuples(sdk)):
                missing.append(f"SDK {name} {version} (from .cproject) is not installed")


# ---------- output ----------

def common_and_extra(files, key):
    sets = [set(item[key]) for item in files]
    common = set.intersection(*sets) if sets else set()
    extra = [(item["source"], sorted(set(item[key]) - common)) for item in files if set(item[key]) - common]
    return sorted(common), extra


def render(project, headers, builds, related):
    out = [f"# Project facts: {project.name}", "", f"Project: {project}", ""]
    out.append("Read these instead of searching. Values come from the build's own files; comments and README are claims.")
    out += ["", "## .syscfg header (shared by all builds; the tool version is the one it was last saved with)"]
    for header in headers:
        out.append(f"- {header['file']}: @cliArgs {header.get('cliArgs', 'none')}; @versions {header.get('versions', 'none')}")
    if not headers:
        out.append("- no .syscfg in the project folder")
    out += ["", "## Related projects (facts from sibling .cproject files; no flash recommendation)"]
    out += [f"- {item['project']}: device {', '.join(item['devices']) or 'none'}; core {', '.join(item['cores']) or 'none'}"
            for item in related] or ["- none detected"]
    for facts in builds:
        entry = facts["cproject"]
        source = "compile commands" if facts["built"] else "from .cproject (not built: build it in CCS first for real facts)"
        out += ["", f"## Build {facts['name']} — {source}"]
        out.append(f"- built: {'yes' if facts['built'] else 'no'}; map: {', '.join(facts['maps']) or 'none'}")
        if facts["built"]:
            out.append(f"- subdir_rules selection: {facts['rule_selection']['mode']}; "
                       f"{len(facts['rule_selection']['files'])} file(s)")
        out += [f"- FOREIGN: {facts['name']} was generated on {item['os']} ({item['path']})"
                for item in facts["foreign"][:1]]
        for item in facts["local_equivalents"]:
            label = f"compiler {item['name']}" if item["kind"] == "compiler" else item["kind"]
            out.append(f"- local equivalent: {label} {item['version']} -> {item['path'] or 'none installed'}")
        out.append(f"- .cproject: PRODUCTS {entry['products'] or 'none'}; codegen {entry['codegen']} ({entry['family']}); device {entry['device']}")
        for compiler in facts["compilers"]:
            out.append(f"- compiler: {compiler['path']} (version {compiler['version']})")
        for sdk in facts["sdks"]:
            out.append(f"- SDK: {sdk['path']} (product {sdk.get('name')} {sdk.get('version')}; "
                       f"folder version {sdk.get('folder_version')}; installed {'yes' if sdk.get('exists', True) else 'no'})")
        if facts["sysconfig"]:
            info = facts["sysconfig"]
            out.append(f"- SysConfig: {info['tool']} (version {info['version']}); products {info['product_jsons']}; "
                       f"device {info['device']}; scripts {info['scripts']} ({info['recipe']})")
            if info["regenerate"]:
                out.append(f"- regenerate SysConfig output with the build's own tool: `{info['regenerate']}`")
        if facts["libraries"]:
            source = facts["libraries"]["source"]
            out.append(f"- linked libraries ({source}):")
            out += [f"  - {item['name']}: {item['path'] or 'unresolved'} ({item['origin']})"
                    for item in facts["libraries"]["libraries"]] or ["  - none"]
            out.append("- linker scripts:")
            out += [f"  - {item['name']}: {item['path'] or 'unresolved'} ({item['origin']})"
                    for item in facts["libraries"]["linker_scripts"]] or ["  - none"]
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
        out += [f"- STALE: {item}" for item in facts["stale"]]
        if facts["foreign"]:
            out.append(f"- ASK USER: rebuild {facts['name']} on this machine")
        if not facts["built"] or facts["stale"]:
            out.append(f"- ASK USER: rebuild {facts['name']} in CCS, then run project_facts again")
        out += [f"- DISAGREE: {item}" for item in facts["disagree"]]
        out += [f"- MISSING: {item}" for item in facts["missing"]]
    return "\n".join(out) + "\n"


def result_status(builds):
    return 3 if any(not facts["built"] or facts["stale"] or facts["foreign"] for facts in builds) else 0


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
    builds = [build_facts(project, entry, headers, installed, platform.system()) for entry in entries]
    related = related_projects(project)
    if args.json:
        print(json.dumps({"project": str(project), "syscfg": headers, "related_projects": related,
                          "builds": builds}, indent=1, default=str))
    else:
        sys.stdout.write(render(project, headers, builds, related))
    return result_status(builds)


if __name__ == "__main__":
    raise SystemExit(main())
