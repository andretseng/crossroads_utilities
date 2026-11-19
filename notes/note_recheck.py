#!/usr/bin/env python3
"""note_recheck.py — re-run cite + quote checks on every note in a vault (D11, item 8).

Runs the cite and quote checks of `note_check` on every `.md` under VAULT; prints one line per note
with failing cites (`note: cite reason`); never edits a note. Exit 1 when any.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import note_check  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print("usage: note_recheck.py VAULT --root DIR [...]", file=sys.stderr)
        return 2
    vault = None
    roots: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--root":
            roots.append(argv[i + 1]); i += 2; continue
        if not tok.startswith("-"):
            vault = tok; i += 1; continue
        i += 1
    if not vault:
        print("usage: note_recheck.py VAULT --root DIR [...]", file=sys.stderr)
        return 2
    vault_path = Path(vault)
    any_fail = False
    for note_path in sorted(vault_path.rglob("*.md")):
        findings = note_check.check_note(note_path, vault_path, roots)
        cite_quote = [f for f in findings if f["kind"] in ("cite", "quote")]
        if cite_quote:
            any_fail = True
            detail = "; ".join(f["detail"] for f in cite_quote)
            print(f"{note_path.name}: cite {detail}")
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
