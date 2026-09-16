"""Structural guard — the A0 CPU-HARD rule, enforced by construction.

A0 proved a per-agent `ps` fork = 2.57% (FAIL) and one-scan-fanout = 0.320%
(PASS). The B1 real-time lane must therefore:
  (1) confine ALL /proc access to proc_sampler.py (the ONE fan-out scanner), and
  (2) NEVER fork a subprocess (ps/os.popen/subprocess) — a fork-per-agent is the
      exact failure A0 forbade; the sampler reads /proc files directly, no forks.
scan_count assertions in proc_sampler_test / cpu_measure_test prove the ONE-scan
count at runtime; this test proves it structurally at the source level.
"""
import os

_HERE = os.path.dirname(__file__)
_MODULES = [f for f in os.listdir(_HERE)
            if f.endswith(".py") and not f.endswith("_test.py")
            and f != "__init__.py"]


def _code(mod):
    with open(os.path.join(_HERE, mod)) as fh:
        return "".join(l for l in fh if not l.lstrip().startswith("#"))


def test_proc_access_confined_to_the_sampler():
    # ACTUAL access idioms (a reader/scanner), NOT the harmless default string
    # `proc_root="/proc"` the daemon forwards to the sampler.
    access_idioms = ("listdir('/proc", 'listdir("/proc', "listdir(self._proc",
                     "open('/proc", 'open("/proc', "/proc/{", "/proc/%",
                     "'/proc/'", '"/proc/"', "join(self._proc")
    for mod in _MODULES:
        if mod == "proc_sampler.py":
            continue
        src = _code(mod)
        for idiom in access_idioms:
            assert idiom not in src, f"{mod} accesses /proc outside the sampler: {idiom!r}"


def test_no_subprocess_fork_anywhere_in_the_realtime_lane():
    # no ps/os.popen/subprocess fork — a fork-per-agent is the 2.57% FAIL. The
    # pipe-pane attach is issued via an INJECTED tmux runner (not a fork here).
    for mod in _MODULES:
        src = _code(mod)
        for banned in ("import subprocess", "os.popen(", "os.system(",
                       "os.fork(", "Popen("):
            assert banned not in src, f"{mod} forks a process ({banned}) — banned in the real-time lane"
