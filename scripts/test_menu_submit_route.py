"""POST /menu-submit — the approve-scoped multi-part submit.

Drives the REAL handler. The orchestrator is a spy where the test is about the ROUTE's own
gates (confirm, arm, own-words arm, card binding, codes), and the REAL durable_first_batch_submit
where the test is about what must NEVER happen (a pane-only replay with no durable anchor).
"""
import os, sys, json, asyncio, pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_gateway as G

ANSWERS = [{"part": 0, "ns": ["1"]}, {"part": 1, "ns": ["2", "3"]}]


class _Req:
    def __init__(self, payload, headers=None):
        self.headers = headers if headers is not None else {"Authorization": "Bearer secret_tok"}
        self._payload = payload
        self.query = {}
        self.match_info = {}

    async def json(self):
        return self._payload


MENU = {"walk_complete": True, "part_count": 2, "parts": [
    {"index": 0, "question": "Lock the API down now?", "select": "single",
     "options": [{"n": 1, "label": "Yes"}, {"n": 2, "label": "No"}, {"n": 3, "label": "Type something."}]},
    {"index": 1, "question": "Which dashboards keep access?", "select": "multi",
     "options": [{"n": 1, "label": "A"}, {"n": 2, "label": "B"}, {"n": 3, "label": "C"},
                 {"n": 4, "label": "Type something."}]},
]}


def _row(rid="apr_1"):
    return {"id": rid, "kind": "menu", "status": "pending", "from_agent": "gm", "menu": json.dumps(MENU)}


class _Store:
    def __init__(self, row=None, latest=None):
        self.row, self.latest = row, latest
        self.recorded = []
        self.acked = []
        self.on_lookup = None          # hook: runs on the orchestrator's own lookup (race tests)

    def migrate(self):
        pass

    def pending_menu_row_for_session(self, session):
        if self.on_lookup:
            hook, self.on_lookup = self.on_lookup, None
            hook(self)
        return self.row

    def latest_menu_row_for_session(self, session):
        return self.latest

    def record_batch_answer(self, rid, answers, surface=None, answered_by=None, device=None):
        self.recorded.append({"id": rid, "answers": answers, "surface": surface,
                              "answered_by": answered_by, "device": device})
        return True

    def get(self, rid):
        return {**(self.row or {}), "status": "answered"}

    def ack(self, rid):
        self.acked.append(rid)
        return True


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "gateway_token", lambda: "secret_tok")
    monkeypatch.setattr(G, "_tmux_session_names", lambda: ["gm"])
    monkeypatch.setattr(G, "MENU_ANSWER_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "0")      # tests opt IN to the (default) arm
    monkeypatch.delenv("MENU_MULTIPART_TEXT_ARMED", raising=False)
    monkeypatch.setattr(G, "_current_menu", lambda session: {"menu_family": "claude", **MENU})
    monkeypatch.setattr(G, "_is_gemini_session", lambda session: False)
    st = _Store(row=_row())
    monkeypatch.setattr(G, "ApprovalStore", lambda: st)
    return st


@pytest.fixture
def spy(monkeypatch):
    calls = []

    def fake(session, answers, *, store, armed=False, **kw):
        calls.append({"session": session, "answers": answers, "armed": armed, **kw})
        if armed:
            return True, {"durable": True, "delivered": True, "id": "apr_1"}
        return True, {"would_submit": True, "plan": [], "durable": False, "delivered": False,
                      "dry_run": True, "id": None}
    monkeypatch.setattr(G, "durable_first_batch_submit", fake)
    return calls


def _post(payload, **kw):
    resp = _run(G.handle_menu_submit(_Req(payload, **kw)))
    return resp.status, json.loads(resp.text)


def test_scope_is_approve_and_route_is_registered():
    assert G.ROUTE_SCOPES[("POST", "/menu-submit")] == "approve"
    app = G.build_app()
    assert any(r.method == "POST" and r.resource.canonical == "/menu-submit"
               for r in app.router.routes())


def test_unauthorized_401(spy):
    status, _ = _post({"session": "gm", "answers": ANSWERS, "confirm": True}, headers={})
    assert status == 401 and spy == []


def test_unknown_session_404(spy):
    status, _ = _post({"session": "nope", "answers": ANSWERS, "confirm": True})
    assert status == 404 and spy == []


@pytest.mark.parametrize("answers", [None, [], ["x"], {"part": 0}])
def test_answers_must_be_a_list_of_objects(spy, answers):
    status, _ = _post({"session": "gm", "answers": answers, "confirm": True})
    assert status == 400 and spy == []


def test_without_confirm_428_and_nothing_runs(spy):
    for confirm in (None, False, "true", 1):
        status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": confirm})
        assert status == 428 and body["needs_confirm"] is True
    assert spy == []


def test_unarmed_is_the_dry_run(spy):
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True})
    assert status == 200 and body["ok"] is True and body["armed"] is False
    assert body["dry_run"] is True and body["would_submit"] is True
    assert spy[0]["armed"] is False


def test_armed_flag_is_the_multipart_one_not_the_single_part_one(spy, monkeypatch):
    monkeypatch.setenv("MENU_SUBMIT_ARMED", "1")
    _post({"session": "gm", "answers": ANSWERS, "confirm": True})
    assert spy[-1]["armed"] is False
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "1")
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 200 and body["armed"] is True and spy[-1]["armed"] is True


def test_armed_own_words_refused_until_text_armed(spy, monkeypatch):
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "1")
    with_text = [{"part": 0, "ns": ["1"]}, {"part": 1, "text": "do it my way"}]
    status, body = _post({"session": "gm", "answers": with_text, "confirm": True, "card_id": "apr_1"})
    assert status == 409 and body["reason"] == "free_text_not_armed" and spy == []
    monkeypatch.setenv("MENU_MULTIPART_TEXT_ARMED", "1")
    status, body = _post({"session": "gm", "answers": with_text, "confirm": True, "card_id": "apr_1"})
    assert status == 200 and spy[-1]["answers"] == [{"part": 0, "ns": ["1"]},
                                                    {"part": 1, "ns": [], "text": "do it my way"}]


def test_unarmed_own_words_is_allowed_as_a_dry_run(spy):
    with_text = [{"part": 0, "ns": ["1"]}, {"part": 1, "text": "x"}]
    status, body = _post({"session": "gm", "answers": with_text, "confirm": True})
    assert status == 200 and body["dry_run"] is True


def test_text_too_long_400(spy):
    long = [{"part": 0, "text": "x" * (G.ANSWER_TEXT_MAX + 1)}]
    status, _ = _post({"session": "gm", "answers": long, "confirm": True})
    assert status == 400 and spy == []


def test_unarmed_card_id_mismatch_is_told_on_the_dry_run(spy, env):
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_OLD"})
    assert status == 409 and body["reason"] == "card_mismatch" and body["anchor_id"] == "apr_1"
    assert spy == []
    status, _ = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 200 and len(spy) == 1


def test_card_id_with_no_anchor_is_a_mismatch(spy, env):
    env.row = None
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 409 and body["reason"] == "card_mismatch" and spy == []


def test_card_id_is_passed_to_the_orchestrator_when_armed(spy, monkeypatch):
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "1")
    _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert spy[-1]["expect_row_id"] == "apr_1"


@pytest.mark.parametrize("bad", [
    [{"part": 0, "ns": ["1"]}, {"part": 0, "ns": ["2"]}],          # duplicate part
    [{"part": "0", "ns": ["1"]}],                                   # part not an int
    [{"part": True, "ns": ["1"]}],                                  # bool is not a part
    [{"part": 0, "ns": "1"}],                                       # ns not a list
    [{"part": 0, "ns": ["one"]}],                                   # not an option number
    [{"part": 0, "ns": [1.0]}],
])
def test_malformed_answers_400(spy, bad):
    status, _ = _post({"session": "gm", "answers": bad, "confirm": True})
    assert status == 400 and spy == []


def test_int_ns_are_coerced_to_the_strings_the_replay_presses(spy):
    _post({"session": "gm", "answers": [{"part": 0, "ns": [1]}, {"part": 1, "ns": [2, "3"]}], "confirm": True})
    assert spy[-1]["answers"] == [{"part": 0, "ns": ["1"]}, {"part": 1, "ns": ["2", "3"]}]


def test_own_words_option_without_text_is_refused_before_anything_runs(spy):
    status, body = _post({"session": "gm", "answers": [{"part": 0, "ns": ["1"]}, {"part": 1, "ns": ["4"]}],
                          "confirm": True})
    assert status == 422 and "part 1" in body["detail"] and spy == []


def test_armed_agy_menu_refused_identity_gate_unavailable(spy, monkeypatch):
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "1")
    monkeypatch.setattr(G, "_current_menu", lambda session: {"menu_family": "agy", **MENU})
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 409 and body["reason"] == "identity_gate_unavailable" and spy == []


def test_surface_from_the_body_is_recorded(spy):
    _post({"session": "gm", "answers": ANSWERS, "confirm": True, "surface": "phone"})
    assert spy[-1]["surface"] == "phone" and spy[-1]["answered_by"] == "operator"
    _post({"session": "gm", "answers": ANSWERS, "confirm": True, "surface": "admin"})
    assert spy[-1]["surface"] == "watch"


# ---- ARMED, the REAL orchestrator against a hydrated anchor (replay stubbed) ----

@pytest.fixture
def replay(monkeypatch):
    calls = []

    def fake_submit(session, *, answers, armed=False, expect_questions=None, **kw):
        calls.append({"answers": answers, "armed": armed, "expect_questions": expect_questions})
        return True, {"submitted": True}
    monkeypatch.setattr(G, "menu_batch_submit", fake_submit)
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "1")
    return calls


def test_armed_real_path_persists_first_and_replays_with_the_identity_gate(replay, env):
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1",
                          "surface": "phone"})
    assert status == 200 and body["durable"] is True and body["delivered"] is True
    assert env.recorded[0]["id"] == "apr_1" and env.recorded[0]["surface"] == "phone"
    assert replay[0]["expect_questions"] == {0: "Lock the API down now?", 1: "Which dashboards keep access?"}


def test_armed_card_race_a_newer_row_between_checks_receives_nothing(replay, env):
    """The bridge writes a NEWER pending row for the session after the device drew apr_1. The
    orchestrator's own lookup sees it: the batch must not be persisted onto it or replayed."""
    def newer(store):
        store.row = _row("apr_NEW")
    env.on_lookup = newer
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 409 and body["reason"] == "card_mismatch" and body["anchor_id"] == "apr_NEW"
    assert env.recorded == [] and replay == []


def test_armed_card_already_answered_is_idempotent(replay, env):
    env.row = None
    env.latest = {**_row("apr_1"), "answer": "batch", "answer_text": json.dumps(ANSWERS)}
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 200 and body["already"] is True and replay == []


def test_validation_is_422(monkeypatch):
    monkeypatch.setattr(G, "durable_first_batch_submit",
                        lambda *a, **k: (False, {"reason": "batch_validation", "detail": "parts unanswered: [1]"}))
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True})
    assert status == 422 and body["detail"] == "parts unanswered: [1]"


def test_audit_line_written(spy, tmp_path):
    _post({"session": "gm", "answers": ANSWERS, "confirm": True})
    line = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[-1])
    assert line["route"] == "menu-submit" and line["parts"] == [0, 1] and line["ok"] is True


# ---- what must NEVER happen: the REAL orchestrator, no anchor -> no pane-only replay ----

def test_no_durable_anchor_is_409_and_never_replays_into_the_pane(monkeypatch, env):
    """/agent-key falls back to a pane-only menu_batch_submit here. That leg has NO identity
    gate, so from a device it could press the operator's old answer into a newer menu. Armed,
    /menu-submit must refuse instead (unarmed it is a press-nothing dry-run either way)."""
    env.row = None
    pressed = []
    monkeypatch.setattr(G, "menu_batch_submit",
                        lambda session, **kw: pressed.append(kw) or (True, {"would_submit": True}))
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "1")
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True})
    assert status == 409 and body["reason"] == "card_required"
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 409 and body["reason"] == "card_mismatch"
    assert pressed == [], "an armed submit with no anchor must press nothing"


def test_capabilities_advertise_menu_submit_and_its_arm_state(monkeypatch):
    monkeypatch.setattr(G, "_capability_providers", lambda: [])
    monkeypatch.setattr(G, "_pending_count", lambda: 0)
    resp = _run(G.handle_gateway_capabilities(_Req(None)))
    body = json.loads(resp.text)
    assert "menu_submit" in body["features"]
    assert body["menu_submit"] == {"armed": False, "text_armed": False}
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "1")
    body = json.loads(_run(G.handle_gateway_capabilities(_Req(None))).text)
    assert body["menu_submit"]["armed"] is True


def test_armed_gemini_runtime_refused_even_when_the_menu_read_misses(spy, monkeypatch):
    """A missed pane read (None) must not pass the agy gate: the runtime alone refuses."""
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "1")
    monkeypatch.setattr(G, "_current_menu", lambda session: None)
    monkeypatch.setattr(G, "_is_gemini_session", lambda session: True)
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 409 and body["reason"] == "identity_gate_unavailable" and spy == []


def test_armed_mismatched_card_without_a_race_persists_and_presses_nothing(replay, env):
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_OLD"})
    assert status == 409 and body["reason"] == "card_mismatch" and body["anchor_id"] == "apr_1"
    assert env.recorded == [] and replay == []


def test_whitespace_only_text_is_no_answer(spy):
    status, body = _post({"session": "gm", "answers": [{"part": 0, "ns": ["1"]}, {"part": 1, "text": "   "}],
                          "confirm": True})
    assert status == 200 and spy[-1]["answers"][1] == {"part": 1, "ns": []}
    status, body = _post({"session": "gm", "answers": [{"part": 0, "ns": ["1"]}, {"part": 1, "ns": ["4"], "text": " "}],
                          "confirm": True})
    assert status == 422 and "part 1" in body["detail"]


def test_menu_submit_armed_without_a_card_is_refused_and_presses_nothing(spy, monkeypatch):
    # With no card the identity comes from whatever anchor is CURRENT.
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "1")
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True})
    assert status == 409 and body["reason"] == "card_required" and spy == []


def test_menu_submit_retry_after_the_menu_changed_presses_nothing(replay, env):
    env.row = _row("apr_B")
    env.latest = _row("apr_B")
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True})
    assert status == 409 and body["reason"] == "card_required"
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_A"})
    assert status == 409 and body["reason"] == "card_mismatch" and body["anchor_id"] == "apr_B"
    assert replay == [] and env.recorded == []


def test_menu_submit_unarmed_dry_run_needs_no_card(spy):
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True})
    assert status == 200 and spy[-1]["armed"] is False


# ---- review leg: identity gate must not silently switch off; a retry must not claim another's answer ----

def test_armed_card_with_parts_but_no_part_questions_is_refused_and_presses_nothing(replay, env):
    # With no per-part questions the replay's identity gate is OFF; from a device that is the
    # stale-tap path (card A still pending, menu B on screen), so refuse before persisting.
    bare = {"walk_complete": True, "part_count": 2, "parts": [
        {"index": 0, "select": "single", "options": MENU["parts"][0]["options"]},
        {"index": 1, "select": "multi", "options": MENU["parts"][1]["options"]}]}
    env.row = {**_row("apr_1"), "menu": json.dumps(bare)}
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 409 and body["reason"] == "identity_gate_unavailable"
    assert replay == [] and env.recorded == []


def _answered(rid, answers):
    return {**_row(rid), "status": "answered", "answer": "batch", "answer_text": json.dumps(answers)}


def test_retry_of_a_card_answered_DIFFERENTLY_elsewhere_is_not_reported_as_sent(replay, env):
    other = [{"part": 0, "ns": ["2"]}, {"part": 1, "ns": ["1"]}]
    env.row, env.latest = None, _answered("apr_1", other)
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 409 and body["reason"] == "answered_elsewhere" and body["answers"] == other
    assert replay == [] and env.recorded == []


def test_retry_of_the_SAME_answers_is_already_whatever_the_ns_order_or_type(replay, env):
    env.row, env.latest = None, _answered("apr_1", [{"part": 1, "ns": [3, 2]}, {"part": 0, "ns": [1]}])
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 200 and body["already"] is True and replay == []


def test_racing_persist_by_someone_else_is_answered_elsewhere_not_already(replay, env):
    other = [{"part": 0, "ns": ["2"]}, {"part": 1, "ns": ["1"]}]
    env.record_batch_answer = lambda rid, answers, **kw: False      # lost the answer-once race
    env.get = lambda rid: _answered(rid, other)
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 409 and body["reason"] == "answered_elsewhere" and replay == []


def test_orchestrator_without_a_card_keeps_the_old_already_semantics(env):
    # /agent-key's batch leg passes no expect_row_id: its answer-once retry stays `already`.
    other = [{"part": 0, "ns": ["2"]}, {"part": 1, "ns": ["1"]}]
    env.row, env.latest = None, _answered("apr_1", other)
    ok, info = G.durable_first_batch_submit("gm", ANSWERS, store=env, armed=True,
                                            submit_fn=lambda *a, **k: (True, {}),
                                            resume_fn=lambda *a, **k: None)
    assert ok is True and info["already"] is True


def test_protected_session_is_403_and_presses_nothing(spy, monkeypatch):
    monkeypatch.setattr(G, "_protected_refusal", lambda request, session: {"ok": False, "error": "protected"})
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "1")
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 403 and spy == []


def test_racing_persist_of_the_SAME_batch_is_already(replay, env):
    env.record_batch_answer = lambda rid, answers, **kw: False      # a duplicate tap won the race
    env.get = lambda rid: _answered(rid, ANSWERS)
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 200 and body["already"] is True and replay == []


def test_batches_differing_only_in_text_are_not_the_same_answer(env):
    with_text = [{"part": 0, "ns": ["1"]}, {"part": 1, "ns": ["2", "3"], "text": "and D"}]
    env.row, env.latest = None, _answered("apr_1", with_text)
    ok, info = G.durable_first_batch_submit("gm", ANSWERS, store=env, armed=True, expect_row_id="apr_1",
                                            submit_fn=lambda *a, **k: (True, {}),
                                            resume_fn=lambda *a, **k: None)
    assert ok is False and info["reason"] == "answered_elsewhere"


def test_the_gate_check_runs_on_the_orchestrators_own_lookup(replay, env):
    # The handler's lookup sees a hydrated card; a re-walk lands before the orchestrator's lookup
    # and leaves the SAME card without part questions. The refusal must still hold.
    bare = {"walk_complete": True, "part_count": 2, "parts": [
        {"index": 0, "select": "single", "options": MENU["parts"][0]["options"]},
        {"index": 1, "select": "multi", "options": MENU["parts"][1]["options"]}]}
    calls = {"n": 0}
    real = env.pending_menu_row_for_session

    def lookup(session):
        calls["n"] += 1
        return real(session) if calls["n"] == 1 else {**_row("apr_1"), "menu": json.dumps(bare)}
    env.pending_menu_row_for_session = lookup
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 409 and body["reason"] == "identity_gate_unavailable"
    assert replay == [] and env.recorded == []


# ---- the arm is ON by default; =0 opts out ----------------------------------------------------

def test_unset_arm_means_armed_and_zero_opts_out(monkeypatch):
    monkeypatch.delenv("MENU_MULTIPART_SUBMIT_ARMED", raising=False)
    assert G._menu_multipart_armed() is True
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "1")
    assert G._menu_multipart_armed() is True
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "0")
    assert G._menu_multipart_armed() is False


def test_default_install_menu_submit_without_a_card_is_card_required_not_a_dry_run(spy, monkeypatch):
    # An unarmed default answered a device with a dry-run 200 that reached no agent.
    monkeypatch.delenv("MENU_MULTIPART_SUBMIT_ARMED", raising=False)
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True})
    assert status == 409 and body["reason"] == "card_required" and spy == []


def test_default_install_capabilities_say_armed_and_text_not_armed(monkeypatch):
    monkeypatch.delenv("MENU_MULTIPART_SUBMIT_ARMED", raising=False)
    monkeypatch.setattr(G, "_capability_providers", lambda: [], raising=False)
    monkeypatch.setattr(G, "_pending_count", lambda: 0, raising=False)
    body = json.loads(_run(G.handle_gateway_capabilities(_Req({}))).text)
    assert body["menu_submit"] == {"armed": True, "text_armed": False}


# ---- /agent-key's batch leg: same card binding, same text arm, no identity-less leg when armed --

def _agent_key(payload):
    resp = _run(G.handle_agent_key(_Req({"session": "gm", "key": "submit", "confirm": True, **payload})))
    return resp.status, json.loads(resp.text)


def test_agent_key_armed_batch_without_a_card_is_refused_and_presses_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(G, "durable_first_batch_submit", lambda *a, **k: calls.append(k) or (True, {}))
    monkeypatch.setattr(G, "menu_batch_submit", lambda *a, **k: calls.append(("legacy", k)) or (True, {}))
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "1")
    status, body = _agent_key({"answers": ANSWERS})
    assert status == 409 and body["reason"] == "use_menu_submit" and calls == []
    status, body = _agent_key({"answers": ANSWERS, "card_id": 7})
    assert status == 400 and calls == []


def test_agent_key_armed_own_words_text_needs_its_own_arm(monkeypatch):
    calls = []
    monkeypatch.setattr(G, "durable_first_batch_submit", lambda *a, **k: calls.append(k) or (True, {}))
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "1")
    with_text = [{"part": 0, "ns": ["1"]}, {"part": 1, "ns": ["4"], "text": "D"}]
    status, body = _agent_key({"answers": with_text, "card_id": "apr_1"})
    assert status == 409 and body["reason"] == "free_text_not_armed" and calls == []
    monkeypatch.setenv("MENU_MULTIPART_TEXT_ARMED", "1")
    status, _ = _agent_key({"answers": with_text, "card_id": "apr_1"})
    assert status == 200 and len(calls) == 1


def test_agent_key_armed_batch_binds_the_card_and_never_falls_to_the_legacy_leg(monkeypatch):
    seen, legacy = [], []
    monkeypatch.setattr(G, "durable_first_batch_submit",
                        lambda *a, **k: seen.append(k.get("expect_row_id")) or (False, {"reason": "no_durable_row"}))
    monkeypatch.setattr(G, "menu_batch_submit", lambda *a, **k: legacy.append(1) or (True, {}))
    monkeypatch.setenv("MENU_MULTIPART_SUBMIT_ARMED", "1")
    status, _ = _agent_key({"answers": ANSWERS, "card_id": "apr_A"})
    assert seen == ["apr_A"], "the card must be bound as expect_row_id"
    assert legacy == [], "armed: the identity-less pane-only leg must never run"
    assert status == 409


def test_agent_key_opted_out_batch_dry_run_is_unchanged(monkeypatch):
    legacy = []
    monkeypatch.setattr(G, "durable_first_batch_submit", lambda *a, **k: (False, {"reason": "no_durable_row"}))
    monkeypatch.setattr(G, "menu_batch_submit",
                        lambda *a, **k: legacy.append(k.get("armed")) or (True, {"dry_run": True}))
    status, _ = _agent_key({"answers": ANSWERS})          # env fixture: MENU_MULTIPART_SUBMIT_ARMED=0
    assert status == 200 and legacy == [False]


def test_agent_key_retry_after_the_menu_changed_presses_nothing(replay, env):
    env.row = _row("apr_B")                  # the NEXT menu is now the pending anchor
    env.latest = _row("apr_B")
    status, body = _agent_key({"answers": ANSWERS})
    assert status == 409 and body["reason"] == "use_menu_submit"
    status, body = _agent_key({"answers": ANSWERS, "card_id": "apr_A"})
    assert status == 409 and body["reason"] == "card_mismatch" and body["anchor_id"] == "apr_B"
    assert replay == [] and env.recorded == []


def test_agent_key_with_no_anchor_at_all_presses_nothing_when_armed(replay, env):
    env.row = None
    env.latest = None
    for payload in ({"answers": ANSWERS}, {"answers": ANSWERS, "card_id": "apr_A"}):
        status, _ = _agent_key(payload)
        assert status == 409
    assert replay == [] and env.recorded == []


def test_agent_key_with_the_right_card_still_submits_through_the_identity_gate(replay, env):
    status, body = _agent_key({"answers": ANSWERS, "card_id": "apr_1"})
    assert status == 200 and env.recorded[0]["id"] == "apr_1"
    assert replay[0]["expect_questions"] == {0: "Lock the API down now?", 1: "Which dashboards keep access?"}


# ---- review leg on ebe1da6: /agent-key's armed batch gets /menu-submit's guards ------------------

def test_agent_key_armed_on_an_agy_pane_is_refused_and_presses_nothing(replay, env, monkeypatch):
    monkeypatch.setattr(G, "_is_gemini_session", lambda session: True)
    status, body = _agent_key({"answers": ANSWERS, "card_id": "apr_1"})
    assert status == 409 and body["reason"] == "identity_gate_unavailable"
    assert replay == [] and env.recorded == []


def test_agent_key_armed_card_without_part_questions_is_refused(replay, env):
    bare = {"walk_complete": True, "part_count": 2, "parts": [
        {"index": 0, "select": "single", "options": MENU["parts"][0]["options"]},
        {"index": 1, "select": "multi", "options": MENU["parts"][1]["options"]}]}
    env.row = {**_row("apr_1"), "menu": json.dumps(bare)}
    status, body = _agent_key({"answers": ANSWERS, "card_id": "apr_1"})
    assert status == 409 and body["reason"] == "identity_gate_unavailable"
    assert replay == [] and env.recorded == []


def test_agent_key_whitespace_text_is_dropped_never_typed(replay, env):
    # The text arm is off; whitespace passed the old gate and the replay would have typed it.
    status, _ = _agent_key({"answers": [{"part": 0, "ns": ["1"]}, {"part": 1, "ns": ["2"], "text": "   "}],
                            "card_id": "apr_1"})
    assert status == 200 and all("text" not in a for a in replay[0]["answers"])


@pytest.mark.parametrize("text", [123, ["x"], "x" * (G.ANSWER_TEXT_MAX + 1)])
def test_agent_key_text_that_is_not_a_short_string_is_400_before_anything(text, replay, env):
    status, _ = _agent_key({"answers": [{"part": 0, "ns": ["1"]}, {"part": 1, "ns": ["2"], "text": text}],
                            "card_id": "apr_1"})
    assert status == 400 and replay == [] and env.recorded == []


def test_agent_key_own_words_option_with_blank_text_is_422_and_presses_nothing(replay, env):
    # part 0's option 3 is its own-words row; "   " is dropped by cleaning, so it would persist
    # a batch whose part 0 gets no key.
    status, body = _agent_key({"answers": [{"part": 0, "ns": ["3"], "text": "   "}, {"part": 1, "ns": ["2"]}],
                               "card_id": "apr_1"})
    assert status == 422 and body["reason"] == "batch_validation"
    assert replay == [] and env.recorded == []


def test_a_MIXED_card_one_part_without_a_question_is_refused(replay, env):
    # Part 0 has its question, part 1 does not: the replay would gate part 0 and press part 1 blind.
    mixed = {"walk_complete": True, "part_count": 2, "parts": [
        MENU["parts"][0],
        {"index": 1, "question": "", "select": "multi", "options": MENU["parts"][1]["options"]}]}
    env.row = {**_row("apr_1"), "menu": json.dumps(mixed)}
    status, body = _post({"session": "gm", "answers": ANSWERS, "confirm": True, "card_id": "apr_1"})
    assert status == 409 and body["reason"] == "identity_gate_unavailable" and body["parts"] == [1]
    assert replay == [] and env.recorded == []


def test_batch_key_tolerates_a_non_string_text():
    assert G._batch_key([{"part": 0, "ns": ["1"], "text": 5}]) == {0: (["1"], "5")}


# ---- a LIVE-delivered replay is terminal (no second delivery by approval_resume) ----

def test_a_live_delivered_replay_acks_its_row(env):
    """Delivered live = terminal, the same self-ack approval_resume does after a keypress.
    Left 'answered', approval_resume would pick the row up again (a multi-part menu has no
    option_n) and re-deliver the answer as a digest inject plus a queued approval_resolved:
    one answer reaching the pane three times."""
    ok, info = G.durable_first_batch_submit("gm", ANSWERS, store=env, armed=True, expect_row_id="apr_1",
                                            submit_fn=lambda *a, **k: (True, {}),
                                            resume_fn=lambda *a, **k: None)
    assert ok is True and info["delivered"] is True
    assert env.acked == ["apr_1"]


def test_a_replay_that_did_not_deliver_stays_answered_for_the_fallback(env):
    """The other side: if the live replay did not deliver, the row must stay 'answered' so the
    durable fallback still delivers it. Acking here would lose the answer."""
    ok, info = G.durable_first_batch_submit("gm", ANSWERS, store=env, armed=True, expect_row_id="apr_1",
                                            submit_fn=lambda *a, **k: (False, {"reason": "menu_gone"}),
                                            resume_fn=lambda *a, **k: {})
    assert ok is True and info["delivered"] is False
    assert env.acked == []
