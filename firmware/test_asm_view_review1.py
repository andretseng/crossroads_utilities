#!/usr/bin/env python3
"""Codex review 1 for asm_view (2026-11-19). Do not edit.

TI expected values were measured from a fresh cl2000 listing for the unseen F28P65x motor-control
build. Clang preprocessing was measured directly with the unseen AM261 build's tiarmclang.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "asm_view.py"
UMC = Path("/home/andre/workspace_ccstheia/motor/universal_motorcontrol_lab_f28p65x")
UMC_BUILD = "Flash_lib_3phGaN_3SC"
UMC_SOURCE = "src_device/f28p65x_codestartbranch.asm"
AM261 = Path("/home/andre/workspace_ccstheia/motor/servo_drive_am261x-lp_r5fss0-0")


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args), "--json"],
                          capture_output=True, text=True, timeout=300)


def snapshot(root):
    return {str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


@unittest.skipUnless((UMC / f"{UMC_BUILD}/makefile").is_file(), "F28P65x project missing")
class UnseenTiListing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proc = run(UMC, "--build", UMC_BUILD, UMC_SOURCE, "--lines", "66-101")
        if proc.returncode != 0:
            raise AssertionError(proc.stdout + proc.stderr)
        cls.data = json.loads(proc.stdout)
        cls.rows = {row["line"]: row for row in cls.data["lines"]}

    def test_fresh_listing_matches_cl2000(self):
        self.assertIn({"name": "WD_DISABLE", "value": 1, "line": 66}, self.data["symbols"])
        self.assertEqual((self.rows[82]["assembled"], self.rows[82]["section"],
                          self.rows[82]["address"], self.rows[82]["encoding"]),
                         (True, "codestart", 0, "0040"))
        self.assertFalse(self.rows[84]["assembled"])
        self.assertEqual((self.rows[101]["section"], self.rows[101]["address"],
                          self.rows[101]["encoding"]), (".text", 3, "2829"))

    def test_text_is_source_text_not_listing_columns(self):
        self.assertTrue(self.rows[82]["text"].lstrip().startswith("LB wd_disable"), self.rows[82])
        self.assertTrue(self.rows[101]["text"].lstrip().startswith("MOV @7029h"), self.rows[101])
        self.assertFalse(self.rows[82]["text"].lstrip().startswith("00000000"), self.rows[82])

    def test_command_is_the_command_that_ran(self):
        command = self.data["command"]
        self.assertIn("-c", command)
        self.assertTrue(any(item.startswith("--obj_directory=") for item in command), command)
        self.assertTrue(any(item.startswith("--asm_directory=") for item in command), command)
        self.assertTrue(any(item.endswith("f28p65x_codestartbranch.asm") for item in command), command)

    def test_nonexistent_source_errors_and_names_source(self):
        proc = run(UMC, "--build", UMC_BUILD, "src_device/no_such_file.asm")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no_such_file.asm", proc.stdout + proc.stderr)

    def test_project_is_unchanged(self):
        before = snapshot(UMC)
        proc = run(UMC, "--build", UMC_BUILD, UMC_SOURCE, "--lines", "81-84")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(snapshot(UMC), before)


@unittest.skipUnless((AM261 / "Debug/makefile").is_file(), "AM261 project missing")
class ClangPreprocessedOnly(unittest.TestCase):
    def test_real_project_clang_S_uses_preprocessed_level_read_only(self):
        before = snapshot(AM261)
        with tempfile.TemporaryDirectory(prefix="asm_view_review1_") as tmp:
            source = Path(tmp) / "probe.S"
            source.write_text(
                "#define CODEX_ASM_FLAG 1\n"
                "#if CODEX_ASM_FLAG\n"
                ".text\n"
                ".global codex_asm_probe\n"
                "codex_asm_probe:\n"
                "    nop\n"
                "#endif\n",
                encoding="utf-8",
            )
            proc = run(AM261, "--build", "Debug", source)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
        self.assertEqual(data["level"], "preprocessed_only")
        rows = {row["line"]: row for row in data["lines"]}
        self.assertEqual(rows[3]["text"].strip(), ".text")
        self.assertEqual(rows[6]["text"].strip(), "nop")
        self.assertTrue(all(row["assembled"] is None for row in rows.values()))
        self.assertEqual(snapshot(AM261), before)


if __name__ == "__main__":
    unittest.main()
