"""Stop-hook grace: a row the Stop-hook digest already showed is not typed into the pane too,
for HOOK_SURFACED_GRACE_S from its last surfacing; past that it is injected once as before."""
import importlib.util
import inspect
import json
import os
from datetime import datetime, timedelta, timezone

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("message_router_hook_grace",
                                               os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


def _row(age_s=None, by="stop-hook:bob", priority="medium", as_str=True, naive=False, raw=None):
    m = {}
    if by:
        m["surfaced_by"] = by
    if age_s is not None:
        at = NOW - timedelta(seconds=age_s)
        m["hook_surfaced_at"] = (at.replace(tzinfo=None) if naive else at).isoformat()
    if raw is not None:
        m["hook_surfaced_at"] = raw
    return {"id": "m1", "priority": priority, "metadata": json.dumps(m) if as_str else m}


def test_default_grace_is_thirty_seconds():
    assert mr.HOOK_SURFACED_GRACE_S == 30


def test_fresh_surfacing_defers_with_seconds_left():
    assert mr.hook_surfaced_defer(_row(age_s=10), NOW.timestamp()) == ("stop-hook:bob", 20)
    assert mr.hook_surfaced_ready(_row(age_s=10), NOW.timestamp()) is False


def test_dict_metadata_and_naive_stamp_are_read():
    assert mr.hook_surfaced_defer(_row(age_s=5, as_str=False), NOW.timestamp())
    assert mr.hook_surfaced_defer(_row(age_s=5, naive=True), NOW.timestamp())


def test_past_the_grace_it_is_released_and_skips_the_breathe_window():
    assert mr.hook_surfaced_defer(_row(age_s=30), NOW.timestamp()) is None
    assert mr.hook_surfaced_ready(_row(age_s=30), NOW.timestamp()) is True


def test_critical_is_never_deferred_or_released():
    assert mr.hook_surfaced_defer(_row(age_s=1, priority="critical"), NOW.timestamp()) is None
    assert mr.hook_surfaced_ready(_row(age_s=99, priority="critical"), NOW.timestamp()) is False


def test_unstamped_or_unreadable_rows_deliver_as_before():
    t = NOW.timestamp()
    assert mr.hook_surfaced_defer({"id": "m", "metadata": None}, t) is None
    assert mr.hook_surfaced_defer({"id": "m", "metadata": "{not json"}, t) is None
    assert mr.hook_surfaced_defer(_row(by=None, age_s=1), t) is None      # no surfaced_by
    assert mr.hook_surfaced_defer(_row(raw="yesterday-ish"), t) is None
    assert mr.hook_surfaced_ready(_row(raw="yesterday-ish"), t) is False
    assert mr.hook_surfaced_ready({"id": "m", "metadata": None}, t) is False


def test_loop_defers_before_the_throttle_and_releases_the_breathe_window():
    src = inspect.getsource(mr.main)
    d = src.index("_hs = hook_surfaced_defer(msg, now)")
    assert d < src.index("if session in delivered_to:")
    breathe = src.index("if idle_secs < BREATHE_WINDOW_S")
    assert "not _hook_release" in src[breathe:src.index("\n", breathe)]
