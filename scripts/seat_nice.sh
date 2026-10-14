#!/usr/bin/env bash
# seat_nice.sh: the CPU priority a seat's CLI (and every child it starts) runs at.
#
# Seats start under `nice -n N` so the services (API, gateway, dashboard, router, at 0) keep the
# CPU when a seat's workload is heavy. On a 2-vCPU box a seat's pipeline at 73% CPU made
# /api/agents take 2-9 s and the dashboard show "Connection lost"; at nice 10 it answered in
# ~0.16 s. N comes from ORCHESTRA_SEAT_NICE ([seats] nice in orchestra.toml), default 10;
# 0 turns it off. Only 0-19 is accepted: a negative value would need root, and anything else
# keeps the default with a warning. Sourced by spawn-agent.sh and scripts/agent-recovery.sh.

SEAT_NICE_DEFAULT=10
_SEAT_NICE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Prints the prefix to put in front of a seat's launch line ("nice -n 10 ", or "" when off).
# Boot recovery does not source orchestra-env.sh, so with no ORCHESTRA_SEAT_NICE in the env the
# value is read from orchestra.toml through the settings module (the one place it is defined).
seat_nice_prefix() {
    local n="${ORCHESTRA_SEAT_NICE-}"
    if [[ -z "${ORCHESTRA_SEAT_NICE+x}" ]]; then
        n="$(cd "$_SEAT_NICE_ROOT" && python3 -c \
            'from orchestra_cli.settings import load_settings; print(load_settings().seat_nice)' \
            2>/dev/null)" || n=""
        [[ -z "$n" ]] && n="$SEAT_NICE_DEFAULT"
    fi
    if [[ ! "$n" =~ ^[0-9]+$ ]] || (( 10#$n > 19 )); then
        echo "ORCHESTRA_SEAT_NICE='$n' is not a whole number 0-19; seats start at nice $SEAT_NICE_DEFAULT" >&2
        n="$SEAT_NICE_DEFAULT"
    fi
    n=$(( 10#$n ))
    (( n == 0 )) && return 0
    printf 'nice -n %d ' "$n"
}
