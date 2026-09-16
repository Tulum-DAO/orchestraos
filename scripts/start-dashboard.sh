#!/usr/bin/env bash
# Thin launcher: exports ORCHESTRA_* from orchestra.toml, then runs the
# dashboard proxy. dashboard-proxy.js can't source a bash file itself, so
# this wrapper is what a supervisor (systemd unit, tmux, doctor) should point
# at instead of `node dashboard-proxy.js` directly.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/orchestra-env.sh"
exec node "$ROOT/dashboard-proxy.js"
