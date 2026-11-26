"""What recall serves, by fact state.

- superseded and archived: never served, by any path.
- unverified (a fact with no source quote found): SERVED, but flagged "no source quote found", ranked below
  sourced facts, and never rendered as anyone's words.
- everything else (active, resolved, expired, no state): served as before.
Synthetic databases only.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "brain"))

from services.arturo import facts_recall as fr  # noqa: E402
from services.recall import core  # noqa: E402
from services.recall import worldview_recall as wv  # noqa: E402
import facts_schema  # noqa: E402
import lane_context  # noqa: E402

STATES = ("active", "resolved", "expired", "superseded", "archived", "unverified")
SERVED = {"active", "resolved", "expired", "unverified"}
FLAG = "no source quote found"


def _facts_db(path, now="2026-11-25T10:00:00Z"):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE facts (fact_id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
            actor TEXT NOT NULL, confidence REAL NOT NULL, entities TEXT NOT NULL, lanes TEXT NOT NULL,
            provenance TEXT NOT NULL, state TEXT NOT NULL, expiry_class TEXT NOT NULL, ingested_at TEXT NOT NULL,
            superseded_by TEXT, slot_key TEXT, valid_from TEXT, valid_to TEXT);
        CREATE TABLE ingest_meta (source_kind TEXT PRIMARY KEY, last_source_event_ts TEXT, last_ingest_at TEXT NOT NULL);
        CREATE TABLE statements (statement_id TEXT PRIMARY KEY, source_type TEXT, source_ref TEXT, author TEXT,
            ts TEXT, topic TEXT, content TEXT, metadata TEXT);
        CREATE TABLE beliefs (belief_id TEXT PRIMARY KEY, subject TEXT, claim TEXT, current_value TEXT,
            confidence REAL, tier TEXT, derived_from TEXT, last_updated TEXT, superseded_by TEXT,
            history TEXT DEFAULT '[]', facet TEXT);
    """)
    for i, st in enumerate(STATES):
        # the unverified row is the NEWEST, so ranking it last is the rule at work, not the data's order
        ts = "2026-11-25T12:00:00Z" if st == "unverified" else now
        con.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"f_{st}", ts, "fact", f"marigold rollout note {st}", "shaw", 1.0, "{}", '["ops"]',
                     json.dumps({"source_kind": "dream-facts"}), st, "standard", ts, None,
                     "shaw.location", ts, None))
        con.execute("INSERT INTO statements VALUES (?,?,?,?,?,?,?,?)",
                    (f"s_{st}", "dream-facts", "{}", "shaw", f"2026-11-2{i}T00:00:00Z", "t",
                     f"marigold {st} is up", json.dumps({"expiry_class": "ephemeral", "state": st})))
        con.execute("INSERT INTO beliefs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (f"b_{st}", f"marigold {st}", "c", f"marigold {st} is healthy", 0.99 if st == "unverified"
                     else 0.9, "standard", json.dumps([f"s_{st}"]), now, None, "[]", None))
    con.commit()
    con.close()
    return str(path)


def _state_of(text):
    return next(st for st in STATES if f" {st}" in f" {text} ")


def _check(entries):
    """States returned, each entry flagged iff unverified, and every unverified entry after every sourced one."""
    states = [_state_of(e["text"]) for e in entries]
    assert all(bool(e.get("unsourced")) == (st == "unverified") for e, st in zip(entries, states))
    if "unverified" in states:
        assert states.index("unverified") == len(states) - 1, states
    return set(states)


def test_the_rule_is_one_rule():
    assert set(fr.UNSERVED_STATES) == {"superseded", "archived"}
    assert set(facts_schema.UNSERVED_STATES) == set(fr.UNSERVED_STATES)
    assert {"unverified", "archived"} <= facts_schema.STATES
    assert {st for st in STATES if fr.served(st)} == SERVED
    assert fr.served(None) and fr.served("")
    assert fr.unsourced("unverified") and not fr.unsourced("active") and not fr.unsourced(None)


def test_facts_query(tmp_path):
    assert _check(fr.query_facts(_facts_db(tmp_path / "f.db"), ["marigold"], k=20)) == SERVED


def test_recent_facts(tmp_path):
    got = fr.recent_facts(24 * 365, ["marigold"], k=20, dbpath=_facts_db(tmp_path / "f.db"),
                          now=datetime(2026, 11, 25, 13, tzinfo=timezone.utc))
    assert _check(got) == SERVED


def test_json_store(tmp_path):
    p = tmp_path / "facts.json"
    p.write_text(json.dumps({"facts": [{"ts": "2026-11-25T12:00:00Z" if st == "unverified" else
                                        "2026-11-25T10:00:00Z", "kind": "fact", "state": st,
                                        "text": f"marigold rollout note {st}"} for st in STATES]}))
    assert _check(fr.query_facts_json(str(p), ["marigold"], k=20)) == SERVED


def test_worldview(tmp_path):
    got = wv.query_worldview(_facts_db(tmp_path / "f.db"), ["marigold"], k=20)
    assert _check([e for e in got if e["kind"] == "belief"]) == SERVED
    assert _check([e for e in got if e["kind"] == "fluid"]) == SERVED


def test_slot_view(tmp_path):
    db = _facts_db(tmp_path / "f.db")
    con = sqlite3.connect(db)
    con.execute("DELETE FROM facts WHERE state NOT IN ('unverified', 'superseded', 'archived')")
    con.commit()
    con.close()
    got = core.open_slot_facts(db, "shaw.location")
    assert [(_state_of(f["text"]), f.get("unsourced")) for f in got] == [("unverified", True)]


def test_lane_context(tmp_path):
    assert _check(lane_context.get_context("ops", db_path=_facts_db(tmp_path / "f.db"))["facts"]) == SERVED


def test_renderers_flag_an_unsourced_fact_and_never_frame_it_as_words():
    line = fr._render([{"kind": "fact", "ts": "2026-11-25", "text": "Shaw prefers short replies", "unsourced": True}])
    assert FLAG in line
    assert "said" not in line.lower().replace("shaw prefers", "")
    assert FLAG not in fr._render([{"kind": "fact", "ts": "2026-11-25", "text": "the proxy is up"}])
    wl = wv._render_worldview([{"kind": "fluid", "tier": "ephemeral", "text": "the proxy is up", "ts": "2026-11-25",
                                "cite": "per dream-facts 2026-11-25", "conf": 1.0, "unsourced": True}])
    assert FLAG in wl


@pytest.mark.parametrize("hidden", ["superseded", "archived"])
def test_end_to_end_through_the_recall_core(tmp_path, hidden):
    """An unverified fact and its backfilled statement come back through core.answer, flagged in both blocks and
    never as anyone's words; a superseded or archived one comes back through neither block."""
    db = _facts_db(tmp_path / "f.db")
    rc = core.Recall(facts_db=db, sm_db=str(tmp_path / "absent-sm.db"), embedder=False, sweep_enabled=False)
    out = rc.answer("marigold rollout note status", vector=False)
    facts, world = out["blocks"]["facts"], out["blocks"]["worldview"]
    assert {_state_of(f["text"]) for f in facts} >= {"unverified"}
    assert [f.get("unsourced") for f in facts if _state_of(f["text"]) == "unverified"] == [True]
    assert any(e.get("unsourced") and "unverified" in e["text"] for e in world)
    assert not any(hidden in f["text"] for f in facts + world)
    rendered = out["rendered"]
    assert hidden not in rendered
    for line in rendered.splitlines():
        if "unverified" in line:
            assert FLAG in line, line
    assert " said" not in rendered.lower()


# --- the proxy's own fact printers: the time-window answer, the knowledge-tool blend, recent_work -------------------

from services.arturo.test_text_turn_brain import P  # noqa: E402,F401 — the shared proxy fixture


@pytest.fixture()
def proxy_store(tmp_path, monkeypatch):
    """A sourced fact, a NEWER unverified one and a newest superseded one, all inside the last few hours."""
    from datetime import timedelta
    p = tmp_path / "facts.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE facts (fact_id TEXT, ts TEXT, kind TEXT, text TEXT, expiry_class TEXT, "
                "confidence REAL, state TEXT)")
    now = datetime.now(timezone.utc)
    for fid, ago, st in [("p1", timedelta(hours=3), "active"), ("p2", timedelta(hours=2), "unverified"),
                         ("p3", timedelta(hours=1), "superseded")]:
        con.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?)",
                    (fid, (now - ago).isoformat().replace("+00:00", "Z"), "fact", f"marigold rollout note {st}",
                     "standard", 1.0, st))
    con.commit()
    con.close()
    monkeypatch.setitem(sys.modules, "facts_recall", fr)    # the proxy also imports it by its bare name
    monkeypatch.setattr(fr, "default_db_path", lambda: str(p))
    monkeypatch.setattr(fr, "default_json_path", lambda: str(tmp_path / "none.json"))
    monkeypatch.delenv("ARTURO_FACTS_RECALL", raising=False)
    monkeypatch.setenv("ARTURO_KNOWLEDGE_FACTS", "1")
    return p


def _fact_lines(block):
    """The printed fact lines of one block, as (state, line)."""
    return [(_state_of(ln), ln) for ln in block.splitlines() if "marigold rollout note" in ln]


def _check_printed(block):
    lines = _fact_lines(block)
    assert [st for st, _ in lines] == ["active", "unverified"], block      # sourced first, superseded never
    (_, sourced), (_, unsrc) = lines
    assert FLAG not in sourced
    # the flag sits in the bracket, the way the FACTS block prints it; the text is never anyone's words
    assert unsrc.endswith(f" · {FLAG}] marigold rollout note unverified"), unsrc
    return lines


def test_the_time_window_answer_flags_an_unsourced_fact_and_prints_it_last(P, proxy_store):
    out = P._knowledge_lookup("what happened last night")
    block = out[out.index("[RECENT"):].split("\n\n")[0]
    _check_printed(block)
    assert "no source quote found last" in block.splitlines()[0]          # the header still tells the truth


def test_the_knowledge_blend_flags_an_unsourced_fact_and_prints_it_last(P, proxy_store):
    out = P._knowledge_lookup("marigold rollout")
    _check_printed(out[out.index("[BRAIN FACTS"):].split("\n\n")[0])


def test_recent_work_flags_an_unsourced_fact_and_prints_it_last(P, proxy_store, monkeypatch, tmp_path):
    monkeypatch.setattr(P, "VOICE_CALLS_DIR", tmp_path / "no-calls")
    monkeypatch.setattr(P, "_git_since", lambda repo, hours: [])
    out = P._recent_work(24)
    _check_printed(out)
    assert "no source quote found last" in out.splitlines()[0]


def test_the_location_pin_never_puts_an_unsourced_fact_above_a_sourced_one(tmp_path):
    """Every fixture fact is an open location-slot fact and the unverified one is the newest, so it is pinned."""
    db = _facts_db(tmp_path / "f.db")
    rc = core.Recall(facts_db=db, sm_db=str(tmp_path / "absent-sm.db"), embedder=False, sweep_enabled=False)
    q = "where is shaw with the marigold rollout"
    assert core._LOCATION_Q.search(q)
    assert any(p["unsourced"] for p in core.open_slot_facts(db, "shaw.location"))
    assert _check(rc._query_facts(fr.extract_keywords(q), 20, q)) == SERVED
    # cut at k, the unsourced entries go first
    short = rc._query_facts(fr.extract_keywords(q), 3, q)
    assert len(short) == 3 and not any(f["unsourced"] for f in short), short


# --- worldview: sourced entries are picked before every cut --------------------------------------------------------

def _wv_db(path, statements=(), beliefs=()):
    """The fixture schema with only the given statements (sid, source_type, ts, content, metadata dict) and beliefs
    (bid, value, confidence, derived_from as stored)."""
    db = _facts_db(path)
    con = sqlite3.connect(db)
    con.execute("DELETE FROM statements")
    con.execute("DELETE FROM beliefs")
    for sid, stype, ts, content, meta in statements:
        con.execute("INSERT INTO statements VALUES (?,?,?,?,?,?,?,?)",
                    (sid, stype, "{}", "shaw", ts, "t", content, json.dumps(meta)))
    for bid, value, conf, derived in beliefs:
        con.execute("INSERT INTO beliefs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (bid, value, "c", value, conf, "standard", derived, "2026-11-25T10:00:00Z", None, "[]", None))
    con.commit()
    con.close()
    return db


def _plain(st):
    return {"state": st}                                  # a statement the fluid pass does not read


def test_worldview_beliefs_are_picked_sourced_first_from_the_whole_window(tmp_path):
    """k=2 fetches four beliefs; the top two by confidence rest only on an unverified and a superseded statement."""
    db = _wv_db(tmp_path / "f.db",
                statements=[("s_u", "dream-facts", "2026-11-20T00:00:00Z", "x", _plain("unverified")),
                            ("s_x", "dream-facts", "2026-11-20T00:00:00Z", "x", _plain("superseded")),
                            ("s_1", "dream-facts", "2026-11-20T00:00:00Z", "x", _plain("active")),
                            ("s_2", "dream-facts", "2026-11-20T00:00:00Z", "x", _plain("active"))],
                beliefs=[("b_u", "zinnia unverified belief", 0.99, '["s_u"]'),
                         ("b_x", "zinnia superseded belief", 0.95, '["s_x"]'),
                         ("b_1", "zinnia first belief", 0.9, '["s_1"]'),
                         ("b_2", "zinnia second belief", 0.8, '["s_2"]')])
    got = wv.query_worldview(db, ["zinnia"], k=2)
    assert [e["text"] for e in got] == ["zinnia first belief", "zinnia second belief"]


def _eph(st):
    return {"expiry_class": "ephemeral", "state": st}


def test_worldview_fluid_newest_wins_prefers_a_sourced_statement(tmp_path):
    """A newer unverified 'X is down' does not hide an older sourced 'X is up'."""
    db = _wv_db(tmp_path / "f.db",
                statements=[("s_up", "dream-facts", "2026-11-20T00:00:00Z", "zinnia is up", _eph("active")),
                            ("s_dn", "dream-facts", "2026-11-24T00:00:00Z", "zinnia is down", _eph("unverified"))])
    got = wv.query_worldview(db, ["zinnia"], k=5)
    assert [(e["text"], e["unsourced"]) for e in got] == [("zinnia is up", False)]
    # with no sourced statement for the subject, the newest unsourced one is served, flagged
    db2 = _wv_db(tmp_path / "g.db",
                 statements=[("s_a", "dream-facts", "2026-11-20T00:00:00Z", "zinnia is up", _eph("unverified")),
                             ("s_b", "dream-facts", "2026-11-24T00:00:00Z", "zinnia is down", _eph("unverified"))])
    assert [(e["text"], e["unsourced"]) for e in wv.query_worldview(db2, ["zinnia"], k=5)] == [("zinnia is down", True)]


def test_worldview_fluid_slice_takes_sourced_statements_first(tmp_path):
    """k=1 and no beliefs: three fluid slots; the three newest subjects are unverified, three older ones sourced."""
    st = [(f"s_u{i}", "dream-facts", f"2026-11-2{i + 4}T00:00:00Z", f"zinnia u{i} is up", _eph("unverified"))
          for i in range(3)]
    st += [(f"s_s{i}", "dream-facts", f"2026-11-1{i}T00:00:00Z", f"zinnia s{i} is up", _eph("active"))
           for i in range(3)]
    got = wv.query_worldview(_wv_db(tmp_path / "f.db", statements=st), ["zinnia"], k=1)
    assert sorted(e["text"] for e in got) == ["zinnia s0 is up", "zinnia s1 is up", "zinnia s2 is up"]


def _wal(st):
    return {"anchor": {"seq": 7}, "expiry_class": "standard", "confidence": 0.5, "state": st}


def test_worldview_reserves_wal_slots_only_for_sourced_rows(tmp_path):
    """k=1: one belief and two fluid entries fill the three slots; a newer unverified wal row must not take a slot."""
    db = _wv_db(tmp_path / "f.db",
                statements=[("s_b", "dream-facts", "2026-11-20T00:00:00Z", "x", _plain("active")),
                            ("s_f1", "dream-facts", "2026-11-21T00:00:00Z", "zinnia f1 is up", _eph("active")),
                            ("s_f2", "dream-facts", "2026-11-20T00:00:00Z", "zinnia f2 is up", _eph("active")),
                            ("w_u", "wal", "2026-11-24T00:00:00Z", "zinnia wal unverified", _wal("unverified")),
                            ("w_s", "wal", "2026-11-23T00:00:00Z", "zinnia wal sourced", _wal("active"))],
                beliefs=[("b_1", "zinnia belief", 0.9, '["s_b"]')])
    got = wv.query_worldview(db, ["zinnia"], k=1)
    assert [e["text"] for e in got] == ["zinnia belief", "zinnia f1 is up", "zinnia wal sourced"]


@pytest.mark.parametrize("derived", ['"5"', "5", '{"a": 1}', "not json", '[{"a": 1}]'])
def test_worldview_a_derived_from_that_is_not_a_list_of_ids_cites_nothing(tmp_path, derived):
    db = _wv_db(tmp_path / "f.db", beliefs=[("b_1", "zinnia belief", 0.9, derived)])
    assert [(e["text"], e["cite"], e["unsourced"]) for e in wv.query_worldview(db, ["zinnia"], k=5)] == [
        ("zinnia belief", "", False)]


def test_worldview_belief_citation(tmp_path):
    """The last derived-from statement is cited when it exists and is servable, nothing when it is missing, and the
    newest servable one when it exists but is unserved."""
    st = [("s_old", "slack", "2026-11-18T00:00:00Z", "x", _plain("active")),
          ("s_new", "voice", "2026-11-21T00:00:00Z", "x", _plain("active")),
          ("s_mid", "email", "2026-11-19T00:00:00Z", "x", _plain("active")),
          ("s_gone", "dream-facts", "2026-11-22T00:00:00Z", "x", _plain("archived"))]
    db = _wv_db(tmp_path / "f.db", statements=st,
                beliefs=[("b_last", "zinnia a", 0.9, '["s_new", "s_old"]'),
                         ("b_missing", "zinnia b", 0.8, '["s_old", "s_absent"]'),
                         ("b_unserved", "zinnia c", 0.7, '["s_mid", "s_new", "s_old", "s_gone"]')])
    got = {e["text"]: e["cite"] for e in wv.query_worldview(db, ["zinnia"], k=5)}
    assert got == {"zinnia a": "per slack 2026-11-18", "zinnia b": "", "zinnia c": "per voice 2026-11-21"}


def test_worldview_an_unsourced_belief_does_not_take_a_fluid_slot(tmp_path):
    """k=2 with one sourced and one unsourced belief: the fluid slots are counted as if the unsourced belief were not
    there, so a sourced statement is never left out to make room for it."""
    st = [("s_b", "dream-facts", "2026-11-20T00:00:00Z", "x", _plain("active")),
          ("s_u", "dream-facts", "2026-11-20T00:00:00Z", "x", _plain("unverified"))]
    st += [(f"s_f{i}", "dream-facts", f"2026-11-1{5 - i}T00:00:00Z", f"zinnia f{i} is up", _eph("active"))
           for i in range(4)]
    db = _wv_db(tmp_path / "f.db", statements=st,
                beliefs=[("b_s", "zinnia sourced belief", 0.9, '["s_b"]'),
                         ("b_u", "zinnia unsourced belief", 0.99, '["s_u"]')])
    got = wv.query_worldview(db, ["zinnia"], k=2)
    assert [e["text"] for e in got] == ["zinnia sourced belief", "zinnia f0 is up", "zinnia f1 is up",
                                        "zinnia f2 is up"]


# --- facts: the bounded candidate windows fill with sourced rows first -------------------------------------------

def _window_db(path, rows):
    """rows: (fact_id, ts, expiry_class, state)."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE facts (fact_id TEXT, ts TEXT, kind TEXT, text TEXT, expiry_class TEXT, "
                "confidence REAL, state TEXT)")
    for fid, ts, tier, st in rows:
        con.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?)",
                    (fid, ts, "fact", f"marigold rollout note {st} {fid}", tier, 1.0, st))
    con.commit()
    con.close()
    return str(path)


def test_query_facts_windows_fill_with_sourced_rows_first(tmp_path, monkeypatch):
    """Both windows hold three rows. Three newer unverified durable rows would fill each; the sourced rows are older."""
    rows = [(f"std{i}", f"2026-11-2{i}T00:00:00Z", "standard", "active") for i in range(3)]
    rows += [("dur0", "2026-11-10T00:00:00Z", "durable", "active")]
    rows += [(f"unv{i}", f"2026-11-2{i + 3}T00:00:00Z", "durable", "unverified") for i in range(3)]
    db = _window_db(tmp_path / "f.db", rows)
    monkeypatch.setattr(fr, "DURABLE_LIMIT", 3)
    got = fr.query_facts(db, ["marigold"], k=20, candidate_limit=3)
    flags = [f["unsourced"] for f in got]
    assert sorted(f["fact_id"] for f in got if not f["unsourced"]) == ["dur0", "std0", "std1", "std2"]
    assert flags == sorted(flags) and True in flags                    # every sourced fact before every unsourced one


def test_recent_facts_window_fills_with_sourced_rows_first(tmp_path):
    """The window holds max(k*8, 100) rows; 100 newer unverified rows would fill it."""
    rows = [(f"unv{i:03d}", f"2026-11-25T{i // 60:02d}:{i % 60:02d}:00Z", "standard", "unverified")
            for i in range(100)]
    rows += [("src", "2026-11-24T12:00:00Z", "standard", "active")]
    got = fr.recent_facts(48, k=5, dbpath=_window_db(tmp_path / "f.db", rows),
                          now=datetime(2026, 11, 25, 6, tzinfo=timezone.utc))
    assert [f["fact_id"] for f in got][:1] == ["src"] and len(got) == 5
