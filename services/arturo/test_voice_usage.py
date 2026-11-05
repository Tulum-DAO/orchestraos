"""Per-vendor daily voice usage cap (Hume Task 9, frozen artifact d3cbafa9).
RED-first: unit tests for UsageStore — minutes accumulate per vendor per day,
one 80% alert per vendor per day, 100% blocks, resets next day."""
from services.arturo import voice_usage as vu


def test_minutes_accumulate_per_vendor_per_day(tmp_path):
    clock = [1_700_000_000.0]
    alerts = []
    u = vu.UsageStore(path=tmp_path / "u.json", cap_min=100, now=lambda: clock[0], alert=alerts.append)
    u.add_seconds("hume", 600)
    u.add_seconds("elevenlabs", 60)
    assert u.today_minutes("hume") == 10 and u.today_minutes("elevenlabs") == 1
    assert not u.over_cap("hume")


def test_alert_once_at_80_percent_and_cap_blocks(tmp_path):
    clock = [1_700_000_000.0]; alerts = []
    u = vu.UsageStore(path=tmp_path / "u.json", cap_min=10, now=lambda: clock[0], alert=alerts.append)
    u.add_seconds("hume", 8 * 60)
    u.add_seconds("hume", 30)
    assert len(alerts) == 1 and "hume" in alerts[0] and "80%" in alerts[0]
    u.add_seconds("hume", 2 * 60)
    assert u.over_cap("hume")


def test_resets_next_day(tmp_path):
    clock = [1_700_000_000.0]
    u = vu.UsageStore(path=tmp_path / "u.json", cap_min=10, now=lambda: clock[0], alert=lambda m: None)
    u.add_seconds("hume", 11 * 60)
    assert u.over_cap("hume")
    clock[0] += 86400
    assert not u.over_cap("hume") and u.today_minutes("hume") == 0


def test_default_alert_is_non_blocking(monkeypatch):
    # gm must-fix: the default alert must NOT block the caller (it fires from end() on the relay
    # finalize path — same class as "timers must never block the relay"). A slow subprocess must
    # not stall the caller; the alert is fire-and-forget on a daemon thread.
    import time as _t
    started = _t.time()

    def slow_run(*a, **k):
        _t.sleep(2.0)

    monkeypatch.setattr(vu.subprocess, "run", slow_run)
    vu._card_alert("hume at 80% of cap")
    elapsed = _t.time() - started
    assert elapsed < 0.5, f"_card_alert blocked {elapsed:.2f}s — must be fire-and-forget"


def test_the_cap_comes_from_the_settings_file_on_every_check(tmp_path):
    limits = tmp_path / "voice-limits.json"
    u = vu.UsageStore(path=tmp_path / "u.json", now=lambda: 1_700_000_000.0, alert=lambda m: None, limits_path=limits)
    assert u.cap_min == vu.DEFAULT_CAP
    limits.write_text('{"daily_cap_min": 5}')
    u.add_seconds("hume", 5 * 60)
    assert u.cap_min == 5 and u.over_cap("hume")
    limits.write_text('{"daily_cap_min": "lots"}')
    assert u.cap_min == vu.DEFAULT_CAP


def test_an_explicit_cap_still_wins(tmp_path):
    limits = tmp_path / "voice-limits.json"
    limits.write_text('{"daily_cap_min": 5}')
    u = vu.UsageStore(path=tmp_path / "u.json", cap_min=50, now=lambda: 1_700_000_000.0, alert=lambda m: None, limits_path=limits)
    assert u.cap_min == 50
