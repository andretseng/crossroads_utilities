#!/usr/bin/env python3
"""Codex review 1 for c_facts (2026-11-19). Do not edit.

Expected ranges and AST facts were measured independently with the AM261 build's own tiarmclang
(`-fsyntax-only -Xclang -ast-dump=json -Xclang -ast-dump-filter=focLoop`). This project is absent
from test_c_facts.py. Ranges are the compiler AST statement-node ranges, including compound braces,
consistent with test_c_facts.py expecting the line-170 else range to begin on its brace at line 173.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "c_facts.py"
AM261 = Path("/home/andre/workspace_ccstheia/motor/servo_drive_am261x-lp_r5fss0-0")


def run(function="focLoop"):
    return subprocess.run(
        [sys.executable, str(TOOL), str(AM261), "--build", "Debug", "servo_drive_main.c",
         "--function", function, "--json"],
        capture_output=True, text=True, timeout=300,
    )


def snapshot(root):
    return {str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


@unittest.skipUnless((AM261 / "Debug/makefile").is_file(), "AM261 project missing")
class UnseenAm261(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proc = run()
        if proc.returncode != 0:
            raise AssertionError(proc.stdout + proc.stderr)
        cls.facts = json.loads(proc.stdout)

    def test_statement_ranges_match_compiler_ast(self):
        ifs = {item["line"]: item for item in self.facts["ifs"]}
        self.assertEqual(ifs[259]["then"], [259, 262])
        self.assertEqual(ifs[263]["then"], [263, 270])
        self.assertEqual(ifs[270]["then"], [270, 276])
        self.assertEqual(ifs[276]["then"], [276, 283])
        self.assertEqual(ifs[336]["then"], [336, 344])
        self.assertEqual(ifs[337]["else"], [340, 343])
        for item in self.facts["ifs"]:
            self.assertLessEqual(item["then"][0], item["then"][1], item)
            if item["else"] is not None:
                self.assertLessEqual(item["else"][0], item["else"][1], item)

    def test_ternary_target(self):
        conditional = next(item for item in self.facts["conditionals"] if item["line"] == 372)
        self.assertEqual(conditional["target"], "motor1.dcBus_V")

    def test_nonexistent_function_errors_without_fallback(self):
        proc = run("definitely_missing_function")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("definitely_missing_function", proc.stdout + proc.stderr)

    def test_project_is_unchanged(self):
        before = snapshot(AM261)
        proc = run()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(snapshot(AM261), before)


if __name__ == "__main__":
    unittest.main()
