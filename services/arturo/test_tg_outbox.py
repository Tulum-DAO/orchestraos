# test_tg_outbox.py — BUG-1 outbound Telegram cap + dedupe.
from services.arturo import tg_outbox as to


def test_duplicate_message_blocked_within_dedupe_window():
    ob = to.TelegramOutbox(max_per_window=100, dedupe_s=300)
    ok, _ = ob.allow("[Forwarded to GM]: identify agents for adaptive payments", now=1000)
    assert ok
    # the exact replay that stormed the operator — blocked
    ok2, reason = ob.allow("[Forwarded to GM]: identify agents for adaptive payments", now=1001)
    assert not ok2 and reason == "duplicate"


def test_dedupe_is_normalized():
    ob = to.TelegramOutbox(max_per_window=100, dedupe_s=300)
    ob.allow("Still investigating.", now=0)
    ok, reason = ob.allow("  still   INVESTIGATING.  ", now=1)   # whitespace/case variant
    assert not ok and reason == "duplicate"


def test_duplicate_allowed_after_dedupe_window():
    ob = to.TelegramOutbox(max_per_window=100, dedupe_s=300)
    ob.allow("ping", now=0)
    ok, _ = ob.allow("ping", now=400)                            # 400s > 300s dedupe → allowed again
    assert ok


def test_rate_cap_blocks_burst_of_distinct_messages():
    ob = to.TelegramOutbox(max_per_window=6, window_s=60, dedupe_s=0)
    allowed = 0
    for i in range(20):
        ok, _ = ob.allow(f"distinct message number {i}", now=1000 + i)   # all within 20s window
        allowed += ok
    assert allowed == 6                                          # capped at 6/window


def test_rate_window_slides():
    ob = to.TelegramOutbox(max_per_window=2, window_s=60, dedupe_s=0)
    assert ob.allow("a", now=0)[0]
    assert ob.allow("b", now=1)[0]
    assert not ob.allow("c", now=2)[0]                           # 3rd in window → blocked
    assert ob.allow("d", now=70)[0]                              # window slid past a,b → allowed


def test_the_actual_storm_is_contained():
    # simulate the 26 send_telegram calls: 1 distinct + 25 identical replays over ~10s
    ob = to.TelegramOutbox(max_per_window=6, window_s=60, dedupe_s=300)
    sent = 0
    for i in range(26):
        msg = "unique opener" if i == 0 else "[Forwarded to GM]: same replay text"
        ok, _ = ob.allow(msg, now=1000 + i * 0.4)
        sent += ok
    # opener (1) + the FIRST forwarded (1); all 24 further replays deduped → 2 total, not 26
    assert sent == 2
