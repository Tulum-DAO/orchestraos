"""context_pct_of_window / context_pct_of_budget: one reading, two named denominators, fresh or None."""
import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from context_reading import STATUSLINE_SLACK_S, claude_context  # noqa: E402

SID = "11111111-2222-4333-8444-555555555555"
T0 = 1_791_560_000.0


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _seat(tmp_path, *, remaining=61, used_pct=49, taken=T0, last_entry=T0 - 5, raw=None):
    (tmp_path / f"claude-ctx-{SID}.json").write_text(raw if raw is not None else json.dumps(
        {"session_id": SID, "remaining_percentage": remaining, "used_pct": used_pct, "timestamp": taken}))
    tr = tmp_path / f"{SID}.jsonl"
    tr.write_text("\n".join([
        json.dumps({"type": "user", "timestamp": _iso(last_entry - 30)}),
        json.dumps({"type": "assistant", "timestamp": _iso(last_entry)}),
        json.dumps({"type": "last-prompt"}),          # entries without a timestamp are skipped
    ]) + "\n")
    return str(tr)


def test_a_1m_seat_at_39_percent_of_its_window_reads_39_and_49(tmp_path):
    # The incident's numbers: ~390K of a 1M window = 39% used; the status bar drew 49 (scaled to 80%).
    tr = _seat(tmp_path)
    assert claude_context(SID, tr, tmp_dir=str(tmp_path)) == (39, 49)


def test_of_window_is_claude_codes_own_figure_not_derived_from_the_budget(tmp_path):
    # A reading where the budget figure is NOT window/0.8 (another status line's scale) still
    # serves the window figure from remaining_percentage: no scale or window size is assumed.
    tr = _seat(tmp_path, remaining=70, used_pct=55)
    assert claude_context(SID, tr, tmp_dir=str(tmp_path)) == (30, 55)


def test_an_idle_seat_with_an_old_reading_is_still_fresh(tmp_path):
    # Hours old, but taken after the seat's last transcript entry: the context has not moved.
    tr = _seat(tmp_path, taken=T0, last_entry=T0 - 4 * 3600)
    assert claude_context(SID, tr, tmp_dir=str(tmp_path)) == (39, 49)


def test_a_reading_older_than_the_seats_last_activity_is_none(tmp_path):
    tr = _seat(tmp_path, taken=T0, last_entry=T0 + STATUSLINE_SLACK_S + 1)
    assert claude_context(SID, tr, tmp_dir=str(tmp_path)) == (None, None)


def test_a_reading_within_the_status_line_lag_counts(tmp_path):
    tr = _seat(tmp_path, taken=T0, last_entry=T0 + STATUSLINE_SLACK_S - 1)
    assert claude_context(SID, tr, tmp_dir=str(tmp_path)) == (39, 49)


def test_no_evidence_of_freshness_is_none(tmp_path):
    _seat(tmp_path)
    assert claude_context(SID, None, tmp_dir=str(tmp_path)) == (None, None), "no transcript"
    assert claude_context(SID, str(tmp_path / "missing.jsonl"), tmp_dir=str(tmp_path)) == (None, None)
    empty = tmp_path / "empty.jsonl"
    empty.write_text(json.dumps({"type": "last-prompt"}) + "\n")
    assert claude_context(SID, str(empty), tmp_dir=str(tmp_path)) == (None, None), "no timestamps"


def test_absent_or_malformed_readings_are_none(tmp_path):
    tr = _seat(tmp_path)
    assert claude_context(None, tr, tmp_dir=str(tmp_path)) == (None, None)
    assert claude_context("other-sid", tr, tmp_dir=str(tmp_path)) == (None, None), "no file"
    for raw in ("not json", "[]", json.dumps({"session_id": SID, "remaining_percentage": 61,
                                               "used_pct": 49})):        # no timestamp
        _seat(tmp_path, raw=raw)
        assert claude_context(SID, tr, tmp_dir=str(tmp_path)) == (None, None), raw


def test_a_file_for_another_session_is_not_this_seats_reading(tmp_path):
    tr = _seat(tmp_path, raw=json.dumps({"session_id": "someone-else", "remaining_percentage": 61,
                                         "used_pct": 49, "timestamp": T0}))
    assert claude_context(SID, tr, tmp_dir=str(tmp_path)) == (None, None)


def test_out_of_range_values_never_reach_the_app(tmp_path):
    tr = _seat(tmp_path, remaining=143, used_pct=-3)
    assert claude_context(SID, tr, tmp_dir=str(tmp_path)) == (None, None)
    tr = _seat(tmp_path, remaining=61, used_pct=True)
    assert claude_context(SID, tr, tmp_dir=str(tmp_path)) == (39, None)


# --- agent-status: whose reading it is --------------------------------------------------------

def _agent_status():
    import importlib.util
    spec = importlib.util.spec_from_file_location("agent_status_pair", os.path.join(_HERE, "agent-status.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _hook(tr, ts):
    return {"session_id": SID, "transcript_path": tr, "ts": ts, "state": "idle"}


def test_a_claude_seat_gets_its_reading(tmp_path, monkeypatch):
    m = _agent_status()
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    tr = _seat(tmp_path)
    proc = {"runtime": "claude", "elapsed": "02:00:00"}            # started T0 - 2 h
    got = m._context_pair(proc, "claude", _hook(tr, T0 - 60), "seat", now=T0)
    assert got == {"context_pct_of_window": 39, "context_pct_of_budget": 49}


def test_a_hook_event_older_than_the_process_is_another_occupants(tmp_path, monkeypatch):
    # Live, 2026-10-09: a pane kept the event of a Claude session that ran there weeks before.
    m = _agent_status()
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    tr = _seat(tmp_path)
    proc = {"runtime": "claude", "elapsed": "1-00:00:00"}          # started T0 - 1 day
    got = m._context_pair(proc, "claude", _hook(tr, T0 - 3 * 86400), "seat", now=T0)
    assert got == {"context_pct_of_window": None, "context_pct_of_budget": None}


def test_a_non_claude_seat_never_reads_a_claude_file(tmp_path, monkeypatch):
    m = _agent_status()
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    tr = _seat(tmp_path)
    proc = {"runtime": "gemini", "elapsed": "02:00:00"}
    got = m._context_pair(proc, "gemini", _hook(tr, T0 - 60), "seat", now=T0)
    assert got == {"context_pct_of_window": None, "context_pct_of_budget": None}


def test_etime_parses_every_ps_form():
    e = _agent_status()._etime_s
    assert (e("05:07"), e("01:05:07"), e("2-01:05:07"), e("garbage")) == (307, 3907, 176707, None)
