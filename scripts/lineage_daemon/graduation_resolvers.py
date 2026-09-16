"""Stage C — the PRODUCTION live resolvers for the graduation Stop-hook.

`graduation_stop_hook.main()` no-ops (`noop:unwired`) until it is handed two live
resolvers. This module IS those two, sourced from the live 3-store world:

  resolve_pred(session_id) -> predecessor agent-id | None
      Reverse-maps a live session_id (state/agent-sessions.json) to its agent-id,
      then answers "is this a lineage predecessor with a LIVE, GRADED successor —
      i.e. an actual retire candidate?". Returns the predecessor id only when ALL of:
        * the session resolves to a registry agent-id,
        * that row carries `succeeded_by` (it IS a lineage predecessor),
        * the named successor row exists AND is live (status online/quiescent-live),
        * the successor has a comprehension grade artifact on disk (graded).
      Every other case (the COMMON case — an ordinary agent's turn boundary, no
      successor yet, ungraded, unreadable store) returns None so the hook no-ops.

  readers_for(pred) -> the 7-key readers dict graduation_dispatch.build_ctx_seat
      consumes, each key a live-state reader closure over the store paths:
        successor_of, seat_meta_of, beats_since_promotion_of, successor_live,
        handoff_confirmed_of, grade_of, pending_duty_of.

Design mirrors the lane's fail-toward-not-acting polarity: every reader degrades to
a KEEP-safe default (None / False / {} / []) on any missing/unreadable store — it
NEVER raises. The dispatch swallow (graduation_dispatch:80) is a second net, but a
reader that returns "no affirmative signal" is what actually keeps the hook inert.

Hermetic by construction: all four store paths are keyword-only injectables with
production defaults, so tests drive an isolated scratch world while production wires
the real locations via `live_resolvers()`.
"""
import json
import os

# Reuse the self-retire layer's strict-grade + lineage helpers rather than
# reinventing them — grade_of consumes the SAME strict comprehension verdict the
# autonomy predicate does, so the two reads can never diverge.
from scripts.lineage_daemon import self_retire_gate as SG

# repo root = scripts/lineage_daemon/graduation_resolvers.py -> three dirs up. In a
# worktree this resolves to that worktree's own registry / sessions / handoffs.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_REGISTRY_PATH = os.path.join(_REPO_ROOT, "registry.json")
_DEFAULT_SESSIONS_PATH = os.path.join(_REPO_ROOT, "state", "agent-sessions.json")
_DEFAULT_HANDOFFS_DIR = os.path.join(_REPO_ROOT, "state", "agent-handoffs")

# A successor counts as "live" (a real head the predecessor is the rollback FOR)
# only in these statuses. retired/killed/quiescent/None => not a live head, so the
# predecessor is NOT a retire candidate (retiring it would leave no oriented head).
_LIVE_STATUSES = frozenset({"online", "active", "live", "running"})


# ---- store loaders (fail-safe: unreadable -> empty, never raise) --------------

def _load_json(path):
    """Load a JSON store; {} on any read/parse error (fail toward no-signal)."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _agents(registry_path):
    reg = _load_json(registry_path)
    ag = reg.get("agents")
    return ag if isinstance(ag, dict) else {}


def _row(registry_path, agent_id):
    row = _agents(registry_path).get(agent_id)
    return row if isinstance(row, dict) else None


# ---- resolve_pred -------------------------------------------------------------

def _agent_id_for_session(sessions_path, registry_path, session_id):
    """Reverse-map a session_id -> agent-id. Prefer the sessions store
    (agent-id -> {session_id}); fall back to the registry row's own session_id
    (some canonical rows carry it directly). None if no store binds it."""
    if not isinstance(session_id, str) or not session_id:
        return None
    sessions = _load_json(sessions_path)
    for aid, meta in sessions.items():
        if isinstance(meta, dict) and meta.get("session_id") == session_id:
            return aid
    for aid, row in _agents(registry_path).items():
        if isinstance(row, dict) and row.get("session_id") == session_id:
            return aid
    return None


def _successor_is_live_graded(registry_path, handoffs_dir, successor):
    """True iff the named successor row exists, is a LIVE head, AND has a
    comprehension grade artifact on disk (an actually-graded successor — the
    signal that a rotation reached completion and the predecessor is retire-ready).
    A missing row / non-live status / absent artifact => False (common case)."""
    if not successor:
        return False
    srow = _row(registry_path, successor)
    if srow is None:
        return False
    if srow.get("status") not in _LIVE_STATUSES:
        return False
    comp = os.path.join(handoffs_dir, f"{successor}.comprehension.json")
    try:
        return os.path.exists(comp)
    except OSError:
        return False


def resolve_pred(session_id, *,
                 registry_path=_DEFAULT_REGISTRY_PATH,
                 sessions_path=_DEFAULT_SESSIONS_PATH,
                 handoffs_dir=_DEFAULT_HANDOFFS_DIR):
    """Map a live session_id to a lineage predecessor that HAS a live graded
    successor (the retire candidate), or None for the common case. NEVER raises —
    any store/read failure resolves to None (the hook then no-ops)."""
    try:
        aid = _agent_id_for_session(sessions_path, registry_path, session_id)
        if not aid:
            return None
        row = _row(registry_path, aid)
        if row is None:
            return None
        successor = row.get("succeeded_by") or row.get("successor")
        if not successor:
            return None                       # not a lineage predecessor
        if not _successor_is_live_graded(registry_path, handoffs_dir, successor):
            return None                       # no live-graded successor yet
        return aid
    except Exception:  # noqa: BLE001 -- resolver must never wedge the Stop-hook turn
        return None


# ---- readers_for --------------------------------------------------------------

def _successor_of(registry_path, pred):
    row = _row(registry_path, pred)
    if row is None:
        return None
    return row.get("succeeded_by") or row.get("successor") or None


def _seat_meta_of(registry_path, pred):
    """The seat metadata build_ctx_seat folds into the substrate seat:
    lineage_root, generation, grade_commit. Sourced from the predecessor's registry
    row; {} on a missing row (build_ctx_seat then falls back to pred/None)."""
    row = _row(registry_path, pred)
    if row is None:
        return {}
    return {
        "lineage_root": row.get("lineage_root")
        or (row.get("lineage") if isinstance(row.get("lineage"), str) else None)
        or pred,
        "generation": row.get("generation"),
        "grade_commit": row.get("grade_commit") or row.get("grade_commit_sha"),
    }


def _successor_live(registry_path, successor):
    srow = _row(registry_path, successor)
    if srow is None:
        return False
    return srow.get("status") in _LIVE_STATUSES


def _grade_of(handoffs_dir, successor):
    """The unattended-consumable grade signal for `may_retire`'s grade_agree:
      "PASS" iff the successor's comprehension is an AFFIRMATIVE STRICT pass
             (self_retire_gate._graduation_pass — pass==true AND mode=="strict").
      "FAIL" iff the artifact is present AND explicitly a failed verdict.
      None    for everything else (absent/unreadable artifact, pending, or a
              supervised grade) — no-data-is-not-permission, KEEP-polarity.
    Reuses the autonomy layer's strict reader so the grade the hook sees can never
    diverge from the grade the graduation predicate consumes."""
    if not successor:
        return None
    # Affirmative strict PASS (the ONLY unattended-consumable positive signal).
    try:
        seat = {"successor": successor}
        if SG._graduation_pass(seat, handoffs_dir):
            return "PASS"
    except Exception:  # noqa: BLE001 -- fall through to explicit-FAIL / None
        pass
    # Present-but-failed: explicit FAIL so may_retire names it (still KEEP).
    path = os.path.join(handoffs_dir, f"{successor}.comprehension.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("pass") is False:
        return "FAIL"
    verdict = data.get("verdict")
    if isinstance(verdict, str) and verdict.strip().upper() == "FAIL":
        return "FAIL"
    return None                               # pending / supervised / unknown


def _handoff_confirmed_of(handoffs_dir, successor):
    """Handoff confirmed == the successor committed its readback, i.e. a
    comprehension artifact exists on disk (the readback record). Absent => False."""
    if not successor:
        return False
    path = os.path.join(handoffs_dir, f"{successor}.comprehension.json")
    try:
        return os.path.exists(path)
    except OSError:
        return False


def _beats_since_promotion_of(registry_path, pred):
    """Healthy beats since the successor's promotion. The registry does not carry a
    live beat counter, so derive it conservatively: if the successor row carries an
    explicit integer `beats_since_promotion`, use it; otherwise None (may_retire
    then KEEPs — no-data is not permission, and this hook stays dry regardless).
    A real beat count is threaded by the separately-gated live daemon caller."""
    successor = _successor_of(registry_path, pred)
    for who in (successor, pred):
        row = _row(registry_path, who) if who else None
        if row is not None and isinstance(row.get("beats_since_promotion"), int):
            return row["beats_since_promotion"]
    return None


def _pending_duty_of(registry_path, pred):
    """Post-grade duties routed to the predecessor (may_retire KEEPs if any). The
    registry row's `pending_duty` list if present, else [] (nothing pending)."""
    row = _row(registry_path, pred)
    if row is None:
        return []
    duty = row.get("pending_duty")
    return duty if isinstance(duty, list) else []


def readers_for(pred, *,
                registry_path=_DEFAULT_REGISTRY_PATH,
                sessions_path=_DEFAULT_SESSIONS_PATH,
                handoffs_dir=_DEFAULT_HANDOFFS_DIR):
    """Build the 7-key live-state readers dict graduation_dispatch.build_ctx_seat
    consumes. Each reader closes over the store paths and degrades to a KEEP-safe
    default on any read failure (never raises). `sessions_path` is accepted for a
    uniform call shape with resolve_pred though these readers key off pred, not sid."""
    return {
        "successor_of": lambda p: _successor_of(registry_path, p),
        "seat_meta_of": lambda p: _seat_meta_of(registry_path, p),
        "beats_since_promotion_of": lambda p: _beats_since_promotion_of(registry_path, p),
        "successor_live": lambda s: _successor_live(registry_path, s),
        "handoff_confirmed_of": lambda s: _handoff_confirmed_of(handoffs_dir, s),
        "grade_of": lambda s: _grade_of(handoffs_dir, s),
        "pending_duty_of": lambda p: _pending_duty_of(registry_path, p),
    }


# ---- production wiring convenience --------------------------------------------

def live_resolvers(*,
                   registry_path=_DEFAULT_REGISTRY_PATH,
                   sessions_path=_DEFAULT_SESSIONS_PATH,
                   handoffs_dir=_DEFAULT_HANDOFFS_DIR):
    """Return (resolve_pred, readers_for) bound to the real live stores — the two
    callables graduation_stop_hook.main() wires in production. Pure wiring; reading
    happens lazily inside the closures at hook time, so this has no side effects and
    does NOT arm anything (registration in settings.json remains the arming step)."""
    def _rp(session_id):
        return resolve_pred(session_id, registry_path=registry_path,
                            sessions_path=sessions_path, handoffs_dir=handoffs_dir)

    def _rf(pred):
        return readers_for(pred, registry_path=registry_path,
                           sessions_path=sessions_path, handoffs_dir=handoffs_dir)

    return _rp, _rf
