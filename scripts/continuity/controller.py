#!/usr/bin/env python3
"""Continuity v4 — CONTROLLER.

The 8 tests in scripts/test_cv4_controller_acceptance.py are REFERENCE-BEHAVIOR
fixtures (gm directive 2026-08-21), each pinned to a real g14 incident. They
prove the interface + behavior SHAPE, NOT production safety / transactional
semantics / fleet integration. This module is a Phase-0 REFERENCE controller —
its checks are illustrative (e.g. authorize_write accepts a {'valid':true} lease
shape, not a real minted-run/epoch/lease-expiry/authorized_by; promote() trusts
its `stores` paths rather than enforcing live-store safety itself). Integration-
strength versions are required before any real writer routes through the shadow
guard (see the test module docstring for the per-test gap list).

Provenance (corrected 2026-08-21, the operator review): TDD RED-FIRST — tests+stub
committed RED (730b6b1b4), then greened in ONE commit (7992b0293), NOT one at a
time; the earlier claim was inaccurate.

Design constraints (write-fencing spec Amendment 2, CONSENSUS_REACHED):
  - The controller is the SOLE epoch issuer; rotation/rollback/recovery/adoption/
    repair route through it (promote_successor becomes a compat wrapper).
  - Write AUTHORITY = controller-minted run + active seat epoch + authorized_by +
    lease. sid_source / observation planes are provenance only, never authz.
  - Transitional containment over the 3 legacy JSON stores; final authority is
    the SQLite transaction. Nothing arms until the Phase-2 gate.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ORCHESTRA_DIR = Path(os.environ.get(
    "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))

# Context evidence is only "fresh" within this window (the detector rewrites
# ~every turn; anything older is a stale sample, not a live measurement).
CTX_FRESH_WINDOW_S = 15 * 60
# Rotation only triggers at/above the fleet rotate line (the operator's rule = 85).
ROTATE_THRESHOLD_PCT = 80


class RotationRefused(RuntimeError):
    """A rotation was refused (e.g. ctx evidence not admissible)."""


class WriteFenceRejected(RuntimeError):
    """A write was rejected fail-closed (no authority / stale observation)."""


class PromotionBlocked(RuntimeError):
    """Autonomous promotion blocked (e.g. grader disagreement uncalibrated)."""


class LiveStoreGuard(RuntimeError):
    """Refused to bind/operate on a LIVE authority store (test-safety)."""


def _parse_ts(s):
    try:
        return datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None


# --- T1/T2: context evidence admissibility -------------------------------
def validate_ctx_evidence(evidence: dict, *, now=None) -> None:
    """Admissible iff independently MEASURED (detector), timestamp-FRESH, and
    bound to a specific TRANSITION. Missing any one -> RotationRefused. A
    self-reported or stale number is never admissible on its own (T1/T2)."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(evidence, dict):
        raise RotationRefused("ctx evidence must be a dict")
    if evidence.get("source") != "detector":
        raise RotationRefused(
            f"ctx evidence not independently measured (source="
            f"{evidence.get('source')!r}; require 'detector')")
    used = evidence.get("used_pct")
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        raise RotationRefused(f"used_pct must be numeric, got {used!r}")
    if not (0 <= used <= 100):
        raise RotationRefused(f"used_pct out of range [0,100]: {used}")
    if not evidence.get("transition_id"):
        raise RotationRefused("ctx evidence not bound to a transition")
    measured = _parse_ts(evidence.get("measured_at"))
    if measured is None:
        raise RotationRefused("ctx evidence has no parseable measured_at")
    if measured.tzinfo is None:
        measured = measured.replace(tzinfo=timezone.utc)
    age = (now - measured).total_seconds()
    if age > CTX_FRESH_WINDOW_S or age < -CTX_FRESH_WINDOW_S:
        raise RotationRefused(
            f"ctx evidence stale/again ({age:.0f}s outside "
            f"+/-{CTX_FRESH_WINDOW_S}s window)")


def gate_rotation(seat: str, *, ctx_evidence: dict, now=None) -> None:
    """A rotation may only be triggered by admissible ctx evidence (T1) AND only
    when the measured used_pct is at/above the rotate line (T1-int)."""
    validate_ctx_evidence(ctx_evidence, now=now)
    if ctx_evidence["used_pct"] < ROTATE_THRESHOLD_PCT:
        raise RotationRefused(
            f"used_pct {ctx_evidence['used_pct']} below rotate line "
            f"{ROTATE_THRESHOLD_PCT}")


# --- T3: seat transfer (mail follows seat, claimed work transferred) -----
def transfer_seat(*, predecessor: str, successor: str, inbox: list,
                  claimed_work: list) -> dict:
    """On transition, UNCLAIMED mail follows the SEAT to the successor and
    CLAIMED work is EXPLICITLY transferred. Nothing is silently dropped (T3)."""
    mail_reassigned = []
    still_claimed_elsewhere = []
    for m in (inbox or []):
        if not m.get("claimed"):
            mail_reassigned.append({**m, "to": successor})
        else:
            still_claimed_elsewhere.append(m)
    work_transferred = [dict(w) for w in (claimed_work or [])]
    return {
        "predecessor": predecessor,
        "successor": successor,
        "mail_reassigned": mail_reassigned,
        "work_transferred": work_transferred,
        "still_claimed_elsewhere": still_claimed_elsewhere,
        "dropped": [],
    }


# --- T4: test-safety — never touch live authority stores -----------------
def _live_authority_paths() -> set:
    # computed at CALL time from ORCHESTRA_DIR so importlib.reload cannot
    # re-open the hole (the key1 monkeypatch-invalidation incident).
    base = ORCHESTRA_DIR
    return {
        (base / "registry.json").resolve(),
        (base / "state" / "agent-sessions.json").resolve(),
        (base / "state" / "agents").resolve(),
    }


def _assert_not_live(paths: dict) -> None:
    """Intrinsic live-store guard — used by bind_stores AND by every mutating op
    (promote/canonical) so live paths are refused even when a caller bypasses
    bind_stores (T4-int: unbypassable, not only at the binding boundary)."""
    live = _live_authority_paths()
    for p in (paths or {}).values():
        rp = Path(p).resolve()
        if rp in live or any(rp == lp or lp in rp.parents for lp in live):
            raise LiveStoreGuard(f"refused: {p} is (under) a live authority store")


def bind_stores(paths: dict) -> dict:
    """Bind the controller to a set of store paths, REFUSING any that resolve to
    a live authority store — even after importlib.reload (T4)."""
    _assert_not_live(paths)
    return {key: str(Path(p).resolve()) for key, p in (paths or {}).items()}


# --- T5: partial transition leaves predecessor canonical -----------------
def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def _write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, p)


def _canon_path(stores):
    # the canonical pointer lives in the registry store under a reserved key
    return stores["registry"]


def promote(*, seat: str, predecessor: str, successor: str, stores: dict,
            fail_after: str | None = None) -> str:
    """Write the successor as provisional across the 3 stores, THEN flip the
    canonical pointer LAST. If the sequence fails partway (fail_after), the
    canonical pointer is never flipped, so the PREDECESSOR stays canonical (T5)."""
    _assert_not_live(stores)   # intrinsic: refuse live paths even if bind was bypassed
    reg = _read_json(stores["registry"], {})
    reg.setdefault("_provisional", {})[seat] = successor
    _write_json(stores["registry"], reg)
    if fail_after == "registry":
        raise RuntimeError("simulated crash after registry write")

    sess = _read_json(stores["sessions"], {})
    sess[successor] = {"seat": seat, "status": "provisioning"}
    _write_json(stores["sessions"], sess)
    if fail_after == "sessions":
        raise RuntimeError("simulated crash after sessions write")

    agents_dir = Path(stores["agents_dir"])
    _write_json(agents_dir / f"{seat}.json", {"seat": seat, "run": successor})
    if fail_after == "agents":
        raise RuntimeError("simulated crash after agents write")

    # COMMIT: flip the canonical pointer only when all 3 stores are written.
    reg = _read_json(stores["registry"], {})
    reg.setdefault("_canonical", {})[seat] = successor
    reg.get("_provisional", {}).pop(seat, None)
    _write_json(stores["registry"], reg)
    return successor


def canonical(seat: str, stores: dict) -> str | None:
    _assert_not_live(stores)   # never read/derive canonical from a live store path
    reg = _read_json(_canon_path(stores), {})
    return (reg.get("_canonical") or {}).get(seat)


# --- T6: authority from lease+epoch, never a stale observation plane ------
def authorize_write(*, seat: str, lease=None, epoch=None,
                    observation=None) -> None:
    """Write authority comes ONLY from a valid controller lease + active seat
    epoch. A stale observation plane (session-index last_active, etc.) is
    provenance and can NEVER authorize a write (T6 / A2-8)."""
    if not (isinstance(lease, dict) and lease.get("valid") and epoch is not None):
        raise WriteFenceRejected(
            "no valid controller lease + active epoch; observation planes are "
            "provenance only and cannot authorize a write")
    # observation is recorded as provenance by the caller; it is NOT a gate here.


# --- T7: rollback is an explicit transition event, never erased ----------
def rollback_rotation(*, seat: str, transition_id: str, ledger: list) -> list:
    """Append an explicit 'rolled_back' event that REVERTS transition_id; the
    original transition record is preserved (append-only, never erased) (T7)."""
    out = list(ledger or [])
    if not any(e.get("transition_id") == transition_id for e in out):
        raise KeyError(f"no transition {transition_id} to roll back")
    out.append({
        "transition_id": f"{transition_id}-rollback",
        "seat": seat,
        "event": "rolled_back",
        "reverts": transition_id,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    return out


# --- T8: grader disagreement blocks autonomous promotion -----------------
def gate_autonomous_promotion(*, grades: list) -> None:
    """Autonomous promotion requires CALIBRATED grader agreement: every grader
    must agree AND the agreed verdict must be PASS. Any disagreement — or an
    agreed non-PASS (the degenerate 'two FAILs = agreement' case) — BLOCKS (T8)."""
    verdicts = {g.get("verdict") for g in (grades or [])}
    if len(verdicts) != 1:
        raise PromotionBlocked(
            f"machine-grader disagreement {verdicts} — blocked until calibrated")
    if verdicts != {"PASS"}:
        raise PromotionBlocked(
            f"graders agree on {verdicts} (not PASS) — not a promotion signal")
