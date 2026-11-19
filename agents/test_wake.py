#!/usr/bin/env python3
"""Tests for wake.py with a fake orca CLI (no Orca needed). Run: python3 -m unittest test_wake"""
import json, os, subprocess, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WAKE = os.path.join(HERE, "wake.py")

FAKE = r'''#!/usr/bin/env python3
# fake orca: pane states in $FAKE_STATE (json {handle: "idle"|"busy"|"approval"|"gone"}); sends logged to $FAKE_SENT
import json, os, sys
a = sys.argv[1:]
st = json.load(open(os.environ["FAKE_STATE"]))
agents = st.pop("_agents", {})  # {handle: agent name}
if a[:2] == ["terminal", "list"]:
    print(json.dumps({"ok": True, "result": {"terminals": [
        {"handle": k, "connected": True, "agentIdentity": agents.get(k), "title": "t"} for k, v in st.items() if v != "gone"]}}))
    sys.exit(0)
h = a[a.index("--terminal") + 1]
s = st.get(h, "gone")
if a[:2] == ["terminal", "show"]:
    if s == "gone":
        print(json.dumps({"ok": False, "error": {"code": "terminal_handle_stale"}})); sys.exit(1)
    print(json.dumps({"ok": True, "result": {"terminal": {"handle": h, "connected": True, "writable": True}}}))
elif a[:2] == ["terminal", "wait"]:
    if s == "idle":
        print(json.dumps({"ok": True, "result": {"wait": {"satisfied": True}}})); sys.exit(0)
    if s in ("approval", "codex_idle", "codex_working"):  # orca gives all three the same blockedReason
        print(json.dumps({"ok": True, "result": {"wait": {"satisfied": False, "blockedReason": "agent-interactive-prompt"}}})); sys.exit(1)
    print(json.dumps({"ok": False, "error": {"code": "timeout"}})); sys.exit(1)
elif a[:2] == ["terminal", "read"]:
    screens = {"codex_idle": ["  Worked for 3s", "› Ask Codex to do anything", "  GPT-5.6-Luna medium"],
               "codex_working": ["• Working (5s • esc to interrupt)", "› Ask Codex to do anything"],
               "approval": ["Would you like to run the following command?", "› 1. Yes, proceed", "  2. No"]}
    print(json.dumps({"ok": True, "result": {"terminal": {"tail": screens.get(s, [])}}}))
elif a[:2] == ["terminal", "send"]:
    with open(os.environ["FAKE_SENT"], "a") as f:
        f.write(json.dumps({"to": h, "text": a[a.index("--text") + 1]}) + "\n")
    print(json.dumps({"ok": True}))
'''


class WakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        fake = os.path.join(self.tmp, "fake_orca")
        with open(fake, "w") as f:
            f.write(FAKE)
        os.chmod(fake, 0o755)
        self.state = os.path.join(self.tmp, "state.json")
        self.sent = os.path.join(self.tmp, "sent.log")
        self.env = dict(os.environ, WAKE_DIR=os.path.join(self.tmp, "wake"), ORCA_CLI=fake, FAKE_STATE=self.state,
                        FAKE_SENT=self.sent, WAKE_NO_DAEMON="1", WAKE_SCREEN_RECHECK="0", ORCA_TERMINAL_HANDLE="term_me")
        self.panes(term_me="idle")

    def panes(self, **st):
        with open(self.state, "w") as f:
            json.dump(st, f)

    def wake(self, *args, ok=True):
        p = subprocess.run([sys.executable, WAKE, *args], env=self.env, capture_output=True, text=True)
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def sent_msgs(self):
        if not os.path.exists(self.sent):
            return []
        return [json.loads(l) for l in open(self.sent)]

    def watch_path(self, wid):
        return os.path.join(self.tmp, "wake", "watches", wid + ".json")

    def age(self, wid, seconds):
        """Pretend the watch was created `seconds` ago."""
        p = self.watch_path(wid)
        w = json.load(open(p))
        w["created"] -= seconds
        json.dump(w, open(p, "w"))

    def test_file_change_fires_once_with_run_output(self):
        report = os.path.join(self.tmp, "report.md")
        counter = os.path.join(self.tmp, "runs")
        wid = self.wake("add", "--file", report, "--msg", "Pi reported", "--id", "w1",
                        "--run", f"echo x >> {counter}; echo GATE ACCEPT").stdout.strip()
        self.assertEqual(wid, "w1")
        self.wake("once")
        self.assertEqual(self.sent_msgs(), [], "must not fire before the file appears")
        open(report, "w").write("done")
        self.wake("once")
        self.wake("once")
        msgs = self.sent_msgs()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["to"], "term_me")
        self.assertIn("WAKE w1: file changed", msgs[0]["text"])
        self.assertIn("Pi reported", msgs[0]["text"])
        self.assertIn("GATE ACCEPT", msgs[0]["text"])
        self.assertEqual(open(counter).read().count("x"), 1)
        self.assertFalse(os.path.exists(self.watch_path("w1")))

    def test_existing_file_fires_only_on_change(self):
        report = os.path.join(self.tmp, "report.md")
        open(report, "w").write("old")
        self.wake("add", "--file", report, "--msg", "m", "--id", "w2")
        self.wake("once")
        self.assertEqual(self.sent_msgs(), [])
        time.sleep(0.01)
        open(report, "w").write("new report")
        self.wake("once")
        self.assertEqual(len(self.sent_msgs()), 1)

    def test_busy_or_approval_target_waits_and_run_is_not_repeated(self):
        counter = os.path.join(self.tmp, "runs")
        self.panes(term_me="busy")
        self.wake("add", "--after", "0", "--msg", "m", "--id", "w3", "--run", f"echo x >> {counter}")
        self.wake("once")
        self.panes(term_me="approval")
        self.wake("once")
        self.assertEqual(self.sent_msgs(), [], "never type into a busy turn or an approval prompt")
        self.assertIn("pane busy", self.wake("list").stdout)
        self.panes(term_me="idle")
        self.wake("once")
        self.assertEqual(len(self.sent_msgs()), 1)
        self.assertEqual(open(counter).read().count("x"), 1, "--run must run once, at fire time")

    def test_idle_codex_reported_as_interactive_prompt_still_gets_message(self):
        # measured 2026-11-19: orca tui-idle says agent-interactive-prompt for an idle Codex at its empty prompt
        self.panes(term_me="codex_working")
        self.wake("add", "--after", "0", "--msg", "m", "--id", "c1")
        self.wake("once")
        self.panes(term_me="approval")
        self.wake("once")
        self.assertEqual(self.sent_msgs(), [], "working or a real approval prompt: do not type")
        self.panes(term_me="codex_idle")
        self.wake("once")
        self.assertEqual(len(self.sent_msgs()), 1)

    def test_timeout_always_fires(self):
        self.wake("add", "--cmd", "false", "--timeout", "100", "--msg", "check Pi", "--id", "w4")
        self.wake("once")
        self.assertEqual(self.sent_msgs(), [])
        self.age("w4", 101)
        self.wake("once")
        self.assertIn("TIMEOUT", self.sent_msgs()[0]["text"])

    def test_idle_needs_two_scans_in_a_row(self):
        self.panes(term_me="idle", term_pi="idle")
        self.wake("add", "--idle", "term_pi", "--msg", "Pi stopped", "--id", "w5")
        self.wake("once")
        self.assertEqual(self.sent_msgs(), [], "one idle scan is not enough")
        self.panes(term_me="idle", term_pi="busy")
        self.wake("once")
        self.panes(term_me="idle", term_pi="idle")
        self.wake("once")
        self.assertEqual(self.sent_msgs(), [], "streak must reset after busy")
        self.wake("once")
        self.assertIn("pane idle: term_pi", self.sent_msgs()[0]["text"])

    def test_pid_exit_and_cmd_ok(self):
        p = subprocess.Popen(["sleep", "30"])
        self.wake("add", "--pid", str(p.pid), "--msg", "build done", "--id", "w6")
        flag = os.path.join(self.tmp, "flag")
        self.wake("add", "--cmd", f"test -f {flag}", "--msg", "flag", "--id", "w7")
        self.wake("once")
        self.assertEqual(self.sent_msgs(), [])
        p.kill(); p.wait()
        open(flag, "w").close()
        self.wake("once")
        texts = sorted(m["text"] for m in self.sent_msgs())
        self.assertEqual(len(texts), 2)
        self.assertIn("pid", texts[0])
        self.assertIn("cmd ok", texts[1])

    def test_gone_target_moves_to_failed_after_deadline(self):
        self.env["WAKE_DELIVER_MAX"] = "0"
        self.panes(term_me="idle", term_gone="idle")
        self.wake("add", "--after", "0", "--to", "term_gone", "--msg", "m", "--id", "w8")
        self.panes(term_me="idle")  # pane closed after the watch was registered
        self.wake("once")  # fires; fired_at = now, delivery fails, deadline 0 not yet exceeded
        time.sleep(0.01)
        self.wake("once")
        self.assertEqual(self.sent_msgs(), [])
        failed = os.path.join(self.tmp, "wake", "failed", "w8.json")
        self.assertTrue(os.path.exists(failed))
        self.assertEqual(json.load(open(failed))["failed"], "pane term_gone not found")
        self.assertIn("FAILED w8", open(os.path.join(self.tmp, "wake", "log")).read())

    def test_bad_add_is_an_error(self):
        p = self.wake("add", "--msg", "m", ok=False)
        self.assertEqual(p.returncode, 2)
        del self.env["ORCA_TERMINAL_HANDLE"]
        p = self.wake("add", "--after", "1", "--msg", "m", ok=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("ORCA_TERMINAL_HANDLE", p.stderr)

    def test_stale_or_unknown_pane_is_refused_at_add(self):
        self.env["ORCA_TERMINAL_HANDLE"] = "term_stale"  # e.g. Codex app-server daemon started in a closed pane
        p = self.wake("add", "--after", "0", "--msg", "m", ok=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("term_stale", p.stderr)
        self.assertIn("ORCA_TERMINAL_HANDLE", p.stderr)
        self.assertIn("term_me", p.stderr, "error lists the live panes")
        p = self.wake("add", "--after", "0", "--to", "term_me", "--idle", "term_nope", "--msg", "m", ok=False)
        self.assertEqual(p.returncode, 2)
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "wake", "watches")) and
                         os.listdir(os.path.join(self.tmp, "wake", "watches")), "nothing registered")

    def test_agent_name_resolves_at_delivery(self):
        self.panes(term_me="idle", term_cx1="busy", _agents={"term_cx1": "codex", "term_pi1": "pi"})
        self.wake("add", "--after", "0", "--to", "codex", "--msg", "m", "--id", "n1")
        self.wake("once")
        self.assertEqual(self.sent_msgs(), [], "codex busy")
        # Codex relaunched: old pane closed, new handle
        self.panes(term_me="idle", term_cx2="idle", _agents={"term_cx2": "codex"})
        self.wake("once")
        self.assertEqual(self.sent_msgs()[0]["to"], "term_cx2")

    def test_ambiguous_agent_name_is_refused(self):
        self.panes(term_me="idle", term_a="idle", term_b="idle", _agents={"term_a": "pi", "term_b": "pi"})
        p = self.wake("add", "--idle", "pi", "--msg", "m", ok=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("2 live panes run agent 'pi'", p.stderr)

    def test_user_fire_overrides_wait(self):
        self.panes(term_me="idle", term_cx="idle", _agents={"term_cx": "codex"})
        self.wake("add", "--to", "codex", "--cmd", "false", "--msg", "review Pi", "--id", "f1", "--run", "echo GATE")
        self.wake("add", "--to", "term_me", "--cmd", "false", "--msg", "other", "--id", "f2")
        self.wake("fire", "codex", "--note", "Pi is done, go")
        msgs = self.sent_msgs()
        self.assertEqual(len(msgs), 1, "only the watches for that agent fire")
        self.assertEqual(msgs[0]["to"], "term_cx")
        for part in ("WAKE f1: woken by user: Pi is done, go", "review Pi", "GATE"):
            self.assertIn(part, msgs[0]["text"])
        self.assertIn("f2", self.wake("list").stdout)
        self.wake("fire", "f2")
        self.assertEqual(len(self.sent_msgs()), 2)
        self.assertEqual(self.wake("fire", "nothing", ok=False).returncode, 2)

    def test_cancel(self):
        self.wake("add", "--after", "0", "--msg", "m", "--id", "w9")
        self.wake("cancel", "w9")
        self.wake("once")
        self.assertEqual(self.sent_msgs(), [])
        self.assertEqual(self.wake("cancel", "w9", ok=False).returncode, 2)

    def test_state_stays_bounded(self):
        wd = os.path.join(self.tmp, "wake")
        os.makedirs(os.path.join(wd, "failed"))
        with open(os.path.join(wd, "log"), "w") as f:
            f.writelines(f"old line {i} {'x' * 100}\n" for i in range(5000))  # > 256 KiB
        old, new = (os.path.join(wd, "failed", n) for n in ("old.json", "new.json"))
        for p in (old, new):
            open(p, "w").write("{}")
        os.utime(old, (time.time() - 25 * 3600,) * 2)
        self.wake("add", "--after", "0", "--msg", "m", "--id", "b1")
        self.wake("once")
        lines = open(os.path.join(wd, "log")).read().splitlines()
        self.assertLess(len(lines), 1010, "log trimmed to ~1000 lines (a few may be appended after the trim)")
        self.assertNotIn("old line 0 ", lines[0], "trim must drop the oldest lines")
        self.assertIn("delivered b1", lines[-1], "trim must keep the newest lines")
        self.assertFalse(os.path.exists(old), "failed entries older than 24 h are deleted")
        self.assertTrue(os.path.exists(new))

    def test_default_dir_is_fixed_tmp_path(self):
        env = {k: v for k, v in self.env.items() if k != "WAKE_DIR"}
        env["TMPDIR"] = self.tmp  # a sandboxed agent's own TMPDIR must not split the state
        code = "import wake; print(wake.WAKE_DIR)"
        out = subprocess.run([sys.executable, "-c", code], env=env, cwd=HERE, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), f"/tmp/wake-{os.getuid()}")

    def test_add_starts_one_daemon(self):
        del self.env["WAKE_NO_DAEMON"]
        self.wake("add", "--cmd", "false", "--msg", "m", "--id", "d1")
        self.wake("add", "--cmd", "false", "--msg", "m", "--id", "d2")
        logpath = os.path.join(self.tmp, "wake", "log")
        for _ in range(50):  # wait up to 5 s for the daemon to log its pid
            if "daemon pid " in open(logpath).read():
                break
            time.sleep(0.1)
        time.sleep(0.3)  # a second daemon, if started, would log by now
        log = open(logpath).read()
        pids = [int(l.split("daemon pid ")[1].split()[0]) for l in log.splitlines() if "daemon pid " in l]
        try:
            self.assertEqual(log.count("daemon started by add"), 1, log)
            self.assertEqual(len(pids), 1, log)
        finally:
            for pid in pids:  # kill only this test's daemon, never a real one
                os.kill(pid, 15)


if __name__ == "__main__":
    unittest.main()
