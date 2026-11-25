"""The outward-action ledger (scripts/outward_ledger.py).

An approved outward action runs exactly what the card showed, once. An interrupted one becomes
`uncertain` and is never re-run automatically; a changed material argument after approval is
refused; a duplicate pending card for the same action is refused. Spec: .workspace/proposals/
outward-action-ledger-spec.md (D1-D9, V1-V7, V6a).

Every test points APPROVAL_DB_PATH, OUTWARD_LEDGER_PATH and ORCHESTRA_DIR at a fresh tmp dir; the
ledger resolves both paths at call time. T2/T4 use a real subprocess SIGKILLed between claim and
finish.
"""
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import outward_ledger as L                      # noqa: E402
from approval_schema import ApprovalStore       # noqa: E402

PY = sys.executable
CLI = os.path.join(HERE, "outward_ledger.py")
APPROVAL_CLI = os.path.join(HERE, "approval.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setenv("APPROVAL_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("OUTWARD_LEDGER_PATH", str(tmp_path / "outward-actions.db"))
    monkeypatch.delenv("OUTWARD_CLAIM_TTL_S", raising=False)
    # No test reaches a real ntfy or Telegram: the token file is absent for every subprocess (a push
    # then fails BEFORE the network call) and the in-process network seams are stubbed.
    monkeypatch.setenv("NTFY_TOKEN_FILE", str(tmp_path / "no-token"))
    import approval_notify as N

    def no_network(*a, **k):
        raise AssertionError("a test reached the network")
    monkeypatch.setattr(N, "_http_post", no_network)
    monkeypatch.setattr(N, "_tg_send", no_network)
    monkeypatch.setattr(N, "ntfy_token", lambda: "")
    s = ApprovalStore(db_path=str(tmp_path / "tasks.db"))
    s.migrate()
    return s


def _card(store, **kw):
    kw.setdefault("from_agent", "deploy-bot")
    kw.setdefault("question", "Deploy build 42 to prod?")
    kw.setdefault("worker_kind", "pane")
    return store.create(**kw)


def _ev_action(store, rid):
    ev = json.loads(store.get(rid)["evidence"] or "{}")
    return ev.get("action")


def _rows(h=None):
    if not os.path.exists(L.db_path()):
        return []
    c = sqlite3.connect(L.db_path())
    c.row_factory = sqlite3.Row
    try:
        if h:
            return [dict(r) for r in c.execute(
                "SELECT * FROM outward_actions WHERE action_hash=? ORDER BY attempt", [h])]
        return [dict(r) for r in c.execute("SELECT * FROM outward_actions ORDER BY created_at")]
    finally:
        c.close()


def _uncertain_cards(store, h):
    c = sqlite3.connect(store.db_path)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(
            "SELECT * FROM approval_requests WHERE op_key=?", [f"oa-uncertain:{h}"])]
    finally:
        c.close()


def _spawn_claim(code, env_extra=None):
    """Run `code` (which must print CLAIMED then block) in a child; return the Popen once it printed."""
    env = dict(os.environ)
    env.update(env_extra or {})
    p = subprocess.Popen([PY, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, env=env, cwd=HERE)
    line = p.stdout.readline()
    if "CLAIMED" not in line:
        p.kill()
        raise AssertionError(f"child did not claim: {line!r} {p.stderr.read()}")
    return p


ARGS = {"target": "prod", "build": "42", "hosts": ["a", "b"]}


# --- T6 / V5: canonical hash -------------------------------------------------------------------

def test_hash_key_order_and_whitespace_do_not_matter():
    a = L.canonical_hash("deploy", {"target": "prod", "build": "42"})
    b = L.canonical_hash("deploy", json.loads('{ "build" : "42",\n "target":"prod" }'))
    assert a == b


def test_hash_list_order_is_material():
    assert L.canonical_hash("deploy", {"hosts": ["a", "b"]}) != L.canonical_hash("deploy", {"hosts": ["b", "a"]})


def test_hash_refuses_floats_and_nan():
    with pytest.raises(ValueError, match="must not carry floats"):
        L.canonical_hash("pay", {"amount": 1.5})
    with pytest.raises(ValueError, match="must not carry floats"):
        L.canonical_hash("pay", {"amount": float("nan")})
    with pytest.raises(ValueError, match="must not carry floats"):
        L.canonical_hash("pay", {"nested": [{"x": 2.0}]})


def test_hash_none_and_missing_are_distinct():
    assert L.canonical_hash("x", {"a": None}) != L.canonical_hash("x", {})


def test_hash_nfc_normalises_strings():
    composed, decomposed = "café", "café"
    assert composed != decomposed
    assert L.canonical_hash("x", {"n": composed}) == L.canonical_hash("x", {"n": decomposed})


def test_hash_refuses_non_str_keys_and_non_json_values():
    with pytest.raises(ValueError):
        L.canonical_hash("x", {1: "a"})
    with pytest.raises(ValueError):
        L.canonical_hash("x", {"a": object()})
    assert L.canonical_hash("x", {"b": True}) != L.canonical_hash("x", {"b": 1})   # bool is not int here


# --- T1: changed material arg after approval is refused ----------------------------------------

def test_T1_changed_material_arg_is_refused_and_row_stays_approved(env):
    store = env
    rid = _card(store)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    assert store.record_answer(rid, "approve", None) is True
    assert _rows(h)[0]["state"] == "approved"
    changed = dict(ARGS, target="staging")
    with pytest.raises(L.LedgerRefused) as e:
        L.claim("deploy", changed, actor="deploy-bot")
    assert "not approved" in e.value.reason
    assert _rows(h)[0]["state"] == "approved"
    # the exact args claim fine
    row = L.claim("deploy", ARGS, actor="deploy-bot")
    assert row["state"] == "executing" and row["claimed_pid"] == os.getpid()
    L.finish(h, "done", "sent")
    assert _rows(h)[0]["state"] == "done"
    assert _ev_action(store, rid)["state"] == "done"


def test_ask_refuses_a_duplicate_live_row(env):
    rid = _card(env)
    L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")
    rid2 = _card(env, op_key="other")
    with pytest.raises(L.DuplicateAction) as e:
        L.ask("deploy", ARGS, card_id=rid2, actor="deploy-bot")
    assert e.value.state == "asked"


def test_ask_mirrors_evidence_action_onto_the_card(env):
    rid = _card(env)
    row = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")
    ev = _ev_action(env, rid)
    assert ev["hash"] == row["action_hash"] and ev["action"] == "deploy" and ev["state"] == "asked"
    assert set(ev) >= {"hash", "action", "state", "reason", "updated_at"}


# --- T2: executor killed between claim and finish ----------------------------------------------

CHILD_CLAIM = """
import os, sys, time
sys.path.insert(0, {here!r})
import outward_ledger as L
L.claim("deploy", {args!r}, actor="deploy-bot")
print("CLAIMED", flush=True)
time.sleep(120)
"""


def test_T2_killed_executor_becomes_uncertain_and_never_reclaims(env):
    store = env
    rid = _card(store)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    store.record_answer(rid, "approve", None)
    p = _spawn_claim(CHILD_CLAIM.format(here=HERE, args=ARGS))
    try:
        row = _rows(h)[0]
        assert row["state"] == "executing" and row["claimed_pid"] == p.pid
        assert row["claimed_pid_start"] and row["claimed_host"]
        assert L.sweep()["uncertain"] == []            # T7: a live claim survives the sweep
        os.kill(p.pid, signal.SIGKILL)
        p.wait(timeout=10)
    finally:
        if p.poll() is None:
            p.kill()
    out = L.sweep()
    assert out["uncertain"] == [h]
    row = _rows(h)[0]
    assert row["state"] == "uncertain" and row["reason"] == "claim lost: pid dead"
    with pytest.raises(L.LedgerRefused) as e:
        L.claim("deploy", ARGS, actor="deploy-bot")
    assert e.value.state == "uncertain"
    assert _ev_action(store, rid)["state"] == "uncertain"
    # D7: ONE human_task card, done/cant/snooze, idempotent across sweeps
    cards = _uncertain_cards(store, h)
    assert len(cards) == 1
    c = cards[0]
    assert c["kind"] == "human_task" and c["from_agent"] == "deploy-bot"
    assert json.loads(c["options"]) == ["done", "cant", "snooze"]
    assert c["question"].startswith("Check: deploy may have run, will not retry")
    assert rid in (c["summary"] or "") and "claim lost: pid dead" in (c["summary"] or "")
    L.sweep(); L.sweep()
    assert len(_uncertain_cards(store, h)) == 1
    # nothing moves out of uncertain: finish is refused
    with pytest.raises(L.LedgerRefused):
        L.finish(h, "done", "sent")


# --- T7 / V1: claim identity -------------------------------------------------------------------

def test_T7_live_pid_survives_repeated_sweeps(env):
    rid = _card(env)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    env.record_answer(rid, "approve", None)
    L.claim("deploy", ARGS, actor="deploy-bot")       # pid = this test process
    for _ in range(3):
        assert L.sweep()["uncertain"] == []
    assert _rows(h)[0]["state"] == "executing"


def test_V1_reused_pid_number_with_other_starttime_is_dead(env):
    rid = _card(env)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    env.record_answer(rid, "approve", None)
    L.claim("deploy", ARGS, actor="deploy-bot", pid=os.getpid(), pid_start="1")
    assert L.sweep()["uncertain"] == [h]
    assert _rows(h)[0]["reason"] == "claim lost: pid reused"


def test_V1_other_host_uses_only_the_ttl(env, monkeypatch):
    rid = _card(env)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    env.record_answer(rid, "approve", None)
    L.claim("deploy", ARGS, actor="deploy-bot", pid=2 ** 22 - 1, host="elsewhere")
    assert L.sweep()["uncertain"] == []                 # pid unknown here: not our call
    monkeypatch.setenv("OUTWARD_CLAIM_TTL_S", "1")
    c = sqlite3.connect(L.db_path())
    c.execute("UPDATE outward_actions SET claimed_at=? WHERE action_hash=?", ["2000-01-01T00:00:00+00:00", h])
    c.commit(); c.close()
    assert L.sweep()["uncertain"] == [h]
    assert _rows(h)[0]["reason"] == "claim lost: ttl expired"


def test_V1_cli_claim_records_the_parent_pid_by_default(env):
    rid = _card(env)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    env.record_answer(rid, "approve", None)
    r = subprocess.run([PY, CLI, "claim", "--action", "deploy", "--material-args", json.dumps(ARGS),
                        "--actor", "deploy-bot"], capture_output=True, text=True, env=dict(os.environ))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["action_hash"] == h and out["claimed_pid"] == os.getpid()
    assert out["claimed_pid_start"] == L.pid_start(os.getpid())
    assert L.sweep()["uncertain"] == []                 # the test process is alive


def test_cli_refusal_is_exit_3_with_one_stderr_line_naming_the_state(env):
    rid = _card(env)
    L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")
    r = subprocess.run([PY, CLI, "claim", "--action", "deploy", "--material-args", json.dumps(ARGS),
                        "--actor", "deploy-bot"], capture_output=True, text=True, env=dict(os.environ))
    assert r.returncode == 3
    lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
    assert len(lines) == 1 and "asked" in lines[0]


# --- T5 / V3: the answer hook and the reconciling sweep ----------------------------------------

def test_T5_deny_is_not_run_and_answer_once_holds(env):
    rid = _card(env)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    assert env.record_answer(rid, "deny", None) is True
    assert _rows(h)[0]["state"] == "not_run" and _rows(h)[0]["reason"] == "answer: deny"
    assert env.record_answer(rid, "approve", None) is False          # answer-once (existing contract)
    assert _rows(h)[0]["state"] == "not_run"
    assert _ev_action(env, rid)["state"] == "not_run"
    # a not_run row does not block a fresh ask
    rid2 = _card(env, op_key="again")
    assert L.ask("deploy", ARGS, card_id=rid2, actor="deploy-bot")["state"] == "asked"


def test_V3_option_answer_matches_run_on_by_option_n(env):
    rid = _card(env, kind="menu", options=["Ship", "Hold"],
                menu={"question": "q", "options": [{"n": "1", "label": "Ship"}, {"n": "2", "label": "Hold"}]})
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot", run_on=["1"])["action_hash"]
    env.record_answer(rid, "option", None, option_n="1")
    assert _rows(h)[0]["state"] == "approved"


def test_V3_text_only_and_batch_answers_are_not_run(env):
    rid = _card(env)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    env.record_answer(rid, "text", "just a note")
    assert _rows(h)[0]["reason"] == "answer: text only"
    rid2 = _card(env, op_key="b")
    h2 = L.ask("notify", {"k": "v"}, card_id=rid2, actor="deploy-bot")["action_hash"]
    assert env.record_batch_answer(rid2, [{"part": 1, "ns": ["1"]}]) is True
    assert _rows(h2)[0]["state"] == "not_run" and _rows(h2)[0]["reason"] == "answer: batch"


def test_V3_hook_failure_never_reaches_the_answerer_and_sweep_heals(env, monkeypatch, capsys):
    rid = _card(env)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]

    def boom(*a, **k):
        raise RuntimeError("ledger down")
    real = L.on_card_answer
    monkeypatch.setattr(L, "on_card_answer", boom)
    assert env.record_answer(rid, "approve", None) is True
    assert "ledger down" in capsys.readouterr().err
    assert _rows(h)[0]["state"] == "asked"
    monkeypatch.setattr(L, "on_card_answer", real)
    out = L.sweep()
    assert h in out["approved"]
    assert _rows(h)[0]["state"] == "approved"


@pytest.mark.parametrize("status", ["expired", "retired", "discarded", "resolved_elsewhere"])
def test_V3_terminal_card_without_an_answer_is_not_run(env, status):
    rid = _card(env)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    c = sqlite3.connect(env.db_path)
    c.execute("UPDATE approval_requests SET status=? WHERE id=?", [status, rid]); c.commit(); c.close()
    assert h in L.sweep()["not_run"]
    assert _rows(h)[0]["reason"] == f"card {status}"


def test_V3_missing_card_is_not_run(env):
    with pytest.raises(L.LedgerRefused) as e:
        L.ask("deploy", ARGS, card_id="apr_gone", actor="deploy-bot")   # never attach to a card that is not there
    assert "not pending" in e.value.reason
    rid = _card(env)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    c = sqlite3.connect(env.db_path); c.execute("DELETE FROM approval_requests WHERE id=?", [rid]); c.commit(); c.close()
    L.sweep()
    assert _rows(h)[0]["state"] == "not_run" and _rows(h)[0]["reason"] == "card missing"


# --- V2: merge_evidence ------------------------------------------------------------------------

def test_V2_merge_evidence_round_trips_a_commitment_evidence(env):
    ev = {"due_ts": "2026-11-26T09:00:00-05:00", "due_source": "said in standup",
          "assist": {"kind": "call", "number": "+1 555 0100"}}
    rid = _card(env, kind="human_task", options=["done", "cant", "snooze"], evidence=ev)
    before = env.get(rid)["evidence"]
    assert env.merge_evidence(rid, "action", {"hash": "h", "state": "asked"}) is True
    after = json.loads(env.get(rid)["evidence"])
    assert after["action"] == {"hash": "h", "state": "asked"}
    rest = {k: v for k, v in after.items() if k != "action"}
    assert json.dumps(rest) == before                       # other keys byte-identical
    # any status, not just pending
    env.record_answer(rid, "done", None)
    assert env.merge_evidence(rid, "action", {"state": "done"}) is True
    again = json.loads(env.get(rid)["evidence"])
    assert again["action"] == {"state": "done"} and again["assist"] == ev["assist"]
    assert env.merge_evidence("apr_nope", "action", {}) is False


# --- T8 / V4: release ----------------------------------------------------------------------------

def _make_uncertain(store, action="deploy", args=ARGS):
    rid = _card(store, op_key=f"k-{action}-{time.time_ns()}")
    h = L.ask(action, args, card_id=rid, actor="deploy-bot")["action_hash"]
    store.record_answer(rid, "approve", None)
    L.claim(action, args, actor="deploy-bot", pid=os.getpid(), pid_start="1")
    L.sweep()
    assert _rows(h)[-1]["state"] == "uncertain"
    return rid, h


def test_T8_release_keeps_uncertain_and_allows_a_new_card(env):
    rid, h = _make_uncertain(env)
    rid2 = _card(env, op_key="second")
    with pytest.raises(L.DuplicateAction):
        L.ask("deploy", ARGS, card_id=rid2, actor="deploy-bot")
    with pytest.raises(L.LedgerRefused):
        L.release(h, by="gm", reason="")                    # a reason is required
    row = L.release(h, by="gm", reason="checked the host,\nit did not run " + "x" * 300)
    assert row["state"] == "uncertain" and row["released_by"] == "gm"
    assert "\n" not in row["release_reason"] and len(row["release_reason"]) <= 200
    assert _rows(h)[0]["state"] == "uncertain" and _rows(h)[0]["released_at"]
    new = L.ask("deploy", ARGS, card_id=rid2, actor="deploy-bot")
    assert new["state"] == "asked" and len(_rows(h)) == 2
    with pytest.raises(L.LedgerRefused):
        L.release(h, by="gm", reason="again")               # nothing unreleased-uncertain left
    assert _ev_action(env, rid)["released_by"] == "gm"


def test_V4_done_does_not_block_a_later_row(env):
    rid = _card(env)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    env.record_answer(rid, "approve", None)
    L.claim("deploy", ARGS, actor="deploy-bot")
    L.finish(h, "done", "sent")
    rid2 = _card(env, op_key="later")
    assert L.ask("deploy", ARGS, card_id=rid2, actor="deploy-bot")["state"] == "asked"


def test_release_refuses_a_non_uncertain_row(env):
    rid = _card(env)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    with pytest.raises(L.LedgerRefused) as e:
        L.release(h, by="gm", reason="nope")
    assert e.value.state == "asked"


# --- claim_direct / T4 / V6a -------------------------------------------------------------------

CHILD_DIRECT = """
import os, sys, time
sys.path.insert(0, {here!r})
import outward_ledger as L
L.claim_direct({action!r}, {args!r}, actor="approval_notify", policy={policy!r})
print("CLAIMED", flush=True)
time.sleep(120)
"""


def test_T4_direct_claim_killed_before_send_is_uncertain_and_once_never_reruns(env):
    args = {"card_id": "apr_x"}
    p = _spawn_claim(CHILD_DIRECT.format(here=HERE, action="deploy.direct", args=args, policy="once"))
    os.kill(p.pid, signal.SIGKILL); p.wait(timeout=10)
    h = L.canonical_hash("deploy.direct", args)
    assert L.sweep()["uncertain"] == [h]
    with pytest.raises(L.LedgerRefused) as e:
        L.claim_direct("deploy.direct", args, actor="approval_notify")
    assert e.value.state == "uncertain"
    assert len(_rows(h)) == 1
    assert len(_uncertain_cards(env, h)) == 1               # D7 card, blocks_what = actor
    assert _uncertain_cards(env, h)[0]["from_agent"] == "approval_notify"


def test_V6a_at_least_once_allows_exactly_one_resend(env, capsys):
    args = {"card_id": "apr_y"}
    p = _spawn_claim(CHILD_DIRECT.format(here=HERE, action="notify.ntfy", args=args, policy="at_least_once"))
    os.kill(p.pid, signal.SIGKILL); p.wait(timeout=10)
    h = L.canonical_hash("notify.ntfy", args)
    assert L.sweep()["uncertain"] == [h]
    assert _uncertain_cards(env, h) == []                    # attempt 1: the resend surfaces it
    row = L.claim_direct("notify.ntfy", args, actor="approval_notify", policy="at_least_once")
    assert row["attempt"] == 2 and row["state"] == "executing"
    assert "resend after uncertain" in capsys.readouterr().err
    rows = _rows(h)
    assert rows[0]["released_by"] == "policy:at_least_once" and rows[0]["state"] == "uncertain"
    assert rows[0]["release_reason"] == "resend after uncertain"
    L.finish(h, "uncertain", "RuntimeError: during send")   # attempt 2 also goes uncertain
    with pytest.raises(L.LedgerRefused) as e:
        L.claim_direct("notify.ntfy", args, actor="approval_notify", policy="at_least_once")
    assert e.value.state == "uncertain"
    assert len(_rows(h)) == 2
    assert len(_uncertain_cards(env, h)) == 1               # one D7 card for the hash


def test_claim_direct_duplicate_live_row_is_refused(env):
    L.claim_direct("deploy.direct", {"x": "1"}, actor="a")
    with pytest.raises(L.LedgerRefused) as e:
        L.claim_direct("deploy.direct", {"x": "1"}, actor="a")
    assert e.value.state == "executing"


# --- V6: the notifier wraps its sends ----------------------------------------------------------

@pytest.fixture
def notify_env(env, monkeypatch):
    import approval_notify as N
    import notify_policy
    monkeypatch.setattr(N, "_policy_gate", lambda kind, row, store: notify_policy.OK)
    monkeypatch.setattr(N, "ntfy_token", lambda: "tk")
    return N


def test_V6_ntfy_send_is_claimed_and_finished_done(notify_env, env, monkeypatch):
    N = notify_env
    sent = []
    monkeypatch.setattr(N, "_http_post", lambda url, headers, data: sent.append(data) or 200)
    rid = _card(env)
    N.notify(rid, store=env)
    assert len(sent) == 1 and env.get(rid)["notified_at"]
    h = L.canonical_hash("notify.ntfy", {"card_id": rid})
    rows = _rows(h)
    assert len(rows) == 1 and rows[0]["state"] == "done" and rows[0]["policy"] == "at_least_once"


def test_V6_exception_before_the_network_call_is_not_run(notify_env, env, monkeypatch):
    N = notify_env
    monkeypatch.setattr(N, "ntfy_token", lambda: "")
    called = []
    monkeypatch.setattr(N, "_http_post", lambda *a: called.append(1))
    rid = _card(env)
    with pytest.raises(RuntimeError):
        N.notify(rid, store=env)
    assert not called
    h = L.canonical_hash("notify.ntfy", {"card_id": rid})
    assert _rows(h)[0]["state"] == "not_run" and _rows(h)[0]["reason"] == "RuntimeError: before send"
    # a not_run never blocks the backstop's retry
    monkeypatch.setattr(N, "ntfy_token", lambda: "tk")
    N.notify(rid, store=env)
    assert [r["state"] for r in _rows(h)] == ["not_run", "done"]


def test_V6_exception_during_the_send_is_uncertain_then_one_resend_then_refused(notify_env, env, monkeypatch):
    N = notify_env
    def boom(*a):
        raise OSError("socket reset")
    monkeypatch.setattr(N, "_http_post", boom)
    rid = _card(env)
    with pytest.raises(OSError):
        N.notify(rid, store=env)
    h = L.canonical_hash("notify.ntfy", {"card_id": rid})
    assert _rows(h)[0]["state"] == "uncertain" and _rows(h)[0]["reason"] == "OSError: during send"
    assert _uncertain_cards(env, h) == []                    # attempt 1 is surfaced by the resend (V6)
    # attempt 2 (at_least_once): one resend
    with pytest.raises(OSError):
        N.notify(rid, store=env)
    assert [r["attempt"] for r in _rows(h)] == [1, 2]
    assert len(_uncertain_cards(env, h)) == 1                # attempt 2 uncertain -> the D7 card (V6a)
    # the D7 card's own push also failed here: it files no further card (no chain)
    d7 = _uncertain_cards(env, h)[0]
    h_d7 = L.canonical_hash("notify.ntfy", {"card_id": d7["id"]})
    assert _rows(h_d7) and _rows(h_d7)[-1]["state"] == "uncertain"
    assert _uncertain_cards(env, h_d7) == []
    assert len(env.pending_to_notify()) == 2                 # the card + ONE check card
    # ...and when the backstop's resend of the check card is uncertain too (attempt 2), still no chain
    monkeypatch.setattr(N, "_http_post", boom)
    with pytest.raises(OSError):
        N.notify(d7["id"], store=env)
    assert [r["attempt"] for r in _rows(h_d7)] == [1, 2] and _rows(h_d7)[-1]["state"] == "uncertain"
    assert _uncertain_cards(env, h_d7) == []
    assert len(env.pending_to_notify()) == 2
    # attempt 3: refused, nothing sent, no exception to the caller
    sent = []
    monkeypatch.setattr(N, "_http_post", lambda *a: sent.append(1) or 200)
    N.notify(rid, store=env)
    assert sent == [] and len(_rows(h)) == 2
    assert env.get(rid)["notified_at"] is None
    assert len(_uncertain_cards(env, h)) == 1


def test_V6_killed_notifier_after_claim_before_send(notify_env, env, monkeypatch):
    """T4 for the notifier: the real process dies between claim_direct and the push."""
    N = notify_env
    rid = _card(env)
    args = {"card_id": rid}
    p = _spawn_claim(CHILD_DIRECT.format(here=HERE, action="notify.ntfy", args=args, policy="at_least_once"))
    os.kill(p.pid, signal.SIGKILL); p.wait(timeout=10)
    h = L.canonical_hash("notify.ntfy", args)
    assert L.sweep()["uncertain"] == [h]
    sent = []
    monkeypatch.setattr(N, "_http_post", lambda *a: sent.append(1) or 200)
    N.notify(rid, store=env)                                 # attempt 2: the one allowed resend
    assert sent == [1]
    assert [r["state"] for r in _rows(h)] == ["uncertain", "done"]


def test_V6_telegram_send_outcomes(notify_env, env, tmp_path):
    N = notify_env
    sp = str(tmp_path / "tg.json")
    assert N.notify_telegram("approval:apr_1", "hi", state_path=sp, send=lambda t: True) is True
    assert _rows(L.canonical_hash("notify.telegram", {"card_id": "approval:apr_1"}))[0]["state"] == "done"
    assert N.notify_telegram("approval:apr_2", "hi", state_path=sp, send=lambda t: False) is False
    assert _rows(L.canonical_hash("notify.telegram", {"card_id": "approval:apr_2"}))[0]["reason"] == "send failed"

    def boom(t):
        raise RuntimeError("net down")
    assert N.notify_telegram("approval:apr_3", "hi", state_path=sp, send=boom) is False
    h3 = L.canonical_hash("notify.telegram", {"card_id": "approval:apr_3"})
    assert _rows(h3)[0]["state"] == "uncertain" and _rows(h3)[0]["reason"] == "RuntimeError: during send"
    assert "approval:apr_3" not in N._load_tg_state(sp)      # not stamped: the existing retry contract
    # already-sent keys never touch the ledger
    assert N.notify_telegram("approval:apr_1", "hi", state_path=sp, send=boom) is True
    assert len(_rows(L.canonical_hash("notify.telegram", {"card_id": "approval:apr_1"}))) == 1


def test_V6_escalate_is_untouched(env, monkeypatch):
    import inspect
    import approval_resume as R
    src = inspect.getsource(R.escalate)
    assert "outward_ledger" not in src and "claim_direct" not in src
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: None)
    rid = _card(env)
    row = dict(env.get(rid), resume_attempts=3)
    R.escalate(row, env)
    assert env.get(rid)["escalated_at"]
    assert _rows() == []


# --- T3 / V7c: approval.py request --action ----------------------------------------------------

def _request(extra, **kw):
    cmd = [PY, APPROVAL_CLI, "request", "Deploy build 42?", "--from", "deploy-bot", "--worker-kind", "node",
           "--action", "deploy", "--material-args", json.dumps(ARGS)] + extra
    return subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ), **kw)


def test_T3_duplicate_request_prints_the_existing_card_and_exits_0(env):
    r1 = _request([])
    assert r1.returncode == 0, r1.stderr
    rid = r1.stdout.strip()
    assert rid.startswith("apr_")
    row = env.get(rid)
    assert json.loads(row["evidence"])["action"]["state"] == "asked"
    h = L.canonical_hash("deploy", ARGS)
    assert _rows(h)[0]["card_id"] == rid and json.loads(_rows(h)[0]["run_on"]) == ["approve"]
    r2 = _request(["--op-key", "different"])
    assert r2.returncode == 0 and r2.stdout.strip() == rid
    assert "already" in r2.stderr and rid in r2.stderr
    assert len(_rows(h)) == 1 and len(env.pending_to_notify()) == 1       # one card
    # approved: still idempotent
    env.record_answer(rid, "approve", None)
    r3 = _request(["--op-key", "third"])
    assert r3.returncode == 0 and r3.stdout.strip() == rid
    # executing: exit 3, one stderr line
    L.claim("deploy", ARGS, actor="deploy-bot")
    r4 = _request(["--op-key", "fourth"])
    assert r4.returncode == 3 and r4.stdout.strip() == ""
    assert "executing" in r4.stderr and "release it or wait" in r4.stderr
    assert len(env.pending_to_notify()) == 0


def test_V7c_unreleased_uncertain_refuses_and_release_lets_a_new_card_through(env):
    _, h = _make_uncertain(env)
    r = _request(["--op-key", "x"])
    assert r.returncode == 3 and "uncertain" in r.stderr and "release it or wait" in r.stderr
    L.release(h, by="gm", reason="checked")
    r2 = _request(["--op-key", "y"])
    assert r2.returncode == 0 and r2.stdout.strip().startswith("apr_")
    assert _rows(h)[-1]["state"] == "asked"


def test_request_refuses_floats_in_material_args(env):
    r = subprocess.run([PY, APPROVAL_CLI, "request", "Pay?", "--from", "deploy-bot", "--worker-kind", "node",
                        "--action", "pay", "--material-args", '{"amount": 1.5}'],
                       capture_output=True, text=True, env=dict(os.environ))
    assert r.returncode == 2 and "float" in r.stderr
    assert env.pending_to_notify() == []


def test_request_run_on_and_pending_json_carry_evidence(env):
    r = _request(["--run-on", "approve,1"])
    rid = r.stdout.strip()
    h = L.canonical_hash("deploy", ARGS)
    assert json.loads(_rows(h)[0]["run_on"]) == ["approve", "1"]
    out = subprocess.run([PY, APPROVAL_CLI, "pending", "--json"], capture_output=True, text=True,
                         env=dict(os.environ))
    rows = json.loads(out.stdout)
    assert rows[0]["id"] == rid and json.loads(rows[0]["evidence"])["action"]["hash"] == h
    got = subprocess.run([PY, APPROVAL_CLI, "get", rid], capture_output=True, text=True, env=dict(os.environ))
    assert json.loads(json.loads(got.stdout)["evidence"])["action"]["state"] == "asked"


def test_approval_cli_runs_the_sweep_first(env):
    rid = _card(env)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    env.record_answer(rid, "approve", None)
    L.claim("deploy", ARGS, actor="deploy-bot", pid=os.getpid(), pid_start="1")   # a dead claim
    subprocess.run([PY, APPROVAL_CLI, "get", rid], capture_output=True, text=True, env=dict(os.environ))
    assert _rows(h)[0]["state"] == "uncertain"


# --- paths resolve at call time -------------------------------------------------------------------

def test_paths_resolve_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTWARD_LEDGER_PATH", str(tmp_path / "a.db"))
    assert L.db_path() == str(tmp_path / "a.db")
    monkeypatch.setenv("OUTWARD_LEDGER_PATH", str(tmp_path / "b.db"))
    assert L.db_path() == str(tmp_path / "b.db")
    monkeypatch.delenv("OUTWARD_LEDGER_PATH")
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "od"))
    assert L.db_path() == str(tmp_path / "od" / "state" / "outward-actions.db")


def test_cli_show_sweep_release_finish(env):
    rid = _card(env)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    env.record_answer(rid, "approve", None)
    e = dict(os.environ)
    r = subprocess.run([PY, CLI, "show", "--hash", h], capture_output=True, text=True, env=e)
    assert r.returncode == 0 and json.loads(r.stdout)[0]["state"] == "approved"
    r = subprocess.run([PY, CLI, "claim", "--action", "deploy", "--material-args", json.dumps(ARGS),
                        "--actor", "deploy-bot"], capture_output=True, text=True, env=e)
    assert r.returncode == 0
    r = subprocess.run([PY, CLI, "finish", "--hash", h, "--state", "not_run", "--reason", "nothing to do"],
                       capture_output=True, text=True, env=e)
    assert r.returncode == 0 and _rows(h)[0]["state"] == "not_run"
    r = subprocess.run([PY, CLI, "release", "--hash", h, "--by", "gm", "--reason", "x"],
                       capture_output=True, text=True, env=e)
    assert r.returncode == 3 and "not_run" in r.stderr
    r = subprocess.run([PY, CLI, "sweep"], capture_output=True, text=True, env=e)
    assert r.returncode == 0 and set(json.loads(r.stdout)) >= {"uncertain", "approved", "not_run"}


# --- follow-ups: the request race, a card with no ledger row, C1/C2 ----------------------------

def _run_request_inproc(argv_extra, capsys):
    """approval.main() in this process (deterministic; the ledger seams can be patched)."""
    import approval
    code = approval.main(["request", "Deploy build 42?", "--from", "deploy-bot", "--worker-kind", "node",
                          "--action", "deploy", "--material-args", json.dumps(ARGS)] + argv_extra)
    out = capsys.readouterr()
    return code, out.out.strip(), out.err


def test_race_loser_card_is_voided_and_points_at_the_winner(env, monkeypatch, capsys):
    """Two requests for the same action pass the pre-check together: the loser's card must not stay
    pending with no ledger row (it could run outside the ledger). Deterministic: the pre-check is
    made blind so create() runs, then ask() is refused by the winner's row."""
    winner = _card(env, op_key="winner")
    h = L.ask("deploy", ARGS, card_id=winner, actor="deploy-bot")["action_hash"]
    monkeypatch.setattr(L, "live_row", lambda **k: None)
    code, out, err = _run_request_inproc(["--op-key", "loser"], capsys)
    assert code == 0 and out == winner
    assert f"superseded by {winner}" in err
    pending = env.pending_to_notify()
    assert [r["id"] for r in pending] == [winner]
    loser = [r for r in env.history(limit=10) if r["id"] != winner]
    assert len(loser) == 1 and loser[0]["status"] in ("discarded", "retired")
    assert env.record_answer(loser[0]["id"], "approve", None) is False      # can never be answered
    rows = _rows(h)
    assert len(rows) == 1 and rows[0]["card_id"] == winner


def test_race_loser_is_retired_as_superseded_when_the_retire_columns_are_armed(env, monkeypatch, capsys):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "m20261010_card_retire")
    env.migrate()
    winner = _card(env, op_key="winner")
    L.ask("deploy", ARGS, card_id=winner, actor="deploy-bot")
    monkeypatch.setattr(L, "live_row", lambda **k: None)
    code, out, err = _run_request_inproc(["--op-key", "loser"], capsys)
    assert code == 0 and out == winner
    loser = [r for r in env.history(limit=10) if r["id"] != winner][0]
    assert loser["status"] == "retired" and loser["superseded_by"] == winner
    assert "superseded by" in (loser["retire_reason"] or "")


def test_race_loser_is_voided_even_when_the_winner_is_executing(env, monkeypatch, capsys):
    winner = _card(env, op_key="winner")
    L.ask("deploy", ARGS, card_id=winner, actor="deploy-bot")
    env.record_answer(winner, "approve", None)
    L.claim("deploy", ARGS, actor="deploy-bot")
    monkeypatch.setattr(L, "live_row", lambda **k: None)
    code, out, err = _run_request_inproc(["--op-key", "loser"], capsys)
    assert code == 3 and out == "" and "release it or wait" in err and "superseded by" in err
    assert env.pending_to_notify() == []


CHILD_REQUEST = """
import os, sys, time
sys.path.insert(0, {here!r})
import approval
while not os.path.exists({flag!r}):
    time.sleep(0.005)
sys.exit(approval.main(["request", "Deploy build 42?", "--from", "deploy-bot", "--worker-kind", "node",
                        "--action", "deploy", "--material-args", {args!r}, "--op-key", {opk!r}]))
"""


def test_race_two_concurrent_requests_leave_one_pending_card_and_one_row(env, tmp_path):
    flag = str(tmp_path / "go")
    procs = [subprocess.Popen([PY, "-c", CHILD_REQUEST.format(here=HERE, flag=flag, args=json.dumps(ARGS),
                                                              opk=f"k{i}")],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ))
             for i in range(2)]
    time.sleep(1.5)                                        # both past their imports, parked on the flag
    open(flag, "w").close()
    outs = [p.communicate(timeout=60) for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    ids = {o.strip() for o, _ in outs}
    assert len(ids) == 1                                   # both printed the WINNER's id
    (winner,) = ids
    assert [r["id"] for r in env.pending_to_notify()] == [winner]
    h = L.canonical_hash("deploy", ARGS)
    rows = _rows(h)
    assert len(rows) == 1 and rows[0]["card_id"] == winner and rows[0]["state"] == "asked"
    for r in env.history(limit=10):
        if r["id"] != winner:
            assert r["status"] in ("discarded", "retired")
            assert env.record_answer(r["id"], "approve", None) is False


def test_approving_a_card_with_no_ledger_row_never_lets_claim_succeed(env):
    rid = _card(env)                                       # a plain card: no --action, no ledger row
    assert env.record_answer(rid, "approve", None) is True
    with pytest.raises(L.LedgerRefused) as e:
        L.claim("deploy", ARGS, actor="deploy-bot")
    assert e.value.state == "none" and "not approved" in e.value.reason
    assert _rows() == []


def _health_file():
    from datetime import datetime
    return os.path.join(os.environ["ORCHESTRA_DIR"], "state", "usage", "health",
                        datetime.now().strftime("%Y-%m-%d") + ".json")


def test_C1_C2_ntfy_resend_carries_the_same_card_id_and_buttons_and_counts_once(notify_env, env, monkeypatch):
    N = notify_env
    sent = []

    def flaky(url, headers, data):
        sent.append(json.loads(data))
        if len(sent) == 1:
            raise OSError("reset after write")           # during send: uncertain
        return 200
    monkeypatch.setattr(N, "_http_post", flaky)
    rid = _card(env)
    with pytest.raises(OSError):
        N.notify(rid, store=env)
    assert not os.path.exists(_health_file())             # a first send never touches the counter
    N.notify(rid, store=env)                               # the one resend
    assert len(sent) == 2
    assert sent[0]["title"] == sent[1]["title"] and rid in sent[0]["title"]
    assert sent[0]["actions"] == sent[1]["actions"]
    assert all(json.loads(a["body"])["id"] == rid for a in sent[1]["actions"])
    assert sent[0] == sent[1]
    health = json.load(open(_health_file()))
    assert health == {"double_pushes": 1, "by_channel": {"ntfy": 1}}


def test_C1_C2_telegram_resend_same_text_and_counter(notify_env, env, tmp_path):
    N = notify_env
    sp = str(tmp_path / "tg.json")
    texts = []

    def flaky(t):
        texts.append(t)
        if len(texts) == 1:
            raise RuntimeError("net down")
        return True
    assert N.notify_telegram("approval:apr_9", "APPROVAL apr_9: hi", state_path=sp, send=flaky) is False
    assert not os.path.exists(_health_file())
    assert N.notify_telegram("approval:apr_9", "APPROVAL apr_9: hi", state_path=sp, send=flaky) is True
    assert texts == ["APPROVAL apr_9: hi", "APPROVAL apr_9: hi"]
    assert json.load(open(_health_file())) == {"double_pushes": 1, "by_channel": {"telegram": 1}}
    # a third call is deduped by the sidecar: no send, no count
    assert N.notify_telegram("approval:apr_9", "x", state_path=sp, send=flaky) is True
    assert len(texts) == 2 and json.load(open(_health_file()))["double_pushes"] == 1


# --- BLOCK fix: a card approves exactly one outward action ---------------------------------------

def _request_opkey(action, args, op_key):
    cmd = [PY, APPROVAL_CLI, "request", "Do it?", "--from", "deploy-bot", "--worker-kind", "node",
           "--action", action, "--material-args", json.dumps(args), "--op-key", op_key]
    return subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ))


def test_BLOCK_a_same_op_key_different_action_files_no_second_row(env):
    """The reviewer's reproduction: create() dedupes on op_key and hands back the EXISTING card; the
    second action must not ride on it."""
    r1 = _request_opkey("deploy", {"target": "a", "hosts": [{"name": "h1"}]}, "same")
    assert r1.returncode == 0, r1.stderr
    rid = r1.stdout.strip()
    h_deploy = L.canonical_hash("deploy", {"target": "a", "hosts": [{"name": "h1"}]})
    r2 = _request_opkey("pay", {"amount": "1", "to": {"iban": "x"}}, "same")
    assert r2.returncode == 3 and r2.stdout.strip() == ""
    lines = [ln for ln in r2.stderr.splitlines() if ln.startswith("REFUSED")]
    assert len(lines) == 1
    assert f"card {rid} already carries action {h_deploy[:8]}" in lines[0] and "different --op-key" in lines[0]
    h_pay = L.canonical_hash("pay", {"amount": "1", "to": {"iban": "x"}})
    assert len(env.pending_to_notify()) == 1                 # one card, still pending (never voided)
    assert len(L.show(card_id=rid)) == 1 and _rows(h_pay) == []
    assert env.record_answer(rid, "approve", None) is True
    assert [r["state"] for r in L.show(card_id=rid)] == ["approved"]
    assert _rows(h_deploy)[0]["state"] == "approved" and _rows(h_pay) == []
    assert _ev_action(env, rid)["hash"] == h_deploy


def test_BLOCK_a_cli_compare_refuses_before_the_ledger_is_asked(env, monkeypatch, capsys):
    rid = _card(env, op_key="same")
    L.ask("pay", {"amount": "1"}, card_id=rid, actor="deploy-bot")

    def never(*a, **k):
        raise AssertionError("ask() must not be reached: the CLI compares first")
    monkeypatch.setattr(L, "ask", never)
    code, out, err = _run_request_inproc(["--op-key", "same"], capsys)      # action deploy on the pay card
    assert code == 3 and out == "" and "already carries action" in err
    assert [r["id"] for r in env.pending_to_notify()] == [rid]


def test_BLOCK_b_same_op_key_same_action_is_idempotent(env):
    r1 = _request_opkey("deploy", {"target": "a"}, "same")
    r2 = _request_opkey("deploy", {"target": "a"}, "same")
    assert r1.returncode == 0 and r2.returncode == 0
    assert r1.stdout.strip() == r2.stdout.strip()
    assert len(L.show(card_id=r1.stdout.strip())) == 1 and len(env.pending_to_notify()) == 1


def test_BLOCK_c_ask_refuses_a_second_hash_on_a_card_with_a_live_row(env):
    rid = _card(env)
    first = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")
    again = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")       # same hash: idempotent
    assert again["id"] == first["id"] and len(L.show(card_id=rid)) == 1
    with pytest.raises(L.CardBusy) as e:
        L.ask("pay", {"amount": "1"}, card_id=rid, actor="deploy-bot")
    assert e.value.state == "asked" and e.value.existing_hash == first["action_hash"]
    assert len(L.show(card_id=rid)) == 1
    # the index itself holds, below ask(): a raw second live row for the card is impossible
    c = sqlite3.connect(L.db_path())
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO outward_actions (id, action_hash, action, material_args, card_id, state, created_at, "
                  "updated_at) VALUES ('oa_raw', 'ff', 'pay', '{}', ?, 'asked', 'x', 'x')", [rid])
    c.close()
    # a spent row (done / not_run) is history, but the card is answered by then: a new action needs a
    # NEW pending card, never the old one (its one human answer is spent)
    env.record_answer(rid, "approve", None)
    L.claim("deploy", ARGS, actor="deploy-bot")
    L.finish(first["action_hash"], "done", "sent")
    with pytest.raises(L.LedgerRefused) as e:
        L.ask("pay", {"amount": "1"}, card_id=rid, actor="deploy-bot")
    assert "not pending" in e.value.reason and len(L.show(card_id=rid)) == 1
    rid2 = _card(env, op_key="new")
    new = L.ask("pay", {"amount": "1"}, card_id=rid2, actor="deploy-bot")
    assert new["state"] == "asked" and len(L.show(card_id=rid2)) == 1


def test_BLOCK_d_answer_approves_only_the_row_the_card_shows(env):
    rid = _card(env)
    row = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")
    other = L.canonical_hash("pay", {"amount": "1"})
    ev = json.loads(env.get(rid)["evidence"])["action"]
    env.merge_evidence(rid, "action", dict(ev, hash=other))               # the card shows another action
    assert env.record_answer(rid, "approve", None) is True
    r = _rows(row["action_hash"])[0]
    assert r["state"] != "approved" and r["state"] == "not_run" and r["reason"] == "card shows another action"
    L.sweep()                                                              # the reconciler agrees
    assert _rows(row["action_hash"])[0]["state"] == "not_run"


def test_BLOCK_same_op_key_same_action_different_nested_args_is_refused(env):
    a1 = {"target": "prod", "hosts": [{"name": "h1", "port": "22"}]}
    a2 = {"target": "prod", "hosts": [{"name": "h1", "port": "2222"}]}
    r1 = _request_opkey("deploy", a1, "same")
    rid = r1.stdout.strip()
    r2 = _request_opkey("deploy", a2, "same")
    assert r2.returncode != 0 and r2.stdout.strip() == "" and "different --op-key" in r2.stderr
    assert len(L.show(card_id=rid)) == 1 and _rows(L.canonical_hash("deploy", a2)) == []
    env.record_answer(rid, "approve", None)
    assert [r["state"] for r in L.show(card_id=rid)] == ["approved"]
    assert _rows(L.canonical_hash("deploy", a1))[0]["state"] == "approved"
    assert len(env.pending_to_notify()) == 0 and env.get(rid)["status"] == "answered"   # never voided


def test_BLOCK_new_card_is_voided_when_the_ledger_refuses_for_any_reason(env, monkeypatch, capsys):
    def refuse(*a, **k):
        raise L.LedgerRefused("asked", "the ledger said no")
    monkeypatch.setattr(L, "ask", refuse)
    code, out, err = _run_request_inproc(["--op-key", "fresh"], capsys)
    assert code == 3 and out == "" and "the ledger said no" in err and "voided" in err
    assert env.pending_to_notify() == []
    (card,) = env.history(limit=5)
    assert card["status"] in ("discarded", "retired") and env.record_answer(card["id"], "approve", None) is False


def test_BLOCK_existing_card_is_never_voided_when_the_ledger_refuses(env, monkeypatch, capsys):
    rid = _card(env, op_key="held")
    L.ask("pay", {"amount": "1"}, card_id=rid, actor="deploy-bot")
    def refuse(*a, **k):
        raise L.LedgerRefused("asked", "the ledger said no")
    monkeypatch.setattr(L, "card_live_row", lambda cid: None)     # make the pre-compare blind
    monkeypatch.setattr(L, "ask", refuse)
    code, out, err = _run_request_inproc(["--op-key", "held"], capsys)
    assert code == 3 and out == "" and "voided" not in err
    assert [r["id"] for r in env.pending_to_notify()] == [rid]      # still pending for its own action
    assert env.record_answer(rid, "approve", None) is True


# --- BLOCK fix, second hole: the re-card guard hands back an ANSWERED card --------------------------

def test_BLOCK2_replay_answered_card_within_the_recard_window(env):
    """approve card A (op_key k); within RECARD_GUARD_S request --action with the same k: create()
    returns the answered card. No row may attach to it; one old human answer never authorises a
    second run."""
    r1 = _request_opkey("deploy", ARGS, "k")
    rid = r1.stdout.strip()
    h = L.canonical_hash("deploy", ARGS)
    assert env.record_answer(rid, "approve", None) is True
    assert _rows(h)[0]["state"] == "approved"
    # the SAME arguments: the hash is already approved, so the idempotent re-requester rule (V7c)
    # answers first: that card's id, exit 0, and no new row
    r = _request_opkey("deploy", ARGS, "k")
    assert r.returncode == 0 and r.stdout.strip() == rid
    # DIFFERENT arguments reach create(), whose re-card guard hands back the answered card
    r = _request_opkey("deploy", dict(ARGS, build="43"), "k")
    assert r.returncode == 3 and r.stdout.strip() == "", r.stderr
    line = [ln for ln in r.stderr.splitlines() if ln.startswith("REFUSED")]
    assert len(line) == 1 and f"op-key k re-card guard returned answered card {rid}" in line[0]
    assert "wait" in line[0] and "new --op-key" in line[0]
    assert len(L.show(card_id=rid)) == 1 and _rows(L.canonical_hash("deploy", dict(ARGS, build="43"))) == []
    before = L.show()
    assert L.sweep() == {"uncertain": [], "approved": [], "not_run": []}
    assert L.show() == before
    assert [r["state"] for r in _rows(h)] == ["approved"]
    assert env.get(rid)["status"] == "answered"                               # nothing voided


def test_BLOCK2_ask_refuses_a_card_that_is_not_pending(env):
    rid = _card(env)
    env.record_answer(rid, "approve", None)
    with pytest.raises(L.LedgerRefused) as e:
        L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")
    assert e.value.reason == f"card {rid} is answered, not pending"
    assert _rows() == []
    rid2 = _card(env, op_key="d")
    env.mark_discarded(rid2)
    with pytest.raises(L.LedgerRefused, match="is discarded, not pending"):
        L.ask("deploy", ARGS, card_id=rid2, actor="deploy-bot")


def test_BLOCK2_an_answer_given_before_the_ask_never_authorises_it(env):
    rid = _card(env)
    h = L.ask("deploy", ARGS, card_id=rid, actor="deploy-bot")["action_hash"]
    c = sqlite3.connect(env.db_path)
    c.execute("UPDATE approval_requests SET status='answered', answer='approve', answered_at=? WHERE id=?",
              ["2000-01-01T00:00:00+00:00", rid])                              # an answer older than the ask
    c.commit(); c.close()
    assert h in L.sweep()["not_run"]
    assert _rows(h)[0]["reason"] == "card answered before the ask"
    # the fast path applies the same rule
    rid2 = _card(env, op_key="2")
    h2 = L.ask("deploy", dict(ARGS, build="44"), card_id=rid2, actor="deploy-bot")["action_hash"]
    c = sqlite3.connect(env.db_path)
    c.execute("UPDATE approval_requests SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", [rid2]); c.commit(); c.close()
    env.record_answer(rid2, "approve", None)                                   # answered_at = now, after the ask
    assert _rows(h2)[0]["state"] == "approved"
    L.on_card_answer(rid2, "approve")                                          # idempotent, nothing to redo
    assert _rows(h2)[0]["state"] == "approved"


# --- the pre-read race: a concurrent requester must never kill another requester's card ---------

def test_RACE2_cardbusy_never_voids_the_shared_card(env, monkeypatch, capsys):
    """Both requesters pass the pre-read before either creates; `pay` wins the row on the shared card;
    `deploy` gets CardBusy. The shared card is the winner's live card: it must survive untouched."""
    winner_rid = _card(env, op_key="same")
    pay = L.ask("pay", {"amount": "1"}, card_id=winner_rid, actor="deploy-bot")
    monkeypatch.setattr(env.__class__, "find_by_op_key", lambda self, op_key, origin=None: [])   # blind pre-read
    monkeypatch.setattr(L, "card_live_row", lambda cid: None)                                   # blind compare
    code, out, err = _run_request_inproc(["--op-key", "same"], capsys)
    assert code == 3 and out == "" and "already carries action" in err and "voided" not in err
    assert [r["id"] for r in env.pending_to_notify()] == [winner_rid]        # still pending
    assert env.get(winner_rid)["status"] == "pending"
    assert [r["id"] for r in L.show(card_id=winner_rid)] == [pay["id"]]      # still carrying its row
    assert L.sweep() == {"uncertain": [], "approved": [], "not_run": []}
    assert env.record_answer(winner_rid, "approve", None) is True
    assert _rows(pay["action_hash"])[0]["state"] == "approved"


def test_RACE2_created_new_comes_from_create_itself(env):
    report = {}
    a = env.create(from_agent="x", question="q", worker_kind="pane", op_key="k", report=report)
    assert report == {"inserted": True}
    report2 = {}
    b = env.create(from_agent="x", question="q", worker_kind="pane", op_key="k", report=report2)
    assert a == b and report2 == {"inserted": False}
    env.record_answer(a, "approve", None)
    report3 = {}
    c = env.create(from_agent="x", question="q", worker_kind="pane", op_key="k", report=report3)
    assert c == a and report3 == {"inserted": False}                         # the re-card guard's return
    assert env.create(from_agent="x", question="q", worker_kind="pane", op_key="k2") != a   # signature unchanged


CHILD_REQUEST2 = """
import os, sys, time
sys.path.insert(0, {here!r})
import approval
while not os.path.exists({flag!r}):
    time.sleep(0.005)
sys.exit(approval.main(["request", "Do it?", "--from", "deploy-bot", "--worker-kind", "node",
                        "--action", {action!r}, "--material-args", {args!r}, "--op-key", "same"]))
"""


def test_RACE2_two_concurrent_requests_different_actions_same_op_key(env, tmp_path):
    flag = str(tmp_path / "go")
    specs = [("deploy", json.dumps({"target": "a"})), ("pay", json.dumps({"amount": "1"}))]
    procs = [subprocess.Popen([PY, "-c", CHILD_REQUEST2.format(here=HERE, flag=flag, action=a, args=g)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ))
             for a, g in specs]
    time.sleep(1.5)
    open(flag, "w").close()
    outs = [p.communicate(timeout=60) for p in procs]
    codes = sorted(p.returncode for p in procs)
    assert codes == [0, 3], outs
    pending = env.pending_to_notify()
    assert len(pending) == 1                                                   # the winner's card, alive
    (winner,) = pending
    rows = L.show(card_id=winner["id"])
    assert len(rows) == 1 and rows[0]["state"] == "asked"
    assert len([r for r in _rows() if r["card_id"]]) == 1                     # one card row in total
    loser_err = [e for (o, e), p in zip(outs, procs) if p.returncode == 3][0]
    assert "voided" not in loser_err and "superseded" not in loser_err
    assert env.history(limit=10) == []                                       # nothing retired or discarded
