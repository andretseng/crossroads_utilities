#!/usr/bin/env bash
# push.sh — push crossroads ~/utilities (crossroads only; dreammaker has its own ~/utilities and remotes).
# At home (LAN): push to all remotes — origin (GitHub), gitea, datejust.
# Away from home: push to origin (GitHub) only.
# Home is detected by reaching the Gitea host and the datejust ssh port on the LAN.
set -u
cd "$HOME/utilities" || exit 1
branch=$(git branch --show-current)
reach() { timeout 3 bash -c "</dev/tcp/$1/$2" 2>/dev/null; }

if reach 192.168.33.119 22 && reach datejust 9588; then
  remotes="origin gitea datejust"; where="home LAN"
else
  remotes="origin"; where="away from home"
fi
echo "$where: pushing $branch to $remotes"

fail=0
for r in $remotes; do
  if GIT_TERMINAL_PROMPT=0 git push "$r" "$branch" >/dev/null 2>&1; then
    echo "  $r: $(git ls-remote "$r" "refs/heads/$branch" | cut -c1-7)"
  else
    echo "  $r: PUSH FAILED"; fail=1
  fi
done
echo "  local: $(git rev-parse --short HEAD)"
exit $fail
