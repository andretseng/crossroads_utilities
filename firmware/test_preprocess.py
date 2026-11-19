#!/usr/bin/env python3
"""Acceptance tests for preprocess.py (Claude, 2026-11-19). Do not edit. Expected values verified by Claude with the real compilers."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "preprocess.py"
BOOT = Path("/home/andre/workspace_ccstheia/drone/escape32_boot_lp-am13e230x")
SDK = Path("/home/andre/ti/freertos_sdk_am275x_12_01_00_28")
RM = SDK / "source/drivers/device_manager/rm_pm_hal"
PSC = RM / "rm_pm_hal_src/pm/drivers/psc/psc.c"
DAB = Path("/home/andre/workspace_ccstheia/power/dab_F28003x")


def run(*args, timeout=300):
    proc = subprocess.run([sys.executable, str(TOOL), *map(str, args), "--json"], capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def lines(out):
    data = json.loads(out)
    return data, {row["line"]: row for row in data["lines"]}


@unittest.skipUnless((BOOT / "Release_TIMER/makefile").is_file(), "escape32_boot missing")
class Build(unittest.TestCase):
    def test_io_c_178_expanded(self):
        code, out, err = run(BOOT, "--build", "Release_TIMER", "src/io.c", "--lines", "178-178")
        self.assertEqual(code, 0, err)
        data, rows = lines(out)
        self.assertEqual(data["level"], "build")
        self.assertTrue(rows[178]["compiled"])
        self.assertEqual(rows[178]["expanded"],
                         "TIMG4->COUNTERREGS.CCACT_01[0] = p ? ((uint32_t)0x00000010U) : ((uint32_t)0x00000008U);")

    def test_header_define_branch_by_resolved_path(self):
        # mcu/AM13E230X/config.h: line 51 "#define IO_CC 1" (under #ifdef IO_PIN_PA27) is NOT compiled in Release_TIMER,
        # line 54 "#define IO_CC 0" is. Other headers ending in config.h must not be confused with it.
        code, out, err = run(BOOT, "--build", "Release_TIMER", "mcu/AM13E230X/config.h", "--lines", "49-55")
        self.assertEqual(code, 0, err)
        data, rows = lines(out)
        self.assertFalse(rows[51]["compiled"])
        self.assertTrue(rows[54]["compiled"])

    def test_stale_build_stops(self):
        code, out, err = run(BOOT, "--build", "Debug", "src/io.c", "--lines", "178-178")
        self.assertEqual(code, 3)
        self.assertIn("ASK USER", out + err)


@unittest.skipUnless((RM / "makefile.am275x.wkup-r5f.ti-arm-clang").is_file(), "AM275x SDK missing")
class SdkMake(unittest.TestCase):
    def test_errata_block_not_compiled_in_release(self):
        code, out, err = run("--sdk-make", RM / "makefile.am275x.wkup-r5f.ti-arm-clang", "--make-var", f"MCU_PLUS_SDK_PATH={SDK}",
                             "--make-var", "PROFILE=release", PSC, "--lines", "151-156")
        self.assertEqual(code, 0, err)
        data, rows = lines(out)
        self.assertEqual(data["level"], "sdk_make")
        self.assertFalse(rows[153]["compiled"])      # under #ifdef CONFIG_PSC_PTSTAT_ERRATA, not defined for this library
        self.assertTrue(rows[156]["compiled"])       # static u32 psc_read(...)
        self.assertTrue(any("-DCONFIG_PSC" == a for a in data["command"]))


class Partial(unittest.TestCase):
    def test_known_and_unknown_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "x.c"
            f.write_text("#ifdef A\nint a;\n#else\nint not_a;\n#endif\n#if B > 1\nint b;\n#endif\nint always;\n")
            code, out, err = run("--partial", f, "--define", "A")
            self.assertEqual(code, 0, err)
            data, rows = lines(out)
            self.assertEqual(data["level"], "partial")
            self.assertEqual((rows[2]["compiled"], rows[4]["compiled"], rows[7]["compiled"], rows[9]["compiled"]),
                             (True, False, None, True))
            self.assertEqual(data["unknown_symbols"], ["B"])


@unittest.skipUnless((DAB / "RELEASE/subdir_rules.mk").is_file(), "dab_F28003x missing")
class TiLegacy(unittest.TestCase):
    def test_cl2000_build_level_runs(self):
        # dab_main.c:158 "#if DAB_SFRA_TYPE == DAB_SFRA_DISABLED" ... #else (161) DAB_setupSFRA(); (162): Claude's cl2000
        # --preproc_with_line run shows line 162 compiled, so the #else branch is active in RELEASE.
        code, out, err = run(DAB, "--build", "RELEASE", "dab_main.c", "--lines", "158-163")
        self.assertEqual(code, 0, err)
        data, rows = lines(out)
        self.assertEqual(data["level"], "build")
        self.assertTrue(rows[162]["compiled"])
        self.assertEqual(rows[162]["expanded"], "DAB_setupSFRA();")


if __name__ == "__main__":
    unittest.main()
