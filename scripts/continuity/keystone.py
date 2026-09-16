#!/usr/bin/env python3
"""Continuity-v4 KEYSTONE — promote_successor -> mint_epoch compat-wrapper (SHADOW)
+ the KCF-1b guarded tmux consolidation fold.

The LAST leg of the authority-layer build + the highest blast radius: it rewires the
LIVE `promote_successor` verb so the seat's identity/epoch write goes THROUGH the
integration-strength SOLE issuer `authority.mint_epoch` (A2-2) — promote_successor
stops being the issuer of record and becomes a compat wrapper. (NB: the sole issuer is
`continuity/authority.py:mint_epoch`, NOT the Phase-0 REFERENCE `controller.py`, whose
own docstring flags its checks as illustrative-shape only.)

STILL SHADOW: `mint_for_promotion` runs ALONGSIDE the verb's existing behavior — it
records the durable epoch/lease authority state in sqlite, and it does NOT replace or
gate the actual promotion and does NOT touch the JSON stores (byte-safety). It is
SWALLOW-BY-RETURN: any failure is signalled by the return value, never raised into
promote() — a broken keystone can never break a real rotation. Enforcement (letting the
mint gate a promotion) stays a separate later the operator gate.

`consolidate_tmux` converges KCF-1b: after the store writes, rename the successor
session -> canonical + retire the predecessor pane, GUARDED fail-CLOSED (never kill
without the successor verified live+vouched AND the predecessor rollback preserved).
"""
from __future__ import annotations

import importlib.util
import os

_here = os.path.dirname(os.path.abspath(__file__))


def _authority():
    """Load the integration-strength authority module (the sole epoch issuer)."""
    spec = importlib.util.spec_from_file_location(
        "cv4_authority", os.path.join(_here, "authority.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _transition_key(seat: str, sid, generation) -> str:
    """Deterministic idempotency key for ONE rotation transition — so a retried
    promotion of the same (seat, sid, generation) returns the SAME mint (F1: no
    double-mint), while a genuinely new transition mints a fresh epoch."""
    return f"promote:{seat}:{sid}:{generation}"


def mint_for_promotion(*, seat: str, generation, sid, successor_session: str,
                       db: str | None = None, records=None,
                       ttl_s: int = 300, db_timeout_s: float = 1.5) -> dict:
    """SHADOW mint ALONGSIDE promote_successor. Acquire a fresh lease for `seat`,
    mint the next epoch through `authority.mint_epoch` (reason=rotation, the sole
    issuer), then release the lease. Records durable authority state; does NOT touch
    the JSON stores; NEVER raises into the caller (swallow-by-return).

    `records` (optional [(store, key, record), ...]) is accepted for context/parity
    with the verb's grouped observe call but is NOT written here (shadow byte-safety).

    Returns {ok, epoch?, mint_id?, reason?, lease_token?, idempotency_key?, reason_err?}
    — ok=False + reason on any failure (the promote proceeds regardless)."""
    a = _authority()
    db = db or a.DEFAULT_DB
    idem = _transition_key(seat, sid, generation)
    lease = None
    try:
        lease = a.acquire_lease(seat=seat, holder_id=successor_session, ttl_s=ttl_s,
                                authorized_by=f"promote:{successor_session}", db=db,
                                db_timeout_s=db_timeout_s)
        mint = a.mint_epoch(
            seat=seat, reason="rotation",
            authorized_by=f"promote:{successor_session}",
            lease_token=lease["token"], idempotency_key=idem, db=db,
            db_timeout_s=db_timeout_s,
            lease_valid=lambda s, t: a.lease_held(s, t, db=db,
                                                  db_timeout_s=db_timeout_s))
        return {"ok": True, "epoch": mint["epoch"], "mint_id": mint["mint_id"],
                "reason": mint["reason"], "lease_token": lease["token"],
                "idempotency_key": idem}
    except Exception as e:
        # SHADOW: a broken mint must never break the promotion — signal by return.
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
    finally:
        if lease is not None:
            try:
                a.release_lease(seat=seat, token=lease["token"], db=db,
                                db_timeout_s=db_timeout_s)
            except Exception:
                pass   # release best-effort; a leaked lease expires on its ttl


# ---- KCF-1b: guarded tmux CONSOLIDATION (rename successor -> canonical + retire) --

def _default_tmux(*args):
    """Real tmux runner: returns (returncode, stdout). Used in production; tests
    inject a fake so no real session is touched."""
    import subprocess
    p = subprocess.run(["tmux", *args], capture_output=True, text=True)
    return (p.returncode, p.stdout)


def _has_session(tmux, name) -> bool:
    rc, _ = tmux("has-session", "-t", name)
    return rc == 0


def _client_attached(tmux, name) -> bool:
    """True iff a tmux CLIENT is attached to session `name` (a human is on it).
    `tmux list-clients -t <session>` prints one line per attached client; empty
    output = nobody attached. Fail-CLOSED: on any error treat as ATTACHED (safer to
    defer a kill than to kill under a possibly-present human)."""
    try:
        rc, out = tmux("list-clients", "-t", name)
    except Exception:
        return True
    if rc != 0:
        return False   # rc!=0 => no such session / no clients (nothing to protect)
    return bool(out and out.strip())


def _rollback_preserved(rollback) -> bool:
    """A predecessor's rollback is preserved iff BOTH its transcript and its
    resume_command are present (so it is reversible-by-resume). Missing either =>
    NOT safe to kill (we would destroy the only way back)."""
    if not isinstance(rollback, dict):
        return False
    return bool(rollback.get("transcript")) and bool(rollback.get("resume_command"))


def consolidate_tmux(*, canonical_id: str, successor_session: str,
                     predecessor_pane: str | None, successor_vouched: bool,
                     predecessor_rollback: dict, predecessor_archive_name: str | None = None,
                     tmux=None, dry_run: bool = False, alert=None) -> dict:
    """KCF-1b: after the store writes, make tmux agree with the stores — rename the
    successor session to the canonical name + retire the predecessor pane.

    TWO ROTATION SHAPES (the dual-verify seat-orphan finding, gm msg_806e95e7):
      - canonical-IS-PREDECESSOR (PRIMARY, gm/ob): the predecessor's LIVE pane is
        named `canonical_id` itself. A naive single rename (successor -> canonical)
        would COLLIDE with the live predecessor, and killing `predecessor_pane`
        (== canonical) AFTER the rename would kill the freshly-renamed SUCCESSOR and
        ORPHAN the seat. So this shape needs a **2-RENAME sequence**: move the
        predecessor OFF the canonical name to its `predecessor_archive_name` FIRST,
        then rename the successor -> canonical, then retire by the archive-name. If
        `predecessor_archive_name` is missing in this shape -> FREEZE (never risk the
        orphan).
      - canonical-is-NOT-predecessor: the predecessor pane is a DISTINCT name (or
        None). Single rename (successor -> canonical) then retire the distinct pane.

    GUARDED fail-CLOSED (never orphan the seat, never destroy the only way back):
      - IDEMPOTENT: successor gone AND canonical live AND no lingering archive-name
        pane -> consolidation already happened -> clean no-op success.
      - successor must be a LIVE pane AND content-vouched (`successor_vouched`);
      - predecessor rollback must be PRESERVED (transcript + resume_command);
      - canonical-IS-predecessor shape requires a distinct `predecessor_archive_name`.
      Any guard fails -> DO NOT rename, DO NOT kill; leave ALL sessions, alert, freeze.
      DEFENSE-IN-DEPTH: the retire step REFUSES to kill any pane named `canonical_id`
      (a miswired archive-name can never destroy the seat). Kill strictly AFTER the
      rename(s), so the seat is never nameless. Reversible-by-resume (we never touch
      the predecessor's transcript/resume_command).

    `dry_run` inspects + returns the plan, mutating nothing. Returns
    {consolidated, frozen?, already?, dry_run?, reason?, renamed?, retired?}."""
    tmux = tmux or _default_tmux
    canonical_is_pred = (predecessor_pane == canonical_id)

    def _freeze(reason):
        if alert is not None:
            try:
                alert(kind="cv4_consolidation_frozen", canonical_id=canonical_id,
                      successor_session=successor_session,
                      predecessor_pane=predecessor_pane, reason=reason)
            except Exception:
                pass
        return {"consolidated": False, "frozen": True, "reason": reason}

    # IDEMPOTENT: already consolidated (canonical live, successor gone, no archive-name
    # pane lingering from a half-done 2-rename).
    if not _has_session(tmux, successor_session) and _has_session(tmux, canonical_id):
        lingering = bool(predecessor_archive_name
                         and _has_session(tmux, predecessor_archive_name))
        if not lingering:
            return {"consolidated": True, "already": True,
                    "reason": "canonical already live; successor session gone"}

    # GUARD 1: successor must be a live, vouched pane.
    if not _has_session(tmux, successor_session):
        return _freeze(f"successor session {successor_session!r} is not a live tmux "
                       f"pane — refusing to hand the seat to a dead successor")
    if not successor_vouched:
        return _freeze(f"successor {successor_session!r} is not content-vouched "
                       f"(declared-identity) — refusing to rename onto an unvouched pane")
    # GUARD 2: predecessor rollback must be preserved (reversible-by-resume).
    if not _rollback_preserved(predecessor_rollback):
        return _freeze("predecessor rollback not preserved (need transcript + "
                       "resume_command) — refusing to kill the only way back")
    # GUARD 3 (FLAG B): a human ATTACHED to the predecessor pane -> FREEZE, never kill
    # under them (the operator caught the gen5/gen6 split BY being attached; killing loses his
    # context + is destructive-under-a-human). Checked BEFORE any rename, so it also
    # defends the predecessor_pane orphan hazard in depth (if he is on the canonical
    # pane, it freezes rather than mutating).
    if predecessor_pane and _client_attached(tmux, predecessor_pane):
        return _freeze(f"a client is ATTACHED to predecessor pane {predecessor_pane!r} "
                       f"(a human is on it) — refusing to rename/kill under them; "
                       f"consolidation deferred + alerted")
    # GUARD 4: canonical-IS-predecessor needs a DISTINCT archive-name to move onto.
    if canonical_is_pred:
        if not predecessor_archive_name:
            return _freeze(
                f"canonical-IS-predecessor shape (predecessor pane == {canonical_id!r}) "
                f"but no predecessor_archive_name to move it to — refusing (a single "
                f"rename would orphan the seat). Provide the predecessor's archive name.")
        if predecessor_archive_name == canonical_id:
            return _freeze(
                f"predecessor_archive_name == canonical_id {canonical_id!r} — refusing "
                f"(would re-collide / kill the seat).")

    if dry_run:
        plan = ([[predecessor_pane, predecessor_archive_name],
                 [successor_session, canonical_id]] if canonical_is_pred
                else [[successor_session, canonical_id]])
        retire = predecessor_archive_name if canonical_is_pred else predecessor_pane
        return {"consolidated": False, "dry_run": True,
                "plan": {"renames": plan, "retire": retire}}

    # ACT.
    if canonical_is_pred:
        # 1) move the predecessor OFF the canonical name to its archive-name FIRST.
        rc, _ = tmux("rename-session", "-t", predecessor_pane, predecessor_archive_name)
        if rc != 0:
            return _freeze(f"predecessor rename {predecessor_pane} -> "
                           f"{predecessor_archive_name} failed (rc={rc}) — nothing "
                           f"killed, seat intact")
        retire_target = predecessor_archive_name
    else:
        retire_target = predecessor_pane          # distinct pane (or None)

    # 2) rename successor -> canonical (now free).
    rc, _ = tmux("rename-session", "-t", successor_session, canonical_id)
    if rc != 0:
        return _freeze(f"rename-session {successor_session} -> {canonical_id} failed "
                       f"(rc={rc}) — predecessor left intact")

    # 3) retire the predecessor by its (archive) name — DEFENSE-IN-DEPTH: NEVER kill a
    # pane named canonical_id (that is the seat we just handed to the successor).
    killed = None
    if retire_target:
        if retire_target == canonical_id:
            return _freeze(f"refused to kill a pane named canonical_id {canonical_id!r} "
                           f"— that is the live seat (would orphan it). Seat handed to "
                           f"successor; predecessor pane left for manual review.")
        if _has_session(tmux, retire_target):
            tmux("kill-session", "-t", retire_target)
            killed = retire_target
    return {"consolidated": True, "renamed": [successor_session, canonical_id],
            "retired": killed,
            "archived_predecessor": predecessor_archive_name if canonical_is_pred else None}
