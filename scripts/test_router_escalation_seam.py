"""Escalation seam: an alarm about a seat goes to its escalation (never to itself) and always
ends at the owner channel; an absent seam and a broken contract are different, loud states.
Scratch registry DBs and fakes only; nothing is sent."""
import importlib.util
import os
import sqlite3
import sys
from types import SimpleNamespace

import pytest

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("message_router_escalation",
                                               os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)
sys.path.insert(0, os.path.dirname(_here))
from scripts.identity_store import alarm_routing as ar  # noqa: E402


def _registry(tmp_path, rows):
    os.makedirs(tmp_path / "state", exist_ok=True)
    con = sqlite3.connect(tmp_path / "state" / "orchestra-registry.db")
    con.execute("CREATE TABLE lineages (root TEXT PRIMARY KEY, reports_to TEXT)")
    con.executemany("INSERT INTO lineages VALUES (?, ?)", rows)
    con.commit()
    con.close()
    return str(tmp_path)


def test_router_and_module_agree_on_the_owner_channel():
    assert mr.OWNER_CHANNEL == ar.OWNER_CHANNEL
    assert mr._OI_IMPORT_ERROR is None and mr._OI_CONTRACT_ERROR is None


def test_reports_to_parent_then_owner_channel(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRA_ROOT_ESCALATION", raising=False)
    d = _registry(tmp_path, [("alpha", "lead-a"), ("lead-a", "gm"), ("solo", None),
                             ("loop", "loop"), ("top", "owner")])
    assert ar.escalation_targets("alpha", d) == ["lead-a", "owner:tg"]
    assert ar.escalation_targets("solo", d) == ["gm", "owner:tg"]       # no parent: gm
    assert ar.escalation_targets("loop", d) == ["gm", "owner:tg"]       # never itself
    assert ar.escalation_targets("top", d) == ["gm", "owner:tg"]        # owner is not an inbox
    assert ar.escalation_targets("unknown", d) == ["gm", "owner:tg"]


def test_alarm_about_gm_never_goes_to_gm(tmp_path, monkeypatch):
    d = _registry(tmp_path, [])
    monkeypatch.delenv("ORCHESTRA_ROOT_ESCALATION", raising=False)
    assert ar.escalation_targets("gm", d) == ["owner:tg"]
    monkeypatch.setenv("ORCHESTRA_ROOT_ESCALATION", "ops-seat")
    assert ar.escalation_targets("gm", d) == ["ops-seat", "owner:tg"]


def test_unreadable_db_still_escalates(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRA_ROOT_ESCALATION", raising=False)
    assert ar.escalation_targets("alpha", str(tmp_path / "missing")) == ["gm", "owner:tg"]


def test_owner_id_env_names_the_human_parent(tmp_path, monkeypatch):
    d = _registry(tmp_path, [("alpha", "pat")])
    assert ar.escalation_targets("alpha", d) == ["pat", "owner:tg"]
    monkeypatch.setenv("ORCHESTRA_OWNER_ID", "pat")
    assert ar.escalation_targets("alpha", d) == ["gm", "owner:tg"]


def test_router_drops_the_seat_and_checks_the_return_type(monkeypatch):
    monkeypatch.setattr(mr, "_oi_escalation_targets", lambda seat: [seat, "gm", "owner:tg"])
    assert mr.escalation_targets("alpha") == ["gm", "owner:tg"]
    monkeypatch.setattr(mr, "_oi_escalation_targets", lambda seat: ("gm",))
    with pytest.raises(mr.EscalationSeamBroken):
        mr.escalation_targets("alpha")


def test_absent_and_broken_seams_raise_different_errors(monkeypatch):
    monkeypatch.setattr(mr, "_oi_escalation_targets", None)
    monkeypatch.setattr(mr, "_OI_IMPORT_ERROR", "ImportError('x')")
    with pytest.raises(mr.EscalationSeamUnavailable):
        mr.escalation_targets("alpha")
    monkeypatch.setattr(mr, "_OI_CONTRACT_ERROR", "does not accept escalation_targets(<seat>)")
    with pytest.raises(mr.EscalationSeamBroken):
        mr.escalation_targets("alpha")
    assert not issubclass(mr.EscalationSeamBroken, mr.EscalationSeamUnavailable)
    assert not issubclass(mr.EscalationSeamUnavailable, mr.EscalationSeamBroken)


def test_send_page_renders_the_owner_channel_and_sends_agents_via_msg_store(monkeypatch):
    tg, runs = [], []
    monkeypatch.setattr(mr, "telegram", tg.append)
    monkeypatch.setattr(mr.subprocess, "run",
                        lambda argv, **k: runs.append(argv) or SimpleNamespace(returncode=0, stderr=""))
    mr._send_page("owner:tg", "[DOWN] a", "body")
    assert tg == ["[DOWN] a\nbody"] and runs == []
    mr._send_page("gm", "[DOWN] a", "body")
    assert runs and runs[0][2:] == ["send", "--from", "message-router", "--to", "gm",
                                    "--subject", "[DOWN] a", "--body", "body"]
    assert runs[0][1].endswith("msg_store.py")


def test_send_page_failure_raises(monkeypatch):
    monkeypatch.setattr(mr.subprocess, "run",
                        lambda argv, **k: SimpleNamespace(returncode=2, stderr="boom"))
    with pytest.raises(RuntimeError):
        mr._send_page("gm", "s", "b")
