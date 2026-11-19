import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

import source_lines


class GrammarTests(unittest.TestCase):
    def one(self, text):
        cites = source_lines.parse_cites(text)
        self.assertEqual(len(cites), 1, cites)
        return cites[0]

    def test_plain_file_line(self):
        self.assertEqual((self.one("file.c:12").file, self.one("file.c:12").first), ("file.c", 12))

    def test_hyphen_range(self):
        cite = self.one("file.c:12-20")
        self.assertEqual((cite.first, cite.last), (12, 20))

    def test_en_dash_and_tilde_ranges(self):
        self.assertEqual(self.one("file.c:2–4").last, 4)
        self.assertEqual(self.one("file.c:5~7").last, 7)

    def test_comma_is_two_discontinuous_cites_fix_22(self):
        cites = source_lines.parse_cites("file.c:12,14")
        self.assertEqual([(c.file, c.first, c.last) for c in cites],
                         [("file.c", 12, 12), ("file.c", 14, 14)])

    def test_range_end_is_not_a_value_fix_22(self):
        cites = source_lines.parse_cites("write is at file.c:12-20; value 20")
        self.assertEqual([(c.first, c.last) for c in cites], [(12, 20)])

    def test_dotfile_and_long_extension_fix_5(self):
        cites = source_lines.parse_cites(".cproject:428 and x.syscfg:46")
        self.assertEqual([(c.file, c.first) for c in cites], [(".cproject", 428), ("x.syscfg", 46)])

    def test_relative_absolute_and_windows_paths(self):
        text = "src/a.c:3 /opt/sdk/b.hpp:4 C:/ti/sdk/c.c:5"
        self.assertEqual([c.file for c in source_lines.parse_cites(text)],
                         ["src/a.c", "/opt/sdk/b.hpp", "C:/ti/sdk/c.c"])

    def test_backticks_parentheses_and_table(self):
        text = "| (`src/a.c:3-4`) |"
        cite = self.one(text)
        self.assertEqual((cite.file, cite.first, cite.last), ("src/a.c", 3, 4))
        self.assertEqual(text[cite.span[0]:cite.span[1]], cite.raw)

    def test_values_and_times_are_not_cites(self):
        self.assertEqual(source_lines.parse_cites("value 0x1000:2 at 10:30"), [])

    def test_file_line_word_form_binds_correct_file_fix_37(self):
        cites = source_lines.parse_cites("main.c:88 then Makefile line 141 and x.syscfg line 9")
        self.assertEqual([(c.file, c.first) for c in cites],
                         [("main.c", 88), ("Makefile", 141), ("x.syscfg", 9)])

    def test_line_word_requires_a_real_filename(self):
        self.assertEqual(source_lines.parse_cites("the gate at line 141"), [])

    def test_quote_check_extensionless_form(self):
        cite = self.one("syscfg:17")
        self.assertEqual((cite.file, cite.first), ("syscfg", 17))

    def test_trailing_colon_is_table_separator_fix(self):
        # A `:` right after the line is a Markdown table column separator, not part of the cite
        # (real answers store cites as `file.c:12: | column`).  Without the fix this returns [].
        cites = source_lines.parse_cites("see rule at doc.md:74: | 中斷優先權 |")
        self.assertEqual([(c.file, c.first) for c in cites], [("doc.md", 74)])

    def test_path_with_space_paren_and_nonascii_fix(self):
        # A knowledge-base file at a path whose final segment has a space/paren/non-ASCII:
        # `.../CPU 模組分析 (SLAU846E).md:74`.  Without the fix this returns [].  And prose
        # `A / B / file.c:5` (space-slash-space) must NOT read as a path.
        text = "rule from /tmp/db/專業領域/CPU 模組分析 (SLAU846E).md:74: header"
        cites = source_lines.parse_cites(text)
        self.assertEqual(len(cites), 1, cites)
        self.assertEqual(cites[0].file.split("/")[-1], "CPU 模組分析 (SLAU846E).md")
        self.assertEqual(cites[0].first, 74)
        self.assertEqual(
            [c.file for c in source_lines.parse_cites("x / y / z.c:5")], ["z.c"]
        )
        # prose `/2 to SemiMCLK (config.h:20` (slash-number, space+lowercase) is not a path
        self.assertEqual(
            [c.file for c in source_lines.parse_cites(
                "then /2 to SemiMCLK; LOAD/CC sized against CLK=MCLK (config.h:20")],
            ["config.h"],
        )

    def test_assembly_asm_cite(self):
        # Addendum: .asm assembly source.
        cite = self.one("startup_asm.ASM:42")
        self.assertEqual((cite.file, cite.first, cite.last), ("startup_asm.ASM", 42, 42))

    def test_assembly_cla_cite(self):
        # Addendum: .cla (TI C2000 CLA) assembly source.
        cite = self.one("table.cla:17")
        self.assertEqual((cite.file, cite.first, cite.last), ("table.cla", 17, 17))


class FileOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.file = self.root / "fixture.c"
        self.file.write_text("alpha\nneedle one\nbeta\nneedle two\nomega\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_show_is_byte_identical_to_numbered_default_range_and_tail(self):
        legacy = "/home/andre/.agents/pool/exploring/explore-guard/numbered.py"
        old = subprocess.run([sys.executable, legacy, str(self.file), "2", "3"],
                             stdout=subprocess.PIPE, check=True).stdout
        new = subprocess.run([sys.executable, source_lines.__file__, "show", str(self.file), "2", "3"],
                             stdout=subprocess.PIPE, check=True).stdout
        self.assertEqual(new, old)

    def test_show_facts_appends_matching_code_view_facts(self):
        view = self.root / "view"; view.mkdir()
        (view / "fixture.c.view.md").write_text(
            "fixture.c:2: needle one\n  compiled: yes (build Debug)\n", encoding="utf-8")
        shown = source_lines.show_text(self.file, 2, 2, view)
        self.assertEqual(shown, "2: needle one\n  compiled: yes (build Debug)\n... (5 lines in " +
                         str(self.file) + "; shown 2-2)\n")

    def test_fill_exact_lines(self):
        result = source_lines.fill(self.file, 2, 3)
        self.assertEqual([(line.number, line.text) for line in result.lines],
                         [(2, "needle one"), (3, "beta")])

    def test_fill_find_zero_one_two(self):
        zero = source_lines.fill(self.file, find="absent")
        one = source_lines.fill(self.file, find="alpha")
        two = source_lines.fill(self.file, find="needle")
        self.assertEqual((zero.matches, zero.status), (0, "no matches"))
        self.assertEqual((one.matches, one.status), (1, "one match"))
        self.assertEqual((two.matches, two.status), (2, "2 matches"))
        self.assertEqual([line.number for line in two.lines], [2, 4])

    def test_resolve_unique_ambiguous_and_skips(self):
        (self.root / "a").mkdir(); (self.root / "b").mkdir(); (self.root / "SDKDocs").mkdir()
        (self.root / "a" / "same.c").write_text("a\n", encoding="utf-8")
        (self.root / "b" / "same.c").write_text("b\n", encoding="utf-8")
        (self.root / "SDKDocs" / "only.c").write_text("ignored\n", encoding="utf-8")
        ambiguous = source_lines.resolve(self.one("same.c:1"), [self.root])
        self.assertIsInstance(ambiguous, list)
        self.assertEqual(len(ambiguous), 2)
        self.assertIsNone(source_lines.resolve(self.one("only.c:1"), [self.root]))
        unique = source_lines.resolve(self.one("a/same.c:1"), [self.root])
        self.assertEqual(unique, (self.root / "a" / "same.c").resolve())

    def one(self, text):
        return source_lines.parse_cites(text)[0]

    def test_verify_bounds_and_quote_check_normalization(self):
        cite = self.one("fixture.c:2")
        result = source_lines.verify(cite, [self.root], "needle = one;")
        self.assertTrue(result.exists)
        self.assertTrue(result.line_in_range)
        self.assertTrue(result.quote_matches)
        bad = source_lines.verify(self.one("fixture.c:99"), [self.root])
        self.assertFalse(bad.line_in_range)


if __name__ == "__main__":
    unittest.main()
