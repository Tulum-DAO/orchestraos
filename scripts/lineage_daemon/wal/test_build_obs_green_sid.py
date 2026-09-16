"""RED (BG leg-(ii) M3 — source the green's sid into obs at promote).

execute_swap already attributes green["session_id"] (F2, test_swap_sid_attribution),
and make_swap_fn's contract already says "green must carry {generation, session_id,
model}". The ONE gap: build_obs (bg_beat.py:69) builds green={generation,model} and
NEVER sets session_id — so canonical ends session_id=null after a bg swap (the rotation
landmine). M3 closes it at the source: build_obs reads the green's real sid from
state/wal/<alias>.sid (written by capture_green_sid at the green's SessionStart, P2.7),
keyed by the projected alias {root}-g{N}.

Drives the REAL build_obs against a REAL .sid file (leg-(i) lesson: real path effect).

RED until build_obs sources the sid.
"""
import os

from scripts.lineage_daemon.wal import bg_beat

ROOT = "second-brain-dev"


def _blue(gen=4):
    return {"generation": gen, "model": "claude-opus-4-8[1m]",
            "blue_generation_id": 41}


def _agent():
    return {"agent_id": ROOT, "runtime": "claude", "ctx": {"status_bar_pct": 78},
            "death": {}}


def test_build_obs_sources_green_sid_from_wal_file(tmp_path):
    wal = str(tmp_path / "wal")
    os.makedirs(wal)
    # the green (gen 5 = blue 4 + 1) wrote its sid at SessionStart to wal/<alias>.sid
    green_alias = f"{ROOT}-g5"
    with open(os.path.join(wal, f"{green_alias}.sid"), "w") as fh:
        fh.write("real-green-sid-5\n")

    obs = bg_beat.build_obs(_agent(), _blue(gen=4), wal_dir=wal)

    assert obs["green"]["generation"] == 5
    assert obs["green"].get("session_id") == "real-green-sid-5", \
        "M3: build_obs must source the green's real sid from state/wal/<alias>.sid"


def test_build_obs_no_sid_file_leaves_session_id_absent(tmp_path):
    wal = str(tmp_path / "wal")
    os.makedirs(wal)
    obs = bg_beat.build_obs(_agent(), _blue(gen=4), wal_dir=wal)
    # green not yet booted => no .sid => no session_id key (the swap attributes
    # nothing this beat; never fabricate a sid)
    assert "session_id" not in obs["green"]


def test_build_obs_without_wal_dir_is_legacy_identical(tmp_path):
    # back-compat: called with no wal_dir (older callers/tests) => no session_id,
    # byte-identical to legacy build_obs(agent, blue).
    obs = bg_beat.build_obs(_agent(), _blue(gen=4))
    assert "session_id" not in obs["green"]
    assert obs["green"] == {"generation": 5, "model": "claude-opus-4-8[1m]"}
