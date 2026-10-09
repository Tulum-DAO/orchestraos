"""Menu instance identity, phase 0 (DEC-1791405753559307): the hook's open-call map, the resolver,
the sticky memo, and where the gateway emits `instance`. Every path points at tmp dirs."""
import importlib.util, json, os, subprocess, sys, threading, asyncio, time, pytest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import menu_instance as MI

HOOK = os.path.join(os.path.dirname(HERE), "hooks", "state-event-hook.py")
_spec = importlib.util.spec_from_file_location("state_event_hook", HOOK)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    ev = tmp_path / "agent-events" / "panes"
    monkeypatch.setenv("ORCH_EVENTS_DIR", str(ev))
    monkeypatch.delenv("ORCH_CALLS_DIR", raising=False)
    return ev, tmp_path / "agent-events" / "calls"


def _hook_mod():
    m = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(m)
    return m


def _fire(payload, pane="%7"):
    """Run the REAL hook script the way the CLI does: stdin JSON, TMUX_PANE in env."""
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, env={**os.environ, "TMUX_PANE": pane}, timeout=10)
    assert r.returncode == 0 and r.stdout == ""          # HARD CONTRACT: exit 0, silent
    return r


def _pre(tid, tool="Bash", **inp):
    return {"hook_event_name": "PreToolUse", "tool_use_id": tid, "tool_name": tool, "tool_input": inp}


def _calls(calls_dir, pane="%7"):
    try:
        return json.loads((calls_dir / (pane.lstrip("%") + ".json")).read_text())
    except FileNotFoundError:
        return {}


AUQ = dict(questions=[{"question": "Which dashboards keep access?", "options": []}])


# ---- hook --------------------------------------------------------------------------------------

def test_pre_adds_post_drops_and_the_pane_state_file_is_still_written(dirs):
    ev, calls = dirs
    _fire(_pre("toolu_A", "AskUserQuestion", **AUQ))
    c = _calls(calls)
    assert list(c) == ["toolu_A"] and c["toolu_A"]["questions"] == ["Which dashboards keep access?"]
    assert json.loads((ev / "7.json").read_text())["event"] == "PreToolUse"      # old contract intact
    _fire({"hook_event_name": "PostToolUse", "tool_use_id": "toolu_A", "tool_name": "AskUserQuestion"})
    assert _calls(calls) == {}


@pytest.mark.parametrize("event", ["SessionStart", "SessionEnd"])
def test_session_boundaries_clear_calls(dirs, event):
    _, calls = dirs
    _fire(_pre("toolu_A", command="rm x"))
    _fire({"hook_event_name": event})
    assert _calls(calls) == {}


@pytest.mark.parametrize("event", ["Stop", "UserPromptSubmit"])
def test_stop_does_NOT_clear_calls_a_background_subagent_still_owns(dirs, event):
    # Review: clearing on Stop let a later same-tool call own a subagent's menu (a WRONG id).
    _, calls = dirs
    _fire(_pre("toolu_SUB", command="npm test"))
    _fire({"hook_event_name": event})
    assert list(_calls(calls)) == ["toolu_SUB"]


def test_the_hook_stores_digests_and_basenames_never_the_raw_input(dirs):
    _, calls = dirs
    _fire(_pre("toolu_B", command="echo SECRET-TOKEN-VALUE"))
    _fire(_pre("toolu_C", "Edit", file_path="/home/x/proj/edit_target.txt", old_string="a", new_string="b"))
    raw = (calls / "7.json").read_text()
    assert "SECRET-TOKEN-VALUE" not in raw and "/home/x/proj" not in raw
    assert _calls(calls)["toolu_C"]["file"] == "edit_target.txt"


def test_garbage_stdin_and_no_pane_are_silent_exit_0(dirs):
    r = subprocess.run([sys.executable, HOOK], input="{not json",
                       capture_output=True, text=True, env={**os.environ, "TMUX_PANE": "%7"}, timeout=10)
    assert r.returncode == 0 and r.stdout == ""
    env = {k: v for k, v in os.environ.items() if k != "TMUX_PANE"}
    r = subprocess.run([sys.executable, HOOK],
                       input=json.dumps(_pre("toolu_X")), capture_output=True, text=True, env=env, timeout=10)
    assert r.returncode == 0 and r.stdout == ""


def test_cap_and_ttl(dirs):
    _, calls = dirs
    H = _hook_mod()
    for n in range(H.CALLS_CAP + 4):
        H.update_open_calls("%7", _pre(f"toolu_{n}"), "PreToolUse", now=1000.0 + n)
    c = _calls(calls)
    assert len(c) == H.CALLS_CAP and "toolu_0" not in c and f"toolu_{H.CALLS_CAP + 3}" in c
    H.update_open_calls("%7", _pre("toolu_late"), "PreToolUse", now=1000.0 + H.CALLS_TTL_S + 100)
    assert list(_calls(calls)) == ["toolu_late"]


def test_concurrent_pre_tool_use_from_parallel_tools_loses_nothing(dirs):
    _, calls = dirs
    H = _hook_mod()
    ts = [threading.Thread(target=H.update_open_calls, args=("%7", _pre(f"toolu_{n}"), "PreToolUse"))
          for n in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(_calls(calls)) == 12


# ---- resolver (pure) ---------------------------------------------------------------------------

def _auq_menu(q="Which dashboards keep access?", **kw):
    return {"kind": "options", "menu_family": "claude", "question": q,
            "options": [{"n": 1, "label": "A"}, {"n": 2, "label": "B"}], **kw}


def _perm(q="Do you want to proceed? [Bash]"):
    return {"kind": "permission", "question": q, "options": [{"n": 1, "label": "Yes"}, {"n": 2, "label": "No"}]}


def test_auq_menu_takes_the_one_matching_open_call():
    calls = {"toolu_A": {"tool": "AskUserQuestion", "questions": ["Which dashboards keep access?"]},
             "toolu_B": {"tool": "Bash", "cmd": "x"}}
    assert MI.resolve(_auq_menu(), calls) == "toolu_A"


def test_a_truncated_screen_question_still_matches():
    calls = {"toolu_A": {"tool": "AskUserQuestion", "questions": ["Which dashboards keep access after the migration?"]}}
    assert MI.resolve(_auq_menu("Which dashboards keep access…"), calls) == "toolu_A"


def test_two_candidates_or_none_means_NO_instance_never_a_guess():
    two = {"toolu_A": {"tool": "AskUserQuestion", "questions": ["Which dashboards keep access?"]},
           "toolu_B": {"tool": "AskUserQuestion", "questions": ["Which dashboards keep access?"]}}
    assert MI.resolve(_auq_menu(), two) is None
    assert MI.resolve(_auq_menu("Something else entirely?"), two) is None
    assert MI.resolve(_auq_menu(), {}) is None


def test_permission_by_tool_suffix_and_the_running_agent_call_is_never_the_owner():
    calls = {"toolu_AG": {"tool": "Agent"}, "toolu_B": {"tool": "Bash", "cmd": "x"}}
    assert MI.resolve(_perm(), calls) == "toolu_B"
    assert MI.resolve(_perm("Do you want to proceed? [Read]"), calls) is None


def test_edit_prompt_has_no_suffix_so_it_matches_by_file_basename():
    calls = {"toolu_W": {"tool": "Write", "file": "other.txt", "ts": 1}, "toolu_E": {"tool": "Edit", "file": "edit_target.txt", "ts": 2}}
    assert MI.resolve(_perm("Do you want to make this edit to edit_target.txt?"), calls) == "toolu_E"


def test_agy_menus_carry_no_instance():
    calls = {"toolu_A": {"tool": "AskUserQuestion", "questions": ["Which dashboards keep access?"]}}
    assert MI.resolve(_auq_menu(menu_family="agy"), calls) is None


# ---- memo --------------------------------------------------------------------------------------

def test_same_menu_keeps_its_first_decision_even_none():
    m = MI.InstanceMemo()
    assert m.stamp("s", _auq_menu(), {}) is None
    late = {"toolu_A": {"tool": "AskUserQuestion", "questions": ["Which dashboards keep access?"]}}
    assert m.stamp("s", _auq_menu(), late) is None          # never None -> id on the same card


def test_answer_A_then_identical_B_within_one_poll_gets_a_NEW_instance():
    # The memo is invalidated when its call closes, not on a poll.
    m = MI.InstanceMemo()
    a = {"toolu_A": {"tool": "Bash", "cmd": "x"}}
    assert m.stamp("s", _perm(), a) == "toolu_A"
    b = {"toolu_B": {"tool": "Bash", "cmd": "x"}}           # A's PostToolUse fired, B's PreToolUse fired
    assert m.stamp("s", _perm(), b) == "toolu_B"


def test_a_closed_call_is_never_served_again_even_with_no_new_candidate():
    m = MI.InstanceMemo()
    assert m.stamp("s", _perm(), {"toolu_A": {"tool": "Bash"}}) == "toolu_A"
    assert m.stamp("s", _perm(), {}) is None


def test_multi_part_walk_and_review_screen_keep_one_instance():
    m = MI.InstanceMemo()
    calls = {"toolu_A": {"tool": "AskUserQuestion", "questions": ["Lock the API down now?", "Which dashboards keep access?"]}}
    assert m.stamp("s", _auq_menu("Lock the API down now?"), calls) == "toolu_A"
    assert m.stamp("s", _auq_menu("Which dashboards keep access?"), calls) == "toolu_A"
    assert m.stamp("s", _auq_menu("Review your answers", has_submit=True), calls) == "toolu_A"


def test_no_menu_resets_the_session():
    m = MI.InstanceMemo()
    assert m.stamp("s", _auq_menu(), {}) is None
    m.stamp("s", None, {})
    calls = {"toolu_A": {"tool": "AskUserQuestion", "questions": ["Which dashboards keep access?"]}}
    assert m.stamp("s", _auq_menu(), calls) == "toolu_A"


def test_instance_for_reads_the_pane_file_and_fails_to_none(dirs, monkeypatch):
    _, calls = dirs
    calls.mkdir(parents=True)
    (calls / "9.json").write_text(json.dumps({"toolu_A": {"tool": "Bash"}}))
    monkeypatch.setattr(MI, "MEMO", MI.InstanceMemo())
    assert MI.instance_for("s", _perm(), pane="%9") == "toolu_A"
    monkeypatch.setattr(MI, "read_open_calls", lambda pane: 1 / 0)
    assert MI.instance_for("s2", _perm(), pane="%9") is None


# ---- gateway -----------------------------------------------------------------------------------

import watch_gateway as G


class _Req(dict):
    def __init__(self, principal, query=None):
        super().__init__(principal=principal)
        self.headers = {"Authorization": "Bearer x"}
        self.query = query or {}
        self.match_info = {}


DEVICE = {"id": "dev_test_quest", "label": "quest", "scopes": ["read"]}
FLEET = {"id": "legacy", "label": "fleet", "scopes": ["*"]}


def test_perm_pseudo_row_carries_instance_and_keeps_instance_id_the_ledgers(monkeypatch):
    # instance_id is a SHIPPED field (the card's render identity): its meaning never changes.
    monkeypatch.setattr(G, "_menu_hook_instance", lambda session, menu: "toolu_B")
    monkeypatch.setattr(G, "_stamp_instance", lambda *a, **k: (1, "abcd"))
    row = G._perm_pseudo_row("gm", _perm())
    assert row["instance"] == row["menu"]["instance"] == "toolu_B"
    assert row["instance_id"] == "abcd:1"
    assert row["id"].startswith("perm:gm:abcd:")             # the answer path's id is unchanged


def test_menu_instance_endpoint_is_fleet_only_and_returns_the_signature(monkeypatch):
    monkeypatch.setattr(G, "_authorized", lambda r: True)
    monkeypatch.setattr(G, "_tmux_session_names", lambda: ["gm"])

    class _St:
        def get_agent_status(self, s):
            return {"pending_menu": _perm()}
    monkeypatch.setattr(G, "_agent_status", lambda: _St())
    monkeypatch.setattr(G, "_menu_hook_instance", lambda session, menu: "toolu_B" if menu else None)
    r = asyncio.run(G.handle_menu_instance(_Req(DEVICE, {"session": "gm"})))
    assert r.status == 403
    monkeypatch.setattr(MI, "decision_for", lambda session, menu: {"instance": "toolu_B", "signature": MI.signature(menu),
                                                                    "question": "q", "tool": "Bash", "decided_at": 1.0,
                                                                    "sticky": False})
    r = asyncio.run(G.handle_menu_instance(_Req(FLEET, {"session": "gm"})))
    body = json.loads(r.text)
    assert r.status == 200 and body["instance"] == "toolu_B" and body["signature"] == MI.signature(_perm())
    assert {"question", "tool", "decided_at", "sticky", "kind"} <= set(body)
    monkeypatch.setattr(MI, "decision_for", lambda session, menu: None)
    r = asyncio.run(G.handle_menu_instance(_Req(FLEET, {"session": "gm"})))
    assert r.status == 503 and json.loads(r.text)["reason"] == "resolver_failed"   # never a null that reads "none"
    assert G.ROUTE_SCOPES[("GET", "/menu-instance")] == "read"


def test_perm_pseudo_row_without_a_hook_instance_keeps_the_ledger_value(monkeypatch):
    monkeypatch.setattr(G, "_menu_hook_instance", lambda session, menu: None)
    monkeypatch.setattr(G, "_stamp_instance", lambda *a, **k: (3, "abcd"))
    row = G._perm_pseudo_row("gm", _perm())
    assert row["instance_id"] == "abcd:3" and "instance" not in row


def test_decision_reports_what_it_resolved_against_and_whether_it_was_sticky():
    m = MI.InstanceMemo()
    calls = {"toolu_A": {"tool": "Bash"}}
    d1 = m.decide("s", _perm(), calls)
    d2 = m.decide("s", _perm(), calls)
    assert d1["instance"] == d2["instance"] == "toolu_A" and d1["signature"] == MI.signature(_perm())
    assert d1["sticky"] is False and d2["sticky"] is True and d2["decided_at"] == d1["decided_at"]
    assert d1["tool"] == "Bash" and m.decide("s", None, {}) is None


class _BodyReq(_Req):
    def __init__(self, principal, body):
        super().__init__(principal)
        self._body = body

    async def json(self):
        return self._body


def test_menu_capture_returns_the_walk_instance_but_never_hydrates_it_onto_rows(monkeypatch):
    hydrated = []

    class _Store:
        def migrate(self): pass
        def cached_hydration(self, session): return None
        def hydrate_menu(self, session, out): hydrated.append(dict(out))
    walk = {"question": "Lock the API down now?", "kind": "options", "walk_complete": True, "part_count": 2,
            "parts": [{"index": 0, "question": "Lock the API down now?"}, {"index": 1, "question": "Which?"}]}
    monkeypatch.setattr(G, "_authorized", lambda r: True)
    monkeypatch.setattr(G, "_tmux_session_names", lambda: ["gm"])
    monkeypatch.setattr(G, "_protected_refusal", lambda r, s: None)
    monkeypatch.setattr(G, "ApprovalStore", lambda: _Store())
    monkeypatch.setattr(G, "menu_capture_walk", lambda session: dict(walk))
    monkeypatch.setattr(G, "_menu_hook_instance", lambda session, menu: "toolu_W")
    r = asyncio.run(G.handle_agent_menu_capture(_BodyReq(FLEET, {"session": "gm"})))
    body = json.loads(r.text)
    assert r.status == 200 and body["instance"] == "toolu_W" and body["walk_complete"] is True
    assert hydrated and "instance" not in hydrated[0]      # until hydration binds to the instance


# ---- review leg on 2e3d33a5: never a WRONG instance ----------------------------------------------

def test_identical_prompt_from_a_concurrent_call_while_A_is_still_open_gets_NO_instance():
    # A approved and still running; a subagent's same-prefix Bash raises an identical prompt at once.
    m = MI.InstanceMemo()
    assert m.stamp("s", _perm(), {"toolu_A": {"tool": "Bash", "ts": 1.0}}) == "toolu_A"
    later = m._by_session["s"]["decided_at"] + 5
    both = {"toolu_A": {"tool": "Bash", "ts": 1.0}, "toolu_B": {"tool": "Bash", "ts": later}}
    assert m.stamp("s", _perm(), both) is None
    assert m.stamp("s", _perm(), both) is None                  # and stays none: no flip back


def test_a_permission_menu_belongs_only_to_the_NEWEST_open_tool_call():
    # [Bash] read off a "● Bash(npm test)" line above a WebFetch prompt must not pick the running Bash.
    calls = {"toolu_BASH": {"tool": "Bash", "ts": 1}, "toolu_WEB": {"tool": "WebFetch", "ts": 2}}
    assert MI.resolve(_perm("Do you want to proceed? [Bash]"), calls) is None
    assert MI.resolve(_perm("Do you want to proceed? [WebFetch]"), calls) == "toolu_WEB"


def test_a_suffix_lost_to_truncation_is_ambiguous_with_two_open_calls_and_resolves_with_one():
    q = "Do you want to proceed with a very long…"
    two = {"toolu_OLD": {"tool": "Bash", "ts": 1}, "toolu_NEW": {"tool": "Read", "ts": 2},
           "toolu_AG": {"tool": "Agent", "ts": 3}}
    assert MI.resolve(_perm(q), two) is None
    one = {"toolu_NEW": {"tool": "Read", "ts": 2}, "toolu_AG": {"tool": "Agent", "ts": 3}}
    assert MI.resolve(_perm(q), one) == "toolu_NEW"


def test_the_hook_stores_no_command_digest():
    H = _hook_mod()
    assert "cmd" not in H._call_entry({"tool_name": "Bash", "tool_input": {"command": "ls"}}, 1.0)


# ---- review cases: two wrong-id defects found in an earlier draft ----------------------------------

def test_two_same_tool_calls_open_at_once_are_ambiguous_never_the_newer_one():
    # Concurrency-safe tools run in parallel: both PreToolUse fire before the first prompt.
    calls = {"toolu_a": {"tool": "WebFetch", "ts": 1}, "toolu_b": {"tool": "WebFetch", "ts": 2}}
    assert MI.resolve(_perm("Do you want to proceed? [WebFetch]"), calls) is None
    m = MI.InstanceMemo()
    assert m.stamp("s", _perm("Do you want to proceed? [WebFetch]"), calls) is None


def test_an_unrelated_background_call_does_not_flip_the_card():
    m = MI.InstanceMemo()
    assert m.stamp("s", _perm(), {"toolu_x": {"tool": "Bash", "ts": 1.0}}) == "toolu_x"
    later = m._by_session["s"]["decided_at"] + 5
    with_read = {"toolu_x": {"tool": "Bash", "ts": 1.0}, "toolu_r": {"tool": "Read", "ts": later}}
    assert m.stamp("s", _perm(), with_read) == "toolu_x"
    assert m.stamp("s", _perm(), {"toolu_x": {"tool": "Bash", "ts": 1.0}}) == "toolu_x"


# ---- review of PR #308: a wrong id is worse than none --------------------------------------------

def _plan_menu():
    return {"kind": "options", "menu_family": "claude", "question": "Would you like to proceed?",
            "options": [{"n": 1, "label": "Yes, auto-accept"}, {"n": 2, "label": "No, keep planning"}]}


def test_an_auq_id_never_sticks_to_a_different_menu_while_its_call_lingers():
    # B1: AUQ X answered by Esc (no PostToolUse, X lingers), no poll while nothing was on screen,
    # then a plan-approval menu. It belongs to no open call: it must get none, not X.
    memo = MI.InstanceMemo()
    calls = {"toolu_X": {"tool": "AskUserQuestion", "questions": ["Which dashboards keep access?"], "ts": 1.0}}
    assert memo.stamp("s", _auq_menu(), calls) == "toolu_X"
    assert memo.stamp("s", _plan_menu(), calls) is None


def test_an_auq_id_does_not_survive_a_newer_call():
    memo = MI.InstanceMemo()
    calls = {"toolu_X": {"tool": "AskUserQuestion", "questions": ["Which dashboards keep access?"], "ts": 1.0}}
    assert memo.stamp("s", _auq_menu(), calls) == "toolu_X"
    calls["toolu_Y"] = {"tool": "Bash", "ts": 9e12}                   # the agent moved on
    assert memo.stamp("s", _auq_menu("Pick a colour?"), calls) is None


def test_the_review_screen_of_the_same_auq_call_keeps_its_id():
    memo = MI.InstanceMemo()
    calls = {"toolu_X": {"tool": "AskUserQuestion", "questions": ["Which dashboards keep access?"], "ts": 1.0}}
    assert memo.stamp("s", _auq_menu(), calls) == "toolu_X"
    review = {"kind": "options", "menu_family": "claude", "question": "Review your answers",
              "has_submit": True,
              "options": [{"n": 1, "label": "Submit answers"}, {"n": 2, "label": "Cancel"}]}
    assert memo.stamp("s", review, calls) == "toolu_X"


def test_an_evicted_call_makes_the_pane_unprovable_not_wrong(dirs):
    # S2: A's prompt is on screen, A is evicted by the cap, and a later Bash call B must not own it.
    _, calls = dirs
    H = _hook_mod()
    H.update_open_calls("%7", _pre("toolu_A"), "PreToolUse", now=time.time() - 60)
    for n in range(H.CALLS_CAP - 1):
        H.update_open_calls("%7", _pre(f"toolu_R{n}", "Read", file_path=f"/x/f{n}"), "PreToolUse", now=time.time() - 50 + n)
    H.update_open_calls("%7", _pre("toolu_B"), "PreToolUse")
    assert "toolu_A" not in _calls(calls)
    assert MI.read_open_calls("%7") == {}
    assert MI.resolve(_perm(), MI.read_open_calls("%7")) is None


def test_a_corrupt_map_marks_the_pane_unprovable(dirs):
    _, calls = dirs
    calls.mkdir(parents=True, exist_ok=True)
    (calls / "7.json").write_text("{not json")
    _fire(_pre("toolu_B"))
    assert list(_calls(calls)) == ["toolu_B"] and MI.read_open_calls("%7") == {}


def test_a_session_start_that_drops_open_calls_marks_the_pane_but_an_empty_one_does_not(dirs):
    _, calls = dirs
    _fire({"hook_event_name": "SessionStart"})
    _fire(_pre("toolu_A"))
    assert list(MI.read_open_calls("%7")) == ["toolu_A"]
    _fire({"hook_event_name": "SessionStart", "source": "compact"})
    _fire(_pre("toolu_B"))
    assert MI.read_open_calls("%7") == {}


@pytest.mark.parametrize("event", ["PostToolUseFailure", "PermissionDenied"])
def test_a_failed_or_denied_call_closes(dirs, event):
    _, calls = dirs
    _fire(_pre("toolu_A"))
    _fire({"hook_event_name": event, "tool_use_id": "toolu_A", "tool_name": "Bash"})
    assert _calls(calls) == {}


def test_the_installer_registers_the_closing_events():
    import importlib.util as iu
    spec = iu.spec_from_file_location("hooks_install", os.path.join(os.path.dirname(HERE), "hooks", "install.py"))
    m = iu.module_from_spec(spec)
    spec.loader.exec_module(m)
    events = {e for e, _, script, _ in m.HOOKS if script == "hooks/state-event-hook.py"}
    assert {"PostToolUseFailure", "PermissionDenied"} <= events


def test_a_held_lock_never_blocks_the_agent(dirs):
    # S3: another process holds the pane's lock; the hook must give up on calls/ and still exit fast.
    import fcntl
    ev, calls = dirs
    _fire(_pre("toolu_RUNNING"))               # a background call already open and running
    with open(calls / "7.lock", "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        t0 = time.time()
        _fire(_pre("toolu_A"))
        assert time.time() - t0 < 3
    assert json.loads((ev / "7.json").read_text())["event"] == "PreToolUse"
    # F1: the skipped PreToolUse is a lost call: the pane is unprovable, never resolved to another call
    assert MI.read_open_calls("%7") == {}


def _parsed(fixture):
    spec = importlib.util.spec_from_file_location("agent_status_fx", os.path.join(HERE, "agent-status.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    with open(os.path.join(HERE, "fixtures", "menus", fixture + ".pane.txt")) as f:
        return m.parse_pending_menu(f.read().splitlines())


def test_on_real_screens_the_review_keeps_the_id_and_a_plan_menu_does_not():
    calls = {"toolu_X": {"tool": "AskUserQuestion", "questions": ["Which surfaces should this E2E test cover?"], "ts": 1.0}}
    memo = MI.InstanceMemo()
    assert memo.stamp("s", _parsed("askuserquestion_multipart_live"), calls) == "toolu_X"
    assert memo.stamp("s", _parsed("askuserquestion_submit_confirm"), calls) == "toolu_X"
    memo = MI.InstanceMemo()
    assert memo.stamp("s", _parsed("askuserquestion_multipart_live"), calls) == "toolu_X"
    assert memo.stamp("s", _parsed("planmode_options"), calls) is None


def test_a_call_pruned_by_age_may_still_be_open_so_the_pane_is_unprovable(dirs):
    # D1: A's prompt waits > TTL (operator away); a background call's PreToolUse prunes A. A's prompt,
    # still on screen, must not resolve to the background call.
    _, calls = dirs
    H = _hook_mod()
    now = time.time()
    H.update_open_calls("%7", _pre("toolu_A"), "PreToolUse", now=now - H.CALLS_TTL_S - 60)
    H.update_open_calls("%7", _pre("toolu_B"), "PreToolUse", now=now)
    assert list(_calls(calls)) == ["toolu_B"]
    assert MI.read_open_calls("%7") == {}


@pytest.mark.parametrize("source", ["startup"])
def test_a_new_process_loses_nothing_so_it_marks_nothing(dirs, source):
    # D2: a new CLI process has no background subagents: its predecessor's calls are dead, not lost.
    _, calls = dirs
    _fire(_pre("toolu_OLD"))
    _fire({"hook_event_name": "SessionStart", "source": source})
    _fire(_pre("toolu_A"))
    assert list(MI.read_open_calls("%7")) == ["toolu_A"]


@pytest.mark.parametrize("source", ["compact", "clear", "resume"])
def test_a_compact_or_clear_with_open_calls_marks_the_pane(dirs, source):
    # resume too: it is not proven that a /resume inside a running process ends its subagents' calls
    _, calls = dirs
    _fire(_pre("toolu_SUB"))
    _fire({"hook_event_name": "SessionStart", "source": source})
    _fire(_pre("toolu_A"))
    assert MI.read_open_calls("%7") == {}


def test_a_hook_write_that_fails_marks_the_pane(dirs, monkeypatch):
    # F1: any exception that loses an update (a full disk at mkstemp) marks the pane.
    _, calls = dirs
    H = _hook_mod()
    H.update_open_calls("%7", _pre("toolu_A"), "PreToolUse")
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(H.tempfile, "mkstemp", boom)
    monkeypatch.setenv("TMUX_PANE", "%7")
    monkeypatch.setattr(H.sys, "stdin", __import__("io").StringIO(json.dumps(_pre("toolu_B"))))
    try:
        H.main()
    except OSError:
        pass                                    # the panes write may fail too; the CLI wrapper exits 0
    assert MI.read_open_calls("%7") == {}
