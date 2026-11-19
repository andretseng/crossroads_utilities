#!/usr/bin/env python3
"""Claude review 1 of link_facts rules (2026-11-19). Do not edit. Real C28 linker file:
dab_F28003x device/f28003x_flash_lnk.cmd (RELEASE). Defects found: lowercase `origin =`/`length =` not read (0 regions);
`codestart` missing; `.text : >> A | B | C` read as load/run ">"; and a hand-written #if evaluator treated __TI_EABI__ as
undefined, dropping the EABI block - cl2000 --abi=eabi defines __TI_EABI__. Expand the .cmd with the build compiler's own
preprocessor (clang: -E -P -x c; cl2000: --preproc_only, same --define/--abi flags as the build) - no hand-written #if logic."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "link_facts.py"
DAB = Path("/home/andre/workspace_ccstheia/power/dab_F28003x")


@unittest.skipUnless((DAB / "RELEASE/makefile").is_file(), "dab_F28003x missing")
class C28Rules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p = subprocess.run([sys.executable, str(TOOL), str(DAB), "--build", "RELEASE", "rules", "--json"],
                           capture_output=True, text=True, timeout=300)
        assert p.returncode == 0, p.stdout + p.stderr
        cls.d = json.loads(p.stdout)
        cls.mem = {m["name"]: m for m in cls.d["memory"]}
        cls.sec = {s["name"]: s for s in cls.d["sections"]}

    def test_lowercase_memory(self):
        self.assertEqual((self.mem["BEGIN"]["origin"], self.mem["BEGIN"]["length"], self.mem["BEGIN"]["line"]), (0x80000, 2, 3))
        self.assertEqual((self.mem["FLASH_BANK0_SEC1"]["origin"], self.mem["FLASH_BANK0_SEC1"]["line"]), (0x81000, 29))
        self.assertNotIn("RAMGS3_RSVD", self.mem)                         # commented out on line 19

    def test_sections_incl_split_list(self):
        self.assertEqual((self.sec["codestart"]["run"], self.sec["codestart"]["line"]), ("BEGIN", 86))
        self.assertEqual(self.sec[".text"]["run"], "FLASH_BANK0_SEC2 | FLASH_BANK0_SEC3 | FLASH_BANK0_SEC4")
        self.assertEqual(self.sec[".stack"]["run"], "RAMM1")

    def test_eabi_block_kept(self):                                         # #if defined(__TI_EABI__) at line 94 is true
        self.assertEqual((self.sec[".init_array"]["run"], self.sec[".init_array"]["line"]), ("FLASH_BANK0_SEC1", 95))
        self.assertEqual(self.sec[".bss"]["run"], "RAMGS3")

    def test_no_hand_written_preprocessor(self):
        source = TOOL.read_text()
        self.assertNotIn("def _preprocess_cmd", source)
        self.assertNotIn("escape32", source)


if __name__ == "__main__":
    unittest.main()
