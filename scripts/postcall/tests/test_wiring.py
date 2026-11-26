"""Wiring (design §2a, §3 rule 3, §5, §6): the pipeline reads the calendar and resolves the call's
participants before dedupe and the card, "by the next call" deadlines come from the calendar, the
live adapter sends due notes and (gated) task patches, and the sweeper plans the results notice.

All data is synthetic: made-up names, example.com addresses, an inline ICS file per test."""
import dataclasses
import datetime as dt
import json
import os
import sqlite3
import sys

import pytest

from postcall import effects, extract, ledger, pipeline, sources, sweep
from postcall.effects import Effect

from ._ics import Z

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
APPROVAL_CLI = os.path.join(REPO, "scripts", "approval.py")
MSG_CLI = os.path.join(REPO, "msg_store.py")


def _vevent(uid, start, end, summary, attendees):
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTART:{start}", f"DTEND:{end}", f"SUMMARY:{summary}"]
    lines += [f"ATTENDEE;CN={cn};PARTSTAT=ACCEPTED:mailto:{email}" for cn, email in attendees]
    return "\r\n".join(lines + ["END:VEVENT"]) + "\r\n"


SHAW_ATT, DANA_ATT = ("Shaw Cole", "shaw@example.com"), ("Dana Reyes", "dana@example.com")
# The fixture call is 2026-11-24 19:00Z for 600 s; this event overlaps it (and starts before it).
CALL_EVENT = _vevent("sync-1@example.com", Z(2026, 11, 24, 18, 55), Z(2026, 11, 24, 19, 30), "Vendor sync",
                     [SHAW_ATT, DANA_ATT])
NEXT_EVENT = _vevent("next-dana-0001@example.com", Z(2026, 12, 1, 19, 0), Z(2026, 12, 1, 20, 0), "Pilot check-in",
                     [SHAW_ATT, DANA_ATT])


@pytest.fixture
def calendar(tmp_path, monkeypatch):
    """Write the given events as the calendar feed the pipeline reads; Shaw's address is known."""
    path = tmp_path / "shaw.ics"
    monkeypatch.setenv("POSTCALL_CALENDAR_FILE", str(path))
    monkeypatch.setenv("POSTCALL_SHAW_EMAILS", "shaw@example.com")

    def write(*vevents):
        path.write_text("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(vevents) + "END:VCALENDAR\r\n")
        return path
    return write


@pytest.fixture
def no_calendar(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTCALL_CALENDAR_FILE", str(tmp_path / "missing" / "shaw.ics"))
    monkeypatch.setenv("POSTCALL_SHAW_EMAILS", "shaw@example.com")


@pytest.fixture
def call(fixture_dir):
    return sources.load_pocket(os.path.join(fixture_dir, "pocket", "two-party-call"))


def _raw(fixture_dir, *, dana_timeframe=None):
    with open(os.path.join(fixture_dir, "llm", "two-party-ok.json")) as f:
        raw = json.load(f)
    raw["speaker_names"] = [{"label": "SPEAKER_01", "name": "Dana Reyes",
                             "evidence": "Hi Dana, thanks for making time."}]
    if dana_timeframe is not None:
        raw["items"][1]["timeframe"] = dana_timeframe
    return raw


def _process(tmp_path, call, raw):
    led, world = ledger.Ledger(str(tmp_path / "ledger.db")), effects.Recorder()
    result = pipeline.process(call, ledger=led, extractor=lambda c: extract.Extraction(ok=True, raw=raw),
                              effects=world)
    (card,) = [e for e in world.sent if e.kind == "card"]
    return result, card


def _with_line(card):
    return card.payload["summary"].splitlines()[1].split(" · With: ")[1]


def _frozen_item(card, owner):
    return next(f["item"] for f in card.payload["frozen"] if f["item"]["owner"] == owner)


# --- 1. calendar -> participants -> trust, before the card ---------------------------------------

def test_calendar_match_and_evidence_verify_the_participant_item(tmp_path, fixture_dir, call, calendar):
    calendar(CALL_EVENT)
    result, card = _process(tmp_path, call, _raw(fixture_dir))
    assert result.status == "carded"
    prompts = [q["prompt"] for q in card.payload["questions"][1:]]
    theirs = next(p for p in prompts if "owes you" in p)
    assert theirs.startswith("Dana Reyes owes you: Dana shares the API credentials")   # no ⚠
    assert _with_line(card) == "Dana Reyes ✓"
    flagged = {f["item"]["owner"]: f["flagged"] for f in card.payload["frozen"]}
    assert flagged["participant"] is False


def test_without_a_calendar_nothing_changes(tmp_path, fixture_dir, call, no_calendar):
    result, card = _process(tmp_path, call, _raw(fixture_dir))
    assert result.status == "carded"
    assert _with_line(card) == "Dana Reyes (unverified)"
    prompts = [q["prompt"] for q in card.payload["questions"][1:]]
    assert all(p.startswith("⚠ ") for p in prompts)


# --- 1b. "by the next call" -------------------------------------------------------------------------

def test_by_the_next_call_is_due_an_hour_before_the_next_event_with_the_counterpart(
        tmp_path, fixture_dir, call, calendar):
    calendar(CALL_EVENT, NEXT_EVENT)
    _, card = _process(tmp_path, call, _raw(fixture_dir, dana_timeframe="by the next call"))
    item = _frozen_item(card, "participant")
    # 2026-12-01 19:00Z is 14:00 local (UTC-5); one hour before is 13:00.
    assert item["due_ts"] == "2026-12-01T13:00:00-05:00"
    assert item["due_source"] == "next call with Dana Reyes (Pilot check-in, 2026-12-01, uid next-dan)"


def test_the_meeting_being_recorded_is_never_its_own_next_call(tmp_path, fixture_dir, call, calendar):
    # A second calendar entry for the same meeting starts during the recording (19:05Z, call ends 19:10Z).
    same_meeting = _vevent("sync-dup@example.com", Z(2026, 11, 24, 19, 5), Z(2026, 11, 24, 19, 30), "Vendor sync (copy)",
                           [SHAW_ATT, DANA_ATT])
    calendar(CALL_EVENT, same_meeting, NEXT_EVENT)
    _, card = _process(tmp_path, call, _raw(fixture_dir, dana_timeframe="by the next call"))
    assert _frozen_item(card, "participant")["due_ts"] == "2026-12-01T13:00:00-05:00"


def test_by_the_next_call_with_no_later_event_stays_unknown(tmp_path, fixture_dir, call, calendar):
    calendar(CALL_EVENT)
    _, card = _process(tmp_path, call, _raw(fixture_dir, dana_timeframe="by the next call"))
    item = _frozen_item(card, "participant")
    assert item["due_ts"] is None
    assert item["due_source"] == "unknown (by the next call)"


# --- 2. live duenote + patch ------------------------------------------------------------------------

def _live(request, monkeypatch, *clis):
    request.node.postcall_allow_argv = [[sys.executable, c] for c in clis]
    monkeypatch.setenv("POSTCALL_APPROVAL_CLI", APPROVAL_CLI)
    monkeypatch.setenv("POSTCALL_MSG_CLI", MSG_CLI)
    return effects.adapter_for("live")


def _scratch_bus(path):
    """The base tables the message bus expects (as test_live_sends.py builds them), then migrate()."""
    with sqlite3.connect(path) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, conversation_id TEXT, task_id TEXT, parent_id TEXT,
                type TEXT NOT NULL, from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
                subject TEXT, body TEXT, priority TEXT NOT NULL DEFAULT 'medium',
                source TEXT DEFAULT 'system', status TEXT NOT NULL DEFAULT 'pending',
                retry_count INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL DEFAULT 5,
                metadata TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
                attempted_at TEXT, delivered_at TEXT, acknowledged_at TEXT, archived_at TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, subject TEXT, participants TEXT, task_id TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')));""")
    sys.path.insert(0, REPO)
    from msg_store import MessageStore
    MessageStore(db_path=path).migrate()


ITEM = {"owner": "shaw", "text": "Send Dana the pricing deck", "counterpart": "Dana Reyes",
        "due_ts": "2026-11-27T17:00:00-05:00", "due_source": "by Friday",
        "quote": "I'll send you the pricing deck by Friday.", "citation": "pocket:r1@02:10",
        "call_title": "Synthetic Vendor Sync"}
CARD_ID = "card:pocket:r1:r0:p1"


def _assigned(led, seat="pm-example"):
    """A filed card whose one item Shaw assigned to `seat`; returns the commitment id."""
    frozen = [{"q_n": 2, "key": "src_1", "suggested_n": 1, "flagged": False, "item": ITEM,
               "options": [{"action": "assign", "seat": seat, "agent_work": "draft the pricing deck"}]}]
    led.plan_card(Effect(CARD_ID, "card", {"title": "Synthetic Vendor Sync"}), call_id="pocket:r1", frozen=frozen)
    led.apply_answers(CARD_ID, status="submitted", answers={1: {"option_n": 2, "final": True},
                                                            2: {"option_n": 1, "final": True}})
    return led.commitment_of("src_1")


def test_ledger_plans_duenote_with_the_dispatch_seat(tmp_path):
    led = ledger.Ledger(str(tmp_path / "ledger.db"))
    cmt = _assigned(led)
    led.record_due_change(cmt, "2026-11-30T17:00:00-05:00", "by Monday", "pocket:r2")
    (note,) = [e for e in led.effects() if e["kind"] == "duenote"]
    assert json.loads(note["payload_json"])["to"] == "pm-example"     # what Live.send relies on


def test_live_duenote_is_one_tagged_message_to_the_seat(request, monkeypatch):
    _scratch_bus(os.environ["MSG_DB_PATH"])
    live = _live(request, monkeypatch, MSG_CLI)
    eff = Effect("duenote:cmt_dddddddddddd:s2", "duenote", {
        "cmt": "cmt_dddddddddddd", "key": "src_1", "seq": 2, "due_ts": "2026-11-30T17:00:00-05:00",
        "due_source": "by Monday", "decision_src": f"{CARD_ID}#2", "to": "pm-example",
        "dispatch": "dispatch:cmt_dddddddddddd"})
    assert live.find(eff) is None
    mid = live.send(eff)
    assert live.find(eff) == mid
    with sqlite3.connect(os.environ["MSG_DB_PATH"]) as c:
        rows = c.execute("SELECT id, to_agent, subject, body FROM messages").fetchall()
    assert len(rows) == 1
    _, to, subject, body = rows[0]
    assert to == "pm-example"
    assert subject.startswith("[postcall duenote:cmt_dddddddddddd:s2] ")
    assert "dispatch:cmt_dddddddddddd" in body and "seq 2" in body
    assert "2026-11-30T17:00:00-05:00" in body


def test_live_patch_updates_the_task_evidence_only_when_the_flag_is_on(request, monkeypatch):
    live = _live(request, monkeypatch, APPROVAL_CLI)
    rid = live.send(Effect("task:cmt_eeeeeeeeeeee", "task", {
        "cmt": "cmt_eeeeeeeeeeee", "key": "src_1", "action": "take", "decision_src": f"{CARD_ID}#2", "item": ITEM}))
    patch = Effect("patch:cmt_eeeeeeeeeeee:s1", "patch", {
        "cmt": "cmt_eeeeeeeeeeee", "key": "src_1", "seq": 1, "due_ts": "2026-11-30T17:00:00-05:00",
        "due_source": "by Monday", "decision_src": f"{CARD_ID}#2"})
    monkeypatch.delenv("POSTCALL_RESCHEDULE_PATCH", raising=False)
    with pytest.raises(RuntimeError, match="POSTCALL_RESCHEDULE_PATCH"):
        live.send(patch)
    monkeypatch.setenv("POSTCALL_RESCHEDULE_PATCH", "on")
    assert live.find(patch) is None                    # evidence still says Friday
    assert live.send(patch) == rid
    assert live.find(patch) == rid
    with sqlite3.connect(os.environ["APPROVAL_DB_PATH"]) as c:
        evidence, summary = c.execute("SELECT evidence, summary FROM approval_requests WHERE id=?", (rid,)).fetchone()
    assert json.loads(evidence) == {"due_ts": "2026-11-30T17:00:00-05:00", "due_source": "by Monday"}
    assert "Due: by Monday" in summary and "Due: by Friday" not in summary


# --- 3. the sweeper plans the results notice ---------------------------------------------------------

NOW = dt.datetime(2026, 11, 26, 0, 0, tzinfo=dt.timezone.utc).timestamp()


@pytest.fixture
def sweep_env(tmp_path, monkeypatch):
    root = tmp_path / "pocket"
    root.mkdir()
    monkeypatch.setenv("POSTCALL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("POCKET_ROOT", str(root))
    monkeypatch.setenv("POSTCALL_MODE", "test")


def test_sweep_plans_and_sends_one_results_notice_once_results_are_recorded(sweep_env):
    led = ledger.Ledger(effects.ledger_path("test"))
    cmt = _assigned(led)
    world = effects.Recorder()
    led.run_pending(world)                              # card + dispatch delivered
    led.record_result(cmt, "deck drafted")

    def extractor(call):
        raise AssertionError("no calls in this test")

    out = sweep.run_once(now=NOW, extractor=extractor, adapter=world)
    assert out["status"] == "ok" and out["results_planned"] == 1
    assert [e.effect_id for e in world.sent if e.kind == "results"] == [f"results:{CARD_ID}"]
    again = sweep.run_once(now=NOW + 60, extractor=extractor, adapter=world)
    assert again["results_planned"] == 0
    assert len([e for e in world.sent if e.kind == "results"]) == 1
