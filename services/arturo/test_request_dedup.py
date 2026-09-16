# test_request_dedup.py — regression for Finding-2 double tool-execution (2026-09-06, build-162 call).
#
# BUG: ElevenLabs re-sent the SAME chat-completion request 3x in ~4s (history=11 @01:41:55/56/59)
# BEFORE Arturo's answer was appended to history. Each ran generate() independently and TWO of them
# executed the side-effecting inject_message tool (near-identical args) -> the operator got a double inject.
# The requests slipped BOTH existing guards: VQ-6 supersede needs them in-flight/concurrent, and
# latest_is_answered_duplicate needs the prior ANSWER present in the messages (not yet appended on a
# rapid resend). Fix: a recently-PROCESSED request-signature guard keyed on the request itself, with
# a short TTL, suppresses the duplicate BEFORE re-processing -> no double tool-execution.
from services.arturo import voice_guards as vg


def _msgs(latest_user, n_prior_user=5):
    m = []
    for i in range(n_prior_user):
        m.append({"role": "user", "content": f"prior {i}"})
        m.append({"role": "assistant", "content": f"reply {i}"})
    m.append({"role": "user", "content": latest_user})
    return m


def test_signature_stable_for_identical_requests():
    a = vg.request_signature(_msgs("inject a test tool call into ios-watch-dev"))
    b = vg.request_signature(_msgs("inject a test tool call into ios-watch-dev"))
    assert a == b and a, "identical requests must share a stable non-empty signature"


def test_signature_differs_for_distinct_latest_turn():
    a = vg.request_signature(_msgs("inject a test tool call"))
    b = vg.request_signature(_msgs("what time is it"))
    assert a != b, "distinct latest user turns must have distinct signatures"


def test_signature_differs_as_conversation_advances():
    # same latest text but a longer history (a later point in the call) is a different request.
    a = vg.request_signature(_msgs("okay", n_prior_user=3))
    b = vg.request_signature(_msgs("okay", n_prior_user=6))
    assert a != b


def test_guard_suppresses_rapid_duplicate_within_ttl():
    g = vg.RecentRequestGuard(ttl=15.0)
    sig = "sig-A"
    assert g.is_duplicate(sig, now=100.0) is False, "first sight must proceed"
    assert g.is_duplicate(sig, now=101.0) is True, "2nd identical @+1s must be deduped (the double-exec)"
    assert g.is_duplicate(sig, now=104.0) is True, "3rd identical @+4s must be deduped (matches the incident)"


def test_guard_allows_after_ttl_expires():
    g = vg.RecentRequestGuard(ttl=15.0)
    assert g.is_duplicate("sig-B", now=100.0) is False
    assert g.is_duplicate("sig-B", now=120.0) is False, "a genuine re-ask after the TTL must NOT be suppressed"


def test_guard_does_not_suppress_distinct_requests():
    g = vg.RecentRequestGuard(ttl=15.0)
    assert g.is_duplicate("sig-C", now=100.0) is False
    assert g.is_duplicate("sig-D", now=100.1) is False, "a different request must never be deduped against another"


def test_window_is_fixed_from_first_sight_not_sliding():
    # BLOCKER-2 (code-review): the TTL must NOT slide on repeated hits, or a sustained resend storm
    # every <ttl would never expire -> permanent dead air. Window is fixed from the FIRST sight.
    g = vg.RecentRequestGuard(ttl=15.0)
    assert g.is_duplicate("sig-storm", now=100.0) is False   # first sight -> proceed (record t0=100)
    assert g.is_duplicate("sig-storm", now=110.0) is True    # within window -> dup
    assert g.is_duplicate("sig-storm", now=114.0) is True    # still within (does NOT refresh t0)
    assert g.is_duplicate("sig-storm", now=116.0) is False, "16s from FIRST sight > ttl -> must expire and proceed (no permanent suppression)"


def test_evict_releases_a_signature():
    g = vg.RecentRequestGuard(ttl=15.0)
    assert g.is_duplicate("sig-E", now=100.0) is False
    assert g.is_duplicate("sig-E", now=101.0) is True
    g.evict("sig-E")                                          # lead request failed -> release
    assert g.is_duplicate("sig-E", now=102.0) is False, "after evict, a retry must proceed"


def test_concurrent_identical_requests_exactly_one_proceeds():
    # HARDENING-4 (code-review): the load-bearing property — N overlapping identical resends, EXACTLY
    # one proceeds (is_duplicate False), the rest are suppressed. Verifies the record-under-lock crux.
    import threading
    g = vg.RecentRequestGuard(ttl=15.0)
    N = 40
    results = []
    barrier = threading.Barrier(N)
    lock = threading.Lock()

    def worker():
        barrier.wait()                      # maximize overlap
        dup = g.is_duplicate("sig-concurrent", now=100.0)
        with lock:
            results.append(dup)

    threads = [threading.Thread(target=worker) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    proceeded = results.count(False)
    assert proceeded == 1, f"exactly one concurrent caller must proceed, got {proceeded} (of {N})"
