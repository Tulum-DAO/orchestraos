"""briefing_packet.py: what a fresh conversation of a seat is given ahead of its first input, built from the store.

Deterministic (no model call), from the conversation index's readers only (episodic_memory: operator_messages,
open_records, wakeups, recent_window). In this order:
  1. a header: current instructions win; finished work is not repeated; this is not everything; earlier things are
     looked up with `orchestra memory search`;
  2. every message the operator typed to this seat, verbatim, oldest first; trimmed oldest-first only above a cap,
     and the trim is said;
  3. the current objective: the latest operator directive that is not a question and not a notification;
  4. open tasks, approval cards and scheduled wakeups, with their ids;
  5. the recent conversation verbatim with message ids (tool pairs repaired first, history_sanitize.py); the latest
     user and the latest assistant message always whole, or cut head-and-tail when huge, never inside a character;
  6. the work steps: tool, outcome, a short output tail, files changed.
The budget comes from the receiving window (a share of it), capped at CAP_TOKENS. Wrapped in
<earlier-conversation>...</earlier-conversation>.

It becomes the receiving conversation's startup instructions where the runtime has a slot for them (Claude: an
appended system prompt, fixed for that conversation's life), else it rides on the first input. It ends with the
standing instruction CONTINUE. `check` (in the result) is the frozen range a fork's continuity check is graded on:
the ids of the last request, of what was being done, and of every open item.

The rendered wording never names what happened to the conversation (a test scans it for BANNED): the model must not
tell the person it is "a new session". Quoted content (the operator's own words, the conversation) is kept as written.
"""
from __future__ import annotations

import json
import re
import unicodedata

from history_sanitize import sanitize_history

OPEN, CLOSE = "<earlier-conversation>", "</earlier-conversation>"
CHARS_PER_TOKEN = 4
CAP_TOKENS = 50_000
MIN_TOKENS = 8_000
#: The share of the receiving window the whole block may take.
WINDOW_SHARE = 0.20
#: Shares of the budget: operator messages, recent conversation, work steps (the rest: header, objective, records).
SHARE_OPERATOR, SHARE_RECENT, SHARE_STEPS = 0.30, 0.50, 0.08
STEP_TAIL = 200
#: A shell command is shown on its step whole up to this many characters (the same limit the check asks about); a
#: longer one is cut head-and-tail, and then the check does not ask for it.
COMMAND_CAP = 300
NEWLINE_MARK = " \u21b5 "
#: Words and phrases the block's own wording must never contain (whole words, any case).
BANNED = ("session", "sessions", "rotation", "rotate", "rotated", "candidate", "compaction", "compact", "compacted",
          "handoff", "hand-off", "successor", "predecessor", "generation", "refresh", "context refresh", "briefing",
          "packet", "reset")
_BANNED_RE = re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in sorted(BANNED, key=len, reverse=True))
                        + r")(?!\w)", re.I)
#: The one place a banned word may appear: the exact name of the command that lists past attempts.
STATUS_COMMAND = "orchestra memory compaction-status"


def wording_of(text: str) -> str:
    """The block's OWN wording: every line that quotes a record (a message, a step with its command or output, a task,
    a card, a fact) taken out. The banned words are checked on this; quoted content stays as it was written (an
    operator's message or a command may well say "session")."""
    return "\n".join(ln for ln in (text or "").splitlines() if not re.match(r"\s*(- )?\[", ln))


def banned_words(text: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _BANNED_RE.finditer((text or "").replace(STATUS_COMMAND, ""))})


def budget_chars(window_tokens) -> int:
    """The block's size limit in characters, from the receiving window (unknown: the cap)."""
    if not isinstance(window_tokens, (int, float)) or window_tokens <= 0:
        tokens = CAP_TOKENS
    else:
        tokens = max(MIN_TOKENS, min(CAP_TOKENS, int(window_tokens * WINDOW_SHARE)))
    return tokens * CHARS_PER_TOKEN


# --- cutting text without breaking a character -----------------------------------------------------------

def _joins(ch: str) -> bool:
    """A character that belongs to the one before it (a combining mark, a joiner, a variation selector, a skin
    tone): a cut must not fall just before it."""
    if not ch:
        return False
    o = ord(ch)
    return (unicodedata.combining(ch) != 0 or ch in "‍︎️" or 0x1F3FB <= o <= 0x1F3FF
            or 0xE0020 <= o <= 0xE007F or 0xFE00 <= o <= 0xFE0F)


def safe_cut(text: str, at: int) -> int:
    """Move a cut position back until it does not split a character cluster."""
    at = max(0, min(len(text), at))
    while 0 < at < len(text) and (_joins(text[at]) or text[at - 1] == "‍"):
        at -= 1
    return at


def head_tail(text: str, limit: int, mark: str = "\n[… {n} characters not shown …]\n") -> str:
    """The text whole when it fits, else its head and its tail around a note, never cutting inside a character."""
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(mark) - 12)
    h = safe_cut(text, keep * 2 // 3)
    t = safe_cut(text, len(text) - (keep - h))
    return text[:h] + mark.format(n=t - h) + text[t:]


# --- reading the store's entries ---------------------------------------------------------------------------

def _eid(e: dict) -> str:
    u = str(e.get("uuid") or e.get("id") or "")
    p = e.get("part")
    return f"{u}:{p}" if p not in (None, 0, "0") else u


def _tool_name(text: str) -> str:
    return (text or "").split(" ", 1)[0] or "tool"


def to_messages(entries: list[dict]) -> list[dict]:
    """Index rows as messages for the tool-pair repair: a tool call row is an assistant message carrying the call,
    a result row a tool message answering it."""
    out = []
    for e in entries or []:
        kind, role, text = e.get("kind"), e.get("role"), str(e.get("text") or "")
        base = {"id": _eid(e), "ts": e.get("ts"), "entry": e}
        if kind == "tool_call" and e.get("tool_id"):
            out.append({**base, "role": "assistant", "content": "", "tool_calls": [{"id": e["tool_id"],
                                                                                    "name": _tool_name(text)}]})
        elif kind == "tool_result" and e.get("tool_id"):
            out.append({**base, "role": "tool", "content": text, "tool_call_id": e["tool_id"]})
        elif text.strip():
            out.append({**base, "role": "user" if role == "user" else "assistant", "content": text})
    return out


_QUESTION = re.compile(r"\?\s*$|^\s*(what|why|how|when|where|who|which|is|are|can|could|would|do|does|did|should|"
                       r"will)\b[^.!]*$", re.I)
_NOTIFICATION = re.compile(r"^\s*(\[|<|📬|✉|new message from|mail from|reply from|\(automated\)|"
                           r"task-notification|system:)", re.I)


def is_directive(text: str) -> bool:
    """An operator message that sets work: not a question, not a notification relayed to the seat."""
    t = (text or "").strip()
    return bool(t) and not _QUESTION.search(t) and not _NOTIFICATION.match(t)


def _line(e: dict, text: str | None = None) -> str:
    ts = f" · {e.get('ts')}" if e.get("ts") else ""
    return f"[{_eid(e)}{ts}] {e.get('text') if text is None else text}"


# --- the block ---------------------------------------------------------------------------------------------

CONTINUE = ("Continue as this agent: match the voice, tone and continuity of the conversation above. For anything not "
            "in it, look it up with `orchestra memory search` or say you don't know. Never invent.")


def checked_facts(lines: list[dict], known_ids: set) -> list[str]:
    """Fact lines for the summary: each must cite an id the records confirm exists, else it is dropped (a
    model-written line with no checkable id never reaches a prompt). lines: [{"text", "id"}]."""
    return [f"- {x['text']} [{x['id']}]" for x in lines or [] if x.get("id") and x["id"] in known_ids and x.get("text")]


def header(seat: str) -> str:
    return ("This is the earlier part of this conversation, gathered from its records so you can carry on with the "
            "next message as you would have. The newest instructions in it win over older ones. Work it shows as "
            "finished is finished: do not do it again. It is not everything that was said or done: to look anything "
            f"earlier up, run `orchestra memory search \"<words>\" --agent {seat}` (each row comes back with its "
            f"message id). Past attempts to make room in this chat are listed by `{STATUS_COMMAND}`. Carry on "
            "without remarking on this block.")


def _redactor(store):
    """The index's own redaction (the same one ingest applies), applied again to what a step shows, so a command or
    an output tail never carries a secret into a prompt whatever its source; identity when the store has none."""
    red = getattr(store, "redact", None)
    return red if callable(red) else (lambda t: t)


def shell_command(m: dict, red=lambda t: t) -> str | None:
    """A Bash step's command, as the block shows it (redacted, newlines marked, cut head-and-tail when over the cap)."""
    cmd = _tool_input(m).get("command")
    if not cmd:
        return None
    one = red(str(cmd)).strip().replace("\r\n", "\n").replace("\n", NEWLINE_MARK)
    return head_tail(one, COMMAND_CAP, mark=" […] ")


def build_packet(seat: str, *, window_tokens, store) -> dict:
    """The block for `seat`, from `store` (episodic_memory or a stand-in with the same readers).
    Returns {"text", "chars", "tokens_est", "budget_chars", "counts", "trimmed"}."""
    budget = budget_chars(window_tokens)
    parts, counts, trimmed = [header(seat)], {}, {}

    # 2. every operator message, verbatim, oldest first; the oldest go first when over their share
    ops = [e for e in (store.operator_messages(seat) or []) if str(e.get("text") or "").strip()]
    cap = int(budget * SHARE_OPERATOR)
    lines = [_line(e) for e in ops]
    dropped = 0
    while lines and sum(len(x) + 1 for x in lines) > cap and len(lines) > 1:
        lines.pop(0)
        dropped += 1
    if lines and len(lines[0]) > cap:
        lines[0] = head_tail(lines[0], cap)
    counts["operator_messages"] = len(lines)
    trimmed["operator_messages"] = dropped
    sec = ["## The operator's messages to you, oldest first, word for word"]
    if dropped:
        sec.append(f"({dropped} older message{'s' if dropped != 1 else ''} not shown here: search for them.)")
    sec += lines or ["(none)"]
    parts.append("\n".join(sec))

    # 3. the current objective
    directive = next((e for e in reversed(ops) if is_directive(str(e.get("text")))), None)
    parts.append("## What you are working on now\n" + (_line(directive, head_tail(str(directive["text"]), 4000))
                                                       if directive else "(no instruction on record)"))

    # 4. open tasks, approval cards, scheduled wakeups
    rec = store.open_records(seat) or {}
    wake = []
    try:
        wake = store.wakeups(seat) or []
    except AttributeError:
        wake = []
    rows = []
    for t in rec.get("tasks") or rec.get("open_tasks") or []:
        rows.append(f"- task [{t.get('id') or t.get('tool_id')}] {t.get('text') or t.get('subject')}"
                    + (f" ({t['status']})" if t.get("status") else ""))
    cards = rec.get("approvals") or rec.get("cards") or rec.get("approval_cards") or []
    for c in cards:
        rows.append(f"- approval card [{c.get('id') or c.get('card_id')}] {c.get('text') or c.get('question')}"
                    + (f" ({c['status']})" if c.get("status") else ""))
    for w in wake:
        what = " ".join(str(x) for x in (w.get("schedule"), w.get("prompt") or w.get("text")) if x)
        rows.append(f"- scheduled wakeup [{w.get('id') or w.get('tool_id')}] {w.get('tool') or w.get('kind') or ''}"
                    + (f": {what}" if what else ""))
    counts.update(tasks=len(rec.get("tasks") or rec.get("open_tasks") or []), cards=len(cards), wakeups=len(wake))
    parts.append("## Open tasks, approval cards and scheduled wakeups\n" + ("\n".join(rows) or "(none)"))

    # 5. the recent conversation, repaired, newest kept first, the latest user and assistant message always there
    rcap = int(budget * SHARE_RECENT)
    entries = store.recent_window(seat, rcap) or []
    msgs = sanitize_history(to_messages(entries))
    counts["repaired"] = len(to_messages(entries)) - len(msgs)
    talk = [m for m in msgs if m["role"] in ("user", "assistant") and m.get("content")]
    last_user = next((m for m in reversed(talk) if m["role"] == "user"), None)
    last_asst = next((m for m in reversed(talk) if m["role"] == "assistant"), None)
    must = {id(m) for m in (last_user, last_asst) if m is not None}
    rendered, used = {}, 0
    for m in (last_user, last_asst):
        if m is not None:
            s = f"[{m['id']}] {m['role']}: " + head_tail(str(m["content"]), max(400, rcap // 3))
            rendered[id(m)] = s
            used += len(s) + 1
    shown = 0
    for m in reversed(talk):
        if id(m) in must:
            continue
        s = f"[{m['id']}] {m['role']}: {m['content']}"
        if used + len(s) + 1 > rcap:
            break
        rendered[id(m)] = s
        used += len(s) + 1
    order = [rendered[id(m)] for m in talk if id(m) in rendered]
    counts["recent_messages"] = len(order)
    trimmed["recent_messages"] = len(talk) - len(order)
    sec = ["## The latest part of the conversation, word for word"]
    if trimmed["recent_messages"]:
        sec.append(f"({trimmed['recent_messages']} earlier message{'s' if trimmed['recent_messages'] != 1 else ''} "
                   f"not shown here: search for them.)")
    parts.append("\n".join(sec + (order or ["(none)"])))

    # the facts, from records only: decisions on record and files changed, each citing its id
    known = {_eid(e) for e in entries} | {str(e.get("tool_id")) for e in entries if e.get("tool_id")}
    facts_in = [{"text": str(e.get("text")), "id": _eid(e)} for e in entries if e.get("kind") == "decision"]
    for m in msgs:
        if m.get("tool_calls") and m["tool_calls"][0]["name"] in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            try:
                fp = json.loads(str(m["entry"].get("text") or "").split(" ", 1)[1]).get("file_path")
            except (IndexError, ValueError, AttributeError):
                fp = None
            if fp:
                facts_in.append({"text": f"file changed: {fp}", "id": m["tool_calls"][0]["id"]})
    latest = {}
    for f in facts_in:                                     # one line per fact: the latest id that carries it
        latest.pop(f["text"], None)
        latest[f["text"]] = f
    facts = checked_facts(list(latest.values()), known)
    counts["facts"] = len(facts)
    parts.append("## Facts on record\n" + ("\n".join(facts) or "(none)"))

    # 6. the work steps
    red = _redactor(store)
    calls = {m["tool_calls"][0]["id"]: m for m in msgs if m.get("tool_calls")}
    results = {m["tool_call_id"]: m for m in msgs if m.get("tool_call_id")}
    steps, scap, sused = [], int(budget * SHARE_STEPS), 0
    for cid, m in reversed(list(calls.items())):
        entry = m["entry"]
        name = m["tool_calls"][0]["name"]
        res = results.get(cid)
        rtext = str(res["content"]) if res else ""
        status = "error" if (res and (res["entry"].get("is_error") or re.match(r"\s*(error|fatal|traceback)",
                                                                                 rtext, re.I))) else "ok"
        files = ""
        try:
            inp = json.loads(str(entry.get("text") or "").split(" ", 1)[1])
            fp = inp.get("file_path") or inp.get("notebook_path")
            files = f" file {fp}" if fp else ""
        except (IndexError, ValueError, AttributeError):
            pass
        cmd = shell_command(m, red) if name == "Bash" else None
        tail = red(rtext[-STEP_TAIL:]).replace("\n", " ").strip()
        s = f"- [{cid}] {name} {status}{files}" + (f" $ {cmd}" if cmd else "") + (f": …{tail}" if tail else "")
        if sused + len(s) + 1 > scap:
            break
        steps.append(s)
        sused += len(s) + 1
    counts["work_steps"] = len(steps)
    parts.append("## Work steps, newest first\n" + ("\n".join(steps) or "(none)"))

    parts.append(CONTINUE)
    body = "\n\n".join(parts)
    if len(body) > budget:
        body = head_tail(body, budget)
    text = f"{OPEN}\n{body}\n{CLOSE}"
    open_ids = [str(t.get("id") or t.get("tool_id")) for t in rec.get("tasks") or rec.get("open_tasks") or []] + \
        [str(c.get("id") or c.get("card_id")) for c in cards] + \
        [str(w.get("id") or w.get("tool_id")) for w in wake]
    # The check never asks for what this block does not hold: each question's expected value must appear, as written,
    # in the final body (a step the budget left out, a command over the cap, a message cut head-and-tail: not asked).
    asked = check_questions(ops, msgs, cards, rec, wake, red=red)
    check = {"questions": [q for q in asked if q["expected"] and q["expected"] in body]}
    counts["check_not_asked"] = [q["kind"] for q in asked if q not in check["questions"]]
    return {"text": text, "chars": len(text), "tokens_est": -(-len(text) // CHARS_PER_TOKEN),
            "budget_chars": budget, "counts": counts, "trimmed": trimmed, "check": check}


# --- the continuity check: questions with ONE answer each, checked against the recorded entry ------------------

_LONG_QUOTE = 600
#: The pre-switch check must ask, and pass, at least this many questions with a checkable answer on record.
MIN_QUESTIONS = 2
#: The first sentence of a reply is only asked when it carries this much (a bare "Done." checks nothing).
MIN_REPLY_SENTENCE = 20


def _first_sentence(text: str) -> str:
    m = re.match(r"\s*(.+?[.!?])(\s|$)", text, re.S)
    return (m.group(1) if m else text[:200]).strip()


def _tool_input(m: dict) -> dict:
    try:
        return json.loads(str(m["entry"].get("text") or "").split(" ", 1)[1])
    except (IndexError, ValueError, AttributeError, KeyError):
        return {}


def check_questions(ops: list, msgs: list, cards: list, rec: dict, wake: list, red=lambda t: t) -> list[dict]:
    """What the copy is asked, each with the one value the records hold for it (a question without a recorded value
    is not asked): the operator's last message word for word (its first sentence when long), the last file written,
    the last command run, the pending card's id, the latest open task's title."""
    qs = []
    if ops:
        text = str(ops[-1].get("text") or "").strip()
        if len(text) > _LONG_QUOTE:
            qs.append({"kind": "last_message", "q": "Quote the first sentence of my last message to you, word for word.",
                       "expected": _first_sentence(text), "id": _eid(ops[-1])})
        elif text:
            qs.append({"kind": "last_message", "q": "Quote my last message to you, word for word.",
                       "expected": text, "id": _eid(ops[-1])})
    reply = next((m for m in reversed(msgs) if m.get("role") == "assistant" and not m.get("tool_calls")
                  and str(m.get("content") or "").strip()), None)
    if reply is not None:
        first = _first_sentence(str(reply["content"]).strip())
        if len(first) >= MIN_REPLY_SENTENCE:
            qs.append({"kind": "last_reply", "q": "Quote the first sentence of your last reply, word for word.",
                       "expected": first, "id": reply["id"]})
    calls = [m for m in msgs if m.get("tool_calls")]
    wrote = next((m for m in reversed(calls) if m["tool_calls"][0]["name"] in ("Write", "Edit", "MultiEdit",
                                                                             "NotebookEdit")), None)
    if wrote is not None:
        fp = _tool_input(wrote).get("file_path") or _tool_input(wrote).get("notebook_path")
        if fp:
            qs.append({"kind": "last_file", "q": "Name the last file you wrote or edited (its full path).",
                       "expected": str(fp), "id": wrote["tool_calls"][0]["id"]})
    ran = next((m for m in reversed(calls) if m["tool_calls"][0]["name"] == "Bash"), None)
    if ran is not None and _tool_input(ran).get("command"):
        cmd = red(str(_tool_input(ran)["command"])).strip()
        if len(cmd) <= COMMAND_CAP and "\n" not in cmd:  # exactly as its step shows it, or not asked
            qs.append({"kind": "last_command", "q": "What was the last command you ran? Give it exactly.",
                       "expected": cmd, "id": ran["tool_calls"][0]["id"]})
    if cards:
        c = cards[-1]
        cid = str(c.get("id") or c.get("card_id") or "")
        if cid:
            qs.append({"kind": "card", "q": "What is the id of the pending approval card?", "expected": cid,
                       "id": cid})
    tasks = rec.get("tasks") or rec.get("open_tasks") or []
    if tasks and str(tasks[-1].get("text") or tasks[-1].get("subject") or "").strip():
        t = tasks[-1]
        qs.append({"kind": "task", "q": "What is the title of the latest open task? Give it exactly.",
                   "expected": str(t.get("text") or t.get("subject")).strip(),
                   "id": str(t.get("id") or t.get("tool_id") or "")})
    return qs


def thin_check(packet: dict) -> dict | None:
    """Why a block's check is too thin to switch on (fewer than MIN_QUESTIONS questions it can answer), or None:
    {"asked": [kinds], "not_asked": [kinds dropped because the block does not hold their answer]}."""
    qs = (packet.get("check") or {}).get("questions") or []
    if len(qs) >= MIN_QUESTIONS:
        return None
    return {"asked": [q["kind"] for q in qs], "not_asked": list((packet.get("counts") or {}).get("check_not_asked") or [])}


def check_message(questions: list[dict]) -> str:
    """The copy's check: the questions, nothing about what the answers should be."""
    if not questions:
        return "Before you go on: reply with the single word OK."
    body = "\n".join(f"## Q{i}\n{q['q']}" for i, q in enumerate(questions, 1))
    return ("Before you go on, a short check, answered only from the conversation above. Answer each question "
            "under its heading with the answer alone.\n\n" + body)


CHECK_QUESTION = check_message([])

_QUOTES = "\"'`\u201c\u201d\u2018\u2019\u00ab\u00bb"


def normalise_answer(text: str) -> str:
    """Whitespace, case, quotes and trailing punctuation do not count."""
    t = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    t = t.strip(_QUOTES + " ")
    return t.rstrip(".!?;:, ").strip(_QUOTES + " ")


def _sections(reply: str, n: int) -> dict:
    hits = list(re.finditer(r"^\s*#{0,6}\s*\**\s*q(\d+)\b[^\n]*\n?", reply or "", re.I | re.M))
    out = {}
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(reply)
        out[int(m.group(1))] = reply[m.end():end].strip()
    return out


def grade_check(reply: str, check: dict) -> dict:
    """Each answer against the recorded value (normalised: the answer must hold the value whole; an id must appear
    as a whole token). Nothing checkable on record: a non-empty reply is the pass (liveness), and says so."""
    qs = (check or {}).get("questions") or []
    reply = reply or ""
    if len(qs) < MIN_QUESTIONS:
        # Too little on record to tell a good copy from a bad one: never a pass (a switch is refused before this).
        return {"pass": False, "results": [], "missing": [], "too_thin": True,
                "note": f"fewer than {MIN_QUESTIONS} checkable questions: not a check"}
    answers = _sections(reply, len(qs))
    results = []
    for i, q in enumerate(qs, 1):
        ans = answers.get(i, "")
        exp = str(q["expected"])
        if q["kind"] == "card":
            ok = bool(re.search(r"(?<![\w-])" + re.escape(exp) + r"(?![\w-])", ans))
        else:
            ok = bool(normalise_answer(exp)) and normalise_answer(exp) in normalise_answer(ans)
        results.append({"kind": q["kind"], "question": q["q"], "expected": exp[:300], "answer": ans[:300],
                        "pass": ok})
    return {"pass": all(r["pass"] for r in results), "results": results,
            "missing": [r["kind"] for r in results if not r["pass"]]}
