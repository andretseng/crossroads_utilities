# SPEC: note tools (D11, item 8) — ~/utilities/notes/ (for Pi, 2026-11-19)
Design note §4.6f. Notes take values from tools; checks are evidence checks, not format checks. Standard library only.
Reuse ~/utilities/firmware/source_lines.py by import (sys.path) for every cite: never write another cite parser.

1. tw_wording.py — `TW_WORDS` (start from ~/.agents/pool/exploring/explore-guard/translate_answer.py, same order) and
   `convert(text) -> str` (fenced code blocks and inline code untouched); CLI `tw_wording.py FILE` prints converted text;
   `tw_wording.py --check FILE` prints `line: word -> replacement` for every hit outside code and exits 1 if any.
2. note_check.py NOTE --vault VAULT [--root DIR ...] [--json]: findings (each {"kind","line","detail"}), exit 1 when any error:
   - frontmatter: `title`, `date` (YYYY-MM-DD), `tags` (list) present -> kind "frontmatter";
   - every `[[target]]` / `[[target|alias]]` / `[[target#heading]]` resolves to `<target>.md` somewhere under VAULT -> "wikilink";
   - every cite found by source_lines.parse_cites resolves under the roots and its line exists -> "cite"; when the cite is
     followed by an inline-code quote (`` `file.c:12` `x = 3;` ``), the quote must be on that line -> "quote";
   - mainland words outside code (tw_wording) -> "wording" (warning, not error).
3. note_tables.py KIND JSON [--source TEXT]: Markdown table from a tool's --json output, last line `Source: <TEXT>`:
   KIND struct_layout -> | member | offset | bit | size note |, header row `| Member | Offset (<unit>) | Bits |`;
   KIND link_regions -> | Region | Origin | Length | Used | Unused | (hex 0x..., from link_facts summary);
   No other cell text than the JSON values.
4. note_recheck.py VAULT --root DIR [...]: run the cite and quote checks of note_check on every .md under VAULT; print one line per
   note with failing cites (`note: cite reason`); never edit a note. Exit 1 when any.
Tests: test_notes.py (given, do not edit). Work in /tmp/notes_pi/.
