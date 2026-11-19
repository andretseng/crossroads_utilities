#!/usr/bin/env python3
"""wake.py: let an agent stop waiting. It registers a watch, ends its turn, and gets a message in its pane later.

  wake.py add --msg TEXT [--to PANE] (--file PATH | --idle PANE | --pid PID | --cmd SHELL | --after SEC)...
              [--timeout SEC] [--run SHELL] [--id NAME]
  wake.py list | cancel ID | fire ID|PANE [--note TEXT] | once | daemon [--interval SEC]

fire = the user overrides a wait: the watch fires now (reason 'woken by user'), its --run output included.

Conditions are OR-ed. --timeout (default 3600 s) always fires with reason TIMEOUT, so nobody waits forever.
--run runs once when the watch fires; its last output is added to the message (e.g. an acceptance gate).
PANE is a handle (term_...) or an agent name (codex, pi, claude) that runs in exactly one live pane; names are
resolved at every use, so a relaunched agent is still found. `add` refuses a pane that does not exist.
The message goes to --to (default $ORCA_TERMINAL_HANDLE; prefer `--to codex`, see readme) only when that pane is idle
(orca `terminal wait --for tui-idle` satisfied), so it never lands in a busy turn or an approval prompt.
`add` starts the daemon if none is running. State is temporary: $WAKE_DIR (default /tmp/wake-<uid>, emptied at
reboot; fixed path, not $TMPDIR, so every agent shares one folder and one daemon even if a sandbox sets its own
TMPDIR): watches/, failed/, log. Bounded: log keeps its last LOG_KEEP lines, failed/ entries are deleted after
FAILED_KEEP seconds, delivered watches are deleted at once.
Standard library only; Python 3.10+.
"""
import argparse, fcntl, json, os, subprocess, sys, time, uuid

WAKE_DIR = os.path.expanduser(os.environ.get("WAKE_DIR", f"/tmp/wake-{os.getuid()}"))
LOG_MAX_BYTES, LOG_KEEP = 256 * 1024, 1000
FAILED_KEEP = 24 * 3600
WATCHES, FAILED, LOG = (os.path.join(WAKE_DIR, p) for p in ("watches", "failed", "log"))
ORCA = os.environ.get("ORCA_CLI", "orca-ide")
DELIVER_MAX = float(os.environ.get("WAKE_DELIVER_MAX", 7200))  # give up delivering after this many seconds
EMPTY_COMPOSER = ("› Ask Codex to do anything",)  # agent prompt lines shown only when idle with nothing typed
SCREEN_RECHECK = float(os.environ.get("WAKE_SCREEN_RECHECK", 5))
RUN_TIMEOUT = 300
OUT_CHARS = 600


def log(text):
    os.makedirs(WAKE_DIR, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
    if os.path.getsize(LOG) > LOG_MAX_BYTES:
        with open(LOG) as f:
            tail = f.readlines()[-LOG_KEEP:]
        with open(LOG + ".tmp", "w") as f:
            f.writelines(tail)
        os.replace(LOG + ".tmp", LOG)


def prune_failed(now):
    if not os.path.isdir(FAILED):
        return
    for name in os.listdir(FAILED):
        path = os.path.join(FAILED, name)
        if now - os.path.getmtime(path) > FAILED_KEEP:
            os.remove(path)


def save(w):
    os.makedirs(WATCHES, exist_ok=True)
    path = os.path.join(WATCHES, w["id"] + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(w, f, indent=1)
    os.replace(tmp, path)


def load_all():
    if not os.path.isdir(WATCHES):
        return []
    out = []
    for name in sorted(os.listdir(WATCHES)):
        if name.endswith(".json"):
            with open(os.path.join(WATCHES, name)) as f:
                out.append(json.load(f))
    return out


def file_sig(path):
    try:
        st = os.stat(path)
        return [st.st_mtime_ns, st.st_size]
    except FileNotFoundError:
        return None


def orca(*args, timeout=30):
    """Run the orca CLI with --json; return (exit code, parsed JSON or None)."""
    try:
        p = subprocess.run([ORCA, *args, "--json"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 99, {"error": str(e)}
    try:
        return p.returncode, json.loads(p.stdout)
    except ValueError:
        return p.returncode, None


def panes():
    rc, out = orca("terminal", "list")
    try:
        return out["result"]["terminals"]
    except (KeyError, TypeError):
        return []


def resolve(target):
    """A pane is named by handle (term_...) or by agent (codex, pi, claude: the one live pane running it).
    Returns (handle, None) or (None, problem). Agent names are resolved on every use, so a relaunched
    agent with a new handle is still found."""
    live = [t for t in panes() if t.get("connected")]
    if target.startswith("term_"):
        return (target, None) if any(t["handle"] == target for t in live) else (None, f"pane {target} not found")
    hits = [t["handle"] for t in live if (t.get("agentIdentity") or "").lower() == target.lower()]
    if len(hits) == 1:
        return hits[0], None
    return None, f"{len(hits)} live panes run agent '{target}'" + (": " + ", ".join(hits) if hits else "")


def pane_table():
    return "\n".join(f"  {t['handle']}  agent={t.get('agentIdentity') or '-'}  {t.get('title', '')[:40]}"
                     for t in panes() if t.get("connected"))


def pane_idle(target):
    """True only when orca says the agent TUI in that pane is idle (not working, not on an approval prompt)."""
    handle, _ = resolve(target)
    if handle is None:
        return False
    rc, out = orca("terminal", "wait", "--terminal", handle, "--for", "tui-idle", "--timeout-ms", "1500")
    try:
        wait = out["result"]["wait"]
    except (KeyError, TypeError):
        return False
    if rc == 0 and wait.get("satisfied") is True:
        return True
    if wait.get("blockedReason") != "agent-interactive-prompt":
        return False
    # Orca reports an idle Codex (empty composer at its prompt) as "agent-interactive-prompt" (measured
    # 2026-11-19), the same reason it gives for a real approval prompt. Tell them apart on the screen:
    # idle = the empty-composer placeholder is shown and nothing is running.
    # Codex can show the placeholder while it streams a reply between "Working" lines, so the screen must
    # look idle twice, SCREEN_RECHECK seconds apart.
    for i in range(2):
        if i:
            time.sleep(SCREEN_RECHECK)
        rc, out = orca("terminal", "read", "--terminal", handle, "--screen")
        try:
            lines = [l.strip() for l in out["result"]["terminal"]["tail"]]
        except (KeyError, TypeError):
            return False
        if not any(l in EMPTY_COMPOSER for l in lines) or any("esc to interrupt" in l for l in lines):
            return False
    return True


def deliver(target, text):
    handle, problem = resolve(target)
    if handle is None:
        return problem
    rc, out = orca("terminal", "show", "--terminal", handle)
    try:
        t = out["result"]["terminal"]
        if not (t["connected"] and t["writable"]):
            return "pane not connected/writable"
    except (KeyError, TypeError):
        return "pane not found"
    if not pane_idle(handle):
        return "pane busy"
    rc, out = orca("terminal", "send", "--terminal", handle, "--text", text, "--enter", "--wait-submit", "10")
    return None if rc == 0 else f"send failed rc={rc}"


def fired(w, now):
    """Return the reason this watch fires now, or None. May update w (idle streak)."""
    c = w["cond"]
    if "file" in c and file_sig(c["file"]) is not None and file_sig(c["file"]) != w["file_base"]:
        return "file changed: " + c["file"]
    if "pid" in c:
        try:
            os.kill(c["pid"], 0)
        except ProcessLookupError:
            return f"pid {c['pid']} exited"
        except PermissionError:
            pass
    if "cmd" in c:
        try:
            if subprocess.run(c["cmd"], shell=True, capture_output=True, timeout=60).returncode == 0:
                return "cmd ok: " + c["cmd"]
        except subprocess.TimeoutExpired:
            pass
    if "idle" in c:
        # two idle scans in a row, so a pane that has not started its job yet, or pauses for a moment, does not count
        w["idle_streak"] = w.get("idle_streak", 0) + 1 if pane_idle(c["idle"]) else 0
        if w["idle_streak"] >= 2:
            return "pane idle: " + c["idle"]
    if "after" in c and now >= w["created"] + c["after"]:
        return f"{c['after']} s passed"
    if now >= w["created"] + w["timeout"]:
        return f"TIMEOUT after {w['timeout']} s"
    return None


def run_hook(cmd):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=RUN_TIMEOUT)
        out = (p.stdout + p.stderr).strip()
        rc = p.returncode
    except subprocess.TimeoutExpired:
        out, rc = "run timed out", "timeout"
    out = " ".join(out.split())
    if len(out) > OUT_CHARS:
        out = "..." + out[-OUT_CHARS:]
    return f"run rc={rc}: {out}"


def scan(now=None):
    now = time.time() if now is None else now
    prune_failed(now)
    for w in load_all():
        path = os.path.join(WATCHES, w["id"] + ".json")
        if "text" not in w:
            reason = fired(w, now)
            if reason is None:
                save(w)  # keeps idle_streak
                continue
            text = f"WAKE {w['id']}: {reason}. {w['msg']}"
            if w.get("run"):
                text += " | " + run_hook(w["run"])
            w.update(text=text, fired_at=now)
            log(f"fired {w['id']}: {reason}")
        problem = deliver(w["to"], w["text"])
        if problem is None:
            os.remove(path)
            log(f"delivered {w['id']} to {w['to']}")
        elif now - w["fired_at"] > DELIVER_MAX:
            os.makedirs(FAILED, exist_ok=True)
            w["failed"] = problem
            with open(os.path.join(FAILED, w["id"] + ".json"), "w") as f:
                json.dump(w, f, indent=1)
            os.remove(path)
            log(f"FAILED {w['id']}: {problem}")
        else:
            w["last_problem"] = problem
            save(w)


def daemon_lock():
    os.makedirs(WAKE_DIR, exist_ok=True)
    fd = os.open(os.path.join(WAKE_DIR, "daemon.lock"), os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def ensure_daemon():
    if os.environ.get("WAKE_NO_DAEMON"):
        return
    fd = daemon_lock()
    if fd is None:
        return  # one is running
    # the daemon inherits the locked fd, so there is no gap in which a second `add` could start another daemon
    with open(LOG, "a") as logf:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "daemon"], stdin=subprocess.DEVNULL,
                         stdout=logf, stderr=logf, start_new_session=True, pass_fds=(fd,),
                         env=dict(os.environ, WAKE_LOCK_FD=str(fd)))
    os.close(fd)
    log("daemon started by add")


def cmd_add(a):
    to = a.to or os.environ.get("ORCA_TERMINAL_HANDLE")
    if not to:
        sys.exit("wake: no --to and no $ORCA_TERMINAL_HANDLE (run inside an Orca pane or pass --to)")
    cond = {k: v for k, v in (("file", a.file), ("idle", a.idle), ("pid", a.pid), ("cmd", a.cmd),
                               ("after", a.after)) if v is not None}
    if not cond:
        print("wake: give at least one of --file --idle --pid --cmd --after", file=sys.stderr)
        sys.exit(2)
    if a.file:
        cond["file"] = os.path.abspath(a.file)
    # fail loud now, not silently at delivery: $ORCA_TERMINAL_HANDLE can be stale (Codex runs tools in a
    # long-lived app-server daemon that keeps the handle of the pane it first started in)
    for what, target in (("--to", to), ("--idle", a.idle)):
        if target and resolve(target)[0] is None:
            src = " (from $ORCA_TERMINAL_HANDLE, which can be stale)" if what == "--to" and not a.to else ""
            print(f"wake: {what} {target}{src}: {resolve(target)[1]}. Pass a live handle or an agent name "
                  f"(codex, pi, claude) that runs in exactly one pane. Live panes:\n{pane_table()}", file=sys.stderr)
            sys.exit(2)
    w = {"id": a.id or time.strftime("%H%M%S-") + uuid.uuid4().hex[:4], "to": to, "msg": a.msg, "cond": cond,
         "timeout": a.timeout, "run": a.run, "created": time.time(),
         "file_base": file_sig(cond["file"]) if a.file else None}
    if os.path.exists(os.path.join(WATCHES, w["id"] + ".json")):
        print(f"wake: watch {w['id']} exists; cancel it first", file=sys.stderr)
        sys.exit(2)
    save(w)
    log(f"added {w['id']} to={to} cond={cond} timeout={a.timeout}")
    ensure_daemon()
    print(w["id"])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="op", required=True)
    ad = sub.add_parser("add")
    ad.add_argument("--msg", required=True)
    ad.add_argument("--to")
    ad.add_argument("--file")
    ad.add_argument("--idle")
    ad.add_argument("--pid", type=int)
    ad.add_argument("--cmd")
    ad.add_argument("--after", type=float)
    ad.add_argument("--timeout", type=float, default=3600)
    ad.add_argument("--run")
    ad.add_argument("--id")
    sub.add_parser("list")
    ca = sub.add_parser("cancel")
    ca.add_argument("id")
    fi = sub.add_parser("fire")
    fi.add_argument("target", help="watch id, pane handle or agent name: fire all its waiting watches now")
    fi.add_argument("--note", help="text from the user added to the message")
    sub.add_parser("once")
    da = sub.add_parser("daemon")
    da.add_argument("--interval", type=float, default=30)
    a = p.parse_args()

    if a.op == "add":
        cmd_add(a)
    elif a.op == "list":
        now = time.time()
        for w in load_all():
            state = "fired, delivering (" + w.get("last_problem", "") + ")" if "text" in w else "waiting"
            print(f"{w['id']}  to={w['to']}  {state}  age={int(now - w['created'])}s  cond={w['cond']}  msg={w['msg']}")
    elif a.op == "cancel":
        path = os.path.join(WATCHES, a.id + ".json")
        if not os.path.exists(path):
            print(f"wake: no watch {a.id}", file=sys.stderr)
            sys.exit(2)
        os.remove(path)
        log(f"cancelled {a.id}")
    elif a.op == "fire":
        # the user overrides a wait: fire now (with --run output) instead of waiting for the condition
        ws = [w for w in load_all() if "text" not in w and (w["id"] == a.target or w["to"] == a.target
                                                           or resolve(w["to"])[0] == resolve(a.target)[0] != None)]
        if not ws:
            print(f"wake: no waiting watch matches {a.target} (give a watch id, a handle or an agent name)",
                  file=sys.stderr)
            sys.exit(2)
        now = time.time()
        for w in ws:
            text = f"WAKE {w['id']}: woken by user{': ' + a.note if a.note else ''}. {w['msg']}"
            if w.get("run"):
                text += " | " + run_hook(w["run"])
            w.update(text=text, fired_at=now)
            save(w)
            log(f"fired {w['id']}: by user")
        scan()  # deliver now if the pane is idle; otherwise the daemon delivers when it is
        ensure_daemon()
    elif a.op == "once":
        scan()
    elif a.op == "daemon":
        fd = int(os.environ.pop("WAKE_LOCK_FD")) if "WAKE_LOCK_FD" in os.environ else daemon_lock()
        if fd is None:
            print("wake: daemon already running", file=sys.stderr)
            sys.exit(0)
        log(f"daemon pid {os.getpid()} interval {a.interval}s")
        while True:
            try:
                scan()
            except Exception as e:  # keep the daemon alive; the error is in the log
                log(f"scan error: {e!r}")
            time.sleep(a.interval)


if __name__ == "__main__":
    main()
