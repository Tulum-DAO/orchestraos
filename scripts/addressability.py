"""Addressability class — starvation leg 4 (DEC-1787052528 CONSENSUS_REACHED).

Mail can be addressed to identities that are STRUCTURALLY INCAPABLE of receiving it,
and the system discovers this 34 hours later, or never. Measured before the build:
36 rows pending, 35 of which could neither be delivered nor dead-lettered — act-2's
dead-letter lives in message-router.note_hold(), which only ever runs for a target that
resolves to a live pane; everything else takes handle_no_delivery() and ages out
silently at 48h.

ONE classifier, consumed by BOTH msg_store.send (send-time verdict) and
message-router (delivery-time verdict). The router's old _TARGET_CLASS map is deleted
in the same change: a second dialect is how this class regrows.

Resolution order — DECLARED beats INFERRED (the derived-never-overwrites-declared law):
  1. registry row's explicit `kind`
  2. the TRANSITIONAL migration table (loud on every hit — see MIGRATION_TABLE)
  3. live ground truth: pane running claude -> agent, pane without claude -> service
  4. spawn-race evidence: an agent-sessions row or a live pane, before the ~10min
     session-index scan has minted a registry row
  5. a registry row with no kind -> agent (a declared identity that is simply offline)
  6. nothing at all -> unknown

...with ONE safety override on the GATE (never on the kind): a live pane demonstrably
running claude is never refused, whatever any record says. Records lied about 15 live
agents' own processes on 2026-08-18; ground truth wins over a row.

FAIL-OPEN, deliberately the inverse of leg-3's fail-closed guard, and the asymmetry is
the whole argument:

    A DELIVERY GUARD THAT FAILS CLOSED **DELAYS** A MESSAGE;
    A SEND REFUSAL THAT FAILS CLOSED **DESTROYS** IT AT THE SOURCE.

Quoted verbatim at gm's instruction (msg_26a4171f) because a future agent will
otherwise "fix" this into symmetry. Any classifier error therefore ALLOWS the send and
logs [addressability-classifier-error] loudly — a silent fail-open is how a dead
classifier looks healthy.
"""
import json
import os
import subprocess
from pathlib import Path

# DATA dir (orchestra.toml [data] dir, exported as ORCHESTRA_DIR by `orchestra up` and every
# beat); the checkout is only the fallback for a bare run.
ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR") or os.environ.get("ORCH_DIR")
                     or Path(__file__).resolve().parent.parent)
CODE_ROOT = Path(__file__).resolve().parent.parent      # the checkout (scripts live here)
REGISTRY_FILE = ORCHESTRA_DIR / "registry.json"
SESSIONS_FILE = ORCHESTRA_DIR / "state" / "agent-sessions.json"

KIND_AGENT = "agent"
KIND_SERVICE = "service"
KIND_DAEMON = "daemon"
KIND_VOTE_SLOT = "vote-slot"
KIND_FIXTURE = "fixture"
KIND_UNKNOWN = "unknown"

# What to DO with a message for each kind. Only unknown/fixture/service are refused:
# daemon mail is a FEEDBACK SIGNAL a mechanism depends on (the 12 lineage-daemon rows
# are agent replies saying a handoff is already fresh), so it is REDIRECTED to a
# readable state channel. Refusing it would stop the replies being written while the
# soft-handoff trigger re-fires forever — silence made official and shipped as a fix.
DISPOSITION = {
    KIND_AGENT: "deliver",
    KIND_DAEMON: "redirect",
    KIND_SERVICE: "refuse",
    KIND_VOTE_SLOT: "refuse",
    KIND_FIXTURE: "refuse",
    KIND_UNKNOWN: "refuse",
}

# TRANSITIONAL ONLY (agy's A1.2). Every hit logs [addressability-migration-table-hit];
# the registry `kind` backfill is what makes those lines stop. A table hit that never
# goes quiet is the signal that the backfill is incomplete — this map is meant to die.
MIGRATION_TABLE = {
    "agy": KIND_VOTE_SLOT,
    "orchestra-claude": KIND_VOTE_SLOT,
    "orchestra-agy": KIND_VOTE_SLOT,
    "lineage-daemon": KIND_DAEMON,
    "pulse": KIND_DAEMON,
    "pulse-rollback": KIND_DAEMON,
    "idle-stall-watchdog": KIND_DAEMON,
    "arturo-proxy": KIND_SERVICE,
    "menu-test": KIND_FIXTURE,
}


class DegenerateClassification(RuntimeError):
    """Every target unknown with no evidence source available. That is a DEAD
    classifier reporting a clean queue — leg-3's dead-probe law applied here."""


def _noop(_msg):
    pass


def load_registry() -> dict:
    try:
        return json.loads(REGISTRY_FILE.read_text()).get("agents", {}) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_sessions() -> dict:
    try:
        return json.loads(SESSIONS_FILE.read_text()) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def live_panes() -> set:
    try:
        out = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"],
                             capture_output=True, text=True, timeout=5)
        return {s for s in out.stdout.split() if s}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return set()


def pane_has_claude(session: str) -> bool:
    """PROCESS TREE, never the leaf (agy's A1.1, adopted verbatim): a working agent
    runs pytest/git/python at the leaf, so a leaf check would read a BUSY agent as a
    service — least accurate exactly when an agent is busiest. check_claude_process
    walks every process on the pane's tty; consume it rather than mint a second
    detection dialect (the same law that deletes _TARGET_CLASS)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_agent_status", CODE_ROOT / "scripts" / "agent-status.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return bool(mod.check_claude_process(session).get("running"))


def _verdict(kind, why, source, live_claude=False):
    disposition = DISPOSITION.get(kind, "refuse")
    if live_claude and disposition != "deliver":
        # GATE override only — the declared kind is preserved so the record's
        # disagreement stays visible instead of being silently rewritten.
        why = f"{why}; override: a live claude pane is never refused"
        disposition = "deliver"
    return {
        "kind": kind,
        "disposition": disposition,
        "addressable": disposition == "deliver",
        "why": why,
        "source": source,
        "live_claude": bool(live_claude),
    }


def classify(target, *, registry=None, sessions=None, live_panes=None,
             pane_has_claude=None, log=None) -> dict:
    """Classify a delivery target. NEVER raises: on any internal error it fails OPEN
    (deliver) and logs loudly — see the module docstring for why this is the inverse
    of leg-3."""
    log = log or _noop
    try:
        registry = load_registry() if registry is None else registry
        sessions = load_sessions() if sessions is None else sessions
        panes = globals()["live_panes"]() if live_panes is None else live_panes
        has_claude = globals()["pane_has_claude"] if pane_has_claude is None else pane_has_claude

        if not isinstance(target, str) or not target:
            return _verdict(KIND_UNKNOWN, f"not a target name: {target!r}", "invalid")

        is_live = target in (panes or set())
        live_claude = bool(is_live and has_claude(target))

        row = (registry or {}).get(target) or {}
        declared = row.get("kind") if isinstance(row, dict) else None
        if declared:
            return _verdict(declared, f"registry row declares kind={declared}",
                            "registry-kind", live_claude)

        if target in MIGRATION_TABLE:
            kind = MIGRATION_TABLE[target]
            log(f"[addressability-migration-table-hit] {target} -> {kind} "
                f"(no registry kind; backfill registry.json to silence this)")
            return _verdict(kind, f"migration table says {kind}", "migration-table",
                            live_claude)

        if is_live:
            if live_claude:
                return _verdict(KIND_AGENT, "live pane running claude (process tree)",
                                "live-pane-claude", True)
            return _verdict(KIND_SERVICE,
                            "live pane with no claude process in its tty tree",
                            "live-pane-no-claude")

        if target in (sessions or {}):
            # agy A1.4: a spawning agent is live in sessions minutes before the
            # ~10min registry scan. Refusing here would drop the KICKOFF message.
            return _verdict(KIND_AGENT, "agent-sessions row (spawn-race window)",
                            "sessions-row")

        if row:
            return _verdict(KIND_AGENT, "registry row with no kind (offline agent)",
                            "registry-row")

        return _verdict(KIND_UNKNOWN, "no registry row, no session row, no live pane",
                        "none")
    except Exception as e:  # noqa: BLE001 — fail OPEN, loudly. See module docstring.
        log(f"[addressability-classifier-error] {target!r}: {type(e).__name__}: {e} "
            f"— failing OPEN (allowing the send)")
        return {"kind": KIND_UNKNOWN, "disposition": "deliver", "addressable": True,
                "why": f"classifier error, failed open: {type(e).__name__}",
                "source": "error", "live_claude": False}


def classify_many(targets, **kw) -> dict:
    """Classify a batch. Raises DegenerateClassification when EVERY target came back
    unknown from the 'none' source — that is a dead classifier (empty registry, dead
    tmux probe), and reporting it as a clean result is exactly the shape that made
    leg-3's shadow look healthy while emitting nothing."""
    out = {t: classify(t, **kw) for t in targets}
    if out and all(v["source"] == "none" for v in out.values()):
        raise DegenerateClassification(
            f"all {len(out)} targets classified unknown with no evidence source — "
            f"registry/sessions/tmux probes are dead, this is not a clean result")
    return out
