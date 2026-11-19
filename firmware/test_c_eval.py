#!/usr/bin/env python3
"""Acceptance tests for c_eval.py (Claude, 2026-11-19). Do not edit. Values measured by Claude with the builds' own compilers."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "c_eval.py"
BOOT = Path("/home/andre/workspace_ccstheia/drone/escape32_boot_lp-am13e230x")
UMC = Path("/home/andre/workspace_ccstheia/motor/universal_motorcontrol_lab_f28p65x")


def run(*args):
    proc = subprocess.run([sys.executable, str(TOOL), *map(str, args), "--json"], capture_output=True, text=True, timeout=300)
    return proc.returncode, proc.stdout, proc.stderr


def values(out):
    return {r["expr"]: r["value"] for r in json.loads(out)["results"]}


@unittest.skipUnless((BOOT / "Release_TIMER/makefile").is_file(), "escape32_boot missing")
class Arm(unittest.TestCase):
    def test_macro_and_c_integer_division(self):
        code, out, err = run(BOOT, "--build", "Release_TIMER", "--source", "src/io.c", "--include", "config.h",
                             "IO_CC ^ 1", "(32000000 + (38400 >> 1)) / 38400")
        self.assertEqual(code, 0, err)
        self.assertEqual(values(out), {"IO_CC ^ 1": 1, "(32000000 + (38400 >> 1)) / 38400": 833})   # not 833.83

    def test_not_a_constant_is_an_error(self):
        code, out, err = run(BOOT, "--build", "Release_TIMER", "--source", "src/io.c", "--include", "config.h", "p + 1")
        self.assertEqual(code, 2)

    def test_stale_build_stops(self):
        code, out, err = run(BOOT, "--build", "Debug", "--include", "config.h", "IO_CC")
        self.assertEqual(code, 3)
        self.assertIn("ASK USER", out + err)


@unittest.skipUnless((UMC / "Flash_lib_3phGaN_3SC/makefile").is_file(), "universal_motorcontrol_lab_f28p65x missing")
class C28(unittest.TestCase):
    def test_target_types(self):
        exprs = ["(uint16_t)(USER_M1_ISR_FREQ_Hz * 2.0f)", "sizeof(int)", "(int)40000"]
        code, out, err = run(UMC, "--build", "Flash_lib_3phGaN_3SC", "--include", "user_mtr1.h", *exprs)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["unit"], "word16")
        self.assertEqual(values(out), {exprs[0]: 30000, exprs[1]: 1, exprs[2]: -25536})   # 16-bit int wraps on C28


if __name__ == "__main__":
    unittest.main()
