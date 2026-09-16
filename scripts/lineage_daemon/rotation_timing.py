"""rotation_timing.py — shadow-safe rotation instrumentation (spec §B, DEC-1787808620).

PURE-ADDITIVE: a timing decorator wraps each existing rotation seam and writes one row
per section boundary to a JSONL log. It changes NO behavior — the wrapped seam returns
the identical value and re-raises the identical exception; the only side effect is the
log write (best-effort, swallowed). Because it touches no decision, it ships safe live
immediately (no arm gate — it only writes a log).

Event schema (one row per section boundary):
    {rotation_id, lineage_root, canary, successor, successor_sid, beat_id,
     section, phase: "start"|"end", t: epoch, duration_s, disposition}

Multi-beat aware: rotation_id = "<lineage_root>:<successor_sid>" accumulates across the
beats of ONE rotation (spawn/readback -> promote -> effect-verify), so `summarize` can
report the wall-clock start->retire AND the inter-beat gaps (the downtime to minimise).

Sections (the rotation lifecycle boundaries):
    spawn_pane, inject_init, readback_author, s3_sample1, s3_settle, s3_sample2, grade,
    safety_gate, graduation_gate, budget_gate, promote, tmux_consolidate, retire,
    resolve_hold, auto_resume_inject, effect_verify.
"""
import json
import time

SECTIONS = (
    "spawn_pane", "inject_init", "readback_author", "s3_sample1", "s3_settle",
    "s3_sample2", "grade", "safety_gate", "graduation_gate", "budget_gate",
    "promote", "tmux_consolidate", "retire", "resolve_hold",
    "auto_resume_inject", "progressing_watch", "effect_verify", "escalate",
)


def make_writer(sink, *, rotation_id, lineage_root, canary, successor,
                successor_sid, beat_id):
    """A section-boundary writer bound to ONE rotation's identity. `sink(row_dict)`
    receives each row (a list.append in tests; a JSONL appender live). The write is
    the ONLY side effect and is best-effort — a sink failure never propagates into the
    wrapped seam (the instrumentation must never break a rotation)."""
    def write(section, phase, t, *, duration_s=None, disposition=None):
        row = {
            "rotation_id": rotation_id, "lineage_root": lineage_root,
            "canary": canary, "successor": successor,
            "successor_sid": successor_sid, "beat_id": beat_id,
            "section": section, "phase": phase, "t": t,
            "duration_s": duration_s, "disposition": disposition,
        }
        try:
            sink(row)
        except Exception:  # noqa: BLE001 -- logging must never break a rotation
            pass
    return write


def jsonl_writer(path, **meta):
    """A make_writer whose sink appends one JSON line per row to `path` (created if
    absent). Best-effort append; any IO error is swallowed by make_writer's guard."""
    import os

    def sink(row):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps(row) + "\n")
    return make_writer(sink, **meta)


def timed(seam, section, writer, *, disposition_fn=None, clock=time.time):
    """Wrap a seam callable to emit a start row before the call and an end row after
    (with duration_s + disposition). Returns a callable with the SAME signature and
    return value. On exception the end row records disposition='error:<Type>' and the
    exception RE-RAISES unchanged (pure-additive)."""
    def wrapped(*args, **kwargs):
        t0 = clock()
        writer(section, "start", t0)
        disposition = None
        try:
            result = seam(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 -- record then re-raise unchanged
            t1 = clock()
            writer(section, "end", t1, duration_s=t1 - t0,
                   disposition=f"error:{type(e).__name__}")
            raise
        t1 = clock()
        if disposition_fn is not None:
            try:
                disposition = disposition_fn(result)
            except Exception:  # noqa: BLE001 -- a bad disposition_fn never breaks the seam
                disposition = None
        writer(section, "end", t1, duration_s=t1 - t0, disposition=disposition)
        return result
    return wrapped


def summarize(rows):
    """Group rows by rotation_id -> {wall_clock_s, sections{name: total_s}, beats,
    inter_beat_gap_s, final_disposition}.

      wall_clock_s     = last end t - first start t across ALL beats of the rotation.
      sections[name]   = sum of (end.t - start.t) for each matched start/end pair.
      beats            = count of distinct beat_id.
      inter_beat_gap_s = sum of gaps between consecutive beats (next beat's first t -
                         this beat's last t) — the downtime between the promote beat and
                         the effect-verify beat(s).
      final_disposition= the disposition of the last end row that carries one.
    """
    by_rot = {}
    for r in rows:
        by_rot.setdefault(r["rotation_id"], []).append(r)

    out = {}
    for rid, rrows in by_rot.items():
        srows = sorted(rrows, key=lambda x: (x["t"], 0 if x["phase"] == "start" else 1))
        ts = [r["t"] for r in srows]
        wall = (max(ts) - min(ts)) if ts else 0

        sections = {}
        open_start = {}
        for r in srows:
            sec = r["section"]
            if r["phase"] == "start":
                open_start[sec] = r["t"]
            elif r["phase"] == "end" and sec in open_start:
                sections[sec] = sections.get(sec, 0) + (r["t"] - open_start.pop(sec))

        # per-beat time span, then gaps between consecutive beats
        beat_span = {}
        for r in srows:
            b = r["beat_id"]
            lo, hi = beat_span.get(b, (r["t"], r["t"]))
            beat_span[b] = (min(lo, r["t"]), max(hi, r["t"]))
        ordered = sorted(beat_span.items(), key=lambda kv: kv[1][0])
        gap = 0
        for i in range(1, len(ordered)):
            gap += ordered[i][1][0] - ordered[i - 1][1][1]

        final_disp = None
        for r in srows:
            if r["phase"] == "end" and r.get("disposition"):
                final_disp = r["disposition"]

        out[rid] = {
            "wall_clock_s": wall,
            "sections": sections,
            "beats": len(beat_span),
            "inter_beat_gap_s": gap,
            "final_disposition": final_disp,
        }
    return out
