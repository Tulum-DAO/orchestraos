"""(b) VERIFY/CAPTURE ON THE GREEN SID — the verify stall-bound must measure the GREEN's
own progress (keyed on its sid), NEVER the blue root's WAL. The live re-fire #3 false-
stalled because progress_fn read state/wal/<root>.db (blue, flat) while the green was
ingesting on its own cid. These lock the contract:
  * ctx_adapters.green_progress is a provider-agnostic (registry) reader of the GREEN
    conversation's step high-water, keyed on the green's sid;
  * verify_stall.verify_with_stall_bound resets on GREEN progress and accrues only when the
    GREEN is flat — so a green advancing on its own cid is NEVER counted as stalled.
"""
import sqlite3
import sys

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal import ctx_adapters  # noqa: E402
from lineage_daemon.wal.verify_stall import (  # noqa: E402
    verify_with_stall_bound, VerifyStalled)


def test_green_progress_registry_dispatch_failsoft():
    """Unknown runtime and an unresolved (None) sid both fail-closed to None (=> the stall
    bound accrues, catching a green that never registered its sid)."""
    assert ctx_adapters.green_progress("no-such-runtime", "abc") is None
    assert ctx_adapters.green_progress("gemini", None) is None


def test_green_progress_gemini_counts_conversation_steps(tmp_path, monkeypatch):
    """The gemini reader returns the green conversation db's step high-water (monotonic)."""
    cid = "206f75be-cbcd-44c6-8c86-8f87ef3c7eff"
    # green_progress_gemini expands "~/.gemini/antigravity-cli/conversations" then joins
    # "<cid>.db"; the monkeypatch points that conversations dir at tmp_path.
    db = tmp_path / f"{cid}.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE steps (id INTEGER PRIMARY KEY)")
    c.executemany("INSERT INTO steps (id) VALUES (?)", [(i,) for i in range(46)])
    c.commit()
    c.close()
    monkeypatch.setattr("os.path.expanduser",
                        lambda p: str(tmp_path) if "antigravity-cli" in p else p)
    assert ctx_adapters.green_progress("gemini", cid) == 46


def test_stall_bound_resets_on_green_progress(tmp_path):
    """Green advancing on its OWN cid => progress_fn (green-keyed) climbs => the stall
    counter resets every beat => VerifyStalled NEVER fires (no false stall)."""
    prog = {"v": 0}

    def green_progress_fn(root, green_alias):
        prog["v"] += 1     # the green ingests -> its step high-water climbs
        return prog["v"]

    wrapped = verify_with_stall_bound(
        verify_fn=lambda r, g: False,       # not yet READY
        progress_fn=green_progress_fn, wal_dir=str(tmp_path))
    for _ in range(12):    # well past max_stall_beats=6
        assert wrapped("demo-root", "demo-root-g2") is False   # never raises


def test_stall_bound_accrues_only_on_flat_green(tmp_path):
    """A genuinely wedged green (flat green-keyed progress) DOES trip VerifyStalled after
    max_stall_beats — the bound still catches a real stall, just measured on the green."""
    wrapped = verify_with_stall_bound(
        verify_fn=lambda r, g: False,
        progress_fn=lambda r, g: 5,          # flat: green not progressing
        wal_dir=str(tmp_path), max_stall_beats=6)
    with pytest.raises(VerifyStalled):
        for _ in range(7):
            wrapped("demo-root", "demo-root-g2")


# ---- (step 5) liveness-before-stall: a LIVE green merely holding after ingest is
#      READY-eligible, not wedged — flat progress on a live green must NOT trip the stall.

def test_flat_but_live_green_does_not_stall(tmp_path):
    """A green that is LIVE (liveness adapter passes) but idle-holding after ingest has
    flat progress — it must NOT trip VerifyStalled (it is holding, not wedged). The rule:
    a beat is 'wedged' only when flat AND not provably live."""
    wrapped = verify_with_stall_bound(
        verify_fn=lambda r, g: False,        # not yet READY (grade still settling)
        progress_fn=lambda r, g: 3,          # FLAT (green did its 3 steps then holds)
        wal_dir=str(tmp_path), max_stall_beats=6,
        live_fn=lambda r, g: True)           # but the green is provably LIVE
    for _ in range(12):                      # well past max_stall_beats
        assert wrapped("demo-root", "demo-root-g2") is False   # never raises


def test_flat_and_not_live_green_still_stalls(tmp_path):
    """Flat AND not-live (dead / frozen-at-trust / never-booted) still trips — the gate
    the stall bound exists for is preserved."""
    wrapped = verify_with_stall_bound(
        verify_fn=lambda r, g: False,
        progress_fn=lambda r, g: 3,          # flat
        wal_dir=str(tmp_path), max_stall_beats=6,
        live_fn=lambda r, g: False)          # NOT live
    with pytest.raises(VerifyStalled):
        for _ in range(7):
            wrapped("demo-root", "demo-root-g2")
