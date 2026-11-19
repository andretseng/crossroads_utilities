#!/usr/bin/env bash
# pi_watch.sh: emits one line when the current Pi task's REPORT.md changes (with the gate result), and when anyone writes
# into ~/utilities outside Claude's commits. The current task is read from /tmp/pi_current (line 1: work dir; line 2: tool;
# line 3: test files), so Claude re-points it without restarting the monitor.
m0=""
while true; do
  if [ -f /tmp/pi_current ]; then
    W=$(sed -n 1p /tmp/pi_current); T=$(sed -n 2p /tmp/pi_current); TESTS=$(sed -n 3p /tmp/pi_current)
    m=$(stat -c %Y "$W/REPORT.md" 2>/dev/null || echo none)
    if [ "$m" != "$m0" ]; then
      [ -n "$m0" ] && [ "$m" != none ] && echo "PI REPORT $W: $($HOME/utilities/firmware/accept.sh $W $T $TESTS 2>&1 | grep -E 'ACCEPT|REJECT|^OK|FAILED' | tr '\n' ' ')"
      m0=$m
    fi
  fi
  s=$(git -C $HOME/utilities status --short --untracked-files=no); [ -n "$s" ] && [ ! -f /tmp/claude_copying ] && echo "UNEXPECTED WRITE in ~/utilities: $s"
  sleep 30
done
