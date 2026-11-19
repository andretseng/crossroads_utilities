#!/usr/bin/env python3
"""Claude review 2 of source_lines (2026-11-19). Do not edit. Cases from real saved ledgers/answers."""
import unittest
import source_lines as s


def cites(text):
    return [(c.file, c.first, c.last) for c in s.parse_cites(text)]


class Review2(unittest.TestCase):
    def test_elided_file_inherits_the_previous_cite_on_the_same_line(self):
        # real: "is set identically in both builds (motor1_drive.c:416 and :438)"
        self.assertEqual(cites("(motor1_drive.c:416 and :438)"), [("motor1_drive.c", 416, 416), ("motor1_drive.c", 438, 438)])
        # real: "config.c:190 sets TIM1_SMCR TS=ITR1, :191 sets TIM2_CR2 MMS=OC3REF, and :211-212 trigger ADC"
        got = cites("config.c:190 sets TIM1_SMCR TS=ITR1, :191 sets TIM2_CR2 MMS=OC3REF, and :211-212 trigger ADC")
        self.assertEqual(got, [("config.c", 190, 190), ("config.c", 191, 191), ("config.c", 211, 212)])
        inherited = [c for c in s.parse_cites("(motor1_drive.c:416 and :438)") if c.first == 438][0]
        self.assertTrue(getattr(inherited, "inherited", False), "an elided cite must carry inherited=True")

    def test_elision_never_crosses_a_line(self):
        self.assertEqual(cites("see main.c:10\n:20 is a time-like label"), [("main.c", 10, 10)])

    def test_lone_colon_number_without_a_previous_cite_is_nothing(self):
        self.assertEqual(cites("| PA11 | 45 | MCAN0 RX | :263 |"), [])


if __name__ == "__main__":
    unittest.main()
