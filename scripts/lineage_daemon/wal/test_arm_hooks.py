"""B2 — PRODUCTION arm hooks. The CONVERGED provider-agnostic register_sid / start_capture
(GOAL-FINDING #1 sid-register + #5 WAL-capture-at-arm) lifted OUT of the one-off demo driver
(fire_gemini_demo.py) into a reusable production module, so the operator arm-path and the demo
driver share ONE implementation and can never drift.

Every hook keys the seat's runtime/model off the CANONICAL LINEAGE (read_canonical_blue's #15
runtime join), NEVER a literal — the SAME hook arms a claude / gemini / codex seat unchanged.
All external effects are dependency-injected (live defaults); tests inject fakes.
"""
import sys

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal import arm_hooks  # noqa: E402


ROOT = "arm-hooks-seat"


def test_register_sid_fn_mints_with_lineage_runtime_not_literal():
    """#1: register resolves the LIVE cid and mints the session doc with runtime/model taken
    from the lineage — no hardwired provider. A codex seat gets runtime=codex end to end."""
    captured = {}

    def fake_update(seat, fields, full_record):
        captured["seat"] = seat
        captured["fields"] = fields
        captured["record"] = full_record
        return True

    reg = arm_hooks.make_register_sid_fn(
        "/orch", "/wal",
        resolve_cid_fn=lambda s: "cid-codex-1",
        lineage_fn=lambda s: ("codex", "gpt-5.6-terra"),
        read_record_fn=lambda s: None,
        update_session_fn=fake_update)
    cid = reg(ROOT)
    assert cid == "cid-codex-1"
    assert captured["fields"] == {"session_id": "cid-codex-1"}
    assert captured["record"]["runtime"] == "codex"
    assert captured["record"]["model"] == "gpt-5.6-terra"
    assert captured["record"]["session_id"] == "cid-codex-1"


def test_register_sid_fn_raises_when_no_cid():
    """No 'You are <seat>' declaration in any provider store => no cid => fail (arm_lineage
    breadcrumbs it fail-soft, then the conformance gate refuses)."""
    reg = arm_hooks.make_register_sid_fn(
        "/orch", "/wal",
        resolve_cid_fn=lambda s: None,
        lineage_fn=lambda s: ("gemini", "g-model"),
        read_record_fn=lambda s: None,
        update_session_fn=lambda *a, **k: True)
    with pytest.raises(RuntimeError):
        reg(ROOT)


def test_register_sid_fn_preserves_existing_record_fields():
    """A REAL seat already has cwd / tier / prompt_file — register overwrites ONLY
    session_id + runtime/model and preserves the rest (never clobbers a live seat's doc)."""
    captured = {}
    existing = {"cwd": "/home/testuser/repos/thing", "tier": "T3",
                "prompt_file": "prompts/custom.md", "runtime": "claude", "model": "old"}
    reg = arm_hooks.make_register_sid_fn(
        "/orch", "/wal",
        resolve_cid_fn=lambda s: "cid-new",
        lineage_fn=lambda s: ("gemini", "g-new"),
        read_record_fn=lambda s: dict(existing),
        update_session_fn=lambda seat, fields, full_record: captured.update(record=full_record))
    reg(ROOT)
    rec = captured["record"]
    assert rec["cwd"] == "/home/testuser/repos/thing"      # preserved
    assert rec["tier"] == "T3"                          # preserved
    assert rec["prompt_file"] == "prompts/custom.md"    # preserved
    assert rec["session_id"] == "cid-new"               # overwritten
    assert rec["runtime"] == "gemini" and rec["model"] == "g-new"  # from lineage


def test_start_capture_fn_backfills_via_runtime_dispatched_adapter():
    """#5: WAL capture backfills through the PRODUCTION dispatch — source_path_for(runtime,cid)
    + make_adapter(runtime).tail(source). Provider-agnostic: the runtime selects the adapter."""
    seen = {}

    class FakeAdapter:
        def tail(self, source):
            seen["source"] = source
            return 45

    class FakeStore:
        def close(self):
            seen["closed"] = True

    cap = arm_hooks.make_start_capture_fn(
        "/orch", "/wal",
        resolve_cid_fn=lambda s: "cid-x",
        lineage_fn=lambda s: ("codex", "m"),
        source_path_fn=lambda runtime, cid, cwd=None: "/live/rollout.jsonl",
        exists_fn=lambda p: True,
        store_fn=lambda seat: FakeStore(),
        adapter_fn=lambda runtime, store, seat, gen: FakeAdapter(),
        blue_generation_fn=lambda s: 42)
    n = cap(ROOT)
    assert n == 45
    assert seen["source"] == "/live/rollout.jsonl"
    assert seen["closed"] is True   # store always closed


def test_start_capture_fn_raises_when_no_source():
    cap = arm_hooks.make_start_capture_fn(
        "/orch", "/wal",
        resolve_cid_fn=lambda s: "cid-x",
        lineage_fn=lambda s: ("codex", "m"),
        source_path_fn=lambda *a, **k: None,
        exists_fn=lambda p: False,
        store_fn=lambda seat: None,
        adapter_fn=lambda *a, **k: None,
        blue_generation_fn=lambda s: 1)
    with pytest.raises(RuntimeError):
        cap(ROOT)


def test_arm_seat_pulls_runtime_from_lineage_and_wires_both_hooks():
    """arm_seat is the PRODUCTION arm entry: it reads the seat's runtime from the canonical
    lineage (NEVER a literal) and calls arm_lineage with BOTH real converged hooks wired."""
    calls = {}

    def fake_arm(wal_dir, root, runtime, seat=None, *,
                 register_sid_fn=None, start_capture_fn=None, **kw):
        calls["wal_dir"] = wal_dir
        calls["root"] = root
        calls["runtime"] = runtime
        calls["seat"] = seat
        calls["has_register"] = callable(register_sid_fn)
        calls["has_capture"] = callable(start_capture_fn)
        return f"/wal/{root}.bg_enabled"

    path = arm_hooks.arm_seat(
        "/orch", "/wal", ROOT,
        blue_reader=lambda r: {"runtime": "codex", "model": "gpt-5.6-terra"},
        arm_fn=fake_arm)
    assert path == f"/wal/{ROOT}.bg_enabled"
    assert calls["runtime"] == "codex"          # pulled from lineage, not passed
    assert calls["root"] == ROOT and calls["seat"] == ROOT
    assert calls["has_register"] and calls["has_capture"]


def test_arm_seat_defaults_seat_to_root():
    calls = {}
    arm_hooks.arm_seat(
        "/orch", "/wal", ROOT,
        blue_reader=lambda r: {"runtime": "claude"},
        arm_fn=lambda w, r, rt, seat=None, **k: calls.update(seat=seat) or "/p")
    assert calls["seat"] == ROOT


# ---- foreign-cwd claude seats (rab root cause, batch-2 2026-09-15): start_capture
# hardcoded cwd=orchestra_dir, so a claude seat whose LIVE cwd != orchestra dir (pm-aiordie,
# close-crm-integration) resolved a nonexistent transcript -> empty WAL -> WalCaptureNotStarted.

def _cap(**over):
    seen = {}

    class FakeAdapter:
        def tail(self, source):
            seen["source"] = source
            return 7

    class FakeStore:
        def close(self):
            pass

    kw = dict(
        resolve_cid_fn=lambda s: "cid-x",
        lineage_fn=lambda s: ("claude", "m"),
        source_path_fn=lambda runtime, cid, cwd=None: f"/proj/{cwd}/{cid}.jsonl",
        exists_fn=lambda p: True,
        store_fn=lambda seat: FakeStore(),
        adapter_fn=lambda runtime, store, seat, gen: FakeAdapter(),
        blue_generation_fn=lambda s: 1)
    kw.update(over)
    return arm_hooks.make_start_capture_fn("/orch", "/wal", **kw), seen


def test_start_capture_resolves_live_seat_cwd_not_orchestra_dir():
    """The claude transcript path is keyed on the seat's LIVE process cwd, never the
    orchestra dir literal."""
    cap, seen = _cap(cwd_fn=lambda seat: "/home/testuser/repos/ai-or-die-explorer")
    cap(ROOT)
    assert seen["source"] == "/proj//home/testuser/repos/ai-or-die-explorer/cid-x.jsonl"


def test_start_capture_falls_back_to_orchestra_dir_when_no_live_cwd():
    """No live process (cwd_fn -> None): keep the orchestra-dir default (no regression for
    seats that run IN the orchestra dir)."""
    cap, seen = _cap(cwd_fn=lambda seat: None)
    cap(ROOT)
    assert seen["source"] == "/proj//orch/cid-x.jsonl"


def test_start_capture_globs_transcript_by_cid_when_cwd_path_missing():
    """Second line of defense: the cwd-derived path does not exist (cwd unknown or the
    harness slug differs) -> locate the transcript by cid across ~/.claude/projects/*."""
    cap, seen = _cap(
        cwd_fn=lambda seat: "/wrong",
        exists_fn=lambda p: p == "/found/by/glob/cid-x.jsonl",
        fallback_source_fn=lambda runtime, cid: "/found/by/glob/cid-x.jsonl")
    cap(ROOT)
    assert seen["source"] == "/found/by/glob/cid-x.jsonl"


def test_start_capture_raises_when_cwd_path_and_glob_both_miss():
    cap, seen = _cap(
        cwd_fn=lambda seat: "/wrong",
        exists_fn=lambda p: False,
        fallback_source_fn=lambda runtime, cid: None)
    with pytest.raises(RuntimeError):
        cap(ROOT)
    assert "source" not in seen
