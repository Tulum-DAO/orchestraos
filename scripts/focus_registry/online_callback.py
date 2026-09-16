"""Online-callback CLI wrapper (WS3 seam, ob-requested).

The successor runs this ONCE on orientation (ob's init-task instructs
`python3 scripts/focus_registry/online_callback.py --successor .. --focus .. --notify ..`)
as its R1 online+CONFIRMED-focus callback. Deterministic + testable so the
successor never hand-constructs the JSON (which invites drift). Builds the payload
via build_online_callback and msg_store-sends it.
"""
import argparse
import json
import os
import subprocess
import sys
from typing import Callable

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from scripts.focus_registry.lineage_init import build_online_callback
from scripts.focus_registry.store import DEFAULT_STORE, load_store


def _bare(entity_id: str) -> str:
    return entity_id[len("agent:"):] if entity_id.startswith("agent:") else entity_id


def _default_send(**kw) -> dict:
    """Send via msg_store.py (verified store). from_agent/to_agent normalised to bare names."""
    subprocess.run(
        ["python3", "msg_store.py", "send",
         "--from", _bare(kw["from_agent"]), "--to", _bare(kw["to_agent"]),
         "--type", "successor_online", "--subject", kw["subject"], "--body", kw["body"]],
        cwd=_ROOT, check=True,
    )
    return {"sent": True}


def emit(successor_id: str, focus_id, notify_target: str, handoff: dict,
         store: dict, readback: dict = None, canary_answers: dict = None,
         nonce: str = None, send_fn: Callable = _default_send) -> dict:
    """Build the online EVIDENCE-submission callback (read-back + canary answers as a
    CLAIM, RED-TEAM H4) and send it. `nonce` echoes a correction id when responding to
    a correction (C2/H8). Returns {sent, callback}. send_fn injected for tests."""
    cb = build_online_callback(successor_id, focus_id, notify_target, handoff, store,
                               readback=readback, canary_answers=canary_answers, nonce=nonce)
    result = send_fn(
        from_agent=cb["from"], to_agent=cb["to"],
        subject=f"{successor_id} online — CLAIM on {focus_id or 'no focus'} (evidence attached, grade before gate)",
        body=cb["body"],
    )
    return {"sent": bool(result and result.get("sent")), "callback": cb, "result": result}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Successor online+confirmed-focus callback")
    ap.add_argument("--successor", required=True)
    ap.add_argument("--focus", default=None, help="CLAIMED focus id (or omit if drift)")
    ap.add_argument("--notify", required=True, help="gm / focus-owner PM / parent")
    ap.add_argument("--handoff", default=None, help="path to the authored handoff JSON")
    ap.add_argument("--readback", default=None, help="path to the successor's generated read-back JSON")
    ap.add_argument("--canary", default=None, help="path to the successor's canary answers JSON")
    ap.add_argument("--ack-nonce", default=None, help="correction nonce this callback acks (C2/H8)")
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--dry-run", action="store_true", help="print, do not send")
    args = ap.parse_args(argv)

    def _load_json(path):
        if path and os.path.exists(path):
            with open(path) as fh:
                return json.load(fh)
        return {}

    handoff = _load_json(args.handoff)
    readback = _load_json(args.readback)
    canary = _load_json(args.canary)
    store = load_store(args.store)

    if args.dry_run:
        cb = build_online_callback(args.successor, args.focus, args.notify, handoff, store,
                                   readback=readback, canary_answers=canary, nonce=args.ack_nonce)
        print(json.dumps(cb, indent=2))
        return 0

    res = emit(args.successor, args.focus, args.notify, handoff, store,
               readback=readback, canary_answers=canary, nonce=args.ack_nonce)
    print(json.dumps({"sent": res["sent"], "to": res["callback"]["to"]}, indent=2))
    return 0 if res["sent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
