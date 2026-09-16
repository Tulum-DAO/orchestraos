"""RED-first: resolve_claude_cid — the claude cid resolver (broad-arm blocker fix, gm ITEM A).
Adapter layer; 3 by-effect sources in order: process --resume (authoritative) -> transcript store
boot-burst 'You are <seat>' (gen-suffix tolerant) -> verify fresh (mtime >= proc start). Ambiguous
or none => None fail-closed. DI seams keep it hermetic."""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import ctx_adapters as ca  # noqa: E402

SID1 = "11111111-1111-4111-8111-111111111111"
SID2 = "22222222-2222-4222-8222-222222222222"


def test_source1_resume_cmdline_is_authoritative():
    """A live `claude ... --resume <sid>` cmdline carries the sid directly — return it, no
    transcript scan needed."""
    got = ca.resolve_claude_cid(
        "pm-x",
        proc_fn=lambda s: {"cmdline": f"claude --resume {SID1} --model m", "cwd": "/c",
                           "start_epoch": 100.0},
        candidates_fn=lambda s, cwd: [(SID2, 200.0)],   # must be IGNORED
        verify_fn=lambda sid, mt, st: True)
    assert got == SID1


def test_source2_transcript_match_verified_fresh():
    """No --resume (fresh spawn) -> the seat's own boot-burst transcript, verified fresh."""
    got = ca.resolve_claude_cid(
        "pm-x",
        proc_fn=lambda s: {"cmdline": "claude --settings /tmp/agent-perms-pm-x-g2.settings.json",
                           "cwd": "/c", "start_epoch": 100.0},
        candidates_fn=lambda s, cwd: [(SID1, 150.0)],
        verify_fn=lambda sid, mt, st: mt >= st)     # 150 >= 100 -> fresh
    assert got == SID1


def test_stale_candidate_rejected_by_verify():
    """A boot-burst-declaring transcript whose mtime predates the process start is a STALE
    prior-gen transcript (wrong-transcript landmine) -> not returned."""
    got = ca.resolve_claude_cid(
        "pm-x",
        proc_fn=lambda s: {"cmdline": "claude --settings x", "cwd": "/c", "start_epoch": 100.0},
        candidates_fn=lambda s, cwd: [(SID1, 50.0)],   # 50 < 100 start -> stale
        verify_fn=lambda sid, mt, st: mt >= st)
    assert got is None


def test_ambiguous_near_tie_is_failclosed():
    """Two boot-burst matches appended within the tie window are a genuine ambiguity (two live
    sessions can't be told apart) -> fail-closed, never guess."""
    got = ca.resolve_claude_cid(
        "pm-x",
        proc_fn=lambda s: {"cmdline": "claude x", "cwd": "/c", "start_epoch": 100.0},
        candidates_fn=lambda s, cwd: [(SID1, 150.0), (SID2, 160.0)],   # delta 10s < 120s
        verify_fn=lambda sid, mt, st: True)
    assert got is None


def test_superseded_early_transcript_loses_to_newest_live():
    """A LONG-running process (ancient start) has an abandoned EARLY transcript that also
    post-dates start; the actively-appended NEWEST boot-burst match is the live session and wins
    (this is the pm-molevera-by-effect case: ~11 days apart)."""
    got = ca.resolve_claude_cid(
        "pm-x",
        proc_fn=lambda s: {"cmdline": "claude --settings x", "cwd": "/c", "start_epoch": 100.0},
        candidates_fn=lambda s, cwd: [(SID2, 200.0), (SID1, 1000000.0)],  # SID1 newest by >>window
        verify_fn=lambda sid, mt, st: mt >= st)
    assert got == SID1


def test_no_process_returns_none():
    assert ca.resolve_claude_cid("pm-x", proc_fn=lambda s: None,
                                 candidates_fn=lambda s, cwd: [(SID1, 150.0)],
                                 verify_fn=lambda *a: True) is None


def test_no_candidate_returns_none():
    got = ca.resolve_claude_cid(
        "pm-x",
        proc_fn=lambda s: {"cmdline": "claude --settings x", "cwd": "/c", "start_epoch": 100.0},
        candidates_fn=lambda s, cwd: [],
        verify_fn=lambda *a: True)
    assert got is None


def test_registered_in_cid_resolver_registry():
    assert ca.CID_RESOLVER_REGISTRY.get("claude") is ca.resolve_claude_cid


def test_transcript_scanner_gen_suffix_tolerant_and_boot_bounded(tmp_path):
    """The default candidate scanner: matches 'You are <seat>' AND '<seat>-gN' in the BOOT burst
    (first few user turns), but NOT a later conversational echo (the false-match gm warned of)."""
    import json
    proj = tmp_path / "proj"
    proj.mkdir()
    # transcript A: boot-burst declares the gen-suffixed identity at user-msg #1 -> MATCH
    a = proj / (SID1 + ".jsonl")
    a.write_text("\n".join(json.dumps(x) for x in [
        {"type": "user", "message": {"role": "user",
         "content": "You are pm-x-g2. Read /tmp/agent-init-pm-x-g2.md and follow it."}},
        {"type": "assistant", "message": {"role": "assistant", "content": "ok"}},
    ]))
    # transcript B: a DIFFERENT seat that only mentions pm-x deep (echo) past the boot bound
    b = proj / (SID2 + ".jsonl")
    lines = [{"type": "user", "message": {"role": "user", "content": "You are other-seat."}}]
    lines += [{"type": "user", "message": {"role": "user", "content": f"turn {i}"}}
              for i in range(6)]
    lines += [{"type": "user", "message": {"role": "user", "content": "let's fix pm-x now"}}]
    b.write_text("\n".join(json.dumps(x) for x in lines))
    cands = ca._claude_transcript_candidates(
        "pm-x", "/c", projects_root=str(tmp_path),
        encode_fn=lambda cwd: "proj")
    sids = {sid for sid, _mt in cands}
    assert SID1 in sids and SID2 not in sids


# ---- gm msg_1074aa87 (by effect on gm 01:00Z/01:15Z): a parked predecessor pane (gm-gen63 /
# gm-gen64, rename-not-kill archives) still gets transcript appends, every candidate verifies
# against the CANONICAL's process start, and newest-mtime wins -> the resolver returned a
# RETIRED generation's sid (578dba5a, then 8ce0b6ba) while the canonical is 99a73817. The
# reconciler's foreign-generation gate failed closed, but re-alarmed every tick with a different
# sid. Fix: sids the DB holds as RETIRED generations of the seat's root are never candidates.

SID3 = "33333333-3333-4333-8333-333333333333"
_PROC = {"cmdline": "claude --settings x", "cwd": "/c", "start_epoch": 100.0}


def test_retired_predecessor_transcript_never_wins_even_when_newer():
    got = ca.resolve_claude_cid(
        "gm", proc_fn=lambda s: _PROC,
        candidates_fn=lambda s, cwd: [(SID1, 900.0), (SID2, 500.0)],   # SID1 newest but retired
        verify_fn=lambda sid, mt, st: True,
        retired_sids_fn=lambda s: {SID1})
    assert got == SID2


def test_near_tie_among_retired_predecessors_is_not_ambiguous():
    got = ca.resolve_claude_cid(
        "gm", proc_fn=lambda s: _PROC,
        candidates_fn=lambda s, cwd: [(SID1, 905.0), (SID3, 900.0), (SID2, 200.0)],
        verify_fn=lambda sid, mt, st: True,
        retired_sids_fn=lambda s: {SID1, SID3})
    assert got == SID2


def test_only_retired_candidates_fail_closed():
    got = ca.resolve_claude_cid(
        "gm", proc_fn=lambda s: _PROC,
        candidates_fn=lambda s, cwd: [(SID1, 900.0)],
        verify_fn=lambda sid, mt, st: True,
        retired_sids_fn=lambda s: {SID1})
    assert got is None


def test_retired_lookup_error_is_fail_soft_to_previous_behaviour():
    def boom(s):
        raise RuntimeError("db locked")
    got = ca.resolve_claude_cid(
        "gm", proc_fn=lambda s: _PROC,
        candidates_fn=lambda s, cwd: [(SID1, 900.0), (SID2, 500.0)],
        verify_fn=lambda sid, mt, st: True,
        retired_sids_fn=boom)
    assert got == SID1


def test_default_retired_lookup_reads_db_and_resolves_alias_root(tmp_path, monkeypatch):
    """The live default reads state/orchestra-registry.db under ORCHESTRA_DIR: generations of
    the seat's ROOT with retired_at set. A -gN / -genN alias seat resolves to its root."""
    import sqlite3
    (tmp_path / "state").mkdir()
    con = sqlite3.connect(str(tmp_path / "state" / "orchestra-registry.db"))
    con.execute("CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, generation INT,"
                " session_id TEXT, retired_at TEXT)")
    con.executemany("INSERT INTO generations (root, generation, session_id, retired_at)"
                    " VALUES (?,?,?,?)",
                    [("gm", 63, SID1, "2026-09-15T22:29:18Z"),
                     ("gm", 64, SID3, "2026-09-16T00:52:37Z"),
                     ("gm", 65, SID2, None),
                     ("other", 3, "44444444-4444-4444-8444-444444444444", "x")])
    con.commit(); con.close()
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    assert ca._claude_retired_sids("gm") == {SID1, SID3}
    assert ca._claude_retired_sids("gm-g65") == {SID1, SID3}
    assert ca._claude_retired_sids("gm-gen64") == {SID1, SID3}
    assert ca._claude_retired_sids("nobody") == set()
