#!/usr/bin/env python3
"""Claude review 1 of project_facts (2026-11-19). Do not edit.
CCS 20+ recipes pass include paths through a response file: tiarmclang -c @"ccsIncludes.opt" (escape32_drv8300 Debug)."""
import unittest
from pathlib import Path
import project_facts

DRV = Path("/home/andre/workspace_ccstheia/drone/escape32_drv8300_lp-am13e230x")


@unittest.skipUnless((DRV / "Debug" / "ccsIncludes.opt").is_file(), "escape32_drv8300 Debug not present")
class ResponseFiles(unittest.TestCase):
    def test_compile_command_expands_response_files(self):
        # shared API for every tool that needs the real compile command (struct_layout, preprocessing, ...)
        compiler, args = project_facts.compile_command(DRV, "Debug")
        self.assertTrue(compiler.endswith("bin/tiarmclang"))
        self.assertFalse(any(a.startswith("@") for a in args), "response files must be expanded")
        self.assertIn("-I/home/andre/ti/am13e230x_sdk_26_01_00_03/source/driverlib/am13e230x", args)
        self.assertIn("-DBSXL8300DRGE_REVA", args)
        self.assertIn("-mcpu=cortex-m33", args)
        self.assertFalse({"-c", "-o"} & set(args))

    def test_compile_command_for_a_named_source(self):
        compiler, args = project_facts.compile_command(DRV, "Debug", source="syscfg/ti_sdk_dl_config.c")
        self.assertTrue(any(a.endswith("Debug/syscfg") for a in args if a.startswith("-I")))


if __name__ == "__main__":
    unittest.main()
