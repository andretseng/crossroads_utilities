#!/usr/bin/env python3
"""Acceptance tests for config_calls.py (Claude, 2026-11-19). Do not edit. Values from Claude's own compiler runs
(escape32_boot Release_TIMER, io.c includes): 1 << IO_CC = 1, IO_CC ^ 1 = 1, GPTIMER_CCCTL_01_COC_CAPTURE = 131072,
COC_CAPTURE | ZCOND_CC_TRIG_FALL = 139264."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "config_calls.py"
BOOT = Path("/home/andre/workspace_ccstheia/drone/escape32_boot_lp-am13e230x")
DAB = Path("/home/andre/workspace_ccstheia/power/dab_F28003x")


def writes(*files):
    proc = subprocess.run([sys.executable, str(TOOL), str(BOOT), "--build", "Release_TIMER", *files, "--json"],
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)["writes"]


@unittest.skipUnless((BOOT / "Release_TIMER/makefile").is_file(), "escape32_boot missing")
class Io(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = {(x["file"].split("/")[-1], x["line"]): x for x in writes("src/io.c")}

    def test_constant_register_writes(self):
        w = self.w[("io.c", 159)]
        self.assertEqual((w["kind"], w["function"], w["index_value"], w["op"], w["value_int"]), ("register", "sendbuf", 1, "=", 131072))
        self.assertEqual(self.w[("io.c", 162)]["value_int"], 1)
        self.assertEqual(self.w[("io.c", 180)]["value_int"], 139264)

    def test_compound_and_conditional(self):
        self.assertEqual(self.w[("io.c", 182)]["op"], "|=")
        self.assertIsNone(self.w[("io.c", 178)]["value_int"])         # p ? LOW : HIGH is not one constant

    def test_register_write_inside_a_branch(self):
        w = self.w[("io.c", 151)]                                    # TIMG4->CPU_INT.ICLR = IO_CCU; inside if (n == 10) at 149
        self.assertEqual(w["function"], "recvbuf")
        self.assertEqual(w["path"][-1], {"if_line": 149, "arm": "then"})

    def test_local_variables_are_not_register_writes(self):
        self.assertNotIn(("io.c", 175), self.w)                        # p = 0;
        self.assertNotIn(("io.c", 172), self.w)                        # b >>= 1;


@unittest.skipUnless((BOOT / "Release_TIMER/makefile").is_file(), "escape32_boot missing")
class Calls(unittest.TestCase):
    def test_driver_call(self):
        w = {x["line"]: x for x in writes("mcu/AM13E230X/config.c")}
        self.assertEqual((w[21]["kind"], w[21]["target"]), ("call", "SYSCFG_DL_initPower"))


@unittest.skipUnless((DAB / "RELEASE/subdir_rules.mk").is_file(), "dab_F28003x missing")
class TiLegacy(unittest.TestCase):
    def test_honest_not_available(self):
        proc = subprocess.run([sys.executable, str(TOOL), str(DAB), "--build", "RELEASE", "dab_main.c", "--json"],
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not available", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
