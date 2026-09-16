"""RED tests for C1 cpu/io ATTRIBUTION (DEC-1788493111, spec §9.2).

The old sampler summed the WHOLE pid subtree into one cpu/io aggregate, so an
idle CLI with an MCP server / background monitor read `computing` (the
pm-molevera "done 2:56 AM, 1 monitor still running" case). C1 PARTITIONS the
tree into own (root CLI) + worker children + resident children, with a per-child
lifecycle ledger:
  * resident = known-signature cmdline (MCP/plumbing) OR (long-lived AND
    steady-state-low). AGE ALONE NEVER DEMOTES — a long-lived high-activity child
    (a 30-min cargo build) stays a worker.
  * worker = young, unsignatured-and-active, or long-lived-and-active.
The setsid-grandchild lesson holds: a freshly spawned worker still counts.

Age is the process's REAL /proc starttime (field 22) relative to uptime — NOT
sampler-first-seen — so a long-lived MCP is resident IMMEDIATELY on daemon
(re)start (no cold-start false-compute). Hermetic synthetic /proc; clk_tck=100.
"""
import os

from lineage_daemon.realtime.proc_sampler import ProcSampler

_CLK = 100
_UPTIME = 100000.0            # synthetic system uptime (s)


def _mkroot(root, uptime=_UPTIME):
    with open(os.path.join(root, "uptime"), "w") as fh:
        fh.write("%f 0.0\n" % uptime)


def _mkproc(root, pid, ppid, utime, stime, rbytes=0, wbytes=0, cmdline="agent",
            age_s=0.0):
    d = os.path.join(root, str(pid))
    os.makedirs(d, exist_ok=True)
    start_ticks = int((_UPTIME - age_s) * _CLK)
    # after-comm layout: state, ppid, 9x, utime(11), stime(12), 6x, starttime(19), pad
    after = ["S", str(ppid)] + ["0"] * 9 + [str(utime), str(stime)] \
        + ["0"] * 6 + [str(start_ticks)] + ["0"] * 20
    open(os.path.join(d, "stat"), "w").write(
        " ".join([str(pid), "(proc)"] + after) + "\n")
    open(os.path.join(d, "io"), "w").write(
        f"rchar: 0\nwchar: 0\nread_bytes: {rbytes}\nwrite_bytes: {wbytes}\n")
    open(os.path.join(d, "cmdline"), "w").write(cmdline.replace(" ", "\0") + "\0")


def _sampler(root):
    return ProcSampler(proc_root=root, clk_tck=_CLK)


def _partition(snap_root):
    return (snap_root["own_cpu_core_pct"], snap_root["worker_cpu_core_pct"],
            snap_root["resident_cpu_core_pct"], snap_root["own_io_delta"],
            snap_root["worker_io_delta"], snap_root["resident_io_delta"])


def test_resident_mcp_child_excluded_from_worker_signal(tmp_path):
    root = str(tmp_path)
    _mkroot(root)
    s = _sampler(root)
    # root CLI idle; a resident MCP child by cmdline signature (any age) doing io.
    _mkproc(root, 100, 1, 0, 0, cmdline="claude", age_s=5000)
    _mkproc(root, 200, 100, 0, 0, cmdline="node /x/episodic-memory-mcp/server.js", age_s=3000)
    s.sample([100], now=0.0)
    _mkproc(root, 100, 1, 0, 0, cmdline="claude", age_s=5000)
    _mkproc(root, 200, 100, 500, 0, rbytes=200000,
            cmdline="node /x/episodic-memory-mcp/server.js", age_s=3000)
    own_cpu, worker_cpu, res_cpu, own_io, worker_io, res_io = _partition(
        s.sample([100], now=5.0)[100])
    assert worker_cpu == 0.0 and worker_io == 0
    assert res_cpu > 0.0 and res_io >= 200000
    assert own_cpu == 0.0


def test_young_worker_child_counts_toward_worker(tmp_path):
    root = str(tmp_path)
    _mkroot(root)
    s = _sampler(root)
    _mkproc(root, 100, 1, 0, 0, cmdline="codex", age_s=5000)
    _mkproc(root, 300, 100, 0, 0, cmdline="python build.py", age_s=3)   # fresh spawn
    s.sample([100], now=0.0)
    _mkproc(root, 100, 1, 0, 0, cmdline="codex", age_s=5000)
    _mkproc(root, 300, 100, 800, 200, rbytes=90000, cmdline="python build.py", age_s=6)
    own_cpu, worker_cpu, res_cpu, own_io, worker_io, res_io = _partition(
        s.sample([100], now=3.0)[100])
    assert worker_cpu > 0.0 and worker_io >= 90000
    assert res_cpu == 0.0


def test_long_lived_high_activity_child_stays_worker(tmp_path):
    # a 30-min cargo build: age alone must NOT demote it to resident.
    root = str(tmp_path)
    _mkroot(root)
    s = _sampler(root)
    _mkproc(root, 100, 1, 0, 0, cmdline="claude", age_s=5000)
    _mkproc(root, 400, 100, 0, 0, cmdline="cargo build", age_s=1800)
    s.sample([100], now=0.0)
    _mkproc(root, 100, 1, 0, 0, cmdline="claude", age_s=5000)
    _mkproc(root, 400, 100, 3000, 1000, rbytes=500000, cmdline="cargo build", age_s=1800)
    own_cpu, worker_cpu, res_cpu, own_io, worker_io, res_io = _partition(
        s.sample([100], now=1800.0)[100])
    assert worker_cpu > 0.0                            # still a worker (busy)
    assert res_cpu == 0.0


def test_long_lived_idle_monitor_is_resident(tmp_path):
    # a leftover Bash(run_in_background) monitor: old + low steady activity, no
    # signature -> resident (excluded), even doing a little io.
    root = str(tmp_path)
    _mkroot(root)
    s = _sampler(root)
    _mkproc(root, 100, 1, 0, 0, cmdline="claude", age_s=5000)
    _mkproc(root, 500, 100, 0, 0, cmdline="bash -c tail -f log", age_s=600)
    s.sample([100], now=0.0)
    _mkproc(root, 100, 1, 0, 0, cmdline="claude", age_s=5000)
    _mkproc(root, 500, 100, 2, 0, rbytes=8000, cmdline="bash -c tail -f log", age_s=600)
    own_cpu, worker_cpu, res_cpu, own_io, worker_io, res_io = _partition(
        s.sample([100], now=600.0)[100])
    assert res_io >= 8000 and worker_io == 0


def test_cold_start_long_lived_mcp_is_resident_on_first_delta(tmp_path):
    # THE cold-start bug: a daemon (re)start sees an hours-old MCP for the FIRST
    # time. Age from /proc starttime (not sampler-first-seen) => resident at once,
    # never a young-worker false-compute for the first RESIDENT_MIN_AGE_S.
    root = str(tmp_path)
    _mkroot(root)
    s = _sampler(root)
    _mkproc(root, 100, 1, 0, 0, cmdline="claude", age_s=9000)
    _mkproc(root, 600, 100, 0, 0, cmdline="python generic_helper.py", age_s=9000)  # hours old, no sig
    s.sample([100], now=0.0)
    _mkproc(root, 100, 1, 0, 0, cmdline="claude", age_s=9000)
    _mkproc(root, 600, 100, 3, 0, rbytes=9000, cmdline="python generic_helper.py", age_s=9000)
    own_cpu, worker_cpu, res_cpu, own_io, worker_io, res_io = _partition(
        s.sample([100], now=5.0)[100])
    assert worker_io == 0 and res_io >= 9000          # resident on the FIRST real delta


def test_aggregate_keys_preserved_backward_compat(tmp_path):
    root = str(tmp_path)
    _mkroot(root)
    s = _sampler(root)
    _mkproc(root, 100, 1, 10, 5, cmdline="claude", age_s=5000)
    r = s.sample([100], now=0.0)[100]
    assert set(["alive", "live_pids", "cpu_core_pct", "io_delta"]) <= set(r)
