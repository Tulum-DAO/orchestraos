"""Warm CLI sessions — one live process per conversation, instead of one per turn.

Measured on staging (claude 2.1.284, ~29KB system prompt, medians of 5): a fresh `claude -p`
per turn reached its first token at 2.64s; one long-lived process fed over
`--input-format stream-json` reached it at 1.64s, and every warm turn was a full prompt-cache
hit (~9.4k cached input tokens, 2 uncached). So a warm session removes the process start AND
stops re-reading a prompt the API could have cached.

What makes a warm session WRONG rather than merely slow, and what this module does about it:

* **One conversation per process.** The CLI keeps its own history. A process shared between
  conversations would answer one operator with another's context. Sessions are keyed by
  (conversation_id, provider, model).
* **Send only the new message.** The CLI already remembers the earlier turns; sending our
  transcript as well would double it.
* **Discard after a divergence.** When a turn falls back to the tool loop, the real answer is
  produced elsewhere and this process's memory no longer matches the conversation. The caller
  discards the session; the next turn starts a fresh one seeded from the thread store.
* **Staleness is bounded.** The system prompt carries live fleet state and is fixed at spawn,
  so a session older than `max_age_s` is restarted rather than answering from a frozen view.
* **Bounded memory.** Idle sessions are evicted after `idle_ttl_s`, and the pool holds at most
  `max_sessions` processes, dropping the least recently used.
* **A dead process is replaced, never reused.**
* **A session behind its conversation is replaced.** A turn answered anywhere else — another
  model, the `/text` path, a fallback — never reaches this process, and one the operator switches
  back to would answer from a conversation missing those turns. Each session carries the
  conversation VERSION it is in step with (the archive's turn count: it only grows and survives
  restarts); a turn that brings a different version gets a fresh process seeded from history.
"""
import json
import threading
import time
from typing import Callable, Dict, Iterator, Optional, Tuple

Key = Tuple[str, str, str]   # (conversation_id, provider, model)


class WarmSession:
    """One live CLI process. Serves one turn at a time."""

    def __init__(self, proc, clock: Callable[[], float] = time.monotonic):
        self.proc = proc
        self.lock = threading.Lock()
        self._clock = clock
        self.started_at = clock()
        self.last_used = self.started_at
        #: The conversation version this process has seen all of. None = unknown (a turn is in
        #: flight, or finished without being recorded), which no caller's version matches.
        self.version = None

    def alive(self) -> bool:
        return self.proc.poll() is None

    def age(self) -> float:
        return self._clock() - self.started_at

    def idle_for(self) -> float:
        return self._clock() - self.last_used

    def turn(self, text: str) -> Iterator[str]:
        """Write one user message; yield stdout lines through that turn's `result` event."""
        msg = {"type": "user", "message": {"role": "user",
                                           "content": [{"type": "text", "text": text}]}}
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        self.last_used = self._clock()
        for line in iter(self.proc.stdout.readline, ""):
            yield line
            if _is_turn_end(line):
                self.last_used = self._clock()
                return
        # stdout closed before the turn ended: the process is gone.

    def kill(self):
        try:
            self.proc.kill()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            pass


def _is_turn_end(line: str) -> bool:
    line = line.strip()
    if not line.startswith("{"):
        return False
    try:
        return json.loads(line).get("type") == "result"
    except Exception:
        return False


class WarmPool:
    """Warm sessions by (conversation, provider, model), with eviction and staleness."""

    def __init__(self, spawn: Callable, argv_for: Callable[[Key], list],
                 clock: Callable[[], float] = time.monotonic,
                 idle_ttl_s: float = 300.0, max_age_s: float = 600.0, max_sessions: int = 8,
                 env_for: Optional[Callable[[Key], dict]] = None):
        self._spawn = spawn
        self._argv_for = argv_for
        self._env_for = env_for
        self._clock = clock
        self.idle_ttl_s = idle_ttl_s
        self.max_age_s = max_age_s
        self.max_sessions = max_sessions
        self._sessions: Dict[Key, WarmSession] = {}
        self._lock = threading.Lock()

    def _get(self, key: Key, argv: Optional[list] = None, env: Optional[dict] = None,
             version: Optional[int] = None) -> Tuple[WarmSession, bool]:
        """(session, fresh). A dead, stale or out-of-step session is replaced; a fresh one is in
        step with `version`, because it is seeded from the history that version describes.
        `version=None` checks nothing (a later tool round of a turn already checked)."""
        with self._lock:
            s = self._sessions.get(key)
            # A session serving a turn is never replaced from under it: its version is None
            # only because that turn has not been recorded yet. turn() re-checks once it has
            # the lock; a prewarm simply leaves it be.
            behind = (version is not None and s is not None and s.version != version
                      and not s.lock.locked())
            if s is not None and (not s.alive() or s.age() > self.max_age_s or behind):
                s.kill()
                del self._sessions[key]
                s = None
            if s is not None:
                return s, False
            self._make_room()
            if env is None and self._env_for:
                env = self._env_for(key)
            proc = self._spawn(argv if argv is not None else self._argv_for(key), env=env)
            s = WarmSession(proc, clock=self._clock)
            s.version = version
            self._sessions[key] = s
            return s, True

    def _make_room(self):
        while len(self._sessions) >= self.max_sessions:
            oldest = min(self._sessions, key=lambda k: self._sessions[k].last_used)
            self._sessions.pop(oldest).kill()

    def has(self, key: Key) -> bool:
        with self._lock:
            s = self._sessions.get(key)
            return bool(s and s.alive() and s.age() <= self.max_age_s)

    def turn(self, key: Key, text: str, argv: Optional[list] = None,
             env: Optional[dict] = None, version: Optional[int] = None) -> Iterator[str]:
        """`argv` is used only if this turn has to START a process — the system prompt (with
        the conversation so far) is fixed at spawn, and a live session already has it.

        The session is released the moment the turn's `result` line arrives, NOT when this
        generator exits. The reader downstream stops pulling at the result, so waiting for the
        generator to finish meant waiting for garbage collection: a later turn on the same
        conversation could wait on the lock forever ("the third response never populates",
        2026-09-30). And a turn dropped BEFORE its result leaves the rest of its answer in the
        pipe, where the next turn would read it as its own — so that session is discarded.
        """
        self.evict_idle()
        session, _fresh = self._get(key, argv=argv, env=env, version=version)
        session.lock.acquire()
        if version is not None and session.version != version:
            # It was busy when we looked, and the turn we waited on left it out of step.
            session.lock.release()
            with self._lock:
                if self._sessions.get(key) is session:
                    del self._sessions[key]
            session.kill()
            session, _fresh = self._get(key, argv=argv, env=env, version=version)
            session.lock.acquire()
        # From here the process holds a turn the archive does not have yet; sync() stamps it
        # once the turn is recorded. A turn that is never recorded leaves it unusable.
        session.version = None
        state = {"released": False, "finished": False}

        def release():
            if not state["released"]:
                state["released"] = True
                session.last_used = self._clock()
                session.lock.release()

        try:
            for line in session.turn(text):
                if _is_turn_end(line):
                    state["finished"] = True
                    release()              # the turn is over the instant its result exists
                yield line
                if state["finished"]:
                    return
        finally:
            if not state["finished"]:
                # Abandoned mid-turn, or the process died: whatever it still has to say
                # belongs to a turn nobody is reading. Never hand it to the next one.
                with self._lock:
                    if self._sessions.get(key) is session:
                        del self._sessions[key]
                session.kill()
            release()

    def prewarm(self, key: Key, argv: list, env: Optional[dict] = None,
                version: Optional[int] = None) -> bool:
        """Start the conversation's process before its first message, so the first turn does
        not pay for the spawn. Returns True if a process was started, False if one was live.
        `version` must be read BEFORE the history `argv` was built from: a turn recorded in
        between then leaves this session behind, and the turn replaces it."""
        self.evict_idle()
        _session, fresh = self._get(key, argv=argv, env=env, version=version)
        return fresh

    def sync(self, key: Key, version: int):
        """The turn this session just served is recorded: it is in step with `version`."""
        with self._lock:
            s = self._sessions.get(key)
            if s is not None:
                s.version = version

    def discard(self, key: Key):
        with self._lock:
            s = self._sessions.pop(key, None)
        if s is not None:
            s.kill()

    def evict_idle(self):
        with self._lock:
            stale = [k for k, s in self._sessions.items()
                     if s.idle_for() > self.idle_ttl_s and not s.lock.locked()]
            for k in stale:
                self._sessions.pop(k).kill()

    def close_all(self):
        with self._lock:
            sessions, self._sessions = list(self._sessions.values()), {}
        for s in sessions:
            s.kill()
