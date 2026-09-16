"""s3_confirm_seam.py — the REAL S3 confirm seam for the fleet beat.

REPLACES cron_beat's old hardcoded stub

    def confirm_fn(canary, successor):
        return {"outcome": "held", "reason": "hard-rotation-not-enabled-first-window"}

which was a DECEPTIVE constant: it always returned `held` regardless of the
successor, so if soft_only were ever flipped the "confirm" gate would reject every
rotation for a reason that no longer described reality. This module wires the ACTUAL
S3 two-sample confirm (confirm_correct via beat.two_sample_confirm + the real
focus_registry rotation_gate) behind an injectable per-rotation seams provider.

Contract of the produced `confirm_fn(canary, successor) -> {outcome, ...}` (the exact
shape execute_rotation consumes at execute.py:142-146):

  * seams_provider(canary, successor) yields the live S3 seams for THIS rotation
    (expected / ground_truth / first_effect / read_successor / correction_fn /
    inject_correction / settle_fn ...), all daemon-held (ground truth is NEVER the
    successor's echo). When it yields those seams, the real beat.two_sample_confirm
    runs — TWO passing samples (content gate + drift check) => confirmed; anything
    less => held (execute_rotation retires NOTHING).

  * When seams_provider yields None (no live seams available — the current first
    window, where the successor-read / daemon-held-ground-truth plumbing is not yet
    provisioned) the confirm_fn FAILS CLOSED: it returns `held` with an HONEST reason
    ("s3-live-seams-unavailable"), never `confirmed`. A confirm gate that cannot
    actually verify the successor must HOLD, never pass.

  * ANY exception building or running the confirm ALSO fails closed to `held`
    (fail-toward-not-retiring — same KEEP-polarity as the whole retire path).

This keeps the seam INJECTABLE (cron_beat forwards its own seams_provider; tests
drive a hermetic one) and REVERSIBLE (default provider => held, so nothing changes
the current first-window posture where soft_only defers every hard rotate anyway).
"""

HELD = "held"


def _held(reason, **extra):
    d = {"outcome": HELD, "reason": reason}
    d.update(extra)
    return d


def build_s3_confirm_fn(*, seams_provider=None, gate_fn=None, completion_mode=False):
    """Return a real `confirm_fn(canary, successor) -> {outcome: confirmed|held,...}`.

    seams_provider(canary, successor) -> dict | None
        The per-rotation LIVE S3 seams. A dict supplies the two_sample_confirm
        kwargs for this rotation (daemon-held ground truth + at-source read + the
        correction/inject seams). None => no live verification is possible right now
        => the confirm HOLDS (never auto-confirms). Default None (first-window safe).
    gate_fn : the content gate (default = focus_registry.gate.rotation_gate, imported
        lazily so this module has no import-time coupling to the gate). A seams dict
        may override it per rotation via key "gate_fn".
    completion_mode : DEC-1787808620. When True, the DEFAULT gate runs in
        completion_mode (comprehension-only; effect deferred post-promote; focus-floor
        exempt) — the SEPARATE completion-provider confirm. The spawn-time confirm
        (completion_mode=False, default) is byte-identical to before. An explicit
        gate_fn or a seams-level "gate_fn" still wins over the default.
    """
    def _completion_gate(observed, expected, evidence, ground_truth,
                         effect=None, cwd=".", effect_runner=None):
        from scripts.focus_registry.gate import rotation_gate
        return rotation_gate(observed, expected, evidence, ground_truth,
                             effect=effect, cwd=cwd, effect_runner=effect_runner,
                             completion_mode=True)

    def confirm_fn(canary, successor):
        # 1) no seams provider at all -> HOLD (honest: we cannot verify).
        if seams_provider is None:
            return _held("s3-live-seams-unavailable", canary=canary,
                         successor=successor)
        # 2) resolve the per-rotation live seams; fail closed on any error.
        try:
            seams = seams_provider(canary, successor)
        except Exception as e:  # noqa: BLE001 -- fail toward HOLD, never confirm
            return _held(f"s3-seams-provider-error:{type(e).__name__}",
                         canary=canary, successor=successor)
        if not seams:
            return _held("s3-live-seams-unavailable", canary=canary,
                         successor=successor)
        # 3) run the REAL two-sample confirm over the live seams.
        try:
            from scripts.lineage_daemon.beat import two_sample_confirm
            g = seams.get("gate_fn") or gate_fn
            if g is None:
                if completion_mode:
                    g = _completion_gate
                else:
                    from scripts.focus_registry.gate import rotation_gate as g
            real = two_sample_confirm(
                expected=seams["expected"],
                ground_truth=seams["ground_truth"],
                first_effect=seams.get("first_effect"),
                read_successor=seams["read_successor"],
                gate_fn=g,
                correction_fn=seams["correction_fn"],
                inject_correction=seams["inject_correction"],
                settle_fn=seams.get("settle_fn"),
                second_read=seams.get("second_read"),
                max_rounds=seams.get("max_rounds", 3),
                cwd=seams.get("cwd", "."),
                effect_runner=seams.get("effect_runner"),
            )
            return real(canary, successor)
        except Exception as e:  # noqa: BLE001 -- fail toward HOLD, never confirm
            return _held(f"s3-confirm-error:{type(e).__name__}",
                         canary=canary, successor=successor)

    return confirm_fn
