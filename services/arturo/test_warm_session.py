"""RED-first for warm CLI sessions.

Measured on staging before writing a line (claude 2.1.284, ~29KB system prompt, 5 runs each):
a fresh `claude -p` per turn reached first token at a median 2.64s; one long-lived process fed
turns over `--input-format stream-json` reached it at 1.64s, with every turn a full prompt-cache
hit (~9.4k cached tokens, 2 uncached). The process start is real cost; so is re-reading a
29KB prompt that could have been cached.

The things that make a warm session WRONG rather than slow, each pinned here:
 - a session serving two conversations would leak one into the other;
 - the CLI keeps its own history, so re-sending ours would double it;
 - a turn that fell back to the tool loop leaves the session's memory out of step with the
   real conversation, so that session must not be reused;
 - a dead or hung process must not take the next turn down with it.
"""
import json
import threading

import pytest

from services.arturo.warm_session import WarmPool, WarmSession


class FakeProc:
    """A CLI that answers each NDJSON user line with a canned stream, then a result."""

    def __init__(self, replies=None, die_after=None):
        self.written = []
        self._out = []
        self._cv = threading.Condition()
        self.replies = list(replies or ["ok"])
        self.die_after = die_after
        self.killed = False
        self.returncode = None
        self.stdin = self
        self.stdout = self

    # stdin side
    def write(self, s):
        self.written.append(s)
        with self._cv:
            if self.die_after is not None and len(self.written) > self.die_after:
                self.returncode = 1
                self._out.append(None)
            else:
                text = self.replies.pop(0) if self.replies else "ok"
                self._out.append(json.dumps({"type": "stream_event", "event": {
                    "type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}}) + "\n")
                self._out.append(json.dumps({"type": "result", "is_error": False}) + "\n")
            self._cv.notify_all()

    def flush(self):
        pass

    # stdout side
    def readline(self):
        with self._cv:
            while not self._out:
                if self.killed:
                    return ""
                self._cv.wait(timeout=1)
            line = self._out.pop(0)
            return "" if line is None else line

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9
        with self._cv:
            self._cv.notify_all()

    def wait(self, timeout=None):
        return self.returncode


def spawner(procs):
    """Hand out FakeProcs in order and remember how many were started."""
    started = []

    def spawn(argv, env=None):
        p = procs.pop(0) if procs else FakeProc()
        started.append((argv, p))
        return p
    spawn.started = started
    return spawn


def text_of(lines):
    out = []
    for line in lines:
        d = json.loads(line)
        if d.get("type") == "stream_event":
            out.append(d["event"]["delta"]["text"])
    return "".join(out)


# ---- one process, many turns ---------------------------------------------------------

def test_two_turns_in_one_conversation_reuse_ONE_process():
    spawn = spawner([FakeProc(replies=["first", "second"])])
    pool = WarmPool(spawn=spawn, argv_for=lambda key: ["claude"], clock=lambda: 0)
    assert text_of(pool.turn(("c1", "claude", ""), "hi")) == "first"
    assert text_of(pool.turn(("c1", "claude", ""), "again")) == "second"
    assert len(spawn.started) == 1, "the whole point: no new process per turn"


def test_only_the_NEW_message_is_sent_the_cli_keeps_its_own_history():
    proc = FakeProc(replies=["a", "b"])
    pool = WarmPool(spawn=spawner([proc]), argv_for=lambda key: ["claude"], clock=lambda: 0)
    list(pool.turn(("c1", "claude", ""), "hello"))
    list(pool.turn(("c1", "claude", ""), "and then"))
    sent = [json.loads(w)["message"]["content"][0]["text"] for w in proc.written]
    assert sent == ["hello", "and then"], "re-sending the transcript would double the history"


def test_two_conversations_never_share_a_process():
    spawn = spawner([FakeProc(replies=["one"]), FakeProc(replies=["two"])])
    pool = WarmPool(spawn=spawn, argv_for=lambda key: ["claude"], clock=lambda: 0)
    list(pool.turn(("c1", "claude", ""), "hi"))
    list(pool.turn(("c2", "claude", ""), "hi"))
    assert len(spawn.started) == 2
    assert spawn.started[0][1] is not spawn.started[1][1]


def test_a_different_model_is_a_different_session():
    spawn = spawner([FakeProc(), FakeProc()])
    pool = WarmPool(spawn=spawn, argv_for=lambda key: ["claude", key[2]], clock=lambda: 0)
    list(pool.turn(("c1", "claude", "opus"), "hi"))
    list(pool.turn(("c1", "claude", "haiku"), "hi"))
    assert len(spawn.started) == 2


# ---- lifecycle -----------------------------------------------------------------------

def test_discard_kills_the_session_and_the_next_turn_starts_fresh():
    """After a tool-loop fallback the CLI's memory no longer matches the conversation."""
    first, second = FakeProc(), FakeProc()
    spawn = spawner([first, second])
    pool = WarmPool(spawn=spawn, argv_for=lambda key: ["claude"], clock=lambda: 0)
    list(pool.turn(("c1", "claude", ""), "hi"))
    pool.discard(("c1", "claude", ""))
    assert first.killed
    list(pool.turn(("c1", "claude", ""), "hi"))
    assert len(spawn.started) == 2


def test_a_process_that_died_is_replaced_not_reused():
    dead = FakeProc(die_after=1)
    spawn = spawner([dead, FakeProc(replies=["recovered"])])
    pool = WarmPool(spawn=spawn, argv_for=lambda key: ["claude"], clock=lambda: 0)
    list(pool.turn(("c1", "claude", ""), "one"))
    dead.returncode = 1                       # it exited between turns
    assert text_of(pool.turn(("c1", "claude", ""), "two")) == "recovered"
    assert len(spawn.started) == 2


def test_an_idle_session_is_evicted_after_its_ttl():
    now = [0.0]
    first = FakeProc()
    spawn = spawner([first, FakeProc()])
    pool = WarmPool(spawn=spawn, argv_for=lambda key: ["claude"], clock=lambda: now[0], idle_ttl_s=300)
    list(pool.turn(("c1", "claude", ""), "hi"))
    now[0] = 301
    pool.evict_idle()
    assert first.killed, "an idle process is memory held for nobody"


def test_a_STALE_session_is_restarted_so_fleet_state_is_not_frozen():
    """The system prompt carries live fleet state and is fixed at spawn."""
    now = [0.0]
    spawn = spawner([FakeProc(), FakeProc()])
    pool = WarmPool(spawn=spawn, argv_for=lambda key: ["claude"], clock=lambda: now[0], max_age_s=600)
    list(pool.turn(("c1", "claude", ""), "hi"))
    now[0] = 601
    list(pool.turn(("c1", "claude", ""), "hi"))
    assert len(spawn.started) == 2


def test_the_pool_is_bounded_and_evicts_the_least_recently_used():
    now = [0.0]
    procs = [FakeProc() for _ in range(3)]
    spawn = spawner(list(procs))
    pool = WarmPool(spawn=spawn, argv_for=lambda key: ["claude"], clock=lambda: now[0], max_sessions=2)
    for i, cid in enumerate(("c1", "c2", "c3")):
        now[0] = float(i)
        list(pool.turn((cid, "claude", ""), "hi"))
    assert procs[0].killed, "the oldest session goes when a third arrives"
    assert not procs[1].killed and not procs[2].killed


def test_close_all_kills_every_process():
    procs = [FakeProc(), FakeProc()]
    pool = WarmPool(spawn=spawner(list(procs)), argv_for=lambda key: ["claude"], clock=lambda: 0)
    list(pool.turn(("c1", "claude", ""), "hi"))
    list(pool.turn(("c2", "claude", ""), "hi"))
    pool.close_all()
    assert all(p.killed for p in procs)


# ---- a session serves one turn at a time --------------------------------------------

def test_a_session_serves_one_turn_at_a_time():
    s = WarmSession(FakeProc(replies=["x"]), clock=lambda: 0)
    assert s.lock.acquire(blocking=False)
    s.lock.release()


# ---- found live: "the third response never populates" ---------------------------------
# read_events stops pulling lines the moment it sees a turn's `result`, so the pool's turn
# generator is never resumed past that line. The session lock was released only when the
# generator exited, i.e. whenever Python got round to collecting it, so a later turn on the
# same conversation could wait on the lock forever.

def _consume_like_read_events(it):
    """Pull lines until the result line, then STOP, holding a reference, never closing."""
    out = []
    for line in it:
        out.append(line)
        if json.loads(line).get("type") == "result":
            break
    return out


def test_a_consumer_that_stops_at_the_result_does_not_hold_the_session():
    proc = FakeProc(replies=["one", "two", "three"])
    pool = WarmPool(spawn=spawner([proc]), argv_for=lambda key: ["claude"], clock=lambda: 0)
    key = ("c1", "claude", "")
    held = []                                  # keep every generator alive, like a live frame would
    for n in range(3):
        it = pool.turn(key, f"turn {n}")
        held.append(it)
        done = threading.Event()
        result = {}

        def run():
            result["lines"] = _consume_like_read_events(it)
            done.set()

        threading.Thread(target=run, daemon=True).start()
        assert done.wait(timeout=3), f"turn {n + 1} hung waiting on the previous turn's lock"
        assert text_of(result["lines"]) == ["one", "two", "three"][n]


def test_a_turn_ABANDONED_mid_stream_discards_its_session():
    """If a turn is dropped before its result, the rest of that turn's output is still in the
    pipe; the next turn on that process would read the previous answer as its own."""
    first = FakeProc(replies=["unfinished"])
    second = FakeProc(replies=["fresh"])
    spawn = spawner([first, second])
    pool = WarmPool(spawn=spawn, argv_for=lambda key: ["claude"], clock=lambda: 0)
    key = ("c1", "claude", "")
    it = pool.turn(key, "hi")
    next(it)                                   # read the first delta, then walk away
    it.close()                                 # the client disconnected
    assert first.killed, "a half-read process must not be reused"
    assert text_of(pool.turn(key, "again")) == "fresh"
    assert len(spawn.started) == 2


# ---- prewarm: the first turn should not pay for the spawn -------------------------------

def test_prewarm_starts_the_process_and_the_first_turn_reuses_it():
    spawn = spawner([FakeProc(replies=["hello"])])
    pool = WarmPool(spawn=spawn, argv_for=lambda key: ["claude"], clock=lambda: 0)
    key = ("c1", "claude", "")
    assert pool.prewarm(key, ["claude", "--system-prompt", "S"]) is True
    assert text_of(pool.turn(key, "hi")) == "hello"
    assert len(spawn.started) == 1, "the turn must adopt the prewarmed process, not start another"


def test_prewarming_twice_does_not_start_a_second_process():
    spawn = spawner([FakeProc(), FakeProc()])
    pool = WarmPool(spawn=spawn, argv_for=lambda key: ["claude"], clock=lambda: 0)
    key = ("c1", "claude", "")
    assert pool.prewarm(key, ["claude"]) is True
    assert pool.prewarm(key, ["claude"]) is False
    assert len(spawn.started) == 1


def test_a_prewarmed_session_left_idle_is_still_evicted():
    now = [0.0]
    proc = FakeProc()
    pool = WarmPool(spawn=spawner([proc]), argv_for=lambda key: ["claude"],
                    clock=lambda: now[0], idle_ttl_s=300)
    pool.prewarm(("c1", "claude", ""), ["claude"])
    now[0] = 301
    pool.evict_idle()
    assert proc.killed, "a tab opened and abandoned must not hold a process forever"
