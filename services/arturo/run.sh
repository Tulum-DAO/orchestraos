#!/usr/bin/env bash
# run.sh — durable, single-writer launcher for the Arturo voice-brain clone (:5071).
#
# WHY: the gm-injection flag ARTURO_GM_INJECT used to live only in an ad-hoc tmux launch env, so
# a bare restart/respawn dropped it — and a stale flagless duplicate once spawned alongside the
# real one (2026-08-10 16:44), so TWO :5071 processes each journaled the operator's ONE call and the wrong
# (thin) shard injected. That duplicate was the bigger half of the fragmentation root cause.
# This script makes the flag survive restarts AND enforces the SINGLE-WRITER invariant: it kills
# ANY existing arturo-proxy python process before launching exactly one.
#
# Usage:  services/arturo/run.sh            # inject ON  (default — genuine the operator calls inject to gm)
#         ARTURO_GM_INJECT=0 services/arturo/run.sh   # inject OFF (kill switch)
set -euo pipefail

# Repo root (this file lives at <root>/services/arturo/run.sh); overridable.
ORCH="${ORCHESTRA_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$ORCH"
# shellcheck source=../../scripts/orchestra-env.sh
source "$ORCH/scripts/orchestra-env.sh"

# SINGLE-WRITER GUARANTEE: never two processes journaling the same call. Kill any existing
# arturo-proxy python instance (match the exact script path so we never hit this script or an
# unrelated python). Idempotent — safe to run repeatedly.
mapfile -t OLD < <(pgrep -f "python3 services/arturo/arturo-proxy.py" || true)
if [ "${#OLD[@]}" -gt 0 ]; then
  echo "run.sh: killing ${#OLD[@]} existing arturo-proxy process(es): ${OLD[*]}"
  kill "${OLD[@]}" 2>/dev/null || true
  sleep 2
  # hard-kill any survivor so we can never end up with two writers
  mapfile -t STILL < <(pgrep -f "python3 services/arturo/arturo-proxy.py" || true)
  if [ "${#STILL[@]}" -gt 0 ]; then
    echo "run.sh: force-killing survivors: ${STILL[*]}"
    kill -9 "${STILL[@]}" 2>/dev/null || true
    sleep 1
  fi
fi

# Durable flag: default ON (inject), overridable to 0 for the kill switch.
export ARTURO_GM_INJECT="${ARTURO_GM_INJECT:-1}"
# Durable delivery target (the operator 2026-08-25: revert the 08-21 gemini-gm reroute -> canonical gm, single-gm).
# Survives restart like ARTURO_GM_INJECT; matches the S1 code default. Deep-brain host is separate (Tier-B held).
export VOICE_BRAIN_SESSION="${VOICE_BRAIN_SESSION:-gm}"
# Durable semantic memory recall flag (the operator approved apr_c8a16ad9_95501904): default ON (1)
export ARTURO_SEMANTIC_RECALL="${ARTURO_SEMANTIC_RECALL:-1}"
# Durable facts.db recall flag (the operator-approved additive read-side wiring, semantic-memory-audit
# 2026-09-14): default ON (1). Bounded read-only retrieval from state/brain/facts.db (~23.5k
# daily-refreshed facts) appended as a FACTS block alongside semantic recall. Override to 0 to disable.
export ARTURO_FACTS_RECALL="${ARTURO_FACTS_RECALL:-1}"
# Durable L2 worldview recall flag (P1.e @ba21031734, gm by-effect PASS msg_36403326_61969331): default ON (1).
export ARTURO_WORLDVIEW_L2="${ARTURO_WORLDVIEW_L2:-1}"
# Durable voice layer dispatcher flag (the operator approved apr_868cbd37_95502061): default ON (1)
export ARTURO_DISPATCHER="${ARTURO_DISPATCHER:-1}"
# Durable ended-once guard flag: default ON (1)
export ARTURO_ENDED_ONCE="${ARTURO_ENDED_ONCE:-1}"
# v2/(b) watch stream relay + the operator-directed live-partials (DEC-1788843712854271, gm GO
# msg_2d139122 coordinated install): relay default ON (1).
# COST PROTECTION (gm-g52 2026-09-08): live-partials DEFAULT flipped 1->0 — the partials
# engine re-STTs the whole growing buffer every 1.5s (ptt_stream.py:246), O(T^2), which
# burned ~21.5k EL credits in ~10min and caused the credit_balance_exhausted 429 storm.
# Default OFF until srw-dev's O(T) sliding-window + per-conversation Scribe budget-cap fix
# lands (gm-gated, folds into the tombstone reland). Env override still works
# (ARTURO_STREAM_PARTIALS=1) and the flag re-enables partials safely once the fix is live.
export ARTURO_STREAM_RELAY="${ARTURO_STREAM_RELAY:-1}"
export ARTURO_STREAM_PARTIALS="${ARTURO_STREAM_PARTIALS:-0}"
# Idle-close (DEC-1788855933961869): default OFF — arming it is a deliberate flip AFTER
# on-wrist VAD_RMS_FLOOR calibration, never automatic at a restart.
export ARTURO_STREAM_IDLE_CLOSE="${ARTURO_STREAM_IDLE_CLOSE:-0}"
# agent_speaking boundary (DEC-1788854280517526): default ON since the 2026-09-09 22:23Z
# SPEAKING-flip (g16's word, watch 216 echo-gate ON THE WRIST; gm ruling msg_61b5007e:
# default must match the live contract — a watchdog crash-restart must not revert it).
export ARTURO_STREAM_SPEAKING="${ARTURO_STREAM_SPEAKING:-1}"
# Per-vendor daily voice cap (SHAW DECISION 2026-09-08 22:05Z via native menu, ios
# msg_e5e964b6): 60 min/day/vendor before Hume credits get added. voice_usage.py reads this
# at import; 80% fires one card, 100% refuses NEW calls 503 for that vendor.
export VOICE_DAILY_CAP_MIN="${VOICE_DAILY_CAP_MIN:-60}"
# Diagnostic uplink PCM tap (ios msg_58d8b8af): =1 appends raw per-conversation uplink to
# state/uplink-tap/<cid>.pcm (10MB cap, never affects the call). Default ON while g16's
# wrist-audio garble diagnosis is open (gm ruling msg_61b5007e); flips back to 0 by a
# one-line commit when that diagnosis closes.
export ARTURO_UPLINK_TAP="${ARTURO_UPLINK_TAP:-1}"
# Warm the Hume voices-list cache at boot (picker cold-fetch timed out on the Funnel).
export ARTURO_WARM_VOICES_CACHE="${ARTURO_WARM_VOICES_CACHE:-1}"
# Hume reply-text streaming to the watch (ios contract msg_0864f940/(a) msg_57c21e6c):
# default ON since 2026-09-10 07:42Z — both keys turned: gm gate PASS msg_00e3b245 + ios
# 'watch 221 on wrist' word msg_0019502f (>=218 client leg renders the partial upserts).
export ARTURO_STREAM_AGENT_TEXT="${ARTURO_STREAM_AGENT_TEXT:-1}"
# Orphan reaper (wave-6 @9942dcaf62, gm gate PASS against live + arming decision msg_26ec1688):
# dead client (no uplink feed AND no events poll for ARTURO_ORPHAN_END_S=60) => full end().
# Default ON so a watchdog restart cannot silently drop it.
export ARTURO_ORPHAN_END="${ARTURO_ORPHAN_END:-1}"
echo "run.sh: launching single Arturo instance on :5071 (ARTURO_GM_INJECT=$ARTURO_GM_INJECT, VOICE_BRAIN_SESSION=$VOICE_BRAIN_SESSION, ARTURO_SEMANTIC_RECALL=$ARTURO_SEMANTIC_RECALL, ARTURO_DISPATCHER=$ARTURO_DISPATCHER, ARTURO_ENDED_ONCE=$ARTURO_ENDED_ONCE, ARTURO_STREAM_RELAY=$ARTURO_STREAM_RELAY, ARTURO_STREAM_PARTIALS=$ARTURO_STREAM_PARTIALS, ARTURO_STREAM_IDLE_CLOSE=$ARTURO_STREAM_IDLE_CLOSE, ARTURO_STREAM_SPEAKING=$ARTURO_STREAM_SPEAKING, VOICE_DAILY_CAP_MIN=$VOICE_DAILY_CAP_MIN)"
exec python3 services/arturo/arturo-proxy.py
