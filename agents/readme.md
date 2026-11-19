# agents/ — tools for agents running in Orca panes

## wake.py — stop polling, get woken

An agent (Codex, Claude, Pi) that must wait for something registers a watch and ENDS ITS TURN.
A small daemon (no model, no tokens) checks the watches every 30 s. When one fires, it types one line
into the agent's pane, but only when that pane is idle (orca `tui-idle`), never into a busy turn
or an approval prompt. That line is the agent's next turn.

Rule for agents: **never wait with sleep, `orca terminal wait`, or repeated status checks.
Register a watch, then end the turn.** Act when a `WAKE <id>: ...` line arrives.

```bash
W=~/utilities/agents/wake.py
# Pi writes report.md -> wake me, with the gate result in the message; 30 min heartbeat via --timeout
$W add --to codex --file /tmp/x_pi/report.md --idle pi --timeout 1800 --msg "Pi x reported: review it" \
       --run "~/utilities/firmware/accept.sh /tmp/x_pi x.py test_x.py | tail -3"
# Pi pane went idle without a report (stuck or stopped): idle on 2 scans in a row
$W add --to codex --idle pi --timeout 1800 --msg "Pi pane idle: check report or screen"
# build process ended / a command succeeds / plain timer
$W add --pid 12345 --msg "build ended"
$W add --cmd "test -f /tmp/done.flag" --msg "flag"
$W add --after 1800 --msg "30 min check"
$W list            # watches and their state
$W cancel <id>     # drop a watch you no longer need (e.g. the idle watch after the report arrived)
$W fire codex --note "Pi is done"   # USER override: fire codex's waiting watches now (id or pane also ok)
```

- Conditions are OR-ed; `--timeout` (default 3600 s) always fires as `TIMEOUT`, so no watch waits forever.
  For a periodic check, re-register on each wake.
- A pane is a handle (`term_...`) or an agent name (`codex`, `pi`, `claude`) that runs in exactly one live
  pane; names are resolved at each use, so a relaunched Pi or Codex is still found. **Codex: always pass
  `--to codex`.** Codex runs tools in a long-lived app-server daemon whose `$ORCA_TERMINAL_HANDLE` is the
  pane it first started in, which may be closed (2026-11-19: watches went to a dead pane). `add` refuses a
  pane that does not exist and lists the live ones. `--to` defaults to `$ORCA_TERMINAL_HANDLE`.
- The user can always override a wait: `wake.py fire <agent|id>` fires now (`woken by user`, with `--run`
  output), or the user just types in the agent's pane. A user message always takes priority over a watch.
- `--run` runs once when the watch fires; the last 600 chars of its output go into the message.
- `add` starts the daemon if none is running (one per user, via a lock). State (temporary): `/tmp/wake-<uid>/` (here `/tmp/wake-1001`), emptied at reboot; fixed path so all agents share one daemon; log capped at 1000 lines, `failed/` entries deleted after 24 h; delivered watches deleted at once:
  (`watches/`, `failed/`, `log`). If a message cannot be delivered for 2 h (`WAKE_DELIVER_MAX`),
  the watch moves to `failed/`.
- Tests: `cd ~/utilities/agents && python3 -m unittest test_wake` (fake orca CLI, no Orca needed).
