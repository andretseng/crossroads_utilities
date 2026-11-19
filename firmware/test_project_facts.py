import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

import project_facts


def configuration(name, chain, codegen, products, device, core=""):
    return f'''<configuration name="{name}">
      <folderInfo><toolChain superClass="{chain}">
        <option superClass="com.ti.ccstudio.buildDefinitions.core.OPT_TAGS">
          <listOptionValue value="PRODUCTS={products}"/>
          <listOptionValue value="DEVICE_CONFIGURATION_ID={device}"/>
          <listOptionValue value="DEVICE_CORE_ID={core}"/>
        </option>
        <option superClass="com.ti.ccstudio.buildDefinitions.core.OPT_CODEGEN_VERSION" value="{codegen}"/>
        <tool><option superClass="x.compilerID.DEFINE"><listOptionValue value="FROM_CPROJECT"/></option></tool>
      </toolChain></folderInfo>
    </configuration>'''


class ProjectFactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "ti"
        self.project = base / "projects" / "demo_r5fss0-0"
        self.project.mkdir(parents=True)
        self.sdk = self.root / "fake_sdk_1_2_3_4"
        metadata = self.sdk / ".metadata" / "product.json"
        metadata.parent.mkdir(parents=True)
        metadata.write_text('{"name":"FAKE-SDK","version":"1.2.3.4"}\n', encoding="utf-8")
        dependency = self.root / "dependency_sdk_2_0_0_1" / ".metadata" / "product.json"
        dependency.parent.mkdir(parents=True)
        dependency.write_text('{"name":"DEPENDENCY-SDK","version":"2.0.0.1"}\n', encoding="utf-8")
        (self.sdk / "source").mkdir()
        (self.sdk / "lib").mkdir()
        (self.sdk / "lib" / "sdk.a").write_bytes(b"")
        self.arm = self.root / "ti-cgt-armllvm_4.0.4.LTS" / "bin" / "tiarmclang"
        self.c2000 = self.root / "ti-cgt-c2000_22.6.2.LTS" / "bin" / "cl2000"
        self.sysconfig = self.root / "sysconfig_1.28.0" / "sysconfig_cli.sh"
        for tool in (self.arm, self.c2000, self.sysconfig):
            tool.parent.mkdir(parents=True, exist_ok=True)
            tool.write_text("", encoding="utf-8")
        cproject = "<cproject>" + configuration(
            "Debug", "x.TMS470_TICLANG_4.0.exe.DebugToolchain", "TICLANG_4.0.4.LTS",
            "FAKE-SDK:1.2.3.4;sysconfig:1.28.0;", "Cortex R.TEST", "r5fss0-0"
        ) + configuration(
            "C2000", "x.C2000_22.6.exe.DebugToolchain", "22.6.2.LTS",
            "FAKE-SDK:1.2.3.4;", "C28.TEST", "cpu1"
        ) + configuration(
            "Never", "x.TMS470_TICLANG_4.0.exe.ReleaseToolchain", "TICLANG_4.0.4.LTS",
            "FAKE-SDK:1.2.3.4;", "Cortex R.TEST", "r5fss0-0"
        ) + configuration(
            "Foreign", "x.TMS470_TICLANG_4.0.exe.DebugToolchain", "TICLANG_4.0.4.LTS",
            "FAKE-SDK:1.2.3.4;sysconfig:1.28.0;", "Cortex R.TEST", "r5fss0-0"
        ) + "</cproject>"
        (self.project / ".cproject").write_text(cproject, encoding="utf-8")
        (self.project / "example.syscfg").write_text(
            '// @cliArgs --device TEST --product "FAKE-SDK@1.2.3.4"\n'
            '// @versions {"tool":"1.28.1+4785"}\n', encoding="utf-8")
        (self.project / "linker.cmd").write_text("", encoding="utf-8")
        (self.project / "lib").mkdir()
        (self.project / "lib" / "local.a").write_bytes(b"")
        debug = self.project / "Debug"
        debug.mkdir()
        (debug / "makefile").write_text(
            f'-include subdir_rules.mk\nOUT: $(OBJS)\n\t"{self.arm}" -Wl,-i"{self.project / "lib"}" '
            f'-Wl,-i"{self.sdk / "lib"}" -Wl,-lrecipe_only.a "../linker.cmd"\n', encoding="utf-8")
        debug_rules = debug / "subdir_rules.mk"
        debug_rules.write_text(
            f'foo.o: ../foo.c\n\t"{self.arm}" -DARM_DEFINE -UOLD -I"{self.sdk / "source"}" ../foo.c\n'
            f'bar.o: ../bar.cpp\n\t"{self.arm}" -DCPP_DEFINE -I"{self.sdk / "source"}" ../bar.cpp\n'
            f'cfg: ../example.syscfg\n\t"{self.sysconfig}" -s "{metadata}" -s "{dependency}" '
            f'-d TEST --script ../example.syscfg -o syscfg\n',
            encoding="utf-8")
        (debug / "demo.map").write_text(" local.a : member.o\n sdk.a(member.o)\n", encoding="utf-8")
        stray = debug / "SDKDocs" / "_downloads" / "copy" / "subdir_rules.mk"
        stray.parent.mkdir(parents=True)
        stray.write_text(
            'stray.o: ../stray.c\n\t"C:/ti/ti_cgt_arm_llvm_9.9.9/bin/tiarmclang.exe" -DSTRAY ../stray.c\n'
            'cfg: ../stray.syscfg\n\t"C:/ti/sysconfig_9.9.9/sysconfig_cli.bat" '
            '-s "C:/ti/stray_sdk_9_9_9/.metadata/product.json" --script ../stray.syscfg -o syscfg\n',
            encoding="utf-8")
        cbuild = self.project / "C2000"
        cbuild.mkdir()
        (cbuild / "makefile").write_text(
            f'-include subdir_rules.mk\nall:\n\t"{self.c2000}" -i"/tmp/example-lp_system" -llibc.a\n',
            encoding="utf-8")
        c2000_rules = cbuild / "subdir_rules.mk"
        c2000_rules.write_text(
            f'asm.obj: ../start.asm\n\t"{self.c2000}" --define=C2000_DEFINE --undefine=LEGACY '
            f'--include_path="{self.sdk / "source"}" ../start.asm\n', encoding="utf-8")
        old = time.time() - 100
        new = time.time()
        os.utime(debug_rules, (old, old))
        os.utime(self.project / "example.syscfg", (old, old))
        os.utime(self.project / ".cproject", (new, new))
        os.utime(c2000_rules, (new + 10, new + 10))
        foreign = self.project / "Foreign"
        foreign.mkdir()
        (foreign / "makefile").write_text("-include subdir_rules.mk\nall:\n\t@echo link\n", encoding="utf-8")
        foreign_rules = foreign / "subdir_rules.mk"
        foreign_rules.write_text(
            'foreign.o: ../foreign.c\n'
            '\t"C:/ti/ti_cgt_arm_llvm_4.0.4.LTS/bin/tiarmclang.exe" '
            '-I"C:/ti/fake_sdk_1_2_3_4/source" ../foreign.c\n'
            'cfg: ../example.syscfg\n'
            '\t"C:/ti/sysconfig_1.28.0/sysconfig_cli.bat" '
            '-s "C:/ti/fake_sdk_1_2_3_4/.metadata/product.json" '
            '--script ../example.syscfg -o syscfg\n', encoding="utf-8")
        os.utime(foreign_rules, (new + 10, new + 10))
        sibling = self.project.parent / "demo_system"
        sibling.mkdir()
        (sibling / ".cproject").write_text(
            "<cproject>" + configuration("Debug", "x.TMS470_TICLANG_4.0.exe.DebugToolchain",
                                         "TICLANG_4.0.4.LTS", "FAKE-SDK:1.2.3.4;", "Cortex R.TEST") + "</cproject>",
            encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def facts(self, build, host_os=None):
        installed = project_facts.inventory([self.root])
        entry = next(item for item in project_facts.cproject_builds(self.project) if item["name"] == build)
        return project_facts.build_facts(self.project, entry, project_facts.syscfg_headers(self.project), installed,
                                         host_os=host_os)

    def test_clang_facts_disagreement_staleness_and_non_c_source(self):
        facts = self.facts("Debug")
        self.assertEqual(facts["compilers"][0]["version"], "4.0.4")
        self.assertEqual(facts["sdks"][0]["version"], "1.2.3.4")
        self.assertEqual({item["version"] for item in facts["sdks"]}, {"1.2.3.4", "2.0.0.1"})
        self.assertEqual(len(facts["sysconfig"]["product_jsons"]), 2)
        self.assertEqual(facts["rule_selection"]["mode"], "makefile includes")
        self.assertEqual(facts["rule_selection"]["files"], ["Debug/subdir_rules.mk"])
        by_source = {item["source"]: item for item in facts["files"]}
        self.assertIn("ARM_DEFINE", by_source["../foo.c"]["defines"])
        self.assertIn("CPP_DEFINE", by_source["../bar.cpp"]["defines"])
        self.assertTrue(any("@versions tool 1.28.1+4785" in line and "ran 1.28.0" in line
                            for line in facts["disagree"]))
        self.assertTrue(any(".cproject changed" in line for line in facts["stale"]))

    def test_stray_rule_copy_not_included_by_makefile_is_ignored(self):
        facts = self.facts("Debug")
        self.assertNotIn("../stray.c", {item["source"] for item in facts["files"]})
        self.assertNotIn("STRAY", {symbol for item in facts["files"] for symbol in item["defines"]})
        self.assertFalse(any(item["path"].startswith("C:/ti/") for item in facts["compilers"]))
        self.assertEqual(facts["sysconfig"]["version"], "1.28.0")

    def test_rglob_fallback_is_labeled_when_makefile_has_no_rule_includes(self):
        makefile = self.project / "Debug" / "makefile"
        makefile.write_text(makefile.read_text(encoding="utf-8").replace("-include subdir_rules.mk\n", ""),
                            encoding="utf-8")
        facts = self.facts("Debug")
        self.assertEqual(facts["rule_selection"]["mode"], "rglob fallback")
        rendered = project_facts.render(self.project, project_facts.syscfg_headers(self.project),
                                        [facts], project_facts.related_projects(self.project))
        self.assertIn("subdir_rules selection: rglob fallback", rendered)

    def test_windows_recipe_is_foreign_and_names_linux_equivalents(self):
        facts = self.facts("Foreign", host_os="Linux")
        self.assertEqual(facts["foreign"][0]["os"], "Windows")
        self.assertTrue(facts["foreign"][0]["path"].startswith("C:/ti/"))
        equivalents = {item["kind"]: item for item in facts["local_equivalents"]}
        self.assertEqual(equivalents["compiler"]["name"], "armllvm")
        self.assertEqual(equivalents["compiler"]["version"], "4.0.4")
        self.assertEqual(equivalents["compiler"]["path"], str(self.arm.parent.parent))
        self.assertEqual(equivalents["SysConfig"]["path"], str(self.sysconfig.parent))
        self.assertEqual(equivalents["SDK"]["path"], str(self.sdk))
        rendered = project_facts.render(self.project, project_facts.syscfg_headers(self.project),
                                        [facts], project_facts.related_projects(self.project))
        self.assertIn("FOREIGN: Foreign was generated on Windows", rendered)
        self.assertIn("local equivalent: compiler armllvm 4.0.4", rendered)
        self.assertIn("ASK USER: rebuild Foreign on this machine", rendered)
        self.assertEqual(project_facts.result_status([facts]), 3)

    def test_cl2000_define_undefine_include_and_version(self):
        facts = self.facts("C2000")
        self.assertEqual(facts["compilers"][0]["version"], "22.6.2")
        self.assertEqual(facts["files"][0]["source"], "../start.asm")
        self.assertEqual(facts["files"][0]["defines"], ["C2000_DEFINE"])
        self.assertEqual(facts["files"][0]["undefines"], ["LEGACY"])
        self.assertFalse(any(line.startswith("Compiler:") for line in facts["disagree"]))
        self.assertEqual([item["name"] for item in facts["libraries"]["libraries"]], ["libc.a"])

    def test_map_libraries_win_and_resolve_project_and_sdk(self):
        libraries = {item["name"]: item for item in self.facts("Debug")["libraries"]["libraries"]}
        self.assertEqual(set(libraries), {"local.a", "sdk.a"})
        self.assertEqual(libraries["local.a"]["origin"], "project")
        self.assertEqual(libraries["sdk.a"]["origin"], "SDK")
        self.assertNotIn("recipe_only.a", libraries)
        scripts = self.facts("Debug")["libraries"]["linker_scripts"]
        self.assertEqual(scripts[0]["path"], str((self.project / "linker.cmd").resolve()))
        self.assertEqual(scripts[0]["origin"], "project")

    def test_related_system_project_has_device_and_core_facts(self):
        related = project_facts.related_projects(self.project)
        self.assertEqual({Path(item["project"]).name for item in related}, {"demo_r5fss0-0", "demo_system"})
        core = next(item for item in related if item["project"].endswith("demo_r5fss0-0"))
        self.assertEqual(core["cores"], ["cpu1", "r5fss0-0"])
        self.assertIn("Cortex R.TEST", core["devices"])

    def test_exit_three_and_ask_user_for_stale_and_never_built(self):
        for build in ("Debug", "Never"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = project_facts.main([str(self.project), "--build", build, "--root", str(self.root)])
            self.assertEqual(status, 3)
            self.assertIn(f"ASK USER: rebuild {build} in CCS, then run project_facts again", output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(project_facts.main([str(self.project), "--build", "C2000", "--root", str(self.root)]), 0)

    @unittest.skipUnless(Path("/home/andre/workspace_ccstheia/drone/escape32_boot_lp-am13e230x").is_dir(),
                         "escape32_boot project is absent")
    def test_escape32_boot_debug_smoke_and_version_disagreement(self):
        project = Path("/home/andre/workspace_ccstheia/drone/escape32_boot_lp-am13e230x")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = project_facts.main([str(project), "--build", "Debug", "--json"])
        self.assertIn(status, (0, 3))
        data = json.loads(output.getvalue())
        self.assertTrue(any("@versions tool 1.28.1+4785" in line and "ran 1.28.0" in line
                            for line in data["builds"][0]["disagree"]))


if __name__ == "__main__":
    unittest.main()
