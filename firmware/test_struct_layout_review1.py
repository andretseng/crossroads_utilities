#!/usr/bin/env python3
"""Claude review 1 of struct_layout (2026-11-19). Do not edit.
Project mode must use the real compile command (project_facts.compile_command, response files expanded): the first version
read the LINK recipe from <BUILD>/makefile and dropped every -I"..." and -DNAME token, so no project header could be found.
Expected values measured by Claude with the build's own tiarmclang 4.0.4 and -fdump-record-layouts."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "struct_layout.py"
DRV = Path("/home/andre/workspace_ccstheia/drone/escape32_drv8300_lp-am13e230x")


@unittest.skipUnless((DRV / "Debug" / "ccsIncludes.opt").is_file(), "escape32_drv8300 Debug not present")
class ProjectMode(unittest.TestCase):
    def test_sdk_struct_from_project_flags(self):
        proc = subprocess.run([sys.executable, str(TOOL), str(DRV), "--build", "Debug", "--type", "DL_MCAN_BitTimingParams",
                               "--include", "dl_mcan.h", "--json"], capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual((data["unit"], data["size"]), ("byte", 32))
        names = ["nomRatePrescalar", "nomTimeSeg1", "nomTimeSeg2", "nomSynchJumpWidth",
                 "dataRatePrescalar", "dataTimeSeg1", "dataTimeSeg2", "dataSynchJumpWidth"]
        self.assertEqual([(m["name"], m["offset"]) for m in data["members"]], [(n, 4 * i) for i, n in enumerate(names)])

    def test_tool_does_not_parse_makefiles_itself(self):
        source = TOOL.read_text()
        self.assertIn("compile_command", source)
        self.assertNotIn('build_dir / "makefile"', source)


if __name__ == "__main__":
    unittest.main()
