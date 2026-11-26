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
    assert out == srv.DETACHED and "gm has not answered yet" in out["result"]
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
    assert r.execute(_B(), "c1", "gm_command", "{}") == srv.DETACHED
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


def test_a_handed_over_call_stays_handed_over_after_gm_answers():
    # codex review of #673: the session polls every pending call again while another tool in the same delegation
    # holds it; the gm call must keep saying "sent", or gm's answer arrives twice (as a note and as the output)
    notes = []
    r, gate, clock = _runner("gm says:\nIt is the backend.", notes.append)
    r.execute(_B(), "c1", "gm_command", "{}")
    clock[0] += srv.DETACH_AFTER_S
    first = r.execute(_B(), "c1", "gm_command", "{}")
    gate.set()
    assert _wait(lambda: notes)
    assert r.execute(_B(), "c1", "gm_command", "{}") == first == srv.DETACHED


def test_a_gm_call_held_beside_a_slow_tool_goes_back_once_as_sent(tmp_path):
    from services.arturo.native.session import NativeSession
    from services.arturo.native import fixtures as fx
    from services.arturo.native.tests.test_native import _fleet, _tools
    import json
    gate_gm, gate_list, clock = threading.Event(), threading.Event(), [100.0]

    class Two:
        def __init__(self, t):
            self.t, self.ledger = t, t.ledger

        def new_turn(self, c):
            self.t.new_turn(c)

        def execute(self, binding, cid, name, args):
            (gate_gm if name == "gm_command" else gate_list).wait(5)
            return {"ok": True, "result": "gm says:\nanswer" if name == "gm_command" else "seats: gm"}
    t, _ = _tools(tmp_path)
    notes = []
    runner = srv.ToolRunner(Two(t), ThreadPoolExecutor(2), late=notes.append, clock=lambda: clock[0])
    sess = NativeSession(_fleet(t), runner, clock=lambda: clock[0], settle=None)
    sess.on_server_frame(fx.started()[0])
    for f in fx.tool_turn("d1", "r1", [("g1", "gm_command", "{}"), ("l1", "list_agents", "{}")]):
        sess.on_server_frame(f[0])
    clock[0] += srv.DETACH_AFTER_S
    assert sess.tick() == []                                  # list_agents still runs: the delegation stays held
    gate_gm.set()
    assert _wait(lambda: notes)
    gate_list.set()
    out = []
    assert _wait(lambda: out.extend(sess.tick()) or out)
    gm = [json.loads(f["item"]["output"]) for f in out if f.get("item", {}).get("call_id") == "g1"]
    assert gm == [srv.DETACHED]
