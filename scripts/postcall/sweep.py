"""The cron sweeper (design §1 "Mechanism", "Concurrency contract", "Recovery"; §7 live_start_ts).

One run, under one lock:
  1. take `flock -n` on <state_dir>/sweep.lock (busy -> do nothing, exit 0);
  2. reconcile and send every effect still open in the ledger (recovery before new work);
  3. fix the ledger's start_ts on its first run (no backfill: older calls are never considered);
  4. process each settled, ingested Pocket call that ended (created_at + duration) at or after
     start_ts, oldest first.
One bad call is recorded and skipped; it never stops the rest of the sweep.

    python3 -m postcall.sweep [--once]      # prints the run summary as one JSON line
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import sqlite3
import time

from . import effects, ledger, pipeline, sources

SETTLE_S = 120  # a file touched more recently than this may still be being written (§1)

_META_SCHEMA = "CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)"


def pocket_root():
    return os.environ.get("POCKET_ROOT") or os.path.expanduser("~/pocket-recordings")


# --- start_ts: a tiny key/value table living in the ledger's own sqlite file -------------------

def _iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch(iso):
    """ISO-8601 (with or without Z / offset; naive means UTC) -> epoch seconds."""
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def _db_path(led):
    return led.path if isinstance(led, ledger.Ledger) else led


def live_start_ts(led):
    """The ledger's activation time (ISO, UTC), or None before its first sweep. Takes a Ledger or a path."""
    with sqlite3.connect(_db_path(led), timeout=30) as c:
        c.execute(_META_SCHEMA)
        row = c.execute("SELECT v FROM meta WHERE k='start_ts'").fetchone()
    return row[0] if row else None


def ensure_start_ts(ledger_path, now):
    """Set start_ts to `now` (epoch) if this ledger has none yet; return the stored value either way.
    The first run in a ledger defines it and later runs never move it."""
    with sqlite3.connect(ledger_path, timeout=30) as c:
        c.execute(_META_SCHEMA)
        c.execute("INSERT OR IGNORE INTO meta(k, v) VALUES('start_ts', ?)", (_iso(now),))
    return live_start_ts(ledger_path)


# --- finding work --------------------------------------------------------------------------------

def scan(root, start_ts, now, settle_s=SETTLE_S):
    """Classify every recording dir under root. Returns
    {"ready": [dirs sorted by created_at], "before_start": n, "settling": n, "not_ready": n, "bad": [(dir, err)]}."""
    out = {"ready": [], "before_start": 0, "settling": 0, "not_ready": 0, "bad": []}
    start = _epoch(start_ts)
    ready = []
    try:
        names = sorted(os.listdir(root))
    except FileNotFoundError:
        return out
    for name in names:
        rec = os.path.join(root, name)
        files = [os.path.join(rec, "meta.json"), os.path.join(rec, "transcript.md")]
        if not all(os.path.isfile(f) for f in files):
            out["not_ready"] += 1                       # no transcript yet (or not a recording at all)
            continue
        settled = all(os.path.getmtime(f) <= now - settle_s for f in files)
        try:
            with open(files[0]) as f:
                meta = json.load(f)
            created = _epoch(meta["created_at"])
            ended = created + float(meta.get("duration") or 0)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            if settled:
                out["bad"].append((name, f"meta.json: {e!r}"))
            else:
                out["settling"] += 1                    # a half-written meta.json is just early
            continue
        if ended < start:
            out["before_start"] += 1                    # no backfill (§1): the call ENDED before activation
        elif not meta.get("ingested"):
            out["not_ready"] += 1
        elif not settled:
            out["settling"] += 1
        else:
            ready.append((created, rec))
    out["ready"] = [rec for _, rec in sorted(ready)]
    return out


def candidates(root, start_ts, now, settle_s=SETTLE_S):
    """Recording dirs ready to process: meta.json + transcript.md present, ingested, ended at or after
    start_ts, and neither file touched within settle_s of now. Oldest call first."""
    yield from scan(root, start_ts, now, settle_s)["ready"]


# --- the lock ------------------------------------------------------------------------------------

def acquire_lock(path):
    """Non-blocking exclusive flock on path; the fd, or None when another holder has it.
    The fd is inheritable so CLI children spawned during the run keep holding the lock (§1)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    os.set_inheritable(fd, True)
    return fd


# --- one sweep -----------------------------------------------------------------------------------

def _default_extractor():
    try:
        from .extractor import default_extractor
    except ImportError as e:
        raise RuntimeError("postcall.sweep: no extractor given and postcall.extractor.default_extractor "
                           f"is not importable ({e})") from e
    return default_extractor


def run_once(*, now=None, extractor=None, adapter=None, mode=None, root=None):
    now = time.time() if now is None else now
    mode = mode or effects.mode()
    extractor = extractor or _default_extractor()   # fail loudly before touching any state
    root = root or pocket_root()

    fd = acquire_lock(os.path.join(effects.state_dir(), "sweep.lock"))
    if fd is None:
        return {"status": "busy"}
    effects.set_lock_fd(fd)
    try:
        path = effects.ledger_path(mode)
        led = ledger.Ledger(path)
        adapter = adapter or effects.adapter_for(mode)
        out = {"status": "ok", "mode": mode, "ledger": path, "processed": 0, "statuses": {},
               "before_start": 0, "settling": 0, "errors": []}

        # Recovery first: whatever an earlier run planned but never confirmed is settled before new work.
        try:
            led.run_pending(adapter)
        except Exception as e:
            out["status"] = "recovery_failed"
            out["errors"].append({"call": None, "error": f"recovery: {e!r}"})
            return out

        # Shaw's answers on cards filed by earlier runs, before any new call (design §4).
        try:
            pipeline.apply_submitted(led, adapter)
        except Exception as e:
            out["errors"].append({"call": None, "error": f"answers: {e!r}"})

        # Cards nobody answered: retired 14 days after their Decide-by (gm ruling §9.6).
        out["retired"] = []
        try:
            out["retired"] = pipeline.retire_stale(led, adapter, dt.datetime.fromtimestamp(now, dt.timezone.utc))
        except Exception as e:
            out["errors"].append({"call": None, "error": f"retire: {e!r}"})

        start_ts = ensure_start_ts(path, now)
        found = scan(root, start_ts, now)
        out["before_start"], out["settling"] = found["before_start"], found["settling"]
        out["errors"] += [{"call": name, "error": err} for name, err in found["bad"]]

        for rec in found["ready"]:
            name = os.path.basename(rec)
            call = None
            try:
                call = sources.load_pocket(rec)
                result = pipeline.process(call, ledger=led, extractor=extractor, effects=adapter)
            except Exception as e:                  # one bad call never stops the sweep
                out["errors"].append({"call": name, "error": repr(e)})
                if call is not None:                # it counts as an attempt (failed_retry -> failed_final)
                    try:
                        led.set_call_status(call.call_id, "failed_retry", content_hash=call.content_hash,
                                            error=repr(e)[-500:])
                    except Exception:
                        pass
                continue
            out["processed"] += 1
            out["statuses"][result.status] = out["statuses"].get(result.status, 0) + 1

        # Seat replies to our dispatches, then the one-per-card gm results notice where due (design §6).
        out["results_recorded"] = out["results_planned"] = 0
        try:
            out["results_recorded"] = pipeline.collect_replies(led, effects.msg_db_path())
        except Exception as e:
            out["errors"].append({"call": None, "error": f"replies: {e!r}"})
        try:
            out["results_planned"] = len(pipeline.results_due(led, dt.datetime.fromtimestamp(now, dt.timezone.utc)))
            led.run_pending(adapter)
        except Exception as e:
            out["errors"].append({"call": None, "error": f"results: {e!r}"})
        out["failed_effects"] = led.failed_effects()
        out["failed_final"] = led.calls_with_status("failed_final")   # for manual review
        return out
    finally:
        effects.set_lock_fd(None)
        os.close(fd)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m postcall.sweep", description=__doc__.split("\n")[0])
    p.add_argument("--once", action="store_true", help="run one sweep and exit (the only mode; cron calls it)")
    p.parse_args(argv)
    print(json.dumps(run_once(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
