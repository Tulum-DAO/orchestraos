"""Seats run at a lower CPU priority than the services (Josh's 2-vCPU box, 2026-10-14).

A seat's heavy child (a pipeline at 73% CPU) starved the API: /api/agents took 2-9 s and the
dashboard showed "Connection lost"; `renice +10` on the seat brought it back to ~0.16 s. Seats now
start under `nice -n 10` ([seats] nice in orchestra.toml, ORCHESTRA_SEAT_NICE; 0 = off), and every
child a seat starts inherits it. The services stay at 0.
"""
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "seat_nice.sh"


def _prefix(value=None):
    env = dict(os.environ, ORCHESTRA_CONFIG="/nonexistent/orchestra.toml")   # no toml: the default
    env.pop("ORCHESTRA_SEAT_NICE", None)
    if value is not None:
        env["ORCHESTRA_SEAT_NICE"] = value
    r = subprocess.run(["bash", "-c", f'source "{HELPER}"; printf "[%s]" "$(seat_nice_prefix)"'],
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout, r.stderr


@pytest.mark.parametrize("value,expected", [
    (None, "[nice -n 10 ]"), ("10", "[nice -n 10 ]"), ("15", "[nice -n 15 ]"), ("19", "[nice -n 19 ]"),
    ("0", "[]"),
])
def test_prefix(value, expected):
    assert _prefix(value)[0] == expected


@pytest.mark.parametrize("value", ["abc", "-5", "20", "1.5", " 7"])
def test_a_bad_value_keeps_the_default_and_says_so(value):
    out, err = _prefix(value)
    assert out == "[nice -n 10 ]"
    assert "ORCHESTRA_SEAT_NICE" in err


def test_toml_seats_nice_reaches_the_env(tmp_path):
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text('[data]\ndir = "%s"\n[seats]\nnice = 5\n' % (tmp_path / "data"))
    env = dict(os.environ, ORCHESTRA_CONFIG=str(cfg))
    env.pop("ORCHESTRA_SEAT_NICE", None)
    r = subprocess.run(["bash", "-c", f'source "{ROOT}/scripts/orchestra-env.sh"; echo "[$ORCHESTRA_SEAT_NICE]"'],
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.stdout.strip().endswith("[5]"), (r.stdout, r.stderr)
    sys.path.insert(0, str(ROOT))
    from orchestra_cli import settings
    st = settings.load_settings(config_path=cfg)
    assert settings.child_env(st, base={})["ORCHESTRA_SEAT_NICE"] == "5"


# --- by effect: the line the pane receives -----------------------------------------------------
def _spawn_line(tmp_path, seat_nice=None):
    import json
    tree = tmp_path / "tree"
    (tree / "state").mkdir(parents=True, exist_ok=True)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text(json.dumps({"tokens": {"access_token": "x"}}))
    log = tmp_path / "tmux.log"
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    (fakebin / "tmux").write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{log}"\n'
        'case "$1" in capture-pane) printf "\\u203a Ask Codex to do anything\\n";; has-session) exit 1;; esac\n'
        "exit 0\n")
    (fakebin / "tmux").chmod(0o755)
    (fakebin / "codex").write_text("#!/bin/bash\necho codex-cli 0.153.4\n")
    (fakebin / "codex").chmod(0o755)
    reg = tree / "registry.json"
    reg.write_text(json.dumps({"agents": {"zz-nice": {"name": "zz-nice", "runtime": "codex",
                                                       "model": "gpt-5.6-terra", "tmux_session": "zz-nice",
                                                       "cwd": str(tree), "tier": "T2", "machine": "vps"}}}))
    env = dict(os.environ, ORCHESTRA_DIR=str(tree), REGISTRY=str(reg), NTFY_TOKEN_FILE="/nonexistent",
               HOME=str(home), AGENT_RUNTIME="codex", PATH=f"{fakebin}:{os.environ.get('PATH', '')}")
    for k in ("TMUX", "TMUX_PANE", "ORCHESTRA_SEAT_NICE"):
        env.pop(k, None)
    if seat_nice is not None:
        env["ORCHESTRA_SEAT_NICE"] = seat_nice
    script = 'sleep() { :; }; unset IDENTITY_STORE_CUTOVER; spawn_agent zz-nice "do nothing" ""; echo "rc=$?"'
    r = subprocess.run(["bash", "-c", f'source "$1"; {script}', "spawn-test", str(ROOT / "spawn-agent.sh")],
                       capture_output=True, text=True, cwd=ROOT, env=env, timeout=120)
    calls = log.read_text().splitlines() if log.exists() else []
    lines = [c for c in calls if c.startswith("send-keys -t zz-nice ") and "--yolo" in c]
    assert lines, (r.stdout[-1500:], r.stderr[-1500:], calls[-10:])
    return lines[-1]


def test_a_spawned_seat_starts_niced_outermost(tmp_path):
    line = _spawn_line(tmp_path)
    body = line[len("send-keys -t zz-nice "):]
    assert body.startswith("nice -n 10 env -u "), body          # outermost: every prefix + the CLI inherit it
    assert body.index("nice -n 10 ") < body.index("/codex --yolo")


def test_seat_nice_zero_launches_as_before(tmp_path):
    body = _spawn_line(tmp_path, "0")[len("send-keys -t zz-nice "):]
    assert body.startswith("env -u ") and "nice -n" not in body


def test_every_direct_cli_launch_in_boot_recovery_is_niced():
    """agent-recovery.sh types four CLI lines itself (agy / claude --resume); each must carry the
    same prefix as spawn-agent.sh, or a seat recovered after a reboot runs at the services' priority."""
    src = (ROOT / "scripts" / "agent-recovery.sh").read_text()
    launches = [l for l in src.splitlines()
                if "tmux send-keys" in l and re.search(r'"(\$\{SEAT_NICE\})?(agy |\$CLAUDE_BIN )', l)]
    assert len(launches) == 4, launches
    assert all('"${SEAT_NICE}' in l for l in launches), launches
    assert 'SEAT_NICE="$(seat_nice_prefix)"' in src and "seat_nice.sh" in src


# --- by effect: a CPU-burning seat child against a service poll ---------------------------------
def _service_poll_ms(cpu, rounds=6):
    """A stand-in for a busy service request (/api/agents under load): 100 ms of its own CPU work,
    timed wall-clock on `cpu`, so every moment it waits for the CPU shows up in the figure."""
    os.sched_setaffinity(0, {cpu})
    samples = []
    for _ in range(rounds):
        start = time.process_time()
        t0 = time.perf_counter()
        while time.process_time() - start < 0.1:
            pass
        samples.append((time.perf_counter() - t0) * 1000)
        time.sleep(0.005)
    return statistics.median(samples)


@pytest.mark.skipif(not hasattr(os, "sched_setaffinity") or not shutil.which("nice"),
                    reason="needs Linux CPU affinity and nice(1)")
def test_a_niced_cpu_burning_seat_child_does_not_starve_a_service_poll():
    """Pin a busy-loop 'seat child' and a 'service' poll to the SAME CPU. Started with the seat
    prefix (nice -n 10), the burner leaves the poll near its idle cost; at the services' priority
    it roughly doubles it (one CPU shared 50/50; at nice 10 the service keeps ~90%). Uses the real helper, so a default of 0 turns this red."""
    if min(19, os.nice(0) + 10) - os.nice(0) < 5:   # nice -n adds to ours and caps at 19
        pytest.skip(f"the test runs at nice {os.nice(0)}: the seat prefix can add < 5 there")
    cpu = sorted(os.sched_getaffinity(0))[-1]
    idle = _service_poll_ms(cpu)
    burn = 'python3 -c "import os; os.sched_setaffinity(0, {%d})\nwhile True: pass"' % cpu

    def with_burner(prefix):
        p = subprocess.Popen(["bash", "-c", f"exec {prefix}{burn}"])
        try:
            time.sleep(0.5)
            return _service_poll_ms(cpu)
        finally:
            p.kill()
            p.wait()

    seat_prefix = _prefix()[0][1:-1]
    niced = with_burner(seat_prefix)
    same = with_burner("")
    assert seat_prefix.startswith("nice -n ")
    assert same > idle * 1.6, (idle, niced, same)       # the starvation is real on one CPU
    assert niced < same * 0.7, (idle, niced, same)      # and the seat prefix removes most of it
