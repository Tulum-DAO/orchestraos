"""/proc pid-tree sampler — the kernel-truth liveness axis for B1(a).

CPU-HARD invariant (A0-proven, the operator-standing): NEVER scan /proc per-agent. A
per-agent `ps` fork measured 2.57% (FAIL); ONE /proc scan per tick fanned to N
agents measured 0.320% (PASS). So sample() does exactly ONE pass over /proc for
stat (building the whole pid->children map + cpu counters), fans it to every
registered agent's pid TREE (ps-BFS descendants — the setsid-grandchild lesson:
a stalled CLI with a live spawned child is COMPUTING), and reads /proc/<pid>/io
ONLY for the pids in the fleet's own trees (a small bounded set — never per-agent
forks, never every pid on the box).

This module DELIBERATELY touches /proc (it is the whole point — the kernel truth
the provider-idle-detection commission was chartered to find). The guard the WAL
lane carries (`test_no_per_agent_proc_scan`) forbids /proc in the DURABLE capture
lane; the real-time lane is where /proc access legitimately lives — bounded to
ONE scan/tick by construction (scan_count is asserted in the tests + the CPU
measure proves the <1% budget on real /proc).

Stateful: sample() computes per-root DELTAS (cpu_core_pct, io_delta) versus the
previous sample using a monotonic wall clock, so the caller gets ready-to-classify
windows. bytes_flowing is NOT a /proc signal — it is fused from the pipe-pane ring
by the daemon.
"""
import os
import re
import time

# C1 (DEC-1788493111 §9.2): child classification. Resident children (excluded
# from the seat's WORKING signal) are known plumbing/MCP by cmdline signature, OR
# long-lived AND steady-state-low. Worker children (a real spawned build/test —
# the setsid-grandchild lesson) count. AGE ALONE NEVER DEMOTES: a long-lived
# child with sustained activity stays a worker.
RESIDENT_MIN_AGE_S = 120.0            # a child must be at least this old to be resident-by-idleness
RESIDENT_STEADY_CPU_PCT = 3.0         # ...and below this sustained cpu (one core %)
RESIDENT_STEADY_IO_BYTES = 131072     # ...and below this io per window (128 KB)
_RESIDENT_CMDLINE_RE = re.compile(
    r"mcp[-_ ]?server|modelcontextprotocol|[-_/]mcp\b|episodic-memory|"
    r"chrome-devtools|chromedriver|\bpipe-pane\b|\btmux\b|"
    r"run_capture|lineage_daemon\.telemetryd|superpowers-chrome",
    re.IGNORECASE)


def _read_cmdline(proc_root, pid):
    """The child's cmdline (NUL-joined -> spaces), '' on any error. Bounded to the
    fleet's own tree pids by the caller (same bound as _read_io; no per-agent
    fork, never every pid on the box)."""
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as fh:
            return fh.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _read_stat(proc_root, pid):
    """Return (ppid, utime_ticks+stime_ticks, starttime_ticks) or None. starttime
    (field 22, ticks since boot) gives the process's REAL age — critical so a
    long-lived MCP is classified resident IMMEDIATELY on daemon (re)start, not
    misread as a young worker for the first RESIDENT_MIN_AGE_S (the cold-start
    false-compute)."""
    try:
        with open(os.path.join(proc_root, str(pid), "stat")) as fh:
            raw = fh.read()
    except OSError:
        return None
    try:
        after = raw.rsplit(")", 1)[1].split()
        ppid = int(after[1])
        cpu = int(after[11]) + int(after[12])   # utime + stime (ticks)
        starttime = int(after[19])              # field 22: ticks since boot
        return ppid, cpu, starttime
    except (IndexError, ValueError):
        return None


def _read_uptime(proc_root):
    """System uptime in seconds (proc_root/uptime; tests override via a fake file).
    Returns None on any error (age check then falls back to conservative worker)."""
    try:
        with open(os.path.join(proc_root, "uptime")) as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _read_io(proc_root, pid):
    """read_bytes+write_bytes, or 0 on any error (PermissionError for another
    user's pid, or a kernel without the io file) — never crash the scan."""
    try:
        with open(os.path.join(proc_root, str(pid), "io")) as fh:
            r = w = 0
            for line in fh:
                if line.startswith("read_bytes:"):
                    r = int(line.split()[1])
                elif line.startswith("write_bytes:"):
                    w = int(line.split()[1])
            return r + w
    except (OSError, ValueError):
        return 0


class ProcSampler:
    def __init__(self, proc_root="/proc", clk_tck=None):
        self._proc = proc_root
        self._clk = clk_tck or os.sysconf("SC_CLK_TCK")
        self.scan_count = 0
        self._last = {}   # root_pid -> (cpu_ticks, io_bytes, wall_ts)
        # C1 per-child lifecycle ledger (pid -> {first_seen, cpu, io, ts}).
        self._pid = {}
        self._pid_cmdline = {}   # cmdline cached on first sight (never changes)

    def _scan_stat(self):
        """ONE pass over /proc: build pid->children and pid->cpu_ticks. This is
        the single fan-out scan (never per-agent)."""
        self.scan_count += 1
        kids, cpu, start, present = {}, {}, {}, set()
        try:
            names = os.listdir(self._proc)
        except OSError:
            return kids, cpu, start, present
        for name in names:
            if not name.isdigit():
                continue
            pid = int(name)
            got = _read_stat(self._proc, pid)
            if got is None:
                continue
            ppid, c, st = got
            present.add(pid)
            cpu[pid] = c
            start[pid] = st
            kids.setdefault(ppid, []).append(pid)
        return kids, cpu, start, present

    @staticmethod
    def _tree(root, kids):
        seen, stack = [root], [root]
        while stack:
            p = stack.pop()
            for c in kids.get(p, ()):  # descendants
                if c not in seen:
                    seen.append(c)
                    stack.append(c)
        return seen

    def _pid_delta(self, pid, cpu_ticks, io_bytes, now):
        """Per-pid (cpu_core_pct, io_delta) vs its last sample. First sight => (0.0, 0)."""
        prev = self._pid.get(pid)
        cpu_pct, io_delta = 0.0, 0
        if prev is not None:
            dcpu, dio, dt = cpu_ticks - prev[0], io_bytes - prev[1], now - prev[2]
            if dt > 0:
                cpu_pct = (dcpu / self._clk) / dt * 100.0
            io_delta = max(0, dio)
        self._pid[pid] = (cpu_ticks, io_bytes, now)
        return cpu_pct, io_delta

    def _classify_child(self, cpu_pct, io_delta, cmdline, age):
        """'resident' (excluded from the working signal) or 'worker' (counts).
        `age` is the process's REAL age (from /proc starttime), so a long-lived MCP
        is resident immediately on daemon (re)start — never a cold-start false
        worker. age None (unknown) => conservative worker (never a false idle)."""
        if _RESIDENT_CMDLINE_RE.search(cmdline or ""):
            return "resident"
        if (age is not None and age >= RESIDENT_MIN_AGE_S
                and cpu_pct < RESIDENT_STEADY_CPU_PCT
                and io_delta < RESIDENT_STEADY_IO_BYTES):
            return "resident"       # long-lived AND steady-state-low, no signature
        return "worker"             # young, active, long-lived-and-active, or unknown-age

    def sample(self, roots, now=None):
        """ONE /proc scan fanned to all `roots`. Returns per root the whole-tree
        aggregate (alive, live_pids, cpu_ticks, io_bytes, cpu_core_pct, io_delta —
        unchanged for back-compat) PLUS the C1 partition: own_* (root CLI), worker_*
        (worker children), resident_* (MCP/plumbing/idle-monitor children). Deltas
        are vs the previous sample (0 on first)."""
        now = time.monotonic() if now is None else now
        kids, cpu, start, present = self._scan_stat()
        uptime = _read_uptime(self._proc)
        out = {}
        alive_pids = set()
        for root in roots:
            if root not in present:
                out[root] = {"alive": False, "live_pids": 0, "cpu_ticks": 0,
                             "io_bytes": 0, "cpu_core_pct": 0.0, "io_delta": 0,
                             "own_cpu_core_pct": 0.0, "own_io_delta": 0,
                             "worker_cpu_core_pct": 0.0, "worker_io_delta": 0,
                             "resident_cpu_core_pct": 0.0, "resident_io_delta": 0}
                self._last.pop(root, None)
                continue
            tree = self._tree(root, kids)
            cpu_ticks = sum(cpu.get(p, 0) for p in tree)
            io_bytes = sum(_read_io(self._proc, p) for p in tree)
            prev = self._last.get(root)
            cpu_core_pct, io_delta = 0.0, 0
            if prev is not None:
                dcpu, dio, dt = cpu_ticks - prev[0], io_bytes - prev[1], now - prev[2]
                if dt > 0:
                    cpu_core_pct = (dcpu / self._clk) / dt * 100.0
                io_delta = max(0, dio)
            self._last[root] = (cpu_ticks, io_bytes, now)

            # --- C1 partition: own (root pid) + per-child classification --------
            own_cpu, own_io = self._pid_delta(root, cpu.get(root, 0),
                                              _read_io(self._proc, root), now)
            w_cpu = r_cpu = 0.0
            w_io = r_io = 0
            for p in tree:
                if p == root:
                    continue
                c_cpu, c_io = self._pid_delta(p, cpu.get(p, 0),
                                              _read_io(self._proc, p), now)
                cmd = self._pid_cmdline.get(p)
                if cmd is None:          # read cmdline ONCE per pid (it never changes)
                    cmd = _read_cmdline(self._proc, p)
                    self._pid_cmdline[p] = cmd
                age = None
                if uptime is not None and p in start:
                    age = max(0.0, uptime - start[p] / self._clk)
                kind = self._classify_child(c_cpu, c_io, cmd, age)
                if kind == "resident":
                    r_cpu += c_cpu
                    r_io += c_io
                else:
                    w_cpu += c_cpu
                    w_io += c_io
            alive_pids.update(tree)
            out[root] = {"alive": True, "live_pids": len(tree),
                         "cpu_ticks": cpu_ticks, "io_bytes": io_bytes,
                         "cpu_core_pct": cpu_core_pct, "io_delta": io_delta,
                         "own_cpu_core_pct": own_cpu, "own_io_delta": own_io,
                         "worker_cpu_core_pct": w_cpu, "worker_io_delta": w_io,
                         "resident_cpu_core_pct": r_cpu, "resident_io_delta": r_io}
        # prune ledger entries for pids that vanished (bounded memory)
        for dead in [p for p in self._pid if p not in alive_pids]:
            self._pid.pop(dead, None)
            self._pid_cmdline.pop(dead, None)
        return out
