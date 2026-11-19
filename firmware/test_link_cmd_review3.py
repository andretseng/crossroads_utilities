#!/usr/bin/env python3
"""Claude review 3 of link_facts rules / whatif-cmd (2026-11-19). Do not edit.
Defects found: (1) `rules` with cl2000 `--preproc_only` wrote <name>.pp NEXT TO THE .cmd inside the project (dab, cia402,
echoback) - projects are read-only: send preprocessor output to /tmp (`--pp_directory=<tmp>` / `-o`). (2) whatif-cmd on a
clang-driver link passed `-m<map>` to tiarmclang ("unknown argument") - the driver needs `-Wl,-m...` / `-Wl,--xml_link_info=...`
and the .cmd as `-Wl,-l"<tmp>/x.cmd"` when the makefile passes it that way (GEN_CMDS__FLAG).
Expected MSPM0 result measured by Claude with a manual relink: FLASH length 0x20000 -> 0x10000, used stays 0x2d0, links."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "link_facts.py"
W = Path("/home/andre/workspace_ccstheia")
DAB = W / "power/dab_F28003x"
MSPM0 = W / "mspm0+/gpio_toggle_output_LP_MSPM0G5187_nortos_ticlang"


def snapshot(root):
    return {str(p): p.stat().st_mtime for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args), "--json"], capture_output=True, text=True, timeout=300)


class ReadOnly(unittest.TestCase):
    @unittest.skipUnless((DAB / "RELEASE/makefile").is_file(), "dab missing")
    def test_rules_writes_nothing_into_the_project(self):
        before = snapshot(DAB)
        p = run(DAB, "--build", "RELEASE", "rules")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(snapshot(DAB), before)


class ClangDriverRelink(unittest.TestCase):
    @unittest.skipUnless((MSPM0 / "Debug/device_linker.cmd").is_file(), "MSPM0 missing")
    def test_mspm0_smaller_flash_links(self):
        before = snapshot(MSPM0)
        p = run(MSPM0, "--build", "Debug", "whatif-cmd", "--replace", "length = 0x00020000", "length = 0x00010000")
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        self.assertTrue(d["linked"], d["errors"])
        changes = {(r["name"], r["field"]): (r["old"], r["new"]) for r in d["diff"]["regions"]}
        self.assertEqual(changes[("FLASH", "length")], (0x20000, 0x10000))
        self.assertNotIn(("FLASH", "used"), changes)
        self.assertEqual(snapshot(MSPM0), before)


AM261 = W / "motor/servo_drive_am261x-lp_r5fss0-0"


class GeneratedCmdIsPassedToTheRelink(unittest.TestCase):
    @unittest.skipUnless((AM261 / "Debug/syscfg/linker.cmd").is_file(), "AM261 missing")
    def test_am261_relink_uses_the_copied_cmd(self):
        # Claude's spot check: the AM261 relink printed `warning #10247-D: creating output section ".text" without a SECTIONS
        # specification` = the linker never saw the (copied) syscfg/linker.cmd.
        p = run(AM261, "--build", "Debug", "whatif-cmd", "--replace",
                "R5F_TCMB   : ORIGIN = 0x80000 , LENGTH = 0x20000", "R5F_TCMB   : ORIGIN = 0x80000 , LENGTH = 0x30000")
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        self.assertFalse([e for e in d["errors"] if "#10247" in e], d["errors"])
        self.assertTrue(d["linked"], d["errors"])
        changes = {(r["name"], r["field"]): (r["old"], r["new"]) for r in d["diff"]["regions"]}
        self.assertEqual(changes[("R5F_TCMB", "length")], (0x20000, 0x30000))


if __name__ == "__main__":
    unittest.main()
