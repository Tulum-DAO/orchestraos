"""The quote gate on the fact bus: an attributed fact without a quote that checks out is LABELLED 'unverified'.

Label only: nothing is refused, dropped or deleted, and the gate is off unless QUOTE_GATE_LABEL=1.
Synthetic transcripts, dream rows and conversation rows only.
"""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bus  # noqa: E402
import quote_gate  # noqa: E402
from facts_schema import make_fact  # noqa: E402

TS = "2026-11-25T10:00:00.000Z"


@pytest.fixture
def world(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    (projects / "proj").mkdir(parents=True)
    (projects / "proj" / "sess.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"type": "assistant", "uuid": "a0", "message": {"content": [{"type": "text", "text": "ok"}]}},
        {"type": "user", "uuid": "u-typed", "message": {"content": "keep the dark theme on every page"}},
        {"type": "user", "uuid": "u-multi", "message": {"content": [
            {"type": "text", "text": "first block"}, {"type": "tool_result", "content": "x"},
            {"type": "text", "text": "ship the beta on friday"}]}},
        {"type": "user", "uuid": "u-hook", "message": {"content": "the build is red, fix it first"}},
    ]) + "\n")
    day = tmp_path / "2026-11-25.jsonl"
    rows = [
        {"ts": TS, "surface": "cli", "agent": "seat", "actor": "shaw", "kind": "prompt",
         "text": "keep the dark theme on every page",
         "citation": {"transcript": "proj/sess.jsonl", "uuid": "u-typed", "part": 0}, "source_key": "cli:u-typed:0"},
        {"ts": TS, "surface": "cli", "agent": "seat", "actor": "shaw", "kind": "prompt",
         "text": "ship the beta on friday",
         "citation": {"transcript": "proj/sess.jsonl", "uuid": "u-multi", "part": 1}, "source_key": "cli:u-multi:1"},
        {"ts": TS, "surface": "cli", "agent": "seat", "actor": "hook", "kind": "prompt",
         "text": "the build is red, fix it first",
         "citation": {"transcript": "proj/sess.jsonl", "uuid": "u-hook", "part": 0}, "source_key": "cli:u-hook:0"},
        {"ts": TS, "surface": "cli", "agent": "seat", "actor": "shaw", "kind": "prompt",
         "text": "an older row from before citations"},
        {"ts": TS, "surface": "cli", "agent": "seat", "actor": "shaw", "kind": "prompt",
         "text": "this turn says something its transcript does not",
         "citation": {"transcript": "proj/sess.jsonl", "uuid": "u-typed", "part": 0}, "source_key": "cli:u-typed:0"},
        {"ts": TS, "surface": "cli", "agent": "seat", "actor": "shaw", "kind": "prompt",
         "text": "the transcript of this turn is gone",
         "citation": {"transcript": "proj/missing.jsonl", "uuid": "u-x", "part": 0}, "source_key": "cli:u-x:0"},
    ]
    day.write_text("".join(json.dumps(r) + "\n" for r in rows))
    conv = tmp_path / "conversation.jsonl"
    conv.write_text("".join(json.dumps(r) + "\n" for r in [
        {"ts": "2026-11-25T11:00:00Z", "channel": "voice", "role": "shaw", "content": "turn the volume down please"},
        {"ts": "2026-11-25T11:01:00Z", "channel": "voice", "role": "system",
         "content": "the caller said the volume is fine"},
    ]))
    monkeypatch.setenv("QUOTE_GATE_PROJECTS_DIR", str(projects))
    monkeypatch.delenv("QUOTE_GATE_LABEL", raising=False)

    def dream(i, actor="shaw"):
        r = rows[i]
        return make_fact(ts=r["ts"], kind="fact", text=r["text"], actor=actor,
                         provenance={"source_kind": "dream-facts", "source_id": f"{day.name}:{i + 1}",
                                     "path": str(day), "surface": "cli", "agent": "seat",
                                     "source_row_kind": "prompt"})

    def voice(ts, text, actor):
        return make_fact(ts=ts, kind="fact", text=text, actor=actor,
                         provenance={"source_kind": "voice", "source_id": f"voice:{ts}", "path": str(conv),
                                     "channel": "voice", "direction": None})

    facts = {
        "typed": dream(0),
        "multi_block": dream(1),
        "hook_row": dream(2),               # adapter took the row's actor, but say it was mislabelled upstream
        "no_citation": dream(3),
        "quote_mismatch": dream(4),
        "transcript_gone": dream(5),
        "voice_shaw": voice("2026-11-25T11:00:00Z", "turn the volume down please", "shaw"),
        "voice_system_claim": voice("2026-11-25T11:01:00Z", "the caller said the volume is fine", "system"),
        "pocket_claim": make_fact(ts=TS, kind="fact", text="Shaw prefers short replies on calls.", actor="pocket",
                                  provenance={"source_kind": "pocket", "source_id": "rec1", "path": "x"}),
        "plain": make_fact(ts=TS, kind="fact", text="the proxy restarted at noon", actor="system",
                           provenance={"source_kind": "dag-events", "source_id": "d1", "path": "x"}),
    }
    facts["hook_row"]["actor"] = "shaw"               # mislabelled upstream; the row and transcript both resolve
    facts["voice_system_claim"]["actor"] = "shaw"     # same text as its row; only the row's speaker gives it away
    return facts


EXPECT = {"typed": "active", "multi_block": "active", "hook_row": "unverified", "no_citation": "unverified",
          "quote_mismatch": "unverified", "transcript_gone": "unverified", "voice_shaw": "active",
          "voice_system_claim": "unverified", "pocket_claim": "unverified", "plain": "active"}


def _ingest(tmp_path, facts):
    db = str(tmp_path / "facts.db")
    r = bus.ingest(db, list(facts.values()), source_kind="test", last_source_event_ts=TS)
    con = sqlite3.connect(db)
    rows = dict(con.execute("SELECT fact_id, state FROM facts").fetchall())
    prov = dict(con.execute("SELECT fact_id, provenance FROM facts").fetchall())
    con.close()
    return r, {k: rows.get(f["fact_id"]) for k, f in facts.items()}, \
        {k: json.loads(prov[f["fact_id"]]) for k, f in facts.items() if f["fact_id"] in prov}


def test_gate_is_off_by_default(world, tmp_path):
    r, states, _ = _ingest(tmp_path, world)
    assert r["inserted"] == len(world)
    assert set(states.values()) == {"active"}


def test_gate_labels_and_never_blocks(world, tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTE_GATE_LABEL", "1")
    r, states, prov = _ingest(tmp_path, world)
    assert r["inserted"] == len(world)                       # every fact written, none refused
    assert states == EXPECT
    assert prov["typed"]["quote_gate"]["status"] == "verified"
    assert prov["no_citation"]["quote_gate"] == {"status": "unverified", "reason": "dream row carries no citation"}
    assert "quote_gate" not in prov["plain"]                 # not attributed: the gate does not touch it


def test_gate_leaves_a_non_active_fact_alone(world, tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTE_GATE_LABEL", "1")
    world["pocket_claim"]["state"] = "resolved"
    _, states, _ = _ingest(tmp_path, world)
    assert states["pocket_claim"] == "resolved"


def test_gate_does_not_relabel_a_row_already_stored(world, tmp_path, monkeypatch):
    _ingest(tmp_path, world)                                  # written with the gate off
    monkeypatch.setenv("QUOTE_GATE_LABEL", "1")
    r, states, _ = _ingest(tmp_path, world)                   # same facts again, gate on
    assert r["inserted"] == 0
    assert set(states.values()) == {"active"}                 # new rows only, never a rewrite


def test_attribution_rule():
    assert quote_gate.attributed({"actor": "shaw", "text": "x"})
    assert quote_gate.attributed({"actor": "pocket", "text": "Shaw decided to ship on friday"})
    assert not quote_gate.attributed({"actor": "pocket", "text": "the team shipped on friday"})


def test_a_labelled_fact_is_served_flagged_not_hidden(world, tmp_path, monkeypatch):
    """The gate and recall together: a fact the gate could not verify is still recalled, flagged as having no
    source quote, and ranked after a verified one; nothing about it is dropped."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from services.arturo import facts_recall as fr  # noqa: PLC0415
    monkeypatch.setenv("QUOTE_GATE_LABEL", "1")
    _ingest(tmp_path, world)
    got = fr.query_facts(str(tmp_path / "facts.db"), ["theme", "replies"], k=10)
    by_text = {f["text"]: f for f in got}
    assert by_text["Shaw prefers short replies on calls."]["unsourced"] is True
    assert by_text["keep the dark theme on every page"]["unsourced"] is False
    assert [f["unsourced"] for f in got] == sorted(f["unsourced"] for f in got)
    assert "no source quote found" in fr._render(got)


def _append(path, rows):
    """Append rows to a jsonl file; returns the 1-based line number of the first one."""
    first = len(path.read_text().splitlines()) + 1
    with path.open("a") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in rows))
    return first


def _dream_at(day, line, text, actor="shaw", path=None):
    return make_fact(ts=TS, kind="fact", text=text, actor=actor,
                     provenance={"source_kind": "dream-facts", "source_id": f"{day.name}:{line}",
                                 "path": str(day) if path is None else path, "surface": "cli", "agent": "seat",
                                 "source_row_kind": "prompt"})


def _cli_row(text, cite, actor="shaw"):
    return {"ts": TS, "surface": "cli", "agent": "seat", "actor": actor, "kind": "prompt", "text": text,
            "citation": cite}


def test_malformed_source_data_is_labelled_never_raised(world, tmp_path, monkeypatch):
    """Each malformed input gets its own reason; the batch is written and every other fact is unaffected."""
    monkeypatch.setenv("QUOTE_GATE_LABEL", "1")
    _append(tmp_path / "projects" / "proj" / "sess.jsonl", [
        {"type": "user", "uuid": "u-strmsg", "message": "a string message"},
        {"type": "user", "uuid": "u-intcontent", "message": {"content": 5}},
    ])
    day = tmp_path / "2026-11-25.jsonl"
    texts = ["a string message", "a record whose content is a number", "a row whose actor is a number",
             "a row whose citation path has a nul byte"]
    first = _append(day, [
        _cli_row(texts[0], {"transcript": "proj/sess.jsonl", "uuid": "u-strmsg", "part": 0}),
        _cli_row(texts[1], {"transcript": "proj/sess.jsonl", "uuid": "u-intcontent", "part": 0}),
        _cli_row(texts[2], {"transcript": "proj/sess.jsonl", "uuid": "u-typed", "part": 0}, actor=7),
        _cli_row(texts[3], {"transcript": "proj/sess\x00.jsonl", "uuid": "u-typed", "part": 0}),
    ])
    bad = {name: _dream_at(day, first + i, texts[i])
           for i, name in enumerate(["str_message", "int_content", "int_row_actor", "nul_cite_path"])}
    bad["nul_day_path"] = _dream_at(day, 1, "keep the dark theme on every page", path=str(day) + "\x00")
    bad["list_day_path"] = _dream_at(day, 1, "keep the dark theme on every page", path=[str(day)])
    bad["int_actor"] = make_fact(ts=TS, kind="fact", text="the disk filled at noon", actor=7,
                                 provenance={"source_kind": "dag-events", "source_id": "d2", "path": "x"})
    bad["int_actor_claim"] = make_fact(ts=TS, kind="fact", text="Shaw prefers email over calls.", actor=7,
                                       provenance={"source_kind": "pocket", "source_id": "rec2", "path": "x"})
    r, states, prov = _ingest(tmp_path, {**world, **bad})
    assert r["inserted"] == len(world) + len(bad)
    assert {k: states[k] for k in world} == EXPECT
    reasons = {k: (prov[k].get("quote_gate") or {}).get("reason") for k in bad}
    assert reasons == {"str_message": "cited transcript turn not found",
                       "int_content": "cited transcript turn not found",
                       "int_row_actor": "dream row author is 7",
                       "nul_cite_path": "cited transcript turn not found",
                       "nul_day_path": "dream day file not readable",
                       "list_day_path": "dream day file not readable",
                       "int_actor": None,
                       "int_actor_claim": "no quote to check for this source"}
    assert states["int_actor"] == "active"
    assert {states[k] for k in bad if k != "int_actor"} == {"unverified"}


def test_a_gate_error_labels_that_fact_and_spares_the_rest(world, tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTE_GATE_LABEL", "1")

    def boom(fact, src):
        raise RuntimeError("synthetic failure")

    monkeypatch.setitem(quote_gate._CHECKS, "voice", boom)
    r, states, prov = _ingest(tmp_path, world)
    assert r["inserted"] == len(world)
    assert states == {**EXPECT, "voice_shaw": "unverified"}
    for k in ("voice_shaw", "voice_system_claim"):
        assert prov[k]["quote_gate"] == {"status": "unverified", "reason": "gate error"}
    assert prov["typed"]["quote_gate"]["status"] == "verified"


ATTRIBUTED = [
    "Shaw doesn't want dark mode on the dashboard",
    "Shaw does not like long calls",
    "Shaw would like a weekly summary",
    "Shaw's preference is dark mode",
    "According to Shaw, the beta ships on friday",
    "Shaw really wants the beta out this week",
    "Shaw needs the report by noon",
    "a note of Shaw saying the beta can wait",
    "Shaw states the beta can wait",
    "Shaw never wants a call before nine",
    "Shaw has told the team to ship on friday",
    "Shaw is asking for a shorter summary",
    "Shaw didn't approve the second draft",
    "Shaw’s preference is dark mode",
    "SHAW SAID ship it",
    "Per Shaw, the launch moves to monday",
    "Shaw Cole wants the beta out",
]
NOT_ATTRIBUTED = [
    "Dana said the beta can wait",
    "Shawn said the beta can wait",
    "Bradshaw said the beta can wait",
    "send the report to Shaw",
    "remind Shaw about the call on friday",
    "the agent told Shaw the deploy is done",
    "Shaw was told the beta slipped",
    "Shaw's team said the beta can wait",
    "let Shaw decide on the venue",
]


@pytest.mark.parametrize("text", ATTRIBUTED)
def test_text_attributing_words_or_a_preference_to_the_operator_is_attributed(text):
    assert quote_gate.attributed({"actor": "pocket", "text": text})


@pytest.mark.parametrize("text", NOT_ATTRIBUTED)
def test_another_name_or_a_mention_as_object_is_not_attributed(text):
    assert not quote_gate.attributed({"actor": "pocket", "text": text})


def _voice_at(path, source_id, text, actor="shaw"):
    return make_fact(ts=TS, kind="fact", text=text, actor=actor,
                     provenance={"source_kind": "voice", "source_id": source_id, "path": str(path),
                                 "channel": "voice", "direction": None})


def test_a_voice_fact_must_be_the_cited_turn_word_for_word(world, tmp_path, monkeypatch):
    """The cited row is the operator's, but it says something else: the fact is a paraphrase, not a quote."""
    monkeypatch.setenv("QUOTE_GATE_LABEL", "1")
    world["paraphrase"] = _voice_at(tmp_path / "conversation.jsonl", "voice:2026-11-25T11:00:00Z",
                                    "Shaw wants it quieter")
    _, states, prov = _ingest(tmp_path, world)
    assert states["paraphrase"] == "unverified"
    assert prov["paraphrase"]["quote_gate"]["reason"] == "conversation row text differs from the fact"
    assert states["voice_shaw"] == "active"
