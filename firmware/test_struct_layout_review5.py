#!/usr/bin/env python3
"""Claude review 5 of struct_layout (2026-11-19). Do not edit.
Found: (a) clang, a struct that CONTAINS another struct returned the inner struct (Outer -> Inner, size 2) - silent wrong answer;
(b) a union listed the members of its nested anonymous struct ("a", "b") and a bogus "RegU" as top-level members.
Top level only = direct members of the asked type; size = the asked type's own sizeof.
Expected values measured by Claude (tiarmclang 5.1.1 record dump; cl2000 22.6.2 DWARF)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "struct_layout.py"
ARMCLANG = Path("/home/andre/ti/ccs2101/ccs/tools/compiler/ti-cgt-armllvm_5.1.1.LTS/bin/tiarmclang")
CL2000 = Path("/home/andre/ti/ti-cgt-c2000_22.6.2.LTS/bin/cl2000")
ARM_H = """#include <stdint.h>
typedef struct { uint8_t lo; uint8_t hi; } Inner;
typedef union { uint32_t all; struct { uint32_t a:4; uint32_t b:28; } bit; } RegU;
typedef struct Outer_ { uint8_t tag; Inner in; RegU reg; uint16_t arr[3]; } Outer;
"""
C28_H = """#include <stdint.h>
typedef struct { uint16_t lo; uint16_t hi; } Inner16;
typedef struct Outer16_ { uint16_t tag; Inner16 in; uint32_t r; uint16_t arr[3]; } Outer16;
"""


def layout(compiler, flags, type_name, header_text):
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "h.h"
        h.write_text(header_text)
        proc = subprocess.run([sys.executable, str(TOOL), "--compiler", str(compiler), "--flags", flags, "--type", type_name,
                               "--include", str(h), "--json"], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    return data["size"], [(m["name"], m["offset"]) for m in data["members"]]


@unittest.skipUnless(ARMCLANG.is_file(), "tiarmclang missing")
class Clang(unittest.TestCase):
    def test_outer_is_not_replaced_by_inner(self):
        self.assertEqual(layout(ARMCLANG, "-mcpu=cortex-m33", "Outer", ARM_H),
                         (16, [("tag", 0), ("in", 1), ("reg", 4), ("arr", 8)]))

    def test_union_top_level_only(self):
        self.assertEqual(layout(ARMCLANG, "-mcpu=cortex-m33", "RegU", ARM_H), (4, [("all", 0), ("bit", 0)]))


@unittest.skipUnless(CL2000.is_file(), "cl2000 missing")
class Cl2000(unittest.TestCase):
    def test_nested_struct_words(self):
        flags = f"-v28 --abi=eabi --include_path={CL2000.parent.parent / 'include'}"
        self.assertEqual(layout(CL2000, flags, "Outer16", C28_H),
                         (10, [("tag", 0), ("in", 1), ("r", 4), ("arr", 6)]))


if __name__ == "__main__":
    unittest.main()
