#!/usr/bin/env python3
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "config_calls.py"
PROJECT = Path("/home/andre/workspace_ccstheia/motor/servo_drive_am261x-lp_r5fss0-0")

class RealProjectReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p = subprocess.run([sys.executable, str(TOOL), str(PROJECT), "--build", "Debug", "servo_drive_main.c", "--json"], capture_output=True, text=True, timeout=600)
        cls.code, cls.out, cls.err = p.returncode, p.stdout, p.stderr
        if cls.code != 0:
            raise AssertionError(cls.out + cls.err)
        cls.writes = json.loads(cls.out)["writes"]

    def test_plain_assignments_are_not_register_writes(self):
        bad = {(x["line"], x["target"], x["kind"]) for x in self.writes if x["line"] in {712, 713, 714, 715, 811}}
        self.assertEqual(bad, set(), "plain non-volatile assignments were reported: %r" % (bad,))

    def test_real_driver_calls_remain(self):
        calls = {(x["line"], x["target"]) for x in self.writes if x["kind"] == "call"}
        self.assertIn((698, "EPWM_setCounterCompareValue"), calls)
        self.assertIn((667, "GPIO_pinWriteHigh"), calls)

if __name__ == "__main__":
    unittest.main()
