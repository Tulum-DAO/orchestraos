"""Call sources. v4: Pocket recordings (~/pocket-recordings/<dir>/{meta.json,transcript.md})."""
import json
import os
from dataclasses import dataclass

MIN_DURATION_S = 120  # gm ruling msg_92221c61 §9.3


@dataclass(frozen=True)
class Call:
    call_id: str
    source: str
    title: str
    created_at: str
    duration_s: float
    category: str
    diarized: bool
    ingested: bool
    transcript: str
    content_hash: str


def load_pocket(rec_dir):
    with open(os.path.join(rec_dir, "meta.json")) as f:
        meta = json.load(f)
    path = os.path.join(rec_dir, "transcript.md")
    transcript = open(path).read() if os.path.exists(path) else ""
    return Call(
        call_id=f"pocket:{meta['recording_id']}",
        source="pocket",
        title=meta.get("title") or os.path.basename(rec_dir.rstrip("/")),
        created_at=meta.get("created_at") or "",
        duration_s=float(meta.get("duration") or 0),
        category=meta.get("category") or "uncategorized",
        diarized=bool(meta.get("diarized")),
        ingested=bool(meta.get("ingested")),
        transcript=transcript,
        content_hash=meta.get("transcript_hash") or "",
    )


def shaw_location():
    """Shaw's current zone record (FOUNDATION §1: the single source of truth for his timezone)."""
    path = os.environ.get("SHAW_LOCATION_FILE") or os.path.expanduser("~/.orchestra/state/shaw-location.json")
    with open(path) as f:
        return json.load(f)


def call_end(call):
    import datetime as dt
    start = dt.datetime.fromisoformat(call.created_at.replace("Z", "+00:00"))
    return (start + dt.timedelta(seconds=call.duration_s)).isoformat()


def _conversation_category(cat):
    # Same predicate as dream/pocket-extract.py:34-39, so both pipelines agree on what a call is.
    cat = (cat or "uncategorized").lower()
    if cat in {"ad", "media", "fragment"}:
        return False
    return cat.startswith(("call", "meeting", "client", "prospect",
                           "internal", "external", "team")) or cat == "uncategorized"


def below_floor(call):
    """Reason the call is never sent to the extractor, or None."""
    if not call.ingested or not call.transcript.strip():
        return "not ingested"
    if not _conversation_category(call.category):
        return f"category {call.category}"
    if call.duration_s < MIN_DURATION_S:
        return f"duration {call.duration_s:.0f}s < {MIN_DURATION_S}s"
    return None
