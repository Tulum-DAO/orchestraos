"""Permission-filter for the durable menu bridge (spec:
.workspace/proposals/permission-filter-durable-bridge-spec.md, incident
<card-id>).

A `pending_menu` capture whose D1 detector kind == 'permission' must NEVER be
durably bridged: its sole surface is the D3 read-time pseudo-row (`perm:` id,
/agent-key transport), which vanishes with the prompt and is structurally
immune to stale answers. The filter is strictly KIND-keyed at ingress
(menu_bridge_core.should_bridge — the single gate every durable caller
passes), NOT shape-keyed: a perm_shaped AskUserQuestion (kind='options',
a prior decision Q1) must keep bridging exactly as today.

Hermetic: real committed D1 fixtures (fixtures/menus/), fake store, no tmux,
no network, instance ledger repointed to tmp_path. No prod state touched.
"""
import importlib
import importlib.util
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

core = importlib.import_module("menu_bridge_core")
mb = importlib.import_module("menu_bridge")

_spec = importlib.util.spec_from_file_location(
    "agent_status", os.path.join(_HERE, "agent-status.py"))
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)


def _real(name):
    raw = open(os.path.join(_HERE, "fixtures", "menus", name)).read()
    return A.parse_status(raw)["pending_menu"]


class _FakeStore:
    def __init__(self):
        self.created = []
        self.db_path = ":memory:"

    def create(self, **kw):
        self.created.append(kw)
        return f"apr_{len(self.created)}"


def _perm_shaped_auq():
    """A permission-SHAPED AskUserQuestion: kind='options' + perm_shaped hint
    . MUST keep bridging durably — the filter keys on the
    D1 classifier's kind only, never on shape."""
    lines = [" Do you want to proceed with the deployment?",
             " ❯ 1. Yes, deploy",
             "   2. No, hold",
             "   Enter to select · ↑↓ to navigate"] + [""] * 14
    m = A.parse_pending_menu(lines, now=1786400000.0)
    assert m["kind"] == "options" and m.get("perm_shaped") is True  # precondition
    return m


# ---------------------------------------------------------------------------
# (i) UNIT — should_bridge refuses kind='permission' at ingress
# ---------------------------------------------------------------------------

def test_should_bridge_false_for_permission_kind():
    cap = {"kind": "permission", "chrome": "allow_deny",
           "question": "Do you want to proceed? Bash(git status)",
           "options": [{"n": "1", "label": "Yes"}, {"n": "2", "label": "No"}]}
    assert core.should_bridge("working", cap, 0.0, 100.0, debounce_s=0.0) is False


# The 4 REAL native permission prompts from the D1 empirical matrix (one per
# tool, live Claude Code captures): all must be refused at ingress.
@pytest.mark.parametrize("fixture", [
    "perm_edit_make.pane.txt",     # Edit
    "perm_write.pane.txt",         # Write
    "perm_bash.pane.txt",          # Bash
    "perm_webfetch.pane.txt",      # WebFetch
])
def test_d1_real_permission_fixture_never_bridges(fixture):
    m = _real(fixture)
    assert m is not None and m["kind"] == "permission"  # precondition (D1 matrix)
    assert core.should_bridge("working", m, 0.0, 100.0, debounce_s=0.0) is False


# The remaining D1 fixtures that ARE menus must keep bridging (kind-keyed
# filter over-reach guard): options-classed captures are untouched.
@pytest.mark.parametrize("fixture", [
    "planmode_options.pane.txt",
    "authored_askuser_run_verb.pane.txt",
    "askuserquestion_long_sublines.pane.txt",
    "askuserquestion_tall_narrow.pane.txt",
])
def test_d1_real_options_fixture_still_bridges(fixture):
    m = _real(fixture)
    assert m is not None and m["kind"] == "options"     # precondition (D1 matrix)
    assert core.should_bridge("working", m, 0.0, 100.0, debounce_s=0.0) is True


def test_d1_real_prose_narration_still_no_menu():
    # 9th D1 fixture: prose narration fabricates NO menu at all — unchanged.
    raw = open(os.path.join(_HERE, "fixtures", "menus",
                            "prose_perm_narration.pane.txt")).read()
    m = A.parse_status(raw)["pending_menu"]
    assert m is None
    assert core.should_bridge("working", m, 0.0, 100.0, debounce_s=0.0) is False


def test_should_bridge_legacy_kindless_capture_unchanged():
    # A capture with NO kind key (defensive: pre-D1 shape) bridges as today —
    # the filter is fail-safe-by-absence, never a lost surface.
    cap = {"question": "Pick one", "options": [{"n": "1", "label": "A"}]}
    assert core.should_bridge("working", cap, 0.0, 100.0, debounce_s=0.0) is True


# ---------------------------------------------------------------------------
# (ii) REGRESSION — perm_shaped AUQ (kind='options') MUST keep bridging
# ---------------------------------------------------------------------------

def test_perm_shaped_auq_still_passes_gate():
    m = _perm_shaped_auq()
    assert core.should_bridge("working", m, 0.0, 100.0, debounce_s=0.0) is True


def test_perm_shaped_auq_still_bridges_durably(monkeypatch):
    # By effect through the cron: the perm-SHAPED (but kind='options') AUQ
    # lands in the ledger with the perm_shaped hint intact.
    monkeypatch.setattr(mb, "ledger_pending_op_keys", lambda store, db_path=None: set())
    monkeypatch.setattr(mb, "reconcile_orphans", lambda *a, **k: [])
    store = _FakeStore()
    fleet = {"worker-1": {"state": "working", "pending_menu": _perm_shaped_auq()}}
    res = mb.run_cron_cycle(store, fleet, now=100.0)
    assert len(store.created) == 1
    assert store.created[0]["kind"] == "menu"
    assert store.created[0]["menu"].get("perm_shaped") is True
    assert len(res["bridged"]) == 1


# ---------------------------------------------------------------------------
# (iii) BY-EFFECT — real D1 permission fixture through run_cron_cycle:
#       ZERO ledger writes, and the D3 pseudo-row still renders the SAME prompt
# ---------------------------------------------------------------------------

def test_cron_cycle_writes_nothing_for_real_permission_prompt(monkeypatch, tmp_path):
    m = _real("perm_bash.pane.txt")
    assert m is not None and m["kind"] == "permission"  # precondition

    monkeypatch.setattr(mb, "ledger_pending_op_keys", lambda store, db_path=None: set())
    monkeypatch.setattr(mb, "reconcile_orphans", lambda *a, **k: [])
    store = _FakeStore()
    fleet = {"worker-1": {"state": "working", "pending_menu": m}}
    res = mb.run_cron_cycle(store, fleet, now=100.0)
    assert store.created == []                          # ZERO durable rows minted
    assert res["bridged"] == []

    # The SAME prompt stays visible via the D3 read-time pseudo-row (skip-from-
    # durable, NOT drop-to-invisible). Instance ledger repointed to tmp_path so
    # no prod state is touched.
    wg = importlib.import_module("watch_gateway")
    monkeypatch.setattr(wg, "_PERM_INSTANCE_LEDGER",
                        str(tmp_path / "perm-instance-ledger.json"))
    row = wg._perm_pseudo_row("worker-1", m)
    assert row["kind"] == "permission"
    assert row["id"].startswith("perm:worker-1:")
    assert row["question"] == m["question"]
    assert row["options"]                               # answerable options present


def test_would_surface_shadow_excludes_permission_prompt(monkeypatch, tmp_path):
    # The shadow/would-surface path uses the same gate — a parked permission
    # prompt must not be reported as would-bridge either.
    store = _FakeStore()
    m = _real("perm_edit_make.pane.txt")
    res = mb.run_pickup_cron(
        store, {"worker-1": {"state": "working", "pending_menu": m}}, now=100.0,
        cfg={"enabled": True, "skip_sessions": [], "skip_prefixes": []},
        killswitch_path=str(tmp_path / "nope"), shadow=True)
    assert res["would_surface"] == []
    assert store.created == []
