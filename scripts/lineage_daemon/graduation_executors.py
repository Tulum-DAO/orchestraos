"""Stage C (task #6) — the REAL executor adapters for the graduation dispatch.

`dispatch_graduation` / `execute_graduation_retire` take six injected seams
({kill_gate1_fn, promote_fn, retire_fn, create_fn, notify_fn, skip_notify_fn}) and
are, by design, PURE over them. The Stop-hook entry (graduation_stop_hook) wired
the READERS but passed NO executors — so an ARMED proceed_no_card dispatch would
call `kill_gate1_fn(seat)` == `None(seat)` and blow up (swallowed by the dispatch
guard to a false "keep", i.e. a silently-skipped retire). This module IS those six
seams, each a THIN adapter that maps the graduation `seat` dict onto the real
function's signature and returns the exact shape the substrate expects.

`live_dispatch_executors()` returns the ready-to-inject dict. It does NOT arm
anything: arming lives entirely in `armed=True` (never passed here) + registering
the hook in settings.json. This module only makes the ARMED path executable — it
changes NO default (dispatch stays armed=False), and every real target is reached
ONLY when the caller passes armed=True.

The real targets (kept behind module-level indirection so tests stub them without
importing live IO):
  kill_gate1_fn  -> execute.default_safety_recheck(agent_id) -> (category, reason);
                    adapter maps category=="SUPERSEDED_SAFE" -> (True, reason) else
                    (False, reason). FAIL-CLOSED: any error -> (False, "<err>").
  promote_fn     -> promote_successor.promote(canonical_id=lineage_root|agent_id,
                    successor_session=successor, generation=..., ...) -> report dict.
  retire_fn      -> park-idle.retire(name=agent_id, reg, meta, roster, reason)
                    (loaded via importlib — the file is hyphenated) -> bool.
  create_fn      -> ApprovalStore().create(**card) -> row id (the promote_graded
                    card producer seam; kwargs already match create's signature).
  notify_fn      -> approval_notify.notify(rid) (push the card to the operator's surface).
  skip_notify_fn -> a durable msg_store row to gm carrying the D2 notify-only ping.
"""
import os

ORCHESTRA_DIR = os.environ.get(
    "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))

# The park-idle retire reason — names this as a graduation completion (not an
# emergency rotation) so the retire log/ledger is unambiguous.
_RETIRE_REASON = ("graduation completion (graded-PASS successor promoted; "
                  "predecessor is the quiescent rollback)")

# The only classify() category that authorises an auto-retire (park-idle's own
# single auto-retire class). Mirrors execute.py KILL GATE 1.
_SAFE_CATEGORY = "SUPERSEDED_SAFE"


# ---- real-target indirection (module-level so tests monkeypatch, no live IO) --

def _safety_recheck(canary, orchestra_dir=None):
    """KILL GATE 1 real target: fresh safety re-classify via park-idle at
    execute-time. Returns (category, reason). Delegates to the lane's existing
    fail-closed implementation (execute.default_safety_recheck)."""
    from scripts.lineage_daemon import execute as _ex
    return _ex.default_safety_recheck(canary, orchestra_dir=orchestra_dir)


def _promote(canonical_id, successor_session, **kw):
    """Promote real target: the atomic identity swap. Imported lazily (the module
    pulls heavy live-state deps) so importing THIS module stays cheap + IO-free."""
    import importlib
    import sys
    sys.path.insert(0, os.path.join(ORCHESTRA_DIR, "scripts"))
    ps = importlib.import_module("promote_successor")
    return ps.promote(canonical_id, successor_session, **kw)


def _load_park_idle():
    """Load the hyphenated park-idle.py as a module (same importlib pattern as
    cron_beat/execute use — the filename is not a valid import name)."""
    import importlib.util
    pk_path = os.path.join(ORCHESTRA_DIR, "scripts", "park-idle.py")
    spec = importlib.util.spec_from_file_location("park_idle_rt", pk_path)
    pk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pk)
    return pk


def _approval_store():
    """The card producer store (ApprovalStore) — the sanctioned single card
    producer seam. Imported lazily so this module stays IO-free at import."""
    import sys
    sys.path.insert(0, os.path.join(ORCHESTRA_DIR, "scripts"))
    from approval_schema import ApprovalStore
    return ApprovalStore()


def _approval_notify(rid):
    """Push an existing card to the operator's surface (best-effort; the cron backstop
    re-notifies). Mirrors approval.py request: store.create -> notify."""
    import sys
    sys.path.insert(0, os.path.join(ORCHESTRA_DIR, "scripts"))
    from approval_notify import notify
    return notify(rid)


def _send_ping(msg):
    """Durable msg_store row carrying the D2 notify-only ping (to gm). A silent
    auto-retire is wrong (the operator Q2) — this is the after-the-fact visibility line."""
    import sys
    sys.path.insert(0, os.path.join(ORCHESTRA_DIR, "scripts"))
    from msg_store import MessageStore
    return MessageStore().send(
        from_agent="lineage-daemon", to_agent="gm",
        type="graduation_retired", priority="high",
        subject="[self-retire] graduated-T2 predecessor retired (card skipped)",
        body=msg, source="graduation-stop-hook")


# ---- the seat -> real-signature adapters --------------------------------------

def _canonical_of(seat):
    """The canonical name to promote INTO. The predecessor occupies the canonical
    name (e.g. `gm`); prefer the durable lineage_root, fall back to agent_id."""
    return seat.get("lineage_root") or seat.get("agent_id")


def make_kill_gate1_fn(*, orchestra_dir=None):
    """(ok:bool, reason:str) — True ONLY on SUPERSEDED_SAFE. FAIL-CLOSED: any
    re-check error -> (False, <err>) so a broken gate holds the kill (never a
    silent proceed). `ok` is a genuine bool (execute_graduation_retire does
    `if not ok`)."""
    def kill_gate1_fn(seat):
        canary = seat.get("agent_id")
        try:
            category, reason = _safety_recheck(canary, orchestra_dir=orchestra_dir)
        except Exception as e:  # noqa: BLE001 -- fail CLOSED: hold the kill
            return (False, f"kill-gate-1 re-check error (fail-closed): "
                           f"{type(e).__name__}: {e}")
        return (bool(category == _SAFE_CATEGORY), str(reason))
    return kill_gate1_fn


def make_promote_fn(*, generation_override=None):
    """seat -> promote_successor.promote(...) -> report dict. Maps the seat's
    canonical name + successor alias + generation onto the real signature. Returns
    promote's report dict unchanged (execute_graduation_retire stores it as-is)."""
    def promote_fn(seat):
        canonical_id = _canonical_of(seat)
        successor_session = seat.get("successor")
        gen = generation_override if generation_override is not None \
            else seat.get("generation")
        return _promote(canonical_id, successor_session, generation=gen)
    return promote_fn


def make_retire_fn(*, reason=_RETIRE_REASON):
    """seat -> park-idle.retire(name, reg, meta, roster, reason) -> bool. Loads the
    three live stores through park-idle's own loaders (single-writer atomic retire:
    registry-removal-first then kill, resume_command preserved). Retires the
    PREDECESSOR (seat.agent_id)."""
    def retire_fn(seat):
        name = seat.get("agent_id")
        pk = _load_park_idle()
        reg = pk._load(pk.REGISTRY)
        meta = pk._load(pk.AGENT_SESSIONS)
        roster = pk._load(pk.LIVE_ROSTER)
        return pk.retire(name, reg, meta, roster, reason)
    return retire_fn


def make_create_fn():
    """create_fn(**card) -> row id via the sanctioned ApprovalStore.create seam.
    dispatch_graduation already passes exactly the card's keyword args (from_agent,
    question, worker_kind, op_key, options, kind, summary, risk_level,
    reversibility, feature, evidence) — they line up with create's signature 1:1."""
    def create_fn(**card):
        st = _approval_store()
        st.migrate()
        return st.create(**card)
    return create_fn


def make_notify_fn():
    """notify_fn(rid) -> push the created card to the operator (best-effort)."""
    def notify_fn(rid):
        return _approval_notify(rid)
    return notify_fn


def make_skip_notify_fn():
    """skip_notify_fn(msg) -> the D2 notify-only send seam. notify_card_skipped
    swallows a send failure itself, but keep this a plain durable send."""
    def skip_notify_fn(msg):
        return _send_ping(msg)
    return skip_notify_fn


def live_dispatch_executors(*, orchestra_dir=None, generation_override=None):
    """The ready-to-inject executor dict for dispatch_graduation. Passing it does
    NOT arm anything (arming = `armed=True` + settings.json registration, neither
    here). It only makes the ARMED proceed_no_card / await_card paths executable
    instead of crashing on a None seam."""
    return {
        "kill_gate1_fn": make_kill_gate1_fn(orchestra_dir=orchestra_dir),
        "promote_fn": make_promote_fn(generation_override=generation_override),
        "retire_fn": make_retire_fn(),
        "create_fn": make_create_fn(),
        "notify_fn": make_notify_fn(),
        "skip_notify_fn": make_skip_notify_fn(),
    }
