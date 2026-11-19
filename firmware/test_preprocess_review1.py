#!/usr/bin/env python3
"""Claude review 1 of preprocess (2026-11-19). Do not edit.
Defects found: (1) comment lines inside the ACTIVE branch were reported compiled=false (io.c:40-43, Release_TIMER compiles the
#else branch 34-...), which reads as "this code is not built" - wrong and misleading. Keep comments in the output
(clang `-C`, cl2000 `--preproc_with_comment`) so comment lines are located like code. Rules: compiled=false ONLY for lines of
an inactive #if branch; lines of an active region are true (comment lines too); blank lines may be null; the directive lines
(#if/#ifdef/#elif/#else/#endif) are null. (2) The tool rewrote dab_F28003x/RELEASE/dab_main.obj (15:40): cl2000 ran a full
compile in the build folder. Projects are read-only: preprocess only (`--preproc_only`), all outputs to /tmp."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "preprocess.py"
W = Path("/home/andre/workspace_ccstheia")
BOOT = W / "drone/escape32_boot_lp-am13e230x"
MSPM0 = W / "mspm0+/gpio_toggle_output_LP_MSPM0G5187_nortos_ticlang"
DAB = W / "power/dab_F28003x"


def rows(*args):
    p = subprocess.run([sys.executable, str(TOOL), *map(str, args), "--json"], capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    return {r["line"]: r["compiled"] for r in json.loads(p.stdout)["lines"]}


def snapshot(root):
    return {str(p): p.stat().st_mtime for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}


class Semantics(unittest.TestCase):
    @unittest.skipUnless((BOOT / "Release_TIMER/makefile").is_file(), "escape32_boot missing")
    def test_branches_comments_directives(self):
        r = rows(BOOT, "--build", "Release_TIMER", "src/io.c", "--lines", "22-50")
        self.assertFalse(r[24])            # #ifdef IO_UC4 branch: not built
        self.assertFalse(r[30])            # #elif defined IO_PA2 branch: not built
        self.assertTrue(r[35])             # #else branch: built
        for line in (37, 40, 41, 42, 43, 47):
            self.assertTrue(r[line], f"comment line {line} is inside the built #else branch")
        for line in (23, 29, 34):
            self.assertIsNone(r[line], f"directive line {line}")

    @unittest.skipUnless((MSPM0 / "Debug/subdir_rules.mk").is_file(), "MSPM0 missing")
    def test_no_false_without_an_inactive_branch(self):
        r = rows(MSPM0, "--build", "Debug", "gpio_toggle_output.c", "--lines", "40-60")
        self.assertFalse([line for line, c in r.items() if c is False], "this range has no #if at all")
        for line in (41, 43, 48, 56, 57, 60):
            self.assertTrue(r[line])


class ReadOnly(unittest.TestCase):
    @unittest.skipUnless((DAB / "RELEASE/subdir_rules.mk").is_file(), "dab missing")
    def test_cl2000_writes_nothing_into_the_project(self):
        before = snapshot(DAB)
        rows(DAB, "--build", "RELEASE", "dab_main.c", "--lines", "158-163")
        self.assertEqual(snapshot(DAB), before)


if __name__ == "__main__":
    unittest.main()
