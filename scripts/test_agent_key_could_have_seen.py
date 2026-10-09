"""A single-digit permission answer can only answer what the device was shown.

/agent-key's digit path sends the key to whatever prompt is on screen, and the apps send no card
identity with it. So a tap meant for "proceed? [Bash: cmd A]" used to approve cmd B if B replaced
A before the tap arrived. The gateway now remembers, per device and session, the permission
instance it last served that device, and refuses (409) unless the prompt on screen is that same
instance, or when it has nothing on record for that device and session.
"""
import asyncio
import json

import pytest

import scripts.watch_gateway as G

SESSION = "seat"
PHONE_UA = "OrchestraOS/269 CFNetwork Darwin iOS"
WATCH_UA = "OrchestraOS/267 CFNetwork Darwin watchOS"


def _menu(cmd):
    return {"kind": "permission", "question": "Do you want to proceed? [Bash]",
            "context": f"Bash command\n{cmd}\nThis command requires approval",
            "options": [{"n": "1", "label": "Yes"}, {"n": "2", "label": "No"}]}


CMD_A, CMD_B = _menu("echo safe"), _menu("rm -rf /tmp/important")


class _Req:
    def __init__(self, payload=None, ua=PHONE_UA, install=None):
        self.headers = {"Authorization": "Bearer fleet-tok", "User-Agent": ua}
        if install:
            self.headers["X-Client-Install"] = install
        self._payload = payload or {}
        self.query = {}
        self.match_info = {}

    async def json(self):
        return self._payload


@pytest.fixture
def screen(monkeypatch, tmp_path):
    state = {"menu": CMD_A, "sent": []}

    class _AS:
        def get_agent_status(self, session):
            return {"pending_menu": state["menu"], "state": "waiting"}

    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))          # the off switch is read here, never on the host
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-tok")
    monkeypatch.setattr(G, "_PERM_INSTANCE_LEDGER", str(tmp_path / "ledger.json"), raising=False)
    monkeypatch.setattr(G, "_agent_status", lambda: _AS())
    monkeypatch.setattr(G, "_tmux_session_names", lambda: [SESSION])
    monkeypatch.setattr(G, "_is_gemini_session", lambda s: False)
    monkeypatch.setattr(G, "_protected_refusal", lambda request, session: None)

    class _R:
        returncode = 0

    monkeypatch.setattr(G, "_tmux", lambda *a: state["sent"].append(a) or _R())
    monkeypatch.setattr(G, "_SERVED", G._OrderedDict())
    return state


def _show(menu, now):
    """Some surface rendered `menu`: the gateway stamps its instance (as /pending-approvals and
    /agent-screen do) at `now`."""
    n, d = G._stamp_instance(SESSION, menu, now=now)
    return f"{d}:{n}"


def _serve(menu, now, ua=PHONE_UA, session=SESSION):
    """`menu` was shown to the device with this User-Agent (stamped and recorded as served)."""
    G._note_served(_Req(ua=ua), session, _show(menu, now))


def _tap(ua=PHONE_UA, key="1"):
    resp = asyncio.run(G.handle_agent_key(_Req({"session": SESSION, "key": key, "confirm": True}, ua=ua)))
    return resp.status, json.loads(resp.text)


def test_a_device_that_was_shown_the_prompt_can_answer_it(screen):
    _serve(CMD_A, now=100.0)
    status, body = _tap()
    assert status == 200 and body["sent"] == "1"
    assert screen["sent"], "the key reached the pane"


def test_a_tap_meant_for_A_does_not_approve_B_that_replaced_it(screen):
    _serve(CMD_A, now=100.0)                  # the device saw A
    screen["menu"] = CMD_B
    _show(CMD_B, now=105.0)                   # another surface stamped B
    status, body = _tap()
    assert status == 409 and body["reason"] == "instance_mismatch"
    assert screen["sent"] == [], "nothing reached the pane"


def test_a_prompt_no_surface_has_shown_yet_is_refused(screen):
    _serve(CMD_A, now=100.0)
    screen["menu"] = CMD_B                    # B is on screen but nothing has stamped it yet
    status, body = _tap()
    assert status == 409 and body["reason"] == "instance_mismatch" and screen["sent"] == []


def test_after_being_shown_the_new_prompt_it_can_be_answered(screen):
    _serve(CMD_A, now=100.0)
    screen["menu"] = CMD_B
    _serve(CMD_B, now=105.0)                  # the device refreshed and saw B
    assert _tap()[0] == 200


def test_nothing_served_on_record_fails_closed_for_permission_prompts(screen):
    _show(CMD_A, now=100.0)
    status, body = _tap()
    assert status == 409 and body["reason"] == "instance_unknown"
    assert "Open it in Approvals" in body["error"]


def test_a_look_at_another_session_does_not_vouch_for_this_one(screen):
    """Review B2: being served session X's prompt says nothing about what it saw on this one."""
    _serve(CMD_B, now=100.0, session="other-seat")
    screen["menu"] = CMD_B
    _show(CMD_B, now=101.0)
    assert _tap()[0] == 409


def test_phone_and_watch_on_one_bearer_do_not_vouch_for_each_other(screen):
    _serve(CMD_A, now=100.0, ua=WATCH_UA)     # the watch last looked when A was up
    screen["menu"] = CMD_B
    _serve(CMD_B, now=105.0, ua=PHONE_UA)     # the phone has seen B
    assert _tap(ua=WATCH_UA)[0] == 409, "the watch's stale card can't answer B"
    assert _tap(ua=PHONE_UA)[0] == 200, "the phone, which saw B, can"


def test_an_install_id_separates_a_phone_and_ipad_on_the_same_build(screen):
    """Same app build = same User-Agent; X-Client-Install is what tells them apart."""
    G._note_served(_Req(install="ipad-1"), SESSION, _show(CMD_A, now=100.0))
    screen["menu"] = CMD_B
    G._note_served(_Req(install="phone-1"), SESSION, _show(CMD_B, now=105.0))

    def tap(install):
        r = _Req({"session": SESSION, "key": "1", "confirm": True}, install=install)
        return asyncio.run(G.handle_agent_key(r)).status

    assert tap("ipad-1") == 409, "the iPad's stale card can't answer B"
    assert tap("phone-1") == 200


def test_without_an_install_id_the_same_build_shares_a_record(screen):
    """The known limit, pinned so it is not mistaken for a guarantee: no install id, same UA."""
    _serve(CMD_A, now=100.0)
    screen["menu"] = CMD_B
    _serve(CMD_B, now=105.0)                  # "the other" device, same User-Agent
    assert _tap()[0] == 200


def test_a_served_record_expires(screen):
    _serve(CMD_A, now=100.0)
    k = next(iter(G._SERVED))
    G._SERVED[k] = (G._SERVED[k][0], G._SERVED[k][1] - G._SERVED_TTL_S - 1)
    assert _tap()[1]["reason"] == "instance_unknown"


def test_the_served_record_is_bounded(screen, monkeypatch):
    monkeypatch.setattr(G, "_SERVED_MAX", 3)
    for i in range(5):
        G._note_served(_Req(ua=f"ua{i}"), SESSION, "d:1")
    assert len(G._SERVED) == 3


def test_non_permission_menus_are_not_gated(screen):
    screen["menu"] = {"kind": "options", "question": "Which?", "options": [{"n": "1", "label": "A"}]}
    status, _ = _tap()
    assert status == 200


def _respond(ua=PHONE_UA):
    resp = asyncio.run(G.handle_agent_key(_Req({"session": SESSION, "answer": "respond",
                                                "text": "do it differently", "confirm": True}, ua=ua)))
    return resp.status, json.loads(resp.text)


def test_the_respond_path_is_gated_too(screen, monkeypatch):
    _show(CMD_A, now=100.0)
    called = []
    monkeypatch.setattr(G, "permission_respond", lambda *a, **k: called.append(1) or (True, {}))
    assert _respond()[0] == 409 and called == []


def test_the_respond_path_never_skips_the_check_on_a_missed_read(screen, monkeypatch):
    """Review B1: no menu on the first read used to skip the check, then a second read acted."""
    screen["menu"] = None
    called = []
    monkeypatch.setattr(G, "permission_respond", lambda *a, **k: called.append(1) or (True, {}))
    status, body = _respond()
    assert status == 409 and body["reason"] == "menu_gone" and called == []


def test_the_respond_path_passes_the_checked_prompt_on(screen, monkeypatch):
    _serve(CMD_A, now=100.0)
    seen = {}
    monkeypatch.setattr(G, "permission_respond",
                        lambda *a, **k: seen.update(k) or (True, {}))
    assert _respond()[0] == 200
    assert seen["expect_digest"] == G._perm_digest(SESSION, CMD_A["question"], CMD_A["context"])


def test_permission_respond_sends_nothing_when_the_prompt_changed_under_it():
    """Review B1: the prompt the gate checked (A) was replaced (B) while the answer waited."""
    keys = []
    ok, info = G.permission_respond(
        SESSION, "do it differently", armed=True,
        read_fn=lambda: dict(CMD_B, options=[{"n": "3", "label": "No, and tell Claude what to do differently"}]),
        key_fn=lambda k: keys.append(k) or True, type_fn=lambda t: keys.append(t) or True,
        gone_fn=lambda: True, settle_s=0,
        expect_digest=G._perm_digest(SESSION, CMD_A["question"], CMD_A["context"]))
    assert not ok and info["reason"] == "instance_mismatch" and keys == []


_TEXT_OPT = [{"n": "1", "label": "Yes"}, {"n": "3", "label": "No, and tell Claude what to do differently"}]


def _respond_with_screens(screens, monkeypatch):
    """permission_respond with the DEFAULT gone check, reading `screens` in order (last repeats)."""
    seq = list(screens)
    keys = []
    monkeypatch.setattr(G, "_mark_instance_answered", lambda *a, **k: None)
    monkeypatch.setattr(G, "INJECT_INGEST_WAIT_S", 0.0)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    ok, info = G.permission_respond(
        SESSION, "do it differently", armed=True,
        read_fn=lambda: seq.pop(0) if len(seq) > 1 else seq[0],
        key_fn=lambda k: keys.append(k) or True, type_fn=lambda t: keys.append(t) or True,
        settle_s=0)
    return ok, info, keys


def test_permission_respond_is_not_verified_while_its_prompt_is_still_up(monkeypatch):
    """Review (agy, #334): the default gone check must not read a STILL-PRESENT prompt as gone."""
    a = dict(CMD_A, options=_TEXT_OPT)
    ok, info, keys = _respond_with_screens([a], monkeypatch)
    assert not ok and info["reason"] == "unverified_submit"


def test_the_next_permission_prompt_is_not_the_answered_one(monkeypatch):
    """The answered prompt (A) is replaced by the NEXT one (B, same question, other command).
    Matching by question alone read B as A still waiting, and the retry Enter approved B."""
    a, b = dict(CMD_A, options=_TEXT_OPT), dict(CMD_B, options=_TEXT_OPT)
    ok, info, keys = _respond_with_screens([a, b], monkeypatch)
    assert ok and info["attempts"] == 1
    assert keys.count("Enter") == 1, keys


def test_both_surfaces_record_what_they_served(screen, monkeypatch):
    class _Store:
        def migrate(self):
            pass

        def pending_to_notify(self):
            return []

        def list_pending(self):
            return []

    monkeypatch.setattr(G, "ApprovalStore", _Store)
    monkeypatch.setattr(G, "QuestionnaireStore", _Store)
    monkeypatch.setattr(G, "_perm_pseudo_rows", lambda: [G._perm_pseudo_row(SESSION, CMD_A)])
    asyncio.run(G.handle_pending(_Req()))
    assert _tap()[0] == 200, "/pending-approvals recorded the row it served"

    G._SERVED.clear()
    monkeypatch.setattr(G, "_capture_pane", lambda *a: "")
    r = _Req(ua=WATCH_UA)
    r.query = {"session": SESSION}
    asyncio.run(G.handle_agent_screen(r))
    assert _tap(ua=WATCH_UA)[0] == 200, "/agent-screen recorded the menu it served"
    assert _tap(ua=PHONE_UA)[0] == 409


def test_a_refused_fetch_records_nothing(screen):
    class _Bad(_Req):
        def __init__(self):
            super().__init__()
            self.headers = {"User-Agent": PHONE_UA}

    asyncio.run(G.handle_pending(_Bad()))
    assert len(G._SERVED) == 0


def test_the_instance_ledger_records_when_each_instance_was_first_seen(screen):
    _show(CMD_A, now=100.0)
    led = G._load_instance_ledger()
    (entry,) = led.values()
    assert entry["first_seen_ts"] == 100.0
    _show(CMD_A, now=150.0)                   # still the same instance: unchanged
    (entry,) = G._load_instance_ledger().values()
    assert entry["first_seen_ts"] == 100.0


def test_two_isolated_misses_do_not_make_a_new_instance(screen):
    """Review B3: one missed scrape, a long run of sightings, another miss: still instance 1."""
    led = {}
    G._instance_step(led, SESSION, "d", present=True, now=0)
    G._instance_step(led, SESSION, "d", present=False, now=1)
    for t in range(2, 101):
        G._instance_step(led, SESSION, "d", present=True, now=t)
    G._instance_step(led, SESSION, "d", present=False, now=101)
    assert G._instance_step(led, SESSION, "d", present=True, now=102) == 1


def test_a_session_the_fast_path_skipped_is_not_reaped(screen, monkeypatch):
    """Review B3: an unflagged session was not looked at, so its prompt must not count as gone."""
    _show(CMD_A, now=100.0)
    monkeypatch.setitem(G._agents_cache, "data", [{"id": SESSION, "has_pending_menu": False}])
    G._perm_pseudo_rows()                     # a sweep that read no session
    (entry,) = G._load_instance_ledger().values()
    assert entry["phase"] == "present" and entry["gone_since"] is None


def test_the_same_prompt_coming_back_as_a_NEW_instance_needs_a_fresh_look(screen):
    _serve(CMD_A, now=100.0)
    G._mark_instance_answered(SESSION, CMD_A["question"], CMD_A["context"])   # answered elsewhere
    _show(CMD_A, now=105.0)                   # the same ask again: a new instance
    assert _tap()[0] == 409
    _serve(CMD_A, now=106.0)
    assert _tap()[0] == 200


def test_ledger_writers_never_share_a_temp_file(screen, tmp_path):
    import threading
    errs = []

    def stamp(i):
        try:
            for j in range(20):
                G._stamp_instance(f"s{i}", _menu(f"cmd {i} {j}"), now=float(j))
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=stamp, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    assert len(G._load_instance_ledger()) == 6 * 20, "no writer lost another's entries"
    assert not list(tmp_path.glob("*.tmp")), "no temp file left behind"


def test_the_off_switch_logs_and_allows_without_a_restart(screen, monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    _show(CMD_A, now=100.0)                   # nothing served on record: would be refused
    with caplog.at_level("WARNING", logger="watch_gateway"):
        assert _tap()[0] == 409
        assert "refused" in caplog.text and "reason=instance_unknown" in caplog.text
        (tmp_path / "state").mkdir(parents=True, exist_ok=True)
        (tmp_path / "state" / "menu-stale-tap.off").write_text("")
        caplog.clear()
        assert _tap()[0] == 200, "flipped off: allowed"
        assert "WOULD REFUSE (check off)" in caplog.text
        (tmp_path / "state" / "menu-stale-tap.off").unlink()
        assert _tap()[0] == 409, "flipped back on, still no restart"


# --- review round 2 ------------------------------------------------------------------------------

def test_an_identical_prompt_back_after_an_answer_is_a_new_instance_even_unpolled(screen):
    """Round 2 B3: shown A#1, A#1 answered elsewhere, identical A#2 up, tap before any poll."""
    _serve(CMD_A, now=100.0)
    G._mark_instance_answered(SESSION, CMD_A["question"], CMD_A["context"])
    status, body = _tap()
    assert status == 409 and body["reason"] == "instance_mismatch" and screen["sent"] == []


def test_the_fleet_sweep_reaps_a_self_cleared_prompt_so_its_return_is_new(screen, monkeypatch, tmp_path):
    """Round 2 B2: the /pending-approvals fast path reads only flagged sessions, so the /agents
    sweep (which reads every session) is where a self-cleared prompt is observed gone."""
    import time
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "_live_voice_call", lambda: None)
    t0 = time.time()
    _serve(CMD_A, now=t0)
    screen["menu"] = None                     # answered in the terminal: no answer signal
    G.compute_agents()                        # sweep 1: absent (gone_since set)
    led = G._load_instance_ledger()
    for e in led.values():                    # the flap grace has passed
        e["gone_since"] -= G.PERM_INSTANCE_FLAP_GRACE_S + 1
    G._save_instance_ledger(G._PERM_INSTANCE_LEDGER, led)
    G.compute_agents()                        # sweep 2: still absent -> gone
    (entry,) = G._load_instance_ledger().values()
    assert entry["phase"] == "gone"
    screen["menu"] = CMD_A                    # the identical prompt is back
    status, body = _tap()
    assert status == 409 and body["reason"] == "instance_mismatch"


def test_the_fleet_sweep_sees_a_present_prompt_as_present(screen, monkeypatch, tmp_path):
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "_live_voice_call", lambda: None)
    _serve(CMD_A, now=100.0)
    for _ in range(3):
        G.compute_agents()
    (entry,) = G._load_instance_ledger().values()
    assert entry["phase"] == "present" and entry["instance_n"] == 1
    assert _tap()[0] == 200


def test_respond_refuses_when_the_screen_is_not_a_permission_prompt(screen, monkeypatch):
    """Round 2 B1: first read an options menu, then permission B: nothing may be typed."""
    _serve(CMD_A, now=100.0)
    screen["menu"] = {"kind": "options", "question": "Which?", "options": [{"n": "1", "label": "x"}]}
    called = []
    monkeypatch.setattr(G, "permission_respond", lambda *a, **k: called.append(1) or (True, {}))
    status, body = _respond()
    assert status == 400 and body["reason"] == "not_permission_prompt" and called == []


def test_a_permission_tap_does_not_land_on_the_menu_that_followed(screen):
    """Round 2 should-fix: the device last saw permission A; an options menu is up now."""
    _serve(CMD_A, now=100.0)
    screen["menu"] = {"kind": "options", "question": "Deploy?", "options": [{"n": "1", "label": "Yes"}]}
    status, body = _tap()
    assert status == 409 and body["reason"] == "instance_mismatch" and screen["sent"] == []


def test_a_device_that_was_shown_the_other_menu_can_answer_it(screen, monkeypatch):
    _serve(CMD_A, now=100.0)
    screen["menu"] = {"kind": "options", "question": "Deploy?", "options": [{"n": "1", "label": "Yes"}]}
    monkeypatch.setattr(G, "_capture_pane", lambda *a: "")
    r = _Req()
    r.query = {"session": SESSION}
    asyncio.run(G.handle_agent_screen(r))     # the chat card now shows the options menu
    assert _tap()[0] == 200


def test_the_digit_path_rereads_under_the_lock(screen, monkeypatch):
    """Round 2 should-fix: B replaces A between the check and the locked send: zero keys."""
    _serve(CMD_A, now=100.0)
    reads = [CMD_A, CMD_B]

    class _AS:
        def get_agent_status(self, session):
            return {"pending_menu": reads.pop(0) if reads else CMD_B, "state": "waiting"}

    monkeypatch.setattr(G, "_agent_status", lambda: _AS())
    status, body = _tap()
    assert status == 409 and body["reason"] == "instance_mismatch" and screen["sent"] == []


def test_the_send_lock_is_taken_off_the_event_loop(screen, monkeypatch):
    """Round 2 should-fix: holding the lock across an await on the loop thread could hang the
    gateway, so both answer paths take it on a worker thread."""
    import threading
    _serve(CMD_A, now=100.0)
    main = threading.get_ident()
    takers = []

    class _Rec:
        def __init__(self):
            self._lk = threading.Lock()

        def __enter__(self):
            takers.append(threading.get_ident())
            return self._lk.__enter__()

        def __exit__(self, *a):
            return self._lk.__exit__(*a)

    rec = _Rec()
    monkeypatch.setattr(G, "_session_send_lock", lambda s: rec)
    monkeypatch.setattr(G, "permission_respond", lambda *a, **k: (True, {}))
    assert _respond()[0] == 200
    assert _tap()[0] == 200
    assert len(takers) == 2 and main not in takers


def test_a_sweep_sighting_clears_an_earlier_miss(screen, monkeypatch, tmp_path):
    """Round 2: miss, sighting, miss (sweeps far apart) is still ONE instance."""
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "_live_voice_call", lambda: None)
    import time
    _serve(CMD_A, now=time.time())
    screen["menu"] = None
    G.compute_agents()                        # a missed scrape
    led = G._load_instance_ledger()
    for e in led.values():
        e["gone_since"] -= G.PERM_INSTANCE_FLAP_GRACE_S + 1
    G._save_instance_ledger(G._PERM_INSTANCE_LEDGER, led)
    screen["menu"] = CMD_A
    G.compute_agents()                        # seen again
    screen["menu"] = None
    G.compute_agents()                        # another isolated miss
    (entry,) = G._load_instance_ledger().values()
    assert entry["phase"] == "present" and entry["instance_n"] == 1


def test_every_refusal_is_logged_with_its_reason_device_and_instances(screen, caplog):
    """gm go-live condition: wrong refusals are the main risk, so each one is readable."""
    _serve(CMD_A, now=100.0)
    screen["menu"] = CMD_B
    with caplog.at_level("WARNING", logger="watch_gateway"):
        assert _tap()[0] == 409
    line = next(r.getMessage() for r in caplog.records if "[stale-tap]" in r.getMessage())
    assert "refused" in line and f"session={SESSION}" in line and "reason=instance_mismatch" in line
    assert "device=" in line and "served=" in line and "current=" in line


# --- review round 3 ------------------------------------------------------------------------------
WEB_UA = "node"
OPTIONS_M = {"kind": "options", "question": "Deploy?", "options": [{"n": "1", "label": "Yes"}]}


def _web_tap(expect, key="1"):
    """The dashboard: the API posts /agent-key from Node with the fleet bearer, and renders menus
    from /api/agents, so the gateway has nothing served on record for it."""
    body = {"session": SESSION, "key": key, "confirm": True}
    if expect is not None:
        body["expect"] = expect
    resp = asyncio.run(G.handle_agent_key(_Req(body, ua=WEB_UA)))
    return resp.status, json.loads(resp.text)


def test_the_web_answers_the_permission_prompt_it_rendered(screen):
    """Round 3 B1: the web card says what it rendered; that, not a served record, is checked."""
    _show(CMD_A, now=100.0)
    status, body = _web_tap({"question": CMD_A["question"], "context": CMD_A["context"]})
    assert status == 200 and screen["sent"]


def test_the_web_tap_for_A_does_not_approve_B(screen):
    _show(CMD_A, now=100.0)
    screen["menu"] = CMD_B
    status, body = _web_tap({"question": CMD_A["question"], "context": CMD_A["context"]})
    assert status == 409 and body["reason"] == "instance_mismatch" and screen["sent"] == []


def test_an_expect_for_an_options_menu_is_checked_too(screen):
    screen["menu"] = OPTIONS_M
    assert _web_tap({"question": "Deploy?"})[0] == 200
    screen["sent"].clear()
    status, body = _web_tap({"question": "Something else?"})
    assert status == 409 and screen["sent"] == []


def test_without_expect_the_web_has_nothing_on_record(screen):
    _show(CMD_A, now=100.0)
    status, body = _web_tap(None)
    assert status == 409 and body["reason"] == "instance_unknown"


def test_a_malformed_expect_is_refused_not_ignored(screen):
    _show(CMD_A, now=100.0)
    for bad in ("A", {"context": "x"}, {"question": 3}, {"question": "q", "context": 4},
                {"question": "q" * (64 * 1024 + 1)}):
        status, body = _web_tap(bad)
        assert status == 400, bad
    assert screen["sent"] == []


def test_respond_honours_expect(screen, monkeypatch):
    _show(CMD_A, now=100.0)
    seen = {}
    monkeypatch.setattr(G, "permission_respond", lambda *a, **k: seen.update(k) or (True, {}))
    body = {"session": SESSION, "answer": "respond", "text": "differently", "confirm": True,
            "expect": {"question": CMD_A["question"], "context": CMD_A["context"]}}
    assert asyncio.run(G.handle_agent_key(_Req(body, ua=WEB_UA))).status == 200
    body["expect"] = {"question": CMD_B["question"], "context": CMD_B["context"]}
    assert asyncio.run(G.handle_agent_key(_Req(body, ua=WEB_UA))).status == 409


def test_a_digit_for_an_options_menu_does_not_land_on_the_permission_prompt_that_replaced_it(
        screen, monkeypatch):
    """Round 3 B2: the locked re-read covers every kind, not only permission prompts."""
    reads = [OPTIONS_M, CMD_B]

    class _AS:
        def get_agent_status(self, session):
            return {"pending_menu": reads.pop(0) if reads else CMD_B, "state": "waiting"}

    monkeypatch.setattr(G, "_agent_status", lambda: _AS())
    status, body = _tap()
    assert status == 409 and body["reason"] == "instance_mismatch" and screen["sent"] == []


def test_a_menu_gone_by_the_locked_read_sends_nothing(screen, monkeypatch):
    _serve(CMD_A, now=100.0)
    reads = [CMD_A, None]

    class _AS:
        def get_agent_status(self, session):
            return {"pending_menu": reads.pop(0) if reads else None, "state": "waiting"}

    monkeypatch.setattr(G, "_agent_status", lambda: _AS())
    status, body = _tap()
    assert status == 409 and body["reason"] == "menu_gone" and screen["sent"] == []


def test_a_long_command_fits_in_expect(screen):
    """Round 4: the detector keeps up to 120 pane lines of context, far over 8000 characters."""
    long_cmd = _menu("x" * 20_000)
    screen["menu"] = long_cmd
    _show(long_cmd, now=100.0)
    assert _web_tap({"question": long_cmd["question"], "context": long_cmd["context"]})[0] == 200


def test_a_rewrapped_question_is_still_the_same_menu(screen):
    """Round 4: a resize breaks a long path mid-token; the comparison must not see a new menu."""
    wide = dict(OPTIONS_M, question="Overwrite /very/long/path/to/some/file.txt?")
    narrow = dict(OPTIONS_M, question="Overwrite /very/long/path/to/some/fi le.txt?")
    screen["menu"] = narrow
    assert _web_tap({"question": wide["question"]})[0] == 200


# --- expect ADDS to the served record, never replaces it -----------------------------------------

def _app_tap_with_expect(menu, ua=PHONE_UA):
    body = {"session": SESSION, "key": "1", "confirm": True,
            "expect": {"question": menu["question"], "context": menu["context"]}}
    resp = asyncio.run(G.handle_agent_key(_Req(body, ua=ua)))
    return resp.status, json.loads(resp.text)


def test_expect_does_not_let_an_identical_re_ask_through_for_a_device_with_a_record(screen):
    """expect checks content; an identical re-asked prompt has the same content. A device the
    gateway served A#1 to must still be refused on A#2, as it is without expect."""
    _serve(CMD_A, now=100.0)                                   # the phone was shown A#1
    G._mark_instance_answered(SESSION, CMD_A["question"], CMD_A["context"])   # answered elsewhere
    status, body = _app_tap_with_expect(CMD_A)                 # A#2 is up, same content
    assert status == 409 and body["reason"] == "instance_mismatch" and screen["sent"] == []


def test_expect_with_a_matching_record_answers(screen):
    _serve(CMD_A, now=100.0)
    assert _app_tap_with_expect(CMD_A)[0] == 200


def test_expect_still_refuses_the_wrong_content_even_with_a_matching_record(screen):
    _serve(CMD_A, now=100.0)
    screen["menu"] = CMD_B
    _serve(CMD_B, now=101.0)                                   # the record is current (B)
    status, body = _app_tap_with_expect(CMD_A)                 # but the card the client rendered is A
    assert status == 409 and body["reason"] == "instance_mismatch" and screen["sent"] == []


def test_expect_does_not_let_a_re_ask_through_after_the_device_last_saw_another_menu(screen, monkeypatch):
    """Review: served A#1, answered elsewhere, the chat view then saw an options menu ("other"),
    an identical A#2 is up: refused, as it is without expect."""
    _serve(CMD_A, now=100.0)
    G._mark_instance_answered(SESSION, CMD_A["question"], CMD_A["context"])
    screen["menu"] = OPTIONS_M
    monkeypatch.setattr(G, "_capture_pane", lambda *a: "")
    r = _Req()
    r.query = {"session": SESSION}
    asyncio.run(G.handle_agent_screen(r))                      # record becomes "other"
    screen["menu"] = CMD_A                                     # A#2, identical
    status, body = _app_tap_with_expect(CMD_A)
    assert status == 409 and body["reason"] == "instance_mismatch" and screen["sent"] == []


def test_expect_alone_after_the_record_expired(screen):
    _serve(CMD_A, now=100.0)
    k = next(iter(G._SERVED))
    G._SERVED[k] = (G._SERVED[k][0], G._SERVED[k][1] - G._SERVED_TTL_S - 1)
    assert _app_tap_with_expect(CMD_A)[0] == 200


# --- menu instance identity phase 0: the hook id rides the wire, the served record stays the ledger's ---

def test_a_hook_instance_on_the_row_does_not_break_the_served_record(screen, monkeypatch):
    """The row's instance_id becomes the CLI tool_use_id when the hook proves one; the served record
    must keep the LEDGER instance (digest:n), or every tap would mismatch."""
    class _Store:
        def migrate(self):
            pass

        def pending_to_notify(self):
            return []

        def list_pending(self):
            return []

    monkeypatch.setattr(G, "_menu_hook_instance", lambda session, menu: "toolu_01PROVEN" if menu else None)
    monkeypatch.setattr(G, "ApprovalStore", _Store)
    monkeypatch.setattr(G, "QuestionnaireStore", _Store)
    row = G._perm_pseudo_row(SESSION, CMD_A)
    assert row["instance"] == row["menu"]["instance"] == "toolu_01PROVEN"
    assert row["instance_id"] != "toolu_01PROVEN" and ":" in row["instance_id"]     # still the ledger's
    monkeypatch.setattr(G, "_perm_pseudo_rows", lambda: [row])
    asyncio.run(G.handle_pending(_Req()))
    assert _tap()[0] == 200, "/pending-approvals recorded the ledger instance"

    G._SERVED.clear()
    monkeypatch.setattr(G, "_capture_pane", lambda *a: "")
    r = _Req(ua=WATCH_UA)
    r.query = {"session": SESSION}
    resp = asyncio.run(G.handle_agent_screen(r))
    pm = json.loads(resp.text)["pending_menu"]
    assert pm["instance"] == "toolu_01PROVEN" and ":" in pm["instance_id"]
    assert _tap(ua=WATCH_UA)[0] == 200, "/agent-screen recorded the ledger instance"


def test_without_a_proven_instance_the_screen_emits_none(screen, monkeypatch):
    monkeypatch.setattr(G, "_menu_hook_instance", lambda session, menu: None)
    monkeypatch.setattr(G, "_capture_pane", lambda *a: "")
    r = _Req()
    r.query = {"session": SESSION}
    pm = json.loads(asyncio.run(G.handle_agent_screen(r)).text)["pending_menu"]
    assert "instance" not in pm and ":" in pm["instance_id"]
