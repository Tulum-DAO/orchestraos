"""RED-first — Hume duplicate/superset user-finals + thought-leak strip (ios
msg_7293955c, wrist call BBE3CF65): Hume emits MULTIPLE interim:false finals per
utterance (growing supersets); is_near_duplicate deliberately rejects extensions
(max_extra_tokens=2) so BUG-3 missed them -> the same sentence journaled 2-3x, each
re-answered ('I've made a note...' repeated). Also one reply SPOKE the model's leaked
reasoning ('thought\\nThe user is reporting...'). Three seams: (1) relay _dispatch_hume
absorbs window-local duplicate/subset finals and emits a superset as a SAME-TURN
replacement; (2) voice_guards.latest_is_answered_superset catches the CLM re-answer;
(3) voice_guards.strip_thought_block keeps the answer, never the reasoning."""
import threading
import time

from services.arturo import stream_relay as sr
from services.arturo import voice_guards as vg

S1 = "We still have to figure out"
S2 = "We still have to figure out how to ensure that we can connect to bluetooth"


# ---------- (2) CLM superset guard ----------

def test_latest_is_answered_superset_true_on_extension():
    msgs = [{"role": "user", "content": S1},
            {"role": "assistant", "content": "I've made a note of that."},
            {"role": "user", "content": S2}]
    assert vg.latest_is_answered_superset(msgs)


def test_latest_is_answered_superset_true_on_exact_dup():
    msgs = [{"role": "user", "content": S2},
            {"role": "assistant", "content": "Noted."},
            {"role": "user", "content": S2 + "."}]
    assert vg.latest_is_answered_superset(msgs)


def test_latest_is_answered_superset_false_cases():
    # unanswered previous final: not suppressed (the answer is still owed)
    assert not vg.latest_is_answered_superset(
        [{"role": "user", "content": S1}, {"role": "user", "content": S2}])
    # genuinely different utterance: never suppressed
    assert not vg.latest_is_answered_superset(
        [{"role": "user", "content": S1},
         {"role": "assistant", "content": "ok"},
         {"role": "user", "content": "what's on my calendar tomorrow"}])
    # short confirmations never deduped
    assert not vg.latest_is_answered_superset(
        [{"role": "user", "content": "do it"},
         {"role": "assistant", "content": "ok"},
         {"role": "user", "content": "do it"}])


# ---------- (3) thought-leak strip ----------

def test_strip_thought_block_keeps_final_answer_paragraph():
    leaked = ("thought\nThe user is reporting a bluetooth pairing problem. I should "
              "acknowledge and note it.\n\nI've noted the Bluetooth issue and we'll dig in.")
    assert vg.strip_thought_block(leaked) == "I've noted the Bluetooth issue and we'll dig in."


def test_strip_thought_block_pure_reasoning_returns_empty():
    leaked = "thought\nThe user is reporting X so I will call remember_note next."
    assert vg.strip_thought_block(leaked) == ""


def test_strip_thought_block_passthrough_normal_text():
    assert vg.strip_thought_block("I thought about it and yes.") == "I thought about it and yes."
    assert vg.strip_thought_block("Two decisions are pending.") == "Two decisions are pending."


# ---------- (3b) thought-leak VARIANT: no marker, backticked tool-plan sentences ----------

LEAK_VARIANT = ("Therefore, the `ask_gm` tool is appropriate here as it can handle both the "
                "investigation and the research, and will provide a summary on Telegram. "
                "I will combine both requests into a single `ask_gm` call.On it, I'll text "
                "you when it's done.")


def test_strip_leading_tool_reasoning_keeps_answer():
    # ios msg_72c19e09 (B): reasoning glued to the answer with NO 'thought' marker —
    # leading sentences that speak BACKTICKED tool identifiers are reasoning, never voice.
    out = vg.strip_leading_tool_reasoning(LEAK_VARIANT)
    assert out == "On it, I'll text you when it's done."


def test_strip_leading_tool_reasoning_passthrough_and_all_reasoning():
    assert vg.strip_leading_tool_reasoning("On it, I'll text you when it's done.") == \
        "On it, I'll text you when it's done."
    assert vg.strip_leading_tool_reasoning("Two decisions are pending. Want the list?") == \
        "Two decisions are pending. Want the list?"
    # pure backticked reasoning -> empty (silence beats spoken tool-planning)
    assert vg.strip_leading_tool_reasoning("I will call `remember_note` with the text.") == ""


# ---------- (1) relay final dedup ----------

class FakeHumeSocket:
    def __init__(self):
        self.sent = []
        self._incoming = []
        self._cv = threading.Condition()
        self.closed = False

    def send(self, payload):
        pass

    def push(self, ev):
        import json
        with self._cv:
            self._incoming.append(json.dumps(ev))
            self._cv.notify()

    def recv(self):
        with self._cv:
            while not self._incoming and not self.closed:
                self._cv.wait(timeout=0.1)
            if self.closed:
                raise RuntimeError("closed")
            return self._incoming.pop(0)

    def close(self):
        self.closed = True
        with self._cv:
            self._cv.notify_all()


def _manager():
    socks = []

    def f(cid, resumed_chat_group_id=None):
        s = FakeHumeSocket()
        socks.append(s)
        return s
    m = sr.RelayManager(factories={"hume": f}, vendor_fn=lambda: "hume",
                        vendor_check=lambda v: "")
    m._t = socks
    return m


def _wait(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _final(s, text):
    s.push({"type": "user_message", "interim": False, "from_text": False,
            "message": {"role": "user", "content": text}})


def test_relay_absorbs_duplicate_and_subset_finals():
    m = _manager()
    try:
        m.feed_audio("h1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        s = m._t[0]
        _final(s, S2)
        assert _wait(lambda: any(e["type"] == "user_transcript" for e in m.events("h1", 0)[0]))
        _final(s, S2 + ".")            # duplicate re-final
        _final(s, S1)                  # subset re-final
        time.sleep(0.2)
        finals = [e for e in m.events("h1", 0)[0] if e["type"] == "user_transcript"]
        assert len(finals) == 1, f"duplicate/subset finals created extra turns: {finals}"
    finally:
        m.shutdown()


def test_relay_superset_final_replaces_same_turn():
    m = _manager()
    try:
        m.feed_audio("h1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        s = m._t[0]
        _final(s, S1)
        assert _wait(lambda: any(e["type"] == "user_transcript" for e in m.events("h1", 0)[0]))
        _final(s, S2)                  # growing superset within the window
        assert _wait(lambda: sum(1 for e in m.events("h1", 0)[0]
                                 if e["type"] == "user_transcript") == 2)
        finals = [e for e in m.events("h1", 0)[0] if e["type"] == "user_transcript"]
        assert finals[0]["turn"] == finals[1]["turn"], "superset must REPLACE the same turn"
        assert finals[1]["text"] == S2
        # a genuinely NEW utterance afterwards gets the NEXT turn number
        _final(s, "what's on my calendar tomorrow")
        assert _wait(lambda: sum(1 for e in m.events("h1", 0)[0]
                                 if e["type"] == "user_transcript") == 3)
        finals = [e for e in m.events("h1", 0)[0] if e["type"] == "user_transcript"]
        assert finals[2]["turn"] == finals[1]["turn"] + 1
    finally:
        m.shutdown()
