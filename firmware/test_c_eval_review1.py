#!/usr/bin/env python3
"""Claude review 1 of c_eval (2026-11-19). Do not edit. Found on AM261: an expression that starts with "-" ("-7 / 2") was
silently dropped (results had fewer entries, exit 0) and `--` gave the usage text with exit 0. Rule: every expression given
appears in results, in the given order, or the tool exits 2 naming the one it could not evaluate. C integer division
truncates toward zero: -7 / 2 == -3, -7 % 2 == -1 (measured by Claude with tiarmclang)."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "c_eval.py"
ARMCLANG = Path("/home/andre/ti/ccs2101/ccs/tools/compiler/ti-cgt-armllvm_5.1.1.LTS/bin/tiarmclang")


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), "--compiler", str(ARMCLANG), "--flags", "-mcpu=cortex-m33", *args, "--json"],
                          capture_output=True, text=True, timeout=120)


@unittest.skipUnless(ARMCLANG.is_file(), "tiarmclang missing")
class LeadingMinus(unittest.TestCase):
    def test_every_expression_is_answered_in_order(self):
        exprs = ["-7 / 2", "-7 % 2", "1 + 1"]
        p = run(*exprs)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        got = [(r["expr"], r["value"]) for r in json.loads(p.stdout)["results"]]
        self.assertEqual(got, [("-7 / 2", -3), ("-7 % 2", -1), ("1 + 1", 2)])

    def test_double_dash_separator(self):
        p = run("--", "-1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual([r["value"] for r in json.loads(p.stdout)["results"]], [-1])

    def test_no_expression_is_an_error(self):
        p = run()
        self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main()
