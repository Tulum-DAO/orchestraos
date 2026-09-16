"""A small stdlib supervisor: one process table, restart-with-backoff, interval
beats run in-process (no crontab), logs under <data>/logs, SIGTERM stops children.

`Supervisor.tick()` is pure over an injected clock + spawner so the loop is
testable without real processes; `run()` is the thin real loop.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from .process_table import ProcEntry

BACKOFF_BASE_S = 2.0
BACKOFF_MAX_S = 300.0
STABLE_UPTIME_S = 60.0
TICK_S = 1.0
STOP_GRACE_S = 10.0


def default_spawn(entry: ProcEntry, env: dict, log_path: str):
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab")
    try:
        return subprocess.Popen(entry.argv, cwd=entry.cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    finally:
        log.close()


class _Child:
    def __init__(self, entry: ProcEntry):
        self.entry = entry
        self.proc = None
        self.started_at: Optional[float] = None
        self.next_start: float = 0.0
        self.restarts = 0
        self.failures = 0            # consecutive short-lived exits (drives backoff)
        self.last_rc: Optional[int] = None
        self.status = "disabled" if not entry.enabled else "pending"


class Supervisor:
    def __init__(self, table: list, data_dir: Path, base_env: dict, spawn: Callable = default_spawn,
                 clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = None):
        self.data_dir = Path(data_dir)
        self.base_env = dict(base_env)
        self.spawn = spawn
        self.clock = clock
        self.children = [_Child(e) for e in table]
        self._log = log or (lambda s: None)
        self.stopping = False

    # --- files ---------------------------------------------------------
    @property
    def state_path(self) -> Path:
        return self.data_dir / "state" / "supervisor.json"

    @property
    def pid_path(self) -> Path:
        return self.data_dir / "state" / "supervisor.pid"

    def write_pidfile(self, pid: int | None = None):
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.pid_path.write_text(str(pid or os.getpid()))

    def write_state(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"pid": os.getpid(), "updated": time.time(), "children": {}}
        for c in self.children:
            payload["children"][c.entry.name] = {
                "kind": c.entry.kind, "status": c.status,
                "pid": c.proc.pid if c.proc is not None else None,
                "port": c.entry.port, "interval": c.entry.interval,
                "restarts": c.restarts, "last_rc": c.last_rc,
                "log": str(self.data_dir / "logs" / f"{c.entry.name}.log"),
            }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1))
        os.replace(tmp, self.state_path)

    # --- loop ----------------------------------------------------------
    def _start(self, c: _Child, now: float):
        env = dict(self.base_env)
        env.update(c.entry.env)
        log_path = str(self.data_dir / "logs" / f"{c.entry.name}.log")
        c.proc = self.spawn(c.entry, env, log_path)
        c.started_at = now
        c.status = "running"
        self._log(f"start {c.entry.name} pid={c.proc.pid}")

    def tick(self):
        now = self.clock()
        for c in self.children:
            if not c.entry.enabled:
                continue
            if c.proc is not None:
                rc = c.proc.poll()
                if rc is None:
                    continue
                c.last_rc = rc
                started = c.started_at if c.started_at is not None else now
                uptime = now - started
                c.proc = None
                if c.entry.kind == "service":
                    c.failures = 0 if uptime >= STABLE_UPTIME_S else c.failures + 1
                    delay = min(BACKOFF_BASE_S * (2 ** max(c.failures - 1, 0)), BACKOFF_MAX_S)
                    c.next_start = now + delay
                    c.status = "restarting"
                    self._log(f"exit {c.entry.name} rc={rc} uptime={uptime:.0f}s restart in {delay:.0f}s")
                else:
                    c.next_start = started + (c.entry.interval or 60)
                    c.status = "idle"
                    if rc not in (0, None):
                        self._log(f"beat {c.entry.name} rc={rc}")
            if not self.stopping and now >= c.next_start:
                if c.entry.kind == "service" and c.started_at is not None:
                    c.restarts += 1
                self._start(c, now)
        self.write_state()

    def run(self):
        self.write_pidfile()

        def _stop(signum, frame):
            self.stopping = True

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        try:
            while not self.stopping:
                self.tick()
                time.sleep(TICK_S)
        finally:
            self.stop()

    def stop(self):
        self.stopping = True
        live = [c for c in self.children if c.proc is not None and c.proc.poll() is None]
        for c in live:
            try:
                c.proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        deadline = self.clock() + STOP_GRACE_S
        for c in live:
            try:
                c.proc.wait(timeout=max(0.1, deadline - self.clock()))
            except Exception:  # noqa: BLE001
                try:
                    c.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        for c in self.children:
            if c.entry.enabled:
                c.status = "stopped"
            c.proc = None
        self.write_state()
        try:
            self.pid_path.unlink()
        except FileNotFoundError:
            pass


# --- status / down ---------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_status(data_dir: Path, pid_alive: Callable[[int], bool] = _pid_alive) -> dict:
    data_dir = Path(data_dir)
    pid_path = data_dir / "state" / "supervisor.pid"
    state_path = data_dir / "state" / "supervisor.json"
    pid = None
    try:
        pid = int(pid_path.read_text().strip())
    except (FileNotFoundError, ValueError):
        pass
    running = bool(pid and pid_alive(pid))
    state = {}
    try:
        state = json.loads(state_path.read_text())
    except (FileNotFoundError, ValueError):
        pass
    return {"pid": pid, "running": running, "children": state.get("children", {}),
            "updated": state.get("updated")}


def stop_running(data_dir: Path, wait_s: float = STOP_GRACE_S + 5) -> bool:
    st = read_status(data_dir)
    if not st["running"]:
        return False
    os.kill(st["pid"], signal.SIGTERM)
    deadline = time.time() + wait_s
    while time.time() < deadline and _pid_alive(st["pid"]):
        time.sleep(0.2)
    return not _pid_alive(st["pid"])
