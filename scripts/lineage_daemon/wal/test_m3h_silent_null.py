"""RED (BG leg-(ii) arm-precondition M3h — surface the silent-null edge M3 does NOT cover).

M3 attributes the green's sid at promote ONLY when it is KNOWN (build_obs sources it from
state/wal/{alias}.sid). The uncovered edge: a green that BOOTED (reached READY) but whose
SessionStart sid-hook silently failed to write .sid → build_obs finds no sid → the promote
attributes NOTHING → canonical.session_id ends NULL SILENTLY (the rotation landmine, via a
path M3's "sid known ⇒ lands or raises" does not reach). M3h makes it LOUD: a seat that is
READY but whose green .sid is absent is detected + stamped as a durable alarm, so it is
caught BEFORE any autonomous arm. Non-gating (arm-precondition surface, not a state-machine
block).

Drives the REAL BgStateStore (state transition) + REAL read_green_sid (real .sid file).
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_beat  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402

ROOT = "second-brain-dev"
GREEN_ALIAS = f"{ROOT}-g5"


def _ready(tmp_path):
    st = BgStateStore(str(tmp_path), ROOT)
    st.write_state("PREWARMING", reason="ctx:prewarm")
    st.write_state("READY", reason="shadow-verified")
    return st


def _write_sid(tmp_path, alias, sid="real-green-sid-5"):
    with open(str(tmp_path / f"{alias}.sid"), "w") as fh:
        fh.write(sid + "\n")


def test_ready_but_sid_absent_is_detected_and_alarmed(tmp_path):
    _ready(tmp_path)                                   # READY, but NO .sid written
    hit = bg_beat.detect_green_sid_silent_null(str(tmp_path), ROOT, GREEN_ALIAS)
    assert hit is True, "READY + absent green .sid is the silent-null landmine -> LOUD"
    # durable alarm stamped so it survives beats / is inspectable before arm
    assert BgStateStore(str(tmp_path), ROOT).read_meta("green_sid_missing_alarm") == GREEN_ALIAS


def test_ready_with_sid_present_is_clean(tmp_path):
    _ready(tmp_path)
    _write_sid(tmp_path, GREEN_ALIAS)                  # green DID write its sid
    hit = bg_beat.detect_green_sid_silent_null(str(tmp_path), ROOT, GREEN_ALIAS)
    assert hit is False
    assert BgStateStore(str(tmp_path), ROOT).read_meta("green_sid_missing_alarm") is None


def test_not_ready_is_never_flagged(tmp_path):
    # PREWARMING (green still warming) + no .sid is NORMAL (sid arrives at SessionStart,
    # but the seat is not promotable yet) -> must NOT alarm.
    st = BgStateStore(str(tmp_path), ROOT)
    st.write_state("PREWARMING", reason="ctx:prewarm")
    hit = bg_beat.detect_green_sid_silent_null(str(tmp_path), ROOT, GREEN_ALIAS)
    assert hit is False
    assert BgStateStore(str(tmp_path), ROOT).read_meta("green_sid_missing_alarm") is None
