#!/usr/bin/env python3
"""Acceptance tests for c_facts.py (Claude, 2026-11-19). Do not edit.
All facts below are read from escape32_boot src/io.c (Release_TIMER), the softuart case where the model kept getting them wrong."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "c_facts.py"
BOOT = Path("/home/andre/workspace_ccstheia/drone/escape32_boot_lp-am13e230x")
DAB = Path("/home/andre/workspace_ccstheia/power/dab_F28003x")


def facts(function):
    proc = subprocess.run([sys.executable, str(TOOL), str(BOOT), "--build", "Release_TIMER", "src/io.c", "--function", function,
                           "--json"], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


@unittest.skipUnless((BOOT / "Release_TIMER/makefile").is_file(), "escape32_boot missing")
class SendBuf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = facts("sendbuf")

    def test_function_range(self):
        self.assertEqual(self.f["function"]["first_line"], 158)

    def test_else_belongs_to_the_else_if(self):   # Fix 35: the else at 173 is the else of `else if (n < 10)` (170), not of `if (b & 1)`
        ifs = {i["line"]: i for i in self.f["ifs"]}
        self.assertEqual(ifs[170]["parent_if_line"], 169)
        self.assertEqual(ifs[169]["else_if_line"], 170)
        self.assertEqual(ifs[170]["else"][0], 173)
        self.assertIsNone(ifs[171]["else"])

    def test_conditional_values(self):            # Fix 23/24: p ? LACT_CCP_LOW : LACT_CCP_HIGH -> 0x10 : 0x08
        c = [c for c in self.f["conditionals"] if c["line"] == 178][0]
        self.assertEqual((c["condition"], c["then"], c["else"]), ("p", "GPTIMER_CCACT_01_LACT_CCP_LOW", "GPTIMER_CCACT_01_LACT_CCP_HIGH"))
        self.assertEqual((c["then_value"], c["else_value"]), (0x10, 0x08))

    def test_assignment_paths(self):              # Fix 24: which branch sets p
        a = {(x["line"], x["var"]): x for x in self.f["assignments"]}
        self.assertEqual(a[(168, "p")]["value_int"], -1)
        self.assertEqual(a[(171, "p")]["path"][-2:], [{"if_line": 170, "arm": "then"}, {"if_line": 171, "arm": "then"}])
        self.assertEqual(a[(175, "p")]["path"][-1], {"if_line": 170, "arm": "else"})
        self.assertEqual((a[(172, "b")]["op"], a[(172, "b")]["value_int"]), (">>=", 1))

    def test_post_increment_in_condition(self):   # Fix 12/31: `if (!n++)` tests n BEFORE the increment
        inc = [i for i in self.f["increments"] if i["line"] == 169][0]
        self.assertEqual((inc["var"], inc["op"], inc["postfix"], inc["in_condition_of"]), ("n", "++", True, 169))


@unittest.skipUnless((BOOT / "Release_TIMER/makefile").is_file(), "escape32_boot missing")
class RecvBuf(unittest.TestCase):
    def test_shift_and_or_and_prefix_decrement(self):   # Fix 15/18 facts: b >>= 1 then b |= 0x80; `if (!--len)` is prefix
        f = facts("recvbuf")
        self.assertEqual((f["function"]["first_line"], f["function"]["last_line"]), (122, 156))
        a = {(x["line"], x["var"]): x for x in f["assignments"]}
        self.assertEqual((a[(138, "b")]["op"], a[(138, "b")]["value_int"]), (">>=", 1))
        self.assertEqual((a[(139, "b")]["op"], a[(139, "b")]["value_int"]), ("|=", 0x80))
        dec = [i for i in f["increments"] if i["line"] == 144][0]
        self.assertEqual((dec["var"], dec["op"], dec["postfix"]), ("len", "--", False))
        post = [i for i in f["increments"] if i["line"] == 132][0]
        self.assertEqual((post["var"], post["postfix"], post["in_condition_of"]), ("n", True, 132))


@unittest.skipUnless((DAB / "RELEASE/subdir_rules.mk").is_file(), "dab_F28003x missing")
class TiLegacy(unittest.TestCase):
    def test_honest_not_available(self):
        proc = subprocess.run([sys.executable, str(TOOL), str(DAB), "--build", "RELEASE", "dab_main.c", "--function", "main", "--json"],
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not available", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
