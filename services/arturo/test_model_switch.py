"""RED-first: switching models mid-conversation (DEC-1790737008817757, spec v2).

Found on staging, 2026-09-30: the operator asked Claude "what model are we using?", switched to
Codex, and asked "what about now?". Codex read Claude's answer as its own and called it
"incorrect for this session", and — with no model named in its prompt — guessed "GPT-6".
Each rule below is one of the ways a switch goes wrong.
"""
import json

from services.arturo import brain as B
from services.arturo import ptt
from services.arturo.thread_store import ThreadStore
from services.arturo.warm_session import WarmPool

from services.arturo.test_warm_session import FakeProc, spawner, text_of

CLAUDE = {"provider": "claude", "model": "claude-opus-5-5"}
SONNET = {"provider": "claude", "model": "claude-sonnet-5-5"}
CODEX = {"provider": "codex", "model": "gpt-6.1-sol"}


def _history():
    return [{"role": "user", "content": "What model are we using?"},
            {"role": "assistant", "content": "Claude Opus 5.5.", "brain": CLAUDE}]


# ---- history carries who answered, and nothing extra reaches a model API ------------------

def test_build_messages_marks_a_reply_from_a_different_model():
    msgs = ptt.build_messages("SYS", _history(), "What about now?", current_brain=CODEX)
    reply = msgs[2]
    assert reply["role"] == "assistant" and reply["content"] == "Claude Opus 5.5."
    assert "claude-opus-5-5" in reply["answered_by"]


def test_a_same_provider_switch_is_still_a_switch():
    msgs = ptt.build_messages("SYS", _history(), "And now?", current_brain=SONNET)
    assert "claude-opus-5-5" in msgs[2]["answered_by"]


def test_a_reply_from_the_same_model_is_not_marked():
    msgs = ptt.build_messages("SYS", _history(), "Thanks", current_brain=CLAUDE)
    assert "answered_by" not in msgs[2]


def test_a_reply_with_no_brain_on_record_is_not_marked():
    old = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"}]
    msgs = ptt.build_messages("SYS", old, "yo", current_brain=CODEX)
    assert "answered_by" not in msgs[2]


def test_build_messages_emits_clean_dicts_and_never_mutates_history():
    history = _history()
    before = json.dumps(history, sort_keys=True)
    msgs = ptt.build_messages("SYS", history, "What about now?", current_brain=CODEX)
    assert all("brain" not in m for m in msgs)       # the stored key never travels
    assert json.dumps(history, sort_keys=True) == before


def test_the_transcript_names_the_earlier_speaker_on_the_speaker_line():
    msgs = ptt.build_messages("SYS", _history(), "What about now?", current_brain=CODEX)
    _system, prompt = B.render_transcript(msgs)
    line = next(l for l in prompt.splitlines() if "Claude Opus 5.5." in l)
    assert line.startswith("ASSISTANT (answered earlier by ") and "claude-opus-5-5" in line
    assert line.endswith("): Claude Opus 5.5.")        # the reply itself is untouched


def test_the_api_brain_gets_only_chat_fields_with_the_marker_folded_into_content():
    msgs = ptt.build_messages("SYS", _history(), "What about now?", current_brain=CODEX)
    out = B.api_messages(msgs)
    assert all(set(m) <= {"role", "content", "tool_calls", "tool_call_id", "name"} for m in out)
    assert out[2]["content"].startswith("[answered earlier by ")
    assert out[2]["content"].endswith("Claude Opus 5.5.")


def test_history_keeps_the_brain():
    h = ptt.PttHistory()
    h.append("c", "user", "hi")
    h.append("c", "assistant", "hey", brain=CODEX)
    assert h.get("c")[1]["brain"] == CODEX
    assert "brain" not in h.get("c")[0]


# ---- the archive: what wrote each turn vs what the operator chose ------------------------

def test_record_turn_stores_the_effective_brain_per_turn_and_keeps_last_brain_null(tmp_path):
    s = ThreadStore(tmp_path / "threads.db")
    s.record_turn("t", "hi", "hey", brain=None, effective=CODEX)
    assert s.history("t")[1]["brain"] == CODEX
    assert s.get_thread("t")["last_brain"] is None   # the picker still reads "default"


def test_record_turn_without_effective_behaves_as_before(tmp_path):
    s = ThreadStore(tmp_path / "threads.db")
    s.record_turn("t", "hi", "hey", brain=CLAUDE)
    assert s.history("t")[1]["brain"] == CLAUDE
    assert s.get_thread("t")["last_brain"] == CLAUDE


def test_turn_count_is_the_archive_version(tmp_path):
    s = ThreadStore(tmp_path / "threads.db")
    assert s.turn_count("t") == 0
    s.record_turn("t", "hi", "hey")
    s.record_turn("t", "again", "sure")
    assert s.turn_count("t") == 4


# ---- a warm session never answers from a conversation it did not see ---------------------
# The version is the ARCHIVE's turn count: in-memory history is evicted and trimmed, and a
# counter that restarts could land on a stale session's number by accident.

KEY = ("c", "claude", "")


def _pool(*procs):
    spawn = spawner(list(procs))
    return WarmPool(spawn=spawn, argv_for=lambda k: ["cli"]), spawn


def test_a_warm_session_in_step_is_reused():
    pool, spawn = _pool(FakeProc(["one", "two"]))
    text_of(pool.turn(KEY, "hi", version=0))
    pool.sync(KEY, 2)                                  # the turn was recorded
    assert text_of(pool.turn(KEY, "again", version=2)) == "two"
    assert len(spawn.started) == 1


def test_a_warm_session_behind_the_conversation_is_replaced():
    pool, spawn = _pool(FakeProc(["one"]), FakeProc(["fresh"]))
    text_of(pool.turn(KEY, "hi", version=0))
    pool.sync(KEY, 2)
    # two more messages were recorded elsewhere (another model, or /text) -> 4, not 2
    assert text_of(pool.turn(KEY, "back to you", argv=["cli", "seeded"], version=4)) == "fresh"
    assert len(spawn.started) == 2 and spawn.started[1][0] == ["cli", "seeded"]


def test_a_turn_that_was_never_recorded_leaves_the_session_unusable():
    # an empty reply or an error is not saved, but the process remembers it
    pool, spawn = _pool(FakeProc(["one"]), FakeProc(["fresh"]))
    text_of(pool.turn(KEY, "hi", version=0))
    assert text_of(pool.turn(KEY, "again", version=0)) == "fresh"


def test_tool_rounds_inside_one_turn_are_not_rechecked():
    pool, spawn = _pool(FakeProc(["round one", "round two"]))
    text_of(pool.turn(KEY, "hi", version=0))
    assert text_of(pool.turn(KEY, "tool results", version=None)) == "round two"
    assert len(spawn.started) == 1


def test_a_prewarm_that_raced_a_recorded_turn_is_not_used():
    pool, spawn = _pool(FakeProc(["stale"]), FakeProc(["fresh"]))
    pool.prewarm(KEY, ["cli"], version=0)              # seeded from an empty conversation
    # ...meanwhile a Codex turn was recorded (2), and now Claude is asked
    assert text_of(pool.turn(KEY, "hello", argv=["cli", "seeded"], version=2)) == "fresh"


def test_a_prewarmed_session_in_step_is_used():
    pool, spawn = _pool(FakeProc(["warm"]))
    pool.prewarm(KEY, ["cli"], version=2)
    assert text_of(pool.turn(KEY, "hello", version=2)) == "warm"
    assert len(spawn.started) == 1


def test_sync_on_a_conversation_with_no_session_is_harmless():
    pool, _ = _pool()
    pool.sync(KEY, 2)


# ---- the brain knows which model it is ---------------------------------------------------

def test_default_models_are_read_from_the_live_cache(tmp_path):
    p = tmp_path / "model-catalog-live.json"
    p.write_text(json.dumps({"providers": {"codex": ["gpt-6.1-sol"]},
                             "defaults": {"codex": "gpt-6.1-sol"}}))
    assert B.load_default_models(p) == {"codex": "gpt-6.1-sol"}
    assert B.load_default_models(tmp_path / "missing.json") == {}


def test_identity_names_the_default_model_when_the_probe_knows_it():
    d = {"kind": "runtime", "runtime": "codex", "cli": "codex", "model": "codex-cli-default"}
    line = B.identity_line(d, default_model="gpt-6.1-sol")
    assert "Codex (gpt-6.1-sol)" in line


def test_identity_admits_an_unknown_default_instead_of_inviting_a_guess():
    d = {"kind": "runtime", "runtime": "gemini", "cli": "agy", "model": ""}
    line = B.identity_line(d, default_model=None)
    assert "default model" in line and "exact name" in line


def test_identity_explains_replies_from_an_earlier_model():
    d = {"kind": "runtime", "runtime": "codex", "cli": "codex", "model": "gpt-5.6-luna"}
    line = B.identity_line(d, default_model=None)
    assert "Codex (gpt-5.6-luna)" in line
    assert "answered earlier by" in line and "accurate for that model" in line
    # ...but it is context, not a talking point: unprompted, it was tacked onto a plain answer
    # ("The earlier answers were accurate for the models you had switched to", 2026-09-30).
    assert "don't bring them up unless asked" in line
    # Sonnet still volunteered "The earlier greeting came from a different model before you
    # switched" in answer to "what model are we on?" — the switch itself is not a talking point.
    assert "never mention that the model was switched" in line


def test_identity_asks_for_the_model_alone_not_the_plumbing():
    # "say exactly that" after "— no API key is involved" made every answer to "what model?"
    # end in "No API key is involved." (operator: "we don't have to say that every time").
    d = {"kind": "runtime", "runtime": "codex", "cli": "codex", "model": "gpt-6.1-sol"}
    line = B.identity_line(d)
    assert "say exactly that" not in line
    assert "just name the model" in line
    assert "unless asked how you are connected" in line


def test_effective_brain_resolves_the_default_to_its_probed_name():
    d = {"kind": "runtime", "runtime": "codex", "cli": "codex", "model": "codex-cli-default"}
    assert B.effective_brain(d, {"codex": "gpt-6.1-sol"}) == CODEX
    assert B.effective_brain(d, {}) == {"provider": "codex", "model": ""}
    explicit = {"kind": "runtime", "runtime": "claude", "cli": "claude", "model": "claude-sonnet-5-5"}
    assert B.effective_brain(explicit, {"claude": "claude-opus-5-5"}) == SONNET


def test_a_prewarm_never_kills_a_turn_in_flight():
    pool, spawn = _pool(FakeProc(["streaming", "next"]))
    gen = pool.turn(KEY, "hi", version=0)
    next(gen)                                            # the turn holds the session
    assert pool.prewarm(KEY, ["cli"], version=0) is False
    list(gen)                                            # the turn finishes normally
    assert not spawn.started[0][1].killed
    assert len(spawn.started) == 1


def test_a_turn_that_waited_for_the_lock_rechecks_the_version():
    import threading
    pool, spawn = _pool(FakeProc(["first", "stale"]), FakeProc(["fresh"]))
    gen = pool.turn(KEY, "hi", version=0)
    next(gen)                                            # turn one holds the session
    got = {}
    t = threading.Thread(target=lambda: got.setdefault("t", text_of(pool.turn(KEY, "b", version=2))))
    t.start()
    import time
    time.sleep(0.2)
    assert not spawn.started[0][1].killed                # turn two WAITS; it does not cut in
    list(gen)                                            # turn one ends; never synced to 2
    t.join(timeout=5)
    assert got["t"] == "fresh" and len(spawn.started) == 2
