#!/usr/bin/env python3
"""Acceptance tests for struct_layout.py (written by Claude before implementation; do not edit to make them pass).
Expected values were measured by Claude on 2026-11-19 with the real compilers (see SPEC_struct_layout.md)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "struct_layout.py"
ARMCLANG = Path("/home/andre/ti/ccs2101/ccs/tools/compiler/ti-cgt-armllvm_5.1.1.LTS/bin/tiarmclang")
CL2000 = Path("/home/andre/ti/ti-cgt-c2000_22.6.2.LTS/bin/cl2000")
CIA = Path("/home/andre/workspace_ccstheia/industrial_comm/f28p65x_cpu1_cia402_solution")
C2000WARE = Path("/home/andre/ti/C2000Ware_26_01_00_00")

HEADER = """#include <stdint.h>
typedef struct { uint16_t a; int32_t b; uint16_t c:3, d:5; } U;
"""


def run(*args):
    proc = subprocess.run([sys.executable, str(TOOL), *map(str, args), "--json"], capture_output=True, text=True, timeout=180)
    return proc.returncode, proc.stdout, proc.stderr


def members(data):
    return {m["name"]: (m["offset"], m["bit_lsb"], m["bit_size"]) for m in data["members"]}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.header = Path(self.dir.name) / "u.h"
        self.header.write_text(HEADER)

    def tearDown(self):
        self.dir.cleanup()

    @unittest.skipUnless(ARMCLANG.is_file(), "tiarmclang missing")
    def test_clang_family_bytes_and_bitfields(self):
        code, out, err = run("--compiler", ARMCLANG, "--flags", "-mcpu=cortex-m33 -mthumb", "--type", "U", "--include", self.header)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual((data["unit"], data["size"]), ("byte", 12))
        self.assertEqual(members(data), {"a": (0, None, None), "b": (4, None, None), "c": (8, 0, 3), "d": (8, 3, 5)})
        self.assertEqual([m["name"] for m in data["members"]], ["a", "b", "c", "d"])

    @unittest.skipUnless(CL2000.is_file(), "cl2000 missing")
    def test_c28x_words_padding_and_bitfields(self):
        flags = f"-v28 --abi=eabi --include_path={CL2000.parent.parent / 'include'}"
        code, out, err = run("--compiler", CL2000, "--flags", flags, "--type", "U", "--include", self.header)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual((data["unit"], data["size"]), ("word16", 6))          # int32 aligned to an even word: 1 word of padding
        self.assertEqual(members(data), {"a": (0, None, None), "b": (2, None, None), "c": (4, 0, 3), "d": (4, 3, 5)})

    @unittest.skipUnless(ARMCLANG.is_file(), "tiarmclang missing")
    def test_compiler_error_is_reported_not_guessed(self):
        code, out, err = run("--compiler", ARMCLANG, "--flags", "-mcpu=cortex-m33", "--type", "NoSuchType", "--include", self.header)
        self.assertEqual(code, 2)
        self.assertIn("NoSuchType", out + err)


@unittest.skipUnless(CL2000.is_file() and (CIA / "cia402appl.h").is_file(), "cia402 project missing")
class RealCia402(unittest.TestCase):
    FLAGS = " ".join([
        "-v28", "--abi=eabi", "--float_support=fpu64",
        f"--include_path={CIA}", f"--include_path={CIA / 'device'}",
        f"--include_path={C2000WARE / 'libraries/communications/Ethercat/f28p65x/examples/f28p65x_cpu1_cia402_solution'}",
        f"--include_path={C2000WARE / 'driverlib/f28p65x/driverlib'}",
        f"--include_path={C2000WARE / 'device_support/f28p65x/common/include'}",
        f"--include_path={C2000WARE / 'libraries/communications/Ethercat/f28p65x/include'}",
        f"--include_path={CL2000.parent.parent / 'include'}",
        "--define=ETHERCAT_STACK", "--define=_FLASH", "--define=CPU1",
    ])

    def test_pdo1600_word_offsets(self):
        code, out, err = run("--compiler", CL2000, "--flags", self.FLAGS, "--type", "TCiA402PDO1600",
                             "--include", CIA / "ecat_def.h", "--include", CIA / "cia402appl.h")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["unit"], "word16")
        got = {m["name"]: m["offset"] for m in data["members"]}
        self.assertEqual(got, {"ObjControlWord": 0, "ObjTargetPosition": 2, "ObjTargetVelocity": 4, "ObjModesOfOperation": 6})

    def test_project_mode_stops_on_stale_build(self):
        code, out, err = run(CIA, "--build", "FLASH", "--type", "TCiA402PDO1600", "--include", "cia402appl.h")
        self.assertEqual(code, 3)                                              # FLASH is older than .cproject
        self.assertIn("ASK USER", out + err)


if __name__ == "__main__":
    unittest.main()
