"""WAL enrichers — capture the side effects transcripts don't carry (spec §2.3).

GitEnricher: a cheap `git status --porcelain` + HEAD sample, diffed against the
  last sample, emits `file_mod` (path + status code) and `git` (HEAD sha +
  branch) events. Large blobs stay in git objects — the WAL stores refs only.
ProcEnricher: reads the EXISTING state/agent-events/panes/*.json Tier-0 hook
  snapshots (one object per pane) and emits `proc` events for the seat's sid,
  deduped so an unchanged pane costs nothing.

CPU discipline (the operator-standing): these are called on beat cadence (poll >= 5s),
one cheap subprocess (git) or a small directory scan per sample. Last-sample
state is held in memory by the long-lived daemon; a daemon restart re-emits the
current dirty set once (append-only, harmless at capture stage — documented).
"""
import hashlib
import json
import os
import subprocess
import time


def _run(cwd, *args):
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, timeout=10)
        return r.stdout if r.returncode == 0 else None
    except (subprocess.SubprocessError, OSError):
        return None


class GitEnricher:
    def __init__(self, store, lineage_root, generation, sid, cwd, runtime="claude",
                 run_cache=None, min_interval_s=0.0, clock=None):
        self._store = store
        self._root = lineage_root
        self._gen = generation
        self._sid = sid
        self._cwd = cwd
        self._runtime = runtime
        self._last_status = {}   # path -> status code
        self._last_head = None
        # F2 CPU lever 2: minimum wall-clock seconds between git samples. The
        # fleet-observer (telemetryd mux) sets this to 60s so its per-cwd git
        # status (the ~68%-of-a-core cost) runs at durable-audit cadence, not
        # every 5s slow tick — the real-time status lane uses no git, so only
        # file_mod/git AUDIT freshness relaxes 5s->60s. 0 (default) = every call
        # (run_capture and existing callers are unchanged).
        self._min_interval_s = min_interval_s
        self._clock = clock or time.time
        self._last_sample_ts = None
        # F2 CPU: a per-(cwd,args) cache SHARED across the enrichers of one mux
        # tick. Many seats share one cwd (the agent-orchestra repo whose git
        # status is ~170ms); without this, that exec ran once PER seat PER tick
        # (~68% of a core in git children). The result is identical for a cwd, so
        # each lineage still emits its own file_mod/git events. Cleared per tick
        # by the caller (MultiplexedTailer.tick); None => no dedup (standalone).
        self._run_cache = run_cache

    def _run_cached(self, *args):
        if self._run_cache is None:
            return _run(self._cwd, *args)
        key = (self._cwd, args)
        if key not in self._run_cache:
            self._run_cache[key] = _run(self._cwd, *args)
        return self._run_cache[key]

    def _porcelain(self):
        out = self._run_cached("status", "--porcelain", "-z")
        if out is None:
            return {}
        status = {}
        for entry in out.split("\0"):
            if not entry or len(entry) < 4:
                continue
            code, path = entry[:2], entry[3:]
            if path:
                status[path] = code
        return status

    def _head(self):
        h = self._run_cached("rev-parse", "HEAD")
        return h.strip() if h else None

    def _branch(self):
        b = self._run_cached("rev-parse", "--abbrev-ref", "HEAD")
        return b.strip() if b else "?"

    def sample(self):
        # Cadence gate (F2 lever 2): skip if sampled within min_interval_s. The
        # first call always runs; the timestamp advances only when we actually
        # sample, so a skipped tick costs nothing (no git exec at all).
        if self._min_interval_s:
            now = self._clock()
            if self._last_sample_ts is not None \
                    and (now - self._last_sample_ts) < self._min_interval_s:
                return 0
            self._last_sample_ts = now
        n = 0
        # HEAD change -> git event
        head = self._head()
        if head and head != self._last_head:
            self._append("git", f"HEAD {head[:12]} {self._branch()}",
                         body_ref=f"git:{self._cwd}@{head}", integrity=head)
            self._last_head = head
            n += 1
        # working-tree changes -> file_mod events (new or changed vs last sample)
        status = self._porcelain()
        for path, code in status.items():
            if self._last_status.get(path) == code:
                continue
            key = f"{code}\0{path}"
            self._append(
                "file_mod", f"{code} {path}",
                body_ref=f"git:{self._cwd}#{path}",
                integrity=hashlib.sha256(key.encode()).hexdigest())
            # Condition B (enricher): record this path per-item RIGHT AFTER its
            # append, not in a bulk assignment at the end. If a later append
            # raises and the lane contains it, the already-emitted paths are
            # deduped next sample (no double-capture) while the failed/pending
            # ones are re-tried (no gap).
            self._last_status[path] = code
            n += 1
        # Reconcile drop-outs (paths no longer dirty) on a clean full pass; on a
        # contained mid-loop raise this is skipped, leaving the per-item updates
        # above as the crash-safe record.
        self._last_status = status
        return n

    def _append(self, kind, summary, *, body_ref, integrity):
        self._store.append(
            ts=0.0, lineage_root=self._root, generation=self._gen,
            sid=self._sid, runtime=self._runtime, kind=kind, summary=summary,
            body_ref=body_ref, source_path=self._cwd, source_off=None,
            integrity=integrity)


class ProcEnricher:
    def __init__(self, store, lineage_root, generation, sid, panes_dir,
                 runtime="claude"):
        self._store = store
        self._root = lineage_root
        self._gen = generation
        self._sid = sid
        self._panes_dir = panes_dir
        self._runtime = runtime
        self._last = {}   # pane file -> signature

    def sample(self):
        n = 0
        try:
            names = sorted(os.listdir(self._panes_dir))
        except OSError:
            return 0
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(self._panes_dir, name)
            try:
                with open(path) as fh:
                    ev = json.load(fh)
            except (OSError, ValueError):
                continue
            if ev.get("session_id") != self._sid:
                continue
            sig = (ev.get("event"), ev.get("tool"), ev.get("state"),
                   ev.get("ts"))
            if self._last.get(path) == sig:
                continue
            self._last[path] = sig
            summary = (f"{ev.get('event', '?')} {ev.get('tool', '')} "
                       f"state={ev.get('state', '?')}").strip()
            self._store.append(
                ts=float(ev.get("ts") or 0.0), lineage_root=self._root,
                generation=self._gen, sid=self._sid, runtime=self._runtime,
                kind="proc", summary=summary,
                body_ref=f"pane:{ev.get('pane', '?')}", source_path=path,
                source_off=None,
                integrity=hashlib.sha256(
                    json.dumps(sig).encode()).hexdigest())
            n += 1
        return n
