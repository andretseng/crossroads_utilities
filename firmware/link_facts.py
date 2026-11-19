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
import shlex
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # own folder only
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
# .cmd (linker command file) parsing — rules / whatif-cmd (Part 2, D9)
# --------------------------------------------------------------------------- #

# A linker command file is a C-preprocessor text with three constructs we read:
#   MEMORY { NAME : ORIGIN = 0xN, LENGTH = 0xN ... }      -> memory regions
#   name = 0xN;                                           -> symbol / value assignments
#   SECTIONS { .sec : load = A, run = B, > 0xADDR, ... }  -> section placements
# We do NOT write a full .cmd parser: for whatif-cmd we let the linker do the work
# (copy the file, apply the change, relink). Here we only read the static structure.


def _strip_cmd_comments(text: str) -> str:
    """Remove C-style /* ... */ and // ... comments from .cmd text (line numbers preserved)."""
    out_lines = []
    for line in text.splitlines():
        # block comment that started on an earlier line
        if "/*" in line and "*/" not in line:
            out_lines.append("")
            continue
        head, sep, tail = line.partition("/*")
        if sep:
            line = head
        # // line comment
        idx = line.find("//")
        if idx >= 0:
            line = line[:idx]
        out_lines.append(line)
    return "\n".join(out_lines)


def _expand_cmd_file(build_dir: Path, cmd_path: Path) -> tuple[str, list[int]]:
    """Preprocess a .cmd file with the build's own compiler and return (text, line_map).

    The .cmd is a C-preprocessor input, so we let the linker/compiler do the `#define` / `#if`
    work instead of hand-rolling it. **clang** family: ``<compiler> -E -x c <cmd>``. **TI legacy**
    (cl2000/cl7x/clpru): ``<compiler> --preproc_only --preproc_with_line <build flags> <cmd>``;
    the output goes to a ``.pp`` file next to the input. ``--preproc_with_line`` (not ``-P``) keeps
    ``# N \"file\"`` line markers so we can map preprocessed lines back to the .cmd.

    Returns the preprocessed text and ``line_map[i]`` = the 1-based .cmd line that output line ``i``
    came from (tracked through the ``# N`` markers, exactly as the preprocessor does for C)."""
    family = compiler_family_for_build(build_dir)
    recipe = _link_recipe(build_dir)
    tokens = shlex_split_recipe(recipe) if recipe else []
    # The compiler is the first token of the link recipe.
    compiler = None
    for tok in tokens:
        if not tok.startswith("-") and ("cl2000" in tok or "tiarmclang" in tok or "c29clang" in tok
                                         or tok.endswith("cl2000") or "/bin/" in tok):
            compiler = tok
            break
    if compiler is None and tokens:
        compiler = tokens[0]
    if compiler is None or not Path(compiler).is_file():
        # No compiler available (e.g. never-built build): fall back to comment stripping only.
        raw = cmd_path.read_text(encoding="utf-8", errors="replace")
        stripped = _strip_cmd_comments(raw).splitlines()
        return "\n".join(stripped), list(range(1, len(stripped) + 1))

    is_ti_legacy = family in ("c2000", "c7000", "c6000", "pru")
    # Pass the build's own --define / --abi / etc. so #if defined(X) evaluates as the linker would.
    flags = _cmd_preproc_flags(tokens)
    # Send the preprocessor output to a /tmp directory: projects are read-only and cl2000's
    # --preproc_only writes <name>.pp next to the input by default.
    pp_dir = Path(tempfile.mkdtemp(prefix="link_cmd_pre_"))
    if is_ti_legacy:
        cmd = [compiler, "--preproc_only", "--preproc_with_line", "--pp_directory", str(pp_dir)] + flags + [str(cmd_path)]
        proc = subprocess.run(cmd, cwd=str(build_dir), capture_output=True, text=True, timeout=120)
        pp_path = pp_dir / (cmd_path.name[:-4] + ".pp") if cmd_path.name.endswith(".cmd") else pp_dir / (cmd_path.name + ".pp")
        if not pp_path.is_file():
            # cl2000 writes the .pp next to the input; if absent, fall back.
            fallback = cmd_path.with_suffix(".pp")
            pp_path = fallback
        text = pp_path.read_text(encoding="utf-8", errors="replace")
        raw_lines = cmd_path.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        # clang: -E -x c keeps line markers (# N "file"); -P would drop them (tests need lines).
        cmd = [compiler, "-E", "-x", "c"] + flags + [str(cmd_path)]
        proc = subprocess.run(cmd, cwd=str(build_dir), capture_output=True, text=True, timeout=120)
        text = proc.stdout
        raw_lines = cmd_path.read_text(encoding="utf-8", errors="replace").splitlines()
    line_map = _map_preproc_lines(text, raw_lines)
    return text, line_map


def _cmd_preproc_flags(tokens: list[str]) -> list[str]:
    """Build flags for the .cmd preprocessor: --define / --abi / -V from the build recipe."""
    flags = []
    for i, tok in enumerate(tokens):
        if tok.startswith("--define="):
            flags.append(tok)
        elif tok.startswith("-D"):
            flags.append(tok)
        elif tok.startswith("--abi="):
            flags.append(tok)
        elif tok == "--abi" and i + 1 < len(tokens):
            flags.append(tok); flags.append(tokens[i + 1]); i += 1
        elif tok.startswith("-V"):
            flags.append(tok)
    return flags


def _map_preproc_lines(text: str, raw_lines: list[str]) -> list[int]:
    """Map each preprocessed output line to its 1-based source (.cmd) line via ``# N`` markers.

    Returns a list with ONE entry per output line (same length as ``text.splitlines()``). Marker
    lines (``# N``) get their own ``N``; content lines get the source line the preprocessor's
    ``# N`` markers assign to them (a run of marker-less lines increments from the last marker)."""
    out_lines = text.splitlines()
    line_map = []
    pending = None   # set by a '# N' marker: this output line is source line N
    src_line = 0     # last assigned source line (for runs without markers)
    for line in out_lines:
        m = re.match(r"^#\s+(\d+)", line)
        if m:
            src = int(m.group(1))
            pending = src
            src_line = src
            line_map.append(min(src, len(raw_lines)))
        else:
            if pending is not None:
                src = pending
                src_line = pending  # subsequent lines increment from here
                pending = None
            else:
                src = src_line + 1
                src_line = src
            # clamp to the real .cmd length (markers can overshoot on trailing directives)
            line_map.append(min(src, len(raw_lines)))
    return line_map


def _cmd_value_to_int(value: str):
    """Return int(value) when value is a number (0x.. or decimal), else the raw text."""
    value = value.strip()
    if not value:
        return None
    try:
        return int(value, 0)
    except ValueError:
        return value


def parse_cmd_file(build_dir: Path, path: Path, defines: list[str] | None = None) -> dict:
    """Parse a linker command file into {memory, symbols, sections} with file:line for each.

    Each entry carries ``file`` (the .cmd path) and ``line`` (1-based .cmd line number). Values are
    int where they are numbers, else the text (e.g. a region name in a section's ``> REGION``).

    The .cmd is preprocessed with the build's own compiler (see ``_expand_cmd_file``) so that
    `#define` / `#if defined(X)` are handled exactly as the linker would; the ``# N`` line markers
    from the preprocessor map every emitted line back to its .cmd line."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    text, line_map = _expand_cmd_file(build_dir, path)
    lines = text.splitlines()

    memory = []
    symbols = []
    sections = []

    index = 0
    n = len(lines)
    while index < n:
        line = lines[index]
        stripped = line.strip()
        # MEMORY {   (the { may sit on the next line in TI .cmd files)
        if stripped == "MEMORY" or re.match(r"^MEMORY\s*{$", stripped):
            # skip a lone { on the following line
            if stripped == "MEMORY" and index + 1 < n and lines[index + 1].strip() == "{":
                index += 2
            else:
                index += 1
            while index < n and lines[index].strip() != "}":
                # origin/length are lowercase in TI C28x .cmd files and uppercase in clang ones.
                # A region attribute list like ``(RX)`` may sit between the name and the colon.
                m = re.match(
                    r"\s*(\S+)\s*(?:\([^)]*\))?\s*:\s*(?:ORIGIN|origin)\s*=\s*"
                    r"(0x[0-9A-Fa-f]+|\d+)\s*,\s*(?:LENGTH|length)\s*=\s*(0x[0-9A-Fa-f]+|\d+)",
                    lines[index])
                if m:
                    name, origin, length = m.group(1), m.group(2), m.group(3)
                    memory.append({
                        "name": name,
                        "origin": _cmd_value_to_int(origin),
                        "length": _cmd_value_to_int(length),
                        "file": str(path),
                        "line": line_map[index] if index < len(line_map) else index + 1,
                    })
                index += 1
            index += 1  # skip the closing }
            continue
        # SECTIONS {   (the { may sit on the next line in TI .cmd files)
        if stripped == "SECTIONS" or re.match(r"^SECTIONS\s*{$", stripped):
            if stripped == "SECTIONS" and index + 1 < n and lines[index + 1].strip() == "{":
                index += 2
            else:
                index += 1
            while index < n and lines[index].strip() != "}":
                entry, index = _parse_cmd_section(lines, index, str(path), line_map)
                if entry:
                    sections.append(entry)
            index += 1  # skip the closing }
            continue
        # top-level assignment:  name = value ;   (not inside MEMORY/SECTIONS, no leading dot-section)
        m = re.match(r"^\s*([A-Za-z_][\w$]*)\s*=\s*([^;]+);\s*$", stripped)
        if m:
            name = m.group(1)
            value = m.group(2).strip()
            # take the first token of the value (an address or number); ignore trailing tokens
            first = value.split()[0] if value.split() else value
            symbols.append({
                "name": name,
                "value": _cmd_value_to_int(first),
                "file": str(path),
                "line": line_map[index] if index < len(line_map) else index + 1,
            })
        index += 1
    return {"memory": memory, "symbols": symbols, "sections": sections}


def _parse_cmd_section(lines: list[str], index: int, cmd_file: str, line_map: list[int] | None = None):
    """Parse one SECTIONS entry starting at ``lines[index]``; return (entry_or_None, next_index).

    A section entry is ``.name : > REGION``, ``.name : >> REGION``, ``.name : palign(8) {} > REGION``,
    or ``.name : load = A, run = B, ...``. It may continue over several physical lines: while the
    current line ends with a comma, or while an opening ``{`` has not yet been closed, more lines
    are consumed. The placement (``> REGION`` / ``>> REGION``) may sit on a later line, e.g.
    ``.vectors : {\n}\n> R5F_VECS``. ``palign``/`{}`/`table(...)`/`LOAD_START`/… are ignored."""
    first = lines[index]
    m = re.match(r"^\s*(\.[\w.]+|[A-Za-z_]\w*)\s*:\s*(.*)$", first)
    if not m:
        return None, index + 1
    name = m.group(1)
    rest = m.group(2)
    load = None
    run = None
    direct = None  # "> REGION" or ">> REGION" keeps the region text (possibly an A | B | C list)
    line_no = (line_map[index] if line_map and index < len(line_map) else index + 1)
    open_braces = rest.count("{") - rest.count("}")
    # consume continuation lines: keep going while the line ends with a comma, or while a brace
    # block opened on this entry has not yet been closed (``.vectors : { ... } > REGION``).
    while True:
        text = rest
        ml = re.search(r"\bload\s*=\s*(\S+?)(?:,|$)", text)
        if ml:
            load = ml.group(1)
        mr = re.search(r"\brun\s*=\s*(\S+?)(?:,|$)", text)
        if mr:
            run = mr.group(1)
        # ">>" or ">" placement: take the region text that follows the ">", skipping any
        # palign()/table()/load-start()/ALIGN() filler and any {} member block that precedes it.
        md = re.search(r">>?\s*((?:[A-Za-z_][\w$.]*\s*(?:\|\s*[A-Za-z_][\w$.]*\s*)*))", text)
        if md and (load is None and run is None):
            direct = md.group(1).rstrip(",").strip()
        # stop when this line has no trailing comma AND the brace block (if any) is closed
        brace_open = text.count("{") - text.count("}")
        if not text.rstrip().endswith(",") and brace_open <= 0:
            break
        index += 1
        if index >= len(lines):
            break
        rest = lines[index]
    if direct is not None and run is None:
        run = direct
        if load is None:
            load = direct
    entry = {
        "name": name,
        "load": load,
        "run": run,
        "file": cmd_file,
        "line": line_no,
    }
    return entry, index + 1


# --------------------------------------------------------------------------- #
# .cmd discovery from the makefile (ORDERED_OBJS)
# --------------------------------------------------------------------------- #

def cmd_files_from_makefile(build_dir: Path) -> list[str]:
    """Return the .cmd file(s) named in the build's link recipe.

    The primary source is ``$(ORDERED_OBJS)`` (the link recipe). SysConfig-generated linker files
    are not in ORDERED_OBJS: they arrive through ``GEN_CMDS__FLAG`` as ``-Wl,-l"...cmd"`` tokens
    (e.g. ``-Wl,-l"./device_linker.cmd"`` for MSPM0, ``-Wl,-l"syscfg/linker.cmd"`` for AM261), so
    the whole makefile is scanned for those too. Every ``.cmd`` token is resolved relative to the
    build folder. When the build folder has no makefile (a never-built build), fall back to the
    project's ``linker/`` folder."""
    makefile = build_dir / "makefile"
    text = makefile.read_text(encoding="utf-8", errors="replace") if makefile.is_file() else ""
    cmd_files = []
    if text:
        text = re.sub(r"\\\r?\n", " ", text)
        # 1) $(ORDERED_OBJS): every ".cmd" token in the variable definition.
        ordered = _makefile_variable(text, "ORDERED_OBJS")
        cmd_files.extend(_cmd_tokens_from_ordered(ordered, build_dir))
        # 2) SysConfig linker files: -Wl,-l"...cmd" / -l"...cmd" anywhere in the makefile.
        cmd_files.extend(_cmd_tokens_from_genflags(text, build_dir))
    if cmd_files:
        return cmd_files
    # Fallback: a never-built build has no makefile; look in the project's linker/ folder.
    linker_dir = build_dir.parent / "linker"
    if linker_dir.is_dir():
        for path in sorted(linker_dir.glob("*.cmd")):
            cmd_files.append(str(path.resolve()))
    return cmd_files


def _cmd_tokens_from_genflags(text: str, build_dir: Path) -> list[str]:
    """Return .cmd paths named by SysConfig ``-Wl,-l"...cmd"`` / ``-l"...cmd"`` tokens."""
    cmd_files = []
    # -Wl,-l"path" or -l"path" (also -Wl,-l path without quotes, and -Wl,-l./x.cmd).
    for m in re.finditer(r"(?:-Wl,)?-l\s*([\"']?[^\s\"']+\.cmd[\"']?)", text):
        token = m.group(1).strip('"\'')
        p = Path(token)
        if p.is_absolute():
            cmd_files.append(str(p))
        else:
            cmd_files.append(str((build_dir / p).resolve()))
    # de-duplicate, keep order
    seen = set()
    unique = []
    for c in cmd_files:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def _cmd_tokens_from_ordered(ordered: str, build_dir: Path) -> list[str]:
    """Return the resolved .cmd paths from an ORDERED_OBJS token string."""
    if not ordered:
        return []
    cmd_files = []
    for token in shlex_split_recipe(ordered):
        if token.lower().endswith(".cmd"):
            p = Path(token)
            if p.is_absolute():
                cmd_files.append(str(p))
            else:
                cmd_files.append(str((build_dir / p).resolve()))
    # de-duplicate, keep order
    seen = set()
    unique = []
    for c in cmd_files:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def _makefile_variable(text: str, name: str) -> str:
    """Return the (possibly multi-line, +=) value of a makefile variable as one string."""
    out = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        row = lines[index]
        m = re.match(rf"^\s*{re.escape(name)}\s*(?:\+?=|\?:|:=)?\s*(.*)$", row)
        if m:
            chunk = m.group(1)
            # strip a trailing backslash-continuation marker, collect the next line(s)
            while chunk.rstrip().endswith("\\"):
                chunk = chunk.rstrip()[:-1]
                out.append(chunk.strip())
                index += 1
                if index < len(lines):
                    chunk = lines[index]
                else:
                    break
            out.append(chunk.strip())
        index += 1
    return " ".join(out)


def shlex_split_recipe(text: str) -> list[str]:
    """Split a makefile recipe/token string, honouring backslash-newline continuations and quotes."""
    text = re.sub(r"\\\r?\n", " ", text)
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


# --------------------------------------------------------------------------- #
# linker --define flags for .cmd preprocessing
# --------------------------------------------------------------------------- #

def linker_defines(build_dir: Path) -> list[str]:
    """Return the build's linker --define flags (from the link recipe) for .cmd preprocessing."""
    makefile = build_dir / "makefile"
    if not makefile.is_file():
        return []
    text = makefile.read_text(encoding="utf-8", errors="replace")
    recipe = None
    for row in text.splitlines():
        if "$(ORDERED_OBJS)" in row:
            recipe = row
            break
    if not recipe:
        return []
    tokens = shlex_split_recipe(recipe)
    defines = []
    for i, tok in enumerate(tokens):
        if tok.startswith("--define=") or (tok.startswith("-D") and not tok.startswith("-D")):
            # -DNAME or --define=NAME
            if tok.startswith("--define="):
                defines.append(tok.split("=", 1)[1])
            elif "=" in tok:
                defines.append(tok[2:])
            else:
                defines.append(tok[2:])
    return defines


# --------------------------------------------------------------------------- #
# rules command
# --------------------------------------------------------------------------- #

def cmd_rules(build_dir, project, build_name, facts) -> dict:
    """Read the build's .cmd file(s): memory, symbol/value assignments, section placements.

    ``basis`` is "rules only" when the build was never built (no link result to merge), else
    "rules" with each section augmented by its placed load/run from the linkInfo."""
    defines = linker_defines(build_dir)
    cmd_files = cmd_files_from_makefile(build_dir)
    if not cmd_files:
        # No makefile-named .cmd: nothing to read.
        return {"basis": "rules only", "cmd_files": [], "memory": [], "symbols": [], "sections": []}

    merged = {"memory": [], "symbols": [], "sections": []}
    for cmd_path in cmd_files:
        p = Path(cmd_path)
        if not p.is_file():
            continue
        parsed = parse_cmd_file(build_dir, p, defines)
        merged["memory"].extend(parsed["memory"])
        merged["symbols"].extend(parsed["symbols"])
        merged["sections"].extend(parsed["sections"])

    basis = "rules only"
    if facts and facts.get("built"):
        basis = "rules"
        placed = _placed_sections_from_linkxml(build_dir)
        for sec in merged["sections"]:
            info = placed.get(sec["name"])
            if info:
                sec["placed_run"] = info.get("run")
                sec["placed_load"] = info.get("load")
    return {"basis": basis, "cmd_files": cmd_files, **merged}


def _placed_sections_from_linkxml(build_dir: Path) -> dict:
    """section name -> {run, load} from the build's _linkInfo.xml (the link result)."""
    xml_path = None
    for path in sorted(build_dir.glob("*_linkInfo.xml"), key=lambda p: p.stat().st_mtime, reverse=True):
        xml_path = path
        break
    if xml_path is None:
        return {}
    try:
        src = LinkXML(xml_path)
    except Exception:
        return {}
    out = {}
    for comp in src.components:
        out.setdefault(comp["name"], {})
        if comp["run_address"] is not None:
            out[comp["name"]]["run"] = comp["run_address"]
        if comp["load_address"] is not None:
            out[comp["name"]]["load"] = comp["load_address"]
    return out


# --------------------------------------------------------------------------- #
# whatif-cmd command
# --------------------------------------------------------------------------- #

def cmd_whatif(build_dir, project, build_name, facts, replacements: list[tuple[str, str]]) -> dict:
    """Apply --replace OLD NEW edits to the build's .cmd, relink into /tmp, never touch the project.

    Each OLD must occur exactly once in the .cmd (else exit 2 with the count). The relink uses the
    build's own link recipe, the existing .o files, and writes -o / -m / --xml_link_info into /tmp."""
    cmd_files = cmd_files_from_makefile(build_dir)
    if not cmd_files:
        print(f"{ASK_USER}: no .cmd named in the build's ORDERED_OBJS in {build_dir}", file=sys.stderr)
        return {"linked": False, "exit_code": 2, "errors": ["no .cmd found"], "script_exit": 2}

    # Work in a fresh /tmp dir; copy the .cmd(s) there and apply the edits.
    work = Path(tempfile.mkdtemp(prefix="link_facts_whatif_"))
    cmd_targets = []
    diff_regions = []
    for cmd_path in cmd_files:
        p = Path(cmd_path)
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        # Remember the MEMORY region (name, length) pairs so a bare ``length = 0xN`` fragment can be
        # attributed to the region it belongs to (the test replaces ``length = 0x00020000`` and
        # expects name "FLASH").
        region_info = _memory_region_info(text)
        dest = work / p.name
        new_text = text
        for old, new in replacements:
            count = text.count(old)
            if count != 1:
                # OLD must occur exactly once; report the count and stop (exit 2).
                sys.stderr.write(f"error: replacement text occurred {count} times (expected 1) in {p.name}: {old!r}\n")
                return {"linked": False, "exit_code": 2,
                        "errors": [f"replacement text occurred {count} times (expected 1) in {p.name}"],
                        "script_exit": 2}
            new_text = new_text.replace(old, new)
            diff_regions.append({"name": _region_name_from_line(old, region_info), "field": "length",
                                 "old": _length_from_line(old), "new": _length_from_line(new)})
        dest.write_text(new_text, encoding="utf-8")
        cmd_targets.append(str(dest))

    if not cmd_targets:
        return {"linked": False, "exit_code": 2, "errors": ["no readable .cmd"], "script_exit": 2}

    # Build the link recipe: copy the build's .out recipe, but point -o/-m/--xml_link_info at /tmp.
    recipe = _link_recipe(build_dir)
    if not recipe:
        print(f"{ASK_USER}: could not find the .out link recipe in {build_dir}/makefile", file=sys.stderr)
        return {"linked": False, "exit_code": 2, "errors": ["no link recipe"], "script_exit": 2}

    # Verify the .o files the recipe needs exist (relink needs real objects).
    if not _recipe_objects_present(build_dir, recipe):
        print(f"{ASK_USER}: some object files the build links are missing in {build_dir}", file=sys.stderr)
        return {"linked": False, "exit_code": 3, "errors": ["missing objects"], "script_exit": 3}

    out_name = build_dir.name + ".out"
    map_name = build_dir.name + ".map"
    xml_name = build_dir.name + "_linkInfo.xml"
    makefile_text = (build_dir / "makefile").read_text(encoding="utf-8", errors="replace")
    cmd = shlex_split_recipe(recipe)
    # Expand every make variable the recipe references ($(ORDERED_OBJS), $(GEN_CMDS__FLAG), …) so
    # SysConfig-generated linker files that arrive through GEN_CMDS__FLAG reach the linker.
    expanded = _expand_make_vars(cmd, makefile_text)
    # Rewrite -o / -m / --xml_link_info to point into /tmp.
    new_cmd = _redirect_link_outputs(expanded, str(work / out_name), str(work / map_name), str(work / xml_name))
    # Substitute the .cmd token(s) with the /tmp copies. The linker file may be a standalone token
    # (``.../x.cmd``) or embedded in a driver flag (``-Wl,-l"syscfg/linker.cmd"``); handle both.
    new_cmd = _substitute_cmd_tokens(new_cmd, cmd_targets)

    # Relink with cwd = build folder (so relative .o paths and -i lib paths resolve), outputs in /tmp.
    proc = subprocess.run(new_cmd, cwd=str(build_dir), capture_output=True, text=True, timeout=240)
    combined = (proc.stdout + proc.stderr)
    errors = [ln for ln in combined.splitlines() if ln.strip()]
    linked = proc.returncode == 0

    result = {
        "linked": linked,
        "exit_code": proc.returncode,
        "errors": errors,
        "script_exit": 0,   # the script ran successfully; the linker's outcome is in the JSON
    }
    if linked:
        # a successful link keeps only real linker errors in ``errors`` (drop warnings such as
        # ``#10247-D: creating output section …`` that the .cmd emits even when it is seen);
        # the verbatim full output is still available to the caller via the process exit code.
        result["errors"] = [ln for ln in errors if not ln.strip().startswith("warning")]
        result["diff"] = {"regions": diff_regions, "symbols": []}
    else:
        # keep only the linker's own error lines (drop the trailing "tiarmclang: error:" wrapper)
        result["errors"] = [ln for ln in errors if "#" in ln or "error" in ln.lower()]
    return result


def _region_name_from_line(line: str, region_info: list[tuple[str, int | None]] = None) -> str:
    """Return the memory-region name a replacement refers to.

    Handles a full ``NAME : ORIGIN = …`` line (``NAME`` may be followed by an ``(RX)`` attribute)
    and a bare ``length = …`` / ``origin = …`` fragment: when the fragment carries no name, the
    MEMORY region whose ``LENGTH`` matches the fragment's value is used, else the sole region name,
    else ``""``."""
    m = re.search(r"(\S+)\s*(?:\([^)]*\))?\s*:\s*ORIGIN", line)
    if m:
        return m.group(1)
    # bare fragment like ``length = 0xN`` — resolve the region from the .cmd MEMORY section
    val = _length_from_line(line)
    if region_info and val is not None:
        for rn, rlen in region_info:
            if rlen == val:
                return rn
        if len(region_info) == 1:
            return region_info[0][0]
    return ""


def _region_length(name: str, text: str = "") -> int | None:
    """Return the LENGTH of the named MEMORY region, or None."""
    m = re.search(
        rf"\b{re.escape(name)}\s*(?:\([^)]*\))?\s*:\s*(?:ORIGIN|origin)\s*=\s*(?:0x[0-9A-Fa-f]+|\d+)\s*,\s*"
        rf"(?:LENGTH|length)\s*=\s*(0x[0-9A-Fa-f]+|\d+)",
        text)
    if not m:
        return None
    try:
        return int(m.group(1), 0)
    except ValueError:
        return None


def _memory_region_info(text: str) -> list[tuple[str, int | None]]:
    """Return ``(name, length)`` pairs for every MEMORY region declared in ``text`` (in order)."""
    info = []
    for m in re.finditer(
        r"^\s*(\S+)\s*(?:\([^)]*\))?\s*:\s*(?:ORIGIN|origin)\s*=\s*(?:0x[0-9A-Fa-f]+|\d+)\s*,\s*"
        r"(?:LENGTH|length)\s*=\s*(0x[0-9A-Fa-f]+|\d+)",
        text, re.M):
        try:
            length = int(m.group(2), 0)
        except ValueError:
            length = None
        info.append((m.group(1), length))
    return info


def _length_from_line(line: str):
    """Return the numeric value in a ``LENGTH = 0xN`` / ``length = 0xN`` / ``ORIGIN = 0xN`` fragment.

    Works on a full ``NAME : ORIGIN = …, LENGTH = …`` line or a bare ``length = 0xN`` fragment."""
    m = re.search(r"(?:LENGTH|length)\s*=\s*(0x[0-9A-Fa-f]+|\d+)", line)
    if not m:
        m = re.search(r"(?:ORIGIN|origin)\s*=\s*(0x[0-9A-Fa-f]+|\d+)", line)
    if not m:
        return None
    val = m.group(1)
    try:
        return int(val, 0)
    except ValueError:
        return val


def _link_recipe(build_dir: Path) -> str:
    """Return the raw link recipe line (the makefile line expanding $(ORDERED_OBJS))."""
    makefile = build_dir / "makefile"
    if not makefile.is_file():
        return ""
    text = makefile.read_text(encoding="utf-8", errors="replace")
    for row in text.splitlines():
        if "$(ORDERED_OBJS)" in row:
            return row
    return ""


def _recipe_objects_present(build_dir: Path, recipe: str) -> bool:
    """True when every ``./name.o`` object referenced by the recipe exists in the build folder."""
    for tok in shlex_split_recipe(recipe):
        if tok.endswith(".o") and not tok.startswith("-"):
            p = build_dir / tok
            if not p.is_file():
                return False
    return True


def _expand_make_vars(tokens: list[str], makefile_text: str) -> list[str]:
    """Expand make variables referenced by the link recipe.

    ``$(ORDERED_OBJS)`` expands to the object / .cmd / ``-l`` tokens, but SysConfig-generated linker
    files arrive through ``$(GEN_CMDS__FLAG)`` (e.g. ``-Wl,-l\"syscfg/linker.cmd\"``), which is itself
    referenced inside ORDERED_OBJS. Expansion is recursive so nested ``$(NAME)`` references resolve.
    Variables with no definition are dropped."""
    # Collect the variable definitions the recipe can reference.
    names = set(re.findall(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)", makefile_text))
    defs = {n: _makefile_variable(makefile_text, n) for n in names}

    def expand_tokens(toks):
        out = []
        for tok in toks:
            if tok.startswith("$(") and tok.endswith(")"):
                name = tok[2:-1]
                value = defs.get(name, "")
                if value:
                    out.extend(expand_tokens(shlex_split_recipe(value)))
                # undefined variable -> drop
            else:
                out.append(tok)
        return out

    return expand_tokens(tokens)


def _substitute_cmd_tokens(cmd: list[str], cmd_targets: list[str]) -> list[str]:
    """Replace the linker-file token(s) in the recipe with the /tmp copies.

    Handles both a standalone ``.../x.cmd`` token and a driver flag ``-Wl,-l\"syscfg/linker.cmd\"``
    (the SysConfig ``GEN_CMDS__FLAG`` form): the ``.cmd`` path inside each is rewritten to the
    copied ``.cmd``. Tokens are paired with ``cmd_targets`` in order."""
    if not cmd_targets:
        return cmd
    target = cmd_targets[0]
    out = []
    used = 0
    for tok in cmd:
        # standalone .cmd token (a real file argument, not a flag)
        if not tok.startswith("-") and tok.lower().endswith(".cmd"):
            if used < len(cmd_targets):
                out.append(cmd_targets[used])
                used += 1
            continue
        # embedded in a -Wl,-l"...cmd" / -l"...cmd" driver flag. Pass the path without extra quotes
        # (subprocess.run gets a list, so the shell does not strip quotes for us).
        m = re.match(r'^(.*-l)([^"]*)\.cmd(.*)$', tok)
        if m and used < len(cmd_targets):
            out.append(m.group(1) + target + m.group(3))
            used += 1
        else:
            out.append(tok)
    return out


def _redirect_link_outputs(cmd, out_path, map_path, xml_path):
    """Rewrite -o / -m / --xml_link_info values in a link recipe to point into /tmp.

    Only the linker map (`-m"file"` / `-Wl,-m"file"`) and `--xml_link_info` are rewritten; the
    ARM architecture flags (`-mcpu`, `-mfloat-abi`, `-mfpu`, `-mlittle-endian`, `-mthumb`, ...) are
    left untouched. Quoted paths keep their quotes so the clang driver keeps getting
    ``-Wl,-m"…"`` rather than a bare ``-m…``."""
    # ARM architecture / FPU flags that start with -m but must not be treated as the map flag.
    _ARM_MFLAGS = ("-mcpu", "-mfloat-abi", "-mfpu", "-mlittle-endian", "-mthumb", "-mmode",
                   "-mtp", "-msuper", "-mfix-cm3", "-mno")
    new = list(cmd)
    i = 0
    while i < len(new):
        tok = new[i]
        # -o <file>  or  -o<file>
        if tok == "-o" or tok == "--output":
            if i + 1 < len(new):
                new[i + 1] = out_path
                i += 2
                continue
        if tok.startswith("-o") and not tok.startswith("--"):
            new[i] = "-o" + out_path
            i += 1
            continue
        # -Wl,-m"file"  or  -m"file"  (the linker map flag). Rewrite the path in place so the clang
        # driver keeps getting ``-Wl,-m"…"`` rather than a bare ``-m…``. Quotes are optional because
        # shlex strips them before we get here.
        m = re.match(r'^(.*-Wl,?-m)([^"]*)$', tok, re.I)
        if m:
            new[i] = m.group(1) + map_path
            i += 1
            continue
        if not tok.startswith(_ARM_MFLAGS) and re.match(r'^-m"', tok, re.I):
            new[i] = '-m' + map_path
            i += 1
            continue
        # --xml_link_info="file"  or  -Wl,--xml_link_info="file"
        m = re.match(r'^(.*--xml_link_info=)([^"]*)$', tok, re.I)
        if m:
            new[i] = m.group(1) + xml_path
            i += 1
            continue
        m = re.match(r'^--xml_link_info=([^"]*)$', tok, re.I)
        if m:
            new[i] = '--xml_link_info="' + xml_path + '"'
            i += 1
            continue
        i += 1
    return new

def _parse_replacements(names: list[str]) -> list[tuple[str, str]]:
    """Parse the ``whatif-cmd`` positional args into [(OLD, NEW), ...].

    The CLI form is ``whatif-cmd --replace OLD NEW [--replace OLD NEW ...]``; by the time we reach
    here each ``--replace`` has been expanded to three positionals (``--replace`` OLD NEW), so we
    skip the ``--replace`` markers and pair the remaining tokens."""
    pairs = []
    i = 0
    while i < len(names):
        if names[i] == "--replace":
            if i + 2 < len(names):
                pairs.append((names[i + 1], names[i + 2]))
                i += 3
            else:
                i += 1
        else:
            i += 1
    return pairs


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
        if tok == "--replace":
            # whatif-cmd --replace OLD NEW: keep the pair as positionals for _parse_replacements.
            positionals.append(tok); positionals.append(tokens[i + 1]); positionals.append(tokens[i + 2])
            i += 3; continue
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

    # rules / whatif-cmd are build-recipe commands; they take their context from build_ctx.
    if command in ("rules", "whatif-cmd"):
        facts = facts_by_name.get(args.build)
        if facts is None:
            print(f"{ASK_USER}: unknown build {args.build!r}", file=sys.stderr)
            sys.exit(EXIT_STALE)
        build_dir = project / args.build
        # whatif-cmd needs a real, fresh build (it relinks real objects); rules need only the .cmd.
        if command == "whatif-cmd":
            if not facts["built"]:
                print(f"{ASK_USER}: {args.build} was never built; build it in CCS then run link_facts again",
                      file=sys.stderr)
                sys.exit(EXIT_STALE)
            for item in facts["foreign"]:
                print(f"{ASK_USER}: {args.build} was generated on {item['os']} ({item['path']}); rebuild on this machine",
                      file=sys.stderr)
                sys.exit(EXIT_STALE)
            if facts["stale"]:
                print(f"{ASK_USER}: {facts['stale'][0]}; rebuild {args.build} in CCS, then run link_facts again",
                      file=sys.stderr)
                sys.exit(EXIT_STALE)
        ctx = {
            "project": project,
            "build_dir": build_dir,
            "build_name": args.build,
            "facts": facts,
            "replacements": _parse_replacements(names),
        }
        return emit(args, None, None, command, names, build_ctx=ctx)

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


def emit(args, src, unit, command, names, ignored=None, xml_path=None, map_path=None, build_ctx=None):
    # rules / whatif-cmd are build-recipe commands; they take their context from build_ctx.
    if command in ("rules", "whatif-cmd"):
        data = _cmd_build_recipe(command, names, build_ctx)
        if args.json:
            print(json.dumps(data, indent=1))
        else:
            sys.stdout.write(render_markdown(command, data, src))
        # whatif-cmd carries its own exit code (0 ran fine; 2 bad replacement; 3 ASK USER).
        return int(data.get("script_exit", 0))
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


def _cmd_build_recipe(command, names, ctx) -> dict:
    """Dispatch the build-recipe commands (rules / whatif-cmd) from shared build context."""
    if command == "rules":
        return cmd_rules(ctx["build_dir"], ctx["project"], ctx["build_name"], ctx["facts"])
    if command == "whatif-cmd":
        return cmd_whatif(ctx["build_dir"], ctx["project"], ctx["build_name"], ctx["facts"], ctx["replacements"])


def render_markdown(command, data, src):
    """Human-readable Markdown. The JSON contract is the machine interface; this is the
    default view. Where a text-map line backs a value, cite it as file:line."""
    source = getattr(data, "source", None)
    if source is None and src is not None:
        source = getattr(src, "source", None)
    lines = [f"## link_facts — command: {command} (source: {source})", ""]
    if command == "rules":
        lines.append(f"basis: {data['basis']}")
        lines.append("")
        if data.get("cmd_files"):
            lines.append(".cmd files:")
            for p in data["cmd_files"]:
                lines.append(f"  - {p}")
            lines.append("")
        if data.get("memory"):
            lines.append("MEMORY:")
            lines.append("| name | origin | length | file:line |")
            lines.append("|--|--:|--:|------|")
            for m in data["memory"]:
                lines.append(f"| {m['name']} | 0x{m['origin']:x} | 0x{m['length']:x} | {m['file']}:{m['line']} |")
            lines.append("")
        if data.get("symbols"):
            lines.append("ASSIGNMENTS:")
            lines.append("| name | value | file:line |")
            lines.append("|--|--:|------|")
            for s in data["symbols"]:
                val = s["value"]
                val = f"0x{val:x}" if isinstance(val, int) else str(val)
                lines.append(f"| {s['name']} | {val} | {s['file']}:{s['line']} |")
            lines.append("")
        if data.get("sections"):
            lines.append("SECTIONS:")
            lines.append("| section | load | run | file:line |")
            lines.append("|--|--|--|------|")
            for s in data["sections"]:
                lines.append(f"| {s['name']} | {s.get('load')} | {s.get('run')} | {s['file']}:{s['line']} |")
            lines.append("")
        return "\n".join(lines) + "\n"
    if command == "whatif-cmd":
        lines.append(f"linked: {data['linked']}")
        lines.append(f"linker exit code: {data['exit_code']}")
        if data.get("errors"):
            lines.append("")
            lines.append("linker output:")
            for ln in data["errors"]:
                lines.append(f"  {ln}")
        if data.get("diff"):
            lines.append("")
            lines.append("region changes:")
            for r in data["diff"]["regions"]:
                lines.append(f"  {r['name']}.{r['field']}: 0x{r['old']:x} -> 0x{r['new']:x}")
        return "\n".join(lines) + "\n"
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
