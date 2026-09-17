#!/usr/bin/env bash
# Source this before any MANUAL by-effect proof that spawns or rotates seats on a host that
# also runs a live fleet:  `source scripts/test_sandbox_env.sh [name]`
#   - isolated tmux server (TMUX unset, TMUX_TMPDIR under /tmp/orchestraos-test-<name>):
#     the fleet's router, orphan scan and identity store never see the test seats;
#   - isolated Claude config dir (hooks, settings, trust, transcripts all land there);
#   - ORCHESTRA_CONFIG / data dir under the same sandbox.
# Teardown:  orchestra_sandbox_down   (kills the isolated tmux server; the dir can be removed).
name="${1:-default}"
export ORCHESTRA_SANDBOX="/tmp/orchestraos-test-$name"
mkdir -p "$ORCHESTRA_SANDBOX/tmux" "$ORCHESTRA_SANDBOX/cfg" "$ORCHESTRA_SANDBOX/data"
unset TMUX
export TMUX_TMPDIR="$ORCHESTRA_SANDBOX/tmux"
export CLAUDE_CONFIG_DIR="$ORCHESTRA_SANDBOX/cfg"
export ORCHESTRA_CONFIG="$ORCHESTRA_SANDBOX/orchestra.toml"
export ORCHESTRA_REALTIME_DIR="$ORCHESTRA_SANDBOX/data/realtime"   # telemetryd snapshot, never ~/.orchestra/realtime
export ORCHESTRA_ROOT="${ORCHESTRA_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
orchestra_sandbox_down() {   # kill-server is refused on shared hosts; kill each sandbox session
  for s in $(tmux ls -F '#S' 2>/dev/null); do tmux kill-session -t "$s"; done
  echo "sandbox tmux sessions down ($TMUX_TMPDIR)"
}
echo "sandbox: $ORCHESTRA_SANDBOX (tmux socket dir $TMUX_TMPDIR, config dir $CLAUDE_CONFIG_DIR)"
