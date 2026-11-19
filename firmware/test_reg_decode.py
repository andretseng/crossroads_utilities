#!/usr/bin/env python3
"""Acceptance tests for reg_decode.py (Claude, 2026-11-19). Do not edit.
Macros read by Claude from the builds' own macro dumps: GPTIMER_CCCTL_01_COC_MASK 0x20000 OFS 17, _COC_CAPTURE 0x20000;
ZCOND_MASK 0x7000 OFS 12, _ZCOND_CC_TRIG_FALL 0x2000. C2000 hw_epwm.h: EPWM_TBCTL_CTRMODE_M 0x3 _S 0, EPWM_TBCTL_PRDLD 0x8,
HSPCLKDIV_M 0x380 _S 7, CLKDIV_M 0x1C00 _S 10."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "reg_decode.py"
BOOT = Path("/home/andre/workspace_ccstheia/drone/escape32_boot_lp-am13e230x")
DAB = Path("/home/andre/workspace_ccstheia/power/dab_F28003x")


def run(*args):
    proc = subprocess.run([sys.executable, str(TOOL), *map(str, args), "--json"], capture_output=True, text=True, timeout=300)
    return proc.returncode, proc.stdout, proc.stderr


@unittest.skipUnless((BOOT / "Release_TIMER/makefile").is_file(), "escape32_boot missing")
class Am13(unittest.TestCase):
    def test_ccctl_capture_zero_on_fall(self):   # io.c:180 writes COC_CAPTURE | ZCOND_CC_TRIG_FALL = 139264
        code, out, err = run(BOOT, "--build", "Release_TIMER", "--source", "src/io.c", "--include", "common.h",
                             "--register", "GPTIMER_CCCTL_01", "--value", "139264")
        self.assertEqual(code, 0, err)
        d = json.loads(out)
        self.assertEqual(d["style"], "mask_ofs")
        f = {x["field"]: x for x in d["fields"]}
        self.assertEqual((f["COC"]["value"], f["COC"]["name"]), (1, "CAPTURE"))
        self.assertEqual((f["ZCOND"]["value"], f["ZCOND"]["mask"], f["ZCOND"]["name"]), (2, 0x7000, "CC_TRIG_FALL"))
        self.assertEqual(d["unassigned_bits"], 0)

    def test_unknown_register(self):
        code, out, err = run(BOOT, "--build", "Release_TIMER", "--source", "src/io.c", "--include", "common.h",
                             "--register", "NO_SUCH_REG", "--value", "1")
        self.assertEqual(code, 2)


@unittest.skipUnless((DAB / "RELEASE/subdir_rules.mk").is_file(), "dab_F28003x missing")
class C2000(unittest.TestCase):
    def test_tbctl_m_s_style_and_single_bit(self):
        code, out, err = run(DAB, "--build", "RELEASE", "--include", "hw_epwm.h", "--register", "EPWM_TBCTL", "--value", "0x40B")
        self.assertEqual(code, 0, err)
        d = json.loads(out)
        self.assertEqual(d["style"], "m_s")
        f = {x["field"]: x["value"] for x in d["fields"]}
        self.assertEqual((f["CTRMODE"], f["PRDLD"], f["HSPCLKDIV"], f["CLKDIV"]), (3, 1, 0, 1))


if __name__ == "__main__":
    unittest.main()
