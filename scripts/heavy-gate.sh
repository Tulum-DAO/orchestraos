#!/usr/bin/env bash
# heavy-gate.sh: run one heavy gate command (a test suite, tsc, a build, a scan) under the
# machine-wide heavy-gate lock, so runs queue on a shared box instead of all starting at once.
#
# Usage: scripts/heavy-gate.sh <command> [args...]
#
# The lock is the one the pre-push hook (.git-hooks/pre-push) takes: ORCHESTRA_GATE_LOCK, default
# ${XDG_RUNTIME_DIR:-/tmp}/orchestra-heavy-gate.lock, with the holder's pid in "$LOCK.pid". A
# second run prints who it is waiting for on stderr and blocks until the lock is free.
#
# The wrapper execs the command, so the command replaces this shell: its exit code is the
# wrapper's exit code, and fd 9 (the lock) is inherited by it and stays open for the command's
# whole life. The lock is released by the kernel when the command's last process holding fd 9
# exits; nothing has to clean up, and a killed command releases it too.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: scripts/heavy-gate.sh <command> [args...]" >&2
  echo "  runs the command under the machine-wide heavy-gate lock (ORCHESTRA_GATE_LOCK)" >&2
  exit 2
fi

LOCK="${ORCHESTRA_GATE_LOCK:-${XDG_RUNTIME_DIR:-/tmp}/orchestra-heavy-gate.lock}"
exec 9>>"$LOCK"
if ! flock -n 9; then
  echo "heavy-gate: waiting for the heavy-gate lock held by pid $(cat "$LOCK.pid" 2>/dev/null || echo '?') ($LOCK)" >&2
  flock 9
fi
echo $$ > "$LOCK.pid"

exec "$@"
