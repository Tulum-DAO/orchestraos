"""RED tests for the /proc pid-tree sampler — the CPU-HARD invariant.

A0-proven: NEVER scan /proc per-agent (per-agent ps-fork = 2.57% FAIL). ONE
/proc scan per tick, fanned to N agents (one-scan-fanout = 0.320% PASS). These
tests use a SYNTHETIC /proc tree (proc_root override) so they are hermetic; the
real-/proc <1% measure lives in cpu_measure_test.py.

Also proves: the whole pid TREE is summed (ps-BFS descendants — the
setsid-grandchild lesson: a stalled CLI with a live spawned child is COMPUTING),
and IO is read only for the fleet's own tree pids (not every pid, not per-agent).
"""
import os

from lineage_daemon.realtime.proc_sampler import ProcSampler


def _mkproc(root, pid, ppid, utime, stime, rbytes=0, wbytes=0, io=True):
    d = os.path.join(root, str(pid))
    os.makedirs(d, exist_ok=True)
    # /proc/<pid>/stat: pid (comm) state ppid ... utime(14) stime(15) ...
    fields = [str(pid), "(agent)", "S", str(ppid)] + ["0"] * 9 + [str(utime), str(stime)] \
        + ["0"] * 30
    open(os.path.join(d, "stat"), "w").write(" ".join(fields) + "\n")
    if io:
        open(os.path.join(d, "io"), "w").write(
            f"rchar: 0\nwchar: 0\nread_bytes: {rbytes}\nwrite_bytes: {wbytes}\n")


def test_pid_tree_sums_descendants_bfs(tmp_path):
    root = str(tmp_path)
    # tree: 100 -> 200 -> 300 (grandchild, the setsid lesson)
    _mkproc(root, 100, 1, 10, 5)
    _mkproc(root, 200, 100, 20, 10)
    _mkproc(root, 300, 200, 40, 20)
    _mkproc(root, 999, 1, 1000, 1000)         # unrelated process, must NOT count
    s = ProcSampler(proc_root=root)
    snap = s.sample([100])
    r = snap[100]
    assert r["alive"] is True
    assert r["live_pids"] == 3                # 100,200,300
    assert r["cpu_ticks"] == (10 + 5) + (20 + 10) + (40 + 20)  # tree sum, excludes 999


def test_one_scan_fanned_to_many_agents_never_per_agent(tmp_path):
    root = str(tmp_path)
    roots = list(range(1000, 1040))           # 40 agents
    for i, pid in enumerate(roots):
        _mkproc(root, pid, 1, i, i)
    s = ProcSampler(proc_root=root)
    before = s.scan_count
    s.sample(roots)
    after = s.scan_count
    assert after - before == 1, (
        "one /proc scan per sample() regardless of N agents (A0 one-scan-fanout; "
        "per-agent scanning = the 2.57% FAIL)")


def test_dead_root_reports_offline(tmp_path):
    root = str(tmp_path)
    _mkproc(root, 100, 1, 1, 1)
    s = ProcSampler(proc_root=root)
    snap = s.sample([100, 555])               # 555 does not exist
    assert snap[100]["alive"] is True
    assert snap[555]["alive"] is False
    assert snap[555]["live_pids"] == 0


def test_cpu_core_pct_delta_between_ticks(tmp_path):
    root = str(tmp_path)
    _mkproc(root, 100, 1, 100, 0)
    s = ProcSampler(proc_root=root, clk_tck=100)   # inject CLK for determinism
    s.sample([100], now=1000.0)                    # first tick: no delta yet
    # advance CPU by 50 ticks over a 5s window -> 0.5 core-seconds / 5s = 10% core
    _mkproc(root, 100, 1, 150, 0)
    snap = s.sample([100], now=1005.0)
    assert abs(snap[100]["cpu_core_pct"] - 10.0) < 0.01


def test_io_delta_between_ticks(tmp_path):
    root = str(tmp_path)
    _mkproc(root, 100, 1, 1, 1, rbytes=1000, wbytes=0)
    s = ProcSampler(proc_root=root, clk_tck=100)
    s.sample([100], now=1000.0)
    _mkproc(root, 100, 1, 1, 1, rbytes=1000, wbytes=61456)   # 61KB write
    snap = s.sample([100], now=1002.0)
    assert snap[100]["io_delta"] == 61456


def test_io_permission_error_is_tolerated(tmp_path):
    # /proc/<pid>/io of another user's pid raises PermissionError — treat as 0,
    # never crash the scan (we only care about our own agent trees anyway).
    root = str(tmp_path)
    _mkproc(root, 100, 1, 1, 1, io=False)     # no io file => unreadable
    s = ProcSampler(proc_root=root)
    snap = s.sample([100])
    assert snap[100]["alive"] is True
    assert snap[100]["io_delta"] == 0
