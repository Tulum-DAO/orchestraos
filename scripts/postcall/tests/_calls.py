"""Synthetic diarized calls for the review-fix tests: made-up names, one turn per item."""
import dataclasses

from postcall import extract, sources


def item(quote, *, owner="shaw", deliverable="pricing deck", operation="send", action_type="document_send",
         text=None, timeframe="by Friday", counterpart="Dana Reyes", **extra):
    return {"owner": owner, "kind": "commitment", "counterpart": counterpart, "action_type": action_type,
            "operation": operation, "deliverable": deliverable, "commitment_text": text or f"Send Dana the {deliverable}",
            "timeframe": timeframe, "quote": quote, "recurring": False, **extra}


def call(items, *, call_id="pocket:c1", content_hash="h1", when="2026-11-24T19:00:00Z", quotes=None):
    """A diarized two-party call: Shaw is SPEAKER_00; each quote (default: each item's) is its own turn."""
    turns = ["**SPEAKER_00** _[00:01]_\nHi Dana, thanks for making time. This is Shaw."]
    seen = []
    for it in items:
        if it["quote"] in seen:
            continue
        seen.append(it["quote"])
        label = "SPEAKER_00" if it["owner"] == "shaw" else "SPEAKER_01"
        turns.append(f"**{label}** _[0{len(seen)}:00]_\n{it['quote']}")
    transcript = "# Sync\n\n## Transcript\n\n" + "\n\n".join(turns) + "\n"
    c = sources.Call(call_id=call_id, source="pocket", title="Synthetic Sync", created_at=when, duration_s=600.0,
                     category="call-external", diarized=True, ingested=True, transcript=transcript,
                     content_hash=content_hash)
    raw = {"shaw_label": "SPEAKER_00", "shaw_label_evidence": "This is Shaw.", "items": list(items)}
    return c, (lambda _c, existing=None: extract.Extraction(ok=True, raw=raw))


def verified(monkeypatch):
    """Stand-in for a calendar-verified roster: every grounded item is speaker-verified."""
    real = extract.ground

    def ground(c, extraction):
        return [dataclasses.replace(i, trust="verified") for i in real(c, extraction)]

    monkeypatch.setattr(extract, "ground", ground)
