"""Onboarding step 'hierarchy' (DEC-1790097700552573, both peers APPROVE on v2).

The operator must learn the tiers exist and be able to say yes to a manager. The rules the peers
made binding: the T0 check is SERVER-side with three states (never two), a refusal never records a
spawn, and an unset `kind` leaves the worker path byte-identical.
"""
import importlib.util
import json
import pathlib


def _load_proxy():
    spec = importlib.util.spec_from_file_location("arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _registry(tmp_path, agents):
    (tmp_path / "registry.json").write_text(json.dumps({"agents": agents}))
    return tmp_path


# ---- the directive ------------------------------------------------------------------------
def test_directive_offers_the_manager_when_none_exists():
    from services.arturo import onboarding as onb
    d = onb.directive("hierarchy", {"manager": None, "manager_known": True, "seat": "scout"})
    assert "T0" in d and "T1" in d and "T2" in d
    assert "scout" in d                       # names the seat they just created
    assert "always on" in d.lower()           # the honest cost sentence
    assert "one question" in d.lower()


def test_directive_does_not_offer_a_second_manager():
    from services.arturo import onboarding as onb
    d = onb.directive("hierarchy", {"manager": "gm", "manager_known": True, "seat": "scout"})
    assert "gm" in d
    assert "do not offer" in d.lower()


def test_an_unreadable_registry_is_a_third_state_explain_and_do_not_offer():
    from services.arturo import onboarding as onb
    d = onb.directive("hierarchy", {"manager": None, "manager_known": False, "seat": "scout"})
    assert "do not offer" in d.lower()        # unknown is NOT "no manager"


def test_an_unknown_step_still_carries_no_directive():
    from services.arturo import onboarding as onb
    assert onb.directive("nope", {"manager": None}) == ""
    assert onb.directive("name") != ""        # the old one-arg call still works


# ---- the server-side manager lookup -------------------------------------------------------
def test_existing_manager_is_found_by_tier_not_by_name(tmp_path):
    mod = _load_proxy()
    mod.ORCHESTRA_DIR = str(_registry(tmp_path, {"boss": {"tier": "T0"}, "w": {"tier": "T2"}}))
    assert mod.existing_manager() == ("boss", True)


def test_no_manager_is_reported_as_known_and_absent(tmp_path):
    mod = _load_proxy()
    mod.ORCHESTRA_DIR = str(_registry(tmp_path, {"w": {"tier": "T2"}}))
    assert mod.existing_manager() == (None, True)


def test_an_unreadable_registry_reports_unknown_not_absent(tmp_path):
    mod = _load_proxy()
    mod.ORCHESTRA_DIR = str(tmp_path / "nothing-here")
    assert mod.existing_manager() == (None, False)


# ---- the tool ------------------------------------------------------------------------------
def test_spawn_agent_schema_has_kind_defaulting_to_worker():
    mod = _load_proxy()
    fn = next(t["function"] for t in mod.TOOLS if t["function"]["name"] == "spawn_agent")
    kind = fn["parameters"]["properties"]["kind"]
    assert set(kind["enum"]) == {"worker", "manager"}
    assert "kind" not in fn["parameters"].get("required", [])


def test_manager_plan_uses_orchestra_spawn_gm_and_pins_the_data_dir():
    mod = _load_proxy()
    plan = mod.manager_plan("gm")
    assert plan.argv[-2:] == ["gm", "--gm"]
    assert plan.argv[0].endswith("/bin/orchestra") and plan.argv[1] == "spawn"
    assert plan.env["ORCHESTRA_DIR"] == str(mod.ORCHESTRA_DIR)


# ---- the execute branch --------------------------------------------------------------------
def _stub_run(mod, ok=True, out="seat up"):
    calls = []
    mod._run_commission = lambda plan, t=120: (calls.append(plan) or (ok, out))
    mod.run_local = lambda cmd, timeout=3: (True, "")
    mod._file_commission_row = lambda *a, **k: "msg_x"
    mod.record_spawned_session = lambda *a, **k: None
    mod._notify_spawned = lambda *a, **k: None
    return calls


def test_manager_kind_goes_through_orchestra_spawn_gm(tmp_path):
    mod = _load_proxy()
    mod.ORCHESTRA_DIR = str(_registry(tmp_path, {"w": {"tier": "T2"}}))
    calls = _stub_run(mod)
    token = mod._SPAWNED_THIS_TURN.set([])
    try:
        out = mod.execute_tool("spawn_agent", {"session_name": "gm", "kind": "manager", "machine": "vps"})
        assert calls and calls[0].argv[1] == "spawn" and calls[0].argv[-1] == "--gm"
        assert mod._SPAWNED_THIS_TURN.get() == ["gm"]       # a verified manager IS a spawn
        assert "FAILED" not in out
    finally:
        mod._SPAWNED_THIS_TURN.reset(token)


def test_a_second_manager_is_refused_in_words_and_records_no_spawn(tmp_path):
    mod = _load_proxy()
    mod.ORCHESTRA_DIR = str(_registry(tmp_path, {"boss": {"tier": "T0"}}))
    calls = _stub_run(mod)
    token = mod._SPAWNED_THIS_TURN.set([])
    try:
        out = mod.execute_tool("spawn_agent", {"session_name": "gm2", "kind": "manager", "machine": "vps"})
        assert calls == []                                   # nothing was spawned
        assert mod._SPAWNED_THIS_TURN.get() == []            # and the client must not advance
        assert "boss" in out and "one" in out.lower()
    finally:
        mod._SPAWNED_THIS_TURN.reset(token)


def test_a_refusing_cli_reports_its_own_stderr_and_records_no_spawn(tmp_path):
    mod = _load_proxy()
    mod.ORCHESTRA_DIR = str(_registry(tmp_path, {}))
    _stub_run(mod, ok=False, out="refusing to spawn: no enabled runtime is installed AND logged in.")
    token = mod._SPAWNED_THIS_TURN.set([])
    try:
        out = mod.execute_tool("spawn_agent", {"session_name": "gm", "kind": "manager", "machine": "vps"})
        assert "FAILED" in out and "no enabled runtime" in out
        assert mod._SPAWNED_THIS_TURN.get() == []
    finally:
        mod._SPAWNED_THIS_TURN.reset(token)


def test_without_kind_the_worker_path_is_untouched(tmp_path):
    mod = _load_proxy()
    mod.ORCHESTRA_DIR = str(_registry(tmp_path, {}))
    calls = _stub_run(mod)
    mod.execute_tool("spawn_agent", {"session_name": "scout", "task": "hi", "machine": "vps"})
    assert calls and calls[0].argv[0] == "bash" and calls[0].argv[1].endswith("spawn-agent.sh")
