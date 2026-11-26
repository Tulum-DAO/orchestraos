"""A question to gm never holds the call (Shaw's 18:3x call, 2026-11-26: gm_command polled gm's reply for 25 s, three
times, and the voice went silent each time). The tool is handed over, the call hears "sent" at once, and gm's answer,
if it lands while the call is up, comes in as a note."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from services.arturo.native import server as srv
from services.arturo.native.session import RUNNING


class _Tools:
    ledger = None

    def __init__(self, reply, gate):
        self.reply, self.gate = reply, gate

    def new_turn(self, conversation_id):
        pass

    def execute(self, binding, tool_call_id, name, arguments_json):
        self.gate.wait(5)
        return {"ok": True, "result": self.reply}


class _B:
    conversation_id = "native-test"


def _runner(reply, late):
    gate, clock = threading.Event(), [100.0]
    r = srv.ToolRunner(_Tools(reply, gate), ThreadPoolExecutor(2), late=late, clock=lambda: clock[0])
    return r, gate, clock


def _wait(pred, s=2.0):
    end = time.monotonic() + s
    while not pred() and time.monotonic() < end:
        time.sleep(0.01)
    return pred()


def test_a_question_to_gm_is_handed_over_and_the_call_hears_sent_at_once():
    notes = []
    r, gate, clock = _runner("gm says:\nIt is the backend's tool decision.", notes.append)
    assert r.execute(_B(), "c1", "gm_command", "{}") is RUNNING
    clock[0] += srv.DETACH_AFTER_S - 0.1
    assert r.execute(_B(), "c1", "gm_command", "{}") is RUNNING          # a fast answer still comes back in the turn
    clock[0] += 0.2
    out = r.execute(_B(), "c1", "gm_command", "{}")
    assert out["ok"] is True and out["result"].startswith("Sent to gm.")
    assert notes == []
    gate.set()                                                               # gm answers while the call is up
    assert _wait(lambda: notes)
    assert notes == ["It is the backend's tool decision."]


def test_gms_promise_after_the_hand_over_is_not_repeated_into_the_call():
    notes = []
    r, gate, clock = _runner("gm has it; the answer comes to your chat. Tell the operator that in a few words and "
                             "move on.", notes.append)
    r.execute(_B(), "c1", "gm_command", "{}")
    clock[0] += srv.DETACH_AFTER_S
    assert r.execute(_B(), "c1", "gm_command", "{}")["result"].startswith("Sent to gm.")
    gate.set()
    time.sleep(0.2)
    assert notes == []


def test_any_other_tool_still_holds_until_its_result():
    notes = []
    r, gate, clock = _runner("seats: gm", notes.append)
    r.execute(_B(), "c1", "list_agents", "{}")
    clock[0] += 60
    assert r.execute(_B(), "c1", "list_agents", "{}") is RUNNING
    gate.set()
    assert _wait(lambda: r.execute(_B(), "c1", "list_agents", "{}") is not RUNNING)
    assert r.execute(_B(), "c1", "list_agents", "{}") == {"ok": True, "result": "seats: gm"}
    assert notes == []
