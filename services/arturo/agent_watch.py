"""agent_watch.py — work Arturo hands to an agent is a job in the drawer until the agent answers.

A user report: Arturo asked the GM to rename seats on a call, the GM did it and replied, and none of
it showed: "I'd expect that to have shown up in the job drawer." Agents reply to the message's sender
("arturo"), and nothing read those replies (the router logs them as unknown-address).

A watch: the message Arturo sent is the first step; the agent's replies are steps as they arrive; the first
reply that is not an interim ("NOT DONE — carded", "on it") ends the job, and its text is the job's result
(posted into the conversation, spoken on a live call). Replies it consumed are archived.
"""
import re
import time

WATCH_TIMEOUT_S = 2 * 3600
POLL_S = 5.0
_TAG_RE = re.compile(r"\[[^\]]*\]")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]*")
_INTERIM_RE = re.compile(r"^\W*(not done|in progress|working on|started|on it|blocked|carded|queued|ack\b|acknowledged,? working)",
                         re.I)
MSG_ID_RE = re.compile(r"\b(msg_[0-9a-f]{8}_\d+)\b")


def _words(subject):
    s = _TAG_RE.sub(" ", (subject or "").lower())
    s = re.sub(r"^\s*(re:\s*)+", "", s.strip())
    return _WORD_RE.findall(s)


def subject_matches(original, reply):
    """A reply's subject ("re: Rename first-project team — DONE") answers the original's
    ("[unverified caller] Rename first-project team to Acme"): their first three words agree."""
    a, b = _words(original), _words(reply)
    n = min(3, len(a))
    return n > 0 and b[:n] == a[:n]


def is_reply(orig, msg):
    if msg.get("from_agent") != orig.get("to_agent") or msg.get("to_agent") != orig.get("from_agent"):
        return False
    if str(msg.get("created_at") or "") <= str(orig.get("created_at") or ""):
        return False
    return (msg.get("parent_id") == orig.get("id") or msg.get("conversation_id") == orig.get("conversation_id")
            or subject_matches(orig.get("subject"), msg.get("subject")))


def interim(msg):
    text = f"{msg.get('subject') or ''}"
    body = (msg.get("body") or "").strip()
    head = re.sub(r"^\s*(re:\s*)+", "", _TAG_RE.sub("", text), flags=re.I)
    return bool(_INTERIM_RE.match(body) or "carded" in head.lower() or "not done" in head.lower())


def watch(store, msg_id, step, cancelled, clock=time.time, sleep=time.sleep, timeout_s=WATCH_TIMEOUT_S,
          scrub=lambda t: t):
    """Block until the agent answers `msg_id` (or the timeout / a cancel). Returns (text, ok)."""
    orig = store.get(msg_id)
    if not orig:
        return f"The message {msg_id} is not in the message store.", False
    to = orig.get("to_agent") or "the agent"
    step({"phase": "start", "call_id": "sent", "name": f"message to {to}", "args_summary": scrub(orig.get("subject") or "")[:200]})
    step({"phase": "done", "call_id": "sent", "name": f"message to {to}", "ok": True, "summary": "sent"})
    step({"phase": "start", "call_id": "wait", "name": f"waiting on {to}", "args_summary": ""})
    seen, delivered, deadline = set(), False, clock() + timeout_s
    while not cancelled.is_set() and clock() < deadline:
        cur = store.get(msg_id) or {}
        if not delivered and cur.get("status") in ("delivered", "acknowledged", "archived"):
            delivered = True
            step({"phase": "start", "call_id": "wait", "name": f"{to} is working on it", "args_summary": ""})
        try:
            rows = store.query(to_agent=orig.get("from_agent"), from_agent=to, since=orig.get("created_at"), limit=50)
        except Exception:  # noqa: BLE001 — a store hiccup is a missed poll, not a failed job
            rows = []
        for msg in sorted(rows, key=lambda r: str(r.get("created_at") or "")):
            if msg.get("id") in seen or not is_reply(orig, msg):
                continue
            seen.add(msg.get("id"))
            body = scrub((msg.get("body") or "").strip())
            try:
                store.archive(msg["id"])
            except Exception:  # noqa: BLE001
                pass
            step({"phase": "start", "call_id": msg["id"], "name": f"{to} replied", "args_summary": ""})
            step({"phase": "done", "call_id": msg["id"], "name": f"{to} replied", "ok": True,
                  "summary": scrub(_TAG_RE.sub("", msg.get("subject") or "").strip())[:200]})
            if not interim(msg):
                step({"phase": "done", "call_id": "wait", "name": f"{to} answered", "ok": True, "summary": ""})
                return body or "(an empty reply)", True
        sleep(POLL_S)
    if cancelled.is_set():
        return "stopped watching", False
    return f"No answer from {to} yet (watched for {int(timeout_s // 60)} minutes). Its reply will still reach it later.", False
