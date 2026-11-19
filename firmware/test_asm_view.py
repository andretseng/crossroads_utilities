#!/usr/bin/env python3
"""Acceptance tests for asm_view.py (Claude, 2026-11-19). Do not edit. Facts from the cl2000 listing of
dab_F28003x device/f28003x_codestartbranch.asm (RELEASE): line 65 WD_DISABLE .set 1; line 80 `.if WD_DISABLE == 1` so line 81
`LB wd_disable` is assembled at codestart:0 and line 83 `LB _c_int00` (the .else) is not; line 100 `MOV @7029h, #0068h` at
.text address 3, encoding 2829."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "asm_view.py"
DAB = Path("/home/andre/workspace_ccstheia/power/dab_F28003x")


@unittest.skipUnless((DAB / "RELEASE/device/subdir_rules.mk").is_file(), "dab_F28003x missing")
class CodeStart(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proc = subprocess.run([sys.executable, str(TOOL), str(DAB), "--build", "RELEASE", "device/f28003x_codestartbranch.asm",
                               "--json"], capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cls.d = json.loads(proc.stdout)
        cls.rows = {r["line"]: r for r in cls.d["lines"]}

    def test_level_and_command(self):
        self.assertEqual(self.d["level"], "listing")
        self.assertTrue(self.d["command"][0].endswith("cl2000"))
        self.assertTrue(any("--asm_listing" in a for a in self.d["command"]))

    def test_conditional_assembly(self):
        self.assertTrue(self.rows[81]["assembled"])
        self.assertFalse(self.rows[83]["assembled"])
        self.assertEqual((self.rows[81]["section"], self.rows[81]["address"]), ("codestart", 0))

    def test_encoding_and_section(self):
        r = self.rows[100]
        self.assertEqual((r["section"], r["address"], r["encoding"]), (".text", 3, "2829"))

    def test_symbol(self):
        self.assertIn({"name": "WD_DISABLE", "value": 1, "line": 65}, self.d["symbols"])

    def test_build_folder_untouched(self):
        lst = DAB / "RELEASE/device/f28003x_codestartbranch.lst"
        before = lst.stat().st_mtime
        subprocess.run([sys.executable, str(TOOL), str(DAB), "--build", "RELEASE", "device/f28003x_codestartbranch.asm", "--json"],
                       capture_output=True, text=True, timeout=300)
        self.assertEqual(lst.stat().st_mtime, before)


if __name__ == "__main__":
    unittest.main()
