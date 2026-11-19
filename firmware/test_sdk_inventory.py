import contextlib
import io
from pathlib import Path
import tempfile
import unittest

import sdk_inventory


class SdkInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "ti"
        product = self.root / "freertos_sdk_am275x_12_01_00_28" / ".metadata" / "product.json"
        product.parent.mkdir(parents=True)
        product.write_text('{"name":"AM275-FREERTOS-SDK","version":"12.01.00.28",}\n', encoding="utf-8")
        compiler = self.root / "ti-cgt-c2000_22.6.2.LTS"
        compiler.mkdir()
        sysconfig = self.root / "sysconfig_1.28.1"
        sysconfig.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_inventory_reads_trailing_comma_and_c2000_version(self):
        data = sdk_inventory.inventory([self.root])
        self.assertEqual(data["sdks"][0]["version"], "12.01.00.28")
        self.assertEqual(data["sdks"][0]["folder_version"], "12_01_00_28")
        self.assertEqual(data["compilers"][0]["name"], "c2000")
        self.assertEqual(data["compilers"][0]["version"], "22.6.2")
        self.assertEqual(sdk_inventory.version_tuple("C2000_22.6.2.LTS"), (22, 6, 2))

    def test_match_am275x_sdk_is_unique_and_reports_missing_claims(self):
        data = sdk_inventory.inventory([self.root])
        matches = sdk_inventory.match_claims("AM275x SDK 12.01", data)
        self.assertEqual(matches["versions"][0]["verdict"], "unique")
        self.assertIn("freertos_sdk_am275x_12_01_00_28", matches["versions"][0]["candidates"][0]["path"])
        self.assertEqual(matches["device_words"], ["AM275x"])
        self.assertEqual(matches["ask_customer"], ["compiler", "SysConfig"])

    def test_no_version_tells_caller_to_ask_for_it(self):
        data = sdk_inventory.inventory([self.root])
        matches = sdk_inventory.match_claims("AM275x SDK", data)
        rendered = sdk_inventory.render(data, matches)
        self.assertIn("the text states no version: ask for it", rendered)
        self.assertIn("ask the customer: compiler, SysConfig", rendered)


if __name__ == "__main__":
    unittest.main()
