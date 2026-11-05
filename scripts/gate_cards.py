"""gate_cards.py — the safety gate's permission cards and their answer lane (attention part 2a, plan §5.1/§5.3).

A held tool call (a gate_holds row, supervision_store) is shown as ONE ledger card: kind='menu', origin='gate',
op_key 'gate:<hold id>', with a `bridge` {"kind": "gate", "permission": true, "hold_id", "hits", "watch_ok"} in its
menu JSON. Its answer never takes a menu lane (no keypress, no inject, no digest): approval_resume.fire_resume hands
a gate card here first, and deliver() is the whole lane:

  grants (one per held hit; idempotent by grant_key) -> hold CAS (waiting|open -> allowed|denied) -> card ack ->
  for an `open` hold only (the agent carried on), claim_notify + one gate_answer message (at most once).

A waiting hook needs no message: it polls the card and answers in place, so a `waiting` hold's CAS stamps
notified_at itself. release_covered() is the gateway tick's half: an `open` hold whose hits a grant made elsewhere
now covers is released, its card retired and the agent told to retry.

A grant an agent proposes from chat (`orchestra grant propose`) gets a confirm card (bridge kind 'gate_grant'):
Confirm activates the pending grant, Don't (or a dismissal) declines it.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_policy as GP  # noqa: E402

BRIDGE_KIND = "gate"
GRANT_BRIDGE_KIND = "gate_grant"
ORIGIN = "gate"
WATCH_OK = ["1", "2", "5"]
DECISIONS = {"1": "allow_once", "2": "allow_session", "3": "allow_agent", "4": "allow_fleet", "5": "deny"}
RETRY_WINDOW = timedelta(minutes=60)
MSG_FROM = "gate"
MSG_TYPE = "gate_answer"


def _now(now=None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def op_key(hold_id: str) -> str:
    return f"gate:{hold_id}"


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
    return b if isinstance(b, dict) and b.get("kind") in (BRIDGE_KIND, GRANT_BRIDGE_KIND) else None


def is_gate(row) -> bool:
    """A gate card: menu.bridge.kind in ('gate', 'gate_grant')."""
    try:
        return bridge_of(row) is not None
    except Exception:  # noqa: BLE001
        return False


def deny_text_optional(row) -> bool:
    """The gate card's "Don't allow" write-in takes optional words (the answer path must not require them)."""
    b = bridge_of(row)
    return b is not None and b.get("kind") == BRIDGE_KIND


# ---- the card ---------------------------------------------------------------------------------------------------

def _options(agent: str, hits) -> list:
    verbs = []
    for h in hits:
        v = GP.verb(h.action, h.category)
        if v not in verbs:
            verbs.append(v)
    what = " and ".join(verbs) or "do this"
    return [
        {"n": "1", "label": "Allow once", "input_kind": "direct"},
        {"n": "2", "label": "Allow for this session", "input_kind": "direct",
         "detail": f"Until {agent} starts a new session"},
        {"n": "3", "label": "Always for this agent", "input_kind": "direct",
         "detail": f"{agent} may {what} without asking"},
        {"n": "4", "label": "Always for all agents", "input_kind": "direct",
         "detail": f"Every agent may {what} without asking"},
        {"n": "5", "label": "Don't allow", "input_kind": "free_text", "detail": "Optional: say why"},
    ]


def card_kwargs(hold_id: str, *, agent: str, hits, resolved, role_names=None, project=None) -> dict:
    """The ApprovalStore.create kwargs for one hold's card. Pure. `options` is the label list (the decode-safe
    top-level shape every client reads); the structured options ride menu.options."""
    hits = list(hits)
    text = GP.card_text(agent, hits, resolved, role_names=role_names, project=project)
    opts = _options(agent, hits)
    bridge = {"kind": BRIDGE_KIND, "permission": True, "hold_id": hold_id, "hits": [h.to_dict() for h in hits],
              "watch_ok": list(WATCH_OK)}
    menu = {"question": text["question"], "options": opts, "bridge": bridge}
    risky = any(h.category in ("destructive", "outside_world") for h in hits)
    return {"from_agent": agent, "question": text["question"], "worker_kind": "pane", "kind": "menu",
            "menu": menu, "options": [o["label"] for o in opts], "op_key": op_key(hold_id),
            "summary": text["summary"], "origin": ORIGIN, "risk_level": "high" if risky else "medium"}


def _maybe_notify(rid, store, notify):
    if notify is None and os.environ.get("GATE_CARDS_NOTIFY", "").strip() != "0":
        try:
            from approval_notify import notify  # noqa: F811
        except Exception:  # noqa: BLE001 — no push: the card still shows on every surface
            notify = None
    if notify:
        try:
            notify(rid, store=store)
        except Exception as e:  # noqa: BLE001 — the notify backstop re-sends
            print(f"[gate_cards] notify deferred for {rid}: {e}", file=sys.stderr)


def file_card(store, hold_id: str, *, agent: str, hits, resolved, role_names=None, project=None,
              notify=None) -> str:
    """File the card for a hold; its id. Deduped by op_key (gate:<hold id>): a second call returns the existing
    card (any status), so a hook retry never files twice."""
    kw = card_kwargs(hold_id, agent=agent, hits=hits, resolved=resolved, role_names=role_names, project=project)
    for row in store.find_by_op_key(kw["op_key"]) or []:
        return row["id"]
    rid = store.create(**kw)
    _maybe_notify(rid, store, notify)
    return rid


def retire_card(store, rid: str, reason: str, *, by: str = "gate") -> bool:
    """A pending gate card nobody needs to answer any more (its hold was released or withdrawn): pending ->
    'retired' with who/why when the retire columns exist, else 'resolved_elsewhere'. ApprovalStore.retire refuses
    every kind='menu' row (the menu bridge owns those), so the gate retires its own cards here, scoped to
    origin='gate'."""
    if not rid:
        return False
    c = store._conn()
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(approval_requests)").fetchall()}
        if {"retired_at", "retired_by", "retire_reason"} <= cols:
            res = c.execute("UPDATE approval_requests SET status='retired', retired_at=?, retired_by=?, "
                            "retire_reason=? WHERE id=? AND status='pending' AND origin=?",
                            [_iso(_now()), by, reason, rid, ORIGIN])
        else:
            res = c.execute("UPDATE approval_requests SET status='resolved_elsewhere' "
                            "WHERE id=? AND status='pending' AND origin=?", [rid, ORIGIN])
        c.commit()
        return res.rowcount > 0
    finally:
        c.close()


# ---- answers ----------------------------------------------------------------------------------------------------

def decision_of(row) -> str | None:
    """The card's decision: allow_once | allow_session | allow_agent | allow_fleet | deny for a gate card,
    confirm | decline for a gate_grant card. A discarded, retired or expired card -> deny / decline. None when
    the row holds no usable answer."""
    b = bridge_of(row)
    if b is None:
        return None
    ended = row.get("status") in ("discarded", "retired", "expired")
    n = str(row.get("option_n") or "")
    if b.get("kind") == GRANT_BRIDGE_KIND:
        if ended:
            return "decline"
        return {"1": "confirm", "2": "decline"}.get(n)
    if ended:
        return "deny"
    return DECISIONS.get(n)


def _hits(hold) -> list:
    return GP.hits_of((hold or {}).get("hits"))


def grants_for(hold: dict, decision: str, card_id: str) -> list:
    """create_grant kwargs (without person/by) for an answer: one grant per held hit, narrowed to the hit's
    action, plus its folder for outside_folder/edit_outside and its recipient for outside_world. Allow once and
    deny make none; a session grant needs the hold's session."""
    if decision == "allow_session":
        if not hold.get("session_id"):
            return []
        scope, ref = "session", hold["session_id"]
    elif decision == "allow_agent":
        scope, ref = "agent", GP.base_agent(hold.get("agent") or "")
    elif decision == "allow_fleet":
        scope, ref = "fleet", None
    else:
        return []
    out, seen = [], set()
    for h in _hits(hold):
        narrow = {"action": h.action}
        if h.category == "outside_folder" and h.action == "edit_outside" and h.folder:
            narrow["folder"] = h.folder
        if h.category == "outside_world" and h.recipient:
            narrow["recipient"] = h.recipient
        key = (h.category, tuple(sorted(narrow.items())))
        if key in seen:
            continue
        seen.add(key)
        g = {"scope": scope, "scope_ref": ref, "category": h.category, "narrow": narrow,
             "source": "card", "source_card": card_id}
        if scope == "session" and hold.get("agent"):
            g["agent"] = GP.base_agent(hold["agent"])         # who the session is (scoped members see and revoke it)
        out.append(g)
    return out


def _person_of(row) -> str:
    """Who answered (grants belong to that person): the card's answered_by when it names a person, else the
    gate.json person, else 'operator'. "operator" on a card is the install's generic answerer (every web and
    gateway answer records it), so it resolves to the configured person the hook looks grants up under."""
    by = str((row or {}).get("answered_by") or "").strip()
    if by and ":" not in by and by != "operator":  # "rule:<id>" etc. are not people
        return by
    try:
        import approval_config
        p = os.path.join(str(approval_config.orchestra_dir()), "state", "attention", "gate.json")
        with open(p) as fh:
            person = json.load(fh).get("person")
        if isinstance(person, str) and person.strip():
            return person.strip()
    except Exception:  # noqa: BLE001
        pass
    return "operator"


def _label(hold, hits=None) -> str:
    hits = hits if hits is not None else _hits(hold)
    return GP.action_text(hits) or (hold or {}).get("tool") or "the held action"


def _grant_label(hold, decision) -> str:
    hits = _hits(hold)
    verbs = []
    for h in hits:
        v = GP.verb(h.action, h.category)
        if v not in verbs:
            verbs.append(v)
    what = " and ".join(verbs) or "do this"
    who = GP.base_agent(hold.get("agent") or "") if decision == "allow_agent" else "every agent"
    return f"{who} may {what} without asking"


def message_body(hold: dict, decision: str, person: str, words: str | None = None) -> str:
    """The §5.3 wording of the gate_answer message."""
    action = _label(hold)
    if decision in ("allow_once", "allow_session"):
        return f"Allowed by {person}: `{action}`. Retry it now; it will pass once."
    if decision in ("allow_agent", "allow_fleet"):
        return f"Allowed by {person} from now on: {_grant_label(hold, decision)}. Retry `{action}` now."
    if decision == "covered":
        return f"Now allowed: `{action}` is covered by a permission {person} gave. You can retry it now."
    w = (words or "").strip().rstrip(".")
    return f"Not allowed by {person}: `{action}`." + (f" {w}." if w else "") + " Drop it."


def _default_send(msg: dict):
    """The real msg_store send, refused under the same fences as approval_resume's real seams."""
    import approval_resume as AR
    why = AR._real_seam_refusal()
    if why:
        raise RuntimeError(f"gate message refused: {why}")
    AR._msg_store().send(**msg)


def _send(ss, hold, decision, person, msg_send, *, words=None, card_id=None, now=None) -> str:
    """claim_notify then send; at most once per hold. A failed send gives the claim back."""
    if not ss.claim_notify(hold["id"], now=_iso(_now(now))):
        return "already_notified"
    msg = {"from_agent": MSG_FROM, "to_agent": hold["agent"], "type": MSG_TYPE,
           "subject": f"Gate: {decision} ({card_id or hold['id']})",
           "body": message_body(hold, decision, person, words), "priority": "high",
           "metadata": {"approval_id": card_id, "hold_id": hold["id"], "decision": decision}}
    try:
        (msg_send or _default_send)(msg)
    except Exception as e:  # noqa: BLE001
        print(f"[gate_cards] message for {hold['id']} failed: {e}", file=sys.stderr)
        ss.set_hold(hold["id"], notified_at=None)
        return "message_failed"
    return "messaged"


def _ss_for(store, ss):
    if ss is not None:
        return ss
    from supervision_store import SupervisionStore
    return SupervisionStore(db_path=getattr(store, "db_path", None))


def _ack(store, row):
    try:
        store.ack(row["id"])
    except Exception as e:  # noqa: BLE001
        print(f"[gate_cards] ack failed for {row.get('id')}: {e}", file=sys.stderr)


def _deliver_grant_card(row, store, ss, b, decision, person, now):
    rid, gid = row["id"], b.get("grant_id")
    from supervision_store import Conflict, NotFound
    try:
        if decision == "confirm":
            ss.activate_grant(gid, by=person, now=_iso(_now(now)))
        else:
            ss.decline_grant(gid, by=person, now=_iso(_now(now)))
        reason = "confirmed" if decision == "confirm" else "declined"
    except (Conflict, NotFound) as e:
        reason = f"grant_{type(e).__name__.lower()}"
    _ack(store, row)
    return {"id": rid, "delivered": True, "reason": reason, "decision": decision}


def deliver(row, store, ss=None, msg_send=None, *, decision=None, person=None, now=None) -> dict:
    """The gate lane for an answered (or, with decision='deny', dismissed) gate card. Returns
    {id, delivered, reason, decision, hold_id?}. Idempotent: a replay writes no second grant (grant_key), moves no
    final hold and sends no second message (claim_notify)."""
    rid = row["id"]
    b = bridge_of(row) or {}
    decision = decision or decision_of(row)
    if decision is None:
        store.mark_resume_failed(rid, "the gate card's answer could not be read as one of its options")
        return {"id": rid, "delivered": False, "reason": "unreadable_answer", "decision": None}
    ss = _ss_for(store, ss)
    person = person or _person_of(row)
    if b.get("kind") == GRANT_BRIDGE_KIND:
        return _deliver_grant_card(row, store, ss, b, decision, person, now)
    hold = ss.hold(b["hold_id"]) if b.get("hold_id") else None
    if hold is None:
        hold = ss.hold_by_card(rid)
    if hold is None:
        _ack(store, row)
        return {"id": rid, "delivered": False, "reason": "no_hold", "decision": decision}
    ts = _iso(_now(now))
    allow = decision.startswith("allow")
    to = "allowed" if allow else "denied"
    won_from = None
    for _ in range(3):
        cur = ss.hold(hold["id"]) or {}
        st = cur.get("state")
        if st not in ("waiting", "open"):
            break
        fields = {"decision": decision}
        if st == "waiting":
            fields["notified_at"] = ts                       # the waiting hook answers in place: no message
        elif decision in ("allow_once", "allow_session"):
            fields["retry_until"] = _iso(_now(now) + RETRY_WINDOW)
        if ss.transition_hold(hold["id"], to, (st,), now=ts, **fields):
            won_from = st
            break
    hold = ss.hold(hold["id"]) or hold
    # Standing grants only for an answer that actually decided this hold: the CAS above won, or a replay of the
    # answer that won (same final state and decision; grant_key makes the second write a no-op). An answer that
    # raced a withdrawn, released or otherwise final hold mints nothing.
    if won_from is not None or (hold.get("state") == to and hold.get("decision") == decision):
        for kw in grants_for(hold, decision, rid):
            ss.create_grant(person, by=person, now=ts, **kw)
    reason = f"{won_from}->{to}" if won_from else "stale_hold"
    if won_from is None:
        print(f"[gate_cards] stale_hold {hold['id']} (state {hold.get('state')}) for card {rid}", file=sys.stderr)
    # The agent carried on (open hold): tell it, once. A replay after a crash between the CAS and the message
    # still sends (notified_at is NULL only until the one send claims it).
    if hold.get("state") == to and hold.get("decision") == decision:
        sent = _send(ss, hold, decision, person, msg_send, words=row.get("answer_text"), card_id=rid, now=now)
        if sent == "message_failed" and row.get("status", "answered") == "answered":
            store.stamp_retry(rid)            # stays answered: the watchdog's next beat sends it (grants/CAS no-op)
            return {"id": rid, "delivered": False, "reason": reason + ",message_failed", "decision": decision,
                    "hold_id": hold["id"]}
        if sent == "messaged":
            reason += ",messaged"
    _ack(store, row)
    return {"id": rid, "delivered": True, "reason": reason, "decision": decision, "hold_id": hold["id"]}


def release_covered(ss, ledger, now=None, msg_send=None, *, person=None, registry=None, data_dir=None) -> list:
    """The gateway tick: every `open` hold whose held hits are all covered by an allow winner now (a grant made in
    Settings, the drawer or another card) -> released (decision 'covered'), its card retired, and the agent told
    to retry. Returns the released hold ids."""
    person = person or _person_of(None)
    grants = ss.grants(person, states=("active",))
    out = []
    for hold in ss.open_holds(("open",)):
        try:
            if not GP.hold_covered(ss, hold, person=person, registry=registry, data_dir=data_dir, grants=grants):
                continue
            if not ss.transition_hold(hold["id"], "released", ("open",), now=_iso(_now(now)), decision="covered"):
                continue
            out.append(hold["id"])
            if hold.get("card_id"):
                retire_card(ledger, hold["card_id"], "a standing permission now covers it")
            _send(ss, dict(hold, state="released"), "covered", person, msg_send, card_id=hold.get("card_id"),
                  now=now)
        except Exception as e:  # noqa: BLE001 — one bad hold never stops the tick
            print(f"[gate_cards] release_covered {hold.get('id')}: {e}", file=sys.stderr)
    return out


# ---- grants proposed from chat ----------------------------------------------------------------------------------

def propose_grant(ledger, ss, *, agent: str, category: str, narrow=None, scope: str = "agent", scope_ref=None,
                  said: str | None = None, person: str | None = None, notify=None) -> tuple:
    """A pending grant (source 'chat', proposed_by the agent) and its confirm card. Returns (grant id, card id).
    The grant does nothing until the card is answered Confirm."""
    person = person or _person_of(None)
    if scope == "agent":
        scope_ref = GP.base_agent(scope_ref or agent)
    elif scope == "fleet":
        scope_ref = None
    g = ss.create_grant(person, scope=scope, scope_ref=scope_ref, category=category, narrow=narrow,
                        source="chat", by=agent, state="pending", said=said, proposed_by=agent)
    action = (narrow or {}).get("action")
    who = "every agent" if scope == "fleet" else agent
    question = f"Confirm: {who} may {GP.verb(action, category)} without asking?"
    opts = [{"n": "1", "label": "Confirm", "input_kind": "direct"},
            {"n": "2", "label": "Don't", "input_kind": "direct"}]
    menu = {"question": question, "options": opts,
            "bridge": {"kind": GRANT_BRIDGE_KIND, "grant_id": g["id"], "scope": scope, "permission": False}}
    summary = f"You said in chat: '{said}'" if said else f"{agent} asked for a standing permission."
    rid = ledger.create(from_agent=agent, question=question, worker_kind="pane", kind="menu", menu=menu,
                        options=[o["label"] for o in opts], op_key=f"gate_grant:{g['id']}", summary=summary,
                        origin=ORIGIN, risk_level="high" if scope == "fleet" else "medium")
    _maybe_notify(rid, ledger, notify)
    return g["id"], rid
