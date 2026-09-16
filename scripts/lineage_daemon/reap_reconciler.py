"""H11 — reap-delivery reconciler (WS3 v2, DEC-1786724046).

On the no-successor reap path, PB's on_graceful_shutdown() is FAIL-OPEN: a
delivery error returns ok:False and the reap proceeds. v1's flag went to a log
line with NO consumer — so a msg_store hiccup at reap time lost the finishing
agent's handoff + pending exactly-once callbacks (the replies other agents wait
on), recreating the original babysitting failure behind a green "reap succeeded"
trace. "Fail open" without a reconciler is "fail silent."

This makes the consumer EXIST: given the on_graceful_shutdown result, produce a
DURABLE artifact (a msg_store row to gm) + a retry-queue entry when ok is False.
Pure — returns the artifacts; the daemon sends/persists them.
"""


def reconcile_reap(shutdown_result, *, agent_id, handoff, now):
    """Given on_graceful_shutdown()'s return {delivered_to, ok, ...}, produce the
    durable follow-ups. Returns {artifacts: [...], retry: {...}|None}.

    ok True  -> no artifact (delivery succeeded).
    ok False -> a durable msg_store artifact to gm (type reap_delivery_failed,
                carrying the undelivered handoff + open_loops) AND a retry-queue
                entry so the delivery is re-attempted, not silently lost.
    """
    if shutdown_result.get("ok"):
        return {"artifacts": [], "retry": None}

    owner = shutdown_result.get("delivered_to")
    open_loops = (handoff or {}).get("open_loops", [])
    artifact = {
        "to": "gm",
        "type": "reap_delivery_failed",
        "from": agent_id,
        "intended_owner": owner,
        "error": shutdown_result.get("error"),
        "handoff": handoff,
        "undelivered_open_loops": open_loops,
        "at": now,
        "body": (
            f"REAP DELIVERY FAILED for {agent_id}: could not hand its "
            f"handoff+{len(open_loops)} open_loop(s) to owner {owner} "
            f"({shutdown_result.get('error')}). Handoff preserved; re-delivery "
            f"queued. The reap proceeded (fail-open) — this is the durable "
            f"reconcile artifact so nothing is silently lost."
        ),
    }
    retry = {
        "owner": owner,
        "agent_id": agent_id,
        "handoff": handoff,
        "queued_at": now,
        "attempts": 1,
    }
    return {"artifacts": [artifact], "retry": retry}
