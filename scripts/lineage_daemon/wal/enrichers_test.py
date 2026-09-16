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
