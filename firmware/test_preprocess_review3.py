#!/usr/bin/env python3
"""Claude round 3 of preprocess (2026-11-19, user-approved). Do not edit.
Real fact: escape32_boot Release_PA26 was never built; its .cproject defines are
['${SYSCONFIG_TOOL_SYMBOLS}', '${COM_TI_AM13E2X_SDK_SYMBOLS}', 'IO_PA2'] (read by project_facts)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "preprocess.py"
BOOT = Path("/home/andre/workspace_ccstheia/drone/escape32_boot_lp-am13e230x")


def run(*args, cwd=None):
    p = subprocess.run([sys.executable, str(TOOL), *map(str, args), "--json"], capture_output=True, text=True, timeout=300, cwd=cwd)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout)


@unittest.skipUnless((BOOT / ".cproject").is_file(), "escape32_boot missing")
class FromCproject(unittest.TestCase):
    def test_seeded_and_unresolved(self):
        d = run("--partial", BOOT / "src/io.c", "--from-cproject", BOOT, "--build", "Release_PA26", "--lines", "22-36")
        self.assertEqual(d["defines_source"], "cproject (inferred)")
        self.assertEqual(set(d["unresolved"]), {"${SYSCONFIG_TOOL_SYMBOLS}", "${COM_TI_AM13E2X_SDK_SYMBOLS}"})
        rows = {r["line"]: r["compiled"] for r in d["lines"]}
        self.assertIsNone(rows[24])                     # IO_UC4 is not in the list, but an unresolved ${...} may define it
        self.assertIn("IO_UC4", d["unknown_symbols"])
        self.assertNotIn("IO_PA2", d["unknown_symbols"])

    def test_seeded_plus_command_line(self):
        d = run("--partial", BOOT / "src/io.c", "--from-cproject", BOOT, "--build", "Release_PA26", "--undefine", "IO_UC4",
                "--lines", "22-36")
        self.assertEqual(d["defines_source"], "cproject (inferred) + command line")
        rows = {r["line"]: r["compiled"] for r in d["lines"]}
        self.assertEqual((rows[24], rows[30], rows[35]), (False, True, False))   # IO_PA2 branch is the one built


class PlainMake(unittest.TestCase):
    def test_repo_makefile_with_gcc(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "src").mkdir()
            (repo / "src/x.c").write_text("#ifdef FEATURE\nint on;\n#else\nint off;\n#endif\n")
            (repo / "Makefile").write_text("CFLAGS = -DFEATURE -O2\nall: build/x.o\nbuild/x.o: src/x.c\n\tgcc $(CFLAGS) -c src/x.c -o build/x.o\n")
            before = sorted(p.name for p in repo.rglob("*"))
            d = run("--make-dir", repo, "src/x.c")
            self.assertEqual(d["level"], "make")
            rows = {r["line"]: r["compiled"] for r in d["lines"]}
            self.assertEqual((rows[2], rows[4]), (True, False))
            self.assertIn("-DFEATURE", d["command"])
            self.assertEqual(sorted(p.name for p in repo.rglob("*")), before)     # make -n wrote nothing


class Platforms(unittest.TestCase):
    def test_gnu_family(self):
        sys.path.insert(0, str(HERE))
        import platforms
        for path in ("/usr/bin/gcc", "/opt/gcc-arm/bin/arm-none-eabi-gcc"):
            p = platforms.for_compiler(path)
            self.assertEqual((p["family_kind"], p["unit"]), ("gnu", "byte"))


if __name__ == "__main__":
    unittest.main()
