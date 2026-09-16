"""RECORD (diarized call record) — pure functions, no Flask. Unit-testable and
windowing-safe: nothing here loads a full timeline into a live session."""

import time as _time


def merge_streams(client_turns, server_tools, surface_events):
    """Merge three conv_id-keyed streams into one ts-ordered event list.
    Each event tagged with _stream ∈ {turn, tool, surface}."""
    events = []
    for t in client_turns or []:
        events.append({**t, "_stream": "turn", "ts": t.get("ts", 0)})
    for t in server_tools or []:
        events.append({**t, "_stream": "tool", "ts": t.get("ts", 0)})
    for s in surface_events or []:
        events.append({**s, "_stream": "surface", "ts": s.get("ts", 0)})
    return sorted(events, key=lambda e: e.get("ts", 0))


def _hhmmss(ts):
    return _time.strftime("%H:%M:%S", _time.localtime(ts)) if ts else "--:--:--"


def diarize(merged):
    """Render the merged stream to a chronological diarized transcript (on-disk artifact)."""
    out = []
    for e in merged:
        t = _hhmmss(e.get("ts"))
        if e["_stream"] == "surface":
            ent = e.get("focused") or {}
            out.append(f"[{t}] SCREEN → {ent.get('kind','?')} '{ent.get('id','')}'")
        elif e["_stream"] == "tool":
            out.append(f"[{t}] [tool: {e.get('tool','?')}]")
        else:
            who = "the operator" if e.get("role") == "user" else "Arturo"
            out.append(f"[{t}] {who}: {e.get('text','')}")
    return "\n".join(out)


_ASK_CUES = ("can you", "could you", "please", "send", "make", "build", "add",
             "show me", "tell", "inject", "retrieve", "get me")
_DECISION_CUES = ("let's", "lets ", "go with", "use the", "decided", "we'll",
                  "approve", "yes,", "ship it")
_FEEDBACK_CUES = ("stop", "don't", "dont ", "too ", "i hate", "i like", "prefer",
                  "instead", "not ", "wrong")


def _user_turns(merged):
    return [e for e in merged if e.get("_stream") == "turn" and e.get("role") == "user"]


def extract_asks_decisions_feedback(merged):
    """Bounded lexical-cue extraction of the operator's intent from user turns. Each item
    carries a ts pointer into the timeline. A turn may fall in multiple buckets.

    UPGRADE PATH (inherited by UNDERSTAND, the operator-ruled next): these lexical cues are
    a deliberately brittle v1 ("yes," over-triggers decisions; paraphrased asks miss).
    The v2 is a finalize-time MODEL pass over the on-disk timeline, owned by the
    UNDERSTAND spec (conversations -> facts/goals). This function is the seam it
    replaces — same signature, same {asks,decisions,feedback} shape, so the injection
    payload builder is unchanged when the model pass lands."""
    asks, decisions, feedback = [], [], []
    for e in _user_turns(merged):
        low = (e.get("text") or "").lower()
        item = {"text": e.get("text", "")[:160], "ts": e.get("ts", 0)}
        if any(c in low for c in _ASK_CUES):
            asks.append(item)
        if any(c in low for c in _DECISION_CUES):
            decisions.append(item)
        if any(c in low for c in _FEEDBACK_CUES):
            feedback.append(item)
    cap = lambda xs: xs[:8]
    return {"asks": cap(asks), "decisions": cap(decisions), "feedback": cap(feedback)}


def is_genuine_shaw(journal):
    """Genuine-the operator injection gate for the client-spine era: a server funnel
    journal OR the app-posted client journal (bearer-authed). Everything else
    (local/test/legacy-untagged) never injects."""
    return journal.get("origin") == "funnel" or journal.get("source") == "client"


def build_injection_payload(call_id, abs_path, counts, merged, extraction):
    """The ONLY thing injected to GM: bounded summary + extraction + counts + marker.
    The full diarized timeline stays on disk (consumers window it via the marker path).

    NB: endcall.sanitize_summary caps to 2 lines/300 chars (built for a 2-line summary),
    so it is applied PER PIECE (each short/single-line) — never to the assembled body,
    which would collapse the buckets. The trusted, validated marker is appended raw."""
    from services.arturo.endcall import build_marker, sanitize_summary
    last_arturo = next((e.get("text", "") for e in reversed(merged)
                        if e.get("_stream") == "turn" and e.get("role") == "arturo"), "")
    lines = [sanitize_summary(
        f"Voice call {call_id}: {counts.get('turns',0)} turns, "
        f"{counts.get('tools',0)} tools, {counts.get('surfaces',0)} surface changes.")]

    def _bucket(name, items):
        if items:
            lines.append(name + ":")
            for it in items:
                lines.append(f"  - {sanitize_summary(it['text'])}  (@{it['ts']:.0f})")

    _bucket("ASKS", extraction.get("asks"))
    _bucket("DECISIONS", extraction.get("decisions"))
    _bucket("FEEDBACK", extraction.get("feedback"))
    if last_arturo:
        lines.append("Last Arturo: " + sanitize_summary(last_arturo))
    lines.append("(full diarized timeline on disk — window it, never full-read)")
    return "\n".join(lines) + "\n" + build_marker(call_id, abs_path)
