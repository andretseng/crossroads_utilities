#!/usr/bin/env python3
"""Claude review 4 of struct_layout (2026-11-19). Do not edit.
Found on real projects: (a) a type name that does not exist returned ANOTHER struct's layout (MSPM0: IOMUX_Regs) - a silent
wrong answer; (b) `typedef struct Tag_ {...} Name;` gave "record layout not found" (AM261 HwiP_Params).
Hint (verified by Claude): probe `struct __probe_wrap { NAME v; };` - the compiler then fails on a missing type, and clang prints
the wrapped record inline under `v`; cl2000 DWARF: DW_TAG_typedef NAME -> DW_AT_type -> the struct.
Expected values: tiarmclang 4.0.3 with the AM261 Debug compile command -> intNum 0, callback 4, args 8, eventId 12,
priority 14, isFIQ 15, isPulse 16, sizeof 20."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "struct_layout.py"
ARMCLANG = Path("/home/andre/ti/ccs2101/ccs/tools/compiler/ti-cgt-armllvm_5.1.1.LTS/bin/tiarmclang")
CL2000 = Path("/home/andre/ti/ti-cgt-c2000_22.6.2.LTS/bin/cl2000")
MSPM0 = Path("/home/andre/workspace_ccstheia/mspm0+/gpio_toggle_output_LP_MSPM0G5187_nortos_ticlang")
AM261 = Path("/home/andre/workspace_ccstheia/motor/servo_drive_am261x-lp_r5fss0-0")
TAGGED = "#include <stdint.h>\ntypedef struct Tag_ { uint16_t a; int32_t b; } Named;\nstruct Other { uint32_t x; uint32_t y; };\n"


def run(*args):
    proc = subprocess.run([sys.executable, str(TOOL), *map(str, args), "--json"], capture_output=True, text=True, timeout=300)
    return proc.returncode, proc.stdout, proc.stderr


class Tagged(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.h = Path(self.dir.name) / "t.h"
        self.h.write_text(TAGGED)

    def tearDown(self):
        self.dir.cleanup()

    @unittest.skipUnless(ARMCLANG.is_file(), "tiarmclang missing")
    def test_clang_tagged_typedef(self):
        code, out, err = run("--compiler", ARMCLANG, "--flags", "-mcpu=cortex-m33", "--type", "Named", "--include", self.h)
        self.assertEqual(code, 0, err)
        self.assertEqual([(m["name"], m["offset"]) for m in json.loads(out)["members"]], [("a", 0), ("b", 4)])

    @unittest.skipUnless(CL2000.is_file(), "cl2000 missing")
    def test_cl2000_tagged_typedef(self):
        flags = f"-v28 --abi=eabi --include_path={CL2000.parent.parent / 'include'}"
        code, out, err = run("--compiler", CL2000, "--flags", flags, "--type", "Named", "--include", self.h)
        self.assertEqual(code, 0, err)
        self.assertEqual([(m["name"], m["offset"]) for m in json.loads(out)["members"]], [("a", 0), ("b", 2)])

    @unittest.skipUnless(ARMCLANG.is_file(), "tiarmclang missing")
    def test_missing_type_never_falls_back_to_another_record(self):
        code, out, err = run("--compiler", ARMCLANG, "--flags", "-mcpu=cortex-m33", "--type", "NoSuch", "--include", self.h)
        self.assertEqual(code, 2)
        self.assertNotIn('"x"', out)                       # struct Other must not be returned


@unittest.skipUnless((MSPM0 / "Debug" / "subdir_rules.mk").is_file(), "MSPM0 project missing")
class RealMSPM0(unittest.TestCase):
    def test_missing_type_in_a_big_header_is_an_error(self):
        code, out, err = run(MSPM0, "--build", "Debug", "--type", "DL_GPIO_noSuchConfigType", "--include", "ti/driverlib/dl_gpio.h")
        self.assertEqual(code, 2, out[:300])
        self.assertNotIn("IOMUX_Regs", out)


@unittest.skipUnless((AM261 / "Debug" / "subdir_rules.mk").is_file(), "AM261 project missing")
class RealAM261(unittest.TestCase):
    def test_hwip_params(self):
        code, out, err = run(AM261, "--build", "Debug", "--type", "HwiP_Params", "--include", "kernel/dpl/HwiP.h")
        self.assertEqual(code, 0, out + err)
        data = json.loads(out)
        self.assertEqual(data["size"], 20)
        self.assertEqual([(m["name"], m["offset"]) for m in data["members"]],
                         [("intNum", 0), ("callback", 4), ("args", 8), ("eventId", 12), ("priority", 14), ("isFIQ", 15), ("isPulse", 16)])


if __name__ == "__main__":
    unittest.main()
