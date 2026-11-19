#!/usr/bin/env python3
"""sdk_inventory.py [--root DIR ...] [--match TEXT] [--json]

What is installed on this machine, read from the files (no model):
SDKs (every .metadata/product.json, also SDKs bundled inside another SDK),
TI compilers (ti-cgt-* / ti_cgt_* folders, also CCS's tools/compiler) and
SysConfig tools (sysconfig_<version> folders, also CCS's utils).

--match TEXT: take the version claims in TEXT (e.g. "SDK 12.01") and list the
installed items whose version starts with the claim. A claim is a claim: the
result says only which local copies fit it.
"""
import argparse
import json
from pathlib import Path
import re
import sys

DEFAULT_ROOT = Path.home() / "ti"
VERSION = re.compile(r"\d+(?:[._]\d+){1,}")
SKIP = {"node_modules", ".git", "docs", "examples", "source", "tools", "packages", "dist", "utilities", "kernel"}
TOOL_FOLDER = re.compile(r"^(ti[-_]cgt[-_]|sysconfig_|ccs\d|CCSTargetConfigurations$|TICloudAgent$|uniflash)", re.I)


def version_tuple(text):
    """'26_01_00_03' / '26.1.0.03' / '1.28.1+4785' / 'TICLANG_5.1.1.LTS' -> (26, 1, 0, 3) ..."""
    if not text:
        return ()
    match = VERSION.search(str(text))
    return tuple(int(part) for part in re.split(r"[._]", match.group(0))) if match else ()


def starts_with(full, claim):
    """True when every part of the claim equals the same part of the full version."""
    return bool(claim) and len(full) >= len(claim) and full[:len(claim)] == claim


def folder_version(path):
    for part in reversed(Path(path).parts):
        if VERSION.search(part):
            return VERSION.search(part).group(0)
    return None


def find_products(root, depth=5):
    found, stack = [], [(root, 0)]
    while stack:
        folder, level = stack.pop()
        product = folder / ".metadata" / "product.json"
        if product.is_file():
            found.append(product)
        if level >= depth:
            continue
        try:
            children = [child for child in folder.iterdir() if child.is_dir() and not child.is_symlink()]
        except OSError:
            continue
        stack += [(child, level + 1) for child in children if child.name not in SKIP and not child.name.startswith(".")]
    return sorted(found)


def read_product(product):
    """name and version of a product.json (some have trailing commas: then read the first two keys by pattern)."""
    try:
        text = product.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return {key: match.group(1) for key in ("name", "version")
                for match in [re.search(r'"%s"\s*:\s*"([^"]*)"' % key, text)] if match}


def sdks(roots):
    """Every install folder under a root (version from the folder name), with the product.json found in it."""
    rows = []
    for root in roots:
        for install in sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".") and not TOOL_FOLDER.match(path.name)):
            products = find_products(install)
            if not any(product.parent.parent == install for product in products):
                rows.append({"kind": "sdk", "name": install.name, "version": None,
                             "folder_version": folder_version(install.name), "path": str(install), "product_json": None})
            for product in products:
                data, home = read_product(product), product.parent.parent
                rows.append({"kind": "sdk", "name": data.get("name"), "version": data.get("version"),
                             "folder_version": folder_version(home.relative_to(root)),
                             "path": str(home), "product_json": str(product)})
    return rows


def tool_folders(roots):
    compilers, sysconfigs = [], []
    for root in roots:
        candidates = list(root.glob("*")) + list(root.glob("ccs*/ccs/tools/compiler/*")) + list(root.glob("ccs*/ccs/utils/*"))
        for folder in sorted(candidates):
            if not folder.is_dir():
                continue
            name = folder.name
            match = re.match(r"ti[-_]cgt[-_]([a-z0-9_]+?)[-_](\d+(?:\.\d+)+)(?:\.(LTS|STS))?$", name, re.I)
            if match:
                compilers.append({"kind": "compiler", "name": re.sub(r"[-_]", "", match.group(1)).lower(),
                                  "version": match.group(2), "path": str(folder)})
            match = re.match(r"sysconfig_(\d+(?:\.\d+)+)$", name)
            if match:
                sysconfigs.append({"kind": "sysconfig", "name": "sysconfig", "version": match.group(1), "path": str(folder)})
    return compilers, sysconfigs


def inventory(roots):
    compilers, sysconfigs = tool_folders(roots)
    return {"sdks": sdks(roots), "compilers": compilers, "sysconfig": sysconfigs}


def item_versions(item):
    return [version_tuple(item.get("version")), version_tuple(item.get("folder_version"))]


def match_claims(text, data):
    """For each version claim in text: the installed items whose version starts with it."""
    words = {word.lower() for word in re.findall(r"[A-Za-z][A-Za-z0-9]*\d[A-Za-z0-9]*", text)}
    results = []
    for claim_text in dict.fromkeys(match.group(0) for match in VERSION.finditer(text)):
        claim = version_tuple(claim_text)
        hits = [item for group in data.values() for item in group
                if any(starts_with(version, claim) for version in item_versions(item))]
        narrowed = [item for item in hits if any(word in item["path"].lower() for word in words)]
        hits = narrowed or hits
        verdict = "none" if not hits else "unique" if len(hits) == 1 else "several"
        results.append({"claim": claim_text, "verdict": verdict, "candidates": hits,
                        "narrowed_by": sorted(word for word in words if any(word in item["path"].lower() for item in narrowed))})
    return results


def render(data, matches):
    out = ["# Installed SDKs and tools", ""]
    out.append("## SDKs (name, product.json version, folder version, path)")
    out += [f"- {item['name']} | {item['version']} | {item['folder_version']} | {item['path']}" for item in data["sdks"]] or ["- none"]
    out += ["", "## Compilers"]
    out += [f"- {item['name']} {item['version']} | {item['path']}" for item in data["compilers"]] or ["- none"]
    out += ["", "## SysConfig"]
    out += [f"- {item['version']} | {item['path']}" for item in data["sysconfig"]] or ["- none"]
    if matches is not None:
        out += ["", "## Version claims in the text (a claim, not a fact)"]
        if not matches:
            out.append("- the text states no version: ask for it")
        for result in matches:
            narrowed = f" (narrowed by: {', '.join(result['narrowed_by'])})" if result["narrowed_by"] else ""
            out.append(f"- \"{result['claim']}\": {result['verdict']}{narrowed}")
            out += [f"  - {item['kind']} {item['name']} {item.get('folder_version') or item['version']} | {item['path']}"
                    for item in result["candidates"]]
            if result["verdict"] == "none":
                out.append("  - no installed copy fits: ask for the exact version, or install it")
    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, action="append")
    parser.add_argument("--match")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    roots = [root.expanduser().resolve() for root in (args.root or [DEFAULT_ROOT])]
    data = inventory(roots)
    matches = match_claims(args.match, data) if args.match is not None else None
    if args.json:
        print(json.dumps({"inventory": data, "matches": matches}, indent=1))
    else:
        sys.stdout.write(render(data, matches))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
