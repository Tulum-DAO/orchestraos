"""Off-tailnet escalation follow-ups (gm msg_083b5273, RED-first, gm gate):
(1) DIGEST mode — when MORE than ESCALATION_DIGEST_MAX_SINGLES (3) rows qualify
    on a beat, send ONE digest Telegram listing id/from/question/options per
    card (same per-card `escalate:` dedup keys, stamped only on ok); at <=3
    keep per-card full messages.
(2) SIDECAR SEAM — TAILNET_NOTIFY_STATE_PATH env override + conftest default so
    a live-tree test run can never write the prod state/notify-tailnet-state.json
    (the existing telegram cron tests call cron_backstop without the kwarg and
    used to write it).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approval_notify as N
import approval_config as C
from approval_schema import ApprovalStore
from questionnaire_schema import QuestionnaireStore

NOW = 1_789_000_000.0
OFF = {"online": False, "off_tailnet": True, "last_seen": None, "reason": "offline_for:50400"}
ON = {"online": True, "off_tailnet": False, "last_seen": None, "reason": "online"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    s = ApprovalStore(db_path=db); s.migrate()
    qs = QuestionnaireStore(db_path=db); qs.migrate()
    monkeypatch.setattr(N, "ntfy_token", lambda: "tok")
    monkeypatch.setattr(N, "_http_post", lambda url, headers, data: 200)
    monkeypatch.setenv("NOTIFY_ESCALATION_LOG_PATH", str(tmp_path / "esc.log"))
    return {"store": s, "qstore": qs, "tg_state": str(tmp_path / "tg.json"),
            "net_state": str(tmp_path / "net.json"), "tg": []}


def _beat(env, status=OFF, ok_tg=True):
    def tg(text):
        env["tg"].append(text); return ok_tg
    return N.cron_backstop(store=env["store"], qstore=env["qstore"], tg_send=tg,
                           tg_state_path=env["tg_state"], tailnet_status=status,
                           tailnet_state_path=env["net_state"],
                           ntfy_publish=lambda p: True, now=NOW)


def _cards(env, n):
    return [env["store"].create(from_agent=f"agent{i}", question=f"Question {i}?",
                                worker_kind="dev", options=["approve", "deny", "hold"])
            for i in range(n)]


def _esc(env):
    return [t for t in env["tg"] if "ESCALATION" in t]


# ---------------- (1) digest ----------------
def test_threshold_is_config():
    assert C.ESCALATION_DIGEST_MAX_SINGLES == 3


def test_three_or_fewer_stay_per_card(env):
    rids = _cards(env, 3)
    _beat(env)
    e = _esc(env)
    assert len(e) == 3 and all(any(r in t for t in e) for r in rids)
    assert not any("DIGEST" in t for t in e)


def test_more_than_three_send_one_digest(env):
    rids = _cards(env, 7)
    _beat(env)
    e = _esc(env)
    assert len(e) == 1 and "DIGEST" in e[0]
    d = e[0]
    assert "7 pending" in d
    for i, r in enumerate(rids):
        assert r in d and f"Question {i}?" in d and f"agent{i}" in d
    assert d.count("approve") >= 7                     # options listed per card
    assert "Tailscale" in d                            # why he got it
    st = N._load_tg_state(env["tg_state"])
    assert all(st[f"escalate:approval:{r}"]["sent_at"] for r in rids)   # every card stamped


def test_digest_counts_questionnaires(env):
    _cards(env, 2)
    for i in range(2):
        env["qstore"].create(from_agent="pm", title=f"Qnr {i}", questions=[
            {"n": "1", "question": "a?", "options": ["x", "y"]}])
    _beat(env)
    e = _esc(env)
    assert len(e) == 1 and "DIGEST" in e[0] and "Qnr 0" in e[0] and "Qnr 1" in e[0]


def test_digest_failure_stamps_nothing_and_retries(env):
    rids = _cards(env, 5)
    _beat(env, ok_tg=False)
    assert not any(k.startswith("escalate:") for k in N._load_tg_state(env["tg_state"]))
    _beat(env, ok_tg=True)
    e = _esc(env)
    assert len(e) == 2 and all("DIGEST" in t for t in e)   # retried as a digest again
    assert all(N._load_tg_state(env["tg_state"]).get(f"escalate:approval:{r}") for r in rids)


def test_digest_dedups_and_only_unsent_count_toward_threshold(env):
    rids = _cards(env, 5)
    _beat(env)                                         # one digest, all 5 stamped
    _beat(env)
    assert len(_esc(env)) == 1                         # nothing new -> nothing sent
    new = _cards(env, 2)                               # 2 NEW unsent rows -> per-card
    _beat(env)
    e = _esc(env)
    assert len(e) == 3
    assert all("DIGEST" not in t for t in e[1:]) and all(any(r in t for t in e[1:]) for r in new)


def test_digest_logged(env, tmp_path):
    _cards(env, 4)
    _beat(env)
    log = (tmp_path / "esc.log").read_text()
    assert "digest" in log and "4 card" in log


# ---------------- (2) sidecar seam ----------------
def test_sidecar_env_override_is_honoured(env, tmp_path, monkeypatch):
    target = tmp_path / "override-net.json"
    monkeypatch.setenv("TAILNET_NOTIFY_STATE_PATH", str(target))
    assert os.path.normpath(N._tailnet_state_path()) == os.path.normpath(str(target))
    N.refire_on_return(env["store"], env["qstore"], ON, publish=lambda p: True, now=NOW)
    assert target.exists() and json.loads(target.read_text())["off"] is False


def test_sidecar_prod_default_when_unset(monkeypatch):
    monkeypatch.delenv("TAILNET_NOTIFY_STATE_PATH", raising=False)
    expect = os.path.normpath(os.path.join(os.path.dirname(N.__file__), "..", "state", "notify-tailnet-state.json"))
    assert os.path.normpath(N._tailnet_state_path()) == expect


def test_conftest_guards_prod_sidecar_for_every_test_run():
    p = os.environ.get("TAILNET_NOTIFY_STATE_PATH")
    assert p and "notify-tailnet-TESTS" in p


def test_cron_without_kwarg_never_touches_prod_sidecar(env):
    """The existing telegram cron tests' shape: cron_backstop with NO
    tailnet_state_path — must land in the tmp seam, not state/."""
    prod = os.path.join(os.path.dirname(N.__file__), "..", "state", "notify-tailnet-state.json")
    before = os.path.getsize(prod) if os.path.exists(prod) else None
    _cards(env, 1)
    N.cron_backstop(store=env["store"], qstore=env["qstore"], tg_send=lambda t: True,
                    tg_state_path=env["tg_state"], tailnet_status=ON,
                    ntfy_publish=lambda p: True, now=NOW)
    after = os.path.getsize(prod) if os.path.exists(prod) else None
    assert after == before
    assert os.path.exists(os.environ["TAILNET_NOTIFY_STATE_PATH"])
