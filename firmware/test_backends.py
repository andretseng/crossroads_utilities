#!/usr/bin/env python3
"""Acceptance tests for the D10 split (Claude, 2026-11-19). Do not edit."""
import unittest
from pathlib import Path
import platforms
import backends
from backends import clang, ti_legacy

HERE = Path(__file__).resolve().parent


class Platforms(unittest.TestCase):
    def test_family_from_compiler_name(self):
        cases = {
            "/x/ti-cgt-armllvm_5.1.1.LTS/bin/tiarmclang": ("clang", "byte"),
            "/x/ti-cgt-c29_2.2.1.LTS/bin/c29clang": ("clang", "byte"),
            "/x/ti-cgt-c2000_22.6.2.LTS/bin/cl2000": ("ti_legacy", "word16"),
            "/x/ti-cgt-c7000_5.0.1.LTS/bin/cl7x": ("ti_legacy", "byte"),
            "/x/ti-cgt-pru_2.3.3/bin/clpru": ("ti_legacy", "byte"),
        }
        for path, (kind, unit) in cases.items():
            p = platforms.for_compiler(path)
            self.assertEqual((p["family_kind"], p["unit"]), (kind, unit), path)

    def test_unknown_compiler_is_an_error_not_a_guess(self):
        with self.assertRaises(KeyError):
            platforms.for_compiler("/usr/bin/sdcc")  # unknown family; gcc became "gnu" in preprocess round 3


class Split(unittest.TestCase):
    def test_backends_export_the_same_function(self):
        self.assertTrue(callable(clang.record_layout))
        self.assertTrue(callable(ti_legacy.record_layout))
        self.assertTrue(issubclass(backends.CompileError, Exception))

    def test_front_end_has_no_compiler_specific_parsing(self):
        front = (HERE / "struct_layout.py").read_text()
        for word in ("-fdump-record-layouts", "DW_AT_", "data_member_location"):
            self.assertNotIn(word, front)
        self.assertIn("platforms", front)


if __name__ == "__main__":
    unittest.main()
