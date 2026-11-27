"""gm_answers.py — gm's answer to a question a voice call asked lands in that call's chat thread, once.

gm_command (arturo-proxy.py _ask_brain) sends gm ONE msg_store message per question, tagged in its metadata with the
call (voice_call) and the page thread it was asked from (thread_id). A reply within the bounded wait is spoken in the
call; a later one used to sit in msg_store addressed to `arturo` with nothing reading it, while the call had told the
operator "the answer comes to your chat" (Shaw's call native-35592a853127, 2026-11-26: gm answered after the hang-up
and the chat never showed it). This is the reader of those replies.

Each pending reply from gm to `arturo` whose question carried a voice call becomes ONE assistant line in that
question's thread, whether it was already spoken in the call or came after it. A call still live is left for the next
sweep, so the answer lands after the call's own lines. The line is deduped on the reply's id (a repeat sweep, a second
process, a hand backfill with the same meta), then the reply is acked and stamped with what happened. A reply that is
not for a voice question is not ours and is left alone. A thread the caller did not start, or a tag that is not a
thread at all ("gm chat"), is acked with no line.

Never raises into its caller: a sweep that cannot read or write leaves the reply pending for the next one.
"""
import json
import logging

log = logging.getLogger("arturo.gm_answers")

ANSWER_MAX = 4000
KIND = "gm_answer"


def _meta(raw):
    try:
        out = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _question_of(store, reply):
    """The message gm answered: the reply's parent, else the first message from arturo in its conversation."""
    pid = reply.get("parent_id")
    if pid:
        q = store.get(pid)
        if q:
            return q
    conv = reply.get("conversation_id")
    if conv:
        for m in store.thread(conv):
            if m.get("from_agent") == "arturo":
                return m
    return None


def _question_words(subject):
    """`[voice thread <t> call <c>] <question>` -> the question."""
    s = str(subject or "")
    return s.split("] ", 1)[1].strip() if s.startswith("[voice") and "] " in s else s.strip()


def answer_line(question, body):
    from services.arturo.thread_context import _redact
    text = _redact(str(body or "").strip())
    if len(text) > ANSWER_MAX:
        text = text[:ANSWER_MAX].rstrip() + " ... (truncated)"
    q = " ".join(_redact(question).split())
    return (f"gm answered a question from the call" + (f" (\"{q}\")" if q else "") + f":\n\n{text}")


def _may_write(threads, thread_id, principal):
    """The proxy's thread rule: the fleet writes any thread; any other caller only a thread it started."""
    started = threads.started_by(thread_id)
    if started is None:
        return "no_thread"
    if principal == "fleet" or (principal and started == principal):
        return None
    return "not_owner"


def _replies(store, to_agent, gm):
    out = []
    for status in ("pending", "delivered"):
        out.extend(m for m in store.inbox(to_agent, status=status, type="reply")
                   if m.get("from_agent") == gm)
    return out


def post_gm_answers(store, threads, live_calls=frozenset, gm="gm", to_agent="arturo"):
    """One sweep. `store` = msg_store.MessageStore, `threads` = thread_store.ThreadStore, `live_calls()` = the
    conversation ids of calls still up. Returns [(reply id, state)] for every reply it looked at."""
    from services.arturo.call_threads import valid_thread_id
    from services.arturo.relay_live_bridge import journal_call_id
    done = []
    try:
        replies = _replies(store, to_agent, gm)
    except Exception as e:  # noqa: BLE001 — the next sweep tries again
        log.warning(f"gm answers: inbox unreadable: {e!r}")
        return done
    live = None
    for r in replies:
        mid = r.get("id")
        try:
            q = _question_of(store, r)
            qmeta = _meta((q or {}).get("metadata"))
            call = str(qmeta.get("voice_call") or "")
            if not call:
                continue                                   # not a voice question: not ours
            if live is None:
                live = set(live_calls())
            if call in live:
                done.append((mid, "call_live"))
                continue
            thread, principal = str(qmeta.get("thread_id") or ""), qmeta.get("principal")
            body = (r.get("body") or "").strip()
            if not body:
                state, seq = "empty", None
            elif not valid_thread_id(thread):
                state, seq = "no_thread", None
            else:
                state, seq = _may_write(threads, thread, principal), None
                if state is None:
                    if threads.has_meta(thread, kind=KIND, msg_id=mid):
                        state = "already"
                    else:
                        seq = threads.append_message(
                            thread, "assistant", answer_line(_question_words((q or {}).get("subject")), body),
                            meta={"kind": KIND, "call_id": journal_call_id(call), "msg_id": mid,
                                  "conversation_id": call})
                        if seq is None:
                            done.append((mid, "post_failed"))      # left pending: the next sweep retries
                            log.warning(f"gm answers: {mid} not posted to {thread[:24]}")
                            continue
                        state = "posted"
            store.acknowledge(mid)
            store.stamp_metadata(mid, chat_log=state, chat_thread=thread or None, chat_seq=seq)
            done.append((mid, state))
            log.info(f"gm answers: {mid} (call {call}) -> {state}" + (f" {thread[:24]}#{seq}" if seq is not None else ""))
        except Exception as e:  # noqa: BLE001 — one bad row never stops the sweep
            log.warning(f"gm answers: {mid} failed: {e!r}")
            done.append((mid, "error"))
    return done
