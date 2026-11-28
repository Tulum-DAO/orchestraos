"""Send-now: a person's message Claude Code already holds in its own queue (the send answered outcome "delivered-steer")
is submitted at once with the CLI's own chord, ctrl+x ctrl+s (the operator: clicking the Queued chip "causes it to be
SENT THEN").

MEASURED on Claude Code 2.1.284 (2026-11-27, a lab seat on its own tmux server; docs/DELIVERY.md "Send now"): the
chord submits the WHOLE queue and any typed draft, moves a running command to the background, and cuts a reply being
written short. So the gateway sends it only for exactly this message, alone in the queue, with an empty composer,
and says what it did to the running step (step_ended).

  S1  a delivered-steer answer carries message_id (additive: the old answer is a subset of the new one)
  S2  the chord submits exactly the queued message, verified by its leaving the queue in the transcript
  S3  refusals (409, nothing sent): another person's message queued, text in the composer, an id for another seat,
      a menu, a screen that does not show exactly this message queued, no transcript
  S4  already-sent / not-queued; 502 unknown when the leaving is not seen, and never the keys twice
  S5  only a person's surface; Claude Code panes only (501 otherwise)

Real app (scope middleware); tmux, the detector and the CLI's transcript are stubbed (the harness of
test_gateway_person_instant). The CLI stub writes the transcript rows measured on the lab seat when the chord lands.
"""
import json
import logging
import os
import stat
import threading
import time
import types
from datetime import datetime, timezone

import pytest

import scripts.watch_gateway as G
from scripts import claude_queue as Q
from scripts import steered_sends as SS
from test_gateway_person_instant import (  # noqa: F401  (fixtures and helpers)
    AGENT_UA, PHONE, _Det, _headers, _post, box,
)


@pytest.fixture(autouse=True)
def _everything_under_tmp(tmp_path, monkeypatch):
    """Nothing here may write the live tree: the ledger, the inject journal and the pane locks all go to tmp."""
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "orch"))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path / "orch")
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("PANE_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.delenv("TMUX", raising=False)


def test_the_ledger_under_test_is_inside_tmp(tmp_path, box):
    # FIRST: the path the code writes is the tmp one (import-time paths have written live state in this fleet)
    p = os.path.realpath(SS.ledger_path(G.ORCH_DIR))
    assert p.startswith(os.path.realpath(str(tmp_path))), p


# ------------------------------------------------------------------ screens (the lab capture's exact rows)
GRAY, BG = "\x1b[38;5;246m", "\x1b[38;5;239m\x1b[48;5;237m"
PLACEHOLDER = (f"{GRAY}❯\xa0\x1b[2m\x1b[39mPress\x1b[0m \x1b[2mup\x1b[0m \x1b[2mto\x1b[0m \x1b[2medit\x1b[0m "
               f"\x1b[2mqueued\x1b[0m \x1b[2mmessages\x1b[0m")
RULE = "\x1b[38;5;244m" + "─" * 60
MENU_SCREEN = "Claude needs your permission to run Bash\n 1. Yes\n 2. Yes, and don't ask again\n 3. No\n"


def queued_pane(*msgs, composer=PLACEHOLDER, hint=True):
    rows = ["● Bash(python3 -c 'import time; time.sleep(75)')", f"{GRAY}  ⎿  Running… (16s · timeout 2m)\x1b[39m", ""]
    for i, m in enumerate(msgs):
        if i:
            rows.append("\x1b[49m")
        rows.append(f"{BG}❯ {GRAY}{m}\x1b[39m")
    if hint:
        rows.append(f"\x1b[49m  {GRAY}ctrl+x ctrl+s to send now\x1b[39m")
    rows += ["\x1b[38;5;174m·\x1b[39m \x1b[38;5;180mThinking…\x1b[38;5;174m (20s)\x1b[39m", "", RULE, composer, RULE,
             "\x1b[39m  ⏵⏵ auto mode on · esc to interrupt"]
    return "\n".join(rows) + "\n"


DRAFT = f"{GRAY}❯\xa0\x1b[39mmy own half-typed draft"
WORDS = "look at the failing test first"
OTHER = "and then the flaky one"


# ------------------------------------------------------------------ transcript rows (shapes measured on the lab seat)
def _iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def enq(c, t=None):
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": _iso(t or time.time()), "content": c}


def deq(t=None):
    return {"type": "queue-operation", "operation": "dequeue", "timestamp": _iso(t or time.time())}


def rem(c, t=None):
    return {"type": "queue-operation", "operation": "remove", "timestamp": _iso(t or time.time()), "content": c,
            "reason": "absorbed_mid_turn"}


def bg_result(t=None):
    return {"type": "user", "timestamp": _iso(t or time.time()),
            "message": {"role": "user", "content": [{"type": "tool_result", "content": "Command was moved to the "
                                                     "background (ID: b1) so that a message ... can reach you"}]},
            "toolUseResult": {"backgroundTaskId": "b1", "backgroundedToDeliverMessage": True, "interrupted": False}}


def tool_result(t=None):
    return {"type": "user", "timestamp": _iso(t or time.time()),
            "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
            "toolUseResult": {"stdout": "ok", "interrupted": False}}


def asst(stop, t=None):
    return {"type": "assistant", "timestamp": _iso(t or time.time()),
            "message": {"stop_reason": stop, "content": [{"type": "text", "text": "a reply being written"}]}}


def turn_end(t=None):
    return {"type": "system", "subtype": "turn_duration", "timestamp": _iso(t or time.time())}


def prompt(c, t=None):
    return {"type": "user", "timestamp": _iso(t or time.time()), "message": {"role": "user", "content": c}}


TASK = "<task-notification>\n<task-id>b9</task-id>\n<status>completed</status>\n</task-notification>"


# ------------------------------------------------------------------ the seat: tmux + detector + transcript stub
class _Seat:
    """tmux stub: every capture answers `screen`; C-s runs `on_chord` (the CLI writing its rows)."""
    def __init__(self, tmp_path, screen, rows, on_chord=None):
        self.calls = []
        self.screen = screen
        self.path = tmp_path / "sid.jsonl"
        self.write(rows)
        self.on_chord = on_chord
        self.clients = ""

    def write(self, rows):
        with open(self.path, "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def __call__(self, *args, timeout=5):
        self.calls.append(args)
        out = self.screen if args[0] == "capture-pane" else (self.clients if args[0] == "list-clients" else "")
        if args[0] == "send-keys" and args[-1] == "C-s" and self.on_chord:
            self.on_chord(self)
        return types.SimpleNamespace(returncode=0, stdout=out)

    def keys(self):
        return [c[-1] if "-l" not in c else ("-l", c[-1]) for c in self.calls if c[0] == "send-keys"]


def _seat(monkeypatch, tmp_path, screen, rows, on_chord=None, runtime="claude", hook=None):
    s = _Seat(tmp_path, screen, rows, on_chord)
    monkeypatch.setattr(G, "_tmux", s)
    monkeypatch.setattr(G, "_agent_status", lambda: _Det("working", runtime, hook))
    monkeypatch.setattr(G, "_seat_transcript_path", lambda sess, meta=None: s.path)
    monkeypatch.setattr(G, "SEND_NOW_POLL_S", 0.0)
    return s


def _steered(text=WORDS, session="seat"):
    return SS.record(G.ORCH_DIR, session, text)


def _send_now(box, body, ua="OrchestraUltra/1 CFNetwork/1 Darwin/1", actor=None):
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            r = await c.post("/agent-send-now", json=body, headers=_headers(box, actor, ua))
            return r.status, await r.json()
    return asyncio.run(go())


def absorbed(rows_fn):
    def chord(seat):
        seat.write(rows_fn())
    return chord


# ------------------------------------------------------------------ S1: message_id on the delivered-steer answer
def test_S1_a_delivered_steer_answer_carries_a_message_id_and_keeps_the_old_fields(box, monkeypatch):
    monkeypatch.setattr(G, "_agent_status", lambda: _Det("working", "claude"))
    monkeypatch.setattr(G, "verified_inject",
                        lambda *a, **k: (True, {"state": "working", "attempts": 1, "queued_mid_turn": True}))
    status, body = _post(box, {**PHONE, "text": WORDS})
    assert status == 200 and body["outcome"] == "delivered-steer", body
    assert SS.valid_id(body["message_id"]), body
    row = SS.get(G.ORCH_DIR, body["message_id"])
    assert row["session"] == "seat" and row["text"] == WORDS
    legacy = {"ok", "delivered", "state", "attempts", "queued_mid_turn", "outcome"}
    assert legacy <= set(body) and set(body) - legacy == {"message_id"}, "additive only"
    mode = stat.S_IMODE(os.stat(SS.ledger_path(G.ORCH_DIR)).st_mode)
    assert mode == 0o600, oct(mode)


def test_S1_a_long_message_is_kept_as_the_banner_the_cli_queued(box, monkeypatch):
    monkeypatch.setattr(G, "_agent_status", lambda: _Det("working", "claude"))
    monkeypatch.setattr(G, "verified_inject", lambda *a, **k: (True, {
        "state": "working", "attempts": 1, "queued_mid_turn": True, "chip_dodge_pointer": "/tmp/agent-inject-1-ab.md"}))
    status, body = _post(box, {**PHONE, "text": "x " * 900})
    row = SS.get(G.ORCH_DIR, body["message_id"])
    assert row["text"].startswith("[LONG-MSG chip-dodge]") and row["text"].endswith("read /tmp/agent-inject-1-ab.md")


@pytest.mark.parametrize("info", [{"state": "idle", "attempts": 1},                       # delivered-now
                                  {"state": "working", "attempts": 1, "interrupted": True}])
def test_S1_no_message_id_unless_the_message_sits_in_the_cli_queue(box, monkeypatch, info):
    monkeypatch.setattr(G, "_agent_status", lambda: _Det("working", "claude"))
    monkeypatch.setattr(G, "verified_inject", lambda *a, **k: (True, dict(info)))
    status, body = _post(box, PHONE)
    assert status == 200 and "message_id" not in body, body


def test_S1_no_message_id_for_a_runtime_without_the_chord(box, monkeypatch):
    monkeypatch.setattr(G, "_agent_status", lambda: _Det("working", "codex"))
    monkeypatch.setattr(G, "verified_inject",
                        lambda *a, **k: (True, {"state": "working", "attempts": 1, "queued_mid_turn": True}))
    status, body = _post(box, PHONE)
    assert body["outcome"] == "delivered-steer" and "message_id" not in body, body


def test_S1_a_ledger_that_cannot_be_written_never_fails_the_send(box, monkeypatch):
    monkeypatch.setattr(G, "_agent_status", lambda: _Det("working", "claude"))
    monkeypatch.setattr(G, "verified_inject",
                        lambda *a, **k: (True, {"state": "working", "attempts": 1, "queued_mid_turn": True}))
    monkeypatch.setattr(SS, "record", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    status, body = _post(box, PHONE)
    assert status == 200 and body["outcome"] == "delivered-steer" and "message_id" not in body


# ------------------------------------------------------------------ S2: the chord submits exactly this message
def test_S2_the_chord_submits_the_queued_message_and_says_the_command_was_backgrounded(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [prompt("run the suite"), enq(WORDS)],
                 on_chord=absorbed(lambda: [bg_result(), rem(WORDS)]))
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 200 and body["outcome"] == "delivered-now", body
    assert body["step_ended"] == "backgrounded" and body["also_submitted"] == [] and body["message_id"] == mid
    assert body["also_submitted_kinds"] == []
    assert seat.keys() == ["C-x", "C-s"], "the chord and nothing else: no words typed, no Enter, no Escape"
    row = SS.get(G.ORCH_DIR, mid)
    assert row["sent_at"] and row["text"] == "", "a sent row is kept as sent, its words dropped"


def test_S2_a_reply_cut_short_says_truncated(box, monkeypatch, tmp_path):
    mid = _steered()
    # measured: the ending turn's last assistant row (stop_reason null) is written AFTER the dequeue row
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [prompt("write a long story"), enq(WORDS)],
                 on_chord=absorbed(lambda: [deq(), asst(None), prompt(WORDS)]))
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 200 and body["step_ended"] == "truncated", body
    assert seat.keys() == ["C-x", "C-s"]


def test_S2_a_task_notification_in_the_queue_goes_too_and_is_named(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [prompt("go"), enq(TASK), enq(WORDS)],
                 on_chord=absorbed(lambda: [bg_result(), rem(TASK), rem(WORDS)]))
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 200 and body["also_submitted"] == [] and body["also_submitted_kinds"] == ["task-notification"], body
    assert seat.keys() == ["C-x", "C-s"]


def test_S2_the_words_never_reach_any_log_line(box, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    mid = _steered()
    _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)], on_chord=absorbed(lambda: [bg_result(), rem(WORDS)]))
    _send_now(box, {"session": "seat", "message_id": mid})
    assert caplog.records and not any(WORDS in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------ S3: refusals, nothing sent
def test_S3_text_in_the_composer_refuses_because_the_chord_would_submit_it(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS, composer=DRAFT), [enq(WORDS)])
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 423 and body["reason"] == "draft-in-composer", body
    assert "send or clear it first" in body["error"] and seat.keys() == []


def test_S3_an_attached_terminal_refuses_with_423(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)])
    seat.clients = "/dev/pts/3: seat [160x45 xterm-256color] (utf8)\n"
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 423 and body["reason"] == "terminal-attached", body
    assert body["error"] == "Someone has this agent's terminal open; send it from there." and seat.keys() == []


def test_S3_tmux_that_cannot_say_who_is_attached_refuses(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)])
    real = seat.__call__

    def fail_list(*args, timeout=5):
        if args[0] == "list-clients":
            seat.calls.append(args)
            return types.SimpleNamespace(returncode=1, stdout="")
        return real(*args, timeout=timeout)
    monkeypatch.setattr(G, "_tmux", fail_list)
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 423 and body["reason"] == "terminal-attached" and seat.keys() == []


def test_S3_an_id_sent_to_another_agent_refuses(box, monkeypatch, tmp_path):
    mid = _steered(session="gm")
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)])
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 409 and body["reason"] == "other-agent", body
    assert seat.keys() == []


def test_S3_a_menu_on_screen_refuses(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, MENU_SCREEN, [enq(WORDS)])
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 409 and body["reason"] == "prompt-open", body
    assert seat.keys() == []


def test_S3_a_fresh_permission_hook_refuses(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)],
                 hook={"state": "waiting_permission", "ts": time.time()})
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 409 and body["reason"] == "prompt-open" and seat.keys() == []


@pytest.mark.parametrize("screen", [queued_pane(OTHER, WORDS),          # a second message the transcript did not show
                                    queued_pane(OTHER),                 # a different message
                                    queued_pane(WORDS, hint=False)])    # no send-now hint: nothing for the chord
def test_S3_a_screen_that_does_not_show_exactly_this_message_refuses(box, monkeypatch, tmp_path, screen):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, screen, [enq(WORDS)])
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 409 and body["reason"] == "queue-unclear", body
    assert seat.keys() == []


def test_S3_no_transcript_refuses(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)])
    monkeypatch.setattr(G, "_seat_transcript_path", lambda sess, meta=None: None)
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 409 and body["reason"] == "no-transcript" and seat.keys() == []


# ------------------------------------------------------------------ S4: already-sent, not-queued, unknown
def test_S4_a_message_that_already_left_the_queue_is_already_sent(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(), [enq(WORDS), tool_result(), rem(WORDS)])
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 200 and body["outcome"] == "already-sent", body
    assert seat.keys() == [] and SS.get(G.ORCH_DIR, mid)["sent_at"]


def test_S4_a_message_the_queue_never_held_is_not_queued(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(), [prompt(WORDS)])
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 200 and body["outcome"] == "not-queued" and seat.keys() == []


def test_S4_a_leaving_not_seen_is_unknown_and_the_keys_never_go_twice(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)], on_chord=None)   # the CLI writes nothing
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 502 and body["state"] == "unknown" and "nothing will be retried" in body["error"], body
    assert seat.keys() == ["C-x", "C-s"]
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 502 and body["state"] == "unknown", body
    assert seat.keys() == ["C-x", "C-s"], "a second send-now sent the chord again"


def test_S4_two_send_nows_for_one_message_send_the_chord_once(box, monkeypatch, tmp_path):
    mid = _steered()
    gate = threading.Event()

    def chord(seat):
        gate.wait(2)                       # the second call is waiting on the lock while the first is mid-chord
        seat.write([bg_result(), rem(WORDS)])
        seat.screen = queued_pane()        # the CLI no longer draws it queued
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)], on_chord=chord)
    out = []
    ts = [threading.Thread(target=lambda: out.append(G._send_now("seat", mid))) for _ in range(2)]
    for t in ts:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in ts:
        t.join(10)
    assert sorted(b["outcome"] for _s, b in out) == ["already-sent", "delivered-now"], out
    assert seat.keys().count("C-s") == 1


# ------------------------------------------------------------------ S5: who may ask, and where
def test_S5_an_agent_with_the_fleet_bearer_is_refused(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)])
    status, body = _send_now(box, {"session": "seat", "message_id": mid}, ua=AGENT_UA)
    assert status == 403 and body["reason"] == "not-a-person" and seat.keys() == []


def test_S5_another_runtime_is_unsupported(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)], runtime="codex")
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 501 and body["outcome"] == "unsupported" and seat.keys() == []


def test_S5_a_headless_seat_is_unsupported(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)])
    monkeypatch.setattr(G, "_headless_seat_for", lambda s: "seat")
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 501 and seat.keys() == []


@pytest.mark.parametrize("body", [{"session": "seat"}, {"session": "seat", "message_id": "msg_123"},
                                  {"message_id": "stq_" + "a" * 16}])
def test_S5_a_malformed_request_is_400(box, monkeypatch, tmp_path, body):
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)])
    status, _ = _send_now(box, body)
    assert status == 400 and seat.keys() == []


def test_S5_an_unknown_session_is_404(box, monkeypatch, tmp_path):
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)])
    status, _ = _send_now(box, {"session": "nope", "message_id": _steered(session="nope")})
    assert status == 404 and seat.keys() == []


# ------------------------------------------------------------------ the readers on their own
def test_queue_items_are_fifo_and_a_remove_takes_its_own_content():
    rows = [enq("a"), enq("b"), deq(), enq("c"), rem("c")]
    items = Q.queue_items(rows)
    assert [(i["content"], i["left"] and i["left"]["how"]) for i in items] == [
        ("a", "dequeue"), ("b", None), ("c", "remove")]


@pytest.mark.parametrize("after,want", [
    ([bg_result(), rem(WORDS)], "backgrounded"),
    ([tool_result(), rem(WORDS)], "none"),
    ([rem(WORDS)], "unknown"),
    ([deq(), asst(None), prompt(WORDS)], "truncated"),
    ([asst("end_turn"), turn_end(), deq(), prompt(WORDS)], "none"),
    ([deq(), asst("end_turn"), turn_end(), prompt(WORDS)], "none"),
    ([deq(), prompt(WORDS)], "unknown"),
])
def test_step_ended_reads_only_what_the_rows_say(after, want):
    rows = [prompt("go"), enq(WORDS)] + after
    assert Q.step_ended(rows, Q.find(rows, WORDS)["item"]) == want


def test_screen_queue_reads_one_two_and_wrapped_messages_and_nothing_without_the_hint():
    assert Q.screen_queue(queued_pane(WORDS)) == [WORDS]
    assert Q.screen_queue(queued_pane(OTHER, WORDS)) == [OTHER, WORDS]
    wrapped = queued_pane("word0 word1").replace("word1\x1b[39m", f"word1\n  {GRAY}word2 word3\x1b[39m")
    assert Q.screen_queue(wrapped) == ["word0 word1 word2 word3"]
    assert Q.screen_queue(queued_pane(WORDS, hint=False)) is None
    # a submitted prompt (white, not gray) right above the hint is not a queued message
    white = queued_pane(WORDS).replace(f"❯ {GRAY}{WORDS}", f"❯ \x1b[38;5;231m{WORDS}")
    assert Q.screen_queue(white) == []


def test_the_ledger_is_capped_by_count_and_age(box):
    t0 = 1_000_000.0
    old = SS.record(G.ORCH_DIR, "seat", "old", now=t0)
    ids = [SS.record(G.ORCH_DIR, "seat", f"m{i}", now=t0 + SS.MAX_AGE_S + i) for i in range(SS.MAX_ROWS + 5)]
    rows = [json.loads(ln) for ln in open(SS.ledger_path(G.ORCH_DIR))]
    assert len(rows) == SS.MAX_ROWS and old not in {r["id"] for r in rows}
    assert rows[-1]["id"] == ids[-1] and SS.get(G.ORCH_DIR, ids[0], now=t0 + SS.MAX_AGE_S) is None


# ------------------------------------------------------------------ review r1 (fenced Claude, BLOCK @ 64815b69)
def _raising_on(seat, key, exc):
    real = seat.__call__

    def t(*args, timeout=5):
        if args[0] == "send-keys" and args[-1] == key:
            seat.calls.append(args)
            raise exc
        return real(*args, timeout=timeout)
    return t


@pytest.mark.parametrize("key", ["C-x", "C-s"])
def test_R1_a_failure_once_the_keys_may_have_gone_is_unknown_never_nothing_sent(box, monkeypatch, tmp_path, key):
    import subprocess
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)])
    monkeypatch.setattr(G, "_tmux", _raising_on(seat, key, subprocess.TimeoutExpired(["tmux"], 5)))
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 502 and body["state"] == "unknown", body
    assert "nothing was sent" not in json.dumps(body).lower()
    assert SS.get(G.ORCH_DIR, mid)["chorded_at"], "the keys may have gone: the mark stays, no second chord"


def test_R1_a_ledger_that_cannot_drop_after_the_leaving_is_seen_still_says_delivered_now(box, monkeypatch, tmp_path):
    mid = _steered()
    _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)], on_chord=absorbed(lambda: [bg_result(), rem(WORDS)]))
    monkeypatch.setattr(SS, "mark_sent", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 200 and body["outcome"] == "delivered-now", body


def test_R1_a_second_key_tmux_refused_is_unknown_and_says_the_chord_is_half_pressed(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)])
    real = seat.__call__

    def t(*args, timeout=5):
        if args[0] == "send-keys" and args[-1] == "C-s":
            seat.calls.append(args)
            return types.SimpleNamespace(returncode=1, stdout="")
        return real(*args, timeout=timeout)
    monkeypatch.setattr(G, "_tmux", t)
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 502 and body["state"] == "unknown" and "first of the two" in body["error"], body


def test_R1_a_tail_that_starts_mid_queue_never_reads_a_still_queued_message_as_sent(box, monkeypatch, tmp_path):
    # the dequeue belongs to an older message enqueued before the rows read; the screen still shows ours queued
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS), deq()])
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 409 and body["reason"] == "queue-unclear", body
    assert seat.keys() == [] and SS.get(G.ORCH_DIR, mid) is not None


def test_R1_not_queued_is_said_only_when_the_screen_agrees(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [prompt("go")])
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 409 and body["reason"] == "queue-unclear", body
    assert seat.keys() == []


def test_R1_an_id_nobody_minted_is_its_own_404_not_a_claim_about_the_queue(box, monkeypatch, tmp_path):
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)])
    status, body = _send_now(box, {"session": "seat", "message_id": "stq_" + "0" * 16})
    assert status == 404 and body["outcome"] == "unknown-id" and body["error"].endswith("nothing was sent."), body
    assert seat.keys() == []


@pytest.mark.parametrize("extra,actor", [({"source": "mail"}, None), ({}, "automation:router")])
def test_R1_mail_and_automation_steers_are_not_kept(box, monkeypatch, extra, actor):
    monkeypatch.setattr(G, "_agent_status", lambda: _Det("working", "claude"))
    monkeypatch.setattr(G, "verified_inject",
                        lambda *a, **k: (True, {"state": "working", "attempts": 1, "queued_mid_turn": True}))
    status, body = _post(box, {**PHONE, **extra}, actor=actor)
    assert status == 200 and "message_id" not in body, body
    assert not os.path.exists(SS.ledger_path(G.ORCH_DIR))


def test_R1_a_later_resend_of_the_same_words_is_never_sent_under_an_old_id(box, monkeypatch, tmp_path):
    t0 = time.time() - 600
    mid = SS.record(G.ORCH_DIR, "seat", WORDS, now=t0)
    rows = [enq(WORDS, t0 - 3), tool_result(t0 + 5), rem(WORDS, t0 + 5), enq(WORDS, t0 + 300)]
    # the re-send is drawn queued: identical words on screen cannot be told apart, so nothing is claimed or sent
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), rows)
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 409 and body["reason"] == "queue-unclear" and seat.keys() == [], body
    # and once the re-send is gone from the screen, the old id reads as what it is: already sent
    seat.screen = queued_pane()
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 200 and body["outcome"] == "already-sent" and seat.keys() == [], body


def test_R1_step_ended_counts_only_rows_after_the_keys():
    t = time.time()
    # an old ordinary tool result before the keys; the background row written AFTER the remove (order not pinned)
    rows = [prompt("go", t - 60), enq(WORDS, t - 30), tool_result(t - 20), rem(WORDS, t + 1), bg_result(t + 1)]
    assert Q.step_ended(rows, Q.find(rows, WORDS)["item"], since=t) == "backgrounded"
    rows = [prompt("go", t - 60), enq(WORDS, t - 30), tool_result(t - 20), rem(WORDS, t + 1)]
    assert Q.step_ended(rows, Q.find(rows, WORDS)["item"], since=t) == "unknown", "an old tool result is no evidence"
    # a dequeue with no prompt row after it yet: the new turn's rows are never read as the old turn's ending
    rows = [prompt("go", t - 60), enq(WORDS, t - 30), deq(t + 1), asst("end_turn", t + 2), turn_end(t + 2)]
    assert Q.step_ended(rows, Q.find(rows, WORDS)["item"], since=t) == "unknown"


def test_R1_the_real_composer_reader_reads_the_queued_layout(monkeypatch):
    real = G._load_agent_status_mod()

    class _DetReal(_Det):
        _find_chrome = staticmethod(real._find_chrome)
    monkeypatch.setattr(G, "_agent_status", lambda: _DetReal("working", "claude"))
    assert G._typed_on_prompt_line(queued_pane(WORDS)) == "", "the placeholder under a queue is not a draft"
    assert G._typed_on_prompt_line(queued_pane(WORDS, composer=DRAFT)) == "my own half-typed draft"


# ------------------------------------------------------------------ grok first pass (non-gating) @ 64815b69
def test_G2_two_enqueues_of_the_same_words_in_the_window_refuse_rather_than_guess(box, monkeypatch, tmp_path):
    t0 = time.time() - 30
    mid = SS.record(G.ORCH_DIR, "seat", "hello there", now=t0)
    seat = _seat(monkeypatch, tmp_path, queued_pane("hello there"),
                 [enq("hello there", t0 - 2), enq("hello there", t0 + 3), deq(t0 + 4)])
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 409 and body["reason"] == "queue-unclear", body
    assert seat.keys() == []


def test_G3_a_screen_row_cut_short_never_matches_by_prefix(box, monkeypatch, tmp_path):
    mid = _steered("Please send the report")
    seat = _seat(monkeypatch, tmp_path, queued_pane("Please send…"), [enq("Please send the report")])
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 409 and body["reason"] == "queue-unclear", body
    assert seat.keys() == []


def test_G5_words_match_with_their_spaces():
    assert Q.same_text("look  at\nthe test", "look at the test")
    assert not Q.same_text("to get her", "together")


# ------------------------------------------------------------------ review r2 (MERGE-OK @ 0bfe3fda, non-blocking folds)
def test_N1_already_sent_needs_a_screen_with_nothing_queued_not_just_no_exact_match(box, monkeypatch, tmp_path):
    # the CLI wrapped the message mid-word, so the screen row is not an exact match; the rows read as left
    mid = _steered("see https://example.test/averyveryverylongpath now")
    seat = _seat(monkeypatch, tmp_path, queued_pane("see https://example.test/averyvery verylongpath now"),
                 [enq("see https://example.test/averyveryverylongpath now"), deq()])
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 409 and body["reason"] == "queue-unclear", body
    assert seat.keys() == [] and SS.get(G.ORCH_DIR, mid) is not None, "the row is kept: send-now can be asked again"


def test_N2_a_remove_for_another_reason_is_not_already_sent(box, monkeypatch, tmp_path):
    # only absorbed_mid_turn was measured as delivery; any other remove (a message pulled back to edit) is not
    mid = _steered()
    edited = {**rem(WORDS), "reason": "edited"}
    seat = _seat(monkeypatch, tmp_path, queued_pane(), [enq(WORDS), edited])
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 200 and body["outcome"] == "not-queued", body
    assert seat.keys() == []


# ------------------------------------------------------------------ review r3 (MERGE-OK @ 7981b4a8, non-blocking F1/F2)
def test_F2_a_screen_with_no_queue_and_no_hint_reads_as_empty(box, monkeypatch, tmp_path):
    # measured: the hint is drawn under a queue block, so a real empty queue draws no hint at all
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(hint=False), [enq(WORDS), tool_result(), rem(WORDS)])
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 200 and body["outcome"] == "already-sent", body
    assert seat.keys() == []


def test_F1_a_screen_that_cannot_be_read_is_no_evidence_the_queue_is_empty(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(), [enq(WORDS), deq()])
    real = seat.__call__

    def t(*args, timeout=5):
        if args[0] == "capture-pane":
            seat.calls.append(args)
            return types.SimpleNamespace(returncode=1, stdout="")
        return real(*args, timeout=timeout)
    monkeypatch.setattr(G, "_tmux", t)
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 409 and body["reason"] == "queue-unclear", body
    assert seat.keys() == [] and SS.get(G.ORCH_DIR, mid) is not None


def test_F1_a_dialog_over_the_queue_is_no_evidence_either(box, monkeypatch, tmp_path):
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, MENU_SCREEN, [enq(WORDS), deq()])
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 409 and body["reason"] == "prompt-open", body
    assert seat.keys() == [] and SS.get(G.ORCH_DIR, mid) is not None


# ------------------------------------------------------------------ Q: Shaw 11-28 (gm msg_13e477af): send now submits ALL queued
# "I can't Send now with multiple messages queued?" The chord submits the whole queue (measured), like Esc in Claude
# Code: with other messages queued it sends them all, in queue order, instead of refusing other-queued. Every other
# id that went is named in also_submitted and marked sent, so a later click on any of them answers already-sent and
# never presses the chord again.
def _two_queued(monkeypatch, tmp_path, first=OTHER, second=WORDS, on_chord="both", screen=None):
    t0 = time.time()
    a = SS.record(G.ORCH_DIR, "seat", first, now=t0 - 10)
    b = SS.record(G.ORCH_DIR, "seat", second, now=t0 - 2)
    # measured shape while a command runs: the command goes to the background and each queued message is absorbed
    chord = absorbed(lambda: [rem(first), rem(second), bg_result()]) if on_chord == "both" else on_chord
    seat = _seat(monkeypatch, tmp_path, screen if screen is not None else queued_pane(first, second),
                 [prompt("run the suite"), enq(first, t0 - 11), enq(second, t0 - 3)], on_chord=chord)
    return a, b, seat


@pytest.mark.parametrize("click", ["second", "first"])
def test_Q1_two_queued_send_now_submits_both_and_names_the_other(box, monkeypatch, tmp_path, click):
    a, b, seat = _two_queued(monkeypatch, tmp_path)
    mid, other = (b, a) if click == "second" else (a, b)
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 200 and body["outcome"] == "delivered-now", body
    assert body["message_id"] == mid and body["also_submitted"] == [other], body
    assert body["also_submitted_kinds"] == [] and body["step_ended"] == "backgrounded"
    assert seat.keys() == ["C-x", "C-s"], "one chord for the whole queue"
    for m in (a, b):
        assert SS.get(G.ORCH_DIR, m)["sent_at"], "every message that went is marked sent"


def test_Q1_a_later_click_on_the_other_answers_already_sent_and_never_chords_again(box, monkeypatch, tmp_path):
    a, b, seat = _two_queued(monkeypatch, tmp_path)
    _send_now(box, {"session": "seat", "message_id": b})
    seat.screen = queued_pane()                     # nothing queued any more
    for m in (a, b):
        status, body = _send_now(box, {"session": "seat", "message_id": m})
        assert status == 200 and body["outcome"] == "already-sent" and body["message_id"] == m, body
    assert seat.keys().count("C-s") == 1


def test_Q2_a_queued_message_with_no_id_here_goes_too_and_is_named_by_kind(box, monkeypatch, tmp_path):
    # another agent's mail typed into the turn, or words typed at the terminal: no ledger row, still in the queue;
    # the chord cannot leave it behind, so it goes too and the answer says so
    mid = _steered()
    seat = _seat(monkeypatch, tmp_path, queued_pane(OTHER, WORDS), [enq(OTHER), enq(WORDS)],
                 on_chord=absorbed(lambda: [rem(OTHER), rem(WORDS), bg_result()]))
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 200 and body["outcome"] == "delivered-now", body
    assert body["also_submitted"] == [] and body["also_submitted_kinds"] == ["message"], body
    assert seat.keys() == ["C-x", "C-s"]


@pytest.mark.parametrize("screen", [queued_pane(WORDS),            # the screen shows one, the transcript two
                                    queued_pane(WORDS, OTHER),     # both, in the wrong order
                                    queued_pane(OTHER, WORDS, "a third")],
                         ids=["one-of-two-shown", "wrong-order", "a-third-shown"])
def test_Q3_the_screen_must_show_exactly_the_queue_in_order(box, monkeypatch, tmp_path, screen):
    a, b, seat = _two_queued(monkeypatch, tmp_path, screen=screen)
    status, body = _send_now(box, {"session": "seat", "message_id": b})
    assert status == 409 and body["reason"] == "queue-unclear", body
    assert seat.keys() == [] and not SS.get(G.ORCH_DIR, a).get("chorded_at")


def test_Q4_a_leaving_not_seen_marks_every_queued_id_so_no_click_chords_twice(box, monkeypatch, tmp_path):
    a, b, seat = _two_queued(monkeypatch, tmp_path, on_chord=None)
    status, body = _send_now(box, {"session": "seat", "message_id": b})
    assert status == 502 and body["state"] == "unknown", body
    status, body = _send_now(box, {"session": "seat", "message_id": a})
    assert status == 502 and body["state"] == "unknown", body
    assert seat.keys().count("C-s") == 1, "a click on the other message sent the chord again"


def test_Q5_a_refusal_with_two_queued_marks_nothing(box, monkeypatch, tmp_path):
    a, b, seat = _two_queued(monkeypatch, tmp_path, screen=queued_pane(OTHER, WORDS, composer=DRAFT))
    status, body = _send_now(box, {"session": "seat", "message_id": b})
    assert status == 423 and body["reason"] == "draft-in-composer", body
    assert seat.keys() == [] and not any(SS.get(G.ORCH_DIR, m).get("chorded_at") for m in (a, b))


def test_Q6_the_other_queued_refusal_is_gone():
    assert "other-queued" not in G.SEND_NOW_REFUSALS


def test_Q7_a_row_marked_sent_answers_already_sent_without_reading_the_pane(box, monkeypatch, tmp_path):
    mid = _steered()
    SS.mark_sent(G.ORCH_DIR, [mid])
    seat = _seat(monkeypatch, tmp_path, queued_pane(WORDS), [enq(WORDS)])
    monkeypatch.setattr(G, "_seat_transcript_path", lambda sess, meta=None: None)
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 200 and body["outcome"] == "already-sent", body
    assert seat.keys() == []


def test_Q8_an_old_row_with_the_same_words_is_never_taken_for_a_queued_message(box, monkeypatch, tmp_path):
    t0 = time.time()
    stale = SS.record(G.ORCH_DIR, "seat", OTHER, now=t0 - 3600)    # the same words sent an hour ago
    mid = SS.record(G.ORCH_DIR, "seat", WORDS, now=t0 - 2)
    seat = _seat(monkeypatch, tmp_path, queued_pane(OTHER, WORDS), [enq(OTHER, t0 - 20), enq(WORDS, t0 - 3)],
                 on_chord=absorbed(lambda: [rem(OTHER), rem(WORDS), bg_result()]))
    status, body = _send_now(box, {"session": "seat", "message_id": mid})
    assert status == 200 and body["also_submitted"] == [] and body["also_submitted_kinds"] == ["message"], body
    assert not SS.get(G.ORCH_DIR, stale).get("sent_at") and not SS.get(G.ORCH_DIR, stale).get("chorded_at")


def test_Q9_another_queued_id_an_earlier_chord_marked_is_unknown_never_the_keys_again(box, monkeypatch, tmp_path):
    a, b, seat = _two_queued(monkeypatch, tmp_path)
    SS.mark_chorded(G.ORCH_DIR, [a])                # an earlier send-now for it pressed the keys and could not confirm
    status, body = _send_now(box, {"session": "seat", "message_id": b})
    assert status == 502 and body["state"] == "unknown", body
    assert seat.keys() == []


# ------------------------------------------------------------------ codex r1 (BLOCK @ aa527801)
def test_C1_two_unsent_rows_that_could_be_the_other_queued_message_refuse_rather_than_guess(box, monkeypatch, tmp_path):
    t0 = time.time()
    earlier = SS.record(G.ORCH_DIR, "seat", OTHER, now=t0 - 12)   # the same words, sent seconds before, never clicked
    a, b, seat = _two_queued(monkeypatch, tmp_path)               # OTHER's own row at t0 - 10, enqueue at t0 - 11
    status, body = _send_now(box, {"session": "seat", "message_id": b})
    assert status == 409 and body["reason"] == "queue-unclear", body
    assert seat.keys() == [] and not any(SS.get(G.ORCH_DIR, m).get("chorded_at") for m in (earlier, a, b))


def test_C2_the_answer_waits_until_every_queued_message_has_left(box, monkeypatch, tmp_path):
    # the transcript shows the clicked message leave one read before the other one does
    a, b, seat = _two_queued(monkeypatch, tmp_path, on_chord=absorbed(lambda: [rem(WORDS)]))
    real, n = Q.read_rows, {"after": 0}

    def reads(path, *aa, **kk):
        if seat.keys().count("C-s"):
            n["after"] += 1
            if n["after"] == 2:
                seat.write([rem(OTHER), bg_result()])
        return real(path, *aa, **kk)
    monkeypatch.setattr(Q, "read_rows", reads)
    status, body = _send_now(box, {"session": "seat", "message_id": b})
    assert status == 200 and body["also_submitted"] == [a], body
    assert SS.get(G.ORCH_DIR, a)["sent_at"]


def test_Q4b_after_an_unconfirmed_chord_the_other_message_never_gets_the_keys_even_once_its_neighbour_left(
        box, monkeypatch, tmp_path):
    a, b, seat = _two_queued(monkeypatch, tmp_path, on_chord=None)
    status, body = _send_now(box, {"session": "seat", "message_id": b})
    assert status == 502 and body["state"] == "unknown", body
    seat.write([rem(WORDS)])                         # the clicked one is seen leaving later; the other is still queued
    seat.screen = queued_pane(OTHER)
    status, body = _send_now(box, {"session": "seat", "message_id": a})
    assert status == 502 and body["state"] == "unknown", body
    assert seat.keys().count("C-s") == 1, "the other message got the keys a second time"
