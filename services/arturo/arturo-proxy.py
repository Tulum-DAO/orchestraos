#!/usr/bin/env python3
"""
Custom-LLM Proxy — Bridges ElevenLabs voice to Gemini with live context + REAL tool execution.

ElevenLabs sends OpenAI chat-completions format → proxy injects context + tools →
calls Gemini → if tool_call, EXECUTES it (SSH to Mac, voice-api) → feeds result back →
streams final response as SSE to ElevenLabs.

Port: 5071 (ARTURO dedicated instance — CLONE of the live :5052 proxy) | Auth: Bearer token

ARTURO INSTANCE: this is the operator's 2026-08-10 clone ruling — a dedicated Arturo voice
brain on its OWN port, so the live :5052 Bradford proxy is NEVER touched. All
STATE WRITES are namespaced under state/arturo/ + logs/arturo/ so this instance
cannot clobber the live proxy's full-overwrite files (voice-memory / voice-live-call
/ voice-call-trace). Shared READS (context enrichment) stay on the canonical files.
"""

import json
import os
import re
import hmac
import subprocess
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, request, Response, jsonify

# --- Intent Router + Conversation Log ---
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))
    from intent_router import route_message as _intent_route
    from conversation_log import log_conversation as _conv_log
    print("[JARVIS] Intent router + conversation log loaded")
except Exception as _e:
    _intent_route = None
    _conv_log = None
    print(f"[JARVIS] Router/log load failed: {_e}")

# --- Config ---

app = Flask(__name__)
log = logging.getLogger("custom-llm")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# DATA dir (state/, logs/, registry.json) — from orchestra.toml via the supervisor /
# orchestra-env.sh; defaults to the checkout so a bare run still works.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", str(_REPO_ROOT)))
OMNI_DIR = Path(os.environ.get("OMNI_CONTEXT_DIR", str(Path.home() / "scripts" / "omni-context")))
SECRETS_FILE = ORCHESTRA_DIR / ".env.secrets"
MAC_IP = os.environ.get("ORCHESTRA_MAC_TAILSCALE_IP", "")
MAC_USER = os.environ.get("ORCHESTRA_MAC_SSH_USER", "")
MAC_SSH_KEY = str(Path.home() / ".ssh" / "id_ed25519")
VOICE_API = "http://127.0.0.1:5051"

# ARTURO INSTANCE — namespaced write roots (never clobber the live :5052 proxy's files).
ARTURO_PORT = int(os.environ.get("ORCHESTRA_ARTURO_PORT", "5071"))
ARTURO_STATE = ORCHESTRA_DIR / "state" / "arturo"
ARTURO_LOGS = ORCHESTRA_DIR / "logs" / "arturo"

# --- §2 per-call transcript journal wiring (spec §2 / agent-state-truth §7 seam) ---
# ElevenLabs' custom-LLM POST carries NO conversation id / user / metadata (verified) — just a
# bare OpenAI body. So a request is resolved to its call by USER-TURN OVERLAP against LIVE
# journals on disk (_resolve_or_create → call_journal.find_matching_call): robust to proxy
# restarts (disk is source of truth) and to ElevenLabs reshaping/truncating history mid-call.
# (The prior first-user-message hash + in-memory map + random filename forked a NEW journal on
# every restart / window-shift — gm caught a real call fragmented into 4 journals.) Journals are
# written to the SHARED state/voice-calls/vc_<id>.json (the gateway + app poller read it).
import threading as _threading
import sys as _sys
# Run as a bare script so the `services` package isn't importable by default — add the CODE
# root (this checkout) so `services.arturo.*` resolves both as-script and as-module. Never
# the data dir: with the code/data split (`orchestra init` puts data outside the checkout)
# the two differ, and the data dir has no `services` package.
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))
from services.arturo.call_journal import CallJournal as _CallJournal

# B1-thin: shared tasks.db connect (WAL + 30s busy_timeout). __file__-relative so it
# resolves to THIS checkout's scripts/ (worktree now, live after merge), not a fixed home path.
import os as _os_dbc
_scripts_dbc = _os_dbc.path.join(
    _os_dbc.path.dirname(_os_dbc.path.dirname(_os_dbc.path.dirname(_os_dbc.path.abspath(__file__)))),
    "scripts")
if _scripts_dbc not in _sys.path:
    _sys.path.insert(0, _scripts_dbc)
import db_connect

# VOICE_CALLS_DIR is env-overridable (gm): tests/verification MUST point at a SCRATCH dir via
# ARTURO_VOICE_CALLS_DIR so they never write the LIVE state/voice-calls/ the app panel reads.
# Default = the live shared seam dir (what the gateway + poller consume).
VOICE_CALLS_DIR = Path(os.environ.get("ARTURO_VOICE_CALLS_DIR",
                                      str(ORCHESTRA_DIR / "state" / "voice-calls")))
_JOURNALS_LOCK = _threading.Lock()   # serializes disk resolve/create/append (Flask threaded=True)

# P6 once-guard (fold-in msg_9b31854d, behind ARTURO_ENDED_ONCE=1): per-CALL cross-journal
# "Voice call ended" inject ledger — enforces kill-switch gate (1) "exactly one injection per
# real call" across the watchdog/server path, the client durable path, and ASR-fork shards.
ENDED_ONCE_LEDGER = VOICE_CALLS_DIR / ".ended-injects.json"

# v2/(b) Watch-Arturo stream relay (DEC-1788843712854271, ARCHITECTURE-B.md @85c97fdee64a,
# gm RED-first msg_ad043c03): server-held EL Conv-AI socket per conversation_id; watch = thin
# relay. INERT unless ARTURO_STREAM_RELAY=1 (routes not even registered flag-off). The relay
# creates NO journals — the exactly-one journal per conversation comes from the EL custom-LLM
# callback into chat_completions (locked gate criterion). Finalize is server-driven on relay
# close; exactly-once inject rides the ended-once ledger.
_STREAM_RELAY = None
if os.environ.get("ARTURO_STREAM_RELAY") == "1":
    from services.arturo import stream_relay as _stream_relay_mod

    def _relay_finalize(_cid):
        call_id = _find_live_by_conv_id(_cid)
        if call_id:
            _finalize_journal_file(VOICE_CALLS_DIR / f"{call_id}.json")
    from services.arturo import voice_usage as _voice_usage_mod
    # Task 9 (spec §4.6): per-vendor daily minutes — 80% card-alert, 100% refuses NEW calls 503.
    _STREAM_RELAY = _stream_relay_mod.RelayManager(on_finalize=_relay_finalize,
                                                   usage=_voice_usage_mod.UsageStore())

    def _warm_voices_cache():
        # ios msg_64481ea2: warm the aggregated Hume voices list at boot so the picker's
        # first open never eats the cold double-provider fetch. Best-effort, never fatal.
        try:
            from services.arturo import voice_choice as _vc_warm
            _vc_warm.cached_hume_voices()
        except Exception:
            pass
    # env-gated (run.sh sets it) so test imports of this module never touch the network
    if os.environ.get("ARTURO_WARM_VOICES_CACHE", "") == "1":
        _threading.Thread(target=_warm_voices_cache, daemon=True, name="voices-cache-warm").start()
        _STREAM_RELAY.daemons.append("voices-cache-warm")
        log.info("voices cache warm started")


def _warm_facts_cache():
    # Warm the facts query at boot (mirrors the voices-cache-warm pattern) so turn 1
    # after a cold boot doesn't lose the FACTS block to the 250ms budget wall, then stay
    # resident: re-warm every 30 min and during a 15-min-idle stretch (a long-idle
    # proxy re-cools its page cache). Best-effort, read-only, never fatal.
    try:
        from services.arturo import facts_recall as _fr_warm
        _fr_warm.warm_cache()
        _fr_warm.keep_warm(interval_s=1800, idle_s=900, tick_s=60)
    except Exception:
        pass
# env-gated (run.sh sets it) so test imports of this module never touch the live db
if os.environ.get("ARTURO_FACTS_RECALL") == "1":
    _threading.Thread(target=_warm_facts_cache, daemon=True, name="facts-cache-warm").start()
    if _STREAM_RELAY is not None:
        _STREAM_RELAY.daemons.append("facts-cache-warm")
    log.info("facts cache warm started")

# KILL SWITCH (the operator order 2026-08-10 — "WHY DO YOU KEEP GETTING INTERRUPTED BY CALLS I HAVEN'T
# HAD"): end-of-call gm injection is DISABLED by default. My test/verification finalizes + probe
# + shard traffic each sprayed an injection into the operator's live gm. Journals STILL write (panel
# unaffected) — ONLY the gm injection is gated off. Re-enable (env ARTURO_GM_INJECT=1) ONLY when
# all 3 gates are shipped + verified: (1) exactly one injection per real call, (2) synthetic/
# test/probe NEVER inject, (3) injection carries the full transcript.
_GM_INJECT_ENABLED = os.environ.get("ARTURO_GM_INJECT", "1") == "1"
VOICE_BRAIN_SESSION = os.environ.get("VOICE_BRAIN_SESSION", "gm")  # the operator 2026-08-25: revert 08-21 gemini-gm reroute -> canonical gm (single-gm)

# finalize-watchdog idle threshold. The watchdog is a FALLBACK for the app-crash/battery case where
# the client /finalize-call never arrives; it keys on journal-file mtime (only moves on new turns).
# 120s was too aggressive: a real conversational PAUSE (>120s of silence in a still-open call) looked
# identical to a drop, so it prematurely finalized+injected a LIVE call -> a double 'Voice call ended'
# to gm when the real client-finalize then landed (live-caught 2026-09-06, the operator's build-161 call: a
# 2m36s pause tripped it). The client's real /finalize-call fires within seconds of hangup, so the
# watchdog only needs to catch genuine abandonment — a threshold well past any plausible pause. 15min.
FINALIZE_IDLE_SECS = int(os.environ.get("ARTURO_FINALIZE_IDLE_SECS", "900"))

# gm injection dispatch mode: async (a daemon thread — inject blocks ~60s on 409-park-and-retry so
# it must never stall finalize) in production; tests flip this False for a deterministic inline call.
_INJECT_ASYNC = True

# Per-request tool-capture (gm bug: summaries said "0 tool runs" on a 2-tool call — the journal
# only recorded user/arturo text turns, never tools). execute_tool appends here; the teeing
# wrapper flushes to the journal after the turn. A contextvar is per-request/thread so concurrent
# calls don't cross-contaminate (Flask threaded=True). Captures BOTH sync tools and the
# async-escape dispatch (async_task-wrapped gm_command etc.) since every path goes through
# execute_tool.
import contextvars as _contextvars
_TOOLS_THIS_TURN = _contextvars.ContextVar("arturo_tools_this_turn", default=None)
# Seats CREATED during this turn. The seat id is known only inside the spawn tool, and the
# surfaces need it to offer a way into the new agent (Shaw, 2026-09-22). Only the verified
# success path records, so a FAILED spawn can never produce a link to nothing.
_SPAWNED_THIS_TURN = _contextvars.ContextVar("arturo_spawned_this_turn", default=None)


def _record_spawned_this_turn(session_name):
    """Best-effort, turn-scoped. Outside a turn (voice, cron) it is a no-op, never an error."""
    bucket = _SPAWNED_THIS_TURN.get()
    if bucket is None or not session_name:
        return
    try:
        if session_name not in bucket:
            bucket.append(session_name)
    except Exception:
        pass

# VQ-6 supersede-on-arrival (pause-duplication). DEC-1786425204 CONSENSUS_REACHED.
# When the operator pauses mid-utterance the endpointer answers the PARTIAL, then the FULL utterance
# answers again -> duplicate response. The later (full) request cancels the earlier (partial)
# in-flight TEXT response, but ONLY before the partial has committed a side-effecting tool
# dispatch (cancel cannot un-run execute_tool). Lock is held only for tiny flag ops.
from services.arturo import thread_store as _thread_store   # G20 durable thread archive
from services.arturo import vq6 as _vq6
_VQ6 = _vq6.InflightRegistry()
from services.arturo import voice_guards as _voice_guards   # VQ-9 re-engagement-filler suppression
# F2 cross-request dedup (DEC-1788674600635445, congruence: AGY + in-process Claude). MODULE-LEVEL
# singleton (a per-request instance is a permanent no-op — reviewer BLOCKER-1). Suppresses an
# IDENTICAL ElevenLabs re-send within the TTL that escapes VQ-6 (in-flight) + BUG-3 (needs the answer
# in history) — the SEQUENTIAL-resend double-response the operator hit on vc_client_7c0e5662271e.
# (BOOT FIX 2026-09-07: this singleton originally sat ABOVE the _voice_guards import — the module
# could not even import, so the gated :5071 restart would have crash-looped. Caught by the
# semantic-recall boot proof; it must stay BELOW the import.)
_REQ_GUARD = _voice_guards.RecentRequestGuard(ttl=15.0)
# per-cid last-ANSWERED user final (ANSWERED-REPEAT guard — see chat_completions)
_ANSWERED_FINALS = _voice_guards.AnsweredFinalMemory()
from services.arturo import tg_outbox as _tg_outbox          # BUG-1 outbound Telegram cap + dedupe
_TG_OUTBOX = _tg_outbox.TelegramOutbox()

# DELIB-BUG-1: durable gm-inject retry. A finalize whose gm-inject fails (gm busy) must be re-driven
# until gm accepts — the OLD optimistic gm_injected=True lost a real 44-turn transcript. In-flight
# set guards against concurrent double-inject of the same call; the durable gm_injected=True flag
# (set only on SUCCESS) + the background sweeper are the source of truth.
_INJECT_INFLIGHT = set()
_INJECT_INFLIGHT_LOCK = _threading.Lock()
from services.arturo import inject_retry as _inject_retry
from services.arturo import pane_inject as _pane_inject   # Bug 1: three-runtime two-phase submit

# VQ-3b (cross-turn filler stacking, call vc_client_2d196728a2d7): VQ-3 caps fillers WITHIN one
# turn's tool-loop (_filler_emitted is per-request), but on rapid successive turns each turn emits
# its own one filler → "One sec." then "Almost there." then "Still on it." across 3 quick turns =
# the stacking the operator flagged AGAIN. This module-global timestamp gates fillers ACROSS turns: no
# filler within FILLER_COOLDOWN_S of the last one, regardless of turn boundary.
_LAST_FILLER_TS = 0.0
_FILLER_COOLDOWN_S = 12.0
_FILLER_LOCK = _threading.Lock()


def _filler_allowed(now):
    # Cross-turn cooldown gate. Returns True + records the emit iff no filler fired in the cooldown.
    global _LAST_FILLER_TS
    with _FILLER_LOCK:
        if now - _LAST_FILLER_TS < _FILLER_COOLDOWN_S:
            return False
        _LAST_FILLER_TS = now
        return True


def _journal_users(live_only):
    # (call_id, [user_texts]) per journal on disk. live_only=True → resolution corpus;
    # False → the coalesce corpus (a fragment may be a subset of an already-ENDED superset).
    import json as _json
    out = []
    for p in VOICE_CALLS_DIR.glob("vc_*.json"):
        try:
            d = _json.loads(p.read_text())
            if live_only and d.get("status") != "live":
                continue
            out.append((d.get("call_id"),
                        [t.get("text", "") for t in d.get("turns", []) if t.get("role") == "user"]))
        except Exception:
            continue
    return out


def _journals_with_start():
    # (call_id, started_at, [user_texts]) for ALL journals — the injection-winner corpus.
    import json as _json
    out = []
    for p in VOICE_CALLS_DIR.glob("vc_*.json"):
        try:
            d = _json.loads(p.read_text())
            out.append((d.get("call_id"), d.get("started_at", 0),
                        [t.get("text", "") for t in d.get("turns", []) if t.get("role") == "user"]))
        except Exception:
            continue
    return out


def _live_journal_users():
    return _journal_users(live_only=True)


def _all_journal_users():
    return _journal_users(live_only=False)


def _find_live_by_conv_id(conv_id):
    # A.1 Layer-1 keying: return the call_id of the LIVE journal already stamped with this conv_id.
    # Exact id match — no fuzzy needed once ElevenLabs' true conversation id flows in.
    if not conv_id:
        return None
    import json as _json
    for p in VOICE_CALLS_DIR.glob("vc_*.json"):
        try:
            d = _json.loads(p.read_text())
            if d.get("status") == "live" and d.get("conv_id") == conv_id:
                return d.get("call_id")
        except Exception:
            continue
    return None


def _resolve_or_create(non_system, page="", origin="local", conv_id=""):
    # DISK-BASED call resolution (fixes gm fragmentation bug): resolve an incoming request to an
    # existing LIVE journal. PRIMARY key = conv_id (A.1 Layer-1, exact) when present; else FALLBACK
    # to fuzzy user-turn OVERLAP (kept — robust to ASR re-transcription + history reshaping, and
    # covers legacy/probe traffic with no conv_id). Robust to proxy restarts (disk is truth).
    # GUARD kept: only ESTABLISHED calls (history has an assistant turn) journal — probes/health skip.
    # `origin` is stamped on a NEW journal (funnel|local|test) and gates gm injection at finalize.
    # `conv_id` is stamped on the journal (on match-if-missing AND on create) → the exact client↔
    # server keying the finalize merge uses.
    from services.arturo.call_journal import find_matching_call, _atomic_write
    has_assistant = any(m.get("role") == "assistant" and m.get("content") for m in non_system)
    if not has_assistant:
        return None
    incoming = [m["content"] for m in non_system if m.get("role") == "user" and m.get("content")]
    if not incoming:
        return None

    def _stamp_conv_id(call_id):
        # best-effort: ensure the resolved journal carries conv_id (idempotent).
        if not conv_id or not call_id:
            return
        try:
            import json as _json
            p = VOICE_CALLS_DIR / f"{call_id}.json"
            d = _json.loads(p.read_text())
            if d.get("conv_id") != conv_id:
                d["conv_id"] = conv_id
                _atomic_write(p, d)
        except Exception:
            pass

    with _JOURNALS_LOCK:             # serialize resolve+create so concurrent turns don't fork
        # PRIMARY: exact conv_id match.
        match = _find_live_by_conv_id(conv_id) if conv_id else None
        if match:
            return match
        # FALLBACK: fuzzy user-turn overlap (unchanged).
        match = find_matching_call(incoming, _live_journal_users())
        if match:
            _stamp_conv_id(match)    # a call that started before conv_id arrived gets stamped now
            return match
        j = _CallJournal(dir=VOICE_CALLS_DIR, page=page)
        # stamp origin (+ conv_id) on the fresh journal (immutable for the call's life)
        try:
            import json as _json
            d = _json.loads(j.path.read_text())
            d["origin"] = origin
            if conv_id:
                d["conv_id"] = conv_id
            # P1b fold-in: stamp the surface the operator is on at call start (phone/watch/web
            # attribution for the brain). None (missing/malformed/empty) => no field at all.
            # v2/(b) carve-out (gm locked criterion #3): RELAY KNOWLEDGE BEATS INFERENCE —
            # a conversation held by the stream relay IS watch-borne, so it stamps
            # surface=watch from the relay registry; the watch-omit guard in call_surface
            # then only governs non-relay voice (where watch has no voice path).
            try:
                # Smoke-proven (msg_2c36c4d2): EL SOCKET-path callbacks carry EL's own conv id
                # or NOTHING — resolve via the metadata mapping, else the sole-live fallback
                # (unambiguous single-user; >=2 live relays refuses). On attribution, ADOPT our
                # conv_id onto the journal: that one write fixes the surface stamp, relay-close
                # finalize lookup, ended-once keying, and brain attribution together.
                _relay_cid = None
                if _STREAM_RELAY is not None:
                    _relay_cid = (_STREAM_RELAY.resolve(conv_id) if conv_id
                                  else _STREAM_RELAY.sole_live())
                _relay_surface = (_STREAM_RELAY.surface(_relay_cid)
                                  if (_relay_cid and _STREAM_RELAY is not None) else None)
                from services.arturo.surface import call_surface as _call_surface
                from services.arturo.stream_relay import journal_surface as _journal_surface
                # item(1): a relay call (phone/watch, from the X-Surface stamp) attributes from the
                # relay registry AND adopts its conv_id; a non-relay call falls back to the
                # active-surface reader (which stays the watch-only writer).
                _surf = _journal_surface(
                    _relay_surface, lambda: _call_surface(ARTURO_STATE / "active-surface.json"))
                if _surf:
                    d["surface"] = _surf
                if _relay_surface in ("phone", "watch"):
                    d["conv_id"] = _relay_cid
            except Exception:
                pass
            _atomic_write(j.path, d)
        except Exception:
            pass
        return j.call_id


def _journal_append(call_id, role, text=None, tool=None, args=None):
    # Load-modify-atomic-write one turn onto a journal by call_id (disk is source of truth — no
    # stale in-memory handle across restarts). Best-effort; never breaks the SSE stream.
    if not call_id:
        return
    import json as _json
    from services.arturo.call_journal import _atomic_write
    with _JOURNALS_LOCK:
        p = VOICE_CALLS_DIR / f"{call_id}.json"
        try:
            d = _json.loads(p.read_text())
        except Exception:
            return
        if d.get("status") == "ended":   # a finalized call never re-opens
            return
        turns = d.setdefault("turns", [])
        if tool is not None:
            turns.append({"role": "tool", "tool": tool, "input": args or {},
                          "result": "dispatched", "status": "done", "ts": time.time()})
        else:
            if turns and turns[-1].get("role") == role and turns[-1].get("text") == (text or "")[:2000]:
                return                    # dedup identical consecutive turn (retry)
            turns.append({"role": role, "text": (text or "")[:2000], "ts": time.time()})
        _atomic_write(p, d)


def _finalize_journal_file(path):
    # Finalize ONE live journal on disk: build a 2-line summary from its turns, flip
    # status→ended + ended_at (what flips the app UI to the ended toast), and inject the
    # sanitized summary + frozen marker into the gm session via the hardened gateway
    # (verified_inject; 409 park-and-retry; a spoken "yes" can't bypass gm's gates). D8-safe.
    from services.arturo import endcall as _endcall
    import json as _json
    # A.6 (round-2 lock fix): the read + superseded_by/status guard + finalize FLIP all happen
    # UNDER _JOURNALS_LOCK, so a concurrent finalize_from_client that stamps superseded_by is seen
    # here (closes the watchdog TOCTOU: watchdog can't inject a server shard the merge just claimed).
    # The off-thread inject stays OUTSIDE the lock (it can block ~60s on 409-park-and-retry).
    with _JOURNALS_LOCK:
        try:
            d = _json.loads(path.read_text())
        except Exception:
            return False
        # superseded server shard (a client journal owns this call) OR already ended → never (re)inject.
        if d.get("superseded_by") or d.get("status") == "ended":
            return False
        call_id = d.get("call_id", "")
        turns = d.get("turns", [])
        n_turns = sum(1 for t in turns if t.get("role") in ("user", "arturo"))
        n_tools = sum(1 for t in turns if t.get("role") == "tool")
        last_arturo = next((t["text"] for t in reversed(turns) if t.get("role") == "arturo"), "")
        # summary kept for the JSON's own `summary` field (a 2-line headline for the panel toast).
        raw_summary = f"Voice call: {n_turns} turns, {n_tools} tool runs.\n{last_arturo}"
        summary = _endcall.sanitize_summary(raw_summary)
        _origin = d.get("origin", "local")
        # flip on disk (atomic) UNDER THE LOCK — reuse CallJournal semantics over the same id
        j = _CallJournal(dir=path.parent, call_id=call_id)
        j._data = d
        j.finalize(summary)
    # ---- outside _JOURNALS_LOCK ----
    # cid-reuse guard (ios msg_33a85910): a finalized journal means the CALL is over — end the
    # relay conversation too (tombstone => events/audio 410), or a zombie/resumed client keeps
    # a live vendor socket burning minutes and re-opens the same cid as a NEW journal (conv
    # 6A4ADC85: watchdog ended the journal 07:09:10Z, relay reconnect-looped at 07:21Z, client
    # re-opened at 07:26Z). Recursion-safe: a relay-initiated end() arrives here with
    # status=ended (early-return above), and this end() on a gone holder no-ops before
    # on_finalize.
    _conv = d.get("conv_id") or ""
    if _conv and _STREAM_RELAY is not None:
        try:
            if _STREAM_RELAY.end(_conv):
                log.info(f"finalize {call_id}: relay conversation {_conv} ended+tombstoned")
        except Exception as _re:
            log.error(f"finalize {call_id}: relay end failed for {_conv}: {_re!r}")
    # ---- injection arbitration + off-thread inject ----
    # INJECTION WINNER (gm 2026-08-10 REOPEN — the operator's call fragmented into 2 journals; the thin
    # shard injected, the real 10-turn call was stranded, because ASR RE-TRANSCRIBED the opener
    # longer ~3s in so exact-subset dedup missed). Fix: among all journals in the SAME physical
    # call (fuzzy-opener + started_at proximity), only the LONGEST/latest injects; a shard suppresses.
    # This holds even if resolution still split. Defense in depth over the fuzzy find_matching_call.
    from services.arturo.call_journal import pick_injection_winner
    if not pick_injection_winner(call_id, _journals_with_start()):
        log.info(f"finalized {call_id} -> ended ({n_turns} turns); SUPPRESSED injection (shard — a longer journal in this call wins)")
        return True
    log.info(f"finalized {call_id} -> ended ({n_turns} turns); dispatching gm injection")
    # gm injection runs OFF-THREAD: inject_to_gm blocks on 409-park-and-retry (~60s worst case
    # when gm is busy). If run inline it stalls the single-threaded watchdog sweep, delaying
    # EVERY other pending finalize (the bug gm caught: files idle >120s still showing live). The
    # status→ended flip above already persisted (what the panel needs); the injection is
    # best-effort and the durable JSON stands regardless.
    if not _GM_INJECT_ENABLED:
        # KILL SWITCH ON (default): journal is finalized (panel updates) but NO gm injection.
        log.info(f"finalized {call_id} -> ended ({n_turns} turns); gm injection DISABLED (kill switch)")
        return True
    # GATE 2: only a genuine funnel-origin the operator call injects. local/test/legacy-untagged never do.
    if _origin != "funnel":
        log.info(f"finalized {call_id} -> ended ({n_turns} turns); NO inject (origin={_origin}, not genuine the operator)")
        return True
    full_transcript = _endcall.build_full_transcript(turns)   # the operator REQ: gm gets the call in full
    _call_key = d.get("conv_id") or call_id
    def _inject():
        from services.arturo import ended_once as _eo
        _claimed = False
        try:
            # P6 once-guard: one "Voice call ended" per CALL across all journals/paths.
            if _eo.guard_enabled():
                if not _eo.claim(_call_key, ENDED_ONCE_LEDGER):
                    log.info(f"gm injection {call_id}: SUPPRESSED (ended-once — this call already injected)")
                    return
                _claimed = True
            marker = _endcall.build_marker(call_id, str(path.resolve()))
            ok, att = _endcall.inject_to_gm(VOICE_BRAIN_SESSION, summary, marker,   # v1 endcall: delivery target = VOICE_BRAIN_SESSION (gm, the operator 2026-08-25)
                                            transcript=full_transcript)
            log.info(f"gm injection {call_id}: ok={ok} attempts={att} (full transcript, {n_turns} turns)")
            if not ok and _claimed:
                _eo.release(_call_key, ENDED_ONCE_LEDGER)   # success-tied: failed inject reopens the claim
        except Exception as _ie:
            if _claimed:
                _eo.release(_call_key, ENDED_ONCE_LEDGER)
            log.error(f"gm injection error ({call_id}): {_ie}")
    _threading.Thread(target=_inject, daemon=True).start()
    return True


def _client_journal_path(client_call_id="", conv_id=""):
    # Resolve the client journal path. client_call_id is authoritative when given (the gateway
    # computed it as vc_client_<sha1(conv_id)[:12]>); else recompute from conv_id.
    import hashlib
    cid = client_call_id
    if not cid and conv_id:
        cid = "vc_client_" + hashlib.sha1(conv_id.encode()).hexdigest()[:12]
    if not cid:
        return None
    return VOICE_CALLS_DIR / f"{cid}.json"


def _settle_for_finalize(conv_id="", cap_s=10.0, quiet_s=1.0):
    # A.4 in-flight settle (round-2 fix): wait for the last SERVER turn to land before merging,
    # so a late/async tool turn isn't missed — but never block finalize indefinitely. Poll (holding
    # NEITHER _JOURNALS_LOCK NOR _INFLIGHT_LOCK) until (a) no live voice generation for this conv is
    # in the VQ-6 registry AND (b) no non-client journal mtime moved in the last quiet_s, capped at
    # cap_s. On cap timeout, proceed anyway (the client spine is complete) and log.
    import time as _t
    key = conv_id or "__live_voice__"
    t0 = _t.time()
    while _t.time() - t0 < cap_s:
        in_flight = _VQ6.current(key) is not None
        newest = 0.0
        for p in VOICE_CALLS_DIR.glob("vc_*.json"):
            if p.name.startswith("vc_client_"):
                continue
            try:
                newest = max(newest, os.path.getmtime(p))
            except Exception:
                pass
        quiet = (_t.time() - newest) >= quiet_s if newest else True
        if not in_flight and quiet:
            return
        _t.sleep(0.25)
    log.warning(f"finalize settle cap hit ({cap_s}s) for conv={conv_id or '(none)'} — proceeding")


def finalize_from_client(client_call_id="", conv_id=""):
    # A.5 client-authoritative finalize: the client journal (source=client, status=ended, full-text
    # spine) is the trigger. Merge the overlapping SERVER journal's TOOL turns + the surface sidecar
    # UI-nav events into the client spine, mark the server superseded_by, and inject a SUMMARY-ONLY
    # record to gm (the frozen [voice-call:] marker carries the path to the full client JSON for
    # on-demand mining). The ENTIRE read-modify-write of BOTH journals is under _JOURNALS_LOCK (no
    # sleep / no IO-to-network / no inject inside the lock). Idempotent (a re-notify never re-injects).
    from services.arturo import endcall as _endcall, finalize_merge as _fm
    import json as _json
    cpath = _client_journal_path(client_call_id, conv_id)
    if cpath is None or not cpath.exists():
        log.warning(f"finalize_from_client: no client journal for call_id={client_call_id!r} conv={conv_id!r}")
        return False

    # settle BEFORE taking the lock (poll-sleep holds no lock)
    _settle_for_finalize(conv_id=conv_id)

    do_inject = False
    summary = ""
    inj_call_id = ""
    with _JOURNALS_LOCK:
        try:
            client = _json.loads(cpath.read_text())
        except Exception as _ce:
            log.error(f"finalize_from_client read error: {_ce}")
            return False
        # idempotency: a re-notify after we've SUCCESSFULLY injected (or given up) must not re-drive.
        # A merged-but-not-yet-injected journal (gm_injected False) MUST fall through so the inject
        # is re-attempted (DELIB-BUG-1: the old optimistic flag made re-notify a silent no-op).
        if client.get("gm_injected") or client.get("gm_inject_gaveup"):
            log.info(f"finalize_from_client: {cpath.name} already resolved "
                     f"(injected={client.get('gm_injected')}, gaveup={client.get('gm_inject_gaveup')}) — skip")
            return True
        _already_merged = client.get("status") == "ended" and client.get("merged_server") is not None
        conv = client.get("conv_id") or conv_id
        # gather candidate (non-client) journals + the surface sidecar
        candidates = []
        for p in VOICE_CALLS_DIR.glob("vc_*.json"):
            if p.name.startswith("vc_client_"):
                continue                       # only SERVER/funnel journals are merge candidates
            try:
                candidates.append((p.stem, _json.loads(p.read_text())))
            except Exception:
                continue
        surface_path = cpath.with_suffix(".surface.jsonl")
        surface_lines = surface_path.read_text().splitlines() if surface_path.exists() else []
        surface_events = []
        for ln in surface_lines:
            try:
                surface_events.append(_json.loads(ln))
            except Exception:
                pass
        surface_ids = _fm.extract_surface_server_ids(surface_lines)
        # link the server journal (conv_id > surface-overlay > time-window)
        server_id = _fm.pick_server_journal(client, candidates, surface_ids)
        server_turns = []
        if server_id:
            server_d = {c: d for c, d in candidates}.get(server_id, {})
            server_turns = server_d.get("turns", [])
        # merge: client spine + server tools + surface events, ts-ordered
        merged = _fm.merge_timeline(client.get("turns", []), server_turns, surface_events)
        summary = _endcall.sanitize_summary(_fm.summarize(merged))
        client["turns"] = merged
        client["summary"] = summary
        client["status"] = "ended"
        client.setdefault("ended_at", client.get("ended_at") or time.time())
        if server_id:
            client["merged_server"] = server_id
        # DELIB-BUG-1: gm_injected stays FALSE until an inject SUCCEEDS. It is set True only by
        # _attempt_gm_inject on a 200 from gm. Preserve any existing retry counters on re-notify.
        client.setdefault("gm_injected", False)
        # DELIB-BUG-3: a trivial split-call stub (failed first EL connection) that abuts a richer
        # client sibling must NOT inject — it occupied gm ahead of the real call. Mark it suppressed.
        from services.arturo import inject_retry as _ir
        _client_sibs = []
        for p in VOICE_CALLS_DIR.glob("vc_client_*.json"):
            if p.samefile(cpath):
                continue
            try:
                _client_sibs.append(_json.loads(p.read_text()))
            except Exception:
                continue
        if _ir.is_trivial_stub_with_richer_sibling(client, _client_sibs):
            client["gm_inject_suppressed"] = True
            log.info(f"finalize_from_client: {cpath.name} is a trivial split-call stub — inject SUPPRESSED")
        from services.arturo.call_journal import _atomic_write
        _atomic_write(cpath, client)
        # flip + supersede the server shard (so the watchdog's superseded_by guard suppresses it)
        if server_id:
            spath = VOICE_CALLS_DIR / f"{server_id}.json"
            try:
                sd = _json.loads(spath.read_text())
                sd["superseded_by"] = client.get("call_id")
                sd["status"] = "ended"
                sd.setdefault("ended_at", sd.get("ended_at") or time.time())
                _atomic_write(spath, sd)
            except Exception as _se:
                log.error(f"finalize_from_client: server supersede error: {_se}")
        inj_call_id = client.get("call_id", "")
        # GATE: kill-switch + genuine (source=client) + not a suppressed stub.
        do_inject = (_GM_INJECT_ENABLED and client.get("source") == "client"
                     and not client.get("gm_inject_suppressed"))
        n_merged = sum(1 for t in merged if t.get("role") in ("user", "arturo"))
        n_tools = sum(1 for t in merged if t.get("role") == "tool")
        log.info(f"finalize_from_client: {cpath.name} merged server={server_id} "
                 f"({n_merged} turns, {n_tools} tools); inject={do_inject}")

    # ---- outside the lock: delete sidecar (durable fold done) + drive the durable inject ----
    try:
        sp = cpath.with_suffix(".surface.jsonl")
        if sp.exists():
            sp.unlink()
    except Exception as _ue:
        log.error(f"finalize_from_client: sidecar unlink error: {_ue}")

    if do_inject:
        # First attempt now; the background sweeper re-drives until gm accepts (DELIB-BUG-1).
        if _INJECT_ASYNC:
            _threading.Thread(target=_attempt_gm_inject, args=(cpath,), daemon=True).start()
        else:
            _attempt_gm_inject(cpath)
    return True


def _attempt_gm_inject(cpath):
    # Durable, self-healing gm injection (DELIB-BUG-1). Guards against concurrent double-inject via
    # the in-flight set + a re-read of gm_injected under the lock. On 200 → gm_injected=True (done).
    # On failure → bump attempts + schedule next_ts (backoff); at the cap → give up + escalate ONCE
    # to the operator via tg-notify. Callable from finalize (first try), re-notify, AND the sweeper.
    from services.arturo import endcall as _endcall, inject_retry as _ir
    from services.arturo.call_journal import _atomic_write
    import json as _json
    if not _GM_INJECT_ENABLED:
        return False                      # defense-in-depth: never inject with the kill-switch off,
                                          # even if a future caller forgets to gate (reviewer note)
    key = cpath.name
    with _INJECT_INFLIGHT_LOCK:
        if key in _INJECT_INFLIGHT:
            return False                      # someone is injecting this call right now
        _INJECT_INFLIGHT.add(key)
    try:
        # snapshot under the journals lock
        with _JOURNALS_LOCK:
            try:
                d = _json.loads(cpath.read_text())
            except Exception:
                return False
            if (d.get("gm_injected") or d.get("gm_inject_gaveup")
                    or d.get("gm_inject_suppressed") or d.get("source") != "client"):
                return False
            call_id = d.get("call_id", "")
            summary = d.get("summary") or ""
            turns = d.get("turns") or []
            _call_key = d.get("conv_id") or call_id
        # P6 once-guard: if another path (watchdog/server shard) already injected this CALL,
        # suppress durably (stops the retry sweeper) instead of double-delivering.
        from services.arturo import ended_once as _eo
        _claimed = False
        if _eo.guard_enabled():
            if not _eo.claim(_call_key, ENDED_ONCE_LEDGER):
                with _JOURNALS_LOCK:
                    try:
                        d = _json.loads(cpath.read_text())
                        d["gm_inject_suppressed"] = True
                        _atomic_write(cpath, d)
                    except Exception:
                        pass
                log.info(f"_attempt_gm_inject: {key} SUPPRESSED (ended-once — this call already injected)")
                return False
            _claimed = True
        try:
            marker = _endcall.build_marker(call_id, str(cpath.resolve()))
            transcript = _endcall.build_full_transcript(turns)
            ok, att = _endcall.inject_to_gm(VOICE_BRAIN_SESSION, summary, marker, max_attempts=2, base_delay=1.0, transcript=transcript)
        except Exception as _ie:
            log.error(f"_attempt_gm_inject build/inject error ({call_id}): {_ie}")
            ok = False
        if not ok and _claimed:
            _eo.release(_call_key, ENDED_ONCE_LEDGER)   # success-tied: keep the retry path open
        # record the outcome durably
        with _JOURNALS_LOCK:
            try:
                d = _json.loads(cpath.read_text())
            except Exception:
                return ok
            if d.get("gm_injected"):
                return True                   # a concurrent attempt already won
            if ok:
                d["gm_injected"] = True
                d.pop("gm_inject_next_ts", None)
                _atomic_write(cpath, d)
                log.info(f"_attempt_gm_inject: {key} injected to gm (attempts so far "
                         f"{d.get('gm_inject_attempts', 0)})")
            else:
                n = int(d.get("gm_inject_attempts", 0)) + 1
                d["gm_inject_attempts"] = n
                d["gm_inject_next_ts"] = _ir.next_ts(n, time.time())
                if _ir.gave_up(d):
                    d["gm_inject_gaveup"] = True
                    log.error(f"_attempt_gm_inject: {key} GAVE UP after {n} attempts — escalating")
                _atomic_write(cpath, d)
                if d.get("gm_inject_gaveup"):
                    _escalate_inject_giveup(call_id)
        return ok
    finally:
        with _INJECT_INFLIGHT_LOCK:
            _INJECT_INFLIGHT.discard(key)


def _escalate_inject_giveup(call_id):
    # After the retry cap, tell the operator ONCE that his call transcript couldn't reach gm (rides the
    # capped/deduped tg outbox so it can't storm).
    try:
        msg = (f"Heads up — I couldn't get your last call's transcript into the GM after many tries "
               f"(it stayed busy). The full transcript is saved ({call_id}); ask me to retry it.")
        if _TG_OUTBOX.allow(msg)[0]:
            subprocess.run(["bash", str(ORCHESTRA_DIR / "scripts" / "tg-notify.sh"),
                            "--from", "arturo-voice", msg],
                           capture_output=True, text=True, timeout=60)
    except Exception as _e:
        log.error(f"_escalate_inject_giveup error: {_e}")


def _start_inject_retry_sweeper(interval_s=15):
    # DELIB-BUG-1 self-heal: periodically re-drive any ended source=client journal whose gm-inject
    # hasn't landed yet (gm was busy). This is what makes a busy-gm inject EVENTUALLY deliver instead
    # of being lost. Gated on the kill switch — no injection at all when ARTURO_GM_INJECT is off.
    import time as _t
    import json as _json

    def _loop():
        while True:
            try:
                if _GM_INJECT_ENABLED:
                    now = _t.time()
                    for p in VOICE_CALLS_DIR.glob("vc_client_*.json"):
                        try:
                            d = _json.loads(p.read_text())
                        except Exception:
                            continue
                        if _inject_retry.should_attempt(d, now):
                            _attempt_gm_inject(p)
            except Exception as _we:
                log.error(f"inject-retry-sweeper error: {_we}")
            _t.sleep(interval_s)

    _threading.Thread(target=_loop, daemon=True).start()
    log.info(f"inject-retry-sweeper started (interval={interval_s}s)")


def _finalize_stale_calls(now=None, idle_secs=None):
    # ONE watchdog sweep pass (extracted for testability): finalize every live journal whose file
    # has been idle (no new turns -> no mtime movement) for >= idle_secs. idle_secs must comfortably
    # exceed any plausible conversational pause (see FINALIZE_IDLE_SECS) — a paused-but-live call is
    # NOT a dropped call, and finalizing one prematurely double-injects gm when the real client
    # /finalize-call later lands.
    import time as _t
    if now is None:
        now = _t.time()
    if idle_secs is None:
        idle_secs = FINALIZE_IDLE_SECS
    for p in VOICE_CALLS_DIR.glob("vc_*.json"):
        try:
            if now - os.path.getmtime(p) < idle_secs:
                continue
            _finalize_journal_file(p)
        except Exception as _pe:
            log.error(f"finalize-watchdog file error: {_pe}")


def _start_finalize_watchdog(idle_secs=None, interval_s=30):
    # FALLBACK finalize trigger (A.7): the PRIMARY is now the gateway /voice-call-ended → proxy
    # /finalize-call notify hop (finalize_from_client, seconds after hangup). This watchdog remains
    # for the app-crash/battery case where NO client journal ever arrives. The A.6 superseded_by
    # guard in _finalize_journal_file makes it impossible for the watchdog to inject a server shard
    # a client journal already claimed.
    import time as _t
    if idle_secs is None:
        idle_secs = FINALIZE_IDLE_SECS

    def _loop():
        while True:
            try:
                _finalize_stale_calls(now=_t.time(), idle_secs=idle_secs)
            except Exception as _we:
                log.error(f"finalize-watchdog error: {_we}")
            _t.sleep(interval_s)

    _threading.Thread(target=_loop, daemon=True).start()
    log.info(f"finalize-watchdog started (idle={idle_secs}s, interval={interval_s}s)")


def load_secrets():
    secrets = {}
    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                secrets[k.strip()] = v.strip()
    return secrets


secrets = load_secrets()
GEMINI_API_KEY = secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
BEARER_TOKEN = secrets.get("CUSTOM_LLM_BEARER", "")
_INTERNAL_NONCE = __import__('secrets').token_hex(16)   # see check_auth()
LLM_MODEL = secrets.get("LLM_MODEL", "gemini-2.5-flash")
_API_MODEL = LLM_MODEL   # the api-brain model name, kept even when LLM_MODEL becomes "none"

# --- Brain selection (track T2): api (BYO key) | runtime (the CLI you're logged in to) | none.
# The service BOOTS in all three cases; a keyless install runs text-only on the CLI brain, and
# with nothing authed /health says so and every turn answers with the fix (no restart loop).
from services.arturo import brain as _brain
BRAIN_MODE = (os.environ.get("ORCHESTRA_ARTURO_BRAIN") or secrets.get("ARTURO_BRAIN") or "auto").strip().lower()
RUNTIME_MODEL = (os.environ.get("ORCHESTRA_ARTURO_RUNTIME_MODEL") or secrets.get("ARTURO_RUNTIME_MODEL") or "").strip()
_RUNTIMES_ENABLED = [r for r in (os.environ.get("ORCHESTRA_RUNTIMES_ENABLED") or "").split(",") if r]
brain = _brain.select_brain(
    BRAIN_MODE, GEMINI_API_KEY,
    probes=[] if (BRAIN_MODE == "api" or (BRAIN_MODE == "auto" and GEMINI_API_KEY))
    else _brain.probe_runtimes(_REPO_ROOT, _RUNTIMES_ENABLED or None),
    api_model=LLM_MODEL, runtime_model=RUNTIME_MODEL)
if brain.kind == "runtime":
    LLM_MODEL = brain.model
elif brain.kind == "none":
    LLM_MODEL = "none"
    log.error(f"brain: NONE — {brain.reason}")
log.info(f"brain: {brain.describe()} (arturo.brain={BRAIN_MODE})")

# Voice needs a vendor key; without one the service runs TEXT-ONLY (the text turn + tools still
# work through the brain, the /ptt + /v1/chat/completions voice callbacks answer 503 vendor-less).
_VOICE_KEYS = ("ELEVENLABS_API_KEY", "CARTESIA_API_KEY", "HUME_API_KEY", "GEMINI_API_KEY")
VOICE_VENDORS_PRESENT = [k for k in _VOICE_KEYS if secrets.get(k) or os.environ.get(k)]
ARTURO_MODE = "voice" if VOICE_VENDORS_PRESENT else "text-only"
log.info(f"mode: {ARTURO_MODE} (voice keys present: {[k.split('_')[0].lower() for k in VOICE_VENDORS_PRESENT]})")


# --- Auth ---

def check_auth():
    # ARTURO INSTANCE: FAIL-CLOSED (diverges from the live :5052 clone source, which
    # `return True`s unconditionally — a debug shortcut). This endpoint is a PUBLIC
    # Funnel ingress onto write-capable tools (run_command / kill_agent / gm_command =
    # RCE-equivalent), so an unauthenticated caller must be rejected. ElevenLabs sends
    # the configured `Authorization: Bearer <CUSTOM_LLM_BEARER>` on every request, so
    # fail-closed is fully compatible. Constant-time compare (mirrors jarvis_poc M1).
    # In-process hop (the loopback /text route replays through chat_completions via the Flask
    # test client): a per-process random nonce only this process knows, never a config secret.
    if hmac.compare_digest(request.headers.get("X-Arturo-Internal", ""), _INTERNAL_NONCE):
        return True
    if not BEARER_TOKEN:
        # No secret configured → refuse rather than silently allow-all on a public port.
        return False
    got = request.headers.get("Authorization", "")
    prefix = "Bearer "
    token = got[len(prefix):] if got.startswith(prefix) else ""
    return hmac.compare_digest(token, BEARER_TOKEN)


# --- SSH + Tool Execution ---

_mac_reachable_cache = {"ok": False, "checked": 0, "sessions": []}

def _read_mac_heartbeat():
    """Read mac-heartbeat.json to determine Mac status without SSH."""
    try:
        hb_file = ORCHESTRA_DIR / "state" / "mac-heartbeat.json"
        if not hb_file.exists():
            return False
        hb = json.loads(hb_file.read_text())
        status = hb.get("status", "offline")
        last_check = hb.get("last_check", "")
        if not last_check:
            return False
        from datetime import datetime, timezone
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last_check.replace("Z", "+00:00"))).total_seconds()
        return status == "online" and age < 300  # online and checked within 5 min
    except Exception:
        return False

# Seed cache from heartbeat file on startup
_mac_reachable_cache["ok"] = _read_mac_heartbeat()
_mac_reachable_cache["checked"] = time.time()
log.info(f"Mac reachability from heartbeat: {'ONLINE' if _mac_reachable_cache['ok'] else 'OFFLINE'}")

def ssh_mac(cmd, timeout=10, force=False):
    """Run a command on Mac via SSH. Returns (success, output).
    Skips if Mac was unreachable in the last 60 seconds, unless force=True."""
    now = time.time()
    # Refresh from heartbeat every 120s (no SSH needed)
    if now - _mac_reachable_cache.get("checked", 0) > 120:
        _mac_reachable_cache["ok"] = _read_mac_heartbeat()
        _mac_reachable_cache["checked"] = now
    if not force and not _mac_reachable_cache["ok"] and (now - _mac_reachable_cache.get("checked", 0)) < 60:
        return False, "MAC_UNREACHABLE (cached)"
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=3", "-o", "StrictHostKeyChecking=no",
             "-o", "IdentitiesOnly=yes", "-i", MAC_SSH_KEY,
             f"{MAC_USER}@{MAC_IP}", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        success = r.returncode == 0
        _mac_reachable_cache["ok"] = success
        _mac_reachable_cache["checked"] = now
        return success, r.stdout.strip() or r.stderr.strip()
    except subprocess.TimeoutExpired:
        _mac_reachable_cache["ok"] = False
        _mac_reachable_cache["checked"] = now
        return False, "MAC_UNREACHABLE"
    except Exception as e:
        _mac_reachable_cache["ok"] = False
        _mac_reachable_cache["checked"] = now
        return False, "MAC_UNREACHABLE"


def mac_is_reachable():
    """Quick check if Mac is reachable via SSH."""
    ok, out = ssh_mac("echo ok", timeout=5)
    return ok and out == "ok"


def run_local(cmd, timeout=15):
    """Run a command locally on VPS. Returns (success, output)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip() or r.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Command timeout"
    except Exception as e:
        return False, str(e)


# --- Claude Code Output Cleaner ---

import re as _re

_CC_NOISE_PATTERNS = [
    _re.compile(r'^[✻✢✽●] .+'),           # Spinners: ✻ Sautéed, ✢ Orbiting, ● Reading
    _re.compile(r'^  ⎿ .+'),                # Tool output markers: ⎿ Read, ⎿ Wrote
    _re.compile(r'^❯\s*$'),                  # Bare prompt
    _re.compile(r'bypass permissions'),       # Status bar
    _re.compile(r'shift\+tab to cycle'),      # Status bar
    _re.compile(r'Opus \d'),                  # Model indicator
    _re.compile(r'Sonnet \d'),                # Model indicator
    _re.compile(r'Haiku \d'),                 # Model indicator
    _re.compile(r'context\) │'),              # Status bar context indicator
    _re.compile(r'██'),                       # Progress bar
    _re.compile(r'Auto-update failed'),       # Update notification
    _re.compile(r'How is Claude doing'),      # Rating prompt
    _re.compile(r'^\s*\d:\s*(Bad|Fine|Good|Dismiss)'), # Rating options
    _re.compile(r'Hookify'),                  # Hook output
    _re.compile(r'UserPromptSubmit'),         # Hook output
    _re.compile(r'Stop says'),                # Hook output
    _re.compile(r'ctrl\+o'),                  # Keyboard hints
    _re.compile(r'^─{4,}$'),                  # Separator lines
    _re.compile(r'claude doctor'),            # Update hints
    _re.compile(r'npm i -g @anthropic'),      # Update hints
    _re.compile(r'/gsd:update'),              # Slash commands in status
    _re.compile(r'✗ Auto-update'),            # Update failure
]

def clean_cc_output(text):
    """Strip Claude Code UI chrome from tmux capture output."""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        if not line.strip():
            continue
        if any(p.search(line) for p in _CC_NOISE_PATTERNS):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


# --- Knowledge System (instant local lookups) ---

KNOWLEDGE_DIR = ORCHESTRA_DIR / "state" / "knowledge"

def _knowledge_lookup(query, category=None):
    """Instant local knowledge lookup. <10ms. No SSH, no GM."""
    query_lower = query.lower().strip()
    results = []

    def _char_sim(a, b):
        """LCS-based similarity (0-1). Handles voice transcription typos."""
        a, b = a.lower(), b.lower()
        if a == b: return 1.0
        m, n = len(a), len(b)
        if not m or not n: return 0.0
        dp = [[0]*(n+1) for _ in range(m+1)]
        for i in range(1, m+1):
            for j in range(1, n+1):
                dp[i][j] = dp[i-1][j-1]+1 if a[i-1]==b[j-1] else max(dp[i-1][j], dp[i][j-1])
        return (2.0*dp[m][n])/(m+n)

    def match_entry(entry, q):
        """Match query to entry — handles voice typos like a mis-heard project or client name."""
        name = entry.get("full_name", entry.get("name", "")).lower()
        aliases = [a.lower() for a in entry.get("aliases", [])]
        if q in name or q in aliases or any(q in a for a in aliases) or name.startswith(q):
            return True
        q_clean = re.sub(r'[\s\-_.]', '', q)
        name_clean = re.sub(r'[\s\-_.]', '', name)
        if q_clean in name_clean or name_clean in q_clean:
            return True
        for a in aliases:
            a_clean = re.sub(r'[\s\-_.]', '', a)
            if q_clean in a_clean or a_clean in q_clean:
                return True
        # Similarity match — 75%+ catches near-miss spellings
        for c in [name_clean] + [re.sub(r'[\s\-_.]', '', a) for a in aliases]:
            if _char_sim(q_clean, c) >= 0.75:
                return True
        return False

    # People
    if category in (None, "people"):
        pf = KNOWLEDGE_DIR / "people.json"
        if pf.exists():
            try:
                people = json.loads(pf.read_text())
                for pid, p in people.items():
                    if match_entry(p, query_lower) or query_lower == pid:
                        results.append(f"[PERSON] {p['full_name']} — {p['role']}. {p.get('context', '')}")
            except Exception:
                pass

    # Projects
    if category in (None, "projects"):
        pf = KNOWLEDGE_DIR / "projects.json"
        if pf.exists():
            try:
                projects = json.loads(pf.read_text())
                for pid, p in projects.items():
                    if match_entry(p, query_lower) or query_lower == pid:
                        block = f"[PROJECT] {p['name']} [{p['status']}, P{p['priority']}] — {p['summary']} Current: {p.get('current_state', '')}"
                        # gm msg_c0a87329 (the operator field-proven 06:30 call): the
                        # facts dict is where ALL behavioral facts live — a KB
                        # hit that omits it made every fact written tonight
                        # invisible (incl. the one telling Arturo not to claim
                        # capabilities — the bug hid its own fix). Render as
                        # key: value lines, per-fact cap 500 chars with a
                        # VISIBLE truncation mark (never silently clipped).
                        facts = p.get("facts")
                        if isinstance(facts, dict) and facts:
                            fact_lines = []
                            for fk, fv in facts.items():
                                fv = str(fv)
                                if len(fv) > 500:
                                    fv = fv[:500] + "…"
                                fact_lines.append(f"  {fk}: {fv}")
                            block += "\nFacts:\n" + "\n".join(fact_lines)
                        results.append(block)
            except Exception:
                pass

    # Priorities
    if category in (None, "priorities"):
        if query_lower in ("focus", "priorities", "today", "this week", "what should"):
            # PRIORITY SOURCE (gm 2026-08-10): steering questions ("what should we focus on")
            # must come from state/decision-queue/TOP3-NOW.md — GM-maintained, always current —
            # NOT the stale knowledge/priorities.json that answered the operator with 19-day-old
            # hallucinated priorities (Kai/JWT/workspace-UI). STALENESS STAMP: if the freshest
            # source is >48h old, prepend a "context may be stale" flag so Arturo hedges rather
            # than answering confidently from dead context (the exact goal-ledger failure mode).
            import time as _t
            STALE_H = 48
            top3 = ORCHESTRA_DIR / "state" / "decision-queue" / "TOP3-NOW.md"
            pf = KNOWLEDGE_DIR / "priorities.json"
            best_age_h = None
            emitted = False
            try:
                if top3.exists():
                    age_h = (_t.time() - os.path.getmtime(top3)) / 3600.0
                    best_age_h = age_h
                    body = top3.read_text().strip()
                    # keep it lean for voice — headline lines only
                    lines = [ln.strip() for ln in body.splitlines()
                             if ln.strip() and not ln.strip().startswith(("#", "*", "###"))][:6]
                    results.append("[PRIORITIES — TOP3-NOW] " + " | ".join(lines))
                    emitted = True
            except Exception:
                pass
            try:
                if pf.exists():
                    age_h = (_t.time() - os.path.getmtime(pf)) / 3600.0
                    best_age_h = age_h if best_age_h is None else min(best_age_h, age_h)
                    if not emitted:
                        pri = json.loads(pf.read_text())
                        parts = [f"Top of mind: {pri.get('top_of_mind', '')}"]
                        if pri.get("this_week"):
                            parts.append("This week: " + "; ".join(pri["this_week"]))
                        if pri.get("blockers"):
                            parts.append("Blockers: " + "; ".join(pri["blockers"]))
                        results.append("[PRIORITIES] " + " | ".join(parts))
                        emitted = True
            except Exception:
                pass
            if emitted and best_age_h is not None and best_age_h > STALE_H:
                results.append(f"[STALENESS WARNING] priority context is ~{int(best_age_h/24)}d old — "
                               f"tell the operator your context may be stale before answering steering questions.")

    # Agents (live — reads registry + tmux)
    if category in (None, "agents"):
        if query_lower in ("agents", "running", "active", "list agents", "tmux"):
            try:
                r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"],
                                   capture_output=True, text=True, timeout=2)
                if r.returncode == 0:
                    sessions = [s.strip() for s in r.stdout.strip().split("\n") if s.strip()]
                    infra = {"cf-tunnel", "restarter", "custom-llm"}
                    interesting = [s for s in sessions if s not in infra][:5]
                    mac_status = "ONLINE" if _mac_reachable_cache.get("ok") else "OFFLINE"
                    mac_count = len(_mac_reachable_cache.get("sessions", []))
                    results.append(f"[AGENTS] {len(sessions)} on VPS including {', '.join(interesting)}. Mac: {mac_status}" +
                                   (f" with {mac_count} sessions" if mac_count else ""))
            except Exception:
                pass

    # BRAIN-FACT BLEND (hotfix 2026-09-14 — the operator live-test: knowledge('Noah') /
    # knowledge('last conversation with Alex') returned stale curated info and
    # never reached state/brain/facts.db, the ~29k-fact daily-refreshed brain).
    # The knowledge tool is Arturo's #1 tool for people/projects, so it MUST also
    # query the brain. Additive: the curated priorities/people/projects answer is
    # KEPT and the top relevant REAL brain facts are appended. Reuses the bounded,
    # scored, noise-filtered facts_recall.query_sources path (fast, read-only) — the
    # SAME merged sources as the passive FACTS block, so a fact written on the
    # dashboard surfaces on every path Arturo answers from.
    # Gated on ARTURO_FACTS_RECALL (same flag as the passive facts seam).
    if category in (None, "people", "projects") and os.environ.get("ARTURO_FACTS_RECALL") == "1":
        try:
            try:
                import facts_recall as _fr
            except ImportError:
                from services.arturo import facts_recall as _fr
            _kws = _fr.extract_keywords(query)
            if _kws:
                _bf = _fr.query_sources(_kws, k=4)
                if _bf:
                    _lines = []
                    for _f in _bf:
                        _snip = " ".join((_f.get("text") or "").split())[:220]
                        _d = (_f.get("ts") or "")[:10]
                        _lines.append(f"  - [{_f.get('kind', 'fact')} {_d}] {_snip}")
                    results.append("[BRAIN FACTS — freshest relevant from the daily fact store]\n"
                                   + "\n".join(_lines))
        except Exception:
            pass

    if not results:
        return f"No knowledge found for '{query}'. This may require a deeper investigation — consider using gm_command or read_file."
    return "\n\n".join(results)


# --- Tool Definitions (OpenAI function calling format) ---

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "knowledge",
            "description": "FAST instant lookup of people, projects, priorities, and agents. Use this FIRST before any other tool when the operator asks about a person, project, status, or what to focus on. Returns in <10ms.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look up. Examples: 'acme', 'globex', 'northwind', 'focus', 'agents'",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["people", "projects", "priorities", "agents"],
                        "description": "Optional category to narrow search. Omit to search all.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_screen_context",
            "description": "See what the operator is looking at in the OrchestraOS app RIGHT NOW and pull that screen's live data. Call this when the operator asks 'what am I looking at', 'what's this doing', 'what's on my screen', or refers to something on screen without naming it. No arguments — it resolves his current screen automatically.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer_menu",
            "description": "Answer a decision menu an agent is PARKED on (shown in your context as 'DECISION PENDING on <agent>'). Two-phase + voice-confirmed: FIRST call with confirm=false to STAGE the choice — you'll then tell the operator 'sending option N to <agent>, confirm?' and wait for his spoken yes; only after he says yes, call AGAIN with confirm=true to actually send it. A spoken yes confirms this SPECIFIC staged option — never send confirm=true without the operator's explicit go. CUSTOM ANSWER: if the operator's answer isn't one of the numbered choices, the menu usually has a 'Type something' option — select THAT option's number and pass the operator's exact words as `text`; I type them in atomically (no separate step). A 'Chat about this' option isn't a decision — it means keep talking it through, so I'll just keep deliberating. Use ONLY for agents you can see have a pending decision.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session": {"type": "string", "description": "The agent session with the pending menu, e.g. 'acme-dev'."},
                    "option": {"type": "string", "description": "The option NUMBER to select (1-9)."},
                    "confirm": {"type": "boolean", "description": "false to stage (default — then ask the operator to confirm by voice); true ONLY after the operator says yes."},
                    "text": {"type": "string", "description": "ONLY for a 'Type something' free-text option: the operator's exact words to type in. Set `option` to that option's number and put his verbatim answer here; confirm his wording back before confirm=true. Omit for a normal numbered choice."},
                },
                "required": ["session", "option"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_entity",
            "description": "Switch top-of-mind focus to something the operator refers to BY DESCRIPTION rather than by exact name — 'that acme approval', 'the northwind dev', 'the recommendations you suggested', 'the one on the Arturo page'. Pass his words verbatim as spoken_ref; this resolves them to the right pending approval / agent / recent output, focuses it, and returns a spoken confirmation. If it comes back ambiguous, read the operator the one-line either/or; if unsure, ask the sharp yes/no it gives you. Call this FIRST when the operator points at something by description, THEN deliberate the now-focused entity — do NOT bounce back with 'I don't see a decision menu'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spoken_ref": {"type": "string", "description": "the operator's own words for the thing he's referring to, verbatim (e.g. 'the adaptiv approval', 'the recommendations you just gave')."},
                },
                "required": ["spoken_ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_agent",
            "description": "Spawn a new Claude Code agent in a tmux session. Defaults to VPS. Only set machine to 'mac' if the operator EXPLICITLY says 'on my Mac'. Agent appears on OrchestraOS dashboard.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_name": {
                        "type": "string",
                        "description": "Name for the tmux session. Use lowercase with hyphens.",
                    },
                    "machine": {
                        "type": "string",
                        "enum": ["vps", "mac"],
                        "description": "REQUIRED. Where to spawn. Use 'vps' by default. ONLY use 'mac' if the operator explicitly says 'on my Mac' or 'on the Mac'.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Optional initial task/prompt to give the agent after spawning.",
                    },
                    "background": {
                        "type": "boolean",
                        "description": "If true, run in background (no visible window). Default false.",
                    },
                },
                "required": ["session_name", "machine"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inject_message",
            "description": "Send a message/task to a running agent's tmux session. Works on both Mac and VPS agents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_name": {"type": "string", "description": "The tmux session name."},
                    "message": {"type": "string", "description": "The message or task to send."},
                },
                "required": ["session_name", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_agent_output",
            "description": "Get recent output from an agent's tmux session (Mac + VPS). Returns only the agent's REAL on-screen output: a CLI ghost SUGGESTION in the composer is stripped (it's a suggestion, never the agent's words) and an unsubmitted composer DRAFT is tagged [not delivered]. For 'what did the agent last SAY / decide / tell me' — the delivered statement — prefer read_agent_conversation (the session transcript), which is authoritative for delivered turns; use get_agent_output for what's on screen right now.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_name": {"type": "string", "description": "The tmux session name."},
                    "lines": {"type": "integer", "description": "Number of lines to capture (default 30)."},
                },
                "required": ["session_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_agents",
            "description": "List all running tmux sessions (agents) across Mac and VPS.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kill_agent",
            "description": "Kill/stop a running agent's tmux session. Works on both Mac and VPS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_name": {"type": "string", "description": "The tmux session name to kill."},
                },
                "required": ["session_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_telegram",
            "description": "Send a text message to the operator on Telegram. Use when the operator asks you to text him something or there's a clickable deliverable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The message to send."},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gm_command",
            "description": "Consult YOUR OWN DEEP BRAIN — your deeper reasoning with FULL context on all projects, codebase access, file read/write, and bash execution. This is you, not a separate person; never name it aloud (say 'let me think on that'). Use for anything complex: code questions, architectural decisions, deploying, debugging, reading files, checking git history. Returns your deep brain's full response.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The command or question for the GM. Be specific. E.g. 'What is the current state of the acme build?' or 'Read the file ~/scripts/agent-orchestra/registry.json and tell me how many agents are registered' or 'Check git log for the last 5 commits in northwind'.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds to wait for GM response (default 60, max 120).",
                    },
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from Mac or VPS. Use for checking configs, logs, code, roadmaps, handoffs, or any file the operator asks about.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path. E.g. ~/scripts/agent-orchestra/registry.json"},
                    "lines": {"type": "integer", "description": "Max lines to read (default 50, max 200)."},
                    "machine": {"type": "string", "enum": ["mac", "vps"], "description": "Which machine (default: vps)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command on Mac or VPS. Use for git status, checking processes, reading logs, system diagnostics, deployment commands. NOT for destructive operations without the operator's explicit approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run."},
                    "machine": {"type": "string", "enum": ["mac", "vps"], "description": "Which machine (default: vps)."},
                    "timeout": {"type": "integer", "description": "Max seconds (default 15, max 30)."},
                },
                "required": ["command"],
            },
        },
    },
    __import__("services.arturo.operator_store", fromlist=["TOOL"]).TOOL,   # set_operator_fact — ONE schema, owned by the store
    {
        "type": "function",
        "function": {
            "name": "remember_note",
            "description": "Save a persistent note to voice memory. Use when the operator says 'remember this', 'don't say that again', 'always do X', 'never do Y', or gives any feedback about how you should behave. Notes persist across ALL future voice calls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "The note to remember. Be specific and actionable."},
                    "category": {
                        "type": "string",
                        "enum": ["behavior", "preference", "fact", "project"],
                        "description": "Category: 'behavior' for how to act, 'preference' for likes/dislikes, 'fact' for things to know, 'project' for project decisions.",
                    },
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agent_message",
            "description": "Send a message from one agent to another with reply tracking. The receiving agent gets the message in their inbox and can reply back. Use this instead of inject_message when you need a response back. Also use to ask an agent a question and get an answer routed back.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_agent": {"type": "string", "description": "Sender agent ID (e.g., 'jarvis', 'gm', 'pm-products')."},
                    "to_agent": {"type": "string", "description": "Receiver agent ID."},
                    "subject": {"type": "string", "description": "Brief subject line."},
                    "body": {"type": "string", "description": "Full message body."},
                    "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"], "description": "Priority level (default: medium)."},
                },
                "required": ["from_agent", "to_agent", "subject"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_agent_conversation",
            "description": "Read the message thread between agents. Use when the operator asks 'what are X and Y talking about?' or 'what did agent X say to agent Y?'. Can also read an agent's inbox to see all pending messages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent whose inbox or conversations to read."},
                    "conversation_id": {"type": "string", "description": "Specific conversation thread ID to read (optional — if omitted, returns inbox summary)."},
                },
                "required": ["agent_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_roadmap",
            "description": "Query roadmap and task status. Use for: project progress, blocked tasks, phase status, what's in progress.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project slug or name (e.g., 'acme', 'northwind'). Use 'all' for cross-project view."},
                    "filter": {"type": "string", "enum": ["all", "blocked", "in_progress", "completed", "pending"], "description": "Filter tasks by status. Default: all."},
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research",
            "description": "Spawn a research agent to investigate a topic on the web. Use for: competitor analysis, market research, finding tools/services, fact-checking. Results come back via Telegram.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to research (be specific)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "client_briefing",
            "description": "Get a briefing on a client or project. Use before calls, meetings, or when the operator asks 'brief me on X'. Returns recent activity, deliverables, team, blockers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "client": {"type": "string", "description": "Client or project name (e.g., 'acme', 'northwind', 'globex')."},
                },
                "required": ["client"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "async_task",
            "description": "Run a tool in the background and text the operator the result on Telegram when done. Use this for ANY operation that might be slow (Mac SSH, GM commands, complex spawns). Jarvis responds immediately with 'On it, I'll text you when done' and the task runs after the voice call continues. ALWAYS use this for Mac operations since SSH may take 5-15 seconds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": "The tool to run async (e.g., 'spawn_agent', 'run_command', 'gm_command', 'read_file').",
                    },
                    "tool_args": {
                        "type": "object",
                        "description": "Arguments to pass to the tool, exactly as you would call it directly.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief description of what you're doing, for the Telegram notification (e.g., 'Spawning jarvis-agent-2 on Mac').",
                    },
                },
                "required": ["tool_name", "tool_args", "summary"],
            },
        },
    },
]

# P1b Voice Layer Dispatcher (DEC-1788772980798256, BUILD-AND-HOLD): behind ARTURO_DISPATCHER=1
# the model-facing sync gm_command tool is RETIRED (D3 — a live call must never wait on the
# standing gm) and deep_query (ephemeral read-only analyst) + ask_gm (alias over
# async_task(gm_command)) are added. Flag off = TOOLS byte-identical. The internal
# execute_tool gm_command branch stays — the async pipeline reuses it off the call path.
if os.environ.get("ARTURO_DISPATCHER") == "1":
    from services.arturo import dispatcher as _dispatcher
    TOOLS = _dispatcher.transform_tools(TOOLS)

# Telegram config — token/chat id come from env only, never a literal here.
# orchestra.toml's notify.telegram.bot_token_env names which env var to read.
TELEGRAM_BOT_TOKEN = os.environ.get("ORCHESTRA_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("ORCHESTRA_TELEGRAM_CHAT_ID", "")


def _run_on_machine(mac_cmd, vps_cmd, timeout=15, prefer_mac=False):
    """Run on VPS by default. Only tries Mac if prefer_mac=True.
    When prefer_mac=True, bypasses the reachability cache and forces a live SSH check.
    Returns (success, output, machine)."""
    if prefer_mac:
        ok_m, out_m = ssh_mac(mac_cmd, timeout=timeout, force=True)
        if ok_m:
            return True, out_m, "mac"
        return False, out_m, "mac"
    ok_v, out_v = run_local(vps_cmd, timeout=timeout)
    if ok_v:
        return True, out_v, "vps"
    return False, out_v, "vps"


def _tmux_cmd(session, action, mac_prefix="PATH=/opt/homebrew/bin:$PATH "):
    """Generate Mac and VPS versions of a tmux command."""
    # NO "spawn" action, deliberately (gm msg_660ad3bf, release-critical): a bare
    # `tmux new-session … claude` mints NO identity — no runtime/model/tier, no registry
    # row, invisible to /api/agents, and a crash retires the seat instead of parking it
    # (no resume_command). Every seat Arturo creates goes through spawn-agent.sh's adopt
    # gate via commission_plan(); a machine that path cannot serve is REFUSED out loud.
    if action == "list":
        return (
            f"{mac_prefix}tmux list-sessions -F '#{{session_name}}: #{{session_activity_string}}' 2>/dev/null",
            "tmux list-sessions -F '#{session_name}: #{session_activity_string}' 2>/dev/null",
        )
    elif action == "capture":
        return (
            f"{mac_prefix}tmux capture-pane -t {session} -p -S -30",
            f"tmux capture-pane -t {session} -p -S -30",
        )
    elif action == "kill":
        return (
            f"{mac_prefix}tmux kill-session -t {session}",
            f"tmux kill-session -t {session}",
        )
    return ("", "")


_current_channel = "voice"  # Set per-request in chat_completions handler

def _effective_focus_agent(gw_get):
    """Bounded content-scan targets for the resolver (spec §3.1 — never a full-fleet scan): the
    FOCUSED agent + the single most-recently-used agent. Voice focus wins, then the screen agent,
    then the freshest live agent (the §0a fixture: the operator was on the Arturo page but meant the GM's
    just-produced output — the freshest agent covers that). Returns (focused_agent, named_agents)."""
    focused, named = None, []
    try:
        from services.arturo.surface import current_focus
        ent = (current_focus(ARTURO_STATE / "focus.json") or {}).get("entity") or {}
        if ent.get("kind") == "agent":
            focused = str(ent.get("id", "")).split(":", 1)[-1] or None
        elif ent.get("kind") == "content" and ent.get("from_agent"):
            focused = ent["from_agent"]
    except Exception:
        pass
    if not focused:
        try:
            doc = json.loads((ARTURO_STATE / "active-surface.json").read_text())
            e = (doc.get("current") or {}).get("entity") or {}
            if e.get("kind") == "agent" and e.get("id"):
                focused = e["id"]
        except Exception:
            pass
    try:
        ag = gw_get("/agents")
        rows = ag.get("agents") if isinstance(ag, dict) else None
        live = [r for r in (rows or []) if isinstance(r, dict)
                and r.get("state") in ("working", "waiting", "stranded", "stalled", "idle")]
        live.sort(key=lambda r: -(r.get("last_used_ts") or 0))
        if live:
            top = live[0].get("tmux_session") or live[0].get("id")
            if top and top != focused:
                named.append(top)
    except Exception:
        pass
    return focused, named


def _spawn_tool_worker(fn_name, fn_args, user_turns=None):
    """Leg-3 daemon worker for a (blocking) tool call, JOURNAL-SAFE.

    REGRESSION FIX (gm msg_467bf15c): threading.Thread does NOT inherit
    contextvars, so the leg-3 wrap silently read _TOOLS_THIS_TURN as None in
    the worker — zero tool events journaled from the 05:00 Aug-18 respawn
    onward (v14's characterization; also the operator's missing chat dropdowns).
    copy_context() is captured HERE, in the calling (handler) thread, and the
    worker runs under it — the context copy shares the same bucket list
    object, so worker appends are visible to the handler's per-turn flush.

    Returns (worker_thread, holder): result in holder['r'], exception in
    holder['e'] (BaseException included — an empty holder must never silently
    become an empty tool result; caller re-raises)."""
    import threading as _threading
    _ctx = _contextvars.copy_context()
    holder = {}

    def _run_tool():
        try:
            holder["r"] = execute_tool(fn_name, fn_args, user_turns=user_turns)
        except BaseException as _e:  # noqa: BLE001 — AGY pass: SystemExit/KI too
            holder["e"] = _e

    worker = _threading.Thread(target=lambda: _ctx.run(_run_tool), daemon=True)
    worker.start()
    return worker, holder


# --- Commission (T2 acceptance): "commission an agent to X" = a REAL seat + a durable row ------
# The VPS path goes through the repo's spawn-agent.sh (runtime-declared, registered, the same
# adopt gate every seat uses) and files the task as a msg_store row from `arturo` to the seat,
# so the commission survives a restart and shows in the Inbox. The Mac path keeps the legacy
# raw-tmux spawn (spawn-agent.sh is a VPS-side script).

_DEFAULT_MODEL_FOR_RUNTIME = {"claude": "claude-sonnet-5", "gemini": "gemini-3.1-pro", "codex": "gpt-5.6-terra"}


class _CommissionPlan:
    def __init__(self, argv, env, session, task):
        self.argv, self.env, self.session, self.task = argv, env, session, task


def commission_plan(session, task, runtime=None, repo_root=None, model=None):
    root = Path(repo_root or _REPO_ROOT)
    rt = runtime or (getattr(brain, "runtime", None) if brain.kind == "runtime" else None) or "claude"
    env = {"AGENT_RUNTIME": rt, "AGENT_MODEL": model or RUNTIME_MODEL or _DEFAULT_MODEL_FOR_RUNTIME.get(rt, rt),
           "ORCHESTRA_DIR": str(ORCHESTRA_DIR), "PARENT_AGENT_ID": "arturo"}
    argv = ["bash", str(root / "spawn-agent.sh"), session]
    if task:
        argv += ["--task", task]
    return _CommissionPlan(argv, env, session, task)


def registration_status(name):
    """THREE states, never two: registered / not registered / could not check.

    A swallowed exception must never read as "not registered" — that inverts the
    purpose of this check (orchestra-builder gate on PR #8). Reads the flat registry,
    and the identity store when the install has one. Schema verified against a live
    store and the repo's own fixtures: canonical(root, generation_id, tmux_session,
    status) + generations(root, generation, resume_command, …).
    Returns {state: "registered"|"unregistered"|"unknown", where, detail}.
    """
    where, missing, unknown = [], [], []
    try:
        reg = json.loads((Path(ORCHESTRA_DIR) / "registry.json").read_text())
        if name in (reg.get("agents") or {}):
            where.append("registry.json")
        else:
            missing.append("no registry row")
    except FileNotFoundError:
        missing.append("no registry file")
    except Exception as e:  # noqa: BLE001 — unreadable is NOT the same as absent
        unknown.append(f"registry unreadable ({str(e)[:60]})")

    db = Path(ORCHESTRA_DIR) / "state" / "orchestra-registry.db"
    if db.exists():
        try:
            import sqlite3
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "SELECT c.root, g.resume_command FROM canonical c "
                    "LEFT JOIN generations g ON g.id = c.generation_id "
                    "WHERE c.root = ? LIMIT 1", (name,)).fetchone()
            finally:
                con.close()
            if row:
                where.append("identity store" + ("" if row[1] else " (no resume_command yet)"))
            else:
                missing.append("no canonical identity row")
        except Exception as e:  # noqa: BLE001
            unknown.append(f"identity store unreadable ({str(e)[:60]})")

    if unknown:
        return {"state": "unknown", "registered": False, "where": " + ".join(where),
                "detail": "; ".join(unknown + missing)}
    if where and not missing:
        return {"state": "registered", "registered": True, "where": " + ".join(where), "detail": ""}
    if where:
        # half-registered: flat row but no identity row (or vice versa) — say which.
        return {"state": "unregistered", "registered": False, "where": " + ".join(where),
                "detail": "; ".join(missing)}
    return {"state": "unregistered", "registered": False, "where": "", "detail": "; ".join(missing)}


def _run_commission(plan, timeout=120):
    env = dict(os.environ)
    env.update(plan.env)
    try:
        r = subprocess.run(plan.argv, capture_output=True, text=True, timeout=timeout, env=env,
                           cwd=str(_REPO_ROOT))
        out = (r.stdout + "\n" + r.stderr).strip()
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, f"spawn-agent.sh timed out after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _message_store():
    _sys.path.insert(0, str(_REPO_ROOT))
    from msg_store import MessageStore
    return MessageStore()


def _file_commission_row(session, task):
    """The durable half of a commission. Returns the msg id or '' (never raises)."""
    try:
        store = _message_store()
        subject = (task or f"commissioned {session}").strip().splitlines()[0][:80]
        return store.send(from_agent="arturo", to_agent=session, type="task", subject=subject,
                          body=task or subject, priority="medium", source="arturo", tenant_id="operator")
    except Exception as e:  # noqa: BLE001
        log.warning(f"commission row for {session} not filed: {e}")
        return ""


def _notify_spawned(session, machine):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    import requests as req_lib
    try:
        req_lib.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                     json={"chat_id": TELEGRAM_CHAT_ID,
                           "text": f"Agent spawned ({machine}): {session}\n\ntmux attach -t {session}",
                           "parse_mode": "HTML"}, timeout=5)
    except Exception:  # noqa: BLE001
        pass


def execute_tool(name, args, user_turns=None):
    """Execute a tool call with Mac→VPS fallback.

    user_turns: recent USER-role content strings from the live request — the Q0
    send_telegram appropriateness gate reads texting intent from them. Callers
    that don't thread turns fail CLOSED on send_telegram (policy: internal
    messages never reach the operator's TG without an ask or a deliverable link)."""
    log.info(f"EXECUTING TOOL: {name}({json.dumps(args)})")
    # Record the tool run for the per-call journal (gm bug fix). Best-effort — never let capture
    # break tool execution. The result is appended after the call returns (see the wrapper below).
    _bucket = _TOOLS_THIS_TURN.get()
    if _bucket is not None:
        try:
            _bucket.append({"tool": name, "args": args})
        except Exception:
            pass

    if name == "knowledge":
        query = args.get("query", "")
        category = args.get("category")
        return _knowledge_lookup(query, category)

    if name == "read_screen_context":
        # Mode 2 (voice-surface-context spec §2): resolve what the operator is looking at NOW + pull that
        # screen's live data via the local gateway. /agents pane-capture (~1s) runs under the
        # keepalive filler cover. Never guesses on a dead route.
        try:
            from services.arturo.surface import resolve_screen
            return resolve_screen(ARTURO_STATE / "active-surface.json",
                                  voice_calls_dir=VOICE_CALLS_DIR)
        except Exception as e:
            return f"Couldn't read your screen context: {e}"

    if name == "answer_menu":
        # Voice Deliberation Mode: verbal-select a parked agent's menu via the gateway's two-phase
        # POST /agent-key (spoken yes = the confirm phase). Uses the same bearer as the surface reads.
        session = str(args.get("session", "")).strip()
        option = str(args.get("option", "")).strip()
        confirm = bool(args.get("confirm", False))
        text = args.get("text")
        text = str(text).strip() if text is not None else None
        if not session or not option:
            return "answer_menu needs a session and an option number."
        try:
            import urllib.request as _u, json as _json
            from services.arturo import surface as _surface, deliberation as _delib

            def _gw_post(path, body):
                req = _u.Request(_surface.GATEWAY + path,
                                 data=_json.dumps(body).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {_surface._gw_token()}"})
                try:
                    with _u.urlopen(req, timeout=10) as r:
                        code = r.status
                        try:
                            return code, _json.loads(r.read().decode("utf-8", "replace"))
                        except Exception:
                            return code, {}
                except _u.HTTPError as he:
                    try:
                        return he.code, _json.loads(he.read().decode("utf-8", "replace"))
                    except Exception:
                        return he.code, {}
                except Exception as _e:
                    return 0, {"error": str(_e)}

            # DELIB-BUG-4 verify-after-answer: pass a short-timeout gw_get so answer_menu can
            # re-read /agent-screen after confirm and detect a menu that didn't actually resolve.
            def _gw_get_fast(path):
                return _surface._gw_get(path, timeout=3)
            # §1a input-kind routing: classify the target option from the gateway-stamped menu so a
            # 'Type something' (free_text) rides the verified three-phase with the operator's typed words and
            # a 'Chat about this' (chat) stays a keep-discussing affordance, never a stray keypress.
            try:
                _scr = _gw_get_fast(f"/agent-screen?session={session}")
                _menu = _scr.get("pending_menu") if isinstance(_scr, dict) else None
            except Exception:
                _menu = None
            _kind = _delib.classify_option(_menu, option)
            return _delib.answer_menu(_gw_post, session, option, confirm=confirm,
                                      gw_get=_gw_get_fast, text=text, input_kind=_kind)
        except Exception as e:
            return f"Couldn't answer the menu on {session}: {e}"

    if name == "focus_entity":
        # Speech-driven focus (spec §3.4): resolve the operator's spoken reference over the live candidate
        # union (pending approvals + agents + the focused/most-recent agent's recent OUTPUT) and, on
        # a strong match, assert voice focus. OBSERVE-ONLY by default (ARTURO_RESOLVE_OBSERVE=1):
        # resolve + log the scores/would-pick WITHOUT switching, so thresholds tune before auto-act
        # arms. Set ARTURO_RESOLVE_OBSERVE=0 (supervised) to arm the real focus switch.
        spoken_ref = str(args.get("spoken_ref", "")).strip()
        if not spoken_ref:
            return "focus_entity needs the operator's words (spoken_ref) to resolve."
        try:
            from services.arturo import surface as _surface
            from services.arturo.focus_tool import focus_entity as _focus_entity

            # Per-call GET cache: _effective_focus_agent and gather_candidates both read /agents —
            # cache idempotent GETs so a single focus_entity call hits each gateway path once.
            _get_cache = {}

            def _gw_get_fast(path):
                if path not in _get_cache:
                    _get_cache[path] = _surface._gw_get(path, timeout=3)
                return _get_cache[path]

            def _get_output(sess):
                # bounded tmux capture for the content provider (never a full-fleet scan)
                ok, out, _m = _run_on_machine(
                    f"PATH=/opt/homebrew/bin:$PATH tmux capture-pane -t {sess} -p -S -60",
                    f"tmux capture-pane -t {sess} -p -S -60", timeout=6)
                return clean_cc_output(out) if ok else ""

            # focused agent: voice focus > screen agent > single most-recently-used agent (fixture:
            # the operator was on the Arturo page but meant the GM's output — the freshest agent covers it).
            focused_agent, named = _effective_focus_agent(_gw_get_fast)
            observe = os.environ.get("ARTURO_RESOLVE_OBSERVE", "1") != "0"
            return _focus_entity(
                spoken_ref, _gw_get_fast, get_output=_get_output,
                focused_agent=focused_agent, named_agents=named,
                focus_path=ARTURO_STATE / "focus.json", observe=observe)
        except Exception as e:
            return f"Couldn't resolve what you're referring to: {e}"

    if name == "spawn_agent":
        session = args.get("session_name", "voice-agent")
        task = args.get("task", "")
        background = args.get("background", False)
        target_machine = args.get("machine", "vps")  # default VPS, "mac" if explicitly requested

        if target_machine == "mac":
            # The sanctioned spawn path is this machine's spawn-agent.sh (adopt gate). There is
            # no remote adopt seam here, and a raw remote tmux pane would be an UNREGISTERED
            # seat — refuse in words instead of silently making one.
            return (f"FAILED: I can only create REGISTERED seats, and that goes through this "
                    f"machine's spawn-agent.sh — I have no way to register '{session}' on another "
                    f"machine. Run `orchestra spawn {session}` there, or let me spawn it here.")

        if True:
            # VPS: a real seat through spawn-agent.sh + a msg_store commission row (T2).
            plan = commission_plan(session, task)
            log.info(f"COMMISSION: {' '.join(plan.argv[:3])} runtime={plan.env['AGENT_RUNTIME']}")
            ok, out = _run_commission(plan, 120)
            log.info(f"RESULT: ok={ok}, out={out[-300:]}")
            if not ok:
                if "duplicate session" in out.lower() or "already running" in out.lower():
                    return f"Session '{session}' already exists on vps. Use inject_message to send it a task."
                return f"FAILED to spawn '{session}' on vps: {out[-400:]}"
            verify_ok, _ = run_local(f"tmux has-session -t {session} 2>/dev/null", timeout=3)
            if not verify_ok:
                return f"FAILED: spawn-agent.sh ran but session '{session}' does not exist on vps: {out[-300:]}"
            msg_id = _file_commission_row(session, task)
            record_spawned_session(session)
            _record_spawned_this_turn(session)
            _notify_spawned(session, "vps")
            # Registration is the thing that makes a seat real (dashboard, mail, rotation,
            # park-not-retire). Verify it by effect and SAY which it is — never claim
            # "visible on the dashboard" without having looked.
            reg = registration_status(session)
            if reg["state"] == "registered":
                result = (f"CONFIRMED: Agent '{session}' is running on vps (runtime "
                          f"{plan.env['AGENT_RUNTIME']}) and is registered in {reg['where']} — "
                          f"it shows up in the agent list.")
            elif reg["state"] == "unknown":
                result = (f"Agent '{session}' is running on vps (runtime {plan.env['AGENT_RUNTIME']}), "
                          f"but I could not verify its registration ({reg['detail']}) — check "
                          f"`orchestra doctor` and the agent list before relying on it.")
            else:
                result = (f"PARTIAL: Agent '{session}' is running on vps (runtime "
                          f"{plan.env['AGENT_RUNTIME']}) but it is NOT registered ({reg['detail']}), "
                          f"so it will not show in the agent list and a crash could lose it. "
                          f"Check `orchestra doctor` and the spawn output.")
            if msg_id:
                result += f" Commission filed as {msg_id}."
            return result

        mac_cmd, vps_cmd = _tmux_cmd(session, "spawn")
        log.info(f"CMD: vps={vps_cmd}, target={target_machine}")
        ok, out, machine = _run_on_machine(mac_cmd, vps_cmd, prefer_mac=(target_machine == "mac"))
        log.info(f"RESULT: ok={ok}, machine={machine}, out={out[:200]}")

        if not ok and "duplicate session" in out.lower():
            return f"Session '{session}' already exists on {machine}. Use inject_message to send it a task."
        if not ok:
            if machine == "mac" and "MAC_UNREACHABLE" in out:
                return f"FAILED: Mac is not reachable. Cannot spawn '{session}' on Mac. the operator's Mac may be closed or offline."
            return f"FAILED to spawn '{session}' on {machine}: {out}"

        # Verify the session actually exists now
        if machine == "vps":
            verify_ok, _ = run_local(f"tmux has-session -t {session} 2>/dev/null", timeout=3)
        else:
            verify_ok, _ = ssh_mac(f"PATH=/opt/homebrew/bin:$PATH tmux has-session -t {session} 2>/dev/null", timeout=5, force=True)
        if not verify_ok:
            return f"FAILED: Spawn command ran but session '{session}' does not exist on {machine}. Something went wrong."

        result = f"CONFIRMED: Agent '{session}' is running on {machine}."
        if machine == "vps":
            result += " Visible on OrchestraOS dashboard."
        else:
            result += " Running on Mac."

        # Auto-register in registry.json so it appears on dashboard immediately
        try:
            reg_file = ORCHESTRA_DIR / "registry.json"
            reg = json.loads(reg_file.read_text())
            if session not in reg.get("agents", {}):
                reg["agents"][session] = {
                    "name": session.replace("-", " ").title(),
                    "tier": "T2",
                    "machine": machine,
                    "tmux_session": session,
                    "always_on": False,
                    "system_prompt": "",
                    "memory_scope": ["global"],
                    "cwd": str(Path.home()),
                    "parent": "gemini-gm"
                }
                reg_file.write_text(json.dumps(reg, indent=2))
                result += " Auto-registered in OrchestraOS."
        except Exception as e:
            log.warning(f"Failed to auto-register {session}: {e}")

        # Track in voice memory
        record_spawned_session(session)
        _record_spawned_this_turn(session)

        # Auto-text session name + attach command to Telegram
        import requests as req_lib
        try:
            req_lib.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": f"Agent spawned ({machine}): {session}\n\ntmux attach -t {session}",
                    "parse_mode": "HTML",
                },
                timeout=5,
            )
        except Exception:
            pass

        # Inject initial task — same three-runtime two-phase invariant as inject_message (Bug 1):
        # literal paste (no Enter), settle, then a standalone C-m. Never a single text+Enter call.
        if task:
            time.sleep(2)
            is_mac = (machine == "mac")
            paste = _pane_inject.paste_command(session, task, mac=is_mac)
            submit = _pane_inject.submit_command(session, mac=is_mac)
            if is_mac:
                ok2, _ = ssh_mac(paste)
                time.sleep(_pane_inject.SUBMIT_SETTLE_S)
                ssh_mac(submit)
            else:
                ok2, _ = run_local(paste)
                time.sleep(_pane_inject.SUBMIT_SETTLE_S)
                run_local(submit)
            if ok2:
                result += f" Initial task injected: {task[:100]}"
            else:
                result += " Warning: task injection failed."
        return result

    elif name == "inject_message":
        session = args.get("session_name", "")
        message = args.get("message", "")
        # Block internal artifacts (context summaries, compaction output) from being injected
        _BLOCK_MARKERS = [
            "This session is being continued from a previous conversation",
            "This is a context summary",
            "The summary below covers the earlier portion",
            "Continue the conversation from where it left off",
        ]
        if any(m in message for m in _BLOCK_MARKERS):
            return f"Blocked: message looks like an internal context summary, not a real task. Not injected into '{session}'."
        # Q0 addendum item 3 (the operator spec, gm msg_599e3219): every Arturo-originated
        # pane inject is attributed with the inter-agent bracket convention so the
        # receiving agent knows the source. Mechanical, idempotent prepend.
        if not message.startswith("[Arturo Voice]:"):
            message = f"[Arturo Voice]: {message}"
        # BUG-1 three-runtime injection invariant (gemini-gm dx msg_d30b98a6/msg_4fbc8aee,
        # the operator-ratified). A single `send-keys '{text}' Enter` submits inconsistently across runtimes:
        # on Gemini/AGY the paste never fires onSubmit, and on Claude/Codex the trailing Enter is
        # absorbed into the paste buffer -> text strands unexecuted. TWO-PHASE fixes it: paste the
        # LITERAL text (no Enter), let the composer settle, then a STANDALONE carriage return (C-m).
        # Landing is judged by the COMPOSER state (cleared / turn active), never whole-pane text
        # visibility (the old false-positive). A retry sends ANOTHER C-m ONLY — never a re-paste —
        # which is the actual cure for the AGY duplicate.
        import time as _t

        def _paste(mach):
            return _run_on_machine(
                _pane_inject.paste_command(session, message, mac=True),
                _pane_inject.paste_command(session, message, mac=False),
                prefer_mac=(mach == "mac"))

        def _submit(mach):
            return _run_on_machine(
                _pane_inject.submit_command(session, mac=True),
                _pane_inject.submit_command(session, mac=False),
                prefer_mac=(mach == "mac"))

        def _landed(mach):
            cok, cout, _ = _run_on_machine(
                f"PATH=/opt/homebrew/bin:$PATH tmux capture-pane -t {session} -p -S -40",
                f"tmux capture-pane -t {session} -p -S -40",
                timeout=8, prefer_mac=(mach == "mac"))
            if not cok:
                return False
            return _pane_inject.is_landed(cout, message)

        # phase 1: literal paste (also resolves which machine the session is on)
        ok, out, machine = _paste("vps")
        if not ok:
            # try Mac explicitly before giving up (session may live on the Mac)
            ok, out, machine = _paste("mac")
            if not ok:
                return f"Failed to inject on {machine}: {out}"
        # phase 2: settle, then a standalone carriage return to fire onSubmit
        _t.sleep(_pane_inject.SUBMIT_SETTLE_S)
        _submit(machine)
        _t.sleep(0.6)                       # let the TUI render the submitted turn
        if _landed(machine):
            return f"Message injected into '{session}' on {machine} (verified landed)."
        # retry: send ANOTHER C-m ONLY — never re-paste (that is what duplicated on AGY)
        log.warning(f"inject_message: '{session}' landing UNVERIFIED on first try — retrying C-m only")
        _submit(machine)
        _t.sleep(0.6)
        if _landed(machine):
            return f"Message injected into '{session}' on {machine} (verified on retry)."
        return (f"Injected into '{session}' on {machine} but COULD NOT VERIFY it landed — "
                f"the session may be busy. Tell the operator it may not have gone through.")

    elif name == "get_agent_output":
        session = args.get("session_name", "")
        lines = min(args.get("lines", 80), 200)  # Default 80 lines, max 200
        # Capture WITH SGR (-e) so the composer's ghost/draft can be classified by
        # style (gm msg_91dd9aa5 / composer-is-a-draft-surface + ghost-vs-typed law).
        # A plain -p capture attributed a CLI ghost suggestion in gm's composer as
        # gm's actual statement (the operator-caught, vc_client_573f6b8afffe).
        mac_cmd = f"PATH=/opt/homebrew/bin:$PATH tmux capture-pane -t {session} -e -p -S -{lines}"
        vps_cmd = f"tmux capture-pane -t {session} -e -p -S -{lines}"
        ok, out, machine = _run_on_machine(mac_cmd, vps_cmd, timeout=10)
        if ok:
            # MECHANICAL ghost/draft strip via the blessed agent-status style-walk:
            # real output = content ABOVE the input box; a ghost is a CLI suggestion
            # (never agent output); a draft is unsubmitted (tagged not-delivered).
            try:
                from services.arturo import pane_output as _po
                parts = _po.split_agent_pane(out)
                rendered = _po.render_for_voice(parts)
                cleaned = clean_cc_output(rendered)
                if parts.get("ghost"):
                    cleaned += (f"\n[note: a CLI ghost SUGGESTION was present in the composer "
                                f"and was NOT included — it is not {session}'s statement.]")
            except Exception:
                # fail-safe: never worse than the old behavior — strip SGR + clean
                from services.arturo import pane_output as _po2
                cleaned = clean_cc_output(_po2._agent_status_mod().strip_ansi(out))
            if len(cleaned) > 3000:
                cleaned = "...(truncated)...\n" + cleaned[-3000:]
                first_nl = cleaned.find("\n", 20)
                if first_nl > 0:
                    cleaned = "...(truncated)...\n" + cleaned[first_nl+1:]
            return f"Output from '{session}' ({machine}):\n{cleaned}"
        return f"Session '{session}' not found on {machine}: {out}"

    elif name == "list_agents":
        # Get live tmux sessions
        ok_v, out_v = run_local("tmux list-sessions -F '#{session_name}' 2>/dev/null", timeout=5)
        live_sessions = set(s.strip() for s in (out_v or "").split("\n") if s.strip()) if ok_v else set()
        infra = {"api-server", "combo-proxy", "custom-llm", "dashboard", "telegram-router",
                 "message-router", "cf-tunnel", "restarter", "session-0", "session-1"}

        # Get hierarchy data for richer context
        hierarchy_agents = []
        try:
            sys.path.insert(0, str(ORCHESTRA_DIR))
            from msg_store import MessageStore
            _store = MessageStore()
            hierarchy_agents = _store.hierarchy_list(tenant_id="operator")
            # Also check readiness for running agents
            sys.path.insert(0, str(ORCHESTRA_DIR / "lib"))
            from agent_readiness import check_agent_ready as _check_ready
        except Exception:
            _check_ready = None

        if hierarchy_agents:
            # Rich view from hierarchy — show role, project, status
            parts = []
            for h in hierarchy_agents:
                aid = h.get("agent_id", "")
                role = h.get("role", "?")
                project = h.get("project", "")
                tmux_name = aid  # Default: agent_id IS tmux session name
                is_live = tmux_name in live_sessions or aid in live_sessions
                readiness = ""
                if is_live and _check_ready:
                    try:
                        readiness = f" [{_check_ready(tmux_name)}]"
                    except Exception:
                        pass
                status = f"RUNNING{readiness}" if is_live else "stopped"
                always_on = " (always-on)" if h.get("always_on") else ""
                parts.append(f"  {aid}: {role} for {project} — {status}{always_on}")

            # Also list running sessions NOT in hierarchy (orphans)
            hierarchy_ids = {h["agent_id"] for h in hierarchy_agents}
            orphans = [s for s in live_sessions - infra if s not in hierarchy_ids]
            if orphans:
                parts.append(f"\n  Unregistered running sessions: {', '.join(sorted(orphans))}")

            return f"Agents ({len(hierarchy_agents)} registered, {len(live_sessions - infra)} running):\n" + "\n".join(parts)
        else:
            # Fallback: raw tmux list if hierarchy is empty
            agent_sessions = sorted(live_sessions - infra)
            return f"{len(live_sessions)} sessions on VPS. Agent sessions: {', '.join(agent_sessions)}"

    elif name == "kill_agent":
        session = args.get("session_name", "")
        mac_cmd = f"PATH=/opt/homebrew/bin:$PATH tmux kill-session -t {session}"
        vps_cmd = f"tmux kill-session -t {session}"
        ok, out, machine = _run_on_machine(mac_cmd, vps_cmd)
        if ok:
            return f"Session '{session}' killed on {machine}."
        return f"Failed to kill '{session}' on {machine}: {out}"

    elif name == "send_telegram":
        message = args.get("message", "")
        # Q0 (the operator ruling 2026-08-18, gm msg_6f4f2050): mechanical appropriateness
        # gate BEFORE the outbox — internal/system messages never reach the operator's TG
        # unless he asked for a text or the message carries a clickable link. The
        # tool description already said this and the model ignored it (05:02 +
        # 05:21 field misroutes); mechanism, not prose. The deny result TEACHES
        # (speak instead) and never claims the send happened. (async_task's TG
        # return path is the operator-initiated by construction — it routes via
        # _deliver/tg-notify and is deliberately NOT gated here.)
        _ok, _why = _voice_guards.tg_send_appropriate(message, user_turns)
        if not _ok:
            log.warning(f"send_telegram DENIED (appropriateness gate): {message[:80]!r}")
            return _why
        log.info(f"send_telegram gate ALLOW ({_why}): {message[:80]!r}")
        # BUG-1 (P0): hard cap + dedupe. A live call looped this tool 26x (mostly one replayed
        # "[Forwarded to GM]" text) → ~73 texts for one lookup. Block duplicates + rate-storms
        # BEFORE the POST so a trivial request can never spam the operator.
        _ok, _reason = _TG_OUTBOX.allow(message)
        if not _ok:
            log.warning(f"send_telegram SUPPRESSED ({_reason}): {message[:80]!r}")
            # Report success-shape so the model doesn't retry-loop trying to 'fix' a failed send.
            return f"Message already handled ({_reason}) — not resent (anti-spam guard)."
        import requests as req_lib
        try:
            resp = req_lib.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
                timeout=10,
            )
            if resp.status_code == 200:
                return "Message sent to the operator on Telegram."
            return f"Failed to send: {resp.text[:200]}"
        except Exception as e:
            return f"Telegram error: {e}"

    elif name == "deep_query":
        # P1b Tier 2 (D1/D2): ephemeral read-only analyst under semaphore + 12s wall + mem
        # precheck. ANY failure degrades to the Tier-3 async path (async_task(gm_command) —
        # inherits provenance/role-inversion/TG-outbox) and speaks the first-person D4 line.
        from services.arturo import dispatcher as _dp
        question = args.get("question", "")
        if not question:
            return "ERROR: deep_query called with an empty question."
        _ok, _text = _dp.run_analyst(question)
        if _ok:
            return _text
        execute_tool("async_task", {
            "tool_name": "gm_command",
            "tool_args": {"prompt": question, "timeout": 120},
            "summary": f"Deep dive: {question[:80]}",
        })
        return _dp.SPOKEN_DEEP_FALLBACK

    elif name == "ask_gm":
        # P1b Tier 3 (D3): thin alias over the hardened async_task(gm_command) pipeline —
        # NO new dispatch machinery; verified TG delivery + _TG_OUTBOX dedupe + role-inversion
        # guard all inherited from the reused branches. First-person ack only (D4).
        from services.arturo import dispatcher as _dp
        request = args.get("request", "")
        if not request:
            return "ERROR: ask_gm called with an empty request."
        execute_tool("async_task", {
            "tool_name": "gm_command",
            "tool_args": {"prompt": request, "timeout": 120},
            "summary": args.get("summary") or request[:80],
        })
        return _dp.SPOKEN_ASK_GM_ACK

    elif name == "gm_command":
        prompt = args.get("prompt", "")
        timeout = min(args.get("timeout", 60), 120)
        if not prompt:
            return "ERROR: gm_command called with empty prompt. The AI did not provide a query. This is a bug — retry or rephrase."

        # ROLE-INVERSION GUARD (gm datapoint 2026-08-10): the `prompt` is model-chosen and
        # relays voice content — it must NOT reach gm as if the operator TYPED it directly. Two guards:
        # (1) reject an assistant-style greeting echoed back as a "question" (the leak signature:
        #     a simulated/model turn like "Hi Arturo! I'm here to chat and help..." routed into
        #     gm_command) — synthetic self-talk is never a real fleet query.
        _low = prompt.strip().lower()
        _inversion_markers = ("i'm here to chat", "how can i help", "how can i assist",
                              "what's on your mind", "i'm arturo", "arturo here",
                              "i am here to chat", "what can i do for you")
        if any(m in _low for m in _inversion_markers):
            log.warning(f"ROLE-INVERSION BLOCKED: assistant-style text routed to gm_command: {prompt[:80]!r}")
            return ("BLOCKED: that looks like assistant/greeting text, not a real request from the operator. "
                    "Do NOT relay it to the GM. Ask the operator what he actually needs.")
        # (2) tag provenance so gm can never mistake a voice-relayed query for the operator's typed input.
        prompt = f"[via Arturo voice — relay of the operator's spoken request] {prompt}"

        # Use dedicated jarvis-gm for voice/chat queries (doesn't block main GM)
        # Falls back to main GM via gm-converse.sh if jarvis-gm unavailable
        gm_script = str(ORCHESTRA_DIR / "jarvis-gm-query.sh")
        try:
            r = subprocess.run(
                ["bash", gm_script, prompt, str(timeout)],
                capture_output=True, text=True, timeout=timeout + 10,
            )
            if r.returncode == 0 and r.stdout.strip():
                response = r.stdout.strip()
                # Truncate if too long for voice context
                if len(response) > 3000:
                    response = response[:3000] + "\n... (truncated)"
                return f"GM response:\n{response}"
            else:
                error = r.stderr.strip() or r.stdout.strip() or "No response"
                return f"GM error: {error[:500]}"
        except subprocess.TimeoutExpired:
            return f"GM timed out after {timeout}s. The query may be too complex for voice. Try breaking it down."
        except Exception as e:
            return f"GM error: {e}"

    elif name == "read_file":
        path = args.get("path", "")
        lines = min(args.get("lines", 50), 200)
        machine = args.get("machine", "vps")
        if not path:
            return "No path provided."

        # Expand ~ for the target machine
        cmd = f"head -n {lines} {path} 2>/dev/null || echo 'FILE_NOT_FOUND'"
        if machine == "mac":
            ok, out = ssh_mac(cmd, timeout=10)
        else:
            ok, out = run_local(cmd, timeout=10)

        if "FILE_NOT_FOUND" in out:
            return f"File not found: {path} on {machine}"
        if ok:
            if len(out) > 2000:
                out = out[:2000] + "\n... (truncated)"
            return f"Contents of {path} ({machine}):\n{out}"
        return f"Failed to read {path} on {machine}: {out[:300]}"

    elif name == "run_command":
        command = args.get("command", "")
        machine = args.get("machine", "vps")
        timeout = min(args.get("timeout", 15), 30)
        if not command:
            return "No command provided."

        if machine == "mac":
            ok, out = ssh_mac(command, timeout=timeout)
        else:
            ok, out = run_local(command, timeout=timeout)

        if ok:
            if len(out) > 2000:
                out = out[:2000] + "\n... (truncated)"
            return f"Command output ({machine}):\n{out}"
        return f"Command failed on {machine}: {out[:500]}"

    elif name == "set_operator_fact":
        # Onboarding facts are BRAIN-extracted (Shaw 2026-09-22): the operator said who they are
        # in conversation; the brain understood it; this is the only writer of operator.json.
        from services.arturo import operator_store as _ops
        try:
            entry = _ops.set_fact(ARTURO_STATE, args.get("field", ""), args.get("value", ""), source="brain")
        except ValueError as e:
            return f"Not recorded: {e}"
        except OSError as e:               # unwritable state dir: the turn must not 500 on a nicety
            log.warning(f"operator fact not stored: {e}")
            return f"Not recorded: could not write the operator store ({e.__class__.__name__})"
        log.info(f"operator fact set [{args.get('field')}] = {entry['value']!r}")
        return f"Recorded: {args.get('field')} = {entry['value']}"

    elif name == "remember_note":
        note = args.get("note", "")
        category = args.get("category", "behavior")
        if not note:
            return "No note provided."
        mem = load_voice_memory()
        entry = {
            "note": note,
            "category": category,
            "created": datetime.now(timezone.utc).isoformat(),
        }
        # VQ-10 (the operator): supersede/conflict resolution — the old dedup only dropped EXACT-text twins,
        # so "the X bug is broken" and "X is fixed now" both lingered and CONTRADICTED. Replace any
        # prior note ABOUT THE SAME SUBJECT with this newer one (recency genuinely wins on a topic),
        # not just byte-identical ones. See services/arturo/note_store.py.
        from services.arturo.note_store import supersede_notes
        mem["notes"], _superseded = supersede_notes(mem.get("notes", []), entry, cap=50)
        save_voice_memory(mem)
        log.info(f"Saved voice note [{category}] (superseded {_superseded} prior on-topic): {note[:100]}")
        return f"Noted and saved: {note[:100]}"

    elif name == "query_roadmap":
        project = args.get("project", "all")
        status_filter = args.get("filter", "all")
        try:
            import sqlite3
            conn = db_connect.connect(str(ORCHESTRA_DIR / "state" / "tasks.db"))
            conn.row_factory = sqlite3.Row
            if project == "all":
                rows = conn.execute(
                    "SELECT r.name as roadmap, rp.name as phase, rt.title, rt.status FROM roadmap_tasks rt JOIN roadmap_phases rp ON rt.phase_id=rp.id JOIN roadmaps r ON rp.roadmap_id=r.id" +
                    (" WHERE rt.status=?" if status_filter != "all" else "") +
                    " ORDER BY r.name, rp.sort_order, rt.sort_order",
                    [status_filter] if status_filter != "all" else []
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT r.name as roadmap, rp.name as phase, rt.title, rt.status FROM roadmap_tasks rt JOIN roadmap_phases rp ON rt.phase_id=rp.id JOIN roadmaps r ON rp.roadmap_id=r.id WHERE LOWER(r.name) LIKE ?" +
                    (" AND rt.status=?" if status_filter != "all" else "") +
                    " ORDER BY rp.sort_order, rt.sort_order",
                    [f"%{project.lower()}%"] + ([status_filter] if status_filter != "all" else [])
                ).fetchall()
            conn.close()
            if not rows:
                return f"No roadmap data found for '{project}'."
            lines = []
            current_phase = None
            for r in rows:
                if r["phase"] != current_phase:
                    current_phase = r["phase"]
                    lines.append(f"\n{r['roadmap']} — {current_phase}:")
                status_icon = {"completed": "✓", "in_progress": "→", "blocked": "✗", "pending": "○"}.get(r["status"], "?")
                lines.append(f"  {status_icon} {r['title']}")
            return "\n".join(lines)
        except Exception as e:
            return f"Roadmap query error: {e}"

    elif name == "research":
        query = args.get("query", "")
        if not query:
            return "No research query provided."
        return execute_tool("async_task", {
            "tool_name": "spawn_agent",
            "tool_args": {
                "session_name": f"research-{int(time.time()) % 10000}",
                "machine": "vps",
                "prompt": f"Research this topic thoroughly and text the operator the results on Telegram when done:\n\n{query}\n\nUse WebSearch and WebFetch tools. Be concise but comprehensive. Send findings via: curl -s -X POST 'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage' -d chat_id={TELEGRAM_CHAT_ID} --data-urlencode 'text=Research results: {query[:50]}...\\n\\n<your findings>'"
            },
            "summary": f"Web research: {query[:80]}"
        })

    elif name == "client_briefing":
        client = args.get("client", "").lower()
        if not client:
            return "No client specified."
        try:
            import sys as _sys_brief
            _sys_brief.path.insert(0, str(ORCHESTRA_DIR / "lib"))
            from context_engine import ContextEngine
            engine = ContextEngine()
            parts = []
            # Project info from knowledge
            k = _knowledge_lookup(client)
            if k and "No knowledge found" not in k:
                parts.append(k)
            # Recent messages
            result = engine.query(f"messages {client}")
            msgs = result.get("messages", [])
            if msgs:
                parts.append(f"\nRecent messages ({len(msgs)}):")
                for m in msgs[:5]:
                    parts.append(f"  {m.get('from_agent','')} → {m.get('to_agent','')}: {m.get('subject','')}")
            # Roadmap status
            roadmap = execute_tool("query_roadmap", {"project": client, "filter": "all"})
            if roadmap and "No roadmap" not in roadmap:
                parts.append(f"\nRoadmap:{roadmap}")
            return "\n".join(parts) if parts else f"No data found for client '{client}'."
        except Exception as e:
            return f"Briefing error: {e}"

    elif name == "async_task":
        tool_name = args.get("tool_name", "")
        tool_args = args.get("tool_args", {})
        summary = args.get("summary", tool_name)
        if not tool_name:
            return "No tool_name provided."

        import threading
        def _run_async():
            # VQ-4 verify-after-inject: the async result MUST actually LAND on Telegram. The old
            # path used raw requests.post with `except: pass` — a failed send was swallowed, so
            # Arturo had already said "I'll text you" but nothing arrived (conv_2901). Route through
            # scripts/tg-notify.sh, which verifies ok:true (2 internal retries) and exits non-zero
            # on failure — so a silent delivery failure becomes a LOUD log, not a false success.
            def _deliver(text):
                # BUG-1: same cap+dedupe as send_telegram — an async_task retry-loop must not storm.
                _ok, _reason = _TG_OUTBOX.allow(text)
                if not _ok:
                    log.warning(f"async_task TG delivery SUPPRESSED ({_reason}): {text[:80]!r}")
                    return True                    # treat as delivered — don't retry-loop the storm
                try:
                    r = subprocess.run(
                        ["bash", str(ORCHESTRA_DIR / "scripts" / "tg-notify.sh"),
                         "--from", "arturo-voice", text],   # message is POSITIONAL (fleet convention)
                        capture_output=True, text=True, timeout=60,
                    )
                    if r.returncode != 0:
                        log.error(f"VQ-4: async_task TG delivery FAILED (rc={r.returncode}) "
                                  f"for {summary!r}: {(r.stderr or r.stdout).strip()[:200]}")
                    return r.returncode == 0
                except Exception as _de:
                    log.error(f"VQ-4: async_task TG delivery EXCEPTION for {summary!r}: {_de}")
                    return False
            try:
                result = execute_tool(tool_name, tool_args)
                # An error-shaped result should NOT be framed as a success (✅).
                _low = (result or "").strip().lower()
                _is_err = _low.startswith(("gm error", "gm timed out", "error", "blocked", "❌", "no response"))
                mark = "\u274c" if _is_err else "\u2705"
                _deliver(f"{mark} {summary}\n\n{(result or '')[:1500]}")
            except Exception as e:
                _deliver(f"\u274c {summary}\n\nFailed: {e}")

        t = threading.Thread(target=_run_async, daemon=True)
        t.start()
        log.info(f"ASYNC TASK launched: {tool_name}({json.dumps(tool_args)[:200]})")
        return f"Task queued: {summary}. I'll text you the result on Telegram when it's done."

    elif name == "agent_message":
        from_agent = args.get("from_agent", "jarvis")
        to_agent = args.get("to_agent", "")
        subject = args.get("subject", "")
        body = args.get("body", "")
        priority = args.get("priority", "medium")
        if not to_agent or not subject:
            return "to_agent and subject are required."
        try:
            sys.path.insert(0, str(ORCHESTRA_DIR))
            from msg_store import MessageStore
            store = MessageStore()
            msg_id = store.send(
                from_agent=from_agent, to_agent=to_agent,
                type="task", subject=subject, body=body,
                priority=priority, source="jarvis", tenant_id="operator",
            )
            return f"Message sent to {to_agent}: [{msg_id}] {subject}. Router will deliver when {to_agent} is idle."
        except Exception as e:
            return f"Failed to send message: {e}"

    elif name == "read_agent_conversation":
        agent_id = args.get("agent_id", "")
        conversation_id = args.get("conversation_id", "")
        if not agent_id:
            return "agent_id is required."
        try:
            sys.path.insert(0, str(ORCHESTRA_DIR))
            from msg_store import MessageStore
            store = MessageStore()
            if conversation_id:
                status = store.conversation_status(conversation_id)
                if status.get("error"):
                    return f"No conversation found: {conversation_id}"
                msgs = status.get("messages", [])
                lines = [f"Conversation {conversation_id} ({status.get('mode','?')}, {status.get('status','?')}, {len(msgs)} msgs):"]
                for m in msgs[-10:]:
                    lines.append(f"  [{m.get('created_at','')[:19]}] {m.get('from_agent','')} → {m.get('to_agent','')}: {m.get('subject','')[:60]} [{m.get('status','')}]")
                return "\n".join(lines)
            else:
                msgs = store.inbox(agent_id, tenant_id="operator")
                pending = [m for m in msgs if m.get("status") == "pending"]
                parts = [f"Inbox for {agent_id}: {len(pending)} pending messages"]
                for m in pending[:5]:
                    parts.append(f"  [{m.get('priority','?')}] from {m.get('from_agent','?')}: {m.get('subject','')[:60]}")
                return "\n".join(parts)
        except Exception as e:
            return f"Failed to read messages: {e}"

    return f"Unknown tool: {name}"


# --- Voice Memory (persists across calls) ---

VOICE_MEMORY_FILE = ARTURO_STATE / "voice-memory.json"          # ARTURO-namespaced
VOICE_TRANSCRIPTS_DIR = ORCHESTRA_DIR / "voice-transcripts-arturo"  # ARTURO-namespaced


def load_voice_memory():
    if VOICE_MEMORY_FILE.exists():
        try:
            return json.loads(VOICE_MEMORY_FILE.read_text())
        except Exception:
            pass
    return {"spawned_sessions": [], "recent_transcripts": [], "notes": []}


def save_voice_memory(mem):
    VOICE_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    VOICE_MEMORY_FILE.write_text(json.dumps(mem, indent=2))


def record_spawned_session(session_name):
    """Track sessions spawned via voice so Jarvis remembers them across calls."""
    mem = load_voice_memory()
    entry = {"name": session_name, "spawned_at": datetime.now(timezone.utc).isoformat()}
    mem["spawned_sessions"] = [s for s in mem["spawned_sessions"] if s["name"] != session_name]
    mem["spawned_sessions"].append(entry)
    # Keep last 20
    mem["spawned_sessions"] = mem["spawned_sessions"][-20:]
    save_voice_memory(mem)


def save_transcript(conversation_id, transcript_data):
    """Save a call transcript for persistent memory."""
    VOICE_TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = VOICE_TRANSCRIPTS_DIR / f"{conversation_id}.json"
    filepath.write_text(json.dumps(transcript_data, indent=2))

    # Also update voice memory with summary
    mem = load_voice_memory()
    summary = []
    for t in transcript_data.get("transcript", []):
        role = t.get("role", "?")
        msg = t.get("message", "")[:150]
        summary.append(f"[{role}] {msg}")

    mem["recent_transcripts"].append({
        "conversation_id": conversation_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": "\n".join(summary[-10:]),  # Last 10 turns
    })
    # Keep last 5 transcripts in memory
    mem["recent_transcripts"] = mem["recent_transcripts"][-5:]
    save_voice_memory(mem)


# --- Context Injection ---

def _brain_identity_line():
    """One sentence, by effect, about what is generating this very reply."""
    b = brain
    d = b.describe() if hasattr(b, "describe") else {"kind": getattr(b, "kind", "?")}
    if d.get("kind") == "runtime":
        pretty = {"claude": "Claude", "gemini": "Gemini", "codex": "Codex"}.get(d.get("runtime", ""), d.get("runtime", ""))
        model = d.get("model", "")
        model_txt = "" if (not model or model.endswith("-cli-default")) else f" ({model})"
        return (f"YOUR BRAIN (by effect): you are thinking with {pretty}{model_txt} through the operator's own "
                f"logged-in `{d.get('cli', pretty.lower())}` CLI — no API key is involved. If asked what model or "
                f"provider you run on, say exactly that; never claim another provider or a 'layer'.")
    if d.get("kind") == "api":
        return (f"YOUR BRAIN (by effect): you are thinking with the {d.get('model', 'configured')} API model via the "
                f"operator's API key. If asked what model you run on, say exactly that.")
    return ("YOUR BRAIN (by effect): no brain is configured yet (no API key and no logged-in CLI); "
            "if asked, say so plainly and point at `orchestra doctor`.")


def build_context(calling_channel="voice"):
    """Build LEAN context for any channel — must be fast. ~5K chars max.
    calling_channel: 'voice', 'telegram', or 'dashboard'."""
    parts = []

    # Channel-specific tone instructions
    CHANNEL_TONES = {
        "voice": "VOICE conversation. Be concise (2-3 sentences max). No markdown/bullets. Spoken dialogue only.",
        "telegram": "TELEGRAM conversation. Be terse and direct. Plain text only, no markdown. 1-3 sentences.",
        "dashboard": "DASHBOARD conversation. Brief responses. Light formatting OK.",
        "text": "TEXT conversation (the Arturo home / pill). Brief, plain sentences; no markdown headers or bullet walls. 1-4 sentences unless asked for more.",
    }
    tone = CHANNEL_TONES.get(calling_channel, CHANNEL_TONES["voice"])

    parts.append(f"You are ARTURO — the operator's AI Operations Commander (the voice and text front door of OrchestraOS). Your name is Arturo; if asked who you are, you are Arturo. {tone}")
    # Name the brain that is ACTUALLY answering (release-readiness v1: a zero-key install used to
    # say "backed by a Gemini-powered layer" because this text was hardcoded). Read by effect from
    # the live brain object (it can be re-selected after a post-boot login, G14).
    parts.append(_brain_identity_line())
    # Who the operator IS (brain-extracted at onboarding, services/arturo/operator_store.py).
    # One line, only when something is actually known — never a placeholder name.
    try:
        from services.arturo import operator_store as _ops
        _op_line = _ops.context_line(ARTURO_STATE)
        if _op_line:
            parts.append(_op_line)
    except Exception:  # noqa: BLE001 — identity is a nicety; a turn never fails on it
        pass
    parts.append(f"""ONE IDENTITY (critical): You and your deep brain are ONE. When you use gm_command / async_task / inject_message you are consulting your OWN deeper reasoning and full-context memory — the manager seat running in the '{VOICE_BRAIN_SESSION}' session. It is NOT a separate person. NEVER refer to "the GM" out loud, NEVER say "I've sent it to the GM", "I'll ask the GM", or "waiting to hear back from the GM". Speak in the FIRST PERSON: "Let me think on that — I'll text you", "Still working through it", "I looked into it", "Give me a bit and I'll get back to you". Any deep question you can't answer instantly, you route to your own deep brain via async_task (which guarantees a Telegram answer back to the operator) — and you say so in the first person.""")
    parts.append(f"""Rules: Be concise (2-3 sentences max). No markdown/bullets. Spoken dialogue only.
Push back when you disagree. You're a strategic partner, not a yes-machine.
NEVER read URLs aloud. NEVER ask for confirmation — just execute.
APPROVAL GATES: ask before production deploys, client comms, spending money, destructive ops.

TONE: Talk like a real person. NEVER say "one moment, executing that now" — just do it silently and report the result.
When looking something up, don't announce it. Just check and answer naturally.
WHEN CORRECTED: Say "got it" or "my bad" and immediately act. NEVER say "You are absolutely right, the operator. My apologies." — the operator hates that phrase. One brief acknowledgment max, then move on. Action over apology.
WHEN THE OPERATOR SAYS "DON'T" or "STOP" or "NOT THAT": Immediately comply. Do NOT explain what you were about to do. Do NOT announce an alternative using the same tool. Just stop and listen.
PAUSES & SILENCE ESCALATION (the operator directive): When there is a pause or lull:
- Stage 1 (Initial pause/lull): Do NOT use generic re-engagement chatter like "Is there anything specific you'd like me to help with?". Instead, proactively suggest work or next steps based on available context (what the operator was working on, active agent tasks, pending approvals, or open roadmap items) or ask a focused follow-up.
- Stage 2 (Second pause): Offer gentle standby reassurance ("Take your time, I'm right here whenever you're ready.").
- Stage 3 (Extended silence): Give a warm sign-off ("Sounds like you're busy. Call me if you need anything!") before wrapping up.
NEVER SPEAK INTERNAL AGENT IDS: refer to work by its human title + a brief description ("the Arturo voice pipeline, live and hardened"), NEVER the internal session id like "jarvis-poc-builder-v3" or "<name>-v2". the operator doesn't think in agent ids — suppress them from anything you say aloud; surface the summary instead.

TOOLS: You have REAL tools that execute on the operator's Mac AND VPS. NEVER fabricate results.
NEVER CLAIM YOU DID SOMETHING WITHOUT CALLING THE TOOL. If you say "I've injected" or "I've instructed" or "I've sent", a tool_call MUST have fired. If you're not sure a tool executed, say "let me try that" — not "done."

ANSWER FULLY: When you call a tool and get results, SYNTHESIZE the answer. Do NOT just acknowledge what you found ("I can see the output shows..."). Instead, ANSWER THE QUESTION using the data. If the operator asks "what's the agent working on?" and you get output, tell him WHAT it's working on — don't describe that you received output. If you need more info to fully answer, call another tool — you can chain up to 5 tool calls per question.

DEEP DIVE: When the operator says "dig into", "deep dive", "research", "investigate", or "look into" — this means he wants a thorough answer, not a surface-level response. Use multiple tools if needed: get_agent_output to see what an agent is doing, read_file to check code, run_command to check git history, etc. Chain tools until you have enough context to give a real answer. For complex research that needs web access or extended investigation, spawn a research agent via async_task.

ASYNC TASK: Use async_task ONLY for genuine long-running BACKGROUND work. It runs the tool in the background and texts the operator ONE result on Telegram. Say "On it, I'll text you when it's done" and move on.
  ALWAYS use async_task for:
  - Any Mac operation (SSH takes 3-15s, will timeout the voice call)
  - Any spawn_agent with machine="mac"
  - Genuinely complex multi-step / long-running operations
  NEVER wrap a TRIVIAL lookup (who's the agent for X, what's the status of Y) in async_task — just call the fast tool (knowledge/list_agents/get_agent_output) directly and answer. Wrapping a quick lookup in async_task caused a 73-text Telegram STORM on a live call.
  For FAST VPS-only operations (list_agents, get_agent_output, inject_message, kill_agent on VPS), call directly — they complete in <2s.
  TELEGRAM DISCIPLINE (hard rule): send the operator AT MOST ONE text per request. NEVER call send_telegram repeatedly, NEVER re-send the same/near-identical message, NEVER replay "[Forwarded to GM]" texts. If you already said you'd text him, do NOT text again to confirm. One request → at most one text.

AGENT TOOLS: spawn_agent, inject_message, get_agent_output, list_agents, kill_agent — manage tmux + claude sessions.
  MACHINE RULE: Default is VPS. ONLY set machine="mac" when the operator EXPLICITLY says "on my Mac" or "on the Mac". When spawning on Mac, ALWAYS use async_task since SSH may be slow.
  AGENT LOOKUP RULE: When the operator mentions an agent by name or description, ALWAYS call list_agents FIRST to get the real session names. NEVER guess session names.
  ROUTING RULE: If a specific agent session exists for a task, use inject_message ONLY to talk to that agent. Only use gm_command for questions that need the GM's cross-project judgment, codebase access, or multi-step reasoning. NEVER send the same task to BOTH gm_command AND inject_message — that causes duplicate work.
  AFTER SPAWNING: Verify the session exists with list_agents or get_agent_output before reporting success.

CIRCUIT BREAKER: If a tool returns the same unhelpful result twice, DO NOT call it again. Try a DIFFERENT tool:
  - knowledge returns "No knowledge found" → try gm_command or read_file
  - list_agents doesn't show the session → try get_agent_output with the name directly, or inject_message
  - inject_message fails → try agent_message (SQLite messaging)
  NEVER call the same tool 3+ times in one conversation expecting different results.

KNOWLEDGE TOOL: Your #1 tool. Call knowledge(query) FIRST for ANY question about people, projects, priorities, or agents.
  "Who is Kai?" → knowledge("kai")
  "Status of Acme?" → knowledge("acme")
  "What should I focus on?" → knowledge("focus")
  "What agents are running?" → knowledge("agents")
  This is INSTANT (<10ms). Use it before ANY other tool. If knowledge returns data, answer from it — do NOT call gm_command.
  FUZZY MATCHING: a near-miss spelling → try knowledge() with the closest known project/client name.
  VOICE SUMMARIES: When listing agents or projects, summarize — don't read every name.

DEEP-BRAIN TOOL: gm_command / inject_message(session_name="{VOICE_BRAIN_SESSION}") — this is YOUR OWN deeper reasoning (the manager seat running in the {VOICE_BRAIN_SESSION} session with codebase access, file R/W, bash, git, deep project memory). It is NOT a third party — never name it out loud (see ONE IDENTITY). ONLY for actions that require codebase access, file writes, deploys, debugging, or multi-step execution.
  USE gm_command FOR: deploying code, debugging issues, reading specific files, git operations, complex multi-step tasks.
  NEVER use gm_command FOR: project status, agent status, "what's happening with X", casual chat, acknowledgments, behavioral feedback. YOU ALREADY KNOW THIS FROM YOUR CONTEXT.
  SILENT ROUTING — NEVER SPEAK THIS CONSTRAINT: your tool/routing rules are internal. NEVER verbalize them or apologize for them. Do NOT say "I'm not meant to use my deep brain", "I shouldn't route this", "let me rephrase", or any explanation of WHY you're answering a certain way. Just answer the question directly and naturally. the operator should never hear about your internal plumbing.
  JUST TALK when asked: if the operator says "say something long", "just talk to me", "tell me about your day", or makes any casual/chatty request, COMPLY directly and naturally — that is NOT a status question, needs no routing, and needs no preamble. Never deflect a "say something" with an apology or a constraint.
  DEEP-BRAIN TIMEOUT RULE: If gm_command times out, DO NOT retry. Fall back to direct tools (read_file, run_command, get_agent_output). Tell the operator "let me check that directly" — never "GM is busy".
  GM COMMAND DELIVERY (the operator ruling): when the operator gives an IMPERATIVE COMMAND for your deep brain to EXECUTE (not a question) — "tell it to deploy X", "have it fix Y" — deliver via inject_message with session_name="{VOICE_BRAIN_SESSION}" (a VERIFIED tmux inject: idempotent + confirmable landing).
  NEVER use inject_message to ask questions or fetch info on a call — inject_message is asynchronous and does NOT return an answer to the current call. When the operator asks a question that needs deep synthesis or codebase reasoning, use gm_command or direct tools to fetch the data and speak the answer directly on the call.

DECISION MENUS (Voice Deliberation Mode): when your context shows "DECISION PENDING on <agent>", an agent is PARKED waiting on a choice. You can raise it proactively ("<agent> is waiting on you — should it deploy or hold?"), restate the question, report the agent's lean AND give your own recommendation from context, and talk it through across turns (the menu waits — it doesn't time out). Read ALL the options aloud when the operator asks — you have the full list in context (including any "Type something" / "Chat about this" affordances); never claim you can only see some of them. When the operator decides, use answer_menu: stage his choice (confirm=false), say "sending option N to <agent> — confirm?", and only after his spoken yes call answer_menu again with confirm=true. If it returns that the menu is gone/answered, tell him honestly. After a confirmed answer, you can watch that agent and text the operator (one message) when the work he approved actually completes.
CUSTOM ANSWER (the operator's answer isn't one of the listed options): the menu usually has a "Type something" (free-text) option. This is now ONE atomic step: call answer_menu with `option` = the "Type something" option's number AND `text` = the operator's EXACT custom words — I select the option and type his words in a single verified action (confirm his wording back to him first, then stage confirm=false, then confirm=true on his spoken yes). This is your native path for "tell the agent to do X instead". Do NOT fall back to the old select-then-inject_message two-step for a free-text menu option — the atomic answer_menu(text=...) is verified end-to-end (a bare digit alone only opens the field and strands it). If answer_menu reports it did not go through (menu gone / submit failed), say so honestly — never claim it landed when it didn't. A "Chat about this" option is NOT a decision: it means keep talking it through — just keep deliberating with the operator, don't send it as an answer. Do NOT use async_task or a message-router for menu commands — those silently dropped on a live call and the operator saw nothing land. Reserve async_task for genuine long background WORK, and even then it sends at most one verified text.
LISTING PENDING ITEMS (the operator refinements): DISTINGUISH approvals from decisions — never lump them. Say "two ledger approvals and one decision," not "three approvals." Don't say "interactive decision menu" — say "<agent> needs a decision." Always include the decision's TOPIC when you list it: "menu-canary-throwaway needs a decision on the Q3 rollout," not just that a decision exists.
POCKET RECORDINGS (the operator refinement): when reading a pocket recording, DON'T read the raw title string — say what it's ABOUT and who was on the call ("the Adaptiv meeting with Paul Smith, Steve Marshall, and Luke Deviney"), not the filename/title.

DIRECT TOOLS: read_file (read any file on Mac/VPS), run_command (run any shell command on Mac/VPS).
  Use these for quick lookups AND as fallback when GM is busy.
ROADMAP TOOL: query_roadmap(project, filter) — instant query of tasks.db. Use for "what's blocked?", "how's the acme project doing?", "what's in progress?". Filter options: all, blocked, in_progress, completed, pending.
RESEARCH TOOL: research(query) — spawns a background research agent for web queries. Results arrive via Telegram. Use for competitor analysis, market research, fact-checking. ALWAYS use this instead of saying "I can't browse the web."
CLIENT BRIEFING TOOL: client_briefing(client) — pre-call briefing with knowledge, recent messages, and roadmap status. Use when the operator says "brief me on X" or before client calls.
TELEGRAM: send_telegram — only when the operator asks or there's a clickable deliverable. Send ACTUAL content, not JSON.
MEMORY: remember_note — call it ONLY on genuinely note-worthy content: when the operator says 'remember this', 'don't say that', 'stop doing X', 'always do Y', gives behavioral feedback, or states a durable fact/preference worth persisting. Then do it IMMEDIATELY — don't wait, don't ask permission; notes persist across ALL future calls so the operator never repeats himself. NEVER fire remember_note on greetings, acknowledgments, chit-chat, "just testing"/"quick test" turns, or your own summaries — a hollow note is noise. If nothing note-worthy was said, save nothing.
  WHEN SAVING URLs: Always include the source agent name. Format: "URL from [agent_name]: [url]"
WHEN THE OPERATOR REPORTS A PROBLEM (a UI glitch, a bug, something looks wrong): NEVER be dismissive — do NOT say "that's a UI issue on your end", "nothing I can do", "I can't fix that", or brush it off. Acknowledge it briefly and capture it so the right builder sees it (remember_note the concrete report, and/or route it) — you and your deep brain CAN get it fixed. Also don't over-apologize: at most ONE brief "my bad" per call, then move on — repeated "my bad"/"my apologies" is its own annoyance.
NEVER FABRICATE AWARENESS OR DIAGNOSIS: do NOT claim you "already know about" / "am aware of" / "that's a known glitch" / assert what is CAUSING something, UNLESS that fact is actually in your context. the operator will catch a guess asserted as fact and it destroys trust. When you don't know: say so plainly and capture it — "I'm not sure what's causing that — noting it so it gets looked at", NOT "that's a UI glitch I'm aware of". Acknowledge + note; never claim knowledge you don't have.
CRITICAL: get_agent_output returns REAL terminal text. You CAN see any tmux session. NEVER say "I can't see" — you CAN. If the operator says he can see something in an agent's output and you don't, call get_agent_output AGAIN with more lines. Do NOT deny what the operator can plainly see.
TRUNCATION RULE: If get_agent_output contains "...(truncated)..." it means you're seeing PARTIAL output. NEVER fabricate, guess, or complete what came before the truncation marker. If the operator asks about content you can't see, say "I can only see the last portion of the output — want me to capture more lines?" and re-call get_agent_output with lines=200.
After spawning, the session name is auto-texted to Telegram.""")

    # Voice memory — THIS IS CRITICAL. the operator expects you to remember previous calls.
    mem = load_voice_memory()
    if mem.get("spawned_sessions") or mem.get("recent_transcripts") or mem.get("notes"):
        parts.append("\n=== YOUR MEMORY FROM PREVIOUS VOICE CALLS ===")
        parts.append("You HAVE talked to the operator before via voice. The transcripts below are REAL conversations YOU had with him.")

    # NOTES — the operator's feedback and instructions. OBEY THESE.
    if mem.get("notes"):
        parts.append("\nRULES FROM THE OPERATOR (you saved these — follow them):")
        for n in mem["notes"]:
            cat = n.get("category", "")
            parts.append(f"  [{cat}] {n['note']}")

    if mem.get("spawned_sessions"):
        sessions = ", ".join(s["name"] for s in mem["spawned_sessions"][-10:])
        parts.append(f"\nSESSIONS YOU PREVIOUSLY SPAWNED: {sessions}")
    if mem.get("recent_transcripts"):
        parts.append("PREVIOUS CONVERSATIONS (most recent first):")
        for t in reversed(mem["recent_transcripts"][-3:]):
            parts.append(f"[Call at {t['timestamp'][:19]}]\n{t['summary'][:800]}")
        parts.append("=== END VOICE MEMORY ===")

    # Implicit memory — auto-extracted facts from conversations
    implicit_profile = ORCHESTRA_DIR / "state" / "users" / "operator" / "profile.json"
    if implicit_profile.exists():
        try:
            profile = json.loads(implicit_profile.read_text())
            facts = profile.get("implicit_facts", [])
            if facts:
                # Show last 10 most recent implicit facts
                recent_facts = facts[-10:]
                parts.append("\nLEARNED FROM CONVERSATIONS (implicit memory):")
                for f in recent_facts:
                    ftype = f.get("type", "")
                    parts.append(f"  [{ftype}] {f.get('fact', '')[:120]}")
        except Exception:
            pass

    # Unified conversation log — cross-channel awareness
    # Show messages from OTHER channels so Jarvis knows what the operator said elsewhere
    channel_map = {"voice": "jarvis-voice", "telegram": "telegram", "dashboard": "dashboard"}
    my_channel = channel_map.get(calling_channel, "jarvis-voice")
    unified_log_file = ORCHESTRA_DIR / "state" / "unified-conversation.jsonl"
    if unified_log_file.exists():
        try:
            recent_lines = unified_log_file.read_text().strip().split("\n")[-20:]
            if recent_lines and recent_lines[0]:
                cross_channel = []
                for line in recent_lines:
                    try:
                        entry = json.loads(line)
                        ch = entry.get("channel", "")
                        if ch and ch != my_channel:
                            role = entry.get("role", "?")
                            content = entry.get("content", "")[:200]
                            cross_channel.append(f"  [{ch}:{role}] {content}")
                    except Exception:
                        pass
                if cross_channel:
                    parts.append("\nRECENT CROSS-CHANNEL (the operator said these on other interfaces):")
                    parts.extend(cross_channel[-8:])
        except Exception:
            pass

    # Knowledge system — Jarvis uses the `knowledge` tool for people, projects, priorities, agents
    # Only inject a brief reminder of what's available, NOT the data itself
    parts.append("\nYou have a `knowledge` tool for instant lookups. Use it for ANY question about people, projects, status, priorities, or agents. It's faster than reading files or calling GM.")

    # Recent Telegram context — so Jarvis knows what was sent/received on Telegram
    tg_log = ORCHESTRA_DIR / "state" / "telegram-outbound.jsonl"
    if tg_log.exists():
        try:
            recent_tg = tg_log.read_text().strip().split("\n")[-5:]
            if recent_tg:
                parts.append("\nRECENT TELEGRAM MESSAGES (you sent these or the system sent these to the operator):")
                for line in recent_tg:
                    entry = json.loads(line)
                    parts.append(f"  [{entry.get('source','?')}] {entry.get('text','')[:200]}")
        except Exception:
            pass

    # Live state is now served by the knowledge tool — no need to dump into prompt

    # Inject learned rules from learning.db
    try:
        import sqlite3
        _learn_db = str(ORCHESTRA_DIR / "state" / "learning.db")
        if os.path.exists(_learn_db):
            _lconn = sqlite3.connect(_learn_db, timeout=2)
            _rules = _lconn.execute(
                "SELECT rule_text FROM rules WHERE status IN ('active', 'probationary')"
            ).fetchall()
            _lconn.close()
            if _rules:
                parts.append("\nLEARNED RULES (from past interactions — follow these):")
                for r in _rules:
                    parts.append(f"  - {r[0]}")
    except Exception:
        pass

    # Mode 1 (voice-surface-context spec §2): one honest line about what the operator is looking at in
    # the app RIGHT NOW, so Arturo can ground "what am I looking at". Arturo KNOWS but does not
    # narrate navigation (no proactive announcements). Omitted entirely if the file is missing/
    # malformed (never inject garbage). Voice channel only.
    if calling_channel == "voice":
        try:
            from services.arturo.surface import readout_line as _readout
            _line = _readout(ARTURO_STATE / "active-surface.json")
            if _line:
                parts.append("\n" + _line)
        except Exception:
            pass

        # Voice Deliberation Mode (spec §5): surface any agent PARKED on a decision menu into the
        # per-turn context so Arturo can raise it + deliberate. Focused agent (from the surface
        # stack) gets the full menu; others are named. Cheap (one /agents boolean scan + one full
        # menu fetch). Degrades silently if the gateway is unreachable.
        try:
            from services.arturo import surface as _surface, deliberation as _delib
            _focused = None
            try:
                _fdoc = json.loads((ARTURO_STATE / "active-surface.json").read_text())
                _ent = (_fdoc.get("current") or {}).get("entity") or {}
                if _ent.get("kind") == "agent":
                    _focused = _ent.get("id")
            except Exception:
                _focused = None
            # SHORT timeout (reviewer note): this runs every voice turn — a slow gateway must
            # degrade to None, never stall the latency wall. 3s cap per GET vs the default 10s.
            def _gw_get_fast(path):
                return _surface._gw_get(path, timeout=3)
            # Voice-asserted focus (spec §3.2) is a first-class switch: if the operator voice-focused an
            # entity more recently than his screen, it becomes the effective top-of-mind. Hydrate it
            # (approval → question/from_agent/feature) and let a focused AGENT drive the menu detail.
            try:
                _vf = _surface.current_focus(ARTURO_STATE / "focus.json")
                if _vf:
                    _vent = _vf.get("entity") or {}
                    if _vent.get("kind") == "agent":
                        _focused = str(_vent.get("id", "")).split(":", 1)[-1] or _focused
                    elif _vent.get("kind") == "content" and _vent.get("from_agent"):
                        _focused = _vent["from_agent"]
                    _fb = _surface._voice_focus_block(_vent, _gw_get_fast)
                    if _fb:
                        parts.append("\nIN FRONT OF YOU (voice-asserted focus — heaviest):\n" + _fb)
            except Exception:
                pass
            # Deliberation now unions LIVE PANES + the pending-approval LEDGER (spec §3.3), so Arturo
            # can deliberate an approval even when its pane is gone / it's only a bridged card.
            _delib_blob = _delib.deliberation_context(_gw_get_fast, focused_session=_focused)
            if _delib_blob:
                parts.append("\n" + _delib_blob +
                             "\nYou can talk the operator through these and, when he decides, act: for a live "
                             "pane menu use answer_menu; for a ledger approval it goes through the "
                             "approval answer path (he confirms by voice). Restate the question, give "
                             "the agent's lean AND your own recommendation. If the operator refers to one by "
                             "description ('that adaptiv approval'), call focus_entity first.")
        except Exception:
            pass

    parts.append(f"\n--- TIME: {datetime.now(timezone.utc).isoformat()} ---")

    return "\n".join(parts)


# --- SSE Streaming with Tool Execution ---

_apology_count = 0  # Reset per-request

_APOLOGY_PATTERNS = [
    "my apologies", "my bad", "i apologize", "i'm sorry",
    "you are absolutely right", "you're absolutely right",
]

def _filter_voice_response(content):
    """Strip repeated apology phrases for voice channel."""
    global _apology_count
    if _current_channel != "voice":
        return content
    lower = content.lower()
    for pattern in _APOLOGY_PATTERNS:
        if pattern in lower:
            _apology_count += 1
            if _apology_count > 1:
                # Strip the apology, keep the rest
                import re
                content = re.sub(r'(?i)(my apologies[,.]?\s*|my bad[,.]?\s*|i apologize[,.]?\s*|i\'?m sorry[,.]?\s*|you are absolutely right[,.]?\s*|you\'re absolutely right[,.]?\s*)', '', content)
            break
    return content

# ============================================================================================
# WATCH PUSH-TO-TALK (PTT) — lifecycle-free stateless turn.  SPEC_watch-ptt-endpoint.md.
# ★ gm gate condition (msg_dc8d4197/msg_05158a29): a PTT turn is a STATELESS HTTP turn keyed on
# conversation_id, NOT an ElevenLabs voice CALL. It runs STT -> the Arturo brain -> TTS and touches
# NONE of the call lifecycle: no _CallJournal, no _log_voice_turn/capture_mid_call_turns, no
# end-of-call finalize/gm-inject/ended_once. That is why the brain reuse below does NOT go through the
# journaled chat_completions() route — it builds context + calls the model directly.
# ============================================================================================
from services.arturo import ptt as _ptt
from services.arturo import local_stt as _local_stt   # item C: key-free web dictation (lazy: never imports faster-whisper here)
# item C: fetch the local speech model in the background at boot (never inside a request). No-op on a
# slim install (faster-whisper absent) or when the files are already on disk. Must sit AFTER the import.
_local_stt.prefetch(log=log)

PTT_STT_MODEL = os.environ.get("ARTURO_PTT_STT_MODEL", "scribe_v1")
# Arturo's ElevenLabs voice for TTS replies (mp3, AVAudioPlayer-native). Overridable.
PTT_TTS_VOICE_ID = os.environ.get("ARTURO_PTT_TTS_VOICE_ID", secrets.get("ARTURO_TTS_VOICE_ID", ""))
PTT_TTS_MODEL = os.environ.get("ARTURO_PTT_TTS_MODEL", "eleven_turbo_v2_5")
# Per-call timeouts so the loopback proxy thread fails WITHIN the gateway's 30 s budget instead of
# running (and charging) after the gateway has already given up.
PTT_STT_TIMEOUT_S = float(os.environ.get("ARTURO_PTT_STT_TIMEOUT", "15"))
PTT_BRAIN_TIMEOUT_S = float(os.environ.get("ARTURO_PTT_BRAIN_TIMEOUT", "20"))

_PTT_HISTORY = _ptt.PttHistory()
_PTT_TURN_CACHE = _ptt.TurnCache()
# Single-flight for turn_id: an in-flight turn registers here so a CONCURRENT retry with the same
# turn_id waits for it instead of launching a second STT/brain/TTS pipeline (Flask is threaded).
_PTT_INFLIGHT = {}
_PTT_INFLIGHT_LOCK = _threading.Lock()

# STT VENDOR (gm lane ruling msg_be5c97df, 2026-09-08): ElevenLabs scribe_v1 — the funded,
# funnel-consistent vendor (~0.65s flat, same key as the EL funnel). OpenAI Whisper is RETIRED
# here: the key auths but billable calls 429 credit_balance_exhausted (verified twice by effect),
# which made the original v1 route a latent live-502 behind green vendor-stubbed tests. Fallback
# = Gemini 2.5 Flash inline-audio transcription (also funded). Both go through the _ptt_http
# transport seam so tests pin the LIVE endpoints, not a stub.

def _ptt_http(url, headers, body, timeout):
    """Transport seam (test-pinned): POST url with headers/body -> (status, response_bytes)."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _ptt_stt_elevenlabs(audio_bytes, filename):
    import uuid as _uuid
    key = secrets.get("ELEVENLABS_API_KEY", os.environ.get("ELEVENLABS_API_KEY", ""))
    boundary = "----arturo-ptt-" + _uuid.uuid4().hex
    fname = (filename or "audio.m4a").replace('"', "")
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"model_id\"\r\n\r\n{PTT_STT_MODEL}\r\n".encode())
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\n"
                  f"Content-Type: audio/mp4\r\n\r\n").encode() + audio_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    status, resp = _ptt_http(
        "https://api.elevenlabs.io/v1/speech-to-text",
        {"xi-api-key": key, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        body, PTT_STT_TIMEOUT_S)
    if status != 200:
        raise RuntimeError(f"EL scribe HTTP {status}: {resp[:200]!r}")
    return (json.loads(resp).get("text") or "").strip()


def _ptt_stt_gemini(audio_bytes, filename):
    import base64 as _b64
    key = secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
    payload = json.dumps({"contents": [{"parts": [
        {"text": "Transcribe this audio verbatim. Reply with ONLY the transcript text, nothing else. If there is no speech, reply with an empty string."},
        {"inline_data": {"mime_type": "audio/mp4",
                         "data": _b64.b64encode(audio_bytes).decode()}},
    ]}]}).encode()
    # key via x-goog-api-key header, never the URL (query strings leak into logs/error text)
    status, resp = _ptt_http(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        {"Content-Type": "application/json", "x-goog-api-key": key}, payload, PTT_STT_TIMEOUT_S)
    if status != 200:
        raise RuntimeError(f"Gemini STT HTTP {status}: {resp[:200]!r}")
    d = json.loads(resp)
    return ((d.get("candidates") or [{}])[0].get("content", {}).get("parts") or [{}])[0].get("text", "").strip()


def _ptt_stt(audio_bytes, filename):
    """Full-utterance STT: EL scribe_v1 primary, Gemini 2.5 Flash fallback. Returns the
    transcript ('' if no speech). Raises when BOTH vendors fail — ptt_turn maps that to a
    502 stt_failed; '' must only ever mean 'no speech' (422), never 'vendors down'."""
    try:
        return _ptt_stt_elevenlabs(audio_bytes, filename)
    except Exception as e:
        log.warning(f"ptt STT: EL scribe failed ({e}) — falling back to Gemini")
        return _ptt_stt_gemini(audio_bytes, filename)


def _ptt_brain(stt_text, conversation_id):
    """Lifecycle-free brain reply. Reuses build_context() + the LLM client directly — deliberately
    NOT the journaled chat_completions() route — so a PTT turn writes no journal and fires no
    end-of-call inject. Threads multi-turn context via the per-conversation_id history."""
    context = build_context(calling_channel="ptt")
    history = _PTT_HISTORY.get(conversation_id)
    messages = _ptt.build_messages(context, history, stt_text)
    resp = brain.complete(model=LLM_MODEL, messages=messages, timeout=PTT_BRAIN_TIMEOUT_S)
    reply = (resp.choices[0].message.content or "").strip()
    _PTT_HISTORY.append(conversation_id, "user", stt_text)
    _PTT_HISTORY.append(conversation_id, "assistant", reply)
    return reply


def _ptt_tts(text):
    """TTS via ElevenLabs -> mp3 bytes (AVAudioPlayer-native). ELEVENLABS_API_KEY is provisioned;
    there is no CARTESIA_API_KEY in .env.secrets, so EL (the funnel's TTS vendor) is the reachable path."""
    import requests as _requests
    el_key = secrets.get("ELEVENLABS_API_KEY", "")
    voice = PTT_TTS_VOICE_ID
    r = _requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
        headers={"xi-api-key": el_key, "Accept": "audio/mpeg", "Content-Type": "application/json"},
        params={"output_format": "mp3_44100_128"},
        json={"text": text, "model_id": PTT_TTS_MODEL},
        timeout=20,
    )
    r.raise_for_status()
    return r.content


def _ptt_run_pipeline(audio_bytes, filename, conversation_id):
    """STT -> brain -> TTS for one turn. Returns (http_status, result_dict). Honest failures; never a
    fabricated reply."""
    try:
        stt_text = _ptt_stt(audio_bytes, filename)
    except Exception as e:
        log.error(f"ptt_turn STT failure: {e}")
        return 502, {"ok": False, "error": "stt_failed"}
    if not stt_text:
        return 422, {"ok": False, "error": "no_speech"}
    try:
        reply_text = _ptt_brain(stt_text, conversation_id)
    except Exception as e:
        log.error(f"ptt_turn brain failure: {e}")
        return 502, {"ok": False, "error": "brain_failed"}
    try:
        import base64 as _b64
        audio_b64 = _b64.b64encode(_ptt_tts(reply_text)).decode("ascii")
    except Exception as e:
        log.error(f"ptt_turn TTS failure: {e}")
        return 502, {"ok": False, "error": "tts_failed"}
    return 200, {"ok": True, "reply_text": reply_text, "stt_text": stt_text,
                 "audio": audio_b64, "audio_format": "mp3"}


def ptt_turn(audio_bytes, filename, conversation_id, turn_id, content_type="audio/m4a"):
    """Orchestrate one stateless push-to-talk turn. Returns (http_status, result_dict).
    Success -> (200, {ok, reply_text, stt_text, audio(base64 mp3), audio_format}).
    Errors  -> (code, {ok:False, error:<code>}): 400 empty/bad_type, 413 too_large, 422 no_speech,
    502 stt/brain/tts failure.

    turn_id is idempotent AND single-flight: a repeated turn_id returns the cached turn, and a
    CONCURRENT retry (Flask is threaded) waits for the in-flight turn instead of launching a second
    STT/brain/TTS pipeline (no double charge). A FAILED turn is not cached, so it stays retryable."""
    ok, err = _ptt.validate_audio(len(audio_bytes or b""), content_type)
    if not ok:
        return (413 if err == "too_large" else 400), {"ok": False, "error": err}

    if not turn_id:
        return _ptt_run_pipeline(audio_bytes, filename, conversation_id)

    # single-flight on turn_id
    while True:
        with _PTT_INFLIGHT_LOCK:
            cached = _PTT_TURN_CACHE.get(turn_id)
            if cached is not None:
                return 200, cached                   # already completed -> idempotent
            ev = _PTT_INFLIGHT.get(turn_id)
            if ev is None:
                ev = _threading.Event()
                _PTT_INFLIGHT[turn_id] = ev          # become the leader
                break
        ev.wait(timeout=PTT_STT_TIMEOUT_S + PTT_BRAIN_TIMEOUT_S + 25)   # a duplicate: wait, then re-check
    try:
        code, result = _ptt_run_pipeline(audio_bytes, filename, conversation_id)
        if code == 200:
            _PTT_TURN_CACHE.put(turn_id, result)     # cache only success -> failures stay retryable
        return code, result
    finally:
        with _PTT_INFLIGHT_LOCK:
            _PTT_INFLIGHT.pop(turn_id, None)
        ev.set()                                     # wake any waiter (it re-checks the cache)


SSE_MODEL = "arturo-proxy"


def _clm_conversation_id(data, metadata, args):
    """Task 8: resolve the CLM callback's conversation id across BOTH vendors. EL stamps it in
    the body (system__conversation_id / conversation_id / metadata); Hume's CLM carries it ONLY
    as the ?custom_session_id= query param — the body fallback chain wins, query is last."""
    cid = (data.get("system__conversation_id") or data.get("conversation_id")
           or (metadata or {}).get("conversation_id") or args.get("custom_session_id") or "")
    return str(cid)[:200] if cid else ""


def _strip_hume_prosody(messages, is_hume):
    """Task 8 (spike-found, attribution-load-bearing): Hume's CLM appends a trailing {prosody}
    block to USER content. Left in place it poisons the journal, the dup/silence guards and the
    prompt itself. Strip it from user turns on Hume requests ONLY (an EL user could legitimately
    dictate braces) — and never mutate the caller's list (journal seams may hold a reference)."""
    if not is_hume:
        return messages
    import re
    out = []
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            stripped = re.sub(r"\s*\{[^{}]*\}\s*$", "", m["content"])
            if stripped != m["content"]:
                m = dict(m, content=stripped)
        out.append(m)
    return out


def make_sse_chunk(content):
    """Create an SSE data line with content. Chunk shape carries created/model and an explicit
    assistant role in the delta (Task 8 hygiene: Hume's OpenAI-compat parser is strict where
    EL's was lenient) — and NEVER a tool_calls delta; tools run server-side, text streams out."""
    content = _filter_voice_response(content)
    if not content:
        return ""  # Skip empty chunks after filtering
    return f"data: {json.dumps({'id': 'chatcmpl-proxy', 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': SSE_MODEL, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': content}, 'finish_reason': None}]})}\n\n"


def make_sse_done():
    """Create SSE done markers."""
    done = json.dumps({"id": "chatcmpl-proxy", "object": "chat.completion.chunk",
                       "created": int(time.time()), "model": SSE_MODEL,
                       "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    return f"data: {done}\n\ndata: [DONE]\n\n"


def capture_mid_call_turns(messages, calling_channel="voice"):
    """Save the latest user/assistant turns incrementally during a live call.
    This means voice memory stays warm even if the post-call webhook never fires.
    Also logs to unified conversation log for cross-channel awareness."""
    try:
        user_turns = [m for m in messages if m.get("role") == "user" and m.get("content")]
        assistant_turns = [m for m in messages if m.get("role") == "assistant" and m.get("content")]
        if not user_turns:
            return

        # Write to a rolling "live call" file (ARTURO-namespaced — full-overwrite, would clobber :5052)
        live_file = ARTURO_STATE / "voice-live-call.json"
        turns = []
        for m in messages:
            if m.get("role") in ("user", "assistant") and m.get("content"):
                turns.append({"role": m["role"], "text": m["content"][:300]})

        live_data = {
            "updated": datetime.now(timezone.utc).isoformat(),
            "turn_count": len(turns),
            "turns": turns[-20:],  # Last 20 turns
        }
        live_file.write_text(json.dumps(live_data, indent=2))

        # Log new turns to unified conversation log
        # Only log the latest turn to avoid duplicates
        unified_log_file = ARTURO_STATE / "unified-conversation.jsonl"  # ARTURO-namespaced write
        unified_log_file.parent.mkdir(parents=True, exist_ok=True)
        # Track what we've already logged via turn count
        _prev_count = getattr(capture_mid_call_turns, "_prev_turn_count", 0)
        current_count = len(turns)
        if current_count > _prev_count:
            new_turns = turns[_prev_count:]
            for turn in new_turns:
                role = "operator" if turn["role"] == "user" else "system"
                entry = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "channel": {"voice": "jarvis-voice", "telegram": "telegram", "dashboard": "dashboard"}.get(calling_channel, "jarvis-voice"),
                    "role": role,
                    "content": turn["text"],
                    "content_type": "text",
                }
                with open(unified_log_file, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            capture_mid_call_turns._prev_turn_count = current_count
    except Exception as e:
        log.error(f"Mid-call capture error: {e}")


VOICE_CALL_LOG = ARTURO_LOGS / "voice-call-trace.jsonl"  # ARTURO-namespaced (periodic full-rewrite)

def _log_voice_turn(user_msg: str, assistant_msg: str, tool_calls: list, tool_results: list, finish_reason: str):
    """Write a structured log entry pairing each voice turn with its tool calls and results.
    One JSONL file, one line per turn. This is the authoritative record for debugging."""
    try:
        VOICE_CALL_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "user": user_msg,
            "assistant": assistant_msg,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "finish_reason": finish_reason,
            "tools_claimed": bool(any(
                w in assistant_msg.lower()
                for w in ["i've injected", "i've instructed", "i've sent", "i've noted", "i've saved", "i will now", "i'm injecting"]
            )),
            "tools_actually_called": len(tool_calls) > 0,
        }
        # Flag hallucinated execution: claimed action but no tool fired
        if entry["tools_claimed"] and not entry["tools_actually_called"]:
            entry["HALLUCINATED_EXECUTION"] = True
            log.warning(f"HALLUCINATED TOOL EXECUTION: Jarvis said '{assistant_msg[:80]}' but no tool was called")

        with open(VOICE_CALL_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")

        # Rotate: keep last 500 entries
        try:
            lines = VOICE_CALL_LOG.read_text().strip().split("\n")
            if len(lines) > 500:
                VOICE_CALL_LOG.write_text("\n".join(lines[-400:]) + "\n")
        except Exception:
            pass
    except Exception as e:
        log.error(f"Voice call trace log error: {e}")


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    if not check_auth():
        return jsonify({"error": {"message": "unauthorized"}}), 401
    # GATE 2 (the operator order): classify request ORIGIN so ONLY a genuine the operator call can ever inject to
    # gm. Discriminator (measured): a real ElevenLabs call arrives via the tailscale funnel and
    # carries X-Forwarded-Host; a localhost curl/test does NOT. An explicit X-Arturo-Test header
    # force-marks test traffic even if it comes through the funnel (belt-and-suspenders for any
    # verification I must run). origin ∈ {"funnel","local","test"}; only "funnel" injects.
    if request.headers.get("X-Arturo-Test"):
        _origin = "test"
    elif request.headers.get("X-Forwarded-Host"):
        _origin = "funnel"
    else:
        _origin = "local"

    data = request.json or {}
    metadata = data.get("metadata", {})
    calling_channel = metadata.get("channel", "voice")
    want_stream = data.get("stream", True)
    # Task 8: ?custom_session_id= is Hume's CLM signature — it identifies the vendor AND is the
    # conv-id fallback. Hume user turns carry a trailing {prosody} block: strip it BEFORE the
    # guards/journal/prompt ever see the content (attribution-load-bearing, spike-found).
    _is_hume_clm = bool(request.args.get("custom_session_id"))
    messages = _strip_hume_prosody(data.get("messages", []), _is_hume_clm)

    # A.1 conv-id stamping (Layer 1): ElevenLabs agent_9001's extra_body carries the true
    # conversation id on every request (system__conversation_id, also sometimes conversation_id).
    # We stamp it on the server journal so it keys EXACTLY to the client journal (source client
    # spine) — and rekey the VQ-6 in-flight registry to conv_id so /finalize-call's settle can
    # detect THIS call's live generation. Absent on legacy/probe traffic → fall back as before
    # (Hume: the query param, Task 8).
    _conv_id = _clm_conversation_id(data, metadata, request.args)

    global _current_channel, _apology_count
    _current_channel = calling_channel
    _apology_count = 0

    log.info(f"Request: {len(messages)} messages, channel={calling_channel}, stream={want_stream}")

    # === GUARDRAIL: Intelligent silence escalation on voice ===
    # Stage 1 (1 silence turn): passes through to generation for contextual proactive suggestions.
    # Stage 2 (2 silence turns): gentle standby reassurance ("Take your time, I'm right here.").
    # Stage 3 (>=3 silence turns): graceful sign-off ("Sounds like you're busy. Call me if you need anything!").
    if calling_channel == "voice" and messages:
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user" and m.get("content"):
                last_user = m["content"].strip()
                break
        # Count consecutive empty/trivial user turns
        silence_count = 0
        for m in reversed(messages):
            if m.get("role") == "user":
                content = (m.get("content") or "").strip()
                if not content or content in ("...", ".", ""):
                    silence_count += 1
                else:
                    break
        if silence_count >= 3:
            log.warning(f"GUARDRAIL: {silence_count} consecutive silence turns — Stage 3 graceful sign-off")
            import random
            signoffs = [
                "Sounds like you're busy. Call me if you need anything!",
                "I'll let you go for now. Hit me up whenever you're ready!",
                "Call me whenever you need anything else. Talk soon!",
            ]
            def signoff_gen():
                yield make_sse_chunk(random.choice(signoffs))
                yield make_sse_done()
            return Response(signoff_gen(), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        if silence_count == 2:
            log.warning(f"GUARDRAIL: {silence_count} silence turns — Stage 2 gentle standby reassurance")
            import random
            reassurances = [
                "Take your time, I'm right here whenever you're ready.",
                "I'm right here whenever you need me.",
                "Take your time, I'm on standby.",
            ]
            def standby_gen():
                yield make_sse_chunk(random.choice(reassurances))
                yield make_sse_done()
            return Response(standby_gen(), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        # Also detect repetitive assistant responses (loop detection)
        recent_assistant = []
        for m in reversed(messages[-10:]):
            if m.get("role") == "assistant" and m.get("content"):
                recent_assistant.append(m["content"].strip()[:100])
        if _vq6.fuzzy_repeat(recent_assistant):   # VQ-6: fuzzy (was exact len(set)==1) — catches near-identical repeats
            log.warning(f"GUARDRAIL: Repetitive loop detected (fuzzy) — suppressing response")
            def loop_gen():
                yield make_sse_done()
            return Response(loop_gen(), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        # BUG-3 (app-dev-v4, call vc_2fb009f8): ElevenLabs re-sent the SAME utterance with tiny
        # variation AFTER Arturo already answered it → Arturo double-answered. VQ-6 only cancels an
        # IN-FLIGHT partial; a dup that arrives after the first answer streamed is sequential and
        # needs this stateless guard: if the latest user turn is a near-duplicate of an EARLIER,
        # already-answered user turn, SUPPRESS the re-answer (silence — the answer already went out).
        if _voice_guards.latest_is_answered_duplicate(messages):
            log.warning("BUG-3: latest user turn duplicates an already-answered turn — suppressing re-answer")
            def dup_gen():
                yield make_sse_done()
            return Response(dup_gen(), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        # HUME-SUPERSET (ios msg_7293955c): Hume re-sends one utterance as GROWING finals;
        # BUG-3's near-duplicate check deliberately rejects extensions, so each superset got a
        # fresh CLM round + journal turn + a repeated spoken reply. Hume path only (EL's
        # endpointer never sends supersets — its behavior stays byte-identical).
        if _is_hume_clm and _voice_guards.latest_is_answered_superset(messages):
            log.warning("HUME-SUPERSET: latest user final extends an already-answered final — suppressing re-answer")
            def sup_gen():
                yield make_sse_done()
            return Response(sup_gen(), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        # ANSWERED-REPEAT (ios 221 grade msg_3a5b4d4b, vc_13d96b7a5d1f6c6b): Hume keeps a
        # WINDOWED history and retried a double-POSTed request ~57s later with the IDENTICAL
        # history — the answered pair never appears in it, so BUG-3/superset are structurally
        # blind. Key on OUR per-cid memory of the last final we actually answered (recorded at
        # journaling time below); identical normalized text = repeat regardless of age, until
        # a different final is answered. Hume path only; short finals exempt inside the guard.
        if _is_hume_clm and _conv_id and _ANSWERED_FINALS.is_answered_repeat(
                _conv_id, _voice_guards.latest_user_text(messages)):
            log.warning("ANSWERED-REPEAT: latest user final identical to the last answered final "
                        f"on {_conv_id} — suppressing re-answer (windowed-history retry)")
            def rep_gen():
                yield make_sse_done()
            return Response(rep_gen(), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        # F2 cross-request dedup (DEC-1788674600635445): the SEQUENTIAL-resend gap VQ-6 + BUG-3 leave.
        # EL re-sent the IDENTICAL request (same history) AFTER the first completed but BEFORE its
        # answer was appended (vc_client_7c0e5662271e: 16:08:08 then 16:08:10, both streamed a full
        # response = double-response). Record first sight under lock; suppress an identical re-send
        # within the TTL so only ONE generate()/stream fires. Empty signature (no user turn) never dedups.
        _req_sig = _voice_guards.request_signature(messages)
        if _req_sig and _REQ_GUARD.is_duplicate(_req_sig, time.time()):
            log.warning(f"F2: duplicate request within TTL (sig={_req_sig}) — suppressing re-generate (prevents double-response)")
            def f2_gen():
                yield make_sse_done()
            return Response(f2_gen(), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # Capture turns mid-call for live memory
    capture_mid_call_turns(messages, calling_channel=calling_channel)

    # Build context and messages
    context = build_context(calling_channel=calling_channel)
    # IMPORTANT: Drop ElevenLabs system messages — they contain frozen/stale state
    # (hardcoded tmux sessions, task counts, agent lists from when the prompt was last synced).
    # The proxy's build_context() provides fresh, live data instead.
    non_system = [m for m in messages if m.get("role") != "system"]
    # Semantic recall (DEC-1788771883922080, BUILD-AND-HOLD): <=250-token paths-only RECALL
    # block appended to the per-turn context. Env-gated BEFORE the import so flag-off boots
    # byte-identical (metadata-absent requests default to channel 'voice' — the flag, not the
    # channel, keeps local traffic inert). Every failure inside degrades to no-preamble.
    if os.environ.get("ARTURO_SEMANTIC_RECALL") == "1" and calling_channel == "voice":
        try:
            from services.arturo import semantic_recall as _semrec
            _rp = _semrec.recall_preamble(_semrec.latest_user_text(non_system))
            if _rp:
                context += "\n\n" + _rp
        except Exception as _sre:
            log.error(f"semantic-recall seam error (non-fatal): {_sre}")
    # FACTS recall (semantic-memory-audit 2026-09-14): additive read-side wiring to
    # state/brain/facts.db (~23.5k structured facts refreshed DAILY by scripts/brain/ingest_run.py
    # — the freshest knowledge source on the box, which Arturo had ZERO wiring to). Independent of
    # and separately gated from semantic recall above; appends a <=250-token FACTS block alongside
    # (never replaces/reranks it). Read-only (mode=ro), bounded (top-K over a recency window behind
    # a budget wall + single-flight), degrades to no-block on any failure. Voice channel only.
    if os.environ.get("ARTURO_FACTS_RECALL") == "1" and calling_channel == "voice":
        try:
            from services.arturo import facts_recall as _factrec
            _fp = _factrec.facts_preamble(_factrec.latest_user_text(non_system))
            if _fp:
                context += "\n\n" + _fp
        except Exception as _fre:
            log.error(f"facts-recall seam error (non-fatal): {_fre}")
    # v2/(b) our-side replay (gm locked criterion #5): after an EL reconnect the fresh EL
    # session carries no history — re-seed the pre-drop turns at this callback seam so the
    # brain continues naturally (consume-once; conversation_id is OUR constant key).
    if _STREAM_RELAY is not None:
        try:
            _rcid = (_STREAM_RELAY.resolve(_conv_id) if _conv_id
                     else _STREAM_RELAY.sole_live())
            if _rcid:
                _rb = _STREAM_RELAY.replay_block(_rcid)
                if _rb:
                    context += "\n\n" + _rb
        except Exception as _rbe:
            log.error(f"stream-relay replay seam error (non-fatal): {_rbe}")
    final_messages = [{"role": "system", "content": context}]
    # Trim conversation to last 20 messages to prevent tool dropout
    if len(non_system) > 20:
        non_system = non_system[-20:]
    # Option-1 HISTORY SCRUB (commission msg_b82abb4f): strip canned fillers from
    # assistant history before it reaches the model — starves the log-proven
    # filler contagion (miner msg_48ce6fa2); with the outbound sanitizer the
    # replayed history self-cleans over time. non_system itself is NOT mutated
    # (journal/user-extraction below read the original).
    final_messages.extend(_voice_guards.scrub_history(non_system))

    log.info(f"Context: {len(context)} chars, messages: {len(final_messages)}")

    # Extract the latest user message for the call log. VQ-11 (app-dev-v4 forensics on
    # vc_e91877ed787b1316): this 500-char cap truncated the journaled user turn on WRITE while the
    # client journal held the full 843/781/668-char twins. Raise to 2000 for parity with the
    # gateway client-journal sanitizer so the server shard isn't lossy for tool-adjacent context.
    _user_msg = ""
    for m in reversed(non_system):
        if m.get("role") == "user" and m.get("content"):
            _user_msg = m["content"][:2000]
            break

    # Log to unified conversation log
    if _conv_log and _user_msg:
        try:
            _conv_log(channel=calling_channel, direction="inbound", from_id="operator", to_id="jarvis", text=_user_msg)
        except Exception:
            pass

    # §2 per-call journal — resolve this request to ONE call (disk overlap-match, restart-safe)
    # and record the user turn (best-effort; a journal error must NEVER break the SSE stream).
    _journal_cid = _resolve_or_create(non_system, page=calling_channel, origin=_origin,
                                      conv_id=_conv_id) if calling_channel == "voice" else None
    if _journal_cid and _user_msg:
        try:
            _journal_append(_journal_cid, "user", text=_user_msg)
        except Exception as _je:
            log.error(f"journal user-turn error: {_je}")
    # Arm per-turn tool capture (gm bug fix): execute_tool appends each run to this bucket;
    # the teeing wrapper journals them after the turn so summaries reflect real tool activity.
    if _journal_cid:
        _TOOLS_THIS_TURN.set([])

    # VQ-6 supersede-on-arrival: register THIS voice generation and, if it is the FULL utterance
    # completing an earlier PARTIAL still in flight (and that partial has not yet dispatched a
    # side-effecting tool), cancel the partial's response. `entry` is created HERE (handler scope)
    # so BOTH generate() and _journaling() close over the SAME object (ghost-turn suppression reads
    # entry["cancelled"] at stream end). Key is the single live-voice bucket for now; when conv-id
    # stamping (Piece A) lands, key becomes the conv id. Single-user => one live generation; the
    # is_partial_of MATCH (not the key) is what prevents any false cancel.
    # A.1: key by conv_id when present (so /finalize-call's settle detects THIS call's live
    # generation), else the shared live-voice bucket (single-user — no concurrent calls).
    _vq6_entry = None
    _vq6_key = None
    if calling_channel == "voice":
        _vq6_key = _conv_id or "__live_voice__"
        _vq6_entry = _vq6.new_entry(_user_msg or "")
        try:
            _sup = _VQ6.register_and_supersede(_vq6_key, _vq6_entry, _user_msg or "")
            if _sup is not None:
                log.info("VQ-6: superseded an in-flight partial response (full utterance arrived)")
        except Exception as _ve:
            log.error(f"VQ-6 register error: {_ve}")

    # Voice channel: gm_command now supported with dynamic tool heartbeat keep-alives.
    # async_task is held back in pass 1 so Gemini doesn't background live questions.
    voice_pass1 = calling_channel == "voice"
    if voice_pass1:
        VOICE_SLOW_TOOLS = {"async_task"}
        active_tools = [t for t in TOOLS if t["function"]["name"] not in VOICE_SLOW_TOOLS]
        log.info(f"Voice pass 1: {len(active_tools)} tools (async_task held back)")
    else:
        active_tools = TOOLS

    def generate():
        _gen_t0 = time.time()
        log.info(f"generate() START: channel={calling_channel}, history={len(final_messages)} msgs")
        try:
            # First call — with tools, force tool_choice auto
            response = brain.complete(
                model=LLM_MODEL,
                messages=final_messages,
                max_tokens=1024,
                temperature=0.7,
                tools=active_tools if active_tools else None,
                tool_choice="auto" if active_tools else None,
            )

            choice = response.choices[0]

            # Debug: log raw response details
            log.info(f"Raw response: finish_reason={choice.finish_reason}, "
                     f"tool_calls={bool(choice.message.tool_calls)}, "
                     f"content_len={len(choice.message.content or '')}")

            # If Gemini returns empty content and no tool calls, retry with tool_choice forced
            # This happens when Gemini "decides" not to respond (common with tool-heavy prompts)
            if not choice.message.tool_calls and not (choice.message.content or "").strip():
                log.warning("Empty response with no tools — retrying with tool_choice=required")
                try:
                    response = brain.complete(
                        model=LLM_MODEL,
                        messages=final_messages,
                        max_tokens=1024,
                        temperature=0.9,
                        tools=active_tools if active_tools else None,
                        tool_choice="required" if active_tools else None,
                    )
                    choice = response.choices[0]
                    log.info(f"Retry response: finish_reason={choice.finish_reason}, "
                             f"tool_calls={bool(choice.message.tool_calls)}, "
                             f"content_len={len(choice.message.content or '')}")
                except Exception as retry_err:
                    log.warning(f"Retry with tool_choice=required failed: {retry_err}")
                    # Final fallback — just acknowledge
                    if not choice.message.tool_calls and not (choice.message.content or "").strip():
                        choice.message.content = "I'm having trouble processing that. Could you try again?"
                        log.warning("Using fallback response after empty retry")

            # Check if the model wants to call tools
            # Note: Gemini's OpenAI-compat API may return finish_reason="stop" even with tool_calls
            if choice.message.tool_calls:
                tool_calls = list(choice.message.tool_calls)

                # === GUARDRAIL: Intercept gm_command for status/info questions ===
                gm_only = len(tool_calls) == 1 and tool_calls[0].function.name == "gm_command"
                if gm_only:
                    gm_prompt = ""
                    try:
                        gm_prompt = json.loads(tool_calls[0].function.arguments or "{}").get("prompt", "").lower()
                    except Exception:
                        pass
                    status_keywords = ["status", "what's happening", "what is happening", "update on",
                                       "how is", "how's the", "what's going on", "progress on",
                                       "where are we", "current state", "what are we working on",
                                       "focus on today", "focus today", "prioritize", "priorities",
                                       "what should i", "what should we", "recommendation",
                                       "who is", "who are", "tell me about"]
                    is_status_q = any(kw in gm_prompt for kw in status_keywords)
                    if is_status_q and calling_channel == "voice":
                        log.warning(f"GUARDRAIL: Blocked gm_command for status question on voice: {gm_prompt[:100]}")
                        retry_msgs = final_messages + [{"role": "assistant", "content": None, "tool_calls": [
                            {"id": tool_calls[0].id, "type": "function",
                             "function": {"name": "gm_command", "arguments": tool_calls[0].function.arguments}}
                        ]}, {"role": "tool", "tool_call_id": tool_calls[0].id,
                              "content": "You already have the PROJECT STATUS in your context — answer the operator's question directly and naturally from it, concise (2-3 sentences). SILENT: do NOT mention this redirect, your tools, your 'deep brain', or any reason for how you're answering — no apology, no 'let me rephrase', no 'I'm not meant to'. Just give the answer as if you always knew it."}]
                        try:
                            retry_resp = brain.complete(
                                model=LLM_MODEL, messages=retry_msgs,
                                max_tokens=1024, temperature=0.7, stream=True,
                            )
                            full = ""
                            for chunk in retry_resp:
                                if chunk.choices and chunk.choices[0].delta.content:
                                    c = chunk.choices[0].delta.content
                                    full += c
                                    yield make_sse_chunk(c)
                            log.info(f"Guardrail redirect: streamed {len(full)} chars from context")
                            _log_voice_turn(user_msg=_user_msg, assistant_msg=full[:500],
                                            tool_calls=[{"name": "gm_command", "args": "BLOCKED_BY_GUARDRAIL"}],
                                            tool_results=[], finish_reason="guardrail_redirect")
                            yield make_sse_done()
                            return
                        except Exception as e:
                            log.error(f"Guardrail retry failed: {e}")

                    # === GUARDRAIL: Force async_task for gm_command on voice ===
                    # gm_command takes 60s+. On voice, always wrap in async_task.
                    if calling_channel == "voice" and not is_status_q:
                        log.warning(f"GUARDRAIL: Wrapping gm_command in async_task for voice channel")
                        original_args = json.loads(tool_calls[0].function.arguments or "{}")
                        # VQ-6 commit-gate: if a superseding full utterance already cancelled us,
                        # ABORT before dispatching (cancel can't un-run a queued GM job); else latch
                        # tool_dispatched so we can no longer be cancelled.
                        if _vq6_entry is not None and _VQ6.commit_or_abort(_vq6_entry):
                            log.info("VQ-6: aborted partial before gm_command dispatch (superseded)")
                            yield make_sse_done()
                            return
                        async_result = execute_tool("async_task", {
                            "tool_name": "gm_command",
                            "tool_args": original_args,
                            "summary": f"GM query: {original_args.get('prompt', '')[:80]}"
                        })
                        yield make_sse_chunk("On it, I'll text you when it's done. ")
                        _log_voice_turn(user_msg=_user_msg, assistant_msg="On it, I'll text you when it's done.",
                                        tool_calls=[{"name": "async_task(gm_command)", "args": str(original_args)[:200]}],
                                        tool_results=[{"id": "guardrail", "result": async_result[:200]}],
                                        finish_reason="guardrail_async")
                        yield make_sse_done()
                        return

                # === MULTI-TURN TOOL LOOP ===
                # Jarvis can call tools iteratively (up to 5 rounds) to fully answer complex questions.
                # Each round: execute tools → feed results back → check if more tools needed → repeat.
                MAX_TOOL_ROUNDS = 5
                all_tool_calls_log = []
                all_tool_results_log = []
                loop_messages = list(final_messages)
                # Filler is TIME-GATED (gm/the operator: fillers were stacking + firing back-to-back as
                # normal speech "Got it, checking. Still on it." on FAST rounds). Rule: emit a
                # filler at the START of a round ONLY if the PREVIOUS round genuinely ran long
                # (> FILLER_GATE_S), at most ONE per gap, never on a fast round, never stacked.
                # ElevenLabs' native soft_timeout keepalive (2s, on the agent) already covers the
                # truly-silent case, so the in-band filler is now belt-and-suspenders, not primary.
                FILLER_GATE_S = 3.5
                _last_round_end = time.time()   # after the FIRST model call already returned
                _filler_pool = ["One sec. ", "Give me a moment. ", "Hang on. ", "Still on it. ",
                                "Almost there. ", "Bear with me. "]
                # VQ-3 (regression fix): fillers were compounding up to FOUR ("One sec. Almost
                # there. Pulling that up. Still on it.") and doubling on fast rounds. The time-gate
                # alone did not stop cross-round stacking. Cap at ONE filler for the WHOLE turn
                # (native soft_timeout keepalive covers longer silences), and never repeat text.
                _filler_emitted = False
                # duplicate-inject guard (gm msg_c74d3ae3): per-TURN ledger so a
                # model repeating an identical SIDE-EFFECTING call across tool
                # rounds executes it once (the operator watched 3x inject into v2's pane).
                _dedup = _voice_guards.ToolDedupLedger()

                # Leg-3 pacing heartbeat (msg_b82abb4f item 3, the operator's verbatim
                # cadence): ONE per-turn policy object — informative re-pings at
                # ~9s DURING a long tool execution, budget 3/turn, first ping
                # gated by the VQ-3b cross-turn cooldown (_filler_allowed also
                # RECORDS the emit). The proxy owns threading; policy is pure.
                _turn_heartbeat = _voice_guards.ToolHeartbeat(
                    [tc.function.name for tc in tool_calls],
                    allowed_fn=_filler_allowed)

                import random as _rnd
                for tool_round in range(MAX_TOOL_ROUNDS):
                    tool_names = [tc.function.name for tc in tool_calls]
                    # Only speak a filler if the gap was long AND we haven't already spoken one this
                    # turn — never stack, never fire on a fast round.
                    _gap = time.time() - _last_round_end
                    # VQ-3 caps within THIS turn (_filler_emitted); VQ-3b adds a cross-turn cooldown
                    # (_filler_allowed) so rapid successive turns don't each emit their own filler.
                    if _gap > FILLER_GATE_S and not _filler_emitted and _filler_allowed(time.time()):
                        log.info(f"Tool round {tool_round + 1}: {tool_names} (slow gap {_gap:.1f}s → filler)")
                        yield make_sse_chunk(_rnd.choice(_filler_pool))
                        _filler_emitted = True
                    else:
                        log.info(f"Tool round {tool_round + 1}: {tool_names} (gap {_gap:.1f}s, emitted={_filler_emitted} → no filler)")

                    # VQ-6 commit-gate: once, before the FIRST tool dispatch of the loop. If a
                    # superseding full utterance already cancelled us, abort (no side effect); else
                    # latch tool_dispatched (subsequent rounds are then already non-cancellable).
                    if _vq6_entry is not None and not _vq6_entry["tool_dispatched"]:
                        if _VQ6.commit_or_abort(_vq6_entry):
                            log.info("VQ-6: aborted partial before tool loop (superseded)")
                            yield make_sse_done()
                            return
                    # Execute each tool in this round (parallelized dispatch across independent tools)
                    tool_results = []
                    _q0_user_turns = [m.get("content") for m in messages
                                      if m.get("role") == "user"
                                      and isinstance(m.get("content"), str)][-5:]
                    workers_and_holders = []
                    for tc in tool_calls:
                        fn_name = tc.function.name
                        fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                        _prior = _dedup.check(fn_name, fn_args)
                        if _prior is not None:
                            log.warning(f"DUP-CALL SUPPRESSED: {fn_name} repeated with "
                                        f"identical args this turn — not re-executed")
                            workers_and_holders.append((tc, fn_name, fn_args, None, {"r": _prior}))
                        else:
                            _worker, _holder = _spawn_tool_worker(fn_name, fn_args,
                                                                  user_turns=_q0_user_turns)
                            workers_and_holders.append((tc, fn_name, fn_args, _worker, _holder))

                    for tc, fn_name, fn_args, _worker, _holder in workers_and_holders:
                        while _worker is not None:
                            _worker.join(timeout=0.5)
                            if not _worker.is_alive():
                                break
                            _hb_line = _turn_heartbeat.maybe_ping(time.time(), tool_name=fn_name, tool_args=fn_args)
                            if _hb_line:
                                _filler_emitted = True   # VQ-3 cap: no round-start filler after a ping
                                log.info(f"pacing-heartbeat during {fn_name}: {_hb_line!r}")
                                yield make_sse_chunk(_hb_line + " ")
                        if "e" in _holder:
                            raise _holder["e"]
                        if "r" not in _holder:      # AGY pass: abnormal worker death
                            raise RuntimeError(f"tool worker died without result: {fn_name}")
                        result = _holder["r"]
                        _dedup.record(fn_name, fn_args, result)
                        log.info(f"Tool result ({fn_name}): {result[:200]}")
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })
                        all_tool_calls_log.append({"name": fn_name, "args": (tc.function.arguments or "")[:200]})
                        all_tool_results_log.append({"id": tc.id, "result": result[:200]})

                    # Build assistant message for this round
                    assistant_msg = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": tc.id, "type": "function",
                             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                            for tc in tool_calls
                        ],
                    }

                    # Add this round to conversation
                    loop_messages = loop_messages + [assistant_msg] + tool_results

                    # Ask model: do you need more tools, or are you ready to answer?
                    followup_resp = brain.complete(
                        model=LLM_MODEL,
                        messages=loop_messages,
                        max_tokens=1024,
                        temperature=0.7,
                        tools=active_tools if active_tools else None,
                        tool_choice="auto" if active_tools else None,
                    )
                    followup_choice = followup_resp.choices[0]

                    # If model wants MORE tools, continue the loop
                    if followup_choice.message.tool_calls:
                        tool_calls = list(followup_choice.message.tool_calls)
                        log.info(f"Model wants more tools (round {tool_round + 2}): {[tc.function.name for tc in tool_calls]}")
                        _last_round_end = time.time()   # measure the NEXT round's gap from here
                        continue

                    # Model is ready to answer — stream the already-generated text directly!
                    # (Fixes duplicate generation penalty: previously discarded followup_choice and re-called model)
                    content = followup_choice.message.content or ""
                    _fgate = _voice_guards.LeadingFillerStreamGate()
                    full = ""
                    clean_content = _fgate.feed(content)
                    tail_content = _fgate.flush()
                    final_text = (clean_content or "") + (tail_content or "")
                    if _fgate.stripped:
                        log.info(f"filler-sanitizer: stripped {_fgate.stripped} model-authored leading filler(s) (tool-loop path)")

                    if final_text:
                        final_text = _voice_guards.strip_tool_code(final_text)  # never speak leaked tool_code
                    if final_text:
                        # never speak leaked reasoning either (ios msg_7293955c: a reply began
                        # 'thought\nThe user is reporting...' and was spoken verbatim)
                        final_text = _voice_guards.strip_thought_block(final_text)
                    if final_text:
                        # markerless variant (ios msg_72c19e09): backticked tool-plan sentences
                        # glued to the answer ('...`ask_gm` call.On it, I'll text...')
                        final_text = _voice_guards.strip_leading_tool_reasoning(final_text)
                    if final_text:
                        words = final_text.split(" ")
                        chunk_size = 4
                        for i in range(0, len(words), chunk_size):
                            if _vq6_entry is not None and _vq6_entry["cancel"].is_set():
                                log.info("VQ-6: tool response superseded mid-stream — aborting")
                                yield make_sse_done()
                                return
                            chunk_text = " ".join(words[i:i + chunk_size])
                            if i > 0:
                                chunk_text = " " + chunk_text
                            yield make_sse_chunk(chunk_text)
                        full = final_text

                    log.info(f"Streamed {len(full)} chars after {tool_round + 1} tool round(s) (single-pass)")
                    _log_voice_turn(
                        user_msg=_user_msg, assistant_msg=full[:500],
                        tool_calls=all_tool_calls_log, tool_results=all_tool_results_log,
                        finish_reason=f"tool_loop_{tool_round + 1}",
                    )
                    break  # Done — answered after tool loop
                else:
                    # Hit MAX_TOOL_ROUNDS — force a final answer without tools
                    log.warning(f"Hit max tool rounds ({MAX_TOOL_ROUNDS}). Forcing final answer.")
                    stream_resp = brain.complete(
                        model=LLM_MODEL, messages=loop_messages,
                        max_tokens=1024, temperature=0.7, stream=True,
                    )
                    _fgate = _voice_guards.LeadingFillerStreamGate()   # Option-1 sanitizer
                    full = ""
                    for chunk in stream_resp:
                        if chunk.choices and chunk.choices[0].delta.content:
                            _clean = _fgate.feed(chunk.choices[0].delta.content)
                            if _clean:
                                full += _clean
                                yield make_sse_chunk(_clean)
                    _tail = _fgate.flush()
                    if _tail:
                        full += _tail
                        yield make_sse_chunk(_tail)
                    if _fgate.stripped:
                        log.info(f"filler-sanitizer: stripped {_fgate.stripped} model-authored leading filler(s) (forced-final path)")
                    log.info(f"Forced final answer after {MAX_TOOL_ROUNDS} rounds: {len(full)} chars")
                    _log_voice_turn(user_msg=_user_msg, assistant_msg=full[:500],
                                    tool_calls=all_tool_calls_log, tool_results=all_tool_results_log,
                                    finish_reason=f"tool_loop_max")

            else:
                # No tool calls — stream the text response
                content = choice.message.content or ""
                # Option-1 sanitizer (no-tool path has the full content up front):
                # strip model-authored leading filler barrages before chunking.
                content, _n_fillers = _voice_guards.strip_leading_fillers(content)
                if _n_fillers:
                    log.info(f"filler-sanitizer: stripped {_n_fillers} model-authored leading filler(s) (no-tool path)")
                log.info(f"No tool call. finish_reason={choice.finish_reason}. Response: {content[:100]}")

                # VQ-9 (the operator complained TWICE): during a pause the model fills the turn with a bare
                # re-engagement keep-alive ("Is there anything specific you'd like me to help with?").
                # the operator wants SILENCE for short pauses. Double-gate: suppress only when the user turn
                # was a pause AND the response is a bare keep-alive (a real answer passes through).
                if voice_pass1 and _voice_guards.should_suppress_reengagement(_user_msg, content):
                    log.info("VQ-9: suppressing re-engagement filler on a pause turn (silence)")
                    yield make_sse_done()
                    return

                # Voice pass-2: if Jarvis admits it can't answer, escalate to GM via async
                cant_answer_phrases = [
                    "i don't have", "i don't know", "i can't find", "i need to check",
                    "i'm not sure", "i don't see", "no information", "not in my context",
                    "i'll need to", "let me ask", "i need more context",
                ]
                if voice_pass1 and content and any(p in content.lower() for p in cant_answer_phrases):
                    log.info(f"Voice pass-2: Jarvis can't answer from context — escalating to GM via async")
                    # VQ-6 commit-gate: abort before dispatching the escalation if superseded.
                    if _vq6_entry is not None and _VQ6.commit_or_abort(_vq6_entry):
                        log.info("VQ-6: aborted partial before pass-2 escalation (superseded)")
                        yield make_sse_done()
                        return
                    # Don't stream the "I don't know" — instead escalate
                    async_result = execute_tool("async_task", {
                        "tool_name": "gm_command",
                        "tool_args": {"prompt": _user_msg},
                        "summary": f"Deep dive: {_user_msg[:80]}"
                    })
                    yield make_sse_chunk("That's a deeper one — let me think it through and I'll text you the answer. ")
                    _log_voice_turn(user_msg=_user_msg, assistant_msg="Escalated to GM via async",
                                    tool_calls=[{"name": "pass2_escalation", "args": _user_msg[:200]}],
                                    tool_results=[], finish_reason="voice_pass2")
                else:
                    if content:
                        # Stream it in chunks for natural voice pacing. VQ-6: this is the DOMINANT
                        # cancellable path (a short text-only partial). If a superseding full
                        # utterance arrives mid-stream, stop yielding — B's full response proceeds.
                        words = content.split(" ")
                        chunk_size = 5
                        for i in range(0, len(words), chunk_size):
                            if _vq6_entry is not None and _vq6_entry["cancel"].is_set():
                                log.info("VQ-6: partial text response superseded mid-stream — aborting")
                                yield make_sse_done()
                                return
                            chunk_text = " ".join(words[i:i + chunk_size])
                            if i > 0:
                                chunk_text = " " + chunk_text
                            yield make_sse_chunk(chunk_text)

                    log.info(f"Streamed {len(content)} chars (no tools)")

                    # Log turn without tool calls
                    _log_voice_turn(
                        user_msg=_user_msg,
                        assistant_msg=content[:500],
                        tool_calls=[],
                        tool_results=[],
                        finish_reason=choice.finish_reason,
                    )

            log.info(f"generate() DONE in {time.time() - _gen_t0:.2f}s")
            yield make_sse_done()

        except Exception as e:
            import traceback as _tb
            log.error(f"generate() EXCEPTION after {time.time() - _gen_t0:.2f}s: {e}\n{_tb.format_exc()[:600]}")
            yield make_sse_chunk(f"I hit a snag. {str(e)[:100]}")
            yield make_sse_done()
        finally:
            # VQ-6: identity-gated registry cleanup — only free the slot if THIS generation still
            # owns it (a later superseding request may have overwritten it; never clobber it).
            if _vq6_entry is not None:
                try:
                    _VQ6.cleanup(_vq6_key, _vq6_entry)
                except Exception as _ce:
                    log.error(f"VQ-6 cleanup error: {_ce}")

    def _journaling(gen):
        # Tee the SSE stream: pass every chunk through unchanged, accumulate spoken delta
        # text, and append the arturo turn to the per-call journal when the stream ends.
        # Best-effort — journaling must NEVER alter or break what ElevenLabs receives.
        spoken = []
        try:
            for chunk in gen:
                if _journal_cid and isinstance(chunk, (bytes, bytearray, str)):
                    try:
                        s = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else chunk
                        if s.startswith("data: ") and "[DONE]" not in s:
                            c = json.loads(s[6:].strip()).get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if c:
                                spoken.append(c)
                    except Exception:
                        pass
                yield chunk
        finally:
            # VQ-6 ghost-turn suppression: if THIS generation was cancelled (its partial response
            # superseded by the full utterance), do NOT persist its assistant text or tool bucket —
            # otherwise a ghost turn corrupts the merged transcript. Cancel only ever fires while
            # tool_dispatched is False, so there is no dispatched tool to strand here.
            if _vq6_entry is not None and _vq6_entry["cancelled"]:
                log.info("VQ-6: suppressing journaling of superseded partial turn")
            elif _journal_cid:
                # Journal any tools that ran this turn BEFORE the arturo turn, so the ordering
                # (user → tools → arturo) reflects reality and finalize summaries count them.
                try:
                    for _t in (_TOOLS_THIS_TURN.get() or []):
                        _journal_append(_journal_cid, "tool", tool=_t.get("tool", "?"), args=_t.get("args", {}))
                except Exception as _te:
                    log.error(f"journal tool-turn error: {_te}")
                if spoken:
                    try:
                        # strip any leaked Gemini tool_code / default_api syntax before it reaches the
                        # journal (rendered in the operator's transcript) — tool activity belongs in role==tool
                        # turns, never in arturo message text (build vc_client_981fecb4fcbf).
                        _arturo_text = _voice_guards.strip_leading_tool_reasoning(
                            _voice_guards.strip_thought_block(
                                _voice_guards.strip_tool_code("".join(spoken))))[:2000]
                        if _arturo_text:
                            _journal_append(_journal_cid, "arturo", text=_arturo_text)
                    except Exception as _je:
                        log.error(f"journal arturo-turn error: {_je}")
                if spoken and _is_hume_clm and _conv_id:
                    # ANSWERED-REPEAT memory: this user final now has a spoken answer —
                    # an identical re-final on this cid is a vendor retry, not a re-ask.
                    try:
                        _ANSWERED_FINALS.record_answered(
                            _conv_id, _voice_guards.latest_user_text(messages))
                    except Exception as _afe:
                        log.error(f"answered-final record error: {_afe}")

    if want_stream:
        return Response(
            _journaling(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )
    else:
        # Non-streaming mode for Telegram — collect full response
        full_text = ""
        for chunk in generate():
            if chunk.startswith("data: ") and "[DONE]" not in chunk:
                try:
                    d = json.loads(chunk[6:].strip())
                    c = d.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if c:
                        full_text += c
                except Exception:
                    pass
        return jsonify({
            "id": "chatcmpl-proxy",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": full_text}, "finish_reason": "stop"}],
        })


# --- A.4 client-authoritative finalize trigger (the trigger swap) ---

@app.route("/finalize-call", methods=["POST"])
def finalize_call():
    # A.4: the gateway POSTs here the instant the operator ends a call (after it writes the client journal),
    # so the proxy finalizes FROM the client spine in SECONDS instead of waiting ~2min for the idle
    # watchdog. LOOPBACK-ONLY is the trust boundary (no check_auth — the gateway needn't propagate a
    # token). Accepts IPv4/IPv6 loopback. conv_id may be null in the body → fall back to call_id.
    remote = request.remote_addr or ""
    if remote not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        return jsonify({"ok": False, "error": "loopback only"}), 403
    data = request.json or {}
    conv_id = str(data.get("conv_id") or "")[:200]
    client_call_id = str(data.get("call_id") or "")[:80]
    if not conv_id and not client_call_id:
        return jsonify({"ok": False, "error": "conv_id or call_id required"}), 400
    # run off-thread so the gateway's best-effort POST returns immediately (it uses a ~3s cap).
    def _run():
        try:
            finalize_from_client(client_call_id=client_call_id, conv_id=conv_id)
        except Exception as _fe:
            log.error(f"/finalize-call error: {_fe}")
    _threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "queued": True})


@app.route("/ptt", methods=["POST"])
def ptt_endpoint():
    # Watch push-to-talk turn. LOOPBACK-ONLY is the trust boundary: the watch gateway (which holds the
    # Bearer watch-gateway-token) authenticates the operator and forwards the multipart here on localhost, same
    # as /finalize-call. Synchronous — the gateway waits for {reply_text, stt_text, audio} to return
    # to the watch. This does NOT touch the call lifecycle (see ptt_turn / SPEC_watch-ptt-endpoint.md).
    remote = request.remote_addr or ""
    if remote not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        return jsonify({"ok": False, "error": "loopback only"}), 403
    f = request.files.get("audio")
    if f is None:
        return jsonify({"ok": False, "error": "audio field required"}), 400
    audio_bytes = f.read()
    conversation_id = str(request.form.get("conversation_id") or "")[:200]
    turn_id = str(request.form.get("turn_id") or "")[:200]
    code, result = ptt_turn(audio_bytes, f.filename, conversation_id, turn_id,
                            content_type=(f.content_type or "application/octet-stream"))
    return jsonify(result), code


@app.route("/transcribe", methods=["POST"])
def transcribe_endpoint():
    """Item C — web composer dictation, transcribe ONLY (no brain, no TTS, no history, no journal):
    the user reads the text and taps send. LOOPBACK-ONLY like /ptt: the gateway authenticates and
    forwards. 200 {ok,text,backend,ms} | 422 no_speech | 400/413 bad clip | 503 stt_unavailable
    (reason warming | not-installed | off | error, + install command) | 504 timeout."""
    remote = request.remote_addr or ""
    if remote not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        return jsonify({"ok": False, "error": "loopback only"}), 403
    f = request.files.get("audio")
    if f is None:
        return jsonify({"ok": False, "error": "audio field required"}), 400
    audio_bytes = f.read()
    ok, err = _ptt.validate_transcribe_audio(len(audio_bytes), f.content_type or "")
    if not ok:
        return jsonify({"ok": False, "error": err}), (413 if err == "too_large" else 400)
    try:
        r = _local_stt.transcribe(audio_bytes, f.filename or "audio.webm", f.content_type or "")
    except _local_stt.SttUnavailable as e:
        st = e.state
        return jsonify({"ok": False, "error": "stt_unavailable", "reason": st.get("state"),
                        "detail": e.reason, "install": st.get("install")}), 503
    except _local_stt.WavRequired as e:
        # The default engine takes 16-bit PCM WAV (the browser encodes it); a raw container clip
        # only works on the opt-in faster-whisper engine.
        return jsonify({"ok": False, "error": "wav_required", "detail": str(e),
                        "install": _local_stt.INSTALL_CMD_BETTER}), 415
    except TimeoutError as e:
        return jsonify({"ok": False, "error": "timeout", "detail": str(e)}), 504
    except Exception as e:  # noqa: BLE001
        log.warning(f"transcribe: local STT failed: {e}")
        return jsonify({"ok": False, "error": "stt_failed"}), 502
    if not r["text"]:
        return jsonify({"ok": False, "error": "no_speech", "ms": r["ms"]}), 422
    return jsonify({"ok": True, "text": r["text"], "backend": r["backend"], "engine": r.get("engine"), "model": r["model"], "ms": r["ms"]}), 200


# --- v2/(b) stream relay routes (registered ONLY when ARTURO_STREAM_RELAY=1 — flag-off the
# proxy serves 404 on these paths, byte-identical to today) ---
if _STREAM_RELAY is not None:
    def _relay_loopback_ok():
        return (request.remote_addr or "") in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    @app.route("/ptt/stream/audio", methods=["POST"])
    def ptt_stream_audio():
        # Uplink: raw s16le/16k PCM delta in the body; conversation_id in a header (tiny,
        # avoids multipart per 100-250ms chunk). Loopback-only; the gateway holds the Bearer.
        if not _relay_loopback_ok():
            return jsonify({"ok": False, "error": "loopback only"}), 403
        cid = str(request.headers.get("X-Conversation-Id") or "")[:200]
        if not cid:
            return jsonify({"ok": False, "error": "conversation_id required"}), 400
        pcm = request.get_data(cache=False) or b""
        if not pcm:
            return jsonify({"ok": False, "error": "empty"}), 400
        if len(pcm) > 256 * 1024:
            return jsonify({"ok": False, "error": "too_large"}), 413
        # item(1): stamp the call's surface from the phone's X-Surface header (forwarded by the
        # gateway). Missing (watch) -> normalized to 'watch' inside feed_audio; set once at creation.
        surface_device = request.headers.get("X-Surface")
        r = _STREAM_RELAY.feed_audio(cid, pcm, surface_device=surface_device)
        if not r.get("ok"):
            err = r.get("error")
            # 410 Gone = the conversation ENDED; the client must hard-stop streaming (a late
            # uplink after End, e.g. build-206 posting ~43s past close) rather than retry.
            # 503 vendor_unavailable = the preferred voice vendor lacks creds/config RIGHT NOW
            # (spec §6): refuse visibly with the reason, never silently fall back to the other.
            code = {"capacity": 503, "ended": 410, "vendor_unavailable": 503}.get(err, 502)
            return jsonify(r), code
        return jsonify(r), 200

    @app.route("/ptt/stream/events", methods=["GET"])
    def ptt_stream_events():
        # Downlink: long-poll/cursor JSON (the gateway/client may also hold this as a
        # repeated fast poll; the cursor makes both shapes lossless over the same buffer).
        if not _relay_loopback_ok():
            return jsonify({"ok": False, "error": "loopback only"}), 403
        cid = str(request.headers.get("X-Conversation-Id") or request.args.get("conversation_id") or "")[:200]
        if not cid:
            return jsonify({"ok": False, "error": "conversation_id required"}), 400
        if _STREAM_RELAY.is_ended(cid):
            # spec §6 downlink half (ios msg_84512683): a client reusing an ENDED cid must
            # hard-stop, never replay the dead conversation — mirror the uplink's 410.
            return jsonify({"ok": False, "error": "ended"}), 410
        try:
            cursor = int(request.args.get("cursor", 0))
        except ValueError:
            cursor = 0
        wait_s = min(float(request.args.get("wait", 0) or 0), 25.0)
        # Page cap (ios msg_2f3e8d3c contract, downlink-amplification fix): server defaults
        # apply even without params — <=128KB payload or <=8 audio events, whichever first;
        # 'more': true tells the client to re-poll immediately with wait=0.
        try:
            max_bytes = max(1, int(request.args.get("max_bytes", 131072)))
        except ValueError:
            max_bytes = 131072
        try:
            max_audio = max(1, int(request.args.get("max_events", 8)))
        except ValueError:
            max_audio = 8
        deadline = time.time() + wait_s
        events, new_cursor, more = _STREAM_RELAY.buffer.since_page(
            cid, cursor, max_bytes=max_bytes, max_audio=max_audio)
        while not events and time.time() < deadline:
            time.sleep(0.1)
            events, new_cursor, more = _STREAM_RELAY.buffer.since_page(
                cid, cursor, max_bytes=max_bytes, max_audio=max_audio)
        body = {"ok": True, "events": events, "cursor": new_cursor}
        if more:
            body["more"] = True
        return jsonify(body), 200

    @app.route("/ptt/stream/end", methods=["POST"])
    def ptt_stream_end():
        if not _relay_loopback_ok():
            return jsonify({"ok": False, "error": "loopback only"}), 403
        cid = str(request.headers.get("X-Conversation-Id") or "")[:200]
        if not cid:
            return jsonify({"ok": False, "error": "conversation_id required"}), 400
        ended = _STREAM_RELAY.end(cid)
        return jsonify({"ok": True, "ended": ended}), 200

    # --- runtime voice-vendor preference (spec §4.1). Loopback-only; the gateway holds the Bearer.
    # Read PER CALL by the relay; a PUT takes effect on the next conversation — no restart.
    from services.arturo import voice_vendor as _voice_vendor

    @app.route("/ptt/vendor", methods=["GET"])
    def ptt_vendor_get():
        if not _relay_loopback_ok():
            return jsonify({"ok": False, "error": "loopback only"}), 403
        return jsonify({"ok": True, **_voice_vendor.state()}), 200

    @app.route("/ptt/vendor", methods=["PUT"])
    def ptt_vendor_put():
        if not _relay_loopback_ok():
            return jsonify({"ok": False, "error": "loopback only"}), 403
        body = request.get_json(silent=True) or {}
        try:
            st = _voice_vendor.set_vendor(str(body.get("vendor", "")), by=str(body.get("by", "settings"))[:40],
                                          source=str(request.headers.get("X-Vendor-Source", "settings"))[:40])
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        log.info(f"voice vendor -> {st['vendor']} by {st['changed_by']}")
        return jsonify({"ok": True, **st}), 200

    # the operator's Hume voice picker (contract msg_f5b57c9d): same runtime-pref pattern as vendor.
    from services.arturo import voice_choice as _voice_choice

    @app.route("/ptt/voices", methods=["GET"])
    def ptt_voices_get():
        if not _relay_loopback_ok():
            return jsonify({"ok": False, "error": "loopback only"}), 403
        vendor = str(request.args.get("vendor", "hume"))
        if vendor != "hume":
            return jsonify({"ok": False,
                            "error": f"voice listing is hume-only ({vendor} voices are picked client-side)"}), 400
        try:
            voices = _voice_choice.cached_hume_voices()
        except Exception as e:
            log.error(f"ptt/voices: hume voices fetch failed: {e!r}")
            return jsonify({"ok": False, "error": f"hume voices unavailable: {e}"}), 502
        return jsonify({"ok": True, "vendor": "hume",
                        "current": _voice_choice.get_voice("hume"), "voices": voices}), 200

    @app.route("/ptt/voice", methods=["GET"])
    def ptt_voice_get():
        if not _relay_loopback_ok():
            return jsonify({"ok": False, "error": "loopback only"}), 403
        return jsonify({"ok": True, **_voice_choice.state("hume")}), 200

    @app.route("/ptt/voice", methods=["PUT"])
    def ptt_voice_put():
        if not _relay_loopback_ok():
            return jsonify({"ok": False, "error": "loopback only"}), 403
        body = request.get_json(silent=True) or {}
        try:
            st = _voice_choice.set_voice(str(body.get("vendor", "")),
                                         str(body.get("voice_id", "")),
                                         by=str(body.get("by", "settings"))[:40],
                                         source=str(request.headers.get("X-Voice-Source", "settings"))[:40])
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        log.info(f"hume voice -> {st['voice_id']} by {st['changed_by']}")
        return jsonify({"ok": True, **st}), 200


# --- Text turn (track T2/T4): the web/iOS Arturo home's text path -------------------------
# LOOPBACK-ONLY, same trust boundary as /ptt: the gateway (Bearer) forwards here. One turn =
# the full tool-enabled chat_completions path (so "commission an agent to X" spawns a seat)
# with channel="text", threaded through the same bounded per-conversation history PTT uses.

_TEXT_HISTORY = _ptt.PttHistory()
_TEXT_TURN_TIMEOUT_S = float(os.environ.get("ARTURO_TEXT_TURN_TIMEOUT_S", "180"))

# G20: the DURABLE side of a conversation. _TEXT_HISTORY is in-memory, bounded and lost on
# restart -- fine as the brain's working context, useless as an archive, which is why "New
# thread" used to make the previous thread unreachable. The store is the archive the pill,
# the home and the phone all read, so they share ONE thread space.
_THREADS = _thread_store.ThreadStore(ARTURO_STATE / "threads.db")


class _BrainHttpError(Exception):
    """The brain answered non-200; text_turn turns this into a 502 with the body."""

    def __init__(self, status, body):
        super().__init__(f"brain_http_{status}")
        self.status = status
        self.body = body


def _brain_reply(messages, conversation_id):
    """One trip through the tool-enabled chat path. Returns (reply_text, tools_called).
    Extracted so the turn's THREADING can be tested without a brain."""
    token = _TOOLS_THIS_TURN.set([])
    spawn_token = _SPAWNED_THIS_TURN.set([])
    try:
        with app.test_client() as c:
            r = c.post("/v1/chat/completions", json={"messages": messages, "stream": False,
                                                     "metadata": {"channel": "text", "conversation_id": conversation_id}},
                       headers={"X-Arturo-Internal": _INTERNAL_NONCE})
            body = r.get_json(silent=True) or {}
        tools_called = [t.get("tool", "?") for t in (_TOOLS_THIS_TURN.get() or [])]
        spawned = list(_SPAWNED_THIS_TURN.get() or [])
    finally:
        _TOOLS_THIS_TURN.reset(token)
        _SPAWNED_THIS_TURN.reset(spawn_token)
    if r.status_code != 200:
        raise _BrainHttpError(r.status_code, body)
    reply = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return reply, tools_called, spawned


def text_turn(text, conversation_id):
    """Returns (http_status, {ok, reply_text, conversation_id, brain, tools_called})."""
    text = (text or "").strip()
    if not text:
        return 400, {"ok": False, "error": "empty"}
    if len(text) > 8000:
        return 413, {"ok": False, "error": "too_large"}
    conversation_id = (conversation_id or "").strip()[:200] or f"text_{int(time.time())}"
    # Onboarding turns carry a first-line marker; the step's directive lives server-side
    # (services/arturo/onboarding.py) and rides in the system context for THIS turn only.
    from services.arturo import onboarding as _onb
    step, text = _onb.split_marker(text)
    text = text.strip()
    if not text:
        return 400, {"ok": False, "error": "empty"}
    context = build_context(calling_channel="text")
    _dir = _onb.directive(step)
    if _dir:
        context = f"{context}\n\n{_dir}"
    elif step:
        log.warning(f"onboarding marker with unknown step {step!r} — no directive applied")
    history = _TEXT_HISTORY.get(conversation_id)
    if not history:
        # Reopening an OLD thread (or any thread after a restart): memory is empty but the
        # conversation is not new. Rehydrate from the archive, or Arturo answers a
        # continuing question with no idea what was already said.
        history = _THREADS.history(conversation_id)
        for turn in history:
            _TEXT_HISTORY.append(conversation_id, turn.get("role"), turn.get("content"))
    messages = _ptt.build_messages(context, history, text)
    try:
        _res = _brain_reply(messages, conversation_id)
        # Tolerant unpack: a stub (and any older caller) may still return the 2-tuple.
        reply, tools_called = _res[0], _res[1]
        spawned = list(_res[2]) if len(_res) > 2 else []
    except _BrainHttpError as e:
        return 502, {"ok": False, "error": f"brain_http_{e.status}", "detail": e.body}
    _TEXT_HISTORY.append(conversation_id, "user", text)
    _TEXT_HISTORY.append(conversation_id, "assistant", reply)
    _THREADS.record_turn(conversation_id, text, reply)
    from services.arturo import operator_store as _ops
    return 200, {"ok": True, "reply_text": reply, "conversation_id": conversation_id,
                 "brain": brain.describe(), "tools_called": tools_called,
                 "spawned": spawned,
                 "operator": _ops.public(ARTURO_STATE)}


def _loopback_only():
    return (request.remote_addr or "") in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


@app.route("/threads", methods=["GET"])
def threads_endpoint():
    """G20: the thread list, newest first. Summaries only -- the pill's switcher and the
    home's drawer render this without loading any transcript."""
    if not _loopback_only():
        return jsonify({"ok": False, "error": "loopback only"}), 403
    try:
        limit = int(request.args.get("limit") or 50)
        offset = int(request.args.get("offset") or 0)
    except ValueError:
        return jsonify({"ok": False, "error": "bad_paging"}), 400
    return jsonify({"ok": True, "threads": _THREADS.list_threads(limit=limit, offset=offset)})


@app.route("/threads/<path:conversation_id>", methods=["GET"])
def thread_detail_endpoint(conversation_id):
    """One thread with its turns, so selecting it in the list loads the real conversation."""
    if not _loopback_only():
        return jsonify({"ok": False, "error": "loopback only"}), 403
    thread = _THREADS.get_thread(conversation_id)
    if thread is None:
        # A thread that has no turns yet is the NORMAL state the first time the pill opens (the
        # browser mints the id before anything is said), not an error: 200 with thread=null, so a
        # fresh install does not log a failed request on every page. Clients already treat a null
        # thread as "nothing to resume".
        return jsonify({"ok": True, "thread": None})
    return jsonify({"ok": True, "thread": thread})


@app.route("/text", methods=["POST"])
def text_endpoint():
    remote = request.remote_addr or ""
    if remote not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        return jsonify({"ok": False, "error": "loopback only"}), 403
    data = request.get_json(silent=True) or {}
    code, result = text_turn(data.get("text"), data.get("conversation_id"))
    return jsonify(result), code


# --- Health ---

@app.route("/health", methods=["GET"])
def health():
    # G14 self-heal: a brain picked at boot as "none" (no CLI logged in yet) is re-probed
    # here, so the onboarding "Check again" tap after a login sees a live brain without a
    # service restart. Never replaces a working brain; rate-limited inside reselect_if_none.
    global brain, LLM_MODEL
    if brain.kind == "none" and BRAIN_MODE != "api":
        nb = _brain.reselect_if_none(brain, BRAIN_MODE, GEMINI_API_KEY, _REPO_ROOT,
                                     _RUNTIMES_ENABLED or None, _API_MODEL, RUNTIME_MODEL)
        if nb is not brain:
            brain = nb
            LLM_MODEL = nb.model if nb.kind == "runtime" else _API_MODEL
    return jsonify({
        "status": "ok",
        "service": "custom-llm-proxy",
        "model": LLM_MODEL,
        # T2/T3: which brain answers turns, and whether voice is even possible on this install.
        "brain": brain.describe(),
        "brain_mode": BRAIN_MODE,
        # Who the operator is, from the server-side store — every surface (home, pill, iOS)
        # reads the same name here instead of a per-browser localStorage copy.
        "operator": __import__("services.arturo.operator_store", fromlist=["public"]).public(ARTURO_STATE),
        "mode": ARTURO_MODE,                       # "voice" | "text-only"
        "voice": bool(VOICE_VENDORS_PRESENT),
        # item C: can the box transcribe a recorded clip with no vendor key? (web dictation tier 2)
        "stt": _local_stt.state(),
        "tools": [t["function"]["name"] for t in TOOLS],
        # comm-probe-safe daemon liveness (gm msg_1f417cdd): threads registered at start
        "daemons": sorted(getattr(_STREAM_RELAY, "daemons", None) or []),
        "time": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/webhook/post-call", methods=["POST"])
def post_call_webhook():
    """ElevenLabs post-call webhook — auto-saves transcript to voice memory."""
    data = request.json or {}
    conversation_id = data.get("conversation_id", "")
    log.info(f"Post-call webhook: {conversation_id}")

    if conversation_id:
        # Fetch full transcript from ElevenLabs
        el_key = secrets.get("ELEVENLABS_API_KEY", "")
        if el_key:
            import requests as req_lib
            try:
                resp = req_lib.get(
                    f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}",
                    headers={"xi-api-key": el_key},
                    timeout=15,
                )
                if resp.status_code == 200:
                    conv_data = resp.json()
                    save_transcript(conversation_id, conv_data)
                    log.info(f"Saved transcript: {conversation_id} ({len(conv_data.get('transcript', []))} turns)")
                else:
                    log.error(f"Failed to fetch transcript: {resp.status_code}")
            except Exception as e:
                log.error(f"Webhook error: {e}")

    return jsonify({"status": "ok"})


@app.route("/", methods=["GET"])
def root():
    return jsonify({"service": "OrchestraOS Custom-LLM Proxy", "endpoint": "/v1/chat/completions"})


if __name__ == "__main__":
    ARTURO_STATE.mkdir(parents=True, exist_ok=True)
    ARTURO_LOGS.mkdir(parents=True, exist_ok=True)
    VOICE_CALLS_DIR.mkdir(parents=True, exist_ok=True)
    _start_finalize_watchdog()
    _start_inject_retry_sweeper()      # DELIB-BUG-1: re-drive busy-gm injects until they land
    log.info(f"ARTURO Proxy (clone of :5052) starting on :{ARTURO_PORT} (model={LLM_MODEL}, tools={[t['function']['name'] for t in TOOLS]})")
    app.run(host="127.0.0.1", port=ARTURO_PORT, threaded=True)
