"""RED-first tests for the WAL enrichers (spec §2.3).

enricher_git: cheap `git status --porcelain` + HEAD sampling vs last sample ->
  file_mod / git events (refs + diffstat, NOT blobs). Tested against a REAL
  temp git repo (real behavior, no mocks).
enricher_proc: consumes the EXISTING state/agent-events/panes/*.json Tier-0 hook
  snapshots (one object per pane: pane/session_id/cwd/event/tool/ts/state) ->
  proc events for the matching seat sid, deduped vs last sample.
"""
import json
import subprocess

from lineage_daemon.wal.store import WalStore
from lineage_daemon.wal.enrichers import GitEnricher, ProcEnricher


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _store(tmp_path):
    return WalStore(str(tmp_path / "l.db"))


# ---- GitEnricher ----

def test_git_enricher_emits_file_mod_for_new_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "a.txt").write_text("hello")
    store = _store(tmp_path)
    en = GitEnricher(store, "ios-watch-dev", 6, "sid", str(repo))
    n = en.sample()
    mods = [r for r in store.events() if r["kind"] == "file_mod"]
    assert n >= 1
    assert any("a.txt" in (r["summary"] or "") for r in mods)


def test_git_enricher_emits_git_event_on_head_change(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "a.txt").write_text("hello")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "first")
    store = _store(tmp_path)
    en = GitEnricher(store, "ios-watch-dev", 6, "sid", str(repo))
    en.sample()
    git_evs = [r for r in store.events() if r["kind"] == "git"]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()
    assert len(git_evs) >= 1
    assert head[:12] in git_evs[-1]["summary"]


def test_git_enricher_quiet_when_unchanged(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "a.txt").write_text("hello")
    store = _store(tmp_path)
    en = GitEnricher(store, "ios-watch-dev", 6, "sid", str(repo))
    en.sample()
    n2 = en.sample()  # nothing changed since last sample
    assert n2 == 0


# ---- ProcEnricher ----

def _pane(panes_dir, num, session_id, event="PreToolUse", tool="Bash",
          state="working", ts=1000.0):
    (panes_dir / f"{num}.json").write_text(json.dumps({
        "pane": f"%{num}", "session_id": session_id, "cwd": "/x",
        "event": event, "tool": tool, "ts": ts, "state": state}))


def test_proc_enricher_emits_for_matching_sid(tmp_path):
    panes = tmp_path / "panes"
    panes.mkdir()
    _pane(panes, 61, "sid-A", event="PreToolUse", tool="Bash")
    store = _store(tmp_path)
    en = ProcEnricher(store, "ios-watch-dev", 6, "sid-A", str(panes))
    n = en.sample()
    procs = [r for r in store.events() if r["kind"] == "proc"]
    assert n == 1
    assert "PreToolUse" in procs[0]["summary"] and "Bash" in procs[0]["summary"]


def test_proc_enricher_ignores_other_sids(tmp_path):
    panes = tmp_path / "panes"
    panes.mkdir()
    _pane(panes, 61, "someone-else")
    store = _store(tmp_path)
    en = ProcEnricher(store, "ios-watch-dev", 6, "sid-A", str(panes))
    assert en.sample() == 0


def test_proc_enricher_dedupes_unchanged_then_emits_on_change(tmp_path):
    panes = tmp_path / "panes"
    panes.mkdir()
    _pane(panes, 61, "sid-A", event="PreToolUse", tool="Bash", ts=1000.0)
    store = _store(tmp_path)
    en = ProcEnricher(store, "ios-watch-dev", 6, "sid-A", str(panes))
    assert en.sample() == 1
    assert en.sample() == 0  # unchanged snapshot
    _pane(panes, 61, "sid-A", event="Stop", tool="", state="idle", ts=1001.0)
    assert en.sample() == 1  # state transition captured


# ---- GitEnricher per-cwd dedup (F2 CPU: git children were ~68% of a core) ----
# The telemetryd mux registers ~20-31 seats that SHARE one cwd (agent-orchestra);
# each GitEnricher ran `git status`/`rev-parse` independently every slow tick, so
# a 170ms git status executed ~20x per tick. A per-(cwd,args) cache shared across
# the enrichers of ONE mux tick collapses those to a single exec — identical git
# result, so each lineage still emits its own file_mod/git events.

def test_git_enricher_dedups_git_exec_per_cwd_within_a_tick(tmp_path, monkeypatch):
    import subprocess
    from lineage_daemon.wal import enrichers as E

    def _git(cwd, *args):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       cwd=cwd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "committed.txt").write_text("x")
    _git(repo, "add", "committed.txt")
    _git(repo, "commit", "-m", "c1")
    (repo / "dirty.txt").write_text("y")           # a working-tree change to emit

    calls = []
    real = E._run
    monkeypatch.setattr(E, "_run", lambda cwd, *a: (calls.append((cwd, a)), real(cwd, *a))[1])

    cache = {}                                       # shared across the tick's enrichers
    s1 = WalStore(str(tmp_path / "l1.db"))
    s2 = WalStore(str(tmp_path / "l2.db"))
    e1 = E.GitEnricher(s1, "root1", 1, "sid1", str(repo), run_cache=cache)
    e2 = E.GitEnricher(s2, "root2", 1, "sid2", str(repo), run_cache=cache)
    e1.sample()
    e2.sample()

    # both lineages still captured their own file_mod events (no lost capture)
    assert any(r["kind"] == "file_mod" for r in s1.events())
    assert any(r["kind"] == "file_mod" for r in s2.events())
    # but git executed ONCE per distinct (cwd,args) despite two enrichers/cwd:
    # pre-fix the second enricher re-ran every git call (duplicates in `calls`).
    assert len(calls) == len(set(calls)), f"git re-executed for same (cwd,args): {calls}"


# ---- GitEnricher cadence gate (F2 lever 2: 5s->60s durable git-audit) ----
# Verified by effect (lock holders): the telemetryd observer git-enriches 29
# seats run_capture does NOT cover, so git can't be removed from the observer
# without losing durable coverage. Instead widen ITS git cadence to 60s (the
# real-time status lane uses no git; only the durable file_mod/git AUDIT
# freshness drops 5s->60s). A min_interval_s gates sample() by wall clock.

def test_git_enricher_min_interval_gates_sampling(tmp_path):
    import subprocess
    from lineage_daemon.wal.enrichers import GitEnricher

    def _git(cwd, *args):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       cwd=cwd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "a.txt").write_text("x")

    clock = {"t": 1000.0}
    store = WalStore(str(tmp_path / "l.db"))
    en = GitEnricher(store, "root", 1, "sid", str(repo),
                     min_interval_s=60.0, clock=lambda: clock["t"])

    n1 = en.sample()                 # first call always runs
    assert n1 >= 1
    events_after_first = len(store.events())

    clock["t"] = 1000.0 + 30.0       # 30s later: within the 60s window -> skip
    assert en.sample() == 0
    assert len(store.events()) == events_after_first   # nothing new emitted

    (repo / "b.txt").write_text("y")  # a change appears...
    clock["t"] = 1000.0 + 61.0        # ...but only sampled after 60s elapse
    n3 = en.sample()
    assert n3 >= 1
    assert len(store.events()) > events_after_first


def test_git_enricher_default_interval_is_every_call_backward_compat(tmp_path):
    # run_capture + existing callers construct GitEnricher without min_interval_s
    # and MUST keep sampling every call (default 0 = no gating).
    import subprocess
    from lineage_daemon.wal.enrichers import GitEnricher
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "init"],
                   cwd=tmp_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (tmp_path / "a.txt").write_text("x")
    store = WalStore(str(tmp_path / "l.db"))
    en = GitEnricher(store, "root", 1, "sid", str(tmp_path))
    assert en.sample() >= 1
    (tmp_path / "b.txt").write_text("y")
    assert en.sample() >= 1          # no gating -> the new change is captured at once


def test_git_enricher_skips_and_logs_once_when_cwd_is_none(tmp_path, capsys):
    """gm-g75 amendment (msg_00b76413) item 2. With cwd=None the enricher used to INSERT
    source_path=None -> NOT NULL -> sqlite3.IntegrityError raised out of tick(), killing the
    pass before anything else ran (2,001 such lines in telemetryd-manual.log). Measured on
    chatmode-latency-investigator. Now: SKIP, log ONCE per seat, return 0, never raise."""
    store = WalStore(str(tmp_path / "w.db"))
    en = GitEnricher(store, "ios-watch-dev", 6, "sid", None)
    assert en.sample() == 0
    assert en.sample() == 0
    assert store.events() == []                       # nothing inserted, nothing raised
    err = capsys.readouterr().err
    assert err.count("cwd is None") == 1              # logged exactly once, not per tick


# ---- requirement (b): deltas since LAST SAMPLE survive a fresh construction ----
# Measured 2026-09-20 (devex-review, dc692f9196 re-review): each per-beat ensure-capture
# pass builds a NEW GitEnricher whose last-sample map is empty, so every pass re-emitted
# the ENTIRE dirty set (11,627 file_mod rows per tick on the shared tree). The last sample
# must persist per (root, sid) so a fresh instance emits only what changed since.

def test_git_enricher_fresh_instance_with_sample_path_emits_only_deltas(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "a.txt").write_text("hello")
    (repo / "b.txt").write_text("hello")
    store = _store(tmp_path)
    sample = str(tmp_path / "root.gitsample.json")
    en1 = GitEnricher(store, "root", 1, "sidA", str(repo), sample_path=sample)
    n1 = en1.sample()
    assert n1 >= 2                                   # first sample: the dirty set, once
    # a NEW instance (a new beat pass) over the same seat + sid: nothing changed -> 0
    en2 = GitEnricher(store, "root", 1, "sidA", str(repo), sample_path=sample)
    assert en2.sample() == 0
    # one path changes its status code -> exactly that delta
    (repo / "c.txt").write_text("new")
    en3 = GitEnricher(store, "root", 1, "sidA", str(repo), sample_path=sample)
    n3 = en3.sample()
    mods = [r["summary"] for r in store.events() if r["kind"] == "file_mod"]
    assert n3 == 1 and any("c.txt" in m for m in mods[-1:])


def test_git_enricher_sample_path_is_keyed_by_sid_so_a_rotated_seat_re_baselines(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "a.txt").write_text("hello")
    store = _store(tmp_path)
    sample = str(tmp_path / "root.gitsample.json")
    GitEnricher(store, "root", 1, "sidA", str(repo), sample_path=sample).sample()
    # same root, NEW sid (a rotation): the live sid's WAL needs its own baseline, once
    n = GitEnricher(store, "root", 2, "sidB", str(repo), sample_path=sample).sample()
    assert n >= 1
    # and a second pass on sidB is quiet
    assert GitEnricher(store, "root", 2, "sidB", str(repo), sample_path=sample).sample() == 0


def test_git_enricher_without_sample_path_is_unchanged_in_memory_behaviour(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "a.txt").write_text("hello")
    store = _store(tmp_path)
    GitEnricher(store, "root", 1, "sidA", str(repo)).sample()
    # no sample_path: a fresh instance still re-emits (the documented daemon-restart case)
    assert GitEnricher(store, "root", 1, "sidA", str(repo)).sample() >= 1


def test_git_enricher_unreadable_sample_file_is_fail_soft(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "a.txt").write_text("hello")
    store = _store(tmp_path)
    sample = tmp_path / "root.gitsample.json"
    sample.write_text("{not json")
    en = GitEnricher(store, "root", 1, "sidA", str(repo), sample_path=str(sample))
    assert en.sample() >= 1                          # corrupt sidecar = no baseline, never a raise
    assert json.loads(sample.read_text())["sid"] == "sidA"   # and it is rewritten cleanly


# ---- devex finding msg_e5d185f4 / gm requirement msg_b7f7d36d: enricher rows carried ts=0.0 against
# ts REAL NOT NULL (70,232 in oss-arturo-dev, ~2.48M fleet-wide), so no tick could be dated from the
# store. New rows stamp the enricher's clock at emission; historical rows are never rewritten. ----

def test_git_enricher_rows_carry_the_emission_clock(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "a.txt").write_text("hello")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "first")
    store = _store(tmp_path)
    en = GitEnricher(store, "root", 1, "sidA", str(repo), clock=lambda: 1_700_000_123.5)
    en.sample()
    rows = [r for r in store.events() if r["kind"] in ("file_mod", "git")]
    assert rows and all(r["ts"] == 1_700_000_123.5 for r in rows)


def test_git_enricher_default_clock_is_wall_time_not_zero(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "a.txt").write_text("hello")
    store = _store(tmp_path)
    GitEnricher(store, "root", 1, "sidA", str(repo)).sample()
    rows = [r for r in store.events() if r["kind"] == "file_mod"]
    assert rows and all(r["ts"] > 1_600_000_000 for r in rows)


def test_proc_enricher_rows_without_a_pane_ts_carry_the_clock(tmp_path):
    panes = tmp_path / "panes"
    panes.mkdir()
    (panes / "1.json").write_text(json.dumps({"pane": "%1", "session_id": "sidA", "cwd": "/x",
                                               "event": "PreToolUse", "tool": "Bash", "state": "working"}))
    store = _store(tmp_path)
    ProcEnricher(store, "root", 1, "sidA", str(panes), clock=lambda: 1_700_000_999.0).sample()
    rows = [r for r in store.events() if r["kind"] == "proc"]
    assert rows and all(r["ts"] == 1_700_000_999.0 for r in rows)


# ---- CROSS-TICK git cache (the operator, 2026-10-08: "reduce the runaway git", gm msg_51e755ee) ----
# Measured: ~30 seats share one cwd; each has its own 60 s gate, staggered, and the per-tick cache
# was cleared every tick -> `git status` (1.35 s on the 75k-file tree) ran whenever ANY seat's
# window expired: 11 execs in 90 s. A TTL cache keyed by (cwd, args) and shared across ticks makes
# it at most once per TTL per cwd fleet-wide; every seat still emits its own events.

def _staggered_fleet(tmp_path, monkeypatch, cache, n_seats=6):
    from lineage_daemon.wal import enrichers as E
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "a.txt").write_text("x")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "c1")
    (repo / "dirty.txt").write_text("y")
    calls = []
    real = E._run
    monkeypatch.setattr(E, "_run", lambda cwd, *a: (calls.append(a), real(cwd, *a))[1])
    now = [0.0]
    clock = lambda: now[0]
    seats = []
    for i in range(n_seats):
        st = WalStore(str(tmp_path / f"l{i}.db"))
        seats.append((st, E.GitEnricher(st, f"root{i}", 1, f"sid{i}", str(repo), run_cache=cache,
                                        min_interval_s=60.0, clock=clock)))
    return E, seats, calls, now


def _drive(seats, cache, now, ticks, step, start_offsets):
    """One tick every `step` s; seat i first samples at start_offsets[i] (the real stagger)."""
    for t in range(ticks):
        now[0] = t * step
        if hasattr(cache, "expire"):
            cache.expire()
        else:
            cache.clear()                    # the pre-fix per-tick behaviour
        for i, (_, e) in enumerate(seats):
            if now[0] >= start_offsets[i]:
                e.sample()


def test_ttl_cache_runs_git_status_at_most_once_per_ttl_per_cwd_across_ticks(tmp_path, monkeypatch):
    from lineage_daemon.wal.enrichers import TTLRunCache
    cache = TTLRunCache(ttl_s=60.0)
    E, seats, calls, now = _staggered_fleet(tmp_path, monkeypatch, cache)
    cache._clock = lambda: now[0]            # the cache and the enrichers share one clock
    _drive(seats, cache, now, ticks=16, step=8.0, start_offsets=[0, 8, 16, 24, 32, 40])  # 0..120 s
    statuses = [a for a in calls if a and a[0] == "status"]
    assert len(statuses) <= 3, f"{len(statuses)} git status in 120 s across 6 seats: {calls}"
    for st, _ in seats:                      # every seat still captured its own file_mod
        assert any(r["kind"] == "file_mod" for r in st.events())


def test_per_tick_clear_is_the_runaway_this_replaces(tmp_path, monkeypatch):
    """The control: the pre-fix per-tick dict runs status once per seat window, so the bound
    above genuinely discriminates."""
    E, seats, calls, now = _staggered_fleet(tmp_path, monkeypatch, {})
    _drive(seats, seats[0][1]._run_cache, now, ticks=16, step=8.0,
           start_offsets=[0, 8, 16, 24, 32, 40])
    statuses = [a for a in calls if a and a[0] == "status"]
    assert len(statuses) > 3


def test_ttl_cache_does_not_cache_a_failed_git_run(tmp_path, monkeypatch):
    from lineage_daemon.wal import enrichers as E
    n = []
    monkeypatch.setattr(E, "_run", lambda cwd, *a: n.append(a) or None)
    cache = E.TTLRunCache(ttl_s=60.0, clock=lambda: 0.0)
    e = E.GitEnricher(_store(tmp_path), "r", 1, "s", str(tmp_path), run_cache=cache)
    e._run_cached("status", "--porcelain", "-z")
    e._run_cached("status", "--porcelain", "-z")
    assert len(n) == 2                       # a failure retries; it is never served for 60 s


def test_ttl_cache_entry_expires_after_ttl():
    from lineage_daemon.wal.enrichers import TTLRunCache
    t = [0.0]
    c = TTLRunCache(ttl_s=60.0, clock=lambda: t[0])
    c[("cwd", ("status",))] = "out"
    t[0] = 59.0
    assert ("cwd", ("status",)) in c and c[("cwd", ("status",))] == "out"
    t[0] = 60.5
    assert ("cwd", ("status",)) not in c
    c[("cwd", ("x",))] = "y"
    t[0] = 200.0
    c.expire()
    assert len(c) == 0


def test_multiplexer_shares_one_ttl_cache_across_ticks(tmp_path):
    """The DEPLOYED path: the tailer hands every seat ONE cross-tick cache, and a tick EXPIRES it
    (an entry younger than the TTL survives) rather than clearing it."""
    from lineage_daemon.wal.multiplexer import MultiplexedTailer
    from lineage_daemon.wal.enrichers import TTLRunCache
    mux = MultiplexedTailer(str(tmp_path / "wal"), str(tmp_path / "locks"))
    assert isinstance(mux._enricher_run_cache, TTLRunCache)
    mux._enricher_run_cache[("cwd", ("status",))] = "fresh"
    mux.tick()
    assert ("cwd", ("status",)) in mux._enricher_run_cache
