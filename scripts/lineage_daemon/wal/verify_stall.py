"""verify_stall — the v2-3 verify stall bound.

A Green that boots but never makes WAL progress (wedged) makes verify return False
FOREVER. In bg_arm's PREWARMING path that scores acted++ each beat with no alarm — a
silent indefinite PREWARMING stall (the readiness-race lesson at fleet scale). This
wraps the raw verify with a persisted no-progress counter: if Green's WAL progress
is FLAT for M consecutive beats, raise VerifyStalled so the beat firewall alarms +
disarms (consistent with inv 7 anti-orphan).

The distinction the bound must preserve: a SLOW-but-live warmup (WAL advancing) is
NOT stalled — the counter resets on any progress, so only a genuinely wedged Green
(flat WAL) trips it. The counter is persisted in bg_state (survives beats/crashes).
"""


class VerifyStalled(Exception):
    """Green made no WAL progress for M consecutive verify beats — wedged, not
    warming. Raised so the firewall alarms + disarms (never a silent stall)."""


def verify_with_stall_bound(*, verify_fn, progress_fn, wal_dir,
                            max_stall_beats=6, live_fn=None):
    """Return a wrapped verify(root, green_alias) -> bool that raises VerifyStalled
    after max_stall_beats of NO WAL progress. progress_fn(root, green_alias) -> int
    is Green's current WAL high-water mark (advancing = live). A True verify (Green
    ready) resets the counter and short-circuits.

    LIVENESS-BEFORE-STALL (sweep step 5): a green that has done its work and merely HOLDS
    (e.g. a gemini green told '…then hold') has FLAT progress but is NOT wedged. The rule:
    a beat is 'wedged' only when the green is flat AND NOT provably LIVE. When ``live_fn``
    is given and the green is live, flat progress resets the counter (READY-eligible, still
    holding) instead of accruing. A flat + not-live green (dead / frozen-at-trust /
    never-booted) still trips — the gate the bound exists for. ``live_fn`` None = legacy
    (flat always accrues)."""
    from .bg_state import BgStateStore

    def wrapped(root, green_alias):
        store = BgStateStore(wal_dir, root)
        ready = verify_fn(root, green_alias)
        if ready:
            store.write_meta("verify_stall_beats", 0)   # ready = no stall
            return True

        cur = progress_fn(root, green_alias)
        last = store.read_meta("verify_last_seq")
        if last is not None and isinstance(cur, int) and cur > last:
            # WAL advanced -> Green is live-warming, not wedged: reset the counter.
            store.write_meta("verify_stall_beats", 0)
            store.write_meta("verify_last_seq", cur)
            return False

        # flat (or first observation with no advance).
        store.write_meta("verify_last_seq", cur)
        if live_fn is not None:
            try:
                _live = bool(live_fn(root, green_alias))
            except Exception:  # noqa: BLE001 — a liveness hiccup is treated as NOT live
                _live = False   # (fail-closed toward the stall, never a false "holding")
            if _live:
                # LIVE but flat => holding after ingest, not wedged: reset, do not accrue.
                store.write_meta("verify_stall_beats", 0)
                return False
        beats = (store.read_meta("verify_stall_beats", 0) or 0) + 1
        store.write_meta("verify_stall_beats", beats)
        if beats >= max_stall_beats:
            raise VerifyStalled(
                f"{root!r} Green flat for {beats} verify beats AND not provably live "
                f"(wedged, not merely holding) — raising so the firewall alarms/disarms")
        return False

    return wrapped
