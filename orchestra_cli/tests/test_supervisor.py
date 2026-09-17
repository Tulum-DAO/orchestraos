"""RED-first: process table + supervisor loop, driven by fake spawns and a fake clock."""
import json
import signal
from pathlib import Path

from orchestra_cli import process_table as PT
from orchestra_cli import settings as S
from orchestra_cli import supervisor as SV


def _settings(tmp_path, extra=""):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    data = tmp_path / "data"
    (root / "orchestra.toml").write_text(
        f'[data]\ndir = "{data}"\n'
        '[gateway]\nhost = "127.0.0.1"\nport = 8890\n'
        '[dashboard]\nhost = "127.0.0.1"\nport = 8891\n'
        '[notify]\nchannel = "none"\n[runtimes]\nenabled = ["claude"]\n' + extra)
    return S.load_settings(repo_root=root)


def test_process_table_default_shape(tmp_path):
    st = _settings(tmp_path)
    table = PT.build_process_table(st)
    by = {e.name: e for e in table}
    assert [e.name for e in table if e.kind == "service"] == ["gateway", "api", "dashboard", "arturo", "telemetryd"]
    # Tier 0 item 4: the telemetry daemon (working/idle truth for the agents page) and the
    # in-agent menu bridge (AskUserQuestion widget -> decision card) run under the supervisor
    assert by["telemetryd"].argv[-2:] == ["-m", "lineage_daemon.telemetryd"] and by["telemetryd"].port is None
    assert by["menu_bridge"].argv[-2:] == [by["menu_bridge"].argv[-2], "--cron"] and by["menu_bridge"].argv[-2].endswith("scripts/menu_bridge.py")
    assert by["gateway"].port == 8890 and "watch_gateway.py" in " ".join(by["gateway"].argv)
    assert by["api"].port == 8888 and by["api"].argv[-1].endswith("api/dist/server.js")
    assert by["dashboard"].port == 8891 and by["dashboard"].argv[-1].endswith("dashboard-proxy.js")
    assert by["arturo"].port == 5071 and by["arturo"].argv[-1].endswith("services/arturo/run.sh")
    beats = {e.name: e.interval for e in table if e.kind == "beat"}
    assert beats == {"bus_beat": 60, "boundary_delivery": 60, "cron_beat": 900, "router": 60, "approval_resume": 60, "menu_bridge": 60}
    assert by["boundary_delivery"].env["BOUNDARY_DELIVER_ARMED"] == "1"
    assert all(e.enabled for e in table)
    for e in table:
        assert e.cwd == str(st.repo_root)


def test_process_table_honors_config_toggles(tmp_path):
    st = _settings(tmp_path, '[arturo]\nenabled = false\n[rotation]\nbeat_enabled = false\n'
                             'boundary_delivery_armed = false\nbus_beat_interval_seconds = 30\n'
                             '[router]\nenabled = false\n')
    by = {e.name: e for e in PT.build_process_table(st)}
    assert by["arturo"].enabled is False
    assert by["cron_beat"].enabled is False
    assert by["bus_beat"].enabled is False and by["bus_beat"].interval == 30
    assert by["boundary_delivery"].enabled is False
    assert by["router"].enabled is False
    st2 = _settings(tmp_path, '[telemetry]\nenabled = false\n[menus]\nbridge_enabled = false\n')
    by2 = {e.name: e for e in PT.build_process_table(st2)}
    assert by2["telemetryd"].enabled is False and by2["menu_bridge"].enabled is False


def test_render_dry_run_lists_every_entry_and_interval(tmp_path):
    st = _settings(tmp_path)
    text = PT.render_table(PT.build_process_table(st))
    assert "gateway" in text and "cron_beat" in text and "900" in text and ":8890" in text


class FakeProc:
    def __init__(self, pid, argv):
        self.pid = pid
        self.argv = argv
        self.rc = None
        self.signals = []

    def poll(self):
        return self.rc

    def send_signal(self, sig):
        self.signals.append(sig)
        self.rc = -int(sig)

    terminate = lambda self: self.send_signal(signal.SIGTERM)  # noqa: E731
    kill = lambda self: self.send_signal(signal.SIGKILL)  # noqa: E731

    def wait(self, timeout=None):
        return self.rc


class FakeSpawner:
    def __init__(self):
        self.spawned = []
        self._pid = 100

    def __call__(self, entry, env, log_path):
        self._pid += 1
        p = FakeProc(self._pid, entry.argv)
        self.spawned.append((entry.name, p))
        return p


def _entries():
    return [
        PT.ProcEntry(name="svc", kind="service", argv=["svc"], cwd="/", env={}, port=1),
        PT.ProcEntry(name="beat", kind="beat", argv=["beat"], cwd="/", env={}, interval=60),
        PT.ProcEntry(name="off", kind="service", argv=["off"], cwd="/", env={}, enabled=False),
    ]


def test_supervisor_starts_enabled_services_and_beats_on_first_tick(tmp_path):
    sp = FakeSpawner()
    sup = SV.Supervisor(_entries(), data_dir=tmp_path, base_env={}, spawn=sp, clock=lambda: 1000.0)
    sup.tick()
    names = [n for n, _ in sp.spawned]
    assert names == ["svc", "beat"]
    state = json.loads((tmp_path / "state" / "supervisor.json").read_text())
    assert state["children"]["svc"]["pid"] == 101 and state["children"]["svc"]["status"] == "running"
    assert state["children"]["off"]["status"] == "disabled"


def test_service_restarts_with_backoff_after_exit(tmp_path):
    sp = FakeSpawner()
    t = [1000.0]
    sup = SV.Supervisor(_entries(), data_dir=tmp_path, base_env={}, spawn=sp, clock=lambda: t[0])
    sup.tick()
    sp.spawned[0][1].rc = 1            # crash
    t[0] += 0.5
    sup.tick()                          # exit noticed; not restarted before backoff
    assert [n for n, _ in sp.spawned].count("svc") == 1
    t[0] += SV.BACKOFF_BASE_S + 0.1
    sup.tick()
    assert [n for n, _ in sp.spawned].count("svc") == 2
    # second crash (noticed now) doubles the backoff
    sp.spawned[-1][1].rc = 1
    sup.tick()
    t[0] += SV.BACKOFF_BASE_S + 0.1
    sup.tick()
    assert [n for n, _ in sp.spawned].count("svc") == 2
    t[0] += SV.BACKOFF_BASE_S + 0.1
    sup.tick()
    assert [n for n, _ in sp.spawned].count("svc") == 3
    state = json.loads((tmp_path / "state" / "supervisor.json").read_text())
    assert state["children"]["svc"]["restarts"] == 2


def test_backoff_resets_after_stable_uptime(tmp_path):
    sp = FakeSpawner()
    t = [0.0]
    sup = SV.Supervisor(_entries(), data_dir=tmp_path, base_env={}, spawn=sp, clock=lambda: t[0])
    sup.tick()
    sp.spawned[0][1].rc = 1
    sup.tick()                          # crash noticed at t=0
    t[0] += SV.BACKOFF_BASE_S + 1
    sup.tick()
    assert [n for n, _ in sp.spawned].count("svc") == 2
    t[0] += SV.STABLE_UPTIME_S + 1     # ran fine for a long time
    sup.tick()
    sp.spawned[-1][1].rc = 0
    sup.tick()                          # exit noticed; backoff reset to base
    t[0] += SV.BACKOFF_BASE_S + 0.1     # only base backoff needed again
    sup.tick()
    assert [n for n, _ in sp.spawned].count("svc") == 3


def test_beat_runs_on_interval_and_never_overlaps(tmp_path):
    sp = FakeSpawner()
    t = [0.0]
    sup = SV.Supervisor(_entries(), data_dir=tmp_path, base_env={}, spawn=sp, clock=lambda: t[0])
    sup.tick()
    assert [n for n, _ in sp.spawned].count("beat") == 1
    t[0] += 61
    sup.tick()                           # previous still running -> no overlap
    assert [n for n, _ in sp.spawned].count("beat") == 1
    sp.spawned[-1][1].rc = 0
    sup.tick()                           # finished; next due 60s after it STARTED (already past)
    assert [n for n, _ in sp.spawned].count("beat") == 2
    t[0] += 10
    sp.spawned[-1][1].rc = 0
    sup.tick()
    assert [n for n, _ in sp.spawned].count("beat") == 2   # not due yet
    t[0] += 60
    sup.tick()
    assert [n for n, _ in sp.spawned].count("beat") == 3


def test_stop_terminates_children_and_clears_pidfile(tmp_path):
    sp = FakeSpawner()
    sup = SV.Supervisor(_entries(), data_dir=tmp_path, base_env={}, spawn=sp, clock=lambda: 0.0)
    sup.write_pidfile(pid=4321)
    assert (tmp_path / "state" / "supervisor.pid").read_text().strip() == "4321"
    sup.tick()
    sup.stop()
    for _, p in sp.spawned:
        assert signal.SIGTERM in p.signals
    assert not (tmp_path / "state" / "supervisor.pid").exists()
    state = json.loads((tmp_path / "state" / "supervisor.json").read_text())
    assert state["children"]["svc"]["status"] == "stopped"


def test_child_env_merges_entry_env_over_base(tmp_path):
    seen = {}

    def spawn(entry, env, log_path):
        seen[entry.name] = (env, log_path)
        return FakeProc(1, entry.argv)

    entries = [PT.ProcEntry(name="x", kind="service", argv=["x"], cwd="/", env={"A": "entry"})]
    sup = SV.Supervisor(entries, data_dir=tmp_path, base_env={"A": "base", "B": "base"}, spawn=spawn, clock=lambda: 0.0)
    sup.tick()
    env, log_path = seen["x"]
    assert env["A"] == "entry" and env["B"] == "base"
    assert Path(log_path) == tmp_path / "logs" / "x.log"


def test_read_status_reports_dead_supervisor(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "supervisor.pid").write_text("999999999")
    st = SV.read_status(tmp_path, pid_alive=lambda pid: False)
    assert st["running"] is False and st["pid"] == 999999999


def test_process_table_runs_the_approval_resume_beat(tmp_path):
    """B1 finding 6 (outsider report, gm msg_d69910cc): an answered card never reached the
    seat under `orchestra up`. In the reference install the answer is delivered by a
    per-minute crontab entry (scripts/approval_resume.py: verified-inject into the seat's
    pane + durable msg_store row + watchdog); the supervisor ran no such beat."""
    st = _settings(tmp_path)
    by = {e.name: e for e in PT.build_process_table(st)}
    e = by["approval_resume"]
    assert e.kind == "beat" and e.enabled is True and e.interval == 60
    assert e.argv[-1].endswith("scripts/approval_resume.py")
    assert e.env.get("EXPIRE_PENDING") == "0"
