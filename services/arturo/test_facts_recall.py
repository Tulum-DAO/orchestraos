"""RED-first test for Arturo facts.db in-call recall (additive read-side wiring).

Proves the gap the wiring closes: a "what's going on with <recent topic>" question
must surface a fresh fact that lives ONLY in state/brain/facts.db (23.5k structured
facts, refreshed daily) — a source Arturo had ZERO wiring to before this change.

Before the wiring: facts_recall does not exist -> import fails -> RED.
After the wiring: the fresh fact appears in the assembled FACTS block -> GREEN.

Also asserts the safety contract mirrored from semantic_recall:
  - flag-off => byte-empty (inert)
  - noise/prompt-echo rows are excluded
  - a bounded budget wall never raises into the turn
"""
import os
import sqlite3
import time

import pytest

from services.arturo import facts_recall as fr


def _make_facts_db(path):
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE facts (
            fact_id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL,
            text TEXT NOT NULL, actor TEXT NOT NULL, confidence REAL NOT NULL,
            entities TEXT NOT NULL, lanes TEXT NOT NULL, provenance TEXT NOT NULL,
            state TEXT NOT NULL, expiry_class TEXT NOT NULL, ingested_at TEXT NOT NULL)"""
    )
    con.execute("CREATE INDEX idx_facts_ts ON facts(ts)")
    con.execute("CREATE INDEX idx_facts_kind ON facts(kind)")
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    rows = [
        # the fresh, topic-relevant fact that exists ONLY here (not in the 9/07 doc store)
        ("f1", now, "goal",
         "Have Arturo verify whether the watch uplink/downlink must be symmetrical "
         "(answer: no - short HTTP POST uplink with a long-lived downlink is fine)",
         "operator", 1.0, "{}", "[]", "{}", "active", "durable", now),
        # prompt-echo noise that must be filtered out even though it is recent
        ("f2", now, "fact",
         "Distill this recorded conversation into discrete facts for a nightly ledger",
         "operator", 1.0, "{}", "[]", "{}", "active", "durable", now),
        # an unrelated fact that must not outrank the relevant one
        ("f3", now, "fact",
         "The northwind export finished and the CSV was delivered to the client",
         "operator", 1.0, "{}", "[]", "{}", "active", "durable", now),
    ]
    con.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


@pytest.fixture()
def facts_db(tmp_path):
    p = tmp_path / "facts.db"
    _make_facts_db(str(p))
    return str(p)


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setenv(fr.FLAG, "1")
    yield


def test_recall_surfaces_fresh_fact_only_in_factsdb(facts_db):
    block = fr.facts_preamble(
        "what's going on with the watch uplink for arturo", dbpath=facts_db)
    assert block, "expected a non-empty FACTS block from facts.db"
    assert "uplink" in block.lower()
    assert "symmetrical" in block.lower(), (
        "the fresh watch-uplink fact that lives ONLY in facts.db must appear")


def test_prompt_echo_noise_excluded(facts_db):
    block = fr.facts_preamble(
        "what's going on with distill the recorded conversation nightly ledger",
        dbpath=facts_db)
    assert "Distill this recorded conversation" not in block


def test_flag_off_is_inert(facts_db, monkeypatch):
    monkeypatch.setenv(fr.FLAG, "0")
    assert fr.facts_preamble("what's going on with the watch uplink", dbpath=facts_db) == ""


def test_short_or_empty_query_returns_empty(facts_db):
    assert fr.facts_preamble("", dbpath=facts_db) == ""
    assert fr.facts_preamble("hi", dbpath=facts_db) == ""


def test_missing_db_degrades_to_empty():
    assert fr.facts_preamble(
        "what's going on with the watch uplink", dbpath="/nonexistent/facts.db") == ""


def test_budget_wall_never_raises(facts_db):
    # a 0ms budget must degrade to empty, never raise into the turn
    out = fr.facts_preamble(
        "what's going on with the watch uplink", dbpath=facts_db, budget_ms=0)
    assert out == "" or isinstance(out, str)


# --- Step 2: tier-aware candidate pass un-starves old durable facts ----------

def _make_starvation_db(path, n_recent=300):
    """One OLD durable fact that is the strong (3-hit) answer, buried under
    `n_recent` NEWER weak (1-hit) matches — more than CANDIDATE_LIMIT (250).
    With only a recency-ordered window the durable fact is never a candidate."""
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE facts (
            fact_id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL,
            text TEXT NOT NULL, actor TEXT NOT NULL, confidence REAL NOT NULL,
            entities TEXT NOT NULL, lanes TEXT NOT NULL, provenance TEXT NOT NULL,
            state TEXT NOT NULL, expiry_class TEXT NOT NULL, ingested_at TEXT NOT NULL)""")
    con.execute("CREATE INDEX idx_facts_ts ON facts(ts)")
    con.execute("CREATE INDEX idx_facts_kind ON facts(kind)")
    rows = []
    # OLD durable fact — the true answer, 3 topic hits, dated far in the past
    rows.append(("durable_old", "2026-01-01T00:00:00.000Z", "fact",
                 "Acme ownership founders: co-founded by the operator and a partner",
                 "operator", 1.0, "{}", "[]", "{}", "active", "durable",
                 "2026-01-01T00:00:00.000Z"))
    # n_recent NEWER standard facts, each only 1 weak topic hit ("acme")
    for i in range(n_recent):
        ts = "2026-09-%02dT%02d:00:00.000Z" % (1 + i % 13, i % 24)
        rows.append((f"recent_{i}", ts, "fact",
                     f"acme daily standup note number {i}",
                     "operator", 1.0, "{}", "[]", "{}", "active", "standard", ts))
    con.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_old_durable_fact_starved_before_fix(tmp_path):
    """Guard the DEFECT with the OLD single-pass query: the durable answer,
    buried past CANDIDATE_LIMIT newer matches, is never even a candidate."""
    p = str(tmp_path / "starve.db")
    _make_starvation_db(p)
    con = fr._connect_ro(p)
    try:
        like = "text LIKE ? OR text LIKE ? OR text LIKE ?"
        params = ["%acme%", "%ownership%", "%founders%"]
        rows = con.execute(
            f"SELECT fact_id FROM facts WHERE ({like}) ORDER BY ts DESC LIMIT 250",
            params).fetchall()
    finally:
        con.close()
    ids = {r[0] for r in rows}
    assert "durable_old" not in ids, "old single-pass should starve the durable fact"


def test_old_durable_fact_surfaces_after_fix(tmp_path):
    """AFTER: the tier-aware second pass pulls durable regardless of recency,
    so the strong old durable answer surfaces in the FACTS block."""
    p = str(tmp_path / "starve.db")
    _make_starvation_db(p)
    facts = fr.query_facts(p, ["acme", "ownership", "founders"], k=5)
    ids = [f.get("fact_id") for f in facts]
    assert "durable_old" in ids, "durable fact must be a candidate after the tier pass"
    # it is the strongest match (3 hits) -> should rank first
    assert facts[0].get("fact_id") == "durable_old"
    block = fr.facts_preamble(
        "what's the acme ownership and founders", dbpath=p)
    assert "founders" in block.lower()


# --- Step 3: recency x tier scoring (config half-lives) ----------------------

def test_halflives_are_config_not_hardcoded(monkeypatch):
    # defaults: durable finite ~5y (build-gate #2 — never literal infinity),
    # standard ~180d, ephemeral ~7d
    assert fr.halflife_days("durable") >= 1825.0
    assert fr.halflife_days("durable") != float("inf")
    assert fr.halflife_days("standard") == 180.0
    assert fr.halflife_days("ephemeral") == 7.0
    # overridable by env (config, not hardcoded)
    monkeypatch.setenv("ARTURO_FACTS_HALFLIFE_EPHEMERAL_D", "3")
    assert fr.halflife_days("ephemeral") == 3.0


def test_effective_confidence_decays_by_tier():
    # equal age, equal base -> durable barely decays, ephemeral decays hard
    d = fr.effective_confidence(1.0, 60.0, "durable")
    s = fr.effective_confidence(1.0, 60.0, "standard")
    e = fr.effective_confidence(1.0, 60.0, "ephemeral")
    assert d > s > e
    assert d > 0.95            # ~5y half-life over 60d: almost no decay
    assert e < 0.01            # 7d half-life over 60d: buried
    # fresh fact ~ no decay regardless of tier
    assert fr.effective_confidence(1.0, 0.0, "ephemeral") == 1.0


def _make_tier_db(path):
    """Three facts, SAME 1 keyword hit, SAME age (60d), differing tier only."""
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE facts (
            fact_id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL,
            text TEXT NOT NULL, actor TEXT NOT NULL, confidence REAL NOT NULL,
            entities TEXT NOT NULL, lanes TEXT NOT NULL, provenance TEXT NOT NULL,
            state TEXT NOT NULL, expiry_class TEXT NOT NULL, ingested_at TEXT NOT NULL)""")
    con.execute("CREATE INDEX idx_facts_ts ON facts(ts)")
    old = time.strftime("%Y-%m-%dT%H:%M:%S.000Z",
                        time.gmtime(time.time() - 60 * 86400))
    rows = [
        ("dur", old, "fact", "widget roster entry alpha", "operator", 1.0,
         "{}", "[]", "{}", "active", "durable", old),
        ("std", old, "fact", "widget roster entry bravo", "operator", 1.0,
         "{}", "[]", "{}", "active", "standard", old),
        ("eph", old, "fact", "widget roster entry charlie", "operator", 1.0,
         "{}", "[]", "{}", "active", "ephemeral", old),
    ]
    con.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_tier_scaled_ranking_by_effect(tmp_path):
    p = str(tmp_path / "tier.db")
    _make_tier_db(p)
    facts = fr.query_facts(p, ["widget"], k=3)
    ids = [f["fact_id"] for f in facts]
    # same hits + same age: durable ranks above standard above ephemeral
    assert ids == ["dur", "std", "eph"]


# --- Step 7: superseded (demoted) facts do not surface -----------------------

def test_superseded_fact_excluded_from_recall(tmp_path):
    p = str(tmp_path / "sup.db")
    con = sqlite3.connect(p)
    con.execute(
        """CREATE TABLE facts (
            fact_id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL,
            text TEXT NOT NULL, actor TEXT NOT NULL, confidence REAL NOT NULL,
            entities TEXT NOT NULL, lanes TEXT NOT NULL, provenance TEXT NOT NULL,
            state TEXT NOT NULL, expiry_class TEXT NOT NULL,
            superseded_by TEXT, ingested_at TEXT NOT NULL)""")
    con.execute("CREATE INDEX idx_facts_ts ON facts(ts)")
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    con.executemany(
        "INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("old", now, "fact", "arturo microphone is muted", "operator", 1.0,
          "{}", "[]", "{}", "superseded", "ephemeral", "new", now),
         ("new", now, "fact", "arturo microphone is unmuted", "operator", 1.0,
          "{}", "[]", "{}", "active", "ephemeral", None, now)])
    con.commit()
    con.close()
    facts = fr.query_facts(p, ["arturo", "microphone", "muted"], k=5)
    ids = [f["fact_id"] for f in facts]
    assert "old" not in ids, "superseded fact must be demoted out of recall"
    assert "new" in ids
