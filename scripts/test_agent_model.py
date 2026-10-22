"""GET/PUT /agent-model: the catalog it offers, who may switch, and the host's default model.

The live path is driven with a stand-in for the pane: `send` plays the Claude CLI (it writes
the settings file's "model" key, as an interactive `/model` does, and appends the
`<local-command-stdout>Set model to ...` line to the transcript). No real CLI and no real
settings file are touched: every path is under tmp_path.
"""
import json
import os
import threading

import pytest

import scripts.agent_model as AM
import scripts.watch_gateway as G
from scripts.test_gateway_scopes import _FakeRequest, _run


# --- catalog ---------------------------------------------------------------------------------

def _providers(tmp_path):
    p = tmp_path / "providers.json"
    p.write_text(json.dumps({"providers": [
        {"id": "claude", "model_catalog": {"static": [
            {"id": "claude-opus-5-5", "label": "Opus 5.5", "capabilities": {"context_window": 200000}},
            {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5"}]}},
        {"id": "codex", "model_catalog": {"static": [{"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra"}]}},
    ]}))
    return p


def test_the_catalog_is_the_live_answer_then_static_then_live_only_ids(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / AM.RESPONSE_CACHE).write_text(json.dumps({"providers": [{"id": "claude", "models": [
        {"id": "claude-fable-5-1", "label": "Fable 5.1"},
        {"id": "claude-opus-5-5[1m]", "label": "Opus 5.5 (1M)"}]}]}))
    (state / AM.LIVE_CATALOG).write_text(json.dumps({"providers": {"claude": ["claude-haiku-4-5", "claude-opus-5-5"]}}))
    rows = AM.load_catalog("claude", _providers(tmp_path), state)
    assert [r["id"] for r in rows] == ["claude-fable-5-1", "claude-opus-5-5[1m]", "claude-opus-5-5",
                                      "claude-sonnet-5-5", "claude-haiku-4-5"]
    by = {r["id"]: r for r in rows}
    assert by["claude-opus-5-5[1m]"]["subtitle"] == "1M context"
    assert by["claude-opus-5-5"] == {"id": "claude-opus-5-5", "title": "Opus 5.5", "subtitle": "200K context"}
    assert by["claude-haiku-4-5"]["title"] == "claude-haiku-4-5"          # live-only: no label known


def test_missing_or_broken_cache_files_mean_fewer_rows_not_an_error(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / AM.RESPONSE_CACHE).write_text("{not json")
    assert [r["id"] for r in AM.load_catalog("codex", _providers(tmp_path), state)] == ["gpt-5.6-terra"]
    assert AM.load_catalog("codex", tmp_path / "absent.json", tmp_path / "nowhere") == []


def test_off_runtimes_is_an_install_setting():
    assert AM.off_runtimes({"AGENT_MODEL_OFF": "gemini=out of usage, grok"}) == {
        "gemini": "out of usage", "grok": "switched off on this server"}
    assert AM.off_runtimes({}) == {}


ROWS = [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]


def test_claude_switches_live_and_codex_at_the_next_rotation():
    d = AM.describe("claude", "a", ROWS, {})
    assert d["how"] == "live" and d["allowed"] == ["a", "b"] and d["unavailable"] == {}
    assert all(m["available"] for m in d["models"]) and d["model"] == "a"
    assert AM.describe("codex", None, ROWS, {})["how"] == "next_rotation"


@pytest.mark.parametrize("runtime,off,reason", [
    ("gemini", {"gemini": "out of usage"}, "out of usage"),
    ("grok", {}, "this runtime cannot switch models yet"),
    (None, {}, "this agent's runtime is not known"),
])
def test_a_runtime_that_cannot_switch_still_lists_its_rows_with_the_reason(runtime, off, reason):
    d = AM.describe(runtime, "a", ROWS, off)
    assert d["how"] is None and d["allowed"] == []
    assert d["unavailable"] == {"a": reason, "b": reason}
    assert [m["available"] for m in d["models"]] == [False, False]
    assert {m["reason"] for m in d["models"]} == {reason}


# --- the host settings key ---------------------------------------------------------------------

def _settings(tmp_path, data, mode=0o600):
    p = tmp_path / "cfg" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    os.chmod(p, mode)
    return p


def test_only_the_model_key_is_put_back_and_the_rest_is_kept(tmp_path):
    p = _settings(tmp_path, {"model": "opus", "hooks": {"Stop": [1]}})
    p.write_text(json.dumps({"model": "claude-haiku-4-5", "hooks": {"Stop": [1, 2]}, "theme": "dark"}))
    assert AM.write_model_key(p, "opus")
    assert json.loads(p.read_text()) == {"model": "opus", "hooks": {"Stop": [1, 2]}, "theme": "dark"}
    assert oct(p.stat().st_mode & 0o777) == oct(0o600)
    assert [x.name for x in p.parent.iterdir() if x.name.endswith(".tmp")] == []


def test_a_key_that_was_absent_is_removed_again(tmp_path):
    p = _settings(tmp_path, {"theme": "dark"})
    before = AM.read_model_key(p)
    p.write_text(json.dumps({"theme": "dark", "model": "claude-opus-5-5"}))
    assert AM.write_model_key(p, before)
    assert json.loads(p.read_text()) == {"theme": "dark"}


def test_a_settings_file_that_is_not_an_object_is_never_overwritten(tmp_path):
    p = _settings(tmp_path, [1, 2])
    with pytest.raises(ValueError):
        AM.write_model_key(p, "opus")
    assert json.loads(p.read_text()) == [1, 2]


# --- the live switch --------------------------------------------------------------------------

class FakeClaude:
    """Plays the CLI's side of `/model <id>`: saves the pick as the host default, then prints."""

    def __init__(self, settings, jsonl, answer="Set model to `Opus 5.5` and saved as your default for new sessions",
                 busy=False, late_save=False):
        self.settings, self.jsonl, self.answer = settings, jsonl, answer
        self.busy, self.late_save, self.sent = busy, late_save, []

    def send(self, text):
        self.sent.append(text)
        if self.busy:
            return False, {"reason": "busy", "state": "working"}
        model = text.split(" ", 1)[1]
        if not self.late_save:
            self._save(model)
        if self.answer is not None:
            with open(self.jsonl, "a") as f:
                f.write(json.dumps({"type": "user", "timestamp": "2099-01-01T00:00:00.000Z", "message": {
                    "role": "user", "content": f"<local-command-stdout>{self.answer}</local-command-stdout>"}}) + "\n")
        self._pending = model
        return True, {}

    def _save(self, model):
        data = json.loads(self.settings.read_text())
        data["model"] = model
        self.settings.write_text(json.dumps(data))

    def sleep(self, _s):
        if self.late_save and getattr(self, "_pending", None):     # the save lands after the print
            self._save(self._pending)
            self._pending = None


def _live(tmp_path, claude, **kw):
    return AM.switch_live(model="claude-opus-5-5", settings_path=claude.settings, send=claude.send,
                          find_jsonl=lambda: claude.jsonl, timeout_s=0, sleep=claude.sleep,
                          now=lambda: "2026-10-22T00:00:00.000Z", **kw)


def _setup(tmp_path, **kw):
    s = _settings(tmp_path, {"model": "opus", "hooks": {}})
    j = tmp_path / "t.jsonl"
    j.write_text(json.dumps({"type": "user", "timestamp": "2026-10-01T00:00:00.000Z", "message": {
        "content": "<local-command-stdout>Set model to `Haiku 4.5` and saved as your default</local-command-stdout>"}}) + "\n")
    return FakeClaude(s, j, **kw)


def test_a_live_switch_is_confirmed_and_the_host_default_comes_back(tmp_path):
    c = _setup(tmp_path)
    status, body = _live(tmp_path, c)
    assert c.sent == ["/model claude-opus-5-5"]
    assert status == 200 and body == {"ok": True, "model": "claude-opus-5-5", "switching": "switched",
                                      "display": "Opus 5.5"}
    assert json.loads(c.settings.read_text()) == {"model": "opus", "hooks": {}}


def test_a_save_that_lands_after_the_print_is_still_undone(tmp_path):
    c = _setup(tmp_path, late_save=True)
    status, _ = _live(tmp_path, c)
    assert status == 200 and json.loads(c.settings.read_text())["model"] == "opus"


def test_a_busy_agent_is_409_and_nothing_changes(tmp_path):
    c = _setup(tmp_path, busy=True)
    status, body = _live(tmp_path, c)
    assert status == 409 and body["error"] == "busy" and body["state"] == "working"
    assert json.loads(c.settings.read_text())["model"] == "opus"


def test_no_answer_is_unconfirmed_and_an_older_line_does_not_count(tmp_path):
    c = _setup(tmp_path, answer=None)          # the transcript only has the 10-01 Haiku line
    status, body = _live(tmp_path, c)
    assert status == 502 and body["error"] == "unconfirmed"
    assert json.loads(c.settings.read_text())["model"] == "opus"


def test_any_other_command_output_is_a_refusal_with_its_words(tmp_path):
    c = _setup(tmp_path, answer="Model 'claude-opus-5-5' not found")
    status, body = _live(tmp_path, c)
    assert status == 502 and body == {"ok": False, "error": "refused", "detail": "Model 'claude-opus-5-5' not found"}


def test_two_switches_at_once_still_leave_the_host_default_as_it_was(tmp_path):
    """The lock is what makes this hold. The worst order is forced with events: B starts after A
    has typed (the setting now holds A's pick) and before A puts it back, and B puts its snapshot
    back last. Without the lock B's snapshot IS A's pick, so an agent's model would end up as
    every new session's default. With it, B waits for A and snapshots the person's own value."""
    s = _settings(tmp_path, {"model": "opus"})
    a_sent, b_sent, a_done = threading.Event(), threading.Event(), threading.Event()

    def switch(name, model, after_send, settle):
        j = tmp_path / f"{name}.jsonl"
        j.write_text("")
        c = FakeClaude(s, j)

        def send(text):
            out = c.send(text)
            after_send.set()
            return out
        AM.switch_live(model=model, settings_path=s, send=send, find_jsonl=lambda: j, timeout_s=0,
                       sleep=lambda _s: settle(), now=lambda: "2026-10-22T00:00:00.000Z")

    def run_a():
        switch("a", "m1", a_sent, lambda: b_sent.wait(1.0))   # hold the restore until B has typed
        a_done.set()

    def run_b():
        a_sent.wait(2.0)
        switch("b", "m2", b_sent, lambda: a_done.wait(1.0))   # restore only after A finished

    ts = [threading.Thread(target=run_a), threading.Thread(target=run_b)]
    [t.start() for t in ts]
    [t.join(5) for t in ts]
    assert json.loads(s.read_text())["model"] == "opus"


def test_scan_reads_only_lines_after_the_send(tmp_path):
    j = tmp_path / "t.jsonl"
    j.write_text("\n".join([
        json.dumps({"type": "user", "timestamp": "2026-10-22T05:00:00.000Z", "message": {
            "content": "<local-command-stdout>Set model to `Fable 5.1` and saved</local-command-stdout>"}}),
        json.dumps({"type": "assistant", "timestamp": "2026-10-22T06:00:01.000Z", "message": {
            "content": [{"type": "text", "text": "<local-command-stdout>Set model to `X`</local-command-stdout>"}]}}),
        json.dumps({"type": "user", "timestamp": "2026-10-22T06:00:02.000Z", "message": {
            "content": [{"type": "text", "text": "<local-command-stdout>Set model to `Opus 5.5` and saved as your default for new sessions</local-command-stdout>"}]}}),
        '{"torn',
    ]))
    assert AM.scan_switch_result(j, "2026-10-22T06:00:00.000Z") == ("set", "Opus 5.5")
    assert AM.scan_switch_result(j, "2026-10-22T07:00:00.000Z") is None


# --- the gateway route ------------------------------------------------------------------------

class _Req(_FakeRequest):
    def __init__(self, method, body=None, query=None, actor=None, principal=None):
        super().__init__(method, "/agent-model", {"X-Actor": actor} if actor else {})
        self._body, self.query = body, query or {}
        self["principal"] = principal if principal is not None else {"id": "legacy", "label": "", "scopes": ["*"]}

    async def json(self):
        return self._body


@pytest.fixture
def gw(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-token-value")
    monkeypatch.setattr(G, "_tmux_session_names", lambda: {"dev"})
    monkeypatch.setattr(G, "_seat_for_session", lambda s: ("dev", {}, "claude", "claude-opus-5-5"))
    monkeypatch.setattr(AM, "load_catalog", lambda rt, p, st: [
        {"id": "claude-opus-5-5", "title": "Opus 5.5"}, {"id": "claude-sonnet-5-5", "title": "Sonnet 5.5"}])
    monkeypatch.delenv("AGENT_MODEL_OFF", raising=False)
    seen = {"live": [], "stored": []}
    monkeypatch.setattr(AM, "switch_live", lambda **kw: (seen["live"].append(kw["model"]) or
                                                         (200, {"ok": True, "model": kw["model"], "switching": "switched"})))
    import identity_store.identity_writer as IW
    monkeypatch.setattr(IW, "update_session", lambda d, root, f: seen["stored"].append((root, f)) or True)
    return seen


def _body(resp):
    return json.loads(resp.body)


def test_get_lists_the_agents_models(gw):
    r = _run(G.handle_agent_model(_Req("GET", query={"session": "dev"})))
    b = _body(r)
    assert r.status == 200 and b["model"] == "claude-opus-5-5" and b["how"] == "live"
    assert b["allowed"] == ["claude-opus-5-5", "claude-sonnet-5-5"]


def test_the_fleet_token_alone_cannot_switch_a_model(gw):
    for actor in (None, "automation:bot"):
        r = _run(G.handle_agent_model(_Req("PUT", {"session": "dev", "model": "claude-sonnet-5-5"}, actor=actor)))
        assert r.status == 403
    assert gw["live"] == [] and gw["stored"] == []


def test_a_person_switches_it_and_the_seat_keeps_the_model(gw):
    r = _run(G.handle_agent_model(_Req("PUT", {"session": "dev", "model": "claude-sonnet-5-5"}, actor="app:ios")))
    assert r.status == 200 and _body(r)["switching"] == "switched" and _body(r)["stored"] is True
    assert gw["live"] == ["claude-sonnet-5-5"] and gw["stored"] == [("dev", {"model": "claude-sonnet-5-5"})]


def test_a_paired_device_needs_no_actor_header(gw):
    dev = {"id": "dev123", "label": "Phone", "scopes": ["read", "message"]}
    r = _run(G.handle_agent_model(_Req("PUT", {"session": "dev", "model": "claude-sonnet-5-5"}, principal=dev)))
    assert r.status == 200


def test_a_model_not_offered_is_422_and_the_same_model_is_a_no_op(gw):
    r = _run(G.handle_agent_model(_Req("PUT", {"session": "dev", "model": "gpt-5"}, actor="app:ios")))
    assert r.status == 422
    r = _run(G.handle_agent_model(_Req("PUT", {"session": "dev", "model": "claude-opus-5-5"}, actor="app:ios")))
    assert r.status == 200 and _body(r)["changed"] is False
    assert gw["live"] == [] and gw["stored"] == []


def test_codex_is_stored_for_the_next_rotation_and_nothing_is_typed(gw, monkeypatch):
    monkeypatch.setattr(G, "_seat_for_session", lambda s: ("cx", {}, "codex", "gpt-5.6-terra"))
    monkeypatch.setattr(AM, "load_catalog", lambda rt, p, st: [{"id": "gpt-5.6-terra", "title": "T"},
                                                              {"id": "gpt-5.6-luna", "title": "L"}])
    r = _run(G.handle_agent_model(_Req("PUT", {"session": "dev", "model": "gpt-5.6-luna"}, actor="operator:web")))
    assert r.status == 200 and _body(r)["switching"] == "next_rotation"
    assert gw["live"] == [] and gw["stored"] == [("cx", {"model": "gpt-5.6-luna"})]


def test_a_switched_off_runtime_refuses_with_its_reason(gw, monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_OFF", "claude=out of usage")
    r = _run(G.handle_agent_model(_Req("PUT", {"session": "dev", "model": "claude-sonnet-5-5"}, actor="app:ios")))
    assert r.status == 422 and _body(r)["error"] == "out of usage"


def test_an_unknown_session_is_404(gw):
    r = _run(G.handle_agent_model(_Req("GET", query={"session": "nope"})))
    assert r.status == 404


def test_the_routes_are_mounted_with_their_scopes():
    app = G.build_app()
    mounted = {(route.method.upper(), res.canonical) for res in app.router.resources() for route in res}
    assert {("GET", "/agent-model"), ("PUT", "/agent-model")} <= mounted
    assert G.ROUTE_SCOPES[("GET", "/agent-model")] == "read"
    assert G.ROUTE_SCOPES[("PUT", "/agent-model")] == "message"


# --- codex R1: ids, confirmation, crash safety, store failures ---------------------------------

def test_a_catalog_id_that_could_type_something_else_is_never_offered(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / AM.LIVE_CATALOG).write_text(json.dumps({"providers": {"claude": [
        "claude-opus-5-5\n/clear", "a b", "claude-opus-5-5[1m]", "-x"]}}))
    assert [r["id"] for r in AM.load_catalog("claude", tmp_path / "none.json", state)] == ["claude-opus-5-5[1m]"]
    status, body = AM.switch_live(model="x\n/clear", settings_path=tmp_path / "s.json", send=None,
                                  find_jsonl=lambda: None)
    assert status == 400


def test_a_line_already_in_the_file_never_confirms_whatever_its_timestamp(tmp_path):
    j = tmp_path / "t.jsonl"
    j.write_text(json.dumps({"type": "user", "timestamp": "2099-01-01T00:00:00.000Z", "message": {
        "content": "<local-command-stdout>Set model to `Opus 5.5`</local-command-stdout>"}}) + "\n")
    off = AM.file_size(j)
    assert AM.scan_switch_result(j, "2026-10-22T00:00:00.000Z", off) is None
    assert AM.scan_switch_result(j, "2026-10-22T00:00:00.000Z") == ("set", "Opus 5.5")   # no offset: ts only


def test_a_confirmation_naming_another_model_is_not_a_switch(tmp_path):
    c = _setup(tmp_path, answer="Set model to `Fable 5.1` and saved as your default for new sessions")
    status, body = _live(tmp_path, c)
    assert status == 502 and body["error"] == "a different model was confirmed"
    assert json.loads(c.settings.read_text())["model"] == "opus"


def test_a_save_after_the_restore_is_caught_by_the_second_look(tmp_path, monkeypatch):
    """The CLI's save lands just AFTER the first restore (its own save timed out and finished
    late). Only the second look puts the setting back."""
    c = _setup(tmp_path, late_save=True)
    real, restores = AM.write_model_key, []

    def write_then_late_save(path, value):
        ok = real(path, value)
        restores.append(value)
        if len(restores) == 1:
            c._save("claude-opus-5-5")
        return ok
    monkeypatch.setattr(AM, "write_model_key", write_then_late_save)
    status, _ = AM.switch_live(model="claude-opus-5-5", settings_path=c.settings, send=c.send,
                               find_jsonl=lambda: c.jsonl, timeout_s=0, sleep=lambda _s: None,
                               now=lambda: "2026-10-22T00:00:00.000Z")
    assert status == 200 and json.loads(c.settings.read_text())["model"] == "opus"


def test_a_switch_that_died_mid_way_is_restored_by_the_next_one(tmp_path):
    c = _setup(tmp_path)
    c.settings.write_text(json.dumps({"model": "claude-haiku-4-5", "hooks": {}}))   # the dead switch's pick
    (c.settings.parent / AM.JOURNAL_NAME).write_text(json.dumps({"present": True, "model": "opus"}))
    status, _ = _live(tmp_path, c)
    assert status == 200 and json.loads(c.settings.read_text())["model"] == "opus"
    assert not (c.settings.parent / AM.JOURNAL_NAME).exists()


def test_an_absent_key_journal_removes_the_key(tmp_path):
    s = _settings(tmp_path, {"model": "claude-haiku-4-5", "theme": "dark"})
    (s.parent / AM.JOURNAL_NAME).write_text(json.dumps({"present": False}))
    assert AM.recover_pending(s) and json.loads(s.read_text()) == {"theme": "dark"}


def test_a_restore_that_cannot_happen_is_a_500_not_an_exception(tmp_path):
    c = _setup(tmp_path)
    real = c.send

    def send(text):
        out = real(text)
        c.settings.write_text("[1]")                 # the file is no longer an object
        return out
    status, body = AM.switch_live(model="claude-opus-5-5", settings_path=c.settings, send=send,
                                  find_jsonl=lambda: c.jsonl, timeout_s=0, sleep=lambda _s: None,
                                  now=lambda: "2026-10-22T00:00:00.000Z")
    assert status == 500 and body["error"] == "host default model not restored" and body["switched"] is True
    assert (c.settings.parent / AM.JOURNAL_NAME).exists()         # kept for the next switch


def test_a_next_rotation_switch_that_is_not_stored_is_an_error(gw, monkeypatch):
    monkeypatch.setattr(G, "_seat_for_session", lambda s: ("cx", {}, "codex", "gpt-5.6-terra"))
    monkeypatch.setattr(AM, "load_catalog", lambda rt, p, st: [{"id": "gpt-5.6-terra", "title": "T"},
                                                              {"id": "gpt-5.6-luna", "title": "L"}])
    import identity_store.identity_writer as IW
    monkeypatch.setattr(IW, "update_session", lambda d, root, f: False)
    r = _run(G.handle_agent_model(_Req("PUT", {"session": "dev", "model": "gpt-5.6-luna"}, actor="app:ios")))
    assert r.status == 502 and _body(r)["error"] == "not stored"
    r = _run(G.handle_agent_model(_Req("PUT", {"session": "dev", "model": "gpt-5.6-terra"}, actor="app:ios")))
    assert r.status == 200 and _body(r) == {"ok": True, "model": "gpt-5.6-terra", "changed": False,
                                            "switching": "next_rotation"}


def test_a_live_switch_the_seat_did_not_keep_says_so(gw, monkeypatch):
    import identity_store.identity_writer as IW
    monkeypatch.setattr(IW, "update_session", lambda d, root, f: False)
    r = _run(G.handle_agent_model(_Req("PUT", {"session": "dev", "model": "claude-sonnet-5-5"}, actor="app:ios")))
    b = _body(r)
    assert r.status == 200 and b["switching"] == "switched" and b["stored"] is False and "warning" in b


def test_an_unknown_transcript_means_nothing_is_typed(tmp_path):
    c = _setup(tmp_path)
    status, body = AM.switch_live(model="claude-opus-5-5", settings_path=c.settings, send=c.send,
                                  find_jsonl=lambda: None, timeout_s=0, sleep=lambda _s: None)
    assert status == 502 and body["error"] == "unconfirmable" and c.sent == []
    assert not (c.settings.parent / AM.JOURNAL_NAME).exists()


def test_a_send_that_raises_is_a_502_and_the_default_still_comes_back(tmp_path):
    c = _setup(tmp_path)

    def send(text):
        c._save("claude-opus-5-5")             # the CLI saved, then the send path blew up
        raise RuntimeError("tmux went away")
    status, body = AM.switch_live(model="claude-opus-5-5", settings_path=c.settings, send=send,
                                  find_jsonl=lambda: c.jsonl, timeout_s=0, sleep=lambda _s: None)
    assert status == 502 and body["error"] == "switch failed" and "tmux went away" in body["detail"]
    assert json.loads(c.settings.read_text())["model"] == "opus"


def test_a_transcript_that_shrank_confirms_nothing(tmp_path):
    j = tmp_path / "t.jsonl"
    j.write_text(json.dumps({"type": "user", "timestamp": "2099-01-01T00:00:00.000Z", "message": {
        "content": "<local-command-stdout>Set model to `Opus 5.5`</local-command-stdout>"}}) + "\n")
    assert AM.scan_switch_result(j, "2026-10-22T00:00:00.000Z", AM.file_size(j) + 500) is None


def test_a_recheck_sleep_that_raises_still_restores(tmp_path):
    c = _setup(tmp_path)
    calls = []

    def sleep(_s):
        calls.append(_s)
        if len(calls) == 2:
            raise RuntimeError("interrupted")
    status, _ = AM.switch_live(model="claude-opus-5-5", settings_path=c.settings, send=c.send,
                               find_jsonl=lambda: c.jsonl, timeout_s=0, sleep=sleep,
                               now=lambda: "2026-10-22T00:00:00.000Z")
    assert status == 200 and json.loads(c.settings.read_text())["model"] == "opus"


def test_a_failed_switch_names_the_models(gw, monkeypatch):
    monkeypatch.setattr(AM, "switch_live", lambda **kw: (502, {"ok": False, "error": "unconfirmed"}))
    r = _run(G.handle_agent_model(_Req("PUT", {"session": "dev", "model": "claude-sonnet-5-5"}, actor="app:ios")))
    assert r.status == 502 and _body(r)["model"] == "claude-sonnet-5-5" and _body(r)["current"] == "claude-opus-5-5"


# --- the confirm dialog (Claude Code 2.1.284, a seat with history; measured 2026-10-22) --------

DIALOG = """  Your next response will be slower and use more tokens
  This conversation is cached for the current model. Switching to {name} means the full history gets re-read on your next message.
  ❯ 1. Yes, switch to {name}
    2. No, go back
"""


class DialogClaude(FakeClaude):
    """A seat with history: `/model` opens the dialog; "1" saves and prints, "2" goes back."""

    def __init__(self, *a, offer="Opus 5.5", lose_keys=False, **k):
        super().__init__(*a, **k)
        self.offer, self.lose_keys, self.screen, self.keyed = offer, lose_keys, "❯ \n", []

    def send(self, text):
        self.sent.append(text)
        self._pending = text.split(" ", 1)[1]
        self.screen = DIALOG.format(name=self.offer)
        return True, {}

    def keys(self, k):
        self.keyed.append(k)
        if self.lose_keys and k == "1":
            return
        if k == "1" and "Yes, switch to" in self.screen:
            self._save(self._pending)
            with open(self.jsonl, "a") as f:
                f.write(json.dumps({"type": "user", "timestamp": "2099-01-01T00:00:00.000Z", "message": {
                    "content": f"<local-command-stdout>Set model to `{self.offer}` and saved as your default for new sessions</local-command-stdout>"}}) + "\n")
        self.screen = "❯ \n"

    def capture(self):
        return self.screen


def _dialog_setup(tmp_path, **kw):
    s = _settings(tmp_path, {"model": "opus", "hooks": {}})
    j = tmp_path / "t.jsonl"
    j.write_text("")
    return DialogClaude(s, j, **kw)


def _dialog_run(c):
    return AM.switch_live(model="claude-opus-5-5", settings_path=c.settings, send=c.send, find_jsonl=lambda: c.jsonl,
                          capture=c.capture, keys=c.keys, timeout_s=0.05, sleep=lambda _s: None,
                          now=lambda: "2026-10-22T00:00:00.000Z")


def test_the_confirm_dialog_is_answered_yes_for_the_model_asked_and_the_default_still_comes_back(tmp_path):
    c = _dialog_setup(tmp_path)
    status, body = _dialog_run(c)
    assert status == 200 and body["display"] == "Opus 5.5"
    assert c.keyed == ["1"] and "Yes, switch" not in c.screen
    assert json.loads(c.settings.read_text())["model"] == "opus"        # the save came inside the lock


def test_a_dialog_offering_another_model_is_answered_no(tmp_path):
    c = _dialog_setup(tmp_path, offer="Fable 5.1")
    status, body = _dialog_run(c)
    assert status == 502 and body == {"ok": False, "error": "the agent offered a different model", "detail": "Fable 5.1"}
    assert c.keyed == ["2"] and json.loads(c.settings.read_text())["model"] == "opus"


def test_a_dialog_still_open_at_the_end_is_answered_no_never_left(tmp_path):
    c = _dialog_setup(tmp_path, lose_keys=True)                          # the "1" never lands
    status, body = _dialog_run(c)
    assert status == 502 and body["error"] == "unconfirmed"
    assert c.keyed == ["1", "2"] and "Yes, switch" not in c.screen


def test_the_real_dialog_text_parses():
    assert AM.switch_dialog(DIALOG.format(name="Sonnet 5.5")) == "Sonnet 5.5"
    assert AM.switch_dialog("❯ 1. Yes, switch to X\n") is None                 # half a dialog is not one
    assert AM.switch_dialog("❯ ") is None


def test_a_pane_still_asking_at_the_end_is_never_a_success(tmp_path):
    """Neither "1" nor "2" lands (a frozen pane): the last screen read before the lock is released
    still shows the question, so the answer says so."""
    c = _dialog_setup(tmp_path)
    c.keys = lambda k: c.keyed.append(k)                 # keys reach nothing
    status, body = _dialog_run(c)
    assert status == 502 and body["error"] == "the agent is still asking to confirm the switch"
    assert body["detail"] == "Opus 5.5" and c.keyed == ["1", "2"]


def test_dialog_text_that_is_not_at_the_bottom_is_not_open():
    answered_above = DIALOG.format(name="Sonnet 5.5") + "  ⎿  Set model to Sonnet 5.5\n❯ \n"
    assert AM.switch_dialog(answered_above) is None
    assert AM.switch_dialog(DIALOG.format(name="Sonnet 5.5") + "\n\n") == "Sonnet 5.5"   # blank lines below are fine


def test_a_stale_dialog_above_the_prompt_gets_no_key(tmp_path):
    c = _dialog_setup(tmp_path)
    real_send = c.send

    def send(text):
        out = real_send(text)
        c.screen = DIALOG.format(name="Opus 5.5") + "  ⎿  something else\n❯ \n"     # not open
        return out
    c.send = send
    status, body = _dialog_run(c)
    assert c.keyed == [] and status == 502 and body["error"] == "unconfirmed"


def test_a_dialog_seen_after_the_deadline_is_declined_not_accepted(tmp_path):
    c = _dialog_setup(tmp_path)
    status, body = AM.switch_live(model="claude-opus-5-5", settings_path=c.settings, send=c.send,
                                  find_jsonl=lambda: c.jsonl, capture=c.capture, keys=c.keys, timeout_s=0,
                                  sleep=lambda _s: None, now=lambda: "2026-10-22T00:00:00.000Z")
    assert c.keyed == ["2"] and status == 502 and json.loads(c.settings.read_text())["model"] == "opus"
