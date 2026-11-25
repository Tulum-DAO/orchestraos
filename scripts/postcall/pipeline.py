"""process(call): floor -> extract -> ground -> card (design §1-§4)."""
from dataclasses import dataclass, field

import hashlib

from . import card, deadlines, extract, sources
from .effects import Effect


@dataclass
class Result:
    status: str
    items: list = field(default_factory=list)
    reason: str | None = None


_DONE = ("carded", "no_card", "skipped_floor")


def process(call, *, ledger, extractor, effects):
    prior = ledger.call_row(call.call_id)
    if prior and prior["status"] in _DONE and prior["content_hash"] == call.content_hash:
        ledger.run_pending(effects)
        return Result("already_processed")
    reason = sources.below_floor(call)
    if reason:
        ledger.set_call_status(call.call_id, "skipped_floor", content_hash=call.content_hash, error=reason)
        return Result("skipped_floor", reason=reason)

    extraction = extractor(call)
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

    card_items = _card_items(call, items, sources.shaw_location())
    pages = card.build(title=call.title, items=card_items, **_card_context(call, card_items))
    for n, page in enumerate(pages, 1):
        payload = {"title": page.title, "summary": page.summary, "questions": page.questions, "frozen": page.frozen}
        ledger.plan(Effect(effect_id=f"card:{call.call_id}:r0:p{n}", kind="card", payload=payload),
                    call_id=call.call_id)
    ledger.set_call_status(call.call_id, "carded", content_hash=call.content_hash)
    ledger.run_pending(effects)
    return Result("carded", items=items)


def _source_key(call, quote):
    norm = extract._normalized_with_map(quote)[0]
    return "src_" + hashlib.sha256(f"{call.call_id}|{norm}".encode()).hexdigest()[:12]


def _card_context(call, card_items):
    tz, label, _ = deadlines.zone_at_call(call.created_at, sources.shaw_location())
    # Until §2a (calendar overlap + speaker mapping) lands, everyone is named from the transcript only.
    named = sorted({it.counterpart for it in card_items if it.owner == "participant" and it.counterpart != "unidentified"})
    return {"tz": tz, "tz_label": label, "call_start": call.created_at, "call_end": sources.call_end(call),
            "participants": [f"{n} (unverified)" for n in named] or ["unidentified"]}


def _card_items(call, items, location):
    tz, label, assumed = deadlines.zone_at_call(call.created_at, location)
    out = []
    for it in items:
        f = it.fields
        due = deadlines.resolve(f.get("timeframe"), call.created_at, tz, zone_assumed=label if assumed else None)
        out.append(card.CardItem(
            key=_source_key(call, it.quote), owner=it.owner, text=f.get("commitment_text") or it.quote,
            counterpart=f.get("counterpart") or "unidentified", due=due, quote=it.quote, citation=it.citation,
            trust=it.trust,
            chase_date=deadlines.chase_date(due.due_ts, call.created_at, tz) if it.owner == "participant" else None))
    return out
