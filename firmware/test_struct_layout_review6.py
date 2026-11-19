#!/usr/bin/env python3
"""Claude review 6 of struct_layout (2026-11-19). Do not edit.
Anonymous struct/union members are accessed as direct members in C (x.h, x.b2), so list them flattened at the anonymous
member's offset (+ their own offset inside it). Found: clang listed only "h" (b2 missing); cl2000 listed a compiler-internal
name "$P$T0". Expected values from Claude's own tiarmclang 5.1.1 record dump and cl2000 22.6.2 DWARF."""
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
typedef enum { E0, E1 } Mode;
typedef struct { uint8_t k; } Small;
typedef struct { Mode m; void *p; Small s[3]; union { uint16_t h; uint8_t b2[2]; }; uint32_t f:3; } Mix;
"""
C28_H = "#include <stdint.h>\ntypedef struct { uint16_t m; union { uint16_t h; uint32_t w; }; uint16_t t; } MixC;\n"


def layout(compiler, flags, type_name, header_text):
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "h.h"
        h.write_text(header_text)
        proc = subprocess.run([sys.executable, str(TOOL), "--compiler", str(compiler), "--flags", flags, "--type", type_name,
                               "--include", str(h), "--json"], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    return data["size"], [(m["name"], m["offset"]) for m in data["members"]]


class AnonymousMembers(unittest.TestCase):
    @unittest.skipUnless(ARMCLANG.is_file(), "tiarmclang missing")
    def test_clang(self):
        self.assertEqual(layout(ARMCLANG, "-mcpu=cortex-m33", "Mix", ARM_H),
                         (16, [("m", 0), ("p", 4), ("s", 8), ("h", 12), ("b2", 12), ("f", 14)]))

    @unittest.skipUnless(CL2000.is_file(), "cl2000 missing")
    def test_cl2000(self):
        flags = f"-v28 --abi=eabi --include_path={CL2000.parent.parent / 'include'}"
        self.assertEqual(layout(CL2000, flags, "MixC", C28_H), (6, [("m", 0), ("h", 2), ("w", 2), ("t", 4)]))


if __name__ == "__main__":
    unittest.main()
