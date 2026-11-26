"""Card shape (design §4): one questionnaire per call, Decide-by first, Q1 bulk control, (a) Yours
then (b) Theirs, evidence in the prompt, suggested option first, options frozen, paged past 19."""
import pytest

from postcall import card, deadlines
from postcall.deadlines import Due

TZ, LABEL = "America/Cancun", "Local"
CALL_START, CALL_END = "2026-11-24T19:00:00Z", "2026-11-24T19:10:00Z"


def _item(key, owner="shaw", due=Due("2026-11-27T17:00:00-05:00", "by Friday"), **kw):
    base = dict(key=key, owner=owner, text=f"Do {key}", counterpart="Dana Reyes", due=due,
                quote=f"quote for {key}", citation=f"pocket:r1@02:{key[-1]}0", trust="inferred")
    base.update(kw)
    return card.CardItem(**base)


def _build(items):
    return card.build(title="Synthetic Vendor Sync", items=items, tz=TZ, tz_label=LABEL,
                      call_start=CALL_START, call_end=CALL_END, participants=["Dana Reyes"])


def test_empty_items_refused():
    with pytest.raises(ValueError):
        _build([])


def test_summary_opens_with_decide_by_two_hours_before_earliest_due():
    page, = _build([_item("a1"), _item("a2", due=Due("2026-11-30T09:00:00-05:00", "before Monday"))])
    first = page.summary.splitlines()[0]
    assert first == ("Decide by: Fri 27 Nov 15:00 Local · If no answer: nothing is created or dispatched"
                     " · Cost of waiting: Do a1 slips")


def test_decide_by_without_any_due_is_call_end_plus_a_day():
    page, = _build([_item("a1", due=Due(None, "unknown (later)"))])
    assert page.summary.startswith("Decide by: Wed 25 Nov 14:10 Local · ")


def test_summary_second_line_names_the_call_and_participants():
    page, = _build([_item("a1")])
    assert page.summary.splitlines()[1] == "From: Synthetic Vendor Sync · Tue 24 Nov 14:00 Local · 10 min · With: Dana Reyes"


def test_q1_is_the_required_bulk_control():
    q1 = _build([_item("a1")])[0].questions[0]
    assert q1["kind"] == "menu" and q1["required"] is True
    labels = [o["label"] for o in q1["menu"]["options"]]
    assert labels == ["Accept every suggested choice (⚠ items need your own answer)",
                      "Decide per item (unanswered = nothing happens)", "Drop all"]
    assert all(o.get("detail") for o in q1["menu"]["options"])


def test_yours_come_before_theirs_each_earliest_due_first():
    later = Due("2026-12-01T17:00:00-05:00", "Dec 1")
    page, = _build([_item("t1", owner="participant", chase_date="2026-11-26T10:00:00-05:00"),
                    _item("s2", due=later), _item("s1")])
    assert [f["key"] for f in page.frozen] == ["s1", "s2", "t1"]


def test_item_prompt_carries_marker_due_quote_and_citation():
    page, = _build([_item("a1")])
    assert page.questions[1]["prompt"] == ('⚠ Do a1 · Dana Reyes · Due: Fri 27 Nov 17:00 Local\n'
                                           '"quote for a1" (pocket:r1@02:10)')
    assert page.questions[1]["required"] is False


def test_unknown_due_is_shown_as_its_source():
    page, = _build([_item("a1", due=Due(None, "unknown (later)"), trust="verified")])
    assert page.questions[1]["prompt"].startswith("Do a1 · Dana Reyes · Due: unknown (later)\n")


def test_shaw_item_options_suggest_taking_it_myself():
    page, = _build([_item("a1")])
    assert [o["label"] for o in page.questions[1]["menu"]["options"]] == ["Take it myself (suggested)", "Drop"]
    assert page.frozen[0]["suggested_n"] == 1


def test_agent_takeable_item_suggests_the_named_seat():
    page, = _build([_item("a1", seat="pm-example", agent_work="draft the pricing deck")])
    assert [o["label"] for o in page.questions[1]["menu"]["options"]] == [
        "Assign to pm-example: draft the pricing deck (suggested)", "Take it myself", "Drop"]
    assert page.frozen[0]["options"][0] == {"action": "assign", "seat": "pm-example",
                                           "agent_work": "draft the pricing deck"}


def test_their_item_is_a_chase_with_an_explicit_date():
    page, = _build([_item("t1", owner="participant", chase_date="2026-11-26T10:00:00-05:00")])
    q = page.questions[1]
    assert q["prompt"].startswith("⚠ Dana Reyes owes you: Do t1 · Due: Fri 27 Nov 17:00 Local\n")
    assert [o["label"] for o in q["menu"]["options"]] == [
        "Track it: chase Dana Reyes on Thu 26 Nov 10:00 Local (suggested)", "Drop"]
    assert page.frozen[0]["options"][0] == {"action": "track", "chase_date": "2026-11-26T10:00:00-05:00"}


def test_uncertain_match_adds_a_same_as_option():
    page, = _build([_item("a1", trust="verified", uncertain_match={"cmt": "cmt_old", "text": "Send the deck",
                                                                  "date": "2026-11-20"})])
    q = page.questions[1]
    assert q["prompt"].startswith("⚠ ")
    assert q["menu"]["options"][-1]["label"] == 'Same as "Send the deck" (2026-11-20): update it'
    assert page.frozen[0]["options"][-1] == {"action": "same_as", "cmt": "cmt_old"}


def test_more_than_19_items_page_with_q1_on_every_page():
    items = [_item(f"k{n:02d}") for n in range(25)]
    pages = _build(items)
    assert [len(p.questions) for p in pages] == [20, 7]
    assert pages[0].title == "Synthetic Vendor Sync: 25 items (page 1 of 2)"
    assert "Page 2 of 2" in pages[1].summary.splitlines()[1]
    assert all(p.questions[0]["required"] for p in pages)


@pytest.mark.parametrize("their_due,expected", [
    (None, "2026-11-26T10:00:00-05:00"),                         # call Tue + 2 business days
    ("2026-11-25T09:00:00-05:00", "2026-11-26T10:00:00-05:00"),  # their due Wed + 1 business day
    ("2026-11-27T17:00:00-05:00", "2026-11-30T10:00:00-05:00"),  # Fri -> Mon
])
def test_chase_date(their_due, expected):
    assert deadlines.chase_date(their_due, CALL_START, TZ) == expected


def test_frozen_entry_carries_the_item_for_later_sends():
    page, = _build([_item("a1")])
    assert page.frozen[0]["item"] == {"owner": "shaw", "text": "Do a1", "counterpart": "Dana Reyes",
                                      "due_ts": "2026-11-27T17:00:00-05:00", "due_source": "by Friday",
                                      "quote": "quote for a1", "citation": "pocket:r1@02:10",
                                      "call_title": "Synthetic Vendor Sync"}


def test_a_backlog_card_never_shows_a_decide_by_in_the_past():
    """gm msg_feff515d (3): a replayed or backlog call gets now + the normal window."""
    page, = card.build(title="Synthetic Vendor Sync", items=[_item("a1", due=Due(None, "none stated"))], tz=TZ,
                       tz_label=LABEL, call_start=CALL_START, call_end=CALL_END, participants=["Dana Reyes"],
                       now="2026-11-26T02:00:00Z")
    assert page.decide_by == "2026-11-27T02:00:00+00:00"
    assert page.summary.startswith("Decide by: Thu 26 Nov 21:00 Local · ")


def test_a_due_already_past_also_gives_now_plus_a_day():
    page, = card.build(title="Synthetic Vendor Sync", items=[_item("a1")], tz=TZ, tz_label=LABEL,
                       call_start=CALL_START, call_end=CALL_END, participants=["Dana Reyes"],
                       now="2026-11-28T00:00:00Z")
    assert page.decide_by == "2026-11-29T00:00:00+00:00"


def test_gm_routed_agent_work_is_offered_and_says_sending_stays_human():
    it = _item("a1", seat="gm", agent_work="draft the pricing deck email", route=True)
    page, = _build([it])
    labels = [o["label"] for o in page.questions[1]["menu"]["options"]]
    assert labels[0].startswith("Assign to gm to route: draft the pricing deck email (you send it)")
    assert page.frozen[0]["options"][0] == {"action": "assign", "seat": "gm", "agent_work":
                                            "draft the pricing deck email", "route": True}
    assert page.frozen[0]["options"][1] == {"action": "take"}


def test_the_pipeline_files_a_backlog_call_with_a_future_decide_by(tmp_path, monkeypatch):
    from postcall import effects, ledger, pipeline
    from . import _calls
    monkeypatch.setattr(pipeline, "_now", lambda: "2026-12-01T12:00:00+00:00")
    world = effects.Recorder()
    call, ex = _calls.call([_calls.item("I'll send you the pricing deck by Friday.")], when="2026-11-24T19:00:00Z")
    assert pipeline.process(call, ledger=ledger.Ledger(str(tmp_path / "l.db")), extractor=ex,
                            effects=world).status == "carded"
    assert world.sent[0].payload["decide_by"] == "2026-12-02T12:00:00+00:00"
