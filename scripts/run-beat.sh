#!/usr/bin/env bash
# The rotation lane's scheduler entrypoint. Exports ORCHESTRA_* from
# orchestra.toml, then runs one of the beat scripts by name. This is what the
# install path's scheduler (cron, or a supervisor timer — see
# orchestra.toml's [rotation] cadence keys) should invoke, rather than the
# raw python3 invocations the private reference install used directly.
#
# Usage: scripts/run-beat.sh {bus|cron|boundary}
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/orchestra-env.sh"

case "${1:-}" in
  bus)
    exec python3 "$ROOT/scripts/lineage_daemon/bus_beat.py"
    ;;
  cron)
    exec python3 "$ROOT/scripts/lineage_daemon/cron_beat.py"
    ;;
  boundary)
    export BOUNDARY_DELIVER_ARMED=1
    exec python3 "$ROOT/scripts/lineage_daemon/boundary_delivery.py"
    ;;
  *)
    echo "usage: $0 {bus|cron|boundary}" >&2
    exit 2
    ;;
esac
