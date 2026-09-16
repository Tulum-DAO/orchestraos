"""Tests for the gm wake-on-delivery PURE CONTRACT (DEC-1786828407 CONSENSUS_REACHED,
gm+agy APPROVE; proposal .workspace/proposals/gm-wake-on-delivery.md §7 binding).

PB lane: the wake DECISION (should_wake) + digest (build_wake_digest) + turn-start
confirm (confirm_turn_started). Pure/no-IO over injected snapshots — ob's beat calls
these; transport (inject/Enter/confirm, cooldown persistence) is ob's lane.

gm BINDING constraints under test:
  - cooldown 120s (CRITICAL bypasses), batch cap 8 then "N more"
  - gm-idle = agent-status `idle` AND bus turn_ended/session_end (BOTH)
  - only HIGH/CRITICAL unacked msgs wake
  - needs_shaw = gm judgment; digest only FLAGS candidates
  - fail-open everywhere (bad input -> no wake, never raise)
  - THE LOAD-BEARING GUARD: human-composing = typed (non-ghost) composer text ->
    ABORT; ghost/placeholder (dim SGR-2) NEVER blocks; unreadable composer =
    ambiguous -> fail-CLOSED abort. Built ON composer_typed_text (agent-status.py).
"""
import time

from scripts.focus_registry.wake import (
    should_wake,
    build_wake_digest,
    confirm_turn_started,
    WAKE_COOLDOWN_S,
    BATCH_CAP,
    WAKE_PRIORITIES,
)


GHOST = "\u276f\xa0\x1b[2mTry \"fix lint errors\"\x1b[0m"          # dim SGR-2 ghost
GHOST_SUGGESTION = "\u276f\xa0\x1b[2mhave gm sequence the fleet\x1b[0m"
EMPTY_CURSOR = "\u276f\xa0\x1b[7m \x1b[27m"                        # reverse-video cursor only
TYPED = "\u276f\xa0fix the router bug"                             # default-style typed
TYPED_PLUS_GHOST = "\u276f\xa0deploy\x1b[2m and verify\x1b[0m"     # typed prefix + dim tail


def _msg(mid, priority="high", frm="app-dev", subject="done: voice card",
         acked=False, archived=False):
    return {"id": mid, "priority": priority, "from_agent": frm, "subject": subject,
            "acknowledged_at": "2026-08-15T00:00:00Z" if acked else None,
            "archived_at": "2026-08-15T00:00:00Z" if archived else None}


IDLE_GM = {"status": "idle", "last_event_type": "turn_ended"}
COLD = {"last_wake_ts": None, "woken_ids": []}


def _wake(inbox=None, gm_state=IDLE_GM, composer=EMPTY_CURSOR, cooldown=COLD,
          now=1000.0):
    return should_wake(inbox if inbox is not None else [_msg("m1")],
                       gm_state, composer, cooldown, now=now)


# --- binding constants --------------------------------------------------------

def test_binding_constants():
    assert WAKE_COOLDOWN_S == 120
    assert BATCH_CAP == 8
    assert WAKE_PRIORITIES == ("high", "critical")


# --- filter: only HIGH/CRITICAL unacked, not already woken-on -----------------

def test_wakes_on_unacked_high():
    r = _wake([_msg("m1", "high")])
    assert r["wake"] is True
    assert [b["id"] for b in r["batch"]] == ["m1"]


def test_wakes_on_critical():
    assert _wake([_msg("m1", "critical")])["wake"] is True


def test_normal_and_low_never_wake():
    r = _wake([_msg("m1", "normal"), _msg("m2", "low")])
    assert r["wake"] is False
    assert r["reason"] == "no_actionable"


def test_acked_and_archived_msgs_do_not_wake():
    r = _wake([_msg("m1", acked=True), _msg("m2", archived=True)])
    assert r["wake"] is False


def test_already_woken_ids_are_deduped():
    cool = {"last_wake_ts": None, "woken_ids": ["m1"]}
    r = _wake([_msg("m1")], cooldown=cool)
    assert r["wake"] is False
    assert r["reason"] == "no_actionable"


def test_empty_inbox_no_wake():
    r = _wake([])
    assert r["wake"] is False


# --- gm-idle = BOTH agent-status idle AND bus turn-boundary (binding Q5) ------

def test_not_idle_status_blocks():
    r = _wake(gm_state={"status": "working", "last_event_type": "turn_ended"})
    assert r["wake"] is False
    assert r["reason"] == "gm_not_idle"


def test_idle_status_without_bus_turn_ended_blocks():
    r = _wake(gm_state={"status": "idle", "last_event_type": "prompt_submit"})
    assert r["wake"] is False
    assert r["reason"] == "gm_not_idle"


def test_session_end_also_counts_as_turn_boundary():
    r = _wake(gm_state={"status": "idle", "last_event_type": "session_end"})
    assert r["wake"] is True


# --- THE LOAD-BEARING GUARD: ghost-vs-typed style-walk (exhaustive) -----------

def test_dim_ghost_placeholder_does_not_block():
    assert _wake(composer=GHOST)["wake"] is True


def test_dim_ghost_suggestion_does_not_block():
    # a CLI-suggested prompt renders dim SGR-2 (reference_ghost_vs_typed_composer);
    # it is NOT the operator typing — must not block the wake.
    assert _wake(composer=GHOST_SUGGESTION)["wake"] is True


def test_reverse_video_cursor_only_does_not_block():
    assert _wake(composer=EMPTY_CURSOR)["wake"] is True


def test_bare_empty_composer_does_not_block():
    assert _wake(composer="\u276f\xa0")["wake"] is True


def test_typed_text_ABORTS_wake():
    # the cardinal sin: never paste over the operator's typed input.
    r = _wake(composer=TYPED)
    assert r["wake"] is False
    assert r["reason"] == "human_composing"


def test_typed_prefix_with_ghost_tail_ABORTS():
    r = _wake(composer=TYPED_PLUS_GHOST)
    assert r["wake"] is False
    assert r["reason"] == "human_composing"


def test_single_typed_char_ABORTS():
    r = _wake(composer="\u276f\xa0f")
    assert r["wake"] is False
    assert r["reason"] == "human_composing"


def test_typed_that_mimics_placeholder_shape_still_ABORTS_when_not_dim():
    # default-style text that happens to look like a Try-placeholder is TYPED text
    # unless it exactly matches the placeholder pattern; a modified one must abort.
    r = _wake(composer='\u276f\xa0Try "fix lint errors" now')
    assert r["wake"] is False
    assert r["reason"] == "human_composing"


def test_multiline_composer_typed_on_wrapped_line_ABORTS():
    r = _wake(composer=[GHOST.replace("\x1b[2m", "\x1b[2m"), "\x1b[2mghost tail\x1b[0m",
                        "typed wrapped line"])
    assert r["wake"] is False
    assert r["reason"] == "human_composing"


def test_multiline_all_ghost_does_not_block():
    r = _wake(composer=["\u276f\xa0\x1b[2mghost head\x1b[0m", "\x1b[2mghost tail\x1b[0m"])
    assert r["wake"] is True


def test_dim_reset_mid_line_makes_tail_typed_ABORTS():
    # SGR 22 turns dim OFF: '\x1b[2mghost\x1b[22mtyped' -> the tail is typed text.
    r = _wake(composer="\u276f\xa0\x1b[2mghost \x1b[22mdeploy now")
    assert r["wake"] is False
    assert r["reason"] == "human_composing"


def test_csi_cursor_sequences_are_not_typed_text():
    # cursor-move/erase CSI noise around a dim ghost must not read as typed.
    r = _wake(composer="\u276f\xa0\x1b[K\x1b[2mTry \"fix lint errors\"\x1b[0m\x1b[1G")
    assert r["wake"] is True


def test_unreadable_composer_is_fail_closed_abort():
    # ambiguity = abort (proposal §2.1): None / no ❯ line visible -> never inject.
    for composer in (None, "", "no prompt glyph here", ["plain output", "lines"]):
        r = _wake(composer=composer)
        assert r["wake"] is False, composer
        assert r["reason"] == "composer_unreadable"


# --- cooldown 120s, CRITICAL bypasses (binding Q3) ----------------------------

def test_cooldown_suppresses_high():
    cool = {"last_wake_ts": 950.0, "woken_ids": []}
    r = _wake([_msg("m1", "high")], cooldown=cool, now=1000.0)
    assert r["wake"] is False
    assert r["reason"] == "cooldown"


def test_cooldown_expired_allows_wake():
    cool = {"last_wake_ts": 1000.0 - WAKE_COOLDOWN_S - 1, "woken_ids": []}
    assert _wake([_msg("m1")], cooldown=cool, now=1000.0)["wake"] is True


def test_critical_bypasses_cooldown():
    cool = {"last_wake_ts": 999.0, "woken_ids": []}
    r = _wake([_msg("m1", "critical"), _msg("m2", "high")], cooldown=cool, now=1000.0)
    assert r["wake"] is True
    # the bypass wake still carries the whole actionable batch
    assert {b["id"] for b in r["batch"]} == {"m1", "m2"}


# --- batch cap 8 (binding Q3) -------------------------------------------------

def test_batch_capped_at_8_with_total():
    msgs = [_msg(f"m{i}") for i in range(12)]
    r = _wake(msgs)
    assert r["wake"] is True
    assert len(r["batch"]) == BATCH_CAP
    assert r["total_pending"] == 12


def test_critical_sorts_ahead_of_high_in_batch():
    msgs = [_msg(f"m{i}", "high") for i in range(8)] + [_msg("crit", "critical")]
    r = _wake(msgs)
    assert r["batch"][0]["id"] == "crit"


# --- fail-open: malformed input -> no wake, never raise -----------------------

def test_fail_open_on_garbage_inputs():
    for bad in (
        dict(inbox=None), dict(inbox="nope"), dict(inbox=[{"no": "fields"}]),
        dict(gm_state=None), dict(gm_state="idle"),
        dict(cooldown=None), dict(cooldown={"last_wake_ts": "soon"}),
    ):
        kw = dict(inbox=[_msg("m1")], gm_state=IDLE_GM, composer=EMPTY_CURSOR,
                  cooldown=COLD)
        kw.update(bad)
        r = should_wake(kw["inbox"], kw["gm_state"], kw["composer"], kw["cooldown"],
                        now=1000.0)
        assert r["wake"] is False
        assert r["reason"]  # always says why


def test_msgs_missing_priority_are_ignored_not_fatal():
    r = _wake([{"id": "mX"}, _msg("m1", "high")])
    assert r["wake"] is True
    assert [b["id"] for b in r["batch"]] == ["m1"]


# --- digest (binding Q2: FLAGS the operator candidates, gm judges) --------------------

def test_digest_lists_items_with_agent_subject_priority():
    items = [_msg("m1", "critical", "app-dev", "watch build failing"),
             _msg("m2", "high", "ob", "seam armed")]
    d = build_wake_digest(items)
    assert d.startswith("[wake] 2 items need you")
    assert "1) app-dev — watch build failing [CRITICAL]" in d
    assert "2) ob — seam armed [HIGH]" in d


def test_digest_flags_shaw_candidates_but_decides_nothing():
    items = [_msg("m1", "critical", "app-dev", "watch build failing"),
             _msg("m2", "high", "ob", "seam armed")]
    d = build_wake_digest(items)
    assert "the operator-worthy candidate" in d
    assert "app-dev" in d
    # judgment stays with gm — the digest must not itself notify
    assert "tg-notify" not in d


def test_digest_no_candidates_line_when_no_critical():
    d = build_wake_digest([_msg("m2", "high", "ob", "seam armed")])
    assert "the operator-worthy" not in d


def test_digest_shows_n_more_beyond_cap():
    d = build_wake_digest([_msg(f"m{i}") for i in range(3)], total_pending=11)
    assert "(+8 more pending)" in d


def test_digest_carries_nonce_when_given():
    d = build_wake_digest([_msg("m1")], nonce="w-abc123")
    assert "w-abc123" in d


def test_digest_empty_items_is_safe():
    assert build_wake_digest([]) == ""


# --- confirm_turn_started (proposal §2.5) -------------------------------------

def test_confirm_on_idle_to_working_flip():
    assert confirm_turn_started({"status": "idle"}, {"status": "working"}) is True


def test_confirm_on_new_jsonl_turn():
    assert confirm_turn_started({"status": "idle", "jsonl_turns": 41},
                                {"status": "idle", "jsonl_turns": 42}) is True


def test_confirm_on_bus_prompt_submit():
    assert confirm_turn_started({"status": "idle", "last_event_type": "turn_ended"},
                                {"status": "idle", "last_event_type": "prompt_submit"}) is True


def test_no_confirm_when_nothing_changed():
    snap = {"status": "idle", "jsonl_turns": 41, "last_event_type": "turn_ended"}
    assert confirm_turn_started(snap, dict(snap)) is False


def test_confirm_fail_open_unknown_is_false():
    # unverifiable = NOT confirmed (transport retries once, then wake_failed)
    assert confirm_turn_started(None, None) is False
    assert confirm_turn_started({}, {}) is False
    assert confirm_turn_started({"jsonl_turns": 5}, {"jsonl_turns": "many"}) is False
