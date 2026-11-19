import contextlib
import io
from pathlib import Path
import tempfile
import textwrap
import unittest

import syscfg_run


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        (self.output / "config.h").write_text(textwrap.dedent("""
            #define CPUCLK_FREQ_HZ 200000000
            #define TIMER_LOAD_VALUE (2500U)
            #define UART_BAUD_DIV 16U
            #define SYMBOLIC_DIV SOME_MACRO
            /* Defines for CAN_TX: GPIO0.12 with pinCMx PA12 on package pin 49 */
        """), encoding="utf-8")
        (self.output / "config.c").write_text(textwrap.dedent("""
            static const DL_MCAN_BitTimingParams gMCANBitTimes = {
                .nomRatePrescalar = 3,
                .nomTimeSeg1 = 78,
                .nomTimeSeg2 = 19,
            };
        """), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_generated_parser_extracts_exact_facts(self):
        facts = syscfg_run.parse_generated(self.output)
        self.assertEqual([(item["label"], item["value"], item["file"], item["line"])
                          for item in facts["clocks"]],
                         [("CPUCLK_FREQ_HZ", "200000000", "config.h", 2)])
        values = {item["label"]: item["value"] for item in facts["parameters"]}
        self.assertEqual(values["TIMER_LOAD_VALUE"], "(2500U)")
        self.assertEqual(values["UART_BAUD_DIV"], "16U")
        self.assertNotIn("SYMBOLIC_DIV", values)
        self.assertEqual(values["gMCANBitTimes.nomTimeSeg1"], "78")
        self.assertEqual(facts["pins"][0]["label"], "CAN_TX")
        self.assertEqual(facts["pins"][0]["pad"], "PA12")
        self.assertEqual(facts["pins"][0]["package_pin"], "49")

    def test_diff_lists_only_changed_values(self):
        before = syscfg_run.parse_generated(self.output)
        text = (self.output / "config.c").read_text(encoding="utf-8").replace(".nomTimeSeg1 = 78", ".nomTimeSeg1 = 38")
        (self.output / "config.c").write_text(text, encoding="utf-8")
        after = syscfg_run.parse_generated(self.output)
        self.assertEqual(syscfg_run.diff_facts(before, after), [{
            "label": "gMCANBitTimes.nomTimeSeg1", "old": "78", "new": "38", "file": "config.c", "line": 4,
        }])


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.script = root / "input.syscfg"
        self.script.write_text("rate = 250;\n", encoding="utf-8")
        self.product = root / "product.json"
        self.product.write_text("{}\n", encoding="utf-8")
        self.cli = root / "fake_sysconfig.py"
        self.cli.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import pathlib
            import sys
            args = sys.argv[1:]
            script = pathlib.Path(args[args.index('--script') + 1])
            output = pathlib.Path(args[args.index('-o') + 1])
            text = script.read_text()
            if 'BAD_ASSIGNMENT' in text:
                print('Error: assignment rejected: BAD_ASSIGNMENT')
                raise SystemExit(1)
            output.mkdir(parents=True, exist_ok=True)
            value = '38' if 'rate = 500' in text else '78'
            (output / 'config.h').write_text('#define CPUCLK_FREQ_HZ 200000000\\n')
            (output / 'config.c').write_text(
                'static const Timing timing = {\\n    .segment = ' + value + ',\\n};\\n')
        """), encoding="utf-8")
        self.cli.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def args(self, action, *assignments):
        return ["--sysconfig", str(self.cli), "--product", str(self.product), "--script", str(self.script),
                action, *assignments]

    def test_explicit_whatif_runs_on_copy_and_reports_only_value_diff(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = syscfg_run.main(self.args("whatif", "rate = 500"))
        self.assertEqual(status, 0)
        rendered = output.getvalue()
        self.assertIn("mode: explicit CLI", rendered)
        self.assertIn("timing.segment: 78 -> 38", rendered)
        self.assertEqual(self.script.read_text(encoding="utf-8"), "rate = 250;\n")

    def test_rejected_whatif_error_is_verbatim_and_has_no_success_facts(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = syscfg_run.main(self.args("whatif", "BAD_ASSIGNMENT = true"))
        self.assertEqual(status, 1)
        rendered = output.getvalue()
        self.assertIn("## WHATIF ERRORS", rendered)
        self.assertIn("Error: assignment rejected: BAD_ASSIGNMENT", rendered)
        self.assertNotIn("## Whatif facts", rendered)


class Escape32IntegrationTests(unittest.TestCase):
    PROJECT = Path("/home/andre/workspace_ccstheia/drone/escape32_drv8300_lp-am13e230x")

    def mode(self):
        if not self.PROJECT.is_dir():
            self.skipTest("escape32 DRV8300 project is absent")
        mode = syscfg_run._project_mode(self.PROJECT, "Debug")
        if mode.get("blocked"):
            self.skipTest("escape32 DRV8300 Debug is blocked by project_facts")
        if not Path(mode["tokens"][0]).is_file():
            self.skipTest("the build's SysConfig is absent")
        return mode

    def test_base_and_500k_whatif(self):
        result = syscfg_run.calculate(self.mode(), "whatif", ["mcan1.desiredNomRate = 500"])
        self.assertEqual(result["base"]["exit_code"], 0, result["base"]["output"])
        self.assertEqual(result["whatif"]["exit_code"], 0, result["whatif"]["output"])
        self.assertEqual(result["base"]["regenerate"]["status"], "identical")
        self.assertEqual(result["base"]["facts"]["values"]["CPUCLK_FREQ_HZ"]["value"], "200000000")
        self.assertEqual(result["base"]["facts"]["values"]["gMCANBitTimes.nomTimeSeg1"]["value"], "78")
        self.assertEqual(result["whatif"]["facts"]["values"]["gMCANBitTimes.nomTimeSeg1"]["value"], "38")
        changed = {item["label"]: item for item in result["diff"]}
        self.assertEqual(changed["gMCANBitTimes.nomTimeSeg1"]["new"], "38")

    def test_real_rejected_rate_uses_verbatim_sysconfig_error(self):
        result = syscfg_run.calculate(self.mode(), "whatif", ["mcan1.desiredNomRate = 999999999"])
        self.assertEqual(result["whatif"]["exit_code"], 1)
        self.assertIn("error: MCAN(/drivers/mcan) desiredNomRate: Must be in the range of 1 to 1000",
                      result["whatif"]["errors"])
        self.assertNotIn("facts", result["whatif"])

    def test_not_built_configuration_stops_with_project_facts_ask(self):
        if not self.PROJECT.is_dir():
            self.skipTest("escape32 DRV8300 project is absent")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = syscfg_run.main([str(self.PROJECT), "--build", "Release", "facts"])
        self.assertEqual(status, 3)
        self.assertIn("ASK USER: rebuild Release in CCS, then run project_facts again", output.getvalue())
        self.assertIn("SysConfig was not run", output.getvalue())


if __name__ == "__main__":
    unittest.main()
