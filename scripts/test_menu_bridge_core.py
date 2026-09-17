"""Tests for the menu bridge multi-part hydration (P1 field regression, a prior decision).

The bridged approval row must carry the FULL multi-part payload (parts[] +
walk_complete:true), else the client fail-safes to the raw view = the old broken
thing (the operator field regression 2026-08-16, <card-id>). Two fixes:
  (1) build_menu_payload (pure) propagates the multi-part keys it was dropping.
  (2) bridge_one runs the active capture walk to hydrate parts[] when the passive
      capture is a multi-part menu that isn't yet walk_complete.
"""
import importlib, sys, os
sys.path.insert(0, os.path.dirname(__file__))
core = importlib.import_module("menu_bridge_core")


def _multipart_capture(walk_complete=False):
    return {
        "question": "Which surfaces?",
        "options": [{"n": "1", "label": "iOS", "checked": False},
                    {"n": "2", "label": "Watch", "checked": True}],
        "selected_n": None, "checkbox": True, "has_submit": True,
        "tabs": ["Surfaces", "Priority", "Submit"], "submit_tab_index": 2,
        "multipart": True, "part_count": 2, "part_index": 0,
        "walk_complete": walk_complete,
        "parts": [{"index": 0, "tab_label": "Surfaces", "question": "Which surfaces?",
                   "select": "multi", "has_free_text": True,
                   "options": [{"n": "1", "label": "iOS", "checked": False},
                               {"n": "2", "label": "Watch", "checked": True}]}],
    }


# --- (1) pure payload propagation ---------------------------------------------

def test_build_menu_payload_propagates_multipart_keys():
    p = core.build_menu_payload(_multipart_capture(walk_complete=True), "sess")
    assert p["multipart"] is True
    assert p["part_count"] == 2
    assert p["walk_complete"] is True
    assert p["part_index"] == 0
    assert isinstance(p["parts"], list) and p["parts"][0]["select"] == "multi"

def test_build_menu_payload_single_select_has_no_multipart_keys():
    cap = {"question": "Q", "options": [{"n": "1", "label": "A"}],
           "selected_n": "1"}
    p = core.build_menu_payload(cap, "sess")
    for k in ("multipart", "part_count", "parts", "walk_complete", "part_index"):
        assert k not in p          # additive: unchanged for non-multipart menus

def test_build_menu_payload_does_not_mutate_capture():
    cap = _multipart_capture(walk_complete=True)
    core.build_menu_payload(cap, "sess")
    assert "source_session" not in cap      # purity preserved

def test_build_menu_payload_propagates_perm_shaped(monkeypatch):
    # perm-card lane : perm_shaped rides through to the card
    # payload so the surface renders Approve-styled verbs. Additive hint.
    cap = {"question": "Do you want to proceed?",
           "options": [{"n": "1", "label": "Yes, deploy"}, {"n": "2", "label": "No"}],
           "selected_n": "1", "perm_shaped": True}
    p = core.build_menu_payload(cap, "sess")
    assert p["perm_shaped"] is True

def test_build_menu_payload_omits_perm_shaped_when_absent():
    cap = {"question": "Which file?", "options": [{"n": "1", "label": "a.py"}],
           "selected_n": "1"}
    p = core.build_menu_payload(cap, "sess")
    assert "perm_shaped" not in p            # additive: absent on ordinary menus


# --- (2) bridge hydration decision (pure predicate) ---------------------------

def test_needs_hydration_true_for_incomplete_multipart():
    assert core.needs_hydration(_multipart_capture(walk_complete=False)) is True

def test_needs_hydration_false_when_already_complete():
    assert core.needs_hydration(_multipart_capture(walk_complete=True)) is False

def test_needs_hydration_false_for_single_part():
    assert core.needs_hydration({"question": "Q", "options": [],
                                 "walk_complete": True}) is False
    assert core.needs_hydration({"question": "Q", "options": []}) is False

def test_needs_hydration_false_on_none():
    assert core.needs_hydration(None) is False


def test_registry_scoped_sessions_only_scans_registered_seats(tmp_path):
    """tmux is host-global: unregistered sessions (another install, a stray shell) are never scanned."""
    import json, menu_bridge as mb
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"agents": {"gm": {"tmux_session": "gm"}, "planner": {"tmux_session": "planner-pane"}}}))
    got = mb.registry_scoped_sessions(["gm", "planner-pane", "stray", "other-install-gm"], registry_path=str(reg))
    assert got == ["gm", "planner-pane"]
    assert mb.registry_scoped_sessions(["gm"], registry_path=str(tmp_path / "missing.json")) == []
