"""process(call): floor -> extract -> ground -> card (design §1-§4)."""
from dataclasses import dataclass, field, replace

import datetime as dt
import hashlib
import json
import os
import re

from . import calendar_ics, card, deadlines, dedupe, extract, participants, seats, sources
from .effects import Effect
from .ledger import _parse as ledger_parse

# A timeframe that means "before our next meeting" (design §3 rule 3).
_NEXT_CALL = re.compile(r"\bnext\s+(?:call|meeting|sync)\b|\bbefore\s+we\s+talk\b|\bnext\s+time\s+we\s+talk\b",
                        re.I)


@dataclass
class Result:
    status: str
    items: list = field(default_factory=list)
    reason: str | None = None
    dupes: dict = field(default_factory=dict)


_DONE = ("carded", "no_card", "no_card_dupes", "skipped_floor")


def process(call, *, ledger, extractor, effects):
    prior = ledger.call_row(call.call_id)
    if prior and prior["status"] in _DONE and prior["content_hash"] == call.content_hash:
        ledger.run_pending(effects)
        return Result("already_processed")
    if prior and prior["status"] == "failed_final" and prior["content_hash"] == call.content_hash:
        return Result("failed_final", reason=prior["last_error"])   # manual review; a new transcript retries
    reason = sources.below_floor(call)
    if reason:
        ledger.set_call_status(call.call_id, "skipped_floor", content_hash=call.content_hash, error=reason)
        return Result("skipped_floor", reason=reason)

    # A re-extraction is told what the call already produced, so it can say what it restates (design §5).
    existing = ledger.occurrences(call.call_id) if prior else []
    extraction = extractor(call, existing=_for_prompt(existing)) if existing else extractor(call)
    if not extraction.ok:
        ledger.set_call_status(call.call_id, "failed_retry", content_hash=call.content_hash,
                               error=extraction.error or "extractor failed")
        return Result("failed_retry", reason=extraction.error)

    try:
        items = extract.ground(call, extraction)
    except extract.AttributionError as e:
        ledger.set_call_status(call.call_id, "failed_retry", content_hash=call.content_hash, error=str(e))
        return Result("failed_retry", reason=str(e))
    if not items:
        ledger.set_call_status(call.call_id, "no_card", content_hash=call.content_hash)
        return Result("no_card")

    # Participants before dedupe and the card, so verified items can auto-link (design §2a, §5).
    events = calendar_ics.load(_calendar_path())
    roster = participants.resolve(call, extraction.raw, events, shaw_emails=_shaw_emails(), contacts=())
    items = participants.apply_trust(items, roster)

    all_items = _card_items(call, items, sources.shaw_location(), roster=roster, events=events)
    # Only items never asked before (design §1 "Revisions"): a re-ingest never re-asks or re-links one,
    # and an item that may restate an earlier occurrence of this call is asked with ⚠ (design §5).
    fresh = _reconcile(ledger, call, list(zip(items, all_items)), existing)
    if not fresh:
        status = "carded" if ledger.has_card(call.call_id) else (
            prior["status"] if prior and prior["status"] in _DONE else "no_card")
        ledger.set_call_status(call.call_id, status, content_hash=call.content_hash)
        ledger.run_pending(effects)
        return Result("no_new_items", items=items)
    items, new_items = [it for it, _ in fresh], [ci for _, ci in fresh]
    revision = ledger.card_revision(call.call_id, call.content_hash)
    card_items, meta, dupes = dedupe.link(ledger, call, items, new_items)
    if not card_items:
        ledger.set_call_status(call.call_id, "no_card_dupes", content_hash=call.content_hash)
        ledger.run_pending(effects)
        return Result("no_card_dupes", items=items, dupes=dupes)
    pages = card.build(title=call.title, items=card_items, now=_now(), **_card_context(call, all_items, roster))
    extra = "".join([f"\nAlready tracked, due date moved: {dupes['due_moved']}" if dupes["due_moved"] else "",
                     f"\nAlready done/dropped: {dupes['already_closed']}" if dupes["already_closed"] else ""])
    planned = []
    for n, page in enumerate(pages, 1):
        payload = {"title": page.title, "summary": page.summary + extra, "questions": page.questions,
                   "frozen": page.frozen, "decide_by": page.decide_by}
        planned.append((Effect(effect_id=f"card:{call.call_id}:r{revision}:p{n}", kind="card", payload=payload),
                        page.frozen))
    ledger.plan_cards(planned, call_id=call.call_id, meta=meta, content_hash=call.content_hash)
    ledger.set_call_status(call.call_id, "carded", content_hash=call.content_hash)
    ledger.run_pending(effects)
    return Result("carded", items=items, dupes=dupes)


def results_due(ledger, now):
    """Plan the one-per-card gm results notice where it is due (design §6). Returns the planned ids."""
    return ledger.results_due(now)


RETIRE_AFTER = dt.timedelta(days=14)


def retire_stale(ledger, adapter, now):
    """Retire every filed card still pending 14 days after its Decide-by, reason "no answer" (gm
    ruling §9.6). Its undecided items become `unreviewed`; nothing is created from them. Returns the
    newly planned retire ids."""
    now = ledger_parse(now)
    planned = []
    have = {e["effect_id"] for e in ledger.effects() if e["kind"] in ("retire", "qack")}
    for e in ledger.effects():
        if e["kind"] != "card" or e["state"] != "confirmed" or not e["external_id"]:
            continue
        eid = f"retire:{e['effect_id']}"
        decide_by = json.loads(e["payload_json"]).get("decide_by")
        if eid in have or f"qack:{e['effect_id']}" in have or not decide_by:
            continue
        if now < ledger_parse(decide_by) + RETIRE_AFTER:
            continue
        status, _ = adapter.card_answers(e["external_id"])
        if status != "pending":
            continue
        ledger.plan_retire(e["effect_id"], Effect(eid, "retire", {"questionnaire": e["external_id"],
                                                                   "reason": "no answer", "card": e["effect_id"]}),
                           call_id=e["call_id"])
        planned.append(eid)
    ledger.run_pending(adapter)
    return planned


def collect_replies(ledger, msg_db):
    """Record one result per dispatched commitment from the first reply addressed to postcall-dev whose
    subject carries `[postcall dispatch:<cmt>]` (design §6). Read-only on the message DB. Returns how
    many results were recorded."""
    import sqlite3
    pending = ledger.dispatched_without_result()
    if not pending or not msg_db or not os.path.exists(msg_db):
        return 0
    recorded = 0
    with sqlite3.connect(f"file:{msg_db}?mode=ro", uri=True) as c:
        for cmt in pending:
            try:
                row = c.execute("SELECT body FROM messages WHERE to_agent='postcall-dev' AND from_agent != "
                                "'postcall-dev' AND instr(subject, ?) > 0 ORDER BY created_at, rowid LIMIT 1",
                                (f"[postcall dispatch:{cmt}]",)).fetchone()
            except sqlite3.OperationalError:
                return recorded
            if row:
                ledger.record_result(cmt, row[0] or "")
                recorded += 1
    return recorded


def _for_prompt(existing):
    return [{"occ": o["key"], "owner": o["owner"], "action": o["text"], "quote": o["quote"]} for o in existing]


def _overlaps(a, b):
    return bool(a and b) and a[0] < b[1] and b[0] < a[1]


def _same_fields(item, old):
    f = item.fields
    return (item.owner == old["owner"]
            and (f.get("action_type") or "").strip().lower() == (old["action_type"] or "")
            and (f.get("operation") or "").strip().lower() == (old["operation"] or "")
            and dedupe.norm(f.get("deliverable")) == (old["deliverable"] or ""))


def _claim(item):
    occ = item.fields.get("same_as_occ")
    return occ if isinstance(occ, str) else None


def _likeliest(item, overlapping, restated):
    """The overlapping occurrence this item most likely restates: not one this extraction already
    restated exactly, then the most equal fields (deliverable first)."""
    f = item.fields
    mine = (dedupe.norm(f.get("deliverable")), (f.get("operation") or "").strip().lower(),
            (f.get("action_type") or "").strip().lower())

    def score(x):
        return (x["key"] not in restated, x["deliverable"] == mine[0], x["operation"] == mine[1],
                x["action_type"] == mine[2])
    return max(overlapping, key=score, default=None)


def _reconcile(ledger, call, pairs, existing):
    """The (item, card item) pairs still to ask, after re-extraction reconciliation (design §5 steps 1-3).

    1. Each earlier occurrence's quote text is relocated in the new transcript (no raw offsets).
    2. `same_as_occ` is accepted only with equal owner / action_type / operation / deliverable, an
       overlapping span (or equal quote) and a one-to-one claim; it is then recorded, never asked.
    3. A failed claim, or the same owner on an overlapping span, is asked with ⚠ and `Same as`.
    Nothing is merged on a guess."""
    old = {o["key"]: {**o, "span": extract.locate_quote(o["quote"], call.transcript) if o["quote"] else None}
           for o in existing}
    claimed = [_claim(it) for it, _ in pairs]
    claims = {}
    for n, (_, ci) in enumerate(pairs):
        for occ in {claimed[n], ci.key} & old.keys():
            claims.setdefault(occ, set()).add(n)
    restated = {ci.key for _, ci in pairs if ci.key in old}
    out = []
    for n, (it, ci) in enumerate(pairs):
        if ledger.known(ci.key):
            continue
        o = old.get(claimed[n])
        if (o and claims[o["key"]] == {n} and _same_fields(it, o)
                and (_overlaps(it.span, o["span"]) or dedupe.norm(it.quote) == dedupe.norm(o["quote"]))):
            ident = dedupe.identity(it, ci, call)
            ledger.link(ci.key, o["cmt"], call_id=call.call_id, meta=ident)
            if ident["due_ts"] and ident["due_ts"] != ledger.due_of(o["cmt"]):
                ledger.record_due_change(o["cmt"], ident["due_ts"], ident["due_source"], call.call_id,
                                         ident["task_due_ts"])
            continue
        target = o or _likeliest(it, [x for x in old.values() if x["owner"] == it.owner
                                      and _overlaps(it.span, x["span"])], restated)
        if target:
            ci = replace(ci, uncertain_match={"cmt": target["cmt"], "text": target["text"],
                                              "date": target["first_call_date"]})
        out.append((it, ci))
    return out


def _source_key(call, quote, fields=None):
    """Occurrence identity (design §5): the call, who committed, the normalized quote and what was
    committed to, so two commitments inside one quote (different deliverable / action_type /
    operation) stay two."""
    f = fields or {}
    parts = [call.call_id, (f.get("owner") or "").strip().lower(), extract._normalized_with_map(quote)[0],
             extract._normalized_with_map(f.get("deliverable") or "")[0],
             (f.get("action_type") or "").strip().lower(), (f.get("operation") or "").strip().lower()]
    return "src_" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _calendar_path():
    return os.environ.get("POSTCALL_CALENDAR_FILE") or os.path.expanduser("~/.orchestra/state/calendar/shaw.ics")


def _shaw_emails():
    return {e.strip().lower() for e in (os.environ.get("POSTCALL_SHAW_EMAILS") or "").split(",") if e.strip()}


def _card_context(call, card_items, roster):
    tz, label, _ = deadlines.zone_at_call(call.created_at, sources.shaw_location())
    with_ = participants.with_line(roster)
    if with_ == ["unidentified"]:
        # Nothing known from the calendar: name participants from the transcript only, unverified.
        named = sorted({it.counterpart for it in card_items
                        if it.owner == "participant" and it.counterpart != "unidentified"})
        with_ = [f"{n} (unverified)" for n in named] or ["unidentified"]
    return {"tz": tz, "tz_label": label, "call_start": call.created_at, "call_end": sources.call_end(call),
            "participants": with_}


def _card_items(call, items, location, *, roster, events):
    tz, label, assumed = deadlines.zone_at_call(call.created_at, location)
    out = []
    for it in items:
        f = it.fields
        due = deadlines.resolve(f.get("timeframe"), call.created_at, tz, zone_assumed=label if assumed else None)
        # "unknown", not "ambiguous": an ambiguous phrase is terminal and never re-guessed (design §3).
        if due.due_ts is None and due.due_source.startswith("unknown") and _NEXT_CALL.search(f.get("timeframe") or ""):
            due = participants.next_call_due(f.get("counterpart") or "", roster, events, (), sources.call_end(call), tz) or due
        counterpart = f.get("counterpart") or "unidentified"
        # Assign is offered only for agent work with a live, resolved seat (design §6 seat resolution).
        work = (f.get("agent_work") or "").strip() if f.get("agent_takeable") is True else ""
        seat = seats.resolve(counterpart) if work else None
        # No client-contact seat: still offer the draft, to gm to route; sending stays Shaw's (gm msg_feff515d).
        route = bool(work) and seat is None
        seat = seat or ("gm" if work else None)
        out.append(card.CardItem(
            key=_source_key(call, it.quote, f), owner=it.owner, text=f.get("commitment_text") or it.quote,
            counterpart=counterpart, due=due, quote=it.quote, citation=it.citation,
            trust=it.trust, seat=seat, agent_work=work if seat else None, route=route,
            chase_date=deadlines.chase_date(due.due_ts, call.created_at, tz) if it.owner == "participant" else None))
    return out


def apply_submitted(ledger, adapter):
    """Apply Shaw's answers for every filed card that is submitted and not yet acked, then qack it
    (design §4). The ledger's first-applied-wins decision makes a repeat harmless."""
    done = {e["effect_id"][len("qack:"):] for e in ledger.effects() if e["kind"] == "qack"}
    for e in ledger.effects():
        if e["kind"] != "card" or e["state"] != "confirmed" or e["effect_id"] in done:
            continue
        status, answers = adapter.card_answers(e["external_id"])
        if status not in ("submitted", "resumed"):
            continue
        ledger.apply_answers(e["effect_id"], status=status, answers=answers)
        ledger.plan(Effect(f"qack:{e['effect_id']}", "qack", {"questionnaire": e["external_id"]}),
                    call_id=e["call_id"])
    ledger.run_pending(adapter)
