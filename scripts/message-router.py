#!/usr/bin/env python3
"""
message-router.py — Deliver SQLite-queued inter-agent messages to idle tmux agents.

Rebuilt 2026-07-10 (predecessors: .old-apr21, .disabled — never ran in prod;
the April design injected into busy agents on critical priority, which can
clobber a human mid-composition. This one never does). Design goals, in order:

  1. NEVER inject into a busy agent (generating, or human mid-composition)
  2. NEVER lose a message (SQLite is source of truth; a message is only marked
     delivered after the injected text is verified visible in the pane)
  3. NEVER clobber the operator's typed-but-unsent input. Claude's ghost suggestions
     render dim (SGR ESC[2m); real typed text renders default. We capture with
     ANSI escapes (-e) and treat the input line as clear only if all visible
     text after the prompt is dim. Verified empirically on Claude Code 2.1.87
     (fleet is version-pinned for arrow-nav). Unknown render state == busy
     (fail safe: a queued message beats a clobbered prompt).

Run from cron every minute:
  * * * * * cd ~/scripts/agent-orchestra && python3 scripts/message-router.py --cron >> logs/message-router.log 2>&1
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
sys.path.insert(0, str(ORCHESTRA_DIR))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from msg_store import MessageStore  # noqa: E402
# G1/G3 charter predicate lives in ONE importable home (lane_charter) so the
# send side (gm's G3 leg-1) and this receipt side share the SAME rule — a
# hyphenated module name is why a second copy was the only alternative.
from lane_charter import charter_gate, load_charter  # noqa: E402,F401

REGISTRY = ORCHESTRA_DIR / "registry.json"
AGENT_SESSIONS = ORCHESTRA_DIR / "state" / "agent-sessions.json"
LOCKFILE = Path("/tmp/message-router.lock")
STALE_HOURS = 48          # pending older than this -> expired at startup
COOLDOWN_SECONDS = 60     # min gap between injections into the same agent
ATTACHED_IDLE_TTL = 600   # attached pane holds mail only while active within this
                          # window; past it, deliver (fixed 2026-08-15: a mere
                          # attached client used to freeze mail FOREVER — a
                          # persistent terminal on gm/ob starved it for hours)
BREATHE_WINDOW_S = 120    # a NON-attached idle agent must be quiet this long before
                          # mail is injected. Was a hardcoded 600 (10 min) — the
                          # Stop-hook queue drain fires only on an END-OF-TURN event,
                          # so a genuinely idle agent (no new Stop) had delivery
                          # governed solely by THIS window; 10 min was the real
                          # idle-mail latency (2026-08-21, the operator). Set to 120 to match
                          # the drain hook's AGE_GATE_S so the two are coherent. Safe:
                          # COOLDOWN_SECONDS(60) + agent_is_idle + attached-hold are
                          # independent anti-clobber layers, so this alone never
                          # interrupts an active agent.
COOLDOWN_FILE = Path("/tmp/message-router-cooldowns.json")
ALERT_FILE = Path("/tmp/message-router-alerts.json")
# R2 (RCA gm-starvation 2026-08-16): hold observability — "held is never lost"
# must include "held is never silent". Every persistent-hold branch was an
# unlogged `continue`, so a 100% hold was indistinguishable from a quiet day.
# leg-4 latent defect (b): this lived in /tmp, so a reboot reset every escalation
# counter and the "escalation always terminates" guarantee silently voided. Durable
# now; load_holds() migrates in-flight counters from the legacy path exactly once
# (gm bind: the move must not lose counters — a row 3 escalations deep must not
# restart at zero and become immortal).
HOLD_FILE = Path(__file__).resolve().parent.parent / "state" / "message-router-holds.json"
HOLD_FILE_LEGACY = Path("/tmp/message-router-holds.json")
HOLD_LOG_THROTTLE_S = 600     # log each (session|reason) hold at most once / 10min
HOLD_SLA_S = 900              # a row held past this escalates (guard-a, backstop)
HOLD_ESCALATE_REPEAT_S = 1800 # re-escalate a still-held row no more than this often
QUEUE_ALERT_AFTER_S = 3600      # alert on mail queued >1h (before 48h silent expiry)
QUEUE_ALERT_EVERY_S = 6 * 3600  # re-alert cadence per stuck message
# Per-runtime prompt signatures + runtime resolution live in ONE importable home
# (runtime_signatures) so the router idle-gate and spawn's ready-wait share the
# SAME table instead of each hardcoding a prompt char (R1, all-model-parity §4.2;
# same pattern as lane_charter). Behavior is unchanged: unknown runtime => claude,
# and the Gemini idle-gate stays dormant until the operator arms it.
from runtime_signatures import (  # noqa: E402
    PROMPT_CHAR,
    PROMPT_SIGNATURES,
    DEFAULT_RUNTIME,
    agent_runtime,
    gemini_idle_routing_armed,
)
# Layer B (Bug 2, live-but-unreachable panes): 4-class classifier EXTENDING
# this idle-gate (gm ruling 3 — one place judges liveness, no parallel
# watchdog). SHADOW-ONLY in this build: would-clear jsonl, zero live actions.
import pane_reachability  # noqa: E402


# Sessions that are infrastructure, never message targets
INFRA = {"dashboard", "custom-llm", "api-server", "combo-proxy", "telegram-router"}

TG_TOKEN = None
TG_ID = None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def log(msg):
    print(f"{now_iso()} [router] {msg}", flush=True)


def load_env_telegram():
    global TG_TOKEN, TG_ID
    env = ORCHESTRA_DIR / ".env.telegram"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                TG_TOKEN = line.split("=", 1)[1].strip()
            elif line.startswith("SHAW_TELEGRAM_ID="):
                TG_ID = line.split("=", 1)[1].strip()


def telegram(text):
    if not (TG_TOKEN and TG_ID):
        return
    subprocess.run(
        ["curl", "-s", "-X", "POST",
         f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
         "-d", f"chat_id={TG_ID}", "--data-urlencode", f"text={text}"],
        capture_output=True, timeout=15,
    )


def tmux(*args, timeout=10):
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=timeout)


def live_sessions() -> set:
    r = tmux("list-sessions", "-F", "#{session_name}")
    if r.returncode != 0:
        return set()
    return {s for s in r.stdout.strip().split("\n") if s}


def session_info() -> dict:
    """Map session -> (attached_clients, seconds_since_activity)."""
    r = tmux("list-sessions", "-F", "#{session_name}|#{session_attached}|#{session_activity}")
    info = {}
    if r.returncode != 0:
        return info
    now = time.time()
    for line in r.stdout.strip().split("\n"):
        parts = line.split("|")
        if len(parts) == 3:
            try:
                info[parts[0]] = (int(parts[1]), now - int(parts[2]))
            except ValueError:
                pass
    return info


def hold_for_attached(attached: int, idle_secs: float,
                      ttl: int = ATTACHED_IDLE_TTL) -> bool:
    """True = hold delivery: a human is plausibly mid-conversation in an ATTACHED
    pane that was active within `ttl`. A persistently-attached-but-idle pane
    (idle >= ttl) is NO LONGER frozen forever — the freeze-forever behaviour
    starved gm/ob for hours (2026-08-15). Typed-composer / busy protection is
    separate (agent_is_idle), so delivering to an idle-past-ttl pane is safe."""
    return attached > 0 and idle_secs < ttl


def agent_tmux_session(agent_id: str) -> str:
    try:
        reg = json.loads(REGISTRY.read_text())
        return reg.get("agents", {}).get(agent_id, {}).get("tmux_session", agent_id)
    except (json.JSONDecodeError, OSError):
        return agent_id


# --- Lineage-aware delivery resolution ----------------------------------------
# (DEC-1786331161, spec .workspace/proposals/lineage-aware-routing-spec.md)
#
# Mail addressed to a lineage name (e.g. orchestraos-app-dev) must land in the
# CURRENT LIVE HEAD of that lineage, not the retired-but-alive predecessor pane.
# Resolution uses EXPLICIT pointers only (never name-stem heuristics — the
# DEC-1786280521 hazard): `succeeded_by` (retired member -> its successor) walked
# to the terminal member, with `lineage_root` as the no-pointer fallback.
#
# Liveness is tmux-AUTHORITATIVE (round-1 correction): state files LIE (a frozen
# scan can read `idle` 8h after a session died). `sessions` is the live tmux set
# computed in main(); the agent-state-truth state file stays advisory-only for the
# idle/busy refinement that agent_is_idle() re-derives downstream — never for
# liveness here. All non-delivery outcomes FAIL LOUD (log + reason), never a
# silent drop.


def load_agent_meta() -> dict:
    """agent-id -> session metadata (status, tmux_session, lineage_root, succeeded_by).

    Under the identity-store cutover the flat agent-sessions.json is a git-tracked
    PROJECTION that flaps on foreign-branch checkouts (~40 sessions, one shared tree),
    so a brand-new agent that IS correct in the DB can vanish from the flat file until
    the next projector pass. Resolve DB-FIRST then: union the DB-derived meta
    (source_records session docs, the flap-immune write-truth) DB-wins-per-key OVER the
    flat file, so a live agent never flaps unregistered and nothing the flat/live set
    knows is ever dropped (DEC-1788554471).

    INERT-safe: read the flat file as before, then a CHEAP INLINE flag/env probe FIRST
    (no import) — mirroring registry-update._cutover_registry_write. The DB resolver is
    imported ONLY on the armed path, so the flag-off path imports nothing new and opens
    no DB fd (byte-identical to the legacy behavior)."""
    try:
        flat = json.loads(AGENT_SESSIONS.read_text())
    except (json.JSONDecodeError, OSError):
        flat = {}
    orch = str(ORCHESTRA_DIR)
    if not (os.path.exists(os.path.join(orch, "state", "identity-store-cutover.flag"))
            or os.environ.get("IDENTITY_STORE_CUTOVER") == "1"):
        return flat
    # Armed path. Delivery-critical: ANY failure here (missing/broken resolver, an
    # unreadable DB) must DEGRADE to the flat file, never crash the router and starve
    # every agent's mail. The flat file is the same source the projector keeps fresh.
    try:
        from scripts.identity_store import resolver
        return resolver.load_meta_db_first(orch, flat)
    except Exception as e:  # noqa: BLE001 — fail-safe: flat is always a valid fallback
        sys.stderr.write(
            f"readers-db-first: DB-first meta resolution failed ({e!r}); falling back "
            f"to the flat agent-sessions.json (delivery continues)\n")
        return flat


def _known_agent(agent_id: str, meta: dict) -> bool:
    if agent_id in meta:
        return True
    try:
        reg = json.loads(REGISTRY.read_text())
        return agent_id in reg.get("agents", {})
    except (json.JSONDecodeError, OSError):
        return False


def _sess_of(agent_id: str, meta: dict) -> str:
    e = meta.get(agent_id)
    if e and e.get("tmux_session"):
        return e["tmux_session"]
    # Registry fallback mirrors the old agent_tmux_session() mapping for any agent
    # present in registry but not (yet) in agent-sessions.
    return agent_tmux_session(agent_id)


def _successor(entry: dict):
    """The explicit successor pointer, normalizing the two field names present in
    the data: `succeeded_by` (canonical) and `superseded_by` (legacy synonym —
    verified same semantic: 'my successor is X'). Explicit pointers only; NEVER a
    name-stem heuristic (the DEC-1786280521 hazard)."""
    if not entry:
        return None
    return entry.get("succeeded_by") or entry.get("superseded_by")


def _walk_succession(to_agent: str, meta: dict):
    """Walk the successor chain from to_agent to the terminal member. Cycle-guarded.
    Returns (terminal_agent, None) or (None, 'cycle-detected')."""
    seen = {to_agent}
    cur = to_agent
    while True:
        nxt = _successor(meta.get(cur) or {})
        if not nxt:
            return cur, None
        if nxt in seen:
            return None, "cycle-detected"
        seen.add(nxt)
        cur = nxt


def _lineage_root_resolve(to_agent: str, entry: dict, sessions: set, meta: dict):
    """No usable succeeded_by target: fall back to members sharing lineage_root.
    Exactly one live -> route (forwarded). Zero -> no-live-head (retryable).
    More than one -> ambiguous (fail loud). Prefer terminal (non-succeeded)
    members so a superseded-but-still-alive member never wins over the head."""
    root = entry.get("lineage_root")
    if not root:
        return (None, to_agent, "no-live-head")
    live_members = []
    for a, e in meta.items():
        if e.get("lineage_root") == root:
            s = e.get("tmux_session", a)
            if s in sessions:
                live_members.append((a, s, e))
    if not live_members:
        return (None, to_agent, "no-live-head")
    terminals = [m for m in live_members if not _successor(m[2])]
    cand = terminals if terminals else live_members
    if len(cand) == 1:
        return (cand[0][1], to_agent, "lineage-root")
    return (None, to_agent, "ambiguous-lineage")


def resolve_delivery_target(to_agent: str, sessions: set, meta: dict):
    """Resolve an address to the live-head tmux session.

    Returns (session|None, forwarded_from|None, reason). session=None means DO
    NOT deliver (keep queued / alert per reason). forwarded_from is the ORIGINAL
    addressee (provenance), None on a direct (non-forwarded) delivery.
    """
    entry = meta.get(to_agent)
    if entry is None:
        if _known_agent(to_agent, meta):
            entry = {}  # known but no lineage metadata -> treat as a bare leaf
        else:
            return (None, None, "unknown-address")

    # Superseded member (has an explicit successor): follow the chain to the head.
    if _successor(entry):
        terminal, err = _walk_succession(to_agent, meta)
        if err:
            return (None, to_agent, err)
        # A PROVISIONING successor is NOT a live head (gm-gen13 mid-gate flag,
        # msg_060955a1). Protocol v2 sets succeeded_by BEFORE the successor's
        # gate, so following the breadcrumb unconditionally steered gm-bound
        # mail at an agent forbidden to act on it — rows marked acknowledged
        # while the lane's actual owner never saw them (delivered-but-unowned,
        # the silent-starvation shape). Until promotion flips the status, the
        # PREDECESSOR still owns the lane.
        if (meta.get(terminal) or {}).get("status") == "provisioning":
            own = _sess_of(to_agent, meta)
            if own in sessions:
                return (own, None, "direct-live")
            return (None, None, "successor-ungated")  # retryable: gate in progress
        tsess = _sess_of(terminal, meta)
        if tsess in sessions:
            return (tsess, to_agent, "succeeded-by-chain")
        # Head not live on this machine -> try root fallback, else no-live-head.
        return _lineage_root_resolve(to_agent, entry, sessions, meta)

    # Leaf / terminal member: direct delivery if its own session is live.
    tsess = _sess_of(to_agent, meta)
    if tsess in sessions:
        return (tsess, None, "direct-live")
    # Not live -> lineage-root fallback (a successor may carry the same root).
    return _lineage_root_resolve(to_agent, entry, sessions, meta)


def agent_for_session(session: str, meta: dict) -> str | None:
    """Reverse map a tmux session -> its agent-id (first match). Used for the
    self-forward guard (a head must not re-forward a reply to its own retired
    address back to itself)."""
    for a, e in meta.items():
        if e.get("tmux_session", a) == session:
            return a
    return None


# --- Idle detection -----------------------------------------------------------

SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")


def input_line_state(ansi_line: str, sig: dict | None = None) -> str:
    """Classify the prompt input line: 'empty', 'ghost', or 'typed'.

    Walks the line tracking SGR dim ([2m) and reverse ([7m, the cursor block)
    state. Visible chars after the prompt char that are neither dim nor reverse
    == human typed text (never inject). Empirical signatures on Claude Code 2.1.87:
      ghost:  every word wrapped in ESC[2m spans, cursor char in ESC[7m
      typed:  default-colored text, cursor ESC[7m at end

    `sig` is the per-runtime prompt signature (defaults to the Claude signature,
    so the Claude call path is byte-identical). For a runtime WITHOUT SGR-dim
    ghost suggestions (Gemini), any non-space visible char after the prompt is
    'typed' (there is no ghost class to distinguish)."""
    sig = sig or PROMPT_SIGNATURES[DEFAULT_RUNTIME]
    prompt_char = sig["prompt_char"]
    idx = ansi_line.find(prompt_char)
    if idx == -1:
        return "typed"  # unrecognized; fail safe (treat as busy)
    rest = ansi_line[idx + len(prompt_char):]

    if not sig.get("has_ghost_suggestions", True):
        # No dim ghost-suggestion class (Gemini): strip SGR codes, and any
        # remaining non-space char after the prompt is human-typed. The cursor
        # block ([7m…[27m) wraps a space on an empty prompt, so ignore spaces.
        stripped = re.sub(r"\x1b\[[0-9;]*m", "", rest)
        return "typed" if stripped.strip() else "empty"

    dim = reverse = False
    saw_ghost = saw_typed = False
    pos = 0
    while pos < len(rest):
        m = SGR_RE.match(rest, pos)
        if m:
            codes = m.group(1).split(";") if m.group(1) else ["0"]
            for c in codes:
                if c in ("", "0"):
                    dim = reverse = False
                elif c == "2":
                    dim = True
                elif c == "7":
                    reverse = True
                elif c == "22":
                    dim = False
                elif c == "27":
                    reverse = False
            pos = m.end()
            continue
        ch = rest[pos]
        if not ch.isspace():
            if dim:
                saw_ghost = True
            elif reverse:
                pass  # cursor block
            else:
                saw_typed = True
        pos += 1

    if saw_typed:
        return "typed"
    if saw_ghost:
        return "ghost"
    return "empty"


def _detector_is_generating(session: str) -> bool:
    """Authoritative active-turn check via agent-status.py (the tiered,
    hook-anchored detector) — the idle ORACLE used to disambiguate a persistent
    /gsd todo panel (◼ rendered below the last turn-end marker post-turn) from a
    real in-progress turn. RCA gm-starvation-2026-08-16: the router's private
    pane-scrape heuristic treated any post-turn ◼ as active-turn and false-busied
    gm for 7h (starving both P0 respawn asks) while this detector said idle.

    True ONLY for working/thinking. Fail-safe: a detector error -> True (treat as
    busy/hold, never clobber on uncertainty) — R2 observability makes such a hold
    non-silent so it can't recur as a 7h black hole."""
    try:
        import importlib
        A = importlib.import_module("agent-status")
        st = A.get_agent_status(session)
        return isinstance(st, dict) and st.get("state") in ("working", "thinking")
    except Exception:
        return True   # fail toward hold; never clobber, and R2 logs it


# --- Tier-0 hook-state gate (Identity Layer v1 item d, gm spec 2026-09-15) ---
# state-event-hook.py writes state/agent-events/panes/<N>.json on every Claude
# lifecycle hook — push truth the TUI cannot mis-render. The router consults it
# BEFORE capture-pane: the capture heuristics treat any unrecognized render as
# busy, which dead-lettered gm's inbox for ~2h (03:10-05:00Z) while gm was
# demonstrably idle. Hook-less seats (gemini/codex) keep the capture path
# byte-identical.
HOOK_WORKING_TTL_S = 900          # working/waiting hook trusted this long;
                                  # older = hung tool / crashed seat -> capture


def _hook_working_ttl_s() -> float:
    """Effective TTL: env ROUTER_HOOK_WORKING_TTL_S overrides at USE time; the
    module constant is the shipped default and is never rewritten."""
    try:
        return float(os.environ.get("ROUTER_HOOK_WORKING_TTL_S",
                                    HOOK_WORKING_TTL_S))
    except (TypeError, ValueError):
        return float(HOOK_WORKING_TTL_S)


def _hook_events_dir() -> str:
    return os.environ.get("ORCH_EVENTS_DIR", str(ORCHESTRA_DIR / "state" / "agent-events" / "panes"))


def hook_state(session: str):
    """(state, ts) from the target pane's hook-event file, or None when the
    pane can't be resolved or no readable file exists (gemini/codex/no-hook
    seats -> caller falls back to capture-pane)."""
    try:
        r = tmux("display", "-p", "-t", session, "#{pane_id}")
        if r.returncode != 0:
            return None
        pane = (r.stdout or "").strip()
        if not pane.startswith("%"):
            return None
        path = os.path.join(_hook_events_dir(), pane.lstrip("%") + ".json")
        with open(path) as fh:
            d = json.load(fh)
        state, ts = d.get("state"), d.get("ts")
        if not state or not isinstance(ts, (int, float)):
            return None
        return state, float(ts)
    except Exception:  # noqa: BLE001 — unreadable/absent = no signal, never crash
        return None


def _hook_says_working_fresh(session: str) -> bool:
    """True iff the target's hook file asserts working/waiting within TTL —
    the dead-letter guard's question (a live busy seat, not a dead one)."""
    hs = hook_state(session)
    if not hs:
        return False
    state, ts = hs
    return (state in ("working", "waiting_permission")
            and (time.time() - ts) <= _hook_working_ttl_s())


def agent_is_idle(session: str, runtime: str = DEFAULT_RUNTIME) -> bool:
    """Idle = not generating, no human mid-composition, pane stable.

    `runtime` selects the per-runtime prompt signature (the operator apr_a72c10f8). It
    defaults to Claude, so every existing Claude call path is byte-identical.

    Tier-0 hook gate first (item d): a fresh hook file out-votes the capture
    heuristics in BOTH directions — hook idle needs only the typed-composer
    check (a human mid-composition is invisible to hooks); hook working/waiting
    within TTL is busy even when the pane renders a settled prompt. No file, or
    a stale working hook (hung tool), falls through to the capture path.

    Fail safe: anything unrecognized reads as busy (message stays queued).
    """
    sig = PROMPT_SIGNATURES.get(runtime, PROMPT_SIGNATURES[DEFAULT_RUNTIME])
    prompt_char = sig["prompt_char"]

    hs = hook_state(session)
    if hs is not None:
        h_state, h_ts = hs
        if h_state == "idle":
            ansi = tmux("capture-pane", "-t", session, "-e", "-p")
            if ansi.returncode != 0:
                return False
            prompt_lines = [l for l in ansi.stdout.split("\n") if prompt_char in l]
            if not prompt_lines:
                return False   # no composer visible -> can't prove it's empty
            if input_line_state(prompt_lines[-1], sig) == "typed":
                return False   # human mid-composition — never clobber
            return True
        if (h_state in ("working", "waiting_permission")
                and (time.time() - h_ts) <= _hook_working_ttl_s()):
            return False       # fresh push truth: mid-turn / blocked on dialog
        # stale working hook or other state -> capture-pane path below
    plain = tmux("capture-pane", "-t", session, "-p")
    if plain.returncode != 0:
        return False
    text = plain.stdout

    # Busy detection v4 (synced w/ dashboard/src/lib/agentActivity.ts):
    # tool lines ("⎿ Running… (4s · timeout 2m)") and timer text PERSIST in
    # scrollback after a turn ends — whole-capture matching made ready agents
    # read busy forever. Only count busy markers occurring AFTER the last
    # completed-turn marker ("✻ Worked/Brewed for Xs" — verbs rotate, match
    # shape). Spinners still self-replace, but scope them too for consistency.
    GLYPHS = "\u273b\u273d\u2722\u00b7\u2736\u2733\u273a*"
    ends = [mm.start() for mm in re.finditer(
        rf"[{GLYPHS}]\s+\w+(ed)?\s+for\s+\d+[hms]", text)]
    live = text[ends[-1]:] if ends else text

    GEMINI_BUSY_SPINNERS = ("⣾", "⣽", "⣻", "⣷", "⣯", "⣟", "⡿", "⢿")
    if ("esc to interrupt" in live
            or "esc to cancel" in live
            or "ctrl+b to run in background" in live
            or "Running…" in live
            or "Generating..." in live
            or "Generating…" in live
            or "Loading..." in live
            or "Loading…" in live
            or "Working..." in live
            or "Working…" in live
            or "Running command..." in live
            or any(s in live for s in GEMINI_BUSY_SPINNERS)):
        return False
    if re.search(rf"[{GLYPHS}]\s+\w+ing\b[^\n]{{0,80}}?(\.{{3}}|\u2026)\s*\(", live):
        return False
    if re.search(r"\w+ing\b[^\n]{0,80}?(\.{3}|\u2026)\s*\(\s*(thought for\s*)?\d+[hms]", live, re.IGNORECASE):
        return False
    # In-progress todo marker (◼): historically assumed to render only inside an
    # active turn — FALSE for a persistent /gsd todo panel, which keeps a ◼ below
    # the last turn-end marker post-turn (RCA gm-starvation-2026-08-16: 7h total
    # hold on gm, both P0 respawn asks starved). Consult the authoritative
    # detector: honor the ◼ as busy ONLY when it confirms a generating turn; a
    # bare ◼ while the detector reads idle is a stale panel, not an active turn.
    if re.search(r"^\s*\u25fc\s+\S", live, re.MULTILINE):
        if _detector_is_generating(session):
            return False
        # else: persistent panel, detector says idle -> fall through (not busy)

    # Classify the input prompt line from the ANSI capture
    ansi = tmux("capture-pane", "-t", session, "-e", "-p")
    if ansi.returncode != 0:
        return False
    # A runtime with an idle-footer signal (Gemini) must show it — a mid-turn
    # pane that lacks the footer reads busy even if momentarily stable. This is a
    # POSITIVE idle requirement, closing the "Processing…"-without-footer false-
    # idle. Claude's signature has no footer requirement (idle_footer_re=None) so
    # the Claude path is unaffected.
    footer_re = sig.get("idle_footer_re")
    if footer_re and not re.search(footer_re, ansi.stdout):
        return False  # runtime idle-footer absent => not a settled prompt
    if runtime == "gemini" and ("esc to cancel" in ansi.stdout or "esc to interrupt generation" in ansi.stdout):
        return False  # active turn footer present => generating/busy
    prompt_lines = [l for l in ansi.stdout.split("\n") if prompt_char in l]
    if not prompt_lines:
        return False  # no input prompt visible == not a ready TUI for this runtime
    if input_line_state(prompt_lines[-1], sig) == "typed":
        return False  # human mid-composition — never clobber

    # Pane must be stable (not mid-render / mid-output)
    time.sleep(2)
    plain2 = tmux("capture-pane", "-t", session, "-p")
    if plain2.returncode != 0 or plain2.stdout != text:
        return False

    return True


def reachability_probe(session: str, runtime: str, agent: str, msg_id: str):
    """Layer B (Bug 2): classify WHY a not-idle pane is holding mail — the
    live-but-unreachable classes (survey / permission prompt / stuck composer /
    AUQ-menu). SHADOW ONLY: logs a would-clear record to
    state/pane-reachability/would-clear-*.jsonl and changes NOTHING about
    delivery (the message stays pending exactly as before, so a future armed
    clear re-delivers by construction — pending mail is never dropped).
    Permission prompts route to ESCALATE, never auto-answer (fleet IRON RULE).
    Kill-switch: ~/runtime/PANE_REACHABILITY_DISABLED. A probe error must
    NEVER sink the delivery cycle — fail to None."""
    try:
        if pane_reachability.disabled():
            return None
        plain = tmux("capture-pane", "-t", session, "-p")
        if plain.returncode != 0:
            return None
        _, input_line, ok = pane_split(session, runtime)
        rec = pane_reachability.observe_hold(
            session=session, runtime=runtime, agent=agent, msg_id=msg_id,
            plain_capture=plain.stdout, input_line=input_line if ok else "")
        if rec:
            log(f"[pane-reachability-shadow] {session} :: {rec['cls']} — "
                f"WOULD {rec['action']} (msg {msg_id}, shadow held)")
        return rec
    except Exception:
        return None   # observability must never break delivery


# --- Stuck-injection self-cleanup ---------------------------------------------

def stuck_own_message(session: str, runtime: str = DEFAULT_RUNTIME) -> str | None:
    """If the input line holds a TYPED (non-dim) line that starts with our own
    '[MSG from' marker, it's a router injection whose Enter was swallowed —
    OURS to clean. Returns the stuck text, else None. Humans never type the
    marker; ghost suggestions are dim (classified 'ghost', not 'typed').

    #1b: runtime-signature-aware — finds the input line by the runtime's
    prompt_char (default claude ⇒ byte-identical)."""
    sig = PROMPT_SIGNATURES.get(runtime, PROMPT_SIGNATURES[DEFAULT_RUNTIME])
    prompt_char = sig["prompt_char"]
    ansi = tmux("capture-pane", "-t", session, "-e", "-p")
    if ansi.returncode != 0:
        return None
    prompt_lines = [l for l in ansi.stdout.split("\n") if prompt_char in l]
    if not prompt_lines:
        return None
    if input_line_state(prompt_lines[-1], sig) != "typed":
        return None
    _, input_line, ok = pane_split(session, runtime)
    if not ok:
        return None
    content = input_line.split(prompt_char, 1)[-1].strip().lstrip("\u00a0").strip()
    if content.startswith("[MSG from"):
        return content
    return None


def cleanup_stuck_injection(session: str, store,
                            runtime: str = DEFAULT_RUNTIME) -> bool:
    """Clear our own stuck [MSG] line (C-u), verify it cleared, and requeue the
    referenced message so it redelivers cleanly. Never touches human text —
    caller guarantees the line starts with our marker. Incident 2026-07-19:
    a stuck router line froze all delivery to an agent until a human (well,
    another agent, badly) intervened.

    #1b: runtime-signature-aware (default claude ⇒ byte-identical)."""
    stuck = stuck_own_message(session, runtime)
    if not stuck:
        return False
    tmux("send-keys", "-t", session, "C-u")
    time.sleep(1)
    if stuck_own_message(session, runtime):
        log(f"CLEANUP failed to clear stuck [MSG] line in {session} — leaving for next cycle")
        return False
    # Requeue: the marker line names the msg file — msg id is in the path
    m = re.search(r"/tmp/agent-msg-([A-Za-z0-9_]+)\.md", stuck)
    if m:
        msg_id = m.group(1)
        with store._conn() as conn:
            cur = conn.execute(
                "UPDATE messages SET status='pending', error='router: requeued after stuck-injection cleanup' "
                "WHERE id=? AND status IN ('delivered','processing','failed')",
                (msg_id,),
            )
            if cur.rowcount:
                log(f"CLEANUP {session}: cleared stuck line, requeued {msg_id}")
                return True
    log(f"CLEANUP {session}: cleared stuck [MSG] line (no requeuable id parsed)")
    return True


# --- Delivery -----------------------------------------------------------------

def pane_split(session: str, runtime: str = DEFAULT_RUNTIME):
    """Return (scrollback_lines, input_line, ok). input_line = last prompt line
    for the agent's RUNTIME (❯ for claude, > for gemini/agy); scrollback =
    everything above it.

    #1b (the operator-gated router arm): the delivery path split on a hardcoded ❯, so an
    agy/Gemini pane — whose prompt is '>' and whose PROSE can contain a ❯ — would
    pick the WRONG line and mis-deliver (paste/parse against a prose line, not the
    real input box). Keying the split on the runtime's prompt_char (the SAME
    PROMPT_SIGNATURES the idle-gate uses) picks the real input line. Default
    runtime=claude ⇒ byte-identical to before."""
    prompt_char = PROMPT_SIGNATURES.get(
        runtime, PROMPT_SIGNATURES[DEFAULT_RUNTIME])["prompt_char"]
    cap = tmux("capture-pane", "-t", session, "-p", "-S", "-50")
    if cap.returncode != 0:
        return [], "", False
    lines = cap.stdout.split("\n")
    last_prompt = -1
    for i, l in enumerate(lines):
        if prompt_char in l:
            last_prompt = i
    if last_prompt == -1:
        return lines, "", False
    if runtime == "gemini":
        input_chunk = "\n".join(lines[last_prompt:])
        return lines[:last_prompt], input_chunk, True
    return lines[:last_prompt], lines[last_prompt], True


def exit_copy_mode(session: str) -> None:
    """If the pane is in copy/scroll mode, exit it before pasting.

    Copy-mode makes a pane look idle-stable to agent_is_idle(), then swallows
    the pasted Enter (root cause of the 2026-08-08 swallowed-Enter incident).
    Pattern ported from kai-gm-query.sh:40-42. `q` exits copy-mode; Escape is a
    harmless no-op in normal mode, so this is safe to run unconditionally, but
    we gate on pane_in_mode to avoid disturbing a live composer.
    """
    r = tmux("display-message", "-t", session, "-p", "#{pane_in_mode}")
    if r.returncode == 0 and r.stdout.strip() == "1":
        log(f"{session} in copy-mode — exiting before inject")
        tmux("send-keys", "-t", session, "q")
        time.sleep(0.3)
        r2 = tmux("display-message", "-t", session, "-p", "#{pane_in_mode}")
        if r2.returncode == 0 and r2.stdout.strip() == "1":
            # `q` didn't take (some binding overrides) — try Escape as fallback
            tmux("send-keys", "-t", session, "Escape")
            time.sleep(0.3)


# --- E7 bug (msg_56c5cdb8): bracketed-paste chip awareness -------------------
# The Claude CLI collapses a large/multiline paste into a "[Pasted text #N]"
# (optionally "+NN lines") chip. The old literal-text grep never matched the
# chip -> false paste-fail -> RE-paste -> chip accumulation wedging the
# composer AND (composer-occupied) the boundary lane. These helpers make all
# three verification decisions chip-aware; pure so they are hermetically
# testable.
# Claude: '[Pasted text #N +NN lines]'. Codex (0.148): '[Pasted Content N chars]'
# (display compression, content intact — dossier §3). Both are composer chips.
_PASTE_CHIP_RE = re.compile(r'\[Pasted (?:text|Content)[^\]]*\]')


def count_paste_chips(line: str) -> int:
    return len(_PASTE_CHIP_RE.findall(line or ""))


def composer_has_stranded_chip(input_line: str) -> bool:
    """ANY pre-existing chip in the composer = a stranded prior paste. It is
    unattributable (our wedged retry OR a human draft), so the router must
    NEVER paste another chip onto it and NEVER press Enter on it (composer-
    draft law). Hold; note_hold/SLA escalation surfaces it."""
    return count_paste_chips(input_line) > 0


def paste_receipt_ok(input_line: str, probe: str, chips_before: int) -> bool:
    """Stage-1 receipt: the literal probe is visible OR a NEW chip appeared."""
    return (probe[:30] in input_line) or count_paste_chips(input_line) > chips_before


def submit_ok(scrollback, input_line: str, probe: str) -> bool:
    """Stage-2 submission: the input line is clear of BOTH the literal text and
    any chip, AND the content (literal or chip marker) moved into scrollback."""
    p = probe[:30]
    input_clear = p not in input_line and count_paste_chips(input_line) == 0
    in_scrollback = (any(p in l for l in scrollback)
                     or any(_PASTE_CHIP_RE.search(l) for l in scrollback))
    return input_clear and in_scrollback


def codex_pane_is_busy(pane_text: str) -> bool:
    """Codex-only busy read: '◦ Working (Ns • esc to interrupt)'. Enter into a
    WORKING codex pane QUEUES to the next tool boundary (verified, dossier §3) —
    the scrollback-moved receipt would then false-ack a message the model has
    not consumed. So the router holds instead of injecting. The queued-composer
    footer ('tab to queue message') is an equivalent busy signal."""
    txt = pane_text or ""
    return ("\u25e6 Working" in txt) or ("tab to queue message" in txt)


def inject(session: str, text: str, runtime: str = DEFAULT_RUNTIME) -> str:
    """Inject with three-stage verification. Returns:
      'submitted'  — text confirmed in scrollback, input line clear
      'stuck'      — text landed in input line but Enter never took (left as-is)
      'failed'     — text never landed at all

    Visibility alone is NOT submission: a swallowed Enter leaves the message
    sitting in the input box, which still shows up in a naive pane capture.
    We verify the text moved ABOVE the prompt and the input line cleared.

    #1b: the pane splits are runtime-signature-aware so paste-receipt + submit
    verification target the RIGHT input line on an agy/Gemini pane (default
    claude ⇒ byte-identical).
    """
    probe = text[:60]

    # Stage 0: exit copy-mode if active (else the paste/Enter gets swallowed)
    exit_copy_mode(session)

    # Stage 0.4 (codex busy-hold): never inject into a WORKING codex pane —
    # Enter queues (not submits) there and the stage-2 receipt would false-ack.
    if runtime == "codex":
        cap0 = tmux("capture-pane", "-t", session, "-p")
        if cap0.returncode == 0 and codex_pane_is_busy(cap0.stdout):
            log(f"codex pane {session} is WORKING — busy-hold, no inject (queue-not-submit semantics)")
            return "stuck"

    # Stage 0.5 (E7 repaste guard): an existing chip means a stranded prior
    # paste — never add another, never Enter on unattributed content.
    _, line0, ok0 = pane_split(session, runtime)
    if ok0 and composer_has_stranded_chip(line0):
        log(f"stranded paste-chip in {session} composer — E7 guard: no repaste, holding")
        return "stuck"

    # Stage 0.6 (De-duplication guard): if the probe text is ALREADY in the input
    # line/composer from a previous unsubmitted attempt, never paste another duplicate copy!
    if ok0 and probe[:30] in line0:
        log(f"probe already present in {session} input line — skipping repaste, retrying submit")
        for attempt in (1, 2, 3):
            tmux("send-keys", "-t", session, "Enter")
            time.sleep(2)
            scrollback, input_line, ok = pane_split(session, runtime)
            if not ok:
                time.sleep(1)
                continue
            if submit_ok(scrollback, input_line, probe):
                return "submitted"
            time.sleep(1)
        return "stuck"

    # Stage 1: receipt — paste and confirm it landed (literal text OR new chip)
    landed = False
    for attempt in (1, 2):
        _, line_before, okb = pane_split(session, runtime)
        chips_before = count_paste_chips(line_before if okb else "")
        tmux("set-buffer", "-b", "msg-router", text)
        tmux("paste-buffer", "-b", "msg-router", "-t", session, "-d")
        time.sleep(0.7)
        _, input_line, ok = pane_split(session, runtime)
        if ok and paste_receipt_ok(input_line, probe, chips_before):
            landed = True
            break
        log(f"paste attempt {attempt} not in input line of {session}")
        time.sleep(1)
    if not landed:
        return "failed"

    # Stage 2: submission — Enter until the content moves to scrollback
    for attempt in (1, 2, 3):
        tmux("send-keys", "-t", session, "Enter")
        time.sleep(2)
        scrollback, input_line, ok = pane_split(session, runtime)
        if not ok:
            time.sleep(1)
            continue
        if submit_ok(scrollback, input_line, probe):
            return "submitted"
        if probe[:30] in input_line or count_paste_chips(input_line) > 0:
            log(f"Enter swallowed in {session} (attempt {attempt}) — retrying Enter")
            time.sleep(1)
    # Text is stuck in the input box. Do NOT clear it (could race a human);
    # leave for next cycle's typed-text guard to see and hold off.
    return "stuck"


def confirm_processing(session: str, timeout: int = 20) -> bool:
    """Stage 3: agent visibly started working (generation markers or pane
    movement) within timeout seconds."""
    base = tmux("capture-pane", "-t", session, "-p")
    base_text = base.stdout if base.returncode == 0 else ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        cap = tmux("capture-pane", "-t", session, "-p")
        if cap.returncode != 0:
            continue
        if ("esc to interrupt" in cap.stdout
                or "Running\u2026" in cap.stdout
                or "ctrl+b to run in background" in cap.stdout):
            return True
        if cap.stdout != base_text:
            return True  # pane moved — agent is responding
    return False


STALE_BANNER_MINUTES = 5   # §9.6 addendum: inject-time banner past this age


def _stale_banner(minutes, superseded, n_min=STALE_BANNER_MINUTES):
    """§9.6 stale-delivery banner (the operator-agreed, mechanical, ZERO judgment — NOT a
    relevance analyzer). Returns the banner string when the row is LATE (age >
    n_min) OR SUPERSEDED (a later same-pair row exists), else None. The token
    states a fact ('sent Xm ago'); the banner COMMANDS a verify — gm saw '7m ago'
    tonight and still re-litigated a settled revert on a late report."""
    if minutes > n_min or superseded:
        return (f"[SENT {minutes}m AGO — world may have moved since; "
                f"verify current state before acting on this]")
    return None


# --- Stale-delivery digest gate (DEC-1787031444, gm commission msg_0aeb0aa8) --
# For protected+attached sessions (canonically gm): when a delivery gap opens,
# 2+ STALE rows batch into ONE index-style digest inject (oldest-first, /tmp
# pointers) instead of interrupting live work as stale singles. The §9.6 banner
# was the warning label on each single; this is the fix. Mechanical throughout
# (banner doctrine: label/batch, never judge). Critical rows are NEVER digested
# (they bypass the attach-hold today; unchanged). gm build binds (msg_39f80ee7):
# (1) hook/gate double-present check via metadata surfaced_by/digested_at
# stamps; (2) shadow-first (--would-digest logs composition, delivers nothing).

PROTECTED_SESSIONS_FILE = str(ORCHESTRA_DIR / "state" / "protected-sessions.json")
DIGEST_DEFAULTS = {"enabled": False, "age_min": 15, "max_rows": 12}
DIGEST_SHADOW = False  # set True by --would-digest at __main__ (bind 2)


def _digest_now():
    return datetime.now(timezone.utc).isoformat()


def digest_config() -> dict:
    """The gm-owned digest knob inside protected-sessions.json. Missing key or
    unreadable file => disabled (fail-closed to today's behavior)."""
    try:
        with open(PROTECTED_SESSIONS_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return dict(DIGEST_DEFAULTS)
    cfg = dict(DIGEST_DEFAULTS)
    d = data.get("digest")
    if isinstance(d, dict):
        cfg.update({k: d[k] for k in DIGEST_DEFAULTS if k in d})
    return cfg


def is_digest_session(session: str) -> bool:
    """Digest scope (spec D2): sessions in the protected[] registry. The
    attach-hold interaction is inherent — mail to an unattended session drains
    normally; the digest only matters when a hold built a stale backlog."""
    try:
        with open(PROTECTED_SESSIONS_FILE) as f:
            return session in (json.load(f).get("protected") or [])
    except (OSError, ValueError):
        return False


def _row_age_minutes(row) -> float:
    try:
        created = datetime.fromisoformat(str(row.get("created_at")))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - created).total_seconds() / 60.0
    except (ValueError, TypeError):
        return 0.0  # unparseable age reads fresh (fail-open to normal delivery)


def _hook_surfaced(row) -> bool:
    """gm bind 1: a row gm's Stop-hook digest already presented (metadata
    surfaced_by) must not be re-presented by this gate."""
    meta = row.get("metadata")
    try:
        m = json.loads(meta) if isinstance(meta, str) else (meta or {})
        return bool(isinstance(m, dict) and m.get("surfaced_by"))
    except (ValueError, TypeError):
        return False


def partition_for_digest(rows, age_min):
    """(fresh, stale) — stale = age > age_min, never critical, never
    hook-surfaced (those are excluded entirely: already presented). Both lists
    created_at asc."""
    ordered = sorted(rows, key=lambda r: str(r.get("created_at")))
    fresh, stale = [], []
    for r in ordered:
        if _hook_surfaced(r):
            continue
        if r.get("priority") == "critical" or _row_age_minutes(r) <= age_min:
            fresh.append(r)
        else:
            stale.append(r)
    return fresh, stale


def _superseded_candidate(row, store) -> bool:
    """Mechanical only (spec D4): a later same-pair row exists OR the recipient
    demonstrably acted past the thread (replied later in the conversation)."""
    if store.has_superseding_row(row.get("from_agent"), row.get("to_agent"),
                                 row.get("created_at")):
        return True
    replied = getattr(store, "recipient_replied_after", None)
    if callable(replied):
        return replied(row.get("conversation_id"), row.get("to_agent"),
                       row.get("created_at"))
    return False


def compose_digest(stale_rows, store) -> str:
    """The index-style digest inject (spec D3): oldest-first subjects with /tmp
    body pointers — the inject stays small (E7 paste-chip lesson); bodies ride
    the existing per-row file mechanism."""
    oldest = int(max(_row_age_minutes(r) for r in stale_rows))
    lines = [f"[DIGEST {len(stale_rows)} stale msgs, oldest {oldest}m — "
             f"batched at your boundary; singles held during attach]"]
    for i, r in enumerate(stale_rows, 1):
        tag = "[SUPERSEDED-CANDIDATE] " if _superseded_candidate(r, store) else ""
        lines.append(
            f"{i}. [from {r.get('from_agent')} | {r.get('priority')} | "
            f"{int(_row_age_minutes(r))}m] {tag}{(r.get('subject') or '')[:70]} "
            f"→ read /tmp/agent-msg-{r['id']}.md")
    lines.append("Reply per-item as usual; SUPERSEDED-CANDIDATE = a newer "
                 "same-thread row exists or you demonstrably acted past this "
                 "thread — verify before acting.")
    return "\n".join(lines)


def deliver_stale_digest(session, store, rows, cfg, *, inject_fn,
                         shadow=False, runtime: str = DEFAULT_RUNTIME) -> int:
    """The gate (spec D1). Returns the number of rows delivered via digest (0 =
    caller proceeds with normal single delivery for everything). Claim ALL
    stale rows pre-compose (CAS; claim-losers drop — boundary condition-A
    shape); inject once; deliver+stamp each on submit, fail ALL on inject-fail
    (backstop retry). Shadow: log composition, write NOTHING."""
    if not cfg.get("enabled"):
        return 0
    _, stale = partition_for_digest(rows, cfg.get("age_min", 15))
    if len(stale) < 2:
        return 0  # a lone stale row keeps today's banner path — 1-row digest is noise
    stale = stale[:cfg.get("max_rows", 12)]  # overflow rolls to the next cycle
    if shadow:
        log(f"[digest-shadow] {session}: WOULD digest {len(stale)} rows: "
            f"{[r['id'] for r in stale]}")
        return 0
    claimed = [r for r in stale if store.claim(r["id"])]
    if len(claimed) < 2:
        # lost the race down to 0/1 — release and let singles handle it
        for r in claimed:
            store.fail(r["id"], error="digest: batch collapsed under claim race")
        return 0
    text = compose_digest(claimed, store)
    if inject_fn(session, text, runtime) == "submitted":
        now_iso = _digest_now()
        for r in claimed:
            store.deliver(r["id"])
            stamp = getattr(store, "stamp_metadata", None)
            if callable(stamp):
                stamp(r["id"], digested_at=now_iso)
        log(f"[digest] {session}: delivered {len(claimed)} stale rows in one "
            f"inject: {[r['id'] for r in claimed]}")
        return len(claimed)
    for r in claimed:
        store.fail(r["id"], error="digest inject failed — released to singles/backstop")
    return 0


ENVELOPE_LINT_TYPES = ("task_request", "directive", "request")


def envelope_lint(msg) -> str | None:
    """§9.6-B.4 sender-envelope lint (DEC-1787032722, FLAG-ONLY v1).

    Drive-class rows should carry machine-readable `reason` + `contributes_to`
    in metadata (justification as a field, not a hope). Returns a
    comma-separated list of the MISSING keys, or None when: not drive-class,
    envelope complete, or already linted (idempotent — `envelope_linted_at`
    stamp short-circuits so ticks don't re-stamp).

    LOAD-BEARING RESTRAINT (gm APPROVE bind): this NEVER affects delivery —
    no deprioritization, no reorder, no hold. Deprioritization is a separate
    future decision gated on flag-soak evidence."""
    if msg.get("type") not in ENVELOPE_LINT_TYPES:
        return None
    md = msg.get("metadata")
    if isinstance(md, str):
        try:
            md = json.loads(md)
        except (ValueError, TypeError):
            md = {}
    if not isinstance(md, dict):
        md = {}
    if md.get("envelope_linted_at"):
        return None
    missing = [k for k in ("reason", "contributes_to") if not md.get(k)]
    return ",".join(missing) if missing else None


def format_injection(msg, store) -> str:
    """Short prefix-marked line; full body goes to a file the agent reads.

    The [MSG ...] prefix visually distinguishes router-injected text from
    anything a human typed (humans don't type the marker), and from ghost
    suggestions (which are never submitted).

    Prefix tokens (in order after [MSG from X | prio]):
      [FINAL]              — metadata.closes_thread is truthy
      [LOOP-SUSPECT depth=N] — depth >= 3 (informational only; delivery never held)
      (sent Xm ago)        — always present; minutes since created_at (UTC)
    """
    import re as _re

    # --- 1. Compute depth = max(parent_chain_len, re_prefix_count) -----------
    # parent_chain_len: walk parent_id up the chain, cap at 10, guard cycles
    parent_chain_len = 0
    seen_ids = set()
    current = msg
    while True:
        pid = current.get("parent_id")
        if not pid or pid in seen_ids or parent_chain_len >= 10:
            break
        seen_ids.add(pid)
        try:
            parent = store.get(pid)
        except Exception:
            break  # transient store error — treat as chain end
        if parent is None:
            break
        parent_chain_len += 1
        current = parent

    # re_prefix_count: count leading "re:" tokens in subject (case-insensitive)
    subject = msg.get("subject") or ""
    re_match = _re.match(r'^(\s*[Rr][Ee]:\s*)+', subject)
    if re_match:
        re_prefix_count = len(_re.findall(r'[Rr][Ee]:', re_match.group(0)))
    else:
        re_prefix_count = 0

    depth = max(parent_chain_len, re_prefix_count)

    # --- 2. Parse metadata for closes_thread ---------------------------------
    closes_thread = False
    try:
        raw_meta = msg.get("metadata")
        if raw_meta:
            meta = json.loads(raw_meta)
            closes_thread = bool(meta.get("closes_thread"))
    except Exception:
        pass  # bad JSON → treat as no closes_thread

    # --- 3. Compute staleness (minutes since created_at, UTC) ----------------
    staleness_token = "(sent ?m ago)"
    try:
        raw_ts = msg.get("created_at") or ""
        # Handle both ISO-8601 with T+tz and sqlite space-separated UTC
        raw_ts = raw_ts.strip()
        if raw_ts:
            # Normalise: replace space separator with T if no T present
            if "T" not in raw_ts:
                raw_ts = raw_ts.replace(" ", "T", 1)
            # Parse; if no tz info assume UTC
            try:
                dt = datetime.fromisoformat(raw_ts)
            except ValueError:
                dt = None
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                now_utc = datetime.now(tz=timezone.utc)
                minutes = max(0, int((now_utc - dt).total_seconds() // 60))
                staleness_token = f"(sent {minutes}m ago)"
    except Exception:
        pass  # malformed timestamp → omit staleness token

    # --- 3b. §9.6 stale-delivery banner: late OR superseded -> command a verify.
    # UPGRADES the plain '(sent Xm ago)' token (fact) to a banner (verify command).
    # deliver_raw rows never reach here (injection_text short-circuits to raw body),
    # so the operator's chat stays unwrapped — banner is [MSG]-envelope agent traffic only.
    minutes_late = None
    try:
        # reuse the minutes computed in step 3 (parsed from created_at)
        if staleness_token and staleness_token.startswith("(sent "):
            minutes_late = int(staleness_token.split()[1].rstrip("m"))
    except (ValueError, IndexError):
        minutes_late = None
    superseded = False
    try:
        has_sup = getattr(store, "has_superseding_row", None)
        if callable(has_sup):
            superseded = bool(has_sup(msg.get("from_agent"), msg.get("to_agent"),
                                      msg.get("created_at") or ""))
    except Exception:  # noqa: BLE001 — a superseding-check error never blocks delivery
        superseded = False
    banner = _stale_banner(minutes_late, superseded) if minutes_late is not None \
        else (_stale_banner(0, True) if superseded else None)

    # --- 4. Build prefix tokens list -----------------------------------------
    prefix_tokens = []
    if closes_thread:
        prefix_tokens.append("[FINAL]")
    if depth >= 3:
        prefix_tokens.append(f"[LOOP-SUSPECT depth={depth}]")
    if banner:
        prefix_tokens.append(banner)          # banner REPLACES the plain token
    elif staleness_token:
        prefix_tokens.append(staleness_token)

    prefix_str = (" " + " ".join(prefix_tokens)) if prefix_tokens else ""

    # --- 5. Write body file --------------------------------------------------
    msg_file = Path(f"/tmp/agent-msg-{msg['id']}.md")
    body = (
        f"# Message {msg['id']}\n"
        f"From: {msg['from_agent']}\nType: {msg['type']}\nPriority: {msg['priority']}\n"
        f"Sent: {msg['created_at']}\nSubject: {msg.get('subject') or '(none)'}\n"
        f"If this message is marked [FINAL]: do NOT reply unless it explicitly asks a question.\n\n"
        f"{msg.get('body') or ''}\n"
    )
    msg_file.write_text(body)

    # --- 6. Return short injection line --------------------------------------
    return (
        f"[MSG from {msg['from_agent']} | {msg['priority']}]{prefix_str} "
        f"{(msg.get('subject') or 'message')[:80]} — Read {msg_file} and act on it. "
        f"To reply: python3 ~/scripts/agent-orchestra/msg_store.py send "
        f"--from YOUR_AGENT_ID --to {msg['from_agent']} --type reply "
        f"--subject 're: {(msg.get('subject') or '')[:40]}' --body 'your reply'"
    )


def _breathe_exempt(msg) -> bool:
    """True if a row skips ONLY the 600s breathe window (recent-activity quiet-
    wait). Exempt: critical (always was), high (the operator latency push 2026-08-16 — a
    high-pri arm-package sat 8+min in breathe), and §9.6 held rows (queued FOR the
    turn boundary). This trims quiet-wait latency for urgent mail WITHOUT touching
    the other guards: the attach-hold (human mid-convo), agent_is_idle (typed-text
    clobber / idle-oracle), cooldown, and once-only delivery all still apply."""
    return msg.get("priority") in ("critical", "high") or _is_held_row(msg)


def _is_held_row(msg) -> bool:
    """True for a §9.6 gateway-HELD row (metadata.held_for_boundary) — a message
    the operator explicitly queued to a mid-turn agent for delivery AT its turn boundary.
    Such a row is EXEMPT from the 600s 'breathe' window: the breathe gate delays
    for recent-conversation cadence, but a held row's whole purpose is to land the
    moment the held-past turn ends (the agent just went idle = the boundary we
    waited for). It still respects agent_is_idle (typed-text clobber guard), the
    attach guard, cooldown, and once-only verified delivery. Fail-safe: any
    metadata parse error -> not held (keeps the conservative breathe gate)."""
    meta = msg.get("metadata")
    if not meta:
        return False
    try:
        m = json.loads(meta) if isinstance(meta, str) else meta
        return bool(isinstance(m, dict) and m.get("held_for_boundary"))
    except (ValueError, TypeError):
        return False


def injection_text(msg, store) -> str:
    """§9.6 P0 boundary delivery: a gateway-HELD direct message
    (metadata.deliver_raw — written by watch_gateway._hold_for_boundary when a
    mid-turn send was accepted-and-held) is delivered as the RAW body. It IS a
    user turn deferred to the target's turn boundary, NOT an inter-agent [MSG]
    envelope — so it must land exactly as the sender typed it, no marker, no
    'read this file' wrapper. Every other message keeps the marked envelope.
    Fail-safe: any metadata parse error falls back to the envelope."""
    meta = msg.get("metadata")
    if meta:
        try:
            m = json.loads(meta) if isinstance(meta, str) else meta
            if isinstance(m, dict) and m.get("deliver_raw"):
                return msg.get("body") or ""
        except (ValueError, TypeError):
            pass
    return format_injection(msg, store)


# --- Cooldowns ----------------------------------------------------------------

def load_cooldowns() -> dict:
    try:
        return json.loads(COOLDOWN_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_cooldowns(cd: dict):
    COOLDOWN_FILE.write_text(json.dumps(cd))


def load_holds() -> dict:
    """Durable state, with a one-time migration from the legacy /tmp path. The
    durable file WINS when both exist (it is the newer writer); the legacy file is
    read only to carry in-flight escalation counters across the move."""
    try:
        return json.loads(HOLD_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    try:
        legacy = json.loads(HOLD_FILE_LEGACY.read_text())
        log(f"hold-state migrated from {HOLD_FILE_LEGACY} "
            f"({len(legacy)} buckets, in-flight counters preserved)")
        return legacy
    except (OSError, json.JSONDecodeError, NameError):
        return {}


def save_holds(h: dict, now: float = None):
    """Persist, pruning per-message records untouched for HOLD_MSG_TTL_S so the
    per-message ledger cannot grow without bound."""
    now = time.time() if now is None else now
    for k in [k for k, r in h.items() if k.startswith(_PARK_NOTICE_PREFIX)
              and now - ((r or {}).get("notified_at") or 0) > PARK_NOTICE_EPISODE_S]:
        h.pop(k, None)
    for rec in h.values():
        msgs = rec.get("msgs") if isinstance(rec, dict) else None
        if isinstance(msgs, dict):
            for mid in [m for m, r in msgs.items()
                        if now - (r.get("seen_at") or 0) > HOLD_MSG_TTL_S]:
                msgs.pop(mid, None)
    HOLD_FILE.parent.mkdir(parents=True, exist_ok=True)
    HOLD_FILE.write_text(json.dumps(h))


def _msg_age_s(msg, now) -> float:
    """Seconds since the row was created (0 on any parse error)."""
    try:
        from datetime import datetime, timezone
        ts = msg.get("created_at") or ""
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, now - dt.timestamp())
    except (ValueError, TypeError, AttributeError):
        return 0.0


# --- #15 parked-idle breathe exemption (DEC-1787044999, starvation leg 3) ----
# An ordinary row to a PARKED-IDLE agent waits the full 600s breathe window
# even though nothing is breathing: no human (unattached), no draft (composer
# empty), no Stops for the drain hook (parked = no turns), and the beat serves
# held rows only. v3 baseline: 79 breathe holds since 08-17, row-age p90 3034s,
# max 5128s, ~72% unattached. Floor is DELIBERATELY its own constant: it must
# never be coupled to the drain hook's (numerically equal) age-gate.
PARKED_IDLE_FLOOR_S = 120
PARKED_IDLE_STATES = ("idle", "parked")
PARKED_EXEMPT_ARMED = os.environ.get("PARKED_IDLE_EXEMPT_ARMED") == "1"
PARKED_EXEMPT_SENTINEL = os.path.expanduser("~/runtime/PARKED_EXEMPT_DISABLED")


def parked_idle_exempt(*, attached, idle_secs, state, composer,
                       floor_s=PARKED_IDLE_FLOOR_S):
    """Is this target parked-idle enough to skip the breathe window?
    Returns (ok, reason). Pure over delivery-time detector values (B1).

    Three-valued composer rule (gm ruling, verbatim): ghost-only DELIVERS
    (ghosts are CLI suggestions the operator never typed; they evaporate on input);
    typed text BLOCKS (a parked pane can hold his draft — live ob case);
    INDETERMINATE BLOCKS (fail-closed on the CLASSIFICATION, not the ghost:
    when we cannot tell ghost from typed, assume typed)."""
    if attached:
        return False, "attached"
    if idle_secs < floor_s:
        return False, "below_floor"
    if state not in PARKED_IDLE_STATES:
        return False, f"state:{state}"
    if composer is None:
        return False, "composer_indeterminate"
    if (composer.get("composer_text") or "").strip():
        return False, "composer_typed"
    return True, "parked_idle"


def _composer_class(composer) -> str:
    """Three-valued composer classification, rendered for the soak scorecard:
    indeterminate | typed | ghost | empty. 'typed' wins over a simultaneous
    ghost (fail-closed toward the operator's draft)."""
    if not isinstance(composer, dict):
        return "indeterminate"
    if (composer.get("composer_text") or "").strip():
        return "typed"
    if (composer.get("composer_ghost") or "").strip():
        return "ghost"
    return "empty"


def _parked_fields(probe) -> str:
    """Predicate state for the [parked-exempt*] log lines (v3 msg_23fed0a7):
    turns the soak from 'matching proven' into 'PREDICATE proven'. A shadow
    line reporting attached!=0 / composer=typed / a non-idle state is a
    DO-NOT-ARM canary on this build. Never raises — absent fields render
    as '?' so the scorecard reports UNPROVEN instead of silently passing."""
    probe = probe if isinstance(probe, dict) else {}
    att = probe.get("attached", "?")
    state = probe.get("state", "?")
    idle = probe.get("idle_secs")
    idle_s = f"{int(idle)}s" if isinstance(idle, (int, float)) else "?"
    return (f"attached={att} composer={_composer_class(probe.get('composer'))} "
            f"state={state} idle={idle_s}")


def _real_parked_probe(session: str) -> dict:
    """Delivery-time detector probe (the _real_* seam). Any failure yields
    composer=None => INDETERMINATE => the predicate blocks. Never raises."""
    out = {"attached": 1, "idle_secs": 0.0, "state": "unknown", "composer": None}
    try:
        import importlib
        A = importlib.import_module("agent-status")
        st = A.get_agent_status(session) or {}
        out["state"] = st.get("state") or "unknown"
    except Exception:
        return out
    try:
        info = session_info().get(session)
        if info:
            out["attached"], out["idle_secs"] = int(info[0]), float(info[1])
    except Exception:
        return out
    try:
        raw = tmux("capture-pane", "-t", session, "-p", "-e").stdout or ""
        if not raw.strip():
            return out                      # unreadable/dead pane => INDETERMINATE
        raw_lines = raw.splitlines()
        stripped = [A.strip_ansi(l) for l in raw_lines]
        chrome = A._find_chrome(stripped, raw_lines)
        if chrome is not None:
            out["composer"] = {"composer_text": chrome.get("composer_text") or "",
                               "composer_ghost": chrome.get("composer_ghost") or ""}
    except Exception:
        out["composer"] = None
    return out


# Leg-4 act (2), gm msg_c74d3ae3: escalation must TERMINATE. After this many
# SLA escalations without delivery the row is dead-lettered and the sender is
# told ONCE — an escalation that repeats forever (34h, every 30min, on gm's own
# HIGH row) trains everyone to ignore escalations, which is worse than silence.
HOLD_ESCALATE_MAX = 3
HOLD_MSG_TTL_S = 7 * 24 * 3600   # prune per-message escalation records after a week
# Park notices are per TARGET EPISODE, not per router run (gm msg_e9a921fe ruling (3),
# 2026-09-16): one stuck target used to send the sender a fresh [PARKED] notice on every
# run that capped another row. An episode opens at the first notice for (sender, target)
# and closes when a row to that target DELIVERS (close_park_episode) — this is only the
# fallback so a never-delivering target cannot silence its sender forever.
PARK_NOTICE_EPISODE_S = 48 * 3600
_PARK_NOTICE_PREFIX = "parknotice|"

# gm bind (a): an ABSOLUTE age floor, INDEPENDENT of HOLD_ESCALATE_MAX — no
# future tuning of the escalation cap can ever let dead-lettering reach a
# young row (the 3 fresh rows to real agents must stay untouched by rule,
# not merely by arithmetic).
DEAD_LETTER_MIN_AGE_S = 3600

# gm bind: ONE summary per (sender, TARGET-CLASS), never per row — 31
# individual notices would be noise ABOUT noise on the very surface we are
# de-noising. Act-3 REPLACED the local map here with the shared classifier: the
# router and msg_store must never carry two dialects of "what is this target"
# (the P2-4 shared-walk lesson).
# gm's own HIGH row: must read as RE-ROUTED, never as work dropped.
DEAD_LETTER_REASON_OVERRIDE = {
    "msg_e290c9bd_26897520":
        "dead-lettered: re-commissioned to orchestra-builder per msg_c74d3ae3",
}


def _classify_target(target: str) -> dict:
    """Load the shared classifier BY PATH, not by import name: the cron context
    (`python3 scripts/message-router.py`) and the test context have different
    sys.path shapes, and an ImportError here would silently fail-close the whole
    class — the exact way leg-3's probe died while its shadow soaked green."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_addressability", str(Path(__file__).resolve().parent / "addressability.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.classify(target, log=log)


def target_class(target: str) -> str:
    """Addressability class of a delivery target, from the SHARED classifier.
    Fails open to 'agent': on the delivery side an unclassifiable target must
    behave exactly as it did before this change, never crash the sweep."""
    try:
        return _classify_target(target).get("kind") or "agent"
    except Exception as e:  # noqa: BLE001
        log(f"[addressability-classifier-error] target_class({target!r}): "
            f"{type(e).__name__}: {e} — defaulting to agent")
        return "agent"


def flush_dead_letter_notices(batch, send_fn=None) -> int:
    """ONE summary per (sender, target-class). Returns notices sent."""
    n = 0
    for (sender, cls), ids in sorted((batch or {}).items()):
        # gm msg_86bcc168 item 5: termination now PARKS (row still pending, retried on
        # the target's next idle cycle), never destroys — tonight 10 live rows to idle
        # seats were dead-lettered on a mis-read not-idle. Tell the sender the truth.
        text = (f"[PARKED] {len(ids)} message(s) you sent to {cls} target(s) hit the SLA "
                f"escalation cap without delivery and are now PARKED: still pending in "
                f"msg_store, escalation stopped, delivery retried automatically on the "
                f"target's next idle cycle (the 48h stale expiry still applies). IDs: "
                f"{', '.join(sorted(ids))}. If the target is a {cls} that can never be "
                f"idle, re-address to a live agent; otherwise nothing to do.")
        (send_fn or _default_dead_letter_notify)(sender, text)
        n += 1
    return n


def _default_dead_letter(msg_id: str, reason: str) -> bool:
    try:
        return bool(MessageStore().dead_letter(msg_id, reason))
    except Exception as e:  # noqa: BLE001 — never let disposition break the sweep
        log(f"dead-letter failed for {msg_id}: {e}")
        return False


def _default_dead_letter_notify(sender: str, text: str) -> None:
    try:
        MessageStore().send(from_agent="message-router", to_agent=sender,
                            type="message", priority="medium",
                            subject="[PARKED] message hit the SLA cap; still pending, retried on idle",
                            body=text)
    except Exception as e:  # noqa: BLE001
        log(f"park notify failed for {sender}: {e}")


def _default_park(msg_id: str, reason: str) -> bool:
    """PARK disposition (gm msg_86bcc168 item 5): the row stays status='pending' (so the
    normal delivery loop keeps retrying it whenever the target reads idle); only a
    metadata breadcrumb records that escalation was stopped and why. Best-effort."""
    try:
        MessageStore().stamp_metadata(msg_id, router_parked_at=now_iso(),
                                      router_parked_reason=reason)
        return True
    except Exception as e:  # noqa: BLE001 — never let disposition break the sweep
        log(f"park stamp failed for {msg_id}: {e}")
        return False


def _registry_db_path() -> str:
    return str(ORCHESTRA_DIR / "state" / "orchestra-registry.db")


def _target_rotated_within(session: str, now: float, window_s: float = 900) -> bool:
    """True iff the target's canonical seat had a generation PROMOTED within window_s
    (gm msg_86bcc168 item 5: never terminate mail within 15 min of a rotation of the
    target — the successor is still hydrating/reading back and its pane/hook state is
    in flux). Address canonical: a '<seat>-gN' / '-genN' alias maps to its root. Reads
    state/orchestra-registry.db generations.promoted_at (UTC ISO). Fails open False."""
    try:
        root = re.sub(r"-g(?:en)?\d+$", "", session or "")
        if not root:
            return False
        c = sqlite3.connect(f"file:{_registry_db_path()}?mode=ro", uri=True, timeout=3)
        try:
            row = c.execute("SELECT MAX(promoted_at) FROM generations WHERE root=? "
                            "AND promoted_at IS NOT NULL", [root]).fetchone()
        finally:
            c.close()
        if not row or not row[0]:
            return False
        ts = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = now - ts.timestamp()
        return 0 <= age <= window_s     # a promotion "in the future" of `now` is no signal
    except Exception:  # noqa: BLE001 — unreadable DB = no signal, never crash the sweep
        return False


def _is_internal_durable_hold(msg):
    """BG hydrate (and kin) is durable-consume-by-hook plumbing: the recipient
    reads the row from its db (green_boot_probe.collect_ingested_artifact / its
    boot hook), so 'could not INJECT the busy pane' is NOT a stranding and must
    NEVER telegram-escalate to the operator or dead-letter. Identified by the lineage
    daemon's hydrate source/type/sender (any one suffices — belt-and-suspenders).
    (gm-g44 2026-09-05: first-real-BG-arm hydrate deltas to a not-idle green were
    escalating to the operator's Telegram every SLA window — a notification leak, not a
    real strand; the green was hydrated fine off the durable db rows.)"""
    return (msg.get("source") == "bg-hydrate"
            or msg.get("type") == "lineage_hydrate"
            or msg.get("from_agent") == "lineage-daemon"
            or str(msg.get("subject", "")).startswith("ORPHAN PANE:"))


def _park_episode_open(hold_state, sender, session, now) -> bool:
    rec = hold_state.get(f"{_PARK_NOTICE_PREFIX}{sender}|{session}") or {}
    at = rec.get("notified_at")
    return at is not None and 0 <= now - at < PARK_NOTICE_EPISODE_S


def close_park_episode(hold_state, session) -> int:
    """A row to `session` DELIVERED: every sender's park episode for it is over, so the
    next cap to this target tells its sender again. Returns latches cleared."""
    keys = [k for k in hold_state
            if k.startswith(_PARK_NOTICE_PREFIX) and k.endswith(f"|{session}")]
    for k in keys:
        hold_state.pop(k, None)
    return len(keys)


def note_hold(hold_state, session, msg, reason, now, queue_n=None,
              dead_letter_fn=None, notify_fn=None, dl_batch=None, park_fn=None):
    """R2: record a held row so it is NEVER silent. (1) Throttled log: each
    (session|reason) at most once / HOLD_LOG_THROTTLE_S — a persistent hold stays
    visible without per-minute spam. (2) SLA escalation (guard-a generalized to
    the backstop): a row held past HOLD_SLA_S telegram-escalates, re-alerting no
    more than once / HOLD_ESCALATE_REPEAT_S, so a P0-class hold can't become a 7h
    black hole again. Mutates hold_state in place (caller persists)."""
    key = f"{session}|{reason}"
    rec = hold_state.get(key) or {}
    if now - rec.get("logged_at", 0) >= HOLD_LOG_THROTTLE_S:
        # #12 (msg_66d1f98d claim 1): the throttled line used to name ONLY the
        # oldest row, which read as oldest-starves-younger in gm's trace; carry
        # the recipient's queue depth so a hold is visibly whole-queue.
        depth = f", {queue_n} queued for this recipient" if queue_n else ""
        log(f"HELD {session} :: {reason} (msg {msg['id']}, "
            f"age {int(_msg_age_s(msg, now))}s{depth} — not delivered this cycle)")
        rec["logged_at"] = now
    # leg-4 latent defect (a): escalation + dead-letter state is PER MESSAGE, not
    # per (session|reason) bucket. The bucket latch meant that once one row
    # terminated, every later row to the same target under the same reason could
    # never dead-letter AND stopped escalating (n >= MAX fell to the `pass` branch)
    # — it held SILENTLY forever. That trades a noisy starvation for a quiet one,
    # which is strictly worse. Log throttling stays per bucket (that is what it is
    # for); accountability is per row.
    msgs = rec.setdefault("msgs", {})
    mrec = msgs.get(msg["id"])
    if mrec is None:
        # Migration: the FIRST row seen in a bucket inherits the legacy bucket
        # counters, so an in-flight escalation chain is not reset to zero by the
        # move to per-message state (an immortal row is the failure mode). The
        # absolute age floor still protects young rows regardless.
        inherit = not msgs and ("escalations" in rec or "escalated_at" in rec)
        mrec = {"escalations": rec.get("escalations", 0) if inherit else 0,
                "escalated_at": rec.get("escalated_at", 0) if inherit else 0}
        msgs[msg["id"]] = mrec
    mrec["seen_at"] = now
    # Internal durable plumbing (BG hydrate) is logged for observability above but
    # NEVER the operator-escalated or dead-lettered — the recipient reads it from the db,
    # so a not-idle inject-hold is expected, not a strand.
    if _msg_age_s(msg, now) >= HOLD_SLA_S and not _is_internal_durable_hold(msg):
        if mrec.get("parked") or mrec.get("dead_lettered"):
            hold_state[key] = rec
            return            # escalation already terminated for this row: quiet hold
        # gm msg_86bcc168 item 5 (c): within 15 min of a ROTATION of the target nothing
        # escalates, counts or terminates — the successor's pane/hook state is in flux.
        if _target_rotated_within(session, now):
            if now - rec.get("rotation_hold_logged_at", 0) >= HOLD_LOG_THROTTLE_S:
                log(f"HELD (recent-rotation) {msg['id']} -> {session}: target promoted "
                    f"within the rotation window — not escalating, not terminating")
                rec["rotation_hold_logged_at"] = now
            hold_state[key] = rec
            return
        if now - mrec.get("escalated_at", 0) >= HOLD_ESCALATE_REPEAT_S:
            n = mrec.get("escalations", 0)
            # gm bind (a): BOTH the cap AND the absolute age floor must be met.
            if n >= HOLD_ESCALATE_MAX and _msg_age_s(msg, now) >= DEAD_LETTER_MIN_AGE_S:
                # Item-d guard (fleet rule 2026-08-15): a target whose hook file
                # asserts FRESH working/waiting is a LIVE BUSY seat — its mail is
                # HELD (TTL effectively extended), never destroyed. The gm inbox
                # dead-lettered 2h of mail while gm was merely mid-long-turns.
                if _hook_says_working_fresh(session):
                    log(f"HELD (hook-working) {msg['id']} -> {session}: at "
                        f"dead-letter threshold but the target's hook asserts a "
                        f"live turn — holding, not terminating")
                    mrec["escalated_at"] = now
                    hold_state[key] = rec
                    return
                # TERMINAL for the ESCALATION LOOP only (gm msg_86bcc168 item 5): PARK
                # the row — status stays pending, delivery is retried on the target's
                # next idle cycle, the sender is told once. Never dead_letter: tonight
                # 10 live rows to idle seats (7 orchestra-builder, 3 gm) were destroyed
                # on a mis-read not-idle. dead_letter_fn is kept in the signature for
                # callers/tests but is NEVER invoked from here any more.
                why = DEAD_LETTER_REASON_OVERRIDE.get(
                    msg["id"],
                    f"parked after {n} SLA escalations without delivery "
                    f"(reason: {reason}, target: {session}); still pending, retried on idle")
                hs = hook_state(session)
                evidence = (f"hook={hs[0]} age {int(now - hs[1])}s" if hs
                            else "hook=none (capture-pane path)")
                ok = (park_fn or _default_park)(msg["id"], why)
                mrec["parked"] = True
                mrec["parked_at"] = now
                log(f"PARKED {msg['id']} -> {session} :: {why} "
                    f"(escalation loop terminated; idle-read evidence: {evidence}; ok={ok})")
                sender = msg.get("from_agent")
                if sender and _park_episode_open(hold_state, sender, session, now):
                    log(f"PARK-NOTICE suppressed for {msg['id']} -> {session}: {sender}'s "
                        f"park episode for this target is open (told already; closes on "
                        f"the target's next delivery)")
                elif sender:
                    hold_state[f"{_PARK_NOTICE_PREFIX}{sender}|{session}"] = {
                        "notified_at": now, "first_msg": msg["id"]}
                    if dl_batch is not None:
                        dl_batch.setdefault(
                            (sender, target_class(session)), []).append(msg["id"])
                    elif notify_fn:
                        notify_fn(sender, f"[PARKED] {msg['id']} -> {session}: {why}")
            elif n >= HOLD_ESCALATE_MAX:
                pass          # capped but too young: hold quietly, never spam
            else:
                telegram(f"[!] Router: message to {session} HELD past SLA "
                         f"({int(_msg_age_s(msg, now) // 60)}min, reason: {reason}). "
                         f"Subject: {(msg.get('subject') or msg['id'])[:60]}")
                mrec["escalations"] = n + 1
            mrec["escalated_at"] = now
    hold_state[key] = rec


# --- No-delivery handling (fail-loud, but throttled — no notification fatigue) --

# Reasons that indicate a genuinely BROKEN lineage config a human must fix →
# these telegram-alert. unknown-address (often an out-of-band / non-tmux peer such
# as a congruence reviewer, or a typo) and no-live-head (retryable: successor still
# spawning / cross-machine head) are LOG-ONLY — they were silently queued under the
# old router too, and both still hard-expire loudly at 48h. Alerting on every such
# message each cron minute is exactly the fatigue this router is built to avoid.
# successor-ungated joins the alert set (gm-gen13's residual, msg_e5eccb58): a
# rotation gate normally passes in ~15min, so mail still waiting behind one after
# the 1h grace means the rotation is STUCK — successor failed, abandoned, or the
# supervisor died mid-gate — at exactly the moment nobody is attending. A hold
# that stops escalating is silent forever, and the 48h expiry would then destroy
# the canonical lane's mail quietly.
TELEGRAM_ALERT_REASONS = {"ambiguous-lineage", "cycle-detected", "successor-ungated"}


def load_alerts() -> dict:
    try:
        return json.loads(ALERT_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_alerts(a: dict):
    ALERT_FILE.write_text(json.dumps(a))


def handle_no_delivery(msg: dict, agent: str, reason: str, alerts: dict, now: float) -> None:
    """A message that resolved to no live target. Fail loud but THROTTLED: stay
    silent per-cycle until it's been queued >1h (transient spawn/cooldown windows
    resolve before then), then surface at most once per 6h per message. Telegram
    only for genuine lineage-config breakage (ambiguous/cycle); everything else is
    log-only (still visible, still hard-expires at 48h)."""
    try:
        raw = (msg.get("created_at") or "").strip()
        if raw and "T" not in raw:
            raw = raw.replace(" ", "T", 1)
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return
    if age < QUEUE_ALERT_AFTER_S:
        return  # transient — no noise for mail queued <1h
    last = alerts.get(msg["id"], 0)
    if now - last < QUEUE_ALERT_EVERY_S:
        return  # already surfaced this message within the throttle window
    alerts[msg["id"]] = now
    telegram_worthy = reason in TELEGRAM_ALERT_REASONS
    kind = "NEEDS-HUMAN" if telegram_worthy else "log-only"
    # leg-4 act 3: 'unknown-address' was a catch-all, so a PERMANENTLY
    # unaddressable row (a daemon, a vote slot) looked identical to a successor
    # that is merely still spawning. Name the class in the line.
    try:
        acls = target_class(agent)
    except Exception:  # noqa: BLE001
        acls = "agent"
    log(f"NO-DELIVERY ({kind}) {msg['id']} -> {agent}: {reason}, "
        f"kind={acls}, queued {age/3600:.1f}h")
    if telegram_worthy:
        subj = (msg.get("subject") or msg["id"])[:60]
        telegram(
            f"⚠️ Router: mail to {agent} un-deliverable ({reason}"
            f"{', rotation appears STUCK' if reason == 'successor-ungated' else ', broken lineage config'}"
            f") queued {age/3600:.1f}h. Needs a human — {subj}"
        )


def merge_forwarded_meta(store, msg_id: str, forwarded_from: str) -> None:
    """Record forwarded_from in the message metadata (provenance persists in the
    store, not just the injected text)."""
    try:
        with store._conn() as conn:
            row = conn.execute("SELECT metadata FROM messages WHERE id=?", (msg_id,)).fetchone()
            meta = {}
            if row and row[0]:
                try:
                    meta = json.loads(row[0])
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            meta["forwarded_from"] = forwarded_from
            conn.execute("UPDATE messages SET metadata=? WHERE id=?", (json.dumps(meta), msg_id))
    except Exception as e:
        log(f"merge_forwarded_meta failed for {msg_id}: {e}")


# --- Main ---------------------------------------------------------------------

def main():
    # Lock (stale after 5 min)
    if LOCKFILE.exists():
        try:
            if time.time() - LOCKFILE.stat().st_mtime < 300:
                return
        except OSError:
            pass
    LOCKFILE.write_text(str(os.getpid()))

    try:
        load_env_telegram()
        store = MessageStore()

        with store._conn() as conn:
            # Expire stale pending (no month-old mail-bombs on enable)
            cur = conn.execute(
                "UPDATE messages SET status='expired', error='router: stale (>48h) at delivery time' "
                "WHERE status='pending' AND created_at < datetime('now', ?)",
                (f"-{STALE_HOURS} hours",),
            )
            if cur.rowcount:
                log(f"expired {cur.rowcount} stale pending messages")
            # Sweep crashed deliveries back to pending (claim() sets 'processing')
            conn.execute(
                "UPDATE messages SET status='pending' "
                "WHERE status='processing' AND attempted_at < datetime('now','-10 minutes')"
            )

        sessions = live_sessions()
        sess_info = session_info()
        cooldowns = load_cooldowns()
        alerts = load_alerts()
        hold_state = load_holds()      # R2: throttled hold-log + SLA-escalate state
        meta = load_agent_meta()
        # Recipient status for the charter bumper's bootstrap exemption
        # (provisioning newborns are being oriented, not dispatched).
        try:
            _reg_agents = (load_registry() or {}).get("agents", {})
        except Exception:
            _reg_agents = {}
        now = time.time()

        # Self-cleanup pass: clear OUR OWN stuck [MSG] injections (swallowed
        # Enter left text in the input box, which freezes all delivery to that
        # agent via the typed-text guard). Skip attached sessions — if the operator is
        # watching, he may be mid-read; the stuck line waits.
        for sess in sessions:
            if sess in INFRA:
                continue
            attached, _ = sess_info.get(sess, (0, 1e9))
            if attached > 0:
                continue
            # #1b: resolve the session's runtime so cleanup finds OUR stuck line
            # by the right prompt_char. GATED like delivery — unarmed forces
            # claude (byte-identical); only an armed gemini pane uses '>'.
            if gemini_idle_routing_armed():
                _crt = agent_runtime(agent_for_session(sess, meta) or sess, meta)
            else:
                _crt = DEFAULT_RUNTIME
            try:
                cleanup_stuck_injection(sess, store, _crt)
            except Exception as e:
                log(f"cleanup error in {sess}: {e}")

        with store._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE status='pending' "
                "ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                "WHEN 'medium' THEN 2 ELSE 3 END, created_at ASC"
            ).fetchall()
        pending = [dict(r) for r in rows]
        # #12 observability: per-recipient queue depth for hold logs
        _qdepth = {}
        for _pm in pending:
            _qdepth[_pm["to_agent"]] = _qdepth.get(_pm["to_agent"], 0) + 1

        delivered_to = set()   # keyed on RESOLVED SESSION (not to_agent) — see below
        _digest_attempted = set()  # DEC-1787031444: one digest attempt per session/cycle
        _dl_batch = {}             # leg-4 act 2: (sender, target-class) -> [ids]
        for msg in pending:
            agent = msg["to_agent"]

            # §9.6-B.4 envelope lint (DEC-1787032722): FLAG-ONLY — log + stamp,
            # zero effect on anything below (no reorder/hold/deprioritize v1).
            try:
                _missing = envelope_lint(msg)
                if _missing:
                    log(f"[envelope-lint] drive-class {msg['id']} from "
                        f"{msg['from_agent']} missing: {_missing} (flag-only)")
                    store.stamp_metadata(msg["id"], envelope_missing=_missing,
                                         envelope_linted_at=now_iso())
            except Exception as e:
                log(f"[envelope-lint] non-fatal error on {msg.get('id')}: {e}")

            # G1 CHARTER BUMPER (the operator directive 2026-08-19, envelope_lint v2):
            # off-charter drive-class mail is HELD, loudly — stamped + logged,
            # row stays pending and visible, never dropped. Opt-in per lane
            # (no charter file -> untouched). Fail-open on gate errors: a
            # broken bumper must never become a fleet-wide mail outage.
            try:
                # BOOTSTRAP EXEMPTION: a provisioning recipient is being
                # ORIENTED — its spawn orders are task_request with no lane tag,
                # so its own not-yet-known charter must not hold them (ob's
                # lived bug, msg_6c15fe27). Only EXPLICIT provisioning/spawning
                # status exempts — an established agent with an unset status is
                # NOT exempted (that would silently disable the gate).
                _rstat = (_reg_agents.get(agent) or {}).get("status")
                _prov = _rstat in ("provisioning", "spawning")
                _hold = charter_gate(msg, load_charter(agent),
                                     recipient_provisioning=_prov)
                if _hold == "already-held":
                    continue
                if _hold:
                    log(f"[charter-gate] HELD {msg['id']} from "
                        f"{msg['from_agent']} to {agent}: {_hold}")
                    store.stamp_metadata(msg["id"], charter_held_at=now_iso(),
                                         charter_hold_reason=_hold)
                    continue
            except Exception as e:
                log(f"[charter-gate] non-fatal error on {msg.get('id')}: {e} "
                    f"— delivering (fail-open)")

            # Lineage-aware resolution: address -> live-head session (or None).
            session, forwarded_from, reason = resolve_delivery_target(agent, sessions, meta)
            if session is None:
                # Not deliverable this cycle. Stays queued (never silent-dropped);
                # surfaced loud-but-throttled once >1h, telegram only for broken
                # lineage config. No per-minute spam for the routine queued state.
                handle_no_delivery(msg, agent, reason, alerts, now)
                continue
            if session in INFRA:
                continue
            if session not in sessions:
                continue  # head not running on this machine; message stays queued

            # THROTTLE: key delivered_to + cooldowns on the RESOLVED SESSION.
            # After forwarding, v1-forwarded mail and v2-native mail share the same
            # session; keying on to_agent would double-inject into the head in one
            # cycle (second lands mid-render of the first). (spec round-1 fix)
            if session in delivered_to:
                continue  # one message per session per cycle
            if now - cooldowns.get(session, 0) < COOLDOWN_SECONDS:
                continue

            # SELF-FORWARD GUARD: a reply FROM the live head addressed to its own
            # retired predecessor must not loop back into the head as self-mail.
            if forwarded_from is not None and agent_for_session(session, meta) == msg["from_agent"]:
                if store.claim(msg["id"]):
                    store.deliver(msg["id"])
                log(f"SELF-FORWARD suppressed {msg['id']}: {msg['from_agent']} -> {agent} resolves to sender's own head")
                continue

            # Empty messages are notifications, not tasks — never worth an
            # injection. Mark delivered silently (agent can query its inbox).
            if not (msg.get("subject") or "").strip() and not (msg.get("body") or "").strip():
                if store.claim(msg["id"]):
                    store.deliver(msg["id"])
                    log(f"SILENT (empty notification) {msg['id']} -> {agent}")
                continue

            # HUMAN-PRESENCE GUARD: if a tmux client is attached, the operator (or
            # someone) is watching/working in this session — an "idle" pane may
            # be an agent waiting for the human's answer mid-conversation.
            # Never interject. Also back off if the session had activity in the
            # last 10 minutes (active conversation cadence).
            attached, idle_secs = sess_info.get(session, (0, 1e9))
            if hold_for_attached(attached, idle_secs) and msg.get("priority") != "critical":
                note_hold(hold_state, session, msg, "attached-active", now, queue_n=_qdepth.get(agent), dl_batch=_dl_batch)
                continue  # attached + active within TTL: human plausibly mid-convo.
                # Past the TTL a persistent-but-idle attach no longer freezes mail
                # (2026-08-15 fix). Typed-input clobber protection is agent_is_idle.
            if idle_secs < BREATHE_WINDOW_S and not _breathe_exempt(msg):
                # #15 leg-3: a PARKED-IDLE target has nothing breathing — skip
                # the quiet-wait. SHADOW unless armed: emit [parked-exempt-
                # shadow] beside the hold so v3 can diff shadow lines against
                # HELD::breathe-window notes before gm arms it. Kill switch:
                # ~/runtime/PARKED_EXEMPT_DISABLED (instant, no deploy).
                _pk_ok, _pk_why, _pk_probe = False, "probe_skipped", {}
                try:
                    _pk_probe = _real_parked_probe(session)
                    _pk_ok, _pk_why = parked_idle_exempt(**_pk_probe)
                except Exception as e:
                    _pk_ok, _pk_why = False, f"probe_error:{type(e).__name__}"
                _pk_armed = PARKED_EXEMPT_ARMED and not os.path.exists(PARKED_EXEMPT_SENTINEL)
                if _pk_ok and _pk_armed:
                    log(f"[parked-exempt] {session} :: delivering {msg['id']} "
                        f"({_parked_fields(_pk_probe)}) — breathe-window skipped")
                    store.stamp_metadata(msg["id"], breathe_exempt_parked_at=now_iso())
                    # fall through to the remaining guards (agent_is_idle,
                    # cooldown, once-only CAS) — nothing else is bypassed
                else:
                    if _pk_ok:
                        log(f"[parked-exempt-shadow] {session} :: WOULD deliver "
                            f"{msg['id']} ({_parked_fields(_pk_probe)}) — shadow, held")
                    note_hold(hold_state, session, msg, "breathe-window", now, queue_n=_qdepth.get(agent), dl_batch=_dl_batch)
                    continue  # recent activity — let the conversation breathe.
                # EXEMPT critical + high + §9.6 held rows: urgent/boundary mail
                # shouldn't eat the 10-min quiet-wait (the operator latency push; a held
                # row was queued FOR this turn boundary). Still idle-gated
                # (agent_is_idle below) + attach-guarded + cooldown'd + once-only.

            # Per-runtime idle-gate (the operator apr_a72c10f8): resolve the TARGET
            # agent's runtime so an agy/Gemini pane ('>') is recognized as idle,
            # not just Claude's ❯. Resolve from the resolved session's agent when
            # a forward happened, else the addressed agent; default claude.
            # GATED: dormant until the operator arms (gemini_idle_routing_armed); UNARMED
            # forces 'claude' => byte-identical to pre-change behavior (shadow).
            if gemini_idle_routing_armed():
                _target_agent = agent_for_session(session, meta) or agent
                _runtime = agent_runtime(_target_agent, meta)
            else:
                _runtime = DEFAULT_RUNTIME
            if not agent_is_idle(session, _runtime):
                # Layer B (Bug 2) SHADOW: classify live-but-unreachable holds
                # (survey/permission/stuck-composer/menu) into the would-clear
                # log. Never raises, never alters the hold. Per-episode
                # de-dup (gm msg_d4237aac): one would-clear per episode.
                reachability_probe(session, _runtime, agent, msg["id"])
                note_hold(hold_state, session, msg, "not-idle", now, queue_n=_qdepth.get(agent), dl_batch=_dl_batch)
                continue  # stays queued; retried next cycle (RCA: this was the
                # 7h-silent branch — now logged + SLA-escalated via note_hold)

            # Idle-gate PASSED => the pane VISIBLY recovered — end any active
            # pane-reachability episode so a future re-stuck re-arms exactly
            # ONE would-clear (gm state-transition ruling msg_d4237aac).
            # Cheap no-op when no episode file exists; never sinks delivery.
            try:
                pane_reachability.note_recovered(session)
            except Exception:
                pass

            # DEC-1787031444 stale-digest gate: for a protected session whose
            # gap just opened, batch the stale backlog into ONE digest inject
            # before any single delivery. Runs at most once per session per
            # cycle; digested rows fail store.claim below when the loop reaches
            # them (delivered => not pending). Fresh rows + single-stale fall
            # through to the normal path unchanged.
            if session not in _digest_attempted and is_digest_session(session):
                _digest_attempted.add(session)
                try:
                    _dcfg = digest_config()
                    same_agent_rows = [m2 for m2 in pending
                                       if m2["to_agent"] == agent
                                       and m2["status"] == "pending"]
                    if deliver_stale_digest(
                            session, store, same_agent_rows, _dcfg,
                            inject_fn=inject,
                            shadow=DIGEST_SHADOW, runtime=_runtime) > 0:
                        cooldowns[session] = now
                        delivered_to.add(session)
                except Exception as e:  # noqa: BLE001 — digest never sinks the cycle
                    log(f"[digest] error for {session}: {e} — singles path continues")

            if not store.claim(msg["id"]):
                continue  # raced with another router instance

            text = injection_text(msg, store)
            if forwarded_from is not None:
                # Provenance: stamp the ORIGINAL addressee (not immediate predecessor)
                text = f"[fwd from {forwarded_from}] " + text
                merge_forwarded_meta(store, msg["id"], forwarded_from)
                log(f"FORWARD {msg['id']}: {agent} -> {session} ({reason}, from={forwarded_from})")
            result = inject(session, text, _runtime)
            if result in ("submitted", "stuck"):
                close_park_episode(hold_state, session)   # target took mail: episode over
            if result == "submitted":
                store.deliver(msg["id"])
                cooldowns[session] = now
                delivered_to.add(session)
                # Stage 3: confirm the agent actually started processing
                if confirm_processing(session):
                    store.acknowledge(msg["id"])
                    log(f"ACKNOWLEDGED {msg['id']} -> {agent} (submitted + processing)")
                else:
                    log(f"DELIVERED {msg['id']} -> {agent} (submitted, no processing signal in 20s)")
            elif result == "stuck":
                # Text sits in the agent's input box; Enter wouldn't take.
                # Mark delivered (a re-pend would double-paste after the operator's
                # manual Enter submits it) and alert for the one manual step.
                store.deliver(msg["id"])
                with store._conn() as conn:
                    conn.execute(
                        "UPDATE messages SET error='router: Enter swallowed — text left in input line, alerted the operator' WHERE id=?",
                        (msg["id"],),
                    )
                cooldowns[session] = now
                log(f"STUCK {msg['id']} -> {agent}: Enter swallowed, text left in input line")
                telegram(
                    f"⚠️ Router: message to {agent} is sitting UNSUBMITTED in its input "
                    f"line (Enter swallowed). Press Enter in that session or clear it. "
                    f"Msg: {(msg.get('subject') or msg['id'])[:60]}"
                )
            else:
                store.fail(msg["id"], error="router: paste never landed after 2 attempts")
                with store._conn() as conn:
                    st = conn.execute(
                        "SELECT status, retry_count FROM messages WHERE id=?", (msg["id"],)
                    ).fetchone()
                log(f"FAILED inject {msg['id']} -> {agent} (status now {st[0]}, retries {st[1]})")
                if st and st[0] == "failed":
                    telegram(
                        f"📪 Message {msg['id']} to {agent} DEAD-LETTERED after "
                        f"{st[1]} failed deliveries. Subject: {msg.get('subject')}"
                    )

        # leg-4 act 2: ONE summary per (sender, target-class) at end of cycle —
        # never one notice per dead-lettered row (gm bind).
        if _dl_batch:
            n = flush_dead_letter_notices(_dl_batch)
            log(f"dead-letter: {sum(len(v) for v in _dl_batch.values())} row(s) "
                f"terminated, {n} summary notice(s) sent")

        save_cooldowns(cooldowns)
        save_alerts(alerts)
        save_holds(hold_state)
    finally:
        LOCKFILE.unlink(missing_ok=True)


if __name__ == "__main__":
    # DEC-1787031444 bind 2: --would-digest = shadow (log would-digest
    # composition, deliver nothing via the digest path; singles unchanged).
    DIGEST_SHADOW = "--would-digest" in sys.argv[1:]
    main()
