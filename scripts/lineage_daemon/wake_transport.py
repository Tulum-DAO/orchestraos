"""gm wake-on-delivery TRANSPORT — ob's lane (DEC-1786828407 CONSENSUS_REACHED).

Consumes PB's frozen pure contract (scripts/focus_registry/wake.py:
should_wake / build_wake_digest / confirm_turn_started — NEVER edited here) and
supplies the transport around it: cooldown persistence, gm-state + composer
gathering, and the inject step. Rides the WS-A bus beat (bus_beat.py) per the
blessed proposal (.workspace/proposals/gm-wake-on-delivery.md §1) — no 5th cron.

Modes (the arming ladder — gm binding Q4, DRY-RUN FIRST IS MANDATORY):
  dry   — inject the digest into gm's composer via the gateway-equivalent
          no-submit path, verify-visible, NO Enter, log "would submit". This is
          the trust window gm runs before any auto-submit.
  armed — full verified inject-AND-SUBMIT + confirm_turn_started, retry ONCE,
          then wake_failed durable artifact (never loops). Arming is a SEPARATE
          gm+the operator step after the dry window proves the guard clean.

Cooldown contract (PB seam): a REAL dict {last_wake_ts, woken_ids} is passed to
should_wake. load_cooldown returns the cold-start dict for a MISSING file but
None for a CORRUPT one — a corrupt file could mask a just-fired wake, and
should_wake fail-closes on any non-dict (bad_cooldown_state): the failure bias
is always the missed wake, never the wake-storm.

Fail-open everywhere: any transport error -> no wake this beat, never a raise,
never a wedged bus beat. A missed wake degrades to the old behavior (gm acts on
the next the operator turn).
"""
import importlib.util
import json
import os
import time

from scripts.focus_registry import wake

WOKEN_IDS_CAP = 500
COLD_START = {"last_wake_ts": None, "woken_ids": []}

# Sync-immune e-brake (standing rule): ONLY the NON-SYNCED runtime sentinel
# (~/runtime/WAKE_DISABLED) brakes — a synced in-tree sentinel can be spuriously
# toggled either direction by a stale Mac Syncthing copy.
WAKE_DISABLED_BASENAME = "WAKE_DISABLED"

_ORCH = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_agent_status():
    spec = importlib.util.spec_from_file_location(
        "agent_status_wt", os.path.join(_ORCH, "scripts", "agent-status.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- cooldown persistence (atomic; beat-level flock lives in the entrypoint) ---

def load_cooldown(path):
    """Missing file -> the cold-start dict (a true cold start is safe to wake).
    CORRUPT/non-dict content -> None, so should_wake fail-closes with
    bad_cooldown_state (an unreadable cooldown could mask a just-fired wake)."""
    if not os.path.exists(path):
        return {"last_wake_ts": None, "woken_ids": []}
    try:
        with open(path) as fh:
            d = json.load(fh)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(d, dict):
        return None
    d.setdefault("last_wake_ts", None)
    d.setdefault("woken_ids", [])
    return d


def save_cooldown(path, state):
    """Atomic write-temp+rename; woken_ids bounded to the newest WOKEN_IDS_CAP."""
    out = dict(state)
    out["woken_ids"] = list(out.get("woken_ids", []))[-WOKEN_IDS_CAP:]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(out, fh)
    os.replace(tmp, path)


# --- gm-state gathering (pure parts) ------------------------------------------

def last_gm_event_type(events, *, resolve_fn):
    """The type of the LAST raw stream event that resolves to canonical gm —
    the bus half of the two-signal gm-idle check (binding Q5). None when gm has
    no event in the window (-> should_wake declines: gm_not_idle)."""
    last = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        try:
            agent = resolve_fn(ev.get("pane"), ev.get("session_id"), ev.get("cwd"))
        except Exception:  # noqa: BLE001
            continue
        if agent == "gm":
            last = ev.get("type")
    return last


def build_gm_state(agent_status, *, last_event_type, jsonl_turns=None):
    """Shape the should_wake/confirm_turn_started gm_state dict from an
    agent-status result + the bus's last gm event type."""
    st = {"status": (agent_status or {}).get("state"),
          "last_event_type": last_event_type}
    if jsonl_turns is not None:
        st["jsonl_turns"] = jsonl_turns
    return st


def composer_lines(pane_ansi):
    """Slice gm's raw ANSI pane capture down to the composer rows only
    (❯ line + wrapped continuations, chrome-anchored via agent-status
    _find_chrome). Returns None when no readable composer chrome exists —
    PB's _composer_verdict then fail-CLOSED aborts the wake. Never includes
    the status bar (its default-style text would false-'typed' every wake)."""
    if not pane_ansi:
        return None
    ast = _load_agent_status()
    raw_lines = pane_ansi.splitlines()
    stripped = [ast.strip_ansi(l) for l in raw_lines]
    chrome = ast._find_chrome(stripped, raw_lines)
    if not chrome:
        return None
    return raw_lines[chrome["composer_idx"]:chrome["box_bottom"]]


# --- the wake step (pure over injected seams) ---------------------------------

def _result(decision, mode, cooldown):
    return {"wake": decision["wake"], "reason": decision.get("reason"),
            "mode": mode, "injected": False, "submitted": False,
            "would_submit": False, "confirmed": None, "wake_failed": False,
            "cooldown": cooldown}


def wake_step(inbox, gm_state, composer, cooldown_state, *, now, mode,
              inject_fn, gm_state_fn=None, artifact_fn=None,
              confirm_wait_s=0.0):
    """One wake evaluation + (maybe) inject, riding a bus beat.

    inject_fn(text, submit) -> (ok, info): the gateway seam. submit=False is
    the DRY path (verify-visible, NO Enter); submit=True the armed verified
    inject-AND-Enter. gm_state_fn() -> a fresh gm_state (the confirm poll) and
    artifact_fn(record) (the wake_failed durable artifact) are REQUIRED in
    armed mode — missing seams refuse to inject (fail-open, no wake).

    Cooldown marking: batch ids are marked woken ONLY after a successful
    inject (a refused/failed inject leaves them unwoken for the next beat);
    in armed mode a confirm failure still marks them — one wake_failed
    artifact, never a re-inject spin on the same batch.
    """
    try:
        if mode == "armed" and not (callable(gm_state_fn) and callable(artifact_fn)):
            d = {"wake": False, "reason": "armed_missing_seams"}
            return _result(d, mode, cooldown_state)

        decision = wake.should_wake(inbox, gm_state, composer, cooldown_state, now=now)
        res = _result(decision, mode, cooldown_state)
        if not decision["wake"]:
            return res

        batch = decision["batch"]
        nonce = format(int((now or time.time()) * 1000) & 0xFFFFFFFF, "x")
        digest = wake.build_wake_digest(batch, total_pending=decision["total_pending"],
                                        nonce=nonce)
        res["nonce"] = nonce

        try:
            ok, info = inject_fn(digest, mode == "armed")
        except Exception as e:  # noqa: BLE001 -- gateway down != wedged beat
            ok, info = False, {"reason": f"inject_error:{type(e).__name__}"}
        res["injected"] = bool(ok)
        res["inject_info"] = info
        if not ok:
            return res      # unwoken; next beat retries

        marked = dict(cooldown_state)
        marked["last_wake_ts"] = now
        marked["woken_ids"] = list(marked.get("woken_ids", [])) + \
            [m["id"] for m in batch]
        res["cooldown"] = marked

        if mode != "armed":
            res["would_submit"] = True      # the DRY 'would submit' log fact
            return res

        # armed: verified submit + confirm the turn actually STARTED
        # (delivered != woken). Retry the full inject ONCE, then durable-fail.
        res["submitted"] = True
        before = gm_state
        for attempt in (1, 2):
            if confirm_wait_s:
                time.sleep(confirm_wait_s)
            after = gm_state_fn()
            if wake.confirm_turn_started(before, after):
                res["confirmed"] = True
                return res
            if attempt == 1:
                try:
                    ok2, _ = inject_fn(digest, True)
                except Exception:  # noqa: BLE001
                    ok2 = False
                if not ok2:
                    break
        res["confirmed"] = False
        res["wake_failed"] = True
        try:
            artifact_fn({"kind": "wake_failed", "nonce": nonce, "ts": now,
                         "batch_ids": [m["id"] for m in batch],
                         "digest": digest})
        except Exception:  # noqa: BLE001 -- artifact best-effort, never wedge
            pass
        return res
    except Exception as e:  # noqa: BLE001 -- fail-open: missed wake, never a crash
        return _result({"wake": False, "reason": f"error:{type(e).__name__}"},
                       mode, cooldown_state)


# --- run_wake: the beat-riding orchestration ----------------------------------

def wake_disabled():
    """True iff the AUTHORITATIVE e-brake exists (non-synced ~/runtime,
    overridable via ORCH_RUNTIME_DIR for tests)."""
    runtime = os.environ.get("ORCH_RUNTIME_DIR", os.path.expanduser("~/runtime"))
    return os.path.exists(os.path.join(runtime, WAKE_DISABLED_BASENAME))


def run_wake(mode, *, cooldown_path, inbox_fn, gm_state_fn, composer_fn,
             inject_fn, artifact_fn, now=None, confirm_wait_s=0.0):
    """One wake pass riding a bus beat: e-brake -> gather (fail-open) ->
    wake_step -> persist cooldown iff a successful inject marked it. A corrupt
    cooldown file is passed through as None (should_wake fail-closes) and is
    NEVER overwritten — a human inspects/removes it to resume."""
    now = now if now is not None else time.time()
    if wake_disabled():
        return {"disabled": True, "wake": False, "reason": "wake_disabled",
                "mode": mode}
    cooldown = load_cooldown(cooldown_path)
    try:
        inbox = inbox_fn()
        gm_state = gm_state_fn()
        composer = composer_fn()
    except Exception as e:  # noqa: BLE001 -- a broken gather = missed wake, never a crash
        return {"disabled": False, "wake": False, "mode": mode,
                "reason": f"gather_error:{type(e).__name__}"}
    res = wake_step(inbox, gm_state, composer, cooldown, now=now, mode=mode,
                    inject_fn=inject_fn, gm_state_fn=gm_state_fn,
                    artifact_fn=artifact_fn, confirm_wait_s=confirm_wait_s)
    res["disabled"] = False
    if res.get("injected") and isinstance(res.get("cooldown"), dict):
        save_cooldown(cooldown_path, res["cooldown"])
    return res
