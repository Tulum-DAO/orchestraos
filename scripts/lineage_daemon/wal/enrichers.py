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

# A timed-out subprocess.run() SIGKILLs git; a status killed mid-refresh leaves a stale
# .git/index.lock that blocks every commit on the shared tree (2026-10-06, load ~60).
# Read-only callers never need the optional index lock.
_GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
import time


def _run(cwd, *args):
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, timeout=10, env=_GIT_ENV)
        return r.stdout if r.returncode == 0 else None
    except (subprocess.SubprocessError, OSError):
        return None


class TTLRunCache:
    """A (cwd, args) -> git stdout cache shared by every seat ACROSS ticks, entries live ttl_s.

    The per-tick dict it replaces was cleared every tick. With ~30 seats on one cwd, each behind
    its own staggered 60 s gate, `git status` (1.35 s on the 75k-file tree) then ran whenever ANY
    seat's window expired: 11 execs in 90 s (gm msg_51e755ee, 2026-10-08). With this cache a given
    (cwd, args) runs at most once per ttl_s fleet-wide. Each seat still diffs against its OWN last
    sample and emits its own events, so no capture is lost; the durable audit is at most ttl_s
    stale, which the 60 s gate already allowed per seat."""

    def __init__(self, ttl_s, clock=None):
        self._ttl_s = float(ttl_s)
        self._clock = clock or time.monotonic
        self._d = {}                     # key -> (stored_at, value)

    def _fresh(self, key):
        hit = self._d.get(key)
        if hit is None:
            return False
        if self._clock() - hit[0] >= self._ttl_s:
            del self._d[key]
            return False
        return True

    def __contains__(self, key):
        return self._fresh(key)

    def __getitem__(self, key):
        if not self._fresh(key):
            raise KeyError(key)
        return self._d[key][1]

    def __setitem__(self, key, value):
        self._d[key] = (self._clock(), value)

    def __len__(self):
        return len(self._d)

    def expire(self):
        """Drop expired entries (bounds memory); a live entry survives the tick."""
        now = self._clock()
        for k in [k for k, (at, _) in self._d.items() if now - at >= self._ttl_s]:
            del self._d[k]

    def clear(self):
        self._d.clear()


class GitEnricher:
    def __init__(self, store, lineage_root, generation, sid, cwd, runtime="claude",
                 run_cache=None, min_interval_s=0.0, clock=None, sample_path=None):
        self._store = store
        self._root = lineage_root
        self._gen = generation
        self._sid = sid
        self._cwd = cwd
        self._runtime = runtime
        self._last_status = {}   # path -> status code
        self._last_head = None
        # Requirement (b) — deltas since the LAST SAMPLE, across constructions. The per-beat
        # ensure-capture caller builds a NEW enricher every pass; with an in-memory-only last
        # sample every pass re-emitted the whole dirty set (11,627 file_mod rows per tick on
        # the shared tree, measured 2026-09-20). `sample_path` is a per-(root) sidecar JSON
        # {sid, head, status} written after each successful sample and loaded here. It is
        # keyed by sid: a rotated seat's live sid gets its own baseline once (the WAL for a
        # new sid must carry the dirty set it started under), then deltas. None (default,
        # the long-lived daemon and the mux) keeps the documented in-memory behaviour.
        self._sample_path = sample_path
        if sample_path:
            self._load_sample()
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

    def _load_sample(self):
        """Seed last-sample state from the sidecar; any failure = no baseline (fail-soft)."""
        try:
            with open(self._sample_path) as fh:
                d = json.load(fh)
            if d.get("sid") != self._sid or not isinstance(d.get("status"), dict):
                return
            self._last_status = dict(d["status"])
            self._last_head = d.get("head")
        except (OSError, ValueError, TypeError):
            return

    def _save_sample(self):
        """Atomic write (tmp + rename) so a crash mid-write never leaves a half sidecar."""
        if not self._sample_path:
            return
        tmp = f"{self._sample_path}.tmp.{os.getpid()}"
        try:
            with open(tmp, "w") as fh:
                json.dump({"sid": self._sid, "head": self._last_head,
                           "status": self._last_status, "ts": self._clock()}, fh)
            os.replace(tmp, self._sample_path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _run_cached(self, *args):
        if self._run_cache is None:
            return _run(self._cwd, *args)
        key = (self._cwd, args)
        if key in self._run_cache:
            return self._run_cache[key]
        out = _run(self._cwd, *args)
        if out is not None:              # a failed run retries next time; it is never served
            self._run_cache[key] = out
        return out

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
        # cwd=None GUARD (gm-g75 amendment msg_00b76413 item 2, 2026-09-20). With no cwd
        # there is no repo to sample AND source_path would be NULL, which the store's
        # NOT NULL constraint turns into an IntegrityError raised out of tick() — killing
        # the whole pass before the transcript could be read (measured: 2,001 such lines
        # in telemetryd-manual.log; every one a seat whose cwd the projection dropped).
        # Skip, say so ONCE per seat so the log is a signal not a flood, return 0.
        if self._cwd is None:
            if not getattr(self, "_warned_no_cwd", False):
                self._warned_no_cwd = True
                import sys as _sys
                print(f"[wal] git enricher skipped for {self._root}: cwd is None "
                      f"(projection gap; resolver fallback should have supplied it)",
                      file=_sys.stderr)
            return 0
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
        self._save_sample()
        return n

    def _append(self, kind, summary, *, body_ref, integrity):
        self._store.append(
            # ts = the enricher's clock at emission (devex msg_e5d185f4 / gm msg_b7f7d36d): rows
            # written ts=0.0 could never be dated, so no tick could be attributed from the store.
            ts=float(self._clock()), lineage_root=self._root, generation=self._gen,
            sid=self._sid, runtime=self._runtime, kind=kind, summary=summary,
            body_ref=body_ref, source_path=self._cwd, source_off=None,
            integrity=integrity)


class ProcEnricher:
    def __init__(self, store, lineage_root, generation, sid, panes_dir,
                 runtime="claude", clock=None):
        self._store = store
        self._root = lineage_root
        self._gen = generation
        self._sid = sid
        self._panes_dir = panes_dir
        self._runtime = runtime
        self._clock = clock or time.time
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
                # the pane's own ts when it carries one, else the emission clock — never 0.0
                ts=float(ev.get("ts") or self._clock()), lineage_root=self._root,
                generation=self._gen, sid=self._sid, runtime=self._runtime,
                kind="proc", summary=summary,
                body_ref=f"pane:{ev.get('pane', '?')}", source_path=path,
                source_off=None,
                integrity=hashlib.sha256(
                    json.dumps(sig).encode()).hexdigest())
            n += 1
        return n
