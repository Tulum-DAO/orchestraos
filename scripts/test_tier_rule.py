"""Tier is hierarchy position; role says what a seat does (DEC-1791574633518521).

The rule table, then BY EFFECT: the real spawn-agent.sh registering a new agent. Its tmux is a shim
that refuses `new-session`, so each spawn stops right after registration and the test reads what
was written. HOME is a temp dir (spawn records a CLI trust entry there), and $TMUX is unset.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import tier_rule as T  # noqa: E402


# ---------------------------------------------------------------- the rule

@pytest.mark.parametrize("parent,known,ptier,requested,want", [
    ("", False, "", "", ("T1", None)),            # no parent: a lead
    ("helper", False, "", "", ("T1", None)),      # the agent itself as parent counts as none
    ("gm", True, "T0", "", ("T1", "gm")),         # under a T0: a lead
    ("lead", True, "T1", "", ("T2", "lead")),     # under a lead: its worker
    ("w", True, "T2", "", ("T2", "w")),
    ("gm", True, "T0", "T3", ("T3", "gm")),       # explicit T0/T1/T3 kept
    ("lead", True, "T1", "T2", ("T2", "lead")),   # explicit T2 with a lead: fine
])
def test_tier_is_position(parent, known, ptier, requested, want):
    tier, reports_to, warning = T.tier_for("helper", parent, known, ptier, requested)
    assert (tier, reports_to) == want and warning is None


def test_a_parent_with_no_readable_tier_gets_a_T2_and_a_warning():
    for ptier in ("", None, "None", "boss"):
        tier, reports_to, warning = T.tier_for("w1", "lead", True, ptier)
        assert (tier, reports_to) == ("T2", "lead") and "no readable tier" in warning


def test_refusals():
    with pytest.raises(T.TierRefused, match="not a registered agent"):
        T.tier_for("o", "nobody", False, "")
    with pytest.raises(T.TierRefused, match="a T2 needs a parent that is not a T0"):
        T.tier_for("o", "", False, "", "T2")
    with pytest.raises(T.TierRefused, match="a T2 needs a parent that is not a T0"):
        T.tier_for("o", "gm", True, "T0", "t2")
    with pytest.raises(T.TierRefused, match="unknown tier"):
        T.tier_for("o", "", False, "", "T9")


@pytest.mark.parametrize("row,want", [
    ({"role": "pm", "tier": "T2"}, "pm"),
    ({"role": "worker", "tier": "T1"}, "worker"),
    ({"tier": "T1"}, "pm"),                  # a row from before roles: a T1 was the PM
    ({"tier": "T2"}, "worker"),
    ({"tier": "T0"}, "worker"),
    ({"role": "None", "tier": "T1"}, "pm"),  # get_agent_field prints JSON null as None
    ({}, "worker"),
    (None, "worker"),
])
def test_role_of(row, want):
    assert T.role_of(row) == want


def test_an_unknown_stored_role_reads_as_worker_with_a_warning(capsys):
    assert T.role_of({"name": "x", "role": "manager", "tier": "T1"}) == "worker"
    assert "unknown role 'manager'" in capsys.readouterr().err


def test_an_unknown_AGENT_ROLE_is_refused():
    r = subprocess.run([sys.executable, str(HERE / "tier_rule.py"), "decide", "h", "--role", "boss"],
                       capture_output=True, text=True)
    assert r.returncode == 3 and "unknown role 'boss'" in r.stderr


def test_cli_fields_keep_an_empty_reports_to():
    out = subprocess.run([sys.executable, str(HERE / "tier_rule.py"), "decide", "h", "--fields"],
                         capture_output=True, text=True, check=True).stdout.rstrip("\n")
    assert out.split("\x1f") == ["T1", "", "worker", ""]


# ---------------------------------------------------------------- by effect: spawn-agent.sh

@pytest.fixture
def spawn(tmp_path):
    d = Path(tempfile.mkdtemp(prefix="tsp.", dir="/tmp"))
    data, bindir, home = d / "data", d / "bin", d / "home"
    for p in (data / "state", bindir, home):
        p.mkdir(parents=True)
    (data / "registry.json").write_text(json.dumps({"agents": {
        "gm": {"name": "gm", "tier": "T0", "runtime": "claude", "tmux_session": "gm"},
        "lead": {"name": "lead", "tier": "T1", "role": "pm", "runtime": "claude", "tmux_session": "lead"},
    }}))
    shim = bindir / "tmux"
    shim.write_text('#!/bin/sh\ncase "$1" in new-session|has-session) exit 1;; *) exit 0;; esac\n')
    shim.chmod(0o755)

    def run(agent, **env_extra):
        env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "PARENT_AGENT_ID", "AGENT_TIER",
                                                              "AGENT_ROLE", "IDENTITY_STORE_CUTOVER")}
        env.update(PATH=f"{bindir}:{env['PATH']}", HOME=str(home), ORCHESTRA_DIR=str(data),
                   AGENT_RUNTIME="claude", AGENT_MODEL="claude-opus-4-8", **env_extra)
        r = subprocess.run(["bash", str(ROOT / "spawn-agent.sh"), agent], env=env,
                           capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        row = json.loads((data / "registry.json").read_text())["agents"].get(agent)
        return r, row
    yield run
    shutil.rmtree(d, ignore_errors=True)


def test_BY_EFFECT_a_parentless_helper_registers_as_a_T1_worker(spawn):
    r, row = spawn("helper-a")
    assert row is not None, r.stderr
    assert row["tier"] == "T1" and row["role"] == "worker" and "reports_to" not in row


def test_BY_EFFECT_under_gm_a_lead_under_a_lead_a_worker(spawn):
    _, row = spawn("planner", PARENT_AGENT_ID="gm")
    assert (row["tier"], row["reports_to"], row["role"]) == ("T1", "gm", "worker")
    _, row = spawn("dev-b", PARENT_AGENT_ID="lead")
    assert (row["tier"], row["reports_to"], row["role"]) == ("T2", "lead", "worker")
    _, row = spawn("pm-c", PARENT_AGENT_ID="gm", AGENT_ROLE="pm")
    assert (row["tier"], row["role"]) == ("T1", "pm")


def test_BY_EFFECT_refusals_register_nothing(spawn):
    r, row = spawn("orphan", PARENT_AGENT_ID="nobody")
    assert r.returncode == 3 and row is None and "not a registered agent" in r.stdout + r.stderr
    r, row = spawn("loner", AGENT_TIER="T2")
    assert r.returncode == 3 and row is None and "a T2 needs a parent" in r.stdout + r.stderr
    r, row = spawn("odd", AGENT_ROLE="boss")
    assert r.returncode == 3 and row is None and "unknown role 'boss'" in r.stdout + r.stderr


def test_the_pm_briefing_keys_on_role_not_tier():
    """spawn-agent.sh runs pm-startup.sh only when tier_rule's role says pm."""
    src = (ROOT / "spawn-agent.sh").read_text()
    assert 'if [[ "$seat_role" == "pm" && -x "$SCRIPT_DIR/pm-startup.sh" ]]' in src
    assert '"$tier" == "T1" && -x "$SCRIPT_DIR/pm-startup.sh"' not in src
    role = lambda *a: subprocess.run([sys.executable, str(HERE / "tier_rule.py"), "role", *a],  # noqa: E731
                                     capture_output=True, text=True, check=True).stdout.strip()
    assert role("--tier", "T1", "--role", "worker") == "worker"      # parentless helper: no briefing
    assert role("--tier", "T1") == "pm"                              # a pre-role T1 PM keeps it
    assert role("--tier", "T2", "--role", "pm") == "pm"


# ---------------------------------------------------------------- one reader (gm condition a)

def _code_lines(pattern):
    out = subprocess.run(["git", "grep", "-nE", pattern, "--", "*.py", "*.sh", ":!*test*", ":!dashboard", ":!api"],
                         cwd=str(ROOT), capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def test_only_role_of_decides_who_is_a_pm():
    """Every 'is this seat a PM' comparison goes through tier_rule.role_of (spawn-agent.sh compares
    $seat_role, which it gets from `tier_rule.py role`). A second reader is how the rule forks."""
    allowed = ("scripts/tier_rule.py:", "scripts/profile_switcher.py:")   # am/pm clock parsing
    bad = [line for line in _code_lines(r"""[=!]= *['"]pm['"]|in \(['"]pm['"]""")
           if not line.startswith(allowed) and "role_of(" not in line and "$seat_role" not in line
           and "template ==" not in line]          # `--template pm` CHOOSES the role at create
    assert not bad, "a PM check that does not go through role_of:\n" + "\n".join(bad)


def test_nothing_keys_behaviour_on_tier_T1():
    """T1 is position only. Comparing a tier to T1 outside tier_rule.py is the coupling this removed
    (the PM briefing, auto-retire and Arturo's PM pick all keyed on it)."""
    bad = [line for line in _code_lines(r"""(tier|TIER).{0,40}(==|!=).{0,6}['"]T1['"]|\$tier" == "T1""")
           if not line.startswith("scripts/tier_rule.py:")
           # fleet._armed: the T1 test is immediately decided by role_of (gm ruling on #348)
           and not (line.startswith("scripts/lineage_daemon/fleet.py:") and '"T2" in armed_tiers' in line)]
    assert not bad, "behaviour keyed on tier T1:\n" + "\n".join(bad)


def test_rotation_arming_goes_through_fleet_armed():
    """gm ruling on #348: arming is (tier in the wave) OR (T2 armed AND a T1 worker), via role_of.
    A bare `tier in armed_tiers` anywhere else would quietly drop the T1 helpers again."""
    bad = [line for line in _code_lines(r"(if|elif|return|and|or|=) .{0,60}tier.{0,40}(not )?in (armed_tiers|ARMED_TIERS)")
           if not (line.startswith("scripts/lineage_daemon/fleet.py:") and "if tier in armed_tiers" in line)]
    assert not bad, "a tier-only arming check outside fleet._armed:\n" + "\n".join(bad)
