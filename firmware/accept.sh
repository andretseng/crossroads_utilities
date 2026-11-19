#!/usr/bin/env bash
# accept.sh WORK_DIR TOOL.py TEST_FILE... : Claude's acceptance gate for code written by Pi (or any implementer).
# Passes only when:
#   1. every acceptance test file in WORK_DIR is byte-identical to the committed copy in ~/utilities/firmware (not edited),
#   2. all those tests pass when run from WORK_DIR, with no test skipped,
#   3. the tool has no skip/xfail tricks and no hard-coded case/project names.
# The implementer's own report and explanations are never part of acceptance.
set -u
WORK=$1; TOOL=$2; shift 2
REPO=$HOME/utilities/firmware
fail=0
for t in "$@"; do
  if ! cmp -s "$WORK/$t" "$REPO/$t"; then echo "REJECT: $t differs from the committed acceptance test"; fail=1; fi
done
out=$(cd "$WORK" && python3 -m unittest -v "${@%.py}" 2>&1)
echo "$out" | tail -3
echo "$out" | grep -qE "^OK$|^OK \(" || { echo "REJECT: tests do not pass"; fail=1; }
echo "$out" | grep -q "skipped" && { echo "REJECT: tests were skipped:"; echo "$out" | grep "skipped" | head -5; fail=1; }
grep -nE "unittest\.skip|expectedFailure|sys\.exit\(0\)" "$WORK/$TOOL" && { echo "REJECT: test-dodging code in $TOOL"; fail=1; }
grep -nE "escape32|softuart|dab_F28003x|cia402|servo_drive|mspm0\+|/workspace_ccstheia/" "$WORK/$TOOL" && { echo "REJECT: case or project names in $TOOL"; fail=1; }
[ $fail -eq 0 ] && echo "ACCEPT (gate only — Claude still spot-checks real projects before copying)"
exit $fail
