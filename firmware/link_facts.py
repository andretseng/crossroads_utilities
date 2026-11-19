#!/usr/bin/env python3
"""link_facts.py — what the linker really did (T6).

Reads a linker's output and reports, without guessing:
  * memory regions (origin/length/used/unused)
  * symbols (address, section, load/run, source object/archive member, linked yes/no)
  * sections (load/run address, size, input pieces)
  * overlapping sections / regions, and regions used over 100%

Two modes:
  * project mode:  link_facts.py PROJECT_DIR --build NAME <command>
  * explicit mode: link_facts.py --xml FILE [--map FILE] --unit byte|word16 <command>

Standard library only, Python 3.10+.
"""
import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, "/home/andre/utilities/firmware")
import project_facts  # noqa: E402  (imported for build selection / staleness / FOREIGN)

ASK_USER = "ASK USER"
EXIT_STALE = 3


# --------------------------------------------------------------------------- #
# Address parsing
# --------------------------------------------------------------------------- #

def parse_addr(text):
    """Parse a hex address/size from the XML (0x...) or the text map (8 hex digits)."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    if text.lower().startswith("0x"):
        try:
            return int(text, 16)
        except ValueError:
            return None
    if re.fullmatch(r"[0-9A-Fa-f]{1,16}", text):
        return int(text, 16)
    return None


# --------------------------------------------------------------------------- #
# XML reader (preferred source)
# --------------------------------------------------------------------------- #

class LinkXML:
    def __init__(self, path):
        self.path = str(path)
        self.source = "xml"
        self.root = ET.parse(path).getroot()
        self._build_indexes()

    def _build_indexes(self):
        self.input_files = {}
        node = self.root.find("input_file_list")
        for item in (node.iter("input_file") if node is not None else []):
            ref = item.get("id")
            if ref is None:
                continue
            self.input_files[ref] = {
                "file": (item.findtext("file") or "").strip(),
                "name": (item.findtext("name") or "").strip(),
                "kind": (item.findtext("kind") or "").strip(),
            }
        self.components = []
        node = self.root.find("object_component_list")
        for item in (node.iter("object_component") if node is not None else []):
            ref = item.get("id")
            run = parse_addr(item.findtext("run_address"))
            load = parse_addr(item.findtext("load_address"))
            size = parse_addr(item.findtext("size"))
            comp = {
                "id": ref,
                "name": (item.findtext("name") or "").strip(),
                "load_address": load,
                "run_address": run,
                "size": size,
                "input_file_ref": item.find("input_file_ref").get("idref")
                if item.find("input_file_ref") is not None else None,
            }
            self.components.append(comp)
        self.symbols = {}
        self.sym_comp = {}
        node = self.root.find("symbol_table")
        for item in (node.iter("symbol") if node is not None else []):
            name = item.findtext("name")
            if name is None:
                continue
            self.symbols[name] = parse_addr(item.findtext("value"))
            ref = None
            refnode = item.find("object_component_ref")
            if refnode is not None:
                ref = refnode.get("idref")
                self.sym_comp[name] = ref
        entry = self.root.find("entry_point")
        if entry is not None:
            self.entry = {
                "name": (entry.findtext("name") or "").strip(),
                "address": parse_addr(entry.findtext("address")),
            }
        else:
            self.entry = {"name": None, "address": None}
        # Components that are actually placed in memory: a component counts when its logical
        # group (logical_group_list) is referenced by a placement_map allocated_space/logical_group_ref.
        self.placed_ids = self._placed_component_ids()

    def regions(self):
        out = []
        node = self.root.find("placement_map")
        for area in (node.iter("memory_area") if node is not None else []):
            name = (area.findtext("name") or "").strip()
            out.append({
                "name": name,
                "page": parse_addr(area.findtext("page_id")),
                "origin": parse_addr(area.findtext("origin")),
                "length": parse_addr(area.findtext("length")),
                "used": parse_addr(area.findtext("used_space")),
                "unused": parse_addr(area.findtext("unused_space")),
            })
        return out

    def component_for_address(self, address, page=None):
        """Object component whose run range [run, run+size) contains address.
        Prefer the component whose run_address equals the address exactly."""
        exact = None
        for comp in self.components:
            run = comp["run_address"]
            size = comp["size"]
            if run is None or size is None:
                continue
            if page is not None and comp.get("page") is not None and comp["page"] != page:
                continue
            if address == run:
                return comp
            if address >= run and address < run + size:
                if exact is None:
                    exact = comp
        return exact

    def input_name(self, comp):
        ref = comp["input_file_ref"]
        if ref is None:
            return None
        info = self.input_files.get(ref, {})
        # archive member: <name> is the member, <file> is the archive
        if info.get("kind") == "archive":
            return info.get("name") or info.get("file")
        return info.get("file") or info.get("name")

    def _placed_component_ids(self):
        """IDs of object components placed in memory: their logical group is referenced by a
        placement_map allocated_space/logical_group_ref."""
        placed = set()
        # logical_group id -> set of object_component_ref ids
        lg_to_comps = {}
        lgnode = self.root.find("logical_group_list")
        for lg in (lgnode.iter("logical_group") if lgnode is not None else []):
            lgid = lg.get("id")
            refs = [c.get("idref") for c in (lg.iter("object_component_ref") if lg is not None else [])]
            if lgid is not None:
                lg_to_comps[lgid] = refs
        # placement_map allocated_space/logical_group_ref
        pnode = self.root.find("placement_map")
        for area in (pnode.iter("memory_area") if pnode is not None else []):
            for alloc in (area.iter("allocated_space") if area is not None else []):
                ref = alloc.find("logical_group_ref")
                if ref is None:
                    continue
                lgid = ref.get("idref")
                placed.update(lg_to_comps.get(lgid, ()))
        return placed


# --------------------------------------------------------------------------- #
# Text map reader (fallback)
# --------------------------------------------------------------------------- #

class LinkMap:
    def __init__(self, path):
        self.path = str(path)
        self.source = "map"
        self.text = path.read_text(encoding="utf-8", errors="replace")

    def entry(self):
        match = re.search(r'ENTRY POINT SYMBOL:\s*"([^"]+)"\s+address:\s*([0-9A-Fa-f]+)', self.text)
        if match:
            return {"name": match.group(1), "address": int(match.group(2), 16)}
        return {"name": None, "address": None}

    def regions(self):
        out = []
        block = self._section("MEMORY CONFIGURATION", "SEGMENT ALLOCATION MAP")
        lines = re.split(r"\r?\n", block)
        for line in lines:
            m = re.match(r"\s*(\S+)\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})", line)
            if m:
                name, origin, length, used, unused = m.groups()
                out.append({
                    "name": name, "page": 0, "origin": int(origin, 16),
                    "length": int(length, 16), "used": int(used, 16),
                    "unused": int(unused, 16),
                })
        return out

    def sections(self):
        """List of (section_name, page, run_origin, length, input_pieces)."""
        out = []
        block = self._section("SECTION ALLOCATION MAP", "MODULE SUMMARY")
        lines = block.splitlines()
        current = None
        for line in lines:
            m = re.match(r"\s*(\S+)\s+(\d+)\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})\s", line)
            if m and not line.strip().startswith("0") and "\t" not in line[:1]:
                pass
            # section header line: name, page, origin, length
            m = re.match(r"\s*(\.\w+)\s+(\d+)\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})\s+(.*)$", line)
            if m and "\t" not in line[:1] and not line.lstrip().startswith("0000"):
                current = {"name": m.group(1), "page": int(m.group(2)),
                           "run": int(m.group(3), 16), "length": int(m.group(4), 16),
                           "inputs": []}
                out.append(current)
                continue
            if current is not None:
                piece = re.search(r"(\S+\.(?:o|a))\s*\(([^)]+)\)", line)
                if piece and piece.group(1) not in ("--HOLE--",):
                    current["inputs"].append((piece.group(1), piece.group(2)))
        return out

    def _section(self, start, end=None):
        text = self.text
        s = text.find(start)
        if s < 0:
            return ""
        if end:
            e = text.find(end, s)
            return text[s:e] if e > 0 else text[s:]
        return text[s:]


# --------------------------------------------------------------------------- #
# Compiler family / unit
# --------------------------------------------------------------------------- #

def compiler_family_for_build(build_dir):
    """Return compiler family token (e.g. c2000, armllvm) from the build's makefile."""
    makefile = build_dir / "makefile"
    if not makefile.is_file():
        return None
    text = makefile.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\\\r?\n", " ", text)
    # Prefer the TI CGT folder name (ti-cgt-c2000 -> c2000), then the tool name.
    match = re.search(r"(ti-cgt-[a-z0-9_.]+)", text, re.I)
    if match:
        return project_facts.compiler_family(match.group(1))
    match = re.search(r"cl2000|cl7x|cl6x|clpru|tiarmclang|c29clang", text, re.I)
    if match:
        tool = match.group(0).lower()
        return {"tiarmclang": "armllvm", "cl2000": "c2000", "cl7x": "c7000",
                "c29clang": "c29", "cl6x": "c6000", "clpru": "pru"}.get(tool, tool)
    return None


def unit_for_family(family):
    return "word16" if family == "c2000" else "byte"


# --------------------------------------------------------------------------- #
# Build selection / staleness (via project_facts)
# --------------------------------------------------------------------------- #

def resolve_build(project):
    """Return (build_dir, facts) or (None, None). Errors via parser / exit 3."""
    installed = project_facts.inventory([project_facts.DEFAULT_ROOT])
    headers = project_facts.syscfg_headers(project)
    entries = project_facts.cproject_builds(project)
    if not entries:
        print(f"{ASK_USER}: no .cproject with builds in {project}", file=sys.stderr)
        sys.exit(EXIT_STALE)
    facts_by_name = {e["name"]: project_facts.build_facts(project, e, headers, installed,
                                                          __import__("platform").system())
                     for e in entries}
    return facts_by_name


def select_and_check(project, facts_by_name, build_name):
    """Return build_dir, or exit 3 with ASK USER for stale / not built / foreign."""
    if build_name not in facts_by_name:
        print(f"{ASK_USER}: unknown build {build_name!r}", file=sys.stderr)
        sys.exit(EXIT_STALE)
    facts = facts_by_name[build_name]
    build_dir = project / build_name
    if not facts["built"]:
        print(f"{ASK_USER}: {build_name} was never built; build it in CCS then run link_facts again",
              file=sys.stderr)
        sys.exit(EXIT_STALE)
    for item in facts["foreign"]:
        print(f"{ASK_USER}: {build_name} was generated on {item['os']} ({item['path']}); rebuild on this machine",
              file=sys.stderr)
        sys.exit(EXIT_STALE)
    if facts["stale"]:
        print(f"{ASK_USER}: {facts['stale'][0]}; rebuild {build_name} in CCS, then run link_facts again",
              file=sys.stderr)
        sys.exit(EXIT_STALE)
    return build_dir


# --------------------------------------------------------------------------- #
# Old map detection
# --------------------------------------------------------------------------- #

def find_other_maps(build_dir, keep_base):
    """Return paths of other .map / _linkInfo.xml in the build folder besides the ones the
    makefile names (keep_base is the stem the makefile uses)."""
    ignored = []
    for path in sorted(build_dir.glob("*.map")):
        if path.stem == keep_base:
            continue
        ignored.append(path)
    for path in sorted(build_dir.glob("*_linkInfo.xml")):
        if path.stem == keep_base + "_linkInfo":
            continue
        ignored.append(path)
    return ignored


def makefile_link_names(build_dir):
    """Parse the makefile linker options for the map and XML the build actually uses.

    Returns (map_stem, xml_stem) — a None means the makefile named none (fall back to
    newest-mtime). Handles both bare (-m"x", --xml_link_info="x") and linker-prefixed
    (-Wl,-m"x", -Wl,--xml_link_info="x") forms."""
    makefile = build_dir / "makefile"
    if not makefile.is_file():
        return None, None
    text = makefile.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\\\r?\n", " ", text)

    def option(pattern):
        m = re.search(pattern, text)
        return m.group(1) if m else None

    q = chr(34)  # a double quote, kept out of the regex literal so it can't close the string
    map_stem = option(r"(?:^|\s)(?:-Wl,)?-m" + q + r"([^" + q + r"]+)" + q)
    xml_stem = option(r"(?:^|\s)(?:-Wl,)?--xml_link_info" + q + r"([^" + q + r"]+)" + q)
    return map_stem, xml_stem


# --------------------------------------------------------------------------- #
# Command implementations
# --------------------------------------------------------------------------- #

def cmd_summary(src, unit, ignored, xml_path=None, map_path=None):
    regions = src.regions()
    entry = src.entry
    if callable(entry):
        entry = entry()
    out = {
        "source": src.source,
        "xml": xml_path,
        "map": map_path,
        "ignored": [str(p) for p in ignored],
        "unit": unit,
        "entry": entry,
        "regions": regions,
    }
    return out


def cmd_symbol(src, names):
    rows = []
    for name in names:
        comp = None
        if name in src.symbols:
            addr = src.symbols[name]
            comp = src.component_for_address(addr) if addr is not None else None
            found = True
        else:
            addr = None
            found = False
        if comp is None:
            section = None
            section_address = None
            section_size = None
            input_file = None
        else:
            section = comp["name"]
            section_address = comp["run_address"]
            section_size = comp["size"]
            input_file = src.input_name(comp)
        if not found:
            note = "not in symbol table: may be inlined or removed"
        else:
            note = ""
        rows.append({
            "name": name,
            "found": found,
            "address": addr,
            "section": section,
            "section_address": section_address,
            "section_size": section_size,
            "input_file": input_file,
            "note": note,
        })
    return {"symbols": rows}


def cmd_section(src, names):
    out = []
    if isinstance(src, LinkXML):
        by_name = {}
        for comp in src.components:
            by_name.setdefault(comp["name"], comp)
        for name in names:
            comp = by_name.get(name)
            if comp is None:
                out.append({"name": name, "found": False, "load_address": None,
                            "run_address": None, "size": None, "inputs": []})
                continue
            out.append({"name": name, "found": True,
                        "load_address": comp["load_address"], "run_address": comp["run_address"],
                        "size": comp["size"], "inputs": [src.input_name(comp)]})
    else:
        sections = src.sections()
        by_name = {s["name"]: s for s in sections}
        for name in names:
            s = by_name.get(name)
            if s is None:
                out.append({"name": name, "found": False, "run_address": None, "length": None, "inputs": []})
                continue
            out.append({"name": name, "found": True, "run_address": s["run"], "length": s["length"],
                        "inputs": [f"{o}({i})" for o, i in s["inputs"]]})
    return {"sections": out}


def cmd_overlaps(src):
    overlaps = []
    # object components' run ranges within the same page
    comps = [c for c in src.components
             if c["run_address"] is not None and c["size"] is not None and c["size"] > 0
             and c["id"] in getattr(src, "placed_ids", set())]
    for i in range(len(comps)):
        for j in range(i + 1, len(comps)):
            a, b = comps[i], comps[j]
            if (a.get("page") is not None and b.get("page") is not None
                    and a["page"] != b["page"]):
                continue
            a_start, a_end = a["run_address"], a["run_address"] + a["size"]
            b_start, b_end = b["run_address"], b["run_address"] + b["size"]
            lo = max(a_start, b_start)
            hi = min(a_end, b_end)
            if lo < hi:
                overlaps.append({"a": a["name"], "b": b["name"], "start": lo, "end": hi})
    over_full = []
    for region in src.regions():
        if region["length"] and region["used"] and region["used"] > region["length"]:
            over_full.append(region["name"])
    return {"overlaps": overlaps, "over_full": over_full}


# --------------------------------------------------------------------------- #
# Source selection
# --------------------------------------------------------------------------- #

def make_source(xml_path, map_path, unit, ignored=None):
    if xml_path is not None:
        return LinkXML(xml_path), "xml"
    if map_path is not None:
        return LinkMap(map_path), "map"
    return None, None


# --------------------------------------------------------------------------- #
# Main / argument parsing
# --------------------------------------------------------------------------- #

def main(argv=None):
    # project, command, and symbol/section names are positional; flags are --xml/--map/
    # --unit/--json and --build NAME. Parse manually so `--build NAME summary` works.
    positionals = []
    flags = {"xml": None, "map": None, "unit": None, "json": False, "build": None}
    i = 0
    tokens = list(argv if argv is not None else sys.argv[1:])
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--build":
            flags["build"] = tokens[i + 1]; i += 2; continue
        if tok.startswith("--build="):
            flags["build"] = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--xml":
            flags["xml"] = Path(tokens[i + 1]); i += 2; continue
        if tok.startswith("--xml="):
            flags["xml"] = Path(tok.split("=", 1)[1]); i += 1; continue
        if tok == "--map":
            flags["map"] = Path(tokens[i + 1]); i += 2; continue
        if tok.startswith("--map="):
            flags["map"] = Path(tok.split("=", 1)[1]); i += 1; continue
        if tok == "--unit":
            flags["unit"] = tokens[i + 1]; i += 2; continue
        if tok.startswith("--unit="):
            flags["unit"] = tok.split("=", 1)[1]; i += 1; continue
        if tok == "--json":
            flags["json"] = True; i += 1; continue
        positionals.append(tok); i += 1
    args = argparse.Namespace(xml=flags["xml"], map=flags["map"], unit=flags["unit"],
                              json=flags["json"], build=flags["build"])
    # positional[0] = project (explicit mode has none), [1] = command, rest = names
    if args.xml is not None or args.map is not None:
        # explicit mode: no project; first positional is the command
        project = None
        command = positionals[0] if positionals else None
        names = positionals[1:]
    else:
        project = Path(positionals[0]).expanduser().resolve() if positionals else None
        command = positionals[1] if len(positionals) >= 2 else None
        names = positionals[2:]

    # explicit mode
    if args.xml is not None or args.map is not None:
        if args.unit not in ("byte", "word16"):
            print(f"{ASK_USER}: --unit required in explicit mode", file=sys.stderr)
            sys.exit(EXIT_STALE)
        src, source = make_source(args.xml, args.map, args.unit)
        return emit(args, src, unit=args.unit, command=command, names=names,
                    xml_path=str(args.xml) if args.xml is not None else None,
                    map_path=str(args.map) if args.map is not None else None)

    # project mode
    if project is None or command is None:
        print("usage: link_facts.py PROJECT_DIR --build NAME <command> [names...]", file=sys.stderr)
        return 2
    if args.build is None:
        print(f"{ASK_USER}: --build NAME required", file=sys.stderr)
        sys.exit(EXIT_STALE)
    facts_by_name = resolve_build(project)
    build_dir = select_and_check(project, facts_by_name, args.build)

    family = compiler_family_for_build(build_dir)
    unit = args.unit or unit_for_family(family)
    if args.unit and args.unit != unit_for_family(family):
        print(f"{ASK_USER}: --unit {args.unit} disagrees with compiler family {family}",
              file=sys.stderr)
        sys.exit(EXIT_STALE)

    keep_base = build_dir.name
    map_stem, xml_stem = makefile_link_names(build_dir)

    def resolve_link_file(glob_pattern, stem, suffix):
        """Return the build-folder file the makefile names, else newest-mtime fallback."""
        if stem:
            candidate = build_dir / stem
            if candidate.is_file():
                return candidate
        candidates = sorted(build_dir.glob(glob_pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    xml_path = resolve_link_file("*_linkInfo.xml", xml_stem, "_linkInfo.xml")
    map_path = resolve_link_file("*.map", map_stem, ".map")
    if xml_path is None and map_path is None:
        print(f"{ASK_USER}: no map or _linkInfo.xml in {build_dir}", file=sys.stderr)
        sys.exit(EXIT_STALE)

    # ignored = other map/XML files in the folder; never the ones in use.
    in_use_names = {xml_path.name, map_path.name}
    ignored = []
    for path in sorted(build_dir.glob("*.map")):
        if path.name not in in_use_names:
            ignored.append(path)
    for path in sorted(build_dir.glob("*_linkInfo.xml")):
        if path.name not in in_use_names:
            ignored.append(path)

    src, source = make_source(xml_path, map_path, unit, ignored)

    return emit(args, src, unit, command, names, ignored, xml_path=str(xml_path), map_path=str(map_path))


def emit(args, src, unit, command, names, ignored=None, xml_path=None, map_path=None):
    if command == "summary":
        data = cmd_summary(src, unit, ignored or [], xml_path=xml_path, map_path=map_path)
    elif command == "symbol":
        data = cmd_symbol(src, names)
    elif command == "section":
        data = cmd_section(src, names)
    elif command == "overlaps":
        data = cmd_overlaps(src)
    else:
        print(f"unknown command {command!r}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(data, indent=1))
    else:
        sys.stdout.write(render_markdown(command, data, src))
    return 0


def render_markdown(command, data, src):
    """Human-readable Markdown. The JSON contract is the machine interface; this is the
    default view. Where a text-map line backs a value, cite it as file:line."""
    lines = [f"## link_facts — command: {command} (source: {getattr(data, 'source', src.source)})", ""]
    if command == "summary":
        lines.append(f"Entry point: {data['entry']['name']} @ 0x{data['entry']['address']:x} "
                     f"({data['entry']['address']})")
        lines.append(f"Address unit: {data['unit']}")
        if data.get("ignored"):
            lines.append("")
            lines.append("Old artifacts in this build folder (not used):")
            for path in data["ignored"]:
                lines.append(f"  - {path}")
        lines.append("")
        lines.append("Memory regions:")
        lines.append("")
        lines.append("| region | page | origin | length | used | unused | used% |")
        lines.append("|--------|-----:|-------:|-------:|-----:|-------:|------:|")
        for r in data["regions"]:
            used_pct = (100 * r["used"] / r["length"]) if r["length"] else 0.0
            flag = "  <-- over 100%" if r["used"] > r["length"] else ""
            lines.append(f"| {r['name']} | {r['page']} | 0x{r['origin']:x} | 0x{r['length']:x} "
                         f"| 0x{r['used']:x} | 0x{r['unused']:x} | {used_pct:.1f}%{flag} |")
        return "\n".join(lines) + "\n"
    if command == "symbol":
        lines.append("| symbol | found | address | section | section addr | section size | input | note |")
        lines.append("|--------|------:|--------:|--------:|-------------:|-------------:|------:|------|")
        for s in data["symbols"]:
            addr = f"0x{s['address']:x}" if s["address"] is not None else "-"
            sec = s["section"] or "-"
            sa = f"0x{s['section_address']:x}" if s["section_address"] is not None else "-"
            ss = f"0x{s['section_size']:x}" if s["section_size"] is not None else "-"
            lines.append(f"| {s['name']} | {str(s['found'])} | {addr} | {sec} | {sa} | {ss} "
                         f"| {s['input_file'] or '-'} | {s['note']} |")
        return "\n".join(lines) + "\n"
    if command == "section":
        lines.append("| section | found | load | run | size | input pieces |")
        lines.append("|---------|------:|-----:|----:|-----:|-------------:|")
        for s in data["sections"]:
            lines.append(f"| {s['name']} | {str(s['found'])} | {s.get('load_address')} | "
                         f"{s.get('run_address')} | {s.get('size')} | {', '.join(s.get('inputs', []))} |")
        return "\n".join(lines) + "\n"
    if command == "overlaps":
        lines.append("Overlapping object components (run ranges, same page):")
        lines.append("")
        if data["overlaps"]:
            lines.append("| a | b | overlap start | overlap end |")
            lines.append("|--|--|------:|------:|")
            for o in data["overlaps"]:
                lines.append(f"| {o['a']} | {o['b']} | 0x{o['start']:x} | 0x{o['end']:x} |")
        else:
            lines.append("(none)")
        lines.append("")
        lines.append("Regions over 100% used: " + (", ".join(data["over_full"]) or "none"))
        return "\n".join(lines) + "\n"
    return json.dumps(data, indent=1) + "\n"


if __name__ == "__main__":
    sys.exit(main())
