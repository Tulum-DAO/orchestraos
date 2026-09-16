# inject_retry.py — pure helpers for the durable gm-inject retry (DELIB-BUG-1) + split-call stub
# suppression (DELIB-BUG-3). Kept pure (no I/O) so the retry/backoff/stub logic is unit-testable.
#
# DELIB-BUG-1: finalize_from_client used to set gm_injected=True OPTIMISTICALLY before the inject
# succeeded — so a re-notify during the retry window short-circuited, and once the bounded retry
# failed (gm busy) NOTHING re-drove it (the watchdog only finalizes LIVE server journals). A real
# 44-turn call transcript was lost. The new model: gm_injected stays False until an inject SUCCEEDS;
# a background sweeper patiently re-drives ended source=client journals with backoff until gm accepts
# (cap + one escalation).
#
# Delivery guarantee is AT-LEAST-ONCE (reviewer note): if the process crashes in the narrow window
# between gm returning 200 and the durable gm_injected=True write, the sweeper re-drives on restart
# and gm may receive the summary twice. This is the deliberate trade — transcript safety (never lose
# the operator's call) is prioritized over exactly-once, and a duplicate summary marker is harmless to gm.

# retry cadence: attempt N waits backoff(N) seconds from the prior attempt. Bounded, patient.
_BACKOFF = [5, 10, 20, 30, 45, 60, 90, 120, 180, 240, 300]   # seconds
INJECT_ATTEMPT_CAP = 40                                        # ~ covers >30 min of a busy gm


def backoff_secs(attempts):
    """Seconds to wait before the NEXT attempt given how many have already happened."""
    if attempts <= 0:
        return 0
    idx = min(attempts - 1, len(_BACKOFF) - 1)
    return _BACKOFF[idx]


def next_ts(attempts, now):
    return now + backoff_secs(attempts)


# Age bound: the sweeper only auto-heals RECENT calls. Without this, a deploy would re-inject every
# historical un-injected client journal (old test fixtures, day-old calls) as stale noise into
# the operator's LIVE gm. A call older than this at finalize is not worth surfacing unprompted (the durable
# JSON is still on disk for manual retry). Strictly narrows what the sweeper touches.
INJECT_MAX_AGE_S = 6 * 3600.0


def should_attempt(journal, now, cap=INJECT_ATTEMPT_CAP, max_age_s=INJECT_MAX_AGE_S):
    """Sweeper predicate: is this ended source=client journal due for another inject attempt?
    True iff it's a genuine RECENT client call, not yet injected, not given up/suppressed, under the
    attempt cap, and its next-attempt time has arrived."""
    if journal.get("source") != "client":
        return False
    if journal.get("status") != "ended":
        return False
    if journal.get("gm_injected"):
        return False
    if journal.get("gm_inject_gaveup"):
        return False
    if journal.get("gm_inject_suppressed"):        # trivial stub (BUG-3) — never inject
        return False
    if int(journal.get("gm_inject_attempts", 0)) >= cap:
        return False
    # age bound: skip stale calls (ended_at older than max_age_s) so a deploy can't spray old calls.
    ended = journal.get("ended_at")
    if isinstance(ended, (int, float)) and (now - ended) > max_age_s:
        return False
    nts = journal.get("gm_inject_next_ts")
    if isinstance(nts, (int, float)) and now < nts:
        return False
    return True


def gave_up(journal, cap=INJECT_ATTEMPT_CAP):
    return int(journal.get("gm_inject_attempts", 0)) >= cap and not journal.get("gm_injected")


# ---- DELIB-BUG-3: split-call trivial-stub suppression ---------------------------------------
# A failed first EL connection produces a tiny stub journal ("Hey the operator, Arturo here", 2 turns, few
# seconds) with its own conv_id, immediately followed by the REAL call. The stub injected first and
# occupied gm exactly when the real call's inject fired. Rule: a client journal that is BOTH trivial
# (< min_turns real turns AND < max_secs duration) AND time-abuts a RICHER client sibling is a stub
# → suppress its inject (the richer sibling carries the call).

def _real_turns(j):
    return sum(1 for t in j.get("turns", []) if t.get("role") in ("user", "arturo"))


def _duration(j):
    s, e = j.get("started_at"), j.get("ended_at")
    if isinstance(s, (int, float)) and isinstance(e, (int, float)) and e >= s:
        return e - s
    return None


def is_trivial_stub_with_richer_sibling(journal, siblings, min_turns=3, max_secs=30.0,
                                        abut_secs=120.0):
    """`journal`: the client journal being finalized. `siblings`: OTHER client journals (list of
    dicts). True if `journal` is a trivial stub that abuts a richer client sibling → suppress inject.
    Trivial = < min_turns real turns AND (duration known and < max_secs, or duration unknown but
    ≤2 turns). Abuts = the sibling's start is within abut_secs of this journal's start/end."""
    turns = _real_turns(journal)
    dur = _duration(journal)
    trivial = turns < min_turns and dur is not None and dur < max_secs
    if not trivial:
        return False
    j_start = journal.get("started_at")
    j_end = journal.get("ended_at") or j_start
    for sib in siblings:
        if sib.get("call_id") == journal.get("call_id"):
            continue
        if _real_turns(sib) <= turns:
            continue                                   # sibling must be RICHER
        s_start = sib.get("started_at")
        if not isinstance(s_start, (int, float)):
            continue
        # abut: sibling starts near this stub's window (before or shortly after)
        near = False
        for anchor in (j_start, j_end):
            if isinstance(anchor, (int, float)) and abs(s_start - anchor) <= abut_secs:
                near = True
                break
        if near:
            return True
    return False
