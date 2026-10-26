"""Work handed to an agent is a drawer job until it answers."""
import threading

from services.arturo import agent_watch as w
from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture

ORIG = {"id": "msg_aaaaaaaa_1", "from_agent": "arturo", "to_agent": "gm", "status": "pending",
        "subject": "[unverified caller] Rename first-project team to Acme", "conversation_id": "conv_1",
        "created_at": "2026-10-24T16:58:44+00:00"}


class Store:
    def __init__(self, script):
        self.script, self.polls, self.archived = script, 0, []
        self.status = "pending"

    def get(self, mid):
        return {**ORIG, "status": self.status} if mid == ORIG["id"] else None

    def query(self, **k):
        self.polls += 1
        self.status = "acknowledged"
        return [r for at, r in self.script if at <= self.polls]

    def archive(self, mid):
        self.archived.append(mid)


def _reply(mid, subject, body, at="2026-10-24T17:00:00+00:00", conv="conv_x"):
    return {"id": mid, "from_agent": "gm", "to_agent": "arturo", "subject": subject, "body": body,
            "conversation_id": conv, "created_at": at}


def test_the_job_ends_on_the_agents_final_answer_not_its_interim_one():
    store = Store([(1, _reply("r1", "re: Rename first-project team to Acme — carded, blocker inside",
                              "NOT DONE — carded for the owner.")),
                   (2, _reply("r2", "re: Rename first-project team to Acme — DONE", "DONE. pm-acme ...",
                              at="2026-10-24T17:05:00+00:00"))])
    steps = []
    text, ok = w.watch(store, ORIG["id"], steps.append, threading.Event(), sleep=lambda s: None)
    assert ok and text.startswith("DONE. pm-acme")
    names = [s["name"] for s in steps]
    assert names[0] == "message to gm" and "gm is working on it" in names and names.count("gm replied") == 4
    assert store.archived == ["r1", "r2"]


def test_another_agents_message_or_an_older_one_is_not_its_answer():
    other = {**_reply("r3", "re: Rename first-project team", "DONE"), "from_agent": "pm-acme"}
    older = _reply("r4", "re: Rename first-project team", "DONE", at="2026-10-24T16:00:00+00:00")
    unrelated = _reply("r5", "re: Spawn a QA agent", "DONE")
    for msg in (other, older, unrelated):
        assert not w.is_reply(ORIG, msg)
    assert w.is_reply(ORIG, _reply("r6", "anything", "DONE", conv="conv_1"))     # same conversation


def test_no_answer_in_time_says_so():
    t = [0.0]
    store = Store([])
    text, ok = w.watch(store, ORIG["id"], lambda e: None, threading.Event(), clock=lambda: t[0],
                       sleep=lambda s: t.__setitem__(0, t[0] + 3600), timeout_s=7200)
    assert not ok and "No answer from gm yet" in text


def test_a_message_from_a_conversation_becomes_a_drawer_job(P, monkeypatch):
    started = []
    monkeypatch.setattr(P, "_current_thread", lambda: "web_handoff")
    monkeypatch.setattr(P._JOBS, "start", lambda thread, kind, title, work, cancel=None:
                        started.append((thread, kind, title)) or {"id": "job_w"})
    out = P._watch_agent_work("agent_message", {"to_agent": "gm", "subject": "[unverified caller] Rename the seats"},
                              "Message sent to gm: [msg_63cf04a8_61124720] Rename the seats. Router will deliver.")
    assert started == [("web_handoff", "agent", "gm: Rename the seats")] and "job_w" in out
    assert P._watch_agent_work("agent_message", {"to_agent": "gm"}, "Failed to send message: x") == "Failed to send message: x"
