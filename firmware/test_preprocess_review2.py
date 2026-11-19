#!/usr/bin/env python3
"""Claude review 2 of preprocess (2026-11-19). Do not edit. Partial mode: a symbol that is neither --define'd nor --undefine'd
is UNKNOWN. Found: io.c `#ifdef IO_UC4` branch reported compiled=true and IO_UC4 missing from unknown_symbols when only
--define IO_TIMER was given. unifdef keeps a line under an unknown symbol in its output - that does not mean it is built."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "preprocess.py"
IO = Path("/home/andre/workspace_ccstheia/drone/escape32_boot_lp-am13e230x/src/io.c")


def run(*args):
    p = subprocess.run([sys.executable, str(TOOL), "--partial", str(IO), *args, "--lines", "22-36", "--json"],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    d = json.loads(p.stdout)
    return {r["line"]: r["compiled"] for r in d["lines"]}, set(d["unknown_symbols"])


@unittest.skipUnless(IO.is_file(), "io.c missing")
class Partial(unittest.TestCase):
    def test_unknown_symbols_stay_unknown(self):
        r, unknown = run("--define", "IO_TIMER")
        self.assertEqual((r[24], r[30], r[35]), (None, None, None))
        self.assertEqual(unknown, {"IO_UC4", "IO_PA2"})

    def test_all_known(self):
        r, unknown = run("--define", "IO_TIMER", "--undefine", "IO_UC4", "--undefine", "IO_PA2")
        self.assertEqual((r[24], r[30], r[35]), (False, False, True))
        self.assertEqual(unknown, set())


if __name__ == "__main__":
    unittest.main()
