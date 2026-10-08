"""Queue order: priority_score descending, ties NEWEST first (2026-10-08, P4).

The phone app (build 258) orders equal-score cards newest-first, as the operator approved.
The watch renders the gateway's order as-is. With ties oldest-first on the server, the two
surfaces disagreed on equal-score rows. One helper orders both queues the gateway serves.
"""
import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _gw():
    spec = importlib.util.spec_from_file_location("watch_gateway", ROOT / "scripts" / "watch_gateway.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_higher_priority_first_then_newest_first_on_ties():
    gw = _gw()
    rows = [
        {"id": "old-tie", "priority_score": 50.0, "created_at": "2026-10-08T09:00:00+00:00"},
        {"id": "low",     "priority_score": 20.0, "created_at": "2026-10-08T09:30:00+00:00"},
        {"id": "new-tie", "priority_score": 50.0, "created_at": "2026-10-08T09:01:00+00:00"},
        {"id": "high",    "priority_score": 90.0, "created_at": "2026-10-08T08:00:00+00:00"},
    ]
    assert [r["id"] for r in gw._sort_queue(rows)] == ["high", "new-tie", "old-tie", "low"]


def test_missing_fields_never_break_the_feed():
    gw = _gw()
    rows = [{"id": "a", "priority_score": None, "created_at": None},
            {"id": "b", "priority_score": 10, "created_at": "2026-10-08T09:00:00+00:00"}]
    assert [r["id"] for r in gw._sort_queue(rows)] == ["b", "a"]


def test_every_queue_the_gateway_serves_uses_the_one_order():
    """Derived from the code: no handler sorts a queue with its own priority key any more."""
    src = (ROOT / "scripts" / "watch_gateway.py").read_text()
    assert not re.search(r'\.sort\(key=lambda r: \(-r\["priority_score"\]', src)
    assert src.count("_sort_queue(") >= 3          # the definition + pending approvals + questionnaires
