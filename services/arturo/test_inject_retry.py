# test_inject_retry.py — DELIB-BUG-1 retry cadence + DELIB-BUG-3 stub suppression.
from services.arturo import inject_retry as ir


# ---- backoff / next_ts ----
def test_backoff_grows_then_caps():
    assert ir.backoff_secs(0) == 0
    assert ir.backoff_secs(1) == 5
    assert ir.backoff_secs(2) == 10
    assert ir.backoff_secs(100) == 300          # capped at the last band


def test_next_ts():
    assert ir.next_ts(1, 1000.0) == 1005.0


# ---- should_attempt ----
def _client(**kw):
    d = {"source": "client", "status": "ended", "call_id": "vc_client_x"}
    d.update(kw)
    return d


def test_should_attempt_fresh_ended_client():
    assert ir.should_attempt(_client(), now=1000)


def test_should_not_attempt_already_injected():
    assert not ir.should_attempt(_client(gm_injected=True), now=1000)


def test_should_not_attempt_gaveup_or_suppressed():
    assert not ir.should_attempt(_client(gm_inject_gaveup=True), now=1000)
    assert not ir.should_attempt(_client(gm_inject_suppressed=True), now=1000)


def test_should_not_attempt_non_client_or_live():
    assert not ir.should_attempt(_client(source="funnel"), now=1000)
    assert not ir.should_attempt(_client(status="live"), now=1000)


def test_should_respect_next_ts_backoff():
    j = _client(gm_inject_attempts=2, gm_inject_next_ts=2000)
    assert not ir.should_attempt(j, now=1999)   # not due yet
    assert ir.should_attempt(j, now=2001)       # due


def test_should_not_attempt_over_cap():
    assert not ir.should_attempt(_client(gm_inject_attempts=ir.INJECT_ATTEMPT_CAP), now=9e9)


def test_gave_up():
    assert ir.gave_up(_client(gm_inject_attempts=ir.INJECT_ATTEMPT_CAP))
    assert not ir.gave_up(_client(gm_inject_attempts=ir.INJECT_ATTEMPT_CAP, gm_injected=True))


# ---- DELIB-BUG-3 stub suppression ----
def _j(call_id, turns, start, end):
    tl = [{"role": "user", "text": "x", "ts": start}] * 0
    tl = ([{"role": "user"}] * (turns // 2)) + ([{"role": "arturo"}] * (turns - turns // 2))
    return {"call_id": call_id, "turns": tl, "started_at": start, "ended_at": end,
            "source": "client"}


def test_trivial_stub_abutting_richer_sibling_suppressed():
    stub = _j("vc_client_stub", 2, 1000.0, 1008.0)         # 2 turns, 8s
    real = _j("vc_client_real", 44, 1020.0, 2800.0)        # rich, starts 12s after stub's start
    assert ir.is_trivial_stub_with_richer_sibling(stub, [real])


def test_rich_call_not_suppressed():
    real = _j("vc_client_real", 44, 1020.0, 2800.0)
    stub = _j("vc_client_stub", 2, 1000.0, 1008.0)
    assert not ir.is_trivial_stub_with_richer_sibling(real, [stub])


def test_trivial_but_no_sibling_not_suppressed():
    stub = _j("vc_client_stub", 2, 1000.0, 1008.0)
    assert not ir.is_trivial_stub_with_richer_sibling(stub, [])


def test_trivial_stub_far_from_sibling_not_suppressed():
    stub = _j("vc_client_stub", 2, 1000.0, 1008.0)
    far = _j("vc_client_far", 44, 5000.0, 6000.0)          # >120s away
    assert not ir.is_trivial_stub_with_richer_sibling(stub, [far])


def test_two_short_calls_neither_richer_not_suppressed():
    a = _j("vc_client_a", 2, 1000.0, 1008.0)
    b = _j("vc_client_b", 2, 1020.0, 1030.0)
    assert not ir.is_trivial_stub_with_richer_sibling(a, [b])   # sibling not richer


def test_age_bound_skips_stale_calls():
    now = 1_000_000.0
    fresh = _client(ended_at=now - 60)                      # 1 min old -> attempt
    stale = _client(ended_at=now - (7 * 3600))              # 7h old -> skip (>6h bound)
    assert ir.should_attempt(fresh, now=now)
    assert not ir.should_attempt(stale, now=now)


def test_age_bound_missing_ended_at_still_attempts():
    # no ended_at (shouldn't happen for ended client journals) -> don't block on age
    assert ir.should_attempt(_client(), now=1_000_000.0)
