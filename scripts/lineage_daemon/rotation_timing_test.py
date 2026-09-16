"""RED tests — rotation-timing instrumentation (spec §B, DEC-1787808620).

Shadow-safe, PURE-ADDITIVE: a timing decorator wraps each existing seam and writes one
row per section boundary to logs/rotation-timing.jsonl. It changes NO behavior — the
wrapped seam returns the identical value (and re-raises the identical exception). It is
multi-beat aware (rotation_id = lineage_root:successor_sid accumulates across beats) so
an analysis helper can summarise per-rotation total wall-clock, per-section durations,
and the inter-beat gaps (the downtime to minimise).
"""
import json

from scripts.lineage_daemon import rotation_timing as RT


def _writer(rows, **over):
    meta = dict(rotation_id="pm-x:sess-L", lineage_root="pm-x", canary="pm-x",
                successor="pm-x-g2", successor_sid="sess-L", beat_id=1000)
    meta.update(over)
    return RT.make_writer(rows.append, **meta)


# --- the decorator is pure-additive: identical return, start+end rows -----------

def test_timed_seam_returns_identical_value_and_records_two_rows():
    rows = []
    w = _writer(rows)
    seam = lambda c, s: {"outcome": "confirmed"}
    wrapped = RT.timed(seam, "s3_sample1", w)
    out = wrapped("pm-x", "pm-x-g2")
    assert out == {"outcome": "confirmed"}                 # ZERO behavior change
    assert [r["phase"] for r in rows] == ["start", "end"]
    assert all(r["section"] == "s3_sample1" for r in rows)
    end = rows[1]
    assert end["duration_s"] is not None and end["duration_s"] >= 0
    # full schema present
    for k in ("rotation_id", "lineage_root", "canary", "successor",
              "successor_sid", "beat_id", "section", "phase", "t"):
        assert k in end


def test_timed_seam_records_disposition_from_result():
    rows = []
    w = _writer(rows)
    wrapped = RT.timed(lambda: {"status": "completed"}, "promote", w,
                       disposition_fn=lambda r: r["status"])
    wrapped()
    assert rows[1]["disposition"] == "completed"


def test_timed_seam_reraises_and_records_error_disposition():
    rows = []
    w = _writer(rows)

    def boom():
        raise RuntimeError("promote refused")
    wrapped = RT.timed(boom, "promote", w)
    try:
        wrapped()
        assert False, "exception must propagate (pure-additive)"
    except RuntimeError:
        pass
    assert rows[1]["phase"] == "end"
    assert rows[1]["disposition"] == "error:RuntimeError"


# --- JSONL writer emits parseable lines -----------------------------------------

def test_jsonl_writer_appends_parseable_lines(tmp_path):
    path = tmp_path / "rotation-timing.jsonl"
    w = RT.jsonl_writer(str(path), rotation_id="pm-x:sess-L", lineage_root="pm-x",
                        canary="pm-x", successor="pm-x-g2", successor_sid="sess-L",
                        beat_id=1000)
    RT.timed(lambda: 1, "grade", w)()
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(x) for x in lines]
    assert parsed[0]["phase"] == "start" and parsed[1]["phase"] == "end"


# --- analysis helper: per-rotation total + per-section + inter-beat gaps ---------

def _row(section, phase, t, *, beat_id, disposition=None):
    return {"rotation_id": "pm-x:sess-L", "lineage_root": "pm-x", "canary": "pm-x",
            "successor": "pm-x-g2", "successor_sid": "sess-L", "beat_id": beat_id,
            "section": section, "phase": phase, "t": t,
            "duration_s": None, "disposition": disposition}


def test_summarize_computes_total_sections_and_interbeat_gap():
    rows = [
        # beat 1 (promote), t 100..104
        _row("s3_sample1", "start", 100, beat_id=1),
        _row("s3_sample1", "end", 102, beat_id=1),
        _row("promote", "start", 103, beat_id=1),
        _row("promote", "end", 104, beat_id=1),
        # beat 2 (effect verify), t 1000..1001 — a big inter-beat gap (downtime)
        _row("effect_verify", "start", 1000, beat_id=2),
        _row("effect_verify", "end", 1001, beat_id=2, disposition="completed"),
    ]
    s = RT.summarize(rows)["pm-x:sess-L"]
    assert s["wall_clock_s"] == 1001 - 100            # start -> retire/verify end
    assert round(s["sections"]["s3_sample1"], 3) == 2.0
    assert round(s["sections"]["promote"], 3) == 1.0
    # inter-beat gap = beat2.first_start - beat1.last_end = 1000 - 104
    assert s["inter_beat_gap_s"] == 1000 - 104
    assert s["beats"] == 2
    assert s["final_disposition"] == "completed"
