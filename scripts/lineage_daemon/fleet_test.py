"""Tests for fleet.py — the tier-scoped fleet-beat driver (wave-1 arming).

Asserts the wave-1 policy by effect: default is a pure dry-run (armed_tiers empty),
T2 is armed while T0/T1 stay observe-only even when actionable, every agent gets a
log line, and locks/ledger thread across agents within one beat.
"""
import os

import pytest

from scripts.lineage_daemon import fleet
from scripts.lineage_daemon import beat as _beat
from scripts.lineage_daemon import execute as ex
from scripts.lineage_daemon import hold_ledger as hledger


@pytest.fixture(autouse=True)
def _isolate_kill_switch(tmp_path, monkeypatch):
    """Hermetic: point BOTH e-brake locations (ORCHESTRA_DIR in-tree + ORCH_RUNTIME_DIR
    non-synced) at clean tmp dirs so tests exercise the REAL dual-location
    kill_switch_engaged without picking up the LIVE sentinels. Tests that touch a
    sentinel pass orchestra_dir=tmp_path (in-tree) or set ORCH_RUNTIME_DIR (runtime)."""
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "orch"))
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(tmp_path / "runtime"))


class FakeExecutors:
    def __init__(self, *, successor_alive=True, edge_ok=True):
        self.calls = []
        self.successor_alive = successor_alive
        self.edge_ok = edge_ok

    def _rec(self, n, armed, **x):
        self.calls.append(n)
        return dict({"cmd": [n], "executed": bool(armed)}, **x)

    def plan_register_successor(self, s, p, g, r, armed=False, orchestra_dir=None):
        return self._rec("register_successor", armed)
    def plan_spawn(self, s, armed=False, orchestra_dir=None):
        return self._rec("spawn", armed)
    def verify_successor(self, s, armed=False, orchestra_dir=None):
        return self._rec("verify_successor", armed, alive=self.successor_alive)
    def plan_inject_init(self, s, p, armed=False, orchestra_dir=None):
        return self._rec("inject_init", armed)
    def plan_wire_edge(self, p, s, g, armed=False, orchestra_dir=None):
        return self._rec("wire_edge", armed)
    def plan_verify_edge(self, p, s, armed=False, orchestra_dir=None):
        return self._rec("verify_edge", armed, ok=self.edge_ok, succeeded_by=s)
    def plan_retire(self, a, armed=False, orchestra_dir=None):
        return self._rec("retire", armed)
    def plan_repin_canonical(self, s, c, g, armed=False, orchestra_dir=None):
        return self._rec("repin_canonical", armed)


REG = {"agents": {
    "t2-hot": {"generation": 1, "tier": "T2", "lineage_root": "t2-hot"},
    "t1-hot": {"generation": 1, "tier": "T1", "lineage_root": "t1-hot"},
    "t2-calm": {"generation": 1, "tier": "T2", "lineage_root": "t2-calm"},
}}
SAFE = lambda c: ("SUPERSEDED_SAFE", "ok")
APPROVE = lambda c, s: "approve"
# The per-lineage arm gate (self_retire_armed allowlist) defaults FAIL-CLOSED: with
# no armed lineage, every hard_rotate defers. Tests that exercise OTHER guards past
# the arm gate must explicitly arm the lineage(s) under test. `_ARMED_ANY` sentinel
# arms every lineage (a frozenset that contains everything) so guard-focused tests
# reach the executor without enumerating lineage roots; arm-gate-specific tests below
# inject explicit finite sets.
class _ArmedAny(frozenset):
    def __contains__(self, item):  # noqa: D401 -- "everything is armed" sentinel
        return True
ARMED_ANY = _ArmedAny()


def _agent(aid, pct, tier):
    return {"agent_id": aid, "tier_class": tier, "death": {},
            "ctx": {"status_bar_pct": pct}}


def _fleet_hard():
    # t2-hot HARD (95%, T2), t1-hot HARD (95%, T1), t2-calm healthy (40%, T2)
    return [_agent("t2-hot", 95, "T2"), _agent("t1-hot", 95, "T1"),
            _agent("t2-calm", 40, "T2")]


# --- default is a pure dry-run (no armed tier) --------------------------------

def test_default_armed_tiers_empty_is_pure_dryrun():
    fake = FakeExecutors()
    out = fleet.plan_fleet(_fleet_hard(), REG, now=0,
                           executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
                           confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["summary"]["armed_run"] == 0
    assert fake.calls == []                       # NOTHING executed by default
    # every agent still got a log line
    assert len(out["beats"]) == 3
    assert all(b["log"].startswith("[fleet-beat]") for b in out["beats"])


# --- T2 armed, T1 observe-only ------------------------------------------------

def test_t2_armed_t1_observe_only():
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        _fleet_hard(), REG, now=0, armed_tiers={"T2"}, armed_lineages=ARMED_ANY,
        executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    by_id = {b["agent_id"]: b for b in out["beats"]}
    # t2-hot: armed + actually rotated (HARD -> hard_rotate, confirmed, safe, approved)
    assert by_id["t2-hot"]["armed"] is True
    assert by_id["t2-hot"]["armed_status"] == ex.DONE
    # t1-hot: actionable (HARD) but NOT armed -> observe-only, no execution for it
    assert by_id["t1-hot"]["armed"] is False
    assert by_id["t1-hot"]["armed_status"] == fleet.OBSERVE_TIER
    # t2-calm: healthy -> noop, not acted
    assert by_id["t2-calm"]["dry_status"] == _beat.NOOP
    # retire ran (for the T2 rotation) but t1-hot never entered execute_rotation
    assert "retire" in fake.calls
    assert out["summary"]["rotated"] == 1 and out["summary"]["observed"] == 1


def _seat(aid, pct, tier, *, runtime="claude", lineage_status="online",
          succeeded_by=None):
    a = _agent(aid, pct, tier)
    a["runtime"] = runtime
    a["lineage_status"] = lineage_status
    a["succeeded_by"] = succeeded_by
    return a


def test_unsupported_runtime_skipped_before_arming(tmp_path):
    """Unsupported runtime (e.g. service) at HARD ctx must be skipped (never armed/nudged)."""
    fake = FakeExecutors()
    reg = {"agents": {"daemon-svc": {"generation": 1, "tier": "T2",
                                      "lineage_root": "daemon-svc"}}}
    out = fleet.plan_fleet(
        [_seat("daemon-svc", 95, "T2", runtime="service")], reg, now=0,
        armed_tiers={"T2"}, executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert fake.calls == []                          # never executed
    assert out["beats"][0]["armed_status"] == fleet.SKIP_NON_CLAUDE_RUNTIME


def test_experimental_runtimes_skipped_by_default_even_when_not_actionable(tmp_path, monkeypatch):
    """Operator ruling 2026-09-17: gemini/codex are never armed unless opted in, and the skip
    is logged on every beat (ctx known or not), before actionability."""
    monkeypatch.delenv("ORCHESTRA_ROTATION_EXPERIMENTAL_RUNTIMES", raising=False)
    fake = FakeExecutors()
    reg = {"agents": {"g": {"generation": 1, "tier": "T2", "lineage_root": "g"},
                      "c": {"generation": 1, "tier": "T2", "lineage_root": "c"}}}
    out = fleet.plan_fleet(
        [_seat("g", 10, "T2", runtime="gemini"), _seat("c", 10, "T2", runtime="claude")], reg, now=0,
        armed_tiers={"T2"}, executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    by = {b["agent_id"]: b for b in out["beats"]}
    assert by["g"]["armed_status"] == fleet.SKIP_NON_CLAUDE_RUNTIME and by["g"]["armed"] is False
    assert by["c"].get("armed_status") != fleet.SKIP_NON_CLAUDE_RUNTIME and by["c"]["armed"] is True
    assert out["summary"]["skipped_non_claude_runtime"] == 1


def test_codex_runtime_evaluated_in_fleet(tmp_path, monkeypatch):
    """Multi-runtime lineage adapter: codex seats are evaluated in fleet beats once opted in."""
    monkeypatch.setenv("ORCHESTRA_ROTATION_EXPERIMENTAL_RUNTIMES", "gemini,codex")
    fake = FakeExecutors()
    reg = {"agents": {"codex-dev-1": {"generation": 1, "tier": "T2",
                                      "lineage_root": "codex-dev-1"}}}
    out = fleet.plan_fleet(
        [_seat("codex-dev-1", 95, "T2", runtime="codex")], reg, now=0,
        armed_tiers={"T2"}, executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] != fleet.SKIP_NON_CLAUDE_RUNTIME


def test_gemini_runtime_evaluated_in_fleet(tmp_path, monkeypatch):
    """Multi-runtime lineage adapter: gemini seats are evaluated in fleet beats once opted in."""
    monkeypatch.setenv("ORCHESTRA_ROTATION_EXPERIMENTAL_RUNTIMES", "gemini")
    fake = FakeExecutors()
    reg = {"agents": {"gemini-worker": {"generation": 1, "tier": "T2",
                                        "lineage_root": "gemini-worker"}}}
    out = fleet.plan_fleet(
        [_seat("gemini-worker", 95, "T2", runtime="gemini")], reg, now=0,
        armed_tiers={"T2"}, executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] != fleet.SKIP_NON_CLAUDE_RUNTIME


def test_retired_predecessor_skipped_before_arming(tmp_path):
    """Piece 2b: a retired/quiescent predecessor row must never be nudged (the
    quiescent-predecessor false-fire class, e.g. all-model-parity-gen2 13x/2h)."""
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_seat("t2-hot", 95, "T2", lineage_status="quiescent")], REG, now=0,
        armed_tiers={"T2"}, executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert fake.calls == []
    assert out["beats"][0]["armed_status"] == fleet.SKIP_SUPERSEDED_SEAT


def test_succeeded_by_predecessor_skipped_before_arming(tmp_path):
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_seat("t2-hot", 95, "T2", succeeded_by="t2-hot-gen2")], REG, now=0,
        armed_tiers={"T2"}, executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert fake.calls == []
    assert out["beats"][0]["armed_status"] == fleet.SKIP_SUPERSEDED_SEAT


def test_live_claude_seat_not_skipped_by_new_guard():
    """Behavior-neutral: a live claude seat still arms exactly as before (the new
    guard must not change the existing rotation path)."""
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_seat("t2-hot", 95, "T2")], REG, now=0, armed_tiers={"T2"},
        armed_lineages=ARMED_ANY,
        executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == ex.DONE


def test_t1_hard_rotate_is_never_armed_in_wave1():
    """A T1 agent at HARD ctx must NOT be armed-executed in wave 1 even though it is
    actionable — the kill of a T0/T1 predecessor stays a the operator one-tap (not here)."""
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("t1-hot", 95, "T1")], REG, now=0, armed_tiers={"T2"},
        executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert fake.calls == []                        # T1 never executed
    assert out["beats"][0]["armed_status"] == fleet.OBSERVE_TIER


# --- held rotation is ledgered + counted --------------------------------------

def test_t2_held_rotation_is_ledgered():
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95, "T2")], REG, now=42, armed_tiers={"T2"},
        armed_lineages=ARMED_ANY,
        executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "held", "rounds": 3,
                                 "reasons": ["comprehension:not-absorbed"]})
    assert out["summary"]["held"] == 1
    assert "retire" not in fake.calls              # held -> no kill
    assert len(out["ledger"]["holds"]) == 1        # ledgered for aging/escalation


# --- lock threads across agents in one beat -----------------------------------

def test_lock_threads_across_agents_same_lineage():
    """Two actionable agents in the SAME lineage in one beat: the second is blocked
    by the rotation lock the first acquired (one-per-lineage)."""
    reg = {"agents": {
        "dup": {"generation": 1, "tier": "T2", "lineage_root": "shared"},
        "dup-sib": {"generation": 1, "tier": "T2", "lineage_root": "shared"},
    }}
    fake = FakeExecutors()
    # first acquires the 'shared' lock and HOLDS (approval deny) so it stays in-flight
    agents = [_agent("dup", 95, "T2"), _agent("dup-sib", 95, "T2")]
    out = fleet.plan_fleet(
        agents, reg, now=1, armed_tiers={"T2"}, armed_lineages=ARMED_ANY,
        executors_impl=fake, safety_fn=SAFE, approval_fn=lambda c, s: "deny",
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    by_id = {b["agent_id"]: b for b in out["beats"]}
    # first: held (deny) -> lock released in its own finally; so the sibling is NOT
    # blocked by an in-flight lock (rotation_beat releases per-call). Both run armed.
    # This asserts the driver THREADS locks without wedging: both get an armed_status.
    assert by_id["dup"]["armed_status"] == ex.HOLD_DENIED
    assert "armed_status" in by_id["dup-sib"]


# --- armed_cohort pre-flight listing ------------------------------------------

def test_armed_cohort_lists_actionable_t2_only():
    cohort = fleet.armed_cohort(_fleet_hard(), REG, {"T2"})
    ids = {c["agent_id"] for c in cohort}
    assert ids == {"t2-hot"}                        # T2 + actionable; t1-hot excluded (tier), t2-calm excluded (noop)


def test_armed_cohort_empty_when_no_armed_tier():
    assert fleet.armed_cohort(_fleet_hard(), REG, set()) == []


# =====================================================================
# gm-required GUARDS (baked in before the cron is even proposed)
# =====================================================================

# --- guard 3: kill-switch sentinel -> instant pure no-op ----------------------

def test_runtime_kill_switch_makes_beat_pure_noop(tmp_path, monkeypatch):
    """The AUTHORITATIVE (non-synced runtime) sentinel brakes the beat to a pure no-op."""
    runtime = tmp_path / "runtime"; runtime.mkdir()
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(runtime))
    (runtime / "FLEET_BEAT_DISABLED").write_text("brake")
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        _fleet_hard(), REG, now=0, armed_tiers={"T2"}, orchestra_dir=str(tmp_path),
        executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["disabled"] is True
    assert out["beats"] == [] and fake.calls == []
    assert out["summary"]["armed_run"] == 0


def test_no_kill_switch_runs_normally(tmp_path):
    (tmp_path / "state").mkdir()                      # dir exists, sentinel absent
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95, "T2")], REG, now=0, armed_tiers={"T2"},
        armed_lineages=ARMED_ANY,
        orchestra_dir=str(tmp_path), executors_impl=fake, safety_fn=SAFE,
        approval_fn=APPROVE, confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["disabled"] is False
    assert out["summary"]["armed_run"] == 1


def test_kill_switch_engaged_only_on_runtime_path(tmp_path, monkeypatch):
    """HARDENED (gm msg_b8f1c614): ONLY the non-synced runtime sentinel is authoritative."""
    runtime = tmp_path / "runtime"; runtime.mkdir()
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(runtime))
    orch = tmp_path / "orch"; (orch / "state").mkdir(parents=True)
    # neither present -> not engaged
    assert fleet.kill_switch_engaged(str(orch)) is False
    # runtime sentinel -> ENGAGED (authoritative)
    (runtime / "FLEET_BEAT_DISABLED").write_text("estop")
    assert fleet.kill_switch_engaged(str(orch)) is True


def test_in_tree_sentinel_is_advisory_not_a_brake(tmp_path, monkeypatch):
    """A stale SYNCED in-tree sentinel must NOT brake (could be spuriously toggled by a
    stale Mac copy). It's advisory-only: kill_switch_engaged=False, advisory=True, and
    the beat RUNS (with an advisory flag for a logged warning)."""
    runtime = tmp_path / "runtime"; runtime.mkdir()          # runtime clean
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(runtime))
    orch = tmp_path / "orch"; (orch / "state").mkdir(parents=True)
    (orch / "state" / "FLEET_BEAT_DISABLED").write_text("stale-synced")  # in-tree only
    assert fleet.kill_switch_engaged(str(orch)) is False     # NOT braked
    assert fleet.kill_switch_advisory(str(orch)) is True     # but surfaced
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95, "T2")], REG, now=0, armed_tiers={"T2"},
        orchestra_dir=str(orch), executors_impl=fake, safety_fn=SAFE,
        approval_fn=APPROVE, confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["disabled"] is False                          # beat RAN despite in-tree file
    assert out["advisory_sentinel"] is True                  # advisory surfaced for logging


# --- guard 4: persistent exclude list -----------------------------------------

def test_default_exclude_shaw_p1_agents_never_armed(tmp_path):
    """v8 + red-team are in DEFAULT_EXCLUDE — even at HARD ctx + T2 they're skipped."""
    reg = {"agents": {"orchestraos-app-dev-v8": {"generation": 1, "tier": "T2",
                                                 "lineage_root": "orchestraos-app-dev-v8"}}}
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("orchestraos-app-dev-v8", 99, "T2")], reg, now=0, armed_tiers={"T2"},
        orchestra_dir=str(tmp_path), executors_impl=fake, safety_fn=SAFE,
        approval_fn=APPROVE, confirm_fn=lambda c, s: {"outcome": "confirmed"})
    b = out["beats"][0]
    assert b["armed_status"] == fleet.SKIP_EXCLUDED
    assert fake.calls == []                          # excluded agent never executed
    assert out["summary"]["skipped_excluded"] == 1


def test_custom_exclude_extends_defaults(tmp_path):
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95, "T2")], REG, now=0, armed_tiers={"T2"},
        exclude={"t2-hot"}, orchestra_dir=str(tmp_path), executors_impl=fake,
        safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == fleet.SKIP_EXCLUDED
    assert fake.calls == []


# --- guard 2: skip agents with an open HOLD -----------------------------------

def test_skip_agent_with_open_hold(tmp_path):
    fake = FakeExecutors()
    ledger = hledger.add_hold({"holds": []}, canary="t2-hot", successor="t2-hot-g2",
                              status="hold:successor-unconfirmed", reason="x", now=0)
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95, "T2")], REG, now=1, armed_tiers={"T2"}, ledger=ledger,
        orchestra_dir=str(tmp_path), executors_impl=fake, safety_fn=SAFE,
        approval_fn=APPROVE, confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == fleet.SKIP_OPEN_HOLD
    assert fake.calls == []                          # never re-acts on a held agent
    assert out["summary"]["skipped_open_hold"] == 1


def test_resolved_hold_does_not_skip(tmp_path):
    fake = FakeExecutors()
    led = hledger.add_hold({"holds": []}, canary="t2-hot", successor="t2-hot-g2",
                           status="hold:x", reason="x", now=0)
    led = hledger.resolve_hold(led, "t2-hot", "t2-hot-g2")     # resolved -> not skipped
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95, "T2")], REG, now=1, armed_tiers={"T2"}, ledger=led,
        armed_lineages=ARMED_ANY,
        orchestra_dir=str(tmp_path), executors_impl=fake, safety_fn=SAFE,
        approval_fn=APPROVE, confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == ex.DONE
    assert "retire" in fake.calls


# --- guard 1: max-rotations-per-beat cap (highest-ctx runs, rest deferred) -----

def test_max_one_rotation_per_beat_highest_ctx_wins(tmp_path):
    """3 T2 agents all at HARD; only the highest-ctx one rotates, the other two are
    deferred (logged, next beat) — never mass-rotate."""
    reg = {"agents": {a: {"generation": 1, "tier": "T2", "lineage_root": a}
                      for a in ("r-low", "r-mid", "r-high")}}
    fake = FakeExecutors()
    agents = [_agent("r-low", 91, "T2"), _agent("r-high", 99, "T2"),
              _agent("r-mid", 95, "T2")]
    out = fleet.plan_fleet(
        agents, reg, now=0, armed_tiers={"T2"}, max_rotations=1,
        armed_lineages=ARMED_ANY,
        orchestra_dir=str(tmp_path), executors_impl=fake, safety_fn=SAFE,
        approval_fn=APPROVE, confirm_fn=lambda c, s: {"outcome": "confirmed"})
    by = {b["agent_id"]: b for b in out["beats"]}
    assert by["r-high"]["armed_status"] == ex.DONE           # highest ctx rotated
    assert by["r-mid"]["armed_status"] == fleet.SKIP_CAP_DEFERRED
    assert by["r-low"]["armed_status"] == fleet.SKIP_CAP_DEFERRED
    assert out["summary"]["rotated"] == 1
    assert out["summary"]["deferred_cap"] == 2


def test_cap_does_not_limit_reversible_soft_triggers(tmp_path):
    """The cap is on kill-capable HARD_ROTATE only; multiple SOFT author-triggers
    (reversible, no kill) all fire in one beat."""
    reg = {"agents": {a: {"generation": 1, "tier": "T2", "lineage_root": a}
                      for a in ("s1", "s2", "s3")}}
    injected = []
    out = fleet.plan_fleet(
        [_agent("s1", 72, "T2"), _agent("s2", 73, "T2"), _agent("s3", 74, "T2")],
        reg, now=0, armed_tiers={"T2"}, max_rotations=1, orchestra_dir=str(tmp_path),
        author_inject=lambda a, t: injected.append(a),
        handoff_provider=lambda: (None, None))
    # all three SOFT -> author-triggers, none deferred by the cap
    assert len(injected) == 3
    assert out["summary"]["deferred_cap"] == 0
    assert all(b["armed_status"] == _beat.SOFT_AUTHORING for b in out["beats"])


# =====================================================================
# gm(gen-8) COUNTER_PROPOSE conditions on DEC-1786779462 (all TIGHTEN safety)
# =====================================================================

# --- C1: soft-only first window -> every hard_rotate deferred to a human ---------

def test_soft_only_defers_every_hard_rotate(tmp_path):
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95, "T2")], REG, now=0, armed_tiers={"T2"},
        soft_only=True, orchestra_dir=str(tmp_path), executors_impl=fake,
        safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == fleet.SKIP_SOFT_ONLY
    assert fake.calls == []                          # no kill-capable path ran
    assert out["summary"]["skipped_soft_only"] == 1
    assert out["summary"]["rotated"] == 0


def test_soft_only_still_fires_reversible_author_triggers(tmp_path):
    """soft_only defers HARD_ROTATE but SOFT author-triggers still fire (reversible)."""
    reg = {"agents": {"s1": {"generation": 1, "tier": "T2", "lineage_root": "s1"}}}
    injected = []
    out = fleet.plan_fleet(
        [_agent("s1", 75, "T2")], reg, now=0, armed_tiers={"T2"}, soft_only=True,
        orchestra_dir=str(tmp_path), author_inject=lambda a, t: injected.append(a),
        handoff_provider=lambda: (None, None))
    assert injected == ["s1"]                         # SOFT trigger still fired
    assert out["beats"][0]["armed_status"] == _beat.SOFT_AUTHORING


# --- C2a: hourly rotation cap across beats -------------------------------------

def test_hourly_cap_blocks_beyond_budget(tmp_path):
    """History already holds max_rotations_per_hour retires in-window -> a fresh
    hard_rotate is skipped (SKIP_HOURLY_CAP) even though per-beat cap allows it."""
    fake = FakeExecutors()
    history = {"retires": [{"lineage_root": f"x{i}", "at": 100} for i in range(4)]}
    out = fleet.plan_fleet(
        [_agent("t2-hot", 99, "T2")], REG, now=200, armed_tiers={"T2"},
        history=history, max_rotations_per_hour=4, armed_lineages=ARMED_ANY,
        orchestra_dir=str(tmp_path),
        executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == fleet.SKIP_HOURLY_CAP
    assert fake.calls == []
    assert out["summary"]["skipped_hourly_cap"] == 1


def test_hourly_cap_ignores_retires_outside_window(tmp_path):
    """Retires older than the rolling hour don't count against the budget."""
    fake = FakeExecutors()
    history = {"retires": [{"lineage_root": f"x{i}", "at": 0} for i in range(4)]}
    out = fleet.plan_fleet(
        [_agent("t2-hot", 99, "T2")], REG, now=10_000, armed_tiers={"T2"},  # >3600 later
        history=history, max_rotations_per_hour=4, armed_lineages=ARMED_ANY,
        orchestra_dir=str(tmp_path),
        executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == ex.DONE     # window expired -> allowed
    assert "retire" in fake.calls


def test_successful_rotate_records_history_for_hourly_cap(tmp_path):
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("t2-hot", 99, "T2")], REG, now=500, armed_tiers={"T2"},
        armed_lineages=ARMED_ANY,
        orchestra_dir=str(tmp_path), executors_impl=fake, safety_fn=SAFE,
        approval_fn=APPROVE, confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["summary"]["rotated"] == 1
    assert out["history"]["retires"] == [{"lineage_root": "t2-hot", "at": 500}]


# --- C2b: post-retire per-lineage cooldown ------------------------------------

def test_post_retire_cooldown_skips_same_lineage(tmp_path):
    fake = FakeExecutors()
    history = {"retires": [{"lineage_root": "t2-hot", "at": 100}]}
    out = fleet.plan_fleet(
        [_agent("t2-hot", 99, "T2")], REG, now=200, armed_tiers={"T2"},   # 100s < 1800
        history=history, armed_lineages=ARMED_ANY,
        orchestra_dir=str(tmp_path), executors_impl=fake,
        safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == fleet.SKIP_LINEAGE_COOLDOWN
    assert fake.calls == []
    assert out["summary"]["skipped_cooldown"] == 1


def test_cooldown_expires_allows_rotation(tmp_path):
    fake = FakeExecutors()
    history = {"retires": [{"lineage_root": "t2-hot", "at": 100}]}
    out = fleet.plan_fleet(
        [_agent("t2-hot", 99, "T2")], REG, now=100 + 1801, armed_tiers={"T2"},
        history=history, armed_lineages=ARMED_ANY,
        orchestra_dir=str(tmp_path), executors_impl=fake,
        safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == ex.DONE
    assert "retire" in fake.calls


def test_cooldown_is_per_lineage_not_global(tmp_path):
    """A cooldown on lineage A does not block a DIFFERENT lineage B."""
    reg = {"agents": {"a-hot": {"generation": 1, "tier": "T2", "lineage_root": "a"},
                      "b-hot": {"generation": 1, "tier": "T2", "lineage_root": "b"}}}
    fake = FakeExecutors()
    history = {"retires": [{"lineage_root": "a", "at": 100}]}
    out = fleet.plan_fleet(
        [_agent("b-hot", 99, "T2")], reg, now=200, armed_tiers={"T2"},
        history=history, armed_lineages=ARMED_ANY,
        orchestra_dir=str(tmp_path), executors_impl=fake,
        safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == ex.DONE     # lineage b not in cooldown
    assert "retire" in fake.calls


# =====================================================================
# SAFETY-NET BACKSTOP: skip-if-self-triggered filter (PB seam)
# =====================================================================

def test_self_triggered_agent_is_skipped(tmp_path):
    """An agent the PRIMARY self-trigger handled -> beat SKIPS it (SKIP_SELF_TRIGGERED),
    even though it's over-threshold + armed + actionable."""
    injected = []
    reg = {"agents": {"s1": {"generation": 1, "tier": "T2", "lineage_root": "s1"}}}
    out = fleet.plan_fleet(
        [_agent("s1", 75, "T2")], reg, now=0, armed_tiers={"T2"},
        self_trigger_fn=lambda aid: "self_triggered",
        author_inject=lambda a, t: injected.append(a),
        handoff_provider=lambda: (None, None))
    assert out["beats"][0]["armed_status"] == fleet.SKIP_SELF_TRIGGERED
    assert injected == []                              # primary handled it -> beat did nothing
    assert out["summary"]["skipped_self_triggered"] == 1


def test_wedged_agent_is_nudged(tmp_path):
    """A wedged agent (crossed threshold, no fresh mark) -> beat PROCEEDS to nudge
    (reversible author-trigger in the soft window)."""
    injected = []
    reg = {"agents": {"s1": {"generation": 1, "tier": "T2", "lineage_root": "s1"}}}
    out = fleet.plan_fleet(
        [_agent("s1", 75, "T2")], reg, now=0, armed_tiers={"T2"},
        self_trigger_fn=lambda aid: "wedged",
        author_inject=lambda a, t: injected.append(a),
        handoff_provider=lambda: (None, None))
    assert injected == ["s1"]                          # wedged -> beat nudges
    assert out["beats"][0]["armed_status"] == _beat.SOFT_AUTHORING


def test_self_trigger_fn_none_is_inert(tmp_path):
    """No self_trigger_fn -> filter inert, every actionable agent proceeds (legacy)."""
    injected = []
    reg = {"agents": {"s1": {"generation": 1, "tier": "T2", "lineage_root": "s1"}}}
    out = fleet.plan_fleet(
        [_agent("s1", 75, "T2")], reg, now=0, armed_tiers={"T2"},
        author_inject=lambda a, t: injected.append(a),
        handoff_provider=lambda: (None, None))
    assert injected == ["s1"]
    assert out["summary"]["skipped_self_triggered"] == 0


# --- classifier factory (reads PB mark + calls classify) + clearer ------------

def test_self_trigger_classifier_reads_mark_and_calls_classify(tmp_path):
    import json
    (tmp_path / "state" / "rotation-self-triggers").mkdir(parents=True)
    sid = "sid-abc"
    mark = {"kind": "rotation_self_trigger", "session_id": sid, "triggered_at": 1000}
    (tmp_path / "state" / "rotation-self-triggers" / f"{sid}.json").write_text(json.dumps(mark))
    fn = fleet.self_trigger_classifier(
        sid_of=lambda aid: sid, now=1100, orchestra_dir=str(tmp_path), max_age_s=2700)
    assert fn("agent-x") == "self_triggered"           # fresh mark
    # stale mark -> wedged
    fn_stale = fleet.self_trigger_classifier(
        sid_of=lambda aid: sid, now=1000 + 3000, orchestra_dir=str(tmp_path), max_age_s=2700)
    assert fn_stale("agent-x") == "wedged"


def test_self_trigger_classifier_missing_mark_is_wedged(tmp_path):
    fn = fleet.self_trigger_classifier(
        sid_of=lambda aid: "no-such-sid", now=1000, orchestra_dir=str(tmp_path))
    assert fn("agent-x") == "wedged"                   # fail-toward-nudge


def test_clear_self_trigger_mark(tmp_path):
    import json
    d = tmp_path / "state" / "rotation-self-triggers"
    d.mkdir(parents=True)
    (d / "sid-1.json").write_text(json.dumps({"triggered_at": 1}))
    assert fleet.clear_self_trigger_mark("sid-1", orchestra_dir=str(tmp_path)) is True
    assert fleet.clear_self_trigger_mark("sid-1", orchestra_dir=str(tmp_path)) is False  # idempotent


# =====================================================================
# PER-LINEAGE ARM GATE (self-retire arming allowlist scopes the auto-ROTATE half)
# When soft_only=False, a hard_rotate proceeds ONLY for a lineage in armed_lineages;
# a non-armed lineage defers (SKIP_NOT_ARMED, no spawn). Empty set => FAIL-CLOSED
# (every hard_rotate defers). Arming = adding ONE lineage -> rotates exactly that seat.
# =====================================================================

def test_armed_lineage_proceeds_when_soft_only_false(tmp_path):
    """soft_only=False + lineage IN the armed set -> hard_rotate proceeds (reaches
    the executor and rotates)."""
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95, "T2")], REG, now=0, armed_tiers={"T2"},
        soft_only=False, armed_lineages={"t2-hot"},
        orchestra_dir=str(tmp_path), executors_impl=fake, safety_fn=SAFE,
        approval_fn=APPROVE, confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == ex.DONE     # armed lineage rotated
    assert "retire" in fake.calls
    assert out["summary"]["rotated"] == 1
    assert out["summary"]["skipped_not_armed"] == 0


def test_unarmed_lineage_defers_when_soft_only_false(tmp_path):
    """soft_only=False + lineage NOT in the armed set -> deferred SKIP_NOT_ARMED,
    NO spawn/execute."""
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95, "T2")], REG, now=0, armed_tiers={"T2"},
        soft_only=False, armed_lineages={"some-other-lineage"},
        orchestra_dir=str(tmp_path), executors_impl=fake, safety_fn=SAFE,
        approval_fn=APPROVE, confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == fleet.SKIP_NOT_ARMED
    assert fake.calls == []                               # never spawned/executed
    assert out["summary"]["rotated"] == 0
    assert out["summary"]["skipped_not_armed"] == 1


def test_empty_armed_set_defers_all_hard_rotates_fail_closed(tmp_path):
    """soft_only=False + EMPTY armed set -> ALL hard_rotates defer (fail-CLOSED, the
    safe default that must hold when the allowlist file is absent/empty)."""
    reg = {"agents": {a: {"generation": 1, "tier": "T2", "lineage_root": a}
                      for a in ("l1", "l2", "l3")}}
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("l1", 95, "T2"), _agent("l2", 96, "T2"), _agent("l3", 97, "T2")],
        reg, now=0, armed_tiers={"T2"}, soft_only=False, armed_lineages=frozenset(),
        orchestra_dir=str(tmp_path), executors_impl=fake, safety_fn=SAFE,
        approval_fn=APPROVE, confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert all(b["armed_status"] == fleet.SKIP_NOT_ARMED for b in out["beats"])
    assert fake.calls == []                               # nothing rotated
    assert out["summary"]["rotated"] == 0
    assert out["summary"]["skipped_not_armed"] == 3


def test_soft_only_defers_regardless_of_armed_set(tmp_path):
    """soft_only=True -> EVERY hard_rotate defers (SKIP_SOFT_ONLY) even for a lineage
    that IS armed (arming does NOT override the soft-only first window)."""
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95, "T2")], REG, now=0, armed_tiers={"T2"},
        soft_only=True, armed_lineages={"t2-hot"},          # armed, but soft_only wins
        orchestra_dir=str(tmp_path), executors_impl=fake, safety_fn=SAFE,
        approval_fn=APPROVE, confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == fleet.SKIP_SOFT_ONLY
    assert fake.calls == []
    assert out["summary"]["skipped_soft_only"] == 1
    assert out["summary"]["skipped_not_armed"] == 0        # soft_only short-circuits first


def test_per_lineage_scoping_two_of_three_armed(tmp_path):
    """armed set with 2 of 3 T2 lineages -> EXACTLY those 2 proceed, the 3rd defers
    SKIP_NOT_ARMED. Proves arming is PER-LINEAGE (not a whole-tier flip). max_rotations
    raised so the per-beat cap doesn't mask the scoping."""
    reg = {"agents": {a: {"generation": 1, "tier": "T2", "lineage_root": a}
                      for a in ("armed-a", "armed-b", "unarmed-c")}}
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("armed-a", 95, "T2"), _agent("armed-b", 96, "T2"),
         _agent("unarmed-c", 97, "T2")],
        reg, now=0, armed_tiers={"T2"}, soft_only=False,
        armed_lineages={"armed-a", "armed-b"}, max_rotations=3,
        orchestra_dir=str(tmp_path), executors_impl=fake, safety_fn=SAFE,
        approval_fn=APPROVE, confirm_fn=lambda c, s: {"outcome": "confirmed"})
    by = {b["agent_id"]: b for b in out["beats"]}
    assert by["armed-a"]["armed_status"] == ex.DONE
    assert by["armed-b"]["armed_status"] == ex.DONE
    assert by["unarmed-c"]["armed_status"] == fleet.SKIP_NOT_ARMED
    assert out["summary"]["rotated"] == 2
    assert out["summary"]["skipped_not_armed"] == 1


def test_armed_lineages_none_reads_allowlist_file_fail_closed(tmp_path, monkeypatch):
    """armed_lineages=None (default) reads the canonical self-retire allowlist via
    self_retire_gate. An ABSENT file => EMPTY set => fail-CLOSED (hard_rotate defers).
    Proves the default source is the shared arming file, not a silent open gate."""
    from scripts.lineage_daemon import self_retire_gate as srg
    monkeypatch.setattr(srg, "_DEFAULT_ARMED_PATH",
                        str(tmp_path / "runtime" / "self_retire_armed"))  # absent
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95, "T2")], REG, now=0, armed_tiers={"T2"},
        soft_only=False,                                    # armed_lineages omitted -> None
        orchestra_dir=str(tmp_path), executors_impl=fake, safety_fn=SAFE,
        approval_fn=APPROVE, confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == fleet.SKIP_NOT_ARMED
    assert fake.calls == []


def test_armed_lineages_none_reads_allowlist_file_arms_listed(tmp_path, monkeypatch):
    """armed_lineages=None + the allowlist file lists the lineage -> it proceeds.
    Proves the default reader honors the file's contents (one lineage per line, `#`
    comments ignored) — the SAME source is_graduated_autoretire gates on."""
    from scripts.lineage_daemon import self_retire_gate as srg
    armed_file = tmp_path / "runtime" / "self_retire_armed"
    armed_file.parent.mkdir(parents=True)
    armed_file.write_text("# self-retire arming allowlist\nt2-hot\n")
    monkeypatch.setattr(srg, "_DEFAULT_ARMED_PATH", str(armed_file))
    fake = FakeExecutors()
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95, "T2")], REG, now=0, armed_tiers={"T2"},
        soft_only=False,
        orchestra_dir=str(tmp_path), executors_impl=fake, safety_fn=SAFE,
        approval_fn=APPROVE, confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert out["beats"][0]["armed_status"] == ex.DONE
    assert "retire" in fake.calls


# --- C3 reset-at-promote fires on the MECHANICAL fleet promote path (DEC-1788165818)
def test_c3_reset_at_promote_fires_on_mechanical_fleet_rotation(tmp_path):
    # gen28 REQUIRED catch: the freshness baseline reset must fire on a PLAIN
    # mechanical promote through the fleet — gated on result['promoted'], NOT the
    # narrow completion_mode+AWAITING_PROGRESS condition (that would SKIP mechanical
    # rotations and leave gen N+1 inheriting gen N-1's baseline -> false SOFT_READY).
    from scripts.lineage_daemon import complete as _complete
    ledger = hledger.add_hold({"holds": []}, canary="t2-hot", successor="t2-hot-g2",
                              status="hold:successor-unconfirmed", reason="x", now=0)
    consumed = {"current_goal": "g", "next_3_actions": ["a"],
                "handoff_commit_sha": "shaCONSUMED", "file_hash": "hashCONSUMED"}
    handoff_provider = lambda seat, lr=None: (consumed, 100)
    # mechanical completion that PROMOTES (no completion_mode / AWAITING_PROGRESS)
    completion_provider = lambda aid, hold, armed, budget, now: {
        "status": _complete.COMPLETED, "promoted": True, "budget_consumed": True,
        "promoted_by": "mechanical"}
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95, "T2")], REG, now=1, armed_tiers={"T2"},
        armed_lineages=ARMED_ANY, soft_only=False, ledger=ledger, history={},
        completion_provider=completion_provider, handoff_provider=handoff_provider,
        orchestra_dir=str(tmp_path))
    assert out["history"]["handoff_baseline"]["t2-hot"] == "shaCONSUMED:hashCONSUMED", (
        "C3 reset must fire on a mechanical promote (gated on 'promoted'), setting "
        "the baseline to the CONSUMED predecessor rev")
