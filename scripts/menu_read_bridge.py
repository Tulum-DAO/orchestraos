"""menu_read_bridge.py — a multi-part menu's card is made the moment a client reads it.

The menu bridge's cron (menu_bridge.py, every 60 s) sees only the first part of a multi-part menu: its passive walk
never presses a key. A client that opens the menu runs the gateway's active walk (/agent-menu-capture), which reads
every part. If the client's walk ran before the bridge's tick there was no card to write the parts back to, and the
card the tick made next held part 0 alone, which the device identity gate then refused to answer.

So when a walk completes and there is no card for the menu on screen yet, the gateway bridges that one session at once
with the walk it already holds: the same bridge_one the cron runs (its gates, its `menu_bridge` provenance, its op_key
dedup), so the card is born with every part's question. The cron's next tick finds it by op_key and adds nothing.

A walk is used only for the menu it read: its first part must be the capture's question, and the card is looked up by
the capture's op_key (session + question + context), so a read of a menu that has since changed is never written onto
the new one, nor served for it.

Imported lazily by the gateway's walk handler only.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import menu_bridge as M  # noqa: E402
import menu_bridge_core as core  # noqa: E402


def op_key_for(session, capture):
    """The card identity of the menu a capture shows, or None when there is no readable menu."""
    if not isinstance(capture, dict) or not capture.get("question"):
        return None
    return core.menu_op_key(session, capture["question"], capture.get("context") or "")


def walk_is_of(capture, walked):
    """True iff the walk read the menu the capture saw: complete, and its first part is the capture's question."""
    if not isinstance(walked, dict) or not walked.get("walk_complete") or not walked.get("parts"):
        return False
    p0 = walked["parts"][0]
    return isinstance(p0, dict) and isinstance(capture, dict) and p0.get("question") == capture.get("question")


def bridge_on_read(store, session, capture, walked, state, *, now=None, cfg=None, killswitch_path=None,
                   registry_path=None):
    """Make the card for the multi-part menu on `session` from a complete walk of it. Returns (row id or None, why):
    "bridged" when a card was made here, "completed" when one already existed (the caller's write-back fills it in).

    Only what the cron itself would bridge: pickup enabled, not killed, not skipped, a registered seat, a multi-part
    capture, and a complete walk of that same menu. Behind the same switch as the cron's multi-part cards
    (store_but_mark): off, no multi-part card is made anywhere, exactly as before. The card made here is never partial:
    the walk is complete or nothing is written."""
    if os.path.exists(killswitch_path or M.SURFACE_PICKUP_KILLSWITCH):
        return None, "killswitch"
    cfg = cfg if cfg is not None else M._load_pickup_cfg()
    if not cfg.get("enabled"):
        return None, "pickup_disabled"
    if not cfg.get("store_but_mark"):
        return None, "store_but_mark_off"
    if M._pickup_skip(cfg)(session):
        return None, "skipped"
    if not M.registry_scoped_sessions([session], registry_path):
        return None, "not_a_seat"
    if not isinstance(capture, dict) or not capture.get("multipart"):
        return None, "not_multipart"
    if not walk_is_of(capture, walked):
        return None, "menu_changed_or_incomplete"
    # A card for this menu already (the bridge's tick stored it marked needs_hydration): the caller's write-back
    # completes it, so nothing is created here.
    existing = [r for r in store.find_by_op_key(op_key_for(session, capture)) if r.get("status") == "pending"]
    if existing:
        return existing[0]["id"], "completed"
    rid = M.bridge_one(store, session, capture, 0.0, now if now is not None else time.time(), state,
                       debounce_s=0.0, walk_fn=lambda _s: walked, reread_fn=lambda _s: capture,
                       store_but_mark=False)
    return rid, ("bridged" if rid else "not_eligible")
