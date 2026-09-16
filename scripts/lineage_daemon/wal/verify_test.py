"""RED-first tests for verify.py — the SOAK PROOF INSTRUMENT (spec §7.1 gate).

verify_capture re-checks a captured WAL against its source transcript BY EFFECT:
  - seq is contiguous 1..N (no holes);
  - each transcript-derived event's integrity hash re-matches a fresh sha256 of
    the source line at its offset (tamper/gap detection);
  - the resume cursor sits on a newline boundary within the file;
  - no 'unparseable' markers slipped in.

The load-bearing test is TAMPER: mutate the source after capture and prove the
verifier FAILS integrity — a verifier that always passes proves nothing.
"""
import json

from lineage_daemon.wal.store import WalStore
from lineage_daemon.wal.adapter_claude import ClaudeWalAdapter
from lineage_daemon.wal.verify import verify_capture


def _line(text):
    # fixed-length payload so a tamper can flip one char without shifting offsets
    return json.dumps({"type": "user", "sessionId": "sid-A",
                       "timestamp": "2026-09-02T03:00:00.000Z",
                       "message": {"role": "user",
                                   "content": [{"type": "text", "text": text}]}}) + "\n"


def _capture(tmp_path, *texts):
    src = str(tmp_path / "s.jsonl")
    with open(src, "w") as fh:
        fh.write("".join(_line(t) for t in texts))
    store = WalStore(str(tmp_path / "l.db"))
    ad = ClaudeWalAdapter(store, "ios-watch-dev", 6)
    ad.tail(src)
    return store, src


def test_verify_clean_capture_passes(tmp_path):
    store, src = _capture(tmp_path, "AAA", "BBB", "CCC")
    rep = verify_capture(store, src, lineage_root="ios-watch-dev")
    assert rep["contiguous"] is True
    assert rep["integrity_ok"] == rep["integrity_total"] > 0
    assert rep["unparseable"] == 0
    assert rep["cursor_ok"] is True
    assert rep["ok"] is True


def test_verify_detects_source_tamper(tmp_path):
    store, src = _capture(tmp_path, "AAA", "BBB", "CCC")
    # flip one char in the middle line, SAME length -> offsets unchanged, hash differs
    data = open(src).read().replace("BBB", "BXB")
    assert len(data) == len(open(src).read())  # length preserved
    with open(src, "w") as fh:
        fh.write(data)
    rep = verify_capture(store, src, lineage_root="ios-watch-dev")
    assert rep["integrity_ok"] < rep["integrity_total"]
    assert rep["ok"] is False


def test_verify_cursor_on_newline_boundary(tmp_path):
    store, src = _capture(tmp_path, "AAA", "BBB")
    rep = verify_capture(store, src, lineage_root="ios-watch-dev")
    # cursor last_off must equal file size (both lines whole) and sit after a \n
    import os
    assert rep["cursor_last_off"] == os.path.getsize(src)
    assert rep["cursor_ok"] is True
