#!/usr/bin/env python3
"""Claude review 2 of project_facts.compile_command (2026-11-19). Do not edit."""
import tempfile
import unittest
from pathlib import Path
import project_facts

DRV = Path("/home/andre/workspace_ccstheia/drone/escape32_drv8300_lp-am13e230x")


class Fixture(unittest.TestCase):
    def test_response_file_quotes_spaces_and_value_less_c(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            build = project / "Debug"
            build.mkdir()
            (project / "a.c").write_text("int a;\n")
            (build / "inc.opt").write_text('-I"/tmp/dir with space/inc" -I"/tmp/second" -DFROM_OPT\n')
            (build / "subdir_rules.mk").write_text(
                'a.o: ../a.c $(GEN_OPTS)\n'
                '\t"/opt/bin/tiarmclang" -c @"inc.opt" -mcpu=cortex-m33 -code_state=16 -Og -DX=1 -MMD -MP -MF"a.d" -o "a.o" "../a.c"\n')
            compiler, args = project_facts.compile_command(project, "Debug")
            self.assertEqual(compiler, "/opt/bin/tiarmclang")
            self.assertIn("-I/tmp/dir with space/inc", args)     # shlex, not str.split
            self.assertIn("-I/tmp/second", args)                 # -c takes no value: the first token after it is kept
            self.assertIn("-DFROM_OPT", args)
            for keep in ("-mcpu=cortex-m33", "-code_state=16", "-Og", "-DX=1"):
                self.assertIn(keep, args)                        # only exact drop flags go, not every "-c..." / "-o..." prefix
            for gone in ("-c", "-MMD", "-MP", "-MFa.d", "a.d", "-o", "a.o", "../a.c"):
                self.assertNotIn(gone, args)


@unittest.skipUnless((DRV / "Debug" / "ccsIncludes.opt").is_file(), "escape32_drv8300 Debug not present")
class Real(unittest.TestCase):
    def test_first_include_of_the_response_file_is_kept(self):
        first = "-I" + str(DRV) + str(DRV)                        # the opt file's first entry, verbatim
        compiler, args = project_facts.compile_command(DRV, "Debug")
        self.assertIn(first, args)


if __name__ == "__main__":
    unittest.main()
