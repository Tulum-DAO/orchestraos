"""(Ported from the reviewed POC for the native OFFLINE fixture mode: $0, no provider socket, no real tools.)
Synthetic gpt-live-1 SERVER frames for offline tests, shaped exactly like the documented examples.

Each builder returns (frame, doc) where doc cites the page and section the shape comes from. Scenario functions
return lists of (frame, doc). dump() writes them as JSONL {"doc": ..., "frame": ...} to poc/fixtures/.
Nothing here is a recording of the real service: these are documented shapes with synthetic ids and content.
"""
import base64
import itertools
import json

import numpy as np

from . import protocol as P

_n = itertools.count(1)


def _id(prefix):
    return f"{prefix}_{next(_n):05d}"


def pcm(rms, seconds=0.1, seed=1):
    n = int(16000 * seconds)
    if not rms:
        return b"\x00" * (n * 2)
    x = np.random.default_rng(seed).standard_normal(n)
    x = np.clip(np.rint(x / np.sqrt((x * x).mean()) * rms), -32768, 32767).astype("<i2")
    return x.tobytes()


# ---- top-level server events (DOC_PRIMARY_WS "Server events") ------------------------------------------
def started(session_id="live_poc_1"):
    return ({"type": "session.started", "event_id": _id("event_started"),
             "session": {"id": session_id, "expires_at": 1788307200, "status": "active", "model": P.MODEL}},
            P.DOC_PRIMARY_WS + "#session.started")


def output_audio(rms, seconds=0.1):
    return ({"type": "session.output_audio.delta", "event_id": _id("evt_audio"),
             "delta": base64.b64encode(pcm(rms, seconds)).decode()}, P.DOC_PRIMARY_WS + "#session.output_audio.delta")


def output_transcript(delta, start_ms=0, end_ms=200):
    return ({"type": "session.output_transcript.delta", "event_id": _id("evt_out_tx"), "delta": delta,
             "start_ms": start_ms, "end_ms": end_ms}, P.DOC_PRIMARY_WS + "#session.output_transcript.delta")


def input_transcript(delta, start_ms=0, end_ms=200):
    return ({"type": "session.input_transcript.delta", "event_id": _id("evt_in_tx"), "delta": delta,
             "start_ms": start_ms, "end_ms": end_ms}, P.DOC_PRIMARY_WS + "#session.input_transcript.delta")


def delegation_created(did, response_id, offset_ms=3600):
    return ({"type": "session.delegation.created", "event_id": _id("evt_delegation"), "offset_ms": offset_ms,
             "delegation": {"id": did, "type": "delegation", "target": "responses", "response_id": response_id}},
            P.DOC_PRIMARY_WS + "#session.delegation.created")


def response_event(did, nested, doc):
    return ({"type": "response.event", "event_id": _id("evt_response"), "delegation_id": did, "event": nested},
            P.DOC_PRIMARY_WS + "#response.event + " + doc)


def nested_created(response_id):
    """Lifecycle snapshot: 'output: [] ... empty tools array, instructions: null, and no input field' (DOC_DELEGATION)."""
    return {"type": "response.created", "sequence_number": 1, "response": {
        "id": response_id, "object": "response", "status": "in_progress", "output": [], "tools": [],
        "instructions": None}}


def nested_function_call_done(call_id, name, arguments, item_id=None, output_index=0, seq=2):
    """DOC_RESPONSES_STREAM response.output_item.done with a FunctionCall item {arguments, call_id, name, type,
    id, status}."""
    return {"type": "response.output_item.done", "output_index": output_index, "sequence_number": seq,
            "item": {"id": item_id or _id("fc"), "type": "function_call", "status": "completed",
                     "call_id": call_id, "name": name, "arguments": arguments}}


def nested_completed(response_id, seq=3, usage=None):
    r = {"id": response_id, "object": "response", "status": "completed", "output": [], "tools": [],
         "instructions": None}
    if usage is not None:
        r["usage"] = usage
    return {"type": "response.completed", "sequence_number": seq, "response": r}


def appended(kind, client_event_id):
    return ({"type": f"session.{kind}.appended", "event_id": _id(f"evt_{kind}_ack"),
             "client_event_id": client_event_id, "start_ms": 1200, "end_ms": 1400},
            P.DOC_PRIMARY_WS + f"#session.{kind}.appended")


def usage(seconds):
    return ({"type": "session.usage.updated", "event_id": _id("evt_usage"), "usage": {"seconds": seconds}},
            P.DOC_PRIMARY_WS + "#session.usage.updated")


def closed(seconds, reason="close_requested"):
    return ({"type": "session.closed", "event_id": _id("evt_closed"), "reason": reason,
             "session": {"id": "live_poc_1", "model": P.MODEL, "status": "active"}, "usage": {"seconds": seconds}},
            P.DOC_PRIMARY_WS + "#session.closed")


def error(code, message, client_event_id=None):
    e = {"type": "invalid_request_error", "code": code, "message": message}
    if client_event_id:
        e["client_event_id"] = client_event_id
    return ({"type": "error", "event_id": _id("evt_error"), "error": e}, P.DOC_PRIMARY_WS + "#error")


# ---- scenarios -----------------------------------------------------------------------------------------
def speech(seconds, rms=640):
    return [output_audio(rms) for _ in range(int(seconds * 10))]


def silence(seconds):
    return [output_audio(0) for _ in range(int(seconds * 10))]


def tool_turn(did, response_id, calls, spoken_after="", completed_usage=None):
    """A user turn that delegates to Responses and requests `calls` [(call_id, name, args_json)]. The live model
    keeps talking (U3) is NOT modelled here; see speech() in the experiment scenarios."""
    frames = [delegation_created(did, response_id),
              response_event(did, nested_created(response_id), P.DOC_RESPONSES_STREAM + "#response.created")]
    for i, (cid, name, args) in enumerate(calls):
        frames.append(response_event(did, nested_function_call_done(cid, name, args, output_index=i, seq=2 + i),
                                     P.DOC_RESPONSES_STREAM + "#response.output_item.done"))
    frames.append(response_event(did, nested_completed(response_id, seq=2 + len(calls), usage=completed_usage),
                                 P.DOC_RESPONSES_STREAM + "#response.completed"))
    if spoken_after:
        frames.append(output_transcript(spoken_after))
        frames += speech(1.0) + silence(1.0)
    return frames


def scenario_smalltalk():
    return [started(), input_transcript(" hey arturo how's it going"), output_transcript("Doing well. What's up?")] \
        + speech(1.2) + silence(1.0) + [usage(6.0)]


def scenario_fleet_status():
    return [started(), input_transcript(" what are my agents doing")] + \
        tool_turn("del_fs1", "resp_fs1", [("call_fs1", "fleet_status", "{}")],
                  spoken_after="poc-builder is building the mock widget; the others are idle or stopped.")


def scenario_create_task_with_duplicate():
    """The backend re-emits the SAME call_id (U8) in a second response: one task row, one side effect."""
    args = json.dumps({"title": "check the deploys"})
    return [started(), input_transcript(" have gm create a task to check deploys")] + \
        tool_turn("del_ct1", "resp_ct1", [("call_ct1", "create_task", args)],
                  spoken_after="It's accepted and queued.") + \
        tool_turn("del_ct1", "resp_ct2", [("call_ct1", "create_task", args)])


def dump(path, frames):
    with open(path, "w") as f:
        for frame, doc in frames:
            f.write(json.dumps({"doc": doc, "frame": frame}, sort_keys=True) + "\n")
