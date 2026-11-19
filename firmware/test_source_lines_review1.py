#!/usr/bin/env python3
"""Claude review 1 of source_lines (2026-11-19). Do not edit. Each case comes from a real saved ledger."""
import unittest
import source_lines as s


def cites(text):
    return [(c.file, c.first, c.last) for c in s.parse_cites(text)]


class Review1(unittest.TestCase):
    def test_slash_joined_cites_are_two_cites(self):
        # proj_bootflow r10 ledger line 6: "recvval() main.c:64/io.c:188; CMD_PROBE sendval main.c:66/io.c:194"
        got = cites("recvval() main.c:64/io.c:188; CMD_PROBE sendval main.c:66/io.c:194")
        for want in [("main.c", 64, 64), ("io.c", 188, 188), ("main.c", 66, 66), ("io.c", 194, 194)]:
            self.assertIn(want, got)

    def test_bare_name_is_marked_and_does_not_resolve(self):
        # escirq q24r2 ledger line 62: "ISRs (iftim_isr main.c:314, tim1_com_isr:295, ... halltim_isr:424)"
        found = [c for c in s.parse_cites("tim1_com_isr:295, halltim_isr:424") if c.file == "halltim_isr"]
        if found:                                   # accepted only as a bare name that must be resolved before use
            self.assertTrue(getattr(found[0], "bare", False), "a cite without an extension must carry bare=True")
            self.assertIsNone(s.resolve(found[0], ["/home/andre/workspace_ccstheia/drone/escape32_boot_lp-am13e230x"]))

    def test_dotted_config_member_is_not_a_cite(self):
        # ho_pin_am261 answer: "adc1.adc_INT1.priority:73", "input_xbar1.xbarOutput line 550" (SysConfig members, not files)
        self.assertEqual(cites("adc1.adc_INT1.priority:73"), [])
        self.assertEqual(cites("input_xbar1.xbarOutput line 550"), [])

    def test_cproject_stays_a_cite(self):
        self.assertEqual(cites("(.cproject:428)"), [(".cproject", 428, 428)])


if __name__ == "__main__":
    unittest.main()
