"""Cross-call linking (design §5): before a card is built, each grounded item is compared with the
commitments earlier calls already created for the same counterpart and deliverable.

  auto-link  every rule holds (same owner, counterpart, action_type, operation and deliverable,
             neither recurring, BOTH speaker-verified): the item is not asked again. An open
             commitment gets a due change if the date moved; one done or dropped < 14 days ago is
             only counted.
  uncertain  same counterpart + deliverable but any rule fails: the item stays on the card with
             a `Same as ... update it` option and ⚠. Nothing merges before Shaw picks it.
  new        no candidate (including one closed 14 days or more ago).
"""
from dataclasses import replace

from . import extract

_AUTO_FIELDS = ("owner", "counterpart", "action_type", "operation", "deliverable")


def norm(text):
    return extract._normalized_with_map(text or "")[0]


def identity(item, card_item, call):
    """What the ledger stores on the commitment row, so later calls can link to it."""
    f = item.fields
    return {"owner": item.owner, "counterpart": norm(card_item.counterpart),
            "action_type": (f.get("action_type") or "").strip().lower(),
            "operation": (f.get("operation") or "").strip().lower(),
            "deliverable": norm(f.get("deliverable")), "recurring": bool(f.get("recurring")),
            "trust": item.trust, "text": card_item.text,
            "due_ts": card_item.due.due_ts, "due_source": card_item.due.due_source,
            # the task's own date is derived: a chase item is chased, not due (design §5)
            "task_due_ts": card_item.chase_date if item.owner == "participant" else card_item.due.due_ts,
            "first_call": call.call_id, "first_call_date": call.created_at[:10]}


def auto_linkable(old, new):
    return (all(old.get(k) and old.get(k) == new.get(k) for k in _AUTO_FIELDS)
            and not old.get("recurring") and not new.get("recurring")
            and old.get("trust") == "verified" and new.get("trust") == "verified")


def link(ledger, call, items, card_items):
    """(card items still to ask, {key: identity}, counts). Auto-links are recorded in the ledger here."""
    keep, meta = [], {}
    counts = {"linked": 0, "due_moved": 0, "already_closed": 0, "uncertain": 0}
    for item, ci in zip(items, card_items):
        ident = identity(item, ci, call)
        meta[ci.key] = ident
        if not ident["deliverable"] or ident["counterpart"] in ("", "unidentified"):
            keep.append(ci)
            continue
        cands = ledger.link_candidates(counterpart=ident["counterpart"], deliverable=ident["deliverable"],
                                       exclude_call=call.call_id, at=call.created_at)
        auto = next((c for c in cands if auto_linkable(c, ident)), None)
        if auto is not None:
            ledger.link(ci.key, auto["cmt"], call_id=call.call_id, meta=ident)
            if auto["closed_at"]:
                counts["already_closed"] += 1
                continue
            counts["linked"] += 1
            if ident["due_ts"] and ident["due_ts"] != auto["due_ts"]:
                ledger.record_due_change(auto["cmt"], ident["due_ts"], ident["due_source"], call.call_id,
                                         ident["task_due_ts"])
                counts["due_moved"] += 1
            continue
        if cands:
            c = cands[-1]
            keep.append(replace(ci, uncertain_match={"cmt": c["cmt"], "text": c["text"], "date": c["first_call_date"]}))
            counts["uncertain"] += 1
            continue
        keep.append(ci)
    return keep, meta, counts
