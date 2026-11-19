#!/usr/bin/env python3
"""note_tables.py — Markdown table from a tool's --json output (D11, item 8).

KIND struct_layout -> | member | offset | bit | size note |, header row `| Member | Offset (<unit>) | Bits |`;
KIND link_regions -> | Region | Origin | Length | Used | Unused | (hex 0x..., from link_facts summary);
No other cell text than the JSON values. Last line `Source: <TEXT>`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _fmt_hex(value) -> str:
    """Format an int as `0x...`; pass through strings unchanged."""
    if isinstance(value, str):
        return value
    return f"0x{value:x}"


def _struct_layout_table(data: dict) -> list[str]:
    unit = data.get("unit", "")
    header = f"| Member | Offset ({unit}) | Bits |"
    sep = "|---|---|---|"
    rows = [header, sep]
    for m in data.get("members", []):
        name = m.get("name", "")
        offset = m.get("offset", "")
        bit_lsb = m.get("bit_lsb")
        bit_size = m.get("bit_size")
        if bit_lsb is None or bit_size is None:
            bit = ""
        else:
            bit = f"{bit_lsb}-{bit_lsb + bit_size - 1}"
        rows.append(f"| {name} | {offset} | {bit} |")
    return rows


def _link_regions_table(data: dict) -> list[str]:
    header = "| Region | Origin | Length | Used | Unused |"
    sep = "|---|---|---|---|---|"
    rows = [header, sep]
    for region in data.get("regions", []):
        name = region.get("name", "")
        origin = _fmt_hex(region.get("origin", ""))
        length = _fmt_hex(region.get("length", ""))
        used = _fmt_hex(region.get("used", ""))
        unused = _fmt_hex(region.get("unused", ""))
        rows.append(f"| {name} | {origin} | {length} | {used} | {unused} |")
    return rows


def build_table(kind: str, data: dict, source: str) -> str:
    if kind == "struct_layout":
        rows = _struct_layout_table(data)
    elif kind == "link_regions":
        rows = _link_regions_table(data)
    else:
        print(f"ERROR: unknown KIND {kind!r}", file=sys.stderr)
        return ""
    rows.append(f"Source: {source}")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) < 2:
        print("usage: note_tables.py KIND JSON [--source TEXT]", file=sys.stderr)
        return 2
    kind = argv[0]
    json_path = argv[1]
    source = "unknown"
    if "--source" in argv:
        idx = argv.index("--source")
        if idx + 1 < len(argv):
            source = argv[idx + 1]
    try:
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    out = build_table(kind, data, source)
    if not out:
        return 2
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
