"""A multi-part menu's card is born complete, whichever comes first: the client's read or the bridge's tick.

The race this closes: the dashboard read the menu (the gateway's active walk) before the menu bridge's cron tick made
the card; the walk had no card to write to, the tick then stored part 0 alone, and the device identity gate refused
the answer. Now the walk makes the card itself (menu_read_bridge) and writes only onto the card of the menu it walked.

Everything here is the REAL code: the gateway's /agent-menu-capture handler on build_app() (the real walk, cache read
and write-back), menu_read_bridge, menu_bridge.bridge_one (the cron's own bridge), and ApprovalStore on a temp
database. Stand-ins only for the tmux pane and the detector: a three-part AskUserQuestion that moves between parts on
Right/Left the way the CLI does.
"""
import asyncio
import json
import os
import sys
import threading
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_gateway as G  # noqa: E402
import menu_bridge as M  # noqa: E402
import menu_read_bridge as MRB  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402

SESSION = "dev-demo"
FLEET = "fleet-bearer-0123456789abcdef-for-tests"


def three_parts(tag):
    return [
        {"question": f"Which colour? ({tag})", "select": "single", "options": [{"n": "1", "label": "Blue"}, {"n": "2", "label": "Green"}]},
        {"question": f"Which sizes? ({tag})", "select": "multi", "options": [{"n": "1", "label": "Small"}, {"n": "2", "label": "Large"}]},
        {"question": f"Which finish? ({tag})", "select": "single", "options": [{"n": "1", "label": "Matte"}, {"n": "2", "label": "Gloss"}]},
    ]


class Pane:
    """The agent's screen: one multi-part menu, on part `at`; Right/Left move between parts."""

    def __init__(self, tag="a"):
        self.show(tag)

    def show(self, tag):
        self.parts, self.at, self.keys = three_parts(tag), 0, []

    def menu(self):
        p = self.parts[self.at]
        return {"kind": "options", "question": p["question"], "context": "", "options": p["options"],
                "multipart": True, "part_count": 3, "part_index": 0, "menu_family": "claude",
                "tabs": ["Colour", "Sizes", "Finish", "Submit"], "submit_tab_index": 3,
                "parts": [{"index": 0, "question": p["question"], "select": p["select"], "options": p["options"]}]}

    def key(self, k):
        self.keys.append(k)
        if k == "Right":
            self.at = min(2, self.at + 1)
        elif k == "Left":
            self.at = max(0, self.at - 1)


class _Done:
    returncode = 0
    stdout = ""


@pytest.fixture
def box(tmp_path, monkeypatch):
    db = str(tmp_path / "tasks.db")
    store = ApprovalStore(db_path=db); store.migrate()
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {SESSION: {"tmux_session": SESSION}}}))
    cfg = tmp_path / "surface-pickup-config.json"
    cfg.write_text(json.dumps({"enabled": True, "skip_sessions": [], "skip_prefixes": [], "store_but_mark": True}))
    tok = tmp_path / "gateway-token"; tok.write_text(FLEET)
    pane = Pane()
    monkeypatch.setattr(G, "TOKEN_FILE", tok)
    monkeypatch.setattr(G, "ApprovalStore", lambda: ApprovalStore(db_path=db))
    monkeypatch.setattr(G, "_tmux_session_names", lambda: {SESSION})
    monkeypatch.setattr(G, "_protected_refusal", lambda request, session: None)
    monkeypatch.setattr(G, "_menu_hook_instance", lambda session, out: None)
    monkeypatch.setattr(G, "_current_menu", lambda session: pane.menu())

    class _Status:
        def get_agent_status(self, session):
            return {"state": "waiting_permission", "pending_menu": pane.menu()}
    monkeypatch.setattr(G, "_agent_status", lambda: _Status())

    def _tmux(*args, **kw):
        if args and args[0] == "send-keys":
            pane.key(args[-1])
        return _Done()
    monkeypatch.setattr(G, "_tmux", _tmux)
    # the walk's settle pause: real code, no real screen to wait for
    real_walk = G.menu_capture_walk
    monkeypatch.setattr(G, "menu_capture_walk", lambda session, **kw: real_walk(session, settle_s=0, **kw))
    monkeypatch.setattr(M, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(M, "SURFACE_PICKUP_CONFIG", str(cfg))
    monkeypatch.setattr(M, "SURFACE_PICKUP_KILLSWITCH", str(tmp_path / "SURFACE_PICKUP_DISABLED"))
    return {"store": store, "pane": pane, "cfg": cfg, "db": db}


def read(box):
    """The dashboard opening the menu: POST /agent-menu-capture on the real app."""
    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            r = await c.post("/agent-menu-capture", json={"session": SESSION},
                             headers={"Authorization": f"Bearer {FLEET}"})
            return r.status, await r.json()
    return asyncio.run(go())


def tick(box):
    """The bridge's cron tick for this session: the real bridge_one, passive walk, store-but-mark on."""
    cap = box["pane"].menu() if box["pane"].at == 0 else None
    return M.bridge_one(box["store"], SESSION, cap, 0.0, time.time(), "waiting_permission", debounce_s=0.0,
                        walk_fn=M._noop_walk_fn, store_but_mark=True)


def rows(box):
    import sqlite3
    c = sqlite3.connect(box["db"]); c.row_factory = sqlite3.Row
    out = []
    for r in c.execute("SELECT * FROM approval_requests WHERE kind='menu' ORDER BY created_at").fetchall():
        d = dict(r); d["menu"] = json.loads(d["menu"]); out.append(d)
    return out


def questions(row):
    return [p.get("question") for p in row["menu"].get("parts") or []]


def test_read_first_makes_one_complete_card_with_bridge_provenance(box):
    st, body = read(box)
    assert st == 200 and body["walk_complete"] is True and len(body["parts"]) == 3
    (row,) = rows(box)
    assert row["origin"] == "menu_bridge" and row["op_key"].startswith(f"menu:{SESSION}:")
    assert row["menu"]["walk_complete"] is True and "needs_hydration" not in row["menu"]
    assert questions(row) == ["Which colour? (a)", "Which sizes? (a)", "Which finish? (a)"]
    # the device identity gate now has every part's question
    assert set(G._anchor_expect_questions(row)) == {0, 1, 2}
    # the walk left the screen where it found it
    assert box["pane"].at == 0 and box["pane"].keys.count("Right") == box["pane"].keys.count("Left") == 2


def test_card_first_is_completed_by_the_read_not_duplicated(box):
    rid = tick(box)
    (row,) = rows(box)
    assert row["id"] == rid and row["menu"]["needs_hydration"] is True and len(row["menu"]["parts"]) == 1
    st, body = read(box)
    assert st == 200 and body["walk_complete"] is True
    (row,) = rows(box)
    assert row["id"] == rid and row["menu"]["walk_complete"] is True and "needs_hydration" not in row["menu"]
    assert questions(row) == ["Which colour? (a)", "Which sizes? (a)", "Which finish? (a)"]


def test_two_reads_and_a_tick_give_one_card(box):
    read(box)
    keys_after_first = len(box["pane"].keys)
    st, body = read(box)
    assert st == 200 and body.get("cached") is True          # the second read is served from the card
    assert len(box["pane"].keys) == keys_after_first          # and presses nothing
    tick(box)
    (row,) = rows(box)
    assert row["menu"]["walk_complete"] is True and len(row["menu"]["parts"]) == 3


def test_a_changed_menu_discards_the_old_read(box):
    read(box)
    (old,) = rows(box)
    box["pane"].show("b")                                     # the agent now shows a different question
    st, body = read(box)
    assert st == 200 and body.get("cached") is not True       # the old walk is not served for the new menu
    assert [p["question"] for p in body["parts"]] == ["Which colour? (b)", "Which sizes? (b)", "Which finish? (b)"]
    a, b = rows(box)
    assert a["id"] == old["id"] and questions(a) == questions(old)          # the old card is not overwritten
    assert questions(b) == ["Which colour? (b)", "Which sizes? (b)", "Which finish? (b)"] and a["op_key"] != b["op_key"]


def test_a_walk_of_a_menu_that_changed_mid_read_writes_nothing(box, monkeypatch):
    """The detector saw menu a; by the time the walk ran the screen showed menu b: nothing is stored."""
    seen = box["pane"].menu()

    class _Stale:
        def get_agent_status(self, session):
            return {"state": "waiting_permission", "pending_menu": seen}
    monkeypatch.setattr(G, "_agent_status", lambda: _Stale())
    box["pane"].show("b")
    st, body = read(box)
    assert st == 200 and body["walk_complete"] is True
    assert rows(box) == []


def test_with_store_but_mark_off_no_multi_part_card_is_made_as_before(box):
    box["cfg"].write_text(json.dumps({"enabled": True, "skip_sessions": [], "skip_prefixes": [], "store_but_mark": False}))
    st, body = read(box)
    assert st == 200 and body["walk_complete"] is True
    assert rows(box) == []


def test_concurrent_creates_of_one_menu_make_one_card(tmp_path):
    """The gateway's read and the cron's tick are different processes; the dedup check and the insert are one write
    transaction, so racing writers cannot both insert."""
    db = str(tmp_path / "t.db")
    ApprovalStore(db_path=db).migrate()
    start = threading.Barrier(8)
    ids = []

    def make():
        s = ApprovalStore(db_path=db)
        start.wait()
        ids.append(s.create(from_agent=SESSION, question="Which colour?", worker_kind="pane", kind="menu",
                            menu={"question": "Which colour?", "options": []}, op_key=f"menu:{SESSION}:race"))
    ts = [threading.Thread(target=make) for _ in range(8)]
    for t in ts: t.start()
    for t in ts: t.join()
    import sqlite3
    n = sqlite3.connect(db).execute("SELECT count(*) FROM approval_requests WHERE op_key=?", [f"menu:{SESSION}:race"]).fetchone()[0]
    assert n == 1 and len(set(ids)) == 1


def test_bridge_on_read_respects_the_pickup_gates(box):
    pane, store = box["pane"], box["store"]
    walked = {"walk_complete": True, "part_count": 3,
              "parts": [{"index": i, "question": p["question"], "options": p["options"]} for i, p in enumerate(pane.parts)]}
    on = {"enabled": True, "skip_sessions": [], "skip_prefixes": [], "store_but_mark": True}
    assert MRB.bridge_on_read(store, SESSION, pane.menu(), walked, "idle", cfg={**on, "enabled": False})[1] == "pickup_disabled"
    assert MRB.bridge_on_read(store, SESSION, pane.menu(), walked, "idle", cfg={**on, "skip_sessions": [SESSION]})[1] == "skipped"
    assert MRB.bridge_on_read(store, "not-a-seat", pane.menu(), walked, "idle", cfg=on)[1] == "not_a_seat"
    assert MRB.bridge_on_read(store, SESSION, {**pane.menu(), "multipart": False}, walked, "idle", cfg=on)[1] == "not_multipart"
    assert MRB.bridge_on_read(store, SESSION, pane.menu(), {**walked, "walk_complete": False}, "idle", cfg=on)[1] == "menu_changed_or_incomplete"
    open(M.SURFACE_PICKUP_KILLSWITCH, "w").close()
    assert MRB.bridge_on_read(store, SESSION, pane.menu(), walked, "idle", cfg=on)[1] == "killswitch"
    assert rows(box) == []


def test_the_log_says_bridged_when_the_read_made_the_card(box, capsys):
    read(box)
    (row,) = rows(box)
    assert f"menu card on read ({SESSION}): {row['id']} bridged" in capsys.readouterr().err


def test_the_log_says_completed_when_the_read_filled_in_an_existing_card(box, capsys):
    rid = tick(box)
    capsys.readouterr()
    read(box)
    err = capsys.readouterr().err
    assert f"menu card on read ({SESSION}): {rid} completed" in err and "bridged" not in err
    (row,) = rows(box)
    assert row["id"] == rid and row["menu"]["walk_complete"] is True
