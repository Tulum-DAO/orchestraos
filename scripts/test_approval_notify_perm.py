"""D2 permission notify backstop (DEC-1786758700 + the operator 2-push amendment 2026-08-15).

MAX 2 pushes per op_key: #1 when a permission prompt persists >60s, #2 at the
next 08:00 ET if the SAME op_key is still stuck overnight, then SILENCE forever.
Durable per-op_key state survives cron restarts. Dedup RESETS when the prompt
clears (op_key-collision fix: a fresh same-shaped prompt is push-eligible again).
All time/scan/send injected — no real tmux, no real network.
"""
import os, sys, json, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approval_notify as N

ET = ZoneInfo("America/New_York")

def _epoch(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=ET).timestamp()

@pytest.fixture
def statef(tmp_path):
    return str(tmp_path / "perm-notify.json")

def _run(now, present, statef, sent):
    return N.permission_backstop(
        now=now, state_path=statef,
        scan=lambda: present,
        send=lambda agent, q, kind: sent.append((agent, kind)))

PROMPT = {"menu:red-team:abc": {"agent": "red-team", "question": "Do you want to proceed?"}}


def test_no_push_before_60s(statef):
    sent = []
    t0 = _epoch(2026, 8, 15, 14, 0, 0)          # afternoon ET
    _run(t0, PROMPT, statef, sent)               # first sighting
    _run(t0 + 59, PROMPT, statef, sent)          # 59s later
    assert sent == []

def test_push1_after_60s(statef):
    sent = []
    t0 = _epoch(2026, 8, 15, 14, 0, 0)
    _run(t0, PROMPT, statef, sent)
    _run(t0 + 61, PROMPT, statef, sent)
    assert sent == [("red-team", "stuck")]

def test_push1_only_once_while_present(statef):
    sent = []
    t0 = _epoch(2026, 8, 15, 14, 0, 0)
    _run(t0, PROMPT, statef, sent)
    _run(t0 + 61, PROMPT, statef, sent)
    _run(t0 + 120, PROMPT, statef, sent)         # still stuck, same afternoon
    _run(t0 + 3600, PROMPT, statef, sent)
    assert sent == [("red-team", "stuck")]       # no re-nag

def test_push2_next_morning_overnight_stuck(statef):
    sent = []
    t_pm = _epoch(2026, 8, 15, 23, 0, 0)         # 11pm ET, prompt appears
    _run(t_pm, PROMPT, statef, sent)
    _run(t_pm + 61, PROMPT, statef, sent)        # push1 ~11:01pm
    assert sent == [("red-team", "stuck")]
    t_am = _epoch(2026, 8, 16, 8, 0, 30)         # next day 08:00 ET, still stuck
    _run(t_am, PROMPT, statef, sent)
    assert sent == [("red-team", "stuck"), ("red-team", "overnight")]

def test_ceiling_2_then_silence(statef):
    sent = []
    t_pm = _epoch(2026, 8, 15, 23, 0, 0)
    _run(t_pm, PROMPT, statef, sent)
    _run(t_pm + 61, PROMPT, statef, sent)        # push1
    _run(_epoch(2026, 8, 16, 8, 0, 30), PROMPT, statef, sent)   # push2
    _run(_epoch(2026, 8, 16, 8, 1, 0), PROMPT, statef, sent)    # same morning, later
    _run(_epoch(2026, 8, 17, 8, 0, 30), PROMPT, statef, sent)   # NEXT morning
    assert sent == [("red-team", "stuck"), ("red-team", "overnight")]   # never a 3rd

def test_no_overnight_push_for_daytime_prompt(statef):
    # a prompt that appears AFTER 08:00 gets push1 but no same-day "overnight"
    # re-surface (push1_at is after today's 08:00 -> waits until tomorrow).
    sent = []
    t = _epoch(2026, 8, 16, 9, 0, 0)             # 9am, after 08:00
    _run(t, PROMPT, statef, sent)
    _run(t + 61, PROMPT, statef, sent)           # push1 ~9:01am
    _run(_epoch(2026, 8, 16, 12, 0, 0), PROMPT, statef, sent)   # noon same day
    assert sent == [("red-team", "stuck")]       # no overnight push same day

def test_dedup_resets_when_prompt_clears(statef):
    # op_key-collision fix: prompt clears (answered), a FRESH same-op_key prompt
    # later must be push-eligible again (not permanently silenced).
    sent = []
    t0 = _epoch(2026, 8, 15, 14, 0, 0)
    _run(t0, PROMPT, statef, sent)
    _run(t0 + 61, PROMPT, statef, sent)          # push1 fired
    _run(t0 + 120, {}, statef, sent)             # prompt CLEARED (answered/vanished)
    # state for this op_key must be dropped
    assert json.load(open(statef)) == {}
    # a fresh prompt of the same shape reappears later
    t1 = t0 + 5000
    _run(t1, PROMPT, statef, sent)               # new first_seen
    _run(t1 + 61, PROMPT, statef, sent)          # push1 AGAIN (correct)
    assert sent == [("red-team", "stuck"), ("red-team", "stuck")]

def test_state_is_durable_across_calls(statef):
    # push1 recorded in one "process", a fresh load must not re-push.
    sent = []
    t0 = _epoch(2026, 8, 15, 14, 0, 0)
    _run(t0, PROMPT, statef, sent)
    _run(t0 + 61, PROMPT, statef, sent)          # push1
    # simulate cron restart: brand-new call reads the file
    N.permission_backstop(now=t0 + 200, state_path=statef,
                          scan=lambda: PROMPT,
                          send=lambda a, q, k: sent.append((a, k)))
    assert sent == [("red-team", "stuck")]       # durable: no double push1

def test_scan_failure_is_fail_open(statef):
    # a scan that raises must not crash the beat (returns [], no state churn).
    def _boom(): raise RuntimeError("tmux down")
    out = N.permission_backstop(now=time.time(), state_path=statef,
                                scan=_boom, send=lambda a, q, k: None)
    assert out == []


# ---- DEC-1786771513: instance-keyed op_key = both consecutive prompts notify ----
def test_consecutive_instances_both_notify(statef):
    # THE NAMED REGRESSION (D2 half): prompt #1 (op_key :1) and prompt #2 (op_key
    # :2, distinct instance) EACH get their own push — #2 is NOT suppressed as a
    # dup of #1. This is what the instance-keyed op_key buys.
    sent = []
    t0 = _epoch(2026, 8, 15, 14, 0, 0)
    P1 = {"menu:red-team:abc:1": {"agent": "red-team", "question": "Do you want to proceed?"}}
    P2 = {"menu:red-team:abc:2": {"agent": "red-team", "question": "Do you want to proceed?"}}
    _run(t0, P1, statef, sent)
    _run(t0 + 61, P1, statef, sent)              # push1 for instance 1
    _run(t0 + 62, {}, statef, sent)              # #1 clears
    _run(t0 + 63, P2, statef, sent)              # #2 appears (distinct op_key)
    _run(t0 + 124, P2, statef, sent)             # push1 for instance 2
    assert sent == [("red-team", "stuck"), ("red-team", "stuck")]   # BOTH notified

def test_read_instance_n_fallback_is_1(tmp_path):
    # fail-open: absent/corrupt ledger -> instance_n 1 (bounded D-A gap, never raise)
    assert N._read_instance_n({}, "red-team", "abc") == 1
    assert N._read_instance_n({"red-team|abc": {"instance_n": 3}}, "red-team", "abc") == 3
    assert N._read_instance_n({"red-team|abc": {}}, "red-team", "abc") == 1
