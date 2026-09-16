"""Off-tailnet escalation for approval notifies (gm commission msg_c5757395,
fix (a) of the apr_321ad47b delivery gap).

the operator never received the BG ARM card: the ntfy push published fine but ntfy is
served tailnet-only and his phone had been off Tailscale ~14h — the push died
silently, the Telegram companion was a ~159-char stub he missed. This builds:
  1. phone_tailnet_status(): `tailscale status --json` last-seen for the phone
     (host + threshold from approval_config), timeout + FAIL-OPEN (unknown =>
     ON-tailnet, never spam).
  2. Off-tailnet => FULL-card Telegram (question + options + reply
     instructions) for ANY pending card, sidecar-deduped, stamped only on ok.
  3. Phone RETURNS => re-fire the ntfy push for still-pending cards, once per
     return event.
  4. Every decision logged (NOTIFY_ESCALATION_LOG_PATH; conftest points tests
     at tmp) — never silent.
Hermetic: injected tailscale runner / tg send / ntfy publish; tmp DB + sidecars.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approval_notify as N
import approval_config as C
from approval_schema import ApprovalStore
from questionnaire_schema import QuestionnaireStore

NOW = 1_789_000_000.0

# Real `tailscale status --json` peer shape (observed 2026-09-09): the phone's
# HostName is literally "localhost"; identity is the DNSName. Online peers carry
# a ZERO LastSeen; offline peers carry the real last-seen.
def _ts_json(online, last_seen_iso):
    return json.dumps({"Self": {"HostName": "test-vps-host"}, "Peer": {
        "k1": {"HostName": "localhost", "DNSName": "iphone-15-pro-max.testnet.ts.net.",
               "Online": False, "LastSeen": "2026-04-16T18:16:42.1Z", "OS": "iOS"},
        "k2": {"HostName": "localhost", "DNSName": "iphone172.testnet.ts.net.",
               "Online": online, "LastSeen": last_seen_iso, "OS": "iOS"},
    }})


def _runner(stdout=None, exc=None):
    def run(cmd, **kw):
        assert cmd[:2] == ["tailscale", "status"] and kw.get("timeout")
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
    return run


def _iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------- 1. detection ----------------
def test_config_is_not_hardcoded():
    assert C.PHONE_TAILNET_HOST == "iphone172"
    assert C.PHONE_OFFTAILNET_THRESHOLD_S >= 300
    assert C.TAILSCALE_STATUS_TIMEOUT_S <= 10


def test_phone_online_is_on_tailnet():
    st = N.phone_tailnet_status(now=NOW, run=_runner(_ts_json(True, "0001-01-01T00:00:00Z")))
    assert st["off_tailnet"] is False and st["online"] is True and st["reason"] == "online"


def test_phone_offline_beyond_threshold_is_off_tailnet():
    st = N.phone_tailnet_status(now=NOW, run=_runner(_ts_json(False, _iso(NOW - 14 * 3600))))
    assert st["off_tailnet"] is True and st["online"] is False
    assert abs(st["last_seen"] - (NOW - 14 * 3600)) < 1
    assert st["reason"].startswith("offline_for:")


def test_phone_offline_within_threshold_not_yet_off():
    st = N.phone_tailnet_status(now=NOW, run=_runner(_ts_json(False, _iso(NOW - 30))))
    assert st["off_tailnet"] is False and st["reason"].startswith("offline_recent:")


@pytest.mark.parametrize("kind,runner", [
    ("timeout", _runner(exc=subprocess.TimeoutExpired(["tailscale"], 5))),
    ("bad_json", _runner(stdout="not json")),
    ("peer_missing", _runner(json.dumps({"Peer": {}}))),
    ("oserror", _runner(exc=OSError("no tailscale binary"))),
])
def test_unknown_fails_open_to_on_tailnet(kind, runner):
    st = N.phone_tailnet_status(now=NOW, run=runner)
    assert st["off_tailnet"] is False
    assert st["reason"].startswith("unknown:")


# ---------------- harness for the cron beat ----------------
@pytest.fixture
def env(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    s = ApprovalStore(db_path=db); s.migrate()
    qs = QuestionnaireStore(db_path=db); qs.migrate()
    monkeypatch.setattr(N, "ntfy_token", lambda: "tok")
    pushes = []
    # The normal notify() path posts to the real ntfy endpoint — intercept it
    # (never hit the live server from a test) and record the payload.
    monkeypatch.setattr(N, "_http_post", lambda url, headers, data: (pushes.append(json.loads(data)), 200)[1])
    log = tmp_path / "escalation.log"
    monkeypatch.setenv("NOTIFY_ESCALATION_LOG_PATH", str(log))
    return {"store": s, "qstore": qs, "tg_state": str(tmp_path / "tg.json"),
            "net_state": str(tmp_path / "tailnet.json"), "log": log,
            "ntfy": pushes, "tg": []}


def _beat(env, off, ok_tg=True, now=NOW):
    status = {"online": not off, "off_tailnet": off, "last_seen": None,
              "reason": "offline_for:50400" if off else "online"}
    def tg(text):
        env["tg"].append(text); return ok_tg
    def publish(payload):
        env["ntfy"].append(payload); return True
    return N.cron_backstop(store=env["store"], qstore=env["qstore"], tg_send=tg,
                           tg_state_path=env["tg_state"], tailnet_status=status,
                           tailnet_state_path=env["net_state"], ntfy_publish=publish,
                           now=now)


def _card(env, q="Arm BG on second-brain-dev?", opts=("approve", "deny", "hold"), **kw):
    return env["store"].create(from_agent="gm", question=q, worker_kind="dev",
                               options=list(opts), **kw)


# ---------------- 2. full-card escalation ----------------
def test_off_tailnet_escalates_full_card_not_stub(env):
    rid = _card(env)
    _beat(env, off=True)
    full = [t for t in env["tg"] if "ESCALATION" in t]
    assert len(full) == 1
    t = full[0]
    assert "Arm BG on second-brain-dev?" in t
    for o in ("approve", "deny", "hold"):
        assert o in t
    assert rid in t                                     # reply instructions carry the id
    assert "Tailscale" in t                             # says WHY he got the long form
    assert len(t) > 300                                 # not the ~159-char stub


def test_menu_card_escalation_lists_menu_option_labels(env):
    menu = {"question": "Which slice first?", "options": [
        {"n": "1", "label": "Onboarding funnel"}, {"n": "2", "label": "Billing hooks"}]}
    rid = env["store"].create(from_agent="pm-x", question="Which slice first?",
                              worker_kind="pane", kind="menu", menu=menu,
                              options=["Onboarding funnel", "Billing hooks"])
    _beat(env, off=True)
    t = [t for t in env["tg"] if "ESCALATION" in t][0]
    assert "1. Onboarding funnel" in t and "2. Billing hooks" in t and rid in t


def test_on_tailnet_never_escalates(env):
    _card(env)
    _beat(env, off=False)
    assert not [t for t in env["tg"] if "ESCALATION" in t]
    assert env["tg"]                                    # the normal stub still fired


def test_escalation_dedups_per_card_and_prunes(env):
    rid = _card(env)
    _beat(env, off=True); _beat(env, off=True); _beat(env, off=True)
    assert len([t for t in env["tg"] if "ESCALATION" in t]) == 1
    st = N._load_tg_state(env["tg_state"])
    assert st[f"escalate:approval:{rid}"]["sent_at"]
    env["store"].record_answer(rid, "approve", None)
    _beat(env, off=True)
    assert f"escalate:approval:{rid}" not in N._load_tg_state(env["tg_state"])


def test_escalation_failure_not_stamped_retries(env):
    _card(env)
    _beat(env, off=True, ok_tg=False)
    assert not any(k.startswith("escalate:") for k in N._load_tg_state(env["tg_state"]))
    _beat(env, off=True, ok_tg=True)
    assert len([t for t in env["tg"] if "ESCALATION" in t]) == 2   # retried, then stamped
    assert any(k.startswith("escalate:") for k in N._load_tg_state(env["tg_state"]))


def test_escalates_even_when_stub_already_sent_earlier(env):
    """The apr_321ad47b case exactly: stub went out while on-tailnet; phone drops."""
    rid = _card(env)
    _beat(env, off=False)                               # stub sent, stamped
    assert not [t for t in env["tg"] if "ESCALATION" in t]
    _beat(env, off=True)
    assert len([t for t in env["tg"] if "ESCALATION" in t and rid in t]) == 1


# ---------------- 3. ntfy re-fire on return ----------------
def test_return_refires_ntfy_once_per_return(env):
    rid = _card(env)
    _beat(env, off=False)                               # first ntfy push (normal path)
    assert len(env["ntfy"]) == 1
    _beat(env, off=True)                                # phone drops; nothing re-fires
    assert len(env["ntfy"]) == 1
    _beat(env, off=False, now=NOW + 600)                # RETURN -> re-fire once
    assert len(env["ntfy"]) == 2 and rid in env["ntfy"][1]["actions"][0]["body"]
    _beat(env, off=False, now=NOW + 660)                # still on -> no repeat
    _beat(env, off=False, now=NOW + 720)
    assert len(env["ntfy"]) == 2
    _beat(env, off=True, now=NOW + 780)                 # second drop + return -> once more
    _beat(env, off=False, now=NOW + 840)
    assert len(env["ntfy"]) == 3


def test_return_does_not_double_push_never_notified_rows(env):
    """A row whose first push never landed (notified_at NULL) is the normal
    backstop's job on this beat — the return re-fire must not push it twice."""
    _beat(env, off=True)
    rid = _card(env)                                    # created while off
    _beat(env, off=False, now=NOW + 600)                # return beat
    assert len([p for p in env["ntfy"] if rid in p["actions"][0]["body"]]) == 1


def test_return_skips_answered_rows(env):
    rid = _card(env)
    _beat(env, off=False); _beat(env, off=True)
    env["store"].record_answer(rid, "approve", None)
    _beat(env, off=False, now=NOW + 600)
    assert len(env["ntfy"]) == 1


# ---------------- 4. never silent ----------------
def test_every_decision_is_logged(env):
    rid = _card(env)
    _beat(env, off=True)
    _beat(env, off=False, now=NOW + 600)
    log = env["log"].read_text()
    assert "off_tailnet" in log and rid in log          # detection + escalation
    assert "escalated" in log
    assert "return" in log and "refire" in log          # return + re-fire
    st = N.phone_tailnet_status(now=NOW, run=_runner(exc=OSError("x")))
    assert "unknown:" in env["log"].read_text()         # fail-open is logged too


# ---------------- gotcha: the Aug-30 crash path ----------------
def test_stamp_outrun_skips_is_healthy_on_migrated_store(env):
    rid = _card(env)
    env["store"].record_answer(rid, "approve", None)          # answered before any push
    N.stamp_outrun_skips(store=env["store"])
    assert env["store"].get(rid)["notify_skipped"].startswith("answered-before-push@")
