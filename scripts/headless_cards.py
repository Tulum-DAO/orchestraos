"""Questions and permissions of a headless seat as the existing 4c cards (scripts/seat_supervisor.py).

A headless seat's CLI asks over stdin, not on a screen: AskUserQuestion and whatever bypass still asks arrive as
`can_use_tool` control requests, which the supervisor holds. This module turns one held request into ONE card in
the approval ledger, the same kind='menu' card the menu bridge makes from a terminal menu (a single question as a
plain menu; several questions, or a multi-select one, as the multi-part card), and carries the person's answer back
as the control response: allow + updatedInput.answers for a question, allow / deny for a permission.

EXACTLY ONCE. A bridged card's answer reaches the agent only as that control response:
  - approval_resume.fire_resume hands a bridged row here before any other lane, so no `approval_resolved` inbox
    row is written, nothing is injected and no keys are pressed;
  - the supervisor answers a request at most once (a second hand-over of the same card returns `already`);
  - the multi-part submit (watch_gateway.durable_first_batch_submit) persists the batch and hands it here
    instead of replaying keys into a pane;
  - a dismissed bridged card is answered "deny, dismissed" the same way, never by an inbox notice.
The card's own state carries the outcome: resumed once handed over; resume_failed when the process that asked is
gone (the question died with it; the agent is never told twice); resolved_elsewhere when the request went away
before anyone answered (answered at the supervisor, cancelled, or the process ended).

The bridge marker lives in the card's menu JSON (`bridge`), with origin 'headless': the menu bridge's
auto-resolvers touch origin 'menu_bridge' rows only, and the op_key keeps the menu bridge's session prefix
(`menu:<session>:hl-…`) so the multi-part submit finds the card by session like any other.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time

BRIDGE_KIND = "headless"
ORIGIN = "headless"
FREE_TEXT_LABEL = "Type something."
DENY_WITH_REASON = "No, and say why"
#: Card text limits (the card shows them; the full request stays with the supervisor).
_Q_MAX = 300
_LABEL_MAX = 100
_DETAIL_MAX = 200


def _clip(v, n):
    t = " ".join(str(v or "").split())
    return t if len(t) <= n else t[:n - 1] + "…"


def op_key(session: str, request_id: str) -> str:
    return f"menu:{session}:hl-{hashlib.sha256(request_id.encode()).hexdigest()[:16]}"


def _options(raw, *, free_text=True):
    out = []
    for i, o in enumerate(raw or []):
        if not isinstance(o, dict) or not str(o.get("label") or "").strip():
            continue
        opt = {"n": str(len(out) + 1), "label": _clip(o["label"], _LABEL_MAX), "input_kind": "direct"}
        if o.get("description"):
            opt["detail"] = _clip(o["description"], _DETAIL_MAX)
        out.append(opt)
    if free_text:
        out.append({"n": str(len(out) + 1), "label": FREE_TEXT_LABEL, "input_kind": "free_text"})
    return out


def _permission_what(tool: str, inp: dict) -> str:
    what = next((str(inp[k]) for k in ("command", "file_path", "url", "description", "path", "pattern")
                 if isinstance(inp, dict) and inp.get(k)), "")
    return _clip(what, 200)


def card_payload(seat: str, session: str, request_id: str, request: dict) -> dict:
    """The card for one held control request: {question, options, menu, summary, op_key}. Pure."""
    tool = (request or {}).get("tool_name") or ""
    inp = (request or {}).get("input") if isinstance((request or {}).get("input"), dict) else {}
    bridge = {"kind": BRIDGE_KIND, "seat": seat, "session": session, "request_id": request_id, "tool": tool}
    qs = inp.get("questions") if tool == "AskUserQuestion" else None
    qs = [q for q in (qs or []) if isinstance(q, dict) and str(q.get("question") or "").strip()]
    if qs:
        bridge["questions"] = [str(q["question"]) for q in qs]
        first = qs[0]
        if len(qs) == 1 and not first.get("multiSelect"):
            opts = _options(first.get("options"))
            menu = {"question": _clip(first["question"], _Q_MAX), "options": opts,
                    "source_session": session, "captured_at": time.time(), "bridge": bridge}
            return {"question": menu["question"], "options": [o["label"] for o in opts], "menu": menu,
                    "summary": f"{seat} is asking (headless).", "op_key": op_key(session, request_id)}
        parts = []
        for i, q in enumerate(qs):
            parts.append({"index": i, "question": _clip(q["question"], _Q_MAX),
                          "options": _options(q.get("options")),
                          "select": "multi" if q.get("multiSelect") else "single",
                          "tab_label": _clip(q.get("header") or f"Question {i + 1}", 40)})
        tabs = [p["tab_label"] for p in parts] + ["Submit"]
        menu = {"question": parts[0]["question"], "options": parts[0]["options"],
                "source_session": session, "captured_at": time.time(),
                "multipart": True, "part_count": len(parts), "part_index": 0, "walk_complete": True,
                "parts": parts, "tabs": tabs, "submit_tab_index": len(parts), "has_submit": True,
                **({"checkbox": True} if any(p["select"] == "multi" for p in parts) else {}),
                "bridge": bridge}
        return {"question": menu["question"], "options": [o["label"] for o in parts[0]["options"]],
                "menu": menu, "summary": f"{seat} is asking {len(parts)} question"
                + ("s" if len(parts) > 1 else "") + " (headless).", "op_key": op_key(session, request_id)}
    what = _permission_what(tool, inp)
    question = _clip(f"Allow {tool or 'this tool'}?" + (f" {what}" if what else ""), _Q_MAX)
    opts = [{"n": "1", "label": "Allow", "input_kind": "direct"},
            {"n": "2", "label": "Deny", "input_kind": "direct"},
            {"n": "3", "label": DENY_WITH_REASON, "input_kind": "free_text"}]
    menu = {"question": question, "options": opts, "source_session": session, "captured_at": time.time(),
            "perm_shaped": True, "bridge": dict(bridge, permission=True)}
    return {"question": question, "options": [o["label"] for o in opts], "menu": menu,
            "summary": f"{seat} wants to use {tool or 'a tool'} (headless)."
                       + (f"\n\n`{what}`" if what else ""), "op_key": op_key(session, request_id)}


def _menu(row):
    m = (row or {}).get("menu")
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except ValueError:
            return {}
    return m if isinstance(m, dict) else {}


def bridge_of(row) -> dict | None:
    b = _menu(row).get("bridge")
    return b if isinstance(b, dict) and b.get("kind") == BRIDGE_KIND and b.get("request_id") else None


def is_bridged(row) -> bool:
    return bridge_of(row) is not None


def _label(opts, n):
    for o in opts or []:
        if isinstance(o, dict) and str(o.get("n")) == str(n):
            return o
    return None


def outcome_for(row) -> dict | None:
    """What the answered card says, as the supervisor's resolve outcome:
      {"kind": "question", "answers": {question text: answer}}   (AskUserQuestion's updatedInput.answers)
      {"kind": "permission", "allow": bool, "message"?: str}
    None when the row holds no usable answer."""
    b = bridge_of(row)
    if b is None:
        return None
    menu = _menu(row)
    if b.get("permission"):
        opt = _label(menu.get("options"), row.get("option_n"))
        label = (opt or {}).get("label")
        if label == "Allow":
            return {"kind": "permission", "allow": True}
        words = (row.get("answer_text") or "").strip()
        msg = "Denied on the card." + (f" The operator says: {words}" if words else "")
        return {"kind": "permission", "allow": False, "message": msg}
    questions = b.get("questions") or [menu.get("question")]
    if row.get("answer") == "batch":
        try:
            batch = json.loads(row.get("answer_text") or "[]")
        except ValueError:
            return None
        parts = {p.get("index"): p for p in menu.get("parts") or [] if isinstance(p, dict)}
        answers = {}
        for a in batch if isinstance(batch, list) else []:
            if not isinstance(a, dict) or a.get("skipped") is True:
                continue
            pi = a.get("part")
            part = parts.get(pi) or {}
            picked = []
            for n in a.get("ns") or []:
                o = _label(part.get("options"), n)
                if o and o.get("input_kind") != "free_text":
                    picked.append(o["label"])
            if a.get("text"):
                picked.append(str(a["text"]).strip())
            if isinstance(pi, int) and pi < len(questions) and picked:
                answers[questions[pi]] = ", ".join(picked)
        return {"kind": "question", "answers": answers} if answers else None
    opt = _label(menu.get("options"), row.get("option_n"))
    if opt is None:
        return None
    value = (row.get("answer_text") or "").strip() if opt.get("input_kind") == "free_text" else opt["label"]
    if not value:
        return None
    return {"kind": "question", "answers": {questions[0]: value}}


def _set_status_if(store, rid, new, was):
    c = store._conn()
    try:
        res = c.execute("UPDATE approval_requests SET status=? WHERE id=? AND status=?", [new, rid, was])
        c.commit()
        return res.rowcount > 0
    finally:
        c.close()


def deliver(row, store, *, hs=None, outcome=None) -> dict:
    """Hand an answered (or dismissed) bridged card to the seat's supervisor, exactly once. Returns
    {id, delivered, reason}. Store transitions: answered -> resumed on hand-over (the agent does not
    ack a card it never saw); answered -> resume_failed when the request is gone; a supervisor that
    does not answer leaves the row answered for the watchdog's next beat (stamp_retry)."""
    if hs is None:
        import headless_switch as hs
    b = bridge_of(row) or {}
    rid = row["id"]
    outcome = outcome or outcome_for(row)
    if outcome is None:
        store.mark_resume_failed(rid, "the card's answer could not be read as an answer to the question")
        return {"id": rid, "delivered": False, "reason": "unreadable_answer"}
    try:
        out = hs.request(b["seat"], {"cmd": "resolve", "request_id": b["request_id"], "card_id": rid,
                                     "outcome": outcome})
    except hs.SupervisorDown as e:
        store.stamp_retry(rid)
        return {"id": rid, "delivered": False, "reason": "supervisor_down", "detail": str(e)}
    if out.get("ok"):
        store.ack(rid)
        return {"id": rid, "delivered": True, "reason": "already" if out.get("already") else "delivered"}
    reason = out.get("reason") or "refused"
    if reason == "no-such-request":
        store.mark_resume_failed(rid, "the agent's question ended before the answer arrived "
                                      "(its process stopped); nothing was sent to it")
    else:
        store.stamp_retry(rid)
    return {"id": rid, "delivered": False, "reason": reason}


def dismissed_outcome(row) -> dict:
    b = bridge_of(row) or {}
    return {"kind": "permission" if b.get("permission") else "dismissed", "allow": False,
            "message": "The operator dismissed this without answering. Do not wait on it; "
                       "carry on by your own judgment or ask again if it still matters."}


def deliver_dismissed(row, *, hs=None) -> dict:
    """A dismissed bridged card: the held request is answered 'deny' with that reason, once, so the
    agent is never left waiting; no inbox notice."""
    if hs is None:
        import headless_switch as hs
    b = bridge_of(row) or {}
    try:
        out = hs.request(b["seat"], {"cmd": "resolve", "request_id": b["request_id"], "card_id": row["id"],
                                     "outcome": dismissed_outcome(row)})
    except hs.SupervisorDown as e:
        return {"id": row["id"], "delivered": False, "reason": "supervisor_down", "detail": str(e)}
    return {"id": row["id"], "delivered": bool(out.get("ok")), "reason": out.get("reason")}


# --- the supervisor's side --------------------------------------------------------------------------

def _store(store=None):
    if store is not None:
        return store
    import os
    from approval_schema import ApprovalStore
    # APPROVAL_DB_PATH as approval.py reads it (a scratch ledger for a proof or a test); else the live one.
    s = ApprovalStore(db_path=os.environ.get("APPROVAL_DB_PATH") or None)
    s.migrate()
    return s


def file_card(seat: str, session: str, request_id: str, request: dict, *, store=None, notify=None) -> str:
    """File one card for a held request; its id. Deduped by op_key: a second call for the same
    request returns the existing pending card."""
    st = _store(store)
    p = card_payload(seat, session, request_id, request)
    for row in st.find_by_op_key(p["op_key"]) or []:
        if row.get("status") == "pending":
            return row["id"]
    rid = st.create(from_agent=seat, question=p["question"], worker_kind="pane", kind="menu",
                    menu=p["menu"], options=p["options"], op_key=p["op_key"], summary=p["summary"],
                    origin=ORIGIN)
    import os
    if notify is None and os.environ.get("HEADLESS_CARDS_NOTIFY", "").strip() != "0":
        try:
            from approval_notify import notify as notify
        except Exception:  # noqa: BLE001 — no push: the card still shows on every surface
            notify = None
    if notify is not None:
        try:
            notify(rid, store=st)
        except Exception as e:  # noqa: BLE001 — the notify backstop re-sends
            print(f"[headless_cards] notify deferred for {rid}: {e}", file=sys.stderr)
    return rid


def withdraw(card_id: str, *, store=None) -> bool:
    """The request went away before anyone answered the card: pending -> resolved_elsewhere (the
    state the menu bridge gives a menu that left the screen). An answered card is left alone; its
    delivery reports the outcome."""
    return _set_status_if(_store(store), card_id, "resolved_elsewhere", "pending")


def withdraw_seat(seat: str, session: str, *, store=None) -> list:
    """A supervisor (re)start holds no request: every pending bridged card of the seat belongs to a
    process that is gone. Returns the ids withdrawn."""
    st = _store(store)
    like = f"menu:{session}:hl-%"
    c = st._conn()
    try:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM approval_requests WHERE status='pending' AND origin=? AND from_agent=? "
            "AND op_key LIKE ?", [ORIGIN, seat, like]).fetchall()]
    finally:
        c.close()
    return [r["id"] for r in rows if is_bridged(r) and withdraw(r["id"], store=st)]


def response_for(request: dict, outcome: dict) -> dict:
    """The control response for a held request and a card's outcome. Pure."""
    inp = (request or {}).get("input") if isinstance((request or {}).get("input"), dict) else {}
    kind = (outcome or {}).get("kind")
    if kind == "question" and isinstance(outcome.get("answers"), dict):
        return {"behavior": "allow", "updatedInput": {**inp, "answers": dict(outcome["answers"])}}
    if kind == "permission" and outcome.get("allow") is True:
        return {"behavior": "allow", "updatedInput": inp}
    return {"behavior": "deny", "message": str((outcome or {}).get("message") or "Denied on the card.")}
