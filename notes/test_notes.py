#!/usr/bin/env python3
"""Acceptance tests for the note tools (Claude, 2026-11-19). Do not edit."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def tool(name, *args):
    return subprocess.run([sys.executable, str(HERE / name), *map(str, args)], capture_output=True, text=True, timeout=120)


class Wording(unittest.TestCase):
    def test_convert_outside_code_only(self):
        sys.path.insert(0, str(HERE))
        import tw_wording
        text = "定時器的函數在下降沿觸發\n`定時器_init()`\n```c\n// 函數\n```\n"
        self.assertEqual(tw_wording.convert(text), "計時器的函式在下降緣觸發\n`定時器_init()`\n```c\n// 函數\n```\n")

    def test_check_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a.md"
            f.write_text("第一行\n使用寄存器\n")
            r = tool("tw_wording.py", "--check", f)
            self.assertEqual(r.returncode, 1)
            self.assertIn("2: 寄存器 -> 暫存器", r.stdout)


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.vault = root / "vault"
        (self.vault / "sub").mkdir(parents=True)
        (self.vault / "sub" / "Other note.md").write_text("---\ntitle: Other\ndate: 2026-11-19\ntags: [x]\n---\n")
        self.src = root / "proj"
        self.src.mkdir()
        (self.src / "io.c").write_text("int a;\nint b = 3;\nint c;\n")
        self.note = self.vault / "n.md"

    def tearDown(self):
        self.tmp.cleanup()

    def kinds(self, body, fm="---\ntitle: T\ndate: 2026-11-19\ntags: [a]\n---\n"):
        self.note.write_text(fm + body)
        r = tool("note_check.py", self.note, "--vault", self.vault, "--root", self.src, "--json")
        return r.returncode, sorted({f["kind"] for f in json.loads(r.stdout)})

    def test_clean_note(self):
        self.assertEqual(self.kinds("See [[Other note]] and `io.c:2` `int b = 3;`.\n"), (0, []))

    def test_bad_wikilink_cite_quote_frontmatter(self):
        self.assertEqual(self.kinds("[[Missing]]\n")[1], ["wikilink"])
        self.assertEqual(self.kinds("io.c:9\n")[1], ["cite"])                     # line 9 does not exist
        self.assertEqual(self.kinds("`io.c:1` `int b = 3;`\n")[1], ["quote"])     # quote is on line 2, not 1
        code, kinds = self.kinds("x\n", fm="---\ntitle: T\ndate: 19/11/2026\n---\n")
        self.assertEqual((code, kinds), (1, ["frontmatter"]))

    def test_recheck_lists_only_stale_notes(self):
        self.note.write_text("---\ntitle: T\ndate: 2026-11-19\ntags: [a]\n---\n`io.c:2` `int b = 3;`\n")
        (self.vault / "old.md").write_text("---\ntitle: O\ndate: 2026-11-01\ntags: [a]\n---\n`io.c:3` `int b = 3;`\n")
        r = tool("note_recheck.py", self.vault, "--root", self.src)
        self.assertEqual(r.returncode, 1)
        self.assertIn("old.md", r.stdout)
        self.assertNotIn("n.md", r.stdout)


class Tables(unittest.TestCase):
    def test_struct_layout_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = Path(tmp) / "s.json"
            j.write_text(json.dumps({"type": "U", "unit": "word16", "size": 6, "members": [
                {"name": "a", "offset": 0, "bit_lsb": None, "bit_size": None},
                {"name": "c", "offset": 4, "bit_lsb": 0, "bit_size": 3}]}))
            r = tool("note_tables.py", "struct_layout", j, "--source", "struct_layout.py dab RELEASE")
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = r.stdout.strip().splitlines()
            self.assertEqual(lines[0], "| Member | Offset (word16) | Bits |")
            self.assertIn("| a | 0 |  |", lines)
            self.assertIn("| c | 4 | 0-2 |", lines)
            self.assertEqual(lines[-1], "Source: struct_layout.py dab RELEASE")


if __name__ == "__main__":
    unittest.main()
