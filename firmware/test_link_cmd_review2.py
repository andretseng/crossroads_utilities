#!/usr/bin/env python3
"""Claude review 2 of link_facts rules (2026-11-19). Do not edit. SysConfig-generated linker files come in through
GEN_CMDS__FLAG in <BUILD>/makefile: MSPM0 `-Wl,-l"./device_linker.cmd"` (relative to the build folder), AM261
`-Wl,-l"syscfg/linker.cmd"`. Round 2 found no .cmd for these built builds and reported "rules only".
Formats seen: `FLASH (RX) : origin = 0x00000000, length = 0x00020000` (attributes in parentheses),
`.text : palign(8) {} > FLASH`, `R5F_VECS : ORIGIN = 0x0 , LENGTH = 0x40`, multi-line `.vectors : { ... } > R5F_VECS`."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "link_facts.py"
W = Path("/home/andre/workspace_ccstheia")
MSPM0 = W / "mspm0+/gpio_toggle_output_LP_MSPM0G5187_nortos_ticlang"
AM261 = W / "motor/servo_drive_am261x-lp_r5fss0-0"


def rules(project, build):
    p = subprocess.run([sys.executable, str(TOOL), str(project), "--build", build, "rules", "--json"],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout)


class GeneratedCmd(unittest.TestCase):
    @unittest.skipUnless((MSPM0 / "Debug/device_linker.cmd").is_file(), "MSPM0 missing")
    def test_mspm0(self):
        d = rules(MSPM0, "Debug")
        self.assertEqual(d["basis"], "rules")
        self.assertTrue(any(p.endswith("Debug/device_linker.cmd") for p in d["cmd_files"]))
        mem = {m["name"]: m for m in d["memory"]}
        self.assertEqual((mem["FLASH"]["origin"], mem["FLASH"]["length"], mem["FLASH"]["line"]), (0, 0x20000, 40))
        self.assertEqual(mem["SRAM"]["origin"], 0x20200000)
        sec = {s["name"]: s for s in d["sections"]}
        self.assertEqual((sec[".text"]["run"], sec[".text"]["line"]), ("FLASH", 50))

    @unittest.skipUnless((AM261 / "Debug/syscfg/linker.cmd").is_file(), "AM261 missing")
    def test_am261(self):
        d = rules(AM261, "Debug")
        self.assertEqual(d["basis"], "rules")
        self.assertTrue(any(p.endswith("Debug/syscfg/linker.cmd") for p in d["cmd_files"]))
        mem = {m["name"]: m for m in d["memory"]}
        self.assertEqual((mem["R5F_TCMB"]["origin"], mem["R5F_TCMB"]["length"], mem["R5F_TCMB"]["line"]), (0x80000, 0x20000, 164))
        sec = {s["name"]: s for s in d["sections"]}
        self.assertEqual((sec[".vectors"]["run"], sec[".vectors"]["line"]), ("R5F_VECS", 42))


if __name__ == "__main__":
    unittest.main()
