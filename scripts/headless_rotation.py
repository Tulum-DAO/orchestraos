#!/usr/bin/env python3
"""headless_rotation.py: the steps of a headless seat's rotation, which scripts/seat_supervisor.py runs.

A headless seat rotates to a fresh session without leaving its supervisor or its pane:
  1. trigger   context in use at or over the seat's threshold (75% unless set), or a manual `rotate`; it only ever
               STARTS at a turn boundary (no turn running, no held request, nothing queued), and the 90% ceiling
               still waits for one. Automatic rotation is OFF per seat until "rotation": true is set.
  2. export    the outgoing session's transcript into the conversation index (scripts/episodic_memory.py), then a
               token from its last turn is looked up there; not found = the rotation stops and the old session keeps
               serving.
  3. handoff   built from RECORDS, never from the model's own summary: open tasks (todo/task tool calls), decisions
               (answered cards, decision lines), pending cards (the approvals ledger), files touched (Write/Edit
               calls), each citing the message uuid or tool id it came from, in the lineage handoff schema
               (lineage_daemon/handoff_schema.py), plus the rule to look things up rather than recall them.
  4. warm-up   a second CLI process on a NEW session with the seat's flags, settings, model and environment, its side
               effects fenced (warm_env): the handoff is its first message.
  5. grade     the existing gate (rotation_gate_manual): canary questions built from the records, pointing into the
               old transcript, the warm session's read-back graded by focus_registry's comprehension check,
               fail-closed. A fail ends the warm process; the old session keeps serving; a retry is scheduled.
  6. promote   at the next boundary: the identity store mints generation N+1 with the new session (promote_identity),
               the supervisor's live process becomes the warm one, the old one is closed.

Everything here is a plain function (the supervisor owns processes and timing), so each step is tested on its own.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.append(_p)

import headless_switch as HS  # noqa: E402

DEFAULT_AT = 75.0
DEFAULT_CEILING = 90.0
#: First retry after a failed attempt; doubles up to BACKOFF_MAX_S.
BACKOFF_S = 300.0
BACKOFF_MAX_S = 3600.0
#: Items of each kind carried in a handoff (the newest); the index holds the rest.
KEEP = 12
#: Failed attempts in a row before a seat stops retrying by itself (a manual rotate starts the count again).
MAX_FAILURES = 5
#: The fault switch's values (test only): force a step to fail.
FAULTS = frozenset({"grade", "verify", "first_turn"})
WARMUP_FILE = "warmup.json"


# --- settings ------------------------------------------------------------------------------------------

def _num(v, default):
    try:
        x = float(v)
        return x if x > 0 else default
    except (TypeError, ValueError):
        return default


def settings(seat: str, orch_dir=None, env=os.environ) -> dict:
    """The seat's rotation settings from its headless-seats.json entry: "rotation" (automatic rotation on; OFF
    unless literally true), "rotation_at" and "rotation_ceiling" (percent of the context window). The environment
    overrides the numbers for a test (HEADLESS_ROTATE_AT, HEADLESS_ROTATE_CEILING, HEADLESS_ROTATE_BACKOFF_S)."""
    entry = HS.load_switch(orch_dir).get(seat)
    entry = entry if isinstance(entry, dict) else {}
    at = _num(env.get("HEADLESS_ROTATE_AT"), _num(entry.get("rotation_at"), DEFAULT_AT))
    ceiling = _num(env.get("HEADLESS_ROTATE_CEILING"), _num(entry.get("rotation_ceiling"), DEFAULT_CEILING))
    return {"enabled": entry.get("rotation") is True, "at": at, "ceiling": max(ceiling, at),
            "backoff_s": _num(env.get("HEADLESS_ROTATE_BACKOFF_S"), BACKOFF_S),
            "max_failures": int(_num(env.get("HEADLESS_ROTATE_MAX_FAILURES"), MAX_FAILURES))}


def context_pct(tokens, window) -> float | None:
    if not isinstance(tokens, (int, float)) or not isinstance(window, (int, float)) or window <= 0:
        return None
    return round(100.0 * tokens / window, 1)


def due(pct: float | None, conf: dict, manual: bool = False) -> str | None:
    """Why a rotation is due ('manual', 'ceiling', 'threshold'), or None. Automatic only when the seat's switch is
    on; the ceiling only names the urgency, it never skips the boundary."""
    if manual:
        return "manual"
    if not conf.get("enabled") or pct is None:
        return None
    if pct >= conf["ceiling"]:
        return "ceiling"
    if pct >= conf["at"]:
        return "threshold"
    return None


OPEN_MESSAGE_STATES = frozenset({"accepted", "queued", "delivered", "unknown"})


def at_boundary(state: str, turn_open: bool, held: int, message_states) -> bool:
    """A turn boundary: the process is idle (its last turn ended with a result), no turn is running, no request is
    held (no question or permission card waiting) and no message is on its way in."""
    return (state == "idle" and not turn_open and not held
            and not any(s in OPEN_MESSAGE_STATES for s in message_states))


def next_backoff(prev_s: float | None, base_s: float) -> float:
    return base_s if not prev_s else min(BACKOFF_MAX_S, prev_s * 2)


def fault(home: Path | None, env=os.environ) -> str | None:
    """The test-only fault switch: HEADLESS_ROTATION_FAULT=grade|verify, or (only when the supervisor runs with
    HEADLESS_ROTATION_TEST=1) the word in <seat dir>/rotation-fault, which a live proof can clear without a
    restart. A real seat never has HEADLESS_ROTATION_TEST, so a stray file there does nothing."""
    v = (env.get("HEADLESS_ROTATION_FAULT") or "").strip().lower()
    if not v and env.get("HEADLESS_ROTATION_TEST") == "1" and home is not None:
        try:
            v = (Path(home) / "rotation-fault").read_text().strip().lower()
        except OSError:
            v = ""
    return v if v in FAULTS else None


# --- reading a transcript ------------------------------------------------------------------------------

def read_lines(path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    o = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(o, dict):
                    out.append(o)
    except OSError:
        pass
    return out


def _blocks(o: dict) -> list:
    c = (o.get("message") or {}).get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return [b for b in c if isinstance(b, dict)] if isinstance(c, list) else []


def _text_of(o: dict) -> str:
    return "\n".join(str(b.get("text") or "") for b in _blocks(o) if b.get("type") == "text").strip()


def _is_operator_line(o: dict) -> bool:
    """A user line a person (or a message) wrote: text, not a tool result, not the harness's own."""
    if o.get("type") != "user" or o.get("isMeta") or o.get("isSidechain"):
        return False
    t = _text_of(o)
    return bool(t) and not t.startswith("<") and not any(b.get("type") == "tool_result" for b in _blocks(o))


def gate_text(d: dict) -> str:
    """A row's text exactly as rotation_gate_manual.resolve_pointer reads it (its `_text`): message content or a
    top-level `content` (system, attachment and queue rows count too), text, thinking or tool input, else
    payload.text. Turn numbers are counted over every row with this text, as the gate counts them."""
    payload = d.get("payload") if isinstance(d.get("payload"), dict) else {}
    msg = d.get("message") if isinstance(d.get("message"), dict) else payload
    c = msg.get("content") or d.get("content")
    if isinstance(c, str):
        return c
    out = []
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict):
                t = b.get("text") or b.get("thinking") or b.get("input") or ""
                if t:
                    out.append(str(t))
    elif isinstance(c, dict):
        t = c.get("text") or c.get("thinking") or ""
        if t:
            out.append(str(t))
    if not out and isinstance(payload.get("text"), str):
        out.append(payload["text"])
    return "\n".join(out)


def text_turns(lines: list[dict]) -> list[dict]:
    """The rows the gate's `turn-<n>` pointer counts (1-based): EVERY row with text by gate_text, whatever its type."""
    return [o for o in lines if gate_text(o).strip()]


_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{5,}")


def has_turn(path) -> bool:
    """Whether a conversation's transcript holds a user or assistant entry yet (no file: no)."""
    if not path:
        return False
    return any(o.get("type") in ("user", "assistant") and not o.get("isMeta") and not o.get("isSidechain")
               for o in read_lines(path))


def last_turn_token(lines: list[dict]) -> str | None:
    """A token from the outgoing session's last turn for the export check: the longest word of its last text the
    index will hold (a user or assistant text block), else its first 40 characters."""
    for o in reversed(lines):
        if o.get("type") not in ("user", "assistant") or o.get("isSidechain") or o.get("isMeta"):
            continue
        t = _text_of(o)
        if not t:
            continue
        words = sorted(_WORD.findall(t), key=len, reverse=True)
        return words[0] if words else t[:40]
    return None


# --- 2. export -----------------------------------------------------------------------------------------

def export_session(agent: str, session_id: str, generation, path, *, db_path=None, em=None,
                   force_fail: bool = False) -> dict:
    """Index the outgoing session, then look up a token from its last turn. Returns
    {"ok", "added", "token", "row"|"reason"}; ok False means the rotation must not go on."""
    if em is None:
        import episodic_memory as em
    got = em.ingest_session(agent, session_id, generation, path, db_path=db_path)
    token = last_turn_token(read_lines(path))
    if not token:
        return {"ok": False, "added": got.get("added", 0), "token": None, "reason": "no text in the last turn"}
    ok, row = (False, None) if force_fail else em.verify(agent, session_id, token, db_path=db_path)
    out = {"ok": bool(ok), "added": got.get("added", 0), "token": token}
    if ok:
        out["row"] = {k: (row or {}).get(k) for k in ("uuid", "session_id", "generation", "agent", "kind")}
    else:
        out["reason"] = "forced by the fault switch" if force_fail else "the last turn's token is not in the index"
    return out


# --- 3. handoff from records ---------------------------------------------------------------------------

TASK_TOOLS = ("TodoWrite", "TaskCreate", "TaskUpdate")
FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
DECISION_LINE = re.compile(r"^\s*\[(?:DECISION ANSWERED|DECISION|APPROVAL (?:APPROVED|DENIED|ANSWERED))\b", re.I)


def _clip(s, n=240):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def records(lines: list[dict]) -> dict:
    """What the transcript itself records: tasks, decisions, files, the last ask and reply, each with its id."""
    todos: dict = {}           # the last TodoWrite's list wins (it restates the whole list)
    todo_tool = todo_uuid = None
    tasks: dict = {}           # TaskCreate/TaskUpdate by task id
    files: dict = {}
    asks: dict = {}            # AskUserQuestion tool id -> the question
    decisions = []
    last_ask = last_reply = None
    for o in lines:
        if o.get("isSidechain"):
            continue
        uuid = o.get("uuid")
        for b in _blocks(o):
            if b.get("type") == "tool_use" and o.get("type") == "assistant":
                name, inp, tid = b.get("name") or "", b.get("input") or {}, b.get("id")
                if name == "TodoWrite" and isinstance(inp.get("todos"), list):
                    todos = {str(i): t for i, t in enumerate(inp["todos"]) if isinstance(t, dict)}
                    todo_tool, todo_uuid = tid, uuid
                elif name == "TaskCreate":
                    key = str(inp.get("taskId") or inp.get("id") or tid)
                    tasks[key] = {"text": _clip(inp.get("subject") or inp.get("description") or inp.get("title")),
                                  "status": "open", "tool_id": tid, "uuid": uuid}
                elif name == "TaskUpdate":
                    key = str(inp.get("taskId") or inp.get("id") or "")
                    if key in tasks and inp.get("status"):
                        tasks[key].update(status=str(inp["status"]), tool_id=tid, uuid=uuid)
                elif name in FILE_TOOLS:
                    fp = inp.get("file_path") or inp.get("notebook_path")
                    if fp:
                        files.pop(fp, None)
                        files[fp] = {"path": str(fp), "tool": name, "tool_id": tid, "uuid": uuid}
                elif name == "AskUserQuestion":
                    qs = inp.get("questions") if isinstance(inp.get("questions"), list) else []
                    asks[tid] = _clip(qs[0].get("question") if qs and isinstance(qs[0], dict) else "", 160)
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in asks:
                c = b.get("content")
                c = "".join(x.get("text", "") for x in c if isinstance(x, dict)) if isinstance(c, list) else str(c or "")
                decisions.append({"text": _clip(f"{asks[b['tool_use_id']]} -> {c}"), "rationale": "answered card",
                                  "tool_id": b.get("tool_use_id"), "uuid": uuid})
        if _is_operator_line(o):
            t = _text_of(o)
            if DECISION_LINE.match(t):
                decisions.append({"text": _clip(t), "rationale": "decision line", "tool_id": None, "uuid": uuid})
            else:
                last_ask = {"text": _clip(t, 400), "uuid": uuid}
        elif o.get("type") == "assistant" and _text_of(o):
            last_reply = {"text": _clip(_text_of(o), 400), "uuid": uuid}
    open_tasks = [{"text": _clip(t.get("content") or t.get("activeForm")), "status": t.get("status"),
                   "tool_id": todo_tool, "uuid": todo_uuid}
                  for t in todos.values() if t.get("status") not in ("completed",)]
    open_tasks += [t for t in tasks.values() if t["status"] not in ("completed", "deleted", "cancelled")]
    return {"open_tasks": open_tasks[-KEEP:], "decisions": decisions[-KEEP:], "files": list(files.values())[-KEEP:],
            "last_ask": last_ask, "last_reply": last_reply}


def pending_cards(seat: str, store=None) -> list[dict]:
    """Cards still waiting on the operator for this seat, from the approvals ledger (read only)."""
    try:
        if store is None:
            from approval_schema import ApprovalStore
            store = ApprovalStore(db_path=os.environ.get("APPROVAL_DB_PATH") or None)
        c = store._conn()
        try:
            rows = c.execute("SELECT id, question FROM approval_requests WHERE status='pending' AND from_agent=? "
                             "ORDER BY rowid DESC LIMIT ?", (seat, KEEP)).fetchall()
        finally:
            c.close()
        return [{"card_id": r[0], "text": _clip(r[1], 200)} for r in rows]
    except Exception:  # noqa: BLE001 — no ledger: none known (the warm-up says so)
        return []


def lookup_rule(seat: str) -> str:
    return (f"Look things up instead of relying on recall: `orchestra memory search \"<words>\" --agent {seat}` "
            f"searches everything your earlier sessions said and did (messages, tool calls and results, decisions), "
            f"each row with its session, generation and ids. Use it whenever a detail matters.")


def build_handoff(seat: str, lines: list[dict], *, cards: list[dict] | None = None):
    """The lineage Handoff (lineage_daemon/handoff_schema.py) from the records, every item citing its id."""
    from lineage_daemon.handoff_schema import Handoff
    r = records(lines)
    cite = lambda it: " ".join(x for x in (f"[uuid {it['uuid']}]" if it.get("uuid") else "",
                                           f"[tool {it['tool_id']}]" if it.get("tool_id") else "") if x)
    h = Handoff()
    if r["last_ask"]:
        h.current_goal = f"{r['last_ask']['text']} [uuid {r['last_ask']['uuid']}]"
    h.working_state = (f"Last reply: {r['last_reply']['text']} [uuid {r['last_reply']['uuid']}]"
                       if r["last_reply"] else "")
    h.open_loops = [f"{t['text']} ({t.get('status') or 'open'}) {cite(t)}".strip() for t in r["open_tasks"]]
    h.decisions = [{"text": d["text"], "rationale": d["rationale"], "uuid": d.get("uuid"), "tool_id": d.get("tool_id")}
                   for d in r["decisions"]]
    h.file_roots_touched = [f"{f['path']} ({f['tool']}) {cite(f)}".strip() for f in r["files"]]
    h.hazards = [f"pending card {c['card_id']}: {c['text']}" for c in (cards or [])]
    h.next_3_actions = [lookup_rule(seat)]
    return h, r


def handoff_message(seat: str, h, generation_from, generation_to) -> str:
    """The warm session's first message: the handoff as data, and what to do with it."""
    d = h.to_successor_init()
    d.pop("canary_questions", None)
    return (f"You are {seat}, continuing in a new session (generation {generation_to}, after {generation_from}). "
            f"Your earlier sessions are in the conversation index, not in this context. Below is the handoff, built "
            f"from the records of the last session (every item cites the message uuid or tool id it came from). "
            f"{lookup_rule(seat)}\n\nReply only: READY.\n\n```json\n{json.dumps(d, indent=1)}\n```")


# --- 5. canary and grading -----------------------------------------------------------------------------

#: A canary region (the transcript row its pointer resolves to) must hold enough to grade: the gate's overlap rule
#: needs at least 2 of the region's distinctive words in the answer, so a one-word reply ("ALIVE") can never pass.
MIN_REGION_CHARS = 80
MIN_REGION_TERMS = 6
#: Above this the gate grounds against the whole transcript instead of the region (and wants rarer anchors).
MAX_REGION_CHARS = 2000
CANARY_N = 3
ACK = re.compile(r"^\W*(ok(ay)?|done|yes|no|sure|thanks?( you)?|ready|alive|pong|ack(nowledged)?|got it|noted|"
                 r"on it|will do|understood|sounds good)\W*$", re.I)


def _region_text(o: dict) -> str:
    """What the gate's resolve_pointer returns for this one row."""
    return gate_text(o).strip()


def _terms(text: str) -> list[str]:
    """The gate's distinctive words (focus_registry.comprehension._distinctive): 5+ letters, or with a digit."""
    try:
        from focus_registry.comprehension import _distinctive
        return list(dict.fromkeys(_distinctive(text)))
    except Exception:  # noqa: BLE001 — same rule, if the module cannot load
        words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
        return list(dict.fromkeys(w for w in words if len(w) >= 5 or any(ch.isdigit() for ch in w)))


def gradeable(o: dict) -> bool:
    """A row the gate can grade an own-words answer against: enough text, enough distinctive words, not an
    acknowledgement, and small enough to be graded as a region."""
    t = _region_text(o)
    return (MIN_REGION_CHARS <= len(t) <= MAX_REGION_CHARS and not ACK.match(t)
            and len(_terms(t)) >= MIN_REGION_TERMS)


def _keyword(text: str, avoid: set) -> str:
    """The word to search the index for: the longest distinctive one not already given away."""
    cands = sorted((w for w in _terms(text) if w not in avoid and not w.isdigit()), key=len, reverse=True)
    return cands[0] if cands else ""


def canary_questions(lines: list[dict], r: dict, seat: str = "the seat", resolve=None) -> list[dict]:
    """Questions the new session answers by LOOKING THINGS UP in the conversation index, each pointing (turn-<n>,
    the gate's own form) at a transcript row with substance: first the rows the records came from (open tasks,
    decisions, files touched), then the longest operator and agent turns. Never a row just because it is the last
    one, never a short or acknowledgement row. Each question names one search word from its row (the longest
    distinctive one), so the answer has to come from the row it finds, in the session's own words."""
    turns = text_turns(lines)
    at = {o.get("uuid"): (i, o) for i, o in enumerate(turns, 1) if o.get("uuid")}
    record_uuids = [t.get("uuid") for t in r.get("open_tasks") or []] + \
        [d.get("uuid") for d in r.get("decisions") or []] + [f.get("uuid") for f in r.get("files") or []]
    chosen, used = [], set()

    def take(u):
        if not u or u in used or u not in at or len(chosen) >= CANARY_N:
            return
        i, o = at[u]
        if not gradeable(o):
            return
        # The gate resolves the pointer itself: it must hand back THIS row, or the answer is graded against another.
        if resolve is not None and resolve(f"jsonl:turn-{i}") != _region_text(o):
            return
        used.add(u)
        chosen.append((i, o))

    for u in reversed(record_uuids):
        take(u)
    talk = [o for o in turns if o.get("uuid") and o.get("type") in ("user", "assistant")
            and (_is_operator_line(o) or (o.get("type") == "assistant" and _text_of(o)))]
    for o in sorted(talk, key=lambda x: len(_region_text(x)), reverse=True):
        take(o.get("uuid"))
    qs, given = [], set()
    for i, o in sorted(chosen, key=lambda x: x[0]):
        kw = _keyword(_region_text(o), given)
        given.add(kw)
        who = "the operator (or a message to you)" if o.get("type") == "user" else "you"
        when = f" at {o['timestamp']}" if o.get("timestamp") else ""
        qs.append({"q": (f"Look up, with `orchestra memory search \"{kw}\" --agent {seat}`, the entry {who} wrote in "
                         f"the previous session{when} that mentions \"{kw}\". Quote its key sentences word for word, "
                         f"then say what it means."),
                   "source_pointer": f"jsonl:turn-{i}"})
    return qs


def grading_message(questions: list[dict]) -> str:
    qs = "\n".join(f"## Q{i}\n{q['q']}" for i, q in enumerate(questions, 1))
    return ("Before you take over, a check. For EACH question run the `orchestra memory search` it names and find "
            "the entry it asks about (never answer from recall). A search shows a snippet; when it is cut short, read "
            "the whole entry by its uuid in that session's transcript. Under the question's heading, quote the "
            "entry's key sentences word for word, with every detail (names, dates, numbers, codes), then say in a "
            "line what it means. Then add a section `## Readback` that restates every open loop and every hazard "
            "from the handoff.\n\n" + qs + "\n\n## Readback")


def readback_truth(r: dict, cards: list[dict] | None) -> dict:
    """What the read-back must restate: each open task and pending card by its words (its id is the handoff's
    citation, not something to recite)."""
    return {"open_loops": [t["text"] for t in r.get("open_tasks") or [] if t.get("text")],
            "hazards": [c["text"] for c in cards or [] if c.get("text")]}


def grade(successor_id: str, questions: list[dict], reply: str, truth: dict, transcript_path, *, gate=None,
          force_fail: bool = False) -> dict:
    """Grade the warm session's reply with the existing gate (rotation_gate_manual): canary authored from the
    records, answers split by heading, read-back graded by focus_registry.comprehension, the artifact read back
    fail-closed. Returns {"pass", "reason"?, "missed"?}."""
    if gate is None:
        import rotation_gate_manual as gate
    if not questions:
        return {"pass": False, "reason": "no canary could be built from the records"}
    gate.author_canary(successor_id, questions)
    try:
        answers = gate.split_readback_sections(reply, len(questions))
    except gate.GradeRefusal as e:
        return {"pass": False, "reason": f"refused: {e}"}
    m = re.search(r"^#{1,6}\s*Readback\b(.*)", reply, re.I | re.S | re.M)
    readback = m.group(1) if m else reply
    gt = {"open_loops": list(truth.get("open_loops") or []), "hazards": list(truth.get("hazards") or []),
          "transcript_path": str(transcript_path), "mode": "supervised"}
    art = gate.record_readback(successor_id, readback, answers, gt)
    passed = gate.comprehension_passed(successor_id) and not force_fail
    out = {"pass": passed}
    if not passed:
        out["reason"] = "forced by the fault switch" if force_fail else "the gate did not pass"
        out["missed"] = ((art.get("detail") or {}).get("canary") or {}).get("missed", []) + \
            ((art.get("detail") or {}).get("readback") or {}).get("missed", [])
    return out


# --- 4. the warm process's environment -----------------------------------------------------------------

def warm_env(env: dict, seat: str) -> dict:
    """The seat's environment with every way its hooks find the SEAT taken out, so nothing the warm session does is
    taken for the seat: no tmux pane (the state, open-call and event-stream hooks keep quiet without TMUX_PANE), no
    tmux client (the mail drain would otherwise ask tmux which session it is in and take the seat's mail), a
    session name no registry row has (the drain resolves no canonical agent), and no self-rotation."""
    e = dict(env)
    for k in ("TMUX", "TMUX_PANE"):
        e.pop(k, None)
    e["AQD_TMUX_SESSION"] = f"headless-warmup.{seat}"
    e["ORCH_SELF_ROTATE"] = "off"
    e["ORCHESTRA_HEADLESS_WARMUP"] = "1"
    return e


def fresh_argv(argv: list) -> list:
    """The seat's own command line, without --resume: a new session with the same flags, settings and model."""
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == "--resume":
            skip = True
            continue
        out.append(a)
    return out


def write_warmup_mark(home: Path, session_id: str, *, uuids=(), through: str | None = None) -> None:
    """The chat's warm-up filter (api chat-transcript.ts, warmupMarkFor): this session's warm-up exchange is left
    out. Merged into the seat's warmup.json (one entry per session), written atomically."""
    p = Path(home) / WARMUP_FILE
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError):
        doc = {}
    sessions = doc.get("sessions") if isinstance(doc, dict) and isinstance(doc.get("sessions"), dict) else {}
    cur = sessions.get(session_id) if isinstance(sessions.get(session_id), dict) else {}
    ids = list(dict.fromkeys([*(cur.get("uuids") or []), *[u for u in uuids if u]]))
    entry = {"uuids": ids, **({"through": through or cur.get("through")} if (through or cur.get("through")) else {})}
    sessions[session_id] = entry
    tmp = p.with_name(f".{WARMUP_FILE}.tmp")
    tmp.write_text(json.dumps({"sessions": sessions}, indent=1) + "\n")
    os.replace(tmp, p)


# --- 6. promote: the identity store --------------------------------------------------------------------

class PromoteRefused(RuntimeError):
    pass


def current_generation(orch_dir, seat: str) -> tuple[str, int] | None:
    """The seat's lineage root and its canonical generation number, read only; None when the store has neither."""
    db = Path(orch_dir) / "state" / "orchestra-registry.db"
    if not db.exists():
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        r = conn.execute("SELECT c.root, g.generation FROM canonical c JOIN generations g ON g.id = c.generation_id "
                         "WHERE c.root = ? OR c.tmux_session = ? LIMIT 1", (seat, seat)).fetchone()
        return (r[0], int(r[1])) if r else None
    finally:
        conn.close()


def promote_identity(orch_dir, seat: str, session_id: str, *, model=None, conversation_path=None,
                     writer=None) -> int:
    """Mint generation N+1 for the seat's lineage with the new session, as one swap in the identity store (the
    canonical pointer moves, the old generation is retired at the same instant, so the chat's lineage chain links
    them), then project the store to its JSON files at once. Returns the new generation number. Raises
    PromoteRefused when the store cannot take it (no lineage, cutover not active)."""
    if writer is None:
        from scripts.identity_store import identity_writer as writer
    cur = current_generation(orch_dir, seat)
    if cur is None:
        raise PromoteRefused(f"{seat} has no lineage in the identity store")
    root, gen = cur
    db = Path(orch_dir) / "state" / "orchestra-registry.db"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        top = conn.execute("SELECT MAX(generation) FROM generations WHERE root = ?", (root,)).fetchone()[0] or gen
    finally:
        conn.close()
    green = {"generation": int(top) + 1, "session_id": session_id, "model": model,
             "conversation_path": str(conversation_path) if conversation_path else None,
             "promoted_by": "headless-rotation", "tmux_session": seat}
    if not writer.swap_generation(str(orch_dir), root, green, sync_effects_owner=True):
        raise PromoteRefused("the identity store cutover is not active")
    writer.project_now(str(orch_dir))
    return green["generation"]


def successor_id(seat: str, generation) -> str:
    return f"{seat}-headless-g{generation}"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Steps:
    """The blocking steps, as the supervisor calls them (in a worker thread). A test passes its own."""

    def __init__(self, orch_dir):
        self.orch_dir = Path(orch_dir)

    def transcript(self, session_id: str):
        import episodic_memory as em
        p = em.find_transcript(session_id)
        return str(p) if p else None

    def generation(self, session_id: str):
        import episodic_memory as em
        return em.generation_of(session_id)

    def export(self, seat: str, session_id: str, generation, path, force_fail: bool = False) -> dict:
        return export_session(seat, session_id, generation, path, force_fail=force_fail)

    def handoff(self, seat: str, path, generation_from, generation_to) -> dict:
        lines = read_lines(path)
        cards = pending_cards(seat)
        h, r = build_handoff(seat, lines, cards=cards)
        return {"message": handoff_message(seat, h, generation_from, generation_to),
                "questions": canary_questions(lines, r, seat, resolve=self._resolver(path)),
                "truth": readback_truth(r, cards),
                "counts": {"open_tasks": len(r["open_tasks"]), "decisions": len(r["decisions"]),
                           "files": len(r["files"]), "cards": len(cards)}}

    @staticmethod
    def _resolver(path):
        import rotation_gate_manual as gate
        return lambda pointer: gate.resolve_pointer(path, pointer)

    def grade(self, succ: str, questions, reply: str, truth: dict, path, force_fail: bool = False) -> dict:
        return grade(succ, questions, reply, truth, path, force_fail=force_fail)

    def promote(self, seat: str, session_id: str, model=None, conversation_path=None) -> int:
        return promote_identity(self.orch_dir, seat, session_id, model=model, conversation_path=conversation_path)


# --- Release 1: briefing-mode switch ----------------------------------------------------------------------

BUILTIN_SETTINGS = {"auto_compact": False, "show_compaction": True, "compact_at": 65, "guard_pct": 90}
#: The idle dwell before an automatic switch (seconds); the guard and the person's /compact skip it.
IDLE_DWELL_S = 60.0


def migrate_legacy_rotation(seat: str, orch_dir=None) -> bool:
    """One-time: the retired `rotation: true` of headless-seats.json becomes `auto_compact: true` in
    seat-settings.json (only when the seat has no auto_compact of its own), with a history row. True when it moved."""
    entry = HS.load_switch(orch_dir).get(seat)
    if not (isinstance(entry, dict) and entry.get("rotation") is True):
        return False
    p = Path(HS.data_dir(orch_dir)) / "state" / "seat-settings.json"
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError):
        doc = {}
    doc = doc if isinstance(doc, dict) else {}
    seats = doc.setdefault("seats", {})
    cur = seats.setdefault(seat, {})
    if "auto_compact" in cur:
        return False
    cur["auto_compact"] = True
    doc.setdefault("history", []).append({"seat": seat, "field": "auto_compact", "old": None, "new": True,
                                          "by": "migration (headless-seats.json rotation)", "at": now_iso()})
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    old = os.umask(0o077)
    try:
        tmp.write_text(json.dumps(doc, indent=2) + "\n")
    finally:
        os.umask(old)
    os.replace(tmp, p)
    HS.set_option(seat, "rotation", None, orch_dir=orch_dir)       # retired: never read again
    return True


def rotation_conf(seat: str, orch_dir=None, env=os.environ, reader=None) -> dict:
    """The seat's switch settings, read at every turn end: seat_settings.rotation_settings (the one reader; built-in
    values when it is not installed), the idle dwell and the backoff (env overrides for a test)."""
    try:
        migrate_legacy_rotation(seat, orch_dir)
    except Exception:  # noqa: BLE001 — a failed migration leaves the switch off
        pass
    conf = dict(BUILTIN_SETTINGS)
    try:
        if reader is None:
            from seat_settings import rotation_settings as reader
        got = reader(seat) or {}
        conf.update({k: got[k] for k in ("auto_compact", "show_compaction", "compact_at", "guard_pct") if k in got})
    except Exception:  # noqa: BLE001 — no settings module or file: built-in values (auto_compact off)
        pass
    conf["auto_compact"] = conf.get("auto_compact") is True
    conf["at"] = float(conf["compact_at"])
    if env.get("HEADLESS_ROTATION_TEST") == "1" and env.get("HEADLESS_ROTATE_AT"):
        conf["at"] = _num(env.get("HEADLESS_ROTATE_AT"), conf["at"])   # TEST ONLY: a proof seat's low trigger
    conf["ceiling"] = float(conf["guard_pct"])
    conf["idle_s"] = _num(env.get("HEADLESS_ROTATE_IDLE_S"), IDLE_DWELL_S) if env.get("HEADLESS_ROTATE_IDLE_S") != "0" \
        else 0.0
    conf["backoff_s"] = _num(env.get("HEADLESS_ROTATE_BACKOFF_S"), BACKOFF_S)
    conf["max_failures"] = int(_num(env.get("HEADLESS_ROTATE_MAX_FAILURES"), MAX_FAILURES))
    return conf


def growth_needed(window) -> int:
    """After a switch, the next one waits until the context has grown by this much: max(1,024 tokens, 5% of window)."""
    return max(1024, int(0.05 * window)) if isinstance(window, (int, float)) and window > 0 else 1024


def trigger(pct, tokens, window, conf: dict, *, person: bool = False, baseline=None) -> str | None:
    """Why a switch is due now: 'person' (the person's /compact: always, any %), 'guard' or 'threshold' (only with
    Auto-compact on, at or over Compact at, and only after the growth since the last switch). Auto-compact off:
    nothing, the guard included."""
    if person:
        return "person"
    if not conf.get("auto_compact") or pct is None or pct < conf["at"]:
        return None
    if baseline is not None and isinstance(tokens, (int, float)) and tokens < baseline + growth_needed(window):
        return None
    return "guard" if pct >= conf["ceiling"] else "threshold"


def guard_card_due(runtime_native_compaction: bool, pct, conf: dict, already: bool) -> bool:
    """A runtime with no compaction of its own, at the guard, with Auto-compact off: one card per crossing."""
    return (not runtime_native_compaction and not conf.get("auto_compact") and pct is not None
            and pct >= conf["ceiling"] and not already)


GUARD_CARD_TEXT = "This chat is nearly full. Type /compact to make room."


def verify_bound(orch_dir, seat: str, session_id: str) -> dict:
    """After the identity swap: does the store name this conversation as the seat's current one, in the database AND
    in its JSON projections? {"db": bool, "json": {file: bool}, "ok": bool}"""
    out = {"db": False, "json": {}}
    db = Path(orch_dir) / "state" / "orchestra-registry.db"
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            r = conn.execute("SELECT g.session_id FROM canonical c JOIN generations g ON g.id = c.generation_id "
                             "WHERE c.root = ? OR c.tmux_session = ? LIMIT 1", (seat, seat)).fetchone()
            out["db"] = bool(r and r[0] == session_id)
        finally:
            conn.close()
    except sqlite3.Error:
        out["db"] = False
    od = Path(orch_dir)
    for name, path, pick in (("registry.json", od / "registry.json", lambda d: (d.get("agents") or {}).get(seat)),
                             ("agent-sessions.json", od / "state" / "agent-sessions.json", lambda d: d.get(seat)),
                             (f"state/agents/{seat}.json", od / "state" / "agents" / f"{seat}.json", lambda d: d)):
        try:
            row = pick(json.loads(path.read_text())) or {}
            out["json"][name] = row.get("session_id") == session_id
        except (OSError, ValueError, AttributeError):
            out["json"][name] = None                    # not projected on this install
    out["ok"] = out["db"] and all(v is not False for v in out["json"].values())
    return out


def write_packet_file(home: Path, session_id: str, text: str) -> Path:
    """The block, on disk (0600) BEFORE the conversation is bound: a crash between binding and the first input
    still finds it."""
    p = Path(home) / f"packet-{session_id}.txt"
    old = os.umask(0o077)
    try:
        tmp = p.with_name(f".{p.name}.tmp")
        tmp.write_text(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    finally:
        os.umask(old)
    return p


def usage_tokens(cost) -> int | None:
    """The tokens a check turn spent, from its result (input, cache and output together), or None."""
    u = (cost or {}).get("usage") if isinstance(cost, dict) else None
    if not isinstance(u, dict):
        return None
    parts = [u.get(k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
                                "output_tokens")]
    return sum(x for x in parts if isinstance(x, int)) if any(isinstance(x, int) for x in parts) else None


def record_swap(home: Path, rec: dict) -> None:
    """One line per switch in <seat dir>/swaps.jsonl: sizes, timings, costs (for the proof and the tuning)."""
    with open(Path(home) / "swaps.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": now_iso(), **rec}) + "\n")


class BriefingSteps:
    """Release 1's blocking steps, as the supervisor calls them in a worker thread. `store` has the contract's
    readers and writers (episodic_memory); a test passes stand-ins."""

    def __init__(self, orch_dir, store=None):
        self.orch_dir = Path(orch_dir)
        if store is None:
            import episodic_memory as store
        self.store = store

    def transcript(self, session_id: str):
        p = self.store.find_transcript(session_id)
        return str(p) if p else None

    def generation(self, session_id: str):
        return self.store.generation_of(session_id)

    def export(self, seat: str, session_id: str, path, force_fail: bool = False, assume_saved: bool = False) -> dict:
        """The incremental ingest of the seat's current conversation, then a token from its last turn found.
        `assume_saved`: the conversation is one this supervisor promoted itself; while it has had no turn there is
        nothing of its own to save or check (its context came from the store, after the previous conversation's
        verified export), so it passes as saved once the index names it as the active one."""
        got = self.store.ingest_tick(seat) or {}
        extra = {}
        act = None
        try:
            act = (self.store.active_session(seat) or {}).get("provider_session_id")
        except Exception:  # noqa: BLE001
            act = None
        if act != session_id:
            # The index's active conversation is not the one running: no switch (its saved history would be the
            # wrong one). The supervisor registers its conversation again at its next start.
            return {"ok": False, "foreign": True, "active": act, "added": got.get("added", 0), "token": None,
                    "reason": "the index's active conversation is another one"}
        if assume_saved and not has_turn(path):
            return {"ok": True, "added": got.get("added", 0), "token": None,
                    "saved": "promoted here and no turn since: its context is what the store already holds"}
        token = last_turn_token(read_lines(path)) if path else None
        if not token:
            return {"ok": False, "added": got.get("added", 0), "token": None, "reason": "no text in the last turn",
                    **extra}
        ok, row = (False, None) if force_fail else self.store.verify(seat, session_id, token)
        out = {"ok": bool(ok), "added": got.get("added", 0), "token": token, **extra}
        if not ok:
            out["reason"] = "forced by the fault switch" if force_fail else "the last turn's token is not in the index"
        else:
            out["row"] = {k: (row or {}).get(k) for k in ("uuid", "session_id", "generation", "kind")}
        return out

    def packet(self, seat: str, window_tokens) -> dict:
        import briefing_packet
        return briefing_packet.build_packet(seat, window_tokens=window_tokens, store=self.store)

    def promote(self, seat: str, session_id: str, model=None) -> int:
        return promote_identity(self.orch_dir, seat, session_id, model=model)

    def verify_bound(self, seat: str, session_id: str) -> dict:
        got = verify_bound(self.orch_dir, seat, session_id)
        if got["db"] and not got["ok"]:
            # the database moved, a projection lags: project once more and look again
            try:
                from scripts.identity_store import identity_writer
                identity_writer.project_now(str(self.orch_dir))
            except Exception:  # noqa: BLE001
                pass
            got = verify_bound(self.orch_dir, seat, session_id)
        return got


# --- what a person reads about an attempt (amendment 15) --------------------------------------------------------

_SECRETY = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|xox[abp]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{12,}|"
                      r"Bearer\s+\S+|[A-Fa-f0-9]{32,}|[A-Za-z0-9+/=_-]{40,})")


def scrub(text: str, store=None) -> str:
    """A reason as stored and shown: through the index's own redaction when the store has one, else key-shaped
    tokens, bearer headers and long opaque runs are replaced."""
    text = str(text or "")
    red = getattr(store, "redact", None) if store is not None else None
    if callable(red):
        try:
            return red(text)
        except Exception:  # noqa: BLE001
            pass
    return _SECRETY.sub("[hidden]", text)


def wait_reason(*, bg_tasks: int, turn_open: bool, held: int, incoming: bool, dwell: bool) -> str:
    if bg_tasks:
        return "a background job was still running"
    if held:
        return "a question was waiting for an answer"
    if turn_open:
        return "a reply was still being written"
    if incoming:
        return "a message was on its way in"
    if dwell:
        return "waiting for a quiet minute first"
    return "waiting for a safe moment"


_CHECK_FAILS = {"last_message": "it couldn't quote your last message",
                "last_file": "it couldn't name the last file it wrote",
                "last_command": "it couldn't name the last command it ran",
                "card": "it couldn't name the pending card",
                "last_reply": "it couldn't quote its own last reply",
                "task": "it couldn't name the latest open task"}


THIN_CHECK_REASON = "there wasn't enough on record to check the compacted chat, so nothing was changed"


def plain_check_reason(graded: dict, check: dict | None = None) -> str:
    if graded.get("too_thin"):
        return THIN_CHECK_REASON
    missing = graded.get("missing") or []
    if missing:
        return f"the check on the copy failed: {_CHECK_FAILS.get(missing[0], 'one of its answers was wrong')}"
    return "the check on the copy failed: it gave no answer"


def plain_capability_reason(diff: dict) -> str:
    mcp = diff.get("mcp_servers")
    if isinstance(mcp, tuple) and len(mcp) == 2 and isinstance(mcp[0], dict):
        lost = sorted(k for k, v in mcp[0].items() if v == "connected" and (mcp[1] or {}).get(k) != "connected")
        if lost:
            return f"the MCP server {lost[0]} didn't connect on the new copy"
    names = {"model": "model", "tools": "tools", "permission_mode": "permission setting", "skills": "skills",
             "agents": "agents", "commands": "commands", "cwd": "working folder", "output_style": "output style",
             "effort": "effort setting", "init": "startup details"}
    what = ", ".join(names.get(k, k) for k in sorted(diff))
    return f"the new copy wouldn't have had the same {what}"


NOTE_OPEN, NOTE_CLOSE = "<compact-note>", "</compact-note>"


def compact_note(reason: str) -> str:
    return (f"{NOTE_OPEN}Your earlier request to compact this chat didn't go through: {reason}. If asked, explain it "
            f"plainly; recent attempts: `orchestra memory compaction-status`.{NOTE_CLOSE}")
