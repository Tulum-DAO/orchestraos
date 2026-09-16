# test_voice_guards.py — VQ-9 re-engagement filler suppression during pauses.
from services.arturo import voice_guards as vg


# ---- is_pause_turn ----
def test_pause_empty_and_ellipsis():
    assert vg.is_pause_turn("")
    assert vg.is_pause_turn("   ")
    assert vg.is_pause_turn("...")
    assert vg.is_pause_turn("…")


def test_pause_very_short():
    assert vg.is_pause_turn("ok")
    assert vg.is_pause_turn("mm")
    assert vg.is_pause_turn("uh")


def test_not_pause_real_short_question():
    # a real (if terse) request is NOT a pause
    assert not vg.is_pause_turn("status?")
    assert not vg.is_pause_turn("what's acme doing")


# ---- is_reengagement_filler ----
def test_flags_the_exact_shaw_phrase():
    assert vg.is_reengagement_filler("Is there anything specific you'd like me to help you with?")


def test_flags_common_keepalives():
    for t in ["How can I help?", "What can I do for you?", "Let me know if you need anything.",
              "I'm here whenever you're ready.", "Is there something else?"]:
        assert vg.is_reengagement_filler(t), t


def test_does_not_flag_real_answer():
    # a substantive answer that merely mentions help is NOT a bare keep-alive (word cap)
    long = ("Acme's deploy finished — the audience builder is live and the destinations "
            "service passed QA, so let me know if you want me to kick off the next batch.")
    assert not vg.is_reengagement_filler(long)


def test_does_not_flag_content():
    assert not vg.is_reengagement_filler("The build is green and deployed.")
    assert not vg.is_reengagement_filler("")


# ---- should_suppress_reengagement (double-gate) ----
def test_suppresses_only_on_pause_plus_filler():
    # pause + filler -> suppress
    assert vg.should_suppress_reengagement("", "Is there anything specific I can help with?")
    assert vg.should_suppress_reengagement("uh", "How can I help?")


def test_no_suppress_when_user_said_something():
    # real question, even if the model rambles a keep-alive, is NOT a pause -> don't suppress here
    assert not vg.should_suppress_reengagement("what's the status?", "How can I help?")


def test_no_suppress_when_response_is_real():
    assert not vg.should_suppress_reengagement("", "The deploy is live and green.")


# ---- BUG-3: near-duplicate + answered-duplicate (call vc_2fb009f8) ----
def test_near_dup_same_length_retranscription():
    # the exact pair VQ-6 missed (same length, punctuation-only diff, ratio 1.0)
    assert vg.is_near_duplicate(
        "I'm curious where you got those from. And why did you just send a bunch of messages",
        "I'm curious where you got those from, and why did you just send a bunch of messages")


def test_near_dup_one_word_swap():
    # the REAL journal pair — "tomorrow" vs "today" ASR variance on a longer utterance (ratio 0.94)
    assert vg.is_near_duplicate(
        "All right. Well, apparently, I have a meeting with, uh, Adaptive Payments tomorrow",
        "All right. Well, apparently, I have a meeting with, uh, Adaptive Payments today")


def test_near_dup_rejects_real_extension():
    # a genuine follow-on that ADDS content is NOT a duplicate (must still be answered)
    assert not vg.is_near_duplicate(
        "I'm curious where you got those from",
        "I'm curious where you got those from and why did you just send a bunch of messages")


def test_near_dup_rejects_distinct():
    assert not vg.is_near_duplicate("what agents handle adaptive payments",
                                    "what's the weather in tulum")


def test_answered_duplicate_detected():
    msgs = [
        {"role": "user", "content": "I'm curious where you got those from."},
        {"role": "assistant", "content": "I used list_agents."},
        {"role": "user", "content": "I'm curious where you got those from, ok."},
    ]
    assert vg.latest_is_answered_duplicate(msgs)


def test_answered_duplicate_ignores_new_question():
    msgs = [
        {"role": "user", "content": "what agents handle adaptive payments"},
        {"role": "assistant", "content": "pm-adaptiv."},
        {"role": "user", "content": "and who handles client provisioning"},
    ]
    assert not vg.latest_is_answered_duplicate(msgs)


def test_short_repeated_confirmation_not_deduped():
    # AGY congruence fix: a genuine repeated SHORT confirmation must NOT be suppressed — the operator says
    # "do it" for one thing (answered), then "do it" again for a DIFFERENT thing later.
    assert not vg.is_near_duplicate("do it", "do it")
    assert not vg.is_near_duplicate("yes please", "yes please")
    msgs = [
        {"role": "user", "content": "should I deploy the acme build now"},
        {"role": "assistant", "content": "yes, it passed QA."},
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": "deploying."},
        {"role": "user", "content": "do it"},                 # NEW confirmation for something else
    ]
    assert not vg.latest_is_answered_duplicate(msgs)


def test_answered_duplicate_requires_prior_answer():
    # two consecutive dup user turns with NO assistant between = in-flight case (VQ-6's job),
    # NOT the sequential answered-duplicate guard → returns False here.
    msgs = [
        {"role": "user", "content": "same thing said twice now"},
        {"role": "user", "content": "same thing said twice, now"},
    ]
    assert not vg.latest_is_answered_duplicate(msgs)


# ---- Option-1 model-filler sanitizer (commission msg_b82abb4f) ---------------
# Root cause LOG-PROVEN (miner msg_48ce6fa2): gemini-2.5-flash AUTHORS stacked
# canned fillers in content (contagion from filler-laden history replay). The
# sanitizer strips exact pool sentences at content START only; proxy VQ gate
# becomes the sole filler authority.

from services.arturo.voice_guards import strip_leading_fillers, scrub_history


def test_strips_the_exact_logged_barrage():
    # verbatim class from logs/arturo-proxy.log 01:55:05 Aug-17
    raw = ("Still on it. Almost there. One sec. Pulling that up. One sec. "
           "Still on it. Almost there. We have the fleet status.")
    out, n = strip_leading_fillers(raw)
    assert out == "We have the fleet status."
    assert n == 7


def test_strips_single_leading_filler():
    out, n = strip_leading_fillers("One sec. The answer is four.")
    assert out == "The answer is four." and n == 1


def test_mid_content_filler_never_stripped():
    raw = "The fix is done. One sec. And pushed."
    out, n = strip_leading_fillers(raw)
    assert out == raw and n == 0


def test_case_insensitive_exact_sentence():
    out, n = strip_leading_fillers("still on it. Done.")
    assert out == "Done." and n == 1


def test_near_miss_sentences_untouched():
    for raw in ("On it, I'll text you when it's done.",
                "Almost there but not quite done.",
                "One second thought: yes.",
                "Checking nowhere near done."):
        out, n = strip_leading_fillers(raw)
        assert out == raw and n == 0


def test_all_filler_reply_keeps_exactly_one_ack():
    # live-path fail-safe: a reply that is ONLY fillers collapses to ONE ack
    # (never an empty spoken turn), matching the one-filler doctrine.
    out, n = strip_leading_fillers("One sec. Still on it. Almost there.")
    assert out == "One sec."
    assert n == 2                       # two of three stripped


def test_no_filler_content_unchanged():
    out, n = strip_leading_fillers("Fleet is green across the board.")
    assert out == "Fleet is green across the board." and n == 0


def test_scrub_history_assistant_only_full_strip():
    msgs = [
        {"role": "system", "content": "One sec. persona"},
        {"role": "user", "content": "Still on it. what?"},
        {"role": "assistant", "content": "Still on it. Almost there. Deploy done."},
        {"role": "assistant", "content": "One sec. Almost there."},   # all filler
        {"role": "assistant", "content": None},                        # tool-call turn
    ]
    out = scrub_history(msgs)
    assert out[0]["content"] == "One sec. persona"          # system untouched
    assert out[1]["content"] == "Still on it. what?"        # user untouched
    assert out[2]["content"] == "Deploy done."
    # P0 field regression 2026-08-18 05:02 (the operator live call): an EMPTIED
    # assistant turn left as content:"" taught Gemini the speech channel was
    # dead -> it answered the operator via send_telegram with confused narration.
    # An all-filler assistant turn must be DROPPED from history entirely.
    assert len(out) == 4                                    # all-filler row GONE
    assert all(not (m.get("role") == "assistant" and m.get("content") == "")
               for m in out)
    assert out[3]["content"] is None                        # tool-call turn kept
    assert msgs[2]["content"].startswith("Still on it.")    # no mutation


# ---- streaming head-gate (fillers can SPAN chunk boundaries) -----------------

from services.arturo.voice_guards import LeadingFillerStreamGate


def test_stream_gate_strips_fillers_split_across_chunks():
    g = LeadingFillerStreamGate()
    out = ""
    for piece in ("Still o", "n it. Alm", "ost there. Rea", "l answer here."):
        out += g.feed(piece)
    out += g.flush()
    assert out == "Real answer here."
    assert g.stripped == 2


def test_stream_gate_clean_content_passes_through():
    g = LeadingFillerStreamGate()
    out = g.feed("Fleet is green. ")
    out += g.feed("One sec. mid-content stays.")   # head already passed
    out += g.flush()
    assert out == "Fleet is green. One sec. mid-content stays."
    assert g.stripped == 0


def test_stream_gate_all_filler_turn_flushes_one_ack():
    g = LeadingFillerStreamGate()
    out = ""
    for piece in ("One sec. ", "Still on it. ", "Almost there."):
        out += g.feed(piece)
    out += g.flush()
    assert out == "One sec."
    assert g.stripped == 2


def test_stream_gate_ambiguous_head_waits_then_releases():
    g = LeadingFillerStreamGate()
    assert g.feed("Almost th") == ""          # could still become "Almost there."
    got = g.feed("e whole fleet is green.")   # now provably NOT a filler
    assert got.startswith("Almost th")
    assert (got + g.flush()) == "Almost the whole fleet is green."
    assert g.stripped == 0


# ---- leg-3 pacing heartbeat (the operator's verbatim cadence, msg_b82abb4f item 3) ----

from services.arturo.voice_guards import ToolHeartbeat, HEARTBEAT_PREFIXES


def test_heartbeat_first_ping_only_after_interval():
    hb = ToolHeartbeat(["gm_command"], interval_s=9.0, budget=3, start_ts=100.0)
    assert hb.maybe_ping(101.0) is None          # too early — breathe
    assert hb.maybe_ping(108.0) is None
    line = hb.maybe_ping(109.5)
    assert line and "gm_command" in line          # informative, names the tool


def test_heartbeat_cadence_and_budget():
    hb = ToolHeartbeat(["deploy"], interval_s=9.0, budget=3, start_ts=0.0)
    pings = [t for t in range(0, 60) if hb.maybe_ping(float(t))]
    assert len(pings) == 3                        # hard budget
    assert pings[0] == 9
    assert all(b - a >= 9 for a, b in zip(pings, pings[1:]))  # >= interval apart


def test_heartbeat_lines_are_distinct_not_canned():
    hb = ToolHeartbeat(["x"], interval_s=1.0, budget=3, start_ts=0.0)
    lines = [hb.maybe_ping(float(t)) for t in (1, 3, 5)]
    assert len(set(lines)) == 3                   # never the same line twice
    from services.arturo.voice_guards import FILLER_SENTENCES, _strip_stacked
    for ln in lines:
        assert _strip_stacked(ln)[1] == 0         # not sanitizer-pool canned filler


def test_heartbeat_first_ping_respects_vq3b_gate():
    calls = []
    def allowed(now):
        calls.append(now)
        return False                              # cooldown active
    hb = ToolHeartbeat(["x"], interval_s=5.0, budget=3, start_ts=0.0,
                       allowed_fn=allowed)
    assert hb.maybe_ping(6.0) is None             # gated by VQ-3b
    assert calls == [6.0]
    hb2 = ToolHeartbeat(["x"], interval_s=5.0, budget=2, start_ts=0.0,
                        allowed_fn=lambda n: True)
    assert hb2.maybe_ping(6.0)                    # allowed -> pings
    assert hb2.maybe_ping(12.0)                   # subsequent NOT re-gated


def test_scrub_history_also_strips_heartbeat_lines():
    # contagion round-2 guard: proxy heartbeat lines self-clean from replayed
    # assistant history exactly like pool fillers do.
    hb_line = ToolHeartbeat(["fleet_check"], interval_s=1.0, budget=1,
                            start_ts=0.0).maybe_ping(2.0)
    msgs = [{"role": "assistant", "content": hb_line + " The fleet is green."}]
    out = scrub_history(msgs)
    assert out[0]["content"] == "The fleet is green."
    assert any(hb_line.startswith(p) for p in HEARTBEAT_PREFIXES)


def test_dynamic_pulse_deep_brain_gm_command():
    # Long deep-brain tasks emit dynamic, contextual progress pings
    hb = ToolHeartbeat(["gm_command"], interval_s=2.0, budget=4, start_ts=0.0)
    p1 = hb.maybe_ping(3.0, tool_name="gm_command", tool_args={"prompt": "Investigate fleet latency"})
    assert p1 and "deep brain" in p1
    assert p1.startswith("Working on it —")

    p2 = hb.maybe_ping(6.0, tool_name="gm_command", tool_args={"prompt": "Investigate fleet latency"})
    assert p2 and "synthesizing" in p2
    assert p2.startswith("Still working —")

    p3 = hb.maybe_ping(9.0, tool_name="gm_command", tool_args={"prompt": "Investigate fleet latency"})
    assert p3 and "breakdown" in p3
    assert p3.startswith("Hang tight —")


def test_dynamic_pulse_agent_and_command_tools():
    hb = ToolHeartbeat(["list_agents"], interval_s=1.0, budget=3, start_ts=0.0)
    p1 = hb.maybe_ping(1.5, tool_name="list_agents", tool_args={"session_id": "v2"})
    assert p1 and "fleet status" in p1

    hb_cmd = ToolHeartbeat(["run_command"], interval_s=1.0, budget=3, start_ts=0.0)
    p2 = hb_cmd.maybe_ping(1.5, tool_name="run_command", tool_args={"cmd": "git status"})
    assert p2 and "command" in p2

