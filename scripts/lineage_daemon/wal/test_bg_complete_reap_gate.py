"""RED (BG leg-(ii) P0.3 — reap-gate: re-read canonical, reap Blue only if it advanced to GREEN).

Today complete_swap drives the reap off LOCAL bg_state alone (DEGRADED + resumable reason);
it NEVER re-reads canonical.generation_id. So if the identity swap did NOT actually commit
(canonical still points to Blue), reaping Blue leaves NO live canonical — catastrophic.
P0.3: before the reap, re-read canonical and require it == GREEN (blue.generation+1); else
DO NOT reap — write RETIRE_PENDING and surface.

DELIVERY-CRITICAL (reap). Drives the REAL complete_swap against a REAL orchestra-registry.db
(canonical read) + a REAL BgStateStore (the DEGRADED->DRAINED / DEGRADED->RETIRE_PENDING
transition) — leg-(i) lesson: verify the decisive seam by effect, never mock it.
"""
import os
import sys

sys.path.insert(0, "scripts")
from identity_store.orchestra_db import get_connection, init_db  # noqa: E402
from lineage_daemon.wal import bg_complete  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402

ROOT = "bg-reap-gate-victim"


def teardown_function(_):
    os.environ.pop("IDENTITY_STORE_CUTOVER", None)


def _seed_db(tmp_path, canonical_gen):
    """Real orchestra-registry.db: blue=gen2, green=gen3; point canonical at
    ``canonical_gen`` (2=still-blue, 3=green). Returns (orchestra_dir, blue_row_id)."""
    od = str(tmp_path)
    os.makedirs(os.path.join(od, "state"), exist_ok=True)
    db = os.path.join(od, "state", "orchestra-registry.db")
    init_db(db)
    conn = get_connection(db)
    conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
                 (ROOT, "T2", "claude"))
    blue = conn.execute("INSERT INTO generations (root, generation, model) "
                        "VALUES (?, 2, 'claude-opus-4-8[1m]')", (ROOT,)).lastrowid
    green = conn.execute("INSERT INTO generations (root, generation, model) "
                         "VALUES (?, 3, 'claude-opus-4-8[1m]')", (ROOT,)).lastrowid
    canonical_id = blue if canonical_gen == 2 else green
    conn.execute("INSERT INTO canonical (root, generation_id, tmux_session) "
                 "VALUES (?, ?, ?)", (ROOT, canonical_id, ROOT))
    conn.close()
    os.environ["IDENTITY_STORE_CUTOVER"] = "1"
    return od, blue


class _ReapSeams:
    def __init__(self):
        self.reaped = []

    def reap(self, root, blue):
        self.reaped.append((root, blue))


def _degraded_store(tmp_path):
    st = BgStateStore(str(tmp_path), ROOT)
    st.write_state("SWAPPING", reason="ctx:swap")
    st.write_state("DEGRADED", reason="effects-incomplete")
    return st


# ── canonical advanced to GREEN => reap fires, DEGRADED -> DRAINED ─────────────

def test_reap_fires_when_canonical_is_green(tmp_path):
    od, blue_id = _seed_db(tmp_path, canonical_gen=3)   # canonical == green(gen3)
    _degraded_store(tmp_path)
    seams = _ReapSeams()
    out = bg_complete.complete_swap(
        ROOT, wal_dir=str(tmp_path), seams=seams, blue_generation_id=blue_id,
        orchestra_dir=od, expected_green_generation=3)
    assert out["reaped"] is True
    assert seams.reaped == [(ROOT, blue_id)]
    assert BgStateStore(str(tmp_path), ROOT).read()["state"] == "DRAINED"


# ── canonical STILL Blue => reap GATED, DEGRADED -> RETIRE_PENDING, zero reap ──

def test_reap_gated_when_canonical_still_blue(tmp_path):
    od, blue_id = _seed_db(tmp_path, canonical_gen=2)   # canonical STILL blue(gen2)
    _degraded_store(tmp_path)
    seams = _ReapSeams()
    out = bg_complete.complete_swap(
        ROOT, wal_dir=str(tmp_path), seams=seams, blue_generation_id=blue_id,
        orchestra_dir=od, expected_green_generation=3)
    assert out["reaped"] is False, "must NOT reap Blue while canonical still points to Blue"
    assert seams.reaped == [], "ZERO reap when the swap identity did not land on green"
    assert BgStateStore(str(tmp_path), ROOT).read()["state"] == "RETIRE_PENDING"


def test_reap_gated_when_canonical_absent(tmp_path):
    od = str(tmp_path)
    os.makedirs(os.path.join(od, "state"), exist_ok=True)
    init_db(os.path.join(od, "state", "orchestra-registry.db"))  # no canonical row
    os.environ["IDENTITY_STORE_CUTOVER"] = "1"
    _degraded_store(tmp_path)
    seams = _ReapSeams()
    out = bg_complete.complete_swap(
        ROOT, wal_dir=str(tmp_path), seams=seams, blue_generation_id=99,
        orchestra_dir=od, expected_green_generation=3)
    assert out["reaped"] is False and seams.reaped == []
    assert BgStateStore(str(tmp_path), ROOT).read()["state"] == "RETIRE_PENDING"


# ── back-compat: no gate context => legacy behavior (reap fires) ───────────────

def test_no_gate_context_is_legacy_reap(tmp_path):
    _degraded_store(tmp_path)
    seams = _ReapSeams()
    out = bg_complete.complete_swap(
        ROOT, wal_dir=str(tmp_path), seams=seams, blue_generation_id=6)
    assert out["reaped"] is True, "no expected_green_generation => legacy path (gate off)"
    assert seams.reaped == [(ROOT, 6)]
    assert BgStateStore(str(tmp_path), ROOT).read()["state"] == "DRAINED"
