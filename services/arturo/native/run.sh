#!/bin/bash
# Native GPT-Live voice service (127.0.0.1:${ARTURO_NATIVE_PORT:-5076}). Run in tmux session 'arturo-native':
#   tmux new-session -d -s arturo-native "bash services/arturo/native/run.sh 2>&1 | tee -a logs/arturo-native.log"
# It imports arturo-proxy for Arturo's tools; it must NEVER set ARTURO_STREAM_RELAY, ARTURO_WARM_VOICES_CACHE or
# ARTURO_FACTS_RECALL (those start the proxy's daemons, which belong to the proxy process alone).
set -u
cd "$(dirname "$0")/../../.."
export ORCHESTRA_DIR="${ORCHESTRA_DIR:-$PWD}"
export ARTURO_NATIVE_GPTLIVE="${ARTURO_NATIVE_GPTLIVE:-0}"          # OFF unless set: every call is refused (503)
export ARTURO_NATIVE_CLAIM_GATE="${ARTURO_NATIVE_CLAIM_GATE:-1}"    # escape hatch: 0 = no hold, no claim check
export ARTURO_NATIVE_SESSION_MAX_USD="${ARTURO_NATIVE_SESSION_MAX_USD:-0.50}"
# Screen context from the app ({type:context}) -> GPT-Live. Probe 10-16 16:3x ET ($0.023): both appends acked, neither
# spoken (idle + delegation pending). Undo: ARTURO_NATIVE_CONTEXT=0.
export ARTURO_NATIVE_CONTEXT="${ARTURO_NATIVE_CONTEXT:-1}"
export VOICE_DAILY_CAP_MIN="${VOICE_DAILY_CAP_MIN:-60}"             # same daily cap as the relay (run.sh)
# The knowledge tool reads the facts store (2026-11-25: native never did; ARTURO_FACTS_RECALL also starts a thread).
export ARTURO_KNOWLEDGE_FACTS="${ARTURO_KNOWLEDGE_FACTS:-1}"
# A call opens with the thread above it and hears the chat during it (call_context). Undo: ARTURO_NATIVE_THREAD_CONTEXT=0.
export ARTURO_NATIVE_THREAD_CONTEXT="${ARTURO_NATIVE_THREAD_CONTEXT:-1}"
unset ARTURO_STREAM_RELAY ARTURO_WARM_VOICES_CACHE ARTURO_FACTS_RECALL
echo "native run.sh: :${ARTURO_NATIVE_PORT:-5076} enabled=$ARTURO_NATIVE_GPTLIVE claim_gate=$ARTURO_NATIVE_CLAIM_GATE session_max_usd=$ARTURO_NATIVE_SESSION_MAX_USD context=$ARTURO_NATIVE_CONTEXT thread=$ARTURO_NATIVE_THREAD_CONTEXT knowledge_facts=$ARTURO_KNOWLEDGE_FACTS daily_cap_min=$VOICE_DAILY_CAP_MIN"
exec python3 -m services.arturo.native.server
