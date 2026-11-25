"""process(call): floor -> extract -> ground -> card (design §1-§4)."""
from dataclasses import dataclass, field

from . import card, extract, sources
from .effects import Effect


@dataclass
class Result:
    status: str
    items: list = field(default_factory=list)
    reason: str | None = None


def process(call, *, ledger, extractor, effects):
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

    payload = card.build_questionnaire(call, items)
    effects.plan(Effect(effect_id=f"card:{call.call_id}:r0:p1", kind="card", payload=payload))
    ledger.set_call_status(call.call_id, "carded", content_hash=call.content_hash)
    return Result("carded", items=items)
