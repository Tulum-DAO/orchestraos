"""RED-first tests for DP-4 (spec §3.3 + DP-4): the 30% digest cap AND the
explicit overflow/truncation-priority policy.

Cap: the FULL rendered digest stays within 30% of the runtime ceiling
(deterministic token estimate — no LLM).
Overflow priority (RETAIN order): mission refs > working-set > decision timeline
> in-flight tail; drop the lowest priority (tail) OLDEST-first. Records
truncated=true + the dropped-span seqs so Green can drill via body_refs.
"""
from lineage_daemon.wal.store import WalStore
from lineage_daemon.wal.digest import render_digest, DIGEST_CAP_FRACTION


def _store(tmp_path):
    return WalStore(str(tmp_path / "l.db"))


def _ev(store, kind, summary):
    return store.append(ts=0.0, lineage_root="ios-watch-dev", generation=6,
                        sid="sid", runtime="claude", kind=kind, summary=summary,
                        body_ref="p:0", source_path="p", source_off=0,
                        integrity="h")


def test_under_cap_not_truncated(tmp_path):
    store = _store(tmp_path)
    _ev(store, "tool_call", "Read")
    d = render_digest(store, "ios-watch-dev", ceiling_tokens=800_000)
    assert d["truncated"] is False
    assert d["dropped_spans"] == []


def test_over_cap_truncates_within_budget(tmp_path):
    store = _store(tmp_path)
    for i in range(200):
        _ev(store, "tool_call", f"Bash command number {i} with padding text")
    ceiling = 200  # cap = 60 tokens
    d = render_digest(store, "ios-watch-dev", ceiling_tokens=ceiling, tail_n=200)
    assert d["truncated"] is True
    assert len(d["dropped_spans"]) > 0
    # the FULL rendered digest respects the 30% cap (deterministic char/4 est)
    assert len(d["text"]) // 4 <= int(ceiling * DIGEST_CAP_FRACTION)


def test_priority_retains_working_over_tail(tmp_path):
    store = _store(tmp_path)
    ws = _ev(store, "file_mod", "M critical/App.swift")   # working-set (rank 1)
    for i in range(50):
        _ev(store, "tool_call", f"Bash padding padding padding {i}")  # tail
    d = render_digest(store, "ios-watch-dev", ceiling_tokens=300, tail_n=50)
    assert d["truncated"] is True
    # working-set retained; in-flight tail dropped first
    assert "critical/App.swift" in d["text"]
    assert ws not in d["dropped_spans"]


def test_tail_keeps_newest(tmp_path):
    store = _store(tmp_path)
    for name in ["ReadOldest", "GrepMid", "EditNewer", "WriteNewest"]:
        _ev(store, "tool_call", f"{name} padding padding padding padding")
    # tiny cap: only the newest tail entries survive
    d = render_digest(store, "ios-watch-dev", ceiling_tokens=180, tail_n=10)
    assert d["truncated"] is True
    assert "WriteNewest" in d["text"]
    assert "ReadOldest" not in d["text"]
