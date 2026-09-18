"""G1 ROUTER CHARTER BUMPER (the operator directive 2026-08-19, spec
docs/SPEC_lane-charter-gate-and-done-hook.md, envelope_lint v1->v2 per its own
APPROVE bind: "deprioritization is a separate future decision" — the operator decided).

THE PREDICATE: a drive-class message (task_request/directive/request) is HELD,
loudly, when the RECIPIENT has a declared charter and the message's
contributes_to matches none of the charter's accepts tags. No charter declared
-> flow untouched (lanes opt in; a bumper that holds everything is the
idle-driver false-escalation defect wearing a new name). the operator-origin never
held. Override: metadata.charter_override — possible, expensive, recorded.
Held rows stay PENDING and visible (never silent-dropped, never status-mangled)
— a stamp makes the skip idempotent.

CALIBRATION SPECIMENS (both from tonight, real):
  must-fire     — gm's blind-score task to codex-integration (off its lane)
  must-NOT-fire — gm's T4 commission to this lane, contributes_to
                  'deterministic-in-lineage-rotation' matching the charter
"""
import importlib.util
import json
import os

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "message_router_charter_uut", os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)


def _msg(mtype="task_request", contributes=None, frm="gm", md_extra=None):
    md = dict(md_extra or {})
    if contributes is not None:
        md["contributes_to"] = contributes
    return {"id": "m1", "type": mtype, "from_agent": frm,
            "to_agent": "codex-integration", "source": "cli",
            "metadata": json.dumps(md)}


_CODEX_CHARTER = {"lane": "codex-integration",
                  "accepts": ["codex", "app-server-events"]}


# ------------------------------------------------------------------ must-fire
def test_off_charter_drive_class_is_held_and_names_the_predicate():
    """(charter_gate, codex-integration, delivery time, real predicate).
    NAMED ASSERTION: off-charter-holds. Tonight's live shape: a blind-score
    task_request to codex-integration whose contributes_to serves another
    lane. The hold reason must NAME the failed tags and the charter, so the
    first false hold is diagnosable (the T4 print-the-path law)."""
    reason = mr.charter_gate(
        _msg(contributes="deterministic-in-lineage-rotation: blind score"),
        _CODEX_CHARTER)
    assert reason is not None, "off-charter drive-class must be held"
    assert "codex" in reason and "deterministic-in-lineage-rotation" in reason, (
        f"the hold must name both sides of the mismatch: {reason}")


def test_missing_contributes_to_with_charter_is_held():
    """NAMED ASSERTION: no-envelope-no-entry. With a charter declared, a
    drive-class message with NO contributes_to at all is held — a blank
    envelope cannot be matched, and unverifiable is never a pass."""
    assert mr.charter_gate(_msg(contributes=None), _CODEX_CHARTER) is not None


# ------------------------------------------------------------- must-NOT-fire
def test_no_charter_means_flow_untouched():
    """NAMED ASSERTION: opt-in-only. No charter -> None, always — the fleet's
    other 400 agents are untouched until their lanes declare."""
    assert mr.charter_gate(_msg(contributes="anything"), None) is None


def test_on_charter_traffic_flows():
    """NAMED ASSERTION: on-charter-flows (tonight's legitimate specimen: the
    T4 commission tagged 'deterministic-in-lineage-rotation' to a lane whose
    charter accepts it)."""
    charter = {"lane": "lineage-v3-auditor",
               "accepts": ["deterministic-in-lineage-rotation", "rotation"]}
    m = _msg(contributes="deterministic-in-lineage-rotation")
    m["to_agent"] = "lineage-v3-auditor"
    assert mr.charter_gate(m, charter) is None


def test_non_drive_class_never_held():
    """NAMED ASSERTION: mail-still-announces. reply/report/decision/ack flow
    regardless of charter — the bumper gates WORK, not communication."""
    for t in ("reply", "report", "decision", "status"):
        assert mr.charter_gate(_msg(mtype=t, contributes="off-lane"),
                               _CODEX_CHARTER) is None


def test_provisioning_recipient_orientation_never_held():
    """NAMED ASSERTION: bootstrap-exemption (ob lived it, msg_6c15fe27 /
    contract f6009ffdd). A successor's DURABLE SPAWN ORDERS are task_request
    (drive-class) and carry NO lane tag by nature — the newborn does not yet
    know its lane. If its canonical name already has a charter, the gate would
    hold the very mail that tells it what its lane is. A provisioning recipient
    is exempt: it is being oriented, not dispatched off-lane."""
    m = _msg(contributes=None)                       # spawn orders, no tag
    m["to_agent"] = "lineage-v3-auditor"
    charter = {"lane": "lineage-v3-auditor", "accepts": ["rotation"]}
    assert mr.charter_gate(m, charter) is not None, "sanity: held when established"
    assert mr.charter_gate(m, charter, recipient_provisioning=True) is None, (
        "bootstrap-exemption: a provisioning newborn's orientation mail must "
        "never be held by its own not-yet-known charter")


def test_established_recipient_still_gated():
    """NAMED ASSERTION: exemption-is-narrow (must-not-over-fire). The
    provisioning exemption must NOT leak to an ESTABLISHED agent — off-charter
    dispatch to a live lane occupant is still held."""
    assert mr.charter_gate(_msg(contributes="off-lane"), _CODEX_CHARTER,
                           recipient_provisioning=False) is not None


def test_shaw_origin_never_held():
    """NAMED ASSERTION: operator-bypasses-bumpers. The bumper constrains
    dispatchers, never the principal."""
    assert mr.charter_gate(_msg(contributes="off-lane", frm="operator"),
                           _CODEX_CHARTER) is None
    assert mr.charter_gate(
        {**_msg(contributes="off-lane"), "source": "operator"},
        _CODEX_CHARTER) is None


def test_override_is_honoured_not_silent():
    """NAMED ASSERTION: override-expensive-never-silent. charter_override
    present -> flows, and the gate's return still distinguishes it (None =
    deliver; the CALLER logs the override — the predicate stays pure)."""
    assert mr.charter_gate(
        _msg(contributes="off-lane",
             md_extra={"charter_override": "gm: cross-lane emergency X"}),
        _CODEX_CHARTER) is None


def test_already_held_stamp_short_circuits():
    """NAMED ASSERTION: hold-is-idempotent. A stamped row skips without
    re-deciding (envelope_lint's own stamp convention)."""
    r = mr.charter_gate(
        _msg(contributes="off-lane",
             md_extra={"charter_held_at": "2026-08-19T10:00:00Z"}),
        _CODEX_CHARTER)
    assert r == "already-held"


def test_malformed_metadata_with_charter_holds_not_crashes():
    """NAMED ASSERTION: broken-envelope-is-missing. Unparseable metadata with
    a charter declared = missing envelope (held), never an exception."""
    m = _msg()
    m["metadata"] = "{not json"
    assert mr.charter_gate(m, _CODEX_CHARTER) is not None


# --------------------------------------------------------------- charter load
def test_load_charter_reads_state_lanes(tmp_path, monkeypatch):
    """NAMED ASSERTION: charter-location-pinned. state/lanes/<id>.charter.json
    — one location resolved from the agent id (the T4 path law). Absent or
    malformed -> None (opt-in-only)."""
    lanes = tmp_path / "state" / "lanes"
    lanes.mkdir(parents=True)
    (lanes / "codex-integration.charter.json").write_text(
        json.dumps(_CODEX_CHARTER))
    (lanes / "broken.charter.json").write_text("{nope")
    # ONE HOME: load_charter lives in lane_charter now (router re-exports it),
    # so the pinned dir is patched there — the same symbol the send side (G3
    # leg-1) imports.
    import importlib
    lc = importlib.import_module("lane_charter")
    monkeypatch.setattr(lc, "ORCHESTRA_DIR", tmp_path)
    assert mr.load_charter("codex-integration") == _CODEX_CHARTER
    assert mr.load_charter("no-such-agent") is None
    assert mr.load_charter("broken") is None
