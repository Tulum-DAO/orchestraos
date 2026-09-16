"""S3 item 4 — permission durable-resume/escalation (Universal Decision-Surface
Pipeline spec §2.5 + build guards §3.1-d/e).

Unlike the 2-push cap, an ignored permission prompt keeps escalating on the
approval SLA cadence (ESCALATE_REPEAT_MINUTES) until it is answered or vanishes.
Sidecar-only (guard d: no ledger row -> no second surface row). Guard e: an
op_key absent from the scan clears its record immediately. All time/scan/send
injected — no real tmux/network. SHIPS DISABLED (flag default False).
"""
import os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approval_notify as N

PROMPT = {"menu:red-team:abc:1": {"agent": "red-team", "question": "Proceed?"}}
REPEAT = 1800   # 30 min, matches ESCALATE_REPEAT_MINUTES


@pytest.fixture
def statef(tmp_path):
    return str(tmp_path / "perm-durable.json")


def _run(now, present, statef, sent, repeat=REPEAT):
    return N.permission_durable_backstop(
        now=now, state_path=statef, scan=lambda: present,
        send=lambda a, q, kind: sent.append((a, kind)),
        escalate_repeat_s=repeat)


def test_no_push_before_delay(statef):
    sent = []
    t0 = 1_000_000.0
    _run(t0, PROMPT, statef, sent)
    _run(t0 + 59, PROMPT, statef, sent)
    assert sent == []


def test_push1_after_delay(statef):
    sent = []
    t0 = 1_000_000.0
    _run(t0, PROMPT, statef, sent)
    _run(t0 + 61, PROMPT, statef, sent)
    assert sent == [("red-team", "stuck")]


def test_escalates_uncapped_on_sla_cadence(statef):
    # THE differentiator from the 2-push cap: keeps escalating while present.
    sent = []
    t0 = 1_000_000.0
    _run(t0, PROMPT, statef, sent)                     # first sight
    _run(t0 + 61, PROMPT, statef, sent)                # push1
    _run(t0 + 61 + REPEAT, PROMPT, statef, sent)       # escalate #1
    _run(t0 + 61 + 2 * REPEAT, PROMPT, statef, sent)   # escalate #2
    _run(t0 + 61 + 3 * REPEAT, PROMPT, statef, sent)   # escalate #3 (past the old 2-cap)
    tags = [k for (_a, k) in sent]
    assert tags == ["stuck", "escalate", "escalate", "escalate"]


def test_no_escalate_before_repeat_window(statef):
    sent = []
    t0 = 1_000_000.0
    _run(t0, PROMPT, statef, sent)
    _run(t0 + 61, PROMPT, statef, sent)                # push1
    _run(t0 + 61 + REPEAT - 1, PROMPT, statef, sent)   # not yet due
    assert [k for (_a, k) in sent] == ["stuck"]


def test_guard_e_absence_clears_record(statef):
    # answered/vanished prompt -> record dropped -> a fresh same op_key re-notifies
    sent = []
    t0 = 1_000_000.0
    _run(t0, PROMPT, statef, sent)
    _run(t0 + 61, PROMPT, statef, sent)                # push1
    _run(t0 + 120, {}, statef, sent)                   # prompt gone -> cleared
    st = N._load_perm_state(statef)
    assert "menu:red-team:abc:1" not in st
    _run(t0 + 200, PROMPT, statef, sent)               # reappears -> new first_seen
    _run(t0 + 262, PROMPT, statef, sent)               # push1 again after delay
    assert [k for (_a, k) in sent] == ["stuck", "stuck"]


def test_guard_d_sidecar_only_no_ledger_row(statef):
    # the durable record is notify-only: state carries ONLY notify-tracking keys,
    # never an approval/ledger id (it must not duplicate the pseudo-row).
    sent = []
    t0 = 1_000_000.0
    _run(t0, PROMPT, statef, sent)
    _run(t0 + 61, PROMPT, statef, sent)
    entry = N._load_perm_state(statef)["menu:red-team:abc:1"]
    assert set(entry.keys()) == {"agent", "question", "first_seen",
                                 "notified_at", "last_escalated_at", "escalations"}
    assert "id" not in entry and "row_id" not in entry and "approval_id" not in entry


def test_flag_disabled_by_default(tmp_path):
    assert N._perm_durable_enabled(str(tmp_path / "nope.json")) is False


def test_flag_enabled_reads_true(tmp_path):
    import json
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"enabled": True}))
    assert N._perm_durable_enabled(str(p)) is True


def test_escalate_message_wording():
    got = {}
    import subprocess
    # _send_perm_push shells tg-notify.sh; capture the text arg without running it
    orig = subprocess.run
    def fake_run(cmd, **kw):
        got["text"] = cmd[-1]
        class R: returncode = 0
        return R()
    subprocess.run = fake_run
    try:
        N._send_perm_push("red-team", "Proceed?", "escalate")
    finally:
        subprocess.run = orig
    assert "STILL NEEDED" in got["text"] and "red-team" in got["text"]
