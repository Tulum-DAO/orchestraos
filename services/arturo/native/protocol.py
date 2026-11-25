"""gpt-live-1 (/v1/live) frames for Responses delegation, built and validated ONLY from the official docs.
Ported verbatim from the reviewed POC (poc/gptlive-native, poc/gptlive_native/protocol.py; docs snapshot there).

Every builder and validator names its source. The docs were fetched 2026-10-13 and are snapshotted in
docs_snapshot/ (SOURCES.txt has the URLs and sha256 of each full page). Anything the docs do not state is
marked UNKNOWN here and is a question for the paid experiment, never an assumption the code relies on.

DOC_* constants are the citations; tests assert that every builder carries one.
"""
import itertools
import json

DOC_PRIMARY_WS = "https://developers.openai.com/api/reference/resources/live/primary-websocket"
DOC_DELEGATION = "https://developers.openai.com/api/docs/guides/live-delegation"
DOC_CONVERSATIONS = "https://developers.openai.com/api/docs/guides/live-conversations"
DOC_RESPONSES_STREAM = "https://developers.openai.com/api/reference/resources/responses/streaming-events"

URL = "wss://api.openai.com/v1/live/sessions"                       # DOC_PRIMARY_WS "Connection"
MODEL = "gpt-live-1"
APPEND_MAX_TOKENS = 500                                             # DOC_PRIMARY_WS: each *.append content
AUDIO_RATE = 16000                                                  # MEASURED 10-10 (rate 16000 accepted);
#                                                                     the doc example uses 24000

# What the docs leave open. Each is answered (or not) by an experiment in experiments.py.
UNKNOWN = {
    "U1_tools_requested": "Whether gpt-live-1 delegates to Responses for our function tools on real speech "
                          "(documented mechanism, never exercised by us).",
    "U2_result_trigger": "Which nested lifecycle event means 'waiting for function results'. The docs say to "
                         "collect calls from response.output_item.done and that lifecycle snapshots carry "
                         "output: []; they do not name the event to submit on. We submit when the nested "
                         "response reaches response.completed or response.incomplete with pending calls.",
    "U3_speech_during_tool": "What the live model says while a tool call is pending (docs: live speech and "
                             "delegated work continue independently).",
    "U4_interrupt": "No interruption/cancel event exists (docs + measured); what happens to a pending tool call "
                    "when the user barges in.",
    "U5_late_results": "Whether a result appended with delegation_id null after the turn moved on is voiced, "
                       "used only when asked (thinking), or ignored.",
    "U6_ack_vs_audio": "Appended acks mean 'accepted into context', not spoken (docs). Correlation with audio is "
                       "only via the output transcript.",
    "U7_response_tokens": "How backend Responses token usage is reported and priced inside response.event "
                          "(docs: 'reported separately in response.event events').",
    "U8_duplicate_calls": "Whether the backend ever re-emits a completed function call (duplicate call_id).",
}

_ids = itertools.count(1)


def _eid(prefix):
    return f"{prefix}_{next(_ids):06d}"


# ---------------------------------------------------------------------------------------------------------
# Client events (DOC_PRIMARY_WS "Client events")

def function_tool(name, description, parameters, strict=True):
    """DOC_PRIMARY_WS session.start > delegation > Responses > tools > FunctionTool {name, type, description,
    parameters, strict} (full page snapshot L640-662: strict 'optional boolean or null')."""
    return {"type": "function", "name": name, "description": description, "parameters": parameters,
            "strict": strict}


def session_start(instructions, tools, backend_model, backend_instructions, voice="marin",
                  tool_choice="auto", parallel_tool_calls=False, event_id=None):
    """DOC_PRIMARY_WS 'First message: session.start' + DOC_DELEGATION 'Configure Responses delegation':
    session.delegation = {type: "responses", responses: {model, instructions, tools, tool_choice,
    parallel_tool_calls}}. The backend model is required at creation (DOC_DELEGATION)."""
    if not backend_model:
        raise ValueError("Responses delegation requires a backend model at creation (DOC_DELEGATION)")
    return {
        "type": "session.start",
        "event_id": event_id or _eid("evt_start"),
        "session": {
            "model": MODEL,
            "instructions": instructions,
            "audio": {"format": {"type": "audio/pcm", "rate": AUDIO_RATE}, "output": {"voice": voice}},
            "delegation": {
                "type": "responses",
                "responses": {
                    "model": backend_model,
                    "instructions": backend_instructions,
                    "tools": list(tools),
                    "tool_choice": tool_choice,
                    "parallel_tool_calls": bool(parallel_tool_calls),
                },
            },
        },
    }


def input_audio_append(b64_audio, event_id=None):
    """DOC_PRIMARY_WS session.input_audio.append (the relay's openai_live.encode_uplink sends the same shape)."""
    f = {"type": "session.input_audio.append", "audio": b64_audio}
    if event_id:
        f["event_id"] = event_id
    return f


def function_call_output(call_id, output, event_id=None):
    """DOC_DELEGATION 'Run a custom function and return its result' + DOC_PRIMARY_WS response.item.create
    (item FunctionCallOutput {output, type, call_id}). `output` is a string; we send compact JSON."""
    if not isinstance(output, str):
        output = json.dumps(output, separators=(",", ":"), sort_keys=True)
    return {"type": "response.item.create", "event_id": event_id or _eid("evt_tool_result"),
            "item": {"type": "function_call_output", "call_id": call_id, "output": output}}


def response_create(event_id=None):
    """DOC_PRIMARY_WS response.create: 'continue a delegated response waiting for tool results. Requires
    Responses delegation.' Body is only {type, event_id}."""
    return {"type": "response.create", "event_id": event_id or _eid("evt_continue")}


def _append(kind, content, event_id=None):
    """DOC_PRIMARY_WS session.{instructions,thinking,commentary}.append: content plain string (<= 500 tokens),
    delegation_id REQUIRED and nullable; 'Non-null IDs are not accepted with Responses delegation', so the
    native POC always sends null."""
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"{kind}: content must be a non-empty plain string")
    return {"type": f"session.{kind}.append", "event_id": event_id or _eid(f"evt_{kind}"),
            "delegation_id": None, "content": content}


def instructions_append(content, event_id=None):
    return _append("instructions", content, event_id)


def thinking_append(content, event_id=None):
    return _append("thinking", content, event_id)


def commentary_append(content, event_id=None):
    return _append("commentary", content, event_id)


def session_close(event_id=None):
    """DOC_PRIMARY_WS session.close; the terminal session.closed carries the reason and final usage."""
    return {"type": "session.close", "event_id": event_id or _eid("evt_close")}


CLIENT_TYPES = {"session.start", "session.update", "session.input_audio.append", "session.input_audio.mute",
                "session.input_audio.unmute", "session.instructions.append", "session.thinking.append",
                "session.commentary.append", "response.item.create", "response.create", "session.close"}


def validate_client(frame):
    """Raise ValueError if a frame we are about to send breaks a documented rule. Returns the frame."""
    t = frame.get("type")
    if t not in CLIENT_TYPES:
        raise ValueError(f"not a documented Live client event: {t!r}")
    if t == "session.start":
        s = frame.get("session") or {}
        if s.get("model") != MODEL:
            raise ValueError("session.model must be gpt-live-1")
        d = s.get("delegation") or {}
        if d.get("type") == "responses":
            r = d.get("responses") or {}
            if not r.get("model"):
                raise ValueError("delegation.responses.model is required")
            for tool in r.get("tools") or []:
                if tool.get("type") == "function" and not tool.get("name"):
                    raise ValueError("a function tool needs a name")
    if t.endswith(".append") and t != "session.input_audio.append":
        if "delegation_id" not in frame:
            raise ValueError(f"{t}: delegation_id is required (nullable)")
        if frame["delegation_id"] is not None:
            raise ValueError(f"{t}: non-null delegation_id is not accepted with Responses delegation")
        if not isinstance(frame.get("content"), str):
            raise ValueError(f"{t}: content must be a plain string")
    if t == "response.item.create":
        item = frame.get("item") or {}
        if item.get("type") == "function_call_output":
            if not item.get("call_id") or not isinstance(item.get("output"), str):
                raise ValueError("function_call_output needs call_id and a string output")
    return frame


# ---------------------------------------------------------------------------------------------------------
# Server events (DOC_PRIMARY_WS "Server events")

SERVER_TYPES = {"session.started", "session.updated", "session.input_audio.muted", "session.input_audio.unmuted",
                "session.instructions.appended", "session.thinking.appended", "session.commentary.appended",
                "session.output_audio.delta", "session.input_transcript.delta", "session.output_transcript.delta",
                "session.delegation.created", "response.event", "session.usage.updated", "session.closed", "error",
                "info", "transport.ringing", "transport.answered", "transport.failed",
                "transport.dtmf.received"}


def classify(frame):
    """('documented'|'undocumented', type). An undocumented type is recorded and counted, never dropped."""
    t = frame.get("type", "")
    return ("documented" if t in SERVER_TYPES else "undocumented"), t


def nested(frame):
    """For response.event: (delegation_id, nested event dict). DOC_PRIMARY_WS response.event: 'dispatch on its
    type field'; delegation_id 'may be null or omitted when the event cannot be correlated'."""
    if frame.get("type") != "response.event":
        return None, None
    return frame.get("delegation_id"), (frame.get("event") or {})


def function_call_from(nested_event):
    """DOC_DELEGATION: 'Read completed function calls from nested response.output_item.done events. The finished
    function item contains call_id, name, and arguments; an arguments-done event alone is not sufficient'.
    DOC_RESPONSES_STREAM FunctionCall {arguments, call_id, name, type:"function_call", id?, status?}.
    Returns (call_id, name, arguments_str) or None."""
    if nested_event.get("type") != "response.output_item.done":
        return None
    item = nested_event.get("item") or {}
    if item.get("type") != "function_call":
        return None
    if not item.get("call_id") or not item.get("name"):
        return None
    return item["call_id"], item["name"], item.get("arguments") or "{}"


# DOC_DELEGATION L123: lifecycle events "including response.completed" carry output: [] while calls await results.
# Only response.completed is named; submitting on response.incomplete/failed is NOT documented, so we never do
# (independent review I6(c)). Whether response.completed is the right trigger at all is UNKNOWN U2 (E2 measures it).
TERMINAL_NESTED = {"response.completed"}
