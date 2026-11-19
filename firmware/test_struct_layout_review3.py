#!/usr/bin/env python3
"""Claude review 3 (2026-11-19). Do not edit. C2000 project mode: cl2000 recipes carry make leftovers and side-output flags
(`--preproc_dependency=$(basename $(<F)).d_raw`, `$(GEN_OPTS__FLAG)`, `$(shell echo $<)`, `--preproc_with_compile`,
`--asm_listing`, `-k`) that broke the probe compile ("cannot open preprocessing output file device/$(basename").
Expected layout measured by Claude with the build's own cl2000 22.6.2 flags and DWARF."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
import project_facts

TOOL = Path(__file__).resolve().parent / "struct_layout.py"
DAB = Path("/home/andre/workspace_ccstheia/power/dab_F28003x")


@unittest.skipUnless((DAB / "RELEASE" / "subdir_rules.mk").is_file(), "dab_F28003x not present")
class C2000ProjectMode(unittest.TestCase):
    def test_compile_command_has_no_make_leftovers_or_side_outputs(self):
        compiler, args = project_facts.compile_command(DAB, "RELEASE")
        self.assertTrue(compiler.endswith("bin/cl2000"))
        self.assertFalse([a for a in args if "$(" in a or a.startswith("$")])
        for gone in ("--preproc_with_compile", "--asm_listing", "-k"):
            self.assertNotIn(gone, args)
        self.assertFalse([a for a in args if a.startswith("--preproc_dependency")])
        self.assertIn("-v28", args)
        self.assertIn("--include_path=/home/andre/workspace_ccstheia/power/dab_F28003x/libraries/DCL", args)

    def test_dcl_pi_cla_word_layout(self):
        proc = subprocess.run([sys.executable, str(TOOL), str(DAB), "--build", "RELEASE", "--type", "DCL_PI_CLA",
                               "--include", "DCLCLA.h", "--json"], capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual((data["unit"], data["size"]), ("word16", 0x12))
        names = ["Kp", "Ki", "i10", "Umax", "Umin", "i6", "i11", "Imax", "Imin"]
        self.assertEqual([(m["name"], m["offset"]) for m in data["members"]], [(n, 2 * i) for i, n in enumerate(names)])


if __name__ == "__main__":
    unittest.main()
