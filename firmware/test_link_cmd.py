#!/usr/bin/env python3
"""Acceptance tests for link_facts.py rules / whatif-cmd (written by Claude; do not edit to make them pass)."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "link_facts.py"
BOOT = Path("/home/andre/workspace_ccstheia/drone/escape32_boot_lp-am13e230x")
CMD = BOOT / "linker" / "boot_ticlang.cmd"


def run(*args):
    proc = subprocess.run([sys.executable, str(TOOL), *map(str, args), "--json"], capture_output=True, text=True, timeout=300)
    return proc.returncode, proc.stdout, proc.stderr


def snapshot():
    return {p: p.stat().st_mtime for p in BOOT.rglob("*") if p.is_file() and ".git" not in p.parts}


@unittest.skipUnless((BOOT / "Release_TIMER" / "makefile").is_file(), "escape32_boot not present")
class Rules(unittest.TestCase):
    def test_memory_symbols_sections_with_lines(self):
        code, out, err = run(BOOT, "--build", "Release_TIMER", "rules")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(any(p.endswith("linker/boot_ticlang.cmd") for p in data["cmd_files"]))
        mem = {m["name"]: (m["origin"], m["length"], m["line"]) for m in data["memory"]}
        self.assertEqual(mem["FLASH_BOOT"], (0x0, 0x1000, 25))
        self.assertEqual(mem["RAM_C"], (0xC18000, 0x8000, 27))
        sym = {s["name"]: (s["value"], s["line"]) for s in data["symbols"]}
        self.assertEqual(sym["_rom_end"], (0x1000, 31))
        sec = {s["name"]: s for s in data["sections"]}
        self.assertEqual((sec[".TI.ramfunc"]["load"], sec[".TI.ramfunc"]["run"], sec[".TI.ramfunc"]["line"]), ("FLASH_BOOT", "RAM_C", 46))
        self.assertEqual((sec[".text"]["load"], sec[".text"]["run"]), ("FLASH_BOOT", "FLASH_BOOT"))
        self.assertEqual(sec[".bss"]["run"], "RAM_BOOT")

    def test_never_built_build_gives_rules_only(self):
        code, out, err = run(BOOT, "--build", "Release_PA26", "rules")
        self.assertEqual(code, 0, err)                     # rules need no build; facts from the link result do
        data = json.loads(out)
        self.assertEqual(data["basis"], "rules only")
        self.assertIn("FLASH_BOOT", {m["name"] for m in data["memory"]})


@unittest.skipUnless((BOOT / "Release_TIMER" / "src" / "main.o").is_file(), "escape32_boot Release_TIMER objects not present")
class WhatIf(unittest.TestCase):
    def test_too_small_flash_reports_linker_error_verbatim(self):
        before = snapshot()
        old = "FLASH_BOOT  : ORIGIN = 0x00000000, LENGTH = 0x00001000"
        new = "FLASH_BOOT  : ORIGIN = 0x00000000, LENGTH = 0x00000800"
        code, out, err = run(BOOT, "--build", "Release_TIMER", "whatif-cmd", "--replace", old, new)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertFalse(data["linked"])
        self.assertTrue(any("#10099" in line and ".text" in line for line in data["errors"]), data["errors"])
        self.assertEqual(snapshot(), before)               # the project is never touched

    def test_bigger_flash_links_and_diff_shows_only_the_change(self):
        old = "FLASH_BOOT  : ORIGIN = 0x00000000, LENGTH = 0x00001000"
        new = "FLASH_BOOT  : ORIGIN = 0x00000000, LENGTH = 0x00002000"
        code, out, err = run(BOOT, "--build", "Release_TIMER", "whatif-cmd", "--replace", old, new)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(data["linked"], data["errors"])
        changes = {(r["name"], r["field"]): (r["old"], r["new"]) for r in data["diff"]["regions"]}
        self.assertEqual(changes[("FLASH_BOOT", "length")], (0x1000, 0x2000))
        self.assertNotIn(("FLASH_BOOT", "used"), changes)  # same code, same use

    def test_replace_text_must_match_exactly_once(self):
        code, out, err = run(BOOT, "--build", "Release_TIMER", "whatif-cmd", "--replace", "LENGTH = 0x00001000", "LENGTH = 0x2")
        self.assertEqual(code, 2)                          # occurs twice (FLASH_BOOT and RAM_BOOT)
        self.assertIn("2", out + err)


if __name__ == "__main__":
    unittest.main()
