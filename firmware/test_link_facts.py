#!/usr/bin/env python3
"""Acceptance tests for link_facts.py (written by Claude before implementation; do not edit to make them pass)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "link_facts.py"
BOOT = Path("/home/andre/workspace_ccstheia/drone/escape32_boot_lp-am13e230x")
DAB = Path("/home/andre/workspace_ccstheia/power/dab_F28003x")

XML = """<?xml version="1.0"?>
<link_info>
  <input_file_list>
    <input_file id="fl-1"><path>./</path><kind>object</kind><file>a.o</file><name>a.o</name></input_file>
    <input_file id="fl-2"><path>./</path><kind>archive</kind><file>lib.a</file><name>b.o</name></input_file>
  </input_file_list>
  <object_component_list>
    <object_component id="oc-1"><name>.text.f</name><load_address>0x100</load_address><run_address>0x100</run_address><size>0x40</size><input_file_ref idref="fl-1"/></object_component>
    <object_component id="oc-2"><name>.text.g</name><load_address>0x120</load_address><run_address>0x120</run_address><size>0x40</size><input_file_ref idref="fl-2"/></object_component>
    <object_component id="oc-3"><name>.data</name><load_address>0x300</load_address><run_address>0x2000</run_address><size>0x10</size><input_file_ref idref="fl-1"/></object_component>
  </object_component_list>
  <placement_map>
    <memory_area><name>FLASH</name><page_id>0x0</page_id><origin>0x0</origin><length>0x400</length><used_space>0x190</used_space><unused_space>0x270</unused_space></memory_area>
    <memory_area><name>RAM</name><page_id>0x0</page_id><origin>0x2000</origin><length>0x8</length><used_space>0x10</used_space><unused_space>0x0</unused_space></memory_area>
  </placement_map>
  <symbol_table>
    <symbol><name>f</name><value>0x101</value></symbol>
    <symbol><name>g</name><value>0x120</value></symbol>
  </symbol_table>
  <entry_point><name>reset</name><address>0x101</address></entry_point>
</link_info>
"""


def run(*args):
    proc = subprocess.run([sys.executable, str(TOOL), *map(str, args), "--json"], capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.xml = Path(self.dir.name) / "x_linkInfo.xml"
        self.xml.write_text(XML)

    def tearDown(self):
        self.dir.cleanup()

    def test_summary_regions_and_entry(self):
        code, out, err = run("--xml", self.xml, "--unit", "byte", "summary")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["source"], "xml")
        self.assertEqual(data["unit"], "byte")
        self.assertEqual(data["entry"], {"name": "reset", "address": 0x101})
        flash = next(r for r in data["regions"] if r["name"] == "FLASH")
        self.assertEqual((flash["origin"], flash["length"], flash["used"], flash["unused"]), (0, 0x400, 0x190, 0x270))

    def test_symbol_section_by_containing_range(self):
        code, out, err = run("--xml", self.xml, "--unit", "byte", "symbol", "f", "g", "missing")
        self.assertEqual(code, 0, err)
        rows = {row["name"]: row for row in json.loads(out)["symbols"]}
        self.assertEqual(rows["f"]["address"], 0x101)                 # thumb bit kept as the symbol value
        self.assertEqual(rows["f"]["section"], ".text.f")             # 0x101 lies in [0x100, 0x140)
        self.assertEqual(rows["f"]["input_file"], "a.o")
        self.assertEqual(rows["g"]["section"], ".text.g")
        self.assertFalse(rows["missing"]["found"])
        self.assertIn("may be inlined or removed", rows["missing"]["note"])

    def test_overlaps_and_over_full(self):
        code, out, err = run("--xml", self.xml, "--unit", "byte", "overlaps")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        pairs = {frozenset((o["a"], o["b"])) for o in data["overlaps"]}
        self.assertIn(frozenset((".text.f", ".text.g")), pairs)       # 0x100-0x140 and 0x120-0x160 overlap
        self.assertEqual((data["overlaps"][0]["start"], data["overlaps"][0]["end"]), (0x120, 0x140))
        self.assertEqual(data["over_full"], ["RAM"])                  # used 0x10 > length 0x8


@unittest.skipUnless((BOOT / "Release_TIMER" / "makefile").is_file(), "escape32_boot not present")
class RealAM13(unittest.TestCase):
    def test_makefile_names_the_map_and_old_one_is_ignored(self):
        code, out, err = run(BOOT, "--build", "Release_TIMER", "summary")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(data["xml"].endswith("Release_TIMER/escape32_boot_lp-am13e230x_linkInfo.xml"))
        self.assertTrue(any(p.endswith("ESCape32_boot_LP_AM13E230.map") for p in data["ignored"]))
        self.assertEqual(data["unit"], "byte")
        flash = next(r for r in data["regions"] if r["name"] == "FLASH_BOOT")
        self.assertEqual((flash["length"], flash["used"]), (0x1000, 0xC00))
        self.assertEqual(data["entry"], {"name": "Reset_Handler", "address": 0x4DD})

    def test_main_symbol(self):
        code, out, err = run(BOOT, "--build", "Release_TIMER", "symbol", "main")
        self.assertEqual(code, 0, err)
        row = json.loads(out)["symbols"][0]
        self.assertEqual((row["address"], row["section"], row["section_address"], row["section_size"], row["input_file"]),
                         (0x41, ".text.main", 0x40, 0x270, "main.o"))

    def test_stale_build_stops(self):
        code, out, err = run(BOOT, "--build", "Debug", "summary")     # Debug is older than .cproject
        self.assertEqual(code, 3)
        self.assertIn("ASK USER", out + err)


@unittest.skipUnless((DAB / "RELEASE" / "makefile").is_file(), "dab_F28003x not present")
class RealC2000(unittest.TestCase):
    def test_word_unit_and_symbols(self):
        code, out, err = run(DAB, "--build", "RELEASE", "symbol", "main", "_c_int00")
        self.assertEqual(code, 0, err)
        rows = {row["name"]: row for row in json.loads(out)["symbols"]}
        self.assertEqual(rows["main"]["address"], 0x835A7)
        self.assertEqual(rows["_c_int00"]["address"], 0x840AD)
        code, out, err = run(DAB, "--build", "RELEASE", "summary")
        self.assertEqual(json.loads(out)["unit"], "word16")


if __name__ == "__main__":
    unittest.main()
