"""Phase-2 item-3 channel wiring (RED) — U12 page fires BOTH channels.

gm ruled (msg_80df6e59) the U12 projector-liveness page has TWO concrete channels:
Telegram to the operator (primary) + a gm msg_store message. This proves both fire, so
the runbook's G3 "verified-to-page" observation is unambiguous. Senders are
injectable so the proof runs without hitting the real channels.

RED until ``monitor.page`` exists.
"""
import os

import pytest

from scripts.identity_store import cutover, monitor, orchestra_db, projector

ROOT = "orchestra-builder"


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def test_page_fires_both_channels():
    tg, gm = [], []
    monitor.page({"kind": "projector-stale", "age": 999},
                 telegram_send=tg.append, gm_send=gm.append)
    assert len(tg) == 1 and len(gm) == 1
    assert "U12" in tg[0] and "U12" in gm[0]


def test_armed_stale_check_pages_both_channels(orchdir):
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
              (ROOT, "T2", "claude"))
    gid = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                    "VALUES (?,?,?,?)", (ROOT, 1, "s1", "m")).lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES (?,?,?,'online')", (ROOT, gid, ROOT))
    proj = os.path.join(orchdir, "projections")
    projector.project(c, proj, now=100.0)
    c.close()

    monitor.arm(orchdir)
    tg, gm = [], []

    def alarm(detail):
        monitor.page(detail, telegram_send=tg.append, gm_send=gm.append)

    res = monitor.check(orchdir, proj, now=100000.0, max_age_s=60.0, alarm=alarm)
    assert res["healthy"] is False
    assert len(tg) == 1 and len(gm) == 1, "a stale projection must page BOTH channels"
