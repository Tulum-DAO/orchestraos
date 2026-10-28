"""jobs.py — Arturo's background work, per thread: what the job tray shows and where results come home.

Work that outlives the turn that asked for it (web research, a question for the GM) used to vanish into a
Telegram outbox or a call that had already ended. Now each one is a JOB on the thread it was asked in:
running jobs and their live steps are listed for the tray above the composer, and when a job ends its result
is posted INTO that thread (`on_done`), where the brain sees it and, if a call is live there, Arturo says it.

In memory, with the last results kept on disk so a reload still shows what finished; a job that was running
when the service restarted is reported as lost, never silently as still running.
"""
import json
import logging
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger("arturo.jobs")

KEEP_DONE = 30           # finished jobs remembered per board (the tray shows only the recent ones)
MAX_STEPS = 60
MAX_RUNNING_PER_THREAD = 4


class JobBoard:
    def __init__(self, path=None, on_done=None, clock=time.time, spawn=None):
        self.path = Path(path) if path else None
        self.on_done = on_done                       # (job dict) -> None, after the job ends
        self.clock = clock
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True, name="arturo-job").start())
        self._lock = threading.Lock()
        self._jobs = {}                              # id -> job dict
        self._cancel = {}                            # id -> callable that stops it
        self._load()

    # -- persistence (finished jobs only; running ones cannot survive a restart) --
    def _load(self):
        if not self.path or not self.path.exists():
            return
        try:
            rows = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        for j in rows if isinstance(rows, list) else []:
            if isinstance(j, dict) and j.get("id"):
                if j.get("status") == "running":
                    j.update(status="lost", ended=j.get("ended") or self.clock(),
                             error="The assistant restarted while this was running")
                self._jobs[j["id"]] = j

    def _save(self):
        if not self.path:
            return
        with self._lock:
            rows = sorted(self._jobs.values(), key=lambda j: j["started"])
            done = [j for j in rows if j["status"] != "running"][-KEEP_DONE:]
            keep = [j for j in rows if j["status"] == "running"] + done
            self._jobs = {j["id"]: j for j in keep}
            blob = json.dumps(keep)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(blob)
            tmp.replace(self.path)
        except OSError as e:
            log.warning(f"jobs: could not save: {e}")

    # -- the API --
    def start(self, thread_id, kind, title, work, cancel=None):
        """Run `work(step)` in the background; `step(event)` reports a tool step. `work` returns (text, ok) or
        (text, ok, digest): `digest` is what the brain reads instead of the full text (a reader's structured result).
        Returns the job dict, or None when this thread already has too many running."""
        with self._lock:
            running = [j for j in self._jobs.values() if j["thread"] == thread_id and j["status"] == "running"]
            if len(running) >= MAX_RUNNING_PER_THREAD:
                return None
            jid = "job_" + uuid.uuid4().hex[:10]
            job = {"id": jid, "thread": thread_id, "kind": kind, "title": (title or kind)[:120],
                   "status": "running", "started": self.clock(), "ended": None, "steps": [],
                   "result": None, "error": None}
            self._jobs[jid] = job
            if cancel is not None:
                self._cancel[jid] = cancel

        def step(ev):
            with self._lock:
                steps = job["steps"]
                done = ev.get("phase") == "done"
                if done:
                    for s in reversed(steps):
                        if s.get("call_id") == ev.get("call_id") and s.get("ok") is None:
                            s.update(ok=bool(ev.get("ok")), summary=ev.get("summary") or "")
                            return
                if len(steps) < MAX_STEPS:
                    steps.append({"call_id": ev.get("call_id"), "name": ev.get("name") or "tool",
                                  "args_summary": ev.get("args_summary") or "",
                                  "ok": bool(ev.get("ok")) if done else None,
                                  "summary": (ev.get("summary") or "") if done else ""})

        def run():
            try:
                out = work(step)
                text, ok = out[0], out[1]
                digest = out[2] if len(out) > 2 else None   # what the brain reads, when not the full text
            except Exception as e:  # noqa: BLE001 — a job that crashed is a failed job, said so
                log.warning(f"job {jid} crashed: {e!r}")
                text, ok, digest = f"it failed: {e}", False, None
            with self._lock:
                if job["status"] == "cancelled":
                    pass
                elif ok:
                    job.update(status="done", result=text)
                    if digest:
                        job["digest"] = digest
                else:
                    job.update(status="failed", error=text)
                job["ended"] = self.clock()
                self._cancel.pop(jid, None)
                done = dict(job)
            self._save()
            if self.on_done is not None:
                try:
                    self.on_done(done)
                except Exception as e:  # noqa: BLE001
                    log.warning(f"job {jid}: posting its result failed: {e!r}")

        self._spawn(run)
        return dict(job)

    def cancel(self, thread_id, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["thread"] != thread_id or job["status"] != "running":
                return False
            job.update(status="cancelled", ended=self.clock(), error="cancelled")
            stop = self._cancel.pop(job_id, None)
        if stop is not None:
            try:
                stop()
            except Exception as e:  # noqa: BLE001
                log.warning(f"job {job_id}: cancel failed: {e!r}")
        self._save()
        return True

    def for_thread(self, thread_id, recent_s=600):
        """What the tray shows: running jobs, and ones that ended in the last `recent_s` (oldest first)."""
        now = self.clock()
        with self._lock:
            rows = [json.loads(json.dumps(j)) for j in self._jobs.values() if j["thread"] == thread_id
                    and (j["status"] == "running" or (now - (j["ended"] or now)) <= recent_s)]
        return sorted(rows, key=lambda j: j["started"])

    def get(self, job_id):
        with self._lock:
            j = self._jobs.get(job_id)
            return json.loads(json.dumps(j)) if j else None
